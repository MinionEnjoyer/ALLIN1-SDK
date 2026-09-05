import SliderField from "./SliderField";
import type {
  VehicleDistributionValues,
  VehiclePackageBuildResult,
  VehiclePackageBuildReview,
} from "./types";

export interface VehiclePackageDraft {
  destination: string;
  pack_name: string;
  mod_id: string;
  name: string;
  version: string;
  legacy: boolean;
  enhanced: boolean;
}

const CATEGORIES = [
  "compacts", "sedans", "suvs", "coupes", "muscle", "sports_classics",
  "sports", "super", "motorcycles", "off_road", "industrial", "utility",
  "vans", "cycles", "boats", "helicopters", "planes", "service",
  "emergency", "military", "commercial", "trains", "open_wheel",
];

const STORAGE = ["garage", "hangar", "helipad", "dock", "special"];

function FieldLabel({ title, hint }: { title: string; hint: string }) {
  return <span>{title}<small>{hint}</small></span>;
}

export function VehicleOutputEditor({
  distribution,
  distributionDirty,
  packageDraft,
  packageReview,
  packageResult,
  busy,
  workspaceClean,
  onDistribution,
  onResetDistribution,
  onReviewDistribution,
  onPackage,
  onChooseDestination,
  onReviewPackage,
}: {
  distribution: VehicleDistributionValues;
  distributionDirty: boolean;
  packageDraft: VehiclePackageDraft;
  packageReview: VehiclePackageBuildReview | null;
  packageResult: VehiclePackageBuildResult | null;
  busy: boolean;
  workspaceClean: boolean;
  onDistribution: (values: VehicleDistributionValues) => void;
  onResetDistribution: () => void;
  onReviewDistribution: () => void;
  onPackage: (values: VehiclePackageDraft) => void;
  onChooseDestination: () => void;
  onReviewPackage: () => void;
}) {
  const updateDistribution = (values: Partial<VehicleDistributionValues>) => {
    onDistribution({ ...distribution, ...values });
  };
  const updatePackage = (values: Partial<VehiclePackageDraft>) => {
    onPackage({ ...packageDraft, ...values });
  };
  const editionSelected = packageDraft.legacy || packageDraft.enhanced;
  const invalidWeight = !Number.isFinite(distribution.traffic_weight) || distribution.traffic_weight < 0 || distribution.traffic_weight > 100;

  return <>
    <div className="vehicle-authoring-intro output-editor-intro">
      <strong>Distribution and validated output</strong>
      <span>Catalog visibility is revisioned with the workspace. Package publication writes a new managed package only after a separate readiness review.</span>
    </div>

    <div className="vehicle-output-grid">
      <section className="vehicle-output-panel" aria-labelledby="distribution-panel-heading">
        <div className="vehicle-output-heading">
          <div><strong id="distribution-panel-heading">Vehicle distribution</strong><span>GBAY catalog record</span></div>
          <span className={distribution.listed ? "output-state ready" : "output-state"}>{distribution.listed ? "Listed" : "Hidden"}</span>
        </div>
        <div className="vehicle-output-fields">
          <label className="output-check" htmlFor="vehicle-distribution-listed"><input id="vehicle-distribution-listed" type="checkbox" checked={distribution.listed} onChange={(event) => updateDistribution({ listed: event.target.checked })} disabled={busy} /><FieldLabel title="Include in catalog" hint="available to package consumers" /></label>
          <label htmlFor="vehicle-distribution-name"><FieldLabel title="Display name" hint="customer-facing vehicle name" /><input id="vehicle-distribution-name" value={distribution.name} onChange={(event) => updateDistribution({ name: event.target.value })} disabled={busy} /></label>
          <label htmlFor="vehicle-distribution-make"><FieldLabel title="Manufacturer" hint="catalog make" /><input id="vehicle-distribution-make" value={distribution.manufacturer} onChange={(event) => updateDistribution({ manufacturer: event.target.value })} disabled={busy} /></label>
          <div className="vehicle-output-field-pair">
            <label htmlFor="vehicle-distribution-category"><FieldLabel title="Category" hint="browse group" /><select id="vehicle-distribution-category" value={distribution.category} onChange={(event) => updateDistribution({ category: event.target.value })} disabled={busy}>{CATEGORIES.map((category) => <option key={category} value={category}>{category.replaceAll("_", " ")}</option>)}</select></label>
            <label htmlFor="vehicle-distribution-storage"><FieldLabel title="Storage" hint="delivery location" /><select id="vehicle-distribution-storage" value={distribution.storage} onChange={(event) => updateDistribution({ storage: event.target.value })} disabled={busy}>{STORAGE.map((storage) => <option key={storage} value={storage}>{storage}</option>)}</select></label>
          </div>
          <div className="vehicle-output-field-pair">
            <label htmlFor="vehicle-distribution-price"><FieldLabel title="Price" hint="catalog currency" /><input id="vehicle-distribution-price" type="number" min="0" max="1000000000" step="1" value={distribution.price} onChange={(event) => updateDistribution({ price: Number(event.target.value) })} disabled={busy} /></label>
            <label htmlFor="vehicle-distribution-size"><FieldLabel title="Size tier" hint="1 compact · 5 largest" /><input id="vehicle-distribution-size" type="number" min="1" max="5" step="1" value={distribution.size_tier} onChange={(event) => updateDistribution({ size_tier: Number(event.target.value) })} disabled={busy} /></label>
          </div>
          <div className="vehicle-output-field-pair">
            <label htmlFor="vehicle-preview-dictionary"><FieldLabel title="Preview dictionary" hint="optional TXD" /><input id="vehicle-preview-dictionary" value={distribution.preview_dictionary ?? ""} onChange={(event) => updateDistribution({ preview_dictionary: event.target.value || null })} disabled={busy} /></label>
            <label htmlFor="vehicle-preview-texture"><FieldLabel title="Preview texture" hint="optional texture" /><input id="vehicle-preview-texture" value={distribution.preview_texture ?? ""} onChange={(event) => updateDistribution({ preview_texture: event.target.value || null })} disabled={busy} /></label>
          </div>
          <div className="vehicle-traffic-row">
            <label className="output-check" htmlFor="vehicle-traffic-enabled"><input id="vehicle-traffic-enabled" type="checkbox" checked={distribution.traffic_enabled} onChange={(event) => updateDistribution({ traffic_enabled: event.target.checked })} disabled={busy} /><FieldLabel title="Allow ambient traffic" hint="explicit opt-in" /></label>
            <SliderField numeric id="vehicle-traffic-weight" label="Spawn weight" min={0} max={100} hardMin={0} hardMax={100} step={.1} value={distribution.traffic_weight} onChange={value => updateDistribution({ traffic_weight: value })} disabled={busy || !distribution.traffic_enabled} />
          </div>
        </div>
        <div className="vehicle-output-actions"><button type="button" className="quiet-button" onClick={onResetDistribution} disabled={busy || !distributionDirty}>Reset catalog</button><button type="button" className="primary-button" onClick={onReviewDistribution} disabled={busy || !distributionDirty || invalidWeight}>Review distribution</button></div>
      </section>

      <section className="vehicle-output-panel" aria-labelledby="package-panel-heading">
        <div className="vehicle-output-heading">
          <div><strong id="package-panel-heading">Managed package</strong><span>new validated output folder</span></div>
          <span className={`output-state ${packageResult || packageReview?.ready ? "ready" : ""}`}>{packageResult ? "Built" : packageReview?.ready ? "Reviewed" : "Draft"}</span>
        </div>
        <div className="vehicle-output-fields">
          <div className="vehicle-output-field-pair">
            <label htmlFor="vehicle-package-pack"><FieldLabel title="DLC pack" hint="lowercase identifier" /><input id="vehicle-package-pack" value={packageDraft.pack_name} onChange={(event) => updatePackage({ pack_name: event.target.value })} disabled={busy} /></label>
            <label htmlFor="vehicle-package-id"><FieldLabel title="Package ID" hint="managed ownership ID" /><input id="vehicle-package-id" value={packageDraft.mod_id} onChange={(event) => updatePackage({ mod_id: event.target.value })} disabled={busy} /></label>
          </div>
          <label htmlFor="vehicle-package-name"><FieldLabel title="Package name" hint="displayed in package tools" /><input id="vehicle-package-name" value={packageDraft.name} onChange={(event) => updatePackage({ name: event.target.value })} disabled={busy} /></label>
          <label htmlFor="vehicle-package-version"><FieldLabel title="Version" hint="single-line release version" /><input id="vehicle-package-version" value={packageDraft.version} onChange={(event) => updatePackage({ version: event.target.value })} disabled={busy} /></label>
          <fieldset className="vehicle-edition-fieldset"><legend>Target editions</legend><div><label className="output-check" htmlFor="vehicle-package-legacy"><input id="vehicle-package-legacy" type="checkbox" checked={packageDraft.legacy} onChange={(event) => updatePackage({ legacy: event.target.checked })} disabled={busy} /><span>Legacy<small>original PC edition</small></span></label><label className="output-check" htmlFor="vehicle-package-enhanced"><input id="vehicle-package-enhanced" type="checkbox" checked={packageDraft.enhanced} onChange={(event) => updatePackage({ enhanced: event.target.checked })} disabled={busy} /><span>Enhanced<small>current PC edition</small></span></label></div></fieldset>
          <div className="vehicle-output-destination"><span>Output folder<small>must be new and outside GTA V</small></span><div><strong title={packageDraft.destination}>{packageDraft.destination || "No destination selected"}</strong><button type="button" className="quiet-button" onClick={onChooseDestination} disabled={busy}>Choose…</button></div></div>
          <div className={`vehicle-package-boundary ${workspaceClean ? "ready" : ""}`}><strong>{workspaceClean ? "Workspace revision is ready" : "Save or reset workspace edits first"}</strong><span>{workspaceClean ? "Review will re-inspect source, catalog, profiles, editions, and output boundaries without writing files." : "Package evidence must bind to a clean, exact authoring revision."}</span></div>
        </div>
        <div className="vehicle-output-actions"><span>{editionSelected ? "Review required before build" : "Choose at least one edition"}</span><button type="button" className="primary-button" onClick={onReviewPackage} disabled={busy || !workspaceClean || !packageDraft.destination || !editionSelected}>Review package</button></div>
      </section>
    </div>

    {(packageReview || packageResult) && <section className="vehicle-package-readiness" aria-live="polite">
      <div className="vehicle-package-readiness-heading"><div><strong>{packageResult ? "Package built" : "Package readiness"}</strong><span>{packageResult ? packageResult.package.root : packageReview?.destination}</span></div><span>{packageResult ? "Output verified" : `${packageReview?.checks.length ?? 0} checks passed`}</span></div>
      {packageReview && !packageResult && <div className="vehicle-package-checks">{packageReview.checks.map((check) => <div key={check.key}><span aria-hidden="true">✓</span><span><strong>{check.label}</strong><small>{check.detail}</small></span></div>)}</div>}
      {packageResult && <dl className="vehicle-package-artifacts"><div><dt>Manifest</dt><dd>{packageResult.package.manifest}</dd></div><div><dt>DLC payload</dt><dd>{packageResult.package.payload}</dd></div><div><dt>Validation report</dt><dd>{packageResult.package.report}</dd></div><div><dt>Profiles</dt><dd>{packageResult.package.profiles ?? "No ALLIN1 runtime profiles included"}</dd></div></dl>}
      {(packageResult?.warnings ?? packageReview?.warnings ?? []).map((warning) => <p className="vehicle-package-warning" key={warning}>{warning}</p>)}
    </section>}
  </>;
}
