import { useRef, useState } from "react";
import { maxScopeXmlBytes, readScopeRig, scopeHeightProposal, type ScopeRig } from "./scopeCalibration";

export function ScopeModelCalibration({ values, editable, disabled, onChange }: {
  values: Record<string, string>; editable: string[]; disabled: boolean;
  onChange: (updates: Record<string, string>) => void;
}) {
  const key = "weapon.firstPersonScopeAttachmentOffset.z";
  const [rigs, setRigs] = useState<(ScopeRig | null)[]>([null, null]);
  const [bones, setBones] = useState(["", ""]);
  const [baseline, setBaseline] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [proposal, setProposal] = useState<ReturnType<typeof scopeHeightProposal> | null>(null);
  const [error, setError] = useState("");
  const generations = useRef([0, 0]);
  const available = editable.includes(key) && key in values;
  function reset() { setProposal(null); setError(""); }
  async function load(file: File | undefined, index: number) {
    const generation = ++generations.current[index];
    reset(); setConfirmed(false);
    setRigs(previous => previous.map((rig, i) => i === index ? null : rig));
    if (!file) return;
    try {
      if (file.size > maxScopeXmlBytes) throw new Error("Model XML must be at most 16 MiB.");
      const rig = readScopeRig(await file.text());
      if (generation !== generations.current[index]) return;
      const names = Object.keys(rig.bones).filter(name => /^WAPScop/i.test(name));
      setRigs(previous => previous.map((value, i) => i === index ? rig : value));
      setBones(previous => previous.map((value, i) => i === index ? (names.find(name => name === "WAPScop_2") || names[0] || "") : value));
    } catch (reason) { if (generation === generations.current[index]) setError((reason as Error).message); }
  }
  return <details className="weapon-camera-group"><summary>Automatic scope-height calculation</summary>
    <p>Read scope-mount heights from two exported weapon YDR XML files. For the same optic and aiming animation, this estimates the attached-scope height correction. Iron-sight offsets stay untouched.</p>
    {["Aligned reference weapon", "Custom weapon"].map((label, index) => <fieldset key={label}><legend>{label}</legend>
      <label>{label} model XML<input type="file" accept=".xml" disabled={disabled} onChange={event => void load(event.target.files?.[0], index)} /></label>
      {rigs[index] && <><p>{rigs[index]!.name}</p><label>{label} scope mount<select value={bones[index]} disabled={disabled} onChange={event => {
        setBones(previous => previous.map((value, i) => i === index ? event.target.value : value)); reset(); setConfirmed(false);
      }}><option value="">Choose the actual scope mount</option>{Object.keys(rigs[index]!.bones).filter(name => /^WAPScop/i.test(name)).map(name => <option key={name}>{name}</option>)}</select></label></>}
    </fieldset>)}
    <label>Reference attached-scope Z (metres)<input inputMode="decimal" value={baseline} placeholder="From the aligned reference’s weapons.meta" disabled={disabled}
      onChange={event => { setBaseline(event.target.value); reset(); }} /></label>
    <label className="weapon-checkbox"><input type="checkbox" checked={confirmed} disabled={disabled} onChange={event => { setConfirmed(event.target.checked); reset(); }} />Both weapons use the same scope geometry and aiming animation, in the same Z-up model frame.</label>
    <button className="quiet-button" disabled={disabled || !available || !confirmed || rigs.some(rig => !rig) || bones.some(bone => !bone)} onClick={() => {
      try {
        if (!baseline.trim()) throw new Error("Enter the aligned reference’s attached-scope Z; blank is not zero.");
        setProposal(scopeHeightProposal(rigs[0]!, rigs[1]!, bones[0], bones[1], Number(baseline))); setError("");
      } catch (reason) { setError((reason as Error).message); setProposal(null); }
    }}>Calculate scope height</button>
    {!available && <p>This weapon has no editable attached-scope Z field.</p>}
    {error && <p role="alert">{error}</p>}
    {proposal && <section aria-label="Automatic scope-height proposal"><dl className="weapon-calibration-result">
      <div><dt>Reference mount Z</dt><dd>{proposal.referenceZ.toFixed(5)} m</dd></div>
      <div><dt>Custom mount Z</dt><dd>{proposal.customZ.toFixed(5)} m</dd></div>
      <div><dt>Attached-scope Z</dt><dd>{values[key]} → {proposal.proposedZ.toFixed(5)} m</dd></div>
    </dl><button className="quiet-button" disabled={disabled || !available} onClick={() => { onChange({ [key]: proposal.proposedZ.toFixed(5) }); reset(); }}>Use calculated height in draft</button></section>}
    <p>Height = reference offset + reference mount height − custom mount height. X/Y, eye relief, rotation and FOV are unchanged. A different optic needs its optical centre measured; a mounting bone alone cannot provide that. Review before saving, then verify in game.</p>
  </details>;
}
