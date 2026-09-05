import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import { CloneRecordList, emptyCloneDraft, WeaponCloneForm, WeaponCloneReview } from "./WeaponClone";
import type { CloneRecord, WeaponCloneDraft, WeaponClonePlan } from "./WeaponClone";
import { WeaponCamera } from "./WeaponCamera";
import { WeaponFireRate, rpmKey, intervalKey } from "./WeaponFireRate";
import type { CameraField } from "./WeaponCamera";
import { WeaponAnimations } from "./WeaponAnimations";
import type { AnimationDraft, AnimationRecord } from "./WeaponAnimations";
import { WeaponNativePreview } from "./WeaponNativePreview";
import type { WeaponPreviewLinks } from "./WeaponNativePreview";
import "./weapon-workbench.css";

export interface WeaponSnapshot {
  kind: "weapon_workbench"; source: string; workspace: string | null; revision: number | null;
  selected_weapon: string | null; editable_fields: string[]; can_undo: boolean;
  camera_fields?: CameraField[];
  native_preview?: WeaponPreviewLinks;
  editor_kind: "weapon" | "component" | "attachment" | "shop" | "animation";
  shop_sources: string[];
  shop_values: { weapon: string; values: Record<string, string>; source: string; affected_weapons: string[];
    identity_field: string; identity_representation: string; representations: Record<string, string> } | null;
  relationship_editable_fields: string[];
  component_values: { component: string; values: Record<string, string>; source: string; affected_weapons: string[] } | null;
  attachment_values: { weapon: string; component: string; values: Record<string, string>; source: string; affected_weapons: string[]; other_defaults: string[] } | null;
  project: {
    edition: string; summary: Record<string, number>;
    weapons: { name: string; model: string; ammo_info: string; source: string }[];
    components: { name: string; model: string; component_type: string; source: string }[];
    attachments: { weapon_name: string; component_name: string; attach_bone: string; default: boolean }[];
    animation_records: AnimationRecord[];
    findings: { severity: string; code: string; message: string; path?: string }[];
  };
  values: { weapon: string; values: Record<string, string>; sources: Record<string, string>; affected_weapons: string[] } | null;
  assets: { path: string; size: number }[];
}
interface Review {
  kind: "weapon_authoring_review"; action: "create" | "edit" | "edit_component" | "edit_attachment" | "edit_shop" | "clone_animation" | "clone" | "undo"; review_sha256: string;
  clone_plan?: WeaponClonePlan; removed_records?: CloneRecord[];
  component?: string; weapon?: string; attach_bone?: string; subject?: string; source?: string; template_weapon?: string;
  destination?: string; weapon_count?: number; affected_weapons?: string[];
  changes?: { field: string; before: string; after: string; source?: string; set?: string }[];
}
const labels: Record<string, string> = {
  [rpmKey]: "Fire rate (RPM)", [intervalKey]: "Time between shots (seconds)",
  "weapon.slot": "Inventory slot", "weapon.ammoInfo": "Ammo definition", "weapon.model": "Model name",
  "weapon.humanNameHash": "Display-name key", "weapon.statName": "Stat-name key",
  "ammo.model": "Ammo model", "ammo.ammoMax": "Maximum ammo", "ammo.ammoMax50": "50% ammo cap",
  "ammo.explosion": "Explosion identifier", "ammo.trailFx": "Trail effect", "ammo.primedFx": "Primed effect",
  "component.model": "Component model", "component.locName": "Component name key",
  "component.locDesc": "Component description key", "component.attachBone": "Component bone",
  "component.type": "Component type (locked)", "attachment.attachBone": "Attachment point (locked)",
  "attachment.default": "Default attachment",
  "shop.cost": "Weapon price (GTA shop)", "shop.ammoCost": "Ammo price (GTA shop)",
  "shop.textLabel": "Shop display-name key", "shop.weaponDesc": "Shop description key",
  "shop.weaponTT": "Shop tooltip key", "shop.weaponUppercase": "Shop uppercase-name key",
  "shop.availableInSP": "Available in single-player shop", "animation.mapping": "Animation mapping",
};
function editorData(snapshot: WeaponSnapshot | null) {
  if (snapshot?.editor_kind === "component") return snapshot.component_values;
  if (snapshot?.editor_kind === "attachment") return snapshot.attachment_values;
  if (snapshot?.editor_kind === "shop") return snapshot.shop_values;
  if (snapshot?.editor_kind === "animation") return null;
  return snapshot?.values;
}
function completed<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message || "Weapon operation failed"));
  if (!message.payload.result) throw new Error("Weapon operation returned no result");
  return message.payload.result as T;
}

export default function WeaponWorkbench({ client, onDirtyChange, initialSource = "" }: {
  client: DesktopClient; onDirtyChange: (dirty: boolean) => void; initialSource?: string;
}) {
  const [snapshot, setSnapshot] = useState<WeaponSnapshot | null>(null);
  const [snapshotEpoch, setSnapshotEpoch] = useState(0);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [cloneDraft, setCloneDraft] = useState<WeaponCloneDraft | null>(null);
  const [animationDraft, setAnimationDraft] = useState<AnimationDraft | null>(null);
  const [query, setQuery] = useState("");
  const [inventoryMode, setInventoryMode] = useState("weapons");
  const [name, setName] = useState("weapon-workspace");
  const [shared, setShared] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [review, setReview] = useState<{ result: Review; payload: Record<string, unknown> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const currentJob = useRef("");
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const data = editorData(snapshot);
  const editorKind = snapshot?.editor_kind ?? "weapon";
  const editableFields = (editorKind === "weapon" ? snapshot?.editable_fields : snapshot?.relationship_editable_fields) ?? [];
  const updates = Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== data?.values[key]));
  const dirty = Object.keys(updates).length > 0 || cloneDraft !== null || animationDraft !== null;
  const locked = busy || Boolean(review);
  useEffect(() => { onDirtyChange(dirty || locked); }, [dirty, locked, onDirtyChange]);
  useEffect(() => { if (review) reviewHeading.current?.focus(); }, [review]);
  useEffect(() => () => { generation.current++; if (currentJob.current) void client.cancelJob(currentJob.current); }, [client]);
  const adopt = (value: WeaponSnapshot) => {
    if (value.kind !== "weapon_workbench" || !Array.isArray(value.project?.weapons)) {
      throw new Error("Unexpected weapon inspection response. Refresh the workbench and try again.");
    }
    setSnapshot(value); setSnapshotEpoch(epoch => epoch + 1); setDraft(editorData(value)?.values ?? {}); setShared(false); setReview(null); setConfirmed(false); setCloneDraft(null); setAnimationDraft(null);
    const nextInventory = value.editor_kind === "component" ? "components" : "weapons";
    if (nextInventory !== inventoryMode) setQuery("");
    setInventoryMode(nextInventory);
  };
  const run = async (operation: "inspect_weapon_workbench" | "review_weapon_authoring", payload: Record<string, unknown>) => {
    const version = ++generation.current;
    let finished = false;
    setBusy(true); setError(""); setNotice("");
    try {
      const started = await client.startJob(operation, payload, `weapon-${version}`, (message) => {
        if (generation.current !== version || !message.terminal) return;
        finished = true; currentJob.current = ""; setJobId(""); setBusy(false);
        try {
          if (operation === "inspect_weapon_workbench") adopt(completed<WeaponSnapshot>(message));
          else {
            const result = completed<Review>(message);
            if (result.kind !== "weapon_authoring_review" || !result.review_sha256 || result.action !== payload.action) throw new Error("Unexpected weapon review response");
            if (result.action === "clone" && (!result.clone_plan || typeof result.clone_plan.ready !== "boolean"
              || !result.clone_plan.spec || !result.clone_plan.donor_completeness || !result.clone_plan.selected_sources
              || !Array.isArray(result.clone_plan.additions) || !Array.isArray(result.clone_plan.collisions)
              || !Array.isArray(result.clone_plan.findings) || !Array.isArray(result.clone_plan.reused_components)
              || !/^[a-f0-9]{64}$/.test(result.clone_plan.plan_sha256))) throw new Error("Unexpected weapon clone plan response");
            setReview({ result, payload }); setConfirmed(false);
          }
        } catch (reason) { setError(String(reason).replace(/^Error:\s*/, "")); }
      });
      if (generation.current !== version) { if (!finished) void client.cancelJob(started.job_id); return; }
      if (!finished) { currentJob.current = started.job_id; setJobId(started.job_id); }
    } catch (reason) {
      if (generation.current === version) { setError(String(reason).replace(/^Error:\s*/, "")); setBusy(false); }
    }
  };
  useEffect(() => {
    // Defer until after StrictMode's setup/cleanup probe so only one job starts.
    const timer = window.setTimeout(() => { if (initialSource) void run("inspect_weapon_workbench", { source: initialSource }); }, 0);
    return () => window.clearTimeout(timer);
  }, [initialSource]);
  const context = snapshot?.workspace ? { workspace: snapshot.workspace } : { source: snapshot?.source };
  const selection = { editor_kind: editorKind, weapon: snapshot?.selected_weapon,
    metadata_source: snapshot?.shop_values?.source,
    component: snapshot?.component_values?.component ?? snapshot?.attachment_values?.component };
  const reviewChanges = () => run("review_weapon_authoring", { ...context, ...selection,
    action: editorKind === "component" ? "edit_component" : editorKind === "attachment" ? "edit_attachment" : editorKind === "shop" ? "edit_shop" : "edit",
    expected_revision: snapshot?.revision, updates, acknowledge_shared: shared });
  const choose = async (workspace: boolean) => {
    try {
      const path = await client.selectPath(workspace ? "weapon_workspace" : "weapon_package");
      if (path) await run("inspect_weapon_workbench", workspace ? { workspace: path } : { source: path });
    } catch (reason) { setError(String(reason)); }
  };
  const create = async () => {
    try {
      const parent = await client.selectPath("weapon_parent");
      if (parent) await run("review_weapon_authoring", { action: "create", source: snapshot?.source, parent, name });
    } catch (reason) { setError(String(reason)); }
  };
  const reviewClone = () => {
    if (!cloneDraft || !snapshot?.workspace || !snapshot.selected_weapon) return;
    const spec = Object.fromEntries(Object.entries(cloneDraft).map(([key, value]) => [key, typeof value === "string" ? value.trim() : value]));
    void run("review_weapon_authoring", { ...context, action: "clone", expected_revision: snapshot.revision,
      spec: { ...spec, donor_weapon: snapshot.selected_weapon, ammo_name: cloneDraft.clone_ammo ? spec.ammo_info : null } });
  };
  const blockedPlan = review?.result.action === "clone" && review.result.clone_plan?.ready !== true;
  const apply = async () => {
    if (!review || !confirmed || busy || blockedPlan) return;
    setBusy(true); setError("");
    try {
      const response = await client.applyWeaponAuthoring({ ...review.payload, review_sha256: review.result.review_sha256, authoring_confirmed: true });
      adopt(completed<WeaponSnapshot>(response));
      if (review.result.action === "clone" || review.result.removed_records) setQuery("");
      setNotice(review.result.action === "clone" ? "Weapon bundle created and validated in the editable copy. Donor records, original package, and game files were not changed."
        : "Saved to the editable copy. Original package and game files were not changed.");
    } catch (reason) {
      setError(String(reason).replace(/^Error:\s*/, "")); setReview(null); setConfirmed(false);
    } finally { setBusy(false); }
  };
  const cancel = async () => {
    const id = currentJob.current;
    if (!id) return;
    generation.current++;
    try { await client.cancelJob(id); setNotice("Read-only operation cancelled."); }
    catch (reason) { setError(String(reason)); }
    finally { currentJob.current = ""; setJobId(""); setBusy(false); }
  };
  const inventory = (inventoryMode === "components" ? snapshot?.project.components : snapshot?.project.weapons) ?? [];
  const records = inventory.filter(w => `${w.name} ${w.model}`.toLowerCase().includes(query.toLowerCase()));
  const attachments = snapshot?.project.attachments.filter(a => editorKind === "component"
    ? a.component_name.toLowerCase() === snapshot.component_values?.component.toLowerCase()
    : a.weapon_name.toLowerCase() === snapshot.selected_weapon?.toLowerCase()) ?? [];
  const ammoChanged = Object.keys(updates).some(key => key.startsWith("ammo."));
  const sharedDefinition = (data?.affected_weapons.length ?? 0) > 1;
  const needsSharedAck = sharedDefinition && (editorKind === "component" || ammoChanged);
  const identity = editorKind === "component" ? snapshot?.component_values?.component
    : editorKind === "attachment" ? `${snapshot?.attachment_values?.weapon} / ${snapshot?.attachment_values?.component}`
    : snapshot?.selected_weapon;
  const sources = editorKind === "weapon" ? snapshot?.values?.sources
    : data && "source" in data ? { [editorKind]: data.source } : {};
  return <section className="weapon-workbench" aria-label="Weapon Workbench">
    <div className="weapon-toolbar">
      <div><h3>Weapon Workbench</h3><p>Inspect an unpacked package. Author only inside a separate, revisioned copy.</p></div>
      <div className="heading-actions">
        <button className="primary-button" onClick={() => void choose(false)} disabled={locked || dirty}>Open weapon folder</button>
        <button className="quiet-button" onClick={() => void choose(true)} disabled={locked || dirty}>Open editable copy</button>
        {snapshot && <button className="quiet-button" onClick={() => void run("inspect_weapon_workbench", { ...context, ...selection })} disabled={locked || dirty}>Refresh weapons</button>}
        {jobId && <button className="danger-button" onClick={() => void cancel()}>Cancel inspection</button>}
      </div>
    </div>
    <div className="source-strip"><strong>{busy ? "Working…" : snapshot?.workspace ? `Editable copy · Revision ${snapshot.revision}` : snapshot ? "Read-only package" : "No weapon package selected"}</strong><span className="source-path">{snapshot?.workspace || snapshot?.source || "Launcher and GTA V are not required for metadata authoring"}</span></div>
    {error && <p className="weapon-message error" role="alert">{error}</p>}
    {notice && <p className="weapon-message" role="status">{notice}</p>}
    {snapshot && !snapshot.workspace && snapshot.project.weapons.length > 0 && <div className="weapon-copy-bar">
      <label>Workspace name<input value={name} onChange={e => setName(e.target.value)} disabled={locked} /></label>
      <button className="quiet-button" onClick={() => void create()} disabled={locked || !name.trim()}>Review editable copy</button>
      <span>The original folder stays untouched.</span>
    </div>}
    {review && <section className="weapon-review" aria-label="Weapon change review">
      <h4 ref={reviewHeading} tabIndex={-1}>{review.result.action === "create" ? "Create editable copy" : review.result.action === "clone" ? "Review new weapon bundle" : review.result.action === "undo" ? "Restore previous revision" : review.result.action === "edit_component" ? "Review component changes" : review.result.action === "edit_attachment" ? "Review attachment changes" : review.result.action === "edit_shop" ? "Review GTA shop changes" : review.result.action === "clone_animation" ? "Review animation mappings" : "Review weapon changes"}</h4>
      {review.result.source && <p className="weapon-review-source"><strong>{review.result.weapon}</strong><code>{review.result.source}</code></p>}
      {review.result.action === "edit_shop" && <p>These values belong to GTA shop metadata, not the ALLIN1 GBAY catalog. GBAY prices and listings will not change.</p>}
      {(review.result.component || review.result.subject) && <p><strong>{review.result.subject || review.result.component}</strong>{review.result.weapon && <span>Weapon: {review.result.weapon}</span>}{review.result.attach_bone && <span>Attachment point: {review.result.attach_bone}</span>}</p>}
      {review.result.destination && <p>{review.result.weapon_count} weapons → <code>{review.result.destination}</code></p>}
      {review.result.clone_plan && <WeaponCloneReview plan={review.result.clone_plan} />}
      {review.result.removed_records && <><p>Undo removes the cloned records below and restores their source files. The donor and reused component definitions remain.</p><CloneRecordList records={review.result.removed_records} label="Records to remove" /></>}
      {!review.result.removed_records && review.result.changes?.length ? <table><thead><tr><th>Field / set</th><th>{review.result.action === "clone_animation" ? "Copy from template" : "Before"}</th><th>{review.result.action === "clone_animation" ? "Add mapping for" : "After"}</th></tr></thead><tbody>{review.result.changes.map((c, i) => <tr key={`${c.field}-${i}`}><th>{labels[c.field] || snapshot?.camera_fields?.find(field => field.key === c.field)?.label || c.field}{c.set && <small>{c.set}</small>}{c.source && <code>{c.source}</code>}</th><td>{review.result.action === "undo" ? c.after : c.before}</td><td>{review.result.action === "undo" ? c.before : c.after}</td></tr>)}</tbody></table> : null}
      {review.result.action === "undo" && review.result.changes?.some(change => change.field === "animation.mapping") && <p>Restore removes the added target mappings. The template mappings remain unchanged.</p>}
      {review.result.affected_weapons && <p>Affected weapons: {review.result.affected_weapons.join(", ")}</p>}
      <p>Python rechecks the reviewed source contents and relationships during the transactional save. No game installation or publication is performed.</p>
      <label className="weapon-checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} disabled={busy || blockedPlan} />I confirm this change to the editable copy only.</label>
      <div className="heading-actions"><button className="quiet-button" onClick={() => { setReview(null); setConfirmed(false); }} disabled={busy}>Cancel review</button><button className="primary-button" onClick={() => void apply()} disabled={busy || !confirmed || blockedPlan}>Confirm {review.result.action === "create" ? "copy" : review.result.action === "clone" ? "clone" : review.result.action === "undo" ? "restore" : "save"}</button></div>
    </section>}
    {snapshot && <WeaponNativePreview client={client} snapshot={snapshot} epoch={snapshotEpoch} dirty={dirty} />}
    <div className="weapon-panes">
      <section className="weapon-pane" aria-label="Weapon inventory">
        <header><span className="pane-kicker">Package</span><h4>{inventoryMode === "components" ? "Components" : "Weapons"} <span>{inventory.length}</span></h4></header>
        <div className="weapon-pane-body">
          <label>Browse definitions<select value={inventoryMode} onChange={e => { setInventoryMode(e.target.value); setQuery(""); }} disabled={locked || dirty || !snapshot}><option value="weapons">Weapons</option><option value="components">Components</option></select></label>
          <label>Filter {inventoryMode}<input value={query} onChange={e => setQuery(e.target.value)} disabled={locked || dirty || !snapshot} /></label>
          {!records.length && <p className="weapon-empty">{snapshot ? "No matching definitions." : "Open a folder containing weapons.meta to begin."}</p>}
          <div className="weapon-inventory">{records.map(w => {
            const selected = inventoryMode === "components" ? snapshot?.component_values?.component === w.name : snapshot?.selected_weapon === w.name;
            return <button key={`${w.name}-${w.source}`} className={selected ? "selected" : ""} aria-pressed={selected} disabled={locked || dirty}
              onClick={() => void run("inspect_weapon_workbench", inventoryMode === "components"
                ? { ...context, editor_kind: "component", component: w.name, weapon: snapshot?.selected_weapon }
                : { ...context, editor_kind: ["shop", "animation"].includes(editorKind) ? editorKind : "weapon", weapon: w.name })}><strong>{w.name}</strong><small>{w.model || "No model reference"}</small></button>;
          })}</div>
        </div>
      </section>
      <section className="weapon-pane" aria-label="Weapon metadata">
        <header><span className="pane-kicker">{snapshot?.workspace ? "Authoring" : "Inspection"}</span><h4>{cloneDraft ? "New from template" : editorKind === "component" ? "Component definition" : editorKind === "attachment" ? "Attachment link" : editorKind === "shop" ? "GTA shop metadata" : editorKind === "animation" ? "Animation mappings" : "Weapon & ammo"}</h4></header>
        <div className="weapon-pane-body">
          {snapshot?.selected_weapon && editorKind !== "component" && editorKind !== "attachment" && <label>Weapon section<select
            value={editorKind} disabled={locked || dirty} onChange={event => void run("inspect_weapon_workbench", { ...context, weapon: snapshot.selected_weapon, editor_kind: event.target.value })}>
            <option value="weapon">Weapon &amp; ammo</option><option value="shop">GTA shop metadata</option><option value="animation">Animation mappings</option>
          </select></label>}
          {!data && editorKind !== "shop" && editorKind !== "animation" && <p className="weapon-empty">Select a definition or an attachment link to inspect its existing metadata.</p>}
          {editorKind === "shop" && snapshot && <>
            <p>Edits existing GTA shop pricing, availability, and text keys. These are not GBAY catalog settings; GBAY prices and listings are managed separately.</p>
            {snapshot.shop_sources.length > 0 ? <label>Shop source<select value={snapshot.shop_values?.source ?? ""} disabled={locked || dirty}
              onChange={event => void run("inspect_weapon_workbench", { ...context, ...selection, metadata_source: event.target.value })}>
              <option value="" disabled>Choose an exact shop source</option>{snapshot.shop_sources.map(source => <option key={source}>{source}</option>)}
            </select></label> : <p>No shop record exists for this weapon. This editor does not synthesize a shop entry.</p>}
            {!snapshot.shop_values && snapshot.shop_sources.length > 1 && <p>Multiple sources define this weapon. Choose the exact record to inspect; no source is selected automatically.</p>}
          </>}
          {editorKind === "animation" && snapshot?.selected_weapon && <>
            <WeaponAnimations weapon={snapshot.selected_weapon} records={snapshot.project.animation_records} draft={animationDraft}
              editable={Boolean(snapshot.workspace)} disabled={locked} onChange={setAnimationDraft} onReset={() => setAnimationDraft(null)}
              onReview={() => void run("review_weapon_authoring", { ...context, action: "clone_animation", weapon: snapshot.selected_weapon,
                template_weapon: animationDraft?.template, metadata_source: animationDraft?.source, expected_revision: snapshot.revision })} />
            {snapshot.workspace && <button className="quiet-button" disabled={dirty || locked || !snapshot.can_undo}
              onClick={() => void run("review_weapon_authoring", { ...context, action: "undo", expected_revision: snapshot.revision })}>Review undo</button>}
          </>}
          {cloneDraft && snapshot?.selected_weapon && <WeaponCloneForm donor={snapshot.selected_weapon} draft={cloneDraft}
            disabled={locked} onChange={setCloneDraft} onReview={reviewClone} onCancel={() => { setCloneDraft(null); setError(""); }} />}
          {!cloneDraft && snapshot && data && <><p className="weapon-identity">{identity}<small>{editorKind === "shop"
            ? "Weapon identity stays locked. Only fields present in this shop record can be edited."
            : "Existing identities are locked. Use a template to create a new weapon in an editable copy."}</small></p>
            {editorKind === "weapon" && snapshot.workspace && <button className="quiet-button" disabled={dirty || locked}
              onClick={() => { setCloneDraft(emptyCloneDraft()); setError(""); setNotice(""); }}>New from template</button>}
            {editorKind === "component" && <p>Changes apply wherever this component definition is used. Its bone field does not move any weapon attachment link.</p>}
            {editorKind === "attachment" && <p>Only the existing default flag is editable. The weapon, component, and attachment point stay fixed.</p>}
            {editorKind === "weapon" && <WeaponFireRate values={draft} original={data.values}
              editable={editableFields} disabled={locked || !snapshot.workspace}
              onChange={changes => setDraft(previous => ({ ...previous, ...changes }))} />}
            {Object.keys(labels).filter(key => key in data.values && key !== rpmKey && key !== intervalKey).map(key => key === "attachment.default" || key === "shop.availableInSP"
              ? <label className="weapon-checkbox" key={key}><input type="checkbox" checked={["true", "1", "yes"].includes((draft[key] ?? data.values[key]).trim().toLowerCase())}
                  disabled={locked || !snapshot.workspace || !editableFields.includes(key)}
                  onChange={e => setDraft({ ...draft, [key]: String(e.target.checked) })} />{labels[key]}</label>
              : <label key={key}>{labels[key]}<input aria-label={labels[key]} value={draft[key] ?? data.values[key]}
                  disabled={locked || !snapshot.workspace || !editableFields.includes(key)}
                  onChange={e => { setDraft({ ...draft, [key]: e.target.value }); setShared(false); }} /></label>)}
            {editorKind === "weapon" && <WeaponCamera key={`${snapshot.source}:${identity}:${snapshot.revision}`} fields={snapshot.camera_fields ?? []}
              values={draft} original={data.values} editable={editableFields} disabled={locked || !snapshot.workspace}
              onChange={changes => setDraft(previous => ({ ...previous, ...changes }))} />}
            {editorKind === "attachment" && snapshot.attachment_values?.other_defaults.length ? <p className="weapon-shared">Other defaults at this bone: {snapshot.attachment_values.other_defaults.join(", ")}. Review and clear a conflicting default first; no other link will be changed automatically.</p> : null}
            {sharedDefinition && <div className="weapon-shared"><strong>{editorKind === "component" ? "Shared component definition" : "Shared ammo definition"}</strong><p>{data.affected_weapons.join(", ")}</p>{needsSharedAck && dirty && <label className="weapon-checkbox"><input type="checkbox" checked={shared} onChange={e => setShared(e.target.checked)} disabled={locked} />Apply {editorKind === "component" ? "component" : "ammo"} changes to every listed weapon.</label>}</div>}
            {snapshot.workspace && <div className="weapon-edit-actions"><button className="quiet-button" disabled={!dirty || locked} onClick={() => { setDraft(data.values); setShared(false); }}>Reset fields</button><button className="primary-button" disabled={!dirty || locked || (needsSharedAck && !shared)} onClick={() => void reviewChanges()}>Review changes</button><button className="quiet-button" disabled={dirty || locked || !snapshot.can_undo} onClick={() => void run("review_weapon_authoring", { ...context, action: "undo", expected_revision: snapshot.revision })}>Review undo</button></div>}
          </>}
        </div>
      </section>
      <section className="weapon-pane" aria-label="Weapon relationships and findings">
        <header><span className="pane-kicker">Validation</span><h4>Relationships &amp; evidence</h4></header>
        <div className="weapon-pane-body">
          {!snapshot && <p className="weapon-empty">Ammo, attachment, animation, and storefront evidence will appear here.</p>}
          {snapshot && <><dl className="weapon-summary">{Object.entries(snapshot.project.summary).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl>
            {data && <><h5>Source metadata</h5>{Object.entries(sources ?? {}).map(([key, path]) => <p key={key}><strong>{key}</strong><code>{path}</code></p>)}</>}
            <h5>{editorKind === "component" ? "Used by weapon links" : "Selected attachments"}</h5>
            {attachments.length ? <div className="weapon-inventory weapon-links">{attachments.map((a, i) =>
              <button key={i} disabled={locked || dirty} aria-label={`Inspect attachment ${a.weapon_name} / ${a.component_name}`}
                aria-pressed={editorKind === "attachment" && snapshot.attachment_values?.weapon === a.weapon_name && snapshot.attachment_values?.component === a.component_name}
                onClick={() => void run("inspect_weapon_workbench", { ...context, editor_kind: "attachment", weapon: a.weapon_name, component: a.component_name })}>
                <strong>{a.component_name}</strong><small>{editorKind === "component" ? `${a.weapon_name} · ` : ""}{a.attach_bone} · {a.default ? "Default" : "Optional"}</small>
              </button>)}</div> : <p>No attachment records for this selection.</p>}
            <h5>Package findings</h5>{snapshot.project.findings.length ? snapshot.project.findings.slice(0, 100).map((f, i) => <p className={`weapon-finding ${f.severity}`} key={i}><strong>{f.severity} · {f.code.replaceAll("_", " ")}</strong><span>{f.message}</span>{f.path && <code>{f.path}</code>}</p>) : <p>No package findings.</p>}
            <p>Attachment authoring changes existing default flags only. Model preview shows separate package assets; assembled previews and publication remain outside this editor.</p>
          </>}
        </div>
      </section>
    </div>
  </section>;
}
