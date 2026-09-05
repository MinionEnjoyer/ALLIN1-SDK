import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import "./Gxt2Workspace.css";
import "./RpfChangeSetWorkspace.css";

type Change = { action: string; archive_path: string; entry: string; new_entry?: string; original: Record<string, unknown>; payload: Record<string, unknown> | null };
export interface RpfTransactionSession {
  kind: "rpf_transaction_session"; source: string; source_kind: "plan" | "receipt"; state_sha256: string;
  archive: string; archive_sha256: string | null; edition: string; plan_id: string; status: string;
  target_scope: string; authorized_root: string | null; changes: Change[]; gta_path: string | null;
  transaction_id: string | null; backup: { path: string; sha256: string; size: number } | null;
  archive_lock: {path: string; pid: number; process_running: boolean; sha256: string; plan_id: string | null; created_at: string | null; identity: string; cleanup_supported: boolean} | null;
  verification: { healthy: boolean; archive_state: string; archive_sha256?: string; backup_valid: boolean; entry_valid: boolean; entry_error?: string } | null;
  read_only: boolean; archive_write_performed: boolean; game_write_performed: boolean;
}
export interface RpfTransactionReview {
  kind: "rpf_transaction_review"; action: "execute" | "rollback" | "recover" | "clear_lock"; request: Record<string, unknown>;
  session: RpfTransactionSession; receipt_root: string; authorized_root: string | null; restore_sha256: string | null;
  game_write_required: boolean; recovery_status: string | null;
  lock_write_required: boolean; lock_evidence: {path: string; sha256: string; existing_sha256: string | null} | null;
  review_sha256: string; review_only: boolean; archive_write_required: boolean; game_write_performed: boolean; warning: string;
}
interface History {
  kind: "rpf_transaction_history"; root: string; truncated: boolean; read_only: boolean;
  archive_write_performed: boolean; game_write_performed: boolean;
  receipts: {source: string; transaction_id: string; valid: boolean; status?: string; archive?: string; created_at?: string; change_count?: number; error?: string}[];
}
const SHA = /^[a-f0-9]{64}$/;
const actionNames: Record<string, string> = {replace:"Replace file",add:"Add file",delete:"Delete file",rename:"Rename entry",mkdir:"Create directory",rmdir:"Remove directory"};
const pathKey = (value: unknown) => typeof value === "string" ? value.replaceAll("\\", "/").toLowerCase() : "";
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
const same = (a: unknown, b: unknown) => canonical(a) === canonical(b);
function validSession(s: RpfTransactionSession) {
  return s?.kind === "rpf_transaction_session" && ["plan", "receipt"].includes(s.source_kind)
    && typeof s.source === "string" && typeof s.archive === "string" && SHA.test(s.state_sha256) && SHA.test(s.plan_id)
    && (s.archive_sha256 === null || SHA.test(s.archive_sha256)) && typeof s.edition === "string" && typeof s.status === "string"
    && Array.isArray(s.changes) && s.changes.length > 0 && s.changes.length <= 128
    && s.changes.every(c => c && ["add", "replace", "delete", "rename", "mkdir", "rmdir"].includes(c.action) && typeof c.archive_path === "string" && typeof c.entry === "string")
    && (s.archive_lock === null || (typeof s.archive_lock?.path === "string" && Number.isInteger(s.archive_lock.pid) && s.archive_lock.pid > 0
      && typeof s.archive_lock.process_running === "boolean" && SHA.test(s.archive_lock.sha256) && typeof s.archive_lock.identity === "string"
      && typeof s.archive_lock.cleanup_supported === "boolean" && (s.archive_lock.plan_id === null || typeof s.archive_lock.plan_id === "string")
      && (s.archive_lock.created_at === null || typeof s.archive_lock.created_at === "string")))
    && (s.source_kind === "plan" || (typeof s.backup?.path === "string" && SHA.test(s.backup.sha256) && !!s.verification
      && typeof s.verification.healthy === "boolean" && typeof s.verification.backup_valid === "boolean" && typeof s.verification.entry_valid === "boolean"))
    && s.read_only === true && s.archive_write_performed === false && s.game_write_performed === false;
}
function unwrap<T>(message: Envelope): T {
  if (message.operation === "error") throw Error(String(message.payload.message ?? "RPF transaction failed"));
  if (!message.terminal || !message.payload.result) throw Error("Incomplete RPF transaction response");
  return message.payload.result as T;
}
const intent = (s: RpfTransactionSession) => s.source_kind === "plan" ? "execute" : "rollback";
const recoverable = (s: RpfTransactionSession) => s.source_kind === "receipt" && s.verification?.healthy
  && ["applied", "original"].includes(s.verification.archive_state) && !s.archive_lock?.process_running
  && !["applied", "rolled_back", "rolled_back_after_failure", "interrupted_before_commit"].includes(s.status);
const clearable = (s: RpfTransactionSession) => s.source_kind === "receipt" && s.verification?.healthy
  && s.archive_lock?.cleanup_supported && !s.archive_lock.process_running && s.archive_lock.plan_id === s.plan_id
  && ["applied", "rolled_back", "rolled_back_after_failure", "interrupted_before_commit"].includes(s.status)
  && s.verification.archive_state === (s.status === "applied" ? "applied" : "original");
const lockEvidencePath = (s: RpfTransactionSession) => pathKey(s.source).replace(/\/receipt\.json$/, `/cleared-lock-${s.archive_lock?.sha256}.json`);
function validReview(value: RpfTransactionReview, request: Record<string, unknown>, session: RpfTransactionSession) {
  const s = value?.session;
  const recovery = request.action === "recover", clearing = request.action === "clear_lock", live = session.target_scope === "mods_copy";
  return value?.kind === "rpf_transaction_review" && same(value.request, request) && value.action === request.action
    && (clearing ? clearable(session) : recovery ? recoverable(session) : value.action === intent(session))
    && SHA.test(value.review_sha256) && value.review_only === true && value.archive_write_required === (!recovery && !clearing) && value.game_write_performed === false
    && value.lock_write_required === clearing
    && (clearing ? pathKey(value.lock_evidence?.path) === lockEvidencePath(session) && value.lock_evidence?.sha256 === session.archive_lock?.sha256
      && [null, session.archive_lock?.sha256].includes(value.lock_evidence?.existing_sha256) : value.lock_evidence === null)
    && value.game_write_required === (live && !recovery)
    && validSession(s) && s.source === session.source && s.source_kind === session.source_kind && s.state_sha256 === session.state_sha256
    && s.archive === session.archive && s.archive_sha256 === session.archive_sha256 && s.plan_id === session.plan_id && s.edition === session.edition && s.status === session.status
    && same(s.changes, session.changes) && same(s.backup, session.backup) && same(s.archive_lock, session.archive_lock)
    && s.target_scope === session.target_scope && (live
      ? !!request.gta_path && pathKey(s.gta_path) === pathKey(request.gta_path) && pathKey(s.archive).startsWith(`${pathKey(request.gta_path)}/mods/`) && value.authorized_root === null
      : s.target_scope === "workspace_copy" && !!request.authorized_root && pathKey(value.authorized_root) === pathKey(request.authorized_root))
    && pathKey(s.authorized_root) === pathKey(value.authorized_root) && typeof value.receipt_root === "string"
    && (clearing ? clearable(s) && same(s.verification, session.verification) && value.restore_sha256 === s.backup?.sha256 && value.recovery_status === null
      : recovery ? recoverable(s) && same(s.verification, session.verification) && value.recovery_status === (s.verification?.archive_state === "applied" ? "applied" : "interrupted_before_commit")
      : value.action === "execute" ? s.status === "ready" : !!s.verification?.healthy && s.verification.archive_state === "applied" && value.restore_sha256 === session.backup?.sha256);
}

export default function RpfTransactionWorkspace({ client, onGuardChange, onArchiveChanged }: {
  client: DesktopClient; onGuardChange: (value: boolean) => void; onArchiveChanged: (archive: string) => void;
}) {
  const [session, setSession] = useState<RpfTransactionSession | null>(null);
  const [selected, setSelected] = useState(0), [game, setGame] = useState(""), [scope, setScope] = useState("");
  const [review, setReview] = useState<RpfTransactionReview | null>(null), [confirmed, setConfirmed] = useState(false);
  const [gameConfirmed, setGameConfirmed] = useState(false), [history, setHistory] = useState<History | null>(null);
  const [phase, setPhase] = useState<"idle" | "choosing" | "reading" | "writing">("idle");
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const generation = useRef(0), inFlight = useRef(false), job = useRef("");
  const title = useRef<HTMLHeadingElement>(null), reviewTitle = useRef<HTMLHeadingElement>(null), hadReview = useRef(false);
  const locked = phase !== "idle" || !!review;
  useEffect(() => onGuardChange(locked), [locked, onGuardChange]);
  useEffect(() => {
    if (review) {
      reviewTitle.current?.focus();
      reviewTitle.current?.scrollIntoView?.({block:"start"});
    } else if (hadReview.current) title.current?.focus();
    hadReview.current = !!review;
  }, [review]);
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => undefined); onGuardChange(false); }, [client, onGuardChange]);
  const load = (value: RpfTransactionSession, source: string) => {
    if (!validSession(value) || pathKey(value.source) !== pathKey(source)) throw Error("Transaction evidence does not match the selected document.");
    setSession(value); setSelected(0);
  };
  const start = async (task: (version: number) => Promise<void>) => {
    if (inFlight.current) return;
    const version = ++generation.current; inFlight.current = true; setError(""); setNotice("");
    try { await task(version); }
    catch (reason) { if (generation.current === version) { setError(String(reason)); setPhase("idle"); inFlight.current = false; } }
  };
  const read = async (operation: "list_rpf_transactions" | "inspect_rpf_transaction" | "review_rpf_transaction", payload: Record<string, unknown>, version: number, accept: (value: unknown) => void) => {
    setPhase("reading"); let finished = false;
    const started = await client.startJob(operation, payload, `rpf-transaction-${version}`, message => {
      if (generation.current !== version || finished || !message.terminal) return;
      finished = true; job.current = ""; inFlight.current = false; setPhase("idle");
      try { accept(unwrap(message)); } catch (reason) { setError(String(reason)); }
    });
    if (generation.current !== version) { if (!finished) void client.cancelJob(started.job_id).catch(() => undefined); }
    else if (!finished) job.current = started.job_id;
  };
  const choose = (kind: "rpf_plan" | "rpf_receipt" | "gta_folder" | "rpf_authorized_root") => void start(async version => {
    setPhase("choosing"); const path = await client.selectPath(kind);
    if (generation.current !== version) return;
    if (path && (kind === "rpf_plan" || kind === "rpf_receipt")) {
      await read("inspect_rpf_transaction", { source: path, ...(game ? { gta_path: game } : {}) }, version, value => {
        load(value as RpfTransactionSession, path); setScope("");
      });
    } else if (path && kind === "gta_folder" && session) {
      setGame(path); setScope("");
      const source = session.source; setSession(null);
      await read("inspect_rpf_transaction", {source, gta_path:path}, version, value => load(value as RpfTransactionSession, source));
    } else {
      if (path) { if (kind === "gta_folder") { setGame(path); setSession(null); setScope(""); } else setScope(path); }
      inFlight.current = false; setPhase("idle");
    }
  });
  const prepare = (action: RpfTransactionReview["action"] = session ? intent(session) : "execute") => { if (!session) return; void start(async version => {
    const payload = { source: session.source, expected_sha256: session.state_sha256, action, ...(session.target_scope === "workspace_copy" ? {authorized_root:scope} : {}), ...(game || session.gta_path ? { gta_path: game || session.gta_path } : {}) };
    await read("review_rpf_transaction", payload, version, value => {
      if (!validReview(value as RpfTransactionReview, payload, session)) throw Error("Review does not match the selected archive and transaction. Nothing was authorized.");
      setReview(value as RpfTransactionReview); setConfirmed(false); setGameConfirmed(false);
    });
  }); };
  const apply = () => {
    if (!review || !confirmed || (review.game_write_required && !gameConfirmed) || phase !== "idle") return;
    void start(async version => {
      setPhase("writing");
      try {
        const clearing = review.action === "clear_lock";
        const result = unwrap<{ kind: string; action: string; review_sha256: string; session: RpfTransactionSession; archive_write_performed: boolean; game_write_performed: boolean; receipt_write_performed: boolean; lock_write_performed: boolean; lock_evidence: RpfTransactionReview["lock_evidence"] }>(
          await client.applyRpfTransaction({ ...review.request, review_sha256: review.review_sha256,
            ...(clearing ? {lock_clear_confirmed:true} : review.action === "recover" ? {receipt_write_confirmed:true} : {archive_write_confirmed:true}),
            ...(review.game_write_required ? {game_write_confirmed:true} : {}) }));
        if (generation.current !== version) return;
        const s = result.session, previous = review.session;
        if (result.kind !== "rpf_transaction_applied" || result.action !== review.action || result.review_sha256 !== review.review_sha256
          || result.archive_write_performed !== review.archive_write_required || result.game_write_performed !== review.game_write_required || result.receipt_write_performed !== !clearing || result.lock_write_performed !== clearing || !validSession(s)
          || !same(result.lock_evidence, review.lock_evidence)
          || s.source_kind !== "receipt" || s.archive !== previous.archive || s.plan_id !== previous.plan_id || !s.verification?.healthy
          || s.target_scope !== previous.target_scope || s.authorized_root !== previous.authorized_root || s.edition !== previous.edition || pathKey(s.gta_path) !== pathKey(previous.gta_path)
          || s.verification.archive_sha256 !== s.archive_sha256 || !same(s.changes.map(c => [c.action, c.archive_path, c.entry, c.new_entry, c.original, c.payload?.sha256, c.payload?.size]), previous.changes.map(c => [c.action, c.archive_path, c.entry, c.new_entry, c.original, c.payload?.sha256, c.payload?.size]))
          || (clearing ? s.status !== previous.status || s.source !== previous.source || s.state_sha256 !== previous.state_sha256 || s.archive_sha256 !== previous.archive_sha256 || s.archive_lock !== null || !same(s.backup, previous.backup) || !same(s.verification, previous.verification)
            : review.action === "recover" ? s.status !== review.recovery_status || s.source !== previous.source || s.archive_sha256 !== previous.archive_sha256 || !same(s.backup, previous.backup) || !same(s.archive_lock, previous.archive_lock)
            : review.action === "execute" ? s.status !== "applied" || s.verification.archive_state !== "applied" || s.backup?.sha256 !== previous.archive_sha256
            || !pathKey(s.source).startsWith(`${pathKey(review.receipt_root)}/`) || !pathKey(s.source).endsWith("/receipt.json")
            : s.status !== "rolled_back" || s.verification.archive_state !== "original" || s.source !== previous.source || s.archive_sha256 !== review.restore_sha256)) {
          throw Error("Saved transaction evidence could not be verified. Reopen its receipt before retrying.");
        }
        load(s, s.source);
        setHistory(null);
        setNotice(clearing ? `Stale lock cleared. Archive, receipt and backup unchanged. Lock evidence retained: ${result.lock_evidence?.path}. No transaction changes were replayed.`
          : `${review.action === "recover" ? "Receipt reconciled; archive and locks unchanged" : review.action === "execute" ? "Archive applied and verified" : "Original archive restored and verified"}. Receipt: ${s.source}. Keep its folder and backup. ${review.game_write_required ? "The selected GTA mods archive was updated. Stock archives were not changed." : "No GTA files were changed."}`);
      } finally {
        if (generation.current === version) { if (review.archive_write_required) onArchiveChanged(review.session.archive); inFlight.current = false; setPhase("idle"); setReview(null); setConfirmed(false); setGameConfirmed(false); }
      }
    });
  };
  const row = session?.changes[selected];
  const canRollback = session?.source_kind === "receipt" && !session.archive_lock && session.verification?.healthy && session.verification.archive_state === "applied" && ["applied", "verified_staging", "rollback_failed"].includes(session.status);
  const scoped = session?.target_scope === "mods_copy" ? !!game && pathKey(game) === pathKey(session.gta_path) : session?.target_scope === "workspace_copy" && !!scope;
  const ready = scoped && (session?.source_kind === "plan" ? session.status === "ready" : canRollback);
  return <section className="gxt-workspace rpf-change-workspace" aria-labelledby="rpf-transaction-title">
    <div className="gxt-title"><div><span className="pane-kicker">Archive transactions</span><h2 id="rpf-transaction-title" ref={title} tabIndex={-1}>Execute & restore</h2>
      <p>Apply reviewed changes to an authoring copy or an existing GTA mods archive. Stock game archives stay protected.</p></div>
      <div className="gxt-actions"><button className="quiet-button" disabled={locked} onClick={() => choose("rpf_receipt")}>Open transaction receipt</button><button className="primary-button" disabled={locked} onClick={() => choose("rpf_plan")}>Open compiled plan</button></div></div>
    {error && <p role="alert" className="error-banner">{error} No automatic retry was made. If execution had started, inspect the transaction receipt before trying again.</p>}
    {notice && <p role="status" className="gxt-notice">{notice}</p>}
    {phase === "reading" && <div className="gxt-actions"><span role="status">Verifying transaction evidence…</span><button className="quiet-button" onClick={() => { generation.current++; inFlight.current = false; setPhase("idle"); if (job.current) void client.cancelJob(job.current).catch(reason => setError(String(reason))); job.current = ""; }}>Cancel transaction review</button></div>}
    <div className="gxt-source"><strong>{session ? `${session.source_kind === "plan" ? "Plan" : "Receipt"}: ${session.status}` : "No transaction selected"}</strong><code>{session?.source || "Open the compiled JSON plan exported from Change sets."}</code><code>{session?.archive || "No archive is changed until you review and confirm."}</code></div>
    <div className="gxt-panels rpf-change-panels">
      <section className="gxt-pane" aria-label="Transaction changes"><header><span className="pane-kicker">Exact targets</span><h3>Ordered changes</h3></header><div className="gxt-rows">
        {session?.changes.map((change, i) => <button key={i} disabled={locked} aria-pressed={selected === i} className={selected === i ? "selected" : ""} onClick={() => setSelected(i)}><span>{i + 1}. {actionNames[change.action]}</span><code>{change.archive_path || "Outer archive"} → {change.entry}</code></button>)}
        {!session && <p className="gxt-empty">The plan's exact members and their execution order appear here.</p>}
      </div>{row && <div className="gxt-evidence"><strong>Selected change</strong><code>{row.new_entry ? `Rename to: ${row.new_entry}` : row.action}</code><small>Original member evidence</small><pre>{JSON.stringify(row.original, null, 2)}</pre>{row.payload && <><small>Payload evidence</small><pre>{JSON.stringify(row.payload, null, 2)}</pre></>}</div>}</section>
      <section className="gxt-pane" aria-label="Transaction scope"><header><span className="pane-kicker">Write boundary</span><h3>Review operation</h3></header><div className="gxt-editor">
        <button className="quiet-button" disabled={locked} onClick={() => choose("gta_folder")}>Choose GTA installation</button><code>{game || session?.gta_path || "Auto-detect GTA for read-only decoding"}</code>
        {session?.target_scope === "mods_copy" ? <p className="gxt-note">GTA mods write. Explicitly choose the installation containing this archive. A second confirmation is required before any game write.</p> : <><button className="quiet-button" disabled={locked || !session} onClick={() => choose("rpf_authorized_root")}>Authorize archive folder</button><code>{scope || "Select the folder directly containing the authoring RPF."}</code></>}
        <p className="gxt-note">The selected folder must match this transaction. Selection and review do not execute anything. GTA must be closed when applying, restoring, reconciling or clearing locks.</p>
        <button className="primary-button" disabled={locked || !ready} onClick={() => prepare()}>{session?.source_kind === "receipt" ? "Review rollback" : "Review execution"}</button>
        {session && !["workspace_copy", "mods_copy"].includes(session.target_scope) && <p className="error-banner">This transaction has no supported write scope. Compile a workspace-copy or mods-copy plan first.</p>}
        {session?.source_kind === "receipt" && !canRollback && <p className="gxt-note">Rollback is unavailable unless the archive is still applied and both its entries and original backup verify. Already restored or externally modified archives are not overwritten.</p>}
        {session?.source_kind === "receipt" && <><button className="quiet-button" disabled={locked || !scoped || !recoverable(session)} onClick={() => prepare("recover")}>Review receipt recovery</button><p className="gxt-note">For interrupted transactions only: reconcile the recorded status with verified archive contents. This never reapplies changes or restores an archive.</p></>}
        {session?.archive_lock && <><button className="quiet-button" disabled={locked || !scoped || !clearable(session)} onClick={() => prepare("clear_lock")}>Review stale lock cleanup</button>
          <p className="gxt-note">Cleanup requires a settled receipt, a matching lock and an exited owner. Recover an interrupted receipt first. The lock's original bytes are retained beside its receipt. Unrelated or unsupported locks cannot be cleared here.</p></>}
      </div></section>
      <aside className="gxt-pane" aria-label="Transaction verification"><header><span className="pane-kicker">Receipt and backup</span><h3>Verified state</h3></header><div className="gxt-evidence">
        <strong>{session?.verification ? `Archive: ${session.verification.archive_state.replaceAll("_", " ")}` : "No receipt verification yet"}</strong>
        {session?.verification && <><p>Original backup: {session.verification.backup_valid ? "verified" : "missing or changed"}<br/>Affected entries: {session.verification.entry_valid ? "verified" : "not verified"}</p>{session.verification.entry_error && <p className="error-banner">{session.verification.entry_error}</p>}</>}
        {session && <><small>Current archive SHA-256</small><code>{session.archive_sha256 || "Archive missing"}</code><small>Document revision SHA-256</small><code>{session.state_sha256}</code></>}
        {session?.backup && <><small>Original archive backup</small><code>{session.backup.path}</code><code>{session.backup.sha256}</code></>}
        {session?.archive_lock && <p className="error-banner">{session.archive_lock.process_running ? "Active" : "Stale"} transaction lock (PID {session.archive_lock.pid}). Archive writes remain blocked. Receipt recovery does not remove locks.</p>}
        <p className="gxt-note">Backups cover the full outer archive, including nested members. Keep the entire transaction folder. Restore refuses later changes by other tools.</p>
        <button className="quiet-button" disabled={locked || !session} onClick={() => session && void start(version => read("inspect_rpf_transaction", { source: session.source, ...(game || session.gta_path ? { gta_path: game || session.gta_path } : {}) }, version, value => load(value as RpfTransactionSession, session.source)))}>Recheck document</button>
        {session?.archive_lock && <><small>Lock owner plan</small><code>{session.archive_lock.plan_id || "No plan identity recorded"}</code><small>Lock created</small><code>{session.archive_lock.created_at || "Not recorded"}</code></>}
      </div></aside>
    </div>
    {review && <section className="gxt-review" aria-label="RPF transaction confirmation"><h3 tabIndex={-1} ref={reviewTitle}>{review.action === "clear_lock" ? "Confirm stale lock cleanup" : review.action === "recover" ? "Confirm receipt recovery" : review.action === "execute" ? "Confirm archive execution" : "Confirm original archive restore"}</h3>
      <p>{review.warning}</p><p><strong>{!review.archive_write_required ? "Verified archive (unchanged)" : "Archive to replace"}</strong><br/><code>{review.session.archive}</code></p><p><strong>Required current SHA-256</strong><br/><code>{review.session.archive_sha256}</code></p>
      <p><strong>{review.session.target_scope === "mods_copy" ? "Selected GTA installation" : "Authorized folder"}</strong><br/><code>{review.authorized_root || review.session.gta_path}</code></p><p><strong>{review.action === "execute" ? "Receipt and full backup location" : "Transaction folder"}</strong><br/><code>{review.receipt_root}</code></p>
      {review.restore_sha256 && <p><strong>{!review.archive_write_required ? "Verified backup SHA-256 (unchanged)" : "Restore original SHA-256"}</strong><br/><code>{review.restore_sha256}</code></p>}
      {review.lock_evidence && <><p><strong>Stale lock to remove</strong><br/><code>{review.session.archive_lock?.path}</code></p><p><strong>Retained lock evidence</strong><br/><code>{review.lock_evidence.path}</code></p><p><strong>Lock SHA-256</strong><br/><code>{review.lock_evidence.sha256}</code></p></>}
      <h4>{review.action === "clear_lock" ? "Verified transaction changes (not replayed)" : review.action === "recover" ? `Reconcile receipt status: ${review.session.status} → ${review.recovery_status}` : review.action === "rollback" ? "Original transaction changes being undone" : "Changes to apply"}</h4>
      <ol className="rpf-change-review-list">{review.session.changes.map((change, i) => <li key={i}><strong>{actionNames[change.action]}</strong><code>{change.archive_path || "Outer archive"} → {change.entry}{change.new_entry ? ` → ${change.new_entry}` : ""}</code></li>)}</ol>
      <label className="gxt-confirm"><input type="checkbox" disabled={phase === "writing"} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{review.action === "clear_lock" ? "Retain the lock evidence and remove only this stale lock. Keep the archive, receipt and backup unchanged." : review.action === "recover" ? "Update only this receipt to the verified status. Keep the archive, backup and any lock unchanged." : review.action === "execute" ? "Replace this archive with the reviewed changes and keep its backup." : "Restore this archive from the reviewed original backup."}</label>
      {review.game_write_required && <label className="gxt-confirm"><input type="checkbox" disabled={phase === "writing"} checked={gameConfirmed} onChange={event => setGameConfirmed(event.target.checked)}/>{review.action === "clear_lock" ? "GTA is closed. I authorize removing only this stale lock from the selected game installation." : "GTA is closed. I authorize replacing this mods archive in the selected game installation."}</label>}
      <p className="gxt-note">Writes cannot be cancelled. Keep the SDK open until verification finishes.</p><div className="gxt-actions"><button className="quiet-button" disabled={phase === "writing"} onClick={() => { setReview(null); setConfirmed(false); setGameConfirmed(false); }}>Back to transaction</button><button className="primary-button" disabled={!confirmed || (review.game_write_required && !gameConfirmed) || phase === "writing"} onClick={apply}>{phase === "writing" ? "Writing and verifying…" : review.action === "clear_lock" ? "Clear stale lock only" : review.action === "recover" ? "Reconcile receipt only" : review.action === "execute" ? review.game_write_required ? "Apply to GTA mods archive" : "Apply to authoring archive" : "Restore original archive"}</button></div>
    </section>}
    <section className="gxt-review" aria-label="Retained transaction history"><div className="gxt-actions rpf-history-heading"><h3>Transaction history</h3><button className="quiet-button" disabled={locked} onClick={() => void start(version => read("list_rpf_transactions", {}, version, value => {
      const h = value as History;
      if (h?.kind !== "rpf_transaction_history" || typeof h.root !== "string" || h.read_only !== true || h.archive_write_performed !== false || h.game_write_performed !== false || !Array.isArray(h.receipts) || h.receipts.length > 256
        || h.receipts.some(r => typeof r.source !== "string" || typeof r.transaction_id !== "string" || typeof r.valid !== "boolean" || !pathKey(r.source).startsWith(`${pathKey(h.root)}/`)
          || (r.valid ? typeof r.status !== "string" || typeof r.archive !== "string" || typeof r.created_at !== "string" || !Number.isInteger(r.change_count) || r.change_count! < 1 || r.change_count! > 128 : typeof r.error !== "string"))) throw Error("Invalid transaction history response");
      setHistory(h);
    }))}>Refresh transaction history</button></div><p className="gxt-note">Retained SDK receipts, newest first within the scanned set. Status is recorded history, not an integrity check; open a receipt to verify it.</p>
      {history && <><code>{history.root}</code>{history.truncated && <p role="status">History reached its 256-folder scan limit. Use Open transaction receipt for older or unlisted folders.</p>}{!history.receipts.length && <p>No retained transactions found.</p>}
        <div className="gxt-rows">{history.receipts.map(r => <button key={r.source} disabled={locked || !r.valid} onClick={() => void start(version => read("inspect_rpf_transaction", {source:r.source,...(game ? {gta_path:game} : {})}, version, value => {load(value as RpfTransactionSession,r.source);setScope("");title.current?.focus();}))}><strong>{r.valid ? `${r.status?.replaceAll("_", " ")} · ${r.change_count} changes` : "Unreadable receipt"}</strong><code>{r.archive || r.source}</code><small>{r.created_at || r.transaction_id}{r.error ? ` · ${r.error}` : ""}</small></button>)}</div></>}
    </section>
  </section>;
}
