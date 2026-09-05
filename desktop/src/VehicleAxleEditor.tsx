import { useState, type CSSProperties } from "react";
import type { VehicleAuthoringAxleSkeleton, VehicleAxle, VehicleAxleConfiguration } from "./types";

function axleLabel(axle: VehicleAxle) {
  const role = axle.logical_role === "middle" ? "Mid" : axle.logical_role[0].toLocaleUpperCase() + axle.logical_role.slice(1);
  return `Axle ${axle.physical_order} · ${role}`;
}

function steeringState(axle: VehicleAxle) {
  const gain = axle.steering_gain ?? (axle.steered ? 1 : 0);
  return gain > 0 ? `same +${gain.toFixed(2)}` : gain < 0 ? `counter ${gain.toFixed(2)}` : axle.steered ? "neutral 0.00" : "fixed";
}

function axleState(axle: VehicleAxle) {
  return [steeringState(axle), axle.powered ? "drive" : "free", axle.handbrake ? "park" : axle.service_brake ? "brake" : "unbraked"].join(" · ");
}

export function VehicleAxleEditor({
  configuration,
  skeleton,
  selectedOrder,
  dirty,
  busy,
  onSelect,
  onConfiguration,
  onChooseSkeleton,
  onMoveOrder,
  onRestoreCanonical,
  onCalculateSteering,
  onReset,
  onReview,
}: {
  configuration: VehicleAxleConfiguration | null;
  skeleton: VehicleAuthoringAxleSkeleton | null;
  selectedOrder: number | null;
  dirty: boolean;
  busy: boolean;
  onSelect: (order: number) => void;
  onConfiguration: (configuration: VehicleAxleConfiguration) => void;
  onChooseSkeleton: () => void;
  onMoveOrder: (order: number, direction: -1 | 1) => void;
  onRestoreCanonical: () => void;
  onCalculateSteering: (request: Record<string, unknown>) => void;
  onReset: () => void;
  onReview: () => void;
}) {
  const [referenceLock, setReferenceLock] = useState(35);
  const [referenceOrder, setReferenceOrder] = useState(0);
  const [pivotPosition, setPivotPosition] = useState("");

  if (!configuration) {
    return <div className="pane-empty axle-empty"><strong>No axle configuration saved</strong><p>Load a CodeWalker vehicle XML so Python can detect canonical wheel pairs and physical front-to-rear order.</p><button type="button" className="primary-button" onClick={onChooseSkeleton} disabled={busy}>Load skeleton XML</button></div>;
  }

  const axles = [...configuration.axles].sort((left, right) => left.physical_order - right.physical_order);
  const selected = axles.find((axle) => axle.physical_order === selectedOrder) ?? axles[0];
  const evidenceRequired = Boolean(configuration.steering_calculation || configuration.intentional_layout_override);
  const evidenceUnavailable = evidenceRequired && !skeleton;
  const updateAxle = (values: Partial<VehicleAxle>, invalidateSteering = false) => {
    if (!selected) return;
    const next: VehicleAxleConfiguration = {
      ...configuration,
      preset: "Custom",
      axles: configuration.axles.map((axle) => axle.physical_order === selected.physical_order ? { ...axle, ...values } : axle),
    };
    if (invalidateSteering) delete next.steering_calculation;
    onConfiguration(next);
  };
  const calculate = () => {
    const request: Record<string, unknown> = { reference_lock_degrees: referenceLock };
    if (referenceOrder) request.reference_axle_order = referenceOrder;
    if (pivotPosition.trim()) request.pivot_longitudinal_position = Number(pivotPosition);
    onCalculateSteering(request);
  };

  return <>
    <div className="vehicle-authoring-intro axle-editor-intro"><strong>Axle configuration</strong><span>Physical order and signed steering are derived from the selected vehicle skeleton; runtime roles remain explicit per axle.</span></div>
    <div className="axle-evidence-toolbar"><div><strong>{skeleton ? "Skeleton linked" : evidenceRequired ? "Skeleton required" : "Skeleton not linked"}</strong><span>{skeleton ? `${skeleton.bone_count} bones · ${skeleton.bone_position_sha256.slice(0, 12)}…` : "Load XML to detect order, calculate steering, or verify signed evidence."}</span></div><button type="button" className="quiet-button" onClick={onChooseSkeleton} disabled={busy}>{skeleton ? "Change XML" : "Load skeleton XML"}</button></div>
    <div className="axle-editor-grid">
      <section className="axle-schematic" aria-label={`${configuration.vehicle_model} axle schematic`}>
        <div className="axle-schematic-heading"><span>Front</span><small>{axles.length} axles · {configuration.expected_wheel_count} wheels</small></div>
        <div className="axle-vehicle-shell" aria-hidden="true"><span /></div>
        <div className="axle-diagram-list">
          {axles.map((axle) => {
            const gain = axle.steering_gain ?? (axle.steered ? 1 : 0);
            const wheelAngle = Math.max(-1, Math.min(1, gain)) * 13;
            const steeringStyle = { "--axle-steer-angle": `${wheelAngle}deg` } as CSSProperties;
            return <button key={`${axle.physical_order}-${axle.left_bone}`} type="button" style={steeringStyle} className={`axle-diagram-row ${axle.powered ? "is-powered" : ""} ${gain !== 0 ? "is-steered" : ""} ${gain < 0 ? "is-counter-steered" : ""} ${selected?.physical_order === axle.physical_order ? "selected" : ""}`} aria-pressed={selected?.physical_order === axle.physical_order} aria-label={`${axleLabel(axle)}: ${axleState(axle)}`} onClick={() => onSelect(axle.physical_order)}>
              <span className="axle-wheel axle-wheel-left" aria-hidden="true" /><span className="axle-beam" aria-hidden="true" /><span className="axle-marker"><strong>A{axle.physical_order}</strong><small>{steeringState(axle)}</small></span><span className="axle-wheel axle-wheel-right" aria-hidden="true" />
            </button>;
          })}
        </div>
        <div className="axle-schematic-key"><span><i className="steer-mark" />Same phase</span><span><i className="counter-mark" />Counter phase</span><span><i className="drive-mark" />Powered</span></div>
      </section>

      {selected && <section className="axle-config-panel" aria-live="polite">
        <div className="axle-config-heading"><div><strong>{axleLabel(selected)}</strong><small>{selected.left_bone} / {selected.right_bone}</small></div><span>{selected.visual_family === "front" ? "front family" : "shared family"}</span></div>
        <div className="axle-order-actions" aria-label="Physical axle order"><button type="button" className="quiet-button" onClick={() => onMoveOrder(selected.physical_order, -1)} disabled={busy || !skeleton || selected.physical_order === 1}>Move forward</button><button type="button" className="quiet-button" onClick={() => onMoveOrder(selected.physical_order, 1)} disabled={busy || !skeleton || selected.physical_order === axles.length}>Move rearward</button></div>
        <div className="axle-role-grid">
          <label className="axle-check"><input type="checkbox" checked={selected.steered} onChange={(event) => updateAxle({ steered: event.target.checked, steering_gain: event.target.checked ? 1 : 0 }, true)} disabled={busy || evidenceUnavailable} /><span>Steered<small>{steeringState(selected)}</small></span></label>
          <label className="axle-check"><input type="checkbox" checked={selected.powered} onChange={(event) => updateAxle({ powered: event.target.checked })} disabled={busy || evidenceUnavailable} /><span>Powered<small>runtime drive torque</small></span></label>
          <label className="axle-check"><input type="checkbox" checked={selected.service_brake} onChange={(event) => updateAxle({ service_brake: event.target.checked })} disabled={busy || evidenceUnavailable} /><span>Service brake<small>normal braking</small></span></label>
          <label className="axle-check"><input type="checkbox" checked={selected.handbrake} onChange={(event) => updateAxle({ handbrake: event.target.checked })} disabled={busy || evidenceUnavailable} /><span>Handbrake<small>parking brake</small></span></label>
        </div>
        {selected.suspension && <label className="axle-support" htmlFor={`axle-support-${selected.physical_order}`}><span>Support weight<small>relative load contribution · 0.75–1.25</small></span><input id={`axle-support-${selected.physical_order}`} type="number" min="0.75" max="1.25" step="0.01" value={selected.suspension.support_weight} onChange={(event) => updateAxle({ suspension: { support_weight: Number(event.target.value) } })} disabled={busy || evidenceUnavailable} /></label>}
        <dl className="axle-evidence-list"><div><dt>Wheel bones</dt><dd>{selected.left_bone}<br />{selected.right_bone}</dd></div><div><dt>Runtime slots</dt><dd>{selected.left_runtime_index} / {selected.right_runtime_index}</dd></div><div><dt>Steering gain</dt><dd>{(selected.steering_gain ?? (selected.steered ? 1 : 0)).toFixed(3)} · {steeringState(selected).split(" ")[0]}</dd></div></dl>
      </section>}
    </div>

    <fieldset className="axle-steering-settings"><legend>Signed steering geometry</legend><div className="axle-steering-fields"><label htmlFor="axle-reference-lock"><span>Reference lock<small>1–80 degrees</small></span><input id="axle-reference-lock" type="number" min="1" max="80" step="0.5" value={referenceLock} onChange={(event) => setReferenceLock(Number(event.target.value))} disabled={busy || !skeleton} /></label><label htmlFor="axle-reference-order"><span>Reference axle<small>automatic if blank</small></span><select id="axle-reference-order" value={referenceOrder} onChange={(event) => setReferenceOrder(Number(event.target.value))} disabled={busy || !skeleton}><option value={0}>Automatic</option>{axles.filter((axle) => axle.steered).map((axle) => <option key={axle.physical_order} value={axle.physical_order}>Axle {axle.physical_order}</option>)}</select></label><label htmlFor="axle-pivot-position"><span>Neutral pivot Y<small>only for all-steer layouts</small></span><input id="axle-pivot-position" type="number" step="0.01" value={pivotPosition} placeholder="Infer from fixed axles" onChange={(event) => setPivotPosition(event.target.value)} disabled={busy || !skeleton} /></label><button type="button" className="quiet-button" onClick={calculate} disabled={busy || !skeleton || !axles.some((axle) => axle.steered)}>Calculate gains</button></div></fieldset>
    <fieldset className="axle-runtime-settings"><legend>Runtime strategy</legend><label htmlFor="vehicle-axle-export-mode"><span>Export mode<small>{configuration.preset} · schema {configuration.schema_version}</small></span><select id="vehicle-axle-export-mode" value={configuration.export_mode} onChange={(event) => onConfiguration({ ...configuration, export_mode: event.target.value as VehicleAxleConfiguration["export_mode"] })} disabled={busy || evidenceUnavailable}><option value="stock_metadata">Stock metadata</option><option value="selective_runtime">Selective runtime</option></select></label>{configuration.intentional_layout_override && <button type="button" className="quiet-button" onClick={onRestoreCanonical} disabled={busy || !skeleton}>Restore canonical order</button>}</fieldset>
    {evidenceUnavailable && <div className="axle-evidence-lock" role="status"><strong>Skeleton evidence required</strong><span>This signed or reordered configuration remains read-only until its wheel-bone positions are reloaded and verified.</span></div>}
    <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={onReset} disabled={busy || !dirty}>Reset axle changes</button><button type="button" className="primary-button" onClick={onReview} disabled={busy || !dirty || evidenceUnavailable}>Review axle changes</button></div>
  </>;
}
