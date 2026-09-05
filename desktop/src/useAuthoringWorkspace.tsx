import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";

export type WorkspaceModule = "binary" | "maps" | "graph" | "program" | "runtime" | "render" | "recipe" | "vehicle_identity" | "data_tools" | "code";
export type WorkspaceResult = Record<string, unknown> & { kind: string; module: WorkspaceModule; schema_version: number; state_sha256?: string };
type Request = Record<string, unknown>;
const SHA = /^[a-f0-9]{64}$/;
function unwrap(message: Envelope): WorkspaceResult {
  if (message.operation === "error") throw new Error(String(message.payload.message ?? "Authoring operation failed"));
  if (!message.terminal || !message.payload.result) throw new Error("Incomplete authoring response");
  return message.payload.result as WorkspaceResult;
}

export function useAuthoringWorkspace(client: DesktopClient, module: WorkspaceModule, adopt: (value: WorkspaceResult) => void) {
  const [busy, setBusy] = useState(false), [reading, setReading] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [review, setReview] = useState<{ request: Request; value: WorkspaceResult } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [lastResult, setLastResult] = useState<WorkspaceResult | null>(null);
  const generation = useRef(0), inFlight = useRef(false), job = useRef("");
  const onAdopt = useRef(adopt); onAdopt.current = adopt;
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => {}); }, [client]);
  const valid = (value: WorkspaceResult, kind: string) => {
    if (value?.kind !== kind || value.module !== module || value.schema_version !== 1 || value.game_write_performed !== false)
      throw new Error("Invalid or incompatible authoring evidence");
  };
  const clearReview = () => { setReview(null); setConfirmed(false); };
  const run = async (operation: "inspect_authoring_workspace" | "review_workspace_action", fields: Request) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setReading(true); setError(""); setNotice(""); clearReview();
    const version = ++generation.current;
    const request: Request = { ...fields, module };
    let finished = false;
    try {
      const started = await client.startJob(operation, request, `${module}-${version}`, message => {
        if (version !== generation.current || finished || !message.terminal) return;
        finished = true; inFlight.current = false; job.current = ""; setBusy(false); setReading(false);
        try {
          const value = unwrap(message);
          if (operation === "inspect_authoring_workspace") {
            valid(value, "workspace_session");
            if (!SHA.test(String(value.state_sha256)) || value.read_only !== true) throw new Error("Invalid workspace identity");
            onAdopt.current(value);
          } else {
            valid(value, "workspace_review");
            if (value.action !== request.action || value.review_only !== true || !SHA.test(String(value.review_sha256))
              || !SHA.test(String(value.request_sha256)) || (request.expected_state_sha256 !== undefined && value.state_sha256 !== request.expected_state_sha256))
              throw new Error("Review does not match this draft and input revision");
            setReview({ request, value });
          }
        } catch (reason) { setError(String(reason)); }
      });
      if (version !== generation.current) { if (!finished) void client.cancelJob(started.job_id).catch(() => {}); return; }
      if (!finished) job.current = started.job_id;
    } catch (reason) {
      if (version === generation.current) { inFlight.current = false; setBusy(false); setReading(false); setError(String(reason)); }
    }
  };
  const choose = async (kind: "code_source" | "metadata" | "package_folder" | "package" | "binary_source" | "binary_workspace" | "authoring_parent" | "map_descriptor" | "map_source" | "gta_folder" | "graph_document" | "program_document" | "graph_source" | "render_model" | "render_textures" | "blender_executable" | "rpf") => {
    if (inFlight.current || review) return null;
    inFlight.current = true; setBusy(true); setError(""); const version = ++generation.current;
    try { const selected = await client.selectPath(kind); return version === generation.current ? selected : null; }
    catch (reason) { if (version === generation.current) setError(String(reason)); return null; }
    finally { if (version === generation.current) { inFlight.current = false; setBusy(false); } }
  };
  const cancel = () => {
    if (!reading) return;
    const id = job.current; generation.current++; job.current = ""; inFlight.current = false;
    setBusy(false); setReading(false); setNotice("Inspection cancelled. No authoring changes were applied.");
    if (id) void client.cancelJob(id).catch(reason => setError(String(reason)));
  };
  const apply = async () => {
    if (!review || !confirmed || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); const version = ++generation.current;
    try {
      const result = unwrap(await client.applyWorkspaceAction({ ...review.request, review_sha256: review.value.review_sha256, authoring_confirmed: true }));
      if (version !== generation.current) return;
      valid(result, "workspace_applied");
      if (result.action !== review.request.action || result.review_sha256 !== review.value.review_sha256) throw new Error("Authoring receipt does not match the confirmed review");
      if (result.session) {
        const session = result.session as WorkspaceResult; valid(session, "workspace_session");
        if (!SHA.test(String(session.state_sha256))) throw new Error("Saved workspace identity is missing");
        onAdopt.current(session);
      }
      setLastResult(result);
      setNotice(result.output ? `Built ${String(result.output)} · SHA-256 ${String(result.output_sha256)}`
        : result.build ? `Package built: ${String((result.build as Request).root)} · ${String((result.build as Request).edition)} · SHA-256 ${String((result.build as Request).payload_sha256)}` : "Saved and revalidated. GTA files were not changed.");
    } catch (reason) { if (version === generation.current) setError(String(reason)); }
    finally { if (version === generation.current) { clearReview(); inFlight.current = false; setBusy(false); } }
  };
  return { busy, reading, error, notice, lastResult, review, confirmed, setConfirmed, clearReview, run, choose, cancel, apply,
    locked: busy || !!review, setError };
}

export function AuthoringFeedback({ work }: { work: ReturnType<typeof useAuthoringWorkspace> }) {
  return <>{work.error && <p className="error-banner" role="alert">{work.error}</p>}
    {work.notice && <p className="action-notice" role="status">{work.notice}</p>}
    {work.reading && <button className="quiet-button" onClick={work.cancel}>Cancel inspection</button>}
    {work.review && <section className="authoring-review" aria-label="Authoring review"><h4>Review: {String(work.review.value.action)}</h4>
      {work.review.value.candidate_only === true && <p><strong>Candidate only. Live game acceptance: NOT TESTED.</strong></p>}
      <p>Only the listed offline authoring files will change. This does not install anything into GTA V.</p>
      <dl>{["source", "destination", "offset", "length", "before", "after", "output_sha256"].filter(k => work.review!.value[k] !== undefined).map(key =>
        <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(work.review!.value[key])}</dd></div>)}</dl>
      {work.review.value.document !== undefined && <details><summary>Validated authoring document</summary><pre>{JSON.stringify(work.review.value.document, null, 2)}</pre></details>}
      {["outputs", "targets", "execution_order", "changes", "issues", "operations"].map(key => Array.isArray(work.review!.value[key]) && (work.review!.value[key] as unknown[]).length > 0 && <div key={key}><h5>{key.replaceAll("_", " ")}</h5><ul>{(work.review!.value[key] as unknown[]).map((row, i) => <li key={i}>{typeof row === "string" ? row : JSON.stringify(row)}</li>)}</ul></div>)}
      <label className="check-label"><input type="checkbox" checked={work.confirmed} disabled={work.busy} onChange={e => work.setConfirmed(e.target.checked)} />I reviewed these authoring changes</label>
      <div className="heading-actions"><button className="primary-button" disabled={!work.confirmed || work.busy} onClick={() => void work.apply()}>Apply reviewed change</button>
        <button className="quiet-button" disabled={work.busy} onClick={work.clearReview}>Back to draft</button></div>
    </section>}</>;
}
