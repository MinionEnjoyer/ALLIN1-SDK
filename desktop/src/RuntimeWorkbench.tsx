import { useEffect, useState } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";

interface Toolchain { mode: string; cmake_path: string; ctest_path: string; visual_studio_path: string }
interface Settings { enabled: boolean; discovery_interval_ms: number; recovery_interval_ms: number; restore_on_unload: boolean; configuration_directory: string; log_file: string }
interface Snapshot extends WorkspaceResult {
  source: string; toolchain: { ready: boolean; selection_fingerprint: string | null; problems: string[]; checks: { key: string; label: string; ready: boolean; detected: string; requirement: string; guidance: string; detail: string }[]; guidance: string[] };
}
const defaultTools = (): Toolchain => ({ mode: "auto", cmake_path: "", ctest_path: "", visual_studio_path: "" });
const defaultSettings = (): Settings => ({ enabled: true, discovery_interval_ms: 250, recovery_interval_ms: 2000, restore_on_unload: true, configuration_directory: "VehicleWorkbenchAxles/configs", log_file: "VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log" });
export default function RuntimeWorkbench({ client, onDirtyChange }: { client: DesktopClient; onDirtyChange: (guarded: boolean) => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null), [toolchain, setToolchain] = useState(defaultTools), [settings, setSettings] = useState(defaultSettings);
  const [legacy, setLegacy] = useState(false), [enhanced, setEnhanced] = useState(true), [archives, setArchives] = useState(true);
  const [configurations, setConfigurations] = useState<string[]>([]), [buildId, setBuildId] = useState("allin1-sdk-local"), [folder, setFolder] = useState("story-runtime-candidate");
  const draft = JSON.stringify({ toolchain, settings, legacy, enhanced, archives, configurations, buildId, folder });
  const [baseline, setBaseline] = useState(draft);
  const work = useAuthoringWorkspace(client, "runtime", value => {
    const s = value as Snapshot;
    if (!s.toolchain || !Array.isArray(s.toolchain.checks) || !Array.isArray(s.toolchain.problems) || typeof s.toolchain.ready !== "boolean") throw new Error("Invalid native preflight evidence");
    setSnapshot(s);
  });
  useEffect(() => { onDirtyChange(work.locked || draft !== baseline); }, [work.locked, draft, baseline, onDirtyChange]);
  useEffect(() => { if (work.lastResult?.runtime_build) setBaseline(draft); }, [work.lastResult]);
  const preflight = () => work.run("inspect_authoring_workspace", { toolchain });
  const setTools = (next: Toolchain) => { setToolchain(next); setSnapshot(null); };
  const addConfiguration = async () => { const selected = await work.choose("graph_document"); if (selected && !configurations.includes(selected)) setConfigurations([...configurations, selected]); };
  const build = async () => {
    if (!snapshot?.toolchain.ready) return;
    const parent = await work.choose("authoring_parent"); if (!parent) return;
    await work.run("review_workspace_action", { action: "build", toolchain, settings, targets: [...(legacy ? ["story-legacy"] : []), ...(enhanced ? ["story-enhanced"] : [])],
      configuration_files: configurations, create_archives: archives, build_id: buildId, expected_state_sha256: snapshot.state_sha256,
      destination: parent.replace(/[\\/]$/, "") + "/" + folder });
  };
  return <section className="offline-workbench" aria-label="Story controller builder"><div className="offline-toolbar"><div><h3>Story controller builder</h3><p>Prove the local compiler, build edition-specific controllers, and retain their test and artifact evidence.</p></div>
    <button className="primary-button" disabled={work.locked} onClick={() => void preflight()}>Run native preflight</button></div>
    <div className="source-strip"><strong>{snapshot ? snapshot.toolchain.ready ? "Toolchain verified" : "Toolchain requires attention" : "Preflight not run"}</strong><span className="source-path">{snapshot?.source || "SDK-owned native runtime sources"}</span></div>
    <AuthoringFeedback work={work} />
    <div className="offline-panes"><section><header><span className="pane-kicker">Build host</span><h4>Toolchain</h4></header><div className="offline-pane-body"><fieldset disabled={work.locked}>
      <label>Toolchain selection<select value={toolchain.mode} onChange={e => setTools({ ...toolchain, mode: e.target.value })}><option value="auto">Automatic discovery</option><option value="manual">Manual paths</option></select></label>
      {toolchain.mode === "manual" && (["cmake_path", "ctest_path", "visual_studio_path"] as const).map(key => <label key={key}>{key === "cmake_path" ? "CMake executable" : key === "ctest_path" ? "CTest executable" : "Visual Studio installation / compiler"}<input value={toolchain[key]} onChange={e => setTools({ ...toolchain, [key]: e.target.value })} /></label>)}
      <p>Manual choices are authoritative. A missing override blocks preflight; the SDK does not silently switch compilers.</p></fieldset>
      {snapshot?.toolchain.problems.map((problem, i) => <p className="error-banner" key={i}>{problem}</p>)}
      <ul>{snapshot?.toolchain.guidance.map((item, i) => <li key={i}>{item}</li>)}</ul>
    </div></section><section><header><span className="pane-kicker">Evidence</span><h4>Compiler & dependency checks</h4></header><div className="offline-pane-body">
      {!snapshot ? <p>Preflight checks source completeness, CMake/CTest, the x64 C++ toolchain, and a real compile/link probe.</p> : <div className="runtime-checks">{snapshot.toolchain.checks.map(check => <details key={check.key}><summary>{check.ready ? "PASS" : "FAIL"} · {check.label}</summary><p>{check.detected}</p><p>Required: {check.requirement}</p>{check.detail && <pre>{check.detail}</pre>}{check.guidance && <p>{check.guidance}</p>}</details>)}</div>}
      {snapshot?.toolchain.selection_fingerprint && <><h5>Selected toolchain identity</h5><p className="hash-value">{snapshot.toolchain.selection_fingerprint}</p></>}
      {work.lastResult?.runtime_build !== undefined && <details open><summary>Candidate build receipt</summary><pre className="runtime-receipt">{JSON.stringify(work.lastResult.runtime_build, null, 2)}</pre></details>}
    </div></section><section><header><span className="pane-kicker">Candidate</span><h4>Runtime settings & targets</h4></header><div className="offline-pane-body"><fieldset disabled={work.locked}>
      <label className="runtime-checkbox"><input type="checkbox" checked={legacy} onChange={e => setLegacy(e.target.checked)} />Story Legacy</label>
      <label className="runtime-checkbox"><input type="checkbox" checked={enhanced} onChange={e => setEnhanced(e.target.checked)} />Story Enhanced</label>
      <label className="runtime-checkbox"><input type="checkbox" checked={settings.enabled} onChange={e => setSettings({ ...settings, enabled: e.target.checked })} />Controller enabled</label>
      <label className="runtime-checkbox"><input type="checkbox" checked={settings.restore_on_unload} onChange={e => setSettings({ ...settings, restore_on_unload: e.target.checked })} />Restore on unload</label>
      <label>Discovery interval (ms)<input type="number" value={settings.discovery_interval_ms} min={100} max={10000} onChange={e => setSettings({ ...settings, discovery_interval_ms: Number(e.target.value) })} /></label>
      <label>Recovery interval (ms)<input type="number" value={settings.recovery_interval_ms} min={settings.discovery_interval_ms} max={60000} onChange={e => setSettings({ ...settings, recovery_interval_ms: Number(e.target.value) })} /></label>
      <label>Configuration directory (GTA-relative)<input value={settings.configuration_directory} onChange={e => setSettings({ ...settings, configuration_directory: e.target.value })} /></label>
      <label>Log file (GTA-relative)<input value={settings.log_file} onChange={e => setSettings({ ...settings, log_file: e.target.value })} /></label>
      <button className="quiet-button" disabled={configurations.length >= 32} onClick={() => void addConfiguration()}>Add axle configuration JSON</button>
      <ul className="runtime-configurations">{configurations.map(file => <li key={file}><span>{file}</span><button className="text-action" onClick={() => setConfigurations(configurations.filter(item => item !== file))}>Remove {file.split(/[\\/]/).pop()}</button></li>)}</ul>
      <p>No configuration files builds a generic controller. Vehicle-specific JSON can be added to its package later.</p>
      <label>Build identity<input value={buildId} maxLength={128} onChange={e => setBuildId(e.target.value)} /></label>
      <label>Candidate folder name<input value={folder} maxLength={100} onChange={e => setFolder(e.target.value)} /></label>
      <label className="runtime-checkbox"><input type="checkbox" checked={archives} onChange={e => setArchives(e.target.checked)} />Create distribution archives</label>
      <button className="primary-button" disabled={!snapshot?.toolchain.ready || (!legacy && !enhanced) || !folder || !buildId} onClick={() => void build()}>Review controller build</button>
      <button className="quiet-button" disabled={draft === baseline} onClick={() => { const saved = JSON.parse(baseline); setTools(saved.toolchain); setSettings(saved.settings); setLegacy(saved.legacy); setEnhanced(saved.enhanced); setArchives(saved.archives); setConfigurations(saved.configurations); setBuildId(saved.buildId); setFolder(saved.folder); }}>Discard runtime draft</button>
      <p>Builds run CTest and validate the resulting binaries. A passing build is still a candidate—not live game acceptance. No installation is performed.</p>
    </fieldset></div></section></div>
  </section>;
}
