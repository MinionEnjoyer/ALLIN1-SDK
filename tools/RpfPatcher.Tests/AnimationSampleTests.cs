using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using RpfPatcher;

static class AnimationSampleTests
{
    public static int Run()
    {
        int checks = 0;
        var xml = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native_animation.ycd.xml"));
        JsonElement Analyze(string source, string selection = null) => JsonSerializer.SerializeToElement(AnimationSamples.Analyze(source, selection));
        void Check(bool result, string label) { if (!result) throw new Exception("Animation: " + label); checks++; }
        void Reject(string source, string selection = null)
        {
            try { Analyze(source, selection); }
            catch (Exception error) when (error is InvalidDataException || error is System.Xml.XmlException) { checks++; return; }
            throw new Exception("Animation invalid input was accepted");
        }
        var packet = Analyze(xml);
        Check(packet.GetProperty("selected").GetString() == "clip:11111111", "default clip identity");
        Check(packet.GetProperty("duration").GetDouble() == .5, "clip rate controls duration");
        var values = packet.GetProperty("tracks")[0].GetProperty("values").EnumerateArray().Select(v => v.GetDouble()).ToArray();
        Check(Math.Abs(values[0] - .5) < 1e-6 && Math.Abs(values[^4] - 1.5) < 1e-6, "clip interval endpoints");
        packet = Analyze(xml, "animation:22222222");
        values = packet.GetProperty("tracks")[0].GetProperty("values").EnumerateArray().Select(v => v.GetDouble()).ToArray();
        Check(values[0] == 0 && values[^4] == 2, "last frame and sequence boundary");
        Check(values.Where((_, i) => i % 4 == 0).Zip(values.Skip(4).Where((_, i) => i % 4 == 0), (a, b) => b >= a).All(v => v), "continuous interpolated translation");
        Check(packet.GetProperty("tracks")[1].GetProperty("quaternion").GetBoolean(), "typed quaternion track");
        Reject(xml, "animation:DEADBEEF");
        Reject(xml.Replace("FrameCount value=\"3\"", "FrameCount value=\"0\""));
        Reject(xml.Replace("Track value=\"1\"", "Track value=\"256\""));
        Reject(xml.Replace("Duration value=\"2\"", "Duration value=\"NaN\""));
        Reject(xml.Replace("Type value=\"RawFloat\"", "Type value=\"Unknown\""));
        Reject(xml.Replace("w=\"1\"", "w=\"0\""));
        Reject(xml.Replace("0 1 2</Values>", "0 1</Values>"));
        Reject("<!DOCTYPE ClipDictionary [<!ENTITY x 'bad'>]><ClipDictionary>&x;</ClipDictionary>");
        packet = Analyze(xml.Replace("Rate value=\"2\"", "Rate value=\"0\""));
        Check(packet.GetProperty("choices")[0].GetProperty("error").GetString() != null && packet.GetProperty("selected").GetString().StartsWith("animation:"), "invalid clip remains visible; valid raw animation remains accessible");
        return checks;
    }
}
