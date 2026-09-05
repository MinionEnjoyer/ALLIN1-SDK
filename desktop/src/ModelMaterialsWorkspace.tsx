import { useEffect, useMemo, useRef, useState } from "react";
import VehicleViewport from "./VehicleViewport";
import { formatBytes } from "./tokenize";
import type {
  DesktopClient,
  Envelope,
  ModelGeometryRecord,
  ModelMaterialBuildResult,
  ModelMaterialBuildReview,
  ModelMaterialEditReview,
  ModelMaterialParameter,
  ModelMaterialProjectResult,
  ModelMaterialRecord,
  ModelMaterialWorkspaceReview,
} from "./types";

type ReviewState =
  | { kind: "workspace"; review: ModelMaterialWorkspaceReview; payload: Record<string, unknown> }
  | { kind: "edit"; review: ModelMaterialEditReview; payload: Record<string, unknown> }
  | { kind: "build"; review: ModelMaterialBuildReview; payload: Record<string, unknown> }
  | { kind: "undo"; payload: Record<string, unknown> };

function eventError(message: Envelope): string {
  const payload = message.payload as Record<string, unknown>;
  return String(payload.message ?? payload.error ?? "Model operation failed.");
}

function completedResult<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(eventError(message));
  const result = (message.payload as Record<string, unknown>).result;
  if (!result || typeof result !== "object") {
    throw new Error("Model operation did not return a result.");
  }
  return result as T;
}

function defaultWorkspaceName(path: string): string {
  const file = path.split(/[\\/]/).at(-1) ?? "model";
  return `${file.replace(/\.[^.]+$/, "")}-materials`;
}

function EmptyPane({ kicker, title, children }: { kicker: string; title: string; children: string }) {
  return (
    <section className="model-material-pane empty">
      <header><span className="pane-kicker">{kicker}</span><strong>{title}</strong></header>
      <div className="model-material-empty"><strong>{title}</strong><p>{children}</p></div>
    </section>
  );
}

export default function ModelMaterialsWorkspace({ client, initialSource = "", onGuardChange }: { client: DesktopClient; initialSource?: string; onGuardChange?: (guarded: boolean) => void }) {
  const [source, setSource] = useState(initialSource);
  const [gtaPath, setGtaPath] = useState("");
  const [edition, setEdition] = useState("Enhanced");
  const [result, setResult] = useState<ModelMaterialProjectResult | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const readGeneration = useRef(0), readFlight = useRef(false), readJob = useRef(""), mounted = useRef(true);
  const quietJobs = useRef(new Set<string>());
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; readGeneration.current++;
    for (const job of [readJob.current, ...quietJobs.current]) if (job) void client.cancelJob(job).catch(() => {});
  }; }, [client]);
  const [activeJob, setActiveJob] = useState("");
  const [materialQuery, setMaterialQuery] = useState("");
  const [geometryQuery, setGeometryQuery] = useState("");
  const [materialIndex, setMaterialIndex] = useState<number | null>(null);
  const [geometryIndex, setGeometryIndex] = useState<number | null>(null);
  const [shaderDraft, setShaderDraft] = useState("");
  const [textureDrafts, setTextureDrafts] = useState<Record<string, string>>({});
  const [parameterName, setParameterName] = useState("");
  const [parameterDrafts, setParameterDrafts] = useState<string[][]>([]);
  const [geometryMaterialDraft, setGeometryMaterialDraft] = useState(0);
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [buildResult, setBuildResult] = useState<ModelMaterialBuildResult | null>(null);
  const [compareBuild, setCompareBuild] = useState(false);

  useEffect(() => {
    if (!initialSource) return;
    setSource(initialSource);
    setResult(null);
    setError("");
    setNotice("");
    setBuildResult(null);
  }, [initialSource]);

  const materials = useMemo(() => {
    const needle = materialQuery.trim().toLocaleLowerCase();
    return (result?.materials ?? []).filter((item) =>
      !needle || `${item.shader} ${item.textures.map((texture) => `${texture.slot} ${texture.texture}`).join(" ")} ${item.parameters.map((parameter) => parameter.name).join(" ")}`
        .toLocaleLowerCase().includes(needle),
    );
  }, [materialQuery, result]);
  const geometries = useMemo(() => {
    const needle = geometryQuery.trim().toLocaleLowerCase();
    return (result?.geometries ?? []).filter((item) =>
      !needle || `${item.component} ${item.lod} ${item.material_name}`.toLocaleLowerCase().includes(needle),
    );
  }, [geometryQuery, result]);
  const selectedMaterial = result?.materials.find((item) => item.index === materialIndex) ?? result?.materials[0] ?? null;
  const selectedParameter = selectedMaterial?.parameters.find((item) => item.name === parameterName) ?? selectedMaterial?.parameters[0] ?? null;
  const selectedGeometry = result?.geometries.find((item) => item.index === geometryIndex) ?? result?.geometries[0] ?? null;
  const authoring = result?.kind === "model_material_authoring_session" && Boolean(result.workspace);

  useEffect(() => {
    setShaderDraft(selectedMaterial?.shader ?? "");
    setTextureDrafts(Object.fromEntries(selectedMaterial?.textures.map((item) => [item.slot, item.texture]) ?? []));
    setParameterName(selectedMaterial?.parameters[0]?.name ?? "");
  }, [result?.revision, selectedMaterial?.index]);

  useEffect(() => {
    setParameterDrafts(selectedParameter?.values.map((row) => row.map(String)) ?? []);
  }, [result?.revision, selectedMaterial?.index, selectedParameter?.name]);

  useEffect(() => {
    setGeometryMaterialDraft(selectedGeometry?.material_index ?? 0);
  }, [result?.revision, selectedGeometry?.index]);

  const loadResult = (loaded: ModelMaterialProjectResult) => {
    setResult(loaded);
    setSource(loaded.source);
    setEdition(loaded.edition);
    setMaterialIndex(loaded.materials[0]?.index ?? null);
    setGeometryIndex(loaded.geometries[0]?.index ?? null);
    setBuildResult(null);
    setCompareBuild(false);
  };

  const runJob = async <T,>(
    operation: Parameters<DesktopClient["startJob"]>[0],
    payload: Record<string, unknown>,
    revision: string,
    onResult: (loaded: T) => void,
  ) => {

    const isQuiet = false;
    if (!mounted.current || (!isQuiet && (readFlight.current || busy))) return;
    const generation = isQuiet ? readGeneration.current : ++readGeneration.current;
    if (!isQuiet) { readFlight.current = true; setBusy(true); setError(""); setNotice(""); }
    let finished = false, returnedId = "";
    try {
      const started = await client.startJob(operation, payload, revision, message => {
        if (!message.terminal || finished || !mounted.current || (!isQuiet && generation !== readGeneration.current)) return;
        finished = true; quietJobs.current.delete(returnedId);
        if (!isQuiet) { readFlight.current = false; readJob.current = ""; setBusy(false); setActiveJob(""); }
        try { onResult(completedResult<T>(message)); }
        catch (reason) {
          const detail = String(reason).replace(/^Error:\s*/, "");
          setError(detail);
        }
      });
      returnedId = started.job_id;
      if (!mounted.current || (!isQuiet && generation !== readGeneration.current)) {
        if (!finished) void client.cancelJob(started.job_id).catch(() => {});
      } else if (!finished) {
        if (isQuiet) quietJobs.current.add(started.job_id);
        else { readJob.current = started.job_id; setActiveJob(started.job_id); }
      }
    } catch (reason) {
      if (!mounted.current || (!isQuiet && generation !== readGeneration.current)) return;
      if (!isQuiet) { readFlight.current = false; setBusy(false); setActiveJob(""); }
      const detail = String(reason).replace(/^Error:\s*/, "");
      setError(detail);
    }
  };

  const selectModel = async () => {
    const selected = await client.selectPath("model_asset");
    if (selected) {
      setSource(selected);
      setResult(null);
      setError("");
      setNotice("");
    }
  };

  const selectGame = async () => {
    const selected = await client.selectPath("gta_folder");
    if (selected) setGtaPath(selected);
  };

  const inspect = async () => {
    if (!source.trim()) return;
    await runJob<ModelMaterialProjectResult>(
      "inspect_model_materials",
      { source, edition, ...(gtaPath ? { gta_path: gtaPath } : {}) },
      `inspect|${source}|${edition}|${gtaPath}`,
      loadResult,
    );
  };

  const openWorkspace = async () => {
    const workspace = await client.selectPath("model_material_workspace");
    if (!workspace) return;
    await runJob<ModelMaterialProjectResult>(
      "inspect_model_material_workspace", { workspace }, `material-workspace|${workspace}`,
      (loaded) => {
        loadResult(loaded);
        setNotice(`Editable workspace opened at revision ${loaded.revision}.`);
      },
    );
  };

  const reviewWorkspace = async () => {
    if (!source || !result || authoring) return;
    const parent = await client.selectPath("model_material_parent");
    if (!parent) return;
    const payload = {
      source, parent, name: defaultWorkspaceName(source), edition,
      ...(gtaPath ? { gta_path: gtaPath } : {}),
    };
    await runJob<ModelMaterialWorkspaceReview>(
      "review_model_material_workspace", payload,
      `material-create|${source}|${parent}|${edition}|${gtaPath}`,
      (review) => setReviewState({ kind: "workspace", review, payload }),
    );
  };

  const reviewMaterial = async () => {
    if (!authoring || !result?.workspace || result.revision === null || !selectedMaterial) return;
    const payload = {
      workspace: result.workspace,
      expected_revision: result.revision,
      action: "material",
      material_index: selectedMaterial.index,
      shader_name: shaderDraft,
      textures: textureDrafts,
    };
    await runJob<ModelMaterialEditReview>(
      "review_model_material_edit", payload,
      `material-edit|${result.workspace}|${result.revision}|${selectedMaterial.index}`,
      (review) => setReviewState({ kind: "edit", review, payload }),
    );
  };

  const reviewGeometry = async () => {
    if (!authoring || !result?.workspace || result.revision === null || !selectedGeometry) return;
    const payload = {
      workspace: result.workspace,
      expected_revision: result.revision,
      action: "geometry",
      geometry_index: selectedGeometry.index,
      material_index: geometryMaterialDraft,
    };
    await runJob<ModelMaterialEditReview>(
      "review_model_material_edit", payload,
      `geometry-edit|${result.workspace}|${result.revision}|${selectedGeometry.index}`,
      (review) => setReviewState({ kind: "edit", review, payload }),
    );
  };

  const reviewParameter = async () => {
    if (!authoring || !result?.workspace || result.revision === null || !selectedMaterial || !selectedParameter) return;
    const payload = {
      workspace: result.workspace,
      expected_revision: result.revision,
      action: "parameter",
      material_index: selectedMaterial.index,
      parameter_name: selectedParameter.name,
      values: parameterDrafts,
    };
    await runJob<ModelMaterialEditReview>(
      "review_model_material_edit", payload,
      `parameter-edit|${result.workspace}|${result.revision}|${selectedMaterial.index}|${selectedParameter.name}`,
      (review) => setReviewState({ kind: "edit", review, payload }),
    );
  };

  const reviewBuild = async () => {
    if (!authoring || !result?.workspace || result.revision === null) return;
    const extension = result.suffix.replace(/^\./, "").toLocaleLowerCase();
    if (extension !== "ydr" && extension !== "ydd" && extension !== "yft") {
      setError("Only .ydr, .ydd, and .yft workspaces can be built.");
      return;
    }
    const destination = await client.selectModelBuildDestination(result.name, extension);
    if (!destination) return;
    const payload = {
      workspace: result.workspace,
      expected_revision: result.revision,
      destination,
      ...(gtaPath ? { gta_path: gtaPath } : {}),
    };
    await runJob<ModelMaterialBuildReview>(
      "review_model_material_build", payload,
      `material-build|${result.workspace}|${result.revision}|${destination}`,
      (review) => setReviewState({ kind: "build", review, payload }),
    );
  };

  const confirmReview = async () => {
    const pending = reviewState;
    if (!pending || busy) return;
    setBusy(true);
    setError("");
    try {
      const operation = pending.kind === "workspace"
        ? "create_model_material_workspace"
        : pending.kind === "edit" ? "apply_model_material_edit"
        : pending.kind === "build" ? "apply_model_material_build" : "apply_model_material_history";
      const payload = pending.kind === "undo"
        ? { ...pending.payload, authoring_confirmed: true }
        : { ...pending.payload, review_sha256: pending.review.review_sha256, authoring_confirmed: true };
      const response = await client.modelMaterialAuthoringAction(operation, payload);
      if (pending.kind === "build") {
        const built = completedResult<ModelMaterialBuildResult>(response);
        setBuildResult(built);
        setCompareBuild(false);
        setNotice("Native model built, reparsed, and verified. Review the receipt or compare both renders.");
      } else {
        const loaded = completedResult<ModelMaterialProjectResult>(response);
        loadResult(loaded);
        setNotice(pending.kind === "workspace"
          ? "Editable copy created. The original model remains untouched."
          : pending.kind === "undo" ? `Prior material state restored at revision ${loaded.revision}.`
          : `Material changes committed at revision ${loaded.revision}.`);
      }
      setReviewState(null);
    } catch (reason) {
      setError(String(reason).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };

  const requestUndo = () => {
    if (!authoring || !result?.workspace || result.revision === null || !result.can_undo) return;
    setReviewState({
      kind: "undo",
      payload: { workspace: result.workspace, direction: "undo", expected_revision: result.revision },
    });
  };

  const cancel = async () => {
    const id = readJob.current;
    readGeneration.current++; readFlight.current = false; readJob.current = "";
    setBusy(false); setActiveJob("");
    setNotice("Inspection cancelled. No authoring changes were applied.");
    if (id) try { await client.cancelJob(id); } catch (reason) { setError(String(reason)); }
  };

  const materialDirty = Boolean(selectedMaterial) && (
    shaderDraft !== selectedMaterial?.shader
    || Boolean(selectedMaterial?.textures.some((item) => textureDrafts[item.slot] !== item.texture))
  );
  const parameterDirty = selectedParameter ? selectedParameter.values.some((row, rowIndex) =>
    row.some((value, axisIndex) => {
      const draft = parameterDrafts[rowIndex]?.[axisIndex] ?? "";
      return draft.trim() === "" || !Number.isFinite(Number(draft)) || Number(draft) !== value;
    }),
  ) : false;
  const geometryDirty = Boolean(selectedGeometry) && selectedGeometry?.material_index !== geometryMaterialDraft;
  const draftDirty = authoring && (materialDirty || parameterDirty || geometryDirty);
  const guarded = busy || Boolean(reviewState) || draftDirty;
  useEffect(() => { onGuardChange?.(guarded); }, [guarded, onGuardChange]);
  const resetDraft = () => {
    setShaderDraft(selectedMaterial?.shader ?? "");
    setTextureDrafts(Object.fromEntries(selectedMaterial?.textures.map(item => [item.slot, item.texture]) ?? []));
    setParameterDrafts(selectedParameter?.values.map(row => row.map(String)) ?? []);
    setGeometryMaterialDraft(selectedGeometry?.material_index ?? 0);
    setNotice("Material and geometry drafts reset to the saved revision.");
  };

  return (
    <section className="workspace-section model-material-workspace" aria-labelledby="model-material-title">
      <div className="workspace-heading">
        <div><span className="eyebrow">Native asset workbench</span><h2 id="model-material-title">Models &amp; Materials</h2><p>Inspect first, then make bounded shader and surface edits in a revisioned workspace copy.</p></div>
        <div className="heading-actions">
          {busy && activeJob && <button className="quiet-button" onClick={() => void cancel()}>Cancel</button>}
          <button className="quiet-button" disabled={guarded} onClick={() => void openWorkspace()}>Open workspace</button>
          <button className="primary-button" disabled={guarded} onClick={() => void selectModel()}>Open model</button>
        </div>
      </div>
      <div className="model-material-source">
        <label><span>{authoring ? "Editable workspace source" : "Model asset"}</span><input value={source} readOnly={authoring} onChange={(event) => { setSource(event.target.value); setResult(null); }} placeholder="Select a loose .ydr, .ydd, or .yft asset" /></label>
        <label><span>Edition</span><select value={edition} disabled={authoring} onChange={(event) => setEdition(event.target.value)}><option>Enhanced</option><option>Legacy</option></select></label>
        <button className="quiet-button" disabled={authoring || busy} onClick={() => void selectGame()} title={gtaPath || "Optional GTA V path"}>{gtaPath ? "Game selected" : "Select game"}</button>
        {authoring
          ? <><button className="quiet-button" disabled={guarded || !result?.can_undo} onClick={requestUndo}>Undo edit</button><button className="primary-button" disabled={guarded} onClick={() => void reviewBuild()}>Build verified asset</button></>
          : <button className="primary-button" disabled={busy || !source.trim()} onClick={() => void inspect()}>{busy ? "Working…" : "Inspect model"}</button>}
      </div>
      {draftDirty && <button className="quiet-button" disabled={busy || !!reviewState} onClick={resetDraft}>Reset material drafts</button>}
      {error && <div className="inline-error" role="alert"><strong>Operation rejected</strong><span>{error}</span></div>}
      {notice && <div className="model-material-notice" role="status"><span aria-hidden="true">●</span><span>{notice}</span></div>}
      {result && <div className="summary-row model-material-summary">
        <strong>{result.name}</strong><span>{formatBytes(result.size)}</span><span>{result.summary.materials} materials</span><span>{result.summary.geometries} geometries</span><span>{result.summary.texture_bindings} bindings</span><span>{result.summary.numeric_parameters} parameters</span><span>{result.lods.length} LODs</span>
        {authoring && <span className="status-pill success">Revision {result.revision}</span>}
        {!authoring && <span className={`status-pill ${result.summary.errors ? "danger" : result.summary.warnings ? "warning" : "success"}`}>{result.summary.errors ? `${result.summary.errors} error${result.summary.errors === 1 ? "" : "s"}` : result.summary.warnings ? `${result.summary.warnings} warning${result.summary.warnings === 1 ? "" : "s"}` : "Decoded cleanly"}</span>}
        <span className="source-path" title={result.source}>{result.sha256.slice(0, 12)} · {authoring ? "guarded copy" : "read only"}</span>
        {!authoring && <button className="quiet-button compact" disabled={busy || result.summary.errors > 0} onClick={() => void reviewWorkspace()}>Create editable copy</button>}
      </div>}
      {buildResult && <BuildReceipt result={buildResult} onCompare={() => setCompareBuild(true)} />}
      <div className="model-material-grid">
        {result ? <MaterialPane items={materials} selected={selectedMaterial} query={materialQuery} onQuery={setMaterialQuery} onSelect={(item) => { if (!draftDirty && !busy && !reviewState) setMaterialIndex(item.index); else setNotice("Review or reset the current draft before selecting another material."); }} editable={authoring} shaderDraft={shaderDraft} textureDrafts={textureDrafts} onShaderDraft={setShaderDraft} onTextureDraft={(slot, value) => setTextureDrafts((current) => ({ ...current, [slot]: value }))} materialDirty={materialDirty} onReviewMaterial={() => void reviewMaterial()} parameter={selectedParameter} parameterName={parameterName} parameterDrafts={parameterDrafts} onParameterName={name => { if (!parameterDirty && !busy && !reviewState) setParameterName(name); else setNotice("Review or reset numeric parameter changes before selecting another parameter."); }} onParameterDraft={(row, axis, value) => setParameterDrafts((current) => current.map((entry, rowIndex) => rowIndex === row ? entry.map((component, axisIndex) => axisIndex === axis ? value : component) : entry))} parameterDirty={parameterDirty} onReviewParameter={() => void reviewParameter()} busy={busy} /> : <EmptyPane kicker="Materials" title="Shader inventory">Open a model to resolve native shader records, texture slots, and numeric parameters.</EmptyPane>}
        {result ? <GeometryPane items={geometries} selected={selectedGeometry} query={geometryQuery} onQuery={setGeometryQuery} onSelect={(item) => { if (!draftDirty && !busy && !reviewState) setGeometryIndex(item.index); else setNotice("Review or reset the current draft before selecting another geometry."); }} editable={authoring} materialDraft={geometryMaterialDraft} onMaterialDraft={setGeometryMaterialDraft} dirty={geometryDirty} busy={busy} onReview={() => void reviewGeometry()} /> : <EmptyPane kicker="Geometry" title="Surface assignments">Geometry, component, LOD, and material relationships will align here.</EmptyPane>}
        {result ? <section className="model-material-pane model-evidence-pane">
          <header><span className="pane-kicker">Viewport</span><strong>Rendered evidence</strong><small>{authoring ? "Source snapshot" : result.viewport.texture_entry ? "Texture linked" : "No sibling YTD"}</small></header>
          <div className="model-selected-evidence">
            <div><span>Material</span><strong>{selectedMaterial?.shader || "Unassigned"}</strong></div>
            <div><span>Geometry</span><strong>{selectedGeometry?.component || "None"}</strong></div>
            <div><span>Surface</span><strong>{selectedGeometry?.material_name || "Unassigned"}</strong></div>
          </div>
          {authoring && <div className="model-viewport-note">The viewport renders the immutable native snapshot. Edited XML evidence is shown in the aligned material and geometry panes.</div>}
          <VehicleViewport client={client} source={result.viewport.source} entry={result.viewport.entry} edition={result.edition} gtaPath={gtaPath || null} model={result.name.replace(/\.[^.]+$/, "")} textureEntry={result.viewport.texture_entry} collisionEntry={result.viewport.collision_entry} />
          {result.findings.length > 0 && <div className="model-findings"><strong>Decoder findings</strong>{result.findings.slice(0, 8).map((finding, index) => <div key={`${finding.code}-${index}`}><span className={`status-pill ${finding.severity === "error" ? "danger" : finding.severity === "warning" ? "warning" : ""}`}>{finding.severity}</span><span>{finding.message}</span></div>)}</div>}
        </section> : <EmptyPane kicker="Viewport" title="Rendered evidence">The native viewport will combine model, texture, UV, material-parameter, and collision evidence.</EmptyPane>}
      </div>
      {reviewState && <MaterialConfirmation state={reviewState} busy={busy} onCancel={() => setReviewState(null)} onConfirm={() => void confirmReview()} />}
      {compareBuild && result && buildResult && <BuildComparison source={result} built={buildResult} client={client} gtaPath={gtaPath} onClose={() => setCompareBuild(false)} />}
    </section>
  );
}

type MaterialPaneProps = {
  items: ModelMaterialRecord[];
  selected: ModelMaterialRecord | null;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (item: ModelMaterialRecord) => void;
  editable: boolean;
  shaderDraft: string;
  textureDrafts: Record<string, string>;
  onShaderDraft: (value: string) => void;
  onTextureDraft: (slot: string, value: string) => void;
  materialDirty: boolean;
  onReviewMaterial: () => void;
  parameter: ModelMaterialParameter | null;
  parameterName: string;
  parameterDrafts: string[][];
  onParameterName: (value: string) => void;
  onParameterDraft: (row: number, axis: number, value: string) => void;
  parameterDirty: boolean;
  onReviewParameter: () => void;
  busy: boolean;
};

function MaterialPane({ items, selected, query, onQuery, onSelect, editable, shaderDraft, textureDrafts, onShaderDraft, onTextureDraft, materialDirty, onReviewMaterial, parameter, parameterName, parameterDrafts, onParameterName, onParameterDraft, parameterDirty, onReviewParameter, busy }: MaterialPaneProps) {
  const [detail, setDetail] = useState<"bindings" | "parameters">("bindings");
  const axes = ["x", "y", "z", "w"] as const;
  return <section className="model-material-pane">
    <header><span className="pane-kicker">Materials</span><strong>Shader inventory</strong><small>{items.length}</small></header>
    <label className="model-pane-filter"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Filter shaders, textures, or parameters" aria-label="Filter materials" /></label>
    <div className="model-pane-list" role="listbox" aria-label="Materials">{items.map((item) => <button key={item.index} className={item.index === selected?.index ? "selected" : ""} role="option" aria-selected={item.index === selected?.index} onClick={() => onSelect(item)}><span><strong>{item.shader || `Material ${item.index}`}</strong><small>{item.textures.length} bindings · {item.parameters.length} parameters · {item.geometry_indices.length} geometries</small></span><span className="row-type">M{item.index}</span></button>)}</div>
    {selected && <>
      <div className="model-material-detail-tabs" role="tablist" aria-label="Material details">
        <button type="button" role="tab" aria-selected={detail === "bindings"} className={detail === "bindings" ? "selected" : ""} onClick={() => setDetail("bindings")}>Bindings <span>{selected.textures.length}</span></button>
        <button type="button" role="tab" aria-selected={detail === "parameters"} className={detail === "parameters" ? "selected" : ""} onClick={() => setDetail("parameters")}>Parameters <span>{selected.parameters.length}</span></button>
      </div>
      {detail === "bindings" && (editable
        ? <div className="model-edit-form"><label><span>Shader name</span><input value={shaderDraft} onChange={(event) => onShaderDraft(event.target.value)} /></label>{selected.textures.map((texture, index) => <label key={`${texture.slot}-${index}`}><span>{texture.slot}<small>{texture.role}</small></span><input value={textureDrafts[texture.slot] ?? ""} onChange={(event) => onTextureDraft(texture.slot, event.target.value)} /></label>)}<button className="primary-button" disabled={busy || !materialDirty} onClick={onReviewMaterial}>Review material changes</button></div>
        : <dl className="model-binding-list">{selected.textures.length ? selected.textures.map((texture, index) => <div key={`${texture.slot}-${index}`}><dt>{texture.slot}</dt><dd>{texture.texture || "Unbound"}<small>{texture.role}</small></dd></div>) : <div><dt>Textures</dt><dd>No texture bindings</dd></div>}</dl>)}
      {detail === "parameters" && (parameter
        ? <div className="model-parameter-editor">
          <label className="model-parameter-select"><span>Parameter</span><select value={parameterName || parameter.name} onChange={(event) => onParameterName(event.target.value)}>{selected.parameters.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
          <div className="model-parameter-meta"><strong>{parameter.name}</strong><span>{parameter.source_type} · {parameter.values.length} {parameter.values.length === 1 ? "row" : "rows"} · Vector4</span></div>
          <div className="model-parameter-rows">{parameter.values.map((row, rowIndex) => <section key={`${parameter.name}-${rowIndex}`}><header><span>{parameter.source_type === "Vector" ? "Value" : `Row ${rowIndex + 1}`}</span><small>float32</small></header><div>{row.map((value, axisIndex) => <label key={axes[axisIndex]}><span>{axes[axisIndex]}</span>{editable ? <input inputMode="decimal" value={parameterDrafts[rowIndex]?.[axisIndex] ?? String(value)} onChange={(event) => onParameterDraft(rowIndex, axisIndex, event.target.value)} aria-label={`${parameter.name} ${parameter.source_type === "Vector" ? "value" : `row ${rowIndex + 1}`} ${axes[axisIndex]}`} /> : <code>{String(value)}</code>}</label>)}</div></section>)}</div>
          {editable && <button className="primary-button" disabled={busy || !parameterDirty} onClick={onReviewParameter}>Review parameter changes</button>}
        </div>
        : <div className="model-parameter-empty"><strong>No numeric parameters</strong><p>This shader exposes texture bindings only.</p></div>)}
    </>}
  </section>;
}

function GeometryPane({ items, selected, query, onQuery, onSelect, editable, materialDraft, onMaterialDraft, dirty, busy, onReview }: { items: ModelGeometryRecord[]; selected: ModelGeometryRecord | null; query: string; onQuery: (value: string) => void; onSelect: (item: ModelGeometryRecord) => void; editable: boolean; materialDraft: number; onMaterialDraft: (value: number) => void; dirty: boolean; busy: boolean; onReview: () => void }) {
  return <section className="model-material-pane"><header><span className="pane-kicker">Geometry</span><strong>Surface assignments</strong><small>{items.length}</small></header><label className="model-pane-filter"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Filter component, LOD, or material" aria-label="Filter geometry" /></label><div className="model-pane-list" role="listbox" aria-label="Geometry">{items.map((item) => <button key={item.index} className={item.index === selected?.index ? "selected" : ""} role="option" aria-selected={item.index === selected?.index} onClick={() => onSelect(item)}><span><strong>{item.component}</strong><small>{item.lod} · {item.material_name || "Unassigned"}</small></span><span className="row-type">G{item.index}</span></button>)}</div>{selected && (editable ? <div className="model-edit-form"><div className="model-readonly-field"><span>Component</span><strong>{selected.component}</strong><small>{selected.lod}</small></div><label><span>Assigned material</span><select value={materialDraft} onChange={(event) => onMaterialDraft(Number(event.target.value))}>{selected.available_materials.map((name, index) => <option key={`${name}-${index}`} value={index}>{name || `Material ${index}`}</option>)}</select></label><button className="primary-button" disabled={busy || !dirty} onClick={onReview}>Review assignment</button></div> : <dl className="model-binding-list"><div><dt>Component</dt><dd>{selected.component}</dd></div><div><dt>LOD</dt><dd>{selected.lod}</dd></div><div><dt>Material</dt><dd>{selected.material_name || "Unassigned"}</dd></div><div><dt>Choices</dt><dd>{selected.available_materials.length}</dd></div></dl>)}</section>;
}

function BuildReceipt({ result, onCompare }: { result: ModelMaterialBuildResult; onCompare: () => void }) {
  const outputName = result.output.path.split(/[\\/]/).at(-1) ?? result.output.path;
  return <section className="model-build-receipt" aria-label="Verified build receipt">
    <div className="model-build-status" aria-hidden="true">✓</div>
    <div className="model-build-summary"><span className="pane-kicker">Verified native build</span><strong>{outputName}</strong><small>{formatBytes(result.output.size)} · {result.output.sha256.slice(0, 12)} · revision {result.revision}</small></div>
    <dl><div><dt>Reparsed</dt><dd>{result.validation.reparsed ? "Yes" : "No"}</dd></div><div><dt>Semantic XML</dt><dd>{result.validation.semantic_xml_match ? "Matched" : "Changed"}</dd></div><div><dt>Evidence</dt><dd>{result.validation_report_sha256.slice(0, 12)}</dd></div></dl>
    <div className="model-build-actions"><span title={result.validation_report}>{result.validation_report.split(/[\\/]/).at(-1)}</span><button className="quiet-button compact" onClick={onCompare}>Compare renders</button></div>
  </section>;
}

function BuildComparison({ source, built, client, gtaPath, onClose }: { source: ModelMaterialProjectResult; built: ModelMaterialBuildResult; client: DesktopClient; gtaPath: string; onClose: () => void }) {
  const output = built.built_project;
  return <div className="confirmation-backdrop model-comparison-backdrop" role="presentation"><section className="model-comparison-dialog" role="dialog" aria-modal="true" aria-labelledby="model-comparison-title">
    <header><div><span className="eyebrow">Compiled-output verification</span><h2 id="model-comparison-title">Source and rebuilt output</h2><p>Both viewports use the same camera and renderer. The output was decoded again after compilation.</p></div><button className="quiet-button" onClick={onClose} aria-label="Close render comparison">Close</button></header>
    <div className="model-comparison-grid">
      <section><div className="model-comparison-label"><span>Editable source snapshot</span><strong>{source.name}</strong><small>{source.summary.materials} materials · {source.summary.geometries} geometries</small></div><VehicleViewport client={client} source={source.viewport.source} entry={source.viewport.entry} edition={source.edition} gtaPath={gtaPath || null} model={source.name.replace(/\.[^.]+$/, "")} textureEntry={source.viewport.texture_entry} collisionEntry={source.viewport.collision_entry} /></section>
      <section><div className="model-comparison-label"><span>Verified rebuilt output</span><strong>{output.name}</strong><small>{output.summary.materials} materials · {output.summary.geometries} geometries</small></div><VehicleViewport client={client} source={output.viewport.source} entry={output.viewport.entry} edition={output.edition} gtaPath={gtaPath || null} model={output.name.replace(/\.[^.]+$/, "")} textureEntry={output.viewport.texture_entry} collisionEntry={output.viewport.collision_entry} /></section>
    </div>
    <footer><span>Output SHA-256</span><code>{built.output.sha256}</code></footer>
  </section></div>;
}

function MaterialConfirmation({ state, busy, onCancel, onConfirm }: { state: ReviewState; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const title = state.kind === "workspace" ? "Create editable material copy" : state.kind === "edit" ? "Commit reviewed material changes" : state.kind === "build" ? "Build verified native asset" : "Undo the latest material edit";
  const changes = state.kind === "edit" ? state.review.changes : [];
  return <div className="confirmation-backdrop" role="presentation"><section className="confirmation-dialog material-confirmation" role="dialog" aria-modal="true" aria-labelledby="material-confirmation-title"><div className="confirmation-heading"><span className="eyebrow">Guarded authoring</span><h2 id="material-confirmation-title">{title}</h2><p>{state.kind === "workspace" ? "The SDK will export and initialize a separate revisioned workspace." : state.kind === "edit" ? "Only the reviewed existing XML fields below will change." : state.kind === "build" ? "The native compiler will write a new asset, decode it again, and publish its evidence receipt." : "The latest verified history snapshot will be restored as a new revision."}</p></div>{state.kind === "workspace" && <dl className="confirmation-details"><div><dt>Source</dt><dd>{state.review.source}</dd></div><div><dt>Destination</dt><dd>{state.review.destination}</dd></div><div><dt>Edition</dt><dd>{state.review.edition}</dd></div><div><dt>Copy size</dt><dd>{formatBytes(state.review.source_size)}</dd></div></dl>}{state.kind === "edit" && <div className="material-review-changes">{changes.map((change) => <div key={change.field}><strong>{change.field}</strong><span>{change.before || "(empty)"}</span><span aria-hidden="true">→</span><span>{change.after || "(empty)"}</span></div>)}</div>}{state.kind === "build" && <><dl className="confirmation-details"><div><dt>Destination</dt><dd>{state.review.destination}</dd></div><div><dt>Evidence receipt</dt><dd>{state.review.validation_report}</dd></div><div><dt>Workspace revision</dt><dd>{state.review.revision}</dd></div><div><dt>Edition</dt><dd>{state.review.edition}</dd></div></dl><div className="model-build-checks">{state.review.checks.map((check) => <div key={check.key}><span aria-hidden="true">✓</span><strong>{check.label}</strong><small>{check.detail}</small></div>)}</div></>}<div className="confirmation-warning"><strong>{state.kind === "workspace" ? "Original remains immutable" : state.kind === "build" ? "New output only" : "Revision checked at apply time"}</strong><span>{state.kind === "workspace" ? "No game or package destination is written." : state.kind === "build" ? "The workspace and GTA V installation remain untouched. An existing file is never overwritten." : "If the workspace changed since review, this action will be rejected."}</span></div><div className="confirmation-actions"><button className="quiet-button" disabled={busy} onClick={onCancel}>Cancel</button><button className="primary-button" disabled={busy} onClick={onConfirm}>{busy ? "Applying…" : state.kind === "undo" ? "Undo edit" : state.kind === "workspace" ? "Create copy" : state.kind === "build" ? "Build asset" : "Commit changes"}</button></div></section></div>;
}
