using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Xml;
using System.Xml.Linq;
using CodeWalker.GameFiles;

namespace RpfPatcher
{
    /// <summary>Read-only graph from the pinned decoder's typed REL references.</summary>
    public static class RelRelationships
    {
        const int MaxXml = 16 * 1024 * 1024;
        const int MaxNodes = 1000;
        const int MaxEdges = 1800;

        static string Canonical(RelFile file)
        {
            var document = XDocument.Parse(RelXml.GetXml(file));
            // Binary layout offsets and top-level record ordering are rebuilt by Save.
            document.Descendants().Attributes("ntOffset").Remove();
            var items = document.Root.Element("Items");
            if (items != null)
            {
                var ordered = items.Elements("Item").Select(item => item.ToString(SaveOptions.DisableFormatting)).OrderBy(value => value, StringComparer.Ordinal).ToArray();
                items.RemoveNodes();
                foreach (var item in ordered) items.Add(XElement.Parse(item));
            }
            return document.ToString(SaveOptions.DisableFormatting);
        }

        public static int Run(string[] args)
        {
            try
            {
                if (args.Length != 2) throw new ArgumentException("Usage: RpfPatcher.exe rel-relationships <input_xml>");
                var info = new FileInfo(args[1]);
                if (!info.Exists || info.Length > MaxXml) throw new InvalidDataException("REL XML is missing or exceeds 16 MiB");
                Console.WriteLine(JsonSerializer.Serialize(Analyze(System.IO.File.ReadAllText(info.FullName))));
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("ERROR: REL relationship analysis failed: " + error.Message);
                return 5;
            }
        }

        public static object Analyze(string xml)
        {
            if (string.IsNullOrWhiteSpace(xml) || xml.Length > MaxXml) throw new InvalidDataException("REL XML exceeds analysis bounds");
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(new StringReader(xml), new XmlReaderSettings {
                DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = MaxXml })) doc.Load(reader);
            if (!Regex.IsMatch(doc.DocumentElement?.Name ?? "", "^Dat(4|10|15|16|22|54|149|150|151)$"))
                throw new InvalidDataException("Unsupported REL XML family");
            var xmlItems = doc.DocumentElement.SelectNodes("Items/Item");
            if (xmlItems.Count > 50000) throw new InvalidDataException("REL exceeds 50000 records");

            // Several ReadXml methods do not populate GetSoundHashes' backing arrays.
            // Reload a memory-only serialization; never mistake those null arrays for no links.
            var authored = XmlRel.GetRel(doc);
            if (authored.RelDatas?.Length != xmlItems.Count) throw new InvalidDataException("Decoder omitted REL XML records");
            var before = Canonical(authored);
            var bytes = authored.Save();
            if (bytes == null || bytes.Length > 128 * 1024 * 1024) throw new InvalidDataException("Normalized REL is absent or exceeds 128 MiB");
            var parsed = new RelFile();
            parsed.Load(bytes, new RpfBinaryFileEntry { Name = "relationships.rel", NameLower = "relationships.rel" });
            var records = parsed.RelDatasSorted ?? parsed.RelDatas ?? Array.Empty<RelData>();
            if (records.Length != xmlItems.Count) throw new InvalidDataException("REL record inventory changed during normalization");
            if (Canonical(parsed) != before) throw new InvalidDataException("Typed REL fields changed during normalization; relationship results would be incomplete");
            var family = (uint)parsed.RelType;
            var lookup = records.Select((record, index) => (record, index)).GroupBy(pair => (uint)pair.record.NameHash)
                .ToDictionary(group => group.Key, group => group.Select(pair => pair.index).ToArray());
            var nodes = new List<object>();
            var edges = new List<object>();
            var ids = new HashSet<string>();
            int nodeCount = 0, edgeCount = 0;
            var warnings = new List<string> {
                "Typed decoder references only; unknown fields and unsupported variants are not inferred from XML names.",
                "REL graph analysis normalizes XML in memory. It does not verify a rebuild or perform audio playback."
            };
            void Node(string id, string label, string kind, object fields, string search = null)
            {
                if (!ids.Add(id)) return;
                nodeCount++;
                if (nodes.Count >= MaxNodes) return;
                var value = new Dictionary<string, object> { ["id"] = id, ["label"] = label.Length > 300 ? label.Substring(0, 300) : label,
                    ["kind"] = kind, ["fields"] = fields };
                if (!string.IsNullOrWhiteSpace(search)) value["search"] = search.Length > 256 ? search.Substring(0, 256) : search;
                nodes.Add(value);
            }
            void Edge(string source, string target, string label, string resolution)
            {
                edgeCount++;
                if (edges.Count < MaxEdges) edges.Add(new { source, target, label, resolution });
            }
            for (int index = 0; index < records.Length; index++)
            {
                var record = records[index];
                Node("record:" + index, RelXml.HashString(record.NameHash), record.GetType().Name,
                    new { normalized_index = index, family, hash = $"0x{(uint)record.NameHash:X8}", type_id = record.TypeID });
            }
            for (int index = 0; index < records.Length; index++)
            {
                var record = records[index];
                var references = new (string kind, bool sameFamily, MetaHash[] values)[] {
                    ("speech", family == 4 && !parsed.IsAudioConfig, record.GetSpeechHashes()),
                    ("synth", family == 10, record.GetSynthHashes()),
                    ("mixer", family == 15, record.GetMixerHashes()),
                    ("curve", family == 16, record.GetCurveHashes()),
                    ("category", family == 22, record.GetCategoryHashes()),
                    ("sound", family == 54, record.GetSoundHashes()),
                    ("game", family == 149 || family == 150 || family == 151, record.GetGameHashes()),
                };
                foreach (var reference in references)
                {
                    foreach (var hash in reference.values ?? Array.Empty<MetaHash>())
                    {
                        if ((uint)hash == 0) continue;
                        var candidates = reference.sameFamily && lookup.TryGetValue((uint)hash, out var matches) ? matches : Array.Empty<int>();
                        string target, resolution;
                        if (candidates.Length == 1) { target = "record:" + candidates[0]; resolution = "local"; }
                        else
                        {
                            target = $"reference:{reference.kind}:{(uint)hash:X8}";
                            resolution = candidates.Length > 1 ? "ambiguous duplicate hash" : "external: unresolved";
                            Node(target, RelXml.HashString(hash), reference.kind + " reference",
                                new { hash = $"0x{(uint)hash:X8}", candidate_count = candidates.Length });
                        }
                        Edge("record:" + index, target, reference.kind, resolution);
                    }
                }
            }
            // ContainerPaths is a document-level catalog, not a proven per-record AWC binding.
            if (parsed.NameTable?.Length > 0)
            {
                Node("document", $"Dat{family} container catalog", "REL document", new { family });
                for (int index = 0; index < parsed.NameTable.Length; index++)
                {
                    var container = parsed.NameTable[index];
                    var identity = "container:" + index;
                    Node(identity, container, "container path", new { path = container.Length > 2048 ? container.Substring(0, 2048) : container,
                        path_truncated = container.Length > 2048, resolution = "unresolved catalog entry" },
                        container.Length <= 256 ? Path.GetFileName(container.Replace('\\', '/')) : null);
                    Edge("document", identity, "declares container", "external: unresolved");
                }
            }
            var truncated = nodeCount > nodes.Count || edgeCount > edges.Count;
            if (truncated) warnings.Add($"Display limited to {MaxNodes} nodes / {MaxEdges} relationships; totals include omitted rows");
            return new { schema_version = 1, format = ".rel", nodes, edges, node_count = nodeCount, edge_count = edgeCount,
                truncated, warnings, read_only = true,
                scope = $"Dat{family} decoded document only. Links use typed decoder hash references; external hashes are unresolved and record indices refer to the normalized inventory." };
        }
    }
}
