import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope, TextureWorkspaceSession } from "./types";
import "./TextureExportPanel.css";

interface BatchReview {
  kind: string; action: string; batch_action: string; ready: boolean; review_sha256: string;
  workspace: string; state_sha256: string; warning: string;
  operations: { action: string; texture_name: string; source_image?: string; output_format?: string; mip_levels?: number }[];
  changes: { field: string; before: string; after: string }[];
}
function result<T>(message: Envelope): T {
  const payload = message.payload as Record<string, unknown>;
  if (message.operation === "error") throw new Error(String(payload.message ?? payload.error ?? "Texture batch failed"));
  if (!payload.result || typeof payload.result !== "object") throw new Error("Texture batch returned no structured result");
  return payload.result as T;
}
const samePath = (a: string, b: string) => a.replace(/\\/g, "/").toLowerCase() === b.replace(/\\/g, "/").toLowerCase();

export default function TextureBatchPanel({ client, session, locked, onGuardChange, onChanged }: {
  client: DesktopClient; session: TextureWorkspaceSession; locked: boolean;
  onGuardChange: (guarded: boolean) => void; onChanged: (value: TextureWorkspaceSession) => void;
}) {
  const [mode, setMode] = useState("convert"), [names, setNames] = useState<string[]>([]);
  const [format, setFormat] = useState("DXT5"), [mips, setMips] = useState("full");
  const [folder, setFolder] = useState(""), [policy, setPolicy] = useState("replace");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [review, setReview] = useState<{ value: BatchReview; payload: Record<string, unknown> } | null>(null);
  const generation = useRef(0), flight = useRef(false), job = useRef("");
  const disabled = locked || busy || !!review;
  const draft = !!names.length || !!folder;
  useEffect(() => { onGuardChange(busy || !!review || draft); }, [busy, review, draft, onGuardChange]);
  useEffect(() => {
    setNames([]); setFolder(""); setReview(null); setBusy(false); flight.current = false;
    return () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => {}); job.current = ""; };
  }, [session.state_sha256, client]);
  async function chooseFolder() {
    if (disabled || flight.current) return;
    const token = ++generation.current;
    flight.current = true; setBusy(true); setError("");
    try {
      const selected = await client.selectPath("texture_workspace_parent");
      if (token === generation.current && selected) setFolder(selected);
    } catch (reason) { if (token === generation.current) setError(String(reason)); }
    finally { if (token === generation.current) { flight.current = false; setBusy(false); } }
  }
  async function begin() {
    if (disabled || flight.current || (mode === "import" ? !folder : !names.length)) return;
    const token = ++generation.current;
    flight.current = true; setBusy(true); setError("");
    const payload = { workspace: session.workspace, expected_state_sha256: session.state_sha256, action: "batch", batch_action: mode,
      ...(mode === "import" ? { source_folder: folder, import_policy: policy } : { texture_names: names }),
      ...(mode === "convert" ? { output_format: format, mip_levels: mips === "full" ? "full" : Number(mips) } : {}) };
    let finished = false;
    try {
      const started = await client.startJob("review_texture_edit", payload, `texture-batch-${token}`, event => {
        if (!event.terminal || finished || token !== generation.current) return;
        finished = true; job.current = ""; flight.current = false; setBusy(false);
        try {
          const value = result<BatchReview>(event);
          if (value.kind !== "texture_edit_review" || value.action !== "batch" || value.batch_action !== mode || value.ready !== true
            || !/^[a-f0-9]{64}$/.test(value.review_sha256) || typeof value.workspace !== "string" || !samePath(value.workspace, session.workspace)
            || value.state_sha256 !== session.state_sha256 || !Array.isArray(value.operations) || value.operations.length < 1 || value.operations.length > 128
            || !Array.isArray(value.changes) || value.changes.length !== value.operations.length) throw new Error("Texture batch review evidence is incomplete or mismatched");
          setReview({ value, payload });
        } catch (reason) { setError(String(reason)); }
      });
      if (token !== generation.current) { if (!finished) void client.cancelJob(started.job_id).catch(() => {}); }
      else if (!finished) job.current = started.job_id;
    } catch (reason) { if (token === generation.current && !finished) { flight.current = false; setBusy(false); setError(String(reason)); } }
  }
  async function cancelJob() {
    const id = job.current; generation.current++; job.current = ""; flight.current = false; setBusy(false);
    if (id) try { await client.cancelJob(id); } catch (reason) { setError(String(reason)); }
  }
  async function apply() {
    if (!review || flight.current || locked) return;
    const token = generation.current;
    flight.current = true; setBusy(true); setError("");
    try {
      const response = await client.textureAuthoringAction("apply_texture_edit", { ...review.payload, review_sha256: review.value.review_sha256, authoring_confirmed: true });
      if (token !== generation.current) return;
      const value = result<TextureWorkspaceSession & { action: string; review_sha256: string; batch_count: number }>(response);
      if (value.kind !== "texture_workspace_session" || value.action !== "batch" || value.review_sha256 !== review.value.review_sha256
        || value.batch_count !== review.value.operations.length || !samePath(value.workspace, session.workspace) || !/^[a-f0-9]{64}$/.test(value.state_sha256)
        || !Array.isArray(value.textures) || value.game_write_performed !== false || value.package_write_performed !== false) throw new Error("Invalid batch completion receipt; refresh the workspace before retrying");
      setReview(null); setNames([]); setFolder(""); onChanged(value);
    } catch (reason) { if (token === generation.current) { setReview(null); setError(String(reason)); } }
    finally { if (token === generation.current) { flight.current = false; setBusy(false); } }
  }
  return <><details className="texture-export-panel">
    <summary>Bulk texture edits · {names.length} selected</summary>
    <p>Up to 128 textures per reviewed batch, with one undo point. Changes affect this exported workspace only.</p>
    <div className="texture-export-controls">
      <label>Batch action<select aria-label="Texture batch action" value={mode} disabled={disabled} onChange={event => { setMode(event.target.value); setNames([]); setFolder(""); }}><option value="convert">Convert selected</option><option value="remove">Remove selected</option><option value="import">Import image folder</option></select></label>
      {mode === "convert" && <><label>Format<select aria-label="Batch texture format" value={format} disabled={disabled} onChange={event => setFormat(event.target.value)}>{["RGBA8", "DXT1", "DXT3", "DXT5"].map(value => <option key={value}>{value}</option>)}</select></label><label>Mips<select aria-label="Batch texture mips" value={mips} disabled={disabled} onChange={event => setMips(event.target.value)}><option value="full">Full chain per texture</option><option value="1">Top mip only</option></select></label></>}
      {mode === "import" && <><label>Import policy<select aria-label="Texture import policy" value={policy} disabled={disabled} onChange={event => setPolicy(event.target.value)}><option value="replace">Replace existing names only</option><option value="add">Add new names only</option><option value="upsert">Replace existing and add new</option></select></label><button className="quiet-button" disabled={disabled} onClick={() => void chooseFolder()}>Choose image folder</button></>}
      <button className="primary-button" disabled={disabled || (mode === "import" ? !folder : !names.length)} onClick={() => void begin()}>Review texture batch</button>
      {draft && <button className="quiet-button" disabled={busy} onClick={() => { setNames([]); setFolder(""); setReview(null); }}>Discard batch draft</button>}
    </div>
    {mode === "import" ? <p>{folder || "Choose a flat folder of DDS/PNG/JPEG/BMP/TGA/WebP images."} Filename stems map to texture names. Duplicate names are rejected; review shows all matching files.</p> : <>
      <button className="quiet-button" disabled={disabled || session.textures.length > 128} onClick={() => setNames(session.textures.map(item => item.name))}>Select all for batch</button>
      <div className="texture-export-selection" role="group" aria-label="Textures to edit in batch">{session.textures.map(texture => <label key={texture.name}><input type="checkbox" checked={names.includes(texture.name)} disabled={disabled || (names.length >= 128 && !names.includes(texture.name))} onChange={event => setNames(current => event.target.checked ? [...current, texture.name] : current.filter(name => name !== texture.name))} />{texture.name}</label>)}</div>
    </>}
    {busy && !review && <button className="quiet-button" onClick={() => void cancelJob()}>Cancel batch review</button>}
  </details>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {review && <div className="confirmation-backdrop texture-export-confirmation"><section className="confirmation-dialog" role="dialog" aria-modal="true" aria-label="Review texture batch">
      <h2>Review {review.value.operations.length} texture changes</h2><p>{review.value.workspace}</p><p>{review.value.warning}</p>
      <div className="texture-export-selection">{review.value.changes.map(change => <p key={change.field}><strong>{change.field}</strong>: {change.before} → {change.after}</p>)}</div>
      <p>No source YTD, archive, or game files will be modified. Undo restores the complete batch.</p>
      <div className="confirmation-actions"><button className="quiet-button" disabled={busy} onClick={() => setReview(null)}>Back to batch</button><button className="primary-button" disabled={busy || locked} onClick={() => void apply()}>{busy ? "Applying batch…" : "Commit texture batch"}</button></div>
    </section></div>}
  </>;
}
