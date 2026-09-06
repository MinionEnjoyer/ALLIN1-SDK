import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import CodeEditor from "./CodeEditor";
import AssetValidationReport, {type AssetReport} from "./AssetValidationReport";
import TextureDictionaryWorkspace from "./TextureDictionaryWorkspace";
import NativeRelationships, { type RelationshipGraph } from "./NativeRelationships";
import NativeCollisionView, { type CollisionPacket } from "./NativeCollisionView";
import NativeAnimationView, { type AnimationPacket } from "./NativeAnimationView";
import type { AnimationModel } from "./animationPose";
import "./OfflineAuthoring.css";
import "./NativeWorkspace.css";

export type NativeRequest = { source?: string; workspace?: string; archive?: string; entry_id?: string; gta_path?: string; edition?: string; requestId: number };
type Dependency = { path: string; size: number; kind: string; sha256: string };
type NativeSession = WorkspaceResult & { source: string; workspace: string | null; name: string; edition: string; gta_path: string | null;
  asset_validation?: AssetReport;
  relationships?: RelationshipGraph;
  collision?: CollisionPacket;
  animation?: AnimationPacket;
  animation_model?: AnimationModel;
  xml_chunks: string[]; xml_editable: boolean; xml_path?: string; xml_size?: number; preview_chunks?: string[]; preview_truncated?: boolean;
  dependencies: Dependency[]; warnings: string[]; metadata?: Record<string, unknown>; archive_binding?: Record<string, string> | null;
  audio_dependency?: string; audio?: { path: string; channels: number; sample_rate: number; duration_seconds: number; selected_channel: number | null } };

export default function NativeWorkspace({ client, onGuardChange, request, active = true, onOpenPlan, onSearchReference }: {
  client: DesktopClient; onGuardChange: (value: boolean) => void; request?: NativeRequest | null; active?: boolean; onOpenPlan?: (path: string) => void;
  onSearchReference?: (query: string, game: string) => void;
}) {
  const [session, setSession] = useState<NativeSession | null>(null);
  const [edition, setEdition] = useState(""), [game, setGame] = useState("");
  const [text, setText] = useState(""), [name, setName] = useState("native-workspace"), [output, setOutput] = useState("");
  const [dependency, setDependency] = useState("");
  const [channel, setChannel] = useState("");
  const [planScope, setPlanScope] = useState("");
  const [textureMode, setTextureMode] = useState(false), [textureGuarded, setTextureGuarded] = useState(false);
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => { if (!active) player.current?.pause(); }, [active]);
  const work = useAuthoringWorkspace(client, "native", value => {
    const native = value as NativeSession;
    if (!Array.isArray(native.xml_chunks) || native.xml_chunks.some(chunk => typeof chunk !== "string") || !Array.isArray(native.dependencies) || !Array.isArray(native.warnings))
      throw new Error("Invalid native workspace evidence");
    setSession(native); setText(native.xml_chunks.join("")); setOutput(native.name); setDependency(native.audio_dependency || "");
    setEdition(native.edition); setGame(native.gta_path || ""); setPlanScope("");
  });
  const dirty = !!session?.workspace && text !== session.xml_chunks.join("");
  const locked = dirty || work.locked || textureGuarded;
  useEffect(() => { onGuardChange(locked); }, [locked, onGuardChange]);
  const handled = useRef(0);
  useEffect(() => {
    if (!request || request.requestId === handled.current) return;
    handled.current = request.requestId;
    if (locked) { work.setError("Save or discard the native/texture draft before opening another resource."); return; }
    setTextureMode(false);
    const { requestId: _id, ...context } = request;
    void work.run("inspect_authoring_workspace", context);
  }, [request]);
  const context = session?.workspace ? { workspace: session.workspace, gta_path: session.gta_path }
    : session?.archive_binding ? { archive: session.archive_binding.outer_archive, entry_id: session.archive_binding.entry_id, gta_path: session.gta_path }
    : { source: session?.source, edition: session?.edition, gta_path: session?.gta_path };
  const review = (action: string, extra = {}) => work.run("review_workspace_action", { ...context, action, expected_state_sha256: session?.state_sha256, ...extra });
  const modelDocument = session?.animation_model?.source ? {model_xml: session.animation_model.source, drawable: session.animation_model.selected??undefined, lod: session.animation_model.lod??undefined,
    ...(session.animation_model.skeleton_binding?.source ? {skeleton_xml:session.animation_model.skeleton_binding.source, skeleton_drawable:session.animation_model.skeleton_binding.selected??undefined} : {})} : {};
  const inspectAnimation = (document: Record<string, unknown>) => {
    if (dirty || work.locked) return;
    void work.run("inspect_authoring_workspace", {...context, document: {animation: session?.animation?.selected, ...document}});
  };
  async function open(workspace: boolean) {
    const selected = await work.choose(workspace ? "binary_workspace" : "binary_source");
    if (selected) await work.run("inspect_authoring_workspace", workspace ? { workspace: selected, gta_path: game || undefined } : { source: selected, edition, gta_path: game || undefined });
  }
  async function destination(action: "export" | "build" | "export_dependency" | "plan_replacement" | "export_validation") {
    const parent = await work.choose("authoring_parent");
    const filename = action === "export_validation" ? `${session?.name}.asset-validation.json` : action === "export" ? name : action === "build" ? output : action === "plan_replacement" ? `${session?.name}.replacement.json` : dependency.split("/").pop();
    if (parent && filename) await review(action, { destination: `${parent.replace(/[\\/]$/, "")}/${filename}`, ...(action === "export_dependency" ? { document: { dependency } } : action === "plan_replacement" ? { document: { authorized_root: planScope || undefined } } : {}) });
  }
  if (textureMode && session?.workspace) return <section className="native-workspace" aria-label="Archive-bound texture editor">
    <div className="heading-actions native-context"><strong>{session.edition} · {session.name}</strong>
      <button className="quiet-button" disabled={textureGuarded || work.locked} onClick={async () => {
        if (textureGuarded || work.locked) return;
        setTextureMode(false);
        await work.run("inspect_authoring_workspace", context);
      }}>Return to XML & replacement plan</button></div>
    <p>{session.archive_binding ? `Exact member: ${session.archive_binding.entry_id} in ${session.archive_binding.outer_archive}` : `Exported native workspace: ${session.workspace}`}</p>
    <TextureDictionaryWorkspace client={client} initialWorkspace={session.workspace} initialGamePath={session.gta_path || ""} onGuardChange={setTextureGuarded} />
  </section>;
  return <section className="offline-workbench native-workspace" aria-label="Native resource workspace">
    <div className="offline-toolbar"><div><h3>Native resources & audio</h3><p>Export editable XML and dependencies, then rebuild and reparse an offline candidate. Game compatibility requires separate testing.</p></div>
      <div className="heading-actions"><button className="quiet-button" disabled={dirty || work.locked || !edition} onClick={() => void open(false)}>Open native file</button>
        <button className="quiet-button" disabled={dirty || work.locked} onClick={() => void open(true)}>Open native workspace</button></div></div>
    <div className="heading-actions native-context"><label>Source edition<select aria-label="Native source edition" value={edition} disabled={dirty || work.locked || !!session} onChange={e => setEdition(e.target.value)}><option value="">Choose edition</option><option>Legacy</option><option>Enhanced</option></select></label>
      <button className="quiet-button" disabled={dirty || work.locked} onClick={async () => { const chosen = await work.choose("gta_folder"); if (chosen) { setGame(chosen); if (session) await work.run("inspect_authoring_workspace", { ...context, gta_path: chosen }); } }}>Choose native decoder installation</button>
      <span>{game || "Select matching GTA keys for encrypted resources"}</span>
      {session && <button className="quiet-button" disabled={dirty || work.locked} onClick={() => void work.run("inspect_authoring_workspace", context)}>Refresh native workspace</button>}
      {session && <button className="quiet-button" disabled={dirty || work.locked} onClick={() => { setSession(null); setText(""); }}>Close native resource</button>}</div>
    <AuthoringFeedback work={work} />
    {!session && <div className="native-empty"><h4>Open a native resource or exported workspace</h4><p>Choose the source edition before opening a loose file. Archive handoffs retain their detected edition and exact member identity.</p><p>Editable copies contain an immutable original, XML, and exported dependencies. AWC copies also expose supported WAV tracks for playback and export.</p></div>}
    {session && <><div className="source-strip"><strong>{session.workspace ? "Editable native copy" : "Read-only native source"} · {session.edition}</strong><span>{session.source}</span></div>
      {session.archive_binding && <p>Archive member: {session.archive_binding.entry_id} · Source SHA-256: {session.archive_binding.outer_archive_sha256}</p>}
      {session.warnings.length > 0 && <ul>{session.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>}
      {session.asset_validation && <AssetValidationReport report={session.asset_validation} stale={dirty} locked={work.locked} onExport={()=>void destination("export_validation")}/>}
      {session.relationships && <NativeRelationships graph={session.relationships} locked={locked} onSearch={onSearchReference ? query => onSearchReference(query, session.gta_path || "") : undefined} />}
      {session.collision && <NativeCollisionView packet={session.collision} draftDirty={dirty} />}
      {session.animation && <NativeAnimationView packet={session.animation} active={active} locked={work.locked} draftDirty={dirty} model={session.animation_model}
        onSelect={animation => inspectAnimation({...modelDocument, animation})}
        onChooseModel={async () => { if (dirty || work.locked) return; const chosen = await work.choose("binary_source"); if(chosen) inspectAnimation({model_xml:chosen}); }}
        onBindModel={selection=>inspectAnimation({...modelDocument, ...(selection.drawable!==undefined ? {lod:undefined} : {}), ...selection})}
        onChooseSkeleton={async()=>{if(dirty || work.locked || !session.animation_model?.source) return; const chosen=await work.choose("binary_source"); if(chosen) inspectAnimation({...modelDocument, skeleton_xml:chosen, skeleton_drawable:undefined});}}
        onClearSkeleton={()=>inspectAnimation({...modelDocument, skeleton_xml:undefined, skeleton_drawable:undefined})}
        onClearModel={()=>inspectAnimation({})} />}
      {!session.workspace ? <><label>New workspace folder name<input aria-label="Native workspace name" value={name} disabled={work.locked} onChange={e => setName(e.target.value)} /></label><button className="quiet-button" disabled={work.locked || !name} onClick={() => void destination("export")}>Review native workspace export</button>
        <details><summary>Decoded resource metadata</summary><pre>{JSON.stringify(session.metadata, null, 2)}</pre></details>
        <pre>{session.preview_chunks?.join("")}</pre>{session.preview_truncated && <p>Preview truncated; export the complete XML to a workspace.</p>}</> : <>
        <p>XML: {session.xml_path} · {session.xml_size?.toLocaleString()} bytes</p>
        {/\.ytd$/i.test(session.name) && <button className="quiet-button" disabled={locked} onClick={() => setTextureMode(true)}>Edit dictionary textures</button>}
        {session.xml_editable && <><CodeEditor value={text} language="xml" lineEnding={text.includes("\r\n") ? "CRLF" : "LF"} locked={work.locked} onChange={setText} />
          <button className="quiet-button" disabled={!dirty || work.locked} onClick={() => void review("save_xml", { document: { language: "xml", chunks: Array.from({ length: Math.ceil(text.length / 8192) }, (_, i) => text.slice(i * 8192, (i + 1) * 8192)) } })}>Review native XML save</button>
          <button className="quiet-button" disabled={!dirty || work.locked} onClick={() => setText(session.xml_chunks.join(""))}>Discard native XML draft</button></>}
        <h4>Exported dependencies ({session.dependencies.length})</h4><select aria-label="Native dependency" value={dependency} disabled={work.locked || dirty} onChange={e => { setDependency(e.target.value); setChannel(""); player.current?.pause(); }}><option value="">Choose a dependency</option>{session.dependencies.map(item => <option key={item.path} value={item.path}>{item.path} · {item.size.toLocaleString()} bytes · {item.kind}</option>)}</select>
        <button className="quiet-button" disabled={!dependency || work.locked || dirty} onClick={() => void destination("export_dependency")}>Review dependency export</button>
        {/\.wav$/i.test(dependency) && <div><label>Audio channel<select aria-label="Audio channel" value={channel} disabled={work.locked || dirty} onChange={e => setChannel(e.target.value)}><option value="">All channels</option>{session.audio_dependency === dependency && Array.from({ length: session.audio?.channels || 0 }, (_, index) => <option key={index} value={index}>Channel {index + 1}</option>)}</select></label>
          <button className="quiet-button" disabled={work.locked || dirty} onClick={() => void work.run("inspect_authoring_workspace", { ...context, document: { dependency, ...(channel === "" ? {} : { channel: Number(channel) }) } })}>Load audio preview</button></div>}
        {session.audio && <div><p>{session.audio_dependency} · {session.audio.channels} channels · {session.audio.sample_rate} Hz · {session.audio.duration_seconds.toFixed(2)} seconds</p>
          <audio ref={player} controls preload="metadata" src={convertFileSrc(session.audio.path)} aria-label="Native audio playback" onError={() => work.setError("This WAV could not be played by the webview. Try an individual channel or export it for external playback.")} /></div>}
        <p>WAV files are exported with AWC workspaces when supported by the source codec. XML and dependencies can also be edited externally; refresh before building.</p>
        <label>Native output filename<input aria-label="Native output filename" value={output} disabled={work.locked || dirty} onChange={e => setOutput(e.target.value)} /></label>
        <button className="quiet-button" disabled={dirty || work.locked || !output} onClick={() => void destination("build")}>Review verified native build</button>
        {session.archive_binding && <div><button className="quiet-button" disabled={dirty || work.locked} onClick={async () => { const folder = await work.choose("package_folder"); if (folder) setPlanScope(folder); }}>Choose native plan archive scope</button><span>{planScope || "For an external RPF, select its containing folder. Stock game archives cannot be authorized."}</span>
          {planScope && <button className="quiet-button" disabled={dirty || work.locked} onClick={() => setPlanScope("")}>Clear native plan scope</button>}
          <button className="quiet-button" disabled={dirty || work.locked} onClick={() => void destination("plan_replacement")}>Review native RPF replacement plan</button></div>}
        {work.lastResult?.plan && <div><p>Replacement plan: {String(work.lastResult.plan)} · {String(work.lastResult.plan_status)}. No archive has been changed.</p>
          <pre>{JSON.stringify(work.lastResult.plan_blocking_reasons, null, 2)}</pre>{onOpenPlan && <button className="quiet-button" disabled={dirty || work.locked} onClick={() => onOpenPlan(String(work.lastResult?.plan))}>Open native plan in Execute & restore</button>}</div>}
        {work.lastResult?.validation && <details open><summary>Native rebuild validation</summary><pre>{JSON.stringify(work.lastResult.validation, null, 2)}</pre></details>}
        {(work.review?.value.build || work.lastResult?.provenance) ? <details><summary>Native build identity</summary>
          <p>Exact SDK/helper and output identities are recorded in the build receipt. This is not a signature or in-game acceptance.</p>
          <pre style={{maxHeight:280,overflow:"auto"}}>{JSON.stringify(work.review?.value.build || work.lastResult?.provenance, null, 2)}</pre>
        </details> : null}
      </>}
    </>}
  </section>;
}
