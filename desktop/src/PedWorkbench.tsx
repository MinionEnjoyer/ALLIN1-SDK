import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import { PedPreview } from "./PedNativePreview";
import "./ped-workbench.css";

export const pedFields: Record<string, string> = {
  "ped.pedType": "Ped type", "ped.modelType": "Model type", "ped.propsName": "Props name",
  "ped.clipDictionary": "Clip dictionary", "ped.expressionSet": "Expression set",
  "ped.movementClipSet": "Movement clip set", "ped.creatureMetadata": "Creature metadata",
};
export interface PedRecord {
  name: string; ped_type: string; model_type: string; props_name: string; clip_dictionary: string;
  expression_set: string; movement_clip_set: string; creature_metadata: string; source: string;
}
export interface PedSnapshot {
  kind: "ped_workbench"; source: string; workspace: string | null; revision: number | null; state_sha256: string | null;
  selected_ped: PedRecord | null; selection_unique: boolean; values: Record<string, string> | null;
  selected_index: number | null;
  editable_fields: string[]; can_create: boolean; can_undo: boolean; decoder_edition: string;
  project: { peds: PedRecord[]; edition: string; source_kind: string; findings: { code: string; severity: string; path: string; message: string }[] };
  assets: { path: string; size: number; role: string; link: string; suffix: string; stem: string }[];
  readiness: { system: string; status: string; evidence: string[] }[];
}
export interface PedReview {
  kind: "ped_authoring_review"; action: string; review_sha256: string; destination?: string; source?: string;
  source_sha256?: string; state_sha256?: string; ped_count?: number; copy_bytes?: number; subject?: string; metadata_source?: string;
  changes?: { field: string; before: string; after: string }[]; renames?: { before: string; after: string }[];
  clone_plan?: { ready: boolean; plan_sha256: string; revision: number; spec: { donor_ped: string; ped_name: string; updates: Record<string, string> };
    selected_sources: Record<string, string>; source_sha256: Record<string, string>; additions: { kind: string; name: string; source: string; detail: string }[];
    findings: { severity: string; code: string; message: string }[] };
}
export function pedResult<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message || "Ped operation failed"));
  if (!message.payload.result) throw new Error("Ped operation returned no result");
  return message.payload.result as T;
}
const digest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
type Tab = "definition" | "author" | "identity" | "clone" | "preview" | "assets";

export default function PedWorkbench({ client, onDirtyChange, initialSource = "", onHelp }: {
  client: DesktopClient; onDirtyChange: (dirty: boolean) => void; initialSource?: string; onHelp?: () => void;
}) {
  const [snapshot, setSnapshot] = useState<PedSnapshot | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<Tab>("definition");
  const [newName, setNewName] = useState("");
  const [props, setProps] = useState("");
  const [query, setQuery] = useState("");
  const [name, setName] = useState("ped-workspace");
  const [review, setReview] = useState<{ result: PedReview; payload: Record<string, unknown> } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [asset, setAsset] = useState("");
  const [game, setGame] = useState("");
  const generation = useRef(0);
  const currentJob = useRef("");
  const inFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const filter = useRef<HTMLInputElement>(null);
  const updates = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== snapshot?.values?.[key]));
  const dirty = Object.keys(updates).length > 0 || !!newName || !!props;
  const locked = busy || !!review;
  const ped = snapshot?.selected_ped;
  const writable = !!snapshot?.workspace && snapshot.selection_unique;
  const context = snapshot?.workspace ? { workspace: snapshot.workspace } : { source: snapshot?.source };
  const mutation = { ...context, ped: ped?.name, expected_revision: snapshot?.revision, expected_state_sha256: snapshot?.state_sha256 };
  useEffect(() => { onDirtyChange(dirty || locked); }, [dirty, locked, onDirtyChange]);
  useEffect(() => { if (review) { heading.current?.focus(); heading.current?.scrollIntoView?.({ block: "nearest" }); } }, [review]);
  useEffect(() => () => { generation.current++; if (currentJob.current) void client.cancelJob(currentJob.current); }, [client]);
  const resetDraft = (value = snapshot) => { setDraft(value?.values ?? {}); setNewName(""); setProps(""); setReview(null); setConfirmed(false); };
  const adopt = (value: PedSnapshot) => {
    if (value.kind !== "ped_workbench" || !Array.isArray(value.project?.peds) || !Array.isArray(value.assets)
      || !Array.isArray(value.readiness) || !Array.isArray(value.editable_fields)
      || (value.workspace && (!digest(value.state_sha256) || !Number.isInteger(value.revision)))) throw new Error("Unexpected ped snapshot; refresh the workbench");
    setSnapshot(value); resetDraft(value); setEpoch(e => e + 1); setAsset("");
  };
  const run = async (operation: "inspect_ped_workbench" | "review_ped_authoring", payload: Record<string, unknown>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    const version = ++generation.current;
    let finished = false;
    setBusy(true); setError(""); setNotice("");
    try {
      const started = await client.startJob(operation, payload, `ped-${version}`, message => {
        if (version !== generation.current || !message.terminal || finished) return;
        finished = true; currentJob.current = ""; setJob(""); setBusy(false); inFlight.current = false;
        try {
          if (operation === "inspect_ped_workbench") adopt(pedResult<PedSnapshot>(message));
          else {
            const result = pedResult<PedReview>(message);
            if (result.kind !== "ped_authoring_review" || result.action !== payload.action || !digest(result.review_sha256)) throw new Error("Unexpected ped review");
            if (result.action === "clone" && (!result.clone_plan || !digest(result.clone_plan.plan_sha256)
              || typeof result.clone_plan.ready !== "boolean" || !result.clone_plan.spec || !result.clone_plan.source_sha256
              || !result.clone_plan.selected_sources || !Array.isArray(result.clone_plan.additions) || !Array.isArray(result.clone_plan.findings))) throw new Error("Incomplete ped clone evidence");
            setReview({ result, payload }); setConfirmed(false);
          }
        } catch (reason) { setError(String(reason)); }
      });
      if (version !== generation.current) { if (!finished) void client.cancelJob(started.job_id); return; }
      if (!finished) { currentJob.current = started.job_id; setJob(started.job_id); }
    } catch (reason) {
      if (version === generation.current) { setError(String(reason)); setBusy(false); inFlight.current = false; }
    }
  };
  useEffect(() => {
    const timer = window.setTimeout(() => { if (initialSource) void run("inspect_ped_workbench", { source: initialSource }); }, 0);
    return () => window.clearTimeout(timer);
  }, [initialSource]);
  const choose = async (kind: "package" | "package_folder" | "ped_workspace" | "ped_parent" | "gta_folder") => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = generation.current;
    try {
      const path = await client.selectPath(kind as "package");
      if (version !== generation.current) return;
      inFlight.current = false; setBusy(false);
      if (!path) return;
      if (kind === "gta_folder") { setGame(path); return; }
      if (kind === "ped_parent") await run("review_ped_authoring", { action: "create", source: snapshot?.source, parent: path, name });
      else await run("inspect_ped_workbench", { ...(kind === "ped_workspace" ? { workspace: path } : { source: path }), ...(game ? { gta_path: game } : {}) });
    } catch (reason) { if (version === generation.current) { setError(String(reason)); setBusy(false); inFlight.current = false; } }
  };
  const cancel = () => {
    const id = currentJob.current;
    generation.current++; currentJob.current = ""; setJob(""); inFlight.current = false; setBusy(false);
    if (id) void client.cancelJob(id).catch(reason => setError(String(reason)));
    setNotice("Read-only operation cancelled.");
  };
  const apply = async () => {
    if (!review || !confirmed || inFlight.current || (review.result.action === "clone" && !review.result.clone_plan?.ready)) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = ++generation.current;
    try {
      const result = await client.applyPedAuthoring({ ...review.payload, review_sha256: review.result.review_sha256, authoring_confirmed: true });
      if (version !== generation.current) return;
      adopt(pedResult<PedSnapshot>(result)); setQuery(""); setTab("author");
      setNotice("Saved and revalidated in the editable copy. The original package and GTA files were not changed.");
    } catch (reason) {
      if (version === generation.current) { setError(String(reason)); setReview(null); setConfirmed(false); }
    } finally { if (version === generation.current) { inFlight.current = false; setBusy(false); } }
  };
  const records = snapshot?.project.peds.filter(p => `${p.name} ${p.ped_type} ${p.model_type} ${p.movement_clip_set} ${p.props_name} ${p.clip_dictionary} ${p.expression_set}`.toLowerCase().includes(query.toLowerCase())) ?? [];
  const blocked = review?.result.action === "clone" && review.result.clone_plan?.ready !== true;
  return <section className="ped-workbench" aria-label="Ped Workbench" onKeyDown={event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f" && !locked && !dirty) {
      event.preventDefault(); setLeftOpen(true); window.setTimeout(() => { filter.current?.focus(); filter.current?.select(); }, 0);
    } else if (event.key === "Escape" && !locked && !dirty) { event.preventDefault(); setQuery(""); }
  }}>
    <div className="ped-toolbar"><div><h3>Ped Workbench</h3><p>Inspect definitions and assets. Author inside a separate, revisioned copy.</p></div>
      <div className="heading-actions"><button className="primary-button" disabled={locked || dirty} onClick={() => void choose("package_folder")}>Open ped folder</button>
        <button className="quiet-button" disabled={locked || dirty} onClick={() => void choose("package")}>Open archive</button>
        <button className="quiet-button" disabled={locked || dirty} onClick={() => void choose("ped_workspace")}>Open editable copy</button>
        <button className="quiet-button" disabled={locked || dirty} onClick={() => void choose("gta_folder")}>Decoder game folder</button>
        {onHelp && <button className="quiet-button" onClick={onHelp} disabled={locked || dirty}>Help</button>}
        {job && <button className="quiet-button" onClick={cancel}>Cancel inspection</button>}
      </div>
    </div>
    {game && <p className="ped-context">Decoder resources only · {game}</p>}
    <div className="source-strip"><strong>{busy ? "Working…" : snapshot?.workspace ? `Editable copy · revision ${snapshot.revision}${dirty ? " · unapplied changes" : ""}` : "Read-only inspection"}</strong><span className="source-path">{snapshot?.workspace || snapshot?.source || "No ped package selected"}</span></div>
    {error && <p className="error-banner" role="alert">{error}</p>}{notice && <p className="action-notice" role="status">{notice}</p>}
    {snapshot && <div className="ped-copy-bar">
      {snapshot.can_create && <><label>Workspace name<input value={name} disabled={locked || dirty} onChange={e => setName(e.target.value)} maxLength={80} /></label><button className="quiet-button" disabled={locked || dirty} onClick={() => void choose("ped_parent")}>Create editable copy</button></>}
      <button className="quiet-button" disabled={locked || dirty} onClick={() => void run("inspect_ped_workbench", { ...context, ...(game ? { gta_path: game } : {}) })}>Refresh catalog</button>
      {snapshot.workspace && <button className="quiet-button" disabled={locked || dirty || !snapshot.can_undo} onClick={() => void run("review_ped_authoring", { ...mutation, action: "undo" })}>Review undo latest</button>}
      {dirty && <button className="quiet-button" disabled={locked} onClick={() => resetDraft()}>Discard unapplied changes</button>}
      {snapshot.project.source_kind === "rpf" && <p>Direct RPF is inspection-only. Extract a reviewed source tree before authoring.</p>}
    </div>}
    <div className={`ped-panes ${leftOpen ? "" : "ped-catalog-closed"} ${rightOpen ? "" : "ped-evidence-closed"}`}>
      <section className="ped-pane" aria-label="Ped catalog"><header><div hidden={!leftOpen}><span className="pane-kicker">Package</span><h4>Peds <small>{records.length}</small></h4></div><button className="ped-toggle" aria-label={leftOpen ? "Collapse ped catalog" : "Expand ped catalog"} aria-expanded={leftOpen} onClick={() => setLeftOpen(!leftOpen)}>{leftOpen ? "‹" : "›"}</button></header>
        <div className="ped-pane-body" hidden={!leftOpen}><label>Filter peds<input ref={filter} value={query} disabled={locked || dirty} onChange={e => setQuery(e.target.value)} placeholder="Model, type or movement" /></label>
          {query && <button className="text-action" disabled={locked || dirty} onClick={() => setQuery("")}>Clear filter</button>}
          {!records.length && <p className="ped-empty">{snapshot ? "No matching peds.meta records." : "Open a package to inspect its ped definitions."}</p>}
          <div className="ped-catalog">{records.map((p, index) => <button key={`${p.source}:${p.name}:${index}`} disabled={locked || dirty} aria-pressed={snapshot?.selected_index === snapshot?.project.peds.indexOf(p)}
            onClick={() => void run("inspect_ped_workbench", { ...context, ped: p.name, metadata_source: p.source, record_index: snapshot?.project.peds.indexOf(p), ...(game ? { gta_path: game } : {}) })}>
            <strong>{p.name}</strong><span>{p.ped_type || "No ped type"} · {p.model_type || "No model type"}</span><small>{p.source}</small></button>)}</div>
        </div></section>
      <section className="ped-pane" aria-label="Ped project"><header><div><span className="pane-kicker">{writable ? "Authoring" : "Inspection"}</span><h4>{ped?.name || "Ped project"}</h4></div></header>
        <div className="ped-pane-body"><label>Ped section<select value={tab} disabled={locked || dirty} onChange={e => { setTab(e.target.value as Tab); setAsset(""); }}>
          <option value="definition">Definition</option><option value="author">Author fields</option><option value="identity">Identity + assets</option><option value="clone">New from template</option><option value="preview">Preview</option><option value="assets">Asset family</option></select></label>
          {!ped ? <p className="ped-empty">Select a ped to inspect its project.</p> : <>
            {!snapshot?.selection_unique && <p role="status">This identity is defined more than once. Inspection is available; authoring is blocked until the conflict is resolved.</p>}
            {tab === "definition" && <dl className="ped-definitions">{Object.entries(snapshot?.values ?? {}).map(([key, value]) => <div key={key}><dt>{pedFields[key]}</dt><dd>{value || "Not declared"}</dd></div>)}<div><dt>Source</dt><dd>{ped.source}</dd></div></dl>}
            {tab === "author" && <><p>Only existing XML fields are editable. Identity changes use the separate asset migration. Unknown fields and representation are preserved.</p>
              {Object.entries(pedFields).map(([key, label]) => <label key={key}>{label}<input value={draft[key] ?? ""} maxLength={160} disabled={locked || !snapshot?.editable_fields.includes(key)} onChange={e => setDraft({ ...draft, [key]: e.target.value })} />{writable && !snapshot?.editable_fields.includes(key) && <small>Node not present; it will not be synthesized.</small>}</label>)}
              {!writable && <p>Create an editable copy to change metadata.</p>}<button className="primary-button" disabled={!writable || locked || !Object.keys(updates).length} onClick={() => void run("review_ped_authoring", { ...mutation, action: "edit", updates })}>Review field changes</button>
            </>}
            {(tab === "identity" || tab === "clone") && <><p>{tab === "clone" ? "Clone this complete metadata record, including unknown fields. The new model and texture family must already exist in this copy; native bytes are not relabeled." : "Rename the selected identity and exact package-owned assets together. Incomplete families and destination conflicts block the transaction."}</p>
              <label>New model identity<input value={newName} maxLength={160} disabled={locked || !writable} onChange={e => setNewName(e.target.value)} /></label>
              <label>New props identity (optional)<input value={props} maxLength={160} disabled={locked || !writable} onChange={e => setProps(e.target.value)} /><small>Blank uses the backend’s existing props relationship; the review shows the result.</small></label>
              <button className="primary-button" disabled={!writable || locked || !newName.trim()} onClick={() => void run("review_ped_authoring", { ...mutation, action: tab === "clone" ? "clone" : "migrate", new_name: newName, new_props: props || null })}>{tab === "clone" ? "Review ped clone" : "Review identity migration"}</button>
            </>}
            {tab === "preview" && <p>The diagnostic drawable view and actual texture sheet appear below. This is not an assembled, animated ped or an in-game compatibility test.</p>}
            {tab === "assets" && <><p>Exact identity matches and broader name candidates are distinguished. A name candidate is not a resolved dependency.</p><div className="ped-catalog">{snapshot?.assets.map(a => <button key={a.path} disabled={locked} aria-pressed={asset === a.path} onClick={() => setAsset(a.path)}><strong>{a.path}</strong><span>{a.role} · {a.size.toLocaleString()} bytes</span><small>{a.link}</small></button>)}</div>{!snapshot?.assets.length && <p>No visible assets in this family.</p>}</>}
          </>}
        </div></section>
      <aside className="ped-pane" aria-label="Ped integration"><header><div hidden={!rightOpen}><span className="pane-kicker">Evidence</span><h4>Integration</h4></div><button className="ped-toggle" aria-label={rightOpen ? "Collapse ped integration" : "Expand ped integration"} aria-expanded={rightOpen} onClick={() => setRightOpen(!rightOpen)}>{rightOpen ? "›" : "‹"}</button></header>
        <div className="ped-pane-body" hidden={!rightOpen}><p className="ped-boundary">Presence and declarations only. Runtime compatibility, YMT capacity and in-game acceptance are not measured here.</p>
          {snapshot?.readiness.map(row => <div className="ped-evidence" key={row.system}><strong>{row.system}</strong><span>{row.status}</span>{row.evidence.map((e, index) => <code key={index}>{e}</code>)}</div>)}
          {snapshot && <details><summary>Package findings · {snapshot.project.findings.length}</summary><div className="ped-findings">{snapshot.project.findings.map((f, index) => <p key={index}><strong>{f.severity} · {f.code}</strong><span>{f.message}</span><code>{f.path}</code></p>)}{!snapshot.project.findings.length && <p>No package findings. This is not runtime acceptance.</p>}</div></details>}
        </div></aside>
    </div>
    {review && <section className="ped-review" aria-label="Ped action review"><h4 ref={heading} tabIndex={-1}>Review {review.result.action === "migrate" ? "identity migration" : review.result.action}</h4>
      <p>Only the copied workspace is changed. Apply rechecks the reviewed bytes and revision.</p>
      {review.result.destination && <p>Create {review.result.ped_count} ped definition(s) · {review.result.copy_bytes?.toLocaleString()} bytes<code>{review.result.source}</code><code>→ {review.result.destination}</code></p>}
      {review.result.subject && <p>Undo latest operation for <strong>{review.result.subject}</strong>. The table shows the original operation that will be reversed.</p>}
      {review.result.changes?.length ? <table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>{review.result.changes.map((c, index) => <tr key={index}><th>{pedFields[c.field] || c.field}</th><td>{c.before || "(empty)"}</td><td>{c.after || "(empty)"}</td></tr>)}</tbody></table> : null}
      {!!review.result.renames?.length && <><h5>Exact asset paths{review.result.action === "undo" ? " to restore" : " to rename"}</h5><ul>{review.result.renames.map(r => <li key={r.before}><code>{review.result.action === "undo" ? `${r.after} → ${r.before}` : `${r.before} → ${r.after}`}</code></li>)}</ul></>}
      {review.result.clone_plan && <div className="ped-clone-review"><p><strong>{review.result.clone_plan.ready ? "Metadata clone ready for confirmation" : "Clone blocked"}</strong>{review.result.clone_plan.spec.donor_ped} → {review.result.clone_plan.spec.ped_name}</p>
        <dl className="ped-definitions">{Object.entries(review.result.clone_plan.spec.updates).map(([field, value]) => <div key={field}><dt>{pedFields[field] || field}</dt><dd>{value || "(empty)"}</dd></div>)}</dl>
        {review.result.clone_plan.additions.map((a, index) => <p key={index}><strong>{a.name}</strong>{a.detail}<code>{a.source}</code></p>)}
        <h5>Required sources</h5>{Object.entries(review.result.clone_plan.selected_sources).map(([role, path]) => <p key={role}><strong>{role.replaceAll("_", " ")}</strong><code>{path}</code><code>{review.result.clone_plan?.source_sha256[path]}</code></p>)}
        {review.result.clone_plan.findings.map((f, index) => <p key={index}><strong>{f.severity} · {f.code}</strong>{f.message}</p>)}
      </div>}
      <code>Review SHA-256 · {review.result.review_sha256}</code>
      <label className="ped-confirm"><input type="checkbox" checked={confirmed} disabled={busy || blocked} onChange={e => setConfirmed(e.target.checked)} />I confirm this exact copied-workspace action.</label>
      <div className="heading-actions"><button className="primary-button" disabled={!confirmed || busy || blocked} onClick={() => void apply()}>Apply reviewed action</button><button className="quiet-button" disabled={busy} onClick={() => { setReview(null); setConfirmed(false); }}>Back to editing</button></div>
    </section>}
    {snapshot && ped && !locked && (tab === "preview" || (tab === "assets" && asset)) && <PedPreview key={`${epoch}:${tab}:${asset}`} client={client} snapshot={snapshot} asset={tab === "assets" ? asset : ""} gtaPath={game} />}
  </section>;
}
