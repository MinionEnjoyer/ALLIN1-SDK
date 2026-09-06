import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";
import "./GraphWorkbench.css";
import NodeCanvas from "./NodeCanvas";
import { childOf, nodeColor, organizeNodes, PALETTE, parentOf, searchNodes, sortNodes, type ColorKey, type ColorMode, type LayoutMode, type SortMode } from "./graphView";

interface Node { id: string; type: string; name?: string; x: number; y: number; source?: string; size?: number; sha256?: string; config?: Record<string, string> }
interface Edge { parent?: string; child?: string; from?: string; to?: string; from_port?: string; to_port?: string }
interface Semantic { entities: (Node & { source_root: string; edition: string; metadata: Record<string, unknown> })[]; relations: { source: string; target: string; role: string }[]; findings: { node_id?: string; message: string; severity: string }[]; summary: Record<string, unknown> }
interface Document { semantic?: Semantic; schema_version: number; operation: string; nodes: Node[]; edges?: Edge[]; links?: Edge[]; root_id?: string; source_id?: string; package_graph?: string; template?: string; [key: string]: unknown }
interface Spec { title: string; input_types: string[]; output_type: string | null; required_config: string[]; optional_config: string[] }
interface Session extends WorkspaceResult { workspace: string | null; document: Document; issues: string[]; node_specs?: Record<string, Spec>; source_node?: Omit<Node, "id" | "x" | "y"> }
const serial = (value: unknown) => JSON.stringify(value);
const unique = (nodes: Node[]) => { let id = 1; while (nodes.some(node => node.id === `node_${id}`)) id++; return `node_${id}`; };
const blankGraph = (): Document => ({ schema_version: 1, operation: "rpf_package_graph", root_id: "root", nodes: [{ id: "root", type: "archive", name: "dlc.rpf", x: 40, y: 40 }], edges: [] });
const templates = { validate: "Validate only", "loose-export": "Loose authoring tree", "verified-build": "Verified RPF build", "compact-release": "Compact verified release", "origin-change-plan": "Imported-origin plan" };


export default function GraphWorkbench({ client, module, onGuardChange, onOpenAsset, onOpenVehicle, initialSource = "", initialFocus = "", initialFocusSerial = 0 }: { client: DesktopClient; module: "graph" | "program"; onGuardChange: (guarded: boolean) => void; onOpenAsset?: (source: string) => void; onOpenVehicle?: (source: string, model: string) => void; initialSource?: string; initialFocus?: string; initialFocusSerial?: number }) {
  const [session, setSession] = useState<Session | null>(null), [document, setDocument] = useState<Document | null>(null), [selected, setSelected] = useState("");
  const [filename, setFilename] = useState(module === "graph" ? "rpf-graph.json" : "rpf-program.json"), [outputName, setOutputName] = useState(module === "graph" ? "materialized-tree" : "program-report.json");
  const [template, setTemplate] = useState("loose-export"), [game, setGame] = useState(""), [query, setQuery] = useState("");
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("hierarchy"), [colorMode, setColorMode] = useState<ColorMode>("kind");
  const [colorFilter, setColorFilter] = useState<ColorKey | "all">("all"), [sortMode, setSortMode] = useState<SortMode>("color");
  const [canvasOnly, setCanvasOnly] = useState(false), [showRelations, setShowRelations] = useState(true);
  const [focusRequest, setFocusRequest] = useState<{ id: string; serial: number } | null>(null), focusSerial = useRef(0);
  const [name, setName] = useState(""), [config, setConfig] = useState<Record<string, string>>({});
  const work = useAuthoringWorkspace(client, module, value => {
    const s = value as Session;
    if (!Array.isArray(s.document?.nodes) || !Array.isArray(s.issues) || s.document.operation !== (module === "graph" ? "rpf_package_graph" : "rpf_package_program")) throw new Error("Invalid node document evidence");
    if (s.source_node && document && session) {
      if (s.state_sha256 !== session.state_sha256) throw new Error("Graph changed while choosing a source; reopen before adding it");
      const id = unique(document.nodes), parent = document.nodes.find(n => n.id === selected && ["archive", "directory"].includes(n.type))?.id || document.root_id!;
      const node: Node = { ...s.source_node, id, x: 340, y: 80 + document.nodes.length * 90 };
      setDocument({ ...document, nodes: [...document.nodes, node], edges: [...(document.edges ?? []), { parent, child: id }] }); chooseNode(node); return;
    }
    setSession(s); setDocument(s.document); chooseNode(s.document.nodes[0]); setQuery(""); setColorFilter("all"); setFocusRequest(null);
  });
  const current = document?.nodes.find(n => n.id === selected) ?? document?.semantic?.entities.find(n => n.id === selected);
  const semanticEntity = document?.semantic?.entities.find(n => n.id === selected);
  const formDirty = !!current && (name !== (current.name ?? "") || serial(config) !== serial(current.config ?? {}));
  const dirty = !!document && (!session?.workspace || serial(document) !== serial(session.document) || formDirty);
  useEffect(() => { onGuardChange(dirty || work.locked); }, [dirty, work.locked, onGuardChange]);
  const openedSource = useRef("");
  useEffect(() => {
    if (!initialSource || openedSource.current === initialSource || dirty || work.locked) return;
    openedSource.current = initialSource;
    void work.run("inspect_authoring_workspace", { workspace: initialSource });
  }, [initialSource, dirty, work.locked]);
  function chooseNode(node?: Node) { setSelected(node?.id ?? ""); setName(node?.name ?? ""); setConfig(node?.config ?? {}); }
  const select = (id: string) => { if (!work.locked && !formDirty) chooseNode(document?.nodes.find(n => n.id === id) ?? document?.semantic?.entities.find(n => n.id === id)); };
  const allNodes = useMemo(() => [...(document?.nodes ?? []), ...(showRelations ? document?.semantic?.entities ?? [] : [])], [document, showRelations]);
  const allEdges = useMemo(() => [...(document?.edges ?? document?.links ?? []), ...(showRelations ? document?.semantic?.relations.map(r => ({ parent: r.source, child: r.target })) ?? [] : [])], [document, showRelations]);
  const findings = useMemo(() => document?.semantic?.findings ?? [], [document]);
  const matches = useMemo(() => searchNodes(allNodes, query), [allNodes, query]);
  const matchIds = useMemo(() => query.trim() ? new Set(matches.map(n => n.id)) : null, [matches, query]);
  const listedNodes = useMemo(() => sortNodes(query.trim() ? matches : allNodes, query.trim() ? "name" : sortMode, colorMode, findings)
    .filter(node => colorFilter === "all" || nodeColor(node, colorMode, findings) === colorFilter), [allNodes, matches, query, sortMode, colorMode, findings, colorFilter]);
  const colors = useMemo(() => [...new Set(sortNodes(allNodes, "color", colorMode, findings).map(node => nodeColor(node, colorMode, findings)))], [allNodes, colorMode, findings]);
  const focusNode = (id: string) => {
    if (work.locked || formDirty) return;
    select(id); setColorFilter("all"); setFocusRequest({ id, serial: ++focusSerial.current });
  };
  const appliedLaunchFocus = useRef("");
  useEffect(() => {
    const pathKey = (value: string) => value.replace(/^\\\\\?\\/, "").replaceAll("\\", "/").toLowerCase();
    if (!initialFocus || !session?.workspace || pathKey(session.workspace) !== pathKey(initialSource) || work.locked || formDirty) return;
    const key = `${initialSource}:${initialFocusSerial}:${initialFocus}`;
    if (appliedLaunchFocus.current === key || !allNodes.some(node => node.id === initialFocus)) return;
    appliedLaunchFocus.current = key;
    setQuery(""); focusNode(initialFocus);
  }, [initialFocus, initialFocusSerial, initialSource, session, allNodes, work.locked, formDirty]);
  useEffect(() => {
    if (!query.trim() || !matches.length || work.locked || formDirty) return;
    const timer = window.setTimeout(() => focusNode(matches[0].id), 180);
    return () => window.clearTimeout(timer);
  }, [query, matches, work.locked, formDirty]);
  const nextMatch = (direction: number) => {
    if (!matches.length) return;
    const index = matches.findIndex(n => n.id === selected);
    focusNode(matches[(index + direction + matches.length) % matches.length].id);
  };
  const open = async () => { const chosen = await work.choose(module === "graph" ? "graph_document" : "program_document"); if (chosen) await work.run("inspect_authoring_workspace", { workspace: chosen }); };
  const create = async () => {
    if (module === "graph") { const doc = blankGraph(); setSession(null); setDocument(doc); chooseNode(doc.nodes[0]); }
    else { const graph = await work.choose("graph_document"); if (graph) await work.run("inspect_authoring_workspace", { graph, template }); }
  };
  const folder = async () => { const source = await work.choose("graph_source"); if (source) await work.run("inspect_authoring_workspace", { source }); };
  const importArchive = async () => {
    const archive = await work.choose("rpf"); if (!archive) return;
    const selectedGame = await work.choose("gta_folder"); if (!selectedGame) return;
    const parent = await work.choose("authoring_parent"); if (!parent) return;
    setGame(selectedGame);
    const name = archive.split(/[\\/]/).pop()!.replace(/\.rpf$/i, "") + "-graph";
    await work.run("review_workspace_action", { action: "import_archive", archive, gta_path: selectedGame, destination: parent.replace(/[\\/]$/, "") + "/" + name });
  };
  const importPackage = async (folder: boolean) => {
    const source = await work.choose(folder ? "graph_source" : "package"); if (!source) return;
    const parent = await work.choose("authoring_parent"); if (!parent) return;
    await work.run("review_workspace_action", { action: "import_package", source, destination: parent.replace(/[\\\\/]$/, "") + "/package-graph-workspace" });
  };
  const addFile = async () => { const file = await work.choose("binary_source"); if (file && session?.workspace) await work.run("inspect_authoring_workspace", { workspace: session.workspace, source_file: file }); };
  const modify = (id: string, changes: Partial<Node>) => { if (document) setDocument({ ...document, nodes: document.nodes.map(n => n.id === id ? { ...n, ...changes } : n), ...(document.semantic ? { semantic: { ...document.semantic, entities: document.semantic.entities.map(n => n.id === id ? { ...n, ...changes } : n) } } : {}) }); };
  const add = (type: string) => {
    if (!document) return;
    const id = unique(document.nodes), node: Node = { id, type, x: 80 + document.nodes.length % 4 * 280, y: 160 + Math.floor(document.nodes.length / 4) * 120,
      ...(module === "graph" ? { name: type === "archive" ? `${id}.rpf` : "New directory" } : { config: {} }) };
    const parent = current && ["archive", "directory"].includes(current.type) ? current.id : document.root_id!;
    setDocument({ ...document, nodes: [...document.nodes, node], ...(module === "graph" ? { edges: [...document.edges!, { parent, child: id }] } : {}) }); chooseNode(node);
  };
  const remove = () => {
    if (!document || !current || current.id === (document.root_id || document.source_id)) return;
    const removed = new Set([current.id]), edges = document.edges ?? document.links ?? [];
    if (module === "graph") { for (let i = 0; i < document.nodes.length; i++) for (const e of edges) if (removed.has(parentOf(e))) removed.add(childOf(e)); }
    const remaining = edges.filter(e => !removed.has(parentOf(e)) && !removed.has(childOf(e)));
    const semantic = document.semantic;
    setDocument({ ...document, nodes: document.nodes.filter(n => !removed.has(n.id)), ...(module === "graph" ? { edges: remaining } : { links: remaining }),
      ...(semantic ? { semantic: { ...semantic, relations: semantic.relations.filter(r => !removed.has(r.source) && !removed.has(r.target)), findings: semantic.findings.filter(f => !removed.has(f.node_id || "")) } } : {}) }); chooseNode(document.nodes[0]);
  };
  const connect = (parent: string) => {
    if (!document || !current) return;
    const edges = (document.edges ?? document.links ?? []).filter(e => childOf(e) !== current.id);
    if (parent) edges.push(module === "graph" ? { parent, child: current.id } : { from: parent, to: current.id, from_port: "artifact", to_port: "input" });
    setDocument({ ...document, ...(module === "graph" ? { edges } : { links: edges }) });
  };
  const layout = () => {
    if (!document) return;
    const placed = new Map(organizeNodes([...document.nodes, ...(document.semantic?.entities || [])], allEdges, layoutMode === "saved" ? "hierarchy" : layoutMode, colorMode, findings).map(n => [n.id, n]));
    setDocument({ ...document, nodes: document.nodes.map(n => ({ ...n, x: placed.get(n.id)!.x, y: placed.get(n.id)!.y })),
      ...(document.semantic ? { semantic: { ...document.semantic, entities: document.semantic.entities.map(n => ({ ...n, x: placed.get(n.id)!.x, y: placed.get(n.id)!.y })) } } : {}) });
    setLayoutMode("saved");
  };
  const review = async (action: string) => {
    let destination: string | undefined;
    if (!["save", "refresh", "expand", "analyze"].includes(action)) { const parent = await work.choose("authoring_parent"); if (!parent) return; destination = `${parent.replace(/[\\/]$/, "")}/${action === "create" ? filename : outputName}`; }
    await work.run("review_workspace_action", { action, ...(session?.workspace ? { workspace: session.workspace, expected_state_sha256: session.state_sha256 } : {}),
      ...(["create", "save"].includes(action) ? { document } : {}), ...(destination ? { destination } : {}), ...(["build", "plan_origin", "expand", "preview_bundle"].includes(action) && game ? { gta_path: game } : {}), ...(action === "expand" ? { node_id: selected } : {}) });
  };
  const title = module === "graph" ? "Package layout" : "Build flow";
  const rootId = document?.root_id || document?.source_id;
  const connected = (document?.edges ?? document?.links ?? []).find(e => childOf(e) === selected);
  return <section className={`offline-workbench graph-workbench ${canvasOnly ? "graph-canvas-only" : ""}`} aria-label={title}><div className="offline-toolbar"><div><h3>{title}</h3><p>{module === "graph" ? "Explore archive contents, source files, and their relationships." : "Connect typed build steps. Plan outputs separately before executing the reviewed flow."}</p></div>
    <div className="heading-actions"><button className="primary-button" disabled={work.locked || dirty} onClick={() => void open()}>Open {module}</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void create()}>New {module}</button>
      {module === "graph" && <><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void folder()}>Graph from folder</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importArchive()}>Import RPF graph</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importPackage(false)}>Import package ZIP</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importPackage(true)}>Import package folder</button></>}</div></div>
    {module === "program" && <label>Program template<select disabled={work.locked || dirty} value={template} onChange={e => setTemplate(e.target.value)}>{Object.entries(templates).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
    <div className="source-strip"><strong>{dirty ? "Unsaved node draft" : title}</strong><span className="source-path">{session?.workspace || "No saved document"}</span></div>
    <AuthoringFeedback work={work} />
    {(work.review?.value.build || work.lastResult?.provenance) ? <details><summary>RPF construction identity</summary>
      <p>Exact SDK/helper identity and sealed source/readback receipt. Package publication and in-game acceptance are separate.</p>
      <pre style={{maxHeight:280,overflow:"auto"}}>{JSON.stringify(work.review?.value.build || work.lastResult?.provenance,null,2)}</pre>
    </details> : null}
    {work.lastResult?.preview_summary !== undefined && <details open><summary>Preview bundle results — failures remain failures</summary><pre>{JSON.stringify(work.lastResult.preview_summary, null, 2)}</pre></details>}
    {session?.issues.length ? <div className="graph-issues" role="status"><strong>Readiness findings</strong><ul>{session.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></div> : null}
    <div className="graph-organization">
      <label>View layout<select value={layoutMode} onChange={e => setLayoutMode(e.target.value as LayoutMode)}><option value="hierarchy">Hierarchy · grouped branches</option><option value="groups">Color groups · compact grid</option><option value="saved">Saved / manual positions</option></select></label>
      <label>Color nodes by<select value={colorMode} onChange={e => { setColorMode(e.target.value as ColorMode); setColorFilter("all"); }}><option value="kind">Asset type</option><option value="findings">Reported findings</option><option value="none">No colors</option></select></label>
      <label>Sort node list<select value={sortMode} onChange={e => setSortMode(e.target.value as SortMode)}><option value="color">Color / category</option><option value="name">Name · A–Z</option><option value="source">Source path</option></select></label>
      {document?.semantic && <label className="graph-check"><input type="checkbox" checked={showRelations} onChange={e => setShowRelations(e.target.checked)} />Show vehicle relationships</label>}
      <button className="quiet-button" aria-pressed={canvasOnly} onClick={() => setCanvasOnly(!canvasOnly)}>{canvasOnly ? "Show panels" : "Expand canvas"}</button>
    </div>
    <div className="graph-legend" aria-label="Node color filters">
      <button className="quiet-button" aria-pressed={colorFilter === "all"} onClick={() => setColorFilter("all")}>All categories · {allNodes.length}</button>
      {colors.map(color => <button className="quiet-button graph-color-chip" style={{ "--node-color": PALETTE[color].color } as CSSProperties} aria-pressed={colorFilter === color}
        key={color} onClick={() => setColorFilter(colorFilter === color ? "all" : color)}><span aria-hidden="true" />{PALETTE[color].label} · {allNodes.filter(n => nodeColor(n, colorMode, findings) === color).length}</button>)}
      {colorMode === "findings" && <small>Reported findings only; a neutral node is not proof of validation.</small>}
    </div>
    <div className="graph-search-bar">
      <label>Find node<input value={query} placeholder="Name, type, or source path…" disabled={work.locked || formDirty} onChange={e => { setQuery(e.target.value); setColorFilter("all"); }}
        onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); nextMatch(e.shiftKey ? -1 : 1); } if (e.key === "Escape") setQuery(""); }} /></label>
      <output aria-live="polite">{query.trim() ? `${matches.length} match${matches.length === 1 ? "" : "es"}` : `${listedNodes.length} listed nodes`}</output>
      <button className="quiet-button" disabled={!query.trim() || !matches.length || work.locked || formDirty} onClick={() => nextMatch(-1)}>Previous match</button>
      <button className="quiet-button" disabled={!query.trim() || !matches.length || work.locked || formDirty} onClick={() => nextMatch(1)}>Next match</button>
      <button className="quiet-button" disabled={!query} onClick={() => setQuery("")}>Clear search</button>
    </div>
    <div className="offline-panes graph-panes"><section className="graph-structure-pane"><header><span className="pane-kicker">Structure</span><h4>Nodes {document ? `· ${allNodes.length}` : ""}</h4></header><div className="offline-pane-body">
      <div className="graph-node-list">{listedNodes.map(node => { const color = nodeColor(node, colorMode, findings); return <button className="quiet-button" data-color={color} style={{ "--node-color": PALETTE[color].color } as CSSProperties} key={node.id} disabled={work.locked || formDirty} aria-pressed={node.id === selected} onClick={() => focusNode(node.id)} title={node.source || node.id}><span>{node.name || node.id}</span><small>{PALETTE[color].label} · {node.type.replaceAll("_", " ")}</small></button>; })}
        {!listedNodes.length && <p>No matching nodes. Try a different name or clear the filter.</p>}</div>
      <fieldset disabled={!document || work.locked || formDirty}><h5>Add node</h5>{module === "graph" ? <><button className="quiet-button" onClick={() => add("directory")}>Add directory</button><button className="quiet-button" onClick={() => add("archive")}>Add nested archive</button><button className="quiet-button" disabled={!session?.workspace} onClick={() => void addFile()}>Add source file</button>{!session?.workspace && <p>Save the graph before binding individual files, or start from a folder.</p>}</>
        : Object.entries(session?.node_specs ?? {}).filter(([type]) => type !== "package_source").map(([type, spec]) => <button className="quiet-button" key={type} onClick={() => add(type)}>Add {spec.title}</button>)}</fieldset>
    </div></section><section className="graph-canvas-pane"><header><span className="pane-kicker">{module === "graph" ? "Containment" : "Execution"}</span><h4>Node canvas</h4></header><div className="offline-pane-body">
      {!document ? <p>Open a document or start a new {module}.</p> : <><NodeCanvas key={session?.workspace || module} nodes={allNodes} edges={allEdges} containment={document.edges || []} findings={findings} selected={selected} select={select}
        layout={layoutMode} setLayout={setLayoutMode} colorMode={colorMode} colorFilter={colorFilter} focusRequest={focusRequest} matches={matchIds} locked={work.locked || formDirty} move={(id, x, y) => modify(id, { x, y })} />
        <details className="graph-document-actions" open={dirty || undefined}><summary>Save layout / authoring</summary><p>Navigation, search and color choices are view-only. Auto layout copies the current organization into the draft; saving still requires review.</p>
        <button className="quiet-button" disabled={work.locked || formDirty} onClick={layout}>Auto layout nodes</button>
        <label>Document filename<input value={filename} disabled={work.locked || !!session?.workspace} onChange={e => setFilename(e.target.value)} maxLength={100} /></label>
        <div className="heading-actions"><button className="primary-button" disabled={work.locked || formDirty || !dirty} onClick={() => void review(session?.workspace ? "save" : "create")}>Review {module} save</button>
          <button className="quiet-button" disabled={work.locked || !dirty} onClick={() => { if (session?.workspace) { setDocument(session.document); chooseNode(session.document.nodes[0]); } else { setDocument(null); setSession(null); chooseNode(); } }}>Discard node draft</button></div></details></>}
    </div></section><section className="graph-inspector-pane"><header><span className="pane-kicker">Inspector</span><h4>{current?.name || current?.id || "Selected node"}</h4></header><div className="offline-pane-body">
      {current && <><p>{current.type.replaceAll("_", " ")} · {current.id}</p><fieldset disabled={work.locked || !!semanticEntity}>
        {module === "graph" ? <label>Node name<input value={name} onChange={e => setName(e.target.value)} maxLength={160} /></label> : [...(session?.node_specs?.[current.type]?.required_config ?? []), ...(session?.node_specs?.[current.type]?.optional_config ?? [])].map(key => <label key={key}>{key === "gta_path" ? "Decoder game path" : key === "output" ? "Output path" : key === "report" ? "Report path" : "Artifact label"}<input value={config[key] || ""} onChange={e => setConfig({ ...config, [key]: e.target.value })} /></label>)}
        {formDirty && <><button className="primary-button" onClick={() => modify(selected, module === "graph" ? { name } : { config })}>Apply node to draft</button><button className="quiet-button" onClick={() => chooseNode(current)}>Revert node fields</button></>}
        <label>{module === "graph" ? "Container parent" : "Input connection"}<select value={connected ? parentOf(connected) : ""} disabled={selected === rootId || formDirty} onChange={e => connect(e.target.value)}><option value="">Disconnected</option>{document?.nodes.filter(n => n.id !== selected && (module === "program" || ["archive", "directory"].includes(n.type))).map(n => <option key={n.id} value={n.id}>{n.name || n.id}</option>)}</select></label>
        {current.source && <><p className="source-path">{current.source}</p><p>{current.size?.toLocaleString()} bytes</p><p className="hash-value">{current.sha256}</p></>}
        <button className="quiet-button" disabled={selected === rootId || formDirty} onClick={remove}>Remove node{module === "graph" ? " and descendants" : ""}</button></fieldset></>}
      {semanticEntity && <><h5>Resolved vehicle relationships</h5><pre>{JSON.stringify(semanticEntity.metadata, null, 2)}</pre>
        <button className="quiet-button" disabled={dirty || work.locked || !onOpenVehicle} onClick={() => onOpenVehicle?.(semanticEntity.source_root, semanticEntity.name!)}>Open graph vehicle</button></>}
      {current?.type === "file" && /\.(yft|ydr|ydd|ytd)$/i.test(current.source || "") && <button className="quiet-button" disabled={dirty || work.locked || !onOpenAsset} onClick={() => onOpenAsset?.(current.source!)}>Open graph asset</button>}
      {document?.semantic && <details><summary>Relationship findings ({document.semantic.findings.length})</summary><ul>{document.semantic.findings.map((finding, i) => <li key={i}>{finding.severity}: {finding.message}</li>)}</ul></details>}
      <fieldset disabled={!session?.workspace || work.locked || dirty}><h4>Output</h4><label>Output / report name<input value={outputName} onChange={e => setOutputName(e.target.value)} maxLength={100} /></label>
        {module === "graph" ? <><button className="quiet-button" onClick={() => void review("refresh")}>Review refreshed sources</button><button className="primary-button" onClick={() => void review("materialize")}>Review materialize tree</button>
          <button className="quiet-button" onClick={async () => { const chosen = await work.choose("gta_folder"); if (chosen) setGame(chosen); }}>Decoder game folder</button><p className="source-path">{game || "No decoder context"}</p>
          <button className="quiet-button" disabled={!game} onClick={() => void review("build")}>Review RPF build</button><button className="quiet-button" disabled={!game || !document?.origin} onClick={() => void review("plan_origin")}>Review origin plan</button>
          <button className="quiet-button" disabled={!game || current?.type !== "sealed_archive"} onClick={() => void review("expand")}>Review sealed archive expansion</button>
          <button className="quiet-button" onClick={() => void review("preview_bundle")}>Review preview bundle</button><button className="quiet-button" disabled={(document?.origin as { type?: string })?.type !== "mod_package_import"} onClick={() => void review("analyze")}>Review package relationships</button></>
          : <><button className="quiet-button" onClick={() => void review("plan")}>Review flow plan</button><button className="primary-button" onClick={() => void review("run")}>Review flow execution</button></>}
        <p>All outputs must be new and outside GTA V. Origin plans are inert; execution against an archive is a separate reviewed workflow.</p></fieldset>
    </div></section></div>
  </section>;
}
