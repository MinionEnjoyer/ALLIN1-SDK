import { useEffect, useMemo, useRef, useState } from "react";
import "./NativeAnimationView.css";
import AnimationModelViewport from "./AnimationModelViewport";
import type { AnimationModel, ModelBindingSelection } from "./animationPose";

export type AnimationPacket = {
  schema_version: number; read_only: boolean; selected: string | null; duration: number; sampled: boolean; scope: string;
  choices: { key: string; name: string; kind: string; duration: number; error: string | null }[];
  times: number[];
  tracks: { id: string; layer: number; animation_hash: string; bone_tag: number; track: number; flags: number; label: string; quaternion: boolean; source_frames: number; values: number[] }[];
};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 1e9;
export function validAnimationPacket(packet: AnimationPacket): boolean {
  if (!packet || packet.schema_version !== 1 || packet.read_only !== true || typeof packet.scope !== "string"
    || !finite(packet.duration) || packet.duration < 0 || packet.duration > 86400 || !Array.isArray(packet.times) || packet.times.length > 240
    || !Array.isArray(packet.choices) || packet.choices.length > 2000 || !Array.isArray(packet.tracks) || packet.tracks.length > 512
    || !packet.times.every((time, i) => finite(time) && (i === 0 ? time === 0 : time > packet.times[i-1]))) return false;
  const keys = new Set<string>(), ids = new Set<string>();
  for (const choice of packet.choices) {
    if (!choice || typeof choice.key !== "string" || !/^(animation|clip):[0-9A-F]{8}$/.test(choice.key) || keys.has(choice.key)
      || typeof choice.name !== "string" || !finite(choice.duration) || !(choice.error === null || typeof choice.error === "string")) return false;
    keys.add(choice.key);
  }
  if (packet.selected === null) return packet.times.length === 0 && packet.tracks.length === 0;
  if (!keys.has(packet.selected) || packet.times.length < 2 || packet.duration <= 0 || Math.abs(packet.times.at(-1)!-packet.duration) > 1e-6) return false;
  for (const track of packet.tracks) {
    if (!track || typeof track.id !== "string" || ids.has(track.id) || typeof track.label !== "string"
      || typeof track.animation_hash !== "string" || typeof track.quaternion !== "boolean"
      || !Number.isInteger(track.bone_tag) || track.bone_tag < 0 || track.bone_tag > 65535
      || !Number.isInteger(track.track) || track.track < 0 || track.track > 255 || !Number.isInteger(track.layer) || track.layer < 0 || track.layer > 15
      || !Number.isInteger(track.flags) || track.flags < 0 || track.flags > 255
      || !Array.isArray(track.values) || track.values.length !== packet.times.length*4 || !track.values.every(finite)) return false;
    ids.add(track.id);
  }
  return true;
}
const colors = ["#61c9b0", "#eecc78", "#8dacf1", "#ea93c4"];

export default function NativeAnimationView({ packet, active, locked, draftDirty, onSelect, model, onChooseModel, onBindModel, onClearModel, onChooseSkeleton, onClearSkeleton }: {
  packet: AnimationPacket; active: boolean; locked: boolean; draftDirty: boolean; onSelect: (key: string) => void;
  model?: AnimationModel; onChooseModel?: ()=>void; onBindModel?: (selection: ModelBindingSelection)=>void; onClearModel?: ()=>void;
  onChooseSkeleton?: ()=>void; onClearSkeleton?: ()=>void;
}) {
  const [playing, setPlaying] = useState(false), [time, setTime] = useState(0), [rate, setRate] = useState(1), [loop, setLoop] = useState(true);
  const [trackId, setTrackId] = useState(""), [open, setOpen] = useState(true);
  const timeRef = useRef(0);
  const safe = useMemo(() => validAnimationPacket(packet), [packet]);
  const tracks = safe ? packet.tracks : [];
  const selectedTrack = tracks.find(track => track.id === trackId) ?? tracks[0];
  const duration = safe ? packet.duration : 0;
  useEffect(() => { setPlaying(false); setTime(0); timeRef.current = 0; }, [packet]);
  useEffect(() => {
    if (!safe || !active || locked || draftDirty || !open || !playing || duration <= 0) { setPlaying(false); return; }
    let frame: number, last: number | undefined;
    function tick(now: number) {
      const delta = last === undefined ? 0 : Math.min(.25, (now-last)/1000)*rate;
      last = now;
      const next = timeRef.current+delta;
      timeRef.current = loop ? next % duration : Math.min(next, duration);
      setTime(timeRef.current);
      if (!loop && next >= duration) { setPlaying(false); return; }
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [safe, active, locked, draftDirty, open, playing, duration, loop, rate]);
  if (!safe) return <p role="alert">Animation evidence is invalid or exceeds display limits.</p>;
  const frame = packet.times.length ? Math.min(packet.times.length-1, Math.max(0, Math.round(time/duration*(packet.times.length-1)))) : 0;
  const values = selectedTrack?.values.slice(frame*4, frame*4+4) ?? [];
  const min = selectedTrack ? Math.min(...selectedTrack.values) : 0, max = selectedTrack ? Math.max(...selectedTrack.values) : 1;
  const span = Math.max(max-min, 1e-6);
  const channels = selectedTrack ? [0, 1, 2, 3].map(component => packet.times.map((_, i) => `${40+i/(packet.times.length-1)*620},${220-(selectedTrack.values[i*4+component]-min)/span*180}`).join(" ")) : [];
  const seek = (value: number) => { setPlaying(false); setTime(value); timeRef.current = value; };
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="native-animation">
    <summary>Animation timeline & decoded channels</summary><p>{model ? "The timeline drives sampled channels and the separately bound model below. Layers remain separate; retail animation codecs and game compatibility require additional qualification." : packet.scope}</p>
    {!model && <p>Channel inspection is not model playback: choose a matching exported model XML to bind its skeleton and skin.</p>}
    {onChooseModel && <div className="animation-controls"><button className="quiet-button" disabled={locked || draftDirty} onClick={()=>{setPlaying(false);onChooseModel();}}>Choose animation model XML</button>
      {model && <button className="quiet-button" disabled={locked || draftDirty} onClick={()=>{setPlaying(false);onClearModel?.();}}>Clear animation model</button>}</div>}
    {model && onChooseSkeleton && <div className="animation-controls"><button className="quiet-button" disabled={locked || draftDirty || !model.source} onClick={()=>{setPlaying(false);onChooseSkeleton();}}>Choose shared skeleton XML</button>
      {model.skeleton_binding && <button className="quiet-button" disabled={locked || draftDirty} onClick={()=>{setPlaying(false);onClearSkeleton?.();}}>Use embedded skeleton</button>}</div>}
    {draftDirty && <p role="status">Showing saved XML. Save or discard the draft before changing animation selection.</p>}
    <div className="animation-controls"><label>Clip or animation<select aria-label="Animation selection" value={packet.selected ?? ""} disabled={locked || draftDirty} onChange={event => { setPlaying(false); onSelect(event.target.value); }}>
      {packet.selected === null && <option value="">No playable channel data</option>}
      {packet.choices.map(choice => <option key={choice.key} value={choice.key} disabled={!!choice.error}>{choice.name} · {choice.kind}{choice.error ? ` · ${choice.error}` : ""}</option>)}
    </select></label>
      <button className="quiet-button" disabled={!packet.selected || !active || locked || draftDirty} onClick={() => { if (time >= duration) seek(0); setPlaying(value => !value); }}>{playing ? model ? "Pause animation" : "Pause channels" : model ? "Play animation" : "Play channels"}</button>
      <button className="quiet-button" onClick={() => seek(0)}>Reset timeline</button>
      <label>Speed<select aria-label="Animation speed" value={rate} onChange={event => setRate(Number(event.target.value))}>{[.25, .5, 1, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
      <label><input type="checkbox" checked={loop} onChange={event => setLoop(event.target.checked)} />Loop channels</label>
    </div>
    <label className="animation-seek">Timeline<input aria-label="Animation time" type="range" min="0" max={duration || 1} step={duration ? duration/(packet.times.length-1) : 1} value={time} disabled={!packet.selected} onChange={event => seek(Number(event.target.value))} /></label>
    <output aria-label="Animation timestamp" aria-live={playing ? "off" : "polite"}>{time.toFixed(3)} / {duration.toFixed(3)} seconds · sample {packet.times.length ? frame+1 : 0} / {packet.times.length}</output>
    {model && packet.selected && <AnimationModelViewport model={model} animation={packet} time={time} locked={locked || draftDirty} onBind={selection=>{setPlaying(false);onBindModel?.(selection);}} />}
    <div className="animation-layout"><aside><label>Decoded track<select aria-label="Animation track" value={selectedTrack?.id ?? ""} onChange={event => setTrackId(event.target.value)}>
      {!tracks.length && <option value="">No tracks</option>}{tracks.map(track => <option key={track.id} value={track.id}>Layer {track.layer} · bone {track.bone_tag} · {track.label}</option>)}
    </select></label>{selectedTrack && <><p>Animation hash {selectedTrack.animation_hash}. {selectedTrack.source_frames} source frames; {packet.times.length} display samples.</p>
      <p>Layer {selectedTrack.layer}; bone tag {selectedTrack.bone_tag}; track {selectedTrack.track}. {selectedTrack.quaternion ? "Normalized quaternion XYZW." : "Decoded XYZW components; interpretation depends on track type."}</p>
      <dl>{values.map((value, i) => <div key={i}><dt>{"XYZW"[i]}</dt><dd>{value.toFixed(6)}</dd></div>)}</dl>
    </>}</aside><svg role="img" aria-label="Animation channel curves" viewBox="0 0 700 260">
      <title>Decoded component values over time; not a skeleton pose</title>
      {channels.map((points, i) => <polyline key={i} points={points} fill="none" stroke={colors[i]} strokeWidth="2"><title>{"XYZW"[i]}</title></polyline>)}
      <line x1={40+(duration ? time/duration : 0)*620} x2={40+(duration ? time/duration : 0)*620} y1="20" y2="235" stroke="white" strokeWidth="1" />
      {colors.map((color, i) => <text key={i} x={40+i*45} y="250" fill={color} fontSize="16">{"XYZW"[i]}</text>)}
    </svg></div>
  </details>;
}
