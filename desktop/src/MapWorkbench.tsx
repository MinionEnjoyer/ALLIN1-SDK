import { useEffect, useState } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";

type RecordValue = Record<string, unknown>;
export interface MapDocument extends RecordValue {
  id: string; package_id: string; name: string; version: string; schema_version: number;
  editions: string[]; streaming: RecordValue; levels: RecordValue[]; portals: RecordValue[]; garages: RecordValue[];
}
const position = (heading = 0) => ({ x: 0, y: 0, z: 0, heading });
export const newMapTemplate = (): MapDocument => ({
  schema_version: 1, id: "custom.map", package_id: "custom.map", name: "Custom Map", version: "1.0.0", editions: ["legacy", "enhanced"],
  streaming: { pack_name: "custom_map", mode: "ipl", content_group: null, ipls: ["custom_map"], activation_radius: 300, release_radius: 500, keep_resident: false },
  levels: [{ id: "interior", name: "Custom Interior", center: position(), ipls: [] }],
  portals: [{ id: "main.entrance", name: "Main Entrance", mode: "both", from: { level: "world", position: position() }, to: { level: "interior", position: position(180) }, radius: 3, one_way: false }],
  garages: [{ id: "main.garage", name: "Main Garage", level_id: "interior", entrance_portal_id: "main.entrance", capacity: 10, vehicle_types: ["land"],
    slots: [{ id: "slot.01", position: { ...position(180), y: 5 }, vehicle_types: ["land"] }], rules: { allow_store: true, allow_retrieve: true, save_policy: "story_save_only" } }],
});
type Selection = { family: "identity" | "streaming" | "levels" | "portals" | "garages"; index?: number; slot?: number };
const identityFields = ["schema_version", "id", "package_id", "name", "version", "editions"];
const format = (value: unknown) => JSON.stringify(value, null, 2);
function selectedRecord(document: MapDocument, selection: Selection): RecordValue {
  if (selection.family === "identity") return Object.fromEntries(identityFields.map(key => [key, document[key]]));
  if (selection.family === "streaming") return document.streaming;
  const row = document[selection.family][selection.index!];
  return selection.slot === undefined ? row : (row.slots as RecordValue[])[selection.slot];
}
interface MapSession extends WorkspaceResult { descriptor: string; document: MapDocument; detection?: Record<string, unknown>; inventory?: { assets: { path: string; role: string }[]; findings: { message: string }[]; summary: { errors: number; warnings: number } } }

export default function MapWorkbench({ client, onDirtyChange }: { client: DesktopClient; onDirtyChange: (guarded: boolean) => void }) {
  const [session, setSession] = useState<MapSession | null>(null), [document, setDocument] = useState<MapDocument | null>(null);
  const [selection, setSelection] = useState<Selection>({ family: "identity" }), [editor, setEditor] = useState("");
  const [game, setGame] = useState("");
  const [source, setSource] = useState(""), [edition, setEdition] = useState("enhanced");
  const [filename, setFilename] = useState("maps.json"), [packageName, setPackageName] = useState("custom-map-enhanced");
  const work = useAuthoringWorkspace(client, "maps", value => {
    const s = value as MapSession;
    if (!s.document || !Array.isArray(s.document.levels) || !Array.isArray(s.document.portals) || !Array.isArray(s.document.garages) || typeof s.descriptor !== "string") throw new Error("Invalid map topology evidence");
    setSession(s); setDocument(s.document); setSelection({ family: "identity" }); setEditor(format(selectedRecord(s.document, { family: "identity" })));
  });
  const recordDirty = !!document && editor !== format(selectedRecord(document, selection));
  const documentDirty = !!document && (!session || format(document) !== format(session.document));
  const dirty = recordDirty || documentDirty;
  useEffect(() => { onDirtyChange(dirty || work.locked); }, [dirty, work.locked, onDirtyChange]);
  const select = (next: Selection, doc = document) => { if (!doc) return; setSelection(next); setEditor(format(selectedRecord(doc, next))); };
  const changeDocument = (next: MapDocument, nextSelection = selection) => { setDocument(next); select(nextSelection, next); };
  const template = () => { setSession(null); setSource(""); changeDocument(newMapTemplate(), { family: "identity" }); };
  const open = async () => { const chosen = await work.choose("map_descriptor"); if (chosen) { setSource(""); await work.run("inspect_authoring_workspace", { descriptor: chosen }); } };
  const inspectSource = async () => { const chosen = await work.choose("map_source"); if (chosen && session) { setSource(chosen); await work.run("inspect_authoring_workspace", { descriptor: session.descriptor, source: chosen, ...(game ? { gta_path: game } : {}) }); } };
  const commitRecord = () => {
    if (!document) return;
    try {
      const value: unknown = JSON.parse(editor);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Enter a JSON object for the selected section");
      const next = structuredClone(document), row = value as RecordValue;
      if (selection.family === "identity") {
        if (Object.keys(row).some(key => !identityFields.includes(key)) || identityFields.some(key => !(key in row))) throw new Error("Keep all six identity fields; topology belongs in its own section");
        Object.assign(next, row);
      } else if (selection.family === "streaming") next.streaming = row;
      else if (selection.slot !== undefined) (next.garages[selection.index!].slots as RecordValue[])[selection.slot] = row;
      else next[selection.family][selection.index!] = row;
      changeDocument(next); work.setError("");
    } catch (reason) { work.setError(String(reason)); }
  };
  const add = (family: "levels" | "portals" | "garages" | "slots") => {
    if (!document) return;
    const next = structuredClone(document), level = next.levels[0], portal = next.portals.find(p => (p.to as RecordValue)?.level === level?.id || (p.from as RecordValue)?.level === level?.id);
    const unique = (rows: RecordValue[], prefix: string) => { let n = 1; while (rows.some(r => r.id === `${prefix}.${n}`)) n++; return `${prefix}.${n}`; };
    if (family === "slots") {
      if (selection.family !== "garages" || selection.index === undefined) { work.setError("Select a garage before adding a slot."); return; }
      const garage = next.garages[selection.index], slots = garage.slots as RecordValue[];
      if (!Array.isArray(slots) || slots.length >= Number(garage.capacity)) { work.setError("Increase the garage capacity before adding another slot."); return; }
      slots.push({ id: unique(slots, "slot"), position: position(), vehicle_types: garage.vehicle_types });
      changeDocument(next, { family: "garages", index: selection.index, slot: slots.length - 1 }); return;
    }
    if ((family === "portals" && !level) || (family === "garages" && (!level || !portal))) { work.setError("Add a level and a connecting portal before adding a garage."); return; }
    const rows = next[family], id = unique(rows, family.slice(0, -1));
    rows.push(family === "levels" ? { id, name: "New Level", center: position(), ipls: [] }
      : family === "portals" ? { id, name: "New Entrance", mode: "both", from: { level: "world", position: position() }, to: { level: level.id, position: position(180) }, radius: 3, one_way: false }
        : { id, name: "New Garage", level_id: level.id, entrance_portal_id: portal!.id, capacity: 10, vehicle_types: ["land"], slots: [{ id: "slot.01", position: position(), vehicle_types: ["land"] }], rules: { allow_store: true, allow_retrieve: true, save_policy: "story_save_only" } });
    changeDocument(next, { family, index: rows.length - 1 });
  };
  const remove = () => {
    if (!document || selection.index === undefined) return;
    const next = structuredClone(document);
    if (selection.slot !== undefined) (next.garages[selection.index].slots as RecordValue[]).splice(selection.slot, 1);
    else (next[selection.family] as RecordValue[]).splice(selection.index, 1);
    changeDocument(next, { family: "identity" });
  };
  const review = async (action: "create" | "save" | "build") => {
    let destination: string | undefined;
    if (action !== "save") {
      const parent = await work.choose("authoring_parent"); if (!parent) return;
      destination = `${parent.replace(/[\\/]$/, "")}/${action === "create" ? filename : packageName}`;
    }
    await work.run("review_workspace_action", { action, document,
      ...(session ? { descriptor: session.descriptor, expected_state_sha256: session.state_sha256 } : {}),
      ...(destination ? { destination } : {}), ...(action === "build" ? { source, edition, ...(game ? { gta_path: game } : {}) } : {}) });
  };
  return <section className="offline-workbench" aria-label="Map Workbench"><div className="offline-toolbar"><div><h3>Map Workbench</h3><p>Streaming, levels, entrances, garages, and slots—validated by the same map contract as Tkinter.</p></div>
    <div className="heading-actions"><button className="primary-button" disabled={work.locked || dirty} onClick={() => void open()}>Open map descriptor</button>
      <button className="quiet-button" disabled={work.locked || dirty} onClick={template}>New map project</button></div></div>
    <div className="source-strip"><strong>{dirty ? "Unsaved map draft" : "Map descriptor"}</strong><span className="source-path">{session?.descriptor || "No descriptor saved"}</span></div>
    <div className="heading-actions"><button className="quiet-button" disabled={work.locked || dirty} onClick={async () => { const selected = await work.choose("gta_folder"); if (selected) setGame(selected); }}>Decoder game folder</button>
      <span className="source-path">{game || "No decoder context selected"}</span>
      <button className="quiet-button" disabled={work.locked || dirty || !session || !game} onClick={() => void work.run("inspect_authoring_workspace", { descriptor: session!.descriptor, gta_path: game, detect_installed: true, ...(source ? { source } : {}) })}>Detect installed IPLs</button></div>
    <AuthoringFeedback work={work} />
    {session?.detection && <details className="map-detection"><summary>Installed placement evidence (read-only)</summary><pre>{format(session.detection)}</pre></details>}
    <div className="offline-panes"><section><header><span className="pane-kicker">Project</span><h4>Topology</h4></header><div className="offline-pane-body">
      {!document ? <p>Open a descriptor or create a map template.</p> : <><div className="map-topology">
        {(["identity", "streaming"] as const).map(family => <button className="quiet-button" key={family} aria-pressed={selection.family === family} disabled={work.locked || recordDirty} onClick={() => select({ family })}>{family === "identity" ? "Identity" : "Streaming"}</button>)}
        {(["levels", "portals", "garages"] as const).map(family => <div key={family}><h5>{family}</h5>{document[family].map((row, index) => <div key={index}>
          <button className="quiet-button" disabled={work.locked || recordDirty} aria-pressed={selection.family === family && selection.index === index && selection.slot === undefined} onClick={() => select({ family, index })}>{String(row.name || row.id || `Record ${index + 1}`)}</button>
          {family === "garages" && Array.isArray(row.slots) && (row.slots as RecordValue[]).map((slot, i) => <button className="text-action" key={i} disabled={work.locked || recordDirty} aria-pressed={selection.family === family && selection.index === index && selection.slot === i} onClick={() => select({ family, index, slot: i })}>↳ {String(slot.id)}</button>)}
        </div>)}</div>)}
      </div><fieldset disabled={work.locked || recordDirty}><div className="heading-actions">{(["levels", "portals", "garages", "slots"] as const).map(f => <button key={f} className="quiet-button" onClick={() => add(f)}>Add {f.slice(0, -1)}</button>)}</div></fieldset></>}
    </div></section>
    <section><header><span className="pane-kicker">Authoring</span><h4>Selected section</h4></header><div className="offline-pane-body">
      <label>Section JSON<textarea className="map-document-editor" value={editor} disabled={!document || work.locked} spellCheck={false} maxLength={100000} onChange={e => setEditor(e.target.value)} /></label>
      <p>Section edits stay in the draft. Review validates every reference and field before saving.</p><div className="heading-actions">
        <button className="primary-button" disabled={work.locked || !recordDirty} onClick={commitRecord}>Apply section to draft</button>
        <button className="quiet-button" disabled={work.locked || !recordDirty} onClick={() => select(selection)}>Revert section</button>
        <button className="quiet-button" disabled={work.locked || recordDirty || selection.index === undefined} onClick={remove}>Remove selected record</button></div>
      {document && <><label>Descriptor filename<input value={filename} disabled={work.locked || !!session} onChange={e => setFilename(e.target.value)} /></label>
        <button className="primary-button" disabled={work.locked || recordDirty || !documentDirty} onClick={() => void review(session ? "save" : "create")}>Review map save</button>
        <button className="quiet-button" disabled={work.locked || !dirty} onClick={() => { if (session) changeDocument(session.document, { family: "identity" }); else { setDocument(null); setEditor(""); } }}>Discard map draft</button></>}
    </div></section>
    <section><header><span className="pane-kicker">Packaging</span><h4>Assets & output</h4></header><div className="offline-pane-body">
      <button className="quiet-button" disabled={!session || work.locked || dirty} onClick={() => void inspectSource()}>Inspect map source folder</button><p className="source-path">{source || "No asset source selected"}</p>
      {session?.inventory && <><p>{session.inventory.summary.errors} errors · {session.inventory.summary.warnings} warnings</p><ul className="map-asset-list">{session.inventory.assets.map((asset, i) => <li key={i}>{asset.path} · {asset.role}</li>)}</ul>
        {session.inventory.findings.map((f, i) => <p key={i}>{f.message}</p>)}</>}
      <fieldset disabled={!session || work.locked || dirty}><label>Target edition<select value={edition} onChange={e => setEdition(e.target.value)}><option value="enhanced">Enhanced</option><option value="legacy">Legacy</option></select></label>
        <label>Package folder name<input value={packageName} onChange={e => setPackageName(e.target.value)} maxLength={100} /></label>
        <button className="primary-button" disabled={!source} onClick={() => void review("build")}>Review map package</button><p>Creates a new ALLIN1 package with edition-specific payloads. It does not install or activate the map.</p></fieldset>
    </div></section></div>
  </section>;
}
