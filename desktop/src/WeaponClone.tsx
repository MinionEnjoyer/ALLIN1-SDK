export interface WeaponCloneDraft {
  weapon_name: string; slot: string; model: string; human_name_hash: string; stat_name: string;
  ammo_info: string; clone_ammo: boolean;
}
export interface WeaponCloneSpec extends WeaponCloneDraft { donor_weapon: string; ammo_name: string | null }
export interface CloneRecord { kind: string; name: string; source: string; detail: string }
export interface WeaponClonePlan {
  ready: boolean; donor_complete: boolean; revision: number; plan_sha256: string;
  spec: WeaponCloneSpec; donor_completeness: Record<string, boolean | number | string[]>;
  selected_sources: Record<string, string>; reused_components: string[]; additions: CloneRecord[];
  collisions: { field: string; value: string; existing: string; reason: string; hash: string }[];
  findings: { severity: string; code: string; message: string; path?: string }[];
}
export const emptyCloneDraft = (): WeaponCloneDraft => ({ weapon_name: "", slot: "", model: "",
  human_name_hash: "", stat_name: "", ammo_info: "", clone_ammo: true });
const cloneLabels: Record<Exclude<keyof WeaponCloneDraft, "clone_ammo" | "ammo_info">, string> = {
  weapon_name: "New weapon identity", slot: "New slot identity", model: "Target model name",
  human_name_hash: "New display-name key", stat_name: "New stat-name key",
};
const evidenceLabels: Record<string, string> = {
  weapon_record: "Weapon definition", ammo_record: "Ammo definition", animation_mappings: "Animation mappings",
  animation_sets: "Animation sets", shop_record: "Shop record", attachment_links: "Attachment links",
  component_definitions: "Component definitions", authorable_source: "Editable source layout",
};
export function WeaponCloneForm({ donor, draft, disabled, onChange, onReview, onCancel }: {
  donor: string; draft: WeaponCloneDraft; disabled: boolean; onChange: (value: WeaponCloneDraft) => void;
  onReview: () => void; onCancel: () => void;
}) {
  const complete = Object.values(draft).every(value => typeof value !== "string" || Boolean(value.trim()));
  return <div className="weapon-clone-form">
    <p className="weapon-identity">Template: {donor}<small>Creates new metadata records in this editable copy. The donor stays unchanged.</small></p>
    {Object.entries(cloneLabels).map(([key, label]) => <label key={key}>{label}<input maxLength={160} autoFocus={key === "weapon_name"}
      value={draft[key as keyof typeof cloneLabels]} disabled={disabled}
      onChange={event => onChange({ ...draft, [key]: event.target.value })} /></label>)}
    <p className="weapon-empty">Use WEAPON_ and SLOT_ prefixes. The target model must already exist in this package; this does not copy or generate model files. Text keys do not create localization entries.</p>
    <label>Ammo mode<select disabled={disabled} value={draft.clone_ammo ? "clone" : "reuse"}
      onChange={event => onChange({ ...draft, clone_ammo: event.target.value === "clone", ammo_info: "" })}>
      <option value="clone">Clone donor ammo</option><option value="reuse">Reuse existing ammo</option>
    </select></label>
    <label>{draft.clone_ammo ? "New ammo identity" : "Existing ammo identity"}<input maxLength={160} value={draft.ammo_info}
      disabled={disabled} onChange={event => onChange({ ...draft, ammo_info: event.target.value })} /></label>
    <p>{draft.clone_ammo ? "Creates a separate AMMO_ record from the donor." : "References one existing AMMO_ record without changing it; future edits may affect its other users."} Component definitions are reused, not duplicated.</p>
    <div className="weapon-edit-actions">
      <button className="quiet-button" disabled={disabled} onClick={onCancel}>Cancel new weapon</button>
      <button className="primary-button" disabled={disabled || !complete} onClick={onReview}>Review clone plan</button>
    </div>
  </div>;
}
export function CloneRecordList({ records, label }: { records: CloneRecord[]; label: string }) {
  return <section className="weapon-clone-records" aria-label={label}><h5>{label}</h5>
    <ul>{records.map((record, index) => <li key={`${record.kind}-${index}`}>
      <strong>{record.kind.replaceAll("_", " ")} · {record.name}</strong>
      <span>{record.detail}</span><code>{record.source}</code>
    </li>)}</ul>
  </section>;
}
export function WeaponCloneReview({ plan }: { plan: WeaponClonePlan }) {
  return <div className="weapon-clone-review">
    <p role="status"><strong>{plan.ready ? "Ready to create" : "Blocked — resolve the findings and review again"}</strong></p>
    <p><strong>{plan.spec.donor_weapon} → {plan.spec.weapon_name}</strong>
      {plan.spec.clone_ammo ? "Clone ammo" : "Reuse ammo"}: {plan.spec.ammo_info} · Model: {plan.spec.model}</p>
    <dl className="weapon-summary">{Object.entries(plan.donor_completeness).map(([key, value]) => <div key={key}>
      <dt>{evidenceLabels[key] || key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") || "None" : typeof value === "boolean" ? value ? "Present" : "Missing" : value}</dd>
    </div>)}</dl>
    <dl className="weapon-summary">{Object.entries(cloneLabels).map(([key, label]) => <div key={key}>
      <dt>{label}</dt><dd>{plan.spec[key as keyof typeof cloneLabels]}</dd>
    </div>)}</dl>
    {!!plan.collisions.length && <section aria-label="Identity collisions"><h5>Identity collisions</h5>
      {plan.collisions.map((collision, index) => <p className="weapon-finding error" key={index}>
        <strong>{collision.field}: {collision.value}</strong><span>Conflicts with {collision.existing} · {collision.reason} · {collision.hash}</span>
      </p>)}
    </section>}
    {!!plan.findings.length && <section aria-label="Clone findings"><h5>Clone findings</h5>
      {plan.findings.map((finding, index) => <p className={`weapon-finding ${finding.severity}`} key={index}>
        <strong>{finding.severity} · {finding.code.replaceAll("_", " ")}</strong><span>{finding.message}</span>{finding.path && <code>{finding.path}</code>}
      </p>)}
    </section>}
    <CloneRecordList records={plan.additions} label="Planned additions" />
    <p>Reused components: {plan.reused_components.join(", ") || "None"}. Their definitions stay unchanged.</p>
    <details><summary>Source evidence · revision {plan.revision}</summary>
      {Object.entries(plan.selected_sources).map(([kind, path]) => <p key={kind}><strong>{kind.replaceAll("_", " ")}</strong><code>{path}</code></p>)}
      <p>Plan SHA-256<code>{plan.plan_sha256}</code></p>
    </details>
  </div>;
}
