import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope, RpfArchiveResult, RpfEntryRecord } from "./types";

type UtilityAction = "extract_entry" | "export_native_workspace" | "extract_subtree" | "extract_archive" | "compare" | "verify_integrity" | "defragment_copy";
type Review = Record<string, unknown> & {
  action: UtilityAction; label: string; destination: string; archive: string;
  archive_sha256: string; review_sha256: string; ready: boolean;
};

function resultFrom(message: Envelope): Record<string, unknown> {
  const payload = message.payload as Record<string, unknown>;
  if (message.operation === "error") throw new Error(String(payload.message ?? payload.error ?? "RPF utility failed"));
  const result = payload.result;
  if (!result || typeof result !== "object") throw new Error("RPF utility returned no structured result");
  return result as Record<string, unknown>;
}

function baseName(path: string): string {
  return (path.split(/[\\/]/).at(-1) ?? "archive.rpf").replace(/\.rpf$/i, "") || "archive";
}

export default function RpfArchiveUtilities({
  client, result, entry, disabled, onGuardChange,
}: {
  client: DesktopClient;
  result: RpfArchiveResult;
  entry: RpfEntryRecord | null;
  disabled?: boolean;
  onGuardChange?: (guarded: boolean) => void;
}) {
  const [mode, setMode] = useState<"metadata" | "logical" | "exact">("logical");
  const [review, setReview] = useState<{ value: Review; payload: Record<string, unknown> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const completed = useRef("");
  const jobRef = useRef("");
  const guarded = busy || Boolean(review);
  useEffect(() => { onGuardChange?.(guarded); }, [guarded, onGuardChange]);
  useEffect(() => () => {
    generation.current++;
    if (jobRef.current) void client.cancelJob(jobRef.current).catch(() => {});
  }, [client]);

  const begin = async (action: UtilityAction) => {
    if (disabled || busy || review) return;
    setError(""); setNotice("");
    let compareArchive = "";
    if (action === "compare") {
      compareArchive = await client.selectPath("rpf") ?? "";
      if (!compareArchive) return;
    }
    const stem = baseName(result.source);
    const suggested = action === "extract_entry" ? entry?.name ?? "rpf-member.bin"
      : action === "export_native_workspace" ? `${(entry?.name ?? "native-asset")}-workspace`
      : action === "extract_subtree" ? `${(entry?.name ?? "subtree")}-rpf-export`
        : action === "extract_archive" ? `${stem}-rpf-export`
          : action === "compare" ? `${stem}-rpf-diff.json`
            : action === "verify_integrity" ? `${stem}-integrity.json`
              : `${stem}-defragmented.rpf`;
    const destination = await client.selectRpfUtilityDestination(action, suggested);
    if (!destination) return;
    const payload: Record<string, unknown> = {
      action, archive: result.source, gta_path: result.gta_path, destination,
      ...(action === "compare" ? { compare_archive: compareArchive, comparison_mode: mode } : {}),
      ...(["extract_entry", "export_native_workspace", "extract_subtree"].includes(action) ? { entry_id: entry?.id } : {}),
    };
    const token = `rpf-utility-${++generation.current}`;
    completed.current = ""; setBusy(true);
    try {
      const started = await client.startJob("review_rpf_utility", payload, token, message => {
        if (!message.terminal || token !== `rpf-utility-${generation.current}`) return;
        completed.current = token; jobRef.current = ""; setBusy(false); setJob("");
        try {
          const value = resultFrom(message) as Review;
          if (!value.ready || !/^[0-9a-f]{64}$/.test(value.review_sha256)) throw new Error("RPF utility review evidence is incomplete");
          setReview({ value, payload });
        } catch (reason) { setError(String(reason).replace(/^Error:\s*/, "")); }
      });
      if (completed.current !== token && token === `rpf-utility-${generation.current}`) {
        jobRef.current = started.job_id;
        setJob(started.job_id);
      }
    } catch (reason) {
      if (token === `rpf-utility-${generation.current}`) { jobRef.current = ""; setBusy(false); setJob(""); setError(String(reason)); }
    }
  };

  const cancelJob = async () => {
    generation.current++; const current = jobRef.current || job; jobRef.current = ""; setJob(""); setBusy(false);
    setNotice("RPF utility review cancelled. No output was created.");
    if (current) try { await client.cancelJob(current); } catch (reason) { setError(String(reason)); }
  };

  const confirm = async () => {
    const pending = review;
    if (!pending || busy) return;
    setBusy(true); setError("");
    try {
      const response = await client.applyRpfUtility({
        ...pending.payload, review_sha256: pending.value.review_sha256,
        authoring_confirmed: true,
      });
      const value = resultFrom(response);
      setReview(null);
      setNotice(`${String(value.label ?? pending.value.label)} completed. Source archive was not changed.`);
    } catch (reason) {
      setReview(null);
      setError(String(reason).replace(/^Error:\s*/, ""));
    } finally { setBusy(false); }
  };

  return <section className="rpf-utility-panel" aria-label="RPF archive utilities">
    <div className="rpf-utility-heading"><div><strong>Archive utilities</strong><small>Reviewed outputs only; the open archive and GTA V stay read-only.</small></div>
      <label><span>Compare as</span><select value={mode} disabled={disabled || guarded} onChange={event => setMode(event.target.value as typeof mode)}><option value="metadata">Metadata</option><option value="logical">Logical content</option><option value="exact">Exact bytes</option></select></label></div>
    <div className="rpf-utility-actions">
      <button className="quiet-button" disabled={disabled || guarded || !entry || entry.kind === "directory"} onClick={() => void begin("extract_entry")}>Extract member</button>
      <button className="quiet-button" disabled={disabled || guarded || !entry || entry.kind === "directory" || !/\.(ydr|ydd|yft|ytd)$/i.test(entry.name)} onClick={() => void begin("export_native_workspace")}>Editable native copy</button>
      <button className="quiet-button" disabled={disabled || guarded || entry?.kind !== "directory"} onClick={() => void begin("extract_subtree")}>Export subtree</button>
      <button className="quiet-button" disabled={disabled || guarded} onClick={() => void begin("extract_archive")}>Export archive tree</button>
      <button className="quiet-button" disabled={disabled || guarded} onClick={() => void begin("compare")}>Compare archive</button>
      <button className="quiet-button" disabled={disabled || guarded} onClick={() => void begin("verify_integrity")}>Verify integrity</button>
      <button className="quiet-button" disabled={disabled || guarded} onClick={() => void begin("defragment_copy")}>Defragment copy</button>
      {busy && job && <button className="danger-button" onClick={() => void cancelJob()}>Cancel review</button>}
    </div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {notice && <p className="action-notice" role="status">{notice}</p>}
    {review && <div className="rpf-utility-review" role="dialog" aria-label="Review RPF utility output">
      <strong>{review.value.label}</strong>
      <dl className="detail-list"><div><dt>Source</dt><dd>{review.value.archive}</dd></div><div><dt>Source SHA-256</dt><dd>{review.value.archive_sha256}</dd></div><div><dt>New output</dt><dd>{review.value.destination}</dd></div><div><dt>Game write</dt><dd>No</dd></div></dl>
      <div className="heading-actions"><button className="quiet-button" disabled={busy} onClick={() => setReview(null)}>Back</button><button className="primary-button" disabled={busy} onClick={() => void confirm()}>{busy ? "Writing…" : "Create reviewed output"}</button></div>
    </div>}
  </section>;
}
