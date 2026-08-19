// RpfPatcher — Tools for managing ALLIN1 DLC content in GTA V RPF archives.
// Uses CodeWalker.Core to read/write RPF7 archives.
//
// Commands:
//   RpfPatcher.exe inject-ytd   <gta_path> <ytd_folder>  — inject .ytd files into script_txds.rpf
//   RpfPatcher.exe remove-ytd   <gta_path> <prefix>      — remove ALLIN1 .ytd files from script_txds.rpf
//   RpfPatcher.exe verify-ytd   <gta_path> <ytd_folder>  — verify expected .ytd files in script_txds.rpf
//   RpfPatcher.exe patch        <gta_path>                — add allin1_previews to dlclist.xml
//   RpfPatcher.exe unpatch      <gta_path>                — remove allin1_previews from dlclist.xml
//   RpfPatcher.exe register-dlc <gta_path> <pack_name>    — register a manifest-owned add-on pack
//   RpfPatcher.exe unregister-dlc <gta_path> <pack_name>  — unregister a manifest-owned add-on pack
//   RpfPatcher.exe build-dlc    <loose_folder> <output_rpf> [--embed-rpf <src_folder> <dest_path>]
//   RpfPatcher.exe defragment-copy <gta_path> <input_rpf> <output_rpf> <report_json>
//   RpfPatcher.exe verify-dlc   <dlc_rpf> <ytd_folder>      — verify a preview DLC and its dictionaries
//   RpfPatcher.exe convert-gen9 <ytd_folder>              — convert .ytd files from Legacy to Enhanced format
//   RpfPatcher.exe inspect      <gta_path> <rpf_path>    — dump RPF structure + XML contents
//   RpfPatcher.exe extract-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>
//   RpfPatcher.exe extract-virtual-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>
//   RpfPatcher.exe apply-entry-changes <gta_path> <rpf_path> <manifest_tsv> <payload_root>
//   RpfPatcher.exe asset-from-xml <input_xml> <output_asset> <asset_folder> [legacy|gen9] [source_asset] [gta_path]
//   RpfPatcher.exe audit-seats  <gta_path> <output_json> [output_cs]
//   RpfPatcher.exe install-euphoria <gta_path> <payload_folder> [--allow-enhanced]
//   RpfPatcher.exe verify-euphoria  <gta_path> <payload_folder>
//   RpfPatcher.exe validate-euphoria <payload_folder_or_archive>
//   RpfPatcher.exe remove-euphoria  <gta_path>
//   RpfPatcher.exe build-colored-smoke-weapons <gta_path> <output_rpf>
//   RpfPatcher.exe build-merged-smoke-canary <gta_path> <output_meta>
//   RpfPatcher.exe build-merged-smoke-weapons <gta_path> <output_meta>

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Xml;
using System.Xml.Linq;
using CodeWalker.Core.Utils;
using CodeWalker.GameFiles;
using CodeWalker.Utils;

namespace RpfPatcher
{
    class Program
    {
        private static readonly Dictionary<string, string> OwnedDlcEntries =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "allin1_previews", "dlcpacks:/allin1_previews/" },
                { "allin1_maps", "dlcpacks:/allin1_maps/" },
                { "allin1_smoke", "dlcpacks:/allin1_smoke/" },
            };

        static int Main(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage:\n" +
                    "  RpfPatcher.exe inject-ytd   <gta_path> <ytd_folder>\n" +
                    "  RpfPatcher.exe remove-ytd   <gta_path> <prefix>\n" +
                    "  RpfPatcher.exe verify-ytd   <gta_path> <ytd_folder>\n" +
                    "  RpfPatcher.exe patch        <gta_path>\n" +
                    "  RpfPatcher.exe unpatch      <gta_path>\n" +
                    "  RpfPatcher.exe register-dlc <gta_path> <pack_name>\n" +
                    "  RpfPatcher.exe unregister-dlc <gta_path> <pack_name>\n" +
                    "  RpfPatcher.exe build-dlc    <loose_folder> <output_rpf> [--embed-rpf <src> <dest>]\n" +
                    "  RpfPatcher.exe defragment-copy <gta_path> <input_rpf> <output_rpf> <report_json>\n" +
                    "  RpfPatcher.exe verify-dlc   <dlc_rpf> <ytd_folder>\n" +
                    "  RpfPatcher.exe verify-map-dlc <dlc_rpf> <manifest_tsv>\n" +
                    "  RpfPatcher.exe convert-gen9 <ytd_folder>\n" +
                    "  RpfPatcher.exe inspect      <gta_path> <rpf_path>\n" +
                    "  RpfPatcher.exe index-json   <gta_path> <rpf_path> <output_json>\n" +
                    "  RpfPatcher.exe extract-virtual-entry <gta_path> <rpf_path> <archive_path> <entry_path> <output>\n" +
                    "  RpfPatcher.exe extract-virtual-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>\n" +
                    "  RpfPatcher.exe asset-xml    <input_asset> <output_xml> <asset_folder> [legacy|gen9] [gta_path]\n" +
                    "  RpfPatcher.exe asset-from-xml <input_xml> <output_asset> <asset_folder> [legacy|gen9] [source_asset] [gta_path]\n" +
                    "  RpfPatcher.exe audit-seats  <gta_path> <output_json> [output_cs]\n" +
                    "  RpfPatcher.exe build-ytd    <dds_folder> <output_ytd> [legacy|gen9]\n" +
                    "  RpfPatcher.exe unpack-ytd   <ytd_path> <output_folder> [legacy|gen9]\n" +
                    "  RpfPatcher.exe extract-entry <gta_path> <rpf_path> <name> <output>\n" +
                    "  RpfPatcher.exe replace-entry <gta_path> <rpf_path> <entry_path> <payload>\n" +
                    "  RpfPatcher.exe delete-entry <gta_path> <rpf_path> <entry_path>\n" +
                    "  RpfPatcher.exe apply-entry-changes <gta_path> <rpf_path> <manifest_tsv> <payload_root>\n" +
                    "  RpfPatcher.exe extract-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>\n" +
                    "  RpfPatcher.exe pso-to-xml <input_pso> <output_xml>\n" +
                    "  RpfPatcher.exe inspect-pso <input_pso>\n" +
                    "  RpfPatcher.exe build-smoke-tuning <input_ymt> <input_dat> <output_folder>\n" +
                    "  RpfPatcher.exe install-smoke-tuning <gta_path>\n" +
                    "  RpfPatcher.exe verify-smoke-tuning <gta_path>\n" +
                    "  RpfPatcher.exe remove-smoke-tuning <gta_path>\n" +
                    "  RpfPatcher.exe build-colored-smoke-weapons <gta_path> <output_rpf>\n" +
                    "  RpfPatcher.exe install-colored-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe verify-colored-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe remove-colored-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe build-merged-smoke-canary <gta_path> <output_meta>\n" +
                    "  RpfPatcher.exe install-merged-smoke-canary <gta_path>\n" +
                    "  RpfPatcher.exe verify-merged-smoke-canary <gta_path>\n" +
                    "  RpfPatcher.exe remove-merged-smoke-canary <gta_path>\n" +
                    "  RpfPatcher.exe build-merged-smoke-weapons <gta_path> <output_meta>\n" +
                    "  RpfPatcher.exe install-merged-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe verify-merged-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe remove-merged-smoke-weapons <gta_path>\n" +
                    "  RpfPatcher.exe open-rpfs <gta_path> <manifest_tsv> <output_root>\n" +
                    "  RpfPatcher.exe install-euphoria <gta_path> <payload_folder_or_archive> [--allow-enhanced]\n" +
                    "  RpfPatcher.exe verify-euphoria <gta_path> <payload_folder_or_archive>\n" +
                    "  RpfPatcher.exe validate-euphoria <payload_folder_or_archive>\n" +
                    "  RpfPatcher.exe remove-euphoria <gta_path>\n" +
                    "  RpfPatcher.exe dump-ytd      <ytd_path> [legacy|gen9]");
                return 1;
            }

            string command = args[0].ToLower();

            if (command == "inject-ytd")
                return InjectYtd(args);
            if (command == "remove-ytd")
                return RemoveYtd(args);
            if (command == "verify-ytd")
                return VerifyYtd(args);
            if (command == "build-dlc")
                return BuildDlc(args);
            if (command == "defragment-copy")
                return DefragmentCopy(args);
            if (command == "verify-dlc")
                return VerifyDlc(args);
            if (command == "verify-map-dlc")
                return VerifyMapDlc(args);
            if (command == "convert-gen9")
                return ConvertGen9(args);
            if (command == "inspect")
                return InspectRpf(args);
            if (command == "index-json")
                return IndexRpfJson(args);
            if (command == "extract-virtual-entry")
                return ExtractVirtualEntry(args);
            if (command == "extract-virtual-entries")
                return ExtractVirtualEntries(args);
            if (command == "asset-xml")
                return ExportAssetXml(args);
            if (command == "asset-from-xml")
                return ImportAssetXml(args);
            if (command == "audit-seats")
                return SeatCatalogAudit.Run(args);
            if (command == "build-ytd")
                return BuildYtd(args);
            if (command == "unpack-ytd")
                return UnpackYtd(args);
            if (command == "extract-entry")
                return ExtractEntry(args);
            if (command == "replace-entry")
                return ReplaceEntry(args);
            if (command == "delete-entry")
                return DeleteEntry(args);
            if (command == "apply-entry-changes")
                return ApplyEntryChanges(args);
            if (command == "extract-entries")
                return ExtractEntries(args);
            if (command == "pso-to-xml")
                return PsoToXml(args);
            if (command == "inspect-pso")
                return InspectPso(args);
            if (command == "build-smoke-tuning")
                return BuildSmokeTuning(args);
            if (command == "install-smoke-tuning")
                return InstallSmokeTuning(args);
            if (command == "verify-smoke-tuning")
                return VerifySmokeTuning(args);
            if (command == "remove-smoke-tuning")
                return RemoveSmokeTuning(args);
            if (command == "build-colored-smoke-weapons")
                return BuildColoredSmokeWeapons(args);
            if (command == "install-colored-smoke-weapons")
                return InstallColoredSmokeWeapons(args);
            if (command == "verify-colored-smoke-weapons")
                return VerifyColoredSmokeWeapons(args);
            if (command == "remove-colored-smoke-weapons")
                return RemoveColoredSmokeWeapons(args);
            if (command == "build-merged-smoke-canary")
                return BuildMergedSmokeCanary(args);
            if (command == "install-merged-smoke-canary")
                return InstallMergedSmokeCanary(args);
            if (command == "verify-merged-smoke-canary")
                return VerifyMergedSmokeCanary(args);
            if (command == "remove-merged-smoke-canary")
                return RemoveMergedSmokeCanary(args);
            if (command == "build-merged-smoke-weapons")
                return BuildMergedSmokeWeapons(args);
            if (command == "install-merged-smoke-weapons")
                return InstallMergedSmokeWeapons(args);
            if (command == "verify-merged-smoke-weapons")
                return VerifyMergedSmokeWeapons(args);
            if (command == "remove-merged-smoke-weapons")
                return RemoveMergedSmokeCanary(args);
            if (command == "merge-smoke-language-worker")
                return MergeSmokeLanguageWorker(args);
            if (command == "merge-smoke-hud-worker")
                return MergeSmokeHudWorker(args);
            if (command == "open-rpfs")
                return OpenRpfs(args);
            if (command == "install-euphoria")
                return InstallEuphoria(args);
            if (command == "verify-euphoria")
                return VerifyEuphoria(args);
            if (command == "validate-euphoria")
                return ValidateEuphoria(args);
            if (command == "remove-euphoria")
                return RemoveEuphoria(args);
            if (command == "dump-ytd")
                return DumpYtd(args);
            if (command == "patch" || command == "unpatch")
                return PatchCommand(command, args);
            if (command == "register-dlc")
                return PatchCommand("patch", args, true);
            if (command == "unregister-dlc")
                return PatchCommand("unpatch", args, true);

            Console.Error.WriteLine($"ERROR: Unknown command '{command}'.");
            return 1;
        }

        // ================================================================
        //  Shared: Open mods/update/update.rpf with encryption keys
        // ================================================================

        /// <summary>
        /// Detect edition, load keys, copy update.rpf to mods/ if needed,
        /// open and scan the mods copy, convert to OPEN encryption.
        /// Returns the opened RpfFile or null on failure (error printed).
        /// </summary>
        static RpfFile OpenModsUpdateRpf(string gtaPath, out int errorCode,
                                         string archiveName = "update.rpf",
                                         bool createModsCopy = true)
        {
            errorCode = 0;

            bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                       || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
            string exeName = isGen9 ? "GTA5_Enhanced.exe" : "GTA5.exe";

            if (!File.Exists(Path.Combine(gtaPath, exeName)))
            {
                Console.Error.WriteLine($"ERROR: {exeName} not found in {gtaPath}");
                errorCode = 2;
                return null;
            }

            Console.WriteLine($"Edition: {(isGen9 ? "Enhanced" : "Legacy")}");

            Console.WriteLine("Loading encryption keys...");
            GTA5Keys.LoadFromPath(gtaPath, isGen9, null);

            if (GTA5Keys.PC_AES_KEY == null)
            {
                Console.Error.WriteLine("ERROR: Failed to load encryption keys.");
                errorCode = 3;
                return null;
            }
            Console.WriteLine("Encryption keys loaded.");

            string originalRpf = Path.Combine(gtaPath, "update", archiveName);
            string modsDir = Path.Combine(gtaPath, "mods", "update");
            string modsRpf = Path.Combine(modsDir, archiveName);

            if (!File.Exists(originalRpf))
            {
                Console.Error.WriteLine($"ERROR: {originalRpf} not found");
                errorCode = 4;
                return null;
            }

            if (Directory.Exists(modsRpf))
            {
                if (!createModsCopy)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Expected an RPF file but found a directory: {modsRpf}");
                    errorCode = 7;
                    return null;
                }
                Console.WriteLine($"Removing stale directory at {modsRpf}...");
                Directory.Delete(modsRpf, true);
            }

            if (!File.Exists(modsRpf))
            {
                if (!createModsCopy)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Mods archive is not installed: {modsRpf}");
                    errorCode = 7;
                    return null;
                }
                Console.WriteLine($"Copying update.rpf to mods folder...");
                Directory.CreateDirectory(modsDir);
                File.Copy(originalRpf, modsRpf);
                Console.WriteLine("Copied update.rpf to mods folder.");
            }
            else
            {
                if (File.GetLastWriteTimeUtc(modsRpf).AddSeconds(1) <
                    File.GetLastWriteTimeUtc(originalRpf))
                {
                    Console.Error.WriteLine(
                        $"ERROR: mods/update/{archiveName} predates the current game archive. " +
                        "Refresh it before using the RPF loader.");
                    errorCode = 6;
                    return null;
                }
                Console.WriteLine("Mods copy of update.rpf already exists.");
            }

            Console.WriteLine($"Opening {modsRpf}...");
            var rpf = new RpfFile(modsRpf, modsRpf);
            rpf.ScanStructure(null, err => Console.Error.WriteLine($"RPF scan warning: {err}"));

            if (rpf.AllEntries == null || rpf.AllEntries.Count == 0)
            {
                Console.Error.WriteLine("ERROR: RPF scan returned no entries.");
                errorCode = 4;
                return null;
            }

            Console.WriteLine($"RPF scanned: {rpf.AllEntries.Count} entries");

            if (createModsCopy)
            {
                Console.WriteLine("Ensuring OPEN encryption...");
                RpfFile.EnsureValidEncryption(rpf, null, true);
                Console.WriteLine("Encryption converted to OPEN.");

                // EnsureValidEncryption can rewrite the archive in place.
                // Never keep reading entry offsets from the pre-conversion
                // scan: reopen the file and validate its new structure first.
                rpf = new RpfFile(modsRpf, modsRpf);
                rpf.ScanStructure(null, err => Console.Error.WriteLine(
                    $"RPF post-conversion scan warning: {err}"));
                if (rpf.AllEntries == null || rpf.AllEntries.Count == 0)
                {
                    Console.Error.WriteLine(
                        "ERROR: RPF post-conversion scan returned no entries.");
                    errorCode = 4;
                    return null;
                }
                Console.WriteLine(
                    $"RPF reopened: {rpf.AllEntries.Count} entries");
            }

            return rpf;
        }

        /// <summary>Find script_txds.rpf nested inside update.rpf.</summary>
        static RpfFile FindScriptTxdsRpf(RpfFile updateRpf)
        {
            // Path: x64/textures/script_txds.rpf (nested RPF inside update.rpf)
            if (updateRpf.Children == null) return null;

            foreach (var child in updateRpf.Children)
            {
                if (child.Name != null &&
                    child.Name.Equals("script_txds.rpf", StringComparison.OrdinalIgnoreCase))
                    return child;
            }

            // Search deeper — it might be nested inside another child
            foreach (var child in updateRpf.Children)
            {
                var found = FindScriptTxdsRpf(child);
                if (found != null) return found;
            }

            return null;
        }

        static RpfDirectoryEntry FindDirectory(
            RpfDirectoryEntry root, string name)
        {
            if (root == null) return null;
            if (root.Name != null &&
                root.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                return root;
            if (root.Directories == null) return null;
            foreach (var directory in root.Directories)
            {
                var found = FindDirectory(directory, name);
                if (found != null) return found;
            }
            return null;
        }

        /// <summary>
        /// Open the edition-specific archive that supplies globally streamed
        /// script textures. Enhanced moved these resources to update2.rpf's
        /// root textures directory; Legacy keeps them in nested script_txds.rpf.
        /// </summary>
        static RpfDirectoryEntry OpenPreviewTextureDirectory(
            string gtaPath, out RpfFile archive, out string label, out int errorCode)
        {
            bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                       || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
            string archiveName = isGen9 ? "update2.rpf" : "update.rpf";
            archive = OpenModsUpdateRpf(gtaPath, out errorCode, archiveName);
            label = isGen9 ? "update2.rpf/textures" : "script_txds.rpf";
            if (archive == null) return null;

            if (isGen9)
            {
                return FindDirectory(archive.Root, "textures");
            }

            return FindScriptTxdsRpf(archive)?.Root;
        }

        // ================================================================
        //  verify-ytd: Re-open script_txds.rpf and ensure every expected
        //  dictionary is actually present after injection.
        // ================================================================

        static int VerifyYtd(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-ytd <gta_path> <ytd_folder>");
                return 1;
            }

            string gtaPath = args[1];
            string ytdFolder = args[2];
            if (!Directory.Exists(ytdFolder))
            {
                Console.Error.WriteLine($"ERROR: Folder not found: {ytdFolder}");
                return 4;
            }

            string[] expected = Directory.GetFiles(ytdFolder, "*.ytd")
                .Select(Path.GetFileName)
                .ToArray();
            if (expected.Length == 0)
            {
                Console.Error.WriteLine("ERROR: No .ytd files found in folder.");
                return 4;
            }

            try
            {
                var target = OpenPreviewTextureDirectory(
                    gtaPath, out var rpf, out string label, out int err);
                if (rpf == null) return err;
                if (target == null)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Preview texture directory not found: {label}");
                    return 5;
                }

                var present = target.Files?
                    .OfType<RpfFileEntry>()
                    .Where(e => !string.IsNullOrEmpty(e.Name))
                    .Select(e => e.Name)
                    .ToHashSet(StringComparer.OrdinalIgnoreCase)
                    ?? new System.Collections.Generic.HashSet<string>(
                        StringComparer.OrdinalIgnoreCase);
                string[] missing = expected.Where(name => !present.Contains(name)).ToArray();
                if (missing.Length > 0)
                {
                    foreach (string name in missing)
                        Console.Error.WriteLine($"MISSING: {name}");
                    Console.Error.WriteLine(
                        $"ERROR: {missing.Length}/{expected.Length} preview dictionaries are missing.");
                    return 7;
                }

                Console.WriteLine(
                    $"Verified {expected.Length} preview dictionaries in {label}.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        // ================================================================
        //  inject-ytd: Add .ytd files into script_txds.rpf inside
        //  mods/update/update.rpf so they're available via
        //  REQUEST_STREAMED_TEXTURE_DICT.
        // ================================================================

        static int InjectYtd(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe inject-ytd <gta_path> <ytd_folder>");
                return 1;
            }

            string gtaPath = args[1];
            string ytdFolder = args[2];

            if (!Directory.Exists(ytdFolder))
            {
                Console.Error.WriteLine($"ERROR: Folder not found: {ytdFolder}");
                return 4;
            }

            string[] ytdFiles = Directory.GetFiles(ytdFolder, "*.ytd");
            if (ytdFiles.Length == 0)
            {
                Console.Error.WriteLine("ERROR: No .ytd files found in folder.");
                return 4;
            }

            try
            {
                var target = OpenPreviewTextureDirectory(
                    gtaPath, out var rpf, out string label, out int err);
                if (rpf == null) return err;
                if (target == null)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Preview texture directory not found: {label}");
                    return 5;
                }

                Console.WriteLine($"Found {label} ({target.Files?.Count ?? 0} files)");

                int injected = 0;
                foreach (string ytdPath in ytdFiles)
                {
                    string fileName = Path.GetFileName(ytdPath);
                    byte[] data = File.ReadAllBytes(ytdPath);

                    // Check if already exists — overwrite if so
                    var existing = target.Files?
                        .OfType<RpfFileEntry>()
                        .FirstOrDefault(e => e.Name != null &&
                            e.Name.Equals(fileName, StringComparison.OrdinalIgnoreCase));

                    if (existing != null)
                    {
                        RpfFile.CreateFile(existing.Parent, fileName, data, true);
                        Console.WriteLine($"  ~ {fileName} ({data.Length:N0} bytes, replaced)");
                    }
                    else
                    {
                        RpfFile.CreateFile(target, fileName, data, true);
                        Console.WriteLine($"  + {fileName} ({data.Length:N0} bytes)");
                    }
                    injected++;
                }

                Console.WriteLine($"Injected {injected} .ytd files into {label}.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        // ================================================================
        //  remove-ytd: Remove .ytd files matching a prefix from
        //  script_txds.rpf inside mods/update/update.rpf.
        // ================================================================

        static int RemoveYtd(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe remove-ytd <gta_path> <prefix>\n" +
                    "  Removes all .ytd entries whose name starts with <prefix>.");
                return 1;
            }

            string gtaPath = args[1];
            string prefix = args[2].ToLowerInvariant();

            try
            {
                var target = OpenPreviewTextureDirectory(
                    gtaPath, out var rpf, out string label, out int err);
                if (rpf == null) return err;
                if (target == null)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Preview texture directory not found: {label}");
                    return 5;
                }

                Console.WriteLine($"Found {label} ({target.Files?.Count ?? 0} files)");

                // Find matching entries
                var toRemove = target.Files?
                    .OfType<RpfFileEntry>()
                    .Where(e => e.Name != null &&
                        e.Name.ToLowerInvariant().StartsWith(prefix) &&
                        e.Name.ToLowerInvariant().EndsWith(".ytd"))
                    .ToList() ?? new System.Collections.Generic.List<RpfFileEntry>();

                if (toRemove.Count == 0)
                {
                    Console.WriteLine($"No .ytd files matching prefix '{prefix}' found.");
                    return 0;
                }

                foreach (var entry in toRemove)
                {
                    RpfFile.DeleteEntry(entry);
                    Console.WriteLine($"  - {entry.Name}");
                }

                Console.WriteLine($"Removed {toRemove.Count} .ytd files from {label}.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        // ================================================================
        //  defragment-copy: Compact a new copy while preserving the source
        // ================================================================

        static int DefragmentCopy(string[] args)
        {
            if (args.Length != 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe defragment-copy <gta_path> <input_rpf> " +
                    "<output_rpf> <report_json>");
                return 1;
            }

            string gtaPath = Path.GetFullPath(args[1]);
            string inputPath = Path.GetFullPath(args[2]);
            string outputPath = Path.GetFullPath(args[3]);
            string reportPath = Path.GetFullPath(args[4]);
            string stagingPath = null;
            string stagingReport = null;
            bool outputPublished = false;
            bool reportPublished = false;
            try
            {
                if (!Directory.Exists(gtaPath))
                    throw new DirectoryNotFoundException($"GTA V directory not found: {gtaPath}");
                if (!File.Exists(inputPath))
                    throw new FileNotFoundException("Input RPF was not found.", inputPath);
                if (!Path.GetExtension(inputPath).Equals(".rpf", StringComparison.OrdinalIgnoreCase) ||
                    !Path.GetExtension(outputPath).Equals(".rpf", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Input and output must use the .rpf extension.");
                if (inputPath.Equals(outputPath, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Defragmentation output must differ from input.");
                if (reportPath.Equals(inputPath, StringComparison.OrdinalIgnoreCase) ||
                    reportPath.Equals(outputPath, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Report path must differ from archive paths.");
                if (File.Exists(outputPath) || Directory.Exists(outputPath))
                    throw new IOException($"Output already exists: {outputPath}");
                if (File.Exists(reportPath) || Directory.Exists(reportPath))
                    throw new IOException($"Report already exists: {reportPath}");

                string outputDirectory = Path.GetDirectoryName(outputPath);
                string reportDirectory = Path.GetDirectoryName(reportPath);
                if (string.IsNullOrEmpty(outputDirectory) || string.IsNullOrEmpty(reportDirectory))
                    throw new InvalidDataException("Output and report require parent directories.");
                Directory.CreateDirectory(outputDirectory);
                Directory.CreateDirectory(reportDirectory);
                stagingPath = Path.Combine(
                    outputDirectory,
                    $".{Path.GetFileNameWithoutExtension(outputPath)}.allin1-defrag-" +
                    $"{Guid.NewGuid():N}.rpf");
                stagingReport = Path.Combine(
                    reportDirectory,
                    $".{Path.GetFileName(reportPath)}.allin1-{Guid.NewGuid():N}.tmp");

                long beforeSize = new FileInfo(inputPath).Length;
                string beforeHash = Sha256File(inputPath);
                File.Copy(inputPath, stagingPath, false);
                string copiedHash = Sha256File(stagingPath);
                if (!beforeHash.Equals(copiedHash, StringComparison.Ordinal))
                    throw new InvalidDataException("Copied RPF does not match the input SHA-256.");

                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                var warnings = new List<string>();
                try
                {
                    GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                }
                catch (Exception ex)
                {
                    warnings.Add("Encryption keys could not be loaded: " + ex.Message);
                }

                var archive = new RpfFile(stagingPath, Path.GetFileName(outputPath));
                archive.ScanStructure(null, warning => warnings.Add(warning));
                if (archive.AllEntries == null || archive.Root == null)
                    throw new InvalidDataException("RPF scan did not produce a complete tree.");
                int beforeEntries = archive.AllEntries.Count;
                long predictedSize = archive.GetDefragmentedFileSize(true);
                RpfFile.Defragment(archive, null, true);

                var verified = new RpfFile(stagingPath, Path.GetFileName(outputPath));
                verified.ScanStructure(null, warning => warnings.Add(warning));
                if (verified.AllEntries == null || verified.Root == null)
                    throw new InvalidDataException("Defragmented RPF did not rescan.");
                long afterSize = new FileInfo(stagingPath).Length;
                if (afterSize != predictedSize)
                    throw new InvalidDataException(
                        $"Defragmented size mismatch: expected {predictedSize:N0}, got {afterSize:N0}.");
                if (afterSize > beforeSize)
                    throw new InvalidDataException("Defragmentation increased the archive size.");
                string afterHash = Sha256File(stagingPath);
                string inputHashAfter = Sha256File(inputPath);
                if (!beforeHash.Equals(inputHashAfter, StringComparison.Ordinal))
                    throw new IOException("Input RPF changed during defragmentation.");

                var report = new Dictionary<string, object>
                {
                    { "schema_version", 1 },
                    { "operation", "rpf_defragment_copy" },
                    { "source", inputPath },
                    { "source_size", beforeSize },
                    { "source_sha256", beforeHash },
                    { "output", outputPath },
                    { "output_size", afterSize },
                    { "output_sha256", afterHash },
                    { "predicted_output_size", predictedSize },
                    { "bytes_saved", beforeSize - afterSize },
                    { "recursive", true },
                    { "edition", isGen9 ? "Enhanced" : "Legacy" },
                    { "entries_before", beforeEntries },
                    { "entries_after", verified.AllEntries.Count },
                    { "warnings", warnings },
                    { "source_unchanged", true },
                };
                File.WriteAllText(
                    stagingReport,
                    JsonSerializer.Serialize(report, new JsonSerializerOptions
                    {
                        WriteIndented = true,
                    }) + Environment.NewLine,
                    new UTF8Encoding(false));
                File.Move(stagingPath, outputPath, false);
                outputPublished = true;
                File.Move(stagingReport, reportPath, false);
                reportPublished = true;
                Console.WriteLine(
                    $"Defragmented verified copy: {beforeSize:N0} -> {afterSize:N0} bytes " +
                    $"({beforeSize - afterSize:N0} saved)");
                return 0;
            }
            catch (Exception ex)
            {
                try
                {
                    if (!string.IsNullOrEmpty(stagingPath) && File.Exists(stagingPath))
                        File.Delete(stagingPath);
                    if (!string.IsNullOrEmpty(stagingReport) && File.Exists(stagingReport))
                        File.Delete(stagingReport);
                    if (outputPublished && !reportPublished && File.Exists(outputPath))
                        File.Delete(outputPath);
                }
                catch
                {
                    // Preserve the original error; Python performs an additional cleanup pass.
                }
                Console.Error.WriteLine($"ERROR: RPF defragment-copy failed: {ex.Message}");
                return 99;
            }
        }

        // ================================================================
        //  build-dlc: Pack a loose DLC folder into a dlc.rpf archive
        //
        //  Recursively adds all files/directories from the loose folder
        //  into a flat RPF archive with OPEN encryption.
        //
        //  Optional --embed-rpf <src_folder> <dest_path>:
        //    First builds a standalone RPF from <src_folder>, then embeds
        //    its raw bytes at <dest_path> inside the outer DLC RPF.
        //    Example: --embed-rpf /tmp/ytds x64/textures/textures.rpf
        //    This avoids CodeWalker's buggy in-place nested RPF creation.
        // ================================================================

        static int BuildDlc(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-dlc <loose_folder> <output_rpf> " +
                    "[--embed-rpf <src_folder> <dest_path>] [--gta-path <path>]");
                return 1;
            }

            string looseFolder = args[1];
            string outputRpf = args[2];

            // Parse optional --embed-rpf flag
            string embedSrcFolder = null;
            string embedDestPath = null;
            string gtaKeysPath = null;

            for (int i = 3; i < args.Length; i++)
            {
                if (args[i] == "--embed-rpf" && i + 2 < args.Length)
                {
                    embedSrcFolder = args[i + 1];
                    embedDestPath = args[i + 2];
                    i += 2;
                }
                else if (args[i] == "--gta-path" && i + 1 < args.Length)
                {
                    gtaKeysPath = args[i + 1];
                    i += 1;
                }
            }

            if (!Directory.Exists(looseFolder))
            {
                Console.Error.WriteLine($"ERROR: Folder not found: {looseFolder}");
                return 4;
            }

            if (embedSrcFolder != null && !Directory.Exists(embedSrcFolder))
            {
                Console.Error.WriteLine($"ERROR: Embed source folder not found: {embedSrcFolder}");
                return 4;
            }

            try
            {
                if (gtaKeysPath != null)
                {
                    bool isGen9 = File.Exists(
                                      Path.Combine(gtaKeysPath, "GTA5_Enhanced.exe"))
                               || File.Exists(Path.Combine(gtaKeysPath, "eboot.bin"));
                    GTA5Keys.LoadFromPath(gtaKeysPath, isGen9, null);
                    Console.WriteLine("Loaded GTA encryption keys for nested RPFs.");
                }
                byte[] innerRpfBytes = null;

                // Phase 1: Build inner RPF as standalone file if requested
                if (embedSrcFolder != null)
                {
                    Console.WriteLine($"Building inner RPF from: {embedSrcFolder}");

                    string tempDir = Path.GetDirectoryName(outputRpf);
                    if (string.IsNullOrEmpty(tempDir)) tempDir = ".";
                    string tempInnerPath = Path.Combine(tempDir, "_inner_temp.rpf");

                    // Clean up any previous temp file
                    if (File.Exists(tempInnerPath))
                        File.Delete(tempInnerPath);

                    var innerRpf = RpfFile.CreateNew(tempDir, "_inner_temp.rpf",
                        RpfEncryption.OPEN);

                    int innerCount = AddDirectoryContents(innerRpf.Root, embedSrcFolder);
                    Console.WriteLine($"Inner RPF: {innerCount} files packed.");

                    // Read the finished RPF bytes
                    innerRpfBytes = File.ReadAllBytes(tempInnerPath);
                    Console.WriteLine($"Inner RPF size: {innerRpfBytes.Length:N0} bytes");

                    // Clean up temp file
                    File.Delete(tempInnerPath);
                }

                // Phase 2: Build outer dlc.rpf
                Console.WriteLine($"Building dlc.rpf from: {looseFolder}");
                Console.WriteLine($"Output: {outputRpf}");

                string outputDir = Path.GetDirectoryName(outputRpf);
                if (!string.IsNullOrEmpty(outputDir))
                    Directory.CreateDirectory(outputDir);

                if (File.Exists(outputRpf))
                    File.Delete(outputRpf);

                var rpf = RpfFile.CreateNew(outputDir ?? ".", Path.GetFileName(outputRpf),
                    RpfEncryption.OPEN);
                Console.WriteLine("Created dlc.rpf.");

                int fileCount = AddDirectoryContents(rpf.Root, looseFolder);

                // Phase 3: Embed inner RPF at the specified path
                if (innerRpfBytes != null && embedDestPath != null)
                {
                    // Navigate/create directory structure for dest path
                    // e.g. "x64/textures/textures.rpf"
                    string[] parts = embedDestPath.Replace('\\', '/').Split('/');
                    RpfDirectoryEntry currentDir = rpf.Root;

                    // Create intermediate directories (all parts except last)
                    for (int i = 0; i < parts.Length - 1; i++)
                    {
                        string dirName = parts[i];
                        // Check if directory already exists
                        var existingDir = currentDir.Directories?
                            .FirstOrDefault(d => d.Name.Equals(dirName,
                                StringComparison.OrdinalIgnoreCase));
                        if (existingDir != null)
                        {
                            currentDir = existingDir;
                        }
                        else
                        {
                            currentDir = RpfFile.CreateDirectory(currentDir, dirName);
                        }
                    }

                    string innerFileName = parts[parts.Length - 1];
                    RpfFile.CreateFile(currentDir, innerFileName, innerRpfBytes, true);
                    Console.WriteLine($"  + {embedDestPath} ({innerRpfBytes.Length:N0} bytes, nested RPF)");
                    fileCount++;
                }

                Console.WriteLine($"dlc.rpf built successfully ({fileCount} files).");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Failed to build dlc.rpf: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 7;
            }
        }

        static int AddDirectoryContents(RpfDirectoryEntry parentEntry, string sourceDir)
        {
            int count = 0;

            foreach (string filePath in Directory.GetFiles(sourceDir)
                .OrderBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase))
            {
                string fileName = Path.GetFileName(filePath);
                byte[] data = File.ReadAllBytes(filePath);
                RpfFile.CreateFile(parentEntry, fileName, data, true);
                Console.WriteLine($"  + {fileName} ({data.Length:N0} bytes)");
                count++;
            }

            foreach (string subDir in Directory.GetDirectories(sourceDir)
                .OrderBy(path => Path.GetFileName(path), StringComparer.OrdinalIgnoreCase))
            {
                string dirName = Path.GetFileName(subDir);
                var subEntry = RpfFile.CreateDirectory(parentEntry, dirName);
                Console.WriteLine($"  / {dirName}/");
                count += AddDirectoryContents(subEntry, subDir);
            }

            return count;
        }

        // ================================================================
        //  verify-dlc: Ensure metadata, nested RPF and every expected YTD
        //  can be read back before the archive is placed in the game.
        // ================================================================

        static int VerifyDlc(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-dlc <dlc_rpf> <ytd_folder>");
                return 1;
            }

            string dlcPath = args[1];
            string ytdFolder = args[2];
            if (!File.Exists(dlcPath) || !Directory.Exists(ytdFolder))
            {
                Console.Error.WriteLine("ERROR: DLC archive or YTD folder not found.");
                return 4;
            }

            try
            {
                var rpf = new RpfFile(dlcPath, dlcPath);
                rpf.ScanStructure(null,
                    err => Console.Error.WriteLine($"RPF scan warning: {err}"));
                var texturesEntry = FindFileRecursive(rpf, "textures.rpf");
                if (FindFileRecursive(rpf, "content.xml") == null ||
                    FindFileRecursive(rpf, "setup2.xml") == null ||
                    texturesEntry == null)
                {
                    Console.Error.WriteLine(
                        "ERROR: DLC is missing content.xml, setup2.xml, or textures.rpf.");
                    return 5;
                }

                string[] expected = Directory.GetFiles(ytdFolder, "*.ytd")
                    .Select(Path.GetFileName)
                    .ToArray();
                if (expected.Length == 0)
                {
                    Console.Error.WriteLine("ERROR: No expected YTD files were supplied.");
                    return 4;
                }

                // CodeWalker's automatic child-RPF discovery depends on the
                // archive's surrounding directory. Extract and scan the
                // embedded file explicitly so verification is path-neutral
                // and proves the bytes GTA will actually mount.
                string tempInner = Path.Combine(
                    Path.GetTempPath(), $"allin1-verify-{Guid.NewGuid():N}.rpf");
                try
                {
                    byte[] innerBytes = texturesEntry.File.ExtractFile(texturesEntry);
                    if (innerBytes == null || innerBytes.Length == 0)
                    {
                        Console.Error.WriteLine("ERROR: Embedded textures.rpf is empty.");
                        return 5;
                    }
                    File.WriteAllBytes(tempInner, innerBytes);
                    var innerRpf = new RpfFile(tempInner, tempInner);
                    innerRpf.ScanStructure(null,
                        err => Console.Error.WriteLine($"Nested RPF scan warning: {err}"));
                    var missing = expected
                        .Where(name => FindFileRecursive(innerRpf, name) == null)
                        .ToArray();
                    if (missing.Length > 0)
                    {
                        Console.Error.WriteLine(
                            "ERROR: Missing dictionaries: " + string.Join(", ", missing));
                        return 5;
                    }
                }
                finally
                {
                    if (File.Exists(tempInner)) File.Delete(tempInner);
                }

                Console.WriteLine(
                    $"Verified preview DLC: {expected.Length} texture dictionaries present.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: DLC verification failed: {ex.Message}");
                return 7;
            }
        }

        // ================================================================
        //  convert-gen9: Convert .ytd files from Legacy to Enhanced format
        //
        //  GTA V Enhanced (gen9) uses different resource file versions.
        //  YTD files built by YTDToolio are in Legacy format (version 13).
        //  Enhanced requires version 5.  This command loads each .ytd and
        //  re-saves it via CodeWalker with RpfManager.IsGen9 = true,
        //  producing Enhanced-compatible files.
        // ================================================================

        static int ConvertGen9(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe convert-gen9 <ytd_folder>\n" +
                    "  Converts all .ytd files in the folder from Legacy to Enhanced format.");
                return 1;
            }

            string ytdFolder = args[1];

            if (!Directory.Exists(ytdFolder))
            {
                Console.Error.WriteLine($"ERROR: Folder not found: {ytdFolder}");
                return 4;
            }

            string[] ytdFiles = Directory.GetFiles(ytdFolder, "*.ytd");
            if (ytdFiles.Length == 0)
            {
                Console.Error.WriteLine("ERROR: No .ytd files found in folder.");
                return 4;
            }

            try
            {
                // Enable gen9 mode so Save() produces Enhanced-format files
                var prevGen9 = RpfManager.IsGen9;
                RpfManager.IsGen9 = true;

                int converted = 0;
                int skipped = 0;

                foreach (string ytdPath in ytdFiles)
                {
                    string fileName = Path.GetFileName(ytdPath);
                    byte[] data = File.ReadAllBytes(ytdPath);

                    byte[] result = Gen9Converter.TryConvert(
                        data, ".ytd",
                        msg => Console.WriteLine($"  {msg}"),
                        fileName, false, out bool wasConverted);

                    if (wasConverted && result != null)
                    {
                        File.WriteAllBytes(ytdPath, result);
                        Console.WriteLine($"  + {fileName} converted ({data.Length:N0} -> {result.Length:N0} bytes)");
                        converted++;
                    }
                    else
                    {
                        Console.WriteLine($"  ~ {fileName} already gen9, skipped");
                        skipped++;
                    }
                }

                RpfManager.IsGen9 = prevGen9;

                Console.WriteLine($"Converted {converted} .ytd files to gen9 format ({skipped} already up to date).");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        // ================================================================
        //  inspect: Dump RPF structure and XML file contents
        // ================================================================

        static int InspectRpf(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine("Usage: RpfPatcher.exe inspect <gta_path> <rpf_path>");
                Console.Error.WriteLine("  gta_path: GTA V root (needed for encryption keys)");
                Console.Error.WriteLine("  rpf_path: path to the .rpf file to inspect");
                return 1;
            }

            string gtaPath = args[1];
            string rpfPath = args[2];

            if (!File.Exists(rpfPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {rpfPath}");
                return 4;
            }

            try
            {
                // Try to load encryption keys (optional — OPEN RPFs don't need them)
                try
                {
                    bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                               || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                    GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                }
                catch
                {
                    Console.WriteLine("Warning: Could not load encryption keys. Encrypted RPFs may fail.");
                }

                // Open and scan RPF
                var rpf = new RpfFile(rpfPath, rpfPath);
                rpf.ScanStructure(null, err => { });

                Console.WriteLine($"=== RPF: {rpfPath} ===");
                Console.WriteLine($"Version: {rpf.Version}");
                Console.WriteLine($"Encryption: {rpf.Encryption}");
                Console.WriteLine($"Entries: {rpf.AllEntries?.Count ?? 0}");
                Console.WriteLine();

                // Print file tree
                Console.WriteLine("--- File Tree ---");
                PrintTree(rpf, "", rpf.Root);
                Console.WriteLine();

                // Extract and print XML files, plus MLO entity-set names from
                // YTYP resources. The latter is useful when auditing native
                // interiors: ACTIVATE_INTERIOR_ENTITY_SET requires the exact
                // name stored in the archetype rather than the drawable name.
                if (rpf.AllEntries != null)
                {
                    var assetNamesByHash = rpf.AllEntries
                        .OfType<RpfFileEntry>()
                        .Where(file => !string.IsNullOrEmpty(file.Name))
                        .Select(file => Path.GetFileNameWithoutExtension(file.Name)
                            .ToLowerInvariant())
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .GroupBy(name => JenkHash.GenHash(name))
                        .ToDictionary(group => group.Key, group => group.First());
                    foreach (var entry in rpf.AllEntries.OfType<RpfFileEntry>())
                    {
                        if (entry.Name == null) continue;
                        string lower = entry.Name.ToLowerInvariant();
                        if (lower.EndsWith(".xml") || lower.EndsWith(".meta"))
                        {
                            Console.WriteLine($"--- {entry.Path} ---");
                            try
                            {
                                byte[] data = entry.File.ExtractFile(entry);
                                if (data != null && data.Length > 0)
                                {
                                    string text = Encoding.UTF8.GetString(data).TrimStart('\uFEFF');
                                    Console.WriteLine(text);
                                }
                                else
                                {
                                    Console.WriteLine("(empty)");
                                }
                            }
                            catch (Exception ex)
                            {
                                Console.WriteLine($"(extract failed: {ex.Message})");
                            }
                            Console.WriteLine();
                        }
                        else if (lower.EndsWith(".ytyp"))
                        {
                            Console.WriteLine($"--- MLO entity sets: {entry.Path} ---");
                            try
                            {
                                byte[] data = entry.File.ExtractFile(entry);
                                var ytyp = new YtypFile(entry);
                                ytyp.Load(data, entry);
                                bool foundMlo = false;
                                foreach (var mlo in (ytyp.AllArchetypes ?? Array.Empty<Archetype>())
                                    .OfType<MloArchetype>())
                                {
                                    foundMlo = true;
                                    Console.WriteLine($"MLO {mlo.Name}");
                                    foreach (var set in mlo.entitySets ?? Array.Empty<MCMloEntitySet>())
                                    {
                                        Console.WriteLine($"  {set.Name} ({set.Entities?.Length ?? 0} entities)");
                                        foreach (var entity in set.Entities ?? Array.Empty<MCEntityDef>())
                                        {
                                            var position = entity.Data.position;
                                            uint archetypeHash = entity.Data.archetypeName.Hash;
                                            string archetypeName = assetNamesByHash.TryGetValue(
                                                archetypeHash, out string resolvedName)
                                                ? resolvedName
                                                : archetypeHash.ToString();
                                            Console.WriteLine(
                                                $"    {archetypeName} " +
                                                $"at ({position.X:F3}, {position.Y:F3}, {position.Z:F3})");
                                        }
                                    }
                                }
                                if (!foundMlo)
                                    Console.WriteLine("(no MLO archetypes)");
                            }
                            catch (Exception ex)
                            {
                                Console.WriteLine($"(YTYP parse failed: {ex.Message})");
                            }
                            Console.WriteLine();
                        }
                        else if (lower.EndsWith(".ymap"))
                        {
                            Console.WriteLine($"--- MLO instances: {entry.Path} ---");
                            try
                            {
                                byte[] data = entry.File.ExtractFile(entry);
                                var ymap = new YmapFile(entry);
                                ymap.Load(data, entry);
                                bool foundMlo = false;
                                foreach (var entity in ymap.AllEntities ?? Array.Empty<YmapEntityDef>())
                                {
                                    if (!entity.IsMlo || entity.MloInstance == null) continue;
                                    foundMlo = true;
                                    Console.WriteLine(
                                        $"MLO {entity.CEntityDef.archetypeName} at " +
                                        $"({entity.Position.X:F3}, {entity.Position.Y:F3}, {entity.Position.Z:F3})");
                                    foreach (var set in entity.MloInstance.defaultEntitySets
                                        ?? Array.Empty<MetaHash>())
                                        Console.WriteLine($"  default {set.Hash}");
                                }
                                if (!foundMlo)
                                    Console.WriteLine("(no MLO instances)");
                            }
                            catch (Exception ex)
                            {
                                Console.WriteLine($"(YMAP parse failed: {ex.Message})");
                            }
                            Console.WriteLine();
                        }
                    }
                }

                // Recurse into child RPFs
                if (rpf.Children != null)
                {
                    foreach (var child in rpf.Children)
                    {
                        Console.WriteLine($"\n=== Nested RPF: {child.Name} ===");
                        Console.WriteLine($"Version: {child.Version}");
                        Console.WriteLine($"Encryption: {child.Encryption}");
                        Console.WriteLine($"Entries: {child.AllEntries?.Count ?? 0}");
                        Console.WriteLine();
                        Console.WriteLine("--- File Tree ---");
                        PrintTree(child, "", child.Root);
                        Console.WriteLine();

                        if (child.AllEntries != null)
                        {
                            foreach (var entry in child.AllEntries.OfType<RpfFileEntry>())
                            {
                                if (entry.Name == null) continue;
                                string lower = entry.Name.ToLowerInvariant();
                                if (lower.EndsWith(".xml") || lower.EndsWith(".meta"))
                                {
                                    Console.WriteLine($"--- {entry.Path} ---");
                                    try
                                    {
                                        byte[] data = entry.File.ExtractFile(entry);
                                        if (data != null && data.Length > 0)
                                        {
                                            string text = Encoding.UTF8.GetString(data).TrimStart('\uFEFF');
                                            Console.WriteLine(text);
                                        }
                                        else
                                        {
                                            Console.WriteLine("(empty)");
                                        }
                                    }
                                    catch (Exception ex)
                                    {
                                        Console.WriteLine($"(extract failed: {ex.Message})");
                                    }
                                    Console.WriteLine();
                                }
                            }
                        }
                    }
                }

                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        static void PrintTree(RpfFile rpf, string indent, RpfDirectoryEntry dir)
        {
            if (dir == null) return;

            if (dir.Directories != null)
            {
                foreach (var sub in dir.Directories)
                {
                    Console.WriteLine($"{indent}{sub.Name}/");
                    PrintTree(rpf, indent + "  ", sub);
                }
            }

            if (dir.Files != null)
            {
                foreach (var file in dir.Files)
                {
                    string size = file is RpfResourceFileEntry res
                        ? $"{res.FileSize:N0}b (res v{res.Version})"
                        : file is RpfBinaryFileEntry bin
                            ? $"{bin.FileUncompressedSize:N0}b"
                            : "?b";
                    Console.WriteLine($"{indent}{file.Name}  [{size}]");
                }
            }
        }

        // Structured, read-only inventory used by the ALLIN1 desktop RPF
        // explorer.  The older `inspect` command remains intentionally human
        // readable; this command is a stable machine contract and never writes
        // to the archive it scans.
        static int IndexRpfJson(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe index-json <gta_path> <rpf_path> <output_json>");
                return 1;
            }

            string gtaPath = Path.GetFullPath(args[1]);
            string rpfPath = Path.GetFullPath(args[2]);
            string outputPath = Path.GetFullPath(args[3]);
            if (!File.Exists(rpfPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {rpfPath}");
                return 4;
            }

            try
            {
                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                var warnings = new List<string>();
                try
                {
                    GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                }
                catch (Exception ex)
                {
                    warnings.Add("Encryption keys could not be loaded: " + ex.Message);
                }

                // A filesystem path contains a drive-colon, which CodeWalker's
                // nested-RPF safety check correctly rejects as a virtual path.
                // Keep the physical FilePath while using only the archive name
                // for the in-archive hierarchy so child RPFs are discovered.
                var rpf = new RpfFile(rpfPath, Path.GetFileName(rpfPath));
                rpf.ScanStructure(null, warning => warnings.Add(warning));
                if (rpf.AllEntries == null || rpf.AllEntries.Count == 0)
                    throw new InvalidDataException("RPF scan returned no entries.");

                var archives = new List<Dictionary<string, object>>();
                var entries = new List<Dictionary<string, object>>();
                IndexArchive(rpf, string.Empty, archives, entries, warnings);
                var document = new Dictionary<string, object>
                {
                    { "schema_version", 1 },
                    { "source", rpfPath },
                    { "edition", isGen9 ? "Enhanced" : "Legacy" },
                    { "archive_size", new FileInfo(rpfPath).Length },
                    { "archives", archives },
                    { "entries", entries },
                    { "warnings", warnings },
                };
                string parent = Path.GetDirectoryName(outputPath);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllText(
                    outputPath,
                    JsonSerializer.Serialize(document, new JsonSerializerOptions
                    {
                        WriteIndented = true,
                    }) + Environment.NewLine,
                    new UTF8Encoding(false));
                Console.WriteLine(
                    $"Indexed {entries.Count:N0} entries across {archives.Count:N0} RPF archive(s): {outputPath}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: RPF indexing failed: {ex.Message}");
                return 99;
            }
        }

        static void IndexArchive(
            RpfFile archive, string virtualPath,
            List<Dictionary<string, object>> archives,
            List<Dictionary<string, object>> entries,
            List<string> warnings)
        {
            archives.Add(new Dictionary<string, object>
            {
                { "path", virtualPath },
                { "name", archive.Name ?? Path.GetFileName(archive.FilePath) },
                { "version", archive.Version },
                { "encryption", archive.Encryption.ToString() },
                { "size", archive.FileSize },
                { "entry_count", archive.AllEntries?.Count ?? 0 },
            });

            foreach (RpfEntry entry in archive.AllEntries ?? new List<RpfEntry>())
            {
                if (entry == archive.Root) continue;
                string relative = RelativeRpfEntryPath(archive, entry)
                    .Replace('\\', '/').Trim('/');
                if (string.IsNullOrWhiteSpace(relative)) continue;
                var item = new Dictionary<string, object>
                {
                    { "id", virtualPath + "::" + relative },
                    { "archive_path", virtualPath },
                    { "path", relative },
                    { "name", entry.Name ?? Path.GetFileName(relative) },
                    { "name_hash", entry.NameHash },
                    { "short_name_hash", entry.ShortNameHash },
                };
                if (entry is RpfDirectoryEntry directory)
                {
                    item["kind"] = "directory";
                    item["size"] = 0L;
                    item["stored_size"] = 0L;
                    item["child_count"] =
                        (directory.Directories?.Count ?? 0) +
                        (directory.Files?.Count ?? 0);
                }
                else if (entry is RpfResourceFileEntry resource)
                {
                    item["kind"] = "resource";
                    item["size"] = (long)resource.SystemSize + resource.GraphicsSize;
                    item["stored_size"] = resource.FileSize;
                    item["offset"] = (long)resource.FileOffset * 512L;
                    item["encrypted"] = resource.IsEncrypted;
                    item["resource_version"] = resource.Version;
                    item["system_size"] = resource.SystemSize;
                    item["graphics_size"] = resource.GraphicsSize;
                    item["system_flags"] = $"0x{resource.SystemFlags.Value:X8}";
                    item["graphics_flags"] = $"0x{resource.GraphicsFlags.Value:X8}";
                }
                else if (entry is RpfBinaryFileEntry binary)
                {
                    item["kind"] = relative.EndsWith(
                        ".rpf", StringComparison.OrdinalIgnoreCase)
                        ? "archive" : "binary";
                    item["size"] = binary.FileUncompressedSize > 0
                        ? binary.FileUncompressedSize : binary.FileSize;
                    item["stored_size"] = binary.FileSize;
                    item["offset"] = (long)binary.FileOffset * 512L;
                    item["encrypted"] = binary.IsEncrypted;
                    item["compressed"] = binary.FileSize > 0
                        && binary.FileUncompressedSize > binary.FileSize;
                }
                entries.Add(item);
            }

            foreach (RpfFile child in archive.Children ?? new List<RpfFile>())
            {
                string childEntry = child.ParentFileEntry == null
                    ? child.Name
                    : RelativeRpfEntryPath(archive, child.ParentFileEntry)
                        .Replace('\\', '/').Trim('/');
                string childVirtual = string.IsNullOrEmpty(virtualPath)
                    ? childEntry
                    : virtualPath + "!" + childEntry;
                try
                {
                    IndexArchive(child, childVirtual, archives, entries, warnings);
                }
                catch (Exception ex)
                {
                    warnings.Add($"Nested RPF could not be indexed ({childVirtual}): {ex.Message}");
                }
            }
        }

        static RpfFile FindVirtualArchive(
            RpfFile root, string requested, string current = "")
        {
            string normalized = (requested ?? string.Empty)
                .Replace('\\', '/').Trim('/');
            if (string.Equals(normalized, current,
                    StringComparison.OrdinalIgnoreCase)) return root;
            foreach (RpfFile child in root.Children ?? new List<RpfFile>())
            {
                string childEntry = child.ParentFileEntry == null
                    ? child.Name
                    : RelativeRpfEntryPath(root, child.ParentFileEntry)
                        .Replace('\\', '/').Trim('/');
                string childVirtual = string.IsNullOrEmpty(current)
                    ? childEntry
                    : current + "!" + childEntry;
                RpfFile found = FindVirtualArchive(child, normalized, childVirtual);
                if (found != null) return found;
            }
            return null;
        }

        static int ExtractVirtualEntry(string[] args)
        {
            if (args.Length < 6)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe extract-virtual-entry <gta_path> <rpf_path> <archive_path> <entry_path> <output>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string rpfPath = Path.GetFullPath(args[2]);
            string archivePath = args[3];
            string entryPath = args[4];
            string outputPath = Path.GetFullPath(args[5]);
            if (!File.Exists(rpfPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {rpfPath}");
                return 4;
            }
            try
            {
                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                var root = new RpfFile(rpfPath, Path.GetFileName(rpfPath));
                root.ScanStructure(null,
                    warning => Console.Error.WriteLine("RPF scan warning: " + warning));
                RpfFile archive = FindVirtualArchive(root, archivePath);
                if (archive == null)
                {
                    Console.Error.WriteLine($"ERROR: Nested archive not found: {archivePath}");
                    return 5;
                }
                RpfFileEntry entry = FindExactFileEntry(archive, entryPath);
                if (entry == null)
                {
                    Console.Error.WriteLine($"ERROR: Entry not found: {entryPath}");
                    return 5;
                }
                byte[] data = entry.File.ExtractFile(entry);
                if (data == null || data.Length == 0)
                {
                    Console.Error.WriteLine("ERROR: Extracted entry was empty.");
                    return 5;
                }
                if (entry is RpfResourceFileEntry resource)
                {
                    data = ResourceBuilder.AddResourceHeader(
                        resource, ResourceBuilder.Compress(data));
                }
                string parent = Path.GetDirectoryName(outputPath);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllBytes(outputPath, data);
                Console.WriteLine(
                    $"Extracted {archivePath}::{entryPath} ({data.Length:N0} bytes) to {outputPath}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: RPF extraction failed: {ex.Message}");
                return 99;
            }
        }

        // Extract exact entries from root or nested virtual archives while scanning
        // the outer RPF only once. Each non-empty TSV line is:
        // archive/path<TAB>entry/path<TAB>destination/path. An empty first field
        // addresses the root archive.
        static int ExtractVirtualEntries(string[] args)
        {
            if (args.Length < 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe extract-virtual-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>");
                return 1;
            }

            string gtaPath = Path.GetFullPath(args[1]);
            string rpfPath = Path.GetFullPath(args[2]);
            string manifestPath = Path.GetFullPath(args[3]);
            string outputRoot = Path.GetFullPath(args[4]);
            if (!File.Exists(rpfPath) || !File.Exists(manifestPath))
            {
                Console.Error.WriteLine("ERROR: Source RPF or extraction manifest not found.");
                return 4;
            }

            try
            {
                var requests = File.ReadAllLines(manifestPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line)
                        && !line.TrimStart().StartsWith("#"))
                    .Select(line => line.Split(new[] { '\t' }, 3))
                    .ToArray();
                if (requests.Length == 0 || requests.Any(parts => parts.Length != 3))
                {
                    Console.Error.WriteLine(
                        "ERROR: Virtual extraction manifest is empty or malformed.");
                    return 4;
                }

                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                var root = new RpfFile(rpfPath, Path.GetFileName(rpfPath));
                root.ScanStructure(null,
                    warning => Console.Error.WriteLine("RPF scan warning: " + warning));

                Directory.CreateDirectory(outputRoot);
                string outputPrefix = outputRoot.TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    + Path.DirectorySeparatorChar;
                var destinations = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var archiveCache = new Dictionary<string, RpfFile>(
                    StringComparer.OrdinalIgnoreCase)
                {
                    { string.Empty, root },
                };
                int extracted = 0;

                foreach (string[] request in requests)
                {
                    string archivePath = request[0].Replace('\\', '/').Trim('/');
                    string entryPath = request[1].Replace('\\', '/').Trim('/');
                    string relativeDestination = request[2]
                        .Replace('/', Path.DirectorySeparatorChar)
                        .TrimStart(Path.DirectorySeparatorChar);
                    if (string.IsNullOrEmpty(entryPath)
                        || string.IsNullOrEmpty(relativeDestination))
                    {
                        Console.Error.WriteLine("ERROR: Empty entry or destination path.");
                        return 4;
                    }
                    string destination = Path.GetFullPath(
                        Path.Combine(outputRoot, relativeDestination));
                    if (!destination.StartsWith(
                            outputPrefix, StringComparison.OrdinalIgnoreCase)
                        || !destinations.Add(destination))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Unsafe or duplicate destination: {request[2]}");
                        return 4;
                    }

                    if (!archiveCache.TryGetValue(archivePath, out RpfFile archive))
                    {
                        archive = FindVirtualArchive(root, archivePath);
                        if (archive == null)
                        {
                            Console.Error.WriteLine(
                                $"ERROR: Nested archive not found: {archivePath}");
                            return 5;
                        }
                        archiveCache[archivePath] = archive;
                    }
                    RpfFileEntry entry = FindExactFileEntry(archive, entryPath);
                    if (entry == null)
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Entry not found: {archivePath}::{entryPath}");
                        return 5;
                    }
                    byte[] data = entry.File.ExtractFile(entry);
                    if (data == null)
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Entry could not be extracted: {archivePath}::{entryPath}");
                        return 5;
                    }
                    if (entry is RpfResourceFileEntry resource)
                    {
                        data = ResourceBuilder.AddResourceHeader(
                            resource, ResourceBuilder.Compress(data));
                    }
                    string parent = Path.GetDirectoryName(destination);
                    if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                    File.WriteAllBytes(destination, data);
                    extracted++;
                    Console.WriteLine(
                        $"Extracted {archivePath}::{entryPath} -> {request[2]} ({data.Length:N0} bytes)");
                }

                Console.WriteLine(
                    $"Extracted {extracted:N0} virtual entries from {rpfPath}.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Virtual RPF extraction failed: {ex.Message}");
                return 99;
            }
        }

        // Converts native RAGE resources to the CodeWalker XML representation
        // and writes referenced textures/audio beside it. This is deliberately
        // read-only and is shared by the package viewer and RPF explorer.
        static int ExportAssetXml(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe asset-xml <input_asset> <output_xml> <asset_folder> [legacy|gen9] [gta_path]");
                return 1;
            }
            string input = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            string assetFolder = Path.GetFullPath(args[3]);
            bool gen9 = args.Length >= 5 && args[4].Equals(
                "gen9", StringComparison.OrdinalIgnoreCase);
            string gtaPath = args.Length >= 6
                ? Path.GetFullPath(args[5]) : null;
            if (!File.Exists(input))
            {
                Console.Error.WriteLine($"ERROR: File not found: {input}");
                return 4;
            }
            bool previous = RpfManager.IsGen9;
            try
            {
                RpfManager.IsGen9 = gen9;
                byte[] data = File.ReadAllBytes(input);
                string suffix = Path.GetExtension(input).ToLowerInvariant();
                string xml;
                switch (suffix)
                {
                    case ".ytd":
                        var ytd = new YtdFile(); ytd.Load(data);
                        xml = YtdXml.GetXml(ytd, assetFolder); break;
                    case ".ydr":
                        var ydr = new YdrFile(); ydr.Load(data);
                        xml = YdrXml.GetXml(ydr, assetFolder); break;
                    case ".ydd":
                        var ydd = new YddFile(); ydd.Load(data);
                        xml = YddXml.GetXml(ydd, assetFolder); break;
                    case ".yft":
                        var yft = new YftFile(); yft.Load(data);
                        xml = YftXml.GetXml(yft, assetFolder); break;
                    case ".ybn":
                        var ybn = new YbnFile(); ybn.Load(data);
                        xml = YbnXml.GetXml(ybn); break;
                    case ".ymap":
                        var ymap = new YmapFile(); ymap.Load(data);
                        xml = MetaXml.GetXml(ymap, out _); break;
                    case ".ytyp":
                        var ytyp = new YtypFile(); ytyp.Load(data);
                        xml = MetaXml.GetXml(ytyp, out _); break;
                    case ".ymt":
                        var ymt = new YmtFile(); ymt.Load(data);
                        xml = MetaXml.GetXml(ymt, out _); break;
                    case ".ymf":
                        var ymfEntry = LooseBinaryEntry(input);
                        var ymf = RpfFile.GetFile<YmfFile>(ymfEntry, data);
                        xml = MetaXml.GetXml(ymf, out _); break;
                    case ".ynd":
                        var ynd = new YndFile(); ynd.Load(data);
                        xml = YndXml.GetXml(ynd); break;
                    case ".ynv":
                        var ynv = new YnvFile(); ynv.Load(data);
                        xml = YnvXml.GetXml(ynv); break;
                    case ".ypt":
                        var ypt = new YptFile(); ypt.Load(data);
                        xml = YptXml.GetXml(ypt, assetFolder); break;
                    case ".ycd":
                        var ycd = RpfFile.GetResourceFile<YcdFile>(data);
                        xml = YcdXml.GetXml(ycd); break;
                    case ".yed":
                        var yed = RpfFile.GetResourceFile<YedFile>(data);
                        xml = YedXml.GetXml(yed); break;
                    case ".yfd":
                        var yfd = RpfFile.GetResourceFile<YfdFile>(data);
                        xml = YfdXml.GetXml(yfd); break;
                    case ".yvr":
                        var yvr = RpfFile.GetResourceFile<YvrFile>(data);
                        xml = YvrXml.GetXml(yvr); break;
                    case ".ywr":
                        var ywr = RpfFile.GetResourceFile<YwrFile>(data);
                        xml = YwrXml.GetXml(ywr); break;
                    case ".rel":
                        var relEntry = LooseBinaryEntry(input);
                        var rel = new RelFile(); rel.Load(data, relEntry);
                        xml = RelXml.GetXml(rel); break;
                    case ".awc":
                        LoadAwcKey(gtaPath, gen9);
                        var awcEntry = LooseBinaryEntry(input);
                        var awc = new AwcFile(); awc.Load(data, awcEntry);
                        if (!string.IsNullOrWhiteSpace(awc.ErrorMessage))
                            throw new InvalidDataException(
                                "AWC parse failed: " + awc.ErrorMessage);
                        xml = AwcXml.GetXml(awc, assetFolder); break;
                    case ".gxt2":
                        var entry = new RpfBinaryFileEntry
                        {
                            Name = Path.GetFileName(input),
                            NameLower = Path.GetFileName(input).ToLowerInvariant(),
                            Path = input,
                        };
                        var gxt = new Gxt2File(); gxt.Load(data, entry);
                        xml = new XElement("GXT2",
                            new XAttribute("count", gxt.EntryCount),
                            (gxt.TextEntries ?? Array.Empty<Gxt2Entry>()).Select(item =>
                                new XElement("Entry",
                                    new XAttribute("hash", $"0x{item.Hash:X8}"),
                                    new XCData(item.Text ?? string.Empty))))
                            .ToString();
                        break;
                    default:
                        Console.Error.WriteLine(
                            $"ERROR: Native XML preview is not available for {suffix}");
                        return 6;
                }
                if (string.IsNullOrWhiteSpace(xml))
                {
                    Console.Error.WriteLine("ERROR: Native asset conversion produced no XML.");
                    return 5;
                }
                string parent = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                Directory.CreateDirectory(assetFolder);
                File.WriteAllText(output, xml, new UTF8Encoding(false));
                Console.WriteLine($"Exported native asset XML: {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Native asset conversion failed: {ex.Message}");
                return 99;
            }
            finally
            {
                RpfManager.IsGen9 = previous;
            }
        }

        // Rebuild a native resource from the CodeWalker XML emitted by asset-xml.
        // Ambiguous META/PSO/RBF containers use the original asset as a schema and
        // container-type authority, preserving newer embedded META definitions.
        static int ImportAssetXml(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe asset-from-xml <input_xml> <output_asset> <asset_folder> [legacy|gen9] [source_asset] [gta_path]");
                return 1;
            }
            string input = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            string assetFolder = Path.GetFullPath(args[3]);
            bool gen9 = args.Length >= 5 && args[4].Equals(
                "gen9", StringComparison.OrdinalIgnoreCase);
            string sourceAsset = args.Length >= 6
                ? Path.GetFullPath(args[5]) : null;
            string gtaPath = args.Length >= 7
                ? Path.GetFullPath(args[6]) : null;
            if (!File.Exists(input) || !Directory.Exists(assetFolder))
            {
                Console.Error.WriteLine("ERROR: XML input or asset folder not found.");
                return 4;
            }
            if (sourceAsset != null && !File.Exists(sourceAsset))
            {
                Console.Error.WriteLine($"ERROR: Original source asset not found: {sourceAsset}");
                return 4;
            }

            bool previous = RpfManager.IsGen9;
            try
            {
                string suffix = Path.GetExtension(output).ToLowerInvariant();
                MetaFormat format;
                object schemaSource = null;
                switch (suffix)
                {
                    case ".rel": format = MetaFormat.AudioRel; break;
                    case ".ynd": format = MetaFormat.Ynd; break;
                    case ".ynv": format = MetaFormat.Ynv; break;
                    case ".ycd": format = MetaFormat.Ycd; break;
                    case ".ybn": format = MetaFormat.Ybn; break;
                    case ".ytd": format = MetaFormat.Ytd; break;
                    case ".ydr": format = MetaFormat.Ydr; break;
                    case ".ydd": format = MetaFormat.Ydd; break;
                    case ".yft": format = MetaFormat.Yft; break;
                    case ".ypt": format = MetaFormat.Ypt; break;
                    case ".yed": format = MetaFormat.Yed; break;
                    case ".ywr": format = MetaFormat.Ywr; break;
                    case ".yvr": format = MetaFormat.Yvr; break;
                    case ".awc": format = MetaFormat.Awc; break;
                    case ".yfd": format = MetaFormat.Yfd; break;
                    case ".ymap":
                    case ".ytyp":
                        format = MetaFormat.RSC;
                        break;
                    case ".ymt":
                    case ".ymf":
                        if (sourceAsset == null)
                            throw new InvalidDataException(
                                $"{suffix} XML import requires its original source asset to identify META, PSO, or RBF encoding.");
                        format = MetaFormat.XML;
                        break;
                    default:
                        Console.Error.WriteLine(
                            $"ERROR: Native XML import is not available for {suffix}");
                        return 6;
                }

                RpfManager.IsGen9 = gen9;
                if (suffix == ".awc")
                    LoadAwcKey(gtaPath, gen9);
                if (sourceAsset != null && (suffix == ".ymap" || suffix == ".ytyp"
                    || suffix == ".ymt" || suffix == ".ymf"))
                {
                    byte[] sourceData = File.ReadAllBytes(sourceAsset);
                    var sourceEntry = LooseBinaryEntry(sourceAsset);
                    Meta meta = null;
                    PsoFile pso = null;
                    RbfFile rbf = null;
                    if (suffix == ".ymap")
                    {
                        var parsed = RpfFile.GetFile<YmapFile>(sourceEntry, sourceData);
                        meta = parsed?.Meta; pso = parsed?.Pso; rbf = parsed?.Rbf;
                    }
                    else if (suffix == ".ytyp")
                    {
                        var parsed = RpfFile.GetFile<YtypFile>(sourceEntry, sourceData);
                        meta = parsed?.Meta; pso = parsed?.Pso; rbf = parsed?.Rbf;
                    }
                    else if (suffix == ".ymt")
                    {
                        var parsed = RpfFile.GetFile<YmtFile>(sourceEntry, sourceData);
                        meta = parsed?.Meta; pso = parsed?.Pso; rbf = parsed?.Rbf;
                    }
                    else
                    {
                        var parsed = RpfFile.GetFile<YmfFile>(sourceEntry, sourceData);
                        meta = parsed?.Meta; pso = parsed?.Pso; rbf = parsed?.Rbf;
                    }
                    if (meta != null)
                    {
                        format = MetaFormat.RSC;
                        schemaSource = meta;
                    }
                    else if (pso != null)
                    {
                        format = MetaFormat.PSO;
                        schemaSource = pso;
                    }
                    else if (rbf != null)
                    {
                        format = MetaFormat.RBF;
                    }
                    else
                    {
                        throw new InvalidDataException(
                            "Original structured asset could not be classified as META, PSO, or RBF.");
                    }
                }

                var settings = new XmlReaderSettings
                {
                    DtdProcessing = DtdProcessing.Prohibit,
                    XmlResolver = null,
                    MaxCharactersInDocument = 256L * 1024L * 1024L,
                };
                var document = new XmlDocument { XmlResolver = null };
                using (var reader = XmlReader.Create(input, settings))
                    document.Load(reader);
                byte[] data = schemaSource == null
                    ? XmlMeta.GetData(document, format, assetFolder)
                    : XmlMeta.GetData(document, format, assetFolder, schemaSource);
                if (data == null || data.Length == 0)
                    throw new InvalidDataException("Native XML rebuild produced no data.");
                string parent = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllBytes(output, data);
                Console.WriteLine(
                    $"Rebuilt {suffix} from CodeWalker XML ({data.Length:N0} bytes): {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Native XML rebuild failed: {ex.Message}");
                return 99;
            }
            finally
            {
                RpfManager.IsGen9 = previous;
            }
        }

        static RpfBinaryFileEntry LooseBinaryEntry(string path)
        {
            string name = Path.GetFileName(path);
            return new RpfBinaryFileEntry
            {
                Name = name,
                NameLower = name.ToLowerInvariant(),
                Path = path,
                FileUncompressedSize = (uint)Math.Min(
                    new FileInfo(path).Length, uint.MaxValue),
            };
        }

        static void LoadAwcKey(string gtaPath, bool gen9)
        {
            if (string.IsNullOrWhiteSpace(gtaPath))
                throw new InvalidOperationException(
                    "AWC conversion requires the matching GTA installation path for audio keys.");
            if (!Directory.Exists(gtaPath))
                throw new DirectoryNotFoundException(
                    "GTA path for AWC keys was not found: " + gtaPath);
            GTA5Keys.LoadFromPath(gtaPath, gen9, null);
            if (GTA5Keys.PC_AWC_KEY == null || GTA5Keys.PC_AWC_KEY.Length == 0)
                throw new InvalidDataException(
                    "The GTA installation did not provide an AWC decryption key.");
        }

        // Build a texture dictionary from standards-compliant DDS files.
        // PNG-to-BC3 conversion is intentionally performed by the Python
        // installer because the historical YTDToolio PNG encoder emits
        // corrupt scanlines on current Windows systems.
        static int BuildYtd(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-ytd <dds_folder> <output_ytd> [legacy|gen9]");
                return 1;
            }

            string ddsFolder = args[1];
            string outputPath = args[2];
            bool isGen9 = args.Length >= 4 &&
                args[3].Equals("gen9", StringComparison.OrdinalIgnoreCase);
            if (!Directory.Exists(ddsFolder))
            {
                Console.Error.WriteLine($"ERROR: Folder not found: {ddsFolder}");
                return 4;
            }

            string[] files = Directory.GetFiles(ddsFolder, "*.dds")
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            if (files.Length == 0)
            {
                Console.Error.WriteLine("ERROR: No .dds files found.");
                return 4;
            }

            var previous = RpfManager.IsGen9;
            try
            {
                var textures = new List<Texture>();
                foreach (string file in files)
                {
                    var texture = DDSIO.GetTexture(File.ReadAllBytes(file));
                    if (texture == null)
                        throw new InvalidDataException($"Unsupported DDS: {file}");
                    texture.Name = Path.GetFileNameWithoutExtension(file).ToLowerInvariant();
                    texture.NameHash = JenkHash.GenHash(texture.Name);
                    texture.Usage = TextureUsage.DIFFUSE;
                    textures.Add(texture);
                    Console.WriteLine(
                        $"  + {texture.Name} ({texture.Width}x{texture.Height}, {texture.Format})");
                }

                var dictionary = new TextureDictionary();
                dictionary.BuildFromTextureList(textures);
                var ytd = new YtdFile { TextureDict = dictionary };
                RpfManager.IsGen9 = isGen9;
                byte[] data = ytd.Save();
                string parent = Path.GetDirectoryName(Path.GetFullPath(outputPath));
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllBytes(outputPath, data);
                Console.WriteLine(
                    $"Built {(isGen9 ? "Gen9" : "Legacy")} YTD with {textures.Count} textures: {outputPath}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
            finally
            {
                RpfManager.IsGen9 = previous;
            }
        }

        static int UnpackYtd(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe unpack-ytd <ytd_path> <output_folder> [legacy|gen9]");
                return 1;
            }

            string ytdPath = args[1];
            string outputFolder = args[2];
            bool isGen9 = args.Length < 4 ||
                !args[3].Equals("legacy", StringComparison.OrdinalIgnoreCase);
            if (!File.Exists(ytdPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {ytdPath}");
                return 4;
            }

            var previous = RpfManager.IsGen9;
            try
            {
                RpfManager.IsGen9 = isGen9;
                var ytd = new YtdFile();
                ytd.Load(File.ReadAllBytes(ytdPath));
                var textures = ytd.TextureDict?.Textures?.data_items ?? Array.Empty<Texture>();
                Directory.CreateDirectory(outputFolder);
                foreach (var texture in textures)
                {
                    if (texture == null || string.IsNullOrWhiteSpace(texture.Name)) continue;
                    string output = Path.Combine(outputFolder, texture.Name + ".dds");
                    File.WriteAllBytes(output, DDSIO.GetDDSFile(texture));
                    Console.WriteLine($"  + {texture.Name}.dds");
                }
                Console.WriteLine($"Unpacked {textures.Length} textures to {outputFolder}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
            finally
            {
                RpfManager.IsGen9 = previous;
            }
        }

        // Read-only diagnostics used to compare ALLIN1 resources with native
        // Enhanced files without requiring a GUI archive editor.
        static int PsoToXml(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe pso-to-xml <input_pso> <output_xml>");
                return 1;
            }

            try
            {
                var pso = new PsoFile();
                pso.Load(args[1]);
                PsoTypes.EnsurePsoTypes(pso);
                string xml = PsoXml.GetXml(pso);
                if (string.IsNullOrWhiteSpace(xml))
                {
                    Console.Error.WriteLine("ERROR: PSO conversion produced no XML.");
                    return 5;
                }

                string output = Path.GetFullPath(args[2]);
                string parent = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllText(output, xml, new UTF8Encoding(false));
                Console.WriteLine($"Converted PSO to XML: {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int InspectPso(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine("Usage: RpfPatcher.exe inspect-pso <input_pso>");
                return 1;
            }

            try
            {
                var pso = new PsoFile();
                pso.Load(args[1]);
                var cont = new PsoXml.PsoCont(pso);
                Console.WriteLine($"Root block: {pso.DataMapSection.RootId}");
                for (int i = 0; i < pso.DataMapSection.Entries.Length; i++)
                {
                    PsoDataMappingEntry block = pso.DataMapSection.Entries[i];
                    Console.WriteLine(
                        $"BLOCK {i + 1}: {PsoXml.HashString(block.NameHash)} " +
                        $"offset={block.Offset} length={block.Length}");
                }

                foreach (PsoStructureInfo structure in
                    pso.SchemaSection.Entries.OfType<PsoStructureInfo>())
                {
                    Console.WriteLine(
                        $"STRUCT {PsoXml.HashString(structure.IndexInfo.NameHash)} " +
                        $"length={structure.StructureLength}");
                    foreach (PsoStructureEntryInfo entry in structure.Entries)
                    {
                        Console.WriteLine(
                            $"  {PsoXml.HashString(entry.EntryNameHash)} " +
                            $"type={entry.Type} subtype={entry.Unk_5h} " +
                            $"offset={entry.DataOffset} ref=0x{entry.ReferenceKey:X8}");
                    }
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        private const string SmokeExplosionPath =
            "x64/data/metadata/explosion.ymt";
        private const string SmokeExplosionFxPath =
            "common/data/effects/explosionfx.dat";
        private const string SmokeExplosionTag = "EXP_TAG_SMOKEGRENADE";
        private const string SmokeVfxTag = "EXP_VFXTAG_SMOKE_GRENADE";
        private const float SmokeRadius = 9.0f;
        private const float SmokeLifetimeSeconds = 40.0f;
        private const float SmokeVfxScale = 2.0f;

        static int BuildSmokeTuning(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-smoke-tuning " +
                    "<input_ymt> <input_dat> <output_folder>");
                return 1;
            }
            try
            {
                byte[] sourceYmt = File.ReadAllBytes(args[1]);
                byte[] sourceDat = File.ReadAllBytes(args[2]);
                Dictionary<string, byte[]> payload = BuildSmokePayload(
                    sourceYmt, sourceDat);
                string output = Path.GetFullPath(args[3]);
                Directory.CreateDirectory(output);
                File.WriteAllBytes(Path.Combine(output, "explosion.ymt"),
                    payload[SmokeExplosionPath]);
                File.WriteAllBytes(Path.Combine(output, "explosionfx.dat"),
                    payload[SmokeExplosionFxPath]);
                Console.WriteLine(
                    $"Built isolated smoke-grenade tuning in {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int InstallSmokeTuning(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe install-smoke-tuning <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before installing smoke archive tuning.");
                return 11;
            }

            string modsRpf = Path.Combine(gtaPath, "mods", "update", "update.rpf");
            bool writesStarted = false;
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(gtaPath, out int errorCode);
                if (rpf == null) return errorCode;
                byte[] sourceYmt = ExtractRequiredEntry(rpf, SmokeExplosionPath);
                byte[] sourceDat = ExtractRequiredEntry(rpf, SmokeExplosionFxPath);
                Dictionary<string, byte[]> payload = BuildSmokePayload(
                    sourceYmt, sourceDat);

                Dictionary<string, byte[]> originals =
                    EnsureSmokeEntryBackups(gtaPath, sourceYmt, sourceDat);
                EnsureSmokeBackup(modsRpf);
                writesStarted = true;
                InstallArchiveEntries(rpf, payload);
                int result = VerifySmokeArchive(rpf);
                if (result != 0)
                {
                    RestoreSmokeBackup(gtaPath);
                    return result;
                }
                WriteSmokeMarker(gtaPath, payload, originals);
                Console.WriteLine(
                    "Installed ALLIN1 custom smoke tuning; native tear gas was not changed.");
                return 0;
            }
            catch (Exception ex)
            {
                if (writesStarted) RestoreSmokeBackup(gtaPath);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int VerifySmokeTuning(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-smoke-tuning <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (rpf == null) return errorCode;
                int result = VerifySmokeArchive(rpf);
                if (result != 0) return result;

                string marker = GetSmokeMarkerPath(gtaPath);
                if (!File.Exists(marker))
                {
                    Console.Error.WriteLine(
                        $"ERROR: Smoke archive marker is missing: {marker}");
                    return 12;
                }
                string json = File.ReadAllText(marker);
                byte[] ymt = ExtractRequiredEntry(rpf, SmokeExplosionPath);
                byte[] dat = ExtractRequiredEntry(rpf, SmokeExplosionFxPath);
                foreach (string expected in new[] { Sha256(ymt), Sha256(dat),
                    "WEAPON_SMOKEGRENADE", SmokeExplosionTag,
                    SmokeVfxTag })
                {
                    if (json.IndexOf(expected,
                            StringComparison.OrdinalIgnoreCase) >= 0) continue;
                    Console.Error.WriteLine(
                        $"ERROR: Smoke marker is incomplete: {expected}");
                    return 12;
                }
                Console.WriteLine("ALLIN1 custom smoke archive tuning verified.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int RemoveSmokeTuning(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe remove-smoke-tuning <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before removing smoke archive tuning.");
                return 11;
            }
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(gtaPath, out int errorCode);
                if (rpf == null) return errorCode;
                Dictionary<string, byte[]> originals =
                    ReadSmokeEntryBackups(gtaPath);
                InstallArchiveEntries(rpf, originals);
                int failures = VerifyArchiveEntries(
                    rpf, originals, "restored smoke original");
                if (failures != 0) return failures;
                string marker = GetSmokeMarkerPath(gtaPath);
                if (File.Exists(marker)) File.Delete(marker);
                Console.WriteLine(
                    "Removed ALLIN1 custom smoke tuning; unrelated RPF entries were preserved.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static Dictionary<string, byte[]> BuildSmokePayload(
            byte[] sourceYmt, byte[] sourceDat)
        {
            var payload = new Dictionary<string, byte[]>(
                StringComparer.OrdinalIgnoreCase)
            {
                { SmokeExplosionPath, PatchSmokeExplosionPso(sourceYmt) },
                { SmokeExplosionFxPath, PatchSmokeExplosionFx(sourceDat) },
            };
            ValidateSmokeExplosionPso(payload[SmokeExplosionPath]);
            ValidateSmokeExplosionFx(payload[SmokeExplosionFxPath]);
            return payload;
        }

        static byte[] PatchSmokeExplosionPso(byte[] source)
        {
            var pso = new PsoFile();
            pso.Load(source);
            int recordOffset = LocateExplosionRecord(
                pso, SmokeExplosionTag, out PsoStructureInfo recordInfo);
            var allowedChanges = new HashSet<int>();

            SetPsoFloat(pso, recordInfo, recordOffset,
                "damageAtCentre", 0.0f, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "damageAtEdge", 0.0f, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "endRadius", SmokeRadius, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "forceFactor", 0.0f, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "fRagdollForceModifier", 0.0f, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "fSelfForceModifier", 0.0f, allowedChanges);
            SetPsoFloat(pso, recordInfo, recordOffset,
                "directedLifeTime", SmokeLifetimeSeconds, allowedChanges);
            SetPsoBool(pso, recordInfo, recordOffset,
                "bAppliesContinuousDamage", false, allowedChanges);
            SetPsoBool(pso, recordInfo, recordOffset,
                "bNoOcclusion", true, allowedChanges);

            // Do not call PsoFile.Save here. CodeWalker's PSO serializer uses
            // its built-in schema and drops newer Enhanced-only fields. PSIN
            // starts at byte zero, so the audited data offsets map directly
            // onto the original file. Copy only those bytes and preserve every
            // section, schema field, and record that the current game shipped.
            byte[] patched = (byte[])source.Clone();
            foreach (int index in allowedChanges)
                patched[index] = pso.DataSection.Data[index];
            return patched;
        }

        static byte[] PatchSmokeExplosionFx(byte[] source)
        {
            string text = Encoding.ASCII.GetString(source);
            var pattern = new Regex(
                @"^(EXP_VFXTAG_SMOKE_GRENADE(?:\s+\S+){8}\s+)(\S+)",
                RegexOptions.Multiline | RegexOptions.CultureInvariant);
            MatchCollection matches = pattern.Matches(text);
            if (matches.Count != 1)
                throw new InvalidDataException(
                    $"Expected one {SmokeVfxTag} row; found {matches.Count}.");
            string replacement = matches[0].Groups[1].Value +
                SmokeVfxScale.ToString("0.0",
                    System.Globalization.CultureInfo.InvariantCulture);
            string patched = text.Substring(0, matches[0].Index) + replacement +
                text.Substring(matches[0].Index + matches[0].Length);
            return Encoding.ASCII.GetBytes(patched);
        }

        static int LocateExplosionRecord(PsoFile pso, string recordName,
            out PsoStructureInfo recordInfo)
        {
            var cont = new PsoXml.PsoCont(pso);
            PsoDataMappingEntry rootBlock = pso.GetBlock(
                pso.DataMapSection.RootId);
            PsoStructureInfo rootInfo = cont.GetStructureInfo(
                rootBlock.NameHash);
            PsoStructureEntryInfo arrayEntry = FindPsoEntry(
                rootInfo, "aExplosionTagData", PsoDataType.Array);
            var records = MetaTypes.ConvertData<Array_Structure>(
                pso.DataSection.Data, rootBlock.Offset + arrayEntry.DataOffset);
            records.SwapEnd();
            PsoDataMappingEntry recordBlock = pso.GetBlock(
                (int)records.PointerDataId);
            if (recordBlock == null)
                throw new InvalidDataException("Explosion record block is missing.");
            recordInfo = cont.GetStructureInfo(recordBlock.NameHash);
            PsoStructureEntryInfo nameEntry = FindPsoEntry(
                recordInfo, "name", PsoDataType.String);

            for (int index = 0; index < records.Count1; index++)
            {
                int offset = recordBlock.Offset +
                    (int)records.PointerDataOffset +
                    index * recordInfo.StructureLength;
                var pointer = MetaTypes.ConvertData<CharPointer>(
                    pso.DataSection.Data, offset + nameEntry.DataOffset);
                pointer.SwapEnd();
                string currentName = PsoTypes.GetString(pso, pointer);
                if (string.Equals(currentName, recordName,
                        StringComparison.Ordinal)) return offset;
            }
            throw new InvalidDataException(
                $"Explosion record not found: {recordName}");
        }

        static PsoStructureEntryInfo FindPsoEntry(PsoStructureInfo structure,
            string name, PsoDataType type)
        {
            PsoStructureEntryInfo entry = structure?.Entries?.FirstOrDefault(
                candidate => candidate.Type == type &&
                PsoXml.HashString(candidate.EntryNameHash).Equals(
                    name, StringComparison.Ordinal));
            if (entry == null)
                throw new InvalidDataException(
                    $"PSO field is missing or has the wrong type: {name}");
            return entry;
        }

        static void SetPsoFloat(PsoFile pso, PsoStructureInfo structure,
            int recordOffset, string name, float value,
            HashSet<int> allowedChanges)
        {
            PsoStructureEntryInfo entry = FindPsoEntry(
                structure, name, PsoDataType.Float);
            int offset = recordOffset + entry.DataOffset;
            byte[] bytes = BitConverter.GetBytes(value);
            if (BitConverter.IsLittleEndian) Array.Reverse(bytes);
            Buffer.BlockCopy(bytes, 0, pso.DataSection.Data, offset, 4);
            for (int index = 0; index < 4; index++)
                allowedChanges.Add(offset + index);
        }

        static void SetPsoBool(PsoFile pso, PsoStructureInfo structure,
            int recordOffset, string name, bool value,
            HashSet<int> allowedChanges)
        {
            PsoStructureEntryInfo entry = FindPsoEntry(
                structure, name, PsoDataType.Bool);
            int offset = recordOffset + entry.DataOffset;
            pso.DataSection.Data[offset] = value ? (byte)1 : (byte)0;
            allowedChanges.Add(offset);
        }

        static float ReadPsoFloat(PsoFile pso, PsoStructureInfo structure,
            int recordOffset, string name)
        {
            int offset = recordOffset + FindPsoEntry(
                structure, name, PsoDataType.Float).DataOffset;
            byte[] bytes = new byte[4];
            Buffer.BlockCopy(pso.DataSection.Data, offset, bytes, 0, 4);
            if (BitConverter.IsLittleEndian) Array.Reverse(bytes);
            return BitConverter.ToSingle(bytes, 0);
        }

        static bool ReadPsoBool(PsoFile pso, PsoStructureInfo structure,
            int recordOffset, string name)
        {
            int offset = recordOffset + FindPsoEntry(
                structure, name, PsoDataType.Bool).DataOffset;
            return pso.DataSection.Data[offset] != 0;
        }

        static void ValidateSmokeExplosionPso(byte[] data)
        {
            var pso = new PsoFile();
            pso.Load(data);
            int offset = LocateExplosionRecord(
                pso, SmokeExplosionTag, out PsoStructureInfo info);
            var expectedFloats = new Dictionary<string, float>
            {
                { "damageAtCentre", 0.0f },
                { "damageAtEdge", 0.0f },
                { "endRadius", SmokeRadius },
                { "forceFactor", 0.0f },
                { "fRagdollForceModifier", 0.0f },
                { "fSelfForceModifier", 0.0f },
                { "directedLifeTime", SmokeLifetimeSeconds },
            };
            foreach (KeyValuePair<string, float> expected in expectedFloats)
            {
                float actual = ReadPsoFloat(pso, info, offset, expected.Key);
                if (Math.Abs(actual - expected.Value) > 0.00001f)
                    throw new InvalidDataException(
                        $"Smoke PSO verification failed for {expected.Key}: {actual}");
            }
            if (ReadPsoBool(pso, info, offset,
                    "bAppliesContinuousDamage"))
                throw new InvalidDataException(
                    "Smoke PSO still applies continuous damage.");
            if (!ReadPsoBool(pso, info, offset, "bNoOcclusion"))
                throw new InvalidDataException(
                    "Smoke PSO no-occlusion flag was not enabled.");
        }

        static void ValidateSmokeExplosionFx(byte[] data)
        {
            string text = Encoding.ASCII.GetString(data);
            var pattern = new Regex(
                @"^EXP_VFXTAG_SMOKE_GRENADE(?:\s+\S+){8}\s+(\S+)",
                RegexOptions.Multiline | RegexOptions.CultureInvariant);
            MatchCollection matches = pattern.Matches(text);
            if (matches.Count != 1 ||
                !float.TryParse(matches[0].Groups[1].Value,
                    System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture,
                    out float scale) ||
                Math.Abs(scale - SmokeVfxScale) > 0.00001f)
                throw new InvalidDataException(
                    "Smoke-grenade VFX scale verification failed.");
        }

        static byte[] ExtractRequiredEntry(RpfFile rpf, string path)
        {
            RpfFileEntry entry = FindRelativeEntry(rpf, path);
            byte[] data = entry?.File.ExtractFile(entry);
            if (data == null || data.Length == 0)
                throw new InvalidDataException(
                    $"Required RPF entry is missing or empty: {path}");
            return data;
        }

        static int VerifySmokeArchive(RpfFile rpf)
        {
            try
            {
                byte[] ymt = ExtractRequiredEntry(rpf, SmokeExplosionPath);
                byte[] dat = ExtractRequiredEntry(rpf, SmokeExplosionFxPath);
                ValidateSmokeExplosionPso(ymt);
                ValidateSmokeExplosionFx(dat);
                Console.WriteLine(
                    $"  OK {SmokeExplosionPath} ({ymt.Length:N0} bytes, " +
                    $"sha256={Sha256(ymt)})");
                Console.WriteLine(
                    $"  OK {SmokeExplosionFxPath} ({dat.Length:N0} bytes, " +
                    $"sha256={Sha256(dat)})");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(
                    $"ERROR: Smoke archive verification failed: {ex.Message}");
                return 9;
            }
        }

        static void EnsureSmokeBackup(string modsRpf)
        {
            string backup = modsRpf + ".allin1-smoke.bak";
            if (File.Exists(backup))
            {
                Console.WriteLine($"Preserving rollback snapshot: {backup}");
                return;
            }
            long sourceLength = new FileInfo(modsRpf).Length;
            var drive = new DriveInfo(Path.GetPathRoot(modsRpf));
            if (drive.AvailableFreeSpace < sourceLength + 268435456L)
                throw new IOException(
                    "Not enough free disk space for the smoke tuning rollback snapshot.");
            string temporary = backup + ".tmp";
            if (File.Exists(temporary)) File.Delete(temporary);
            try
            {
                File.Copy(modsRpf, temporary, false);
                if (new FileInfo(temporary).Length != sourceLength)
                    throw new IOException(
                        "Smoke tuning rollback snapshot failed size verification.");
                File.Move(temporary, backup);
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
            Console.WriteLine($"Created rollback snapshot: {backup}");
        }

        static bool RestoreSmokeBackup(string gtaPath)
        {
            string target = Path.Combine(
                gtaPath, "mods", "update", "update.rpf");
            string backup = target + ".allin1-smoke.bak";
            if (!File.Exists(backup))
            {
                Console.Error.WriteLine(
                    $"MISSING ROLLBACK SNAPSHOT: {backup}");
                return false;
            }
            File.Copy(backup, target, true);
            Console.WriteLine($"Restored: {target}");
            return true;
        }

        static Dictionary<string, byte[]> EnsureSmokeEntryBackups(
            string gtaPath, byte[] currentYmt, byte[] currentDat)
        {
            string directory = GetSmokeEntryBackupDirectory(gtaPath);
            string ymtPath = Path.Combine(directory, "explosion.ymt");
            string datPath = Path.Combine(directory, "explosionfx.dat");
            bool ymtExists = File.Exists(ymtPath);
            bool datExists = File.Exists(datPath);
            if (ymtExists != datExists)
                throw new InvalidDataException(
                    $"Smoke entry backup is incomplete: {directory}");
            if (ymtExists)
            {
                Console.WriteLine(
                    $"Preserving original smoke entries: {directory}");
                return ReadSmokeEntryBackups(gtaPath);
            }

            byte[] originalYmt = currentYmt;
            byte[] originalDat = currentDat;
            string marker = GetSmokeMarkerPath(gtaPath);
            string archive = Path.Combine(
                gtaPath, "mods", "update", "update.rpf");
            string legacyBackup = archive + ".allin1-smoke.bak";
            if (File.Exists(marker))
            {
                if (!File.Exists(legacyBackup))
                    throw new InvalidDataException(
                        "Smoke tuning is marked installed, but no original-entry " +
                        "or legacy full-archive backup is available.");
                var legacyRpf = new RpfFile(legacyBackup, legacyBackup);
                legacyRpf.ScanStructure(null, err =>
                    Console.Error.WriteLine(
                        $"RPF backup scan warning: {err}"));
                originalYmt = ExtractRequiredEntry(
                    legacyRpf, SmokeExplosionPath);
                originalDat = ExtractRequiredEntry(
                    legacyRpf, SmokeExplosionFxPath);
                Console.WriteLine(
                    "Migrating original smoke entries from the legacy full snapshot.");
            }

            Directory.CreateDirectory(directory);
            WriteAtomicFile(ymtPath, originalYmt);
            WriteAtomicFile(datPath, originalDat);
            string manifest = Path.Combine(directory, "manifest.txt");
            File.WriteAllText(manifest,
                $"{SmokeExplosionPath}\t{originalYmt.Length}\t{Sha256(originalYmt)}\n" +
                $"{SmokeExplosionFxPath}\t{originalDat.Length}\t{Sha256(originalDat)}\n",
                new UTF8Encoding(false));
            Console.WriteLine($"Saved original smoke entries: {directory}");
            return new Dictionary<string, byte[]>(
                StringComparer.OrdinalIgnoreCase)
            {
                { SmokeExplosionPath, originalYmt },
                { SmokeExplosionFxPath, originalDat },
            };
        }

        static Dictionary<string, byte[]> ReadSmokeEntryBackups(
            string gtaPath)
        {
            string directory = GetSmokeEntryBackupDirectory(gtaPath);
            string ymtPath = Path.Combine(directory, "explosion.ymt");
            string datPath = Path.Combine(directory, "explosionfx.dat");
            if (!File.Exists(ymtPath) || !File.Exists(datPath))
                throw new FileNotFoundException(
                    $"Original smoke-entry backup is missing: {directory}");
            byte[] ymt = File.ReadAllBytes(ymtPath);
            byte[] dat = File.ReadAllBytes(datPath);
            if (ymt.Length == 0 || dat.Length == 0)
                throw new InvalidDataException(
                    $"Original smoke-entry backup is empty: {directory}");
            return new Dictionary<string, byte[]>(
                StringComparer.OrdinalIgnoreCase)
            {
                { SmokeExplosionPath, ymt },
                { SmokeExplosionFxPath, dat },
            };
        }

        static int VerifyArchiveEntries(RpfFile rpf,
            Dictionary<string, byte[]> expected, string label)
        {
            int failures = 0;
            foreach (KeyValuePair<string, byte[]> item in expected)
            {
                byte[] actual = ExtractRequiredEntry(rpf, item.Key);
                bool matches = actual.SequenceEqual(item.Value);
                Console.WriteLine(
                    $"  {(matches ? "OK" : "FAIL")} {label}/{item.Key}" +
                    $" ({actual.Length:N0} bytes, sha256={Sha256(actual)})");
                if (!matches) failures++;
            }
            if (failures == 0) return 0;
            Console.Error.WriteLine(
                $"ERROR: {label} verification failed ({failures} entries).");
            return 9;
        }

        static void WriteAtomicFile(string path, byte[] data)
        {
            string temporary = path + ".tmp";
            if (File.Exists(temporary)) File.Delete(temporary);
            try
            {
                File.WriteAllBytes(temporary, data);
                File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
        }

        static string GetSmokeEntryBackupDirectory(string gtaPath)
        {
            return Path.Combine(gtaPath, "mods", "update",
                ".allin1-smoke-originals");
        }

        static void WriteSmokeMarker(string gtaPath,
            Dictionary<string, byte[]> payload,
            Dictionary<string, byte[]> originals)
        {
            string marker = GetSmokeMarkerPath(gtaPath);
            Directory.CreateDirectory(Path.GetDirectoryName(marker));
            string json = "{\n" +
                $"  \"installed_utc\": \"{DateTime.UtcNow:o}\",\n" +
                "  \"archive\": \"mods/update/update.rpf\",\n" +
                "  \"carrier\": \"WEAPON_SMOKEGRENADE\",\n" +
                $"  \"explosion_tag\": \"{SmokeExplosionTag}\",\n" +
                $"  \"vfx_tag\": \"{SmokeVfxTag}\",\n" +
                $"  \"radius\": {SmokeRadius:0.0},\n" +
                $"  \"lifetime_seconds\": {SmokeLifetimeSeconds:0.0},\n" +
                $"  \"vfx_scale\": {SmokeVfxScale:0.0},\n" +
                $"  \"explosion_ymt_sha256\": \"{Sha256(payload[SmokeExplosionPath])}\",\n" +
                $"  \"explosionfx_dat_sha256\": \"{Sha256(payload[SmokeExplosionFxPath])}\",\n" +
                $"  \"original_explosion_ymt_sha256\": \"{Sha256(originals[SmokeExplosionPath])}\",\n" +
                $"  \"original_explosionfx_dat_sha256\": \"{Sha256(originals[SmokeExplosionFxPath])}\"\n" +
                "}\n";
            File.WriteAllText(marker, json, new UTF8Encoding(false));
            Console.WriteLine($"Wrote smoke archive marker: {marker}");
        }

        static string GetSmokeMarkerPath(string gtaPath)
        {
            return Path.Combine(gtaPath, "scripts",
                "ALLIN1_smoke_tuning.json");
        }

        static int ExtractEntry(string[] args)
        {
            if (args.Length < 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe extract-entry <gta_path> <rpf_path> <name> <output>");
                return 1;
            }

            string gtaPath = args[1];
            string rpfPath = args[2];
            string entryName = args[3];
            string outputPath = args[4];
            if (!File.Exists(rpfPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {rpfPath}");
                return 4;
            }

            try
            {
                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                var rpf = new RpfFile(rpfPath, rpfPath);
                rpf.ScanStructure(null,
                    err => Console.Error.WriteLine($"RPF scan warning: {err}"));
                string normalizedRequest = entryName
                    .Replace('\\', '/').TrimStart('/');
                bool pathRequest = normalizedRequest.Contains('/');
                var matches = pathRequest
                    ? new[] { FindExactFileEntry(rpf, normalizedRequest) }
                        .Where(entry => entry != null).ToArray()
                    : rpf.AllEntries?
                        .OfType<RpfFileEntry>()
                        .Where(entry => string.Equals(entry.Name, entryName,
                            StringComparison.OrdinalIgnoreCase))
                        .ToArray() ?? Array.Empty<RpfFileEntry>();
                if (matches.Length == 0)
                {
                    Console.Error.WriteLine($"ERROR: Entry not found: {entryName}");
                    return 5;
                }
                if (matches.Length > 1)
                {
                    Console.Error.WriteLine(
                        "ERROR: Entry name is ambiguous: " +
                        string.Join(", ", matches.Select(entry => entry.Path)));
                    return 5;
                }

                byte[] data = matches[0].File.ExtractFile(matches[0]);
                if (data == null || data.Length == 0)
                {
                    Console.Error.WriteLine("ERROR: Extracted entry was empty.");
                    return 5;
                }
                // ExtractFile returns decompressed resource payloads. Re-wrap
                // them as standalone header-bearing RAGE resource files so the
                // result can be opened and compared outside its source RPF.
                if (matches[0] is RpfResourceFileEntry resourceEntry)
                {
                    data = ResourceBuilder.AddResourceHeader(
                        resourceEntry, ResourceBuilder.Compress(data));
                }
                string parent = Path.GetDirectoryName(Path.GetFullPath(outputPath));
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                File.WriteAllBytes(outputPath, data);
                Console.WriteLine(
                    $"Extracted {matches[0].Path} ({data.Length:N0} bytes) to {outputPath}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        // Extract a manifest of entries while scanning the large source RPF
        // only once. Each non-empty TSV line is: source/path<TAB>dest/path.
        static int ExtractEntries(string[] args)
        {
            if (args.Length < 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe extract-entries <gta_path> <rpf_path> <manifest_tsv> <output_root>");
                return 1;
            }

            string gtaPath = args[1];
            string rpfPath = args[2];
            string manifestPath = args[3];
            string outputRoot = Path.GetFullPath(args[4]);
            if (!File.Exists(rpfPath) || !File.Exists(manifestPath))
            {
                Console.Error.WriteLine("ERROR: Source RPF or extraction manifest not found.");
                return 4;
            }

            try
            {
                var requests = File.ReadAllLines(manifestPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line)
                        && !line.TrimStart().StartsWith("#"))
                    .Select(line => line.Split(new[] { '\t' }, 2))
                    .ToArray();
                if (requests.Length == 0 || requests.Any(parts => parts.Length != 2))
                {
                    Console.Error.WriteLine(
                        "ERROR: Extraction manifest is empty or malformed.");
                    return 4;
                }

                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                var rpf = new RpfFile(rpfPath, rpfPath);
                rpf.ScanStructure(null,
                    err => Console.Error.WriteLine($"RPF scan warning: {err}"));

                string archivePrefix = Path.GetFullPath(rpfPath)
                    .Replace('\\', '/').TrimEnd('/') + "/";
                string outputPrefix = outputRoot.TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    + Path.DirectorySeparatorChar;
                var destinations = new HashSet<string>(
                    StringComparer.OrdinalIgnoreCase);
                int extracted = 0;

                foreach (string[] request in requests)
                {
                    string source = request[0].Replace('\\', '/').TrimStart('/');
                    string relativeDestination = request[1]
                        .Replace('/', Path.DirectorySeparatorChar)
                        .TrimStart(Path.DirectorySeparatorChar);
                    string destination = Path.GetFullPath(
                        Path.Combine(outputRoot, relativeDestination));
                    if (!destination.StartsWith(
                            outputPrefix, StringComparison.OrdinalIgnoreCase)
                        || !destinations.Add(destination))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Unsafe or duplicate destination: {request[1]}");
                        return 4;
                    }

                    var matches = rpf.AllEntries?
                        .OfType<RpfFileEntry>()
                        .Where(entry => string.Equals(
                            entry.Path.Replace('\\', '/').StartsWith(
                                archivePrefix, StringComparison.OrdinalIgnoreCase)
                                ? entry.Path.Replace('\\', '/').Substring(
                                    archivePrefix.Length)
                                : entry.Path.Replace('\\', '/'),
                            source, StringComparison.OrdinalIgnoreCase))
                        .ToArray() ?? Array.Empty<RpfFileEntry>();
                    if (matches.Length != 1)
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Expected one match for {source}; found {matches.Length}.");
                        return 5;
                    }

                    byte[] data = matches[0].File.ExtractFile(matches[0]);
                    if (data == null || data.Length == 0)
                    {
                        Console.Error.WriteLine($"ERROR: Extracted entry was empty: {source}");
                        return 5;
                    }
                    if (matches[0] is RpfResourceFileEntry resourceEntry)
                    {
                        data = ResourceBuilder.AddResourceHeader(
                            resourceEntry, ResourceBuilder.Compress(data));
                    }
                    string parent = Path.GetDirectoryName(destination);
                    if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                    File.WriteAllBytes(destination, data);
                    extracted++;
                    Console.WriteLine(
                        $"Extracted {source} -> {request[1]} ({data.Length:N0} bytes)");
                }

                Console.WriteLine($"Extracted {extracted} entries from {rpfPath}.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        // Rewrite staged game-owned nested RPFs to Open encryption. This is
        // required when moving them under a different DLC device/mount path.
        static int OpenRpfs(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe open-rpfs <gta_path> <manifest_tsv> <output_root>");
                return 1;
            }

            string gtaPath = args[1];
            string manifestPath = args[2];
            string outputRoot = Path.GetFullPath(args[3]);
            if (!File.Exists(manifestPath) || !Directory.Exists(outputRoot))
            {
                Console.Error.WriteLine("ERROR: Manifest or staging root not found.");
                return 4;
            }

            try
            {
                bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                           || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
                GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
                string outputPrefix = outputRoot.TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    + Path.DirectorySeparatorChar;
                string[] destinations = File.ReadAllLines(manifestPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line)
                        && !line.TrimStart().StartsWith("#"))
                    .Select(line => line.Split(new[] { '\t' }, 2))
                    .Where(parts => parts.Length == 2
                        && parts[1].EndsWith(".rpf", StringComparison.OrdinalIgnoreCase))
                    .Select(parts => parts[1])
                    .ToArray();
                if (destinations.Length == 0)
                {
                    Console.Error.WriteLine("ERROR: Manifest contains no nested RPFs.");
                    return 4;
                }

                int converted = 0;
                foreach (string relative in destinations)
                {
                    string path = Path.GetFullPath(Path.Combine(
                        outputRoot,
                        relative.Replace('/', Path.DirectorySeparatorChar)
                            .TrimStart(Path.DirectorySeparatorChar)));
                    if (!path.StartsWith(outputPrefix, StringComparison.OrdinalIgnoreCase)
                        || !File.Exists(path))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Unsafe or missing staged RPF: {relative}");
                        return 4;
                    }

                    var rpf = new RpfFile(path, path);
                    rpf.ScanStructure(null,
                        err => Console.Error.WriteLine($"RPF scan warning: {err}"));
                    RpfFile.EnsureValidEncryption(rpf, null, false);
                    if (!RpfFile.IsValidEncryption(rpf, false))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Could not convert staged RPF to Open: {relative}");
                        return 5;
                    }
                    converted++;
                    Console.WriteLine($"Converted staged RPF to Open: {relative}");
                }

                Console.WriteLine($"Converted {converted} staged RPFs to Open encryption.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Open RPF conversion failed: {ex.Message}");
                return 99;
            }
        }

        private const string EroBehavioursSha256 =
            "884805ff75e2e40b8a27f6909c2bcc2f7f7024687e0288a6f6126f2ec49793b5";
        private const string EroPhysicsTasksSha256 =
            "d06f620686ccddbc94e260dfc9a5e0d7aa3e0caa392541009ad6405bd2d0a9a0";

        private sealed class EuphoriaArchiveSpec
        {
            internal string Archive;
            internal Dictionary<string, byte[]> Entries;
        }

        static string RelativeRpfEntryPath(RpfFile rpf, RpfEntry entry)
        {
            string normalized = entry.Path.Replace('\\', '/');
            string authoredPrefix = (rpf.Path ?? string.Empty)
                .Replace('\\', '/').TrimEnd('/') + "/";
            if (normalized.StartsWith(
                    authoredPrefix, StringComparison.OrdinalIgnoreCase))
                return normalized.Substring(authoredPrefix.Length);
            string physicalPrefix = Path.GetFullPath(rpf.Path)
                .Replace('\\', '/').TrimEnd('/') + "/";
            return normalized.StartsWith(
                    physicalPrefix, StringComparison.OrdinalIgnoreCase)
                ? normalized.Substring(physicalPrefix.Length)
                : normalized.TrimStart('/');
        }

        static RpfFileEntry FindExactFileEntry(RpfFile rpf, string entryPath)
        {
            string requested = entryPath.Replace('\\', '/').Trim('/');
            var matches = rpf.AllEntries?
                .OfType<RpfFileEntry>()
                .Where(entry =>
                {
                    string relative = RelativeRpfEntryPath(rpf, entry);
                    return string.Equals(relative, requested,
                            StringComparison.OrdinalIgnoreCase)
                        || relative.EndsWith("/" + requested,
                            StringComparison.OrdinalIgnoreCase);
                })
                .ToArray() ?? Array.Empty<RpfFileEntry>();
            if (matches.Length > 1)
                throw new InvalidOperationException(
                    "RPF entry path is ambiguous: " + entryPath);
            return matches.SingleOrDefault();
        }

        static RpfDirectoryEntry FindExactDirectory(RpfFile rpf, string directoryPath)
        {
            string requested = directoryPath.Replace('\\', '/').Trim('/');
            if (string.IsNullOrEmpty(requested)) return rpf.Root;
            var matches = rpf.AllEntries?
                .OfType<RpfDirectoryEntry>()
                .Where(entry =>
                {
                    string relative = RelativeRpfEntryPath(rpf, entry).TrimEnd('/');
                    return string.Equals(relative, requested,
                            StringComparison.OrdinalIgnoreCase)
                        || relative.EndsWith("/" + requested,
                            StringComparison.OrdinalIgnoreCase);
                })
                .ToArray() ?? Array.Empty<RpfDirectoryEntry>();
            if (matches.Length > 1)
                throw new InvalidOperationException(
                    "RPF directory path is ambiguous: " + directoryPath);
            return matches.SingleOrDefault();
        }

        static RpfEntry FindExactEntry(RpfFile rpf, string entryPath)
        {
            string requested = entryPath.Replace('\\', '/').Trim('/');
            if (string.IsNullOrEmpty(requested)) return null;
            var matches = rpf.AllEntries?
                .Where(entry =>
                {
                    string relative = RelativeRpfEntryPath(rpf, entry).TrimEnd('/');
                    return string.Equals(relative, requested,
                            StringComparison.OrdinalIgnoreCase)
                        || relative.EndsWith("/" + requested,
                            StringComparison.OrdinalIgnoreCase);
                })
                .ToArray() ?? Array.Empty<RpfEntry>();
            if (matches.Length > 1)
                throw new InvalidOperationException(
                    "RPF entry path is ambiguous: " + entryPath);
            return matches.SingleOrDefault();
        }

        static RpfFile OpenWritableRpf(string gtaPath, string rpfPath)
        {
            bool isGen9 = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"))
                       || File.Exists(Path.Combine(gtaPath, "eboot.bin"));
            GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
            // Keep the physical path in FilePath but use a virtual root name for
            // CodeWalker's entry graph. Drive-colons in the virtual path make
            // otherwise valid Enhanced binary entries fail structural scanning.
            string virtualRoot = Path.GetFileName(rpfPath);
            var rpf = new RpfFile(rpfPath, virtualRoot);
            rpf.ScanStructure(null,
                err => Console.Error.WriteLine($"RPF scan warning: {err}"));
            if (rpf.AllEntries == null || rpf.AllEntries.Count == 0)
                throw new InvalidDataException("RPF scan returned no entries.");
            RpfFile.EnsureValidEncryption(rpf, null, true);
            rpf = new RpfFile(rpfPath, virtualRoot);
            rpf.ScanStructure(null,
                err => Console.Error.WriteLine($"RPF reopen warning: {err}"));
            return rpf;
        }

        static int ReplaceEntry(string[] args)
        {
            if (args.Length < 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe replace-entry <gta_path> <rpf_path> <entry_path> <payload>");
                return 1;
            }
            string gtaPath = args[1];
            string rpfPath = args[2];
            string entryPath = args[3].Replace('\\', '/').Trim('/');
            string payloadPath = args[4];
            if (!File.Exists(rpfPath) || !File.Exists(payloadPath))
            {
                Console.Error.WriteLine("ERROR: RPF or payload file not found.");
                return 4;
            }
            if (entryPath.Contains("../") || entryPath.Contains("/..")
                || Path.IsPathRooted(entryPath))
            {
                Console.Error.WriteLine("ERROR: Unsafe RPF entry path.");
                return 4;
            }
            try
            {
                var rpf = OpenWritableRpf(gtaPath, rpfPath);
                var existing = FindExactFileEntry(rpf, entryPath);
                string name = entryPath.Split('/').Last();
                byte[] data = File.ReadAllBytes(payloadPath);
                if (existing != null)
                {
                    RpfFile.CreateFile(existing.Parent, existing.Name, data, true);
                    Console.WriteLine($"Replaced RPF entry: {entryPath} ({data.Length:N0} bytes)");
                }
                else
                {
                    int separator = entryPath.LastIndexOf('/');
                    string parentPath = separator >= 0
                        ? entryPath.Substring(0, separator) : string.Empty;
                    var parent = FindExactDirectory(rpf, parentPath);
                    if (parent == null)
                    {
                        Console.Error.WriteLine(
                            $"ERROR: RPF target directory not found: {parentPath}");
                        return 5;
                    }
                    RpfFile.CreateFile(parent, name, data, true);
                    Console.WriteLine($"Added RPF entry: {entryPath} ({data.Length:N0} bytes)");
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: RPF entry replacement failed: {ex.Message}");
                return 99;
            }
        }

        static int DeleteEntry(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe delete-entry <gta_path> <rpf_path> <entry_path>");
                return 1;
            }
            string gtaPath = args[1];
            string rpfPath = args[2];
            string entryPath = args[3].Replace('\\', '/').Trim('/');
            if (!File.Exists(rpfPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {rpfPath}");
                return 4;
            }
            try
            {
                var rpf = OpenWritableRpf(gtaPath, rpfPath);
                var existing = FindExactFileEntry(rpf, entryPath);
                if (existing == null)
                {
                    Console.WriteLine($"No changes needed; RPF entry is absent: {entryPath}");
                    return 0;
                }
                RpfFile.DeleteEntry(existing);
                Console.WriteLine($"Deleted RPF entry: {entryPath}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: RPF entry deletion failed: {ex.Message}");
                return 99;
            }
        }

        // Apply a reviewed set of exact root-archive entry operations after one
        // writable scan. Each TSV line is action<TAB>entry/path<TAB>argument.
        // File add/replace arguments are payload paths, rename arguments are the
        // destination entry path, and delete/mkdir/rmdir arguments are empty.
        // Nested archive orchestration
        // is handled by the SDK transaction layer and reaches this command as a
        // verified replacement of the immediate child RPF.
        static int ApplyEntryChanges(string[] args)
        {
            if (args.Length < 5)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe apply-entry-changes <gta_path> <rpf_path> <manifest_tsv> <payload_root>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string rpfPath = Path.GetFullPath(args[2]);
            string manifestPath = Path.GetFullPath(args[3]);
            string payloadRoot = Path.GetFullPath(args[4]);
            if (!File.Exists(rpfPath) || !File.Exists(manifestPath)
                || !Directory.Exists(payloadRoot))
            {
                Console.Error.WriteLine(
                    "ERROR: RPF, change manifest, or payload root not found.");
                return 4;
            }
            try
            {
                var changes = File.ReadAllLines(manifestPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line)
                        && !line.TrimStart().StartsWith("#"))
                    .Select(line => line.Split(new[] { '\t' }, 3))
                    .ToArray();
                if (changes.Length == 0 || changes.Any(parts => parts.Length != 3))
                {
                    Console.Error.WriteLine("ERROR: Entry-change manifest is empty or malformed.");
                    return 4;
                }
                var targets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var results = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                string payloadPrefix = payloadRoot.TrimEnd(
                    Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                    + Path.DirectorySeparatorChar;
                foreach (string[] change in changes)
                {
                    string action = change[0].Trim().ToLowerInvariant();
                    string entryPath = change[1].Replace('\\', '/').Trim('/');
                    if (action != "replace" && action != "add" && action != "delete"
                        && action != "mkdir" && action != "rmdir" && action != "rename")
                    {
                        Console.Error.WriteLine($"ERROR: Invalid RPF action: {change[0]}");
                        return 4;
                    }
                    if (string.IsNullOrWhiteSpace(entryPath)
                        || entryPath.Split('/').Any(part => string.IsNullOrEmpty(part)
                            || part == "." || part == "..")
                        || Path.IsPathRooted(entryPath) || !targets.Add(entryPath))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Unsafe or duplicate RPF target: {change[1]}");
                        return 4;
                    }
                    if (action == "rename")
                    {
                        string destination = change[2].Replace('\\', '/').Trim('/');
                        int sourceSeparator = entryPath.LastIndexOf('/');
                        int destinationSeparator = destination.LastIndexOf('/');
                        string sourceParent = sourceSeparator >= 0
                            ? entryPath.Substring(0, sourceSeparator) : string.Empty;
                        string destinationParent = destinationSeparator >= 0
                            ? destination.Substring(0, destinationSeparator) : string.Empty;
                        if (string.IsNullOrWhiteSpace(destination)
                            || destination.Split('/').Any(part => string.IsNullOrEmpty(part)
                                || part == "." || part == "..")
                            || Path.IsPathRooted(destination)
                            || !sourceParent.Equals(destinationParent,
                                StringComparison.OrdinalIgnoreCase)
                            || destination.Equals(entryPath,
                                StringComparison.OrdinalIgnoreCase)
                            || !results.Add(destination))
                        {
                            Console.Error.WriteLine(
                                $"ERROR: Unsafe or duplicate RPF rename destination: {change[2]}");
                            return 4;
                        }
                        continue;
                    }
                    if (action == "delete" || action == "mkdir" || action == "rmdir")
                    {
                        if (!string.IsNullOrEmpty(change[2]))
                        {
                            Console.Error.WriteLine(
                                $"ERROR: {action} action has an argument: {entryPath}");
                            return 4;
                        }
                        if (action == "mkdir" && !results.Add(entryPath))
                        {
                            Console.Error.WriteLine(
                                $"ERROR: Duplicate RPF result target: {entryPath}");
                            return 4;
                        }
                        continue;
                    }
                    if (!results.Add(entryPath))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Duplicate RPF result target: {entryPath}");
                        return 4;
                    }
                    string relative = change[2]
                        .Replace('/', Path.DirectorySeparatorChar)
                        .TrimStart(Path.DirectorySeparatorChar);
                    string payload = Path.GetFullPath(Path.Combine(payloadRoot, relative));
                    if (!payload.StartsWith(payloadPrefix, StringComparison.OrdinalIgnoreCase)
                        || !File.Exists(payload))
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Unsafe or missing payload for {entryPath}: {change[2]}");
                        return 4;
                    }
                }

                var rpf = OpenWritableRpf(gtaPath, rpfPath);
                // Validate the entire manifest before the first change. The SDK also
                // operates only on a disposable staged archive, but this keeps the
                // helper deterministic when called independently.
                var plannedDirectories = new HashSet<string>(
                    changes.Where(change => change[0].Trim().Equals(
                        "mkdir", StringComparison.OrdinalIgnoreCase))
                    .Select(change => change[1].Replace('\\', '/').Trim('/')),
                    StringComparer.OrdinalIgnoreCase);
                foreach (string[] change in changes)
                {
                    string action = change[0].Trim().ToLowerInvariant();
                    string entryPath = change[1].Replace('\\', '/').Trim('/');
                    var existing = FindExactEntry(rpf, entryPath);
                    if ((action == "add" || action == "mkdir") && existing != null)
                        throw new InvalidDataException(
                            $"{action} target already exists: {entryPath}");
                    if ((action == "replace" || action == "delete") && existing == null)
                        throw new InvalidDataException(
                            $"{action} target does not exist: {entryPath}");
                    if ((action == "replace" || action == "delete")
                        && !(existing is RpfFileEntry))
                        throw new InvalidDataException(
                            $"{action} target is not a file: {entryPath}");
                    if (action == "rmdir" && !(existing is RpfDirectoryEntry))
                        throw new InvalidDataException(
                            $"rmdir target is not a directory: {entryPath}");
                    if (action == "rename")
                    {
                        if (existing == null)
                            throw new InvalidDataException(
                                $"rename target does not exist: {entryPath}");
                        if (existing is RpfFileEntry
                            && existing.Name.EndsWith(".rpf",
                                StringComparison.OrdinalIgnoreCase))
                            throw new InvalidDataException(
                                $"rename does not support archive entries: {entryPath}");
                        string destination = change[2].Replace('\\', '/').Trim('/');
                        if (FindExactEntry(rpf, destination) != null)
                            throw new InvalidDataException(
                                $"rename destination already exists: {destination}");
                    }
                    if (action == "add" || action == "mkdir")
                    {
                        int separator = entryPath.LastIndexOf('/');
                        string parentPath = separator >= 0
                            ? entryPath.Substring(0, separator) : string.Empty;
                        if (FindExactDirectory(rpf, parentPath) == null
                            && !plannedDirectories.Contains(parentPath))
                            throw new InvalidDataException(
                                $"RPF target directory not found: {parentPath}");
                    }
                }

                int applied = 0;
                var ordered = changes
                    .OrderBy(change =>
                    {
                        string action = change[0].Trim().ToLowerInvariant();
                        if (action == "mkdir") return 0;
                        if (action == "rename") return 1;
                        if (action == "replace" || action == "add") return 2;
                        if (action == "delete") return 3;
                        return 4;
                    })
                    .ThenBy(change =>
                    {
                        string action = change[0].Trim().ToLowerInvariant();
                        int depth = change[1].Count(character => character == '/');
                        return action == "rmdir" ? -depth : depth;
                    })
                    .ToArray();
                foreach (string[] change in ordered)
                {
                    string action = change[0].Trim().ToLowerInvariant();
                    string entryPath = change[1].Replace('\\', '/').Trim('/');
                    var existing = FindExactEntry(rpf, entryPath);
                    if (action == "delete")
                    {
                        RpfFile.DeleteEntry(existing);
                        Console.WriteLine($"Deleted RPF entry: {entryPath}");
                    }
                    else if (action == "mkdir")
                    {
                        int separator = entryPath.LastIndexOf('/');
                        string parentPath = separator >= 0
                            ? entryPath.Substring(0, separator) : string.Empty;
                        string name = entryPath.Split('/').Last();
                        var parent = FindExactDirectory(rpf, parentPath);
                        RpfFile.CreateDirectory(parent, name);
                        Console.WriteLine($"Created RPF directory: {entryPath}");
                    }
                    else if (action == "rmdir")
                    {
                        var directory = existing as RpfDirectoryEntry;
                        if (directory.Directories.Count != 0 || directory.Files.Count != 0)
                            throw new InvalidDataException(
                                $"rmdir target is not empty: {entryPath}");
                        RpfFile.DeleteEntry(directory);
                        Console.WriteLine($"Removed empty RPF directory: {entryPath}");
                    }
                    else if (action == "rename")
                    {
                        string destination = change[2].Replace('\\', '/').Trim('/');
                        string name = destination.Split('/').Last();
                        RpfFile.RenameEntry(existing, name);
                        Console.WriteLine($"Renamed RPF entry: {entryPath} -> {destination}");
                    }
                    else
                    {
                        string relative = change[2]
                            .Replace('/', Path.DirectorySeparatorChar)
                            .TrimStart(Path.DirectorySeparatorChar);
                        byte[] data = File.ReadAllBytes(Path.Combine(payloadRoot, relative));
                        if (action == "replace")
                        {
                            var file = existing as RpfFileEntry;
                            RpfFile.CreateFile(file.Parent, file.Name, data, true);
                            Console.WriteLine(
                                $"Replaced RPF entry: {entryPath} ({data.Length:N0} bytes)");
                        }
                        else
                        {
                            int separator = entryPath.LastIndexOf('/');
                            string parentPath = separator >= 0
                                ? entryPath.Substring(0, separator) : string.Empty;
                            string name = entryPath.Split('/').Last();
                            var parent = FindExactDirectory(rpf, parentPath);
                            RpfFile.CreateFile(parent, name, data, true);
                            Console.WriteLine(
                                $"Added RPF entry: {entryPath} ({data.Length:N0} bytes)");
                        }
                    }
                    applied++;
                }
                Console.WriteLine($"Applied {applied:N0} RPF entry changes in one session.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: RPF batch change failed: {ex.Message}");
                return 99;
            }
        }

        private sealed class EuphoriaArchiveTarget
        {
            internal EuphoriaArchiveSpec Spec;
            internal RpfFile Rpf;
            internal string ModsPath;
        }

        // E.R.O.'s OIV writes both update.rpf and the two base fallback
        // archives. Installing all four declared entries matters on builds
        // whose load order ignores one copy. Stock archives are never edited:
        // only OpenRPF's mods copies are created or changed.
        static int InstallEuphoria(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe install-euphoria <gta_path> <payload_folder_or_oiv> [--allow-enhanced]");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string source = Path.GetFullPath(args[2]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before installing Euphoria archive tuning.");
                return 11;
            }
            bool enhanced = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"));
            bool allowEnhanced = args.Skip(3).Any(value =>
                value.Equals("--allow-enhanced", StringComparison.OrdinalIgnoreCase));
            if (enhanced && !allowEnhanced)
            {
                Console.Error.WriteLine(
                    "ERROR: E.R.O. 1.9.4 targets GTA V Legacy. Enhanced compatibility " +
                    "is experimental; rerun with --allow-enhanced to acknowledge that risk.");
                return 8;
            }

            if (!TryLoadEuphoriaPayload(source, out var payload,
                    out string sourceLabel, out string payloadError))
            {
                Console.Error.WriteLine("ERROR: " + payloadError);
                return 4;
            }

            var targets = new List<EuphoriaArchiveTarget>();
            bool tuningWritesStarted = false;
            try
            {
                EuphoriaArchiveSpec[] specs = BuildEuphoriaArchiveSpecs(payload);
                foreach (EuphoriaArchiveSpec spec in specs)
                {
                    RpfFile rpf = OpenEuphoriaArchive(
                        gtaPath, spec.Archive, true, out int errorCode);
                    if (rpf == null) return errorCode;
                    targets.Add(new EuphoriaArchiveTarget
                    {
                        Spec = spec,
                        Rpf = rpf,
                        ModsPath = GetModsArchivePath(gtaPath, spec.Archive),
                    });
                }

                // Do not write a single tuning entry until every archive can be
                // opened and every complete pre-install snapshot is available.
                foreach (EuphoriaArchiveTarget target in targets)
                    EnsureEuphoriaBackup(target.ModsPath);

                tuningWritesStarted = true;
                foreach (EuphoriaArchiveTarget target in targets)
                {
                    InstallArchiveEntries(target.Rpf, target.Spec.Entries);
                    int verification = VerifyEuphoriaArchive(
                        target.Rpf, target.Spec.Entries,
                        "mods/" + target.Spec.Archive);
                    if (verification != 0)
                    {
                        TryRollbackEuphoriaInstall(gtaPath, targets);
                        return verification;
                    }
                }
                WriteEuphoriaMarker(gtaPath, enhanced, payload, sourceLabel);
                int markerVerification = VerifyEuphoriaMarker(gtaPath, payload);
                if (markerVerification != 0)
                {
                    TryRollbackEuphoriaInstall(gtaPath, targets);
                    return markerVerification;
                }
                return 0;
            }
            catch (Exception ex)
            {
                if (tuningWritesStarted)
                    TryRollbackEuphoriaInstall(gtaPath, targets);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        static int VerifyEuphoria(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-euphoria <gta_path> <payload_folder_or_oiv>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (!TryLoadEuphoriaPayload(Path.GetFullPath(args[2]),
                    out var payload, out _, out string payloadError))
            {
                Console.Error.WriteLine("ERROR: " + payloadError);
                return 4;
            }
            try
            {
                foreach (EuphoriaArchiveSpec spec in
                    BuildEuphoriaArchiveSpecs(payload))
                {
                    RpfFile rpf = OpenEuphoriaArchive(
                        gtaPath, spec.Archive, false, out int errorCode);
                    if (rpf == null) return errorCode;
                    int result = VerifyEuphoriaArchive(
                        rpf, spec.Entries, "mods/" + spec.Archive);
                    if (result != 0) return result;
                }
                int markerResult = VerifyEuphoriaMarker(gtaPath, payload);
                if (markerResult != 0) return markerResult;
                Console.WriteLine("All Euphoria archive tuning entries verified.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int ValidateEuphoria(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe validate-euphoria <payload_folder_or_archive>");
                return 1;
            }
            if (!TryLoadEuphoriaPayload(Path.GetFullPath(args[1]),
                    out var payload, out string sourceLabel,
                    out string payloadError))
            {
                Console.Error.WriteLine("ERROR: " + payloadError);
                return 4;
            }
            Console.WriteLine($"Euphoria payload is valid: {sourceLabel}");
            Console.WriteLine(
                $"  behaviours.xml: {payload["behaviours.xml"].Length:N0} bytes");
            Console.WriteLine(
                $"  physicstasks.ymt: {payload["physicstasks.ymt"].Length:N0} bytes");
            return 0;
        }

        static int RemoveEuphoria(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe remove-euphoria <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before removing Euphoria archive tuning.");
                return 11;
            }
            string[] archives = { "update/update.rpf", "common.rpf", "x64a.rpf" };
            var restore = new List<KeyValuePair<string, string>>();
            foreach (string archive in archives)
            {
                string target = GetModsArchivePath(gtaPath, archive);
                string backup = target + ".allin1-euphoria.bak";
                if (!File.Exists(backup))
                {
                    Console.Error.WriteLine($"MISSING ROLLBACK SNAPSHOT: {backup}");
                    return 10;
                }
                restore.Add(new KeyValuePair<string, string>(target, backup));
            }
            foreach (KeyValuePair<string, string> item in restore)
            {
                string target = item.Key;
                string backup = item.Value;
                File.Copy(backup, target, true);
                Console.WriteLine($"Restored: {target}");
            }
            string marker = Path.Combine(gtaPath, "scripts",
                "ALLIN1_euphoria_tuning.json");
            if (File.Exists(marker)) File.Delete(marker);
            Console.WriteLine("Euphoria archive tuning removed; rollback snapshots retained.");
            return 0;
        }

        static bool TryLoadEuphoriaPayload(string source,
            out Dictionary<string, byte[]> payload, out string sourceLabel,
            out string error)
        {
            payload = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
            sourceLabel = source;
            error = null;
            try
            {
                if (Directory.Exists(source))
                {
                    string behaviours = Directory.GetFiles(source,
                        "behaviours.xml", SearchOption.AllDirectories).FirstOrDefault();
                    string physics = Directory.GetFiles(source,
                        "physicstasks.ymt", SearchOption.AllDirectories).FirstOrDefault();
                    if (behaviours == null || physics == null)
                    {
                        error = "Payload directory must contain behaviours.xml and physicstasks.ymt.";
                        return false;
                    }
                    payload["behaviours.xml"] = File.ReadAllBytes(behaviours);
                    payload["physicstasks.ymt"] = File.ReadAllBytes(physics);
                }
                else if (File.Exists(source))
                {
                    using (ZipArchive archive = ZipFile.OpenRead(source))
                    {
                        if (!TryReadEuphoriaEntries(archive, payload))
                        {
                            ZipArchiveEntry oiv = archive.Entries.FirstOrDefault(entry =>
                                entry.FullName.EndsWith(".oiv",
                                    StringComparison.OrdinalIgnoreCase) &&
                                entry.FullName.IndexOf("1.9.4",
                                    StringComparison.OrdinalIgnoreCase) >= 0)
                                ?? archive.Entries.FirstOrDefault(entry =>
                                    entry.FullName.EndsWith(".oiv",
                                        StringComparison.OrdinalIgnoreCase));
                            if (oiv == null)
                            {
                                error = "Archive contains neither the tuning files nor an OIV package.";
                                return false;
                            }
                            using (var memory = new MemoryStream())
                            {
                                using (Stream input = oiv.Open()) input.CopyTo(memory);
                                memory.Position = 0;
                                using (var inner = new ZipArchive(memory,
                                    ZipArchiveMode.Read, false))
                                    if (!TryReadEuphoriaEntries(inner, payload))
                                    {
                                        error = $"OIV payload is missing required tuning files: {oiv.FullName}";
                                        return false;
                                    }
                            }
                            sourceLabel += "::" + oiv.FullName;
                        }
                    }
                }
                else
                {
                    error = $"Payload source not found: {source}";
                    return false;
                }
            }
            catch (Exception ex)
            {
                error = "Could not read Euphoria payload: " + ex.Message;
                return false;
            }

            string behavioursHash = Sha256(payload["behaviours.xml"]);
            string physicsHash = Sha256(payload["physicstasks.ymt"]);
            if (!behavioursHash.Equals(EroBehavioursSha256,
                    StringComparison.OrdinalIgnoreCase) ||
                !physicsHash.Equals(EroPhysicsTasksSha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                error = "Payload does not match the audited E.R.O. 1.9.4 tuning files. " +
                    $"behaviours={behavioursHash}, physicstasks={physicsHash}";
                return false;
            }
            Console.WriteLine($"Validated E.R.O. 1.9.4 payload: {sourceLabel}");
            return true;
        }

        static bool TryReadEuphoriaEntries(ZipArchive archive,
            Dictionary<string, byte[]> payload)
        {
            ZipArchiveEntry behaviours = archive.Entries.FirstOrDefault(entry =>
                entry.FullName.EndsWith("/behaviours.xml",
                    StringComparison.OrdinalIgnoreCase) ||
                entry.FullName.Equals("behaviours.xml",
                    StringComparison.OrdinalIgnoreCase));
            ZipArchiveEntry physics = archive.Entries.FirstOrDefault(entry =>
                entry.FullName.EndsWith("/physicstasks.ymt",
                    StringComparison.OrdinalIgnoreCase) ||
                entry.FullName.Equals("physicstasks.ymt",
                    StringComparison.OrdinalIgnoreCase));
            if (behaviours == null || physics == null) return false;
            payload["behaviours.xml"] = ReadZipEntry(behaviours);
            payload["physicstasks.ymt"] = ReadZipEntry(physics);
            return true;
        }

        static byte[] ReadZipEntry(ZipArchiveEntry entry)
        {
            using (var memory = new MemoryStream())
            {
                using (Stream stream = entry.Open()) stream.CopyTo(memory);
                return memory.ToArray();
            }
        }

        static EuphoriaArchiveSpec[] BuildEuphoriaArchiveSpecs(
            Dictionary<string, byte[]> payload)
        {
            byte[] behaviours = payload["behaviours.xml"];
            byte[] physics = payload["physicstasks.ymt"];
            return new[]
            {
                new EuphoriaArchiveSpec
                {
                    Archive = "update/update.rpf",
                    Entries = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase)
                    {
                        { "common/data/naturalmotion/behaviours.xml", behaviours },
                        { "x64/data/tune/physicstasks.ymt", physics },
                    },
                },
                new EuphoriaArchiveSpec
                {
                    Archive = "common.rpf",
                    Entries = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase)
                    {
                        { "data/naturalmotion/behaviours.xml", behaviours },
                    },
                },
                new EuphoriaArchiveSpec
                {
                    Archive = "x64a.rpf",
                    Entries = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase)
                    {
                        { "data/tune/physicstasks.ymt", physics },
                    },
                },
            };
        }

        static RpfFile OpenEuphoriaArchive(string gtaPath,
            string archive, bool createModsCopy, out int errorCode)
        {
            return archive.Equals("update/update.rpf",
                    StringComparison.OrdinalIgnoreCase)
                ? OpenModsUpdateRpf(gtaPath, out errorCode, "update.rpf",
                    createModsCopy)
                : OpenModsRootRpf(gtaPath, archive, createModsCopy,
                    out errorCode);
        }

        static RpfFile OpenModsRootRpf(string gtaPath,
            string archive, bool createModsCopy, out int errorCode)
        {
            errorCode = 0;
            bool enhanced = File.Exists(Path.Combine(gtaPath,
                "GTA5_Enhanced.exe"));
            string exe = enhanced ? "GTA5_Enhanced.exe" : "GTA5.exe";
            if (!File.Exists(Path.Combine(gtaPath, exe)))
            {
                Console.Error.WriteLine($"ERROR: {exe} not found in {gtaPath}");
                errorCode = 2;
                return null;
            }
            GTA5Keys.LoadFromPath(gtaPath, enhanced, null);
            if (GTA5Keys.PC_AES_KEY == null)
            {
                Console.Error.WriteLine("ERROR: Failed to load encryption keys.");
                errorCode = 3;
                return null;
            }
            string original = Path.Combine(gtaPath,
                archive.Replace('/', Path.DirectorySeparatorChar));
            string mods = GetModsArchivePath(gtaPath, archive);
            if (!File.Exists(original))
            {
                Console.Error.WriteLine($"ERROR: Stock archive not found: {original}");
                errorCode = 4;
                return null;
            }
            Directory.CreateDirectory(Path.GetDirectoryName(mods));
            if (!File.Exists(mods))
            {
                if (!createModsCopy)
                {
                    Console.Error.WriteLine(
                        $"ERROR: Mods archive is not installed: {mods}");
                    errorCode = 7;
                    return null;
                }
                File.Copy(original, mods, false);
                Console.WriteLine($"Copied stock archive to mods: {mods}");
            }
            else if (File.GetLastWriteTimeUtc(mods).AddSeconds(1) <
                     File.GetLastWriteTimeUtc(original))
            {
                Console.Error.WriteLine(
                    $"ERROR: Mods archive predates stock archive: {mods}");
                errorCode = 6;
                return null;
            }
            var rpf = new RpfFile(mods, mods);
            rpf.ScanStructure(null,
                warning => Console.Error.WriteLine($"RPF scan warning: {warning}"));
            if (rpf.AllEntries == null || rpf.AllEntries.Count == 0)
            {
                Console.Error.WriteLine($"ERROR: RPF scan returned no entries: {mods}");
                errorCode = 4;
                return null;
            }
            if (createModsCopy)
                RpfFile.EnsureValidEncryption(rpf, null, true);
            Console.WriteLine($"Opened mods archive: {mods} ({rpf.AllEntries.Count} entries)");
            return rpf;
        }

        private static void ReloadGtaEncryptionKeys(string gtaPath)
        {
            bool isGen9 = File.Exists(Path.Combine(
                    gtaPath, "GTA5_Enhanced.exe")) ||
                File.Exists(Path.Combine(gtaPath, "eboot.bin"));
            GTA5Keys.LoadFromPath(gtaPath, isGen9, null);
            if (GTA5Keys.PC_AES_KEY == null)
                throw new InvalidDataException(
                    "Could not reload GTA encryption keys for a nested archive.");
        }

        static string GetModsArchivePath(string gtaPath, string archive)
        {
            return Path.GetFullPath(Path.Combine(gtaPath, "mods",
                archive.Replace('/', Path.DirectorySeparatorChar)));
        }

        static bool IsGtaProcessRunning()
        {
            return System.Diagnostics.Process.GetProcessesByName("GTA5").Length > 0 ||
                System.Diagnostics.Process.GetProcessesByName("GTA5_Enhanced").Length > 0;
        }

        static void EnsureEuphoriaBackup(string modsRpf)
        {
            string backup = modsRpf + ".allin1-euphoria.bak";
            if (!File.Exists(backup))
            {
                File.Copy(modsRpf, backup, false);
                Console.WriteLine($"Created rollback snapshot: {backup}");
            }
            else Console.WriteLine($"Preserving rollback snapshot: {backup}");
        }

        static void TryRollbackEuphoriaInstall(string gtaPath,
            IEnumerable<EuphoriaArchiveTarget> targets)
        {
            Console.Error.WriteLine(
                "Installation did not complete; restoring every pre-install snapshot.");
            foreach (EuphoriaArchiveTarget target in targets)
            {
                string backup = target.ModsPath + ".allin1-euphoria.bak";
                try
                {
                    if (!File.Exists(backup))
                    {
                        Console.Error.WriteLine(
                            $"ROLLBACK SNAPSHOT MISSING: {backup}");
                        continue;
                    }
                    File.Copy(backup, target.ModsPath, true);
                    Console.Error.WriteLine($"Rolled back: {target.ModsPath}");
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine(
                        $"ROLLBACK FAILED: {target.ModsPath}: {ex.Message}");
                }
            }
            string marker = GetEuphoriaMarkerPath(gtaPath);
            try
            {
                if (File.Exists(marker)) File.Delete(marker);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine(
                    $"ROLLBACK MARKER CLEANUP FAILED: {marker}: {ex.Message}");
            }
        }

        static void InstallArchiveEntries(RpfFile rpf,
            Dictionary<string, byte[]> entries)
        {
            foreach (KeyValuePair<string, byte[]> item in entries)
            {
                string[] parts = item.Key.Split('/');
                RpfDirectoryEntry directory = rpf.Root;
                for (int index = 0; index < parts.Length - 1; index++)
                {
                    string name = parts[index];
                    RpfDirectoryEntry existing = directory.Directories?
                        .FirstOrDefault(entry => entry.Name.Equals(name,
                            StringComparison.OrdinalIgnoreCase));
                    directory = existing ?? RpfFile.CreateDirectory(directory, name);
                }
                string fileName = parts[parts.Length - 1];
                RpfFile.CreateFile(directory, fileName, item.Value, true);
                Console.WriteLine($"  ~ {item.Key} ({item.Value.Length:N0} bytes, " +
                    $"sha256={Sha256(item.Value)})");
            }
        }

        static int VerifyEuphoriaArchive(RpfFile rpf,
            Dictionary<string, byte[]> payload, string label)
        {
            int failures = 0;
            foreach (KeyValuePair<string, byte[]> item in payload)
            {
                RpfFileEntry entry = FindRelativeEntry(rpf, item.Key);
                byte[] actual = entry?.File.ExtractFile(entry);
                bool matches = actual != null && actual.SequenceEqual(item.Value);
                Console.WriteLine($"  {(matches ? "OK" : "FAIL")} {label}/{item.Key}" +
                    (actual == null ? " (missing)" :
                    $" ({actual.Length:N0} bytes, sha256={Sha256(actual)})"));
                if (!matches) failures++;
            }
            if (failures > 0)
            {
                Console.Error.WriteLine(
                    $"ERROR: Euphoria payload verification failed for {label} ({failures} entries). ");
                return 9;
            }
            Console.WriteLine($"Euphoria archive tuning verified in {label}.");
            return 0;
        }

        static RpfFileEntry FindRelativeEntry(RpfFile rpf, string requested)
        {
            string normalized = requested.Replace('\\', '/').TrimStart('/');
            string archivePrefix = Path.GetFullPath(rpf.FilePath)
                .Replace('\\', '/').TrimEnd('/') + "/";
            RpfFileEntry[] matches = rpf.AllEntries?
                .OfType<RpfFileEntry>()
                .Where(entry =>
                {
                    string path = entry.Path.Replace('\\', '/');
                    string relative = path.StartsWith(archivePrefix,
                            StringComparison.OrdinalIgnoreCase)
                        ? path.Substring(archivePrefix.Length)
                        : path.TrimStart('/');
                    return relative.Equals(normalized,
                        StringComparison.OrdinalIgnoreCase);
                }).ToArray() ?? Array.Empty<RpfFileEntry>();
            if (matches.Length > 1)
                throw new InvalidDataException(
                    $"Archive-relative entry is ambiguous: {requested}");
            return matches.Length == 1 ? matches[0] : null;
        }

        static string Sha256(byte[] data)
        {
            using (SHA256 algorithm = SHA256.Create())
                return BitConverter.ToString(algorithm.ComputeHash(data))
                    .Replace("-", "").ToLowerInvariant();
        }

        static string Sha256File(string path)
        {
            using (FileStream stream = File.OpenRead(path))
            using (SHA256 algorithm = SHA256.Create())
                return BitConverter.ToString(algorithm.ComputeHash(stream))
                    .Replace("-", "").ToLowerInvariant();
        }

        static void WriteEuphoriaMarker(string gtaPath, bool enhanced,
            Dictionary<string, byte[]> payload, string source)
        {
            string scripts = Path.Combine(gtaPath, "scripts");
            Directory.CreateDirectory(scripts);
            string marker = GetEuphoriaMarkerPath(gtaPath);
            string json = "{\n" +
                $"  \"installed_utc\": \"{DateTime.UtcNow:o}\",\n" +
                $"  \"edition\": \"{(enhanced ? "Enhanced" : "Legacy")}\",\n" +
                "  \"archives\": [\"mods/update/update.rpf\", \"mods/common.rpf\", \"mods/x64a.rpf\"],\n" +
                $"  \"source\": \"{JsonEscape(source)}\",\n" +
                $"  \"behaviours_sha256\": \"{Sha256(payload["behaviours.xml"])}\",\n" +
                $"  \"physicstasks_sha256\": \"{Sha256(payload["physicstasks.ymt"])}\"\n" +
                "}\n";
            File.WriteAllText(marker, json, new UTF8Encoding(false));
            Console.WriteLine($"Wrote archive tuning marker: {marker}");
        }

        static int VerifyEuphoriaMarker(string gtaPath,
            Dictionary<string, byte[]> payload)
        {
            string marker = GetEuphoriaMarkerPath(gtaPath);
            if (!File.Exists(marker))
            {
                Console.Error.WriteLine(
                    $"ERROR: Euphoria archive marker is missing: {marker}");
                return 12;
            }
            string json = File.ReadAllText(marker);
            string[] expected =
            {
                "mods/update/update.rpf",
                "mods/common.rpf",
                "mods/x64a.rpf",
                Sha256(payload["behaviours.xml"]),
                Sha256(payload["physicstasks.ymt"]),
            };
            foreach (string value in expected)
            {
                if (json.IndexOf(value, StringComparison.OrdinalIgnoreCase) >= 0)
                    continue;
                Console.Error.WriteLine(
                    $"ERROR: Euphoria archive marker is incomplete: {value}");
                return 12;
            }
            Console.WriteLine($"Euphoria archive marker verified: {marker}");
            return 0;
        }

        static string GetEuphoriaMarkerPath(string gtaPath)
        {
            return Path.Combine(gtaPath, "scripts",
                "ALLIN1_euphoria_tuning.json");
        }

        static string JsonEscape(string value)
        {
            return (value ?? "").Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r").Replace("\n", "\\n");
        }

        static int DumpYtd(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine("Usage: RpfPatcher.exe dump-ytd <ytd_path> [legacy|gen9]");
                return 1;
            }

            string ytdPath = args[1];
            bool isGen9 = args.Length < 3 ||
                !args[2].Equals("legacy", StringComparison.OrdinalIgnoreCase);
            if (!File.Exists(ytdPath))
            {
                Console.Error.WriteLine($"ERROR: File not found: {ytdPath}");
                return 4;
            }

            var previous = RpfManager.IsGen9;
            try
            {
                RpfManager.IsGen9 = isGen9;
                var ytd = new YtdFile();
                ytd.Load(File.ReadAllBytes(ytdPath));
                var textures = ytd.TextureDict?.Textures?.data_items ?? Array.Empty<Texture>();
                Console.WriteLine(
                    $"YTD: {ytdPath} mode={(isGen9 ? "gen9" : "legacy")} textures={textures.Length}");
                foreach (var texture in textures)
                {
                    if (texture == null) continue;
                    Console.WriteLine(
                        $"  {texture.Name}: {texture.Width}x{texture.Height}x{texture.Depth}, " +
                        $"levels={texture.Levels}, legacy={texture.Format}, stride={texture.Stride}, " +
                        $"g9format={texture.G9_Format}, flags=0x{texture.G9_Flags:X8}, " +
                        $"blocks={texture.G9_BlockCount}, blockStride={texture.G9_BlockStride}, " +
                        $"tile={texture.G9_TileMode}, data={texture.Data?.FullData?.Length ?? 0}, " +
                        $"srv={(texture.G9_SRV == null ? "none" : "present")}");
                }
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
            finally
            {
                RpfManager.IsGen9 = previous;
            }
        }

        static int VerifyMapDlc(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-map-dlc <dlc_rpf> <manifest_tsv>");
                return 1;
            }

            string dlcPath = args[1];
            string manifestPath = args[2];
            if (!File.Exists(dlcPath) || !File.Exists(manifestPath))
            {
                Console.Error.WriteLine("ERROR: Map DLC or manifest not found.");
                return 4;
            }

            try
            {
                var expected = File.ReadAllLines(manifestPath)
                    .Where(line => !string.IsNullOrWhiteSpace(line)
                        && !line.TrimStart().StartsWith("#"))
                    .Select(line => line.Split(new[] { '\t' }, 2))
                    .ToArray();
                if (expected.Length == 0 || expected.Any(parts => parts.Length != 2))
                {
                    Console.Error.WriteLine("ERROR: Map manifest is empty or malformed.");
                    return 4;
                }

                var rpf = new RpfFile(dlcPath, dlcPath);
                rpf.ScanStructure(null,
                    err => Console.Error.WriteLine($"RPF scan warning: {err}"));
                if (FindFileRecursive(rpf, "content.xml") == null
                    || FindFileRecursive(rpf, "setup2.xml") == null)
                {
                    Console.Error.WriteLine(
                        "ERROR: Map DLC is missing content.xml or setup2.xml.");
                    return 5;
                }

                string archivePrefix = Path.GetFullPath(dlcPath)
                    .Replace('\\', '/').TrimEnd('/') + "/";
                foreach (string[] request in expected)
                {
                    string destination = request[1].Replace('\\', '/').TrimStart('/');
                    var matches = rpf.AllEntries?
                        .OfType<RpfFileEntry>()
                        .Where(entry => string.Equals(
                            entry.Path.Replace('\\', '/').StartsWith(
                                archivePrefix, StringComparison.OrdinalIgnoreCase)
                                ? entry.Path.Replace('\\', '/').Substring(
                                    archivePrefix.Length)
                                : entry.Path.Replace('\\', '/'),
                            destination, StringComparison.OrdinalIgnoreCase))
                        .ToArray() ?? Array.Empty<RpfFileEntry>();
                    if (matches.Length != 1)
                    {
                        Console.Error.WriteLine(
                            $"ERROR: Expected one packed entry for {destination}; found {matches.Length}.");
                        return 5;
                    }

                    byte[] data = matches[0].File.ExtractFile(matches[0]);
                    if (data == null || data.Length == 0)
                    {
                        Console.Error.WriteLine($"ERROR: Packed entry is empty: {destination}");
                        return 5;
                    }
                    if (destination.EndsWith(".rpf", StringComparison.OrdinalIgnoreCase))
                    {
                        string temporary = Path.Combine(
                            Path.GetTempPath(), $"allin1-map-verify-{Guid.NewGuid():N}.rpf");
                        try
                        {
                            File.WriteAllBytes(temporary, data);
                            var nested = new RpfFile(temporary, temporary);
                            nested.ScanStructure(null,
                                err => Console.Error.WriteLine(
                                    $"Nested RPF scan warning: {err}"));
                            if (nested.Encryption != RpfEncryption.OPEN)
                            {
                                Console.Error.WriteLine(
                                    $"ERROR: Packed nested archive is not Open: {destination}");
                                return 5;
                            }
                        }
                        finally
                        {
                            if (File.Exists(temporary)) File.Delete(temporary);
                        }
                    }
                }

                Console.WriteLine(
                    $"Verified standalone map DLC: {expected.Length} local assets present.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Map DLC verification failed: {ex.Message}");
                return 7;
            }
        }

        private sealed class ColoredSmokeWeaponSpec
        {
            internal string Color;
            internal string DisplayName;
            internal string WeaponName;
            internal string AmmoName;
            internal string SlotName;
            internal string HumanNameLabel;
            internal string DescriptionLabel;
            internal string TooltipLabel;
            internal string UppercaseLabel;
            internal string LockHash;
        }

        private const string BaseWeaponsMetaPath =
            "common/data/ai/weapons.meta";
        private const string BaseWeaponAnimationsMetaPath =
            "common/data/ai/weaponanimations.meta";
        private const string BaseAmericanLanguageArchivePath =
            "x64/patch/data/lang/american_rel.rpf";
        private const string BaseScaleformGenericArchivePath =
            "x64/data/cdimages/scaleform_generic.rpf";
        private const string MergedSmokeCanaryMarkerName =
            "ALLIN1_colored_smoke_merged_canary.json";

        private static readonly ColoredSmokeWeaponSpec[] ColoredSmokeWeapons =
        {
            ColoredSmokeSpec("white", "White Smoke"),
            ColoredSmokeSpec("red", "Red Smoke"),
            ColoredSmokeSpec("orange", "Orange Smoke"),
            ColoredSmokeSpec("yellow", "Yellow Smoke"),
            ColoredSmokeSpec("green", "Green Smoke"),
            ColoredSmokeSpec("blue", "Blue Smoke"),
            ColoredSmokeSpec("purple", "Purple Smoke"),
        };

        private static ColoredSmokeWeaponSpec ColoredSmokeSpec(
            string color, string displayName)
        {
            string suffix = color.ToUpperInvariant();
            string labelSuffix = suffix.Substring(
                0, Math.Min(3, suffix.Length));
            return new ColoredSmokeWeaponSpec
            {
                Color = color,
                DisplayName = displayName,
                WeaponName = "WEAPON_ALLIN1_SMOKE_" + suffix,
                AmmoName = "AMMO_ALLIN1_SMOKE_" + suffix,
                SlotName = "SLOT_ALLIN1_SMOKE_" + suffix,
                HumanNameLabel = "WT_A1SM" + labelSuffix,
                DescriptionLabel = "WTD_A1SM" + labelSuffix,
                TooltipLabel = "WTT_A1SM" + labelSuffix,
                UppercaseLabel = "WTU_A1SM" + labelSuffix,
                LockHash = "CU_WEP_A1SM" + labelSuffix,
            };
        }

        static int InstallColoredSmokeWeapons(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe install-colored-smoke-weapons <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before installing colored smoke weapons.");
                return 11;
            }

            string staging = Path.Combine(Path.GetTempPath(),
                $"allin1-colored-smoke-install-{Guid.NewGuid():N}");
            string destination = Path.Combine(gtaPath, "mods", "update",
                "x64", "dlcpacks", "allin1_smoke", "dlc.rpf");
            string backup = destination + ".allin1.bak";
            bool destinationWritten = false;
            try
            {
                string output = Path.Combine(staging, "dlc.rpf");
                int buildResult = BuildColoredSmokeDlcArtifact(
                    gtaPath, output);
                if (buildResult != 0) return buildResult;

                Directory.CreateDirectory(Path.GetDirectoryName(destination));
                if (File.Exists(destination) && !File.Exists(backup))
                    File.Copy(destination, backup, false);
                File.Copy(output, destination, true);
                destinationWritten = true;

                int patchResult = PatchCommand("patch", new[]
                {
                    "patch", gtaPath, "allin1_smoke",
                });
                if (patchResult != 0)
                {
                    RestoreColoredSmokeDestination(destination, backup);
                    destinationWritten = false;
                    return patchResult;
                }
                WriteColoredSmokeMarker(gtaPath, destination);
                Console.WriteLine(
                    "Installed seven independent ALLIN1 smoke weapons and ammo pools.");
                return 0;
            }
            catch (Exception ex)
            {
                if (destinationWritten)
                    RestoreColoredSmokeDestination(destination, backup);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
            finally
            {
                if (Directory.Exists(staging))
                    Directory.Delete(staging, true);
            }
        }

        static int BuildColoredSmokeWeapons(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-colored-smoke-weapons " +
                    "<gta_path> <output_rpf>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before reading its weapon archives.");
                return 11;
            }
            return BuildColoredSmokeDlcArtifact(gtaPath, output);
        }

        static int BuildColoredSmokeDlcArtifact(
            string gtaPath, string output)
        {
            string staging = Path.Combine(Path.GetTempPath(),
                $"allin1-colored-smoke-build-{Guid.NewGuid():N}");
            try
            {
                RpfFile update = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (update == null) return errorCode;
                byte[] sourceWeapons = ExtractRequiredEntry(
                    update, "common/data/ai/weapons.meta");

                string outer = Path.Combine(staging, "outer");
                string language = Path.Combine(staging, "language");
                Directory.CreateDirectory(Path.Combine(
                    outer, "common", "data", "ai"));
                Directory.CreateDirectory(Path.Combine(
                    outer, "common", "data"));
                Directory.CreateDirectory(language);
                string outputDirectory = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(outputDirectory))
                    Directory.CreateDirectory(outputDirectory);

                File.WriteAllBytes(Path.Combine(outer, "common", "data", "ai",
                    "weaponAllin1Smoke.meta"),
                    BuildColoredSmokeWeaponMeta(sourceWeapons));
                File.WriteAllText(Path.Combine(outer, "common", "data",
                    "shop_weapon.meta"), BuildColoredSmokeShopMeta(),
                    new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(outer, "content.xml"),
                    BuildColoredSmokeContentXml(), new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(outer, "setup2.xml"),
                    BuildColoredSmokeSetupXml(), new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(outer, "common", "data",
                    "dlctext.meta"), BuildColoredSmokeTextMeta(),
                    new UTF8Encoding(false));
                File.WriteAllBytes(Path.Combine(language, "global.gxt2"),
                    BuildColoredSmokeGxt2());

                int buildResult = BuildDlc(new[]
                {
                    "build-dlc", outer, output,
                    "--embed-rpf", language,
                    "x64/data/lang/americandlc.rpf",
                    "--gta-path", gtaPath,
                });
                if (buildResult != 0)
                {
                    if (File.Exists(output)) File.Delete(output);
                    return buildResult;
                }
                int verifyResult = VerifyColoredSmokeDlc(output);
                if (verifyResult != 0)
                {
                    if (File.Exists(output)) File.Delete(output);
                    return verifyResult;
                }
                Console.WriteLine(
                    $"Built boot-schema-validated colored smoke DLC: {output}");
                return 0;
            }
            catch (Exception ex)
            {
                if (File.Exists(output)) File.Delete(output);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
            finally
            {
                if (Directory.Exists(staging))
                    Directory.Delete(staging, true);
            }
        }

        static int VerifyColoredSmokeWeapons(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-colored-smoke-weapons <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string archive = Path.Combine(gtaPath, "mods", "update", "x64",
                "dlcpacks", "allin1_smoke", "dlc.rpf");
            int result = VerifyColoredSmokeDlc(archive);
            if (result != 0) return result;
            string marker = GetColoredSmokeMarkerPath(gtaPath);
            if (!File.Exists(marker) ||
                File.ReadAllText(marker).IndexOf(Sha256(
                    File.ReadAllBytes(archive)),
                    StringComparison.OrdinalIgnoreCase) < 0)
            {
                Console.Error.WriteLine(
                    "ERROR: Colored smoke weapon marker is missing or stale.");
                return 12;
            }
            Console.WriteLine("ALLIN1 colored smoke weapon DLC verified.");
            return 0;
        }

        static int RemoveColoredSmokeWeapons(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe remove-colored-smoke-weapons <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before removing colored smoke weapons.");
                return 11;
            }
            int patchResult = PatchCommand("unpatch", new[]
            {
                "unpatch", gtaPath, "allin1_smoke",
            });
            if (patchResult != 0) return patchResult;
            string destination = Path.Combine(gtaPath, "mods", "update",
                "x64", "dlcpacks", "allin1_smoke", "dlc.rpf");
            if (File.Exists(destination)) File.Delete(destination);
            string marker = GetColoredSmokeMarkerPath(gtaPath);
            if (File.Exists(marker)) File.Delete(marker);
            Console.WriteLine("Removed ALLIN1 colored smoke weapon DLC.");
            return 0;
        }

        static int BuildMergedSmokeCanary(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-merged-smoke-canary " +
                    "<gta_path> <output_meta>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before reading its base weapon data.");
                return 11;
            }
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (rpf == null) return errorCode;
                byte[] source = ExtractRequiredEntry(rpf,
                    BaseWeaponsMetaPath);
                byte[] merged = BuildMergedSmokeWeaponMeta(source, 1);
                ValidateMergedSmokeWeaponMeta(source, merged, 1);
                string directory = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(directory))
                    Directory.CreateDirectory(directory);
                WriteAtomicFile(output, merged);
                Console.WriteLine(
                    $"Built White Smoke base-meta canary: {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int BuildMergedSmokeWeapons(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe build-merged-smoke-weapons " +
                    "<gta_path> <output_meta>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string output = Path.GetFullPath(args[2]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before reading its base weapon data.");
                return 11;
            }
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (rpf == null) return errorCode;
                byte[] current = ExtractRequiredEntry(rpf,
                    BaseWeaponsMetaPath);
                byte[] source = current;
                if (ContainsAllin1SmokeDefinitions(current))
                {
                    string backup = GetMergedSmokeEntryBackupPath(gtaPath);
                    if (!File.Exists(backup))
                        throw new InvalidDataException(
                            "Original weapons.meta backup is missing.");
                    source = File.ReadAllBytes(backup);
                    int count = GetMergedSmokeDefinitionCount(current);
                    ValidateMergedSmokeWeaponMeta(source, current, count);
                }
                byte[] merged = BuildMergedSmokeWeaponMeta(
                    source, ColoredSmokeWeapons.Length);
                ValidateMergedSmokeWeaponMeta(source, merged,
                    ColoredSmokeWeapons.Length);
                string directory = Path.GetDirectoryName(output);
                if (!string.IsNullOrEmpty(directory))
                    Directory.CreateDirectory(directory);
                WriteAtomicFile(output, merged);
                Console.WriteLine(
                    $"Built seven-color base-meta smoke payload: {output}");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int InstallMergedSmokeCanary(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe install-merged-smoke-canary <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before installing the merged smoke canary.");
                return 11;
            }
            string modsRpf = Path.Combine(
                gtaPath, "mods", "update", "update.rpf");
            bool writesStarted = false;
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode);
                if (rpf == null) return errorCode;
                byte[] current = ExtractRequiredEntry(
                    rpf, BaseWeaponsMetaPath);
                byte[] currentAnimations = ExtractRequiredEntry(
                    rpf, BaseWeaponAnimationsMetaPath);
                if (ContainsAllin1SmokeDefinitions(current))
                {
                    int existing = VerifyMergedSmokeCanaryCore(gtaPath);
                    if (existing == 0)
                    {
                        Console.WriteLine(
                            "White Smoke merged canary is already installed.");
                        return 0;
                    }
                    throw new InvalidDataException(
                        "Base weapons.meta already contains unverified ALLIN1 smoke definitions.");
                }

                byte[] merged = BuildMergedSmokeWeaponMeta(current, 1);
                byte[] mergedAnimations =
                    BuildMergedSmokeWeaponAnimationsMeta(
                        currentAnimations, 1);
                ValidateMergedSmokeWeaponMeta(current, merged, 1);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    currentAnimations, mergedAnimations, 1);
                EnsureMergedSmokeEntryBackups(
                    gtaPath, current, currentAnimations);
                CreateMergedSmokeArchiveSnapshot(modsRpf, gtaPath);
                writesStarted = true;
                InstallArchiveEntries(rpf,
                    new Dictionary<string, byte[]>(
                        StringComparer.OrdinalIgnoreCase)
                    {
                        { BaseWeaponsMetaPath, merged },
                        { BaseWeaponAnimationsMetaPath, mergedAnimations },
                    });

                RpfFile reopened = OpenModsUpdateRpf(
                    gtaPath, out errorCode, "update.rpf", false);
                if (reopened == null)
                    throw new InvalidDataException(
                        $"Could not reopen modified update.rpf (error {errorCode}).");
                byte[] installed = ExtractRequiredEntry(
                    reopened, BaseWeaponsMetaPath);
                byte[] installedAnimations = ExtractRequiredEntry(
                    reopened, BaseWeaponAnimationsMetaPath);
                ValidateMergedSmokeWeaponMeta(current, installed, 1);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    currentAnimations, installedAnimations, 1);
                if (!installed.SequenceEqual(merged))
                    throw new InvalidDataException(
                        "Installed weapons.meta differs from the verified canary payload.");
                if (!installedAnimations.SequenceEqual(mergedAnimations))
                    throw new InvalidDataException(
                        "Installed weaponanimations.meta differs from the verified canary payload.");
                WriteMergedSmokeCanaryMarker(
                    gtaPath, current, installed, currentAnimations,
                    installedAnimations, 1, "pending", false);
                Console.WriteLine(
                    "Installed one White Smoke definition by merging it into " +
                    "the existing base CWeaponInfoBlob; no DLC pack was added.");
                return 0;
            }
            catch (Exception ex)
            {
                if (writesStarted)
                    RestoreMergedSmokeArchiveSnapshot(gtaPath);
                string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
                if (File.Exists(marker)) File.Delete(marker);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int InstallMergedSmokeWeapons(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe install-merged-smoke-weapons <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before installing merged smoke weapons.");
                return 11;
            }
            bool writesStarted = false;
            try
            {
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode);
                if (rpf == null) return errorCode;
                byte[] current = ExtractRequiredEntry(
                    rpf, BaseWeaponsMetaPath);
                byte[] currentAnimations = ExtractRequiredEntry(
                    rpf, BaseWeaponAnimationsMetaPath);
                byte[] currentLanguage = ExtractRequiredEntry(
                    rpf, BaseAmericanLanguageArchivePath);
                byte[] currentHud = ExtractRequiredEntry(
                    rpf, BaseScaleformGenericArchivePath);
                Console.WriteLine(
                    $"Read base language archive ({currentLanguage.Length:N0} bytes, " +
                    $"sha256={Sha256(currentLanguage)}).");
                byte[] original;
                byte[] originalAnimations;
                byte[] originalLanguage;
                byte[] originalHud;
                bool promotedFromWhiteCanary = false;
                if (ContainsAllin1SmokeDefinitions(current))
                {
                    string backup = GetMergedSmokeEntryBackupPath(gtaPath);
                    string animationBackup =
                        GetMergedSmokeAnimationBackupPath(gtaPath);
                    string languageBackup =
                        GetMergedSmokeLanguageBackupPath(gtaPath);
                    if (!File.Exists(backup) || !File.Exists(animationBackup))
                        throw new InvalidDataException(
                            "Original smoke metadata backup is missing.");
                    original = File.ReadAllBytes(backup);
                    originalAnimations = File.ReadAllBytes(animationBackup);
                    int currentCount = GetMergedSmokeDefinitionCount(current);
                    ValidateMergedSmokeWeaponMeta(
                        original, current, currentCount);
                    ValidateMergedSmokeWeaponAnimationsMeta(
                        originalAnimations, currentAnimations, currentCount);
                    if (currentCount == ColoredSmokeWeapons.Length)
                        return VerifyMergedSmokeWeaponsCore(gtaPath);
                    if (currentCount != 1 ||
                        VerifyMergedSmokeCanaryCore(gtaPath) != 0)
                        throw new InvalidDataException(
                            "Only the verified White Smoke canary can be promoted.");
                    promotedFromWhiteCanary = true;
                    originalLanguage = currentLanguage;
                    originalHud = currentHud;
                    EnsureMergedSmokeEntryBackups(gtaPath, original,
                        originalAnimations, originalLanguage, originalHud);
                }
                else
                {
                    original = current;
                    originalAnimations = currentAnimations;
                    originalLanguage = currentLanguage;
                    originalHud = currentHud;
                    EnsureMergedSmokeEntryBackups(
                        gtaPath, original, originalAnimations,
                        originalLanguage, originalHud);
                    CreateMergedSmokeArchiveSnapshot(
                        Path.Combine(gtaPath, "mods", "update", "update.rpf"),
                        gtaPath);
                }

                ReloadGtaEncryptionKeys(gtaPath);
                ValidateMergedSmokeArchiveSnapshot(
                    gtaPath, original, originalAnimations, originalLanguage,
                    originalHud);
                byte[] merged = BuildMergedSmokeWeaponMeta(
                    original, ColoredSmokeWeapons.Length);
                byte[] mergedAnimations =
                    BuildMergedSmokeWeaponAnimationsMeta(
                        originalAnimations, ColoredSmokeWeapons.Length);
                byte[] mergedLanguage =
                    BuildMergedSmokeLanguageArchive(
                        gtaPath, originalLanguage);
                byte[] mergedHud = BuildMergedSmokeHudArchive(
                    gtaPath, originalHud);
                ValidateMergedSmokeWeaponMeta(original, merged,
                    ColoredSmokeWeapons.Length);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    originalAnimations, mergedAnimations,
                    ColoredSmokeWeapons.Length);
                writesStarted = true;
                InstallArchiveEntries(rpf,
                    new Dictionary<string, byte[]>(
                        StringComparer.OrdinalIgnoreCase)
                    {
                        { BaseWeaponsMetaPath, merged },
                        { BaseWeaponAnimationsMetaPath, mergedAnimations },
                        { BaseAmericanLanguageArchivePath, mergedLanguage },
                        { BaseScaleformGenericArchivePath, mergedHud },
                    });

                RpfFile reopened = OpenModsUpdateRpf(
                    gtaPath, out errorCode, "update.rpf", false);
                if (reopened == null)
                    throw new InvalidDataException(
                        $"Could not reopen modified update.rpf (error {errorCode}).");
                byte[] installed = ExtractRequiredEntry(
                    reopened, BaseWeaponsMetaPath);
                byte[] installedAnimations = ExtractRequiredEntry(
                    reopened, BaseWeaponAnimationsMetaPath);
                byte[] installedLanguage = ExtractRequiredEntry(
                    reopened, BaseAmericanLanguageArchivePath);
                byte[] installedHud = ExtractRequiredEntry(
                    reopened, BaseScaleformGenericArchivePath);
                ValidateMergedSmokeWeaponMeta(original, installed,
                    ColoredSmokeWeapons.Length);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    originalAnimations, installedAnimations,
                    ColoredSmokeWeapons.Length);
                if (!installed.SequenceEqual(merged))
                    throw new InvalidDataException(
                        "Installed weapons.meta differs from the verified seven-color payload.");
                if (!installedAnimations.SequenceEqual(mergedAnimations))
                    throw new InvalidDataException(
                        "Installed weaponanimations.meta differs from the verified seven-color payload.");
                if (!installedLanguage.SequenceEqual(mergedLanguage))
                    throw new InvalidDataException(
                        "Installed American language archive differs from the verified smoke-label payload.");
                if (!installedHud.SequenceEqual(mergedHud))
                    throw new InvalidDataException(
                        "Installed Scaleform HUD archive differs from the verified BZ Gas icon-alias payload.");
                WriteMergedSmokeFullMarker(gtaPath, original, installed,
                    originalAnimations, installedAnimations,
                    originalLanguage, installedLanguage,
                    originalHud, installedHud,
                    ColoredSmokeWeapons.Length, "full_pending",
                    promotedFromWhiteCanary);
                Console.WriteLine(
                    "Installed seven independent colored smoke weapons and " +
                    "their stock-cloned animations and native wheel labels " +
                    "with BZ Gas wheel-icon aliases inside existing base " +
                    "archives; no DLC pack was added.");
                return 0;
            }
            catch (Exception ex)
            {
                if (writesStarted)
                    RestoreMergedSmokeArchiveSnapshot(gtaPath);
                string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
                if (File.Exists(marker)) File.Delete(marker);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        static int VerifyMergedSmokeCanary(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-merged-smoke-canary <gta_path>");
                return 1;
            }
            return VerifyMergedSmokeCanaryCore(
                Path.GetFullPath(args[1]));
        }

        static int VerifyMergedSmokeWeapons(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe verify-merged-smoke-weapons <gta_path>");
                return 1;
            }
            return VerifyMergedSmokeWeaponsCore(
                Path.GetFullPath(args[1]));
        }

        static int RemoveMergedSmokeCanary(string[] args)
        {
            if (args.Length < 2)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe remove-merged-smoke-canary <gta_path>");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            if (IsGtaProcessRunning())
            {
                Console.Error.WriteLine(
                    "ERROR: Close GTA V before removing the merged smoke canary.");
                return 11;
            }
            string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
            string backup = GetMergedSmokeEntryBackupPath(gtaPath);
            string animationBackup =
                GetMergedSmokeAnimationBackupPath(gtaPath);
            string languageBackup =
                GetMergedSmokeLanguageBackupPath(gtaPath);
            string hudBackup =
                GetMergedSmokeHudBackupPath(gtaPath);
            if (!File.Exists(marker))
            {
                Console.WriteLine("Merged smoke canary is not installed.");
                return 0;
            }
            if (!File.Exists(backup) || !File.Exists(animationBackup))
            {
                Console.Error.WriteLine(
                    "ERROR: Original smoke metadata backup is missing: " +
                    $"{backup}, {animationBackup}");
                return 12;
            }
            try
            {
                byte[] original = File.ReadAllBytes(backup);
                byte[] originalAnimations =
                    File.ReadAllBytes(animationBackup);
                byte[] originalLanguage = File.Exists(languageBackup)
                    ? File.ReadAllBytes(languageBackup) : null;
                byte[] originalHud = File.Exists(hudBackup)
                    ? File.ReadAllBytes(hudBackup) : null;
                ValidateBaseWeaponsMeta(original);
                XDocument animationDoc = XDocument.Parse(
                    Encoding.UTF8.GetString(originalAnimations)
                        .TrimStart('\uFEFF'));
                if (animationDoc.Root?.Name.LocalName !=
                        "CWeaponAnimationsSets")
                    throw new InvalidDataException(
                        "Original weaponanimations.meta backup is invalid.");
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode);
                if (rpf == null) return errorCode;
                ReloadGtaEncryptionKeys(gtaPath);
                var originals = new Dictionary<string, byte[]>(
                    StringComparer.OrdinalIgnoreCase)
                    {
                        { BaseWeaponsMetaPath, original },
                        { BaseWeaponAnimationsMetaPath, originalAnimations },
                    };
                if (originalLanguage != null)
                {
                    originals.Add(BaseAmericanLanguageArchivePath,
                        originalLanguage);
                }
                if (originalHud != null)
                {
                    originals.Add(BaseScaleformGenericArchivePath,
                        originalHud);
                }
                InstallArchiveEntries(rpf, originals);
                RpfFile reopened = OpenModsUpdateRpf(
                    gtaPath, out errorCode, "update.rpf", false);
                if (reopened == null) return errorCode;
                byte[] restored = ExtractRequiredEntry(
                    reopened, BaseWeaponsMetaPath);
                byte[] restoredAnimations = ExtractRequiredEntry(
                    reopened, BaseWeaponAnimationsMetaPath);
                byte[] restoredLanguage = originalLanguage == null ? null :
                    ExtractRequiredEntry(reopened,
                        BaseAmericanLanguageArchivePath);
                byte[] restoredHud = originalHud == null ? null :
                    ExtractRequiredEntry(reopened,
                        BaseScaleformGenericArchivePath);
                if (!restored.SequenceEqual(original))
                    throw new InvalidDataException(
                        "Restored weapons.meta does not match its original backup.");
                if (!restoredAnimations.SequenceEqual(originalAnimations))
                    throw new InvalidDataException(
                        "Restored weaponanimations.meta does not match its original backup.");
                if (originalLanguage != null &&
                    !restoredLanguage.SequenceEqual(originalLanguage))
                    throw new InvalidDataException(
                        "Restored American language archive does not match its original backup.");
                if (originalHud != null &&
                    !restoredHud.SequenceEqual(originalHud))
                    throw new InvalidDataException(
                        "Restored Scaleform HUD archive does not match its original backup.");
                if (File.Exists(marker)) File.Delete(marker);
                Console.WriteLine(
                    "Removed merged smoke data and restored every exact base " +
                    "weapon, animation, language, and HUD backup that was installed.");
                return 0;
            }
            catch (Exception ex)
            {
                if (RestoreMergedSmokeArchiveSnapshot(gtaPath) &&
                    File.Exists(marker))
                    File.Delete(marker);
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 99;
            }
        }

        private static byte[] BuildMergedSmokeWeaponMeta(
            byte[] source, int smokeCount)
        {
            if (smokeCount < 1 || smokeCount > ColoredSmokeWeapons.Length)
                throw new ArgumentOutOfRangeException(nameof(smokeCount));
            ValidateBaseWeaponsMeta(source);
            XDocument doc = XDocument.Parse(
                Encoding.UTF8.GetString(source).TrimStart('\uFEFF'));
            XElement root = doc.Root;
            XElement sourceAmmo = root.Descendants("Item").FirstOrDefault(
                item => item.Element("Name")?.Value ==
                    "AMMO_SMOKEGRENADE");
            XElement sourceWeapon = root.Descendants("Item").FirstOrDefault(
                item => item.Element("Name")?.Value ==
                    "WEAPON_SMOKEGRENADE");
            XElement[] navigationGroups = root.Element("SlotNavigateOrder")?
                .Elements("Item").ToArray() ?? Array.Empty<XElement>();
            XElement bestSlots = root.Element("SlotBestOrder")?
                .Element("WeaponSlots");
            XElement infos = root.Element("Infos");
            XElement ammoInfos = infos?.Elements("Item")
                .Select(item => item.Element("Infos"))
                .FirstOrDefault(group => group?.Elements("Item").Any(
                    item => item.Element("Name")?.Value ==
                        "AMMO_SMOKEGRENADE") == true);
            XElement weaponInfos = infos?.Elements("Item")
                .Select(item => item.Element("Infos"))
                .FirstOrDefault(group => group?.Elements("Item").Any(
                    item => item.Element("Name")?.Value ==
                        "WEAPON_SMOKEGRENADE") == true);
            if (sourceAmmo == null || sourceWeapon == null ||
                navigationGroups.Length != 2 || bestSlots == null ||
                ammoInfos == null || weaponInfos == null)
                throw new InvalidDataException(
                    "Base weapons.meta does not match the current Enhanced weapon schema.");

            for (int index = 0; index < smokeCount; index++)
            {
                ColoredSmokeWeaponSpec spec = ColoredSmokeWeapons[index];
                XElement NavigationSlot() => new XElement("Item",
                    new XElement("OrderNumber",
                        new XAttribute("value", 451 + index)),
                    new XElement("Entry", spec.SlotName));
                foreach (XElement group in navigationGroups)
                    group.Element("WeaponSlots").Add(NavigationSlot());
                bestSlots.Add(new XElement("Item",
                    new XElement("OrderNumber",
                        new XAttribute("value", 401 + index)),
                    new XElement("Entry", spec.SlotName)));
                ammoInfos.Add(CloneColoredSmokeAmmo(sourceAmmo, spec));
                weaponInfos.Add(CloneColoredSmokeWeapon(
                    sourceWeapon, spec));
            }
            doc.Declaration = new XDeclaration("1.0", "UTF-8", null);
            return Encoding.UTF8.GetBytes(doc.Declaration + "\n\n" + doc);
        }

        private static void ValidateBaseWeaponsMeta(byte[] data)
        {
            if (data == null || data.Length == 0)
                throw new InvalidDataException("Base weapons.meta is empty.");
            XDocument doc = XDocument.Parse(
                Encoding.UTF8.GetString(data).TrimStart('\uFEFF'));
            if (doc.Root?.Name.LocalName != "CWeaponInfoBlob" ||
                doc.Root.Element("SlotNavigateOrder") == null ||
                doc.Root.Element("SlotBestOrder") == null ||
                doc.Root.Element("Infos") == null ||
                doc.Descendants("Name").Count(element =>
                    element.Value == "AMMO_SMOKEGRENADE") != 1 ||
                doc.Descendants("Name").Count(element =>
                    element.Value == "WEAPON_SMOKEGRENADE") != 1)
                throw new InvalidDataException(
                    "Base weapons.meta failed structural validation.");
            if (ContainsAllin1SmokeDefinitions(data))
                throw new InvalidDataException(
                    "Base weapons.meta already contains ALLIN1 smoke definitions.");
        }

        private static bool ContainsAllin1SmokeDefinitions(byte[] data)
        {
            string text = Encoding.UTF8.GetString(data ?? Array.Empty<byte>());
            return text.IndexOf("WEAPON_ALLIN1_SMOKE_",
                       StringComparison.Ordinal) >= 0 ||
                text.IndexOf("AMMO_ALLIN1_SMOKE_",
                       StringComparison.Ordinal) >= 0 ||
                text.IndexOf("SLOT_ALLIN1_SMOKE_",
                       StringComparison.Ordinal) >= 0;
        }

        private static int GetMergedSmokeDefinitionCount(byte[] data)
        {
            XDocument doc = XDocument.Parse(
                Encoding.UTF8.GetString(data).TrimStart('\uFEFF'));
            int count = 0;
            bool gapSeen = false;
            foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
            {
                bool present = doc.Descendants("Item").Any(item =>
                    item.Element("Name")?.Value == spec.WeaponName);
                if (present && gapSeen)
                    throw new InvalidDataException(
                        "Merged smoke definitions are not a contiguous color set.");
                if (present) count++;
                else gapSeen = true;
            }
            if (count < 1)
                throw new InvalidDataException(
                    "No complete merged smoke definitions were found.");
            return count;
        }

        private static void ValidateMergedSmokeWeaponMeta(
            byte[] original, byte[] candidate, int smokeCount)
        {
            ValidateBaseWeaponsMeta(original);
            XDocument originalDoc = XDocument.Parse(
                Encoding.UTF8.GetString(original).TrimStart('\uFEFF'));
            XDocument candidateDoc = XDocument.Parse(
                Encoding.UTF8.GetString(candidate).TrimStart('\uFEFF'));
            if (candidateDoc.Root?.Name.LocalName != "CWeaponInfoBlob")
                throw new InvalidDataException(
                    "Merged weapons.meta root is invalid.");

            for (int index = 0; index < ColoredSmokeWeapons.Length; index++)
            {
                ColoredSmokeWeaponSpec spec = ColoredSmokeWeapons[index];
                int expected = index < smokeCount ? 1 : 0;
                int ammoCount = candidateDoc.Descendants("Item").Count(
                    item => item.Element("Name")?.Value == spec.AmmoName);
                int weaponCount = candidateDoc.Descendants("Item").Count(
                    item => item.Element("Name")?.Value == spec.WeaponName);
                int slotCount = candidateDoc.Descendants("Item").Count(
                    item => item.Element("Entry")?.Value == spec.SlotName);
                if (ammoCount != expected || weaponCount != expected ||
                    slotCount != expected * 3)
                    throw new InvalidDataException(
                        $"Merged smoke definition count is invalid for {spec.Color}.");
                if (expected == 0) continue;
                XElement ammo = candidateDoc.Descendants("Item")
                    .Single(item => item.Element("Name")?.Value ==
                        spec.AmmoName);
                XElement weapon = candidateDoc.Descendants("Item")
                    .Single(item => item.Element("Name")?.Value ==
                        spec.WeaponName);
                if ((ammo.Element("AmmoFlags")?.Value ?? "").Contains(
                        "AddSmokeOnExplosion") ||
                    ammo.Element("Explosion")?.Element("Default")?.Value !=
                        "DONTCARE" ||
                    weapon.Element("AmmoInfo")?.Attribute("ref")?.Value !=
                        spec.AmmoName ||
                    weapon.Element("HumanNameHash")?.Value !=
                        spec.HumanNameLabel ||
                    weapon.Element("StatName")?.Value !=
                        "A1SM" + spec.Color.ToUpperInvariant())
                    throw new InvalidDataException(
                        $"Merged smoke isolation is invalid for {spec.Color}.");
                foreach (string maxName in new[] { "AmmoMax", "AmmoMax50",
                    "AmmoMax100", "AmmoMaxMP", "AmmoMax50MP",
                    "AmmoMax100MP" })
                    if (ammo.Element(maxName)?.Attribute("value")?.Value != "5")
                        throw new InvalidDataException(
                            $"Merged smoke ammo cap is invalid for {spec.Color}: {maxName}.");
                int[] orders = candidateDoc.Descendants("Item")
                    .Where(item => item.Element("Entry")?.Value ==
                        spec.SlotName)
                    .Select(item => int.Parse(item.Element("OrderNumber")
                        .Attribute("value").Value)).ToArray();
                if (orders.Count(value => value == 451 + index) != 2 ||
                    orders.Count(value => value == 401 + index) != 1)
                    throw new InvalidDataException(
                        $"Merged weapon-wheel order is invalid for {spec.Color}.");
            }

            var stripped = new XDocument(candidateDoc);
            foreach (XElement item in stripped.Descendants("Item").ToArray())
            {
                string name = item.Element("Name")?.Value ?? "";
                string entry = item.Element("Entry")?.Value ?? "";
                if (name.StartsWith("AMMO_ALLIN1_SMOKE_",
                        StringComparison.Ordinal) ||
                    name.StartsWith("WEAPON_ALLIN1_SMOKE_",
                        StringComparison.Ordinal) ||
                    entry.StartsWith("SLOT_ALLIN1_SMOKE_",
                        StringComparison.Ordinal))
                    item.Remove();
            }
            if (!XNode.DeepEquals(originalDoc.Root, stripped.Root))
                throw new InvalidDataException(
                    "Merged canary changed data outside its appended smoke definitions.");
        }

        private static byte[] BuildMergedSmokeWeaponAnimationsMeta(
            byte[] source, int smokeCount)
        {
            if (smokeCount < 1 || smokeCount > ColoredSmokeWeapons.Length)
                throw new ArgumentOutOfRangeException(nameof(smokeCount));
            XDocument doc = XDocument.Parse(
                Encoding.UTF8.GetString(source).TrimStart('\uFEFF'));
            if (doc.Root?.Name.LocalName != "CWeaponAnimationsSets")
                throw new InvalidDataException(
                    "Base weaponanimations.meta root is invalid.");
            XElement[] groups = doc.Descendants("WeaponAnimations")
                .Where(group => group.Elements("Item").Any(item =>
                    item.Attribute("key")?.Value == "WEAPON_SMOKEGRENADE"))
                .ToArray();
            if (groups.Length == 0)
                throw new InvalidDataException(
                    "No stock smoke-grenade animation mappings were found.");
            if (doc.Descendants("Item").Any(item =>
                    (item.Attribute("key")?.Value ?? "").StartsWith(
                        "WEAPON_ALLIN1_SMOKE_", StringComparison.Ordinal)))
                throw new InvalidDataException(
                    "Base weaponanimations.meta already contains ALLIN1 mappings.");

            foreach (XElement group in groups)
            {
                XElement[] templates = group.Elements("Item").Where(item =>
                    item.Attribute("key")?.Value == "WEAPON_SMOKEGRENADE")
                    .ToArray();
                if (templates.Length != 1)
                    throw new InvalidDataException(
                        "Stock smoke animation mapping is duplicated within a set.");
                XElement insertionPoint = templates[0];
                for (int index = 0; index < smokeCount; index++)
                {
                    var clone = new XElement(templates[0]);
                    clone.SetAttributeValue(
                        "key", ColoredSmokeWeapons[index].WeaponName);
                    insertionPoint.AddAfterSelf(clone);
                    insertionPoint = clone;
                }
            }
            return Encoding.UTF8.GetBytes(doc.Declaration + "\n" + doc);
        }

        private static void ValidateMergedSmokeWeaponAnimationsMeta(
            byte[] original, byte[] candidate, int smokeCount)
        {
            XDocument originalDoc = XDocument.Parse(
                Encoding.UTF8.GetString(original).TrimStart('\uFEFF'));
            XDocument candidateDoc = XDocument.Parse(
                Encoding.UTF8.GetString(candidate).TrimStart('\uFEFF'));
            if (originalDoc.Root?.Name.LocalName != "CWeaponAnimationsSets" ||
                candidateDoc.Root?.Name.LocalName != "CWeaponAnimationsSets")
                throw new InvalidDataException(
                    "Smoke animation metadata root is invalid.");

            XElement[] originalGroups = originalDoc.Descendants(
                    "WeaponAnimations")
                .Where(group => group.Elements("Item").Any(item =>
                    item.Attribute("key")?.Value == "WEAPON_SMOKEGRENADE"))
                .ToArray();
            XElement[] candidateGroups = candidateDoc.Descendants(
                    "WeaponAnimations")
                .Where(group => group.Elements("Item").Any(item =>
                    item.Attribute("key")?.Value == "WEAPON_SMOKEGRENADE"))
                .ToArray();
            if (originalGroups.Length == 0 ||
                candidateGroups.Length != originalGroups.Length)
                throw new InvalidDataException(
                    "Smoke animation-set coverage changed unexpectedly.");

            for (int groupIndex = 0;
                groupIndex < originalGroups.Length; groupIndex++)
            {
                XElement template = originalGroups[groupIndex]
                    .Elements("Item").Single(item =>
                        item.Attribute("key")?.Value ==
                            "WEAPON_SMOKEGRENADE");
                XElement candidateGroup = candidateGroups[groupIndex];
                for (int index = 0;
                    index < ColoredSmokeWeapons.Length; index++)
                {
                    ColoredSmokeWeaponSpec spec = ColoredSmokeWeapons[index];
                    int expected = index < smokeCount ? 1 : 0;
                    XElement[] matches = candidateGroup.Elements("Item")
                        .Where(item => item.Attribute("key")?.Value ==
                            spec.WeaponName).ToArray();
                    if (matches.Length != expected)
                        throw new InvalidDataException(
                            $"Animation mapping count is invalid for {spec.Color} " +
                            $"smoke in set {groupIndex}.");
                    if (expected == 0) continue;
                    var normalized = new XElement(matches[0]);
                    normalized.SetAttributeValue(
                        "key", "WEAPON_SMOKEGRENADE");
                    if (!XNode.DeepEquals(template, normalized))
                        throw new InvalidDataException(
                            $"Animation mapping differs from stock smoke for " +
                            $"{spec.Color} in set {groupIndex}.");
                }
            }

            var stripped = new XDocument(candidateDoc);
            foreach (XElement item in stripped.Descendants("Item").ToArray())
                if ((item.Attribute("key")?.Value ?? "").StartsWith(
                        "WEAPON_ALLIN1_SMOKE_", StringComparison.Ordinal))
                    item.Remove();
            if (!XNode.DeepEquals(originalDoc.Root, stripped.Root))
                throw new InvalidDataException(
                    "Merged animation canary changed data outside appended smoke mappings.");
        }

        private static int VerifyMergedSmokeCanaryCore(string gtaPath)
        {
            try
            {
                string backup = GetMergedSmokeEntryBackupPath(gtaPath);
                string animationBackup =
                    GetMergedSmokeAnimationBackupPath(gtaPath);
                string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
                if (!File.Exists(backup) || !File.Exists(animationBackup) ||
                    !File.Exists(marker))
                {
                    Console.Error.WriteLine(
                        "ERROR: Merged smoke canary backup or marker is missing.");
                    return 12;
                }
                byte[] original = File.ReadAllBytes(backup);
                byte[] originalAnimations = File.ReadAllBytes(animationBackup);
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (rpf == null) return errorCode;
                byte[] installed = ExtractRequiredEntry(
                    rpf, BaseWeaponsMetaPath);
                byte[] installedAnimations = ExtractRequiredEntry(
                    rpf, BaseWeaponAnimationsMetaPath);
                ValidateMergedSmokeWeaponMeta(original, installed, 1);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    originalAnimations, installedAnimations, 1);
                string json = File.ReadAllText(marker);
                foreach (string expected in new[]
                {
                    "\"canary_state\": \"pending\"",
                    Sha256(original), Sha256(installed),
                    Sha256(originalAnimations), Sha256(installedAnimations),
                    ColoredSmokeWeapons[0].WeaponName,
                })
                    if (json.IndexOf(expected,
                            StringComparison.OrdinalIgnoreCase) < 0)
                        throw new InvalidDataException(
                            $"Merged smoke canary marker is stale: {expected}");
                Console.WriteLine(
                    "Verified one White Smoke definition and all stock-cloned " +
                    "animation mappings inside the existing base metadata.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 9;
            }
        }

        private static int VerifyMergedSmokeWeaponsCore(string gtaPath)
        {
            try
            {
                string backup = GetMergedSmokeEntryBackupPath(gtaPath);
                string animationBackup =
                    GetMergedSmokeAnimationBackupPath(gtaPath);
                string languageBackup =
                    GetMergedSmokeLanguageBackupPath(gtaPath);
                string hudBackup =
                    GetMergedSmokeHudBackupPath(gtaPath);
                string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
                if (!File.Exists(backup) || !File.Exists(animationBackup) ||
                    !File.Exists(languageBackup) || !File.Exists(hudBackup) ||
                    !File.Exists(marker))
                    throw new InvalidDataException(
                        "Merged smoke backup or marker is missing.");
                byte[] original = File.ReadAllBytes(backup);
                byte[] originalAnimations = File.ReadAllBytes(animationBackup);
                byte[] originalLanguage = File.ReadAllBytes(languageBackup);
                byte[] originalHud = File.ReadAllBytes(hudBackup);
                RpfFile rpf = OpenModsUpdateRpf(
                    gtaPath, out int errorCode, "update.rpf", false);
                if (rpf == null) return errorCode;
                ReloadGtaEncryptionKeys(gtaPath);
                // OpenModsUpdateRpf loads the Enhanced encryption keys used by
                // the stock-format rollback snapshot before we scan it.
                ValidateMergedSmokeArchiveSnapshot(
                    gtaPath, original, originalAnimations, originalLanguage,
                    originalHud);
                byte[] installed = ExtractRequiredEntry(
                    rpf, BaseWeaponsMetaPath);
                byte[] installedAnimations = ExtractRequiredEntry(
                    rpf, BaseWeaponAnimationsMetaPath);
                byte[] installedLanguage = ExtractRequiredEntry(
                    rpf, BaseAmericanLanguageArchivePath);
                byte[] installedHud = ExtractRequiredEntry(
                    rpf, BaseScaleformGenericArchivePath);
                ValidateMergedSmokeWeaponMeta(original, installed,
                    ColoredSmokeWeapons.Length);
                ValidateMergedSmokeWeaponAnimationsMeta(
                    originalAnimations, installedAnimations,
                    ColoredSmokeWeapons.Length);
                byte[] expectedLanguage = BuildMergedSmokeLanguageArchive(
                    gtaPath, originalLanguage);
                if (!installedLanguage.SequenceEqual(expectedLanguage))
                    throw new InvalidDataException(
                        "Installed smoke wheel labels differ from the verified language payload.");
                byte[] expectedHud = BuildMergedSmokeHudArchive(
                    gtaPath, originalHud);
                if (!installedHud.SequenceEqual(expectedHud))
                    throw new InvalidDataException(
                        "Installed smoke wheel icons differ from the verified BZ Gas HUD-alias payload.");
                string json = File.ReadAllText(marker);
                foreach (string expected in new[]
                {
                    "\"canary_state\": \"full_pending\"",
                    $"\"weapon_count\": {ColoredSmokeWeapons.Length}",
                    Sha256(original), Sha256(installed),
                    Sha256(originalAnimations), Sha256(installedAnimations),
                    Sha256(originalLanguage), Sha256(installedLanguage),
                    Sha256(originalHud), Sha256(installedHud),
                }.Concat(ColoredSmokeWeapons.Select(spec => spec.WeaponName)))
                    if (json.IndexOf(expected,
                            StringComparison.OrdinalIgnoreCase) < 0)
                        throw new InvalidDataException(
                            $"Merged smoke marker is stale: {expected}");
                Console.WriteLine(
                    "Verified seven colored smoke definitions, all stock-cloned " +
                    "animation mappings, native wheel labels, BZ Gas icon " +
                    "routing, and their rollback snapshot.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 9;
            }
        }

        private static void EnsureMergedSmokeEntryBackups(
            string gtaPath, byte[] original, byte[] originalAnimations)
        {
            ValidateBaseWeaponsMeta(original);
            XDocument animationDoc = XDocument.Parse(
                Encoding.UTF8.GetString(originalAnimations)
                    .TrimStart('\uFEFF'));
            if (animationDoc.Root?.Name.LocalName != "CWeaponAnimationsSets")
                throw new InvalidDataException(
                    "Base weaponanimations.meta backup is invalid.");
            string directory = GetMergedSmokeBackupDirectory(gtaPath);
            Directory.CreateDirectory(directory);
            string path = GetMergedSmokeEntryBackupPath(gtaPath);
            string animationPath =
                GetMergedSmokeAnimationBackupPath(gtaPath);
            WriteAtomicFileReplacing(path, original);
            WriteAtomicFileReplacing(animationPath, originalAnimations);
            File.WriteAllText(Path.Combine(directory, "manifest.txt"),
                $"{BaseWeaponsMetaPath}\t{original.Length}\t{Sha256(original)}\n" +
                $"{BaseWeaponAnimationsMetaPath}\t{originalAnimations.Length}\t" +
                $"{Sha256(originalAnimations)}\n",
                new UTF8Encoding(false));
            Console.WriteLine(
                $"Saved exact base weapon metadata backups: {path}, {animationPath}");
        }

        private static void EnsureMergedSmokeEntryBackups(
            string gtaPath, byte[] original, byte[] originalAnimations,
            byte[] originalLanguage)
        {
            EnsureMergedSmokeEntryBackups(
                gtaPath, original, originalAnimations);
            if (originalLanguage == null || originalLanguage.Length < 32)
                throw new InvalidDataException(
                    "Base American language archive backup is invalid.");
            string languagePath =
                GetMergedSmokeLanguageBackupPath(gtaPath);
            WriteAtomicFileReplacing(languagePath, originalLanguage);
            File.WriteAllText(Path.Combine(
                    GetMergedSmokeBackupDirectory(gtaPath), "manifest.txt"),
                $"{BaseWeaponsMetaPath}\t{original.Length}\t{Sha256(original)}\n" +
                $"{BaseWeaponAnimationsMetaPath}\t{originalAnimations.Length}\t" +
                    $"{Sha256(originalAnimations)}\n" +
                $"{BaseAmericanLanguageArchivePath}\t{originalLanguage.Length}\t" +
                    $"{Sha256(originalLanguage)}\n",
                new UTF8Encoding(false));
            Console.WriteLine(
                $"Saved exact base American language backup: {languagePath}");
        }

        private static void EnsureMergedSmokeEntryBackups(
            string gtaPath, byte[] original, byte[] originalAnimations,
            byte[] originalLanguage, byte[] originalHud)
        {
            EnsureMergedSmokeEntryBackups(gtaPath, original,
                originalAnimations, originalLanguage);
            if (originalHud == null || originalHud.Length < 32)
                throw new InvalidDataException(
                    "Base Scaleform HUD archive backup is invalid.");
            string hudPath = GetMergedSmokeHudBackupPath(gtaPath);
            WriteAtomicFileReplacing(hudPath, originalHud);
            File.WriteAllText(Path.Combine(
                    GetMergedSmokeBackupDirectory(gtaPath), "manifest.txt"),
                $"{BaseWeaponsMetaPath}\t{original.Length}\t{Sha256(original)}\n" +
                $"{BaseWeaponAnimationsMetaPath}\t{originalAnimations.Length}\t" +
                    $"{Sha256(originalAnimations)}\n" +
                $"{BaseAmericanLanguageArchivePath}\t{originalLanguage.Length}\t" +
                    $"{Sha256(originalLanguage)}\n" +
                $"{BaseScaleformGenericArchivePath}\t{originalHud.Length}\t" +
                    $"{Sha256(originalHud)}\n",
                new UTF8Encoding(false));
            Console.WriteLine(
                $"Saved exact base Scaleform HUD backup: {hudPath}");
        }

        private static void CreateMergedSmokeArchiveSnapshot(
            string modsRpf, string gtaPath)
        {
            string backup = GetMergedSmokeArchiveBackupPath(gtaPath);
            Directory.CreateDirectory(Path.GetDirectoryName(backup));
            long sourceLength = new FileInfo(modsRpf).Length;
            var drive = new DriveInfo(Path.GetPathRoot(backup));
            if (drive.AvailableFreeSpace < sourceLength + 268435456L)
                throw new IOException(
                    "Not enough free disk space for the merged-smoke archive snapshot.");
            string temporary = backup + ".tmp";
            if (File.Exists(temporary)) File.Delete(temporary);
            try
            {
                File.Copy(modsRpf, temporary, true);
                if (new FileInfo(temporary).Length != sourceLength)
                    throw new IOException(
                        "Merged-smoke archive snapshot failed size verification.");
                if (File.Exists(backup)) File.Delete(backup);
                File.Move(temporary, backup);
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
            Console.WriteLine($"Created pre-merge archive snapshot: {backup}");
        }

        private static void ValidateMergedSmokeArchiveSnapshot(
            string gtaPath, byte[] expectedWeaponsMeta,
            byte[] expectedWeaponAnimationsMeta)
        {
            string backup = GetMergedSmokeArchiveBackupPath(gtaPath);
            if (!File.Exists(backup))
                throw new InvalidDataException(
                    $"Merged-smoke archive snapshot is missing: {backup}");
            var snapshot = new RpfFile(backup, backup);
            snapshot.ScanStructure(null, error => Console.Error.WriteLine(
                $"Snapshot RPF scan warning: {error}"));
            byte[] archived = ExtractRequiredEntry(
                snapshot, BaseWeaponsMetaPath);
            byte[] archivedAnimations = ExtractRequiredEntry(
                snapshot, BaseWeaponAnimationsMetaPath);
            if (!archived.SequenceEqual(expectedWeaponsMeta))
                throw new InvalidDataException(
                    "Rollback snapshot does not contain the exact original weapons.meta.");
            if (!archivedAnimations.SequenceEqual(
                    expectedWeaponAnimationsMeta))
                throw new InvalidDataException(
                    "Rollback snapshot does not contain the exact original weaponanimations.meta.");
            Console.WriteLine(
                "Verified rollback snapshot against both original weapon metadata hashes.");
        }

        private static void ValidateMergedSmokeArchiveSnapshot(
            string gtaPath, byte[] expectedWeaponsMeta,
            byte[] expectedWeaponAnimationsMeta,
            byte[] expectedLanguageArchive)
        {
            ValidateMergedSmokeArchiveSnapshot(gtaPath,
                expectedWeaponsMeta, expectedWeaponAnimationsMeta);
            string backup = GetMergedSmokeArchiveBackupPath(gtaPath);
            var snapshot = new RpfFile(backup, backup);
            snapshot.ScanStructure(null, error => Console.Error.WriteLine(
                $"Snapshot language RPF scan warning: {error}"));
            byte[] archivedLanguage = ExtractRequiredEntry(
                snapshot, BaseAmericanLanguageArchivePath);
            if (!archivedLanguage.SequenceEqual(expectedLanguageArchive))
                throw new InvalidDataException(
                    "Rollback snapshot does not contain the exact original American language archive.");
            Console.WriteLine(
                "Verified rollback snapshot against the original language archive hash.");
        }

        private static void ValidateMergedSmokeArchiveSnapshot(
            string gtaPath, byte[] expectedWeaponsMeta,
            byte[] expectedWeaponAnimationsMeta,
            byte[] expectedLanguageArchive, byte[] expectedHudArchive)
        {
            ValidateMergedSmokeArchiveSnapshot(gtaPath,
                expectedWeaponsMeta, expectedWeaponAnimationsMeta,
                expectedLanguageArchive);
            string backup = GetMergedSmokeArchiveBackupPath(gtaPath);
            var snapshot = new RpfFile(backup, backup);
            snapshot.ScanStructure(null, error => Console.Error.WriteLine(
                $"Snapshot HUD RPF scan warning: {error}"));
            byte[] archivedHud = ExtractRequiredEntry(
                snapshot, BaseScaleformGenericArchivePath);
            if (!archivedHud.SequenceEqual(expectedHudArchive))
                throw new InvalidDataException(
                    "Rollback snapshot does not contain the exact original Scaleform HUD archive.");
            Console.WriteLine(
                "Verified rollback snapshot against the original Scaleform HUD archive hash.");
        }

        private static void WriteAtomicFileReplacing(string path, byte[] data)
        {
            string temporary = path + ".tmp";
            if (File.Exists(temporary)) File.Delete(temporary);
            try
            {
                File.WriteAllBytes(temporary, data);
                if (File.Exists(path)) File.Replace(temporary, path, null);
                else File.Move(temporary, path);
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
        }

        private static bool RestoreMergedSmokeArchiveSnapshot(string gtaPath)
        {
            string target = Path.Combine(
                gtaPath, "mods", "update", "update.rpf");
            string backup = GetMergedSmokeArchiveBackupPath(gtaPath);
            if (!File.Exists(backup))
            {
                Console.Error.WriteLine(
                    $"MISSING MERGED-SMOKE ARCHIVE SNAPSHOT: {backup}");
                return false;
            }
            File.Copy(backup, target, true);
            Console.Error.WriteLine(
                $"Restored pre-merge archive snapshot: {target}");
            return true;
        }

        private static void WriteMergedSmokeCanaryMarker(
            string gtaPath, byte[] original, byte[] installed,
            byte[] originalAnimations, byte[] installedAnimations,
            int weaponCount, string canaryState, bool whiteCanaryPassed)
        {
            string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
            Directory.CreateDirectory(Path.GetDirectoryName(marker));
            string weapons = string.Join(",\n    ",
                ColoredSmokeWeapons.Take(weaponCount).Select(spec =>
                    $"\"{spec.WeaponName}\""));
            File.WriteAllText(marker,
                "{\n" +
                "  \"schema\": 3,\n" +
                "  \"canary_state\": \"" + canaryState + "\",\n" +
                "  \"mode\": \"base_weapons_meta_merge\",\n" +
                "  \"weapon_count\": " + weaponCount + ",\n" +
                "  \"white_canary_passed\": " +
                    (whiteCanaryPassed ? "true" : "false") + ",\n" +
                "  \"weapons\": [\n    " + weapons + "\n  ],\n" +
                "  \"original_sha256\": \"" + Sha256(original) + "\",\n" +
                "  \"installed_sha256\": \"" + Sha256(installed) + "\",\n" +
                "  \"original_animations_sha256\": \"" +
                    Sha256(originalAnimations) + "\",\n" +
                "  \"installed_animations_sha256\": \"" +
                    Sha256(installedAnimations) + "\"\n" +
                "}\n", new UTF8Encoding(false));
        }

        private static void WriteMergedSmokeFullMarker(
            string gtaPath, byte[] original, byte[] installed,
            byte[] originalAnimations, byte[] installedAnimations,
            byte[] originalLanguage, byte[] installedLanguage,
            byte[] originalHud, byte[] installedHud,
            int weaponCount, string canaryState, bool whiteCanaryPassed)
        {
            string marker = GetMergedSmokeCanaryMarkerPath(gtaPath);
            Directory.CreateDirectory(Path.GetDirectoryName(marker));
            string weapons = string.Join(",\n    ",
                ColoredSmokeWeapons.Take(weaponCount).Select(spec =>
                    $"\"{spec.WeaponName}\""));
            File.WriteAllText(marker,
                "{\n" +
                "  \"schema\": 5,\n" +
                "  \"canary_state\": \"" + canaryState + "\",\n" +
                "  \"mode\": \"base_weapon_animation_language_merge\",\n" +
                "  \"weapon_count\": " + weaponCount + ",\n" +
                "  \"white_canary_passed\": " +
                    (whiteCanaryPassed ? "true" : "false") + ",\n" +
                "  \"weapon_wheel_labels\": true,\n" +
                "  \"bz_gas_icon_reused\": true,\n" +
                "  \"weapons\": [\n    " + weapons + "\n  ],\n" +
                "  \"original_sha256\": \"" + Sha256(original) + "\",\n" +
                "  \"installed_sha256\": \"" + Sha256(installed) + "\",\n" +
                "  \"original_animations_sha256\": \"" +
                    Sha256(originalAnimations) + "\",\n" +
                "  \"installed_animations_sha256\": \"" +
                    Sha256(installedAnimations) + "\",\n" +
                "  \"original_language_sha256\": \"" +
                    Sha256(originalLanguage) + "\",\n" +
                "  \"installed_language_sha256\": \"" +
                    Sha256(installedLanguage) + "\",\n" +
                "  \"original_hud_sha256\": \"" +
                    Sha256(originalHud) + "\",\n" +
                "  \"installed_hud_sha256\": \"" +
                    Sha256(installedHud) + "\"\n" +
                "}\n", new UTF8Encoding(false));
        }

        private static string GetMergedSmokeBackupDirectory(string gtaPath)
        {
            return Path.Combine(gtaPath, "scripts", "ALLIN1_backups",
                "smoke_weapon_merge");
        }

        private static string GetMergedSmokeEntryBackupPath(string gtaPath)
        {
            return Path.Combine(GetMergedSmokeBackupDirectory(gtaPath),
                "weapons.meta");
        }

        private static string GetMergedSmokeAnimationBackupPath(string gtaPath)
        {
            return Path.Combine(GetMergedSmokeBackupDirectory(gtaPath),
                "weaponanimations.meta");
        }

        private static string GetMergedSmokeLanguageBackupPath(string gtaPath)
        {
            return Path.Combine(GetMergedSmokeBackupDirectory(gtaPath),
                "american_rel.rpf");
        }

        private static string GetMergedSmokeHudBackupPath(string gtaPath)
        {
            return Path.Combine(GetMergedSmokeBackupDirectory(gtaPath),
                "scaleform_generic.rpf");
        }

        private static string GetMergedSmokeArchiveBackupPath(string gtaPath)
        {
            return Path.Combine(GetMergedSmokeBackupDirectory(gtaPath),
                "update.rpf.pre-merge.bak");
        }

        private static string GetMergedSmokeCanaryMarkerPath(string gtaPath)
        {
            return Path.Combine(gtaPath, "scripts",
                MergedSmokeCanaryMarkerName);
        }

        private static byte[] BuildColoredSmokeWeaponMeta(byte[] source)
        {
            XDocument sourceDoc = XDocument.Parse(
                Encoding.UTF8.GetString(source).TrimStart('\uFEFF'));
            XElement sourceAmmo = sourceDoc.Descendants("Item").FirstOrDefault(
                item => string.Equals(item.Element("Name")?.Value,
                    "AMMO_SMOKEGRENADE", StringComparison.Ordinal));
            XElement sourceWeapon = sourceDoc.Descendants("Item").FirstOrDefault(
                item => string.Equals(item.Element("Name")?.Value,
                    "WEAPON_SMOKEGRENADE", StringComparison.Ordinal));
            if (sourceAmmo == null || sourceWeapon == null)
                throw new InvalidDataException(
                    "Current weapons.meta does not contain the smoke grenade templates.");

            // Current Enhanced data uses navigation orders through 450 and
            // best-order values through 400. Continue those sequences rather
            // than using extreme values that the native loader may treat as
            // bounded indices.
            XElement[] navigationSlots = ColoredSmokeWeapons.Select((spec, index) =>
                new XElement("Item",
                    new XElement("OrderNumber",
                        new XAttribute("value", 451 + index)),
                    new XElement("Entry", spec.SlotName))).ToArray();
            XElement[] bestSlots = ColoredSmokeWeapons.Select((spec, index) =>
                new XElement("Item",
                    new XElement("OrderNumber",
                        new XAttribute("value", 401 + index)),
                    new XElement("Entry", spec.SlotName))).ToArray();
            XElement SlotGroup() => new XElement("Item",
                new XElement("WeaponSlots",
                    navigationSlots.Select(item => new XElement(item))));
            var doc = new XDocument(
                new XDeclaration("1.0", "UTF-8", null),
                new XElement("CWeaponInfoBlob",
                    new XElement("SlotNavigateOrder",
                        SlotGroup(), SlotGroup()),
                    new XElement("SlotBestOrder",
                        new XElement("WeaponSlots",
                            bestSlots.Select(item => new XElement(item)))),
                    new XElement("TintSpecValues"),
                    new XElement("FiringPatternAliases"),
                    new XElement("UpperBodyFixupExpressionData"),
                    new XElement("AimingInfos"),
                    new XElement("Infos",
                        new XElement("Item", new XElement("Infos",
                            ColoredSmokeWeapons.Select(spec =>
                                CloneColoredSmokeAmmo(sourceAmmo, spec)))),
                        new XElement("Item", new XElement("Infos",
                            ColoredSmokeWeapons.Select(spec =>
                                CloneColoredSmokeWeapon(sourceWeapon, spec)))),
                        new XElement("Item", new XElement("Infos"))),
                    new XElement("VehicleWeaponInfos"),
                    new XElement("Name", "DLC - ALLIN1 Colored Smoke")));
            return Encoding.UTF8.GetBytes(doc.Declaration + "\n" + doc);
        }

        private static XElement CloneColoredSmokeAmmo(
            XElement template, ColoredSmokeWeaponSpec spec)
        {
            var item = new XElement(template);
            SetElementValue(item, "Name", spec.AmmoName);
            foreach (string name in new[] { "AmmoMax", "AmmoMax50",
                "AmmoMax100", "AmmoMaxMP", "AmmoMax50MP",
                "AmmoMax100MP" })
                item.Element(name)?.SetAttributeValue("value", "5");
            SetElementValue(item, "AmmoFlags", "Fuse FixedAfterExplosion");
            foreach (string name in new[] { "LifeTime", "FromVehicleLifeTime",
                "LifeTimeAfterImpact", "ExplosionTime" })
                item.Element(name)?.SetAttributeValue("value", "30.000000");
            XElement explosion = item.Element("Explosion");
            if (explosion != null)
                foreach (XElement value in explosion.Elements())
                    value.Value = "DONTCARE";
            SetElementValue(item, "TrailFx", "");
            SetElementValue(item, "PrimedFx", "");
            return item;
        }

        private static XElement CloneColoredSmokeWeapon(
            XElement template, ColoredSmokeWeaponSpec spec)
        {
            var item = new XElement(template);
            SetElementValue(item, "Name", spec.WeaponName);
            SetElementValue(item, "Slot", spec.SlotName);
            item.Element("AmmoInfo")?.SetAttributeValue("ref", spec.AmmoName);
            SetElementValue(item, "HumanNameHash", spec.HumanNameLabel);
            SetElementValue(item, "StatName",
                "A1SM" + spec.Color.ToUpperInvariant());
            SetElementValue(item, "PickupHash", "");
            SetElementValue(item, "MPPickupHash", "");
            return item;
        }

        private static void SetElementValue(
            XElement parent, string name, string value)
        {
            XElement element = parent.Element(name);
            if (element == null)
            {
                element = new XElement(name);
                parent.Add(element);
            }
            element.Value = value ?? "";
        }

        private static byte[] BuildColoredSmokeGxt2()
        {
            var entries = new List<string>();
            foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
            {
                void Add(string label, string value)
                {
                    entries.Add(
                        $"0x{JenkHash.GenHash(label.ToLowerInvariant()):X8} = " +
                        value);
                }
                Add(spec.HumanNameLabel, spec.DisplayName);
                Add(spec.DescriptionLabel,
                    $"Deploys a dense {spec.Color} smoke screen after settling.");
                Add(spec.TooltipLabel,
                    "Throw to mark or conceal an area.");
                Add(spec.UppercaseLabel,
                    spec.DisplayName.ToUpperInvariant());
            }
            string text = string.Join("\n", entries);
            var gxt = Gxt2File.FromText(text);
            return gxt.Save();
        }

        private static byte[] BuildMergedSmokeLanguageArchive(
            string gtaPath, byte[] source)
        {
            string input = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-lang-input-{Guid.NewGuid():N}.rpf");
            string output = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-lang-output-{Guid.NewGuid():N}.rpf");
            try
            {
                File.WriteAllBytes(input, source);
                string executable = Environment.ProcessPath;
                if (string.IsNullOrEmpty(executable))
                    throw new InvalidOperationException(
                        "Could not locate the RPF patcher process.");
                var start = new ProcessStartInfo
                {
                    FileName = executable,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };
                if (Path.GetFileNameWithoutExtension(executable).Equals(
                        "dotnet", StringComparison.OrdinalIgnoreCase))
                    start.ArgumentList.Add(
                        System.Reflection.Assembly.GetExecutingAssembly()
                            .Location);
                start.ArgumentList.Add("merge-smoke-language-worker");
                start.ArgumentList.Add(gtaPath);
                start.ArgumentList.Add(input);
                start.ArgumentList.Add(output);
                using (Process process = Process.Start(start))
                {
                    string stdout = process.StandardOutput.ReadToEnd();
                    string stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    if (!string.IsNullOrWhiteSpace(stdout))
                        Console.Write(stdout);
                    if (process.ExitCode != 0 || !File.Exists(output))
                        throw new InvalidDataException(
                            "Isolated language merge failed: " +
                            (string.IsNullOrWhiteSpace(stderr)
                                ? $"exit {process.ExitCode}" : stderr.Trim()));
                }
                return File.ReadAllBytes(output);
            }
            finally
            {
                if (File.Exists(input)) File.Delete(input);
                if (File.Exists(output)) File.Delete(output);
            }
        }

        private static int MergeSmokeLanguageWorker(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: merge-smoke-language-worker <gta_path> <input_rpf> <output_rpf>");
                return 1;
            }
            try
            {
                ReloadGtaEncryptionKeys(Path.GetFullPath(args[1]));
                byte[] source = File.ReadAllBytes(args[2]);
                byte[] result =
                    BuildMergedSmokeLanguageArchiveInProcess(source);
                WriteAtomicFileReplacing(args[3], result);
                Console.WriteLine(
                    $"Built isolated smoke wheel-label archive ({result.Length:N0} bytes, " +
                    $"sha256={Sha256(result)})." );
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 9;
            }
        }

        private static byte[] BuildMergedSmokeLanguageArchiveInProcess(
            byte[] source)
        {
            string directory = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-base-lang-{Guid.NewGuid():N}");
            string temporary = Path.Combine(directory, "american_rel.rpf");
            try
            {
                Directory.CreateDirectory(directory);
                File.WriteAllBytes(temporary, source);
                var archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Base language RPF warning: {error}"));
                RpfFile.EnsureValidEncryption(archive, null, true);
                archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Open language RPF warning: {error}"));
                RpfFileEntry entry = FindFileRecursive(
                    archive, "global.gxt2");
                if (entry == null)
                    throw new InvalidDataException(
                        "Base American language archive has no global.gxt2.");
                var current = new Gxt2File();
                current.Load(entry.File.ExtractFile(entry), entry);
                var lines = new List<string> { current.ToText().TrimEnd() };
                foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
                    lines.Add(
                        $"0x{JenkHash.GenHash(spec.HumanNameLabel.ToLowerInvariant()):X8} = " +
                        spec.DisplayName);
                byte[] merged = Gxt2File.FromText(
                    string.Join("\n", lines)).Save();
                RpfFile.CreateFile(entry.Parent, entry.Name, merged, true);
                byte[] result = File.ReadAllBytes(temporary);
                ValidateMergedSmokeLanguageArchive(source, result);
                return result;
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
                if (Directory.Exists(directory)) Directory.Delete(directory);
            }
        }

        private static byte[] BuildMergedSmokeHudArchive(
            string gtaPath, byte[] source)
        {
            string input = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-hud-input-{Guid.NewGuid():N}.rpf");
            string output = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-hud-output-{Guid.NewGuid():N}.rpf");
            try
            {
                File.WriteAllBytes(input, source);
                string executable = Environment.ProcessPath;
                if (string.IsNullOrEmpty(executable))
                    throw new InvalidOperationException(
                        "Could not locate the RPF patcher process.");
                var start = new ProcessStartInfo
                {
                    FileName = executable,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };
                if (Path.GetFileNameWithoutExtension(executable).Equals(
                        "dotnet", StringComparison.OrdinalIgnoreCase))
                    start.ArgumentList.Add(
                        System.Reflection.Assembly.GetExecutingAssembly()
                            .Location);
                start.ArgumentList.Add("merge-smoke-hud-worker");
                start.ArgumentList.Add(gtaPath);
                start.ArgumentList.Add(input);
                start.ArgumentList.Add(output);
                using (Process process = Process.Start(start))
                {
                    string stdout = process.StandardOutput.ReadToEnd();
                    string stderr = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    if (!string.IsNullOrWhiteSpace(stdout))
                        Console.Write(stdout);
                    if (process.ExitCode != 0 || !File.Exists(output))
                        throw new InvalidDataException(
                            "Isolated HUD icon merge failed: " +
                            (string.IsNullOrWhiteSpace(stderr)
                                ? $"exit {process.ExitCode}" : stderr.Trim()));
                }
                return File.ReadAllBytes(output);
            }
            finally
            {
                if (File.Exists(input)) File.Delete(input);
                if (File.Exists(output)) File.Delete(output);
            }
        }

        private static int MergeSmokeHudWorker(string[] args)
        {
            if (args.Length < 4)
            {
                Console.Error.WriteLine(
                    "Usage: merge-smoke-hud-worker <gta_path> <input_rpf> <output_rpf>");
                return 1;
            }
            try
            {
                ReloadGtaEncryptionKeys(Path.GetFullPath(args[1]));
                byte[] source = File.ReadAllBytes(args[2]);
                byte[] result = BuildMergedSmokeHudArchiveInProcess(source);
                WriteAtomicFileReplacing(args[3], result);
                Console.WriteLine(
                    $"Built isolated BZ Gas wheel-icon archive ({result.Length:N0} bytes, " +
                    $"sha256={Sha256(result)}).");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 9;
            }
        }

        private static byte[] BuildMergedSmokeHudArchiveInProcess(
            byte[] source)
        {
            string directory = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-base-hud-{Guid.NewGuid():N}");
            string temporary = Path.Combine(directory,
                "scaleform_generic.rpf");
            try
            {
                Directory.CreateDirectory(directory);
                File.WriteAllBytes(temporary, source);
                var archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Base Scaleform RPF warning: {error}"));
                RpfFile.EnsureValidEncryption(archive, null, true);
                archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Open Scaleform RPF warning: {error}"));
                RpfFileEntry entry = FindFileRecursive(archive, "hud.gfx");
                if (entry == null)
                    throw new InvalidDataException(
                        "Base Scaleform archive has no hud.gfx.");
                byte[] originalHud = entry.File.ExtractFile(entry);
                byte[] patchedHud = PatchSmokeHudGfx(originalHud);
                RpfFile.CreateFile(entry.Parent, entry.Name, patchedHud, true);
                byte[] result = File.ReadAllBytes(temporary);

                archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Patched Scaleform RPF warning: {error}"));
                RpfFileEntry installedEntry = FindFileRecursive(
                    archive, "hud.gfx");
                byte[] installedHud = installedEntry?.File.ExtractFile(
                    installedEntry);
                if (installedHud == null ||
                    !installedHud.SequenceEqual(patchedHud))
                    throw new InvalidDataException(
                        "Patched Scaleform archive did not retain the verified hud.gfx payload.");
                ValidateSmokeHudGfx(originalHud, installedHud);
                return result;
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
                if (Directory.Exists(directory)) Directory.Delete(directory);
            }
        }

        private static Dictionary<uint, string> ReadGxtEntries(
            byte[] languageArchive)
        {
            string directory = Path.Combine(Path.GetTempPath(),
                $"allin1-smoke-read-lang-{Guid.NewGuid():N}");
            string temporary = Path.Combine(directory, "american_rel.rpf");
            try
            {
                Directory.CreateDirectory(directory);
                File.WriteAllBytes(temporary, languageArchive);
                var archive = new RpfFile(temporary, temporary);
                archive.ScanStructure(null, error => Console.Error.WriteLine(
                    $"Language verification RPF warning: {error}"));
                RpfFileEntry entry = FindFileRecursive(
                    archive, "global.gxt2");
                if (entry == null)
                    throw new InvalidDataException(
                        "Language archive has no global.gxt2.");
                var gxt = new Gxt2File();
                gxt.Load(entry.File.ExtractFile(entry), entry);
                var result = new Dictionary<uint, string>();
                foreach (string line in gxt.ToText().Split(
                    new[] { "\r\n", "\n" },
                    StringSplitOptions.RemoveEmptyEntries))
                {
                    Match match = Regex.Match(line,
                        @"^0x([0-9A-Fa-f]{8})\s*=\s?(.*)$");
                    if (!match.Success) continue;
                    result[Convert.ToUInt32(match.Groups[1].Value, 16)] =
                        match.Groups[2].Value;
                }
                return result;
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
                if (Directory.Exists(directory)) Directory.Delete(directory);
            }
        }

        private static void ValidateMergedSmokeLanguageArchive(
            byte[] original, byte[] candidate)
        {
            Dictionary<uint, string> before = ReadGxtEntries(original);
            Dictionary<uint, string> after = ReadGxtEntries(candidate);
            foreach (KeyValuePair<uint, string> entry in before)
                if (!after.TryGetValue(entry.Key, out string value) ||
                    !string.Equals(value, entry.Value,
                        StringComparison.Ordinal))
                    throw new InvalidDataException(
                        $"Merged language archive changed stock label 0x{entry.Key:X8}.");
            foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
            {
                uint hash = JenkHash.GenHash(
                    spec.HumanNameLabel.ToLowerInvariant());
                if (!after.TryGetValue(hash, out string value) ||
                    !string.Equals(value, spec.DisplayName,
                        StringComparison.Ordinal))
                    throw new InvalidDataException(
                        $"Merged language archive is missing {spec.DisplayName}.");
            }
            int expected = before.Keys.Union(ColoredSmokeWeapons.Select(spec =>
                JenkHash.GenHash(spec.HumanNameLabel.ToLowerInvariant()))).Count();
            if (after.Count != expected)
                throw new InvalidDataException(
                    "Merged language archive contains unexpected label changes.");
        }

        private static string[] SmokeHudAliasLabels()
        {
            return ColoredSmokeWeapons.Select(spec =>
                "INT" + unchecked((int)JenkHash.GenHash(
                    spec.WeaponName.ToLowerInvariant())).ToString(
                        System.Globalization.CultureInfo.InvariantCulture))
                .ToArray();
        }

        private static byte[] PatchSmokeHudGfx(byte[] source)
        {
            Dictionary<string, int> before = ReadGfxFrameLabels(source);
            const string bzGasLabel = "INT-1600701090";
            int bzGasFrames = before.TryGetValue(
                bzGasLabel, out int count) ? count : 0;
            if (bzGasFrames != 2)
                throw new InvalidDataException(
                    $"Expected two stock BZ Gas HUD frames; found {bzGasFrames}.");
            foreach (string alias in SmokeHudAliasLabels())
                if (before.ContainsKey(alias))
                    throw new InvalidDataException(
                        $"Smoke HUD alias already exists: {alias}");
            byte[] candidate = RewriteSmokeHudGfx(source, true);
            ValidateSmokeHudGfx(source, candidate);
            return candidate;
        }

        private static void ValidateSmokeHudGfx(
            byte[] original, byte[] candidate)
        {
            Dictionary<string, int> before = ReadGfxFrameLabels(original);
            Dictionary<string, int> after = ReadGfxFrameLabels(candidate);
            const string bzGasLabel = "INT-1600701090";
            int expected = before.TryGetValue(
                bzGasLabel, out int count) ? count : 0;
            if (expected != 2 ||
                !after.TryGetValue(bzGasLabel, out int installedBz) ||
                installedBz != expected)
                throw new InvalidDataException(
                    "The patched HUD did not preserve both BZ Gas frames.");
            foreach (string alias in SmokeHudAliasLabels())
                if (!after.TryGetValue(alias, out int aliases) ||
                    aliases != expected)
                    throw new InvalidDataException(
                        $"The patched HUD does not map {alias} to both BZ Gas frames.");
            byte[] stripped = RewriteSmokeHudGfx(candidate, false);
            if (!stripped.SequenceEqual(original))
                throw new InvalidDataException(
                    "The smoke HUD patch changed data outside its BZ Gas frame aliases.");
        }

        private static byte[] RewriteSmokeHudGfx(byte[] source, bool add)
        {
            int firstTag = GetGfxFirstTagOffset(source);
            byte[] body = RewriteGfxTagStream(
                source, firstTag, source.Length - firstTag, add);
            var result = new byte[firstTag + body.Length];
            Buffer.BlockCopy(source, 0, result, 0, firstTag);
            Buffer.BlockCopy(body, 0, result, firstTag, body.Length);
            WriteUInt32(result, 4, unchecked((uint)result.Length));
            return result;
        }

        private static byte[] RewriteGfxTagStream(
            byte[] source, int offset, int length, bool add)
        {
            string[] aliases = SmokeHudAliasLabels();
            var aliasSet = new HashSet<string>(
                aliases, StringComparer.Ordinal);
            const string bzGasLabel = "INT-1600701090";
            using (var output = new MemoryStream(length + 512))
            {
                int position = offset;
                int end = checked(offset + length);
                while (position < end)
                {
                    if (position + 2 > end)
                        throw new InvalidDataException(
                            "Truncated GFX tag header.");
                    ushort rawHeader = ReadUInt16(source, position);
                    position += 2;
                    int code = rawHeader >> 6;
                    int shortLength = rawHeader & 0x3F;
                    bool usedLongHeader = shortLength == 0x3F;
                    int payloadLength;
                    if (usedLongHeader)
                    {
                        if (position + 4 > end)
                            throw new InvalidDataException(
                                "Truncated long GFX tag header.");
                        payloadLength = checked((int)ReadUInt32(
                            source, position));
                        position += 4;
                    }
                    else
                    {
                        payloadLength = shortLength;
                    }
                    if (payloadLength < 0 ||
                        position + payloadLength > end)
                        throw new InvalidDataException(
                            "GFX tag payload exceeds its containing stream.");
                    var payload = new byte[payloadLength];
                    Buffer.BlockCopy(source, position, payload, 0,
                        payloadLength);
                    position += payloadLength;
                    if (code == 39)
                    {
                        if (payload.Length < 4)
                            throw new InvalidDataException(
                                "Truncated DefineSprite GFX tag.");
                        byte[] nested = RewriteGfxTagStream(
                            payload, 4, payload.Length - 4, add);
                        var rebuilt = new byte[4 + nested.Length];
                        Buffer.BlockCopy(payload, 0, rebuilt, 0, 4);
                        Buffer.BlockCopy(nested, 0, rebuilt, 4,
                            nested.Length);
                        payload = rebuilt;
                    }
                    string label = code == 43
                        ? ReadGfxFrameLabel(payload) : null;
                    if (!add && label != null && aliasSet.Contains(label))
                        continue;
                    WriteGfxTag(output, code, payload, usedLongHeader);
                    if (add && string.Equals(label, bzGasLabel,
                            StringComparison.Ordinal))
                        foreach (string alias in aliases)
                            WriteGfxTag(output, 43,
                                Encoding.ASCII.GetBytes(alias + "\0\0"),
                                true);
                }
                return output.ToArray();
            }
        }

        private static Dictionary<string, int> ReadGfxFrameLabels(
            byte[] source)
        {
            int firstTag = GetGfxFirstTagOffset(source);
            var result = new Dictionary<string, int>(
                StringComparer.Ordinal);
            ReadGfxFrameLabelsFromStream(source, firstTag,
                source.Length - firstTag, result);
            return result;
        }

        private static void ReadGfxFrameLabelsFromStream(
            byte[] source, int offset, int length,
            Dictionary<string, int> result)
        {
            int position = offset;
            int end = checked(offset + length);
            while (position < end)
            {
                if (position + 2 > end)
                    throw new InvalidDataException(
                        "Truncated GFX tag header during validation.");
                ushort rawHeader = ReadUInt16(source, position);
                position += 2;
                int code = rawHeader >> 6;
                int payloadLength = rawHeader & 0x3F;
                if (payloadLength == 0x3F)
                {
                    if (position + 4 > end)
                        throw new InvalidDataException(
                            "Truncated long GFX tag header during validation.");
                    payloadLength = checked((int)ReadUInt32(
                        source, position));
                    position += 4;
                }
                if (payloadLength < 0 ||
                    position + payloadLength > end)
                    throw new InvalidDataException(
                        "GFX validation tag exceeds its containing stream.");
                if (code == 39)
                {
                    if (payloadLength < 4)
                        throw new InvalidDataException(
                            "Truncated DefineSprite during GFX validation.");
                    ReadGfxFrameLabelsFromStream(source,
                        position + 4, payloadLength - 4, result);
                }
                else if (code == 43)
                {
                    var payload = new byte[payloadLength];
                    Buffer.BlockCopy(source, position, payload, 0,
                        payloadLength);
                    string label = ReadGfxFrameLabel(payload);
                    if (!string.IsNullOrEmpty(label))
                        result[label] = result.TryGetValue(
                            label, out int existing) ? existing + 1 : 1;
                }
                position += payloadLength;
            }
        }

        private static int GetGfxFirstTagOffset(byte[] source)
        {
            if (source == null || source.Length < 13 ||
                source[0] != (byte)'G' || source[1] != (byte)'F' ||
                source[2] != (byte)'X')
                throw new InvalidDataException(
                    "HUD payload is not an uncompressed Scaleform GFX file.");
            uint declaredLength = ReadUInt32(source, 4);
            if (declaredLength != source.Length)
                throw new InvalidDataException(
                    "HUD GFX declared length does not match its payload.");
            int coordinateBits = source[8] >> 3;
            int rectBytes = checked((5 + coordinateBits * 4 + 7) / 8);
            int firstTag = checked(8 + rectBytes + 4);
            if (firstTag > source.Length)
                throw new InvalidDataException(
                    "HUD GFX header is truncated.");
            return firstTag;
        }

        private static string ReadGfxFrameLabel(byte[] payload)
        {
            int end = Array.IndexOf(payload, (byte)0);
            if (end < 0) end = payload.Length;
            return Encoding.ASCII.GetString(payload, 0, end);
        }

        private static void WriteGfxTag(Stream output, int code,
            byte[] payload, bool preferLongHeader)
        {
            bool useLong = preferLongHeader || payload.Length >= 0x3F;
            ushort header = checked((ushort)((code << 6) |
                (useLong ? 0x3F : payload.Length)));
            var headerBytes = new byte[useLong ? 6 : 2];
            WriteUInt16(headerBytes, 0, header);
            if (useLong)
                WriteUInt32(headerBytes, 2,
                    unchecked((uint)payload.Length));
            output.Write(headerBytes, 0, headerBytes.Length);
            output.Write(payload, 0, payload.Length);
        }

        private static ushort ReadUInt16(byte[] data, int offset)
        {
            return unchecked((ushort)(data[offset] |
                data[offset + 1] << 8));
        }

        private static uint ReadUInt32(byte[] data, int offset)
        {
            return unchecked((uint)(data[offset] |
                data[offset + 1] << 8 |
                data[offset + 2] << 16 |
                data[offset + 3] << 24));
        }

        private static void WriteUInt16(byte[] data, int offset,
            ushort value)
        {
            data[offset] = unchecked((byte)value);
            data[offset + 1] = unchecked((byte)(value >> 8));
        }

        private static void WriteUInt32(byte[] data, int offset,
            uint value)
        {
            data[offset] = unchecked((byte)value);
            data[offset + 1] = unchecked((byte)(value >> 8));
            data[offset + 2] = unchecked((byte)(value >> 16));
            data[offset + 3] = unchecked((byte)(value >> 24));
        }

        private static string BuildColoredSmokeShopMeta()
        {
            XElement ShopItem(ColoredSmokeWeaponSpec spec) =>
                new XElement("Item",
                    new XElement("lockHash", spec.LockHash),
                    new XElement("nameHash", spec.WeaponName),
                    new XElement("cost", new XAttribute("value", "750")),
                    new XElement("ammoCost", new XAttribute("value", "150")),
                    new XElement("textLabel", spec.HumanNameLabel),
                    new XElement("weaponDesc", spec.DescriptionLabel),
                    new XElement("weaponTT", spec.TooltipLabel),
                    new XElement("weaponUppercase", spec.UppercaseLabel),
                    new XElement("id", new XAttribute("value", "32")),
                    new XElement("weaponComponents"));
            var doc = new XDocument(
                new XDeclaration("1.0", "UTF-8", null),
                new XElement("WeaponShopItemArray",
                    new XElement("weaponShopItems",
                        ColoredSmokeWeapons.Select(ShopItem))));
            return doc.Declaration + "\n" + doc;
        }

        private static string BuildColoredSmokeContentXml()
        {
            const string weaponPath =
                "dlc_allin1_smokeCRC:/common/data/ai/weaponAllin1Smoke.meta";
            const string shopPath =
                "dlc_allin1_smokeCRC:/common/data/shop_weapon.meta";
            const string textPath =
                "dlc_allin1_smoke:/common/data/dlctext.meta";
            XElement DataFile(string path, string type, bool persistent) =>
                new XElement("Item",
                new XElement("filename", path),
                new XElement("fileType", type),
                new XElement("overlay", new XAttribute("value", "false")),
                new XElement("disabled", new XAttribute("value", "true")),
                new XElement("persistent", new XAttribute("value",
                    persistent ? "true" : "false")));
            var doc = new XDocument(
                new XDeclaration("1.0", "UTF-8", null),
                new XElement("CDataFileMgr__ContentsOfDataFileXml",
                    new XElement("disabledFiles"),
                    new XElement("includedXmlFiles"),
                    new XElement("includedDataFiles"),
                    new XElement("dataFiles",
                        DataFile(weaponPath, "WEAPONINFO_FILE", false),
                        DataFile(shopPath,
                            "WEAPON_SHOP_INFO_METADATA_FILE", false),
                        DataFile(textPath, "TEXTFILE_METAFILE", true)),
                    new XElement("contentChangeSets",
                        new XElement("Item",
                            new XElement("changeSetName",
                                "ALLIN1_SMOKE_AUTOGEN"),
                            new XElement("mapChangeSetData"),
                            new XElement("filesToInvalidate"),
                            new XElement("filesToDisable"),
                            new XElement("filesToEnable",
                                new XElement("Item", weaponPath),
                                new XElement("Item", shopPath),
                                new XElement("Item", textPath)),
                            new XElement("txdToLoad"),
                            new XElement("txdToUnload"),
                            new XElement("residentResources"),
                            new XElement("unregisterResources"),
                            new XElement("requiresLoadingScreen",
                                new XAttribute("value", "false")))),
                    new XElement("patchFiles")));
            return doc.Declaration + "\n" + doc;
        }

        private static string BuildColoredSmokeSetupXml()
        {
            var doc = new XDocument(
                new XDeclaration("1.0", "UTF-8", null),
                new XElement("SSetupData",
                    new XElement("deviceName", "dlc_allin1_smoke"),
                    new XElement("datFile", "content.xml"),
                    new XElement("timeStamp", "18/08/2026 00:00:00"),
                    new XElement("nameHash", "allin1_smoke"),
                    // SSetupData is positional in the game data loader. These
                    // empty/default nodes are present in Rockstar DLCs and in
                    // ALLIN1's known-working map and preview packs.
                    new XElement("contentChangeSets"),
                    new XElement("contentChangeSetGroups",
                        new XElement("Item",
                            new XElement("NameHash", "GROUP_STARTUP"),
                            new XElement("ContentChangeSets",
                                new XElement("Item",
                                    "ALLIN1_SMOKE_AUTOGEN")))),
                    new XElement("startupScript"),
                    new XElement("scriptCallstackSize",
                        new XAttribute("value", "0")),
                    new XElement("type", "EXTRACONTENT_COMPAT_PACK"),
                    // The current Enhanced sequence ends at 57
                    // (mp2026_01_G9EC). Load immediately after stock content.
                    new XElement("order", new XAttribute("value", "58")),
                    new XElement("minorOrder", new XAttribute("value", "0")),
                    new XElement("isLevelPack",
                        new XAttribute("value", "false")),
                    new XElement("dependencyPackHash"),
                    new XElement("requiredVersion")));
            doc.Root.Add(new XElement("subPackCount",
                new XAttribute("value", "0")));
            return doc.Declaration + "\n" + doc;
        }

        private static string BuildColoredSmokeTextMeta()
        {
            return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" +
                "<CExtraTextMetaFile>\n" +
                "  <hasGlobalTextFile value=\"true\"/>\n" +
                "  <hasAdditionalText value=\"true\"/>\n" +
                "  <isTitleUpdate value=\"false\"/>\n" +
                "</CExtraTextMetaFile>\n";
        }

        private static int VerifyColoredSmokeDlc(string archive)
        {
            if (!File.Exists(archive))
            {
                Console.Error.WriteLine(
                    $"ERROR: Colored smoke DLC is missing: {archive}");
                return 4;
            }
            try
            {
                var rpf = new RpfFile(archive, archive);
                rpf.ScanStructure(null, error => Console.Error.WriteLine(
                    $"RPF scan warning: {error}"));
                RpfFileEntry weaponEntry = FindRelativeEntry(
                    rpf, "common/data/ai/weaponAllin1Smoke.meta");
                RpfFileEntry shopEntry = FindRelativeEntry(
                    rpf, "common/data/shop_weapon.meta");
                RpfFileEntry textMeta = FindRelativeEntry(
                    rpf, "common/data/dlctext.meta");
                RpfFileEntry languageEntry = FindRelativeEntry(
                    rpf, "x64/data/lang/americandlc.rpf");
                RpfFileEntry contentEntry = FindFileRecursive(
                    rpf, "content.xml");
                RpfFileEntry setupEntry = FindFileRecursive(
                    rpf, "setup2.xml");
                if (weaponEntry == null || shopEntry == null ||
                    textMeta == null ||
                    languageEntry == null ||
                    contentEntry == null || setupEntry == null)
                    throw new InvalidDataException(
                        "Colored smoke DLC is missing required metadata.");

                string setupText = Encoding.UTF8.GetString(
                    setupEntry.File.ExtractFile(setupEntry)).TrimStart('\uFEFF');
                XDocument setupDoc = XDocument.Parse(setupText);
                XElement setup = setupDoc.Root;
                string[] requiredSetupNodes =
                {
                    "deviceName", "datFile", "timeStamp", "nameHash",
                    "contentChangeSets", "contentChangeSetGroups",
                    "startupScript", "scriptCallstackSize", "type", "order",
                    "minorOrder", "isLevelPack", "dependencyPackHash",
                    "requiredVersion", "subPackCount",
                };
                if (setup == null || setup.Name.LocalName != "SSetupData" ||
                    !setup.Elements().Select(element => element.Name.LocalName)
                        .SequenceEqual(requiredSetupNodes))
                    throw new InvalidDataException(
                        "Colored smoke setup2.xml does not match the required positional SSetupData schema.");
                string startupChangeSet = setup.Element(
                        "contentChangeSetGroups")?.Descendants("ContentChangeSets")
                    .SelectMany(element => element.Elements("Item"))
                    .Select(element => element.Value?.Trim())
                    .FirstOrDefault(value => value == "ALLIN1_SMOKE_AUTOGEN");
                if (startupChangeSet == null ||
                    setup.Element("deviceName")?.Value != "dlc_allin1_smoke" ||
                    setup.Element("type")?.Value != "EXTRACONTENT_COMPAT_PACK" ||
                    setup.Element("order")?.Attribute("value")?.Value != "58" ||
                    setup.Element("minorOrder")?.Attribute("value")?.Value != "0" ||
                    setup.Element("isLevelPack")?.Attribute("value")?.Value != "false" ||
                    setup.Element("subPackCount")?.Attribute("value")?.Value != "0")
                    throw new InvalidDataException(
                        "Colored smoke setup2.xml startup registration is invalid.");

                string contentText = Encoding.UTF8.GetString(
                    contentEntry.File.ExtractFile(contentEntry)).TrimStart('\uFEFF');
                XDocument contentDoc = XDocument.Parse(contentText);
                string[] requiredContentNodes =
                {
                    "disabledFiles", "includedXmlFiles", "includedDataFiles",
                    "dataFiles", "contentChangeSets", "patchFiles",
                };
                if (contentDoc.Root == null ||
                    contentDoc.Root.Name.LocalName !=
                        "CDataFileMgr__ContentsOfDataFileXml" ||
                    !contentDoc.Root.Elements()
                        .Select(element => element.Name.LocalName)
                        .SequenceEqual(requiredContentNodes))
                    throw new InvalidDataException(
                        "Colored smoke content.xml does not match the required positional root schema.");
                XElement changeSet = contentDoc.Descendants("Item")
                    .FirstOrDefault(element =>
                        element.Element("changeSetName")?.Value ==
                            "ALLIN1_SMOKE_AUTOGEN");
                string[] requiredChangeSetNodes =
                {
                    "changeSetName", "mapChangeSetData",
                    "filesToInvalidate", "filesToDisable", "filesToEnable",
                    "txdToLoad", "txdToUnload", "residentResources",
                    "unregisterResources", "requiresLoadingScreen",
                };
                if (changeSet == null ||
                    !changeSet.Elements()
                        .Select(element => element.Name.LocalName)
                        .SequenceEqual(requiredChangeSetNodes) ||
                    changeSet.Element("requiresLoadingScreen")?
                        .Attribute("value")?.Value != "false")
                    throw new InvalidDataException(
                        "Colored smoke content.xml change set does not match " +
                        "the current positional schema.");
                string weaponPath =
                    "dlc_allin1_smokeCRC:/common/data/ai/weaponAllin1Smoke.meta";
                string shopPath =
                    "dlc_allin1_smokeCRC:/common/data/shop_weapon.meta";
                XElement weaponRegistration = contentDoc.Descendants("dataFiles")
                    .Elements("Item").FirstOrDefault(item =>
                        item.Element("filename")?.Value == weaponPath);
                XElement shopRegistration = contentDoc.Descendants("dataFiles")
                    .Elements("Item").FirstOrDefault(item =>
                        item.Element("filename")?.Value == shopPath);
                XElement textRegistration = contentDoc.Descendants("dataFiles")
                    .Elements("Item").FirstOrDefault(item =>
                        item.Element("filename")?.Value ==
                            "dlc_allin1_smoke:/common/data/dlctext.meta");
                if (weaponRegistration?.Element("fileType")?.Value !=
                        "WEAPONINFO_FILE" ||
                    shopRegistration?.Element("fileType")?.Value !=
                        "WEAPON_SHOP_INFO_METADATA_FILE" ||
                    textRegistration?.Element("persistent")?
                        .Attribute("value")?.Value != "true" ||
                    !changeSet.Element("filesToEnable").Elements("Item")
                        .Any(item => item.Value == weaponPath) ||
                    !changeSet.Element("filesToEnable").Elements("Item")
                        .Any(item => item.Value == shopPath))
                    throw new InvalidDataException(
                        "Colored smoke content.xml CRC/text registration is invalid.");

                string shopText = Encoding.UTF8.GetString(
                    shopEntry.File.ExtractFile(shopEntry)).TrimStart('\uFEFF');
                XDocument shopDoc = XDocument.Parse(shopText);
                XElement[] shopItems = shopDoc.Root?
                    .Element("weaponShopItems")?.Elements("Item").ToArray()
                    ?? Array.Empty<XElement>();
                if (shopDoc.Root?.Name.LocalName != "WeaponShopItemArray" ||
                    shopItems.Length != ColoredSmokeWeapons.Length ||
                    shopItems.Select(item => item.Element("nameHash")?.Value)
                        .Distinct(StringComparer.Ordinal).Count() !=
                            ColoredSmokeWeapons.Length)
                    throw new InvalidDataException(
                        "Colored smoke shop metadata is missing or duplicated.");
                foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
                {
                    XElement item = shopItems.SingleOrDefault(candidate =>
                        candidate.Element("nameHash")?.Value == spec.WeaponName);
                    string[] requiredShopNodes =
                    {
                        "lockHash", "nameHash", "cost", "ammoCost",
                        "textLabel", "weaponDesc", "weaponTT",
                        "weaponUppercase", "id", "weaponComponents",
                    };
                    if (item == null ||
                        !item.Elements().Select(element =>
                            element.Name.LocalName).SequenceEqual(
                                requiredShopNodes) ||
                        item.Element("lockHash")?.Value != spec.LockHash ||
                        item.Element("textLabel")?.Value !=
                            spec.HumanNameLabel ||
                        item.Element("weaponDesc")?.Value !=
                            spec.DescriptionLabel ||
                        item.Element("weaponTT")?.Value !=
                            spec.TooltipLabel ||
                        item.Element("weaponUppercase")?.Value !=
                            spec.UppercaseLabel ||
                        item.Element("cost")?.Attribute("value")?.Value !=
                            "750" ||
                        item.Element("ammoCost")?.Attribute("value")?.Value !=
                            "150" ||
                        item.Element("id")?.Attribute("value")?.Value != "32" ||
                        item.Element("weaponComponents")?.HasElements == true)
                        throw new InvalidDataException(
                            $"Invalid shop registration for {spec.Color} smoke.");
                }

                string textMetaText = Encoding.UTF8.GetString(
                    textMeta.File.ExtractFile(textMeta)).TrimStart('\uFEFF');
                XDocument textMetaDoc = XDocument.Parse(textMetaText);
                if (textMetaDoc.Root?.Name.LocalName != "CExtraTextMetaFile" ||
                    textMetaDoc.Root.Element("hasGlobalTextFile")?
                        .Attribute("value")?.Value != "true" ||
                    textMetaDoc.Root.Element("hasAdditionalText")?
                        .Attribute("value")?.Value != "true" ||
                    textMetaDoc.Root.Element("isTitleUpdate")?
                        .Attribute("value")?.Value != "false")
                    throw new InvalidDataException(
                        "Colored smoke dlctext.meta does not match the current text schema.");

                string weaponText = Encoding.UTF8.GetString(
                    weaponEntry.File.ExtractFile(weaponEntry)).TrimStart('\uFEFF');
                XDocument weaponDoc = XDocument.Parse(weaponText);
                int[] navigationOrders = weaponDoc.Root?
                    .Element("SlotNavigateOrder")?
                    .Elements("Item").FirstOrDefault()?
                    .Descendants("OrderNumber")
                    .Select(element => int.TryParse(
                        element.Attribute("value")?.Value, out int value)
                        ? value : -1)
                    .ToArray() ?? Array.Empty<int>();
                int[] bestOrders = weaponDoc.Root?.Element("SlotBestOrder")?
                    .Descendants("OrderNumber")
                    .Select(element => int.TryParse(
                        element.Attribute("value")?.Value, out int value)
                        ? value : -1)
                    .ToArray() ?? Array.Empty<int>();
                if (!navigationOrders.SequenceEqual(
                        Enumerable.Range(451, ColoredSmokeWeapons.Length)) ||
                    !bestOrders.SequenceEqual(
                        Enumerable.Range(401, ColoredSmokeWeapons.Length)))
                    throw new InvalidDataException(
                        "Colored smoke weapon-wheel ordering is unsafe or incomplete.");
                foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
                {
                    XElement ammo = weaponDoc.Descendants("Item").FirstOrDefault(
                        item => item.Element("Name")?.Value == spec.AmmoName);
                    XElement weapon = weaponDoc.Descendants("Item").FirstOrDefault(
                        item => item.Element("Name")?.Value == spec.WeaponName);
                    if (ammo == null || weapon == null)
                        throw new InvalidDataException(
                            $"Missing colored smoke definition: {spec.Color}");
                    if ((ammo.Element("AmmoFlags")?.Value ?? "").Contains(
                            "AddSmokeOnExplosion") ||
                        !string.IsNullOrEmpty(ammo.Element("TrailFx")?.Value) ||
                        !string.IsNullOrEmpty(ammo.Element("PrimedFx")?.Value) ||
                        ammo.Element("Explosion")?.Element("Default")?.Value !=
                            "DONTCARE")
                        throw new InvalidDataException(
                            $"Native smoke was not isolated for {spec.Color}.");
                    if (weapon.Element("AmmoInfo")?.Attribute("ref")?.Value !=
                            spec.AmmoName)
                        throw new InvalidDataException(
                            $"Ammo pool is not independent for {spec.Color}.");
                }

                string tempInner = Path.Combine(Path.GetTempPath(),
                    $"allin1-smoke-lang-{Guid.NewGuid():N}.rpf");
                try
                {
                    File.WriteAllBytes(tempInner,
                        languageEntry.File.ExtractFile(languageEntry));
                    var inner = new RpfFile(tempInner, tempInner);
                    inner.ScanStructure(null, error => Console.Error.WriteLine(
                        $"Language RPF warning: {error}"));
                    RpfFileEntry gxtEntry = FindFileRecursive(inner, "global.gxt2");
                    if (gxtEntry == null)
                        throw new InvalidDataException(
                            "Colored smoke language file is missing.");
                    var gxt = new Gxt2File();
                    gxt.Load(gxtEntry.File.ExtractFile(gxtEntry), gxtEntry);
                    string labels = gxt.ToText();
                    foreach (ColoredSmokeWeaponSpec spec in ColoredSmokeWeapons)
                    {
                        string[] expectedText =
                        {
                            spec.DisplayName,
                            $"Deploys a dense {spec.Color} smoke screen after settling.",
                            "Throw to mark or conceal an area.",
                            spec.DisplayName.ToUpperInvariant(),
                        };
                        foreach (string expected in expectedText)
                            if (labels.IndexOf("= " + expected,
                                    StringComparison.Ordinal) < 0)
                            throw new InvalidDataException(
                                $"Missing smoke text label: {expected}");
                    }
                }
                finally
                {
                    if (File.Exists(tempInner)) File.Delete(tempInner);
                }
                Console.WriteLine(
                    "Verified seven registered colored smoke weapons, shop " +
                    "records, ammo pools, labels, and isolated projectiles.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                return 7;
            }
        }

        private static void WriteColoredSmokeMarker(
            string gtaPath, string archive)
        {
            string marker = GetColoredSmokeMarkerPath(gtaPath);
            Directory.CreateDirectory(Path.GetDirectoryName(marker));
            string weapons = string.Join(",\n    ", ColoredSmokeWeapons.Select(
                spec => $"\"{spec.WeaponName}\""));
            File.WriteAllText(marker,
                "{\n" +
                "  \"schema\": 2,\n" +
                "  \"pack_id\": \"allin1_smoke\",\n" +
                "  \"canary_state\": \"pending\",\n" +
                "  \"archive_sha256\": \"" +
                    Sha256(File.ReadAllBytes(archive)) + "\",\n" +
                "  \"native_tear_gas_unchanged\": true,\n" +
                "  \"native_smoke_vfx_disabled_per_projectile\": true,\n" +
                "  \"weapons\": [\n    " + weapons + "\n  ]\n" +
                "}\n", new UTF8Encoding(false));
        }

        private static string GetColoredSmokeMarkerPath(string gtaPath)
        {
            return Path.Combine(gtaPath, "scripts",
                "ALLIN1_colored_smoke_weapons.json");
        }

        private static void RestoreColoredSmokeDestination(
            string destination, string backup)
        {
            if (File.Exists(backup)) File.Copy(backup, destination, true);
            else if (File.Exists(destination)) File.Delete(destination);
        }

        // ================================================================
        //  patch / unpatch: Modify dlclist.xml inside mods/update.rpf
        // ================================================================

        static int PatchCommand(
            string command, string[] args, bool allowManifestOwnedPack = false)
        {
            string gtaPath = args[1];

            try
            {
                string[] requested = args.Skip(2).ToArray();
                if (allowManifestOwnedPack && requested.Length == 0)
                {
                    Console.Error.WriteLine(
                        "ERROR: Managed DLC registration requires a pack name.");
                    return 2;
                }
                if (allowManifestOwnedPack && command == "patch")
                {
                    foreach (string pack in requested)
                    {
                        ValidateManagedDlcPackName(pack);
                        string payload = Path.Combine(
                            gtaPath, "mods", "update", "x64", "dlcpacks",
                            pack, "dlc.rpf");
                        if (!File.Exists(payload))
                        {
                            Console.Error.WriteLine(
                                $"ERROR: Refusing to register '{pack}'; payload " +
                                $"does not exist at {payload}");
                            return 4;
                        }
                    }
                }

                var rpf = OpenModsUpdateRpf(gtaPath, out int err);
                if (rpf == null) return err;

                // --- Find dlclist.xml ---
                var dlclistEntry = FindFileRecursive(rpf, "dlclist.xml");

                if (dlclistEntry == null)
                {
                    Console.Error.WriteLine("ERROR: dlclist.xml not found in update.rpf (searched all nested RPFs)");
                    return 5;
                }

                Console.WriteLine($"Found dlclist.xml at: {dlclistEntry.Path}");

                // --- Extract and parse XML ---
                byte[] xmlBytes = dlclistEntry.File.ExtractFile(dlclistEntry);
                if (xmlBytes == null || xmlBytes.Length == 0)
                {
                    Console.Error.WriteLine("ERROR: Failed to extract dlclist.xml (empty data).");
                    return 5;
                }

                // Strip BOM if present
                string xmlStr = Encoding.UTF8.GetString(xmlBytes).TrimStart('\uFEFF');

                XDocument doc;
                try
                {
                    doc = XDocument.Parse(xmlStr);
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine($"ERROR: Failed to parse dlclist.xml: {ex.Message}");
                    return 6;
                }

                var paths = doc.Root?.Element("Paths");
                if (paths == null)
                {
                    Console.Error.WriteLine("ERROR: <Paths> element not found in dlclist.xml");
                    return 6;
                }

                // --- Patch or unpatch ---
                bool modified;
                if (command == "patch")
                    modified = PatchDlcList(
                        paths, requested, allowManifestOwnedPack);
                else
                    modified = UnpatchDlcList(
                        paths, requested, allowManifestOwnedPack);

                if (!modified)
                {
                    Console.WriteLine("No changes needed — dlclist.xml already up to date.");
                    return 0;
                }

                // --- Write modified XML back into RPF ---
                Console.WriteLine("Writing modified dlclist.xml back to mods RPF...");

                byte[] newXmlBytes = Encoding.UTF8.GetBytes(doc.Declaration + "\n" + doc.ToString());

                try
                {
                    RpfFile.CreateFile(dlclistEntry.Parent, dlclistEntry.Name, newXmlBytes, true);
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine($"ERROR: Failed to write dlclist.xml: {ex.Message}");
                    return 7;
                }

                Console.WriteLine("dlclist.xml updated successfully in mods/update/update.rpf.");
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: Unexpected error: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        private static void ValidateManagedDlcPackName(string pack)
        {
            if (string.IsNullOrWhiteSpace(pack) ||
                !Regex.IsMatch(pack, "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"))
                throw new ArgumentException(
                    $"Invalid manifest-owned DLC pack name: {pack}");
        }

        private static string DlcEntry(string pack, bool allowManifestOwnedPack)
        {
            if (!allowManifestOwnedPack)
            {
                if (!OwnedDlcEntries.ContainsKey(pack))
                {
                    Console.Error.WriteLine(
                        $"ERROR: Refusing to register unowned DLC pack '{pack}'.");
                    throw new ArgumentException($"Unknown ALLIN1 DLC pack: {pack}");
                }
                return OwnedDlcEntries[pack];
            }
            ValidateManagedDlcPackName(pack);
            return $"dlcpacks:/{pack}/";
        }

        private static bool PatchDlcList(
            XElement paths, string[] requested, bool allowManifestOwnedPack = false)
        {
            string[] packs = requested != null && requested.Length > 0
                ? requested
                : new[] { "allin1_previews" };

            bool modified = false;
            foreach (string pack in packs.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                string entry = DlcEntry(pack, allowManifestOwnedPack);
                bool exists = paths.Elements("Item").Any(item =>
                    string.Equals(
                        item.Value?.Trim().TrimEnd('/'),
                        entry.TrimEnd('/'),
                        StringComparison.OrdinalIgnoreCase));
                if (exists)
                {
                    Console.WriteLine($"Entry '{pack}' already present in dlclist.xml.");
                    continue;
                }
                paths.Add(new XElement("Item", entry));
                Console.WriteLine($"Added '{entry}' to dlclist.xml.");
                modified = true;
            }
            return modified;
        }

        private static RpfFileEntry FindFileRecursive(RpfFile rpf, string fileName)
        {
            var entry = rpf.AllEntries?
                .OfType<RpfFileEntry>()
                .FirstOrDefault(e =>
                    e.Name != null &&
                    e.Name.Equals(fileName, StringComparison.OrdinalIgnoreCase));

            if (entry != null) return entry;

            if (rpf.Children != null)
            {
                foreach (var child in rpf.Children)
                {
                    entry = FindFileRecursive(child, fileName);
                    if (entry != null) return entry;
                }
            }

            return null;
        }

        private static bool UnpatchDlcList(
            XElement paths, string[] requested,
            bool allowManifestOwnedPack = false)
        {
            string[] packs = requested != null && requested.Length > 0
                ? requested : OwnedDlcEntries.Keys.ToArray();
            packs = packs.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
            var entries = packs.ToDictionary(
                pack => pack,
                pack => DlcEntry(pack, allowManifestOwnedPack),
                StringComparer.OrdinalIgnoreCase);
            bool removed = false;
            var toRemove = paths.Elements("Item")
                .Where(item =>
                {
                    string text = item.Value?.Trim().TrimEnd('/').ToLower() ?? "";
                    return packs.Any(name => string.Equals(
                        text, entries[name].TrimEnd('/').ToLower(),
                        StringComparison.OrdinalIgnoreCase));
                })
                .ToList();

            foreach (var item in toRemove)
            {
                item.Remove();
                removed = true;
            }

            if (removed)
                Console.WriteLine("Removed managed DLC entries from dlclist.xml.");
            else
                Console.WriteLine("No requested managed DLC entries found in dlclist.xml.");

            return removed;
        }
    }
}
