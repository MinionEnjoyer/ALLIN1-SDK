import { convertFileSrc } from "@tauri-apps/api/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DesktopClient, Envelope, VehicleViewportResult } from "./types";

type RenderMode = VehicleViewportResult["camera"]["render_mode"];
type RenderQuality = VehicleViewportResult["camera"]["quality"];

interface RenderRequest {
  yaw: number;
  pitch: number;
  lod: string;
  component: string;
  material: string;
  renderMode: RenderMode;
  collisionVisible: boolean;
  quality: RenderQuality;
}

interface DragState {
  pointerId: number;
  mode: "orbit" | "pan";
  originX: number;
  originY: number;
  yaw: number;
  pitch: number;
  panX: number;
  panY: number;
}

function responseMessage(response: Envelope): string {
  const payload = response.payload as Record<string, unknown>;
  return String(payload.message ?? payload.error ?? "Vehicle viewport request failed.");
}

function frameResult(response: Envelope): VehicleViewportResult {
  if (response.operation === "error") throw new Error(responseMessage(response));
  const result = (response.payload as Record<string, unknown>).result;
  if (!result || typeof result !== "object") {
    throw new Error("Vehicle viewport did not return a render artifact.");
  }
  return result as VehicleViewportResult;
}

function formatShaderNumber(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (Object.is(value, -0)) return "0";
  const absolute = Math.abs(value);
  if (absolute !== 0 && (absolute >= 100_000 || absolute < 0.0001)) {
    return value.toExponential(4).replace(/\.0+(?=e)/, "");
  }
  return Number(value.toPrecision(7)).toString();
}

function formatShaderVector(row: [number, number, number, number]): string {
  return `x ${formatShaderNumber(row[0])} y ${formatShaderNumber(row[1])} z ${formatShaderNumber(row[2])} w ${formatShaderNumber(row[3])}`;
}

function countLabel(count: number, singular: string): string {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

export default function VehicleViewport({
  client,
  source,
  entry,
  edition,
  gtaPath,
  model,
  textureEntry,
  collisionEntry,
  onResult,
  ariaLabel = "Interactive vehicle viewport",
  meshLabel = "Component",
}: {
  client: DesktopClient;
  source: string;
  entry: string;
  edition: string;
  gtaPath?: string | null;
  model: string;
  textureEntry?: string | null;
  collisionEntry?: string | null;
  onResult?: (result: VehicleViewportResult | null) => void;
  ariaLabel?: string;
  meshLabel?: string;
}) {
  const [result, setResult] = useState<VehicleViewportResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [yaw, setYaw] = useState(34);
  const [pitch, setPitch] = useState(24);
  const [lod, setLod] = useState("All");
  const [component, setComponent] = useState("All");
  const [material, setMaterial] = useState("All");
  const [renderMode, setRenderMode] = useState<RenderMode>("shaded");
  const [collisionVisible, setCollisionVisible] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragMode, setDragMode] = useState<DragState["mode"] | null>(null);
  const [materialOpen, setMaterialOpen] = useState(false);
  const [collisionOpen, setCollisionOpen] = useState(false);
  const [atlasOpen, setAtlasOpen] = useState(true);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const desired = useRef<RenderRequest | null>(null);
  const drag = useRef<DragState | null>(null);
  const camera = useRef({ yaw: 34, pitch: 24 });
  const selection = useRef({ lod: "All", component: "All", material: "All", renderMode: "shaded" as RenderMode });
  const collision = useRef(false);

  const requestFrame = useCallback(async (request: RenderRequest) => {
    desired.current = request;
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    const activeGeneration = generation.current;
    try {
      while (desired.current && activeGeneration === generation.current) {
        const next = desired.current;
        desired.current = null;
        try {
          const response = await client.renderVehicleModel({
            source,
            entry,
            edition,
            ...(gtaPath ? { gta_path: gtaPath } : {}),
            yaw: next.yaw,
            pitch: next.pitch,
            lod: next.lod,
            component: next.component,
            material: next.material,
            ...(textureEntry ? { texture_entry: textureEntry } : {}),
            ...(collisionEntry ? { collision_entry: collisionEntry } : {}),
            collision_visible: next.collisionVisible,
            render_mode: next.renderMode,
            quality: next.quality,
          });
          if (activeGeneration !== generation.current) return;
          const frame = frameResult(response);
          setResult(frame);
          setError("");
          onResult?.(frame);
        } catch (reason) {
          if (activeGeneration !== generation.current) return;
          setError(String(reason).replace(/^Error:\s*/, ""));
        }
      }
    } finally {
      if (activeGeneration === generation.current) setBusy(false);
      inFlight.current = false;
      if (desired.current && activeGeneration === generation.current) {
        void requestFrame(desired.current);
      }
    }
  }, [client, collisionEntry, edition, entry, gtaPath, onResult, source, textureEntry]);

  const queueCurrentFrame = useCallback((quality: RenderQuality, overrides: Partial<RenderRequest> = {}) => {
    const current = {
      yaw: camera.current.yaw,
      pitch: camera.current.pitch,
      lod: selection.current.lod,
      component: selection.current.component,
      material: selection.current.material,
      renderMode: selection.current.renderMode,
      collisionVisible: collision.current,
      quality,
      ...overrides,
    };
    void requestFrame(current);
  }, [requestFrame]);

  useEffect(() => {
    generation.current += 1;
    desired.current = null;
    inFlight.current = false;
    camera.current = { yaw: 34, pitch: 24 };
    selection.current = { lod: "All", component: "All", material: "All", renderMode: "shaded" };
    collision.current = false;
    setResult(null);
    setError("");
    setBusy(true);
    setYaw(34);
    setPitch(24);
    setLod("All");
    setComponent("All");
    setMaterial("All");
    setRenderMode("shaded");
    setCollisionVisible(false);
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setMaterialOpen(false);
    setCollisionOpen(false);
    setAtlasOpen(true);
    onResult?.(null);
    queueCurrentFrame("final", {
      yaw: 34,
      pitch: 24,
      lod: "All",
      component: "All",
      material: "All",
      renderMode: "shaded",
      collisionVisible: false,
    });
    return () => {
      generation.current += 1;
      desired.current = null;
    };
  }, [collisionEntry, edition, entry, gtaPath, onResult, queueCurrentFrame, source, textureEntry]);

  const imageSource = result?.artifact
    ? result.artifact.preview_url ?? convertFileSrc(result.artifact.path)
    : null;
  const lods = result?.scene.lods ?? [];
  const components = useMemo(() => {
    const records = result?.scene.components ?? [];
    const eligible = lod === "All"
      ? records
      : records.filter((item) => item.lod.toLocaleLowerCase() === lod.toLocaleLowerCase());
    return [...new Set(eligible.map((item) => item.name))];
  }, [lod, result?.scene.components]);
  const materials = useMemo(() => {
    const records = result?.scene.materials ?? [];
    return records.filter((item) => (
      (lod === "All" || item.lods.some((value) => value.toLocaleLowerCase() === lod.toLocaleLowerCase()))
      && (component === "All" || item.components.some((value) => value.toLocaleLowerCase() === component.toLocaleLowerCase()))
    ));
  }, [component, lod, result?.scene.materials]);
  const selectedMaterial = result?.scene.materials?.find((item) => item.name === material) ?? null;
  const selectedParameters = selectedMaterial?.parameters ?? [];
  const selectedParameterCount = selectedMaterial?.parameter_count ?? selectedParameters.length;
  const textureDictionary = result?.texture_dictionary ?? null;
  const collisionDictionary = result?.collision_dictionary ?? null;
  const uvReady = result?.camera.render_mode === "uvs";
  const uvAtlas = renderMode === "uvs" && uvReady ? result?.uv_atlas ?? null : null;
  const textureSource = textureDictionary?.artifact
    ? textureDictionary.artifact.preview_url ?? convertFileSrc(textureDictionary.artifact.path)
    : null;
  const uvAtlasSource = uvAtlas?.artifact
    ? uvAtlas.artifact.preview_url ?? convertFileSrc(uvAtlas.artifact.path)
    : null;
  const uvResolved = uvReady ? Number(result.metadata.model_render_uv_resolved_triangle_count ?? 0) : null;
  const uvUnresolved = uvReady ? Number(result.metadata.model_render_uv_unresolved_triangle_count ?? 0) : null;
  const uvDegenerate = uvReady ? Number(result.metadata.model_render_uv_degenerate_triangle_count ?? 0) : null;
  const uvMissing = uvReady ? Number(result.metadata.model_render_uv_missing_triangle_count ?? 0) : null;
  const uvCoverage = uvReady ? Number(result.metadata.model_render_uv_coverage_percent ?? 0) : null;

  const setCamera = (nextYaw: number, nextPitch: number, quality: RenderQuality) => {
    const normalizedYaw = ((nextYaw % 360) + 360) % 360;
    const normalizedPitch = Math.max(-89, Math.min(89, nextPitch));
    camera.current = { yaw: normalizedYaw, pitch: normalizedPitch };
    setYaw(normalizedYaw);
    setPitch(normalizedPitch);
    queueCurrentFrame(quality, { yaw: normalizedYaw, pitch: normalizedPitch });
  };

  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setCamera(34, 24, "final");
  };

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 && event.button !== 1) return;
    const mode = event.shiftKey || event.button === 1 ? "pan" : "orbit";
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      mode,
      originX: event.clientX,
      originY: event.clientY,
      yaw: camera.current.yaw,
      pitch: camera.current.pitch,
      panX: pan.x,
      panY: pan.y,
    };
    setDragMode(mode);
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - active.originX;
    const deltaY = event.clientY - active.originY;
    if (active.mode === "pan") {
      setPan({ x: active.panX + deltaX, y: active.panY + deltaY });
      return;
    }
    setCamera(active.yaw + deltaX * 0.45, active.pitch - deltaY * 0.3, "interactive");
  };

  const handlePointerEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    setDragMode(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (active.mode === "orbit") queueCurrentFrame("final");
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "=", "-", "0"].includes(event.key)) {
      event.preventDefault();
    }
    if (event.key === "ArrowLeft") setCamera(camera.current.yaw - 5, camera.current.pitch, "final");
    if (event.key === "ArrowRight") setCamera(camera.current.yaw + 5, camera.current.pitch, "final");
    if (event.key === "ArrowUp") setCamera(camera.current.yaw, camera.current.pitch + 4, "final");
    if (event.key === "ArrowDown") setCamera(camera.current.yaw, camera.current.pitch - 4, "final");
    if (event.key === "+" || event.key === "=") setZoom((value) => Math.min(3, value * 1.12));
    if (event.key === "-") setZoom((value) => Math.max(0.55, value / 1.12));
    if (event.key === "0") resetView();
  };

  return (
    <section className="vehicle-viewport" aria-label={ariaLabel}>
      <div className="vehicle-viewport-toolbar">
        <div className="vehicle-render-modes" role="group" aria-label="Render mode">
          {(["shaded", "textured", "materials", "uvs", "wireframe"] as RenderMode[]).map((mode) => <button key={mode} type="button" className={renderMode === mode ? "active" : ""} aria-pressed={renderMode === mode} title={mode === "textured" ? "Diagnostic UV0 sampling from the linked texture dictionary" : mode === "uvs" ? "Show UV0 coverage and linked-texture resolution on the model" : undefined} onClick={() => {
            setRenderMode(mode);
            if (mode === "uvs") setAtlasOpen(true);
            selection.current.renderMode = mode;
            queueCurrentFrame("final", { renderMode: mode });
          }}>{mode === "materials" ? "Material IDs" : mode === "uvs" ? "UVs" : mode[0].toLocaleUpperCase() + mode.slice(1)}</button>)}
        </div>
        <button type="button" className={`quiet-button compact vehicle-collision-toggle ${collisionVisible ? "active" : ""}`} aria-label="Collision overlay" aria-pressed={collisionVisible} disabled={!collisionEntry} title={collisionEntry ? "Overlay the package-owned YBN in the same camera" : "No same-stem package YBN was resolved"} onClick={() => {
          const next = !collision.current;
          collision.current = next;
          setCollisionVisible(next);
          if (next) setCollisionOpen(true);
          queueCurrentFrame("final", { collisionVisible: next });
        }}><i aria-hidden="true" />Collision</button>
        <button type="button" className="quiet-button compact vehicle-fit-button" onClick={resetView}>Reset view</button>
      </div>

      <div className="vehicle-viewport-selectors">
        <label htmlFor={`viewport-lod-${model}`}><span>LOD</span><select id={`viewport-lod-${model}`} value={lod} onChange={(event) => {
          const next = event.target.value;
          setLod(next);
          setComponent("All");
          setMaterial("All");
          selection.current = { ...selection.current, lod: next, component: "All", material: "All" };
          queueCurrentFrame("final", { lod: next, component: "All", material: "All" });
        }}><option value="All">All geometry</option>{lods.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label htmlFor={`viewport-component-${model}`}><span>{meshLabel}</span><select id={`viewport-component-${model}`} value={component} onChange={(event) => {
          const next = event.target.value;
          setComponent(next);
          setMaterial("All");
          selection.current = { ...selection.current, component: next, material: "All" };
          queueCurrentFrame("final", { component: next, material: "All" });
        }}><option value="All">All components</option>{components.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
        <label htmlFor={`viewport-material-${model}`}><span>Surface</span><select id={`viewport-material-${model}`} value={material} onChange={(event) => {
          const next = event.target.value;
          setMaterial(next);
          if (next !== "All") setMaterialOpen(true);
          selection.current.material = next;
          queueCurrentFrame("final", { material: next });
        }}><option value="All">All materials</option>{materials.map((item) => <option key={`${item.index}-${item.name}`} value={item.name}>{item.name}</option>)}</select></label>
      </div>

      {renderMode === "uvs" && <div className="vehicle-uv-legend" aria-label="UV coverage legend" aria-live="polite">
        <span className="resolved"><i aria-hidden="true" /><b>UV + texture</b><strong>{uvResolved === null ? "—" : uvResolved.toLocaleString()}</strong></span>
        <span className="unresolved"><i aria-hidden="true" /><b>UV only</b><strong>{uvUnresolved === null ? "—" : uvUnresolved.toLocaleString()}</strong></span>
        <span className="degenerate"><i aria-hidden="true" /><b>Degenerate</b><strong>{uvDegenerate === null ? "—" : uvDegenerate.toLocaleString()}</strong></span>
        <span className="missing"><i aria-hidden="true" /><b>Missing UV0</b><strong>{uvMissing === null ? "—" : uvMissing.toLocaleString()}</strong></span>
      </div>}

      {collisionVisible && collisionDictionary && <div className="vehicle-collision-legend" aria-label="Collision overlay legend" aria-live="polite">
        <span className="exact"><i aria-hidden="true" /><b>Triangle mesh</b><strong>{(collisionDictionary.primitive_counts.find((item) => item.kind === "Triangle")?.count ?? 0).toLocaleString()}</strong></span>
        <span className="diagnostic"><i aria-hidden="true" /><b>Box hulls</b><strong>{(collisionDictionary.primitive_counts.find((item) => item.kind === "Box")?.count ?? 0).toLocaleString()}</strong></span>
        <span className="unrendered"><i aria-hidden="true" /><b>Count only</b><strong>{collisionDictionary.unrendered_polygon_count.toLocaleString()}</strong></span>
      </div>}

      <div
        className={`vehicle-viewport-stage ${dragMode ? `is-${dragMode}` : ""}`}
        tabIndex={0}
        aria-label={`${model} model view. Drag to orbit; hold Shift and drag to pan.`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onWheel={(event) => {
          event.preventDefault();
          setZoom((value) => Math.max(0.55, Math.min(3, value * (event.deltaY < 0 ? 1.12 : 1 / 1.12))));
        }}
        onKeyDown={handleKeyDown}
      >
        {imageSource && <img
          src={imageSource}
          alt={`Rendered native geometry for ${model}`}
          draggable={false}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
        />}
        {!imageSource && busy && <div className="vehicle-viewport-loading"><span className="activity-dot busy" /><strong>Decoding native geometry</strong><span>The package member is being validated before React receives a frame.</span></div>}
        {busy && imageSource && <div className="vehicle-viewport-rendering"><span className="activity-dot busy" />Rendering {dragMode === "orbit" ? "interactive" : "detailed"} frame</div>}
        <div className="vehicle-viewport-hud"><span>{yaw.toFixed(0)}° yaw · {pitch.toFixed(0)}° pitch</span><span>{Math.round(zoom * 100)}%</span></div>
      </div>

      {error && <div className="vehicle-viewport-error" role="alert"><strong>Native view unavailable</strong><span>{error}</span><button className="quiet-button" disabled={busy} onClick={() => queueCurrentFrame("final")}>Retry native preview</button></div>}
      <div className="vehicle-viewport-footer">
        <span>{collisionVisible ? "Package YBN overlay · same model-local camera" : renderMode === "textured" ? "UV0 texture diagnostic · linked YTD pixels" : renderMode === "uvs" ? `${uvCoverage === null ? "—" : uvCoverage.toFixed(1)}% valid UV0 · rendered sample` : "Drag orbit · Shift-drag pan · wheel zoom"}</span>
        {result && <span>{Number(result.metadata.model_rendered_triangle_count ?? result.metadata.model_rendered_triangles ?? 0).toLocaleString()} rendered triangles · {result.scene.component_count} mesh parts</span>}
      </div>

      {uvAtlas && <section className="vehicle-uv-atlas-inspector" aria-label="UV0 island atlas evidence">
        <button type="button" className="vehicle-material-heading" aria-expanded={atlasOpen} onClick={() => setAtlasOpen((value) => !value)}>
          <div><span className="section-kicker">UV0 TOPOLOGY</span><strong>{countLabel(uvAtlas.island_count, "island")}</strong></div>
          <span>{uvAtlas.returned_texture_group_count}/{uvAtlas.texture_group_count} texture groups · {uvAtlas.rendered_triangle_count.toLocaleString()} drawn<b aria-hidden="true">›</b></span>
        </button>
        {atlasOpen && <div className="vehicle-uv-atlas-layout">
          <div className="vehicle-uv-atlas-image">
            {uvAtlasSource && <img src={uvAtlasSource} alt={`Flattened UV0 island atlas for ${model}`} />}
            <span>{uvAtlas.sampled ? `${uvAtlas.sampled_triangle_count.toLocaleString()} of ${uvAtlas.source_triangle_count.toLocaleString()} triangles sampled` : `${uvAtlas.source_triangle_count.toLocaleString()} source triangles`} · {uvAtlas.seam_triangle_count.toLocaleString()} cross-tile seams</span>
          </div>
          <div className="vehicle-uv-group-list" role="list" aria-label="UV texture groups">
            {uvAtlas.texture_groups.slice(0, uvAtlas.returned_texture_group_count).map((group) => <div key={group.name} role="listitem" className={group.resolved ? "resolved" : "unresolved"}>
              <span><strong>{group.name}</strong><small>{group.material_names.join(", ") || "Unbound material"}</small></span>
              <span><b>{group.island_count.toLocaleString()}</b><small>islands</small></span>
              <span><b>{group.rendered_triangle_count.toLocaleString()}</b><small>drawn</small></span>
              <span><b>{group.seam_triangle_count.toLocaleString()}</b><small>seams</small></span>
            </div>)}
            <p>{uvAtlas.fidelity}</p>
          </div>
        </div>}
      </section>}

      {collisionDictionary && <section className="vehicle-collision-inspector" aria-label="Collision ownership evidence">
        <button type="button" className="vehicle-material-heading" aria-expanded={collisionOpen} onClick={() => setCollisionOpen((value) => !value)}>
          <div><span className="section-kicker">COLLISION OWNERSHIP</span><strong>{collisionDictionary.name}</strong></div>
          <span>{countLabel(collisionDictionary.polygon_count, "shape")} · {collisionDictionary.overlay_polygon_count.toLocaleString()} overlay-ready<b aria-hidden="true">›</b></span>
        </button>
        {collisionOpen && <div className="vehicle-collision-evidence">
          <dl>
            <div><dt>Package member</dt><dd title={collisionDictionary.path}>{collisionDictionary.path}</dd></div>
            <div><dt>Geometry groups</dt><dd>{collisionDictionary.geometry_count.toLocaleString()}</dd></div>
            <div><dt>Source vertices</dt><dd>{collisionDictionary.vertex_count.toLocaleString()}</dd></div>
            <div><dt>Materials</dt><dd>{collisionDictionary.material_count.toLocaleString()}</dd></div>
            <div><dt>Bounds</dt><dd>{collisionDictionary.bounds ? collisionDictionary.bounds.size.map((value) => formatShaderNumber(value)).join(" × ") : "Unavailable"}</dd></div>
            <div><dt>SHA-256</dt><dd title={collisionDictionary.sha256}>{collisionDictionary.sha256.slice(0, 12)}…</dd></div>
          </dl>
          <div className="vehicle-collision-primitives" role="list" aria-label="Collision primitive types">
            {collisionDictionary.primitive_counts.map((item) => <div key={item.kind} role="listitem" className={item.fidelity === "exact mesh" ? "exact" : item.fidelity === "diagnostic hull" ? "diagnostic" : "unrendered"}><span><strong>{item.kind}</strong><small>{item.fidelity}</small></span><b>{item.count.toLocaleString()}</b></div>)}
          </div>
          {collisionDictionary.warnings.map((warning, index) => <p key={`${warning}-${index}`} className="vehicle-collision-warning">{warning}</p>)}
        </div>}
      </section>}

      {result && <section className="vehicle-material-inspector" aria-label="Model materials and textures">
        <button type="button" className="vehicle-material-heading" aria-expanded={materialOpen} onClick={() => setMaterialOpen((value) => !value)}>
          <div><span className="section-kicker">SURFACE EVIDENCE</span><strong>{selectedMaterial?.name ?? `${result.scene.surface_count} material surfaces`}</strong></div>
          <span>{selectedMaterial ? `${countLabel(selectedMaterial.texture_bindings.length, "sampler")} · ${countLabel(selectedParameterCount, "constant")}` : textureDictionary ? `${textureDictionary.texture_count} textures in ${textureDictionary.name}` : "No linked YTD"}<b aria-hidden="true">›</b></span>
        </button>
        {materialOpen && <div className="vehicle-material-layout">
          <div className="vehicle-material-list" role="list" aria-label="Decoded model materials">
            {materials.map((item) => <button
              key={`${item.index}-${item.name}`}
              type="button"
              role="listitem"
              className={material === item.name ? "active" : ""}
              aria-pressed={material === item.name}
              onClick={() => {
                const next = material === item.name ? "All" : item.name;
                setMaterial(next);
                setMaterialOpen(true);
                selection.current.material = next;
                queueCurrentFrame("final", { material: next });
              }}
            ><span><strong>{item.name}</strong><small>{item.geometry_count} geometries · {item.triangle_count.toLocaleString()} triangles</small></span><span className="material-binding-count" title={`${countLabel(item.texture_bindings.length, "sampler binding")} · ${countLabel(item.parameter_count ?? item.parameters?.length ?? 0, "shader constant")}`}>S{item.texture_bindings.length} · C{item.parameter_count ?? item.parameters?.length ?? 0}</span></button>)}
            {!materials.length && <p className="vehicle-material-empty">No materials resolve inside the current geometry filter.</p>}
          </div>
          <div className="vehicle-texture-evidence">
            {textureSource && <img src={textureSource} alt={`Texture contact sheet for ${textureDictionary?.name ?? model}`} />}
            <div className="vehicle-evidence-subheading"><span>SAMPLER BINDINGS</span><strong>{selectedMaterial?.texture_bindings.length ?? "—"}</strong></div>
            <div className="vehicle-texture-bindings">
              {(selectedMaterial?.texture_bindings ?? []).map((binding) => {
                const texture = textureDictionary?.textures.find((item) => item.name.toLocaleLowerCase() === binding.name.toLocaleLowerCase());
                return <div key={`${binding.slot}-${binding.name}`} className={`vehicle-texture-binding ${binding.resolved === true ? "resolved" : binding.resolved === false ? "missing" : "unknown"}`}>
                  <span><strong>{binding.name || "Unbound"}</strong><small>{binding.slot}</small></span>
                  <span>{texture ? `${texture.width}×${texture.height} · ${texture.format}` : binding.resolved === false ? "Not in linked YTD" : "Dictionary not inspected"}</span>
                </div>;
              })}
              {!selectedMaterial && <p>Select a surface to inspect its sampler slots and exact texture resolution.</p>}
              {selectedMaterial && !selectedMaterial.texture_bindings.length && <p>This material exposes no texture sampler bindings.</p>}
            </div>
            <div className="vehicle-evidence-subheading"><span>SHADER CONSTANTS</span><strong>{selectedMaterial ? selectedParameterCount : "—"}</strong></div>
            <div className="vehicle-shader-parameters">
              {selectedParameters.map((parameter, parameterIndex) => <div key={`${parameter.name}-${parameter.source_type}-${parameterIndex}`} className="vehicle-shader-parameter">
                <span><strong>{parameter.name || "Unnamed parameter"}</strong><small>{parameter.source_type === "Array" ? `Vector4 array · ${parameter.values.length} rows` : "Vector4"}{parameter.record_count > 1 ? ` · ${parameter.record_count} records` : ""}</small></span>
                <span className="vehicle-parameter-values">
                  {parameter.values.map((row, rowIndex) => <code key={rowIndex} aria-label={formatShaderVector(row)}><i>x</i> {formatShaderNumber(row[0])} <i>y</i> {formatShaderNumber(row[1])} <i>z</i> {formatShaderNumber(row[2])} <i>w</i> {formatShaderNumber(row[3])}</code>)}
                  {!parameter.values.length && <code>No value rows</code>}
                </span>
              </div>)}
              {!selectedMaterial && <p>Select a surface to inspect its exact numeric shader constants.</p>}
              {selectedMaterial && !selectedParameters.length && <p>This material exposes no numeric Vector4 or array parameters.</p>}
            </div>
          </div>
        </div>}
      </section>}
    </section>
  );
}
