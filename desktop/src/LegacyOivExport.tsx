import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import { formatBytes } from "./tokenize";
import "./LegacyOivExport.css";

interface OivReview {
  kind: string; edition: string; destination: string; author: string; name: string;
  version: string; package_id: string; payload_member: string; payload_size: number;
  payload_sha256: string; review_sha256: string; members: string[];
  review_only: boolean; game_write_performed: boolean; file_write_performed: boolean;
}
interface OivResult { kind: string; archive: string; archive_size: number; archive_sha256: string; review_sha256: string; game_write_performed: boolean }
const SHA = /^[a-f0-9]{64}$/;
function completed<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message ?? message.payload.error ?? "OIV operation failed"));
  if (!message.terminal || !message.payload.result) throw new Error("Incomplete OIV response; review again before exporting.");
  return message.payload.result as T;
}

export default function LegacyOivExport({ client, source, gtaPath, edition, identity, disabled, onGuardChange }: {
  client: DesktopClient; source: string; gtaPath: string; edition: string;
  identity?: { package_id: string; name: string; version: string };
  disabled: boolean; onGuardChange: (guarded: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [author, setAuthor] = useState("");
  const [destination, setDestination] = useState("");
  const [phase, setPhase] = useState<"idle" | "choosing" | "reviewing" | "exporting">("idle");
  const [review, setReview] = useState<{ value: OivReview; payload: Record<string, unknown> } | null>(null);
  const [result, setResult] = useState<OivResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const job = useRef("");
  const inFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const busy = phase !== "idle";
  useEffect(() => { onGuardChange(open && !result); }, [open, result, onGuardChange]);
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => undefined); }, [client]);
  useEffect(() => { if (review) heading.current?.focus(); }, [review]);
  const resetReview = () => { setReview(null); setConfirmed(false); setError(""); setResult(null); };
  const choose = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setPhase("choosing"); setError("");
    const version = ++generation.current;
    try {
      const selected = await client.selectOivDestination(`${identity?.package_id ?? "legacy-vehicle"}.oiv`);
      if (generation.current === version && selected) { setDestination(selected); resetReview(); }
    } catch (reason) { if (generation.current === version) setError(String(reason)); }
    finally { if (generation.current === version) { inFlight.current = false; setPhase("idle"); } }
  };
  const inspect = async () => {
    if (inFlight.current || disabled || edition !== "legacy") return;
    inFlight.current = true; resetReview(); setPhase("reviewing");
    const version = ++generation.current;
    const payload = { source, ...(gtaPath ? { gta_path: gtaPath } : {}), edition, ...identity, author: author.trim(), destination };
    let finished = false;
    try {
      const started = await client.startJob("review_vehicle_oiv_export", payload, `oiv-${version}`, message => {
        if (generation.current !== version || !message.terminal) return;
        finished = true; job.current = ""; inFlight.current = false; setPhase("idle");
        try {
          const value = completed<OivReview>(message);
          if (value.kind !== "vehicle_oiv_export_review" || value.edition !== "legacy"
              || !SHA.test(value.review_sha256) || !SHA.test(value.payload_sha256)
              || typeof value.destination !== "string" || !value.destination || value.author !== payload.author
              || !Array.isArray(value.members) || value.members.length !== 2 || !value.members.every(v => typeof v === "string")
              || !Number.isSafeInteger(value.payload_size) || value.payload_size <= 0
              || value.review_only !== true || value.game_write_performed !== false || value.file_write_performed !== false) {
            throw new Error("Unexpected OIV review response; no export was authorized.");
          }
          setReview({ value, payload });
        } catch (reason) { setError(String(reason)); }
      });
      if (generation.current !== version) { if (!finished) void client.cancelJob(started.job_id).catch(() => undefined); return; }
      if (!finished) job.current = started.job_id;
    } catch (reason) {
      if (generation.current === version) { inFlight.current = false; setPhase("idle"); setError(String(reason)); }
    }
  };
  const cancel = () => {
    generation.current++; inFlight.current = false; setPhase("idle"); resetReview();
    if (job.current) void client.cancelJob(job.current).catch(reason => setError(String(reason)));
    job.current = "";
  };
  const write = async () => {
    if (!review || !confirmed || inFlight.current || disabled) return;
    inFlight.current = true; setPhase("exporting"); setError("");
    const version = ++generation.current;
    try {
      const value = completed<OivResult>(await client.applyVehicleOivExport({ ...review.payload, review_sha256: review.value.review_sha256, authoring_confirmed: true }));
      if (value.kind !== "vehicle_oiv_exported" || value.review_sha256 !== review.value.review_sha256
          || value.game_write_performed !== false || !SHA.test(value.archive_sha256)
          || value.archive !== review.value.destination || !Number.isSafeInteger(value.archive_size) || value.archive_size <= 0) {
        throw new Error("Export outcome could not be verified. Check the destination before starting a new review.");
      }
      if (generation.current === version) setResult(value);
    } catch (reason) { if (generation.current === version) setError(String(reason)); }
    finally { if (generation.current === version) { inFlight.current = false; setPhase("idle"); setReview(null); setConfirmed(false); } }
  };
  return <section className="oiv-export" aria-labelledby="oiv-title">
    <div className="oiv-heading"><div><h3 id="oiv-title">Legacy OIV export</h3><p>A standalone vehicle archive for OpenIV. Exporting does not install it.</p></div>
      {!open && <button className="quiet-button" disabled={disabled || edition !== "legacy"} onClick={() => { resetReview(); setOpen(true); }}>Set up OIV export</button>}
      {open && <button className="quiet-button" disabled={busy} onClick={() => { resetReview(); setOpen(false); }}>Close export</button>}
    </div>
    {edition !== "legacy" && <p className="oiv-note">Select a detected Legacy branch to export. Enhanced assets are not converted.</p>}
    {open && <>
      <p className="oiv-note">Includes DLC and dlclist registration only. GBAY listings, traffic preferences, ALLIN1 receipts, managed backups and rollback are not included.</p>
      {!result && !review && <div className="oiv-form">
        <div className="form-field"><label htmlFor="oiv-author">Package author</label><input id="oiv-author" value={author} maxLength={200} disabled={busy || !!review || disabled} onChange={event => { setAuthor(event.target.value); resetReview(); }} placeholder="Name credited in the OIV" /></div>
        <div className="form-field"><label htmlFor="oiv-destination">New OIV file</label><div className="input-action"><input id="oiv-destination" value={destination} readOnly placeholder="Choose a destination outside GTA and the source" title={destination} /><button disabled={busy || !!review || disabled} onClick={choose}>Choose file</button></div></div>
      </div>}
      {error && <p className="error-banner" role="alert">{error}</p>}
      {review && <div className="oiv-review">
        <h4 ref={heading} tabIndex={-1}>Review OIV export</h4>
        <dl><dt>Package</dt><dd>{review.value.name} · {review.value.version} ({review.value.package_id})</dd><dt>Author</dt><dd>{review.value.author}</dd><dt>Destination</dt><dd>{review.value.destination}</dd><dt>Payload</dt><dd>{review.value.payload_member} · {formatBytes(review.value.payload_size)}</dd><dt>Contents</dt><dd>{review.value.members.join(" · ")}</dd><dt>Payload SHA-256</dt><dd className="oiv-hash">{review.value.payload_sha256}</dd></dl>
        <label className="quick-import-check"><input type="checkbox" checked={confirmed} disabled={busy || disabled} onChange={event => setConfirmed(event.target.checked)} /><span>Create this new Legacy OIV. Existing files will not be replaced.</span></label>
        <p className="oiv-note">The source is checked again before export. Writing cannot be cancelled once it begins.</p>
      </div>}
      {result && <div className="oiv-review" role="status"><h4>Legacy OIV exported</h4><p>{result.archive} · {formatBytes(result.archive_size)}</p><p className="oiv-hash">Archive SHA-256: {result.archive_sha256}</p><p>GTA was not modified.</p></div>}
      {!result && <div className="oiv-actions">
        {phase === "reviewing" ? <><span role="status">Checking Legacy payload and export destination…</span><button className="quiet-button" onClick={cancel}>Cancel review</button></> : review ? <><button className="quiet-button" disabled={busy} onClick={resetReview}>Back to export settings</button><button className="primary-button" disabled={!confirmed || busy || disabled} onClick={write}>{phase === "exporting" ? "Writing OIV…" : "Export Legacy OIV"}</button></> : <button className="primary-button" disabled={busy || disabled || !author.trim() || !destination || edition !== "legacy"} onClick={inspect}>Review OIV export</button>}
      </div>}
    </>}
  </section>;
}
