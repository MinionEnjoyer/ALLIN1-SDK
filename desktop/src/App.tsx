import {
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import sdkLogo from "../../assets/ALLIN1_SDK.png";
import { tauriClient } from "./client";
import { useDesktopLifecycle } from "./useDesktopLifecycle";
import { deferWorkspace } from "./deferWorkspace";
import { VehicleAxleEditor } from "./VehicleAxleEditor";
import { VehicleOutputEditor, type VehiclePackageDraft } from "./VehicleOutputEditor";
import { VehicleTransmissionEditor } from "./VehicleTransmissionEditor";
import VehicleViewport from "./VehicleViewport";
import SliderField from "./SliderField";
import { handlingSlider } from "./handlingSliders";
import QwenAssistant from "./QwenAssistant";
import LegacyOivExport from "./LegacyOivExport";
import QuickImportPublish from "./QuickImportPublish";
import VehicleIdentityEditor from "./VehicleIdentityEditor";
import RecipeConversionPanel from "./RecipeConversionPanel";
import type { Gxt2ArchiveRequest } from "./Gxt2Workspace";
import type { RpfChangeRequest } from "./RpfChangeSetWorkspace";
import { formatBytes, tokenizeCommandLine } from "./tokenize";
import type {
  DesktopCatalog,
  DesktopClient,
  AssetPreviewResult,
  Envelope,
  HelpTopic,
  LaunchRequest,
  PackageLifecycleExecutionResult,
  PackageLifecycleReviewResult,
  PackageOwnershipCheck,
  PackageReceiptResult,
  PackageResult,
  RecipePlanResult,
  RpfArchiveResult,
  RpfEntryRecord,
  UpdateResult,
  VehicleQuickImportCatalogEntry,
  VehicleQuickImportPreparedResult,
  VehicleQuickImportReviewResult,
  VehicleQuickImportResult,
  VehicleProjectAsset,
  VehicleProjectResult,
  VehicleAppearance,
  VehicleAuthoringAppearanceReview,
  VehicleAuthoringAxleReview,
  VehicleAuthoringAxleSkeleton,
  VehicleAuthoringEditReview,
  VehicleAuthoringDistributionReview,
  VehicleAuthoringLightProfileReview,
  VehicleAuthoringSession,
  VehicleAuthoringTuningReview,
  VehicleAuthoringTransmissionReview,
  VehicleAuthoringWorkspaceReview,
  VehicleAxleConfiguration,
  VehicleDistributionValues,
  VehiclePackageBuildResult,
  VehiclePackageBuildReview,
  VehicleTransmissionConfiguration,
  VehicleLightProfile,
  VehicleTuningBuilder,
  VehicleTuningCollection,
  VehicleViewportResult,
  WorkspaceId,
} from "./types";

type ThemeMode = "light" | "dark" | "system";

const DataToolsWorkspace = deferWorkspace(() => import("./DataToolsWorkspace"));
const ModelsWorkspace = deferWorkspace(() => import("./ModelsWorkspace"));
const WeaponWorkbench = deferWorkspace(() => import("./WeaponWorkbench"));
const PedWorkbench = deferWorkspace(() => import("./PedWorkbench"));
const BinaryWorkspace = deferWorkspace(() => import("./BinaryWorkspace"));
const GraphWorkbench = deferWorkspace(() => import("./GraphWorkbench"));
const MapWorkbench = deferWorkspace(() => import("./MapWorkbench"));
const RuntimeWorkbench = deferWorkspace(() => import("./RuntimeWorkbench"));
const RenderWorkbench = deferWorkspace(() => import("./RenderWorkbench"));
const RpfArchiveUtilities = deferWorkspace(() => import("./RpfArchiveUtilities"));
const RpfTransactionWorkspace = deferWorkspace(() => import("./RpfTransactionWorkspace"));
const Gxt2Workspace = deferWorkspace(() => import("./Gxt2Workspace"));
const RpfChangeSetWorkspace = deferWorkspace(() => import("./RpfChangeSetWorkspace"));

const EMPTY_CATALOG: DesktopCatalog = {
  commands: [],
  navigation: [],
  help_topics: [],
  operations: [],
  job_operations: [],
};

const WORKSPACE_COPY: Record<WorkspaceId, { title: string; description: string; phase: number }> = {
  data_tools: { title: "Data Tools", description: "Edit XML/Lua source, compare metadata and compile data reports.", phase: 5 },
  linker: {
    title: "Package Linker",
    description: "Inspect package ownership, integration links, and safety diagnostics.",
    phase: 3,
  },
  assets: {
    title: "Asset Viewer",
    description: "Browse bounded package inventory and validated preview artifacts.",
    phase: 4,
  },
  workbench: {
    title: "Content Workbench",
    description: "Author Vehicles, Weapons, Peds, and Maps through existing Python services.",
    phase: 3,
  },
  receipts: {
    title: "Package Receipts",
    description: "Verify receipt-backed files, backups, and archive entries without changing GTA V.",
    phase: 4,
  },
  quick_import: {
    title: "Quick Import",
    description: "Prepare a validated Launcher package or standalone Legacy OIV.",
    phase: 4,
  },
  models: {
    title: "Models & Materials",
    description: "Inspect geometry, materials, textures, and compiled renders.",
    phase: 5,
  },
  rpf: {
    title: "RPF Archives",
    description: "Inspect archives, author verified copies, and review guarded transactions.",
    phase: 3,
  },
  recipes: {
    title: "Package Recipes",
    description: "Review and compile ordered OIV package operations.",
    phase: 4,
  },
  help: {
    title: "Help Center",
    description: "Search task-oriented guidance and SDK safety boundaries.",
    phase: 3,
  },
};

const NAV_SECTIONS: { label: string; items: WorkspaceId[] }[] = [
  { label: "Workspace", items: ["linker", "assets", "workbench", "receipts"] },
  { label: "Tools", items: ["quick_import", "models", "rpf", "recipes", "data_tools"] },
  { label: "Reference", items: ["help"] },
];

function WorkspaceIcon({ workspace }: { workspace: WorkspaceId }) {
  const glyphs: Record<WorkspaceId, React.ReactNode> = {
    data_tools: <><path d="M5 4h14v16H5zM5 9h14M10 9v11" /></>,
    linker: <><path d="M7 6.5h3l1.5 2h5.5v9H7z" /><path d="m10 13 2 2 4-4" /></>,
    assets: <><rect x="5" y="5" width="14" height="14" rx="1" /><path d="m7.5 16 3.5-4 2.5 2.5 2-2 2.5 3.5" /><circle cx="15.5" cy="9" r="1" /></>,
    workbench: <><path d="M5 8h14v10H5z" /><path d="M9 8V6h6v2M8 12h8M12 8v10" /></>,
    receipts: <><path d="M7 4h10v16H7z" /><path d="M9.5 9h5M9.5 13h5" /><path d="m9.5 17 1.4 1.4 3-3" /></>,
    quick_import: <><path d="M12 4v11m0 0-4-4m4 4 4-4" /><path d="M5 17v2h14v-2" /></>,
    models: <><path d="m12 4 7 4-7 4-7-4z" /><path d="m5 12 7 4 7-4M5 16l7 4 7-4" /></>,
    rpf: <><path d="M7 4h8l3 3v13H7z" /><path d="M15 4v4h4M10 12h5M10 15h5" /></>,
    recipes: <><path d="M7 4h10v16H7z" /><path d="M10 9h4M10 13h4M10 17h3" /><path d="m9.5 6 1 1 2-2" /></>,
    help: <><circle cx="12" cy="12" r="8" /><path d="M9.8 9.5a2.4 2.4 0 1 1 3 2.3c-.8.3-.8.8-.8 1.7M12 17h.01" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{glyphs[workspace]}</svg>;
}

function messageText(message: Envelope): string {
  if (message.operation === "error") {
    return String(message.payload.message ?? "The operation failed.");
  }
  const result = message.payload.result;
  if (result && typeof result === "object" && "output" in result) {
    return String((result as Record<string, unknown>).output ?? "");
  }
  return JSON.stringify(result ?? message.payload, null, 2);
}

function resultFromJob(message: Envelope): Record<string, unknown> | null {
  const result = message.payload.result;
  return result && typeof result === "object" ? (result as Record<string, unknown>) : null;
}

function readConsoleHistory(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem("allin1.console.history") ?? "[]");
    return Array.isArray(value)
      ? value.filter((item): item is string => typeof item === "string").slice(0, 100)
      : [];
  } catch {
    return [];
  }
}

function safeRows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}

function formatReadiness(value: string): string {
  const sentence = value
    .replaceAll("_", " ")
    .toLocaleLowerCase()
    .replace(/\brpf\b/g, "RPF")
    .replace(/\boiv\b/g, "OIV");
  return sentence ? sentence[0].toLocaleUpperCase() + sentence.slice(1) : "Review required";
}

function StatusPill({
  valid,
  tone,
  children,
}: {
  valid?: boolean;
  tone?: "neutral" | "success" | "warning" | "danger";
  children: React.ReactNode;
}) {
  const resolvedTone = tone ?? (valid === undefined ? "neutral" : valid ? "success" : "danger");
  return <span className={`status-pill ${resolvedTone}`}>{children}</span>;
}

function AuthoringConfirmation({
  title,
  description,
  details,
  confirmLabel,
  warning,
  onCancel,
  onConfirm,
}: {
  title: string;
  description: string;
  details: { label: string; value: string }[];
  confirmLabel: string;
  warning?: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    confirmRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onCancel]);

  return (
    <div className="confirmation-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onCancel(); }}>
      <section className="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="authoring-confirmation-title" aria-describedby="authoring-confirmation-description">
        <div className="confirmation-heading">
          <span className="eyebrow">Authoring write</span>
          <h2 id="authoring-confirmation-title">{title}</h2>
          <p id="authoring-confirmation-description">{description}</p>
        </div>
        <dl className="confirmation-details">
          {details.map((detail) => <div key={detail.label}><dt>{detail.label}</dt><dd title={detail.value}>{detail.value}</dd></div>)}
        </dl>
        {warning && <div className="confirmation-warning"><strong>Review before continuing</strong><span>{warning}</span></div>}
        <div className="confirmation-actions">
          <button type="button" className="quiet-button" onClick={onCancel}>Cancel</button>
          <button ref={confirmRef} type="button" className="primary-button" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </section>
    </div>
  );
}

const PACKAGE_LIFECYCLE_COPY: Record<PackageLifecycleReviewResult["action"], {
  reviewTitle: string;
  prompt: string;
  continueLabel: string;
  executeLabel: string;
  executingLabel: string;
  resultTitle: string;
  destructive: boolean;
}> = {
  install: { reviewTitle: "Install review", prompt: "Install", continueLabel: "Continue to install", executeLabel: "Install package", executingLabel: "Installing…", resultTitle: "Package installed", destructive: false },
  uninstall: { reviewTitle: "Uninstall review", prompt: "Uninstall", continueLabel: "Continue to uninstall", executeLabel: "Uninstall package", executingLabel: "Uninstalling…", resultTitle: "Package uninstalled", destructive: true },
  enable: { reviewTitle: "Enable review", prompt: "Enable", continueLabel: "Continue to enable", executeLabel: "Enable package", executingLabel: "Enabling…", resultTitle: "Package enabled", destructive: false },
  disable: { reviewTitle: "Disable review", prompt: "Disable", continueLabel: "Continue to disable", executeLabel: "Disable package", executingLabel: "Disabling…", resultTitle: "Package disabled", destructive: true },
};

function PackageLifecycleReviewDialog({
  review,
  execution,
  executing,
  error,
  onClose,
  onExecute,
}: {
  review: PackageLifecycleReviewResult;
  execution: PackageLifecycleExecutionResult | null;
  executing: boolean;
  error: string;
  onClose: () => void;
  onExecute: () => void;
}) {
  const dialogRef = useRef<HTMLElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const rollbackRows = Object.entries(review.rollback);
  const copy = PACKAGE_LIFECYCLE_COPY[review.action];

  useEffect(() => setConfirming(false), [review.review_sha256]);

  useEffect(() => {
    dialogRef.current?.focus({ preventScroll: true });
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !executing) {
        event.preventDefault();
        if (confirming && !execution) setConfirming(false);
        else onClose();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [confirming, executing, execution, onClose]);

  useEffect(() => {
    if (confirming && !execution) confirmRef.current?.focus();
  }, [confirming, execution]);

  if (execution) {
    const appliedRollback = Object.entries(execution.rollback);
    return (
      <div className="confirmation-backdrop">
        <section ref={dialogRef} tabIndex={-1} className="confirmation-dialog lifecycle-result-dialog" role="dialog" aria-modal="true" aria-labelledby="lifecycle-result-title" aria-describedby="lifecycle-result-description">
          <div className="confirmation-heading lifecycle-review-heading">
            <div>
              <span className="eyebrow">Managed package result</span>
              <h2 id="lifecycle-result-title">{PACKAGE_LIFECYCLE_COPY[execution.action].resultTitle}</h2>
              <p id="lifecycle-result-description">The reviewed change completed and its post-write ownership state was checked.</p>
            </div>
            <StatusPill tone="success">Complete</StatusPill>
          </div>
          <dl className="confirmation-details lifecycle-result-details">
            <div><dt>Package</dt><dd>{execution.package.name}</dd></div>
            <div><dt>ID</dt><dd>{execution.package.id}</dd></div>
            <div><dt>Target</dt><dd>{execution.gta_path}</dd></div>
            <div><dt>GTA process check</dt><dd>{execution.process_check.gta_closed ? "Closed before write" : "Not verified"}</dd></div>
            <div><dt>Review digest</dt><dd>{execution.review_sha256}</dd></div>
          </dl>
          <section className="lifecycle-result-panel" aria-label="Rollback and ownership result">
            <div className="lifecycle-section-heading"><strong>Rollback and ownership</strong><span>{appliedRollback.length}</span></div>
            <dl className="lifecycle-rollback">
              {appliedRollback.map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "boolean" ? value ? "Yes" : "No" : String(value)}</dd></div>)}
            </dl>
          </section>
          <div className="confirmation-actions"><button type="button" className="primary-button" onClick={onClose}>Done</button></div>
        </section>
      </div>
    );
  }

  if (confirming) {
    return (
      <div className="confirmation-backdrop">
        <section ref={dialogRef} tabIndex={-1} className="confirmation-dialog lifecycle-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="lifecycle-confirm-title" aria-describedby="lifecycle-confirm-description">
          <div className="confirmation-heading">
            <span className="eyebrow">Game write confirmation</span>
            <h2 id="lifecycle-confirm-title">{copy.prompt} {review.package.name}?</h2>
            <p id="lifecycle-confirm-description">The SDK will re-run the preflight, compare its digest, and verify GTA V is closed before changing any managed file.</p>
          </div>
          <dl className="confirmation-details">
            <div><dt>Package</dt><dd>{review.package.id} · {review.package.version}</dd></div>
            <div><dt>Target</dt><dd>{review.gta_path}</dd></div>
            <div><dt>Changes</dt><dd>{review.operations.length} managed {review.operations.length === 1 ? "operation" : "operations"}</dd></div>
            <div><dt>Review digest</dt><dd>{review.review_sha256}</dd></div>
          </dl>
          <div className="confirmation-warning"><strong>Close GTA V before continuing</strong><span>If the package, installation, ownership evidence, or process state has changed, the SDK will refuse the operation and ask for a new review.</span></div>
          {error && <div className="error-banner" role="alert">{error}</div>}
          <div className="confirmation-actions">
            <button type="button" className="quiet-button" disabled={executing} onClick={() => setConfirming(false)}>Back to review</button>
            <button ref={confirmRef} type="button" className={copy.destructive ? "danger-button" : "primary-button"} disabled={executing} onClick={onExecute}>{executing ? copy.executingLabel : copy.executeLabel}</button>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="confirmation-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !executing) onClose(); }}>
      <section ref={dialogRef} tabIndex={-1} className="confirmation-dialog lifecycle-review-dialog" role="dialog" aria-modal="true" aria-labelledby="lifecycle-review-title" aria-describedby="lifecycle-review-description">
        <div className="confirmation-heading lifecycle-review-heading">
          <div>
            <span className="eyebrow">Game-write preflight</span>
            <h2 id="lifecycle-review-title">{copy.reviewTitle}</h2>
            <p id="lifecycle-review-description">This is a current-state review only. No package or GTA V file has changed.</p>
          </div>
          <StatusPill tone={review.ready ? "success" : "danger"}>{review.ready ? "Preflight passed" : "Blocked"}</StatusPill>
        </div>
        <div className="lifecycle-review-grid">
          <div className="lifecycle-review-summary">
            <dl className="confirmation-details">
              <div><dt>Package</dt><dd>{review.package.name}</dd></div>
              <div><dt>ID</dt><dd>{review.package.id}</dd></div>
              <div><dt>Version</dt><dd>{review.package.version}</dd></div>
              <div><dt>Target</dt><dd>{formatReadiness(review.target_edition)}</dd></div>
              {review.action === "install" && <div><dt>Install mode</dt><dd>{review.replacing ? `Replace ${review.installed_version || "installed version"}` : "New managed install"}</dd></div>}
              {(review.action === "enable" || review.action === "disable") && <><div><dt>Current state</dt><dd>{review.current_enabled ? "Enabled" : "Disabled"}</dd></div><div><dt>Target state</dt><dd>{review.target_enabled ? "Enabled" : "Disabled"}</dd></div></>}
              {review.source && <div><dt>Source</dt><dd title={review.source}>{review.source}</dd></div>}
              <div><dt>Review digest</dt><dd>{review.review_sha256}</dd></div>
            </dl>
            <section className="lifecycle-findings" aria-label="Lifecycle review findings">
              <div className="lifecycle-section-heading"><strong>Findings</strong><span>{review.findings.length}</span></div>
              {!review.findings.length && <p>No blocking dependency, ownership, conflict, or destination findings.</p>}
              {review.findings.map((finding, index) => <div className="lifecycle-finding" key={`${finding.code}-${index}`}><StatusPill tone="danger">{finding.severity}</StatusPill><span><strong>{formatReadiness(finding.code)}</strong><small>{finding.message}</small></span></div>)}
            </section>
          </div>
          <div className="lifecycle-change-set">
            <div className="lifecycle-section-heading"><strong>Proposed change set</strong><span>{review.operations.length}</span></div>
            <div className="lifecycle-operation-list">
              {review.operations.map((operation, index) => {
                const target = String(operation.destination ?? (operation.archive && operation.entry ? `${operation.archive} / ${operation.entry}` : operation.entry ?? "Managed target"));
                const kind = operation.kind === "rpf_entry" ? "RPF" : operation.kind === "dlc_registration" ? "DLC" : "FILE";
                return <div className="lifecycle-operation" key={`${target}-${index}`}><span className="row-type">{kind}</span><span><strong>{target}</strong><small>{formatReadiness(String(operation.disposition ?? "review"))}</small></span></div>;
              })}
              {!review.operations.length && <p className="empty-copy">No file or archive changes were declared.</p>}
            </div>
            <dl className="lifecycle-rollback">
              {rollbackRows.map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{typeof value === "boolean" ? value ? "Yes" : "No" : String(value)}</dd></div>)}
            </dl>
          </div>
        </div>
        <div className="lifecycle-review-boundary"><strong>{review.ready ? "Execution remains gated" : "Action remains locked"}</strong><span>{review.ready ? "Continue to a separate action-time confirmation. The SDK will regenerate this digest, prove GTA V is closed, and retain rollback ownership before writing." : "Resolve every finding and run a new preflight before a package action can be confirmed."}</span></div>
        <div className="confirmation-actions">
          <button type="button" className="quiet-button" onClick={onClose}>Close review</button>
          {review.ready && <button type="button" className="primary-button" onClick={() => setConfirming(true)}>{copy.continueLabel}</button>}
        </div>
      </section>
    </div>
  );
}

function ownershipCheckState(check: PackageOwnershipCheck): {
  label: string;
  tone: "valid" | "warning" | "invalid";
} {
  if (check.kind === "rpf_entry") {
    return check.matches_receipt
      ? { label: "Verified", tone: "valid" }
      : { label: "Issue", tone: "invalid" };
  }
  if (check.exists === false || check.hash_matches === false || check.backup_present === false) {
    return { label: "Issue", tone: "invalid" };
  }
  if (check.hash_recorded === false) return { label: "Unproven", tone: "warning" };
  return { label: "Verified", tone: "valid" };
}

function PackageReceiptsWorkspace({
  client,
  result,
  lifecycleReview,
  lifecycleExecution,
  lifecycleExecuting,
  gtaPath,
  busy,
  error,
  lifecycleError,
  onPathChange,
  onInspect,
  onReview,
  onExecute,
  onCloseReview,
  onCancel,
}: {
  client: DesktopClient;
  result: PackageReceiptResult | null;
  lifecycleReview: PackageLifecycleReviewResult | null;
  lifecycleExecution: PackageLifecycleExecutionResult | null;
  lifecycleExecuting: boolean;
  gtaPath: string;
  busy: boolean;
  error: string;
  lifecycleError: string;
  onPathChange: (path: string) => void;
  onInspect: (path: string, selectedId: string | null) => void;
  onReview: (action: PackageLifecycleReviewResult["action"], subject: string) => void;
  onExecute: (review: PackageLifecycleReviewResult) => void;
  onCloseReview: () => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedCheckIndex, setSelectedCheckIndex] = useState(0);
  const packages = result?.packages ?? [];
  const selectedPackage = packages.find((item) => item.mod_id === result?.selected_id) ?? null;
  const checks = result?.verification?.checks ?? [];
  const selectedCheck = checks[selectedCheckIndex] ?? null;
  const receipt = result?.receipt ?? null;
  const receiptFiles = safeRows(receipt?.files);
  const receiptRpfEntries = safeRows(receipt?.rpf_entries);
  const filteredPackages = packages.filter((item) => `${item.name} ${item.mod_id} ${item.version}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

  useEffect(() => setSelectedCheckIndex(0), [result?.selected_id]);

  const chooseGame = async () => {
    const selected = await client.selectPath("gta_folder");
    if (!selected) return;
    onPathChange(selected);
    onInspect(selected, null);
  };

  const chooseInstallCandidate = async () => {
    const selected = await client.selectPath("mod_package");
    if (selected) onReview("install", selected);
  };

  return (
    <section className="workspace-section receipts-workspace" aria-labelledby="receipts-title">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Installation ownership</span>
          <h2 id="receipts-title">Package Receipts</h2>
          <p>Review SDK-managed installs and verify their current on-disk ownership evidence.</p>
        </div>
        <div className="heading-actions">
          {busy && <button type="button" className="quiet-button" onClick={onCancel}>Cancel</button>}
          <button type="button" className="primary-button" onClick={chooseGame} disabled={busy}>Choose GTA V</button>
          <button type="button" className="quiet-button" onClick={chooseInstallCandidate} disabled={busy || !gtaPath}>Review install</button>
          <button type="button" className="quiet-button" onClick={() => onInspect(gtaPath, result?.selected_id ?? null)} disabled={busy || !gtaPath}>Refresh</button>
        </div>
      </div>

      <div className="source-strip">
        <span className={`activity-dot ${busy ? "busy" : error ? "error" : result ? "ready" : ""}`} />
        <strong>{busy ? "Verifying receipt evidence" : gtaPath ? "GTA V installation" : "Choose a GTA V installation"}</strong>
        <span className="source-path" title={gtaPath}>{gtaPath || "No installation selected"}</span>
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      {lifecycleError && <div className="error-banner" role="alert">{lifecycleError}</div>}
      {result && <div className="summary-row receipts-summary" role="status">
        <StatusPill tone="neutral">Receipt evidence</StatusPill>
        <span><strong>{result.package_count}</strong> managed {result.package_count === 1 ? "package" : "packages"}</span>
        <span><strong>{result.enabled_count}</strong> enabled</span>
        <span><strong>{formatReadiness(result.edition)}</strong> installation</span>
        {result.verification && <StatusPill tone={result.verification.ownership_verified ? "success" : result.verification.healthy ? "warning" : "danger"}>{result.verification.ownership_verified ? "Ownership verified" : result.verification.healthy ? "Evidence incomplete" : `${result.issue_count} ${result.issue_count === 1 ? "issue" : "issues"}`}</StatusPill>}
      </div>}

      <div className={`panel-grid receipts-grid ${result ? "has-result" : "is-empty"}`}>
        <section className="pane receipts-inventory-pane" aria-label="Managed package receipts">
          <div className="pane-header"><div><span className="pane-kicker">Installation</span><h3>Managed packages</h3></div><span className="pane-count">{filteredPackages.length}/{packages.length}</span></div>
          {result && packages.length > 0 && <label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Filter managed packages</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter managed packages" /></label>}
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>No installation selected</strong><p>Choose a GTA V folder to discover validated ALLIN1 receipts.</p><button type="button" className="text-action" onClick={chooseGame}>Select installation</button></div>}
            {result && !packages.length && <div className="pane-empty"><strong>No managed packages</strong><p>This installation does not contain any valid ALLIN1 package receipts.</p></div>}
            {result && packages.length > 0 && !filteredPackages.length && <p className="empty-copy">No managed packages match this filter.</p>}
            {filteredPackages.map((item) => <button type="button" key={item.mod_id} className={`data-row receipt-package-row ${result?.selected_id === item.mod_id ? "selected" : ""}`} onClick={() => onInspect(gtaPath, item.mod_id)} disabled={busy}>
              <span className="row-type">PKG</span>
              <span><strong>{item.name}</strong><small>{item.mod_id} · {item.version}</small></span>
              <span className={`row-state ${item.enabled ? "valid" : "warning"}`}>{item.enabled ? "Enabled" : "Disabled"}</span>
            </button>)}
          </div>
        </section>

        <section className="pane receipts-checks-pane" aria-label="Ownership checks">
          <div className="pane-header"><div><span className="pane-kicker">Verification</span><h3>Ownership checks</h3></div><span className="pane-count">{checks.length}</span></div>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>Waiting for installation</strong><p>File hashes, backups, and archive-entry checks will appear here.</p></div>}
            {result && !result.selected_id && <div className="pane-empty"><strong>Select a managed package</strong><p>Receipt inspection runs only after you choose an installed package.</p></div>}
            {result?.verification?.issues.map((issue, index) => <div className="finding-row receipt-issue" key={`${issue}-${index}`}><span className="row-state invalid">Issue</span><span><strong>Ownership mismatch</strong><small>{issue}</small></span></div>)}
            {result?.selected_id && !checks.length && !result.verification?.issues.length && <div className="pane-empty"><strong>No owned payload records</strong><p>The receipt does not declare loose files or RPF entries.</p></div>}
            {checks.map((check, index) => {
              const state = ownershipCheckState(check);
              const target = check.kind === "rpf_entry" ? `${check.archive ?? "Archive"} / ${check.entry ?? "entry"}` : check.destination ?? "Managed file";
              return <button type="button" key={`${check.kind}-${target}-${index}`} className={`data-row receipt-check-row ${selectedCheckIndex === index ? "selected" : ""}`} onClick={() => setSelectedCheckIndex(index)}>
                <span className="row-type">{check.kind === "rpf_entry" ? "RPF" : "FILE"}</span>
                <span><strong>{target}</strong><small>{check.kind === "rpf_entry" ? "Archive entry compared with receipt" : check.backup_present === null ? "Managed payload" : "Managed payload with rollback backup"}</small></span>
                <span className={`row-state ${state.tone}`}>{state.label}</span>
              </button>;
            })}
          </div>
        </section>

        <aside className="pane receipts-detail-pane" aria-label="Receipt evidence">
          <div className="pane-header"><div><span className="pane-kicker">Inspector</span><h3>{selectedCheck ? "Selected evidence" : selectedPackage ? "Receipt detail" : "Package evidence"}</h3></div></div>
          {!selectedPackage && <div className="pane-empty inspector-empty"><strong>No package selected</strong><p>Choose a managed package to inspect its immutable receipt and current disk evidence.</p></div>}
          {selectedPackage && <dl className="detail-list">
            <div><dt>Package</dt><dd>{selectedPackage.name}</dd></div>
            <div><dt>ID</dt><dd>{selectedPackage.mod_id}</dd></div>
            <div><dt>Version</dt><dd>{selectedPackage.version}</dd></div>
            <div><dt>Type</dt><dd>{selectedPackage.mod_type}</dd></div>
            <div><dt>State</dt><dd>{selectedPackage.enabled ? "Enabled" : "Disabled"}</dd></div>
            <div><dt>Installed</dt><dd>{String(receipt?.installed_at ?? "Not recorded")}</dd></div>
            <div><dt>Owned files</dt><dd>{receiptFiles.length}</dd></div>
            <div><dt>RPF entries</dt><dd>{receiptRpfEntries.length}</dd></div>
            {selectedCheck && Object.entries(selectedCheck).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value === null ? "Not applicable" : typeof value === "boolean" ? value ? "Yes" : "No" : String(value)}</dd></div>)}
            <div><dt>Receipt root</dt><dd>{result?.receipt_root}</dd></div>
          </dl>}
          {selectedPackage && <div className="receipt-lifecycle-actions"><span><strong>Lifecycle controls</strong><small>Review state changes or removal without changing the installation.</small></span><div className="receipt-lifecycle-buttons"><button type="button" className="quiet-button" disabled={busy} onClick={() => onReview(selectedPackage.enabled ? "disable" : "enable", selectedPackage.mod_id)}>Review {selectedPackage.enabled ? "disable" : "enable"}</button><button type="button" className="quiet-button" disabled={busy} onClick={() => onReview("uninstall", selectedPackage.mod_id)}>Review uninstall</button></div></div>}
          <div className="recipe-safety-note"><strong>Guarded write boundary</strong><span>Every lifecycle change requires a fresh digest-bound review, a separate confirmation, a closed-game check, and SDK-owned rollback evidence.</span></div>
        </aside>
      </div>
      {lifecycleReview && <PackageLifecycleReviewDialog review={lifecycleReview} execution={lifecycleExecution} executing={lifecycleExecuting} error={lifecycleError} onClose={onCloseReview} onExecute={() => onExecute(lifecycleReview)} />}
    </section>
  );
}

function EmptyWorkspace({ workspace }: { workspace: WorkspaceId }) {
  const copy = WORKSPACE_COPY[workspace];
  const rows = workspace === "workbench"
    ? ["Vehicle definitions", "Weapon definitions", "Ped definitions", "Map projects"]
    : ["Python service", "Typed desktop contract", "React workspace"];
  return (
    <section className="workspace-section placeholder-workspace" aria-labelledby={`${workspace}-title`}>
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Migration phase {copy.phase}</span>
          <h2 id={`${workspace}-title`}>{copy.title}</h2>
          <p>{copy.description}</p>
        </div>
      </div>
      <div className="migration-board" aria-label={`${copy.title} migration status`}>
        <div className="migration-board-header"><strong>Surface readiness</strong><span>Phase {copy.phase}</span></div>
        <div className="migration-rows">
          {rows.map((row, index) => (
            <div className="migration-row" key={row}>
              <WorkspaceIcon workspace={workspace} />
              <div><strong>{row}</strong><small>{workspace === "workbench" ? "Existing Python domain service is mapped" : index < 2 ? "Available and preserved" : "Desktop view scheduled in this migration phase"}</small></div>
              <span className={workspace === "workbench" || index < 2 ? "state-ready" : "state-queued"}>{workspace === "workbench" || index < 2 ? "Mapped" : "Queued"}</span>
            </div>
          ))}
        </div>
        <div className="boundary-note"><strong>Current access</strong><span>This capability remains available in the Tkinter desktop while the React surface is completed. Validation and write policy stay in Python.</span></div>
      </div>
    </section>
  );
}

type WorkbenchCategory = "vehicles" | "weapons" | "peds" | "maps" | "runtime" | "render";

const VEHICLE_AUTHORING_FIELDS = [
  { title: "Identity", fields: [
    ["vehicle.gameName", "Display label"],
    ["vehicle.vehicleMakeName", "Manufacturer label"],
    ["vehicle.txdName", "Texture dictionary"],
    ["vehicle.vehicleClass", "Vehicle class"],
    ["vehicle.type", "Vehicle type"],
    ["vehicle.layout", "Layout"],
    ["vehicle.audioNameHash", "Audio profile"],
  ] },
  { title: "Handling", fields: [
    ["handling.fMass", "Mass"],
    ["handling.fInitialDragCoeff", "Initial drag coefficient"],
    ["handling.fDriveBiasFront", "Front drive bias"],
    ["handling.nInitialDriveGears", "Drive gears"],
    ["handling.fInitialDriveForce", "Initial drive force"],
    ["handling.fDriveInertia", "Drive inertia"],
    ["handling.fInitialDriveMaxFlatVel", "Maximum flat velocity"],
    ["handling.fBrakeForce", "Brake force"],
    ["handling.fBrakeBiasFront", "Front brake bias"],
    ["handling.fHandBrakeForce", "Handbrake force"],
    ["handling.fSteeringLock", "Steering lock"],
    ["handling.fTractionCurveMax", "Traction curve maximum"],
    ["handling.fTractionCurveMin", "Traction curve minimum"],
    ["handling.fTractionCurveLateral", "Lateral traction curve"],
    ["handling.fLowSpeedTractionLossMult", "Low-speed traction loss"],
    ["handling.fTractionBiasFront", "Front traction bias"],
    ["handling.fTractionLossMult", "Traction loss multiplier"],
    ["handling.fSuspensionForce", "Suspension force"],
    ["handling.fSuspensionCompDamp", "Suspension compression damping"],
    ["handling.fSuspensionReboundDamp", "Suspension rebound damping"],
    ["handling.fSuspensionUpperLimit", "Suspension upper limit"],
    ["handling.fSuspensionLowerLimit", "Suspension lower limit"],
    ["handling.fSuspensionRaise", "Suspension raise"],
    ["handling.fSuspensionBiasFront", "Front suspension bias"],
    ["handling.fAntiRollBarForce", "Anti-roll bar force"],
    ["handling.fAntiRollBarBiasFront", "Front anti-roll bias"],
    ["handling.fCollisionDamageMult", "Collision damage multiplier"],
    ["handling.fWeaponDamageMult", "Weapon damage multiplier"],
    ["handling.fDeformationDamageMult", "Deformation damage multiplier"],
    ["handling.fEngineDamageMult", "Engine damage multiplier"],
  ] },
] as const;

type VehicleAppearanceDraft = {
  colors: { indices: string; liveries: string }[];
  kits: string[];
  light_settings: string;
  siren_settings: string;
};

const appearanceDraftFromSession = (appearance: VehicleAppearance | null): VehicleAppearanceDraft => ({
  colors: (appearance?.colors ?? []).map((color) => ({
    indices: color.indices.join(", "),
    liveries: color.liveries.map((enabled) => enabled ? "1" : "0").join(", "),
  })),
  kits: [...(appearance?.kits ?? [])],
  light_settings: appearance?.light_settings ?? "0",
  siren_settings: appearance?.siren_settings ?? "0",
});

const parseAppearanceDraft = (draft: VehicleAppearanceDraft) => ({
  colors: draft.colors.map((color, index) => {
    const indices = color.indices.split(",").map((value) => value.trim()).filter(Boolean);
    if (indices.length < 4 || indices.length > 8 || indices.some((value) => !/^\d+$/.test(value) || Number(value) > 255)) {
      throw new Error(`Color preset ${index + 1} needs 4–8 comma-separated indices from 0 through 255.`);
    }
    const liveryTokens = color.liveries.split(",").map((value) => value.trim().toLocaleLowerCase()).filter(Boolean);
    if (liveryTokens.length > 64 || liveryTokens.some((value) => !["0", "1", "true", "false"].includes(value))) {
      throw new Error(`Color preset ${index + 1} livery flags must use 0, 1, true, or false.`);
    }
    return {
      indices: indices.map(Number),
      liveries: liveryTokens.map((value) => value === "1" || value === "true"),
    };
  }),
  kits: [...draft.kits],
  light_settings: draft.light_settings.trim(),
  siren_settings: draft.siren_settings.trim(),
});

type AppearanceEditorSection = "presets" | "tuning" | "lights";
type TuningEntryMode = "existing" | "new" | "duplicate";

const emptyVehiclePackageDraft = (): VehiclePackageDraft => ({
  destination: "",
  pack_name: "",
  mod_id: "",
  name: "",
  version: "1.0.0",
  legacy: true,
  enhanced: true,
});

const vehiclePackageDraftFromSession = (session: VehicleAuthoringSession): VehiclePackageDraft => {
  const model = (session.selected_model ?? session.project.models[0]?.model ?? "vehicle").toLocaleLowerCase();
  return {
    ...emptyVehiclePackageDraft(),
    pack_name: model,
    mod_id: `vehicle.${model}`,
    name: `${session.distribution?.name || model} vehicle add-on`,
  };
};

const TUNING_COLLECTION_LABELS: Record<VehicleTuningCollection, string> = {
  visibleMods: "Visible",
  linkMods: "Linked",
  statMods: "Performance",
  slotNames: "Slot labels",
};

const tuningDraftDefaults = (
  builder: VehicleTuningBuilder,
  collection: VehicleTuningCollection,
) => Object.fromEntries(Object.entries(builder.field_schemas[collection] ?? {}).map(
  ([field, schema]) => [field, schema.default],
));

function VehicleTuningEditor({
  builder,
  kits,
  collection,
  selectedIndex,
  entryMode,
  entryDraft,
  kitType,
  liveryNames,
  entryDirty,
  kitDirty,
  busy,
  onLoadKit,
  onCollection,
  onSelectEntry,
  onNewEntry,
  onDuplicateEntry,
  onEntryField,
  onKitType,
  onLiveryNames,
  onReviewKit,
  onReviewEntry,
  onRemoveEntry,
  onMoveEntry,
  onReset,
}: {
  builder: VehicleTuningBuilder | null;
  kits: VehicleAppearance["available_kits"];
  collection: VehicleTuningCollection;
  selectedIndex: number | null;
  entryMode: TuningEntryMode;
  entryDraft: Record<string, string> | null;
  kitType: string;
  liveryNames: string;
  entryDirty: boolean;
  kitDirty: boolean;
  busy: boolean;
  onLoadKit: (kitName: string) => void;
  onCollection: (collection: VehicleTuningCollection) => void;
  onSelectEntry: (index: number) => void;
  onNewEntry: () => void;
  onDuplicateEntry: () => void;
  onEntryField: (field: string, value: string) => void;
  onKitType: (value: string) => void;
  onLiveryNames: (value: string) => void;
  onReviewKit: () => void;
  onReviewEntry: () => void;
  onRemoveEntry: () => void;
  onMoveEntry: (newIndex: number) => void;
  onReset: () => void;
}) {
  if (!builder) {
    return <div className="pane-empty tuning-empty"><strong>No tuning kit loaded</strong><p>Select a kit to inspect its editable entries, linked models, and validation findings.</p>{kits.length > 0 && <button type="button" className="quiet-button" onClick={() => onLoadKit(kits[0].name)} disabled={busy}>Load first kit</button>}</div>;
  }
  const entries = builder.entries.filter((entry) => entry.collection === collection);
  const selectedEntry = entryMode === "existing"
    ? entries.find((entry) => entry.index === selectedIndex) ?? null
    : null;
  const schema = builder.field_schemas[collection] ?? {};
  const fieldNames = [...new Set([...Object.keys(schema), ...Object.keys(entryDraft ?? {})])];
  const selectedPosition = selectedEntry ? entries.findIndex((entry) => entry.index === selectedEntry.index) : -1;
  return <>
    <div className="vehicle-authoring-intro tuning-intro"><strong>Tuning-kit internals</strong><span>Every entry is validated against the Python builder before a new workspace revision is offered.</span></div>
    <fieldset className="tuning-kit-metadata"><legend>Kit metadata</legend>
      <label htmlFor="vehicle-tuning-kit"><span>Tuning kit<small>carcols kits</small></span><select id="vehicle-tuning-kit" value={builder.kit_name} onChange={(event) => onLoadKit(event.target.value)} disabled={busy}>{kits.map((kit) => <option key={kit.name} value={kit.name}>{kit.name}</option>)}</select></label>
      <label htmlFor="vehicle-tuning-type"><span>Kit type<small>kitType</small></span><input id="vehicle-tuning-type" value={kitType} onChange={(event) => onKitType(event.target.value)} disabled={busy} /></label>
      <label htmlFor="vehicle-tuning-liveries"><span>Livery labels<small>comma-separated</small></span><input id="vehicle-tuning-liveries" value={liveryNames} onChange={(event) => onLiveryNames(event.target.value)} disabled={busy} /></label>
      <div className="tuning-inline-actions"><span>{builder.kit_id} · {builder.entries.length} entries</span><button type="button" className="quiet-button compact" onClick={onReviewKit} disabled={busy || !kitDirty}>Review kit metadata</button></div>
    </fieldset>
    <div className="tuning-collection-tabs" role="tablist" aria-label="Tuning entry collection">{builder.collections.map((item) => <button type="button" role="tab" aria-selected={collection === item} className={collection === item ? "active" : ""} key={item} onClick={() => onCollection(item)} disabled={busy}><span>{TUNING_COLLECTION_LABELS[item]}</span><small>{builder.entries.filter((entry) => entry.collection === item).length}</small></button>)}</div>
    <div className="tuning-entry-workspace">
      <section className="tuning-entry-list" aria-label={`${TUNING_COLLECTION_LABELS[collection]} entries`}>
        <div className="tuning-section-heading"><span>{TUNING_COLLECTION_LABELS[collection]}</span><button type="button" className="text-action" onClick={onNewEntry} disabled={busy}>New entry</button></div>
        {!entries.length && <p className="empty-copy">This collection has no entries.</p>}
        {entries.map((entry) => <button type="button" className={`tuning-entry-row ${entryMode === "existing" && selectedIndex === entry.index ? "selected" : ""}`} key={entry.key} onClick={() => onSelectEntry(entry.index)} disabled={busy}><span>{String(entry.index + 1).padStart(2, "0")}</span><span><strong>{entry.summary || "Unnamed entry"}</strong><small>{entry.mod_type || collection}</small></span></button>)}
      </section>
      <section className="tuning-entry-form" aria-label="Tuning entry editor">
        <div className="tuning-section-heading"><span>{entryMode === "new" ? "New entry" : entryMode === "duplicate" ? "Duplicate entry" : selectedEntry?.summary || "Entry fields"}</span>{entryMode === "existing" && selectedEntry && <button type="button" className="text-action" onClick={onDuplicateEntry} disabled={busy}>Duplicate</button>}</div>
        {!entryDraft && <p className="empty-copy">Select an entry or start a new one.</p>}
        {entryDraft && <div className="tuning-field-list">{fieldNames.map((field) => {
          const fieldSchema = schema[field];
          const value = entryDraft[field] ?? "";
          return <label key={field} htmlFor={`vehicle-tuning-${collection}-${field}`}><span>{field}<small>{fieldSchema ? `${fieldSchema.kind}${fieldSchema.required ? " · required" : ""}` : "source field"}</small></span>{fieldSchema?.kind === "boolean" ? <select id={`vehicle-tuning-${collection}-${field}`} value={value} onChange={(event) => onEntryField(field, event.target.value)} disabled={busy}><option value="true">true</option><option value="false">false</option></select> : fieldSchema?.kind === "vmt" ? <select id={`vehicle-tuning-${collection}-${field}`} value={value} onChange={(event) => onEntryField(field, event.target.value)} disabled={busy}>{!builder.vmt_types.includes(value) && <option value={value}>{value || "Choose type"}</option>}{builder.vmt_types.map((type) => <option key={type} value={type}>{type}</option>)}</select> : <input id={`vehicle-tuning-${collection}-${field}`} value={value} onChange={(event) => onEntryField(field, event.target.value)} disabled={busy} />}</label>;
        })}</div>}
        {entryDraft && <div className="tuning-entry-actions"><div>{entryMode === "existing" && selectedEntry && <><button type="button" className="text-action danger" onClick={onRemoveEntry} disabled={busy}>Remove</button><button type="button" className="quiet-button compact" onClick={() => onMoveEntry(Math.max(0, selectedPosition - 1))} disabled={busy || selectedPosition <= 0} aria-label="Move entry up">↑</button><button type="button" className="quiet-button compact" onClick={() => onMoveEntry(Math.min(entries.length - 1, selectedPosition + 1))} disabled={busy || selectedPosition < 0 || selectedPosition >= entries.length - 1} aria-label="Move entry down">↓</button></>}</div><button type="button" className="primary-button compact" onClick={onReviewEntry} disabled={busy || !entryDirty}>{entryMode === "existing" ? "Review entry" : "Review addition"}</button></div>}
      </section>
    </div>
    <div className="tuning-evidence-grid"><section><div className="tuning-section-heading"><span>Asset inventory</span><small>{builder.assets.filter((asset) => asset.referenced).length}/{builder.assets.length} linked</small></div>{builder.assets.slice(0, 6).map((asset) => <div className="tuning-evidence-row" key={asset.path}><span className={asset.referenced ? "linked" : "unlinked"}>{asset.referenced ? "linked" : "unused"}</span><span><strong>{asset.name}</strong><small>{asset.kind}</small></span></div>)}{!builder.assets.length && <p className="empty-copy">No stream assets were found.</p>}</section><section><div className="tuning-section-heading"><span>Validation</span><small>{builder.error_count} errors · {builder.warning_count} warnings</small></div>{builder.findings.slice(0, 6).map((finding, index) => <div className={`tuning-finding severity-${finding.severity}`} key={`${finding.code}-${index}`}><strong>{finding.code.replaceAll("_", " ")}</strong><span>{finding.message}</span></div>)}{!builder.findings.length && <p className="empty-copy">No tuning findings.</p>}</section></div>
    <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={onReset} disabled={busy || (!entryDirty && !kitDirty)}>Reset tuning edits</button></div>
  </>;
}

function VehicleLightProfileEditor({
  profiles,
  profileId,
  draft,
  dirty,
  busy,
  onProfile,
  onField,
  onReset,
  onReview,
}: {
  profiles: VehicleLightProfile[];
  profileId: string;
  draft: Record<string, string>;
  dirty: boolean;
  busy: boolean;
  onProfile: (profileId: string) => void;
  onField: (field: string, value: string) => void;
  onReset: () => void;
  onReview: () => void;
}) {
  const profile = profiles.find((item) => item.profile_id === profileId) ?? null;
  return <>
    <div className="vehicle-authoring-intro"><strong>Light-profile scalars</strong><span>Edit the resolved carcols profile directly. The vehicle’s profile reference remains in Presets.</span></div>
    <fieldset className="light-profile-fields"><legend>Resolved profile</legend>
      <label htmlFor="vehicle-light-profile"><span>Profile<small>carcols lights</small></span><select id="vehicle-light-profile" value={profileId} onChange={(event) => onProfile(event.target.value)} disabled={busy}>{profiles.map((item) => <option key={item.profile_id} value={item.profile_id}>{item.profile_id} · {item.name || "Unnamed profile"}</option>)}</select></label>
      {profile && <div className="light-profile-source"><span>Source</span><strong title={profile.source}>{profile.source.split(/[\\/]/).at(-1)}</strong></div>}
    </fieldset>
    {profile ? <fieldset className="light-scalar-list"><legend>Scalar values</legend>{Object.entries(draft).sort(([left], [right]) => left.localeCompare(right)).map(([field, value]) => <label key={field} htmlFor={`vehicle-light-value-${field}`}><span>{field.split(".").at(-1)}<small>{field}</small></span><input id={`vehicle-light-value-${field}`} value={value} onChange={(event) => onField(field, event.target.value)} disabled={busy} /></label>)}</fieldset> : <div className="pane-empty"><strong>No light profiles resolved</strong><p>This authoring workspace has no carcols light profiles to edit.</p></div>}
    <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={onReset} disabled={busy || !dirty}>Reset values</button><button type="button" className="primary-button" onClick={onReview} disabled={busy || !dirty}>Review light profile</button></div>
  </>;
}

function ContentWorkbench({
  client,
  result,
  source,
  gtaPath,
  category,
  busy,
  activeJob,
  error,
  onSourceChange,
  onGameChange,
  onCategoryChange,
  onInspect,
  onCancel,
  onJob,
  navigationNotice,
  onDirtyChange,
  onHelp,
  requestedModel,
}: {
  client: DesktopClient;
  result: VehicleProjectResult | null;
  source: string;
  gtaPath: string;
  category: WorkbenchCategory;
  busy: boolean;
  activeJob: string | null;
  error: string;
  onSourceChange: (source: string) => void;
  onGameChange: (path: string) => void;
  onCategoryChange: (category: WorkbenchCategory) => void;
  onInspect: (source: string, gtaPath: string) => void;
  onCancel: () => void;
  onJob: (jobId: string | null) => void;
  navigationNotice: string;
  onDirtyChange: (dirty: boolean) => void;
  onHelp: () => void;
  requestedModel?: { source: string; model: string } | null;
}) {
  const [modelQuery, setModelQuery] = useState("");
  const [assetQuery, setAssetQuery] = useState("");
  const [selectedModelId, setSelectedModelId] = useState("");
  const [selectedAsset, setSelectedAsset] = useState<VehicleProjectAsset | null>(null);
  const [preview, setPreview] = useState<AssetPreviewResult | null>(null);
  const [viewportResult, setViewportResult] = useState<VehicleViewportResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [authoringSession, setAuthoringSession] = useState<VehicleAuthoringSession | null>(null);
  const [authoringValues, setAuthoringValues] = useState<Record<string, string>>({});
  const [authoringBaseline, setAuthoringBaseline] = useState<Record<string, string>>({});
  const [appearanceDraft, setAppearanceDraft] = useState<VehicleAppearanceDraft | null>(null);
  const [appearanceBaseline, setAppearanceBaseline] = useState<VehicleAppearanceDraft | null>(null);
  const [appearanceSection, setAppearanceSection] = useState<AppearanceEditorSection>("presets");
  const [tuningBuilder, setTuningBuilder] = useState<VehicleTuningBuilder | null>(null);
  const [tuningCollection, setTuningCollection] = useState<VehicleTuningCollection>("visibleMods");
  const [tuningSelectedIndex, setTuningSelectedIndex] = useState<number | null>(null);
  const [tuningEntryMode, setTuningEntryMode] = useState<TuningEntryMode>("existing");
  const [tuningEntryDraft, setTuningEntryDraft] = useState<Record<string, string> | null>(null);
  const [tuningEntryBaseline, setTuningEntryBaseline] = useState<Record<string, string> | null>(null);
  const [tuningKitType, setTuningKitType] = useState("");
  const [tuningKitTypeBaseline, setTuningKitTypeBaseline] = useState("");
  const [tuningLiveries, setTuningLiveries] = useState("");
  const [tuningLiveriesBaseline, setTuningLiveriesBaseline] = useState("");
  const [lightProfileId, setLightProfileId] = useState("");
  const [lightProfileDraft, setLightProfileDraft] = useState<Record<string, string>>({});
  const [lightProfileBaseline, setLightProfileBaseline] = useState<Record<string, string>>({});
  const [axleDraft, setAxleDraft] = useState<VehicleAxleConfiguration | null>(null);
  const [axleBaseline, setAxleBaseline] = useState<VehicleAxleConfiguration | null>(null);
  const [axleSkeleton, setAxleSkeleton] = useState<VehicleAuthoringAxleSkeleton | null>(null);
  const [selectedAxleOrder, setSelectedAxleOrder] = useState<number | null>(null);
  const [transmissionDraft, setTransmissionDraft] = useState<VehicleTransmissionConfiguration | null>(null);
  const [transmissionBaseline, setTransmissionBaseline] = useState<VehicleTransmissionConfiguration | null>(null);
  const [distributionDraft, setDistributionDraft] = useState<VehicleDistributionValues | null>(null);
  const [distributionBaseline, setDistributionBaseline] = useState<VehicleDistributionValues | null>(null);
  const [packageDraft, setPackageDraft] = useState<VehiclePackageDraft>(emptyVehiclePackageDraft);
  const [packageReview, setPackageReview] = useState<VehiclePackageBuildReview | null>(null);
  const [packageResult, setPackageResult] = useState<VehiclePackageBuildResult | null>(null);
  const [identityGuarded, setIdentityGuarded] = useState(false);
  const [authoringMode, setAuthoringMode] = useState<"evidence" | "edit" | "appearance" | "axles" | "transmission" | "output" | "identity">("evidence");
  const [authoringBusy, setAuthoringBusy] = useState(false);
  const [authoringError, setAuthoringError] = useState("");
  const [authoringNotice, setAuthoringNotice] = useState("");
  const [pendingCreate, setPendingCreate] = useState<{ review: VehicleAuthoringWorkspaceReview; payload: Record<string, unknown> } | null>(null);
  const [pendingEdit, setPendingEdit] = useState<{ review: VehicleAuthoringEditReview; payload: Record<string, unknown> } | null>(null);
  const [pendingAppearance, setPendingAppearance] = useState<{ review: VehicleAuthoringAppearanceReview; payload: Record<string, unknown> } | null>(null);
  const [pendingTuning, setPendingTuning] = useState<{ review: VehicleAuthoringTuningReview; payload: Record<string, unknown> } | null>(null);
  const [pendingLightProfile, setPendingLightProfile] = useState<{ review: VehicleAuthoringLightProfileReview; payload: Record<string, unknown> } | null>(null);
  const [pendingAxles, setPendingAxles] = useState<{ review: VehicleAuthoringAxleReview; payload: Record<string, unknown> } | null>(null);
  const [pendingTransmission, setPendingTransmission] = useState<{ review: VehicleAuthoringTransmissionReview; payload: Record<string, unknown> } | null>(null);
  const [pendingDistribution, setPendingDistribution] = useState<{ review: VehicleAuthoringDistributionReview; payload: Record<string, unknown> } | null>(null);
  const [pendingPackageBuild, setPendingPackageBuild] = useState<{ review: VehiclePackageBuildReview; payload: Record<string, unknown> } | null>(null);
  const [pendingHistory, setPendingHistory] = useState<"undo" | "redo" | null>(null);
  const latestPreviewRevision = useRef("");
  const completedPreviewRevision = useRef("");
  const previewJob = useRef<string | null>(null);
  const latestAuthoringRevision = useRef("");
  const completedAuthoringRevision = useRef("");
  const categories: { id: WorkbenchCategory; label: string; status: string }[] = [
    { id: "vehicles", label: "Vehicles", status: "Inspection active" },
    { id: "weapons", label: "Weapons", status: "Inspect & author" },
    { id: "peds", label: "Peds", status: "Inspect & author" },
    { id: "maps", label: "Maps", status: "Topology + packages" },
    { id: "runtime", label: "Story runtime", status: "Preflight + build" },
    { id: "render", label: "Render studio", status: "Blender + export" },
  ];
  const activeResult = authoringSession?.project ?? result;
  const models = activeResult?.models ?? [];
  const fieldAuthoringDirty = Boolean(authoringSession) && Object.keys(authoringBaseline).some(
    (field) => authoringValues[field] !== authoringBaseline[field],
  );
  const appearanceDirty = Boolean(authoringSession && appearanceDraft && appearanceBaseline)
    && JSON.stringify(appearanceDraft) !== JSON.stringify(appearanceBaseline);
  const tuningEntryDirty = Boolean(tuningEntryDraft) && (
    tuningEntryMode !== "existing" || JSON.stringify(tuningEntryDraft) !== JSON.stringify(tuningEntryBaseline)
  );
  const tuningKitDirty = Boolean(tuningBuilder) && (
    tuningKitType !== tuningKitTypeBaseline || tuningLiveries !== tuningLiveriesBaseline
  );
  const lightProfileDirty = Boolean(lightProfileId)
    && JSON.stringify(lightProfileDraft) !== JSON.stringify(lightProfileBaseline);
  const axleDirty = Boolean(axleDraft)
    && JSON.stringify(axleDraft) !== JSON.stringify(axleBaseline);
  const transmissionDirty = Boolean(transmissionDraft)
    && JSON.stringify(transmissionDraft) !== JSON.stringify(transmissionBaseline);
  const distributionDirty = Boolean(distributionDraft)
    && JSON.stringify(distributionDraft) !== JSON.stringify(distributionBaseline);
  const otherAuthoringDirty = fieldAuthoringDirty || appearanceDirty || tuningEntryDirty || tuningKitDirty || lightProfileDirty || axleDirty || transmissionDirty || distributionDirty;
  const authoringDirty = otherAuthoringDirty || identityGuarded;
  const filteredModels = useMemo(() => {
    const needle = modelQuery.trim().toLocaleLowerCase();
    return models.filter((model) => !needle || `${model.model} ${model.display_name} ${model.make_name} ${model.vehicle_class}`.toLocaleLowerCase().includes(needle));
  }, [modelQuery, models]);
  const selectedModel = models.find((model) => model.model === selectedModelId) ?? null;
  const selectedAssetIsModel = Boolean(
    selectedAsset && [".yft", ".ydr", ".ydd"].some(
      (suffix) => selectedAsset.path.toLocaleLowerCase().endsWith(suffix),
    ),
  );
  const filteredAssets = useMemo(() => {
    const needle = assetQuery.trim().toLocaleLowerCase();
    return (selectedModel?.assets ?? []).filter((asset) => !needle || `${asset.role} ${asset.path}`.toLocaleLowerCase().includes(needle));
  }, [assetQuery, selectedModel]);

  useEffect(() => {
    if (!selectedModel || selectedAsset) return;
    const primaryPath = selectedModel.primary_model || selectedModel.high_detail_model;
    const primary = selectedModel.assets.find(
      (asset) => asset.path === primaryPath,
    ) ?? selectedModel.assets.find(
      (asset) => asset.role === "primary_model" || asset.path.toLocaleLowerCase().endsWith(".yft"),
    );
    if (primary) setSelectedAsset(primary);
  }, [selectedAsset, selectedModel]);

  useEffect(() => {
    latestPreviewRevision.current = `vehicle-preview-reset-${Date.now()}`;
    if (previewJob.current) void client.cancelJob(previewJob.current);
    previewJob.current = null;
    setModelQuery("");
    setAssetQuery("");
    setSelectedModelId(requestedModel?.source === activeResult?.source && activeResult?.models.some(model => model.model === requestedModel?.model) ? requestedModel!.model : activeResult?.models[0]?.model ?? "");
    setSelectedAsset(null);
    setPreview(null);
    setViewportResult(null);
    setPreviewError("");
    setPreviewBusy(false);
  }, [client, activeResult?.source, requestedModel]);

  useEffect(() => {
    if (category === "vehicles") onDirtyChange(authoringDirty || authoringBusy);
  }, [authoringBusy, authoringDirty, category, onDirtyChange]);

  const choosePackage = async () => {
    const path = await client.selectPath("vehicle_import_source");
    if (path) onSourceChange(path);
  };
  const chooseFolder = async () => {
    const path = await client.selectPath("vehicle_import_folder");
    if (path) onSourceChange(path);
  };
  const chooseGame = async () => {
    const path = await client.selectPath("gta_folder");
    if (path) onGameChange(path);
  };

  const adoptTuningBuilder = (
    builder: VehicleTuningBuilder,
    availableKits: VehicleAppearance["available_kits"] = authoringSession?.appearance?.available_kits ?? [],
  ) => {
    const collection = builder.collections.includes(tuningCollection)
      ? tuningCollection
      : builder.collections[0] ?? "visibleMods";
    const entry = builder.entries.find((item) => item.collection === collection) ?? null;
    setTuningBuilder(builder);
    setTuningCollection(collection);
    setTuningSelectedIndex(entry?.index ?? null);
    setTuningEntryMode("existing");
    setTuningEntryDraft(entry ? { ...entry.fields } : null);
    setTuningEntryBaseline(entry ? { ...entry.fields } : null);
    const kitSummary = availableKits.find(
      (kit) => kit.name === builder.kit_name,
    );
    const liveryNames = ((builder.livery_names as string[] | undefined) ?? kitSummary?.livery_names ?? []).join(", ");
    setTuningKitType(builder.kit_type);
    setTuningKitTypeBaseline(builder.kit_type);
    setTuningLiveries(liveryNames);
    setTuningLiveriesBaseline(liveryNames);
  };

  const adoptLightProfile = (profiles: VehicleLightProfile[], requestedId?: string) => {
    const profile = profiles.find((item) => item.profile_id === requestedId) ?? profiles[0] ?? null;
    setLightProfileId(profile?.profile_id ?? "");
    setLightProfileDraft(profile ? { ...profile.values } : {});
    setLightProfileBaseline(profile ? { ...profile.values } : {});
  };

  const adoptAxleConfiguration = (session: VehicleAuthoringSession) => {
    const model = session.selected_model?.toLocaleLowerCase();
    const configuration = session.project.axle_configurations.find(
      (item) => item.vehicle_model.toLocaleLowerCase() === model,
    ) ?? null;
    setAxleDraft(configuration ? structuredClone(configuration) : null);
    setAxleBaseline(configuration ? structuredClone(configuration) : null);
    setSelectedAxleOrder(configuration?.axles[0]?.physical_order ?? null);
  };

  const adoptTransmissionConfiguration = (session: VehicleAuthoringSession) => {
    const configuration = session.transmission;
    setTransmissionDraft(configuration ? structuredClone(configuration) : null);
    setTransmissionBaseline(configuration ? structuredClone(configuration) : null);
  };

  const adoptDistribution = (session: VehicleAuthoringSession) => {
    const distribution = session.distribution;
    setDistributionDraft(distribution ? structuredClone(distribution) : null);
    setDistributionBaseline(distribution ? structuredClone(distribution) : null);
  };

  const adoptAuthoringSession = (
    session: VehicleAuthoringSession,
    notice: string,
    mode: "edit" | "appearance" | "axles" | "transmission" | "output" | "identity" = "edit",
  ) => {
    const changedVehicle = authoringSession?.workspace !== session.workspace
      || authoringSession?.selected_model !== session.selected_model;
    const appearance = appearanceDraftFromSession(session.appearance);
    setAuthoringSession(session);
    setAuthoringValues({ ...session.values });
    setAuthoringBaseline({ ...session.values });
    setAppearanceDraft(appearance);
    setAppearanceBaseline(appearanceDraftFromSession(session.appearance));
    setTuningBuilder(null);
    setTuningSelectedIndex(null);
    setTuningEntryMode("existing");
    setTuningEntryDraft(null);
    setTuningEntryBaseline(null);
    setTuningKitType("");
    setTuningKitTypeBaseline("");
    setTuningLiveries("");
    setTuningLiveriesBaseline("");
    adoptLightProfile(session.appearance?.light_profiles ?? [], session.appearance?.light_settings);
    adoptAxleConfiguration(session);
    adoptTransmissionConfiguration(session);
    adoptDistribution(session);
    setPackageReview(null);
    setPackageResult(null);
    if (changedVehicle) {
      setAxleSkeleton(null);
      setPackageDraft(vehiclePackageDraftFromSession(session));
    }
    setSelectedModelId(session.selected_model ?? session.project.models[0]?.model ?? "");
    setAuthoringMode(mode);
    setAuthoringNotice(notice);
    setAuthoringError("");
  };

  const startAuthoringJob = async (
    operation: "inspect_vehicle_authoring_workspace" | "review_vehicle_authoring_workspace" | "review_vehicle_authoring_edit" | "review_vehicle_authoring_appearance" | "inspect_vehicle_authoring_tuning" | "review_vehicle_authoring_tuning" | "review_vehicle_authoring_light_profile" | "review_vehicle_authoring_axles" | "inspect_vehicle_authoring_axle_skeleton" | "review_vehicle_authoring_transmission" | "review_vehicle_authoring_distribution" | "review_vehicle_package_build",
    payload: Record<string, unknown>,
    accept: (loaded: Record<string, unknown>) => void,
  ) => {
    const revision = `vehicle-authoring-${operation}-${Date.now()}`;
    latestAuthoringRevision.current = revision;
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const started = await client.startJob(operation, payload, revision, (message) => {
        if (!message.terminal || message.payload.revision !== latestAuthoringRevision.current || revision !== latestAuthoringRevision.current) return;
        completedAuthoringRevision.current = revision;
        setAuthoringBusy(false);
        onJob(null);
        if (message.operation === "error") {
          setAuthoringError(messageText(message));
          return;
        }
        const loaded = resultFromJob(message);
        if (loaded) accept(loaded);
      });
      if (completedAuthoringRevision.current !== revision && latestAuthoringRevision.current === revision) onJob(started.job_id);
    } catch (reason) {
      if (latestAuthoringRevision.current === revision) {
        setAuthoringBusy(false);
        onJob(null);
        setAuthoringError(String(reason));
      }
    }
  };

  const inspectAuthoringWorkspace = (workspace: string, model?: string) => {
    void startAuthoringJob(
      "inspect_vehicle_authoring_workspace",
      { workspace, ...(model ? { model } : {}) },
      (loaded) => adoptAuthoringSession(loaded as VehicleAuthoringSession, "Editable copy loaded. Game files remain untouched."),
    );
  };

  const openAuthoringWorkspace = async () => {
    if (authoringDirty) {
      setAuthoringNotice("Review or reset the current field changes before opening another workspace.");
      return;
    }
    const workspace = await client.selectPath("vehicle_authoring_workspace");
    if (workspace) inspectAuthoringWorkspace(workspace);
  };

  const closeAuthoringWorkspace = () => {
    if (authoringDirty) {
      setAuthoringNotice("Review or reset the current field changes before closing this editable copy.");
      return;
    }
    setAuthoringSession(null);
    setAuthoringValues({});
    setAuthoringBaseline({});
    setAppearanceDraft(null);
    setAppearanceBaseline(null);
    setAppearanceSection("presets");
    setTuningBuilder(null);
    setTuningSelectedIndex(null);
    setTuningEntryDraft(null);
    setTuningEntryBaseline(null);
    setLightProfileId("");
    setLightProfileDraft({});
    setLightProfileBaseline({});
    setAxleDraft(null);
    setAxleBaseline(null);
    setSelectedAxleOrder(null);
    setTransmissionDraft(null);
    setTransmissionBaseline(null);
    setDistributionDraft(null);
    setDistributionBaseline(null);
    setPackageDraft(emptyVehiclePackageDraft());
    setPackageReview(null);
    setPackageResult(null);
    setAuthoringMode("evidence");
    setAuthoringNotice("Editable copy closed. The source project remains in read-only inspection mode.");
  };

  const reviewAuthoringWorkspace = async () => {
    if (!activeResult) return;
    const parent = await client.selectPath("vehicle_authoring_parent");
    if (!parent) return;
    const leaf = activeResult.source.split(/[\\/]/).filter(Boolean).at(-1)?.replace(/\.[^.]+$/, "") ?? "vehicle-project";
    const safeLeaf = leaf.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "") || "vehicle-project";
    const payload = {
      source: activeResult.source,
      parent,
      name: `${safeLeaf.slice(0, 70)}-authoring`,
      ...(selectedModelId ? { model: selectedModelId } : {}),
    };
    void startAuthoringJob("review_vehicle_authoring_workspace", payload, (loaded) => {
      setPendingCreate({ review: loaded as VehicleAuthoringWorkspaceReview, payload });
    });
  };

  const reviewAuthoringEdit = () => {
    if (!authoringSession?.selected_model) return;
    const updates = Object.fromEntries(Object.entries(authoringValues).filter(
      ([field, value]) => value !== authoringBaseline[field],
    ));
    if (!Object.keys(updates).length) {
      setAuthoringNotice("No field values have changed.");
      return;
    }
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      updates,
    };
    void startAuthoringJob("review_vehicle_authoring_edit", payload, (loaded) => {
      setPendingEdit({ review: loaded as VehicleAuthoringEditReview, payload });
    });
  };

  const reviewAuthoringAppearance = () => {
    if (!authoringSession?.selected_model || !appearanceDraft) return;
    try {
      const appearance = parseAppearanceDraft(appearanceDraft);
      const payload = {
        workspace: authoringSession.workspace,
        model: authoringSession.selected_model,
        expected_revision: authoringSession.revision,
        appearance,
      };
      setAuthoringError("");
      void startAuthoringJob("review_vehicle_authoring_appearance", payload, (loaded) => {
        setPendingAppearance({ review: loaded as VehicleAuthoringAppearanceReview, payload });
      });
    } catch (reason) {
      setAuthoringError(String(reason));
    }
  };

  const loadAuthoringTuning = (kitName?: string) => {
    if (!authoringSession?.selected_model) return;
    if ((tuningEntryDirty || tuningKitDirty) && tuningBuilder?.kit_name !== kitName) {
      setAuthoringNotice("Reset or review the current tuning edits before loading another kit.");
      return;
    }
    const resolvedKit = kitName
      ?? appearanceDraft?.kits[0]
      ?? authoringSession.appearance?.available_kits[0]?.name;
    if (!resolvedKit) {
      setAuthoringNotice("No tuning kit is linked to this vehicle.");
      return;
    }
    void startAuthoringJob("inspect_vehicle_authoring_tuning", {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      kit_name: resolvedKit,
    }, (loaded) => {
      adoptTuningBuilder(loaded as VehicleTuningBuilder);
      setAuthoringNotice(`Loaded ${resolvedKit} from the copied workspace.`);
    });
  };

  const selectAppearanceSection = (section: AppearanceEditorSection) => {
    setAppearanceSection(section);
    setAuthoringNotice("");
    if (section === "tuning" && !tuningBuilder) loadAuthoringTuning();
    if (section === "lights" && !lightProfileId) {
      adoptLightProfile(authoringSession?.appearance?.light_profiles ?? [], appearanceDraft?.light_settings);
    }
  };

  const selectTuningCollection = (collection: VehicleTuningCollection) => {
    if (!tuningBuilder) return;
    if (tuningEntryDirty) {
      setAuthoringNotice("Reset or review the current entry before changing collections.");
      return;
    }
    const entry = tuningBuilder.entries.find((item) => item.collection === collection) ?? null;
    setTuningCollection(collection);
    setTuningSelectedIndex(entry?.index ?? null);
    setTuningEntryMode("existing");
    setTuningEntryDraft(entry ? { ...entry.fields } : null);
    setTuningEntryBaseline(entry ? { ...entry.fields } : null);
  };

  const selectTuningEntry = (index: number) => {
    if (!tuningBuilder) return;
    if (tuningEntryDirty) {
      setAuthoringNotice("Reset or review the current entry before selecting another one.");
      return;
    }
    const entry = tuningBuilder.entries.find(
      (item) => item.collection === tuningCollection && item.index === index,
    );
    if (!entry) return;
    setTuningSelectedIndex(index);
    setTuningEntryMode("existing");
    setTuningEntryDraft({ ...entry.fields });
    setTuningEntryBaseline({ ...entry.fields });
  };

  const startNewTuningEntry = () => {
    if (!tuningBuilder) return;
    if (tuningEntryDirty) {
      setAuthoringNotice("Reset or review the current entry before starting another one.");
      return;
    }
    const defaults = tuningDraftDefaults(tuningBuilder, tuningCollection);
    setTuningSelectedIndex(null);
    setTuningEntryMode("new");
    setTuningEntryDraft(defaults);
    setTuningEntryBaseline(null);
  };

  const startDuplicateTuningEntry = () => {
    if (!tuningBuilder || tuningSelectedIndex === null) return;
    const entry = tuningBuilder.entries.find(
      (item) => item.collection === tuningCollection && item.index === tuningSelectedIndex,
    );
    if (!entry) return;
    setTuningEntryMode("duplicate");
    setTuningEntryDraft({ ...entry.fields });
    setTuningEntryBaseline(null);
    setAuthoringNotice("Change the identifying fields before reviewing this duplicate.");
  };

  const reviewTuningMutation = (mutation: Record<string, unknown>) => {
    if (!authoringSession?.selected_model) return;
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      mutation,
    };
    void startAuthoringJob("review_vehicle_authoring_tuning", payload, (loaded) => {
      setPendingTuning({ review: loaded as VehicleAuthoringTuningReview, payload });
    });
  };

  const reviewTuningKit = () => {
    if (!tuningBuilder) return;
    reviewTuningMutation({
      action: "update_kit",
      kit_name: tuningBuilder.kit_name,
      kit_type: tuningKitType.trim(),
      livery_names: tuningLiveries.split(",").map((value) => value.trim()).filter(Boolean),
    });
  };

  const reviewTuningEntry = () => {
    if (!tuningBuilder || !tuningEntryDraft) return;
    const mutation: Record<string, unknown> = {
      action: tuningEntryMode === "existing" ? "update_entry" : tuningEntryMode === "duplicate" ? "duplicate_entry" : "add_entry",
      kit_name: tuningBuilder.kit_name,
      collection: tuningCollection,
      values: tuningEntryDraft,
    };
    if (tuningEntryMode !== "new") mutation.index = tuningSelectedIndex;
    reviewTuningMutation(mutation);
  };

  const resetTuningEdits = () => {
    if (!tuningBuilder) return;
    const entry = tuningBuilder.entries.find(
      (item) => item.collection === tuningCollection && item.index === tuningSelectedIndex,
    ) ?? tuningBuilder.entries.find((item) => item.collection === tuningCollection) ?? null;
    setTuningSelectedIndex(entry?.index ?? null);
    setTuningEntryMode("existing");
    setTuningEntryDraft(entry ? { ...entry.fields } : null);
    setTuningEntryBaseline(entry ? { ...entry.fields } : null);
    setTuningKitType(tuningKitTypeBaseline);
    setTuningLiveries(tuningLiveriesBaseline);
    setAuthoringNotice("Tuning edits reset to the inspected revision.");
  };

  const selectLightProfile = (profileId: string) => {
    if (lightProfileDirty) {
      setAuthoringNotice("Reset or review the current light values before selecting another profile.");
      return;
    }
    adoptLightProfile(authoringSession?.appearance?.light_profiles ?? [], profileId);
  };

  const reviewLightProfile = () => {
    if (!authoringSession?.selected_model || !lightProfileId) return;
    const updates = Object.fromEntries(Object.entries(lightProfileDraft).filter(
      ([field, value]) => value !== lightProfileBaseline[field],
    ));
    if (!Object.keys(updates).length) return;
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      profile_id: lightProfileId,
      updates,
    };
    void startAuthoringJob("review_vehicle_authoring_light_profile", payload, (loaded) => {
      setPendingLightProfile({ review: loaded as VehicleAuthoringLightProfileReview, payload });
    });
  };

  const reviewAxleConfiguration = () => {
    if (!authoringSession?.selected_model || !axleDraft || !axleDirty) return;
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      configuration: axleDraft,
      ...(axleSkeleton ? { skeleton_xml: axleSkeleton.skeleton_xml } : {}),
    };
    void startAuthoringJob("review_vehicle_authoring_axles", payload, (loaded) => {
      setPendingAxles({ review: loaded as VehicleAuthoringAxleReview, payload });
    });
  };

  const runAxleSkeletonAction = (
    action: VehicleAuthoringAxleSkeleton["action"],
    skeletonXml: string,
    extra: Record<string, unknown> = {},
  ) => {
    if (!authoringSession?.selected_model) return;
    const payload: Record<string, unknown> = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      skeleton_xml: skeletonXml,
      action,
      ...extra,
    };
    if (action !== "detect" && axleDraft) payload.configuration = axleDraft;
    void startAuthoringJob("inspect_vehicle_authoring_axle_skeleton", payload, (loaded) => {
      const evidence = loaded as VehicleAuthoringAxleSkeleton;
      setAxleSkeleton(evidence);
      setAxleDraft(structuredClone(evidence.configuration));
      setSelectedAxleOrder(evidence.configuration.axles[0]?.physical_order ?? null);
      setAuthoringNotice(action === "detect"
        ? `Detected ${evidence.configuration.axles.length} physical axles from the selected skeleton.`
        : action === "steering"
          ? "Signed steering gains calculated from wheel-bone positions. Review the axle revision before saving."
          : action === "physical_order"
            ? "Custom physical order signed against the selected skeleton."
            : action === "canonical_order"
              ? "Canonical wheel-bone order restored."
              : "Skeleton evidence verified for the current axle configuration.");
    });
  };

  const chooseAxleSkeleton = async () => {
    const path = await client.selectPath("vehicle_skeleton");
    if (!path) return;
    runAxleSkeletonAction(axleDraft ? "validate" : "detect", path, axleDraft ? {} : {
      export_mode: "stock_metadata",
      target: "fivem-legacy",
    });
  };

  const moveAxleOrder = (order: number, direction: -1 | 1) => {
    if (!axleDraft || !axleSkeleton) return;
    const ordered = [...axleDraft.axles].sort((left, right) => left.physical_order - right.physical_order);
    const index = ordered.findIndex((axle) => axle.physical_order === order);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= ordered.length) return;
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    runAxleSkeletonAction("physical_order", axleSkeleton.skeleton_xml, {
      physical_bone_pairs: ordered.map((axle) => [axle.left_bone, axle.right_bone]),
    });
  };

  const createTransmissionProfile = () => {
    if (!authoringSession?.selected_model) return;
    const parsed = Number(authoringSession.values["handling.nInitialDriveGears"] ?? 6);
    const count = Number.isInteger(parsed) ? Math.max(1, Math.min(16, parsed)) : 6;
    const ratios = Array.from({ length: count }, (_, index) => {
      if (count === 1) return 1;
      return Number((3.5 * Math.pow(0.75 / 3.5, index / (count - 1))).toFixed(3));
    });
    setTransmissionDraft({
      schema_version: 1,
      vehicle_model: authoringSession.selected_model.toLocaleLowerCase(),
      transmission_type: "automatic",
      gear_ratios: ratios,
      reverse_gear_ratio: 3.2,
      final_drive_ratio: 3.42,
    });
    setAuthoringNotice("Created an evenly spaced starting ratio curve. Tune every ratio before review.");
  };

  const reviewTransmission = () => {
    if (!authoringSession?.selected_model || !transmissionDraft || !transmissionDirty) return;
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      configuration: transmissionDraft,
    };
    void startAuthoringJob("review_vehicle_authoring_transmission", payload, (loaded) => {
      setPendingTransmission({
        review: loaded as VehicleAuthoringTransmissionReview,
        payload,
      });
    });
  };

  const reviewDistribution = () => {
    if (!authoringSession?.selected_model || !distributionDraft || !distributionBaseline || !distributionDirty) return;
    const updates = Object.fromEntries(Object.entries(distributionDraft).filter(
      ([field, value]) => field !== "model" && value !== distributionBaseline[field],
    ));
    const payload = {
      workspace: authoringSession.workspace,
      model: authoringSession.selected_model,
      expected_revision: authoringSession.revision,
      updates,
    };
    void startAuthoringJob("review_vehicle_authoring_distribution", payload, (loaded) => {
      setPendingDistribution({
        review: loaded as VehicleAuthoringDistributionReview,
        payload,
      });
    });
  };

  const choosePackageDestination = async () => {
    if (!authoringSession?.selected_model) return;
    const parent = await client.selectPath("vehicle_package_parent");
    if (!parent) return;
    const sourceLeaf = packageDraft.pack_name || authoringSession.selected_model;
    const safeLeaf = sourceLeaf.toLocaleLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[^a-z0-9]+/, "") || "vehicle";
    const separator = parent.includes("\\") ? "\\" : "/";
    setPackageDraft((draft) => ({
      ...draft,
      destination: `${parent.replace(/[\\/]+$/, "")}${separator}${safeLeaf.slice(0, 64)}-package`,
    }));
    setPackageReview(null);
    setPackageResult(null);
    setAuthoringNotice("Output folder selected. Review will confirm it is new and outside GTA V.");
  };

  const reviewPackageBuild = () => {
    if (!authoringSession || authoringDirty || !packageDraft.destination) return;
    const editions = [
      ...(packageDraft.legacy ? ["legacy"] : []),
      ...(packageDraft.enhanced ? ["enhanced"] : []),
    ];
    const payload = {
      workspace: authoringSession.workspace,
      expected_revision: authoringSession.revision,
      destination: packageDraft.destination,
      pack_name: packageDraft.pack_name,
      mod_id: packageDraft.mod_id,
      name: packageDraft.name,
      version: packageDraft.version,
      editions,
      ...(gtaPath ? { gta_path: gtaPath } : {}),
    };
    setPackageResult(null);
    void startAuthoringJob("review_vehicle_package_build", payload, (loaded) => {
      const review = loaded as VehiclePackageBuildReview;
      setPackageReview(review);
      setPendingPackageBuild({ review, payload });
    });
  };

  const confirmCreate = async () => {
    if (!pendingCreate) return;
    const pending = pendingCreate;
    setPendingCreate(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("create_vehicle_authoring_workspace", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle authoring did not return a workspace session.");
      adoptAuthoringSession(loaded, "Editable copy created. Revision 0 is ready for authoring.");
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmEdit = async () => {
    if (!pendingEdit) return;
    const pending = pendingEdit;
    setPendingEdit(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_edit", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} reviewed field${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`);
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmAppearance = async () => {
    if (!pendingAppearance) return;
    const pending = pendingAppearance;
    setPendingAppearance(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_appearance", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle appearance authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} appearance change${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "appearance");
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmTuning = async () => {
    if (!pendingTuning) return;
    const pending = pendingTuning;
    setPendingTuning(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_tuning", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle tuning authoring did not return the updated session.");
      const builder = loaded.tuning_builder;
      adoptAuthoringSession(loaded, `${pending.review.changes.length} tuning change${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "appearance");
      setAppearanceSection("tuning");
      if (builder) adoptTuningBuilder(builder, loaded.appearance?.available_kits ?? []);
      else loadAuthoringTuning(String(pending.review.mutation.kit_name ?? ""));
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmLightProfile = async () => {
    if (!pendingLightProfile) return;
    const pending = pendingLightProfile;
    setPendingLightProfile(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_light_profile", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle light-profile authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} light value${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "appearance");
      setAppearanceSection("lights");
      adoptLightProfile(loaded.appearance?.light_profiles ?? [], pending.review.profile_id);
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmAxles = async () => {
    if (!pendingAxles) return;
    const pending = pendingAxles;
    const selectedOrder = selectedAxleOrder;
    setPendingAxles(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_axles", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle axle authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} axle change${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "axles");
      if (selectedOrder !== null) setSelectedAxleOrder(selectedOrder);
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmTransmission = async () => {
    if (!pendingTransmission) return;
    const pending = pendingTransmission;
    setPendingTransmission(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_transmission", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle transmission authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} transmission change${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "transmission");
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmDistribution = async () => {
    if (!pendingDistribution) return;
    const pending = pendingDistribution;
    setPendingDistribution(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_distribution", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error("Vehicle distribution authoring did not return the updated session.");
      adoptAuthoringSession(loaded, `${pending.review.changes.length} distribution change${pending.review.changes.length === 1 ? "" : "s"} saved as revision ${loaded.revision}.`, "output");
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmPackageBuild = async () => {
    if (!pendingPackageBuild) return;
    const pending = pendingPackageBuild;
    setPendingPackageBuild(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_package_build", {
        ...pending.payload,
        review_sha256: pending.review.review_sha256,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehiclePackageBuildResult | undefined;
      if (!loaded) throw new Error("Vehicle package build did not return an output receipt.");
      setPackageResult(loaded);
      setAuthoringMode("output");
      setAuthoringNotice(`Validated package built at ${loaded.package.root}. GTA V was not modified.`);
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const confirmHistory = async () => {
    if (!pendingHistory || !authoringSession) return;
    const direction = pendingHistory;
    setPendingHistory(null);
    setAuthoringBusy(true);
    setAuthoringError("");
    try {
      const response = await client.vehicleAuthoringAction("apply_vehicle_authoring_history", {
        workspace: authoringSession.workspace,
        model: authoringSession.selected_model,
        expected_revision: authoringSession.revision,
        direction,
        authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = response.payload.result as VehicleAuthoringSession | undefined;
      if (!loaded) throw new Error(`Vehicle authoring ${direction} did not return a session.`);
      adoptAuthoringSession(loaded, `${direction === "undo" ? "Undo" : "Redo"} completed as revision ${loaded.revision}.`, authoringMode === "appearance" ? "appearance" : authoringMode === "axles" ? "axles" : authoringMode === "transmission" ? "transmission" : authoringMode === "output" ? "output" : "edit");
    } catch (reason) {
      setAuthoringError(String(reason));
    } finally {
      setAuthoringBusy(false);
    }
  };

  const selectModel = (model: string) => {
    if (authoringDirty) {
      setAuthoringNotice("Review or reset the current field changes before selecting another vehicle.");
      return;
    }
    if (authoringSession && model !== authoringSession.selected_model) {
      inspectAuthoringWorkspace(authoringSession.workspace, model);
      return;
    }
    latestPreviewRevision.current = `vehicle-preview-reset-${Date.now()}`;
    if (previewJob.current) void client.cancelJob(previewJob.current);
    previewJob.current = null;
    setSelectedModelId(model);
    setAssetQuery("");
    const nextModel = models.find((item) => item.model === model);
    const primaryPath = nextModel?.primary_model || nextModel?.high_detail_model;
    setSelectedAsset(nextModel?.assets.find((asset) => asset.path === primaryPath)
      ?? nextModel?.assets.find((asset) => asset.role === "primary_model" || asset.path.toLocaleLowerCase().endsWith(".yft"))
      ?? null);
    setPreview(null);
    setViewportResult(null);
    setPreviewError("");
    setPreviewBusy(false);
    onJob(null);
  };

  const loadPreview = async (asset: VehicleProjectAsset) => {
    setSelectedAsset(asset);
    setPreview(null);
    setViewportResult(null);
    setPreviewError("");
    if (!activeResult) return;
    if ([".yft", ".ydr", ".ydd"].some((suffix) => asset.path.toLocaleLowerCase().endsWith(suffix))) {
      latestPreviewRevision.current = `vehicle-viewport-${Date.now()}-${asset.path}`;
      if (previewJob.current) {
        try {
          await client.cancelJob(previewJob.current);
        } catch {
          // The static preview may already have completed.
        }
      }
      previewJob.current = null;
      setPreviewBusy(false);
      onJob(null);
      return;
    }
    const revision = `vehicle-preview-${Date.now()}-${asset.path}`;
    latestPreviewRevision.current = revision;
    setPreviewBusy(true);
    if (previewJob.current) {
      try {
        await client.cancelJob(previewJob.current);
      } catch {
        // A terminal preview can win cancellation; the revision remains authoritative.
      }
      previewJob.current = null;
      if (latestPreviewRevision.current !== revision) return;
      onJob(null);
    }
    const normalizedEdition = ["legacy", "enhanced"].includes(activeResult.edition.toLocaleLowerCase())
      ? activeResult.edition
      : "Enhanced";
    try {
      const started = await client.startJob(
        "preview_asset",
        {
          source: activeResult.source,
          entry: asset.path,
          edition: normalizedEdition,
          ...(activeResult.gta_path ? { gta_path: activeResult.gta_path } : {}),
        },
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestPreviewRevision.current || revision !== latestPreviewRevision.current) return;
          completedPreviewRevision.current = revision;
          previewJob.current = null;
          setPreviewBusy(false);
          onJob(null);
          if (message.operation === "error") {
            setPreviewError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded) setPreview(loaded as AssetPreviewResult);
        },
      );
      if (completedPreviewRevision.current !== revision && latestPreviewRevision.current === revision) {
        previewJob.current = started.job_id;
        onJob(started.job_id);
      }
    } catch (reason) {
      if (latestPreviewRevision.current === revision) {
        setPreviewError(String(reason));
        setPreviewBusy(false);
        previewJob.current = null;
        onJob(null);
      }
    }
  };

  const previewArtifactSource = preview?.artifact
    ? preview.artifact.preview_url ?? convertFileSrc(preview.artifact.path)
    : null;
  const sourceState = (busy || authoringBusy) && !activeResult
    ? "Resolving vehicle project…"
    : previewBusy
      ? "Reading linked asset…"
      : authoringSession
        ? `Editable copy · revision ${authoringSession.revision}`
        : activeResult
          ? "Vehicle project ready"
        : source
          ? "Source ready for inspection"
          : "Choose a vehicle package to begin";

  return (
    <section className="workspace-section vehicle-workbench" aria-labelledby="workbench-title">
      <div className="workspace-heading">
        <div><span className="eyebrow">Content authoring</span><h2 id="workbench-title">Content Workbench</h2><p>Inspect package-owned metadata and relationships before creating an editable copy.</p></div>
        {category === "vehicles" && <div className="heading-actions">
          {!authoringSession ? <>
            <button type="button" className="primary-button" onClick={choosePackage} disabled={busy}>Open package</button>
            <button type="button" className="quiet-button" onClick={chooseFolder} disabled={busy}>Open folder</button>
            <button type="button" className="quiet-button" onClick={chooseGame} disabled={busy}>Choose GTA V</button>
            {activeResult && <button type="button" className="quiet-button" onClick={() => void reviewAuthoringWorkspace()} disabled={busy || authoringBusy}>Create editable copy</button>}
            <button type="button" className="quiet-button" onClick={() => void openAuthoringWorkspace()} disabled={busy || authoringBusy}>Open editable copy</button>
            {source && <button type="button" className="quiet-button" onClick={() => onInspect(source, gtaPath)} disabled={busy}>Refresh</button>}
          </> : <>
            <button type="button" className="quiet-button" onClick={chooseGame} disabled={busy || authoringBusy}>Choose GTA V</button>
            <button type="button" className="quiet-button" onClick={() => void openAuthoringWorkspace()} disabled={busy || authoringBusy || authoringDirty}>Open another copy</button>
            <button type="button" className="quiet-button" onClick={closeAuthoringWorkspace} disabled={busy || authoringBusy || authoringDirty}>Close editable copy</button>
          </>}
          {activeJob && <button type="button" className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>}
      </div>

      <div className="workbench-category-bar" role="tablist" aria-label="Content type">
        {categories.map((item) => <button key={item.id} type="button" role="tab" aria-selected={category === item.id} className={category === item.id ? "active" : ""} onClick={() => onCategoryChange(item.id)}><span>{item.label}</span><small>{item.status}</small></button>)}
      </div>

      {(category === "weapons" || category === "peds" || category === "maps" || category === "runtime" || category === "render") && navigationNotice && <div className="error-banner" role="alert">{navigationNotice}</div>}
      {category === "peds" ? <PedWorkbench client={client} onDirtyChange={onDirtyChange} initialSource={source || ""} onHelp={onHelp} /> : category === "weapons" ? <WeaponWorkbench client={client} onDirtyChange={onDirtyChange} initialSource={source || ""} /> : category === "maps" ? <MapWorkbench client={client} onDirtyChange={onDirtyChange} /> : category === "runtime" ? <RuntimeWorkbench client={client} onDirtyChange={onDirtyChange} /> : category === "render" ? <RenderWorkbench client={client} onDirtyChange={onDirtyChange} /> : category !== "vehicles" ? <div className="workbench-fallback-card">
        <span className="eyebrow">Migration boundary</span>
        <h3>{categories.find((item) => item.id === category)?.label} Workbench</h3>
        <p>This authoring workspace remains available in the Tkinter fallback while its mutation and rollback session is separated from widget code.</p>
        <div className="recipe-safety-note"><strong>No imitation editor</strong><span>The React shell will expose this only after it can call the same Python validation, history, and package services.</span></div>
      </div> : <>
        <div className="source-strip" aria-live="polite"><span className={`activity-dot ${busy || previewBusy || authoringBusy ? "busy" : activeResult ? "ready" : ""}`} /><strong>{sourceState}</strong><span className="source-path" title={authoringSession?.workspace || source || "No source selected"}>{authoringSession?.workspace || source || "No source selected"}</span></div>
        {gtaPath && <div className="workbench-context-strip"><strong>Decoder context</strong><span title={gtaPath}>{gtaPath}</span></div>}
        {error && <div className="error-banner" role="alert">{error}</div>}
        {previewError && <div className="error-banner" role="alert">{previewError}</div>}
        {authoringError && <div className="error-banner" role="alert">{authoringError}</div>}
        {navigationNotice && <div className="error-banner" role="alert">{navigationNotice}</div>}
        {authoringNotice && <div className="action-notice" role="status">{authoringNotice}</div>}
        {activeResult && <div className="summary-row vehicle-project-summary">
          <span><strong>{activeResult.model_count}</strong> vehicles</span><span><strong>{activeResult.asset_count}</strong> linked assets</span><span><strong>{activeResult.previewable_count}</strong> previewable</span><span><strong>{activeResult.error_count}</strong> errors</span><span><strong>{activeResult.warning_count}</strong> warnings</span><span><strong>{formatReadiness(activeResult.edition)}</strong> edition</span>{activeResult.truncated && <StatusPill tone="warning">bounded result</StatusPill>}
        </div>}

        {authoringSession && <div className="vehicle-authoring-toolbar">
          <div><span className={`activity-dot ${authoringDirty ? "busy" : "ready"}`} /><strong>{authoringSession.selected_model}</strong><span>Revision {authoringSession.revision}{authoringDirty ? " · unsaved changes" : " · workspace clean"}</span></div>
          <div><button type="button" className="quiet-button compact" onClick={() => setPendingHistory("undo")} disabled={authoringBusy || authoringDirty || !authoringSession.can_undo}>Undo</button><button type="button" className="quiet-button compact" onClick={() => setPendingHistory("redo")} disabled={authoringBusy || authoringDirty || !authoringSession.can_redo}>Redo</button></div>
        </div>}

        <div className={`panel-grid vehicle-project-grid ${activeResult ? "has-result" : "is-empty"} ${authoringSession ? "is-authoring" : ""}`}>
          <section className="pane vehicle-models-pane" aria-label="Resolved vehicles">
            <div className="pane-header"><div><span className="pane-kicker">Project models</span><h3>Vehicles</h3></div><span className="pane-count">{filteredModels.length}/{models.length}</span></div>
            <div className="asset-filter-bar vehicle-filter-bar"><label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Filter vehicles</span><input value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} placeholder="Filter model, make, or class" disabled={!models.length} /></label></div>
            <div className="row-list">
              {!activeResult && <div className="pane-empty"><strong>No vehicle project loaded</strong><p>Open a DLC RPF, package archive, or extracted folder to resolve its vehicle definitions.</p><button type="button" className="text-action" onClick={choosePackage}>Choose package</button></div>}
              {activeResult && !filteredModels.length && <p className="empty-copy">No vehicle model matches this filter.</p>}
              {filteredModels.map((model) => <button type="button" key={model.model} className={`data-row vehicle-model-row ${selectedModelId === model.model ? "selected" : ""}`} onClick={() => selectModel(model.model)} aria-pressed={selectedModelId === model.model}><span className="row-type">V</span><span><strong>{model.display_name || model.model}</strong><small>{model.make_name || "Unknown make"} · {model.model}</small></span><span className={`row-state ${model.complete ? "valid" : "invalid"}`}>{model.complete ? "Ready" : "Review"}</span></button>)}
            </div>
          </section>

          <section className="pane vehicle-assets-pane" aria-label="Linked vehicle assets">
            <div className="pane-header"><div><span className="pane-kicker">Package ownership</span><h3>Linked assets</h3></div><span className="pane-count">{filteredAssets.length}/{selectedModel?.assets.length ?? 0}</span></div>
            <div className="asset-filter-bar vehicle-filter-bar"><label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Filter linked assets</span><input value={assetQuery} onChange={(event) => setAssetQuery(event.target.value)} placeholder="Filter role or package path" disabled={!selectedModel?.assets.length} /></label></div>
            <div className="row-list">
              {!selectedModel && <div className="pane-empty"><strong>No vehicle selected</strong><p>Choose a resolved model to inspect its metadata, fragments, textures, and registrations.</p></div>}
              {selectedModel && !filteredAssets.length && <p className="empty-copy">No linked asset matches this filter.</p>}
              {filteredAssets.map((asset) => <button type="button" key={`${asset.role}-${asset.path}`} className={`data-row vehicle-asset-row ${selectedAsset?.path === asset.path ? "selected" : ""}`} onClick={() => void loadPreview(asset)} aria-pressed={selectedAsset?.path === asset.path}><span className="row-type">{asset.path.split(".").at(-1)?.slice(0, 3).toLocaleUpperCase() || "FILE"}</span><span><strong title={asset.path}>{asset.path}</strong><small>{formatReadiness(asset.role)} · {formatBytes(asset.size)}</small></span><span className="row-state">{asset.previewable ? "Native" : "Data"}</span></button>)}
            </div>
          </section>

          <aside className="pane vehicle-evidence-pane" aria-label="Vehicle evidence">
            <div className="pane-header"><div><span className="pane-kicker">{authoringMode === "edit" && authoringSession ? "Authoring fields" : authoringMode === "appearance" && authoringSession ? "Vehicle variation" : authoringMode === "axles" && authoringSession ? "Axle topology" : authoringMode === "transmission" && authoringSession ? "Transmission profile" : authoringMode === "output" && authoringSession ? "Distribution and output" : "Inspector"}</span><h3>{authoringMode !== "evidence" && authoringSession ? authoringSession.selected_model : selectedAsset?.path.split("/").at(-1) || selectedModel?.model || "Vehicle evidence"}</h3></div>{selectedModel && <StatusPill valid={selectedModel.complete}>{authoringSession ? `rev ${authoringSession.revision}` : selectedModel.complete ? "linked" : "review"}</StatusPill>}</div>
            {authoringSession && <div className="vehicle-inspector-tabs" role="tablist" aria-label="Vehicle inspector mode"><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "evidence"} className={authoringMode === "evidence" ? "active" : ""} onClick={() => setAuthoringMode("evidence")}>Evidence</button><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "edit"} className={authoringMode === "edit" ? "active" : ""} onClick={() => setAuthoringMode("edit")}>Core fields</button><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "appearance"} className={authoringMode === "appearance" ? "active" : ""} onClick={() => setAuthoringMode("appearance")}>Appearance</button><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "axles"} className={authoringMode === "axles" ? "active" : ""} onClick={() => setAuthoringMode("axles")}>Axles</button><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "transmission"} className={authoringMode === "transmission" ? "active" : ""} onClick={() => setAuthoringMode("transmission")}>Transmission</button><button type="button" disabled={identityGuarded} role="tab" aria-selected={authoringMode === "output"} className={authoringMode === "output" ? "active" : ""} onClick={() => setAuthoringMode("output")}>Output</button><button type="button" role="tab" aria-selected={authoringMode === "identity"} disabled={otherAuthoringDirty || authoringBusy} onClick={() => setAuthoringMode("identity")}>Identity</button></div>}
            {authoringSession && <div hidden={authoringMode !== "identity"} className="vehicle-authoring-editor"><VehicleIdentityEditor key={`${authoringSession.workspace}:${authoringSession.selected_model}:${authoringSession.revision}`} client={client} session={authoringSession} disabled={authoringBusy || otherAuthoringDirty || !!pendingCreate || !!pendingEdit} onGuardChange={setIdentityGuarded} onSaved={value => adoptAuthoringSession(value, "Vehicle identity and linked assets migrated. Undo is available in workspace history.", "identity")} /></div>}
            {authoringMode === "identity" && authoringSession ? null : authoringMode === "edit" && authoringSession ? <div className="vehicle-authoring-editor" aria-live="polite">
              <div className="vehicle-authoring-intro"><strong>Copied workspace</strong><span>Values are validated by the Python authoring service before anything is saved.</span></div>
              {VEHICLE_AUTHORING_FIELDS.map((group) => <fieldset key={group.title}><legend>{group.title}</legend>{group.fields.map(([field, label]) => {
                const range = handlingSlider(field);
                const update = (value: string) => { setAuthoringValues(values => ({ ...values, [field]: value })); setAuthoringNotice(""); };
                return range ? <SliderField key={field} id={`vehicle-authoring-${field}`} label={label} hint={field} {...range}
                  value={authoringValues[field] ?? ""} resetValue={authoringSession.values[field]} onChange={update}
                  disabled={authoringBusy || !authoringSession.editable_fields.includes(field)} />
                  : <label key={field} htmlFor={`vehicle-authoring-${field}`}><span>{label}<small>{field}</small></span><input id={`vehicle-authoring-${field}`} value={authoringValues[field] ?? ""} onChange={event => update(event.target.value)} disabled={authoringBusy || !authoringSession.editable_fields.includes(field)} /></label>;
              })}</fieldset>)}
              <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={() => { setAuthoringValues({ ...authoringBaseline }); setAuthoringNotice("Field changes reset to the current saved revision."); }} disabled={authoringBusy || !fieldAuthoringDirty}>Reset fields</button><button type="button" className="primary-button" onClick={reviewAuthoringEdit} disabled={authoringBusy || !fieldAuthoringDirty}>Review changes</button></div>
            </div> : authoringMode === "appearance" && authoringSession && appearanceDraft ? <div className="vehicle-authoring-editor vehicle-appearance-editor" aria-live="polite">
              <div className="appearance-editor-tabs" role="tablist" aria-label="Appearance editor section"><button type="button" role="tab" aria-selected={appearanceSection === "presets"} className={appearanceSection === "presets" ? "active" : ""} onClick={() => selectAppearanceSection("presets")}>Presets</button><button type="button" role="tab" aria-selected={appearanceSection === "tuning"} className={appearanceSection === "tuning" ? "active" : ""} onClick={() => selectAppearanceSection("tuning")}>Tuning kit</button><button type="button" role="tab" aria-selected={appearanceSection === "lights"} className={appearanceSection === "lights" ? "active" : ""} onClick={() => selectAppearanceSection("lights")}>Light profile</button></div>
              {appearanceSection === "presets" && <>
                <div className="vehicle-authoring-intro"><strong>Structured appearance</strong><span>Variation presets and linked resources are reviewed together as one revision.</span></div>
                <fieldset className="appearance-settings"><legend>Lighting references</legend><div className="appearance-setting-grid"><label htmlFor="vehicle-light-settings"><span>Light profile<small>variation.lightSettings</small></span><select id="vehicle-light-settings" value={appearanceDraft.light_settings} onChange={(event) => { setAppearanceDraft((draft) => draft ? { ...draft, light_settings: event.target.value } : draft); setAuthoringNotice(""); }} disabled={authoringBusy}>{authoringSession.appearance?.light_profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id} · {profile.name || "Unnamed profile"}</option>)}{!authoringSession.appearance?.light_profiles.some((profile) => profile.profile_id === appearanceDraft.light_settings) && <option value={appearanceDraft.light_settings}>{appearanceDraft.light_settings} · Unresolved reference</option>}</select></label><label htmlFor="vehicle-siren-settings"><span>Siren settings<small>variation.sirenSettings</small></span><input id="vehicle-siren-settings" inputMode="numeric" value={appearanceDraft.siren_settings} onChange={(event) => { setAppearanceDraft((draft) => draft ? { ...draft, siren_settings: event.target.value } : draft); setAuthoringNotice(""); }} disabled={authoringBusy} /></label></div></fieldset>
                <fieldset className="appearance-kits"><legend>Linked tuning kits</legend><div className="appearance-kit-list">{authoringSession.appearance?.available_kits.map((kit) => { const selected = appearanceDraft.kits.includes(kit.name); return <label key={kit.name} className={selected ? "selected" : ""}><input type="checkbox" checked={selected} onChange={(event) => { setAppearanceDraft((draft) => draft ? { ...draft, kits: event.target.checked ? [...draft.kits, kit.name] : draft.kits.filter((name) => name !== kit.name) } : draft); setAuthoringNotice(""); }} disabled={authoringBusy} /><span><strong>{kit.name}</strong><small>{kit.kit_type} · {kit.visible_mods} visible · {kit.stat_mods} stat{kit.livery_names.length ? ` · ${kit.livery_names.length} liveries` : ""}</small></span></label>; })}{!authoringSession.appearance?.available_kits.length && <p className="empty-copy">No tuning kits were resolved in this workspace.</p>}</div></fieldset>
                <fieldset className="appearance-colors"><legend>Color presets</legend><div className="appearance-color-list">{appearanceDraft.colors.map((color, index) => <div className="appearance-color-row" key={index}><div className="appearance-color-heading"><strong>Preset {index + 1}</strong><button type="button" className="text-action" onClick={() => setAppearanceDraft((draft) => draft ? { ...draft, colors: draft.colors.filter((_, colorIndex) => colorIndex !== index) } : draft)} disabled={authoringBusy}>Remove</button></div><label htmlFor={`vehicle-color-indices-${index}`}><span>Paint indices<small>4–8 values, 0–255</small></span><input id={`vehicle-color-indices-${index}`} value={color.indices} onChange={(event) => setAppearanceDraft((draft) => draft ? { ...draft, colors: draft.colors.map((item, colorIndex) => colorIndex === index ? { ...item, indices: event.target.value } : item) } : draft)} disabled={authoringBusy} /></label><label htmlFor={`vehicle-color-liveries-${index}`}><span>Livery flags<small>0 or 1, in slot order</small></span><input id={`vehicle-color-liveries-${index}`} value={color.liveries} onChange={(event) => setAppearanceDraft((draft) => draft ? { ...draft, colors: draft.colors.map((item, colorIndex) => colorIndex === index ? { ...item, liveries: event.target.value } : item) } : draft)} disabled={authoringBusy} /></label></div>)}</div><button type="button" className="quiet-button compact appearance-add-color" onClick={() => setAppearanceDraft((draft) => draft ? { ...draft, colors: [...draft.colors, { indices: "0, 0, 0, 0", liveries: "" }] } : draft)} disabled={authoringBusy || appearanceDraft.colors.length >= 64}>Add color preset</button></fieldset>
                <div className="vehicle-authoring-actions"><button type="button" className="quiet-button" onClick={() => { if (appearanceBaseline) setAppearanceDraft(structuredClone(appearanceBaseline)); setAuthoringNotice("Appearance changes reset to the current saved revision."); }} disabled={authoringBusy || !appearanceDirty}>Reset appearance</button><button type="button" className="primary-button" onClick={reviewAuthoringAppearance} disabled={authoringBusy || !appearanceDirty}>Review appearance</button></div>
              </>}
              {appearanceSection === "tuning" && <VehicleTuningEditor builder={tuningBuilder} kits={authoringSession.appearance?.available_kits ?? []} collection={tuningCollection} selectedIndex={tuningSelectedIndex} entryMode={tuningEntryMode} entryDraft={tuningEntryDraft} kitType={tuningKitType} liveryNames={tuningLiveries} entryDirty={tuningEntryDirty} kitDirty={tuningKitDirty} busy={authoringBusy} onLoadKit={loadAuthoringTuning} onCollection={selectTuningCollection} onSelectEntry={selectTuningEntry} onNewEntry={startNewTuningEntry} onDuplicateEntry={startDuplicateTuningEntry} onEntryField={(field, value) => { setTuningEntryDraft((draft) => draft ? { ...draft, [field]: value } : draft); setAuthoringNotice(""); }} onKitType={(value) => { setTuningKitType(value); setAuthoringNotice(""); }} onLiveryNames={(value) => { setTuningLiveries(value); setAuthoringNotice(""); }} onReviewKit={reviewTuningKit} onReviewEntry={reviewTuningEntry} onRemoveEntry={() => { if (tuningBuilder && tuningSelectedIndex !== null) reviewTuningMutation({ action: "remove_entry", kit_name: tuningBuilder.kit_name, collection: tuningCollection, index: tuningSelectedIndex }); }} onMoveEntry={(newIndex) => { if (tuningBuilder && tuningSelectedIndex !== null) reviewTuningMutation({ action: "move_entry", kit_name: tuningBuilder.kit_name, collection: tuningCollection, index: tuningSelectedIndex, new_index: newIndex }); }} onReset={resetTuningEdits} />}
              {appearanceSection === "lights" && <VehicleLightProfileEditor profiles={authoringSession.appearance?.light_profiles ?? []} profileId={lightProfileId} draft={lightProfileDraft} dirty={lightProfileDirty} busy={authoringBusy} onProfile={selectLightProfile} onField={(field, value) => { setLightProfileDraft((draft) => ({ ...draft, [field]: value })); setAuthoringNotice(""); }} onReset={() => { setLightProfileDraft({ ...lightProfileBaseline }); setAuthoringNotice("Light values reset to the saved profile."); }} onReview={reviewLightProfile} />}
            </div> : authoringMode === "axles" && authoringSession ? <div className="vehicle-authoring-editor vehicle-axle-editor" aria-live="polite"><VehicleAxleEditor configuration={axleDraft} skeleton={axleSkeleton} selectedOrder={selectedAxleOrder} dirty={axleDirty} busy={authoringBusy} onSelect={setSelectedAxleOrder} onConfiguration={(configuration) => { setAxleDraft(configuration); setAuthoringNotice(""); }} onChooseSkeleton={() => void chooseAxleSkeleton()} onMoveOrder={moveAxleOrder} onRestoreCanonical={() => { if (axleSkeleton) runAxleSkeletonAction("canonical_order", axleSkeleton.skeleton_xml); }} onCalculateSteering={(request) => { if (axleSkeleton) runAxleSkeletonAction("steering", axleSkeleton.skeleton_xml, { request }); }} onReset={() => { setAxleDraft(axleBaseline ? structuredClone(axleBaseline) : null); setSelectedAxleOrder(axleBaseline?.axles[0]?.physical_order ?? null); setAuthoringNotice("Axle changes reset to the saved configuration."); }} onReview={reviewAxleConfiguration} /></div> : authoringMode === "transmission" && authoringSession ? <div className="vehicle-authoring-editor vehicle-transmission-editor" aria-live="polite"><VehicleTransmissionEditor configuration={transmissionDraft} stockGearCount={Number(authoringSession.values["handling.nInitialDriveGears"] ?? 0)} dirty={transmissionDirty} busy={authoringBusy} onCreate={createTransmissionProfile} onConfiguration={(configuration) => { setTransmissionDraft(configuration); setAuthoringNotice(""); }} onReset={() => { setTransmissionDraft(transmissionBaseline ? structuredClone(transmissionBaseline) : null); setAuthoringNotice("Transmission changes reset to the saved profile."); }} onReview={reviewTransmission} /></div> : authoringMode === "output" && authoringSession && distributionDraft ? <div className="vehicle-authoring-editor vehicle-output-editor" aria-live="polite"><VehicleOutputEditor distribution={distributionDraft} distributionDirty={distributionDirty} packageDraft={packageDraft} packageReview={packageReview} packageResult={packageResult} busy={authoringBusy} workspaceClean={!authoringDirty} onDistribution={(values) => { setDistributionDraft(values); setPackageReview(null); setPackageResult(null); setAuthoringNotice(""); }} onResetDistribution={() => { setDistributionDraft(distributionBaseline ? structuredClone(distributionBaseline) : null); setAuthoringNotice("Distribution changes reset to the saved revision."); }} onReviewDistribution={reviewDistribution} onPackage={(values) => { setPackageDraft(values); setPackageReview(null); setPackageResult(null); setAuthoringNotice(""); }} onChooseDestination={() => void choosePackageDestination()} onReviewPackage={reviewPackageBuild} /></div> : <>
              {!selectedModel && <div className="pane-empty inspector-empty"><strong>No model selected</strong><p>Vehicle identity and exact package links will remain read-only here.</p></div>}
              {selectedModel && <div className="vehicle-evidence-body" aria-live="polite">
                {previewBusy && <div className="preview-progress"><span className="activity-dot busy" /><strong>Preparing linked asset</strong><p>Python is revalidating the exact package member.</p></div>}
                {selectedAsset && selectedAssetIsModel && <VehicleViewport
                  key={`${activeResult?.source ?? ""}:${selectedAsset.path}:${selectedModel.texture_asset ?? ""}:${selectedModel.collision_asset ?? ""}:${activeResult?.edition ?? ""}:${activeResult?.gta_path ?? gtaPath}`}
                  client={client}
                  source={activeResult?.source ?? source}
                  entry={selectedAsset.path}
                  edition={activeResult?.edition ?? "Enhanced"}
                  gtaPath={activeResult?.gta_path || gtaPath || null}
                  model={selectedModel.model}
                  textureEntry={selectedModel.texture_asset}
                  collisionEntry={selectedModel.collision_asset}
                  onResult={setViewportResult}
                />}
                {!selectedAssetIsModel && preview?.display_kind === "image" && preview.artifact && previewArtifactSource && <figure className="image-preview vehicle-image-preview"><div><img src={previewArtifactSource} alt={`Read-only preview of ${selectedAsset?.path ?? selectedModel.model}`} /></div><figcaption><span>Normalized preview</span><span>{formatBytes(preview.artifact.size)} · SHA-256 {preview.artifact.sha256.slice(0, 12)}…</span></figcaption></figure>}
                {!selectedAssetIsModel && preview?.display_kind === "text" && <pre className="asset-text-preview vehicle-text-preview">{preview.text || "(empty preview)"}</pre>}
                {!selectedAssetIsModel && preview?.display_kind === "metadata" && <div className="pane-empty preview-empty"><strong>Metadata only</strong><p>No renderable artifact was produced for this linked member.</p></div>}
                <dl className="detail-list vehicle-detail-list">
                  <div><dt>Model</dt><dd>{selectedModel.model}</dd></div><div><dt>Display label</dt><dd>{selectedModel.display_name || "—"}</dd></div><div><dt>Manufacturer</dt><dd>{selectedModel.make_name || "—"}</dd></div><div><dt>Class</dt><dd>{selectedModel.vehicle_class || "—"}</dd></div><div><dt>Type</dt><dd>{selectedModel.vehicle_type || "—"}</dd></div><div><dt>Handling</dt><dd>{selectedModel.handling_id || "—"}</dd></div><div><dt>Layout</dt><dd>{selectedModel.layout || "—"}</dd></div><div><dt>Audio</dt><dd>{selectedModel.audio_name_hash || "—"}</dd></div><div><dt>Texture dictionary</dt><dd>{selectedModel.texture_dictionary || "—"}</dd></div><div><dt>Tuning kits</dt><dd>{selectedModel.tuning_kits.join(", ") || "None"}</dd></div>
                  {selectedAsset && <><div><dt>Selected role</dt><dd>{formatReadiness(selectedAsset.role)}</dd></div><div><dt>Selected path</dt><dd>{selectedAsset.path}</dd></div><div><dt>Required link</dt><dd>{selectedAsset.required ? "Yes" : "No"}</dd></div></>}
                  {preview && <><div><dt>Bytes read</dt><dd>{formatBytes(preview.bytes_read)}</dd></div><div><dt>SHA-256</dt><dd>{preview.sha256 ?? "Not calculated for a truncated read"}</dd></div></>}
                  {viewportResult && <><div><dt>Native geometry</dt><dd>{viewportResult.scene.component_count} components · {viewportResult.scene.material_count} materials · {viewportResult.scene.bone_count} bones</dd></div><div><dt>Bytes read</dt><dd>{formatBytes(viewportResult.bytes_read)}</dd></div><div><dt>SHA-256</dt><dd>{viewportResult.sha256}</dd></div></>}
                </dl>
                {viewportResult?.warnings.length ? <div className="vehicle-finding-list"><strong>Viewport findings</strong>{viewportResult.warnings.map((warning, index) => <span key={`${warning}-${index}`} className="severity-warning">{warning}</span>)}</div> : null}
                {selectedModel.findings.length > 0 && <div className="vehicle-finding-list"><strong>Model findings</strong>{selectedModel.findings.map((finding, index) => <span key={`${finding.code}-${index}`} className={`severity-${finding.severity}`}>{finding.code.replaceAll("_", " ")}: {finding.message}</span>)}</div>}
              </div>}
            </>}
            <div className="recipe-safety-note"><strong>{authoringSession ? "Copied-workspace boundary" : "Read-only boundary"}</strong><span>{authoringSession ? "Reviewed edits write only to this authoring copy. No GTA V files are opened for writing." : "This view resolves Python-owned project evidence. Create an editable copy before changing metadata."}</span></div>
          </aside>
        </div>
        {pendingCreate && <AuthoringConfirmation title="Create editable vehicle copy?" description="The source package was re-inspected and the destination is still new. This copies package-owned files into a revisioned authoring workspace." details={[{ label: "Source", value: pendingCreate.review.source }, { label: "Destination", value: pendingCreate.review.destination }, { label: "Vehicles", value: String(pendingCreate.review.model_count) }, { label: "Copy size", value: formatBytes(pendingCreate.review.copy_bytes) }]} confirmLabel="Create editable copy" warning="This writes a new workspace, but does not change the source package or GTA V installation." onCancel={() => setPendingCreate(null)} onConfirm={() => void confirmCreate()} />}
        {pendingEdit && <AuthoringConfirmation title="Save reviewed vehicle fields?" description="Python normalized and validated every changed value against the current workspace revision." details={[{ label: "Vehicle", value: pendingEdit.review.model }, { label: "Current revision", value: String(pendingEdit.review.revision) }, { label: "Changes", value: String(pendingEdit.review.changes.length) }, ...pendingEdit.review.changes.slice(0, 4).map((change) => ({ label: change.field, value: `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save new revision" warning="Only the copied authoring workspace will be updated. The original package and game installation remain untouched." onCancel={() => setPendingEdit(null)} onConfirm={() => void confirmEdit()} />}
        {pendingAppearance && <AuthoringConfirmation title="Save reviewed appearance?" description="Python resolved the color presets, tuning kits, and lighting references against the current workspace revision." details={[{ label: "Vehicle", value: pendingAppearance.review.model }, { label: "Current revision", value: String(pendingAppearance.review.revision) }, { label: "Changes", value: String(pendingAppearance.review.changes.length) }, ...pendingAppearance.review.changes.map((change) => ({ label: change.field, value: change.field === "variation.colors" ? "Color preset collection updated" : `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save appearance revision" warning="Only the copied authoring workspace will be updated. The original package and GTA V files remain untouched." onCancel={() => setPendingAppearance(null)} onConfirm={() => void confirmAppearance()} />}
        {pendingTuning && <AuthoringConfirmation title="Save reviewed tuning change?" description="Python rebuilt the proposed kit in memory and rejected any new validation errors before producing this review." details={[{ label: "Vehicle", value: pendingTuning.review.model }, { label: "Action", value: pendingTuning.review.action.replaceAll("_", " ") }, { label: "Kit", value: String(pendingTuning.review.mutation.kit_name ?? "") }, { label: "Current revision", value: String(pendingTuning.review.revision) }, { label: "Changes", value: String(pendingTuning.review.changes.length) }, ...pendingTuning.review.changes.slice(0, 4).map((change) => ({ label: change.field, value: `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save tuning revision" warning="Only carcols metadata inside the copied authoring workspace will be updated." onCancel={() => setPendingTuning(null)} onConfirm={() => void confirmTuning()} />}
        {pendingLightProfile && <AuthoringConfirmation title="Save reviewed light profile?" description="Python resolved the selected carcols profile and normalized only the scalar values shown below." details={[{ label: "Vehicle", value: pendingLightProfile.review.model }, { label: "Profile", value: pendingLightProfile.review.profile_id }, { label: "Current revision", value: String(pendingLightProfile.review.revision) }, { label: "Changes", value: String(pendingLightProfile.review.changes.length) }, ...pendingLightProfile.review.changes.slice(0, 5).map((change) => ({ label: change.field, value: `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save light revision" warning="Only the copied authoring workspace will be updated. GTA V files remain untouched." onCancel={() => setPendingLightProfile(null)} onConfirm={() => void confirmLightProfile()} />}
        {pendingAxles && <AuthoringConfirmation title="Save reviewed axle configuration?" description="Python validated the physical axle roles and calculated the handling metadata changes for this revision." details={[{ label: "Vehicle", value: pendingAxles.review.model }, { label: "Current revision", value: String(pendingAxles.review.revision) }, { label: "Physical axles", value: String(pendingAxles.review.configuration.axles.length) }, { label: "Changes", value: String(pendingAxles.review.changes.length) }, ...pendingAxles.review.changes.map((change) => ({ label: change.field, value: change.field === "axles.configuration" ? "Axle roles and runtime configuration updated" : `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save axle revision" warning="Only the copied authoring workspace and its package-owned handling metadata will be updated." onCancel={() => setPendingAxles(null)} onConfirm={() => void confirmAxles()} />}
        {pendingTransmission && <AuthoringConfirmation title="Save reviewed transmission profile?" description="Python validated every ratio and synchronized the stock handling gear count with the ALLIN1 profile." details={[{ label: "Vehicle", value: pendingTransmission.review.model }, { label: "Type", value: pendingTransmission.review.configuration.transmission_type.replaceAll("_", " ") }, { label: "Forward gears", value: String(pendingTransmission.review.configuration.gear_ratios.length) }, { label: "Final drive", value: String(pendingTransmission.review.configuration.final_drive_ratio) }, { label: "Changes", value: String(pendingTransmission.review.changes.length) }, ...pendingTransmission.review.warnings.map((warning) => ({ label: "Review warning", value: warning }))]} confirmLabel="Save transmission revision" warning="The ratio table is ALLIN1 extension metadata. Stock handling.meta receives only the forward gear count." onCancel={() => setPendingTransmission(null)} onConfirm={() => void confirmTransmission()} />}
        {pendingDistribution && <AuthoringConfirmation title="Save reviewed distribution settings?" description="Python normalized the catalog record and bound every changed value to the current authoring revision." details={[{ label: "Vehicle", value: pendingDistribution.review.model }, { label: "Catalog state", value: pendingDistribution.review.distribution.listed ? "Listed" : "Hidden" }, { label: "Category", value: pendingDistribution.review.distribution.category.replaceAll("_", " ") }, { label: "Price", value: String(pendingDistribution.review.distribution.price) }, { label: "Changes", value: String(pendingDistribution.review.changes.length) }, ...pendingDistribution.review.changes.slice(0, 4).map((change) => ({ label: change.field, value: `${change.before || "(empty)"} → ${change.after || "(empty)"}` }))]} confirmLabel="Save distribution revision" warning="This updates only the copied workspace catalog settings. It does not publish a package or modify GTA V." onCancel={() => setPendingDistribution(null)} onConfirm={() => void confirmDistribution()} />}
        {pendingPackageBuild && <AuthoringConfirmation title="Build this validated vehicle package?" description="The exact workspace revision, source payload, distribution record, profiles, editions, and new output folder passed the read-only review." details={[{ label: "Package", value: pendingPackageBuild.review.name }, { label: "Package ID", value: pendingPackageBuild.review.mod_id }, { label: "Workspace revision", value: String(pendingPackageBuild.review.revision) }, { label: "Target editions", value: pendingPackageBuild.review.editions.join(", ") }, { label: "Destination", value: pendingPackageBuild.review.destination }, ...pendingPackageBuild.review.warnings.map((warning) => ({ label: "Integration note", value: warning }))]} confirmLabel="Build validated package" warning="This creates a new package folder only. It does not install the package or write to GTA V." onCancel={() => setPendingPackageBuild(null)} onConfirm={() => void confirmPackageBuild()} />}
        {pendingHistory && authoringSession && <AuthoringConfirmation title={`${pendingHistory === "undo" ? "Undo" : "Redo"} the last vehicle edit?`} description="History restoration is transactional and creates a new workspace revision." details={[{ label: "Vehicle", value: authoringSession.selected_model ?? "Current vehicle" }, { label: "Workspace revision", value: String(authoringSession.revision) }, { label: "Action", value: pendingHistory }]} confirmLabel={`${pendingHistory === "undo" ? "Undo" : "Redo"} edit`} warning="This changes only the copied authoring workspace and can be reviewed again afterward." onCancel={() => setPendingHistory(null)} onConfirm={() => void confirmHistory()} />}
      </>}
    </section>
  );
}

function PackageLinker({
  client,
  result,
  source,
  busy,
  error,
  onInspect,
  onCancel,
}: {
  client: DesktopClient;
  result: PackageResult | null;
  source: string;
  busy: boolean;
  error: string;
  onInspect: (source: string) => void;
  onCancel: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [manifestView, setManifestView] = useState<"nodes" | "references" | "steps">("nodes");
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [exportError, setExportError] = useState("");
  const nodeRows = safeRows(result?.nodes);
  const referenceRows = safeRows(result?.references);
  const installRows = safeRows(result?.install_steps);
  const inventoryRows = safeRows(result?.entries);
  const manifestRows = manifestView === "references" ? referenceRows : manifestView === "steps" ? installRows : nodeRows;
  const primaryRows = result?.kind === "manifest" ? manifestRows : inventoryRows;
  const findingRows = safeRows(result?.kind === "manifest" ? result.issues : result?.findings);
  const filteredRows = primaryRows.filter((item) =>
    JSON.stringify(item).toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()),
  );

  useEffect(() => {
    setSelected(null);
    setFilter("");
    setManifestView("nodes");
    setExportNotice("");
    setExportError("");
  }, [source]);

  const choose = async () => {
    const selectedPath = await client.selectPath("package");
    if (selectedPath) onInspect(selectedPath);
  };
  const chooseFolder = async () => {
    const selectedPath = await client.selectPath("package_folder");
    if (selectedPath) onInspect(selectedPath);
  };
  const exportReport = async () => {
    if (!result || result.kind !== "manifest") return;
    const reportId = String(result.id ?? "allin1")
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "allin1";
    const destination = await client.selectReportDestination(`${reportId}-link-report.md`);
    if (!destination) return;
    setExporting(true);
    setExportNotice("");
    setExportError("");
    try {
      const response = await client.exportLinkReport(String(result.source ?? source), destination);
      if (response.operation === "error") throw new Error(messageText(response));
      setExportNotice(`Report exported to ${destination}`);
    } catch (reason) {
      setExportError(String(reason));
    } finally {
      setExporting(false);
    }
  };

  const selectManifestView = (view: "nodes" | "references" | "steps") => {
    setManifestView(view);
    setFilter("");
    setSelected(null);
  };

  return (
    <section aria-labelledby="linker-title" className="workspace-section">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Package intake</span>
          <h2 id="linker-title">Package Linker</h2>
          <p>Open an addon manifest, product workspace, package folder, or bounded archive.</p>
        </div>
        <div className="heading-actions">
          <button className="primary-button" onClick={choose} disabled={busy}>
            Open package
          </button>
          <button className="quiet-button" onClick={chooseFolder} disabled={busy}>
            Open folder
          </button>
          {source && (
            <button className="quiet-button" onClick={() => onInspect(source)} disabled={busy}>
              Refresh
            </button>
          )}
          {result?.kind === "manifest" && (
            <button className="quiet-button" onClick={exportReport} disabled={busy || exporting}>
              {exporting ? "Exporting…" : "Export report"}
            </button>
          )}
          {busy && <button className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>
      </div>

      <div className="source-strip" aria-live="polite">
        <span className={`activity-dot ${busy ? "busy" : result ? "ready" : ""}`} />
        <strong>{busy ? "Inspecting package…" : result ? "Inspection complete" : "Choose a package to begin"}</strong>
        <span className="source-path" title={source}>{source || "No source selected"}</span>
      </div>

      {error && <div className="error-banner" role="alert">{error}</div>}
      {exportError && <div className="error-banner" role="alert">Report export failed: {exportError}</div>}
      {exportNotice && <div className="action-notice" role="status">{exportNotice}</div>}

      {result && (
        <div className="summary-row" aria-label="Inspection summary">
          <StatusPill valid={result.valid}>{result.valid ? "Passing" : "Review required"}</StatusPill>
          <span><strong>{Number(result.error_count ?? 0)}</strong> errors</span>
          <span><strong>{Number(result.warning_count ?? 0)}</strong> warnings</span>
          {result.kind === "package_scan" && <span><strong>{Number(result.file_count ?? 0)}</strong> files</span>}
          {result.kind === "package_scan" && <span><strong>{formatBytes(result.total_bytes)}</strong></span>}
          {result.kind === "manifest" && <span><strong>{referenceRows.filter((item) => item.valid === true).length}/{referenceRows.length}</strong> links resolved</span>}
          {result.kind === "manifest" && <span><strong>{installRows.length}</strong> install steps</span>}
          {result.kind === "manifest" && <span className="summary-name"><strong>{String(result.name ?? result.id ?? "Manifest")}</strong></span>}
        </div>
      )}

      <div className={`panel-grid linker-grid ${result ? "has-result" : "is-empty"}`}>
        <section className="pane" aria-label={result?.kind === "manifest" ? "Integration graph" : "Package entries"}>
          <div className="pane-header">
            <div>
              <span className="pane-kicker">Package</span>
              <h3>{result?.kind === "manifest" ? "Integration graph" : "Package inventory"}</h3>
            </div>
            <span className="pane-count">{primaryRows.length}</span>
          </div>
          {result?.kind === "manifest" && (
            <div className="pane-tabs" role="tablist" aria-label="Integration graph views">
              {([
                ["nodes", "Nodes", nodeRows.length],
                ["references", "Links", referenceRows.length],
                ["steps", "Install", installRows.length],
              ] as const).map(([view, label, count]) => (
                <button key={view} role="tab" aria-selected={manifestView === view} onClick={() => selectManifestView(view)}>
                  {label}<span>{count}</span>
                </button>
              ))}
            </div>
          )}
          <label className="search-field">
            <span className="sr-only">Filter {result?.kind === "manifest" ? manifestView : "package rows"}</span>
            <span aria-hidden="true">⌕</span>
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={result?.kind === "manifest" ? `Filter ${manifestView}` : "Filter package rows"} disabled={!result} />
          </label>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>No package loaded</strong><p>Open a manifest, package folder, or bounded archive to build the inventory.</p><button className="text-action" onClick={choose}>Choose package</button></div>}
            {result && filteredRows.length === 0 && <p className="empty-copy">No inventory rows match this filter.</p>}
            {filteredRows.map((item, index) => {
              const isReference = result?.kind === "manifest" && manifestView === "references";
              const isStep = result?.kind === "manifest" && manifestView === "steps";
              const label = isReference
                ? `${String(item.source ?? "source")}.${String(item.source_field ?? "field")} → ${String(item.target ?? "target")}.${String(item.target_field ?? "field")}`
                : String(item.label ?? item.path ?? item.id ?? item.step_id ?? `Item ${index + 1}`);
              const meta = String(isReference ? item.relationship ?? "reference" : isStep ? item.strategy ?? "install step" : item.kind ?? item.category ?? item.preview_kind ?? "item");
              const rowKey = String(item.id ?? item.step_id ?? label);
              return (
                <button
                  className={`data-row ${selected === item ? "selected" : ""}`}
                  key={`${rowKey}-${index}`}
                  onClick={() => setSelected(item)}
                >
                  <span className="row-type" aria-hidden="true">{isReference ? "↗" : isStep ? String(item.order ?? index + 1) : meta.slice(0, 1).toUpperCase()}</span>
                  <span><strong>{label}</strong><small>{meta}</small></span>
                  {isReference && <span className={`row-state ${item.valid ? "valid" : "invalid"}`}>{item.valid ? "Resolved" : "Review"}</span>}
                  {!isReference && typeof item.size === "number" && <small>{formatBytes(item.size)}</small>}
                </button>
              );
            })}
          </div>
        </section>

        <section className="pane" aria-label="Diagnostics">
          <div className="pane-header">
            <div>
              <span className="pane-kicker">Validation</span>
              <h3>Diagnostics</h3>
            </div>
            <span className="pane-count">{findingRows.length}</span>
          </div>
          <div className="row-list diagnostics">
            {!result && <div className="pane-empty"><strong>Waiting for inspection</strong><p>Containment, ownership, and package-policy findings will appear here.</p></div>}
            {result && findingRows.length === 0 && <div className="pane-empty compact-empty"><strong>No diagnostics</strong><p>The Python validator did not report any findings.</p></div>}
            {findingRows.map((item, index) => (
              <button
                className="finding-row"
                key={`${String(item.code)}-${index}`}
                onClick={() => setSelected(item)}
              >
                <StatusPill tone={String(item.severity) === "error" ? "danger" : String(item.severity) === "warning" ? "warning" : "neutral"}>{String(item.severity ?? "info")}</StatusPill>
                <span><strong>{String(item.code ?? "diagnostic")}</strong><small>{String(item.message ?? "")}</small></span>
              </button>
            ))}
          </div>
        </section>

        <aside className="pane inspector-pane" aria-label="Field inspector">
          <div className="pane-header">
            <div>
              <span className="pane-kicker">Inspector</span>
              <h3>Selected evidence</h3>
            </div>
          </div>
          {selected ? (
            <dl className="detail-list">
              {Object.entries(selected).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "—")}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <div className="pane-empty inspector-empty"><strong>No selection</strong><p>{result ? "Select an inventory row or diagnostic to inspect its evidence." : "Evidence fields will remain read-only and sourced from Python."}</p></div>
          )}
        </aside>
      </div>
    </section>
  );
}

function AssetViewer({
  client,
  result,
  source,
  busy,
  activeJob,
  error,
  onInspect,
  onCancel,
  onJob,
}: {
  client: DesktopClient;
  result: PackageResult | null;
  source: string;
  busy: boolean;
  activeJob: string | null;
  error: string;
  onInspect: (source: string) => void;
  onCancel: () => void;
  onJob: (jobId: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [selectedEntry, setSelectedEntry] = useState<Record<string, unknown> | null>(null);
  const [preview, setPreview] = useState<AssetPreviewResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const latestRevision = useRef("");
  const completedRevision = useRef("");
  const previewJob = useRef<string | null>(null);
  const entries = result?.kind === "package_scan" ? safeRows(result.entries) : [];
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      const key = String(entry.category ?? "Other");
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts].sort(([left], [right]) => left.localeCompare(right));
  }, [entries]);
  const filteredEntries = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => {
      const entryCategory = String(entry.category ?? "Other");
      return (category === "all" || entryCategory === category)
        && (!needle || String(entry.path ?? "").toLocaleLowerCase().includes(needle));
    });
  }, [category, entries, query]);

  useEffect(() => {
    latestRevision.current = `assets-reset-${Date.now()}`;
    if (previewJob.current) void client.cancelJob(previewJob.current);
    previewJob.current = null;
    setSelectedEntry(null);
    setPreview(null);
    setPreviewError("");
    setPreviewBusy(false);
    setQuery("");
    setCategory("all");
  }, [client, source]);

  const choose = async () => {
    const selectedPath = await client.selectPath("package");
    if (selectedPath) onInspect(selectedPath);
  };
  const chooseFolder = async () => {
    const selectedPath = await client.selectPath("package_folder");
    if (selectedPath) onInspect(selectedPath);
  };

  const loadPreview = async (entry: Record<string, unknown>) => {
    const path = String(entry.path ?? "");
    if (!source || !path) return;
    const revision = `assets-${Date.now()}-${path}`;
    latestRevision.current = revision;
    setSelectedEntry(entry);
    setPreview(null);
    setPreviewError("");
    setPreviewBusy(true);
    if (previewJob.current) {
      try {
        await client.cancelJob(previewJob.current);
      } catch {
        // A terminal completion can win this race; its revision is still rejected.
      }
      previewJob.current = null;
      if (latestRevision.current !== revision) return;
      onJob(null);
    }
    try {
      const started = await client.startJob(
        "preview_asset",
        {
          source,
          entry: path,
          edition: String(result?.edition ?? "Enhanced"),
        },
        revision,
        (message) => {
          if (
            !message.terminal
            || message.payload.revision !== latestRevision.current
            || revision !== latestRevision.current
          ) return;
          completedRevision.current = revision;
          previewJob.current = null;
          setPreviewBusy(false);
          onJob(null);
          if (message.operation === "error") {
            setPreviewError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded) setPreview(loaded as AssetPreviewResult);
        },
      );
      if (completedRevision.current !== revision && latestRevision.current === revision) {
        previewJob.current = started.job_id;
        onJob(started.job_id);
      }
    } catch (reason) {
      if (latestRevision.current === revision) {
        setPreviewError(String(reason));
        setPreviewBusy(false);
        previewJob.current = null;
        onJob(null);
      }
    }
  };

  const selectedDetails: [string, unknown][] = selectedEntry ? [
    ["Package path", selectedEntry.path],
    ["Category", selectedEntry.category],
    ["Authored size", typeof selectedEntry.size === "number" ? formatBytes(selectedEntry.size) : selectedEntry.size],
    ["Preview class", selectedEntry.preview_kind],
    ...(preview ? [
      ["Bytes read", formatBytes(preview.bytes_read)],
      ["SHA-256", preview.sha256 ?? "Not calculated for a truncated read"],
      ["Truncated", preview.truncated || preview.text_truncated ? "Yes" : "No"],
      ["Artifact digest", preview.artifact?.sha256 ?? "—"],
      ...Object.entries(preview.metadata ?? {}).map(([key, value]) => [key.replaceAll("_", " "), value] as [string, unknown]),
    ] as [string, unknown][] : []),
  ] : [];

  const sourceState = busy && !result
    ? "Indexing package…"
    : previewBusy
      ? "Preparing bounded preview…"
      : result?.kind === "package_scan"
        ? "Inventory ready"
        : result?.kind === "manifest"
          ? "Manifest loaded"
          : "Choose a package to begin";
  const previewArtifactSource = preview?.artifact
    ? preview.artifact.preview_url ?? convertFileSrc(preview.artifact.path)
    : null;

  return (
    <section aria-labelledby="assets-title" className="workspace-section asset-workspace">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Read-only inspection</span>
          <h2 id="assets-title">Asset Viewer</h2>
          <p>Browse the Python-owned inventory and inspect one guarded package member at a time.</p>
        </div>
        <div className="heading-actions">
          <button className="primary-button" onClick={choose} disabled={busy}>Open package</button>
          <button className="quiet-button" onClick={chooseFolder} disabled={busy}>Open folder</button>
          {source && <button className="quiet-button" onClick={() => onInspect(source)} disabled={busy}>Refresh</button>}
          {activeJob && <button className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>
      </div>

      <div className="source-strip" aria-live="polite">
        <span className={`activity-dot ${busy || previewBusy ? "busy" : result ? "ready" : ""}`} />
        <strong>{sourceState}</strong>
        <span className="source-path" title={source}>{source || "No source selected"}</span>
      </div>
      {error && <div className="error-banner" role="alert">Package inspection failed: {error}</div>}
      {previewError && <div className="error-banner" role="alert">Preview failed: {previewError}</div>}
      {result?.kind === "manifest" && <div className="action-notice" role="status">This source is an integration manifest. Open its package folder to inspect authored assets.</div>}

      {result?.kind === "package_scan" && (
        <div className="summary-row asset-summary" aria-label="Asset inventory summary">
          <StatusPill tone="neutral">read only</StatusPill>
          <span><strong>{Number(result.inventory_count ?? entries.length)}</strong> indexed assets</span>
          <span><strong>{categories.length}</strong> categories</span>
          <span><strong>{formatBytes(result.total_bytes)}</strong> authored</span>
          <span><strong>{Number(result.warning_count ?? 0)}</strong> warnings</span>
          {result.truncated === true && <StatusPill tone="warning">inventory bounded</StatusPill>}
        </div>
      )}

      <div className={`panel-grid asset-grid ${result?.kind === "package_scan" ? "has-result" : "is-empty"}`}>
        <section className="pane asset-inventory-pane" aria-label="Asset inventory">
          <div className="pane-header">
            <div><span className="pane-kicker">Package</span><h3>Inventory</h3></div>
            <span className="pane-count">{filteredEntries.length}/{entries.length}</span>
          </div>
          <div className="asset-filter-bar">
            <label className="search-field">
              <span aria-hidden="true">⌕</span><span className="sr-only">Filter assets</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter paths" disabled={!entries.length} />
            </label>
            <label><span className="sr-only">Asset category</span><select value={category} onChange={(event) => setCategory(event.target.value)} disabled={!entries.length}><option value="all">All categories</option>{categories.map(([name, count]) => <option key={name} value={name}>{name} · {count}</option>)}</select></label>
          </div>
          <div className="row-list asset-rows">
            {!result && <div className="pane-empty"><strong>No package loaded</strong><p>Open a folder or bounded archive to build the asset inventory.</p><button className="text-action" onClick={choose}>Choose package</button></div>}
            {result?.kind === "manifest" && <div className="pane-empty"><strong>Package folder required</strong><p>Manifests describe integration links; the viewer reads assets from the package itself.</p><button className="text-action" onClick={chooseFolder}>Open package folder</button></div>}
            {result?.kind === "package_scan" && filteredEntries.length === 0 && <p className="empty-copy">No asset paths match this filter.</p>}
            {filteredEntries.map((entry, index) => {
              const path = String(entry.path ?? `Asset ${index + 1}`);
              const kind = String(entry.preview_kind ?? "binary");
              return <button key={`${path}-${index}`} className={`data-row asset-row ${selectedEntry === entry ? "selected" : ""}`} onClick={() => void loadPreview(entry)} aria-pressed={selectedEntry === entry}><span className="row-type" aria-hidden="true">{kind === "image" ? "▧" : kind === "text" ? "¶" : "01"}</span><span><strong title={path}>{path}</strong><small>{String(entry.category ?? "Other")} · {kind}</small></span><small>{formatBytes(entry.size)}</small></button>;
            })}
          </div>
        </section>

        <section className="pane asset-preview-pane" aria-label="Asset preview">
          <div className="pane-header">
            <div><span className="pane-kicker">Guarded output</span><h3>{selectedEntry ? String(selectedEntry.path) : "Preview"}</h3></div>
            {preview && <StatusPill tone={preview.truncated ? "warning" : "success"}>{preview.display_kind}</StatusPill>}
          </div>
          <div className="asset-preview-body" aria-live="polite">
            {!selectedEntry && <div className="pane-empty preview-empty"><strong>No asset selected</strong><p>Select an inventory row. Python will revalidate the member and return bounded evidence.</p></div>}
            {selectedEntry && previewBusy && <div className="preview-progress"><span className="activity-dot busy" /><strong>Preparing preview</strong><p>Reading and decoding remain outside the WebView.</p></div>}
            {preview?.display_kind === "image" && preview.artifact && previewArtifactSource && <figure className="image-preview"><div><img src={previewArtifactSource} alt={`Read-only preview of ${preview.path}`} /></div><figcaption><span>{preview.artifact.media_type === "image/png" ? "Normalized PNG" : "Preview artifact"}</span><span>{formatBytes(preview.artifact.size)} · SHA-256 {preview.artifact.sha256.slice(0, 12)}…</span></figcaption></figure>}
            {preview?.display_kind === "text" && <pre className="asset-text-preview">{preview.text || "(empty preview)"}</pre>}
            {preview?.display_kind === "metadata" && <div className="pane-empty preview-empty"><strong>Metadata only</strong><p>The asset was classified safely, but no renderable artifact was produced.</p></div>}
          </div>
          {preview && (preview.truncated || preview.text_truncated || preview.warnings.length > 0) && <div className="preview-notes">{preview.truncated && <span>Source read reached its safety limit.</span>}{preview.text_truncated && <span>Displayed text was clipped to the desktop contract limit.</span>}{preview.warnings.map((warning, index) => <span key={`${warning}-${index}`}>{warning}</span>)}</div>}
        </section>

        <aside className="pane asset-evidence-pane" aria-label="Preview evidence">
          <div className="pane-header"><div><span className="pane-kicker">Evidence</span><h3>Read record</h3></div></div>
          {selectedEntry ? <dl className="detail-list">{selectedDetails.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "—")}</dd></div>)}</dl> : <div className="pane-empty inspector-empty"><strong>Nothing read yet</strong><p>Size, digest, truncation, decoder metadata, and artifact identity appear after selection.</p></div>}
        </aside>
      </div>
    </section>
  );
}

function RecipeWorkspace({
  client,
  result,
  source,
  busy,
  error,
  onInspect,
  onCancel,
  onGuardChange,
  onConversionGuardChange,
}: {
  client: DesktopClient;
  onConversionGuardChange: (guarded: boolean) => void;
  result: RecipePlanResult | null;
  source: string;
  busy: boolean;
  error: string;
  onInspect: (source: string) => void;
  onCancel: () => void;
  onGuardChange: (guarded: boolean) => void;
}) {
  const [conversionGuarded, setConversionGuarded] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<{ kind: "operation" | "finding"; index: number } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportNotice, setExportNotice] = useState("");
  const [exportError, setExportError] = useState("");
  const locked = busy || exporting || choosing || conversionGuarded;
  useEffect(() => { onConversionGuardChange(exporting || conversionGuarded); }, [exporting, conversionGuarded, onConversionGuardChange]);
  useEffect(() => { onGuardChange(exporting || choosing || conversionGuarded); }, [exporting, choosing, conversionGuarded, onGuardChange]);
  const operations = safeRows(result?.operations);
  const findings = safeRows(result?.findings);
  const filteredOperations = operations
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => JSON.stringify(item).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

  useEffect(() => {
    setQuery("");
    setExportNotice("");
    setExportError("");
    setSelection(result && operations.length ? { kind: "operation", index: 0 } : null);
  }, [result, source]);

  const choose = async () => {
    if (locked) return;
    setChoosing(true);
    try { const selected = await client.selectPath("recipe"); if (selected) onInspect(selected); }
    catch (reason) { setExportError(String(reason)); }
    finally { setChoosing(false); }
  };
  const chooseFolder = async () => {
    if (locked) return;
    setChoosing(true);
    try { const selected = await client.selectPath("recipe_folder"); if (selected) onInspect(selected); }
    catch (reason) { setExportError(String(reason)); }
    finally { setChoosing(false); }
  };
  const exportReport = async () => {
    if (!result || !source || locked) return;
    const sourceName = (source.split(/[\\/]/).at(-1) ?? "package-recipe")
      .replace(/\.(oiv|zip)$/i, "")
      .replace(/[^a-zA-Z0-9._-]+/g, "-") || "package-recipe";
    setExporting(true);
    setExportNotice("");
    setExportError("");
    try {
      const destination = await client.selectReportDestination(`${sourceName}-recipe-plan.md`);
      if (!destination) return;
      const response = await client.exportRecipeReport(source, destination);
      if (response.operation === "error") throw new Error(messageText(response));
      setExportNotice(`Recipe report exported to ${destination}`);
    } catch (reason) {
      setExportError(String(reason));
    } finally {
      setExporting(false);
    }
  };

  const selectedItem = selection?.kind === "operation"
    ? operations[selection.index]
    : selection?.kind === "finding"
      ? findings[selection.index]
      : null;
  const selectedDetails: [string, unknown][] = selection?.kind === "operation" && selectedItem ? [
    ["Operation", selectedItem.number],
    ["Action", selectedItem.kind],
    ["Archive chain", Array.isArray(selectedItem.archives) && selectedItem.archives.length ? selectedItem.archives.join(" → ") : "Filesystem"],
    ["Source", selectedItem.source || "—"],
    ["Target", selectedItem.target || "—"],
    ["Translation", selectedItem.supported ? "Supported" : "Manual review required"],
    ["Creates archive", selectedItem.creates_archive ? "Yes" : "No"],
    ["Structured edits", Array.isArray(selectedItem.edits) ? selectedItem.edits.length : 0],
    ["Detail", selectedItem.detail || "—"],
  ] : selection?.kind === "finding" && selectedItem ? [
    ["Severity", selectedItem.severity],
    ["Code", selectedItem.code],
    ["Operation", selectedItem.operation || "Package-level"],
    ["Message", selectedItem.message],
  ] : result ? [
    ["Package", result.name],
    ["Author", result.author || "Unknown"],
    ["OIV format", result.format_version || "Unspecified"],
    ["Assembly digest", result.assembly_sha256],
    ["Recipe supported", result.recipe_supported ? "Yes" : "No"],
    ["Managed export", result.managed_exportable ? "Eligible" : "Not eligible"],
  ] : [];
  const sourceState = busy
    ? "Inspecting ordered package operations…"
    : result
      ? "Recipe inspection complete"
      : "Choose an OIV recipe to begin";
  const readinessTone = result?.error_count
    ? "danger"
    : result?.warning_count || result?.readiness === "manual_review_required"
      ? "warning"
      : "success";

  return (
    <section className="workspace-section recipe-workspace" aria-labelledby="recipes-title">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Ordered package inspection</span>
          <h2 id="recipes-title">Package Recipes</h2>
          <p>Review OIV instructions through the Python planner without executing package content.</p>
        </div>
        <div className="heading-actions">
          <button className="primary-button" onClick={choose} disabled={locked}>Open recipe</button>
          <button className="quiet-button" onClick={chooseFolder} disabled={locked}>Open folder</button>
          {source && <button className="quiet-button" onClick={() => onInspect(source)} disabled={locked}>Refresh</button>}
          <button className="quiet-button" onClick={exportReport} disabled={!result || locked}>{exporting ? "Exporting…" : "Export report"}</button>
          {busy && <button className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>
      </div>

      <div className="source-strip" aria-live="polite">
        <span className={`activity-dot ${busy ? "busy" : result ? "ready" : ""}`} />
        <strong>{sourceState}</strong>
        <span className="source-path" title={source}>{source || "No recipe selected"}</span>
      </div>
      {error && <div className="error-banner" role="alert">Recipe inspection failed: {error}</div>}
      {exportError && <div className="error-banner" role="alert">Recipe report export failed: {exportError}</div>}
      {exportNotice && <div className="action-notice" role="status">{exportNotice}</div>}

      {result && <div className="summary-row recipe-summary" aria-label="Recipe inspection summary">
        <StatusPill tone={readinessTone}>{formatReadiness(result.readiness_label)}</StatusPill>
        <span><strong>{result.name}</strong>{result.version ? ` · v${result.version}` : ""}</span>
        <span><strong>{result.operation_count}</strong> operations</span>
        <span><strong>{result.error_count}</strong> errors</span>
        <span><strong>{result.warning_count}</strong> warnings</span>
        <span>{result.editions.map((edition) => edition[0]?.toLocaleUpperCase() + edition.slice(1)).join(" / ")}</span>
      </div>}

      <div className={`panel-grid recipe-grid ${result ? "has-result" : "is-empty"}`}>
        <section className="pane recipe-operations-pane" aria-label="Ordered recipe operations">
          <div className="pane-header"><div><span className="pane-kicker">Assembly.xml</span><h3>Ordered operations</h3></div><span className="pane-count">{filteredOperations.length}/{operations.length}</span></div>
          <label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Filter recipe operations</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter actions, archives, and targets" disabled={!operations.length} /></label>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>No recipe loaded</strong><p>Open an OIV, ZIP, or unpacked recipe folder to inspect its ordered instructions.</p><button className="text-action" onClick={choose}>Choose recipe</button></div>}
            {result && !filteredOperations.length && <p className="empty-copy">No operations match this filter.</p>}
            {filteredOperations.map(({ item, index }) => {
              const archives = Array.isArray(item.archives) && item.archives.length ? item.archives.join(" → ") : "Filesystem";
              return <button key={`${item.number}-${index}`} className={`data-row recipe-operation-row ${selection?.kind === "operation" && selection.index === index ? "selected" : ""}`} onClick={() => setSelection({ kind: "operation", index })} aria-pressed={selection?.kind === "operation" && selection.index === index}><span className="row-type" aria-hidden="true">{String(item.number ?? index + 1)}</span><span><strong>{String(item.kind ?? "operation").toLocaleUpperCase()} · {String(item.target || item.source || "Unnamed target")}</strong><small>{archives}</small></span><span className={`row-state ${item.supported ? "valid" : "invalid"}`}>{item.supported ? "Supported" : "Review"}</span></button>;
            })}
          </div>
        </section>

        <section className="pane recipe-findings-pane" aria-label="Recipe findings">
          <div className="pane-header"><div><span className="pane-kicker">Validation</span><h3>Findings</h3></div><span className="pane-count">{findings.length}</span></div>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>Waiting for inspection</strong><p>Recipe blockers and translation warnings will appear here.</p></div>}
            {result && !findings.length && <div className="pane-empty compact-empty"><strong>No blockers found</strong><p>Every declared operation passed the planner’s current safety checks.</p></div>}
            {findings.map((item, index) => <button key={`${item.code}-${index}`} className={`finding-row ${selection?.kind === "finding" && selection.index === index ? "selected" : ""}`} onClick={() => setSelection({ kind: "finding", index })} aria-pressed={selection?.kind === "finding" && selection.index === index}><span className={`row-state ${item.severity === "error" ? "invalid" : item.severity === "warning" ? "warning" : "valid"}`}>{String(item.severity ?? "info")}</span><span><strong>{String(item.code ?? "finding")}</strong><small>{String(item.message ?? "")}</small></span></button>)}
          </div>
        </section>

        <aside className="pane recipe-detail-pane" aria-label="Recipe detail">
          <div className="pane-header"><div><span className="pane-kicker">Read-only evidence</span><h3>{selection?.kind === "finding" ? "Finding detail" : selection?.kind === "operation" ? "Operation detail" : "Recipe detail"}</h3></div></div>
          {selectedDetails.length ? <dl className="detail-list">{selectedDetails.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value ?? "—")}</dd></div>)}</dl> : <div className="pane-empty inspector-empty"><strong>Nothing selected</strong><p>Choose an operation or finding to inspect its exact source, target, archive chain, and safety decision.</p></div>}
          {result && <div className="recipe-safety-note"><strong>Execution boundary</strong><span>Inspection never runs assembly instructions. Offline conversions below require a separate review. Generated RPF plans are never executed here.</span></div>}
        </aside>
      </div>
      {result && <RecipeConversionPanel key={source} client={client} source={source} disabled={busy || exporting || choosing} onGuardChange={setConversionGuarded} />}
    </section>
  );
}

const QUICK_IMPORT_CATEGORIES = [
  "boats", "compacts", "coupes", "cycles", "emergency", "helicopters",
  "industrial", "military", "motorcycles", "muscle", "offroad", "openwheel",
  "planes", "sedans", "service", "special", "sports", "sportsclassics",
  "super", "suvs", "vans",
];
const QUICK_IMPORT_ROAD_CATEGORIES = new Set([
  "compacts", "coupes", "sedans", "suvs", "muscle", "sports",
  "sportsclassics", "super", "offroad", "motorcycles", "vans",
]);

type QuickImportVehicleDraft = {
  model: string;
  name: string;
  manufacturer: string;
  category: string;
  price: string;
  freePriceConfirmed: boolean;
  storage: string;
  sizeTier: string;
  previewDictionary: string;
  previewTexture: string;
  trafficEnabled: boolean;
  trafficWeight: string;
};

type QuickImportEditionDraft = {
  packageId: string;
  packageName: string;
  version: string;
  vehicles: Record<string, QuickImportVehicleDraft>;
};

function storageForVehicleCategory(category: string): string {
  return category === "boats" ? "harbour" : category === "helicopters" ? "helipad" : category === "planes" ? "hangar" : "garage";
}

function quickImportDraftFromReview(review: VehicleQuickImportReviewResult): QuickImportEditionDraft {
  const acknowledged = new Set(review.acknowledged_free_models.map((item) => item.toLocaleLowerCase()));
  return {
    packageId: review.plan.package_id,
    packageName: review.plan.name,
    version: review.plan.version,
    vehicles: Object.fromEntries(review.plan.catalog.vehicles.map((entry) => [entry.model.toLocaleLowerCase(), {
      model: entry.model,
      name: entry.name,
      manufacturer: entry.manufacturer,
      category: entry.category,
      price: String(entry.price),
      freePriceConfirmed: entry.price === 0 && acknowledged.has(entry.model.toLocaleLowerCase()),
      storage: entry.storage,
      sizeTier: String(entry.size_tier),
      previewDictionary: entry.preview_dictionary ?? "",
      previewTexture: entry.preview_texture ?? "",
      trafficEnabled: entry.traffic.enabled,
      trafficWeight: String(entry.traffic.weight),
    }])),
  };
}

function quickImportReviewPayload(draft: QuickImportEditionDraft): Record<string, unknown> {
  return {
    package_id: draft.packageId,
    name: draft.packageName,
    version: draft.version,
    updates: Object.fromEntries(Object.entries(draft.vehicles).map(([model, entry]) => [model, {
      name: entry.name,
      manufacturer: entry.manufacturer,
      category: entry.category,
      price: Number(entry.price),
      free_price_confirmed: entry.freePriceConfirmed,
      storage: entry.storage,
      size_tier: Number(entry.sizeTier),
      preview_dictionary: entry.previewDictionary,
      preview_texture: entry.previewTexture,
      traffic_enabled: entry.trafficEnabled,
      traffic_weight: Number(entry.trafficWeight),
    }])),
  };
}

function QuickImportWorkspace({
  client,
  result,
  reviews,
  prepared,
  source,
  gtaPath,
  busy,
  preparing,
  error,
  reviewError,
  prepareError,
  navigationNotice,
  onSourceChange,
  onGameChange,
  onInspect,
  onReview,
  onPrepare,
  onDirtyChange,
  onCancel,
}: {
  client: DesktopClient;
  result: VehicleQuickImportResult | null;
  reviews: Record<string, VehicleQuickImportReviewResult>;
  prepared: VehicleQuickImportPreparedResult | null;
  source: string;
  gtaPath: string;
  busy: boolean;
  preparing: boolean;
  error: string;
  reviewError: string;
  prepareError: string;
  navigationNotice: string;
  onSourceChange: (source: string) => void;
  onGameChange: (path: string) => void;
  onInspect: (source: string, gtaPath: string, preferredEdition: string | null) => void;
  onReview: (source: string, gtaPath: string, edition: string, draft: QuickImportEditionDraft | null) => void;
  onPrepare: (source: string, gtaPath: string, edition: string, draft: QuickImportEditionDraft, review: VehicleQuickImportReviewResult) => void;
  onDirtyChange: (guarded: boolean, draftDirty: boolean) => void;
  onCancel: () => void;
}) {
  const [preferredEdition, setPreferredEdition] = useState("auto");
  const [selectedEdition, setSelectedEdition] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [drafts, setDrafts] = useState<Record<string, QuickImportEditionDraft>>({});
  const [confirmingPrepare, setConfirmingPrepare] = useState(false);
  const [oivGuarded, setOivGuarded] = useState(false);
  const [zipGuarded, setZipGuarded] = useState(false);
  const seenReviews = useRef<Record<string, VehicleQuickImportReviewResult>>({});
  const vehicles = safeRows(result?.vehicles);
  const editions = result?.available_editions ?? [];
  const review = reviews[selectedEdition];
  const draft = drafts[selectedEdition];
  const reviewedVehicles = review?.plan.catalog.vehicles ?? [];
  const inspectionVehicles = selectedEdition
    ? vehicles.filter((vehicle) => String(vehicle.edition ?? "").toLocaleLowerCase() === selectedEdition)
    : vehicles;
  const visibleVehicles: (Record<string, unknown> | VehicleQuickImportCatalogEntry)[] = reviewedVehicles.length
    ? reviewedVehicles
    : inspectionVehicles;
  const selectedDraft = draft?.vehicles[selectedModel.toLocaleLowerCase()];
  const baselineDraft = review ? quickImportDraftFromReview(review) : null;
  const dirty = Boolean(draft && baselineDraft && JSON.stringify(draft) !== JSON.stringify(baselineDraft));
  const anyDirty = Object.entries(drafts).some(([edition, candidate]) => {
    const cached = reviews[edition];
    return Boolean(cached && JSON.stringify(candidate) !== JSON.stringify(quickImportDraftFromReview(cached)));
  });
  const locked = busy || preparing || oivGuarded || zipGuarded;

  useEffect(() => {
    const nextEdition = result?.suggested_edition?.toLocaleLowerCase() ?? "";
    setSelectedEdition(nextEdition);
    const first = vehicles.find((vehicle) => String(vehicle.edition ?? "").toLocaleLowerCase() === nextEdition) ?? vehicles[0];
    setSelectedModel(String(first?.model ?? "").toLocaleLowerCase());
    setDrafts({});
    setConfirmingPrepare(false);
    seenReviews.current = {};
  }, [result, source]);

  useEffect(() => {
    if (!review || !selectedEdition || seenReviews.current[selectedEdition] === review) return;
    seenReviews.current[selectedEdition] = review;
    const canonical = quickImportDraftFromReview(review);
    setDrafts((current) => ({ ...current, [selectedEdition]: canonical }));
    if (!canonical.vehicles[selectedModel]) {
      setSelectedModel(Object.keys(canonical.vehicles)[0] ?? "");
    }
  }, [review, selectedEdition, selectedModel]);

  useEffect(() => {
    onDirtyChange(anyDirty || oivGuarded || zipGuarded, anyDirty);
  }, [anyDirty, oivGuarded, zipGuarded, onDirtyChange]);

  useEffect(() => {
    if (dirty || !review) setConfirmingPrepare(false);
  }, [dirty, review]);

  const chooseArchive = async () => {
    const selected = await client.selectPath("vehicle_import_source");
    if (selected) onSourceChange(selected);
  };
  const chooseFolder = async () => {
    const selected = await client.selectPath("vehicle_import_folder");
    if (selected) onSourceChange(selected);
  };
  const chooseGame = async () => {
    const selected = await client.selectPath("gta_folder");
    if (selected) onGameChange(selected);
  };
  const inspect = () => onInspect(
    source,
    gtaPath,
    preferredEdition === "auto" ? null : preferredEdition,
  );
  const selectEdition = (edition: string) => {
    const normalized = edition.toLocaleLowerCase();
    setSelectedEdition(normalized);
    const reviewed = reviews[normalized]?.plan.catalog.vehicles[0];
    const inspected = vehicles.find((vehicle) => String(vehicle.edition ?? "").toLocaleLowerCase() === normalized);
    setSelectedModel(String(reviewed?.model ?? inspected?.model ?? "").toLocaleLowerCase());
  };
  const updateDraft = (update: (current: QuickImportEditionDraft) => QuickImportEditionDraft) => {
    if (!selectedEdition) return;
    setDrafts((current) => {
      const active = current[selectedEdition];
      return active ? { ...current, [selectedEdition]: update(active) } : current;
    });
  };
  const updateSelectedVehicle = (changes: Partial<QuickImportVehicleDraft>) => {
    if (!selectedModel) return;
    updateDraft((current) => ({
      ...current,
      vehicles: {
        ...current.vehicles,
        [selectedModel]: { ...current.vehicles[selectedModel], ...changes },
      },
    }));
  };
  const updateCategory = (category: string) => updateSelectedVehicle({
    category,
    storage: storageForVehicleCategory(category),
    trafficEnabled: QUICK_IMPORT_ROAD_CATEGORIES.has(category) ? selectedDraft?.trafficEnabled ?? false : false,
  });
  const resetDraft = () => {
    if (!review) return;
    setDrafts((current) => ({ ...current, [selectedEdition]: quickImportDraftFromReview(review) }));
  };
  const reviewDraft = () => onReview(source, gtaPath, selectedEdition, draft ?? null);
  const sourceState = preparing
    ? "Preparing the validated Launcher package…"
    : busy
    ? review ? "Revalidating the selected draft…" : "Inspecting vehicle package branches…"
    : result
      ? "Read-only inspection complete"
      : source
        ? "Source ready for inspection"
        : "Choose a vehicle package to begin";

  return (
    <section className="workspace-section quick-import-workspace" aria-labelledby="quick-import-title">
      <div className="workspace-heading">
        <div>
          <span className="eyebrow">Guided vehicle intake</span>
          <h2 id="quick-import-title">Quick Import</h2>
          <p>Identify safe Legacy and Enhanced vehicle branches before creating any Launcher package.</p>
        </div>
        <div className="heading-actions">
          <button className="primary-button" onClick={chooseArchive} disabled={locked || anyDirty} title={anyDirty ? "Validate or reset every changed draft first" : undefined}>Open archive</button>
          <button className="quiet-button" onClick={chooseFolder} disabled={locked || anyDirty} title={anyDirty ? "Validate or reset every changed draft first" : undefined}>Open folder</button>
          <button className="quiet-button" onClick={inspect} disabled={!source || locked || anyDirty} title={anyDirty ? "Validate or reset every changed draft first" : undefined}>{result ? "Refresh" : "Inspect source"}</button>
          {result && selectedEdition && <button className="quiet-button" onClick={reviewDraft} disabled={locked}>{review ? "Revalidate draft" : "Build draft"}</button>}
          {busy && <button className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>
      </div>

      <div className="source-strip" aria-live="polite">
        <span className={`activity-dot ${busy ? "busy" : result ? "ready" : ""}`} />
        <strong>{sourceState}</strong>
        <span className="source-path" title={source}>{source || "No vehicle source selected"}</span>
      </div>

      <div className="quick-import-config" aria-label="Quick Import inspection settings">
        <div className="form-field">
          <label htmlFor="quick-import-gta-path">GTA installation</label>
          <div className="input-action"><input id="quick-import-gta-path" value={gtaPath} readOnly placeholder="Use the detected installation when empty" /><button type="button" onClick={chooseGame} disabled={locked || anyDirty}>Browse</button></div>
        </div>
        <div className="form-field">
          <label htmlFor="quick-import-edition">Preferred edition</label>
          <select id="quick-import-edition" value={preferredEdition} onChange={(event) => setPreferredEdition(event.target.value)} disabled={locked}>
            <option value="auto">Auto-detect</option>
            <option value="legacy">Legacy</option>
            <option value="enhanced">Enhanced</option>
          </select>
        </div>
      </div>

      {error && <div className="error-banner" role="alert">Quick Import inspection failed: {error}</div>}
      {reviewError && <div className="error-banner" role="alert">Draft review failed: {reviewError}</div>}
      {prepareError && <div className="error-banner" role="alert">Package preparation failed: {prepareError}</div>}
      {navigationNotice && <div className="action-notice" role="status">{navigationNotice}</div>}
      {result && <div className="summary-row quick-import-summary" aria-label="Quick Import inspection summary">
        <StatusPill tone="success">Read only</StatusPill>
        {review && <StatusPill tone={dirty ? "warning" : "success"}>{dirty ? "Draft changed" : "Draft validated"}</StatusPill>}
        <span><strong>{result.branch_count}</strong> branches</span>
        <span><strong>{result.vehicle_count}</strong> vehicles</span>
        <span><strong>{result.errors}</strong> errors</span>
        <span><strong>{review?.warning_count ?? result.warnings}</strong> warnings</span>
        <span>Suggested <strong>{formatReadiness(result.suggested_edition)}</strong></span>
      </div>}

      <div className={`panel-grid quick-import-grid ${result ? "has-result" : "is-empty"}`}>
        <section className="pane quick-import-branches-pane" aria-label="Detected vehicle branches">
          <div className="pane-header"><div><span className="pane-kicker">Package targets</span><h3>Detected branches</h3></div><span className="pane-count">{editions.length}</span></div>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>No branches inspected</strong><p>Open an archive or folder to identify bounded Legacy and Enhanced vehicle branches.</p><button className="text-action" onClick={chooseArchive}>Choose archive</button></div>}
            {result && !editions.length && <div className="pane-empty compact-empty"><strong>No compatible branch</strong><p>This source did not expose a complete vehicle and RPF pairing.</p></div>}
            {editions.map((edition) => {
              const normalized = edition.toLocaleLowerCase();
              const count = vehicles.filter((vehicle) => String(vehicle.edition ?? "").toLocaleLowerCase() === normalized).length;
              return <button key={edition} disabled={locked} className={`data-row ${selectedEdition === normalized ? "selected" : ""}`} onClick={() => selectEdition(edition)} aria-pressed={selectedEdition === normalized}><span className="row-type" aria-hidden="true">{normalized === "enhanced" ? "E" : "L"}</span><span><strong>{formatReadiness(edition)}</strong><small>{count} discovered {count === 1 ? "vehicle" : "vehicles"}</small></span><span className="row-state valid">Available</span></button>;
            })}
          </div>
        </section>

        <section className="pane quick-import-vehicles-pane" aria-label="Discovered vehicles">
          <div className="pane-header"><div><span className="pane-kicker">Package metadata</span><h3>Discovered vehicles</h3></div><span className="pane-count">{visibleVehicles.length}</span></div>
          <div className="row-list">
            {!result && <div className="pane-empty"><strong>Waiting for inspection</strong><p>Model identifiers and available metadata will appear here by edition.</p></div>}
            {result && !visibleVehicles.length && <div className="pane-empty compact-empty"><strong>No vehicles in this branch</strong><p>Select another detected branch or inspect a different source.</p></div>}
            {visibleVehicles.map((vehicle, index) => {
              const model = String(vehicle.model ?? "").toLocaleLowerCase();
              return <button key={`${selectedEdition}-${model}-${index}`} className={`data-row ${selectedModel === model ? "selected" : ""}`} onClick={() => setSelectedModel(model)} aria-pressed={selectedModel === model}><span className="row-type" aria-hidden="true">V</span><span><strong>{String(vehicle.name || vehicle.display_name || vehicle.model || "Unnamed vehicle")}</strong><small>{String(vehicle.manufacturer || "Unknown manufacturer")} · {String(vehicle.model || "No model id")}</small></span>{review && <span className="row-state valid">Draft</span>}</button>;
            })}
          </div>
        </section>

        <aside className="pane quick-import-detail-pane" aria-label="Selected vehicle evidence">
          <div className="pane-header"><div><span className="pane-kicker">Validated draft</span><h3>{review ? "Listing review" : "Selected vehicle"}</h3></div>{dirty && <span className="pane-count">Edited</span>}</div>
          {!result && <div className="pane-empty inspector-empty"><strong>No vehicle selected</strong><p>Choose a detected vehicle to inspect its source metadata.</p></div>}
          {result && !review && <div className="pane-empty inspector-empty"><strong>Draft not built</strong><p>Generate the selected edition’s inferred package and storefront listing without writing any files.</p><button className="text-action" onClick={reviewDraft} disabled={locked}>Build {formatReadiness(selectedEdition)} draft</button></div>}
          {review && selectedDraft && draft && <form className="quick-import-editor" onSubmit={(event) => { event.preventDefault(); reviewDraft(); }}>
            <fieldset>
              <legend>Package identity</legend>
              <label htmlFor="quick-package-id">Package ID</label>
              <input id="quick-package-id" value={draft.packageId} onChange={(event) => updateDraft((current) => ({ ...current, packageId: event.target.value }))} disabled={locked} />
              <label htmlFor="quick-package-name">Package name</label>
              <input id="quick-package-name" value={draft.packageName} onChange={(event) => updateDraft((current) => ({ ...current, packageName: event.target.value }))} disabled={locked} />
              <label htmlFor="quick-package-version">Version</label>
              <input id="quick-package-version" value={draft.version} onChange={(event) => updateDraft((current) => ({ ...current, version: event.target.value }))} disabled={locked} />
            </fieldset>
            <fieldset>
              <legend>{selectedDraft.model} listing</legend>
              <label htmlFor="quick-listing-name">Display name</label>
              <input id="quick-listing-name" value={selectedDraft.name} onChange={(event) => updateSelectedVehicle({ name: event.target.value })} disabled={locked} />
              <label htmlFor="quick-listing-manufacturer">Manufacturer</label>
              <input id="quick-listing-manufacturer" value={selectedDraft.manufacturer} onChange={(event) => updateSelectedVehicle({ manufacturer: event.target.value })} disabled={locked} />
              <label htmlFor="quick-listing-price">GBAY price</label>
              <input id="quick-listing-price" inputMode="numeric" value={selectedDraft.price} onChange={(event) => updateSelectedVehicle({ price: event.target.value, freePriceConfirmed: event.target.value.trim() === "0" ? selectedDraft.freePriceConfirmed : false })} disabled={locked} />
              <label className="quick-import-check"><input type="checkbox" checked={selectedDraft.freePriceConfirmed} onChange={(event) => updateSelectedVehicle({ freePriceConfirmed: event.target.checked })} disabled={locked || selectedDraft.price.trim() !== "0"} /><span>This vehicle is intentionally free</span></label>
              <label htmlFor="quick-listing-category">Category</label>
              <select id="quick-listing-category" value={selectedDraft.category} onChange={(event) => updateCategory(event.target.value)} disabled={locked}>{QUICK_IMPORT_CATEGORIES.map((category) => <option key={category} value={category}>{formatReadiness(category)}</option>)}</select>
              <label htmlFor="quick-listing-storage">Storage</label>
              <select id="quick-listing-storage" value={selectedDraft.storage} onChange={(event) => updateSelectedVehicle({ storage: event.target.value })} disabled={locked}><option value="garage">Garage</option><option value="harbour">Harbour</option><option value="helipad">Helipad</option><option value="hangar">Hangar</option></select>
              <label htmlFor="quick-listing-size">Vehicle size</label>
              <select id="quick-listing-size" value={selectedDraft.sizeTier} onChange={(event) => updateSelectedVehicle({ sizeTier: event.target.value })} disabled={locked}><option value="0">Standard</option><option value="1">Large</option><option value="2">Oversize</option></select>
              <label className="quick-import-check"><input type="checkbox" checked={selectedDraft.trafficEnabled} onChange={(event) => updateSelectedVehicle({ trafficEnabled: event.target.checked })} disabled={locked || !QUICK_IMPORT_ROAD_CATEGORIES.has(selectedDraft.category)} /><span>Offer for ambient traffic</span></label>
              <label htmlFor="quick-preview-dictionary">Preview dictionary</label>
              <input id="quick-preview-dictionary" value={selectedDraft.previewDictionary} onChange={(event) => updateSelectedVehicle({ previewDictionary: event.target.value })} disabled={locked} placeholder="Use the Launcher placeholder when empty" />
              <label htmlFor="quick-preview-texture">Preview texture</label>
              <input id="quick-preview-texture" value={selectedDraft.previewTexture} onChange={(event) => updateSelectedVehicle({ previewTexture: event.target.value })} disabled={locked} placeholder="Exact existing texture name" />
            </fieldset>
            <div className="quick-import-review-notes">
              <strong>{review.warning_count ? `${review.warning_count} review ${review.warning_count === 1 ? "warning" : "warnings"}` : "No storefront warnings"}</strong>
              {review.warnings.map((warning, index) => <span key={`${warning}-${index}`}>{warning}</span>)}
              <small>Destination preview: {review.destination_preview}</small>
            </div>
            <div className="quick-import-editor-actions">
              <button type="button" className="quiet-button" onClick={resetDraft} disabled={!dirty || locked}>Reset draft</button>
              <div>
                <button type="submit" className="quiet-button" disabled={locked}>{dirty ? "Validate changes" : "Revalidate draft"}</button>
                <button type="button" className="primary-button" disabled={locked || dirty || !review.destination_review.replaceable} title={!review.destination_review.replaceable ? review.destination_review.message : undefined} onClick={() => setConfirmingPrepare(true)}>Prepare for Launcher</button>
              </div>
            </div>
          </form>}
        </aside>
      </div>

      {prepared && <div className="quick-import-complete" role="status"><div><StatusPill tone="success">Package ready</StatusPill><strong>{prepared.replaced_existing ? "Managed package replaced" : "Launcher package created"}</strong></div><span className="source-path" title={prepared.package.package_root}>{prepared.package.package_root}</span><small>GTA was not modified. Open the package in ALLIN1 Launcher to review trust and installation.</small></div>}
      <div className="quick-import-safety"><strong>Guarded authoring</strong><span>Preparation writes only to the per-user Launcher package library. Python rechecks the reviewed digest and source evidence, and only intact SDK-managed packages can be replaced. GTA installation remains a separate Launcher action.</span></div>
      {result && <QuickImportPublish key={`${prepared?.package.package_root ?? ""}-${prepared?.review_sha256 ?? ""}`} client={client}
        sourcePackage={prepared?.package.package_root ?? ""} gtaPath={gtaPath}
        disabled={busy || preparing || anyDirty || confirmingPrepare || oivGuarded} onGuardChange={setZipGuarded} />}
      {result && <LegacyOivExport key={`${source}-${gtaPath}-${selectedEdition}`} client={client} source={source} gtaPath={gtaPath} edition={selectedEdition}
        identity={review ? { package_id: review.plan.package_id, name: review.plan.name, version: review.plan.version } : undefined}
        disabled={busy || preparing || anyDirty || confirmingPrepare || zipGuarded} onGuardChange={setOivGuarded} />}
      {confirmingPrepare && review && draft && <AuthoringConfirmation
        title={review.destination_review.exists ? "Replace managed Launcher package?" : "Create Launcher package?"}
        description="The SDK will revalidate this exact draft before writing. This operation cannot be cancelled after it begins."
        details={[
          { label: "Package", value: `${review.plan.name} (${review.plan.package_id})` },
          { label: "Edition", value: formatReadiness(review.plan.edition) },
          { label: "Source", value: source },
          { label: "Destination", value: review.destination_preview },
          { label: "Action", value: review.destination_review.exists ? "Atomic replacement of an SDK-managed package" : "Create a new Launcher package" },
        ]}
        warning={review.destination_review.exists ? "The existing managed package will be replaced after its ownership evidence is checked again." : undefined}
        confirmLabel={review.destination_review.exists ? "Replace package" : "Create package"}
        onCancel={() => setConfirmingPrepare(false)}
        onConfirm={() => {
          setConfirmingPrepare(false);
          onPrepare(source, gtaPath, selectedEdition, draft, review);
        }}
      />}
    </section>
  );
}

function RpfInspector({
  client,
  result,
  source,
  gtaPath,
  busy,
  activeJob,
  error,
  onSourceChange,
  onGameChange,
  onInspect,
  onCancel,
  onJob,
  onOpenGameText,
  onOpenBinary,
  onStageMember,
  onUtilityGuardChange,
}: {
  client: DesktopClient;
  result: RpfArchiveResult | null;
  source: string;
  gtaPath: string;
  busy: boolean;
  activeJob: string | null;
  error: string;
  onSourceChange: (source: string) => void;
  onGameChange: (path: string) => void;
  onInspect: (source: string, gtaPath: string) => void;
  onCancel: () => void;
  onJob: (jobId: string | null) => void;
  onOpenBinary: (request: Omit<Gxt2ArchiveRequest, "requestId">) => void;
  onOpenGameText: (request: Omit<Gxt2ArchiveRequest, "requestId">) => void;
  onStageMember: (request: Omit<RpfChangeRequest, "requestId">) => void;
  onUtilityGuardChange: (guarded: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [selectedArchive, setSelectedArchive] = useState("__all__");
  const [selectedEntry, setSelectedEntry] = useState<RpfEntryRecord | null>(null);
  const [preview, setPreview] = useState<AssetPreviewResult | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const latestPreviewRevision = useRef("");
  const completedPreviewRevision = useRef("");
  const previewJob = useRef<string | null>(null);
  const archives = result?.archives ?? [];
  const entries = result?.entries ?? [];
  const archiveCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of entries) {
      counts.set(entry.archive_path, (counts.get(entry.archive_path) ?? 0) + 1);
    }
    return counts;
  }, [entries]);
  const filteredEntries = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return entries.filter((entry) => (
      (selectedArchive === "__all__" || entry.archive_path === selectedArchive)
      && (kind === "all" || entry.kind === kind)
      && (!needle || `${entry.archive_path} ${entry.path}`.toLocaleLowerCase().includes(needle))
    ));
  }, [entries, kind, query, selectedArchive]);
  const visibleEntries = filteredEntries.slice(0, 500);

  useEffect(() => {
    latestPreviewRevision.current = `rpf-preview-reset-${Date.now()}`;
    if (previewJob.current) void client.cancelJob(previewJob.current);
    previewJob.current = null;
    setQuery("");
    setKind("all");
    setSelectedArchive("__all__");
    setSelectedEntry(null);
    setPreview(null);
    setPreviewError("");
    setPreviewBusy(false);
  }, [client, result?.source]);

  const chooseArchive = async () => {
    const path = await client.selectPath("rpf");
    if (path) onSourceChange(path);
  };
  const chooseGame = async () => {
    const path = await client.selectPath("gta_folder");
    if (path) onGameChange(path);
  };

  const loadPreview = async (entry: RpfEntryRecord) => {
    setSelectedEntry(entry);
    setPreview(null);
    setPreviewError("");
    if (entry.kind === "directory" || !result) {
      setPreviewBusy(false);
      return;
    }
    const revision = `rpf-preview-${Date.now()}-${entry.id}`;
    latestPreviewRevision.current = revision;
    setPreviewBusy(true);
    if (previewJob.current) {
      try {
        await client.cancelJob(previewJob.current);
      } catch {
        // A completed preview can win the cancellation race; revision still gates it.
      }
      previewJob.current = null;
      if (latestPreviewRevision.current !== revision) return;
      onJob(null);
    }
    try {
      const started = await client.startJob(
        "preview_asset",
        {
          source: result.source,
          entry: entry.id,
          edition: result.edition,
          gta_path: result.gta_path,
        },
        revision,
        (message) => {
          if (
            !message.terminal
            || message.payload.revision !== latestPreviewRevision.current
            || revision !== latestPreviewRevision.current
          ) return;
          completedPreviewRevision.current = revision;
          previewJob.current = null;
          setPreviewBusy(false);
          onJob(null);
          if (message.operation === "error") {
            setPreviewError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded) setPreview(loaded as AssetPreviewResult);
        },
      );
      if (
        completedPreviewRevision.current !== revision
        && latestPreviewRevision.current === revision
      ) {
        previewJob.current = started.job_id;
        onJob(started.job_id);
      }
    } catch (reason) {
      if (latestPreviewRevision.current === revision) {
        setPreviewError(String(reason));
        setPreviewBusy(false);
        previewJob.current = null;
        onJob(null);
      }
    }
  };

  const previewArtifactSource = preview?.artifact
    ? preview.artifact.preview_url ?? convertFileSrc(preview.artifact.path)
    : null;
  const sourceState = busy && !result
    ? "Indexing archive…"
    : previewBusy
      ? "Reading selected entry…"
      : result
        ? "Recursive index ready"
        : source
          ? "Archive ready to index"
          : "Choose an archive to begin";
  const entryKindLabel = (entry: RpfEntryRecord) => (
    entry.kind === "directory" ? "DIR"
      : entry.kind === "archive" ? "RPF"
        : entry.kind === "resource" ? "RES" : "BIN"
  );

  return (
    <section className="workspace-section rpf-workspace" aria-labelledby="rpf-title">
      <div className="workspace-heading">
        <div><span className="eyebrow">Read-only archive analysis</span><h2 id="rpf-title">RPF Archives</h2><p>Navigate a recursive Python-owned index and preview one exact archive member at a time.</p></div>
        <div className="heading-actions">
          <button className="primary-button" onClick={chooseArchive} disabled={busy}>Open archive</button>
          <button className="quiet-button" onClick={chooseGame} disabled={busy}>Choose GTA V</button>
          {source && <button className="quiet-button" onClick={() => onInspect(source, gtaPath)} disabled={busy}>Index archive</button>}
          {activeJob && <button className="danger-button" onClick={onCancel}>Cancel</button>}
        </div>
      </div>
      <div className="source-strip" aria-live="polite">
        <span className={`activity-dot ${busy || previewBusy ? "busy" : result ? "ready" : ""}`} />
        <strong>{sourceState}</strong>
        <span className="source-path" title={source}>{source || "No RPF source selected"}</span>
      </div>
      <div className="rpf-context-strip"><strong>GTA context</strong><span title={result?.gta_path || gtaPath}>{result?.gta_path || gtaPath || "Auto-detect from the archive or configured installation"}</span></div>
      {error && <div className="error-banner" role="alert">Archive indexing failed: {error}</div>}
      {previewError && <div className="error-banner" role="alert">Entry preview failed: {previewError}</div>}
      {result && <div className="summary-row rpf-summary" aria-label="RPF archive summary">
        <StatusPill tone="neutral">read only</StatusPill>
        <span><strong>{result.archive_count}</strong> archive layers</span>
        <span><strong>{result.entry_count}</strong> indexed entries</span>
        <span><strong>{formatBytes(result.logical_bytes)}</strong> logical</span>
        <span><strong>{formatBytes(result.stored_bytes)}</strong> stored</span>
        <span><strong>{formatReadiness(result.edition)}</strong> edition</span>
        {result.truncated && <StatusPill tone="warning">index bounded</StatusPill>}
      </div>}

      <div className={`panel-grid rpf-grid ${result ? "has-result" : "is-empty"}`}>
        <section className="pane rpf-archives-pane" aria-label="Archive layers">
          <div className="pane-header"><div><span className="pane-kicker">Container map</span><h3>Archive layers</h3></div><span className="pane-count">{archives.length}</span></div>
          <div className="row-list rpf-archive-rows">
            {!result && <div className="pane-empty"><strong>No archive indexed</strong><p>Open a loose RPF to map its root and nested archive layers.</p><button className="text-action" onClick={chooseArchive}>Choose archive</button></div>}
            {result && <button type="button" className={`data-row rpf-archive-row ${selectedArchive === "__all__" ? "selected" : ""}`} onClick={() => setSelectedArchive("__all__")} aria-pressed={selectedArchive === "__all__"}><span className="row-type">ALL</span><span><strong>All archive layers</strong><small>Search the complete recursive index</small></span><span className="row-state">{entries.length}</span></button>}
            {archives.map((archive) => {
              const archivePath = archive.path || "Root archive";
              return <button type="button" key={archive.path || "root"} className={`data-row rpf-archive-row ${selectedArchive === archive.path ? "selected" : ""}`} onClick={() => setSelectedArchive(archive.path)} aria-pressed={selectedArchive === archive.path}><span className="row-type">RPF</span><span><strong title={archivePath}>{archive.name}</strong><small>{archivePath} · {archive.encryption}</small></span><span className="row-state">{archiveCounts.get(archive.path) ?? 0}</span></button>;
            })}
          </div>
          {result?.warnings.length ? <div className="preview-notes">{result.warnings.map((warning, index) => <span key={`${warning}-${index}`}>{warning}</span>)}</div> : null}
        </section>

        <section className="pane rpf-entries-pane" aria-label="Archive entries">
          <div className="pane-header"><div><span className="pane-kicker">Recursive index</span><h3>Entries</h3></div><span className="pane-count">{filteredEntries.length}/{entries.length}</span></div>
          <div className="asset-filter-bar rpf-filter-bar">
            <label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Filter RPF entries</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter archive paths" disabled={!entries.length} /></label>
            <label><span className="sr-only">Entry kind</span><select value={kind} onChange={(event) => setKind(event.target.value)} disabled={!entries.length}><option value="all">All entry types</option><option value="directory">Directories</option><option value="archive">Nested archives</option><option value="resource">Resources</option><option value="binary">Binary files</option></select></label>
          </div>
          <div className="row-list rpf-entry-rows">
            {!result && <div className="pane-empty"><strong>Waiting for an index</strong><p>Recursive paths and storage evidence will appear here.</p></div>}
            {result && !filteredEntries.length && <p className="empty-copy">No archive entries match this filter.</p>}
            {visibleEntries.map((entry) => <button type="button" key={entry.id} className={`data-row rpf-entry-row ${selectedEntry?.id === entry.id ? "selected" : ""}`} onClick={() => void loadPreview(entry)} aria-pressed={selectedEntry?.id === entry.id}><span className="row-type">{entryKindLabel(entry)}</span><span><strong title={entry.path}>{entry.path}</strong><small>{entry.archive_path || "Root archive"} · {formatBytes(entry.size)}</small></span><span className="row-state">{entry.compressed ? "Packed" : entry.kind === "directory" ? "Folder" : "Stored"}</span></button>)}
            {filteredEntries.length > visibleEntries.length && <p className="empty-copy">Showing the first {visibleEntries.length} matching entries. Refine the path filter to narrow the list.</p>}
          </div>
        </section>

        <aside className="pane rpf-evidence-pane" aria-label="RPF entry evidence">
          <div className="pane-header"><div><span className="pane-kicker">Inspector</span><h3>{selectedEntry ? selectedEntry.name : "Entry evidence"}</h3></div>{preview && <StatusPill tone={preview.truncated ? "warning" : "success"}>{preview.display_kind}</StatusPill>}</div>
          {!selectedEntry && <div className="pane-empty inspector-empty"><strong>No entry selected</strong><p>Choose a recursive index row to inspect exact metadata and request a bounded preview.</p></div>}
          {selectedEntry && <div className="rpf-evidence-body" aria-live="polite">
            {result && selectedEntry.kind !== "directory" && /\.gxt2$/i.test(selectedEntry.name) && <div className="gxt-actions">
              <button className="primary-button" disabled={busy || previewBusy || selectedEntry.size < 16 || selectedEntry.size > 128 * 1024 * 1024}
                onClick={() => onOpenGameText({ archive: result.source, entry_id: selectedEntry.id, gta_path: result.gta_path })}>Open in text editor</button>
              <small>{selectedEntry.size < 16 || selectedEntry.size > 128 * 1024 * 1024 ? "Dictionary size is outside the supported 16-byte–128-MiB range." : "Opens read-only. Editing requires a separately confirmed workspace copy."}</small>
            </div>}
            {result && selectedEntry.kind !== "directory" && <button className="quiet-button" disabled={busy || previewBusy || selectedEntry.size < 1 || selectedEntry.size > 128 * 1024 * 1024}
              onClick={() => onOpenBinary({ archive: result.source, entry_id: selectedEntry.id, gta_path: result.gta_path })}>Open in binary editor</button>}
            {previewBusy && <div className="preview-progress"><span className="activity-dot busy" /><strong>Reading exact entry</strong><p>The sidecar is re-indexing and extracting through RpfPatcher.</p></div>}
            {selectedEntry.kind === "directory" && <div className="pane-empty preview-empty"><strong>Directory metadata</strong><p>Directories are navigational records and are never extracted as preview assets.</p></div>}
            {preview?.display_kind === "image" && preview.artifact && previewArtifactSource && <figure className="image-preview rpf-image-preview"><div><img src={previewArtifactSource} alt={`Read-only preview of ${selectedEntry.path}`} /></div><figcaption><span>Normalized preview</span><span>{formatBytes(preview.artifact.size)} · SHA-256 {preview.artifact.sha256.slice(0, 12)}…</span></figcaption></figure>}
            {preview?.display_kind === "text" && <pre className="asset-text-preview rpf-text-preview">{preview.text || "(empty preview)"}</pre>}
            {preview?.display_kind === "metadata" && <div className="pane-empty preview-empty"><strong>Metadata only</strong><p>No renderable artifact was produced for this entry.</p></div>}
            <dl className="detail-list rpf-entry-details">
              <div><dt>Virtual path</dt><dd>{selectedEntry.path}</dd></div>
              <div><dt>Archive layer</dt><dd>{selectedEntry.archive_path || "Root archive"}</dd></div>
              <div><dt>Kind</dt><dd>{formatReadiness(selectedEntry.kind)}</dd></div>
              <div><dt>Logical size</dt><dd>{formatBytes(selectedEntry.size)}</dd></div>
              <div><dt>Stored size</dt><dd>{formatBytes(selectedEntry.stored_size)}</dd></div>
              <div><dt>Compressed</dt><dd>{selectedEntry.compressed === null ? "Not reported" : selectedEntry.compressed ? "Yes" : "No"}</dd></div>
              <div><dt>Encrypted</dt><dd>{selectedEntry.encrypted === null ? "Not reported" : selectedEntry.encrypted ? "Yes" : "No"}</dd></div>
              {selectedEntry.resource_version !== null && <div><dt>Resource version</dt><dd>{selectedEntry.resource_version}</dd></div>}
              {preview && <><div><dt>Bytes read</dt><dd>{formatBytes(preview.bytes_read)}</dd></div><div><dt>SHA-256</dt><dd>{preview.sha256 ?? "Not calculated for a truncated read"}</dd></div></>}
              {preview && Object.entries(preview.metadata).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}
            </dl>
          </div>}
          {result && selectedEntry && <div className="gxt-actions"><button className="quiet-button" disabled={busy || previewBusy} onClick={() => onStageMember({archive: result.source, archive_path: selectedEntry.archive_path, entry: selectedEntry.path, kind: selectedEntry.kind})}>Stage this member</button><small>Capture this exact target in the change-set editor. No archive changes are made.</small></div>}
          {result && <RpfArchiveUtilities client={client} result={result} entry={selectedEntry} disabled={busy || previewBusy} onGuardChange={onUtilityGuardChange} />}
          <div className="recipe-safety-note"><strong>Read-only boundary</strong><span>Indexing and previews never rewrite the archive. Change sets stage and compile plans separately; archive execution remains guarded.</span></div>
        </aside>
      </div>
    </section>
  );
}

function HelpCenter({ topics, initialTopic = "getting-started" }: { topics: HelpTopic[]; initialTopic?: string }) {
  const [query, setQuery] = useState("");
  const topicListRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLElement>(null);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return topics;
    return topics.filter((topic) =>
      [topic.title, topic.summary, topic.body, topic.category, ...topic.keywords]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [query, topics]);
  const [selectedKey, setSelectedKey] = useState(initialTopic);
  const selected = filtered.find((topic) => topic.key === selectedKey) ?? filtered[0];

  useEffect(() => {
    if (topicListRef.current) topicListRef.current.scrollTop = 0;
  }, [query]);
  useEffect(() => {
    if (articleRef.current) articleRef.current.scrollTop = 0;
  }, [selected?.key]);

  return (
    <section className="workspace-section help-layout" aria-labelledby="help-title">
      <div className="workspace-heading full-span">
        <div><span className="eyebrow">SDK reference</span><h2 id="help-title">Help Center</h2><p>Search the same task-oriented topics as the existing desktop.</p></div>
      </div>
      <aside className="help-index">
        <label className="search-field"><span aria-hidden="true">⌕</span><span className="sr-only">Search help</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search help" /></label>
        <div className="help-topic-list" ref={topicListRef} role="listbox" aria-label="Help topics">
          {filtered.map((topic) => <button role="option" aria-selected={topic.key === selected?.key} className={topic.key === selected?.key ? "selected" : ""} key={topic.key} onClick={() => setSelectedKey(topic.key)}><small>{topic.category}</small><strong>{topic.title}</strong><span>{topic.summary}</span></button>)}
        </div>
      </aside>
      <article className="help-article" ref={articleRef} tabIndex={0} aria-label={selected?.title ?? "Help article"}>
        {selected ? <><span className="eyebrow">{selected.category}</span><h3>{selected.title}</h3><p className="help-summary">{selected.summary}</p>{selected.body.split("\n").filter(Boolean).map((paragraph, index) => <p key={index}>{paragraph}</p>)}</> : <p>No help topic matches this search.</p>}
      </article>
    </section>
  );
}

function ConsoleDock({
  client,
  catalog,
  expanded,
  onToggle,
}: {
  client: DesktopClient;
  catalog: DesktopCatalog;
  expanded: boolean;
  onToggle: () => void;
}) {
  const [line, setLine] = useState("");
  const [output, setOutput] = useState("Ready. Type help or an SDK command.");
  const [running, setRunning] = useState(false);
  const [pendingAuthoring, setPendingAuthoring] = useState<{
    line: string;
    command: string;
    args: string[];
    description: string;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const matches = useMemo(() => {
    const prefix = line.trim().split(/\s/, 1)[0].toLocaleLowerCase();
    if (!prefix) return catalog.commands.slice(0, 8);
    return catalog.commands.filter((item) => item.name.includes(prefix)).slice(0, 8);
  }, [catalog.commands, line]);

  useEffect(() => {
    if (expanded) inputRef.current?.focus();
  }, [expanded]);

  const runCommand = async (
    commandLine: string,
    command: string,
    args: string[],
    authoringConfirmed = false,
  ) => {
    try {
      setRunning(true);
      setOutput(`> ${commandLine}\nRunning ${authoringConfirmed ? "confirmed authoring" : "read-only"} command…`);
      const response = authoringConfirmed
        ? await client.execute(command, args, true)
        : await client.execute(command, args);
      setOutput(`> ${commandLine}\n${messageText(response)}`);
      const history = readConsoleHistory();
      localStorage.setItem("allin1.console.history", JSON.stringify([commandLine, ...history.filter((item) => item !== commandLine)].slice(0, 100)));
    } catch (reason) {
      setOutput(`> ${commandLine}\nERROR: ${String(reason)}`);
    } finally {
      setRunning(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      const tokens = tokenizeCommandLine(line);
      if (!tokens.length) return;
      if (tokens[0] === "help") {
        const exact = catalog.commands.find((item) => item.name === tokens[1]);
        setOutput(exact ? `${exact.name}\n${exact.description}\nRisk: ${exact.risk}` : catalog.commands.map((item) => `${item.name.padEnd(36)} ${item.description}`).join("\n"));
        return;
      }
      const known = catalog.commands.find((item) => item.name === tokens[0]);
      if (!known) throw new Error(`Unknown SDK command: ${tokens[0]}`);
      if (known.risk === "game_write") {
        setOutput(`> ${line}\nBLOCKED: Game-write commands are disabled in the Tauri desktop process.`);
        return;
      }
      if (known.risk === "unclassified") {
        setOutput(`> ${line}\nBLOCKED: This command has no reviewed desktop risk classification.`);
        return;
      }
      if (known.risk === "authoring_write") {
        setOutput(`> ${line}\nAwaiting authoring confirmation. No command has run.`);
        setPendingAuthoring({ line, command: tokens[0], args: tokens.slice(1), description: known.description });
        return;
      }
      void runCommand(line, tokens[0], tokens.slice(1));
    } catch (reason) {
      setOutput(`> ${line}\nERROR: ${String(reason)}`);
    }
  };

  return (
    <>
      <section className={`console-dock ${expanded ? "expanded" : ""}`} aria-label="SDK Console">
      <button className="console-handle" onClick={onToggle} aria-expanded={expanded}><span><span className="terminal-mark" aria-hidden="true">›_</span><strong>SDK Console</strong><small>{running ? "Running…" : "Structured command surface"}</small></span><span>{expanded ? "Collapse" : "Expand"}</span></button>
      <form onSubmit={submit} className="console-prompt">
        <span aria-hidden="true">allin1&gt;</span>
        <label className="sr-only" htmlFor="console-input">SDK command</label>
        <input id="console-input" ref={inputRef} value={line} onChange={(event) => setLine(event.target.value)} onKeyDown={(event) => { if (event.key === "ArrowUp" && !line) { const previous = readConsoleHistory()[0]; if (previous) { event.preventDefault(); setLine(previous); } } }} disabled={running} autoComplete="off" placeholder="Type an SDK command" />
        <button disabled={running || !line.trim()}>Run</button>
      </form>
      <div className="console-body" hidden={!expanded}><div className="console-main"><pre>{output}</pre><QwenAssistant client={client} visible={expanded} /></div><aside aria-label="Command suggestions"><button onClick={() => { localStorage.removeItem("allin1.console.history"); setOutput("Console history cleared."); }}><strong>clear-history</strong><span>Remove locally stored command history</span><StatusPill>local</StatusPill></button>{matches.map((item) => <button key={item.name} onClick={() => setLine(item.name)}><strong>{item.name}</strong><span>{item.description}</span><StatusPill>{item.risk.replaceAll("_", " ")}</StatusPill></button>)}</aside></div>
      </section>
      {pendingAuthoring && <AuthoringConfirmation
        title="Run authoring command?"
        description="This command can create or change files outside the SDK interface. Review the exact structured command before continuing."
        details={[
          { label: "Command", value: pendingAuthoring.command },
          { label: "Arguments", value: pendingAuthoring.args.length ? pendingAuthoring.args.join(" ") : "No arguments" },
          { label: "Purpose", value: pendingAuthoring.description },
          { label: "Risk", value: "Authoring write — GTA writes remain disabled" },
        ]}
        confirmLabel="Run command"
        onCancel={() => setPendingAuthoring(null)}
        onConfirm={() => {
          const pending = pendingAuthoring;
          setPendingAuthoring(null);
          void runCommand(pending.line, pending.command, pending.args, true);
        }}
      />}
    </>
  );
}

export default function App({ client = tauriClient }: { client?: DesktopClient }) {
  const [catalog, setCatalog] = useState<DesktopCatalog>(EMPTY_CATALOG);
  const [workspace, setWorkspace] = useState<WorkspaceId>("linker");
  const [helpTopic, setHelpTopic] = useState("getting-started");
  const [history, setHistory] = useState<WorkspaceId[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem("allin1.sidebar.collapsed") === "true");
  const [consoleExpanded, setConsoleExpanded] = useState(() => localStorage.getItem("allin1.console.expanded") === "true");
  const [theme, setTheme] = useState<ThemeMode>(() => (localStorage.getItem("allin1.theme") as ThemeMode | null) ?? "system");
  const [bootError, setBootError] = useState("");
  const [sidecarStatus, setSidecarStatus] = useState("Connecting to SDK services…");
  const [packageResult, setPackageResult] = useState<PackageResult | null>(null);
  const [packageSource, setPackageSource] = useState("");
  const [packageError, setPackageError] = useState("");
  const [recipeResult, setRecipeResult] = useState<RecipePlanResult | null>(null);
  const [recipeSource, setRecipeSource] = useState("");
  const [recipeError, setRecipeError] = useState("");
  const [rpfResult, setRpfResult] = useState<RpfArchiveResult | null>(null);
  const [rpfSource, setRpfSource] = useState("");
  const [rpfGamePath, setRpfGamePath] = useState("");
  const [rpfError, setRpfError] = useState("");
  const [graphVehicleRequest, setGraphVehicleRequest] = useState<{ source: string; model: string } | null>(null);
  const [modelMaterialSource, setModelMaterialSource] = useState("");
  const [vehicleProjectResult, setVehicleProjectResult] = useState<VehicleProjectResult | null>(null);
  const [vehicleProjectSource, setVehicleProjectSource] = useState("");
  const [vehicleProjectGamePath, setVehicleProjectGamePath] = useState("");
  const [vehicleProjectCategory, setVehicleProjectCategory] = useState<WorkbenchCategory>("vehicles");
  const [vehicleProjectError, setVehicleProjectError] = useState("");
  const [receiptResult, setReceiptResult] = useState<PackageReceiptResult | null>(null);
  const [receiptGamePath, setReceiptGamePath] = useState("");
  const [receiptError, setReceiptError] = useState("");
  const [lifecycleReview, setLifecycleReview] = useState<PackageLifecycleReviewResult | null>(null);
  const [lifecycleExecution, setLifecycleExecution] = useState<PackageLifecycleExecutionResult | null>(null);
  const [lifecycleExecuting, setLifecycleExecuting] = useState(false);
  const [lifecycleError, setLifecycleError] = useState("");
  const [quickImportResult, setQuickImportResult] = useState<VehicleQuickImportResult | null>(null);
  const [quickImportSource, setQuickImportSource] = useState("");
  const [quickImportGamePath, setQuickImportGamePath] = useState("");
  const [quickImportError, setQuickImportError] = useState("");
  const [quickImportReviews, setQuickImportReviews] = useState<Record<string, VehicleQuickImportReviewResult>>({});
  const [quickImportReviewError, setQuickImportReviewError] = useState("");
  const [quickImportPrepared, setQuickImportPrepared] = useState<VehicleQuickImportPreparedResult | null>(null);
  const [quickImportPrepareError, setQuickImportPrepareError] = useState("");
  const [quickImportPreparing, setQuickImportPreparing] = useState(false);
  const [quickImportDirty, setQuickImportDirty] = useState(false);
  const [quickImportNavigationNotice, setQuickImportNavigationNotice] = useState("");
  const [vehicleAuthoringDirty, setVehicleAuthoringDirty] = useState(false);
  const [rpfMode, setRpfMode] = useState<"archive" | "text" | "binary" | "graph" | "program" | "changes" | "transactions">("archive");
  const [graphLaunchSource, setGraphLaunchSource] = useState("");
  const [changeRequest, setChangeRequest] = useState<RpfChangeRequest | null>(null);
  const [changeGuarded, setChangeGuarded] = useState(false);
  const [transactionGuarded, setTransactionGuarded] = useState(false);
  const [utilityGuarded, setUtilityGuarded] = useState(false);
  const [gxtArchiveRequest, setGxtArchiveRequest] = useState<Gxt2ArchiveRequest | null>(null);
  const [gxtGuarded, setGxtGuarded] = useState(false);
  const [binaryGuarded, setBinaryGuarded] = useState(false);
  const [binaryArchiveRequest, setBinaryArchiveRequest] = useState<Gxt2ArchiveRequest | null>(null);
  const [graphGuarded, setGraphGuarded] = useState(false);
  const [programGuarded, setProgramGuarded] = useState(false);
  const [modelsGuarded, setModelsGuarded] = useState(false);
  const [modelsNavigationNotice, setModelsNavigationNotice] = useState("");
  useEffect(() => { if (!modelsGuarded) setModelsNavigationNotice(""); }, [modelsGuarded]);
  const [recipeGuarded, setRecipeGuarded] = useState(false);
  const [dataToolsGuarded, setDataToolsGuarded] = useState(false);
  const [dataToolsNotice, setDataToolsNotice] = useState("");
  const [recipeConverting, setRecipeConverting] = useState(false);
  const [recipeNavigationNotice, setRecipeNavigationNotice] = useState("");
  useEffect(() => { if (!recipeGuarded) setRecipeNavigationNotice(""); }, [recipeGuarded]);
  const rpfGuarded = gxtGuarded || binaryGuarded || graphGuarded || programGuarded || changeGuarded || transactionGuarded || utilityGuarded;
  const [gxtNavigationNotice, setGxtNavigationNotice] = useState("");
  useEffect(() => { if (workspace !== "rpf") { setGxtArchiveRequest(null); setBinaryArchiveRequest(null); setChangeRequest(null); } }, [workspace]);
  const [vehicleAuthoringNavigationNotice, setVehicleAuthoringNavigationNotice] = useState("");
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const lifecycleGuarded = quickImportDirty || quickImportPreparing || vehicleAuthoringDirty
    || rpfGuarded || modelsGuarded || recipeGuarded || dataToolsGuarded || lifecycleExecuting || Boolean(lifecycleReview);
  const desktopLifecycle = useDesktopLifecycle(client, lifecycleGuarded);
  const [update, setUpdate] = useState<UpdateResult | null>(null);
  const [updateError, setUpdateError] = useState("");
  const headingRef = useRef<HTMLDivElement>(null);
  const latestRevision = useRef("");
  const completedRevision = useRef("");
  const latestRecipeRevision = useRef("");
  const completedRecipeRevision = useRef("");
  const recipeStarting = useRef(false);
  const latestRpfRevision = useRef("");
  const completedRpfRevision = useRef("");
  const rpfStarting = useRef(false);
  const latestVehicleProjectRevision = useRef("");
  const completedVehicleProjectRevision = useRef("");
  const vehicleProjectStarting = useRef(false);
  const latestReceiptRevision = useRef("");
  const completedReceiptRevision = useRef("");
  const receiptStarting = useRef(false);
  const latestLifecycleRevision = useRef("");
  const completedLifecycleRevision = useRef("");
  const lifecycleStarting = useRef(false);
  const latestQuickImportRevision = useRef("");
  const completedQuickImportRevision = useRef("");
  const quickImportStarting = useRef(false);
  const latestQuickImportReviewRevision = useRef("");
  const completedQuickImportReviewRevision = useRef("");
  const quickImportReviewStarting = useRef(false);

  const navigate = useCallback((target: WorkspaceId, remember = true) => {
    setWorkspace((current) => {
      if (current === target) return current;
      if (current === "data_tools" && dataToolsGuarded) { setDataToolsNotice("Save or discard the code draft and finish or cancel the current review before leaving Data Tools."); return current; }
      if (lifecycleExecuting) return current;
      if (current === "quick_import" && (quickImportDirty || quickImportPreparing)) {
        setQuickImportNavigationNotice(quickImportPreparing
          ? "Package preparation must finish before leaving this workspace."
          : "Validate or reset every changed Quick Import draft, close the OIV export, and finish or cancel ZIP publication before leaving this workspace.");
        return current;
      }
      if (current === "models" && modelsGuarded) { setModelsNavigationNotice("Finish the current model/texture action, or review/reset the unsaved draft, before leaving this workspace."); return current; }
      if (current === "recipes" && recipeGuarded) {
        setRecipeNavigationNotice("Finish the recipe conversion or close its review before leaving this workspace.");
        return current;
      }
      if (current === "rpf" && rpfGuarded) {
        setGxtNavigationNotice("Finish the RPF write, close/cancel its review, or save/reset the active draft, before leaving this workspace.");
        return current;
      }
      if (current === "workbench" && vehicleAuthoringDirty) {
        setVehicleAuthoringNavigationNotice(
          "Finish the current authoring action, or review/reset unsaved content fields, before leaving Content Workbench.",
        );
        return current;
      }
      if (remember) setHistory((items) => [...items.slice(-19), current]);
      return target;
    });
  }, [quickImportDirty, quickImportPreparing, vehicleAuthoringDirty, rpfGuarded, recipeGuarded, modelsGuarded, dataToolsGuarded, lifecycleExecuting]);

  useEffect(() => { if (!rpfGuarded) setGxtNavigationNotice(""); }, [rpfGuarded]);

  useEffect(() => {
    if (!quickImportDirty && !quickImportPreparing) setQuickImportNavigationNotice("");
  }, [quickImportDirty, quickImportPreparing]);

  useEffect(() => {
    if (!vehicleAuthoringDirty) setVehicleAuthoringNavigationNotice("");
  }, [vehicleAuthoringDirty]);

  const applyLaunch = useCallback((launch: LaunchRequest) => {
    if (dataToolsGuarded || lifecycleExecuting) { setDataToolsNotice("Finish the current operation before opening another source."); return; }
    if (modelsGuarded) { setModelsNavigationNotice("Finish the current model/texture action or reset the unsaved draft before opening another source."); return; }
    if (recipeGuarded) { setRecipeNavigationNotice("Finish the recipe conversion or close its review before opening another source."); return; }
    if (rpfGuarded) {
      setGxtNavigationNotice("Finish the RPF write, close/cancel its review, or save/reset the active draft, before opening another source.");
      return;
    }
    if (vehicleAuthoringDirty) {
      setVehicleAuthoringNavigationNotice("Finish the current authoring action, or review/reset unsaved content fields, before opening another source.");
      return;
    }
    navigate(launch.workspace);
    if (launch.category === "assistant") setConsoleExpanded(true);
    if (launch.warning) setSidecarStatus(launch.warning);
    if (["linker", "assets"].includes(launch.workspace) && launch.source) setPackageSource(launch.source);
    if (launch.workspace === "recipes" && launch.source) setRecipeSource(launch.source);
    if (launch.workspace === "rpf" && launch.source) {
      if (launch.category === "graph") { setGraphLaunchSource(launch.source); setRpfMode("graph"); }
      else { setRpfSource(launch.source); setRpfMode("archive"); }
    }
    if (launch.workspace === "models" && launch.source) setModelMaterialSource(launch.source);
    if (launch.workspace === "workbench") {
      if (launch.source) setVehicleProjectSource(launch.source);
      if (["vehicles", "weapons", "peds", "maps"].includes(launch.category ?? "")) {
        setVehicleProjectCategory(launch.category as WorkbenchCategory);
      } else if (launch.selection === "axle-model") {
        setVehicleProjectCategory("vehicles");
      }
    }
    if (launch.workspace === "receipts" && launch.source) setReceiptGamePath(launch.source);
    if (launch.workspace === "quick_import" && launch.source) {
      if (quickImportDirty || quickImportPreparing) setQuickImportNavigationNotice(quickImportPreparing
        ? "Package preparation must finish before opening another source."
        : "Validate or reset every changed Quick Import draft, close the OIV export, and finish or cancel ZIP publication before opening another source.");
      else setQuickImportSource(launch.source);
    }
  }, [navigate, quickImportDirty, quickImportPreparing, vehicleAuthoringDirty, rpfGuarded, recipeGuarded, modelsGuarded, dataToolsGuarded, lifecycleExecuting]);

  const applyLaunchRef = useRef(applyLaunch);
  applyLaunchRef.current = applyLaunch;

  useEffect(() => {
    let active = true;
    let removeLaunch: (() => void) | undefined;
    let removeStatus: (() => void) | undefined;
    void (async () => {
      try {
        const handshake = await client.handshake();
        const loaded = await client.catalog();
        const initial = await client.initialLaunchRequest();
        if (!active) return;
        setCatalog(loaded);
        setSidecarStatus(`SDK ${String(handshake.payload.sdk_version ?? "ready")} · protocol ${handshake.protocol_version}`);
        if (initial) applyLaunchRef.current(initial);
        removeLaunch = await client.onLaunchRequest((launch) => applyLaunchRef.current(launch));
        removeStatus = await client.onSidecarStatus((status) => {
          setSidecarStatus(status);
          if (status.toLocaleLowerCase().includes("crash")) setBootError(status);
        });
      } catch (reason) {
        if (active) setBootError(String(reason));
      }
    })();
    return () => {
      active = false;
      removeLaunch?.();
      removeStatus?.();
    };
  }, [client]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme = theme === "system" ? (media.matches ? "dark" : "light") : theme;
      document.documentElement.dataset.themeMode = theme;
    };
    apply();
    media.addEventListener("change", apply);
    localStorage.setItem("allin1.theme", theme);
    return () => media.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("allin1.sidebar.collapsed", String(sidebarCollapsed));
  }, [sidebarCollapsed]);

  useEffect(() => {
    localStorage.setItem("allin1.console.expanded", String(consoleExpanded));
  }, [consoleExpanded]);

  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 720px)");
    const collapseForCompactLayout = () => {
      if (narrow.matches) setSidebarCollapsed(true);
    };
    collapseForCompactLayout();
    narrow.addEventListener("change", collapseForCompactLayout);
    return () => narrow.removeEventListener("change", collapseForCompactLayout);
  }, []);

  useEffect(() => {
    headingRef.current?.focus();
  }, [workspace]);

  const inspectPackage = useCallback(async (source: string) => {
    const revision = `linker-${Date.now()}`;
    latestRevision.current = revision;
    setPackageSource(source);
    setPackageError("");
    setPackageResult(null);
    try {
      const started = await client.startJob(
        "inspect_package",
        { source },
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestRevision.current) return;
          completedRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setPackageError(messageText(message));
            return;
          }
          const result = resultFromJob(message);
          if (result) setPackageResult(result as PackageResult);
        },
      );
      if (completedRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setPackageError(String(reason));
      setActiveJob(null);
    }
  }, [client]);

  useEffect(() => {
    if (packageSource && !packageResult && !activeJob && !packageError) void inspectPackage(packageSource);
  }, [activeJob, inspectPackage, packageError, packageResult, packageSource]);

  // Native source-selection dialogs close before invoking this callback. Only
  // an actual conversion/report guard should prevent the selected intake.
  const conversionBusyRef = useRef(false);
  conversionBusyRef.current = recipeConverting;
  const inspectRecipe = useCallback(async (source: string) => {
    if (conversionBusyRef.current) { setRecipeNavigationNotice("Finish the recipe conversion or close its review before refreshing."); return; }
    if (recipeStarting.current) return;
    recipeStarting.current = true;
    const revision = `recipes-${Date.now()}`;
    latestRecipeRevision.current = revision;
    setRecipeSource(source);
    setRecipeError("");
    setRecipeResult(null);
    try {
      const started = await client.startJob(
        "inspect_recipe",
        { source },
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestRecipeRevision.current) return;
          completedRecipeRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setRecipeError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded) setRecipeResult(loaded as RecipePlanResult);
        },
      );
      if (completedRecipeRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setRecipeError(String(reason));
      setActiveJob(null);
    } finally {
      recipeStarting.current = false;
    }
  }, [client]);

  useEffect(() => {
    if (recipeSource && !recipeResult && !activeJob && !recipeError && !recipeStarting.current) void inspectRecipe(recipeSource);
  }, [activeJob, inspectRecipe, recipeError, recipeResult, recipeSource]);

  const inspectRpf = useCallback(async (source: string, gtaPath: string) => {
    if (!source || rpfStarting.current) return;
    rpfStarting.current = true;
    const revision = `rpf-index-${Date.now()}`;
    latestRpfRevision.current = revision;
    setRpfSource(source);
    setRpfError("");
    setRpfResult(null);
    try {
      const started = await client.startJob(
        "inspect_rpf_archive",
        { archive: source, ...(gtaPath ? { gta_path: gtaPath } : {}) },
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestRpfRevision.current) return;
          completedRpfRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setRpfError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded?.kind === "rpf_archive_index") {
            const indexed = loaded as RpfArchiveResult;
            setRpfResult(indexed);
            setRpfGamePath(indexed.gta_path);
          }
        },
      );
      if (completedRpfRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setRpfError(String(reason));
      setActiveJob(null);
    } finally {
      rpfStarting.current = false;
    }
  }, [client]);

  useEffect(() => {
    if (rpfSource && !rpfResult && !activeJob && !rpfError && !rpfStarting.current) {
      void inspectRpf(rpfSource, rpfGamePath);
    }
  }, [activeJob, inspectRpf, rpfError, rpfGamePath, rpfResult, rpfSource]);

  const inspectVehicleProject = useCallback(async (source: string, gtaPath: string) => {
    if (!source || vehicleProjectStarting.current) return;
    vehicleProjectStarting.current = true;
    const revision = `vehicle-project-${Date.now()}`;
    latestVehicleProjectRevision.current = revision;
    setVehicleProjectSource(source);
    setVehicleProjectError("");
    setVehicleProjectResult(null);
    try {
      const started = await client.startJob(
        "inspect_vehicle_project",
        { source, ...(gtaPath ? { gta_path: gtaPath } : {}) },
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestVehicleProjectRevision.current) return;
          completedVehicleProjectRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setVehicleProjectError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded?.kind === "vehicle_project_inspection") {
            const project = loaded as VehicleProjectResult;
            setVehicleProjectResult(project);
            if (project.gta_path) setVehicleProjectGamePath(project.gta_path);
          }
        },
      );
      if (completedVehicleProjectRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setVehicleProjectError(String(reason));
      setActiveJob(null);
    } finally {
      vehicleProjectStarting.current = false;
    }
  }, [client]);

  useEffect(() => {
    if (
      vehicleProjectCategory === "vehicles"
      && vehicleProjectSource
      && !vehicleProjectResult
      && !activeJob
      && !vehicleProjectError
      && !vehicleProjectStarting.current
    ) {
      void inspectVehicleProject(vehicleProjectSource, vehicleProjectGamePath);
    }
  }, [activeJob, inspectVehicleProject, vehicleProjectCategory, vehicleProjectError, vehicleProjectGamePath, vehicleProjectResult, vehicleProjectSource]);

  const inspectReceipts = useCallback(async (gtaPath: string, selectedId: string | null) => {
    if (!gtaPath || receiptStarting.current) return;
    receiptStarting.current = true;
    const revision = `receipts-${Date.now()}`;
    latestReceiptRevision.current = revision;
    setReceiptGamePath(gtaPath);
    setReceiptError("");
    setReceiptResult((current) => current?.gta_path === gtaPath ? current : null);
    const payload: Record<string, unknown> = { gta_path: gtaPath };
    if (selectedId) payload.selected_id = selectedId;
    try {
      const started = await client.startJob(
        "inspect_package_receipts",
        payload,
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestReceiptRevision.current) return;
          completedReceiptRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setReceiptError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded?.kind === "package_receipt_inventory") setReceiptResult(loaded as PackageReceiptResult);
        },
      );
      if (completedReceiptRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setReceiptError(String(reason));
      setActiveJob(null);
    } finally {
      receiptStarting.current = false;
    }
  }, [client]);

  useEffect(() => {
    if (receiptGamePath && !receiptResult && !activeJob && !receiptError && !receiptStarting.current) void inspectReceipts(receiptGamePath, null);
  }, [activeJob, inspectReceipts, receiptError, receiptGamePath, receiptResult]);

  const reviewPackageLifecycle = useCallback(async (
    action: PackageLifecycleReviewResult["action"],
    subject: string,
  ) => {
    if (!receiptGamePath || !subject || lifecycleStarting.current) return;
    lifecycleStarting.current = true;
    const revision = `lifecycle-${Date.now()}`;
    latestLifecycleRevision.current = revision;
    setLifecycleError("");
    setLifecycleReview(null);
    setLifecycleExecution(null);
    const payload: Record<string, unknown> = {
      action,
      gta_path: receiptGamePath,
      ...(action === "install" ? { source: subject } : { mod_id: subject }),
    };
    try {
      const started = await client.startJob(
        "review_package_lifecycle",
        payload,
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestLifecycleRevision.current) return;
          completedLifecycleRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setLifecycleError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded?.kind === "package_lifecycle_review") setLifecycleReview(loaded as PackageLifecycleReviewResult);
        },
      );
      if (completedLifecycleRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setLifecycleError(String(reason));
      setActiveJob(null);
    } finally {
      lifecycleStarting.current = false;
    }
  }, [client, receiptGamePath]);

  const executePackageLifecycle = useCallback(async (
    review: PackageLifecycleReviewResult,
  ) => {
    if (lifecycleExecuting || !review.ready) return;
    setLifecycleExecuting(true);
    setLifecycleError("");
    setLifecycleExecution(null);
    const payload: Record<string, unknown> = {
      action: review.action,
      gta_path: review.gta_path,
      review_sha256: review.review_sha256,
      confirmation_id: review.package.id,
      game_write_confirmed: true,
      replace_confirmed: Boolean(review.replacing),
      ...(review.action === "install" ? { source: review.source } : { mod_id: review.package.id }),
    };
    try {
      const response = await client.applyPackageLifecycle(payload);
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = resultFromJob(response) as PackageLifecycleExecutionResult | null;
      if (
        !loaded
        || loaded.kind !== "package_lifecycle_execution"
        || loaded.review_sha256 !== review.review_sha256
        || loaded.action !== review.action
        || loaded.game_write_performed !== true
        || loaded.process_check?.gta_closed !== true
      ) {
        throw new Error("The SDK service returned an invalid lifecycle execution result.");
      }
      setLifecycleExecution(loaded);
      void inspectReceipts(
        review.gta_path,
        review.action === "uninstall" ? null : loaded.package.id,
      );
    } catch (reason) {
      setLifecycleError(String(reason));
    } finally {
      setLifecycleExecuting(false);
    }
  }, [client, inspectReceipts, lifecycleExecuting]);

  const inspectQuickImport = useCallback(async (
    source: string,
    gtaPath: string,
    preferredEdition: string | null,
  ) => {
    if (!source || quickImportStarting.current) return;
    quickImportStarting.current = true;
    const revision = `quick-import-${Date.now()}`;
    latestQuickImportRevision.current = revision;
    setQuickImportSource(source);
    setQuickImportGamePath(gtaPath);
    setQuickImportError("");
    setQuickImportReviewError("");
    setQuickImportPrepareError("");
    setQuickImportPrepared(null);
    setQuickImportReviews({});
    setQuickImportResult(null);
    const payload: Record<string, unknown> = { source };
    if (gtaPath) payload.gta_path = gtaPath;
    if (preferredEdition) payload.preferred_edition = preferredEdition;
    try {
      const started = await client.startJob(
        "inspect_vehicle_quick_import",
        payload,
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestQuickImportRevision.current) return;
          completedQuickImportRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setQuickImportError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message);
          if (loaded) setQuickImportResult(loaded as VehicleQuickImportResult);
        },
      );
      if (completedQuickImportRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setQuickImportError(String(reason));
      setActiveJob(null);
    } finally {
      quickImportStarting.current = false;
    }
  }, [client]);

  const reviewQuickImport = useCallback(async (
    source: string,
    gtaPath: string,
    edition: string,
    draft: QuickImportEditionDraft | null,
  ) => {
    if (!source || !edition || quickImportReviewStarting.current) return;
    quickImportReviewStarting.current = true;
    const revision = `quick-import-review-${Date.now()}`;
    latestQuickImportReviewRevision.current = revision;
    setQuickImportReviewError("");
    setQuickImportPrepareError("");
    setQuickImportPrepared(null);
    const payload: Record<string, unknown> = { source, edition };
    if (gtaPath) payload.gta_path = gtaPath;
    if (draft) Object.assign(payload, quickImportReviewPayload(draft));
    try {
      const started = await client.startJob(
        "review_vehicle_quick_import",
        payload,
        revision,
        (message) => {
          if (!message.terminal || message.payload.revision !== latestQuickImportReviewRevision.current) return;
          completedQuickImportReviewRevision.current = revision;
          setActiveJob(null);
          if (message.operation === "error") {
            setQuickImportReviewError(messageText(message));
            return;
          }
          const loaded = resultFromJob(message) as VehicleQuickImportReviewResult | null;
          if (loaded?.plan?.edition) {
            setQuickImportReviews((current) => ({ ...current, [loaded.plan.edition.toLocaleLowerCase()]: loaded }));
          }
        },
      );
      if (completedQuickImportReviewRevision.current !== revision) setActiveJob(started.job_id);
    } catch (reason) {
      setQuickImportReviewError(String(reason));
      setActiveJob(null);
    } finally {
      quickImportReviewStarting.current = false;
    }
  }, [client]);

  const prepareQuickImport = useCallback(async (
    source: string,
    gtaPath: string,
    edition: string,
    draft: QuickImportEditionDraft,
    review: VehicleQuickImportReviewResult,
  ) => {
    if (!source || !edition || quickImportPreparing) return;
    setQuickImportPreparing(true);
    setQuickImportPrepareError("");
    setQuickImportPrepared(null);
    const payload: Record<string, unknown> = {
      source,
      edition,
      ...quickImportReviewPayload(draft),
      review_sha256: review.review_sha256,
      authoring_confirmed: true,
      replace_confirmed: review.destination_review.exists,
    };
    if (gtaPath) payload.gta_path = gtaPath;
    try {
      const response = await client.prepareVehicleQuickImport(payload);
      if (response.operation === "error") throw new Error(messageText(response));
      const loaded = resultFromJob(response) as VehicleQuickImportPreparedResult | null;
      if (
        !loaded
        || loaded.kind !== "vehicle_quick_import_prepared"
        || loaded.review_sha256 !== review.review_sha256
        || loaded.game_write_performed !== false
        || loaded.package_write_performed !== true
      ) {
        throw new Error("The SDK service returned an invalid package-preparation result.");
      }
      setQuickImportPrepared(loaded);
    } catch (reason) {
      setQuickImportPrepareError(String(reason));
    } finally {
      setQuickImportPreparing(false);
    }
  }, [client, quickImportPreparing]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const editing = target?.closest("input, textarea, select, [contenteditable=true]");
      if (event.key === "F1") {
        event.preventDefault();
        navigate("help");
      } else if (event.key === "F5" && ["linker", "assets"].includes(workspace) && packageSource) {
        event.preventDefault();
        void inspectPackage(packageSource);
      } else if (event.key === "F5" && workspace === "recipes" && recipeSource) {
        event.preventDefault();
        void inspectRecipe(recipeSource);
      } else if (event.key === "F5" && workspace === "workbench" && vehicleProjectCategory === "vehicles" && vehicleProjectSource) {
        event.preventDefault();
        void inspectVehicleProject(vehicleProjectSource, vehicleProjectGamePath);
      } else if (event.key === "F5" && workspace === "receipts" && receiptGamePath) {
        event.preventDefault();
        void inspectReceipts(receiptGamePath, receiptResult?.selected_id ?? null);
      } else if (event.ctrlKey && event.key === "`") {
        event.preventDefault();
        setConsoleExpanded((value) => !value);
      } else if (event.ctrlKey && event.key.toLocaleLowerCase() === "b") {
        event.preventDefault();
        setSidebarCollapsed((value) => !value);
      } else if (event.altKey && event.key === "ArrowLeft") {
        event.preventDefault();
        setHistory((items) => {
          const previous = items.at(-1);
          if (previous && workspace === "data_tools" && dataToolsGuarded) { setDataToolsNotice("Save or discard the code draft and finish or cancel the current review before leaving Data Tools."); return items; }
          if (previous && workspace === "models" && modelsGuarded) { setModelsNavigationNotice("Finish the current model/texture action or reset the unsaved draft before leaving this workspace."); return items; }
          if (previous && workspace === "recipes" && recipeGuarded) { setRecipeNavigationNotice("Finish the recipe conversion or close its review before leaving this workspace."); return items; }
          if (previous && workspace === "workbench" && vehicleAuthoringDirty) { setVehicleAuthoringNavigationNotice("Finish or reset the active content draft before leaving this workspace."); return items; }
          if (previous && workspace === "rpf" && rpfGuarded) {
            setGxtNavigationNotice("Finish the RPF write, close/cancel its review, or save/reset the active draft, before leaving this workspace.");
            return items;
          }
          if (previous && workspace === "quick_import" && (quickImportDirty || quickImportPreparing)) {
            setQuickImportNavigationNotice(quickImportPreparing
              ? "Package preparation must finish before leaving this workspace."
              : "Validate or reset every changed Quick Import draft, close the OIV export, and finish or cancel ZIP publication before leaving this workspace.");
            return items;
          }
          if (previous) setWorkspace(previous);
          return items.slice(0, -1);
        });
      } else if (!editing && event.ctrlKey && event.key === "Tab" && catalog.navigation.length) {
        event.preventDefault();
        const current = catalog.navigation.findIndex((item) => item.id === workspace);
        const delta = event.shiftKey ? -1 : 1;
        const next = (current + delta + catalog.navigation.length) % catalog.navigation.length;
        navigate(catalog.navigation[next].id);
      } else if (!editing && event.ctrlKey) {
        const shortcuts: Record<string, WorkspaceId> = { "1": "linker", "2": "assets", "3": "workbench", i: "quick_import", "4": "models", "5": "rpf", "6": "recipes", "7": "help", "8": "receipts" };
        const route = shortcuts[event.key.toLocaleLowerCase()];
        if (route) {
          event.preventDefault();
          navigate(route);
        }
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [catalog.navigation, inspectPackage, inspectReceipts, inspectRecipe, inspectVehicleProject, navigate, packageSource, quickImportDirty, quickImportPreparing, receiptGamePath, receiptResult?.selected_id, recipeSource, vehicleProjectCategory, vehicleProjectGamePath, vehicleProjectSource, workspace, rpfGuarded, recipeGuarded, vehicleAuthoringDirty, modelsGuarded, dataToolsGuarded]);

  const cancelActive = async () => {
    if (!activeJob) return;
    try {
      await client.cancelJob(activeJob);
    } finally {
      setActiveJob(null);
    }
  };

  const checkUpdate = async () => {
    setUpdateError("");
    try {
      setUpdate(await client.checkUpdate());
    } catch (reason) {
      setUpdateError(String(reason));
    }
  };

  const currentCopy = WORKSPACE_COPY[workspace];
  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand-lockup"><span className="brand-mark" aria-hidden="true"><img src={sdkLogo} alt="" /></span><div><strong>ALLIN1 SDK</strong><small>GTA V toolchain</small></div></div>
        <div className="header-actions">
          <button className="icon-button" onClick={() => setTheme(theme === "system" ? "dark" : theme === "dark" ? "light" : "system")} aria-label={`Theme: ${theme}. Change theme`} title={`Theme: ${theme}`}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg></button>
          <button className="quiet-button compact" onClick={checkUpdate}>Updates</button>
        </div>
      </header>

      <div className="banner-stack">
        {desktopLifecycle.notice && <div className="error-banner top-banner" role="alert"><span>{desktopLifecycle.notice}</span><button onClick={desktopLifecycle.dismiss}>Return to workspace</button></div>}
        {dataToolsNotice && <div className="error-banner top-banner" role="alert">{dataToolsNotice}<button onClick={() => setDataToolsNotice("")}>Dismiss</button></div>}
        {bootError && <div className="crash-banner" role="alert"><span>{bootError}</span><button onClick={async () => {
          try {
            await client.restartSidecar();
            setCatalog(await client.catalog());
            setBootError(""); setSidecarStatus("SDK service restarted; workspace drafts retained");
          } catch (reason) { setBootError(String(reason)); }
        }}>Restart SDK service</button></div>}
        {updateError && <div className="error-banner top-banner" role="alert">Update check failed: {updateError}</div>}
        {update && <div className="update-banner" role="status"><span><strong>{update.update_available ? `ALLIN1 SDK ${update.latest_version} is available` : `ALLIN1 SDK ${update.current_version} is current`}</strong><small>{update.update_available ? "Installation is disabled until signed Tauri update metadata is configured." : "No newer verified release was found."}</small></span><button onClick={() => setUpdate(null)} aria-label="Dismiss update result">×</button></div>}
      </div>

      <div className="body-shell">
        <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`} aria-label="Workspace navigation">
          <button className="sidebar-toggle" onClick={() => setSidebarCollapsed((value) => !value)} aria-label={sidebarCollapsed ? "Show workspace sidebar" : "Hide workspace sidebar"} aria-expanded={!sidebarCollapsed}>{sidebarCollapsed ? "›" : "‹"}</button>
          <nav aria-label="Primary">
            {NAV_SECTIONS.map((section) => {
              const items = catalog.navigation.filter((item) => section.items.includes(item.id));
              if (!items.length) return null;
              return <div className="nav-group" key={section.label}><span className="nav-label">{section.label}</span>{items.map((item) => (
                <button key={item.id} className={workspace === item.id ? "active" : ""} onClick={() => navigate(item.id)} aria-current={workspace === item.id ? "page" : undefined} title={`${item.label} (${item.shortcut})`}>
                  <span className="nav-glyph"><WorkspaceIcon workspace={item.id} /></span><span className="nav-copy"><strong>{item.label}</strong><small>{item.shortcut}</small></span>
                </button>
              ))}</div>;
            })}
          </nav>
          <div className="sidebar-status"><span className={`activity-dot ${bootError ? "error" : "ready"}`} /><span>{sidecarStatus}</span></div>
        </aside>

        <main className={`workspace-host${workspace === "help" ? " help-host" : ""}`}>
          <div ref={headingRef} tabIndex={-1} className="focus-anchor" aria-label={`${currentCopy.title} workspace`} />
          {workspace === "data_tools" && <DataToolsWorkspace client={client} onGuardChange={setDataToolsGuarded} />}
          {workspace === "linker" && <PackageLinker client={client} result={packageResult} source={packageSource} busy={Boolean(activeJob)} error={packageError} onInspect={inspectPackage} onCancel={cancelActive} />}
          {workspace === "assets" && <AssetViewer client={client} result={packageResult} source={packageSource} busy={Boolean(activeJob)} activeJob={activeJob} error={packageError} onInspect={inspectPackage} onCancel={cancelActive} onJob={setActiveJob} />}
          {workspace === "workbench" && <ContentWorkbench requestedModel={graphVehicleRequest}
            client={client}
            onHelp={() => { setHelpTopic("ped-workbench"); navigate("help"); }}
            result={vehicleProjectResult}
            source={vehicleProjectSource}
            gtaPath={vehicleProjectGamePath}
            category={vehicleProjectCategory}
            busy={Boolean(activeJob)}
            activeJob={activeJob}
            error={vehicleProjectError}
            onSourceChange={(source) => {
              setVehicleProjectSource(source);
              setVehicleProjectResult(null);
              setVehicleProjectError("");
            }}
            onGameChange={(path) => {
              setVehicleProjectGamePath(path);
              setVehicleProjectResult(null);
              setVehicleProjectError("");
            }}
            onCategoryChange={(category) => {
              if (vehicleAuthoringDirty && category !== vehicleProjectCategory) {
                setVehicleAuthoringNavigationNotice(
                  "Finish the current authoring action, or review/reset unsaved content fields, before changing content type.",
                );
                return;
              }
              setVehicleProjectCategory(category);
              setVehicleProjectError("");
            }}
            onInspect={inspectVehicleProject}
            onCancel={cancelActive}
            onJob={setActiveJob}
            navigationNotice={vehicleAuthoringNavigationNotice}
            onDirtyChange={setVehicleAuthoringDirty}
          />}
          {workspace === "receipts" && <PackageReceiptsWorkspace
            client={client}
            result={receiptResult}
            lifecycleReview={lifecycleReview}
            lifecycleExecution={lifecycleExecution}
            lifecycleExecuting={lifecycleExecuting}
            gtaPath={receiptGamePath}
            busy={Boolean(activeJob) || lifecycleExecuting}
            error={receiptError}
            lifecycleError={lifecycleError}
            onPathChange={(path) => {
              setReceiptGamePath(path);
              setReceiptResult(null);
              setReceiptError("");
              setLifecycleReview(null);
              setLifecycleExecution(null);
              setLifecycleExecuting(false);
              setLifecycleError("");
            }}
            onInspect={inspectReceipts}
            onReview={reviewPackageLifecycle}
            onExecute={executePackageLifecycle}
            onCloseReview={() => {
              setLifecycleReview(null);
              setLifecycleExecution(null);
              setLifecycleError("");
            }}
            onCancel={cancelActive}
          />}
          {workspace === "recipes" && recipeNavigationNotice && <p className="action-notice" role="status">{recipeNavigationNotice}</p>}
          {workspace === "recipes" && <RecipeWorkspace client={client} result={recipeResult} source={recipeSource} busy={Boolean(activeJob)} error={recipeError} onInspect={inspectRecipe} onCancel={cancelActive} onGuardChange={setRecipeGuarded} onConversionGuardChange={setRecipeConverting} />}
          {workspace === "quick_import" && <QuickImportWorkspace
            client={client}
            result={quickImportResult}
            reviews={quickImportReviews}
            prepared={quickImportPrepared}
            source={quickImportSource}
            gtaPath={quickImportGamePath}
            busy={Boolean(activeJob)}
            preparing={quickImportPreparing}
            error={quickImportError}
            reviewError={quickImportReviewError}
            prepareError={quickImportPrepareError}
            navigationNotice={quickImportNavigationNotice}
            onSourceChange={(source) => {
              setQuickImportDirty(false);
              setQuickImportSource(source);
              setQuickImportResult(null);
              setQuickImportReviews({});
              setQuickImportPrepared(null);
              setQuickImportError("");
              setQuickImportReviewError("");
              setQuickImportPrepareError("");
            }}
            onGameChange={(path) => {
              setQuickImportDirty(false);
              setQuickImportGamePath(path);
              setQuickImportResult(null);
              setQuickImportReviews({});
              setQuickImportPrepared(null);
              setQuickImportError("");
              setQuickImportReviewError("");
              setQuickImportPrepareError("");
            }}
            onInspect={inspectQuickImport}
            onReview={reviewQuickImport}
            onPrepare={prepareQuickImport}
            onDirtyChange={(guarded, draftDirty) => {
              setQuickImportDirty(guarded);
              if (draftDirty) setQuickImportPrepared(null);
            }}
            onCancel={cancelActive}
          />}
          {workspace === "rpf" && <div className="workspace-section rpf-tools-shell"><div className="rpf-workspace-tabs" role="tablist" aria-label="RPF tools">
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "archive"} disabled={rpfGuarded || Boolean(activeJob)} onClick={() => setRpfMode("archive")}>Archive inspection</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "text"} disabled={graphGuarded || programGuarded || binaryGuarded || changeGuarded || transactionGuarded || Boolean(activeJob)} onClick={() => setRpfMode("text")}>GXT2 game text</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "changes"} disabled={graphGuarded || programGuarded || binaryGuarded || gxtGuarded || transactionGuarded || Boolean(activeJob)} onClick={() => setRpfMode("changes")}>Change sets</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "transactions"} disabled={graphGuarded || programGuarded || binaryGuarded || gxtGuarded || changeGuarded || Boolean(activeJob)} onClick={() => setRpfMode("transactions")}>Execute & restore</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "binary"} disabled={graphGuarded || programGuarded || gxtGuarded || changeGuarded || transactionGuarded || Boolean(activeJob)} onClick={() => setRpfMode("binary")}>Binary editor</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "graph"} disabled={programGuarded || binaryGuarded || gxtGuarded || changeGuarded || transactionGuarded || Boolean(activeJob)} onClick={() => setRpfMode("graph")}>Package layout</button>
            <button className="quiet-button" role="tab" aria-selected={rpfMode === "program"} disabled={graphGuarded || binaryGuarded || gxtGuarded || changeGuarded || transactionGuarded || Boolean(activeJob)} onClick={() => setRpfMode("program")}>Build flow</button>
          </div>{gxtNavigationNotice && <p role="alert" className="error-banner">{gxtNavigationNotice}</p>}
          <div hidden={rpfMode !== "graph"}><GraphWorkbench client={client} module="graph" initialSource={graphLaunchSource} onGuardChange={setGraphGuarded} onOpenAsset={source => { if (rpfGuarded) return; setModelMaterialSource(source); navigate("models"); }} onOpenVehicle={(source, model) => { if (rpfGuarded) return; setGraphVehicleRequest({ source, model }); setVehicleProjectSource(source); setVehicleProjectCategory("vehicles"); navigate("workbench"); void inspectVehicleProject(source, vehicleProjectGamePath); }} /></div>
          <div hidden={rpfMode !== "program"}><GraphWorkbench client={client} module="program" onGuardChange={setProgramGuarded} /></div>
          <div hidden={rpfMode !== "binary"}><BinaryWorkspace client={client} onGuardChange={setBinaryGuarded} archiveRequest={binaryArchiveRequest} /></div>
          <div hidden={rpfMode !== "text"}><Gxt2Workspace client={client} onGuardChange={setGxtGuarded} archiveRequest={gxtArchiveRequest} /></div>
          <div hidden={rpfMode !== "changes"}><RpfChangeSetWorkspace client={client} indexed={rpfResult} onGuardChange={setChangeGuarded} targetRequest={changeRequest} /></div>
          <div hidden={rpfMode !== "transactions"}><RpfTransactionWorkspace client={client} onGuardChange={setTransactionGuarded} onArchiveChanged={() => setRpfResult(null)} /></div>
          <div hidden={rpfMode !== "archive"}><RpfInspector
            client={client}
            result={rpfResult}
            source={rpfSource}
            gtaPath={rpfGamePath}
            busy={Boolean(activeJob)}
            activeJob={activeJob}
            error={rpfError}
            onSourceChange={(source) => {
              setRpfSource(source);
              setRpfResult(null);
              setRpfError("");
            }}
            onGameChange={(path) => {
              setRpfGamePath(path);
              setRpfResult(null);
              setRpfError("");
            }}
            onInspect={inspectRpf}
            onCancel={cancelActive}
            onJob={setActiveJob}
            onOpenBinary={(request) => {
              if (rpfGuarded || activeJob) return;
              setBinaryArchiveRequest(previous => ({ ...request, requestId: (previous?.requestId ?? 0) + 1 }));
              setRpfMode("binary");
            }}
            onOpenGameText={(request) => {
              if (rpfGuarded || activeJob) return;
              setGxtArchiveRequest(previous => ({ ...request, requestId: (previous?.requestId ?? 0) + 1 }));
              setRpfMode("text");
            }}
            onStageMember={(request) => {
              if (rpfGuarded || activeJob) return;
              setChangeRequest(previous => ({...request, requestId: (previous?.requestId ?? 0) + 1}));
              setRpfMode("changes");
            }}
            onUtilityGuardChange={setUtilityGuarded}
          /></div></div>}
          {workspace === "models" && modelsNavigationNotice && <p className="action-notice" role="status">{modelsNavigationNotice}</p>}
          {workspace === "models" && <ModelsWorkspace client={client} initialSource={modelMaterialSource} onGuardChange={setModelsGuarded} />}
          {workspace === "help" && <HelpCenter topics={catalog.help_topics} initialTopic={helpTopic} />}
        </main>
      </div>

      <ConsoleDock client={client} catalog={catalog} expanded={consoleExpanded} onToggle={() => setConsoleExpanded((value) => !value)} />
    </div>
  );
}
