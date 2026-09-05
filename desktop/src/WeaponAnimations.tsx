export interface AnimationRecord {
  weapon_name: string; source: string; set_name: string; set_ordinal: number; ordinal: number;
}
export interface AnimationDraft { template: string; source: string }

export function WeaponAnimations({ weapon, records, draft, editable, disabled, onChange, onReview, onReset }: {
  weapon: string; records: AnimationRecord[]; draft: AnimationDraft | null;
  editable: boolean; disabled: boolean; onChange: (draft: AnimationDraft) => void;
  onReview: () => void; onReset: () => void;
}) {
  const target = records.filter(record => record.weapon_name.toLowerCase() === weapon.toLowerCase());
  const templates = [...new Set(records.map(record => record.weapon_name))].filter(name => name.toLowerCase() !== weapon.toLowerCase()).sort();
  const donor = records.filter(record => record.weapon_name === draft?.template);
  const sources = [...new Set(donor.map(record => record.source))].sort();
  const selected = donor.filter(record => record.source === draft?.source);
  const coverage = target.length ? target : selected;
  const canClone = editable && !disabled && !target.length;
  return <div className="weapon-animation-editor">
    <p className="weapon-identity">{weapon}</p>
    <p>{target.length ? `${target.length} existing animation mappings. Existing mappings are protected from replacement.`
      : "No animation mappings found. Copy a mapped template from this package to fill the missing coverage."}</p>
    <p>Copies each complete mapping in the chosen source, including its clip references and flags. This does not create custom reloads or convert animation assets.</p>
    {!target.length && <>
      <label>Animation template<select value={draft?.template ?? ""} disabled={!canClone || !templates.length}
        onChange={event => {
          const template = event.target.value;
          const paths = [...new Set(records.filter(record => record.weapon_name === template).map(record => record.source))];
          onChange({ template, source: paths.length === 1 ? paths[0] : "" });
        }}><option value="">Choose a mapped weapon</option>{templates.map(name => <option key={name}>{name}</option>)}</select></label>
      {!templates.length && <p>No mapped templates are available in this package.</p>}
      {draft?.template && <label>Animation source<select value={draft.source} disabled={!canClone}
        onChange={event => onChange({ ...draft, source: event.target.value })}>
        <option value="">Choose an exact source</option>{sources.map(source => <option key={source}>{source}</option>)}
      </select></label>}
    </>}
    {coverage.length > 0 && <section aria-label="Animation set coverage">
      <h5>{target.length ? "Existing set coverage" : "Mappings to add"}</h5>
      <ul className="weapon-mapping-list">{coverage.map((record, index) => <li key={`${record.source}:${record.set_ordinal}:${index}`}>
        <strong>{record.set_name || `Unnamed set ${record.set_ordinal + 1}`}</strong><code>{record.source}</code>
      </li>)}</ul>
    </section>}
    {!editable && <p>Create an editable copy to add missing mappings.</p>}
    {editable && !target.length && <div className="weapon-edit-actions">
      <button className="quiet-button" disabled={disabled || !draft} onClick={onReset}>Reset animation draft</button>
      <button className="primary-button" disabled={!canClone || !draft?.source || !selected.length} onClick={onReview}>Review animation mappings</button>
    </div>}
  </div>;
}
