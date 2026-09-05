import { useEffect, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import type { DesktopClient, PreviewArtifact } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";

interface Settings {
  width: number; height: number; quality: string; samples: number | null; engine: string; device: string;
  light_rig: string; light_rotation_deg: number; light_strength: number; background: string; background_color: string;
  transparent: boolean; lens_mm: number; ground_plane: boolean; contact_shadows: boolean;
}
interface RenderSnapshot extends WorkspaceResult {
  blender: { executable: string; version: string; sha256: string } | null; render_ready: boolean;
  render_id?: string; artifact?: PreviewArtifact;
  render_record?: { width: number; height: number; elapsed_seconds: number; output_sha256: string; fidelity: string; metadata: Record<string, unknown> };
}
const defaults = (): Settings => ({ width: 1920, height: 1080, quality: "production", samples: null, engine: "eevee", device: "auto",
  light_rig: "studio", light_rotation_deg: 0, light_strength: 1, background: "studio_dark", background_color: "#111714",
  transparent: false, lens_mm: 52, ground_plane: true, contact_shadows: true });
const initialCamera = () => ({ yaw: 34, pitch: 18, lod: "", component: "" });

export default function RenderWorkbench({ client, onDirtyChange }: { client: DesktopClient; onDirtyChange: (value: boolean) => void }) {
  const [source, setSource] = useState(""), [texture, setTexture] = useState(""), [game, setGame] = useState("");
  const [edition, setEdition] = useState("Enhanced"), [blender, setBlender] = useState("");
  const [settings, setSettings] = useState(defaults), [camera, setCamera] = useState(initialCamera), [filename, setFilename] = useState("compiled-render.png");
  const [snapshot, setSnapshot] = useState<RenderSnapshot | null>(null), [frame, setFrame] = useState<RenderSnapshot | null>(null);
  const draft = JSON.stringify({ source, texture, game, edition, blender, settings, camera });
  const [baseline, setBaseline] = useState(draft), [frameDraft, setFrameDraft] = useState(""), [exportedId, setExportedId] = useState("");
  const work = useAuthoringWorkspace(client, "render", value => {
    const s = value as RenderSnapshot;
    if (typeof s.render_ready !== "boolean" || (s.blender && !/^[a-f0-9]{64}$/.test(s.blender.sha256))) throw new Error("Invalid Blender dependency evidence");
    if (s.render_id && (!/^[a-f0-9]{64}$/.test(s.render_id) || !s.artifact || !s.render_record || !/^[a-f0-9]{64}$/.test(s.artifact.sha256))) throw new Error("Incomplete compiled-render evidence");
    setSnapshot(s);
    if (s.render_id) { setFrame(s); setFrameDraft(draft); }
  });
  useEffect(() => { onDirtyChange(work.locked || draft !== baseline || (!!frame && frame.render_id !== exportedId)); }, [work.locked, draft, baseline, frame, exportedId, onDirtyChange]);
  useEffect(() => { if (work.lastResult?.output && frame) { setBaseline(frameDraft); setExportedId(frame.render_id!); } }, [work.lastResult]);
  const choose = async (kind: "render_model" | "render_textures" | "gta_folder" | "blender_executable", setter: (path: string) => void) => {
    const selected = await work.choose(kind); if (selected) setter(selected);
  };
  const renderFrame = () => work.run("inspect_authoring_workspace", {
    source, ...(texture ? { texture_dictionary: texture } : {}), ...(game ? { gta_path: game } : {}), edition,
    ...(blender ? { blender_executable: blender } : {}), render: true, settings,
    camera: { ...camera, lod: camera.lod || null, component: camera.component || null },
  });
  const exportFrame = async () => {
    if (!frame) return;
    const parent = await work.choose("authoring_parent"); if (!parent) return;
    await work.run("review_workspace_action", { action: "export", render_id: frame.render_id, expected_state_sha256: frame.state_sha256,
      destination: parent.replace(/[\\/]$/, "") + "/" + filename });
  };
  const numberField = (key: keyof Settings, label: string, min: number, max: number, step = 1) => <label>{label}<input type="number" min={min} max={max} step={step} value={settings[key] as number} onChange={e => setSettings({ ...settings, [key]: Number(e.target.value) })} /></label>;
  const choice = (key: keyof Settings, label: string, values: string[]) => <label>{label}<select value={settings[key] as string} onChange={e => setSettings({ ...settings, [key]: e.target.value })}>{values.map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>;
  const reset = () => { const saved = JSON.parse(baseline); setSource(saved.source); setTexture(saved.texture); setGame(saved.game); setEdition(saved.edition); setBlender(saved.blender); setSettings(saved.settings); setCamera(saved.camera); setFrame(null); setFrameDraft(""); setSnapshot(null); };
  return <section className="offline-workbench render-workbench" aria-label="Compiled render studio"><div className="offline-toolbar"><div><h3>Compiled render studio</h3><p>Render decoded geometry and linked texture pixels with the existing isolated Blender workflow.</p></div><button className="primary-button" disabled={work.locked || !source} onClick={() => void renderFrame()}>Render frame</button></div>
    <AuthoringFeedback work={work} />
    <div className="offline-panes"><section><header><span className="pane-kicker">Inputs</span><h4>Model & render host</h4></header><div className="offline-pane-body"><fieldset disabled={work.locked}>
      <button className="quiet-button" onClick={() => void choose("render_model", setSource)}>Choose render model</button><p className="source-path">{source || "Loose YFT, YDR or YDD"}</p>
      <button className="quiet-button" disabled={!source} onClick={() => void choose("render_textures", setTexture)}>Link render textures</button><p className="source-path">{texture || "No linked YTD. Materials will be approximated."}</p>{texture && <button className="text-action" onClick={() => setTexture("")}>Unlink render textures</button>}
      <label>Render edition<select value={edition} onChange={e => setEdition(e.target.value)}><option>Enhanced</option><option>Legacy</option></select></label>
      <button className="quiet-button" onClick={() => void choose("gta_folder", setGame)}>Choose render decoder context</button><p className="source-path">{game || "No game folder selected"}</p>
      <h5>Blender dependency</h5><button className="quiet-button" onClick={() => void choose("blender_executable", selected => { setBlender(selected); setSnapshot(null); })}>Locate Blender</button><p className="source-path">{blender || "Automatic discovery"}</p>
      {blender && <button className="text-action" onClick={() => { setBlender(""); setSnapshot(null); }}>Use automatic Blender discovery</button>}
      <button className="quiet-button" onClick={() => void work.run("inspect_authoring_workspace", blender ? { blender_executable: blender } : {})}>Check Blender</button>
      {snapshot && <p>{snapshot.blender ? `Blender ${snapshot.blender.version} verified` : "Blender is missing. Install it or locate its executable."}</p>}
      <p>Blender runs headlessly with factory settings and auto-execution disabled. No supplied scripts or .blend files are executed.</p>
      <h5>Camera & selection</h5>{(["yaw", "pitch"] as const).map(key => <label key={key}>{key === "yaw" ? "Camera yaw" : "Camera pitch"}<input type="number" value={camera[key]} onChange={e => setCamera({ ...camera, [key]: Number(e.target.value) })} /></label>)}
      <label>LOD name (blank: all)<input value={camera.lod} onChange={e => setCamera({ ...camera, lod: e.target.value })} /></label><label>Component name (blank: all)<input value={camera.component} onChange={e => setCamera({ ...camera, component: e.target.value })} /></label>
      {numberField("lens_mm", "Lens (mm)", 18, 200)}
    </fieldset></div></section><section><header><span className="pane-kicker">Frame</span><h4>Compiled output</h4></header><div className="offline-pane-body render-frame-pane">
      {frame?.artifact && frame.render_record ? <><img className="compiled-frame" src={frame.artifact.preview_url || convertFileSrc(frame.artifact.path)} alt="Compiled Blender frame" /><p>{frame.render_record.width} × {frame.render_record.height} · {frame.render_record.elapsed_seconds.toFixed(1)} s</p>
        {frameDraft !== draft && <p className="action-notice">Settings changed. This is the previous completed frame; render again to apply them.</p>}
        <p>{frame.render_record.fidelity}</p><details><summary>Render identities & evidence</summary><pre>{JSON.stringify(frame.render_record, null, 2)}</pre></details>
        <fieldset disabled={work.locked}><label>PNG export name<input value={filename} onChange={e => setFilename(e.target.value)} /></label><button className="primary-button" disabled={!filename} onClick={() => void exportFrame()}>Review PNG export</button><button className="quiet-button" onClick={() => { setFrame(null); setFrameDraft(""); }}>Discard completed frame</button></fieldset>
      </> : <div className="render-empty"><h5>No completed frame</h5><p>Choose a model and render. Full-resolution pixels remain in the SDK preview cache until you export them.</p></div>}
      <p>Offline render evidence is not in-game Reactor acceptance. Shader programs, skinning and the game lighting pipeline are not reproduced.</p>
    </div></section><section><header><span className="pane-kicker">Output</span><h4>Quality & lighting</h4></header><div className="offline-pane-body"><fieldset disabled={work.locked}>
      {numberField("width", "Width (px)", 256, 15360)}{numberField("height", "Height (px)", 256, 15360)}
      {choice("quality", "Render quality", ["preview", "production", "maximum"])}{choice("engine", "Render engine", ["eevee", "cycles"])}{choice("device", "Render device", ["auto", "cpu", "gpu"])}
      <label>Samples (blank: quality default)<input type="number" min={1} max={4096} value={settings.samples ?? ""} onChange={e => setSettings({ ...settings, samples: e.target.value ? Number(e.target.value) : null })} /></label>
      {choice("light_rig", "Light rig", ["studio", "outdoor", "dramatic", "neutral"])}{numberField("light_rotation_deg", "Light rotation (degrees)", -3600, 3600)}{numberField("light_strength", "Light strength", 0.05, 10, 0.05)}
      {choice("background", "Background", ["studio_dark", "studio_light", "transparent", "custom"])}<label>Background color<input type="color" value={settings.background_color} onChange={e => setSettings({ ...settings, background_color: e.target.value })} /></label>
      {(["transparent", "ground_plane", "contact_shadows"] as const).map(key => <label className="runtime-checkbox" key={key}><input type="checkbox" checked={settings[key]} onChange={e => setSettings({ ...settings, [key]: e.target.checked })} />{key.replaceAll("_", " ")}</label>)}
      <button className="quiet-button" onClick={() => setSettings(defaults())}>Reset render settings</button><button className="quiet-button" disabled={draft === baseline && !frame} onClick={reset}>Discard render draft</button>
    </fieldset></div></section></div>
  </section>;
}
