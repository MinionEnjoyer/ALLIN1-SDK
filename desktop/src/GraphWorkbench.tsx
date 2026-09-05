import { useEffect, useRef, useState } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";
import "./GraphWorkbench.css";
import SliderField from "./SliderField";

interface Node { id: string; type: string; name?: string; x: number; y: number; source?: string; size?: number; sha256?: string; config?: Record<string, string> }
interface Edge { parent?: string; child?: string; from?: string; to?: string; from_port?: string; to_port?: string }
interface Semantic { entities: (Node & { source_root: string; edition: string; metadata: Record<string, unknown> })[]; relations: { source: string; target: string; role: string }[]; findings: { node_id?: string; message: string; severity: string }[]; summary: Record<string, unknown> }
interface Document { semantic?: Semantic; schema_version: number; operation: string; nodes: Node[]; edges?: Edge[]; links?: Edge[]; root_id?: string; source_id?: string; package_graph?: string; template?: string; [key: string]: unknown }
interface Spec { title: string; input_types: string[]; output_type: string | null; required_config: string[]; optional_config: string[] }
interface Session extends WorkspaceResult { workspace: string | null; document: Document; issues: string[]; node_specs?: Record<string, Spec>; source_node?: Omit<Node, "id" | "x" | "y"> }
const serial = (value: unknown) => JSON.stringify(value);
const parentOf = (e: Edge) => e.parent ?? e.from!;
const childOf = (e: Edge) => e.child ?? e.to!;
const unique = (nodes: Node[]) => { let id = 1; while (nodes.some(node => node.id === `node_${id}`)) id++; return `node_${id}`; };
const blankGraph = (): Document => ({ schema_version: 1, operation: "rpf_package_graph", root_id: "root", nodes: [{ id: "root", type: "archive", name: "dlc.rpf", x: 40, y: 40 }], edges: [] });
const templates = { validate: "Validate only", "loose-export": "Loose authoring tree", "verified-build": "Verified RPF build", "compact-release": "Compact verified release", "origin-change-plan": "Imported-origin plan" };

function NodeCanvas({ document, selected, select, move, locked }: { document: Document; selected: string; select: (id: string) => void; move: (id: string, x: number, y: number) => void; locked: boolean }) {
  const [zoom, setZoom] = useState(1), [drag, setDrag] = useState<{ id: string; x: number; y: number; startX: number; startY: number } | null>(null);
  const scroll = useRef<HTMLDivElement>(null), pan = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set()), [showRelations, setShowRelations] = useState(true);
  const hidden = new Set<string>();
  for (let i = 0; i < document.nodes.length; i++) for (const edge of document.edges ?? []) if (collapsed.has(parentOf(edge)) || hidden.has(parentOf(edge))) hidden.add(childOf(edge));
  const positions = [...document.nodes.filter(node => !hidden.has(node.id)), ...(showRelations ? document.semantic?.entities ?? [] : [])].map(node => drag?.id === node.id ? { ...node, x: drag.x, y: drag.y } : node);
  const edges = [...(document.edges ?? document.links ?? []), ...(showRelations ? document.semantic?.relations.map(r => ({ parent: r.source, child: r.target })) ?? [] : [])];
  const width = Math.max(850, ...positions.map(n => n.x + 280)), height = Math.max(470, ...positions.map(n => n.y + 140));
  return <><div className="graph-view-controls"><button className="quiet-button" onClick={() => setZoom(z => Math.max(.4, z - .1))} aria-label="Zoom out graph">−</button>
    <span>{Math.round(zoom * 100)}%</span><button className="quiet-button" onClick={() => setZoom(z => Math.min(1.8, z + .1))} aria-label="Zoom in graph">+</button>
    <button className="quiet-button" onClick={() => { setZoom(1); scroll.current?.scrollTo?.({ left: 0, top: 0 }); }}>Reset view</button>
    <button className="quiet-button" onClick={() => setZoom(Math.max(.1, Math.min(1.8, (scroll.current?.clientWidth || 850) / width, (scroll.current?.clientHeight || 470) / height)))}>Fit graph</button>
    {document.edges && <><button className="quiet-button" disabled={!document.edges.some(edge => parentOf(edge) === selected)} onClick={() => setCollapsed(old => { const next = new Set(old); if (next.has(selected)) next.delete(selected); else next.add(selected); return next; })}>{collapsed.has(selected) ? "Expand selected branch" : "Collapse selected branch"}</button>
      <button className="quiet-button" disabled={!collapsed.size} onClick={() => setCollapsed(new Set())}>Expand all branches</button></>}
    {document.semantic && <label><input type="checkbox" checked={showRelations} onChange={e => setShowRelations(e.target.checked)} />Show vehicle relationships</label>}</div>
    <details className="viewport-slider-settings"><summary>Graph zoom</summary><SliderField numeric commitValidOnly label="Graph zoom" unit="%" min={10} max={180} hardMin={10} hardMax={180} step={5} value={zoom * 100} resetValue={100}
      onChange={value => { if (Number.isFinite(value) && value >= 10 && value <= 180) setZoom(value / 100); }} /></details>
    <div className="graph-canvas-scroll" ref={scroll} onPointerDown={e => {
      if ((e.target as Element).closest("[data-node]")) return;
      pan.current = { x: e.clientX, y: e.clientY, left: e.currentTarget.scrollLeft, top: e.currentTarget.scrollTop };
      e.currentTarget.setPointerCapture?.(e.pointerId);
    }} onPointerMove={e => { if (pan.current) { e.currentTarget.scrollLeft = pan.current.left - e.clientX + pan.current.x; e.currentTarget.scrollTop = pan.current.top - e.clientY + pan.current.y; } }} onPointerUp={() => { pan.current = null; }}>
      <svg width={width * zoom} height={height * zoom} viewBox={`0 0 ${width} ${height}`} aria-label="Package node canvas">
        <defs><marker id={`arrow-${document.operation}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" /></marker></defs>
        {edges.map((edge, i) => { const parent = positions.find(n => n.id === parentOf(edge)), child = positions.find(n => n.id === childOf(edge));
          return parent && child && <path key={i} className="graph-edge" d={`M ${parent.x + 220} ${parent.y + 35} C ${parent.x + 260} ${parent.y + 35}, ${child.x - 40} ${child.y + 35}, ${child.x} ${child.y + 35}`} markerEnd={`url(#arrow-${document.operation})`} />; })}
        {positions.map(node => <g data-node={node.id} key={node.id} role="button" tabIndex={0} aria-label={`Select node ${node.name || node.id}`} aria-pressed={selected === node.id}
          className={`graph-node ${selected === node.id ? "selected" : ""}`} transform={`translate(${node.x}, ${node.y})`}
          onClick={() => select(node.id)} onKeyDown={e => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(node.id); }
            if (!locked && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) {
              e.preventDefault(); move(node.id, Math.max(0, node.x + (e.key === "ArrowLeft" ? -10 : e.key === "ArrowRight" ? 10 : 0)), Math.max(0, node.y + (e.key === "ArrowUp" ? -10 : e.key === "ArrowDown" ? 10 : 0)));
            }
          }} onPointerDown={e => { if (locked) return; e.stopPropagation(); e.currentTarget.setPointerCapture?.(e.pointerId); select(node.id); setDrag({ id: node.id, x: node.x, y: node.y, startX: e.clientX - node.x * zoom, startY: e.clientY - node.y * zoom }); }}
          onPointerMove={e => { if (drag?.id === node.id) setDrag({ ...drag, x: Math.max(0, (e.clientX - drag.startX) / zoom), y: Math.max(0, (e.clientY - drag.startY) / zoom) }); }}
          onPointerUp={() => { if (drag?.id === node.id) { move(node.id, Math.round(drag.x), Math.round(drag.y)); setDrag(null); } }} onPointerCancel={() => setDrag(null)}>
          <rect width="220" height="70" rx="4" /><text x="14" y="25">{(node.name || node.id).slice(0, 26)}</text><text className="graph-node-kind" x="14" y="49">{node.type.replaceAll("_", " ")}</text>
        </g>)}
      </svg>
    </div><p className="field-hint">Drag nodes to arrange. Drag the background to pan. Focus a node and use arrow keys for precise placement.</p></>;
}

export default function GraphWorkbench({ client, module, onGuardChange, onOpenAsset, onOpenVehicle, initialSource = "" }: { client: DesktopClient; module: "graph" | "program"; onGuardChange: (guarded: boolean) => void; onOpenAsset?: (source: string) => void; onOpenVehicle?: (source: string, model: string) => void; initialSource?: string }) {
  const [session, setSession] = useState<Session | null>(null), [document, setDocument] = useState<Document | null>(null), [selected, setSelected] = useState("");
  const [filename, setFilename] = useState(module === "graph" ? "rpf-graph.json" : "rpf-program.json"), [outputName, setOutputName] = useState(module === "graph" ? "materialized-tree" : "program-report.json");
  const [template, setTemplate] = useState("loose-export"), [game, setGame] = useState(""), [query, setQuery] = useState("");
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
    setSession(s); setDocument(s.document); chooseNode(s.document.nodes[0]);
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
    const edges = document.edges ?? document.links ?? [], depths = new Map<string, number>([[document.root_id || document.source_id!, 0]]);
    for (let i = 0; i < document.nodes.length; i++) for (const e of edges) if (depths.has(parentOf(e)) && !depths.has(childOf(e))) depths.set(childOf(e), depths.get(parentOf(e))! + 1);
    const rows = new Map<number, number>();
    setDocument({ ...document, nodes: document.nodes.map(n => { const depth = depths.get(n.id) ?? 0, row = rows.get(depth) ?? 0; rows.set(depth, row + 1); return { ...n, x: 40 + depth * 280, y: 40 + row * 105 }; }) });
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
  return <section className="offline-workbench" aria-label={title}><div className="offline-toolbar"><div><h3>{title}</h3><p>{module === "graph" ? "Arrange archive contents and bind each file to its source hash." : "Connect typed build steps. Plan outputs separately before executing the reviewed flow."}</p></div>
    <div className="heading-actions"><button className="primary-button" disabled={work.locked || dirty} onClick={() => void open()}>Open {module}</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void create()}>New {module}</button>
      {module === "graph" && <><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void folder()}>Graph from folder</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importArchive()}>Import RPF graph</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importPackage(false)}>Import package ZIP</button><button className="quiet-button" disabled={work.locked || dirty} onClick={() => void importPackage(true)}>Import package folder</button></>}</div></div>
    {module === "program" && <label>Program template<select disabled={work.locked || dirty} value={template} onChange={e => setTemplate(e.target.value)}>{Object.entries(templates).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
    <div className="source-strip"><strong>{dirty ? "Unsaved node draft" : title}</strong><span className="source-path">{session?.workspace || "No saved document"}</span></div>
    <AuthoringFeedback work={work} />
    {work.lastResult?.preview_summary !== undefined && <details open><summary>Preview bundle results — failures remain failures</summary><pre>{JSON.stringify(work.lastResult.preview_summary, null, 2)}</pre></details>}
    {session?.issues.length ? <div className="graph-issues" role="status"><strong>Readiness findings</strong><ul>{session.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul></div> : null}
    <div className="offline-panes graph-panes"><section><header><span className="pane-kicker">Structure</span><h4>Nodes {document ? `· ${document.nodes.length}` : ""}</h4></header><div className="offline-pane-body">
      <label>Find node<input value={query} onChange={e => setQuery(e.target.value)} /></label><div className="graph-node-list">{[...(document?.nodes ?? []), ...(document?.semantic?.entities ?? [])].filter(n => `${n.id} ${n.name || ""} ${n.type}`.toLowerCase().includes(query.toLowerCase())).map(node => <button className="quiet-button" key={node.id} disabled={work.locked || formDirty} aria-pressed={node.id === selected} onClick={() => select(node.id)}>{node.name || node.id}<small>{node.type.replaceAll("_", " ")}</small></button>)}</div>
      <fieldset disabled={!document || work.locked || formDirty}><h5>Add node</h5>{module === "graph" ? <><button className="quiet-button" onClick={() => add("directory")}>Add directory</button><button className="quiet-button" onClick={() => add("archive")}>Add nested archive</button><button className="quiet-button" disabled={!session?.workspace} onClick={() => void addFile()}>Add source file</button>{!session?.workspace && <p>Save the graph before binding individual files, or start from a folder.</p>}</>
        : Object.entries(session?.node_specs ?? {}).filter(([type]) => type !== "package_source").map(([type, spec]) => <button className="quiet-button" key={type} onClick={() => add(type)}>Add {spec.title}</button>)}</fieldset>
    </div></section><section><header><span className="pane-kicker">{module === "graph" ? "Containment" : "Execution"}</span><h4>Node canvas</h4></header><div className="offline-pane-body">
      {!document ? <p>Open a document or start a new {module}.</p> : <><NodeCanvas document={document} selected={selected} select={select} locked={work.locked || formDirty} move={(id, x, y) => modify(id, { x, y })} />
        <button className="quiet-button" disabled={work.locked || formDirty} onClick={layout}>Auto layout nodes</button>
        <label>Document filename<input value={filename} disabled={work.locked || !!session?.workspace} onChange={e => setFilename(e.target.value)} maxLength={100} /></label>
        <div className="heading-actions"><button className="primary-button" disabled={work.locked || formDirty || !dirty} onClick={() => void review(session?.workspace ? "save" : "create")}>Review {module} save</button>
          <button className="quiet-button" disabled={work.locked || !dirty} onClick={() => { if (session?.workspace) { setDocument(session.document); chooseNode(session.document.nodes[0]); } else { setDocument(null); setSession(null); chooseNode(); } }}>Discard node draft</button></div></>}
    </div></section><section><header><span className="pane-kicker">Inspector</span><h4>{current?.name || current?.id || "Selected node"}</h4></header><div className="offline-pane-body">
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
