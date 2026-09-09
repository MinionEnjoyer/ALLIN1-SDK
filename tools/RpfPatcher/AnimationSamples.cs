using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Xml;
using CodeWalker.GameFiles;
using SharpDX;

namespace RpfPatcher
{
    /// <summary>Decoded local YCD channels. Track order is never treated as a skeleton.</summary>
    public static class AnimationSamples
    {
        const int MaxXml = 16 * 1024 * 1024, MaxTracks = 512, MaxSamples = 240;
        record Layer(Animation Animation, float Start, float End, float Rate);
        record Choice(string Key, string Name, string Kind, double Duration, Layer[] Layers, string Error);
        static string Hash(MetaHash hash) => ((uint)hash).ToString("X8", CultureInfo.InvariantCulture);
        static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
        static string Label(byte track) => track switch { 0 => "bone translation", 1 => "bone rotation", 2 => "bone scale", 5 => "root translation", 6 => "root rotation", 17 => "UV track 0", 18 => "UV track 1", _ => "unresolved track " + track };

        public static int Run(string[] args)
        {
            try
            {
                if (args.Length < 2 || args.Length > 3) throw new ArgumentException("Usage: RpfPatcher.exe animation-samples <input_xml> [selection_key|--inventory]");
                var file = new FileInfo(args[1]);
                if (!file.Exists || file.Length > MaxXml) throw new InvalidDataException("YCD XML is missing or exceeds 16 MiB");
                bool inventory = args.Length == 3 && args[2] == "--inventory";
                Console.WriteLine(JsonSerializer.Serialize(Analyze(System.IO.File.ReadAllText(file.FullName), args.Length == 3 && !inventory ? args[2] : null, inventory)));
                return 0;
            }
            catch (Exception error) { Console.Error.WriteLine("ERROR: Animation sampling failed: " + error.Message); return 5; }
        }

        public static object Analyze(string xml, string selection = null, bool inventoryOnly = false)
        {
            if (inventoryOnly && selection != null) throw new ArgumentException("Inventory cannot select a clip");
            if (string.IsNullOrWhiteSpace(xml) || xml.Length > MaxXml) throw new InvalidDataException("YCD XML exceeds analysis limits");
            var doc = new XmlDocument { XmlResolver = null };
            using (var reader = XmlReader.Create(new StringReader(xml), new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = MaxXml })) doc.Load(reader);
            if (doc.DocumentElement?.Name != "ClipDictionary") throw new InvalidDataException("Expected ClipDictionary XML");
            var xmlAnimations = doc.SelectNodes("/ClipDictionary/Animations/Item");
            var xmlClips = doc.SelectNodes("/ClipDictionary/Clips/Item");
            if (xmlAnimations.Count > 1000 || xmlClips.Count > 1000) throw new InvalidDataException("Animation or clip inventory exceeds 1000 records");
            foreach (XmlNode node in doc.SelectNodes("//FrameCount|//SequenceFrameLimit|//BoneId|//Track|//Unk0"))
            {
                var maximum = node.Name == "Track" || node.Name == "Unk0" ? 255 : 65535;
                if (!uint.TryParse(node.Attributes?["value"]?.Value, out var number) || number > maximum
                    || ((node.Name == "FrameCount" || node.Name == "SequenceFrameLimit") && number == 0))
                    throw new InvalidDataException("Invalid or overflowing animation " + node.Name);
            }
            if (doc.SelectNodes("//Channels/Item").Count > 32768) throw new InvalidDataException("Animation channel inventory exceeds 32768 records");
            foreach (XmlNode node in doc.SelectNodes("//Channels/Item"))
                if (!Enum.TryParse<AnimChannelType>(node.SelectSingleNode("Type")?.Attributes?["value"]?.Value, out var channelType) || !Enum.IsDefined(typeof(AnimChannelType), channelType))
                    throw new InvalidDataException("Unsupported animation channel encoding");
            var ycd = XmlYcd.GetYcd(doc);
            if (ycd.AnimMap.Count != xmlAnimations.Count || ycd.ClipMap.Count != xmlClips.Count)
                throw new InvalidDataException("Duplicate hashes or omitted animation records");
            var animations = ycd.AnimMap.Values.Select(entry => entry.Animation).ToArray();
            // Keep a bad/unsupported clip visible without making all other
            // clips disappear. Sampling never skips an invalid channel.
            var animationErrors = new Dictionary<Animation, string>();
            foreach (var animation in animations)
                try { Validate(animation); }
                catch (InvalidDataException error) { animationErrors[animation] = error.Message; }
            var choices = new List<Choice>();
            foreach (var entry in ycd.ClipMap.OrderBy(entry => (uint)entry.Key))
            {
                var clip = entry.Value.Clip;
                Layer[] layers;
                double duration;
                if (clip is ClipAnimation single)
                {
                    layers = new[] { new Layer(single.Animation, single.StartTime, single.EndTime, single.Rate) };
                    duration = (single.EndTime-single.StartTime) / (double)single.Rate;
                }
                else if (clip is ClipAnimationList multiple)
                {
                    layers = multiple.Animations.Data.Select(item => new Layer(item.Animation, item.StartTime, item.EndTime, item.Rate)).ToArray();
                    duration = multiple.Duration;
                }
                else { layers = Array.Empty<Layer>(); duration = 0; }
                string error = !Finite(duration) || duration <= 0 || duration > 86400 ? "Invalid clip duration or rate" : null;
                if (layers.Length == 0 || layers.Length > 16) error = "Clip must have 1–16 animation layers";
                foreach (var layer in layers)
                    if (layer.Animation == null || !Finite(layer.Start) || !Finite(layer.End) || !Finite(layer.Rate)
                        || layer.Start < 0 || layer.End <= layer.Start || layer.Rate <= 0 || layer.End > layer.Animation.Duration + .0001)
                        error = "Unresolved animation binding or invalid clip interval/rate";
                if (layers.Sum(layer => layer.Animation?.BoneIds?.data_items?.Length ?? 0) > MaxTracks) error = "Clip exceeds 512 combined tracks";
                foreach (var layer in layers)
                    if (layer.Animation != null && animationErrors.TryGetValue(layer.Animation, out var problem)) error = problem;
                choices.Add(new Choice("clip:" + Hash(entry.Key), clip?.Name ?? Hash(entry.Key), "clip", Finite(duration) && duration > 0 ? duration : 0, layers, error));
            }
            choices.AddRange(animations.OrderBy(anim => (uint)anim.Hash).Select(anim => new Choice("animation:" + Hash(anim.Hash), Hash(anim.Hash), "animation", Finite(anim.Duration) && anim.Duration > 0 ? anim.Duration : 0,
                new[] { new Layer(anim, 0, anim.Duration, 1) }, animationErrors.GetValueOrDefault(anim))));
            var chosen = inventoryOnly ? null : selection == null ? choices.FirstOrDefault(choice => choice.Error == null) : choices.FirstOrDefault(choice => choice.Key == selection);
            if (selection != null && chosen == null) throw new InvalidDataException("Selected animation/clip is not in this dictionary");
            if (!inventoryOnly && chosen == null && choices.Any(choice => choice.Error != null)) throw new InvalidDataException(choices.First(choice => choice.Error != null).Error);
            if (chosen?.Error != null) throw new InvalidDataException(chosen.Error);
            var times = chosen == null ? Array.Empty<double>() : Enumerable.Range(0, MaxSamples).Select(i => chosen.Duration * i / (MaxSamples-1)).ToArray();
            var tracks = new List<object>();
            if (chosen != null) for (int li = 0; li < chosen.Layers.Length; li++)
            {
                var layer = chosen.Layers[li];
                var animation = layer.Animation;
                for (int ti = 0; ti < animation.BoneIds.data_items.Length; ti++)
                {
                    var bone = animation.BoneIds.data_items[ti];
                    bool quaternion = bone.Track == 1 || bone.Track == 6;
                    var values = new List<float>();
                    foreach (var time in times)
                    {
                        // Endpoint is shown explicitly; Play loops separately in the UI.
                        double t = chosen.Kind == "animation" ? Math.Min(time, animation.Duration)
                            : time == chosen.Duration && chosen.Layers.Length == 1 ? layer.End
                            : layer.Start + (time * layer.Rate) % (layer.End-layer.Start);
                        double frame = Math.Clamp(t / animation.Duration * (animation.Frames-1), 0, animation.Frames-1);
                        int f0 = (int)Math.Floor(frame), f1 = Math.Min(f0+1, animation.Frames-1);
                        var a = Evaluate(animation, ti, f0, quaternion);
                        var b = Evaluate(animation, ti, f1, quaternion);
                        float alpha = (float)(frame-f0);
                        if (quaternion)
                        {
                            var q = Quaternion.Slerp(new Quaternion(a.X, a.Y, a.Z, a.W), new Quaternion(b.X, b.Y, b.Z, b.W), alpha);
                            values.AddRange(new[] { q.X, q.Y, q.Z, q.W });
                        }
                        else
                        {
                            var value = a*(1-alpha)+b*alpha;
                            values.AddRange(new[] { value.X, value.Y, value.Z, value.W });
                        }
                    }
                    tracks.Add(new { id = li + ":" + ti, layer = li, animation_hash = Hash(animation.Hash), bone_tag = (int)bone.BoneId, track = (int)bone.Track, flags = (int)bone.Unk0,
                        label = Label(bone.Track), quaternion, source_frames = (int)animation.Frames, values });
                }
            }
            return new { schema_version = 1, read_only = true, selected = chosen?.Key, duration = chosen?.Duration ?? 0,
                choices = choices.Select(choice => new { key = choice.Key, name = choice.Name, kind = choice.Kind, duration = choice.Duration, error = choice.Error }),
                times, tracks, sampled = chosen != null,
                scope = "Decoded local channels, up to 240 time samples. Quaternion tracks use shortest-arc interpolation. Layers remain separate; model/skeleton binding, skinning, expressions and game playback are not implied." };
        }

        static void Validate(Animation animation)
        {
            if (animation == null || !Finite(animation.Duration) || animation.Duration <= 0 || animation.Duration > 86400 || animation.Frames < 1 || animation.SequenceFrameLimit < 1)
                throw new InvalidDataException("Invalid animation duration/frame layout");
            var bones = animation.BoneIds?.data_items;
            var sequences = animation.Sequences?.data_items;
            if (bones == null || bones.Length == 0 || bones.Length > MaxTracks || sequences == null || sequences.Length == 0 || sequences.Length > 512)
                throw new InvalidDataException("Animation exceeds supported track/sequence bounds");
            if (bones.Select(bone => (bone.BoneId, bone.Track, bone.Unk0)).Distinct().Count() != bones.Length)
                throw new InvalidDataException("Duplicate bone track identity");
            int needed = (animation.Frames-1) / animation.SequenceFrameLimit + 1;
            if (sequences.Length < needed) throw new InvalidDataException("Missing animation sequence");
            for (int i = 0; i < sequences.Length; i++)
            {
                var sequence = sequences[i];
                int framesNeeded = Math.Min(animation.SequenceFrameLimit, animation.Frames-i*animation.SequenceFrameLimit);
                if (sequence == null || sequence.Sequences?.Length != bones.Length || sequence.NumFrames < framesNeeded)
                    throw new InvalidDataException("Animation sequence/track counts do not match");
                foreach (var track in sequence.Sequences)
                {
                    if (track?.Channels == null || track.Channels.Length == 0 || track.Channels.Length > 5)
                        throw new InvalidDataException("Animation track has an invalid channel layout");
                    int dimensions = track.Channels.Sum(channel => channel is AnimChannelStaticVector3 ? 3 : channel is AnimChannelStaticQuaternion ? 4 : 1);
                    bool cached = track.Channels.Any(channel => channel is AnimChannelCachedQuaternion);
                    if ((!cached && dimensions > 4) || (cached && !FullQuaternionWithCache(track) && (track.Channels.Length < 4 || track.Channels.Take(3).Any(channel => channel is AnimChannelCachedQuaternion || channel is AnimChannelStaticVector3 || channel is AnimChannelStaticQuaternion)
                        || track.Channels.Skip(3).Any(channel => channel is not AnimChannelCachedQuaternion))))
                        throw new InvalidDataException("Animation channel components would be omitted");
                    foreach (var channel in track.Channels)
                    {
                        float[] values = channel switch { AnimChannelRawFloat raw => raw.Values, AnimChannelQuantizeFloat quantized => quantized.Values, AnimChannelLinearFloat linear => linear.Values, _ => null };
                        if ((channel is AnimChannelRawFloat || channel is AnimChannelQuantizeFloat || channel is AnimChannelLinearFloat) && values?.Length != sequence.NumFrames)
                            throw new InvalidDataException("Animation channel frame count mismatch");
                        if (values != null && values.Any(value => !Finite(value))) throw new InvalidDataException("Non-finite animation values");
                        if (channel is AnimChannelIndirectQuantizeFloat indirect && (indirect.Frames?.Length != sequence.NumFrames || indirect.Values == null
                            || indirect.Frames.Any(index => index >= indirect.Values.Length) || indirect.Values.Any(value => !Finite(value))))
                            throw new InvalidDataException("Invalid indirect animation value references");
                        if (channel is AnimChannelCachedQuaternion cachedQuaternion && (cachedQuaternion.QuatIndex < 0 || cachedQuaternion.QuatIndex > 3))
                            throw new InvalidDataException("Invalid cached quaternion component index");
                    }
                }
            }
        }

        static Vector4 Evaluate(Animation animation, int track, int frame, bool quaternion)
        {
            var channel = animation.Sequences.data_items[frame / animation.SequenceFrameLimit].Sequences[track];
            int local = frame % animation.SequenceFrameLimit;
            Vector4 value;
            var cached = channel.Channels.OfType<AnimChannelCachedQuaternion>().FirstOrDefault();
            if (FullQuaternionWithCache(channel))
            {
                // Clip.cs marks IsType7Quat only for CachedQuaternion1. A lone
                // type-2 cache after four stored scalars uses those four scalars,
                // including their sign; reconstructing a positive component here
                // flips real stock hand poses during cover/MG reloads.
                value = new Vector4(channel.Channels[0].EvaluateFloat(local), channel.Channels[1].EvaluateFloat(local),
                    channel.Channels[2].EvaluateFloat(local), channel.Channels[3].EvaluateFloat(local));
            }
            else if (cached != null)
            {
                // ReadXml has no binary reader/value cache. Match Clip.cs's cached
                // quaternion reconstruction directly from its three decoded scalars.
                var xyz = channel.Channels.Take(3).Select(component => component.EvaluateFloat(local)).ToArray();
                float missing = (float)Math.Sqrt(Math.Max(1 - xyz.Sum(component => (double)component*component), 0));
                var components = new List<float>(xyz); components.Insert(cached.QuatIndex, missing);
                value = new Vector4(components[0], components[1], components[2], components[3]);
            }
            else value = channel.EvaluateVector(local);
            if (!Finite(value.X) || !Finite(value.Y) || !Finite(value.Z) || !Finite(value.W)
                || Math.Max(Math.Max(Math.Abs(value.X), Math.Abs(value.Y)), Math.Max(Math.Abs(value.Z), Math.Abs(value.W))) > 1e9)
                throw new InvalidDataException("Animation channel produced invalid coordinates");
            if (quaternion || cached != null)
            {
                float length = value.Length();
                if (length < 1e-8) throw new InvalidDataException("Animation contains a zero quaternion");
                value /= length;
            }
            return value;
        }

        static bool FullQuaternionWithCache(AnimSequence track)
        {
            return track?.Channels?.Length == 5
                && track.Channels.Take(4).All(channel => channel is AnimChannelStaticFloat || channel is AnimChannelRawFloat
                    || channel is AnimChannelQuantizeFloat || channel is AnimChannelIndirectQuantizeFloat || channel is AnimChannelLinearFloat)
                && track.Channels[4] is AnimChannelCachedQuaternion cache && cache.Type == AnimChannelType.CachedQuaternion2;
        }
    }
}
