import { useEffect, useMemo, useState } from "react";
import "./NativeRelationships.css";

type Node = { id: string; label: string; kind: string; position?: number[]; vertices?: number[][]; fields: Record<string, unknown>; search?: string };
export type RelationshipGraph = { schema_version: number; format: string; nodes: Node[]; edges: { source: string; target: string; label: string; resolution: string }[];
  node_count: number; edge_count: number; truncated: boolean; warnings: string[]; scope: string; read_only: boolean };

function validGraph(graph: RelationshipGraph) {
  const point = (value: unknown): value is number[] => Array.isArray(value) && value.length === 3 && value.every(n => typeof n === "number" && Number.isFinite(n) && Math.abs(n) <= 1e9);
  return graph?.schema_version === 1 && graph.read_only === true && typeof graph.format === "string" && typeof graph.scope === "string"
    && Array.isArray(graph.nodes) && graph.nodes.length <= 1000 && new Set(graph.nodes.map(node => node?.id)).size === graph.nodes.length
    && graph.nodes.every(node => node && typeof node.id === "string" && typeof node.label === "string" && typeof node.kind === "string"
      && node.fields && typeof node.fields === "object" && !Array.isArray(node.fields)
      && (node.position === undefined || point(node.position))
      && (node.vertices === undefined || Array.isArray(node.vertices) && node.vertices.length <= 128 && node.vertices.every(point))
      && (node.search === undefined || typeof node.search === "string"))
    && Array.isArray(graph.edges) && graph.edges.length <= 1800 && graph.edges.every(edge => edge && [edge.source, edge.target, edge.label, edge.resolution].every(value => typeof value === "string"))
    && Array.isArray(graph.warnings) && graph.warnings.every(value => typeof value === "string")
    && Number.isSafeInteger(graph.node_count) && graph.node_count >= graph.nodes.length
    && Number.isSafeInteger(graph.edge_count) && graph.edge_count >= graph.edges.length;
}

export default function NativeRelationships({ graph, locked, onSearch }: { graph: RelationshipGraph; locked: boolean; onSearch?: (query: string) => void }) {
  const [query, setQuery] = useState(""), [selected, setSelected] = useState("");
  const [plane, setPlane] = useState("XY"), [zoom, setZoom] = useState(1), [focus, setFocus] = useState(false);
  useEffect(() => { setSelected(""); setQuery(""); setZoom(1); setFocus(false); }, [graph]);
  const valid = validGraph(graph);
  const nodes = valid ? graph.nodes : [];
  const current = nodes.find(node => node.id === selected);
  const filtered = nodes.filter(node => `${node.label} ${node.kind} ${node.id}`.toLowerCase().includes(query.toLowerCase()));
  const shown = new Set(filtered.map(node => node.id));
  const positions = useMemo(() => {
    const a = plane[0] === "X" ? 0 : 1, b = plane[1] === "Y" ? 1 : 2;
    const spatial = nodes.filter(node => node.position?.length === 3);
    const coords = spatial.flatMap(node => [node.position!, ...(node.vertices || [])]);
    const [minA, maxA, minB, maxB] = coords.reduce((bounds, point) => [Math.min(bounds[0], point[a]), Math.max(bounds[1], point[a]), Math.min(bounds[2], point[b]), Math.max(bounds[3], point[b])], [Infinity, -Infinity, Infinity, -Infinity]);
    const span = Math.max(maxA - minA, maxB - minB, 1);
    const project = (point: number[]) => [40 + 620 * (point[a] - (minA + maxA - span) / 2) / span, 660 - 620 * (point[b] - (minB + maxB - span) / 2) / span];
    const map = new Map<string, number[]>();
    const groups = [...new Set(nodes.map(node => node.kind))];
    const counts: Record<string, number> = {};
    for (const node of nodes) counts[node.kind] = (counts[node.kind] || 0) + 1;
    const offsets: Record<string, number> = {};
    for (const node of nodes) {
      const index = offsets[node.kind] || 0; offsets[node.kind] = index + 1;
      map.set(node.id, node.position ? project(node.position) : [groups.length === 1 ? 350 : 60 + groups.indexOf(node.kind) * 580 / (groups.length - 1), counts[node.kind] === 1 ? 350 : 80 + index * 540 / (counts[node.kind] - 1)]);
    }
    return { map, project, spatial: spatial.length > 0,
      labelLength: groups.length > 8 ? 0 : Math.max(4, Math.min(18, Math.floor((580 / Math.max(1, groups.length - 1) - 24) / 12))) };
  }, [nodes, plane]);
  if (!valid) return <p role="alert">Relationship view returned invalid evidence.</p>;
  const connections = graph.edges.filter(edge => edge.source === selected || edge.target === selected);
  const center = focus && current ? positions.map.get(current.id)! : [350, 350];
  const viewSize = 700 / zoom;
  return <details className="native-relationships" open>
    <summary>{graph.format.replace(/^\./, "").toUpperCase()} relationships & {positions.spatial ? "spatial" : "dependency"} view</summary>
    <p>{graph.scope} Saved XML evidence; drafts appear here only after save and refresh.</p>
    <p>Green points have coordinates; blue references use a diagram layout, not world positions. Dashed links are unresolved or ambiguous.</p>
    <p>{graph.node_count} nodes · {graph.edge_count} relationships · {nodes.length} nodes displayed{graph.truncated ? " — display truncated" : ""}</p>
    {graph.warnings.length > 0 && <details><summary>Relationship findings ({graph.warnings.length})</summary><ul>{graph.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>}
    <div className="relationship-controls">
      <label>Find record<input aria-label="Find relationship record" value={query} onChange={event => setQuery(event.target.value)} /></label>
      {positions.spatial && <label>Projection<select aria-label="Relationship projection" value={plane} onChange={event => setPlane(event.target.value)}><option>XY</option><option>XZ</option><option>YZ</option></select></label>}
      <button className="quiet-button" onClick={() => setZoom(value => Math.min(16, value * 2))}>Zoom in</button><button className="quiet-button" onClick={() => setZoom(value => Math.max(1, value / 2))}>Zoom out</button>
      <button className="quiet-button" disabled={!current} onClick={() => { setFocus(true); setZoom(4); }}>Focus selected record</button><button className="quiet-button" onClick={() => { setFocus(false); setZoom(1); }}>Fit relationship view</button>
    </div>
    <div className="relationship-layout">
      <div className="relationship-records" role="listbox" aria-label="Relationship records">{filtered.map(node => <button className={selected === node.id ? "selected" : ""} key={node.id} role="option" aria-selected={selected === node.id} onClick={() => setSelected(node.id)}><strong>{node.label}</strong><small>{node.kind} · {node.id}</small></button>)}{!filtered.length && <p>No matching records.</p>}</div>
      <svg role="img" aria-label={positions.spatial ? "Native spatial relationships" : "Native dependency relationships"} viewBox={`${center[0] - viewSize / 2} ${center[1] - viewSize / 2} ${viewSize} ${viewSize}`}>
        {graph.edges.map((edge, i) => {
          const a = positions.map.get(edge.source), b = positions.map.get(edge.target);
          return a && b && shown.has(edge.source) && shown.has(edge.target) ? <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={edge.source === selected || edge.target === selected ? "#a0e49b" : "#486554"} strokeWidth={edge.source === selected || edge.target === selected ? 2 : 1} strokeDasharray={edge.resolution !== "local" ? "5 5" : undefined}><title>{edge.label} · {edge.resolution}</title></line> : null;
        })}
        {filtered.map(node => {
          const point = positions.map.get(node.id)!;
          const color = selected === node.id ? "#eecc78" : node.position ? "#67bc86" : "#8ca6d0";
          return <g key={node.id} onClick={() => setSelected(node.id)}>
            {node.vertices && node.position && <polygon points={node.vertices.map(vertex => positions.project(vertex).join(",")).join(" ")} fill={node.vertices.length < 3 ? "none" : "#244c3a"} stroke={color} strokeWidth={1} />}
            <circle cx={point[0]} cy={point[1]} r={!positions.spatial && filtered.length <= 40 ? (selected === node.id ? 10 : 7) : (selected === node.id ? 6 : 3)} fill={color} /><title>{node.label} · {node.kind}</title>
            {!positions.spatial && filtered.length <= 40 && positions.labelLength > 0 && <text x={point[0]} y={point[1]+26} fill={color} fontSize="20" textAnchor="middle">{node.label.length > positions.labelLength ? `${node.label.slice(0, positions.labelLength-1)}…` : node.label}</text>}
          </g>;
        })}
      </svg>
      <div className="relationship-detail">{current ? <><h4>{current.label}</h4><p>{current.kind} · {current.id}</p>{current.position && <p>Position: {current.position.map(value => value.toFixed(3)).join(", ")}</p>}
        <pre>{JSON.stringify(current.fields, null, 2)}</pre>
        {current.search && onSearch && <button className="quiet-button" disabled={locked} onClick={() => onSearch(current.search!)}>Find reference in game browser</button>}
        <h4>Connected relationships ({connections.length})</h4>{connections.map((edge, i) => {
          const target = edge.source === selected ? edge.target : edge.source;
          const other = nodes.find(node => node.id === target);
          return <div key={i}><button className="quiet-button" disabled={!other} onClick={() => setSelected(target)}>{other?.label || target}</button><small>{edge.label} · {edge.resolution}{!other ? " · outside displayed subset or missing" : ""}</small></div>;
        })}</> : <p>Select a record or point to inspect exact indices, properties and references.</p>}</div>
    </div>
  </details>;
}
