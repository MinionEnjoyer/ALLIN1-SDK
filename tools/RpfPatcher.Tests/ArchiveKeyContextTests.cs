using System;
using System.IO;
using System.Reflection;

static class ArchiveKeyContextTests
{
    public static int Run()
    {
        var method = Type.GetType("RpfPatcher.Program, RpfPatcher", true)
            .GetMethod("HasArchiveKeyContext", BindingFlags.Static | BindingFlags.NonPublic);
        var root = Path.Combine(Path.GetTempPath(), "allin1-key-context-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        int checks = 0;
        void Check(string path, bool gen9, bool expected)
        {
            if ((bool)method.Invoke(null, new object[] { path, gen9 }) != expected)
                throw new Exception("Wrong archive game-key context classification.");
            checks++;
        }
        try
        {
            Check(null, true, false);
            Check("", false, false);
            Check(root, true, false);
            Check(root, false, false);
            // Presence classification only: never read these fixture files as keys.
            File.WriteAllBytes(Path.Combine(root, "GTA5.exe"), new byte[] { 1 });
            Check(root, false, true);
            Check(root, true, false);
            File.WriteAllBytes(Path.Combine(root, "GTA5_Enhanced.exe"), new byte[] { 1 });
            Check(root, true, true);
            Check(root, false, true);
            return checks;
        }
        finally { Directory.Delete(root, true); }
    }
}
