// No GTA files, keys, test framework, or network services are required.
using System;
using System.IO;
using System.Reflection;
using CodeWalker.GameFiles;

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
}
