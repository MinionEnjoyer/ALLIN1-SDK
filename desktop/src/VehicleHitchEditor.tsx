import { useEffect, useRef, useState } from "react";
import type { DesktopClient, VehicleAuthoringSession } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import SliderField from "./SliderField";

type Point = { id: "front" | "rear"; mode: "native" | "physical"; bone: string; position: number[]; rotation: number[];
  coupler_bone: string; coupler_offset: number[]; compatible_models: string[]; connect_distance: number; break_force: number };
type Profile = { schema_version: 1; vehicle_model: string; points: Point[] };
export const newHitch = (id: Point["id"]): Point => ({ id, mode: id === "rear" ? "native" : "physical",
  bone: id === "rear" ? "attach_female" : "chassis", position: [0, 0, 0], rotation: [0, 0, 0],
  coupler_bone: "attach_male", coupler_offset: [0, 0, 0], compatible_models: [], connect_distance: 1, break_force: 10000 });

export default function VehicleHitchEditor({ client, session, disabled, onGuardChange, onSaved }: {
  client: DesktopClient; session: VehicleAuthoringSession; disabled: boolean;
  onGuardChange: (guarded: boolean) => void; onSaved: (value: VehicleAuthoringSession) => void;
}) {
  const [snapshot, setSnapshot] = useState<WorkspaceResult | null>(null);
  const [draft, setDraft] = useState<Profile | null>(null);
  const [modelsText, setModelsText] = useState<Record<string, string>>({});
  const work = useAuthoringWorkspace(client, "vehicle_hitches", value => {
    if (value.workspace !== session.workspace || value.model !== session.selected_model || value.revision !== session.revision)
      throw new Error("Vehicle changed. Reopen the hitch editor.");
    setSnapshot(value); setDraft(structuredClone(value.document as Profile));
    setModelsText(Object.fromEntries((value.document as Profile).points.map(p => [p.id, p.compatible_models.join(", ")])));
  });
  useEffect(() => {
    // StrictMode rehearses mount/cleanup. Do not start a cancellable backend job
    // for that discarded mount and leave the workspace hook latched as busy.
    let active = true;
    queueMicrotask(() => { if (active) void work.run("inspect_authoring_workspace", { workspace: session.workspace, model: session.selected_model }); });
    return () => { active = false; };
  }, []);
  const dirty = !!draft && JSON.stringify(draft) !== JSON.stringify(snapshot?.document);
  useEffect(() => { onGuardChange(dirty || work.locked); }, [dirty, work.locked, onGuardChange]);
  useEffect(() => () => onGuardChange(false), [onGuardChange]);
  const saved = useRef(onSaved); saved.current = onSaved;
  useEffect(() => {
    if (!work.lastResult) return;
    const value = work.lastResult.vehicle_session as VehicleAuthoringSession;
    if (value?.kind !== "vehicle_authoring_session" || value.workspace !== session.workspace || value.revision !== session.revision + 1
      || value.selected_model !== session.selected_model || value.game_write_performed !== false) {
      work.setError("Invalid hitch save receipt; reopen the workspace."); return;
    }
    saved.current(value);
  }, [work.lastResult]);
  const locked = disabled || work.locked;
  const update = (id: Point["id"], values: Partial<Point>) => setDraft(old => old && ({ ...old, points: old.points.map(p => p.id === id ? { ...p, ...values } : p) }));
  const reset = () => {
    setDraft(structuredClone(snapshot!.document as Profile));
    setModelsText(Object.fromEntries((snapshot!.document as Profile).points.map(p => [p.id, p.compatible_models.join(", ")])));
  };
  return <div className="offline-workbench" aria-label="Vehicle hitch editor">
    <div className="vehicle-authoring-intro"><strong>Trailer hitches</strong><span>One configured front and rear slot; one live connection at a time. Exported to the GBAY vehicle catalog. This does not move or create YFT bones.</span></div>
    <button className="quiet-button" disabled={locked || dirty} onClick={() => void work.run("inspect_authoring_workspace", { workspace: session.workspace, model: session.selected_model })}>Reload hitches</button>
    {draft && <>
      <div className="heading-actions">{(["front", "rear"] as const).map(id => <button key={id} className="quiet-button" disabled={locked || draft.points.some(p => p.id === id)} onClick={() => setDraft({ ...draft, points: [...draft.points, newHitch(id)] })}>Add {id} hitch</button>)}</div>
      {draft.points.map(point => <fieldset key={point.id} disabled={locked}>
        <legend>{point.id === "front" ? "Front" : "Rear"} hitch</legend>
        <label>Connection mode<select aria-label={`${point.id} connection mode`} value={point.mode} onChange={e => update(point.id, e.target.value === "native"
          ? { mode: "native", bone: "attach_female", coupler_bone: "attach_male", position: [0,0,0], coupler_offset: [0,0,0], rotation: [0,0,0] }
          : { mode: "physical" })}><option value="native">Native towing — authored hitch bones</option><option value="physical">Experimental physical joint — custom placement</option></select></label>
        <p>{point.mode === "physical" ? "Experimental: confirm in GBAY before connecting. Collision and handling must be tested in game." : "GTA chooses the authored hitch. Offset and rotation overrides are not supported in native mode."}</p>
        <label>Vehicle bone<input aria-label={`${point.id} vehicle bone`} disabled={locked || point.mode === "native"} value={point.bone} maxLength={64} onChange={e => update(point.id, { bone: e.target.value.toLowerCase() })} /></label>
        <label>Trailer coupler bone<input aria-label={`${point.id} coupler bone`} disabled={locked || point.mode === "native"} value={point.coupler_bone} maxLength={64} onChange={e => update(point.id, { coupler_bone: e.target.value.toLowerCase() })} /></label>
        <label>Compatible trailer model names<textarea aria-label={`${point.id} compatible trailers`} value={modelsText[point.id] ?? ""} maxLength={4200} placeholder="trailers, trailers2" onChange={e => {
          setModelsText(old => ({ ...old, [point.id]: e.target.value }));
          update(point.id, { compatible_models: e.target.value.toLowerCase().split(/[\s,]+/).filter(Boolean) });
        }} /></label>
        {point.mode === "physical" && <>
          <p>Offsets use the selected bone's local axes, in metres; rotation is in degrees. For an unrotated chassis, X is right, Y forward and Z up.</p>
          {(["position", "coupler_offset", "rotation"] as const).map(field => <div key={field}><strong>{field.replaceAll("_", " ")}</strong>{["X", "Y", "Z"].map((axis, i) => <SliderField numeric key={axis}
            id={`hitch-${point.id}-${field}-${axis}`} label={`${point.id} ${field} ${axis}`} value={point[field][i]} min={field === "rotation" ? -180 : -5} max={field === "rotation" ? 180 : 5}
            hardMin={field === "rotation" ? -180 : field === "position" ? -20 : -5} hardMax={field === "rotation" ? 180 : field === "position" ? 20 : 5} step={field === "rotation" ? 1 : .01}
            onChange={v => update(point.id, { [field]: point[field].map((n, j) => i === j ? v : n) })} disabled={locked} />)}</div>)}
          <svg viewBox="0 0 240 140" role="img" aria-label={`${point.id} bone-local hitch offset schematic, not model geometry`} style={{ width: "100%", maxHeight: 180 }}>
            <path d="M20 70H220M120 10V130" stroke="currentColor" opacity=".3"/><circle cx="120" cy="70" r="4" fill="currentColor"/>
            <line x1="120" y1="70" x2={120 + point.position[0]*5} y2={70 - point.position[1]*3} stroke="#efb64f" />
            <circle cx={120 + point.position[0]*5} cy={70 - point.position[1]*3} r="5" fill="#efb64f"/><text x="4" y="136" fill="currentColor" fontSize="10">Top view · bone-local offset only · Y forward ↑</text>
          </svg>
          <SliderField numeric id={`hitch-${point.id}-force`} label="Joint break force (experimental)" min={1000} max={100000} step={1000} value={point.break_force} onChange={v => update(point.id, { break_force: v })} disabled={locked}/>
        </>}
        <SliderField numeric id={`hitch-${point.id}-distance`} label="Maximum coupling distance (m)" min={.25} max={2} step={.05} value={point.connect_distance} onChange={v => update(point.id, { connect_distance: v })} disabled={locked}/>
        <button className="quiet-button" onClick={() => setDraft({ ...draft, points: draft.points.filter(p => p.id !== point.id) })}>Remove {point.id} hitch</button>
      </fieldset>)}
      <div className="heading-actions"><button className="quiet-button" disabled={locked || !dirty} onClick={reset}>Reset hitches</button>
        <button className="primary-button" disabled={locked || !dirty || !snapshot} onClick={() => void work.run("review_workspace_action", { action: "configure", workspace: session.workspace,
          model: session.selected_model, document: draft, expected_revision: session.revision, expected_state_sha256: snapshot?.state_sha256 })}>Review hitches</button></div>
    </>}
    <AuthoringFeedback work={work}/>
  </div>;
}
