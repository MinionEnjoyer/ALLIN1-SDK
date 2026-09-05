// Shared SDK/Launcher schema-4 member I/O. No suffix lookup or in-place nested writes.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using CodeWalker.GameFiles;

namespace RpfPatcher
{
    partial class Program
    {
        const long ExactMemberLimit = 128L * 1024 * 1024;
        const long NestedArchiveLimit = 512L * 1024 * 1024;
        const long OuterArchiveLimit = 2L * 1024 * 1024 * 1024;

        static string[] ParseNestedMember(string path)
        {
            if (path == null || path.Length > 2048) throw new InvalidDataException("Unbounded nested target.");
            string[] parts = path.Replace('\\', '/').Split('!');
            if (parts.Length < 2 || parts.Length > 9)
                throw new InvalidDataException("Expected 1–8 explicit nested RPF layers.");
            for (int i = 0; i < parts.Length; i++)
            {
                if (parts[i] != parts[i].Trim()) throw new InvalidDataException("Whitespace around an archive layer.");
                string[] components = parts[i].Split('/');
                foreach (string segment in components)
                {
                    if (string.IsNullOrWhiteSpace(segment) || segment == "." || segment == ".."
                        || segment.EndsWith(".") || segment.EndsWith(" ")
                        || segment.Any(c => char.IsControl(c) || "<>:\"|?*".Contains(c))
                        || Regex.IsMatch(segment.Split('.')[0], @"^(con|prn|aux|nul|conin\$|conout\$|com[1-9]|lpt[1-9])$", RegexOptions.IgnoreCase))
                        throw new InvalidDataException("Unsafe nested member path.");
                }
                if (components.Take(components.Length - 1).Any(p => p.EndsWith(".rpf", StringComparison.OrdinalIgnoreCase))
                    || components.Last().EndsWith(".rpf", StringComparison.OrdinalIgnoreCase) != (i < parts.Length - 1))
                    throw new InvalidDataException("RPF layers must be separated by !; the final target must be a file.");
            }
            return parts;
        }

        static RpfFileEntry FindExactNestedMember(RpfFile root, string target)
        {
            string[] parts = ParseNestedMember(target);
            RpfFile current = root;
            for (int i = 0; i < parts.Length; i++)
            {
                var entry = FindExactFileEntry(current, parts[i]);
                if (entry == null) return null;
                if (i == parts.Length - 1) return entry;
                // Identity is the exact parent entry, never display paths or basenames.
                var children = (current.Children ?? new List<RpfFile>())
                    .Where(child => ReferenceEquals(child.ParentFileEntry, entry)).ToArray();
                if (children.Length > 1) throw new InvalidDataException("Ambiguous nested archive identity.");
                if (children.Length == 0) return null;
                current = children[0];
            }
            return null;
        }

        static RpfFile ReadExactArchive(string gta, string path)
        {
            bool gen9 = File.Exists(Path.Combine(gta, "GTA5_Enhanced.exe")) || File.Exists(Path.Combine(gta, "eboot.bin"));
            LoadReadOnlyArchiveKeys(gta, gen9, path);
            var root = new RpfFile(path, Path.GetFileName(path));
            root.ScanStructure(null, warning => { throw new InvalidDataException("RPF scan: " + warning); });
            if (root.Root == null) throw new InvalidDataException("RPF has no root.");
            return root;
        }

        static byte[] ExactMemberBytes(RpfFileEntry entry, long limit = ExactMemberLimit)
        {
            if (entry == null) throw new FileNotFoundException("Exact member not found.");
            long size = entry is RpfResourceFileEntry resourceSize
                ? (long)resourceSize.SystemSize + resourceSize.GraphicsSize
                : entry is RpfBinaryFileEntry binary ? Math.Max(binary.FileSize, binary.FileUncompressedSize) : 0;
            if (size > limit) throw new InvalidDataException("Member exceeds the bounded exact-I/O size limit.");
            byte[] bytes = entry.File.ExtractFile(entry);
            if (bytes == null || bytes.Length == 0 || bytes.LongLength > limit)
                throw new InvalidDataException("Member is empty, unreadable or over the exact-I/O size limit.");
            if (entry is RpfResourceFileEntry resource)
                bytes = ResourceBuilder.AddResourceHeader(resource, ResourceBuilder.Compress(bytes));
            return bytes;
        }

        static void ExactFingerprints(RpfFile archive, string prefix, Dictionary<string, string> hashes, int depth = 0)
        {
            if (depth > 8) throw new InvalidDataException("Archive verification exceeds 8 layers.");
            foreach (var entry in archive.AllEntries.OfType<RpfFileEntry>())
            {
                string relative = RelativeRpfEntryPath(archive, entry).Replace('\\', '/');
                var exact = FindExactFileEntry(archive, relative);
                if (!ReferenceEquals(exact, entry)) throw new InvalidDataException("Inconsistent member identity.");
                if (entry.Name.EndsWith(".rpf", StringComparison.OrdinalIgnoreCase))
                {
                    var children = (archive.Children ?? new List<RpfFile>())
                        .Where(child => ReferenceEquals(child.ParentFileEntry, entry)).ToArray();
                    if (children.Length != 1) throw new InvalidDataException("Unreadable or ambiguous child archive.");
                    ExactFingerprints(children[0], prefix + relative + "!", hashes, depth + 1);
                }
                else
                {
                    if (hashes.Count >= 25000) throw new InvalidDataException("Archive verification exceeds 25,000 files.");
                    hashes.Add(prefix + relative, Sha256(ExactMemberBytes(entry)));
                }
            }
        }

        static int ExtractExactNestedEntry(string[] args)
        {
            if (args.Length != 5) return 1;
            try
            {
                ParseNestedMember(args[3]);
                var entry = FindExactNestedMember(ReadExactArchive(args[1], args[2]), args[3]);
                byte[] bytes = ExactMemberBytes(entry);
                File.WriteAllBytes(args[4], bytes);
                Console.WriteLine("Extracted exact nested member: " + args[3]);
                return 0;
            }
            catch (FileNotFoundException error) { Console.Error.WriteLine(error.Message); return 5; }
            catch (Exception error) { Console.Error.WriteLine("Exact nested extraction refused: " + error.Message); return 99; }
        }

        static void RequireExactGameClosed()
        {
            foreach (var process in Process.GetProcesses())
            {
                using (process)
                {
                    string name = process.ProcessName;
                    if (name.Equals("GTA5", StringComparison.OrdinalIgnoreCase)
                        || name.Equals("GTA5_Enhanced", StringComparison.OrdinalIgnoreCase)
                        || name.Equals("GTA5_Enhanced_BE", StringComparison.OrdinalIgnoreCase)
                        || name.Equals("GTA5_BE", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException("Close GTA V before replacing a nested member.");
                }
            }
        }

        static void NoReparseAncestors(string path)
        {
            for (string current = Path.GetFullPath(path); current != null; current = Path.GetDirectoryName(current))
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Reparse paths are not permitted for exact nested writes.");
        }

        static int ReplaceExactNestedEntry(string[] args)
        {
            // expected current SHA + independently declared replacement SHA; no legacy fallback.
            if (args.Length != 7) return 1;
            string staging = null;
            try
            {
                string[] parts = ParseNestedMember(args[3]);
                if (!Regex.IsMatch(args[5], "^[a-f0-9]{64}$") || !Regex.IsMatch(args[6], "^[a-f0-9]{64}$"))
                    throw new InvalidDataException("Expected current and replacement SHA-256 are required.");
                string target = Path.GetFullPath(args[2]);
                NoReparseAncestors(target);
                RequireExactGameClosed();
                long outerSize = new FileInfo(target).Length, payloadSize = new FileInfo(args[4]).Length;
                if (outerSize > OuterArchiveLimit || payloadSize <= 0 || payloadSize > ExactMemberLimit)
                    throw new InvalidDataException("Outer archive or payload exceeds the bounded exact-I/O size limit.");
                byte[] payload = File.ReadAllBytes(args[4]);
                if (Sha256(payload) != args[6]) throw new InvalidDataException("Replacement checksum mismatch.");
                using var guard = new FileStream(target + ".allin1-member.lock", FileMode.CreateNew, FileAccess.ReadWrite,
                    FileShare.None, 1, FileOptions.DeleteOnClose);
                // Keep the original open read-only with write/delete sharing denied throughout staging.
                using var source = new FileStream(target, FileMode.Open, FileAccess.Read, FileShare.Read);
                var original = ReadExactArchive(args[1], target);
                string currentHash = Sha256(ExactMemberBytes(FindExactNestedMember(original, args[3])));
                if (currentHash != args[5] && currentHash != args[6])
                    throw new InvalidDataException("Current nested member checksum mismatch.");
                if (currentHash == args[6]) return 0; // Idempotent recovery of a completed or unstarted write.
                var before = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                ExactFingerprints(original, "", before);
                long required = checked(outerSize * (parts.Length + 2L) + 64L * 1024 * 1024);
                if (new DriveInfo(Path.GetPathRoot(target)).AvailableFreeSpace < required)
                    throw new IOException("Insufficient free space for verified nested staging.");
                staging = Path.Combine(Path.GetDirectoryName(target), ".allin1-member-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(staging);
                string outerFolder = Path.Combine(staging, "outer");
                Directory.CreateDirectory(outerFolder);
                var copies = new List<string> { Path.Combine(outerFolder, Path.GetFileName(target)) };
                using (var copy = new FileStream(copies[0], FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    source.CopyTo(copy);
                // Detach every child. Only the innermost copy is edited; parents are rebuilt bottom-up.
                for (int i = 0; i < parts.Length - 1; i++)
                {
                    var parent = ReadExactArchive(args[1], copies[i]);
                    byte[] child = ExactMemberBytes(FindExactFileEntry(parent, parts[i]), NestedArchiveLimit);
                    // Preserve each basename for name-dependent decoding. Per-depth
                    // directories also prevent collisions with the outer filename.
                    string childFolder = Path.Combine(staging, "layer-" + i);
                    Directory.CreateDirectory(childFolder);
                    string childPath = Path.Combine(childFolder, parts[i].Split('/').Last());
                    File.WriteAllBytes(childPath, child);
                    copies.Add(childPath);
                }
                byte[] replacement = payload;
                for (int i = copies.Count - 1; i >= 0; i--)
                {
                    var writable = OpenWritableRpf(args[1], copies[i]);
                    var entry = FindExactFileEntry(writable, parts[i]);
                    if (entry == null) throw new FileNotFoundException("Exact replacement member not found.");
                    RpfFile.CreateFile(entry.Parent, entry.Name, replacement, true);
                    if (i > 0)
                    {
                        if (new FileInfo(copies[i]).Length > NestedArchiveLimit)
                            throw new InvalidDataException("Rebuilt child exceeds the nested archive size limit.");
                        replacement = File.ReadAllBytes(copies[i]);
                    }
                }
                var after = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                ExactFingerprints(ReadExactArchive(args[1], copies[0]), "", after);
                before[args[3].Replace('\\', '/')] = args[6];
                if (after.Count != before.Count || before.Any(pair => !after.TryGetValue(pair.Key, out string hash) || hash != pair.Value))
                    throw new InvalidDataException("Staged archive verification failed; original unchanged.");
                using (var durable = new FileStream(copies[0], FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                    durable.Flush(true);
                RequireExactGameClosed();
                NoReparseAncestors(target);
                // Windows replacement is atomic. The cooperative lock remains held through commit.
                source.Dispose();
                File.Replace(copies[0], target, null);
                Console.WriteLine("Verified and replaced exact nested member: " + args[3]);
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine("Exact nested replacement refused: " + error.Message); return 99; }
            finally
            {
                if (staging != null && Directory.Exists(staging))
                {
                    try { Directory.Delete(staging, true); }
                    catch (IOException error) { Console.Error.WriteLine("Staging cleanup warning: " + error.Message); }
                }
            }
        }
    }
}
