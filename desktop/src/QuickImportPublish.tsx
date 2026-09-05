import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import { formatBytes } from "./tokenize";
import "./LegacyOivExport.css";

export interface PublishReview {
  kind: "vehicle_package_publish_review";
  source_package: string; destination: string; package_id: string; name: string; version: string;
  edition: "legacy" | "enhanced"; total_bytes: number; traffic_opt_in: boolean;
  members: { path: string; size: number; sha256: string }[];
  vehicles: { model: string; name: string; price: number }[];
  review_sha256: string; review_only: boolean; game_write_performed: boolean; file_write_performed: boolean;
}
interface Published { kind: string; archive: string; archive_size: number; archive_sha256: string; review_sha256: string; file_write_performed: boolean; game_write_performed: boolean; upload_performed: boolean }
const SHA = /^[a-f0-9]{64}$/;
function resultFrom<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message ?? message.payload.error ?? "ZIP publication failed"));
  if (!message.terminal || !message.payload.result) throw new Error("Incomplete publication response; check the destination before reviewing again.");
  return message.payload.result as T;
}

export default function QuickImportPublish({ client, sourcePackage, gtaPath, disabled, onGuardChange }: {
  client: DesktopClient; sourcePackage: string; gtaPath: string; disabled: boolean; onGuardChange: (guarded: boolean) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "choosing" | "reviewing" | "publishing">("idle");
  const [review, setReview] = useState<{ value: PublishReview; payload: Record<string, unknown> } | null>(null);
  const [result, setResult] = useState<Published | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const job = useRef("");
  const inFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const busy = phase !== "idle";
  useEffect(() => { onGuardChange(busy || !!review); }, [busy, review, onGuardChange]);
  useEffect(() => { if (review) heading.current?.focus(); }, [review]);
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => undefined); }, [client]);

  const chooseAndReview = async () => {
    if (inFlight.current || disabled || !sourcePackage) return;
    const version = ++generation.current;
    inFlight.current = true; setPhase("choosing"); setReview(null); setResult(null); setConfirmed(false); setError("");
    let finished = false;
    try {
      const filename = `${sourcePackage.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "vehicle-package"}.zip`;
      const destination = await client.selectPackageZipDestination(filename);
      if (generation.current !== version) return;
      if (!destination) { inFlight.current = false; setPhase("idle"); return; }
      const payload = { source_package: sourcePackage, destination, ...(gtaPath ? { gta_path: gtaPath } : {}) };
      setPhase("reviewing");
      const started = await client.startJob("review_vehicle_package_publish", payload, `publish-${version}`, message => {
        if (generation.current !== version || !message.terminal) return;
        finished = true; job.current = ""; inFlight.current = false; setPhase("idle");
        try {
          const value = resultFrom<PublishReview>(message);
          if (value.kind !== "vehicle_package_publish_review" || !["legacy", "enhanced"].includes(value.edition)
              || !SHA.test(value.review_sha256) || typeof value.source_package !== "string" || !value.source_package
              || typeof value.destination !== "string" || !value.destination || typeof value.package_id !== "string"
              || !Array.isArray(value.members) || value.members.length !== 5
              || !value.members.every(row => typeof row?.path === "string" && SHA.test(row.sha256) && Number.isSafeInteger(row.size) && row.size >= 0)
              || !Array.isArray(value.vehicles) || !value.vehicles.length || !value.vehicles.every(row => typeof row?.model === "string" && typeof row.name === "string" && Number.isFinite(row.price))
              || !Number.isSafeInteger(value.total_bytes) || value.total_bytes <= 0 || typeof value.traffic_opt_in !== "boolean"
              || value.review_only !== true || value.file_write_performed !== false || value.game_write_performed !== false) {
            throw new Error("Unexpected ZIP review evidence; publication was not authorized.");
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
    generation.current++; inFlight.current = false; setPhase("idle"); setReview(null); setConfirmed(false);
    if (job.current) void client.cancelJob(job.current).catch(reason => setError(String(reason)));
    job.current = "";
  };
  const publish = async () => {
    if (!review || !confirmed || disabled || inFlight.current) return;
    const version = ++generation.current;
    inFlight.current = true; setPhase("publishing"); setError("");
    try {
      const value = resultFrom<Published>(await client.applyVehiclePackagePublish({ ...review.payload,
        review_sha256: review.value.review_sha256, authoring_confirmed: true }));
      if (value.kind !== "vehicle_package_published" || value.review_sha256 !== review.value.review_sha256
          || value.archive !== review.value.destination || !SHA.test(value.archive_sha256)
          || !Number.isSafeInteger(value.archive_size) || value.archive_size <= 0 || value.file_write_performed !== true
          || value.game_write_performed !== false || value.upload_performed !== false) {
        throw new Error("ZIP outcome could not be verified. Check the destination before reviewing again.");
      }
      if (generation.current === version) setResult(value);
    } catch (reason) { if (generation.current === version) setError(String(reason)); }
    finally { if (generation.current === version) { inFlight.current = false; setPhase("idle"); setReview(null); setConfirmed(false); } }
  };

  return <section className="oiv-export package-publish" aria-labelledby="publish-title">
    <div className="oiv-heading"><div><h3 id="publish-title">Shareable ALLIN1 package</h3><p>Publish the prepared package as a ZIP. Nothing is uploaded or installed.</p></div>
      {!review && phase !== "reviewing" && <button className="quiet-button" disabled={busy || disabled || !sourcePackage} onClick={chooseAndReview}>{phase === "choosing" ? "Choosing destination…" : "Review ZIP publication"}</button>}
    </div>
    {!sourcePackage && <p className="oiv-note">Prepare a package above to enable ZIP publication.</p>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {review && <div className="oiv-review">
      <h4 ref={heading} tabIndex={-1}>Review package ZIP</h4>
      <dl><dt>Package</dt><dd>{review.value.name} · {review.value.version} ({review.value.package_id})</dd>
        <dt>Edition</dt><dd>{review.value.edition === "legacy" ? "Legacy" : "Enhanced"} only</dd>
        <dt>Prepared source</dt><dd>{review.value.source_package}</dd><dt>Destination</dt><dd>{review.value.destination}</dd>
        <dt>Included</dt><dd>DLC, GBAY vehicle catalog, ALLIN1 content manifest and preparation evidence · {formatBytes(review.value.total_bytes)}</dd>
        <dt>Traffic preference</dt><dd>{review.value.traffic_opt_in ? "Opt-in included; controlled by the user at installation" : "Not enabled"}</dd>
      </dl>
      <table className="publish-table"><caption>Included GBAY listings</caption><thead><tr><th>Vehicle</th><th>Model</th><th>Price</th></tr></thead><tbody>{review.value.vehicles.map(row => <tr key={row.model}><td>{row.name}</td><td>{row.model}</td><td>{row.price.toLocaleString("en-US")}</td></tr>)}</tbody></table>
      <details className="publish-members"><summary>{review.value.members.length} verified archive files</summary>{review.value.members.map(row => <div key={row.path}><strong>{row.path}</strong><span>{formatBytes(row.size)}</span><code>{row.sha256}</code></div>)}</details>
      <p className="oiv-note">Only the prepared files are included—not unsaved drafts or extra folder contents. Files are checked again before writing. Publication cannot be cancelled once it starts.</p>
      <label className="quick-import-check"><input type="checkbox" checked={confirmed} disabled={busy || disabled} onChange={event => setConfirmed(event.target.checked)} /><span>Create this new package ZIP. Existing files will not be replaced.</span></label>
      <div className="oiv-actions"><button className="quiet-button" disabled={busy} onClick={cancel}>Back to package</button><button className="primary-button" disabled={busy || disabled || !confirmed} onClick={publish}>{phase === "publishing" ? "Publishing ZIP…" : "Publish package ZIP"}</button></div>
    </div>}
    {phase === "reviewing" && <div className="oiv-actions"><span role="status">Verifying prepared package files and GBAY metadata…</span><button className="quiet-button" onClick={cancel}>Cancel ZIP review</button></div>}
    {result && <div className="oiv-review" role="status"><h4>Package ZIP published</h4><p>{result.archive} · {formatBytes(result.archive_size)}</p><p className="oiv-hash">Archive SHA-256: {result.archive_sha256}</p><p>Ready to share manually. GTA was not modified and nothing was uploaded.</p></div>}
  </section>;
}
