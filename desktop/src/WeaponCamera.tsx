import { useState } from "react";
import { ScopeModelCalibration } from "./ScopeModelCalibration";
import SliderField from "./SliderField";

export interface CameraField {
  key: string; tag: string; attribute: string; label: string; group: string;
  unit: string; minimum: number; maximum: number; step: string;
}
type Vector = [number, number, number];
const axes = ["x", "y", "z"] as const;

export function calibratedOffset(current: Vector, reference: Vector, custom: Vector, scale = 1): Vector {
  if (![...current, ...reference, ...custom, scale].every(Number.isFinite) || scale <= 0) throw new Error("All coordinates must be finite numbers.");
  const result = current.map((value, i) => value + (reference[i] - custom[i]) * scale) as Vector;
  if (result.some(value => Math.abs(value) > 10)) throw new Error("Calculated position exceeds the ±10 metre authoring limit. Check units and coordinate frames.");
  return result;
}

export function magnifiedFov(reference: number, magnification: number): number {
  if (!Number.isFinite(reference) || reference < 1 || reference > 179
    || !Number.isFinite(magnification) || magnification < 1 || magnification > 100) throw new Error("Use a 1–179° reference FOV and 1–100× magnification.");
  const result = 2 * Math.atan(Math.tan(reference * Math.PI / 360) / magnification) * 180 / Math.PI;
  if (result < 1 || result > 179) throw new Error("Calculated FOV is outside the 1–179° authoring limit.");
  return result;
}

function finiteInput(value: string): number {
  if (!value.trim() || !Number.isFinite(Number(value))) throw new Error("Enter every anchor coordinate; blank values are not treated as zero.");
  return Number(value);
}

function ScopeCalibration({ values, editable, disabled, onChange }: {
  values: Record<string, string>; editable: string[]; disabled: boolean;
  onChange: (updates: Record<string, string>) => void;
}) {
  const [target, setTarget] = useState("firstPersonScopeAttachmentOffset");
  const [reference, setReference] = useState(["", "", ""]);
  const [custom, setCustom] = useState(["", "", ""]);
  const [unit, setUnit] = useState("metres");
  const [magnification, setMagnification] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [requested, setRequested] = useState(false);
  const keys = axes.map(axis => `weapon.${target}.${axis}`);
  const fovKey = `weapon.${target.replace("Offset", "Fov")}`;
  const available = keys.every(key => editable.includes(key) && key in values);
  let proposal: Record<string, string> | null = null;
  let error = "";
  if (requested) {
    try {
      if (!available) throw new Error("This target needs all three existing, editable position axes.");
      if (!confirmed) throw new Error("Confirm the shared coordinate frame first.");
      const offset = calibratedOffset(keys.map(key => finiteInput(values[key])) as Vector,
        reference.map(finiteInput) as Vector, custom.map(finiteInput) as Vector, unit === "mm" ? .001 : 1);
      proposal = Object.fromEntries(keys.map((key, i) => [key, offset[i].toFixed(5)]));
      if (magnification.trim()) {
        if (!editable.includes(fovKey)) throw new Error("This target has no editable FOV node; leave magnification blank.");
        proposal[fovKey] = magnifiedFov(finiteInput(values[fovKey]), finiteInput(magnification)).toFixed(5);
      }
    } catch (reason) { error = (reason as Error).message; proposal = null; }
  }
  const reset = () => setRequested(false);
  return <details className="weapon-camera-group"><summary>Custom scope calibration</summary>
    <p>Start with an already aligned reference scope. Measure its sight centre and the custom scope’s sight centre in the same, already-oriented weapon-local frame. This estimates translation only; it does not infer eye relief, bone transforms, or rotation.</p>
    <label>Calibration target<select value={target} disabled={disabled} onChange={e => { setTarget(e.target.value); reset(); }}>
      <option value="firstPersonScopeAttachmentOffset">Attached scope position</option><option value="firstPersonScopeOffset">Base sight / iron sights (unscoped)</option>
    </select></label>
    <label>Anchor units<select value={unit} disabled={disabled} onChange={e => { setUnit(e.target.value); reset(); }}><option value="metres">Metres</option><option value="mm">Millimetres</option></select></label>
    {[["Reference sight centre", reference, setReference], ["Custom sight centre", custom, setCustom]].map(([label, coordinates, setter]) =>
      <fieldset key={label as string}><legend>{label as string}</legend><div className="weapon-camera-axes">{axes.map((axis, i) =>
        <label key={axis}>{axis.toUpperCase()}<input aria-label={`${label} ${axis.toUpperCase()}`} inputMode="decimal"
          value={(coordinates as string[])[i]} disabled={disabled} onChange={e => {
            const next = [...coordinates as string[]]; next[i] = e.target.value;
            (setter as (value: string[]) => void)(next); reset();
          }} /></label>)}</div></fieldset>)}
    <label>Magnification relative to current FOV (optional)<input value={magnification} inputMode="decimal" disabled={disabled}
      onChange={e => { setMagnification(e.target.value); reset(); }} placeholder="For example, 2 for 2×" /></label>
    <p>Position = reference offset + reference anchor − custom anchor. Start with the aligned reference offset in the draft; do not apply this delta repeatedly. Optional FOV uses 2·atan(tan(current FOV/2) ÷ magnification), on the same FOV axis. Both remain estimates until checked in game.</p>
    <label className="weapon-checkbox"><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e => { setConfirmed(e.target.checked); reset(); }} />The reference is aligned and both anchors use the same oriented weapon-local frame.</label>
    <button className="quiet-button" disabled={disabled || !available || !confirmed} onClick={() => setRequested(true)}>Calculate scope proposal</button>
    {!available && <p>Position axes are missing or read-only for this target. No fields will be synthesized.</p>}
    {error && <p role="alert">{error}</p>}
    {proposal && <section aria-label="Scope calibration proposal"><dl className="weapon-calibration-result">{Object.entries(proposal).map(([key, value]) =>
      <div key={key}><dt>{key.endsWith("Fov") ? "FOV (degrees)" : `${key.slice(-1).toUpperCase()} (metres)`}</dt><dd>{values[key]} → {value}</dd></div>)}</dl>
      <button className="quiet-button" disabled={disabled} onClick={() => {
        onChange(proposal!); reset(); setConfirmed(false); setReference(["", "", ""]); setCustom(["", "", ""]); setMagnification("");
      }}>Use proposal in draft</button>
      <p>This changes the draft only. Review and confirm before saving.</p>
    </section>}
  </details>;
}

export function WeaponCamera({ fields, values, original, editable, disabled, onChange }: {
  fields: CameraField[]; values: Record<string, string>; original: Record<string, string>;
  editable: string[]; disabled: boolean; onChange: (updates: Record<string, string>) => void;
}) {
  const groups = [...new Set(fields.map(field => field.group))];
  const scopeGroups = groups.filter(group => (group.toLowerCase().includes("scope") && !group.startsWith("Third-person")) || group === "Field of view");
  const otherGroups = groups.filter(group => !scopeGroups.includes(group));
  const flagsKey = "weapon.weaponFlags";
  const flagTokens = [...new Set(`${original[flagsKey] ?? ""} ${values[flagsKey] ?? ""}`.split(/\s+/).filter(Boolean))];
  const active = (values[flagsKey] ?? "").split(/\s+/).filter(Boolean);
  const renderGroup = (group: string) => <fieldset key={group}><legend>{group}</legend><div className="weapon-camera-axes">{fields.filter(field => field.group === group).map(field =>
    <SliderField key={field.key} label={field.label} value={values[field.key] ?? ""} resetValue={original[field.key]}
      min={field.unit === "metres" ? Math.max(-.1, field.minimum) : field.minimum}
      max={field.unit === "metres" ? Math.min(.1, field.maximum) : field.maximum}
      step={field.unit === "metres" ? .001 : .1} fineStep={Number(field.step)}
      hardMin={field.minimum} hardMax={field.maximum} unit={field.unit}
      disabled={disabled || !editable.includes(field.key)}
      onChange={next => onChange({ [field.key]: next })} />)}</div></fieldset>;
  return <>
    {fields.length > 0 && <details className="weapon-camera-group" open><summary>First-person camera &amp; scope</summary>
      <p>Only existing source fields are editable. Position is in metres; rotation and FOV are in degrees. Missing axes are left untouched.</p>
      <p>Base-sight fields control aiming without the attachment. Use attached-scope fields to correct an equipped optic without changing working iron sights.</p>
      {scopeGroups.map(renderGroup)}
      <ScopeModelCalibration values={values} editable={editable} disabled={disabled} onChange={onChange} />
      <ScopeCalibration values={values} editable={editable} disabled={disabled} onChange={onChange} />
      {otherGroups.length > 0 && <details className="weapon-camera-group"><summary>Other first-person offsets</summary>{otherGroups.map(renderGroup)}</details>}
    </details>}
    {flagsKey in values && <details className="weapon-camera-group"><summary>Weapon behavior flags</summary>
      <p>Toggle source flags or edit the complete list. Names are syntax-checked, not checked against a game-version catalog. Unknown names may be ignored by GTA V.</p>
      <div className="weapon-flag-list">{flagTokens.map(flag => <label className="weapon-checkbox" key={flag}><input type="checkbox"
        checked={active.includes(flag)} disabled={disabled || !editable.includes(flagsKey)} onChange={e => onChange({ [flagsKey]:
          (e.target.checked ? [...active, flag] : active.filter(value => value !== flag)).join(" ") })} />{flag}</label>)}</div>
      <label>Complete weapon flags<textarea value={values[flagsKey]} maxLength={8192} rows={6} disabled={disabled || !editable.includes(flagsKey)}
        onChange={e => onChange({ [flagsKey]: e.target.value })} /></label>
    </details>}
  </>;
}
