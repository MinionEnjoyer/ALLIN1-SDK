import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import SliderField from "./SliderField";
import { ancestors, childOf, fitCamera, hiddenDescendants, NODE_HEIGHT, NODE_WIDTH, nodeColor, organizeNodes, PALETTE, parentOf, wrapNodeName, zoomAt,
  type Camera, type ColorKey, type ColorMode, type GraphEdge, type GraphFinding, type GraphNode, type LayoutMode } from "./graphView";

interface Props {
  nodes: GraphNode[]; edges: GraphEdge[]; containment: GraphEdge[]; findings: GraphFinding[];
  selected: string; select: (id: string) => void; move: (id: string, x: number, y: number) => void; locked: boolean;
  layout: LayoutMode; setLayout: (layout: LayoutMode) => void; colorMode: ColorMode; colorFilter: ColorKey | "all";
  focusRequest: { id: string; serial: number } | null; matches: Set<string> | null;
}
type Gesture = { pointer: number; kind: "pan"; clientX: number; clientY: number; camera: Camera }
  | { pointer: number; kind: "node"; id: string; clientX: number; clientY: number; x: number; y: number; zoom: number; moved: boolean };

export default function NodeCanvas(props: Props) {
  const { nodes, edges, containment, findings, selected, select, move, locked, layout, setLayout, colorMode, colorFilter, focusRequest, matches } = props;
  const viewport = useRef<HTMLDivElement>(null), gesture = useRef<Gesture | null>(null), space = useRef(false);
  const viewportSize = useRef({ width: 900, height: 600 });
  const [camera, setCamera] = useState<Camera>({ x: 32, y: 32, zoom: 1 });
  const cameraRef = useRef(camera); cameraRef.current = camera;
  const [drag, setDrag] = useState<{ id: string; x: number; y: number } | null>(null), [panning, setPanning] = useState(false);
  const [arrange, setArrange] = useState(false), [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [edgeMode, setEdgeMode] = useState("selected");
  const marker = useId().replace(/:/g, "");
  const hidden = useMemo(() => hiddenDescendants(containment, collapsed), [containment, collapsed]);
  const visible = useMemo(() => nodes.filter(node => !hidden.has(node.id) && (colorFilter === "all" || nodeColor(node, colorMode, findings) === colorFilter)), [nodes, hidden, colorFilter, colorMode, findings]);
  const organized = useMemo(() => organizeNodes(visible, edges, layout, colorMode, findings), [visible, edges, layout, colorMode, findings]);
  const positions = useMemo(() => organized.map(node => node.id === drag?.id ? { ...node, x: drag.x, y: drag.y } : node), [organized, drag]);
  const byId = useMemo(() => new Map(positions.map(node => [node.id, node])), [positions]);
  const colorGroups = useMemo(() => {
    if (layout !== "groups") return [];
    return [...new Set(positions.map(n => nodeColor(n, colorMode, findings)))].map(color => {
      const group = positions.filter(n => nodeColor(n, colorMode, findings) === color);
      return { color, x: Math.min(...group.map(n => n.x)), y: Math.min(...group.map(n => n.y)) - 18, count: group.length };
    });
  }, [positions, layout, colorMode, findings]);
  const related = useMemo(() => new Set([selected, ...edges.filter(e => parentOf(e) === selected || childOf(e) === selected).flatMap(e => [parentOf(e), childOf(e)])]), [selected, edges]);
  const dimensions = () => {
    const size = { width: viewport.current?.clientWidth || 900, height: viewport.current?.clientHeight || 600 };
    // Fit/focus can run before ResizeObserver delivers a pending resize. Mark
    // that size as consumed so its callback does not shift the camera twice.
    viewportSize.current = size;
    return size;
  };
  const center = (node: GraphNode) => {
    const { width, height } = dimensions(), zoom = Math.max(.85, Math.min(1.2, cameraRef.current.zoom));
    setCamera({ zoom, x: width / 2 - (node.x + NODE_WIDTH / 2) * zoom, y: height / 2 - (node.y + NODE_HEIGHT / 2) * zoom });
  };
  const fit = (all = true) => {
    const { width, height } = dimensions();
    const group = all ? organized : organized.filter(n => related.has(n.id));
    setCamera(fitCamera(group.length ? group : organized, width, height));
  };
  const focusSelected = () => { setCollapsed(old => new Set([...old].filter(id => !ancestors(selected, containment).has(id)))); if (byId.has(selected)) center(byId.get(selected)!); };
  const previousLayout = useRef("");
  useEffect(() => {
    if (previousLayout.current === layout || !organized.length) return;
    previousLayout.current = layout;
    const { width, height } = dimensions();
    const node = organized.find(n => n.id === selected) || organized[0];
    const neighbors = new Set([node.id, ...edges.filter(e => parentOf(e) === node.id).map(childOf)]);
    setCamera(fitCamera(organized.filter(n => neighbors.has(n.id)), width, height, .65));
  }, [layout, organized, selected, edges]);
  const previousFilter = useRef(`${colorMode}:${colorFilter}`);
  useEffect(() => {
    const next = `${colorMode}:${colorFilter}`;
    if (previousFilter.current === next) return;
    previousFilter.current = next;
    if (colorFilter !== "all") { const { width, height } = dimensions(); setCamera(fitCamera(organized, width, height)); }
  }, [colorMode, colorFilter, organized]);
  useEffect(() => {
    if (!focusRequest) return;
    const parents = ancestors(focusRequest.id, containment);
    setCollapsed(old => new Set([...old].filter(id => !parents.has(id))));
  }, [focusRequest, containment]);
  const focusedSerial = useRef(-1);
  useEffect(() => {
    if (!focusRequest || focusedSerial.current === focusRequest.serial || !byId.has(focusRequest.id)) return;
    focusedSerial.current = focusRequest.serial; center(byId.get(focusRequest.id)!);
  }, [focusRequest, byId]);
  useEffect(() => {
    const element = viewport.current; if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      if (event.shiftKey && !event.ctrlKey) setCamera(c => ({ ...c, x: c.x - event.deltaY - event.deltaX }));
      else setCamera(c => zoomAt(c, c.zoom * Math.exp(-Math.max(-500, Math.min(500, event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1))) * .0015), event.clientX - rect.left, event.clientY - rect.top));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      const nextWidth = element.clientWidth, nextHeight = element.clientHeight;
      if (!nextWidth || !nextHeight) return;
      const { width, height } = viewportSize.current;
      viewportSize.current = { width: nextWidth, height: nextHeight };
      if (nextWidth !== width || nextHeight !== height) setCamera(c => ({ ...c, x: c.x + (nextWidth - width) / 2, y: c.y + (nextHeight - height) / 2 }));
    });
    resize?.observe(element);
    const release = () => { gesture.current = null; space.current = false; setPanning(false); setDrag(null); };
    window.addEventListener("blur", release);
    return () => { element.removeEventListener("wheel", onWheel); resize?.disconnect(); window.removeEventListener("blur", release); };
  }, []);
  useEffect(() => { if (locked) { gesture.current = null; setDrag(null); setPanning(false); } }, [locked]);
  const zoom = (value: number) => { const { width, height } = dimensions(); setCamera(c => zoomAt(c, value, width / 2, height / 2)); };
  const cancel = () => { gesture.current = null; setDrag(null); setPanning(false); };
  const pointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (gesture.current || ![0, 1].includes(event.button)) return;
    const nodeId = (event.target as Element).closest("[data-node]")?.getAttribute("data-node");
    if (event.button === 1 || (event.button === 0 && (!nodeId || space.current))) {
      event.preventDefault(); viewport.current?.focus({ preventScroll: true });
      gesture.current = { kind: "pan", pointer: event.pointerId, clientX: event.clientX, clientY: event.clientY, camera: cameraRef.current }; setPanning(true);
    } else if (nodeId && !locked) {
      select(nodeId);
      if (!arrange || layout !== "saved") return;
      const node = byId.get(nodeId)!;
      event.preventDefault(); gesture.current = { kind: "node", pointer: event.pointerId, id: node.id, clientX: event.clientX, clientY: event.clientY, x: node.x, y: node.y, zoom: cameraRef.current.zoom, moved: false };
    } else return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = gesture.current; if (!active || event.pointerId !== active.pointer) return;
    const dx = event.clientX - active.clientX, dy = event.clientY - active.clientY;
    if (active.kind === "pan") setCamera({ ...active.camera, x: active.camera.x + dx, y: active.camera.y + dy });
    else { active.moved ||= Math.abs(dx) + Math.abs(dy) > 3; setDrag({ id: active.id, x: Math.max(0, active.x + dx / active.zoom), y: Math.max(0, active.y + dy / active.zoom) }); }
  };
  const pointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const active = gesture.current; if (!active || active.pointer !== event.pointerId) return;
    if (active.kind === "node" && active.moved && !locked) move(active.id, Math.max(0, Math.round(active.x + (event.clientX - active.clientX) / active.zoom)), Math.max(0, Math.round(active.y + (event.clientY - active.clientY) / active.zoom)));
    cancel(); if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  return <>
    <div className="graph-view-controls" role="toolbar" aria-label="Graph navigation">
      <button className="quiet-button" aria-label="Zoom out graph" disabled={camera.zoom <= .1} onClick={() => zoom(camera.zoom / 1.2)}>−</button>
      <output aria-label="Graph zoom level">{Math.round(camera.zoom * 100)}%</output>
      <button className="quiet-button" aria-label="Zoom in graph" disabled={camera.zoom >= 2} onClick={() => zoom(camera.zoom * 1.2)}>+</button>
      <button className="quiet-button" onClick={() => zoom(1)}>100%</button>
      <button className="quiet-button" onClick={() => fit()}>Fit graph</button>
      <button className="quiet-button" disabled={!byId.has(selected)} onClick={focusSelected}>Focus selected</button>
      <button className="quiet-button" onClick={() => fit(false)}>Reset view</button>
      <label className="graph-check"><input type="checkbox" checked={arrange && layout === "saved"} disabled={locked} onChange={e => { setArrange(e.target.checked); if (e.target.checked) setLayout("saved"); }} />Arrange nodes</label>
    </div>
    <div className="graph-secondary-controls">
      <label>Connections<select value={edgeMode} onChange={e => setEdgeMode(e.target.value)}><option value="selected">Emphasize selected</option><option value="all">Show all</option><option value="none">Hide connections</option></select></label>
      {!!containment.length && <><button className="quiet-button" disabled={!containment.some(e => parentOf(e) === selected)} onClick={() => setCollapsed(old => { const next = new Set(old); if (next.has(selected)) next.delete(selected); else next.add(selected); return next; })}>{collapsed.has(selected) ? "Expand selected branch" : "Collapse selected branch"}</button>
        <button className="quiet-button" disabled={!collapsed.size} onClick={() => setCollapsed(new Set())}>Expand all branches</button></>}
      <details className="graph-zoom-details"><summary>Exact zoom</summary><SliderField numeric commitValidOnly label="Graph zoom" unit="%" min={10} max={200} hardMin={10} hardMax={200} step={5} value={Math.round(camera.zoom * 100)} resetValue={100} onChange={value => zoom(value / 100)} /></details>
    </div>
    <div className={`graph-canvas-scroll ${panning ? "is-panning" : ""} ${arrange ? "is-arranging" : ""}`} ref={viewport} tabIndex={0} role="region" aria-label="Graph viewport"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={cancel} onLostPointerCapture={cancel}
      onMouseDown={event => { if (event.button === 1) event.preventDefault(); }} onAuxClick={event => { if (event.button === 1) event.preventDefault(); }}
      onKeyDown={event => {
        if (event.key === " ") { event.preventDefault(); space.current = true; }
        if (event.key === "Escape") { cancel(); space.current = false; }
        if (event.key.toLowerCase() === "f") { event.preventDefault(); focusSelected(); }
        if (event.key === "Home") { event.preventDefault(); fit(); }
        if (["+", "=", "-"].includes(event.key)) { event.preventDefault(); zoom(camera.zoom * (event.key === "-" ? 1 / 1.2 : 1.2)); }
        if (event.target !== event.currentTarget) return;
        const delta: Record<string, [number, number]> = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] };
        if (delta[event.key]) { event.preventDefault(); const [x, y] = delta[event.key]; setCamera(c => ({ ...c, x: c.x + x, y: c.y + y })); }
      }} onKeyUp={event => { if (event.key === " ") space.current = false; }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) space.current = false; }}>
      <svg width="100%" height="100%" aria-label="Package node canvas">
        <defs><marker id={`arrow-${marker}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" /></marker></defs>
        <g data-testid="graph-camera" transform={`translate(${camera.x}, ${camera.y}) scale(${camera.zoom})`}>
          {colorGroups.map(group => <text className="graph-group-heading" key={group.color} x={group.x} y={group.y} fill={PALETTE[group.color].color}>{PALETTE[group.color].label} · {group.count}</text>)}
          {edgeMode !== "none" && edges.map((edge, index) => {
            const parent = byId.get(parentOf(edge)), child = byId.get(childOf(edge)); if (!parent || !child) return null;
            const emphasis = parent.id === selected || child.id === selected, dx = Math.max(48, Math.abs(child.x - parent.x - NODE_WIDTH) / 2);
            return <path key={index} className={`graph-edge ${emphasis ? "is-related" : edgeMode === "selected" ? "is-muted" : ""}`}
              d={`M ${parent.x + NODE_WIDTH} ${parent.y + NODE_HEIGHT / 2} C ${parent.x + NODE_WIDTH + dx} ${parent.y + NODE_HEIGHT / 2}, ${child.x - dx} ${child.y + NODE_HEIGHT / 2}, ${child.x} ${child.y + NODE_HEIGHT / 2}`} markerEnd={`url(#arrow-${marker})`} />;
          })}
          {positions.map(node => {
            const color = nodeColor(node, colorMode, findings), lines = wrapNodeName(node.name || node.id), match = matches?.has(node.id);
            return <g data-node={node.id} data-color={color} key={node.id} role="button" tabIndex={0} aria-label={`Select node ${node.name || node.id}`} aria-pressed={selected === node.id}
              style={{ "--node-color": PALETTE[color].color } as CSSProperties} className={`graph-node ${selected === node.id ? "selected" : ""} ${match ? "is-match" : matches?.size ? "is-search-muted" : ""}`}
              transform={`translate(${node.x}, ${node.y})`} onClick={event => { if (event.button === 0 && !space.current && !locked) select(node.id); }}
              onDoubleClick={() => center(node)} onKeyDown={event => {
                if (event.key === "Enter") { event.preventDefault(); if (!locked) { select(node.id); center(node); } }
                if (!locked && arrange && layout === "saved" && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                  event.preventDefault(); event.stopPropagation(); move(node.id, Math.max(0, node.x + (event.key === "ArrowLeft" ? -10 : event.key === "ArrowRight" ? 10 : 0)), Math.max(0, node.y + (event.key === "ArrowUp" ? -10 : event.key === "ArrowDown" ? 10 : 0)));
                }
              }}>
              <title>{node.name || node.id}{"\n"}{node.type.replaceAll("_", " ")}{node.source ? `\n${node.source}` : ""}</title>
              <rect className="graph-node-card" width={NODE_WIDTH} height={NODE_HEIGHT} rx="8" />
              <path className="graph-node-stripe" d={`M 5 12 V ${NODE_HEIGHT - 12}`} />
              {lines.map((line, i) => <text key={i} x="18" y={27 + i * 21}>{line}</text>)}
              <text className="graph-node-kind" x="18" y="75">{colorMode === "kind" ? PALETTE[color].label : node.type.replaceAll("_", " ")}{collapsed.has(node.id) ? " · collapsed" : ""}</text>
            </g>;
          })}
        </g>
      </svg>
      {!positions.length && <p className="graph-empty">No nodes in this view. Clear the color filter or expand branches.</p>}
      <span className="graph-visible-count">{positions.length} / {nodes.length} nodes</span>
    </div>
    <p className="graph-navigation-hint">Middle-drag anywhere or drag empty space to pan · Wheel to zoom at cursor · Double-click / F to focus · Home to fit. Enable Arrange nodes to move nodes; view controls never edit the package.</p>
  </>;
}
