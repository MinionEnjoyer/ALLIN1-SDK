using System;
using System.IO;
using System.Linq;
using System.Xml;
using CodeWalker.GameFiles;
using RpfPatcher;

static class ClipSetPsoTests
{
    static XmlDocument Document(string fields)
    {
        var doc = new XmlDocument { XmlResolver = null };
        doc.LoadXml("<fwClipSetManager>" + fields + "</fwClipSetManager>");
        return doc;
    }
    public static int Run()
    {
        const string maps = "<clipDictionaryMetadatas/><memoryGroupMetadatas/><memorySituations/><clipVariationSets/>";
        var doc = Document("<clipSets><Item type='fwClipSet' key='test_clipset'><fallbackId/><clipDictionaryName>test_dict</clipDictionaryName><clipItems/><moveNetworkFlags/></Item></clipSets>" + maps);
        var source = XmlPso.GetPso(doc);
        // Reproduce the reduced-stock-document failure: an inherited block whose
        // type is absent from the active schema must never enter the new file.
        source.DataMapSection.Entries = source.DataMapSection.Entries.Concat(new[] {
            new PsoDataMappingEntry { NameHash = (MetaName)0xDEADBEEF, Offset = 16, Length = 0 }
        }).ToArray();
        var bytes = ClipSetPsoCompiler.Build(doc, source);
        var result = new PsoFile(); result.Load(bytes);
        ClipSetPsoCompiler.Validate(result);
        if (!PsoXml.GetXml(result).Contains("fwClipSetManager")) throw new Exception("Clip-set readback failed");
        if (result.DataMapSection.Entries.Any(b => b.Length == 0)) throw new Exception("Empty inherited block");
        if (result.STRESection != null || result.PSIGSection != null || result.CHKSSection != null) throw new Exception("Inherited stock sections");
        if (result.DataMapSection.Entries.Any(b => (uint)b.NameHash == 0xDEADBEEF)) throw new Exception("Inherited orphan descriptor");
        foreach (var bad in new[] { Document("<clipSets/>"), Document("<clipSets/>" + maps + "<clipSets/>") })
        {
            bool rejected = false;
            try { ClipSetPsoCompiler.Build(bad, source); } catch (InvalidDataException) { rejected = true; }
            if (!rejected) throw new Exception("Incomplete or duplicate root maps accepted");
        }
        var first = result.DataMapSection.Entries[0];
        var originalName = first.NameHash;
        first.NameHash = (MetaName)0xDEADBEEF;
        bool missingRejected = false;
        try { ClipSetPsoCompiler.Validate(result); } catch (InvalidDataException) { missingRejected = true; }
        if (!missingRejected) throw new Exception("Missing schema accepted");
        first.NameHash = originalName;
        first.Length = 0;
        try { ClipSetPsoCompiler.Validate(result); } catch (InvalidDataException) { return 8; }
        throw new Exception("Empty block accepted");
    }
}
