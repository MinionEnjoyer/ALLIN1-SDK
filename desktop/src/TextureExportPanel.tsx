import { useEffect, useRef, useState } from "react";
import { formatBytes } from "./tokenize";
import type { DesktopClient, Envelope, TextureWorkspaceSession } from "./types";
import "./TextureExportPanel.css";

interface ExportReview {
  kind: "texture_export_review"; ready: boolean; review_sha256: string;
  destination: string; texture_count: number; source_bytes: number; format: string;
  warning: string; entries_preview: { texture: string; file: string }[]; preview_truncated: boolean;
}
interface ExportResult { kind: "texture_export_result"; texture_count: number; destination: string; receipt: string; receipt_sha256: string; }
function result<T>(message: Envelope): T {
  const body = message.payload as Record<string, unknown>;
  if (message.operation === "error") throw new Error(String(body.message ?? body.error ?? "Texture export failed"));
  if (!body.result || typeof body.result !== "object") throw new Error("Texture export returned no result");
  return body.result as T;
}

export default function TextureExportPanel({ client, session, gtaPath, locked, onGuardChange }: {
  client: DesktopClient; session: TextureWorkspaceSession; gtaPath: string;
  locked: boolean; onGuardChange: (guarded: boolean) => void;
}) {
  const [names, setNames] = useState<string[]>([]), [format, setFormat] = useState("dds");
  const [folderName, setFolderName] = useState("texture-export"), [busy, setBusy] = useState(false);
  const [review, setReview] = useState<{ value: ExportReview; payload: Record<string, unknown> } | null>(null);
  const [receipt, setReceipt] = useState<ExportResult | null>(null), [error, setError] = useState("");
  const generation = useRef(0), job = useRef(""), flight = useRef(false);
  const disabled = locked || busy || !!review;
  useEffect(() => { onGuardChange(busy || !!review); }, [busy, review, onGuardChange]);
  useEffect(() => {
    setNames([]); setReview(null); setReceipt(null); setError("");
    return () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => {}); };
  }, [session.state_sha256, client]);
  async function begin(mode: "all" | "selected") {
    if (disabled || flight.current || (mode === "selected" && !names.length)) return;
    flight.current = true; setBusy(true); setError(""); setReceipt(null);
    const current = ++generation.current;
    try {
      const parent = await client.selectPath("texture_workspace_parent");
      if (current !== generation.current) return;
      if (!parent) { flight.current = false; setBusy(false); return; }
      const payload = { workspace: session.workspace, expected_state_sha256: session.state_sha256,
        destination: `${parent.replace(/[\\/]$/, "")}/${folderName.trim()}`, format, mode,
        ...(mode === "selected" ? { texture_names: names } : {}), ...(gtaPath ? { gta_path: gtaPath } : {}) };
      let finished = false;
      const started = await client.startJob("review_texture_export", payload, `texture-export|${current}`, message => {
        if (!message.terminal || finished || current !== generation.current) return;
        finished = true; job.current = ""; flight.current = false; setBusy(false);
        try {
          const value = result<ExportReview>(message);
          if (value.kind !== "texture_export_review" || !value.ready || !/^[a-f0-9]{64}$/.test(value.review_sha256) || !Array.isArray(value.entries_preview)) throw new Error("Texture export review was not ready");
          setReview({ value, payload });
        } catch (reason) { setError(String(reason)); }
      });
      if (current !== generation.current) { if (!finished) void client.cancelJob(started.job_id).catch(() => {}); }
      else if (!finished) job.current = started.job_id;
    } catch (reason) {
      if (current === generation.current) { setError(String(reason)); flight.current = false; setBusy(false); }
    }
  }
  async function apply() {
    if (!review || flight.current || locked) return;
    flight.current = true; setBusy(true); setError("");
    const current = generation.current;
    try {
      const response = await client.textureAuthoringAction("apply_texture_export", { ...review.payload, review_sha256: review.value.review_sha256, authoring_confirmed: true });
      if (current !== generation.current) return;
      const exported = result<ExportResult>(response);
      if (exported.kind !== "texture_export_result" || !exported.receipt || !/^[a-f0-9]{64}$/.test(exported.receipt_sha256)) throw new Error("Texture export receipt is missing");
      setReceipt(exported); setReview(null);
    } catch (reason) { if (current === generation.current) setError(String(reason)); }
    finally { if (current === generation.current) { flight.current = false; setBusy(false); } }
  }
  async function cancel() {
    const id = job.current;
    generation.current++; job.current = ""; flight.current = false; setBusy(false);
    if (id) try { await client.cancelJob(id); } catch (reason) { setError(String(reason)); }
  }
  const validFolder = !!folderName.trim() && !/[\\/:*?"<>|]/.test(folderName) && ![".", ".."].includes(folderName.trim());
  return <><details className="texture-export-panel">
    <summary>Bulk texture export · {names.length} selected</summary>
    <p>DDS preserves every mip and original bytes. PNG exports the top mip only. Choose an output parent; a new folder is created after review.</p>
    <div className="texture-export-controls">
      <label>Export format<select aria-label="Texture export format" value={format} disabled={disabled} onChange={e => setFormat(e.target.value)}><option value="dds">DDS — original</option><option value="png">PNG — top mip</option></select></label>
      <label>New export folder<input aria-label="New texture export folder" value={folderName} disabled={disabled} onChange={e => setFolderName(e.target.value)} /></label>
      <button className="quiet-button" disabled={disabled || !names.length || !validFolder} onClick={() => void begin("selected")}>Review selected export</button>
      <button className="quiet-button" disabled={disabled || !session.textures.length || !validFolder} onClick={() => void begin("all")}>Review all {session.texture_count} textures</button>
    </div>
    <div className="texture-export-selection" role="group" aria-label="Textures to export">
      {session.textures.map(texture => <label key={texture.name}><input type="checkbox" disabled={disabled} checked={names.includes(texture.name)} onChange={e => setNames(current => e.target.checked ? [...current, texture.name] : current.filter(n => n !== texture.name))} />{texture.name}</label>)}
    </div>
    {!!names.length && <button className="quiet-button" disabled={disabled} onClick={() => setNames([])}>Clear export selection</button>}
    {busy && !review && <button className="quiet-button" onClick={() => void cancel()}>Cancel export review</button>}
    {error && <p role="alert" className="error-banner">{error}</p>}
    {receipt && <div role="status"><strong>Exported {receipt.texture_count} textures</strong><p>{receipt.destination}</p><p>Receipt: {receipt.receipt}</p><small>SHA-256: {receipt.receipt_sha256}</small></div>}
  </details>
    {review && <div className="confirmation-backdrop texture-export-confirmation"><section className="confirmation-dialog" role="dialog" aria-modal="true" aria-label="Review texture export">
      <h2>Export {review.value.texture_count} textures as {review.value.format.toUpperCase()}</h2>
      <p>{review.value.destination}</p><p>{formatBytes(review.value.source_bytes)} source data. {review.value.warning}</p>
      <div className="texture-export-selection">{review.value.entries_preview.map(entry => <p key={entry.texture}>{entry.texture} → {entry.file}</p>)}</div>
      {review.value.preview_truncated && <p>First 100 mappings shown; the receipt will list every exported file.</p>}
      <p>New output only. No workspace, archive, or game files are changed.</p>
      <div className="confirmation-actions"><button className="quiet-button" disabled={busy} onClick={() => setReview(null)}>Cancel</button><button className="primary-button" disabled={busy || locked} onClick={() => void apply()}>{busy ? "Exporting…" : "Export textures"}</button></div>
    </section></div>}
  </>;
}
