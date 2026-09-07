using System;
using System.IO;
using System.Linq;
using System.Xml;
using CodeWalker.GameFiles;

namespace RpfPatcher
{
    /// <summary>Build clip-set documents from schema, never from stock data pointers.</summary>
    public static class ClipSetPsoCompiler
    {
        static readonly string[] Fields = { "clipSets", "clipDictionaryMetadatas",
            "memoryGroupMetadatas", "memorySituations", "clipVariationSets" };

        public static byte[] Build(XmlDocument document, PsoFile source)
        {
            if (document.DocumentElement?.Name != "fwClipSetManager" || source?.SchemaSection?.Entries == null)
                throw new InvalidDataException("Clip-set compilation requires an explicit source PSO schema.");
            var children = document.DocumentElement.ChildNodes.OfType<XmlElement>().ToArray();
            if (children.Length != Fields.Length || Fields.Any(name => children.Count(n => n.Name == name) != 1))
                throw new InvalidDataException("Clip-set XML must explicitly include all five root maps; use empty elements for intentionally omitted maps.");

            // UseSchemaFrom normally seeds raw source structures and preallocates all
            // source blocks. That is unsafe when a stock document is reduced to a DLC
            // subset: omitted fields retain stale pointers, and unused blocks lose
            // their schema descriptors. Only the edition-specific schema is reusable.
            var schemaOnly = new PsoFile { SchemaSection = source.SchemaSection };
            var result = XmlPso.GetPso(document, schemaOnly);
            var schemas = result.SchemaSection.Entries.ToDictionary(e => e.IndexInfo.NameHash);
            foreach (var entry in source.SchemaSection.Entries)
                if (!schemas.ContainsKey(entry.IndexInfo.NameHash)) schemas.Add(entry.IndexInfo.NameHash, entry);
            result.SchemaSection.Entries = schemas.Values.ToArray();
            result.SchemaSection.EntriesIdx = schemas.Values.Select(e =>
                new PsoElementIndexInfo { NameHash = e.IndexInfo.NameHash }).ToArray();
            Validate(result);
            // No source STRE/PSIG/CHKS: those describe the original document, not
            // this newly authored DLC. The serializer emits the new schema/data.
            return result.Save();
        }

        public static void Validate(PsoFile result)
        {
            var blocks = result.DataMapSection?.Entries;
            var schemas = result.SchemaSection?.Entries;
            if (blocks == null || schemas == null || result.DataSection?.Data == null)
                throw new InvalidDataException("Incomplete compiled clip-set PSO.");
            if (result.DataMapSection.RootId < 1 || result.DataMapSection.RootId > blocks.Length)
                throw new InvalidDataException("Clip-set root block is invalid.");
            foreach (var block in blocks)
            {
                if (block.Length <= 0 || block.Offset < 16 || (long)block.Offset + block.Length > result.DataSection.Data.Length)
                    throw new InvalidDataException("Clip-set contains an empty or out-of-range data block.");
                // 1 is raw scalar/string storage; 0x100 is the generic map node.
                if ((uint)block.NameHash > 0x100 && !schemas.Any(e => e.IndexInfo.NameHash == block.NameHash))
                    throw new InvalidDataException("Clip-set data block has no schema descriptor.");
            }
        }
    }
}
