import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope, RpfArchiveResult } from "./types";
import "./Gxt2Workspace.css";
import "./RpfChangeSetWorkspace.css";

export interface RpfChangeRequest { archive: string; archive_path: string; entry: string; kind: string; requestId: number }
export interface RpfChange { id: string; action: string; archive_path: string; entry: string; new_entry?: string; payload?: {path: string; size: number; sha256: string} }
export interface RpfChangeSession {
  kind: "rpf_change_set_session"; change_set: string; state_sha256: string;
  archive: {path: string; size: number; edition: string; sha256: string}; actions: RpfChange[];
  action_limit: number; files_verified: boolean; read_only: boolean; game_write_performed: boolean;
}
export interface RpfChangeReview {
  kind: "rpf_change_set_review"; action: string; request: Record<string, unknown>; review_sha256: string;
  change_set: string | null; state_sha256: string | null; destination: string | null;
  archive: RpfChangeSession["archive"]; gta_path: string | null; authorized_root: string | null;
  before: RpfChange[]; after: RpfChange[]; review_only: boolean; game_write_performed: boolean; archive_write_performed: boolean;
  plan: null | {status: string; plan_id: string; changes: Record<string, unknown>[]; blocking_reasons: string[]; warnings: string[]; [key: string]: unknown};
}
const SHA = /^[a-f0-9]{64}$/;
const actions: Record<string,string> = {replace:"Replace file",add:"Add file",delete:"Delete file",rename:"Rename entry",mkdir:"Create directory",rmdir:"Remove directory"};
const operations: Record<string,string> = {create:"Create change set",stage:"Stage change",remove:"Remove staged action",move:"Reorder action",compile:"Export compiled plan"};
const pathKey = (v: unknown) => typeof v === "string" ? v.replaceAll("\\","/") : "";
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a],[b]) => a.localeCompare(b)).map(([k,v])=>`${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  return JSON.stringify(value);
}
const same = (a: unknown,b: unknown) => canonical(a) === canonical(b);
function validRows(rows: RpfChange[]) {
  return Array.isArray(rows) && rows.length <= 128 && new Set(rows.map(r=>r?.id)).size === rows.length && rows.every(r => r &&
    typeof r.id === "string" && !!r.id && Object.hasOwn(actions,r.action) && typeof r.entry === "string" && !!r.entry && typeof r.archive_path === "string"
    && (["add","replace"].includes(r.action) ? !!r.payload && typeof r.payload.path === "string" && SHA.test(r.payload.sha256)
      && Number.isSafeInteger(r.payload.size) && r.payload.size >= 0 : r.payload === undefined)
    && (r.action === "rename" ? typeof r.new_entry === "string" && !!r.new_entry : r.new_entry === undefined));
}
function validSession(s: RpfChangeSession) {
  return s?.kind === "rpf_change_set_session" && SHA.test(s.state_sha256) && typeof s.change_set === "string"
    && typeof s.archive?.path === "string" && SHA.test(s.archive.sha256) && Number.isSafeInteger(s.archive.size)
    && typeof s.archive.edition === "string" && validRows(s.actions) && s.action_limit === 128 && s.read_only === true && s.game_write_performed === false;
}
function unwrap<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message ?? "RPF action failed"));
  if (!message.terminal || !message.payload.result) throw new Error("Incomplete RPF change-set response");
  return message.payload.result as T;
}
function validReview(v: RpfChangeReview, p: Record<string,unknown>, session: RpfChangeSession | null) {
  if (v?.kind !== "rpf_change_set_review" || !SHA.test(v.review_sha256) || v.action !== p.action || !same(v.request,p)
      || v.review_only !== true || v.game_write_performed !== false || v.archive_write_performed !== false
      || !validRows(v.before) || !validRows(v.after) || !SHA.test(v.archive?.sha256)
      || pathKey(v.destination) !== pathKey(p.destination)) return false;
  if (p.action === "create") return v.change_set === null && v.state_sha256 === null && pathKey(v.archive.path) === pathKey(p.archive)
    && v.after.length === 0 && v.before.length === 0 && v.plan === null;
  if (!session || v.state_sha256 !== session.state_sha256 || v.change_set !== session.change_set || !same(v.archive,session.archive)
      || !same(v.before,session.actions)) return false;
  if (p.action === "compile") return same(v.after,v.before) && !!v.plan && ["ready","blocked"].includes(v.plan.status)
    && typeof v.plan.plan_id === "string" && v.plan.changes?.length === v.after.length
    && v.plan.changes.every((row,i)=> row.action === v.after[i].action
      && pathKey(row.entry).toLowerCase() === pathKey(v.after[i].entry).toLowerCase()
      && pathKey(row.archive_path).toLowerCase() === pathKey(v.after[i].archive_path).toLowerCase()
      && row.new_entry === v.after[i].new_entry && same(row.payload ?? null,v.after[i].payload ?? null))
    && Array.isArray(v.plan.blocking_reasons) && Array.isArray(v.plan.warnings);
  if (v.plan !== null) return false;
  const expected = [...session.actions];
  if (p.action === "stage") {
    const row = v.after.at(-1), change = p.change as Record<string,unknown>;
    return v.after.length === expected.length + 1 && same(v.after.slice(0,-1),expected) && !!row
      && row.action === change.action && row.entry === change.entry && row.archive_path === (change.archive_path ?? "")
      && row.new_entry === change.new_entry && pathKey(row.payload?.path) === pathKey(change.payload);
  }
  const index = expected.findIndex(row=> row.id === p.action_id);
  if (index < 0) return false;
  const [row] = expected.splice(index,1);
  if (p.action === "move") expected.splice(Number(p.position)-1,0,row);
  return same(v.after,expected);
}

export default function RpfChangeSetWorkspace({client,indexed,onGuardChange,targetRequest}: {
  client: DesktopClient; indexed: RpfArchiveResult | null; onGuardChange: (guarded:boolean)=>void; targetRequest: RpfChangeRequest | null;
}) {
  const [session,setSession] = useState<RpfChangeSession|null>(null), [selected,setSelected] = useState("");
  const [kind,setKind] = useState("replace"), [layer,setLayer] = useState(""), [entry,setEntry] = useState(""), [newEntry,setNewEntry] = useState(""), [payloadPath,setPayloadPath] = useState("");
  const [game,setGame] = useState(""), [authorized,setAuthorized] = useState("");
  const [phase,setPhase] = useState<"idle"|"choosing"|"reading"|"writing">("idle");
  const [review,setReview] = useState<{value:RpfChangeReview;payload:Record<string,unknown>}|null>(null);
  const [confirmed,setConfirmed] = useState(false), [error,setError] = useState(""), [notice,setNotice] = useState("");
  const generation = useRef(0), job = useRef(""), inFlight = useRef(false), heading = useRef<HTMLHeadingElement>(null);
  const workspaceHeading = useRef<HTMLHeadingElement>(null), hadReview = useRef(false);
  const dirty = !!(entry || layer || newEntry || payloadPath), busy = phase !== "idle", locked = busy || !!review;
  const selectedRow = session?.actions.find(row=>row.id===selected), position = session?.actions.findIndex(row=>row.id===selected) ?? -1;
  const reviewedRow = review?.value.before.find(row=>row.id===review.payload.action_id);
  useEffect(()=>{onGuardChange(locked || dirty);},[locked,dirty,onGuardChange]);
  useEffect(()=>{if(review) heading.current?.focus();else if(hadReview.current)workspaceHeading.current?.focus();hadReview.current=!!review;},[review]);
  useEffect(()=>()=>{generation.current++; if(job.current) void client.cancelJob(job.current).catch(()=>undefined); onGuardChange(false);},[client,onGuardChange]);
  const reset = () => {setEntry("");setLayer("");setNewEntry("");setPayloadPath("");};
  const load = (s:RpfChangeSession, preserveDraft=false) => {
    if(!validSession(s)) throw new Error("Invalid change-set evidence; no workspace was replaced.");
    if(s.change_set !== session?.change_set) {setAuthorized("");setGame("");}
    setSession(s); setSelected(s.actions.find(row=>row.id===selected)?.id ?? s.actions[0]?.id ?? ""); if(!preserveDraft)reset();
  };
  useEffect(()=>{
    if(!targetRequest) return;
    if(locked || dirty) {setError("Finish or reset the current change before choosing another target.");return;}
    if(session && pathKey(session.archive.path)!==pathKey(targetRequest.archive)) {setError("This change set belongs to another archive. Open or create the matching change set first.");return;}
    setLayer(targetRequest.archive_path);setEntry(targetRequest.entry);setKind(targetRequest.kind==="directory"?"rmdir":"replace");
    // Only an explicit captured archive selection changes this draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[targetRequest]);
  const start = async(task:(version:number)=>Promise<void>)=>{
    if(inFlight.current) return;
    const version=++generation.current;inFlight.current=true;setError("");setNotice("");
    try{await task(version);}catch(reason){if(generation.current===version){setError(String(reason));inFlight.current=false;setPhase("idle");}}
  };
  const read = async(operation:"inspect_rpf_change_set"|"review_rpf_change_set",payload:Record<string,unknown>,onResult:(v:unknown)=>void,version:number)=>{
    setPhase("reading");let finished=false;
    const started=await client.startJob(operation,payload,`rpf-change-${version}`,message=>{
      if(generation.current!==version || finished || !message.terminal)return;
      finished=true;job.current="";inFlight.current=false;setPhase("idle");
      try{onResult(unwrap(message));}catch(reason){setError(String(reason));}
    });
    if(generation.current!==version){if(!finished)void client.cancelJob(started.job_id).catch(()=>undefined);return;}
    if(!finished)job.current=started.job_id;
  };
  const cancel=()=>{generation.current++;inFlight.current=false;setPhase("idle");if(job.current)void client.cancelJob(job.current).catch(reason=>setError(String(reason)));job.current="";};
  const choose = (what:"open"|"payload"|"game"|"scope")=>void start(async version=>{
    setPhase("choosing"); const path=await client.selectPath(what==="open"?"rpf_change_set":what==="payload"?"rpf_payload":what==="game"?"gta_folder":"rpf_authorized_root");
    if(generation.current!==version)return;
    if(path && what==="open") await read("inspect_rpf_change_set",{change_set:path},v=>{const s=v as RpfChangeSession;if(pathKey(s.change_set)!==pathKey(path))throw Error("Wrong change-set source returned");load(s);},version);
    else {if(path){if(what==="payload")setPayloadPath(path);if(what==="game")setGame(path);if(what==="scope")setAuthorized(path);}inFlight.current=false;setPhase("idle");}
  });
  const prepare = (action:string,fields:Record<string,unknown>={})=>void start(async version=>{
    let p:Record<string,unknown> = action==="create"?{action,archive:indexed?.source,gta_path:indexed?.gta_path}:
      {action,change_set:session?.change_set,expected_sha256:session?.state_sha256,...fields};
    if(action==="create" || action==="compile"){
      setPhase("choosing");const destination=await client.selectRpfPlanDestination(action==="create"?"rpf-changes.json":"rpf-plan.json");
      if(generation.current!==version)return;
      if(!destination){inFlight.current=false;setPhase("idle");return;}
      p={...p,destination};
      if(action==="compile")p={...p,...(game || indexed?.gta_path ? {gta_path:game || indexed?.gta_path}:{}),...(authorized?{authorized_root:authorized}:{})};
    }
    if(action==="stage")p.change={action:kind,archive_path:layer.replaceAll("\\","/"),entry:entry.replaceAll("\\","/"),...(["add","replace"].includes(kind)?{payload:payloadPath}:{}),...(kind==="rename"?{new_entry:newEntry.replaceAll("\\","/")}:{})};
    await read("review_rpf_change_set",p,result=>{const value=result as RpfChangeReview;if(!validReview(value,p,session))throw Error("Review does not match this change set and target. Nothing was authorized.");setReview({value,payload:p});setConfirmed(false);},version);
  });
  const apply=()=>{
    if(!review || !confirmed || busy)return;
    void start(async version=>{setPhase("writing");try{
      const v=review.value,result=unwrap<Record<string,unknown>>(await client.applyRpfChangeSet({...review.payload,review_sha256:v.review_sha256,authoring_confirmed:true}));
      if(generation.current!==version)return;
      const s=result.session as RpfChangeSession;
      if(result.kind!=="rpf_change_set_applied" || result.action!==v.action || result.review_sha256!==v.review_sha256
          || result.file_write_performed!==true || result.archive_write_performed!==false || result.game_write_performed!==false
          || !SHA.test(String(result.output_sha256)) || pathKey(result.output)!==pathKey(v.destination ?? v.change_set)
          || !validSession(s) || !same(s.archive,v.archive) || !same(s.actions,v.after)
          || pathKey(s.change_set)!==pathKey(v.action==="create"?v.destination:v.change_set)
          || (v.action==="compile" ? s.state_sha256!==v.state_sha256 || result.plan_status!==v.plan?.status : s.state_sha256!==result.output_sha256)) throw Error("Saved evidence could not be verified. Refresh before retrying.");
      load(s,v.action==="create" && dirty);setNotice(`${operations[v.action]} completed.\n${result.output}\nSHA-256: ${result.output_sha256}\n${v.action==="compile"?`Plan status: ${result.plan_status}. Archive execution and rollback remain separate operations.`:"Only the change-set document was saved. No archive was modified."}`);
    }finally{if(generation.current===version){inFlight.current=false;setPhase("idle");setReview(null);setConfirmed(false);}}});
  };
  return <section className="gxt-workspace rpf-change-workspace" aria-labelledby="rpf-change-title">
    <div className="gxt-title"><div><span className="pane-kicker">Archive authoring</span><h2 id="rpf-change-title" ref={workspaceHeading} tabIndex={-1}>RPF change sets</h2><p>Stage file and directory changes, then compile one verified plan. Archives stay unchanged.</p></div>
      <div className="gxt-actions"><button className="quiet-button" disabled={locked || dirty} onClick={()=>choose("open")}>Open change set</button><button className="primary-button" disabled={!indexed || locked || (dirty && !!session)} onClick={()=>prepare("create")}>Create change set</button></div></div>
    {error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status" className="gxt-notice">{notice}</p>}
    {phase==="reading" && <div className="gxt-actions"><span role="status">Reading source and change-set evidence…</span><button className="quiet-button" onClick={cancel}>Cancel review</button></div>}
    <div className="gxt-source"><strong>{session?`${session.actions.length} staged action${session.actions.length===1?"":"s"} · ${session.archive.edition}`:"No change set open"}</strong><code>{session?.change_set || "Create a change set from Archive inspection, or open an existing .json change set."}</code><code>Archive: {session?.archive.path || indexed?.source || "Select an archive in Archive inspection"}</code></div>
    <div className="gxt-panels rpf-change-panels">
      <section className="gxt-pane" aria-label="Staged RPF actions"><header><span className="pane-kicker">Ordered changes</span><h3>Staged actions</h3></header>
        <div className="gxt-rows">{session?.actions.map((row,i)=><button key={row.id} disabled={locked || dirty} aria-pressed={selected===row.id} className={selected===row.id?"selected":""} onClick={()=>setSelected(row.id)}><span>{i+1}. {actions[row.action]}</span><code>{row.archive_path?`${row.archive_path}::`:"::"}{row.entry}</code></button>)}{!session?.actions.length && <p className="gxt-empty">No changes staged. Choose an exact member or enter a new path.</p>}</div>
        <div className="gxt-pagination"><button className="quiet-button" disabled={locked || dirty || position<=0} onClick={()=>prepare("move",{action_id:selected,position})}>Move up</button><button className="quiet-button" disabled={locked || dirty || position<0 || position===(session?.actions.length ?? 0)-1} onClick={()=>prepare("move",{action_id:selected,position:position+2})}>Move down</button><button className="quiet-button" disabled={locked || dirty || !selectedRow} onClick={()=>prepare("remove",{action_id:selected})}>Remove staged</button></div>
      </section>
      <section className="gxt-pane" aria-label="Stage RPF change"><header><span className="pane-kicker">Action draft</span><h3>Stage a change</h3></header><div className="gxt-editor">
        <label>Change type<select value={kind} disabled={locked || !session} onChange={e=>{setKind(e.target.value);setNewEntry("");setPayloadPath("");}}>{Object.entries(actions).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
        <label>Archive layer<input value={layer} disabled={locked || !session} placeholder="Empty for outer archive" maxLength={2048} onChange={e=>setLayer(e.target.value)} /></label>
        <label>Member path<input value={entry} disabled={locked || !session} placeholder="text/global.gxt2" maxLength={2048} onChange={e=>setEntry(e.target.value)} /></label>
        {kind==="rename" && <label>New member path<input value={newEntry} disabled={locked} maxLength={2048} onChange={e=>setNewEntry(e.target.value)} /></label>}
        {["add","replace"].includes(kind) && <div className="rpf-change-payload"><button className="quiet-button" disabled={locked || !session} onClick={()=>choose("payload")}>Choose payload file</button><code>{payloadPath || "No payload selected"}</code></div>}
        <p className="gxt-note">Staging records paths and payload hashes. Compile checks exact targets, conflicts and action compatibility. Renames stay within one directory.</p>
        <div className="gxt-actions"><button className="quiet-button" disabled={locked || !dirty} onClick={reset}>Reset change draft</button><button className="primary-button" disabled={locked || !session || !entry || (["add","replace"].includes(kind) && !payloadPath) || (kind==="rename" && !newEntry)} onClick={()=>prepare("stage")}>Review staged change</button></div>
      </div></section>
      <aside className="gxt-pane" aria-label="RPF change evidence"><header><span className="pane-kicker">Source and payload</span><h3>Selected evidence</h3></header><div className="gxt-evidence">
        {selectedRow?<><strong>{actions[selectedRow.action]}</strong><code>{selectedRow.archive_path || "Outer archive"} → {selectedRow.entry}</code>{selectedRow.new_entry && <><small>Rename destination</small><code>{selectedRow.new_entry}</code></>}{selectedRow.payload && <><small>Payload file</small><code>{selectedRow.payload.path}</code><small>Payload SHA-256 · {selectedRow.payload.size.toLocaleString()} bytes</small><code>{selectedRow.payload.sha256}</code></>}</>:<p>Select a staged action to inspect its stored evidence.</p>}
        {session && <><small>Required source archive SHA-256</small><code>{session.archive.sha256}</code><small>Change-set revision SHA-256</small><code>{session.state_sha256}</code></>}
        <p className="gxt-note">Saved rows are an inert plan draft, not installed changes. Opening a document does not verify every payload; review and compile recheck them.</p>
        <button className="quiet-button" disabled={locked || dirty || !session} onClick={()=>void start(version=>read("inspect_rpf_change_set",{change_set:session?.change_set},v=>load(v as RpfChangeSession),version))}>Refresh change set</button>
      </div></aside>
    </div>
    {session && <section className="gxt-rpf-package" aria-label="Compile RPF plan"><span className="pane-kicker">Verified handoff</span><h3>Compile an atomic plan</h3><p>Re-index the original archive and verify payloads, target ownership and tree conflicts. Export creates a new JSON plan, never an archive write.</p>
      <div className="gxt-copy"><button className="quiet-button" disabled={locked || dirty} onClick={()=>choose("game")}>Choose GTA context</button><code>{game || indexed?.gta_path || "Auto-detect matching GTA installation"}</code></div>
      <div className="gxt-copy"><button className="quiet-button" disabled={locked || dirty} onClick={()=>choose("scope")}>Choose plan workspace folder</button><code>{authorized || "No external execution scope authorized"}</code>{authorized && <button className="quiet-button" disabled={locked || dirty} onClick={()=>setAuthorized("")}>Clear scope</button>}</div>
      <p className="gxt-note">For external archives, explicitly select the folder directly containing the RPF to authorize that scope in the plan. Without it, a blocked plan can still be exported for inspection. This screen cannot execute it.</p>
      <div className="gxt-actions"><button className="primary-button" disabled={locked || dirty || !session.actions.length} onClick={()=>prepare("compile")}>Review compiled plan</button></div>
    </section>}
    {review && <section className="gxt-review" aria-label="RPF change-set review"><h3 ref={heading} tabIndex={-1}>Review: {operations[review.value.action]}</h3><p><strong>Source archive</strong> <code>{review.value.archive.path}</code></p><p><strong>Source SHA-256</strong> <code>{review.value.archive.sha256}</code></p>{review.value.destination && <p><strong>New output</strong> <code>{review.value.destination}</code></p>}
      <p>{review.value.before.length} → {review.value.after.length} staged actions. Only {review.value.action==="compile"?"a new compiled plan":"the change-set document"} will be written.</p>
      {reviewedRow && <p><strong>{review.value.action==="move"?`Move to position ${review.payload.position}`:"Remove from staged list"}</strong><br/><code>{reviewedRow.archive_path || "Outer archive"} → {reviewedRow.entry}</code><br/>{actions[reviewedRow.action]}</p>}
      <ol className="rpf-change-review-list">{review.value.after.map(row=><li key={row.id}><strong>{actions[row.action]}</strong><code>{row.archive_path || "Outer archive"} → {row.entry}{row.new_entry?` → ${row.new_entry}`:""}</code>{row.payload && <><code>{row.payload.path}</code><small>{row.payload.size.toLocaleString()} bytes · SHA-256</small><code>{row.payload.sha256}</code></>}</li>)}</ol>
      {review.value.plan && <><h4>Plan: {review.value.plan.status}</h4><p>Execution scope: {review.value.authorized_root || (review.value.plan.target_scope === "mods_copy" ? "Selected GTA mods directory" : "No external workspace authorized")}</p>{review.value.plan.blocking_reasons.map((reason,i)=><p key={i} className="error-banner">{reason}</p>)}{review.value.plan.warnings.map((warning,i)=><p key={i}>{warning}</p>)}<details><summary>Compiled plan evidence</summary><pre>{JSON.stringify(review.value.plan,null,2)}</pre></details></>}
      <p>Python repeats this review before saving. No RPF or game files are changed. Applying an archive transaction and rollback are separate workflows.</p>
      <label className="gxt-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} />Save this reviewed {review.value.action==="compile"?"plan and its explicit execution scope":"change-set document"}. No archive writes are authorized.</label>
      <div className="gxt-actions"><button className="quiet-button" disabled={busy} onClick={()=>{setReview(null);setConfirmed(false);}}>Back to change set</button><button className="primary-button" disabled={busy || !confirmed} onClick={apply}>{phase==="writing"?"Saving…":operations[review.value.action]}</button></div>
    </section>}
  </section>;
}
