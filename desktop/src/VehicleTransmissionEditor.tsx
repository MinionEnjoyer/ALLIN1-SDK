import type { VehicleTransmissionConfiguration, VehicleTransmissionType } from "./types";
import SliderField from "./SliderField";

const TRANSMISSION_LABELS: Record<VehicleTransmissionType, string> = {
  automatic: "Automatic",
  manual: "Manual",
  sequential: "Sequential",
  dual_clutch: "Dual-clutch",
};

function ratioValid(value: number) {
  return Number.isFinite(value) && value >= 0.1 && value <= 10;
}

export function VehicleTransmissionEditor({
  configuration,
  stockGearCount,
  dirty,
  busy,
  onCreate,
  onConfiguration,
  onReset,
  onReview,
}: {
  configuration: VehicleTransmissionConfiguration | null;
  stockGearCount: number;
  dirty: boolean;
  busy: boolean;
  onCreate: () => void;
  onConfiguration: (configuration: VehicleTransmissionConfiguration) => void;
  onReset: () => void;
  onReview: () => void;
}) {
  if (!configuration) {
    return <div className="pane-empty transmission-empty">
      <strong>No custom transmission profile</strong>
      <p>The stock handling record declares {stockGearCount} forward gears. Create an ALLIN1 profile to specify transmission behavior and each ratio.</p>
      <button type="button" className="primary-button" onClick={onCreate} disabled={busy}>Create {stockGearCount}-gear profile</button>
    </div>;
  }

  const invalid = !ratioValid(configuration.final_drive_ratio)
    || !ratioValid(configuration.reverse_gear_ratio)
    || configuration.gear_ratios.some((ratio) => !ratioValid(ratio));
  const nonDescending = configuration.gear_ratios.some(
    (ratio, index) => index > 0 && ratio >= configuration.gear_ratios[index - 1],
  );
  const update = (values: Partial<VehicleTransmissionConfiguration>) => {
    onConfiguration({ ...configuration, ...values });
  };
  const updateRatio = (index: number, value: number) => {
    update({
      gear_ratios: configuration.gear_ratios.map((ratio, ratioIndex) => ratioIndex === index ? value : ratio),
    });
  };
  const addGear = () => {
    if (configuration.gear_ratios.length >= 16) return;
    const last = configuration.gear_ratios.at(-1) ?? 1;
    update({ gear_ratios: [...configuration.gear_ratios, Math.max(0.1, Number((last * 0.84).toFixed(3)))] });
  };
  const removeGear = () => {
    if (configuration.gear_ratios.length <= 1) return;
    update({ gear_ratios: configuration.gear_ratios.slice(0, -1) });
  };

  return <>
    <div className="vehicle-authoring-intro transmission-editor-intro">
      <strong>Transmission profile</strong>
      <span>Type and per-gear ratios are stored as ALLIN1 extension metadata; the forward gear count is synchronized to handling.meta.</span>
    </div>
    <div className="transmission-editor-grid">
      <section className="transmission-settings-panel" aria-labelledby="transmission-settings-heading">
        <div className="transmission-panel-heading"><strong id="transmission-settings-heading">Transmission</strong><span>schema {configuration.schema_version}</span></div>
        <div className="transmission-setting-fields">
          <label htmlFor="vehicle-transmission-type"><span>Type<small>runtime shift strategy</small></span><select id="vehicle-transmission-type" value={configuration.transmission_type} onChange={(event) => update({ transmission_type: event.target.value as VehicleTransmissionType })} disabled={busy}>{Object.entries(TRANSMISSION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <SliderField numeric id="vehicle-final-drive" label="Final drive" hint="applies after each gear" min={.1} max={10} hardMin={.1} hardMax={10} step={.01} value={configuration.final_drive_ratio} onChange={value => update({ final_drive_ratio: value })} disabled={busy} />
          <SliderField numeric id="vehicle-reverse-ratio" label="Reverse ratio" hint="positive magnitude" min={.1} max={10} hardMin={.1} hardMax={10} step={.01} value={configuration.reverse_gear_ratio} onChange={value => update({ reverse_gear_ratio: value })} disabled={busy} />
        </div>
        <div className="transmission-boundary-note"><strong>Stock boundary</strong><span>GTA handling stores the gear count, not this ratio table. Runtime/export consumers read the saved ALLIN1 profile.</span></div>
      </section>

      <section className="transmission-ratios-panel" aria-labelledby="transmission-ratios-heading">
        <div className="transmission-panel-heading"><strong id="transmission-ratios-heading">Forward gears</strong><span>{configuration.gear_ratios.length} configured</span></div>
        <div className="transmission-ratio-table" role="group" aria-label="Forward gear ratios">
          {configuration.gear_ratios.map((ratio, index) => <SliderField numeric key={index} id={`vehicle-gear-ratio-${index + 1}`} label={`Gear ${index + 1}`} min={.1} max={10} hardMin={.1} hardMax={10} step={.01} fineStep={.001} value={ratio} onChange={value => updateRatio(index, value)} disabled={busy} />)}
        </div>
        <div className="transmission-gear-actions"><button type="button" className="quiet-button" onClick={removeGear} disabled={busy || configuration.gear_ratios.length <= 1}>Remove last</button><button type="button" className="quiet-button" onClick={addGear} disabled={busy || configuration.gear_ratios.length >= 16}>Add gear</button></div>
      </section>
    </div>
    {(invalid || nonDescending) && <div className={`transmission-validation ${invalid ? "is-error" : ""}`} role={invalid ? "alert" : "status"}><strong>{invalid ? "Ratio outside supported range" : "Check ratio progression"}</strong><span>{invalid ? "Every ratio must be between 0.1 and 10.0." : "Forward ratios are usually strictly descending. Python will retain this as a review warning rather than rewriting it."}</span></div>}
    <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={onReset} disabled={busy || !dirty}>Reset transmission</button><button type="button" className="primary-button" onClick={onReview} disabled={busy || !dirty || invalid}>Review transmission</button></div>
  </>;
}
