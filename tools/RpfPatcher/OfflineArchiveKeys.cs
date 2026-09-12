using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CodeWalker.GameFiles;

namespace RpfPatcher
{
    partial class Program
    {
        static bool IsUnencryptedRpf(string path)
        {
            using var stream = System.IO.File.OpenRead(path);
            if (stream.Length < 16) return false;
            using var reader = new BinaryReader(stream);
            if (reader.ReadUInt32() != 0x52504637) return false;
            reader.ReadUInt32();
            reader.ReadUInt32();
            var encryption = (RpfEncryption)reader.ReadUInt32();
            return encryption == RpfEncryption.OPEN || encryption == RpfEncryption.NONE;
        }

        static string LoadReadOnlyArchiveKeys(string gtaPath, bool gen9, string archivePath)
        {
            bool hasGameContext = HasArchiveKeyContext(gtaPath, gen9);
            bool unencryptedRoot = IsUnencryptedRpf(archivePath);
            if (unencryptedRoot)
            {
                // OPEN/NONE authoring archives do not need game encryption keys.
                // A file named like a GTA executable is not proof it contains
                // usable keys; this is common for edition-only isolated tooling.
                // If loading those optional keys fails, continue keyless; the
                // later structural scan records any encrypted-child failure.
                if (hasGameContext)
                {
                    try
                    {
                        GTA5Keys.LoadFromPath(gtaPath, gen9, null);
                        return "loaded";
                    }
                    catch
                    {
                        Console.Error.WriteLine(
                            "No usable game keys; reading an unencrypted authoring archive.");
                    }
                }
                else
                {
                    Console.Error.WriteLine(
                        "No game context; reading an unencrypted authoring archive.");
                }
                return "not-required-unencrypted-root";
            }
            GTA5Keys.LoadFromPath(gtaPath, gen9, null);
            return "loaded";
        }

        static bool HasArchiveKeyContext(string gtaPath, bool gen9)
        {
            return !string.IsNullOrWhiteSpace(gtaPath) && File.Exists(Path.Combine(
                gtaPath, gen9 ? "GTA5_Enhanced.exe" : "GTA5.exe"));
        }

        static void RequireCompleteKeylessScan(
            string keyMode, IReadOnlyCollection<string> warnings)
        {
            if (keyMode == "not-required-unencrypted-root"
                && warnings != null && warnings.Count != 0)
            {
                throw new InvalidDataException(
                    "RPF scan could not be completed without encryption keys: "
                    + warnings.FirstOrDefault());
            }
        }
    }
}
