import { useEffect, useRef, useState } from "react";
import type { DesktopClient, VehicleAuthoringSession } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";

export default function VehicleIdentityEditor({ client, session, disabled, onGuardChange, onSaved }: {
  client: DesktopClient; session: VehicleAuthoringSession; disabled: boolean;
  onGuardChange: (guarded: boolean) => void; onSaved: (value: VehicleAuthoringSession) => void;
}) {
  const currentModel = session.selected_model ?? "";
  const currentHandling = session.project.models.find(model => model.model === currentModel)?.handling_id ?? "";
  const [model, setModel] = useState(currentModel), [handling, setHandling] = useState(currentHandling);
  const [snapshot, setSnapshot] = useState<WorkspaceResult | null>(null);
  const work = useAuthoringWorkspace(client, "vehicle_identity", value => {
    if (value.workspace !== session.workspace || value.model !== currentModel || value.revision !== session.revision)
      throw new Error("Vehicle revision changed. Reopen the editable copy before migrating its identity.");
    setSnapshot(value);
  });
  const dirty = model !== currentModel || handling !== currentHandling;
  useEffect(() => { onGuardChange(dirty || work.locked); }, [dirty, work.locked, onGuardChange]);
  const saved = useRef(onSaved); saved.current = onSaved;
  useEffect(() => {
    if (!work.lastResult) return;
    const value = work.lastResult.vehicle_session as VehicleAuthoringSession;
    if (value?.kind !== "vehicle_authoring_session" || value.workspace !== session.workspace || value.revision !== session.revision + 1 || value.selected_model !== model || value.game_write_performed !== false) {
      work.setError("Invalid identity migration receipt; reopen the workspace to inspect its state."); return;
    }
    saved.current(value);
  }, [work.lastResult]);
  const locked = disabled || work.locked;
  return <div className="offline-workbench" aria-label="Vehicle identity editor">
    <div className="vehicle-authoring-intro"><strong>Model and handling identity</strong><span>Renames matching metadata references and streamed model/texture files in this editable copy. Shared handling definitions cannot be renamed implicitly.</span></div>
    <button className="quiet-button" disabled={locked} onClick={() => void work.run("inspect_authoring_workspace", { workspace: session.workspace, model: currentModel })}>Inspect identity migration</button>
    <label>New model identifier<input aria-label="New vehicle model identifier" value={model} disabled={locked || !snapshot} onChange={e => setModel(e.target.value)} /></label>
    <label>New handling identifier<input aria-label="New vehicle handling identifier" value={handling} disabled={locked || !snapshot} onChange={e => setHandling(e.target.value)} /></label>
    <div className="heading-actions"><button className="quiet-button" disabled={locked || !dirty} onClick={() => { setModel(currentModel); setHandling(currentHandling); }}>Reset identity</button>
      <button className="primary-button" disabled={locked || !dirty || !snapshot} onClick={() => void work.run("review_workspace_action", { action: "migrate", workspace: session.workspace, model: currentModel, new_model: model, new_handling: handling, expected_revision: session.revision, expected_state_sha256: snapshot?.state_sha256 })}>Review identity migration</button></div>
    <AuthoringFeedback work={work} />
  </div>;
}
