// No GTA files, keys, test framework, or network services are required.
using System;
using System.IO;
using System.Reflection;
using System.Linq;
using System.Collections.Generic;
using System.Text.Json;
using CodeWalker.GameFiles;
using CodeWalker.Utils;

class ExactEntryTests
{
    static int checks;
    static readonly Type Helper = Type.GetType("RpfPatcher.Program, RpfPatcher", true);

    static object Find(string method, RpfFile archive, string path)
    {
        try
        {
            return Helper.GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { archive, path });
        }
        catch (TargetInvocationException error) { throw error.InnerException; }
    }

    static void Same(object expected, object actual, string label)
    {
        if (!ReferenceEquals(expected, actual)) throw new Exception("Wrong identity: " + label);
        checks++;
    }

    static void Reject<T>(Action action) where T : Exception
    {
        try { action(); }
        catch (T) { checks++; return; }
        throw new Exception("Expected rejection: " + typeof(T).Name);
    }

    static RpfDirectoryEntry Folder(RpfDirectoryEntry parent, string name)
    {
        var child = new RpfDirectoryEntry { Name = name, Parent = parent, Path = "unreliable/display/path" };
        parent.Directories.Add(child);
        return child;
    }

    static RpfBinaryFileEntry File(RpfDirectoryEntry parent, string name)
    {
        var child = new RpfBinaryFileEntry { Name = name, Parent = parent, Path = "unreliable/display/path" };
        parent.Files.Add(child);
        return child;
    }

    static void Main()
    {
        CheckTextureMipRoundTrips();
        CheckCollisionQuantization();
        CheckRelRelationships();
        checks += AnimationSampleTests.Run();
        var probe = Path.Combine(Path.GetTempPath(), "allin1-open-header-" + Guid.NewGuid().ToString("N") + ".rpf");
        try
        {
            foreach (uint encryption in new uint[] { 0, 0x4E45504F, 0x0FFFFFF9, 0x0FEFFFFF, 0x12345678 })
            {
                using (var stream = System.IO.File.Create(probe))
                using (var writer = new BinaryWriter(stream))
                {
                    writer.Write(0x52504637u); writer.Write(1u); writer.Write(16u); writer.Write(encryption);
                }
                bool actual = (bool)Helper.GetMethod("IsUnencryptedRpf", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { probe });
                if (actual != (encryption == 0 || encryption == 0x4E45504F)) throw new Exception("Wrong keyless encryption classification");
                checks++;
            }
            System.IO.File.WriteAllBytes(probe, new byte[] { 1, 2, 3 });
            if ((bool)Helper.GetMethod("IsUnencryptedRpf", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { probe })) throw new Exception("Truncated header was accepted");
            checks++;
            using (var stream = System.IO.File.Create(probe))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(0x52504637u); writer.Write(1u); writer.Write(16u); writer.Write(0x4E45504Fu);
            }
            string keyMode = (string)Helper.GetMethod("LoadReadOnlyArchiveKeys", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { Path.GetTempPath(), true, probe });
            if (keyMode != "not-required-unencrypted-root") throw new Exception("OPEN archive unexpectedly required GTA keys");
            checks++;
        }
        finally { System.IO.File.Delete(probe); }
        var archive = new RpfFile("unused.rpf", "unused.rpf", 0) { Root = new RpfDirectoryEntry { Name = "" } };
        var rootFile = File(archive.Root, "global.gxt2");
        var text = Folder(archive.Root, "text");
        var textFile = File(text, "global.gxt2");
        var shadow = Folder(archive.Root, "shadow");
        var shadowText = Folder(shadow, "text");
        var shadowFile = File(shadowText, "global.gxt2");
        var only = Folder(shadow, "only");
        File(only, "global.gxt2");
        File(shadow, "new.gxt2");
        var childEntry = File(archive.Root, "child.rpf");
        var child = new RpfFile("child.rpf", "child.rpf", 0) { Root = new RpfDirectoryEntry { Name = "" }, ParentFileEntry = childEntry };
        archive.Children ??= new System.Collections.Generic.List<RpfFile>();
        archive.Children.Add(child);
        var nestedText = Folder(child.Root, "text");
        var nestedFile = File(nestedText, "global.gxt2");
        Same(nestedFile, Find("FindExactNestedMember", archive, "child.rpf!text/global.gxt2"), "nested exact identity");
        Same(null, Find("FindExactNestedMember", archive, "child.rpf!global.gxt2"), "nested no suffix fallback");
        Same(null, Find("FindExactNestedMember", archive, "missing.rpf!text/global.gxt2"), "missing parent");
        foreach (string path in new[] { "global.gxt2", "child.rpf/global.gxt2", "child.rpf!!global.gxt2",
            "child.rpf!../global.gxt2", "child.rpf!inner.rpf", "child.rpf!CON.gxt2", "child.rpf!/global.gxt2",
            "child.rpf!inner.rpf/global.gxt2", "child.bin!global.gxt2", "child.rpf!global.gxt2." })
            Reject<InvalidDataException>(() => Find("FindExactNestedMember", archive, path));
        var innerEntry = File(child.Root, "inner.rpf");
        var inner = new RpfFile("inner.rpf", "inner.rpf", 0) { Root = new RpfDirectoryEntry { Name = "" }, ParentFileEntry = innerEntry };
        child.Children = new System.Collections.Generic.List<RpfFile> { inner };
        var deep = File(inner.Root, "global.gxt2");
        Same(deep, Find("FindExactNestedMember", archive, "child.rpf!inner.rpf!global.gxt2"), "two layers");
        archive.Children ??= new System.Collections.Generic.List<RpfFile>();
        archive.Children.Add(child);
        Reject<InvalidDataException>(() => Find("FindExactNestedMember", archive, "child.rpf!text/global.gxt2"));
        archive.Children.RemoveAt(archive.Children.Count - 1);

        Same(rootFile, Find("FindExactFileEntry", archive, "global.gxt2"), "root");
        Same(textFile, Find("FindExactFileEntry", archive, "text/global.gxt2"), "folder");
        Same(shadowFile, Find("FindExactFileEntry", archive, "shadow/text/global.gxt2"), "full path");
        Same(textFile, Find("FindExactFileEntry", archive, "TEXT\\GLOBAL.GXT2"), "case and separator");
        Same(null, Find("FindExactFileEntry", archive, "only/global.gxt2"), "missing directory");
        Same(null, Find("FindExactFileEntry", archive, "new.gxt2"), "missing root");
        Same(null, Find("FindExactFileEntry", archive, "child.rpf/global.gxt2"), "no implicit nested traversal");
        Same(null, Find("FindExactFileEntry", archive, "text"), "directory is not file");
        Same(null, Find("FindExactDirectory", archive, "global.gxt2"), "file is not directory");
        Same(text, Find("FindExactDirectory", archive, "text"), "root folder");
        Same(shadowText, Find("FindExactDirectory", archive, "shadow/text"), "deep folder");
        Same(null, Find("FindExactDirectory", archive, "only"), "no directory suffix fallback");
        Same(archive.Root, Find("FindExactDirectory", archive, ""), "root directory");
        Same(textFile, Find("FindExactEntry", archive, "text/global.gxt2"), "batch file identity");
        Same(text, Find("FindExactEntry", archive, "text"), "batch directory identity");
        Same(null, Find("FindExactEntry", archive, ""), "empty is not a member");

        foreach (string method in new[] { "FindExactFileEntry", "FindExactDirectory", "FindExactEntry" })
            foreach (string path in new[] { "/global.gxt2", "text/", "text//global.gxt2", "../global.gxt2",
                "text/../global.gxt2", "./global.gxt2", "C:/global.gxt2", "child.rpf!global.gxt2", "text\t/global.gxt2", new string('x', 2049) })
                Reject<InvalidDataException>(() => Find(method, archive, path));

        archive.Root.Files.Remove(rootFile);
        Same(null, Find("FindExactFileEntry", archive, "global.gxt2"), "removed root cannot resolve to another folder");
        File(text, "GLOBAL.GXT2");
        Reject<InvalidOperationException>(() => Find("FindExactFileEntry", archive, "text/global.gxt2"));
        Folder(archive.Root, "TEXT");
        Reject<InvalidOperationException>(() => Find("FindExactDirectory", archive, "text"));
        Reject<InvalidOperationException>(() => Find("FindExactFileEntry", archive, "text/global.gxt2"));
        Folder(archive.Root, "child.rpf");
        Reject<InvalidOperationException>(() => Find("FindExactFileEntry", archive, "child.rpf"));
        Console.WriteLine($"Exact native member resolution: {checks} checks passed (no game required).");
    }

    static void CheckCollisionQuantization()
    {
        var geometry = new BoundBVH {
            BoxMin = new SharpDX.Vector3(-1, -1, -1), BoxMax = new SharpDX.Vector3(1, 2.5f, 1),
            Vertices = new[] { new SharpDX.Vector3(0, 2, 0), new SharpDX.Vector3(-3, 0, 0) },
            VerticesShrunk = new[] { new SharpDX.Vector3(0, 0, -4) }
        };
        geometry.CalculateQuantum();
        foreach (var vertex in geometry.Vertices.Concat(geometry.VerticesShrunk))
        {
            var restored = new BoundVertex_s(vertex / geometry.Quantum).Vector * geometry.Quantum;
            if ((restored - vertex).Length() > 0.0002f) throw new Exception("Collision vertex clamped outside off-centre quantization range");
            checks++;
        }
        geometry.Vertices = new[] { SharpDX.Vector3.Zero };
        geometry.VerticesShrunk = null;
        geometry.BoxMin = geometry.BoxMax = SharpDX.Vector3.Zero;
        geometry.CalculateQuantum();
        if (!(geometry.Quantum.X > 0 && geometry.Quantum.Y > 0 && geometry.Quantum.Z > 0)) throw new Exception("Degenerate collision bounds have zero quantum");
        checks++;
    }

    static void CheckRelRelationships()
    {
        const string xml = "<Dat54><Version value=\"1\"/><ContainerPaths><Item>audio/demo.awc</Item></ContainerPaths><Items>"
            + "<Item type=\"LoopingSound\"><Name>demo_a</Name><Header><Flags value=\"32768\"/><Category>demo_b</Category></Header><ChildSound>demo_b</ChildSound></Item>"
            + "<Item type=\"LoopingSound\"><Name>demo_b</Name><Header><Flags value=\"0\"/></Header><ChildSound>demo_external</ChildSound></Item>"
            + "</Items></Dat54>";
        using var doc = JsonDocument.Parse(JsonSerializer.Serialize(RpfPatcher.RelRelationships.Analyze(xml)));
        var graph = doc.RootElement;
        var edges = graph.GetProperty("edges").EnumerateArray().ToArray();
        if (!edges.Any(edge => edge.GetProperty("label").GetString() == "sound" && edge.GetProperty("resolution").GetString() == "local"))
            throw new Exception("REL binary reload must populate typed child-sound links");
        checks++;
        if (!edges.Any(edge => edge.GetProperty("label").GetString() == "category" && edge.GetProperty("resolution").GetString() == "external: unresolved"))
            throw new Exception("Matching hashes from different REL families must not be linked locally");
        checks++;
        if (!graph.GetProperty("nodes").EnumerateArray().Any(node => node.GetProperty("kind").GetString() == "container path" && node.GetProperty("search").GetString() == "demo.awc"))
            throw new Exception("REL container catalog must remain searchable without inventing record bindings");
        checks++;
        Reject<System.Xml.XmlException>(() => RpfPatcher.RelRelationships.Analyze("<!DOCTYPE Dat54 [<!ENTITY x 'a'>]><Dat54>&x;</Dat54>"));
        Reject<InvalidDataException>(() => RpfPatcher.RelRelationships.Analyze("<Dat999><Items/></Dat999>"));
    }

    static void CheckTextureMipRoundTrips()
    {
        bool previous = RpfManager.IsGen9;
        try
        {
            foreach (bool gen9 in new[] { false, true })
            foreach (var format in new[] { TextureFormat.D3DFMT_A8R8G8B8, TextureFormat.D3DFMT_DXT1, TextureFormat.D3DFMT_DXT3, TextureFormat.D3DFMT_DXT5 })
            {
                RpfManager.IsGen9 = gen9;
                int length = format == TextureFormat.D3DFMT_A8R8G8B8 ? 684 : format == TextureFormat.D3DFMT_DXT1 ? 104 : 208;
                byte[] pixels = Enumerable.Range(0, length).Select(i => (byte)(i % 251)).ToArray();
                var texture = new Texture { Name = "mip_fixture", NameHash = JenkHash.GenHash("mip_fixture"),
                    Width = 16, Height = 8, Depth = 1, Levels = 5, Format = format,
                    Data = new TextureData { FullData = pixels } };
                texture.Stride = texture.CalculateStride();
                if (texture.CalcDataSize() != length) throw new Exception("Incorrect full mip-chain size: " + format);
                var dictionary = new TextureDictionary();
                dictionary.BuildFromTextureList(new List<Texture> { texture });
                byte[] encoded = new YtdFile { TextureDict = dictionary }.Save();
                var loaded = new YtdFile();
                loaded.Load(encoded);
                var reparsed = loaded.TextureDict.Textures.data_items[0];
                if (!reparsed.Data.FullData.SequenceEqual(pixels)) throw new Exception("Native texture mip payload changed: " + format);
                var dds = DDSIO.GetTexture(DDSIO.GetDDSFile(reparsed));
                if (dds.Levels != 5 || dds.Width != 16 || dds.Height != 8 || !dds.Data.FullData.SequenceEqual(pixels))
                    throw new Exception("DDS mip payload changed: " + format);
                checks++;
            }
        }
        finally { RpfManager.IsGen9 = previous; }
    }
}
