// Read-only Rockstar vehicle seat/layout catalog extraction.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;
using CodeWalker.GameFiles;
using CodeWalker.Utils;

namespace RpfPatcher
{
    internal static class SeatCatalogAudit
    {
        private sealed class SourceDocument
        {
            internal string Archive;
            internal string Entry;
            internal string Pack;
            internal int Priority;
            internal XDocument Xml;
        }

        private sealed class VehicleDefinition
        {
            internal string Model;
            internal string Layout;
            internal string GameName;
            internal string MakeName;
            internal string Flags;
            internal SourceDocument Source;
        }

        private sealed class LayoutDefinition
        {
            internal string Name;
            internal XElement Node;
            internal SourceDocument Source;
        }

        private sealed class EntryDefinition
        {
            internal string Name;
            internal XElement Node;
            internal SourceDocument Source;
        }

        internal sealed class SeatRecord
        {
            public int Index { get; set; }
            public string Label { get; set; }
            public string RockstarSeat { get; set; }
            public bool Turret { get; set; }
        }

        internal sealed class VehicleRecord
        {
            public string Model { get; set; }
            public int ModelHash { get; set; }
            public string Layout { get; set; }
            public string GameName { get; set; }
            public string MakeName { get; set; }
            public int SeatCount { get; set; }
            public int PassengerCapacity { get; set; }
            public int PassengerDoorCount { get; set; }
            public int AccessHatchCount { get; set; }
            public int EntryPointCount { get; set; }
            public List<string> DoorBones { get; set; } = new List<string>();
            public List<SeatRecord> Seats { get; set; } = new List<SeatRecord>();
            public string ModelSource { get; set; }
            public string LayoutSource { get; set; }
            public string Pack { get; set; }
            public string Status { get; set; }
        }

        internal sealed class CatalogRecord
        {
            public int SchemaVersion { get; set; } = 1;
            public string GeneratedUtc { get; set; }
            public string Edition { get; set; }
            public int ArchiveCount { get; set; }
            public int SourceDocumentCount { get; set; }
            public int ModelCount { get; set; }
            public int ResolvedCount { get; set; }
            public int UnresolvedCount { get; set; }
            public List<string> Warnings { get; set; } = new List<string>();
            public List<VehicleRecord> Vehicles { get; set; } =
                new List<VehicleRecord>();
        }

        private static readonly Dictionary<string, Dictionary<int, string>>
            LabelOverrides = new Dictionary<string, Dictionary<int, string>>(
                StringComparer.OrdinalIgnoreCase)
            {
                { "limo2", Labels(
                    (1, "Left Rear"), (2, "Right Rear"),
                    (3, "Roof Turret")) },
                { "caracara", Labels((3, "Bed Turret")) },
                { "technical", Labels((1, "Bed Turret")) },
                { "technical3", Labels((1, "Bed Turret")) },
                { "insurgent", Labels(
                    (3, "Left Side Seat"), (4, "Right Side Seat"),
                    (5, "Left Bed Seat"), (6, "Right Bed Seat"),
                    (7, "Roof Turret")) },
                { "insurgent2", Labels(
                    (3, "Left Side Seat"), (4, "Right Side Seat"),
                    (5, "Left Bed Seat"), (6, "Right Bed Seat"),
                    (7, "Roof Turret")) },
                { "insurgent3", Labels(
                    (3, "Left Side Seat"), (4, "Right Side Seat"),
                    (5, "Left Bed Seat"), (6, "Right Bed Seat"),
                    (7, "Roof Turret")) },
                { "halftrack", Labels((1, "Rear Turret")) },
                { "barrage", Labels((1, "Top Turret"), (2, "Rear Turret")) },
                { "menacer", Labels((3, "Roof Turret")) },
                { "apc", Labels(
                    (0, "Cannon Turret"), (1, "Left Rear Turret"),
                    (2, "Right Rear Turret")) },
                { "chernobog", Labels((0, "Missile Turret")) },
                { "khanjali", Labels(
                    (0, "Machine Gun Turret"), (1, "Left Grenade Turret"),
                    (2, "Right Grenade Turret")) },
                { "dune3", Labels((0, "Front Turret")) },
                { "boxville5", Labels((3, "Roof Turret")) },
                { "guardian", Labels(
                    (3, "Left Bed Seat"), (4, "Right Bed Seat")) },
                { "wastelander", Labels(
                    (1, "Left Deck 1"), (2, "Right Deck 1"),
                    (3, "Left Deck 2"), (4, "Right Deck 2")) },
                { "valkyrie", Labels(
                    (0, "Front Turret"), (1, "Left Turret"),
                    (2, "Right Turret")) },
                { "hunter", Labels((0, "Front Turret")) },
                { "annihilator2", Labels(
                    (3, "Left Rappel Seat"), (4, "Right Rappel Seat")) },
                { "bombushka", Labels(
                    (0, "Co-pilot / Nose Turret"), (1, "Top Turret"),
                    (2, "Rear Turret"), (3, "Left Bench"),
                    (4, "Right Bench")) },
                { "mogul", Labels((1, "Rear Turret")) },
                { "tula", Labels((1, "Rear Turret")) },
                { "volatol", Labels(
                    (0, "Co-pilot / Nose Turret"), (1, "Top Turret"),
                    (2, "Passenger")) },
                { "dinghy5", Labels((3, "Front Turret")) },
            };

        internal static int Run(string[] args)
        {
            if (args.Length < 3)
            {
                Console.Error.WriteLine(
                    "Usage: RpfPatcher.exe audit-seats <gta_path> <output_json> [output_cs]");
                return 1;
            }
            string gtaPath = Path.GetFullPath(args[1]);
            string outputJson = Path.GetFullPath(args[2]);
            string outputCs = args.Length >= 4 ? Path.GetFullPath(args[3]) : null;
            bool enhanced = File.Exists(Path.Combine(gtaPath, "GTA5_Enhanced.exe"));
            string executable = enhanced ? "GTA5_Enhanced.exe" : "GTA5.exe";
            if (!File.Exists(Path.Combine(gtaPath, executable)))
            {
                Console.Error.WriteLine($"ERROR: {executable} not found in {gtaPath}");
                return 2;
            }

            try
            {
                GTA5Keys.LoadFromPath(gtaPath, enhanced, null);
                var catalog = BuildCatalog(gtaPath, enhanced);
                string parent = Path.GetDirectoryName(outputJson);
                if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                var options = new JsonSerializerOptions { WriteIndented = true };
                File.WriteAllText(outputJson,
                    JsonSerializer.Serialize(catalog, options), new UTF8Encoding(false));
                string markdown = Path.ChangeExtension(outputJson, ".md");
                File.WriteAllText(markdown, BuildMarkdown(catalog), new UTF8Encoding(false));
                if (!string.IsNullOrWhiteSpace(outputCs))
                {
                    parent = Path.GetDirectoryName(outputCs);
                    if (!string.IsNullOrEmpty(parent)) Directory.CreateDirectory(parent);
                    File.WriteAllText(outputCs, BuildCSharp(catalog), new UTF8Encoding(false));
                }
                Console.WriteLine(
                    $"Seat catalog: {catalog.ResolvedCount}/{catalog.ModelCount} models resolved "
                    + $"from {catalog.ArchiveCount} archives.");
                Console.WriteLine($"JSON: {outputJson}");
                Console.WriteLine($"Markdown: {markdown}");
                if (outputCs != null) Console.WriteLine($"C#: {outputCs}");
                // An unresolved layout is useful audit output rather than a
                // fatal extraction error.  Keep the command successful so a
                // public catalog can still be reviewed while warnings and the
                // unresolved count make incomplete metadata explicit.
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: {ex.Message}");
                Console.Error.WriteLine(ex.StackTrace);
                return 99;
            }
        }

        private static CatalogRecord BuildCatalog(string gtaPath, bool enhanced)
        {
            var catalog = new CatalogRecord
            {
                GeneratedUtc = DateTime.UtcNow.ToString("O"),
                Edition = enhanced ? "Enhanced" : "Legacy",
            };
            var documents = new List<SourceDocument>();
            var archives = DiscoverArchives(gtaPath).ToArray();
            int scannedArchives = archives.Length;
            for (int i = 0; i < archives.Length; i++)
            {
                Console.WriteLine(
                    $"[{i + 1}/{archives.Length}] Scanning {Path.GetFileName(Path.GetDirectoryName(archives[i]))}...");
                ScanArchive(archives[i], gtaPath, i, documents, catalog.Warnings);
            }
            scannedArchives += ScanConsolidatedDlcArchives(
                gtaPath, archives.Length, documents, catalog.Warnings);
            catalog.ArchiveCount = scannedArchives;
            catalog.SourceDocumentCount = documents.Count;

            var vehicleDefinitions = ReadVehicles(documents).ToArray();
            var layouts = ReadLayouts(documents).ToArray();
            var entries = ReadEntries(documents).ToArray();
            var activeVehicles = vehicleDefinitions
                .GroupBy(item => item.Model, StringComparer.OrdinalIgnoreCase)
                .Select(group => group.OrderByDescending(item => item.Source.Priority).First())
                .OrderBy(item => item.Model, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            foreach (VehicleDefinition vehicle in activeVehicles)
            {
                LayoutDefinition layout = ResolveLayout(vehicle, layouts);
                VehicleRecord record = BuildVehicleRecord(vehicle, layout, entries);
                catalog.Vehicles.Add(record);
            }
            catalog.ModelCount = catalog.Vehicles.Count;
            catalog.ResolvedCount = catalog.Vehicles.Count(item => item.Status == "resolved");
            catalog.UnresolvedCount = catalog.ModelCount - catalog.ResolvedCount;
            return catalog;
        }

        private static IEnumerable<string> DiscoverArchives(string gtaPath)
        {
            string common = Path.Combine(gtaPath, "common.rpf");
            if (File.Exists(common)) yield return common;
            string update = Path.Combine(gtaPath, "update", "update.rpf");
            if (File.Exists(update)) yield return update;
            string packs = Path.Combine(gtaPath, "update", "x64", "dlcpacks");
            if (!Directory.Exists(packs)) yield break;
            foreach (string rpf in Directory.EnumerateFiles(
                packs, "dlc.rpf", SearchOption.AllDirectories)
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
                yield return rpf;
        }

        private static void ScanArchive(
            string archivePath,
            string gtaPath,
            int archiveOrder,
            List<SourceDocument> documents,
            List<string> warnings,
            string sourceArchive = null)
        {
            try
            {
                var rpf = new RpfFile(archivePath, archivePath);
                rpf.ScanStructure(null, warning => warnings.Add(
                    $"{archivePath}: {warning}"));
                foreach (RpfFileEntry entry in rpf.AllEntries?.OfType<RpfFileEntry>()
                    ?? Enumerable.Empty<RpfFileEntry>())
                {
                    string lower = entry.Name?.ToLowerInvariant() ?? "";
                    if (lower != "vehicles.meta"
                        && !(lower.StartsWith("vehiclelayouts")
                            && lower.EndsWith(".meta")))
                        continue;
                    byte[] data = entry.File.ExtractFile(entry);
                    if (data == null || data.Length == 0) continue;
                    string text = Encoding.UTF8.GetString(data).TrimStart('\uFEFF');
                    XDocument xml;
                    try { xml = XDocument.Parse(text, LoadOptions.None); }
                    catch (Exception ex)
                    {
                        warnings.Add($"{entry.Path}: XML parse failed: {ex.Message}");
                        continue;
                    }
                    string normalized = entry.Path.Replace('\\', '/');
                    string pack = GetPackName(archivePath, normalized);
                    bool patch = normalized.IndexOf("/dlc_patch/",
                        StringComparison.OrdinalIgnoreCase) >= 0;
                    int priority = patch ? 100000 + archiveOrder
                        : archivePath.EndsWith("update.rpf",
                            StringComparison.OrdinalIgnoreCase)
                        ? archiveOrder : 1000 + archiveOrder;
                    if (archivePath.EndsWith("common.rpf",
                        StringComparison.OrdinalIgnoreCase))
                        priority = archiveOrder;
                    else if (!patch && archivePath.EndsWith("update.rpf",
                        StringComparison.OrdinalIgnoreCase))
                        priority = 50000 + archiveOrder;
                    documents.Add(new SourceDocument
                    {
                        Archive = sourceArchive ?? RelativeTo(gtaPath, archivePath),
                        Entry = normalized.Substring(Math.Min(
                            normalized.Length, archivePath.Replace('\\', '/').Length))
                            .TrimStart('/'),
                        Pack = pack,
                        Priority = priority,
                        Xml = xml,
                    });
                }
            }
            catch (Exception ex)
            {
                warnings.Add($"{archivePath}: archive scan failed: {ex.Message}");
            }
        }

        // Enhanced and current Legacy installations consolidate the earliest
        // DLC packs inside root x64*.rpf archives (not update/x64/dlcpacks).
        // Scan only embedded dlc.rpf entries and delete each temporary copy as
        // soon as its metadata has been read; this keeps the operation
        // read-only with respect to the game and bounds temporary disk use.
        private static int ScanConsolidatedDlcArchives(
            string gtaPath,
            int archiveOrder,
            List<SourceDocument> documents,
            List<string> warnings)
        {
            int scanned = 0;
            foreach (string outerPath in Directory.EnumerateFiles(
                gtaPath, "x64*.rpf", SearchOption.TopDirectoryOnly)
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
            {
                try
                {
                    var outer = new RpfFile(outerPath, outerPath);
                    outer.ScanStructure(null, warning => warnings.Add(
                        $"{outerPath}: {warning}"));
                    RpfFileEntry[] embedded = outer.AllEntries?
                        .OfType<RpfFileEntry>()
                        .Where(entry => string.Equals(entry.Name, "dlc.rpf",
                            StringComparison.OrdinalIgnoreCase)
                            && entry.Path.IndexOf("dlcpacks",
                                StringComparison.OrdinalIgnoreCase) >= 0)
                        .ToArray() ?? Array.Empty<RpfFileEntry>();
                    foreach (RpfFileEntry entry in embedded)
                    {
                        byte[] data = entry.File.ExtractFile(entry);
                        if (data == null || data.Length == 0)
                        {
                            warnings.Add($"{entry.Path}: embedded DLC was empty");
                            continue;
                        }
                        string pack = Directory.GetParent(entry.Path)?.Name
                            ?? $"embedded_{scanned}";
                        string tempRoot = Path.Combine(Path.GetTempPath(),
                            "allin1-seat-audit", Guid.NewGuid().ToString("N"), pack);
                        Directory.CreateDirectory(tempRoot);
                        string tempRpf = Path.Combine(tempRoot, "dlc.rpf");
                        File.WriteAllBytes(tempRpf, data);
                        try
                        {
                            Console.WriteLine(
                                $"[embedded] Scanning {pack} from {Path.GetFileName(outerPath)}...");
                            ScanArchive(tempRpf, gtaPath, archiveOrder + scanned,
                                documents, warnings,
                                $"{Path.GetFileName(outerPath)}/dlcpacks/{pack}/dlc.rpf");
                            scanned++;
                        }
                        finally
                        {
                            if (File.Exists(tempRpf)) File.Delete(tempRpf);
                            Directory.Delete(tempRoot);
                            string guidRoot = Directory.GetParent(tempRoot)?.FullName;
                            if (!string.IsNullOrEmpty(guidRoot)
                                && Directory.Exists(guidRoot))
                                Directory.Delete(guidRoot);
                        }
                    }
                }
                catch (Exception ex)
                {
                    warnings.Add($"{outerPath}: consolidated scan failed: {ex.Message}");
                }
            }
            return scanned;
        }

        private static IEnumerable<VehicleDefinition> ReadVehicles(
            IEnumerable<SourceDocument> documents)
        {
            foreach (SourceDocument source in documents.Where(
                item => item.Entry.EndsWith("vehicles.meta",
                    StringComparison.OrdinalIgnoreCase)))
            {
                foreach (XElement item in source.Xml.Descendants("InitDatas")
                    .Elements("Item"))
                {
                    string model = Value(item, "modelName");
                    if (string.IsNullOrWhiteSpace(model)) continue;
                    yield return new VehicleDefinition
                    {
                        Model = model.ToLowerInvariant(),
                        Layout = Value(item, "layout"),
                        GameName = Value(item, "gameName"),
                        MakeName = Value(item, "vehicleMakeName"),
                        Flags = Value(item, "flags"),
                        Source = source,
                    };
                }
            }
        }

        private static IEnumerable<LayoutDefinition> ReadLayouts(
            IEnumerable<SourceDocument> documents)
        {
            foreach (SourceDocument source in documents.Where(
                item => Path.GetFileName(item.Entry).StartsWith(
                    "vehiclelayouts", StringComparison.OrdinalIgnoreCase)))
            {
                foreach (XElement item in source.Xml.Descendants("VehicleLayoutInfos")
                    .Elements("Item"))
                {
                    string name = Value(item, "Name");
                    if (!string.IsNullOrWhiteSpace(name))
                        yield return new LayoutDefinition
                        { Name = name, Node = item, Source = source };
                }
            }
        }

        private static IEnumerable<EntryDefinition> ReadEntries(
            IEnumerable<SourceDocument> documents)
        {
            foreach (SourceDocument source in documents.Where(
                item => Path.GetFileName(item.Entry).StartsWith(
                    "vehiclelayouts", StringComparison.OrdinalIgnoreCase)))
            {
                foreach (XElement item in source.Xml
                    .Descendants("VehicleEntryPointInfos").Elements("Item"))
                {
                    string name = Value(item, "Name");
                    if (!string.IsNullOrWhiteSpace(name))
                        yield return new EntryDefinition
                        { Name = name, Node = item, Source = source };
                }
            }
        }

        private static LayoutDefinition ResolveLayout(
            VehicleDefinition vehicle, IEnumerable<LayoutDefinition> layouts)
        {
            LayoutDefinition[] candidates = layouts.Where(item =>
                string.Equals(item.Name, vehicle.Layout,
                    StringComparison.OrdinalIgnoreCase)).ToArray();
            return candidates
                .OrderByDescending(item => string.Equals(
                    item.Source.Pack, vehicle.Source.Pack,
                    StringComparison.OrdinalIgnoreCase))
                .ThenByDescending(item => item.Source.Priority)
                .FirstOrDefault();
        }

        private static VehicleRecord BuildVehicleRecord(
            VehicleDefinition vehicle,
            LayoutDefinition layout,
            IEnumerable<EntryDefinition> entries)
        {
            var record = new VehicleRecord
            {
                Model = vehicle.Model,
                ModelHash = unchecked((int)JenkHash.GenHash(vehicle.Model)),
                Layout = vehicle.Layout,
                GameName = vehicle.GameName,
                MakeName = vehicle.MakeName,
                ModelSource = SourceName(vehicle.Source),
                LayoutSource = layout == null ? null : SourceName(layout.Source),
                Pack = vehicle.Source.Pack,
                Status = layout == null ? "layout_unresolved" : "resolved",
            };
            if (layout == null) return record;

            XElement seats = layout.Node.Element("Seats");
            XElement[] seatNodes = seats?.Elements("Item").ToArray()
                ?? Array.Empty<XElement>();
            for (int offset = 0; offset < seatNodes.Length; offset++)
            {
                int index = offset - 1;
                string rockstarSeat = seatNodes[offset].Element("SeatInfo")?
                    .Attribute("ref")?.Value ?? "";
                string label = ResolveLabel(vehicle.Model, index, rockstarSeat);
                record.Seats.Add(new SeatRecord
                {
                    Index = index,
                    Label = label,
                    RockstarSeat = rockstarSeat,
                    // Semantic labels are authoritative.  Some layouts reuse
                    // a model-specific name containing TURRET for ordinary
                    // rear seats (notably limo2), so the raw token alone is
                    // not sufficient evidence of a weapon station.
                    Turret = label.IndexOf("Turret",
                        StringComparison.OrdinalIgnoreCase) >= 0,
                });
            }
            record.SeatCount = record.Seats.Count;
            record.PassengerCapacity = Math.Max(0, record.SeatCount - 1);

            XElement entryPoints = layout.Node.Element("EntryPoints");
            XElement[] entryNodes = entryPoints?.Elements("Item").ToArray()
                ?? Array.Empty<XElement>();
            record.EntryPointCount = entryNodes.Length;
            var bones = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (XElement node in entryNodes)
            {
                string reference = node.Element("EntryPointInfo")?
                    .Attribute("ref")?.Value;
                EntryDefinition definition = entries.Where(item =>
                        string.Equals(item.Name, reference,
                            StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(item => string.Equals(
                        item.Source.Pack, layout.Source.Pack,
                        StringComparison.OrdinalIgnoreCase))
                    .ThenByDescending(item => item.Source.Priority)
                    .FirstOrDefault();
                if (definition == null) continue;
                AddBone(bones, Value(definition.Node, "DoorBoneName"));
                AddBone(bones, Value(definition.Node, "SecondDoorBoneName"));
            }
            record.DoorBones = bones.OrderBy(item => item,
                StringComparer.OrdinalIgnoreCase).ToList();
            record.PassengerDoorCount = record.DoorBones.Count(IsPassengerDoor);
            record.AccessHatchCount = record.DoorBones.Count - record.PassengerDoorCount;
            return record;
        }

        private static string ResolveLabel(string model, int index, string seat)
        {
            Dictionary<int, string> labels;
            string label;
            if (LabelOverrides.TryGetValue(model, out labels)
                && labels.TryGetValue(index, out label))
                return label;
            if (index == -1) return "Driver";
            if (seat.IndexOf("TURRET", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                if (seat.IndexOf("TOP", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "Top Turret";
                if (seat.IndexOf("REAR", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "Rear Turret";
                if (seat.IndexOf("FRONT", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "Front Turret";
                if (seat.IndexOf("LEFT", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "Left Turret";
                if (seat.IndexOf("RIGHT", StringComparison.OrdinalIgnoreCase) >= 0)
                    return "Right Turret";
                return "Turret";
            }
            if (index == 0) return "Passenger";
            bool left = seat.IndexOf("LEFT", StringComparison.OrdinalIgnoreCase) >= 0;
            bool right = seat.IndexOf("RIGHT", StringComparison.OrdinalIgnoreCase) >= 0;
            bool rear = seat.IndexOf("REAR", StringComparison.OrdinalIgnoreCase) >= 0;
            bool side = seat.IndexOf("SIDE", StringComparison.OrdinalIgnoreCase) >= 0;
            if (rear && left) return "Left Rear";
            if (rear && right) return "Right Rear";
            if (side && left) return "Left Side";
            if (side && right) return "Right Side";
            if (left) return $"Left Seat {index}";
            if (right) return $"Right Seat {index}";
            return $"Passenger {index + 1}";
        }

        private static string BuildMarkdown(CatalogRecord catalog)
        {
            var sb = new StringBuilder();
            sb.AppendLine("# GTA V Vehicle Seat Metadata Audit");
            sb.AppendLine();
            sb.AppendLine($"Generated: `{catalog.GeneratedUtc}`");
            sb.AppendLine($"Edition: `{catalog.Edition}`");
            sb.AppendLine($"Models: **{catalog.ResolvedCount}/{catalog.ModelCount} resolved**");
            sb.AppendLine();
            sb.AppendLine("Door count means unique occupant-access door bones; turret hatches are listed separately.");
            sb.AppendLine();
            sb.AppendLine("| Model | Layout | Seats | Doors | Hatches | Seat labels | Source |");
            sb.AppendLine("|---|---|---:|---:|---:|---|---|");
            foreach (VehicleRecord vehicle in catalog.Vehicles)
            {
                string labels = string.Join("; ", vehicle.Seats.Select(
                    seat => $"{seat.Index}: {seat.Label}"));
                sb.AppendLine($"| `{vehicle.Model}` | `{vehicle.Layout}` | "
                    + $"{vehicle.SeatCount} | {vehicle.PassengerDoorCount} | "
                    + $"{vehicle.AccessHatchCount} | {EscapeTable(labels)} | "
                    + $"`{EscapeTable(vehicle.LayoutSource ?? vehicle.Status)}` |");
            }
            return sb.ToString();
        }

        private static string BuildCSharp(CatalogRecord catalog)
        {
            var sb = new StringBuilder();
            sb.AppendLine("// Auto-generated by RpfPatcher audit-seats. Do not hand edit.");
            sb.AppendLine("using System.Collections.Generic;");
            sb.AppendLine();
            sb.AppendLine("namespace ALLIN1");
            sb.AppendLine("{");
            sb.AppendLine("    internal sealed class VehicleSeatLayoutRecord");
            sb.AppendLine("    {");
            sb.AppendLine("        internal string Model;");
            sb.AppendLine("        internal string Layout;");
            sb.AppendLine("        internal int SeatCount;");
            sb.AppendLine("        internal int DoorCount;");
            sb.AppendLine("        internal int HatchCount;");
            sb.AppendLine("        internal Dictionary<int, string> Labels;");
            sb.AppendLine("        internal HashSet<int> Turrets;");
            sb.AppendLine("    }");
            sb.AppendLine();
            sb.AppendLine("    internal static class VehicleSeatLayoutCatalog");
            sb.AppendLine("    {");
            sb.AppendLine("        internal static readonly Dictionary<int, VehicleSeatLayoutRecord> All =");
            sb.AppendLine("            new Dictionary<int, VehicleSeatLayoutRecord>");
            sb.AppendLine("            {");
            foreach (VehicleRecord vehicle in catalog.Vehicles.Where(
                item => item.Status == "resolved"))
            {
                string labels = string.Join(", ", vehicle.Seats.Select(seat =>
                    $"{{ {seat.Index}, \"{EscapeCs(seat.Label)}\" }}"));
                string turrets = string.Join(", ", vehicle.Seats
                    .Where(seat => seat.Turret).Select(seat => seat.Index));
                sb.AppendLine($"                {{ {vehicle.ModelHash}, new VehicleSeatLayoutRecord");
                sb.AppendLine("                    {");
                sb.AppendLine($"                        Model = \"{EscapeCs(vehicle.Model)}\",");
                sb.AppendLine($"                        Layout = \"{EscapeCs(vehicle.Layout)}\",");
                sb.AppendLine($"                        SeatCount = {vehicle.SeatCount},");
                sb.AppendLine($"                        DoorCount = {vehicle.PassengerDoorCount},");
                sb.AppendLine($"                        HatchCount = {vehicle.AccessHatchCount},");
                sb.AppendLine($"                        Labels = new Dictionary<int, string> {{ {labels} }},");
                sb.AppendLine($"                        Turrets = new HashSet<int> {{ {turrets} }},");
                sb.AppendLine("                    } },");
            }
            sb.AppendLine("            };");
            sb.AppendLine();
            sb.AppendLine("        internal static string GetLabel(int modelHash, int seatIndex)");
            sb.AppendLine("        {");
            sb.AppendLine("            VehicleSeatLayoutRecord record = Get(modelHash);");
            sb.AppendLine("            string label;");
            sb.AppendLine("            return record != null");
            sb.AppendLine("                && record.Labels.TryGetValue(seatIndex, out label)");
            sb.AppendLine("                ? label : null;");
            sb.AppendLine("        }");
            sb.AppendLine();
            sb.AppendLine("        internal static VehicleSeatLayoutRecord Get(int modelHash)");
            sb.AppendLine("        {");
            sb.AppendLine("            VehicleSeatLayoutRecord record;");
            sb.AppendLine("            return All.TryGetValue(modelHash, out record) ? record : null;");
            sb.AppendLine("        }");
            sb.AppendLine("    }");
            sb.AppendLine("}");
            return sb.ToString();
        }

        private static Dictionary<int, string> Labels(
            params (int Index, string Label)[] values)
        {
            return values.ToDictionary(item => item.Index, item => item.Label);
        }

        private static void AddBone(HashSet<string> bones, string value)
        {
            if (!string.IsNullOrWhiteSpace(value)
                && !string.Equals(value, "NULL", StringComparison.OrdinalIgnoreCase)
                && !string.Equals(value, "none", StringComparison.OrdinalIgnoreCase))
                bones.Add(value);
        }

        private static bool IsPassengerDoor(string value)
        {
            return value.StartsWith("door_dside", StringComparison.OrdinalIgnoreCase)
                || value.StartsWith("door_pside", StringComparison.OrdinalIgnoreCase);
        }

        private static string Value(XElement node, string name)
        {
            return node?.Element(name)?.Value?.Trim() ?? "";
        }

        private static string SourceName(SourceDocument source)
        {
            return source == null ? null : $"{source.Archive}/{source.Entry}";
        }

        private static string GetPackName(string archive, string entry)
        {
            string marker = "/dlc_patch/";
            int index = entry.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (index >= 0)
            {
                string tail = entry.Substring(index + marker.Length);
                return tail.Split('/')[0];
            }
            DirectoryInfo parent = Directory.GetParent(archive);
            return string.Equals(Path.GetFileName(archive), "dlc.rpf",
                StringComparison.OrdinalIgnoreCase)
                ? parent?.Name ?? "unknown" : "base";
        }

        private static string RelativeTo(string root, string path)
        {
            return Path.GetRelativePath(root, path).Replace('\\', '/');
        }

        private static string EscapeTable(string value)
        {
            return (value ?? "").Replace("|", "\\|").Replace("\r", " ")
                .Replace("\n", " ");
        }

        private static string EscapeCs(string value)
        {
            return (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        }
    }
}
