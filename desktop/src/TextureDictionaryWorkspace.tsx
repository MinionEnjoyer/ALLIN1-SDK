import { convertFileSrc } from "@tauri-apps/api/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatBytes } from "./tokenize";
import type {
  DesktopClient,
  Envelope,
  TextureBuildResult,
  TextureBuildReview,
  TextureEditReview,
  TextureRecord,
  TextureWorkspacePreview,
  TextureWorkspaceReview,
  TextureWorkspaceSession,
} from "./types";

type ReviewState =
  | { kind: "workspace"; review: TextureWorkspaceReview; payload: Record<string, unknown> }
  | { kind: "edit"; review: TextureEditReview; payload: Record<string, unknown> }
  | { kind: "build"; review: TextureBuildReview; payload: Record<string, unknown> }
  | { kind: "undo"; payload: Record<string, unknown> };

function eventError(message: Envelope): string {
  const payload = message.payload as Record<string, unknown>;
  return String(payload.message ?? payload.error ?? "Texture operation failed.");
}

function completedResult<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(eventError(message));
  const result = (message.payload as Record<string, unknown>).result;
  if (!result || typeof result !== "object") throw new Error("Texture operation did not return a result.");
  return result as T;
}

function defaultWorkspaceName(path: string): string {
  const file = path.split(/[\\/]/).at(-1) ?? "textures";
  return `${file.replace(/\.[^.]+$/, "")}-textures`;
}

function texturePreviewSource(preview: TextureWorkspacePreview | null): string | null {
  const artifact = preview?.artifact;
  if (!artifact) return null;
  if (artifact.preview_url) return artifact.preview_url;
  return artifact.path.startsWith("/") ? artifact.path : convertFileSrc(artifact.path);
}

export default function TextureDictionaryWorkspace({ client, initialSource = "", onGuardChange }: { client: DesktopClient; initialSource?: string; onGuardChange?: (guarded: boolean) => void }) {
  const [source, setSource] = useState(initialSource.toLocaleLowerCase().endsWith(".ytd") ? initialSource : "");
  const [gtaPath, setGtaPath] = useState("");
  const [edition, setEdition] = useState("Enhanced");
  const [session, setSession] = useState<TextureWorkspaceSession | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<TextureWorkspacePreview | null>(null);
  const [newName, setNewName] = useState("");
  const [reviewState, setReviewState] = useState<ReviewState | null>(null);
  const [buildResult, setBuildResult] = useState<TextureBuildResult | null>(null);
  const [busy, setBusy] = useState(false);
  const readGeneration = useRef(0), readFlight = useRef(false), readJob = useRef(""), mounted = useRef(true);
  const quietJobs = useRef(new Set<string>());
  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; readGeneration.current++;
    for (const job of [readJob.current, ...quietJobs.current]) if (job) void client.cancelJob(job).catch(() => {});
  }; }, [client]);
  const [activeJob, setActiveJob] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const previewSequence = useRef(0);
  const guarded = busy || Boolean(reviewState) || Boolean(newName.trim());
  useEffect(() => { onGuardChange?.(guarded); }, [guarded, onGuardChange]);

  useEffect(() => {
    if (!initialSource.toLocaleLowerCase().endsWith(".ytd")) return;
    setSource(initialSource);
    setSession(null);
    setPreview(null);
  }, [initialSource]);

  const textures = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return (session?.textures ?? []).filter((item) =>
      !needle || `${item.name} ${item.format} ${item.usage}`.toLocaleLowerCase().includes(needle),
    );
  }, [query, session]);
  const selected = session?.textures.find((item) => item.name === selectedName) ?? session?.textures[0] ?? null;
  const previewSource = texturePreviewSource(preview);

  const loadSession = (loaded: TextureWorkspaceSession) => {
    setSession(loaded);
    setSource(loaded.source);
    setEdition(loaded.edition);
    setSelectedName((current) => loaded.textures.some((item) => item.name === current) ? current : loaded.textures[0]?.name ?? "");
    setPreview(null);
    setBuildResult(null);
  };

  const runJob = async <T,>(
    operation: Parameters<DesktopClient["startJob"]>[0],
    payload: Record<string, unknown>,
    revision: string,
    onResult: (loaded: T) => void,
    quiet = false,
    onError?: (message: string) => void,
  ) => {

    const isQuiet = quiet;
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
          if (onError) onError(detail); else setError(detail);
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
      if (onError) onError(detail); else setError(detail);
    }
  };

  useEffect(() => {
    if (!session || !selected) {
      setPreview(null);
      return;
    }
    const sequence = ++previewSequence.current;
    void runJob<TextureWorkspacePreview>(
      "preview_texture_workspace",
      { workspace: session.workspace, expected_state_sha256: session.state_sha256, texture_name: selected.name },
      `texture-preview|${session.state_sha256}|${selected.name}`,
      (loaded) => { if (sequence === previewSequence.current) setPreview(loaded); },
      true,
      (message) => { if (sequence === previewSequence.current) setError(message); },
    );
    return () => { previewSequence.current += 1; };
  }, [session?.state_sha256, selected?.name]);

  const chooseYtd = async () => {
    const selectedPath = await client.selectPath("texture_asset");
    if (!selectedPath) return;
    setSource(selectedPath);
    setSession(null);
    setPreview(null);
    setBuildResult(null);
    setError("");
    setNotice("YTD selected. Create an editable copy to inspect and change its textures.");
  };

  const chooseGame = async () => {
    const selectedPath = await client.selectPath("gta_folder");
    if (selectedPath) setGtaPath(selectedPath);
  };

  const openWorkspace = async () => {
    const workspace = await client.selectPath("texture_workspace");
    if (!workspace) return;
    await runJob<TextureWorkspaceSession>(
      "inspect_texture_workspace", { workspace }, `texture-workspace|${workspace}`,
      (loaded) => {
        loadSession(loaded);
        setNotice(`Editable texture dictionary opened at revision ${loaded.revision}.`);
      },
    );
  };

  const reviewWorkspace = async () => {
    if (!source.trim() || session) return;
    const parent = await client.selectPath("texture_workspace_parent");
    if (!parent) return;
    const payload = { source, parent, name: defaultWorkspaceName(source), edition, ...(gtaPath ? { gta_path: gtaPath } : {}) };
    await runJob<TextureWorkspaceReview>(
      "review_texture_workspace", payload, `texture-create|${source}|${parent}|${edition}|${gtaPath}`,
      (review) => setReviewState({ kind: "workspace", review, payload }),
    );
  };

  const reviewEdit = async (action: "replace" | "add" | "remove", texture: TextureRecord | null = selected) => {
    if (!session || (action !== "add" && !texture)) return;
    const textureName = action === "add" ? newName.trim() : texture!.name;
    if (!textureName) {
      setError("Enter a texture name before choosing its source image.");
      return;
    }
    let sourceImage: string | null = null;
    if (action !== "remove") {
      sourceImage = await client.selectPath("texture_source");
      if (!sourceImage) return;
    }
    const payload = {
      workspace: session.workspace,
      expected_state_sha256: session.state_sha256,
      action,
      texture_name: textureName,
      ...(sourceImage ? { source_image: sourceImage } : {}),
    };
    await runJob<TextureEditReview>(
      "review_texture_edit", payload,
      `texture-edit|${session.state_sha256}|${action}|${textureName}|${sourceImage ?? ""}`,
      (review) => setReviewState({ kind: "edit", review, payload }),
    );
  };

  const reviewBuild = async () => {
    if (!session) return;
    const destination = await client.selectTextureBuildDestination(session.source_name);
    if (!destination) return;
    const payload = { workspace: session.workspace, expected_state_sha256: session.state_sha256, destination, ...(gtaPath ? { gta_path: gtaPath } : {}) };
    await runJob<TextureBuildReview>(
      "review_texture_build", payload, `texture-build|${session.state_sha256}|${destination}`,
      (review) => setReviewState({ kind: "build", review, payload }),
    );
  };

  const confirmReview = async () => {
    const pending = reviewState;
    if (!pending || busy) return;
    setBusy(true);
    setError("");
    try {
      const operation = pending.kind === "workspace" ? "create_texture_workspace"
        : pending.kind === "edit" ? "apply_texture_edit"
        : pending.kind === "build" ? "apply_texture_build" : "apply_texture_history";
      const payload = pending.kind === "undo"
        ? { ...pending.payload, authoring_confirmed: true }
        : { ...pending.payload, review_sha256: pending.review.review_sha256, authoring_confirmed: true };
      const response = await client.textureAuthoringAction(operation, payload);
      if (pending.kind === "build") {
        setBuildResult(completedResult<TextureBuildResult>(response));
        setNotice("Native texture dictionary built, reparsed, and verified against the reviewed workspace state.");
      } else {
        const loaded = completedResult<TextureWorkspaceSession>(response);
        loadSession(loaded);
        if (pending.kind === "edit" && pending.review.action === "add") setNewName("");
        setNotice(pending.kind === "workspace" ? "Editable copy created. The original YTD remains immutable."
          : pending.kind === "undo" ? `Previous texture state restored at revision ${loaded.revision}.`
          : `Texture ${pending.review.action} committed at revision ${loaded.revision}.`);
      }
      setReviewState(null);
    } catch (reason) {
      setError(String(reason).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };

  const requestUndo = () => {
    if (!session?.can_undo) return;
    setReviewState({ kind: "undo", payload: { workspace: session.workspace, expected_state_sha256: session.state_sha256 } });
  };

  const cancel = async () => {
    const id = readJob.current;
    readGeneration.current++; readFlight.current = false; readJob.current = "";
    setBusy(false); setActiveJob("");
    setNotice("Inspection cancelled. No authoring changes were applied.");
    if (id) try { await client.cancelJob(id); } catch (reason) { setError(String(reason)); }
  };

  return (
    <section className="workspace-section texture-workspace" aria-labelledby="texture-workspace-title">
      <div className="workspace-heading">
        <div><span className="eyebrow">Native asset workbench</span><h2 id="texture-workspace-title">Texture Dictionary</h2><p>Inspect, replace, add, and remove YTD textures in a revisioned copy with bounded previews.</p></div>
        <div className="heading-actions">
          {busy && activeJob && <button className="quiet-button" onClick={() => void cancel()}>Cancel</button>}
          <button className="quiet-button" disabled={guarded} onClick={() => void openWorkspace()}>Open workspace</button>
          <button className="primary-button" disabled={guarded} onClick={() => void chooseYtd()}>Open YTD</button>
        </div>
      </div>
      <div className="model-material-source texture-source-strip">
        <label><span>{session ? "Immutable source snapshot" : "Texture dictionary"}</span><input value={source} readOnly={Boolean(session)} onChange={(event) => { setSource(event.target.value); setSession(null); }} placeholder="Select a loose .ytd asset" /></label>
        <label><span>Edition</span><select value={edition} disabled={Boolean(session)} onChange={(event) => setEdition(event.target.value)}><option>Enhanced</option><option>Legacy</option></select></label>
        <button className="quiet-button" disabled={busy || Boolean(session)} onClick={() => void chooseGame()} title={gtaPath || "Optional GTA V path"}>{gtaPath ? "Game selected" : "Select game"}</button>
        {session
          ? <><button className="quiet-button" disabled={guarded || !session.can_undo} onClick={requestUndo}>Undo edit</button><button className="primary-button" disabled={guarded} onClick={() => void reviewBuild()}>Build YTD</button></>
          : <button className="primary-button" disabled={busy || !source.trim()} onClick={() => void reviewWorkspace()}>{busy ? "Reviewing…" : "Create editable copy"}</button>}
      </div>
      {error && <div className="inline-error" role="alert"><strong>Operation rejected</strong><span>{error}</span></div>}
      {notice && <div className="model-material-notice" role="status"><span aria-hidden="true">●</span><span>{notice}</span></div>}
      {session && <div className="summary-row model-material-summary texture-summary">
        <strong>{session.source_name}</strong><span>{formatBytes(session.source_size)}</span><span>{session.texture_count} textures</span><span>Revision {session.revision}</span>
        <span className={`status-pill ${session.warnings.length ? "warning" : "success"}`}>{session.warnings.length ? `${session.warnings.length} warning${session.warnings.length === 1 ? "" : "s"}` : "Dependencies verified"}</span>
        <span className="source-path" title={session.state_sha256}>{session.state_sha256.slice(0, 12)} · guarded copy</span>
      </div>}
      {buildResult && <TextureBuildReceipt result={buildResult} />}
      <div className={`texture-grid${session ? " has-result" : ""}`}>
        <section className="model-material-pane texture-inventory-pane">
          <header><span className="pane-kicker">Dictionary</span><strong>Texture inventory</strong><small>{session?.texture_count ?? 0}</small></header>
          <label className="model-pane-filter"><span aria-hidden="true">⌕</span><input value={query} disabled={!session} onChange={(event) => setQuery(event.target.value)} placeholder="Filter name, format, or usage" aria-label="Filter textures" /></label>
          {session ? <>
            <div className="model-pane-list texture-list" role="listbox" aria-label="Textures">
              {textures.map((item) => <button key={item.name} className={item.name === selected?.name ? "selected" : ""} role="option" aria-selected={item.name === selected?.name} onClick={() => setSelectedName(item.name)}><span><strong>{item.name}</strong><small>{item.width} × {item.height} · {item.format.replace("D3DFMT_", "")}</small></span><span className="row-type">{item.mip_levels} mip</span></button>)}
              {!textures.length && <div className="texture-no-results">No textures match this filter.</div>}
            </div>
            <div className="texture-add-form">{newName && <button className="quiet-button" disabled={busy || !!reviewState} onClick={() => setNewName("")}>Reset texture draft</button>}<label><span>New texture name</span><input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="comet6_badge" /></label><button className="quiet-button" disabled={busy || !newName.trim()} onClick={() => void reviewEdit("add", null)}>Choose image and review</button></div>
          </> : <div className="model-material-empty"><strong>No editable dictionary</strong><p>Open an existing workspace, or select a loose YTD and create a guarded copy.</p></div>}
        </section>
        <section className="model-material-pane texture-preview-pane">
          <header><span className="pane-kicker">Selected texture</span><strong>{selected?.name ?? "Preview & evidence"}</strong><small>{selected ? `${selected.width} × ${selected.height}` : "No selection"}</small></header>
          {selected ? <>
            <div className="texture-preview-stage">
              {previewSource ? <img src={previewSource} alt={`Preview of ${selected.name}`} /> : <div className="texture-preview-empty"><strong>Preview unavailable</strong><span>{preview?.warning ?? "Rendering bounded texture preview…"}</span></div>}
            </div>
            <div className="texture-evidence">
              <dl>
                <div><dt>Format</dt><dd>{selected.format}</dd></div><div><dt>Usage</dt><dd>{selected.usage || "Unspecified"}</dd></div>
                <div><dt>Mip levels</dt><dd>{selected.mip_levels}</dd></div><div><dt>DDS size</dt><dd>{selected.size === null ? "Missing" : formatBytes(selected.size)}</dd></div>
                <div><dt>Dependency</dt><dd>{selected.file_name}</dd></div><div><dt>SHA-256</dt><dd title={selected.sha256 ?? ""}>{selected.sha256?.slice(0, 12) ?? "Unavailable"}</dd></div>
              </dl>
              {selected.warnings.map((warning) => <div className="texture-warning" key={warning}>{warning}</div>)}
              <div className="texture-actions"><button className="quiet-button" disabled={busy} onClick={() => void reviewEdit("replace")}>Replace image</button><button className="danger-button" disabled={busy} onClick={() => void reviewEdit("remove")}>Remove texture</button></div>
            </div>
          </> : <div className="model-material-empty"><strong>No texture selected</strong><p>Select a dictionary texture to render its bounded preview and inspect its native evidence.</p></div>}
        </section>
      </div>
      {reviewState && <TextureConfirmation state={reviewState} busy={busy} onCancel={() => setReviewState(null)} onConfirm={() => void confirmReview()} />}
    </section>
  );
}

function TextureBuildReceipt({ result }: { result: TextureBuildResult }) {
  const name = result.output.path.split(/[\\/]/).at(-1) ?? result.output.path;
  return <section className="model-build-receipt texture-build-receipt" aria-label="Verified texture build receipt"><div className="model-build-status" aria-hidden="true">✓</div><div className="model-build-summary"><span className="pane-kicker">Verified native YTD</span><strong>{name}</strong><small>{formatBytes(result.output.size)} · {result.output.sha256.slice(0, 12)} · revision {result.revision}</small></div><dl><div><dt>Reparsed</dt><dd>{result.validation.reparsed ? "Yes" : "No"}</dd></div><div><dt>Semantic XML</dt><dd>{result.validation.semantic_xml_match ? "Matched" : "Changed"}</dd></div><div><dt>Dependencies</dt><dd>{result.validation.dependency_count}</dd></div></dl><div className="model-build-actions"><span title={result.validation_report}>{result.validation_report.split(/[\\/]/).at(-1)}</span></div></section>;
}

function TextureConfirmation({ state, busy, onCancel, onConfirm }: { state: ReviewState; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const title = state.kind === "workspace" ? "Create editable texture copy" : state.kind === "edit" ? `${state.review.action[0].toUpperCase()}${state.review.action.slice(1)} reviewed texture` : state.kind === "build" ? "Build verified texture dictionary" : "Undo the latest texture edit";
  return <div className="confirmation-backdrop" role="presentation"><section className="confirmation-dialog material-confirmation" role="dialog" aria-modal="true" aria-labelledby="texture-confirmation-title"><div className="confirmation-heading"><span className="eyebrow">Guarded authoring</span><h2 id="texture-confirmation-title">{title}</h2><p>{state.kind === "workspace" ? "The native source will be decoded into a separate revisioned workspace." : state.kind === "edit" ? "The source image and current workspace digest were inspected without writing." : state.kind === "build" ? "The compiler will write a new YTD, decode it again, and publish an evidence receipt." : "The latest verified snapshot will be restored as a new revision."}</p></div>{state.kind === "workspace" && <dl className="confirmation-details"><div><dt>Source</dt><dd>{state.review.source}</dd></div><div><dt>Destination</dt><dd>{state.review.destination}</dd></div><div><dt>Edition</dt><dd>{state.review.edition}</dd></div><div><dt>Source size</dt><dd>{formatBytes(state.review.source_size)}</dd></div></dl>}{state.kind === "edit" && <><div className="material-review-changes">{state.review.changes.map((change) => <div key={change.field}><strong>{change.field}</strong><span>{change.before || "(empty)"}</span><span aria-hidden="true">→</span><span>{change.after || "(empty)"}</span></div>)}</div>{state.review.warning && <div className="texture-review-note">{state.review.warning}</div>}</>}{state.kind === "build" && <><dl className="confirmation-details"><div><dt>Destination</dt><dd>{state.review.destination}</dd></div><div><dt>Evidence receipt</dt><dd>{state.review.validation_report}</dd></div><div><dt>Revision</dt><dd>{state.review.revision}</dd></div><div><dt>State digest</dt><dd>{state.review.state_sha256}</dd></div></dl><div className="model-build-checks">{state.review.checks.map((check) => <div key={check.key}><span aria-hidden="true">✓</span><strong>{check.label}</strong><small>{check.detail}</small></div>)}</div></>}<div className="confirmation-warning"><strong>{state.kind === "workspace" ? "Original remains immutable" : state.kind === "build" ? "New output only" : "State checked at apply time"}</strong><span>{state.kind === "workspace" ? "No package or GTA V location is modified." : state.kind === "build" ? "Existing output files are never overwritten." : "If the texture dictionary or selected source changed since review, the action is rejected."}</span></div><div className="confirmation-actions"><button className="quiet-button" disabled={busy} onClick={onCancel}>Cancel</button><button className="primary-button" disabled={busy} onClick={onConfirm}>{busy ? "Applying…" : state.kind === "undo" ? "Undo edit" : state.kind === "workspace" ? "Create copy" : state.kind === "build" ? "Build YTD" : "Commit texture edit"}</button></div></section></div>;
}
