export const rpmKey = "weapon.roundsPerMinute";
export const intervalKey = "weapon.timeBetweenShots";

export function WeaponFireRate({ values, original, editable, disabled, onChange }: {
  values: Record<string, string>; original: Record<string, string>;
  editable: string[]; disabled: boolean; onChange: (updates: Record<string, string>) => void;
}) {
  if (!(intervalKey in original)) return <section className="weapon-camera-group" aria-label="Fire rate">
    <h5>Fire rate</h5><p>No editable TimeBetweenShots value in this definition. No field will be added.</p>
  </section>;
  const value = values[rpmKey] ?? "";
  const number = Number(value);
  const valid = value.trim() !== "" && Number.isFinite(number) && number >= 1 && number <= 60000;
  const dirty = value !== original[rpmKey];
  const interval = dirty && valid ? String(60 / number) : original[intervalKey];
  return <section className="weapon-camera-group" aria-label="Fire rate">
    <h5>Fire rate</h5>
    <label>Rounds per minute (RPM)<input aria-label="Rounds per minute (RPM)" inputMode="decimal"
      value={value} disabled={disabled || !editable.includes(rpmKey)} aria-invalid={!valid}
      aria-describedby="weapon-rpm-help" onChange={e => onChange({ [rpmKey]: e.target.value })} /></label>
    <p id="weapon-rpm-help">Nominal cyclic rate. The saved interval is 60 ÷ RPM seconds.
      Animation timing, burst settings and firing mode can also affect the rate in game.</p>
    <p><strong>{dirty && valid ? "Proposed shot interval" : "Saved shot interval"}</strong>{" "}
      <code>{interval}</code> seconds <small>(TimeBetweenShots)</small></p>
    {!valid && <p role="alert">Enter a finite rate from 1 to 60,000 RPM. The source interval is left unchanged until a valid edit is reviewed and confirmed.</p>}
    <p>Uses the existing weapon node only. Does not change animation, firing-mode or burst flags.
      RPM is stored with up to six decimal places; the review shows the exact XML interval.</p>
  </section>;
}
