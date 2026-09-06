import { useMemo, useState } from "react";
import "./NativeCollisionView.css";

type Vector = [number, number, number];
type Triangle = [Vector, Vector, Vector];
export type CollisionPacket = {
  schema_version: number; read_only: boolean; scope: string;
  bounds: { min: number[]; max: number[]; size: number[] };
  triangle_count: number; displayed_triangles: number; group_count: number; truncated: boolean;
  primitive_counts: Record<string, number>; material_count: number;
  groups: { id: string; name: string; material_index: number | null; source_triangle_count: number; triangles: number[][][] }[];
};
const coordinate = (value: unknown): value is Vector => Array.isArray(value) && value.length === 3
  && value.every(item => typeof item === "number" && Number.isFinite(item) && Math.abs(item) <= 1e9);
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
function valid(packet: CollisionPacket): boolean {
  if (!packet || packet.schema_version !== 1 || packet.read_only !== true || typeof packet.scope !== "string"
    || !packet.bounds || !coordinate(packet.bounds.min) || !coordinate(packet.bounds.max)
    || packet.bounds.min.some((value, i) => value > packet.bounds.max[i])
    || !Array.isArray(packet.groups) || packet.groups.length > 96 || !count(packet.triangle_count)
    || !count(packet.displayed_triangles) || packet.displayed_triangles > 1500 || !count(packet.group_count)
    || !count(packet.material_count) || packet.group_count < packet.groups.length
    || !packet.primitive_counts || typeof packet.primitive_counts !== "object"
    || Object.entries(packet.primitive_counts).length > 64 || !Object.values(packet.primitive_counts).every(count)) return false;
  const ids = new Set<string>();
  let shown = 0;
  for (const group of packet.groups) {
    if (!group || typeof group.id !== "string" || ids.has(group.id) || typeof group.name !== "string"
      || !(group.material_index === null || count(group.material_index)) || !count(group.source_triangle_count)
      || !Array.isArray(group.triangles) || group.triangles.length > 1500 || group.triangles.length > group.source_triangle_count
      || !group.triangles.every(face => Array.isArray(face) && face.length === 3 && face.every(coordinate))) return false;
    ids.add(group.id); shown += group.triangles.length;
  }
  return shown === packet.displayed_triangles && shown <= packet.triangle_count
    && typeof packet.truncated === "boolean" && packet.truncated === (shown < packet.triangle_count);
}
const subtract = (a: Vector, b: Vector): Vector => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const colors = ["#58caba", "#e9b866", "#929df3", "#e38bae", "#90c570"];

export default function NativeCollisionView({ packet, draftDirty = false }: { packet: CollisionPacket; draftDirty?: boolean }) {
  const [yaw, setYaw] = useState(35), [pitch, setPitch] = useState(25), [zoom, setZoom] = useState(1);
  const [selection, setSelection] = useState(""), [wire, setWire] = useState(false), [normals, setNormals] = useState(false);
  const safe = useMemo(() => valid(packet), [packet]);
  const selected = safe ? packet.groups.find(group => group.id === selection) : undefined;
  const faces = useMemo(() => {
    if (!safe) return [];
    const center = packet.bounds.min.map((value, i) => (value + packet.bounds.max[i]) / 2) as Vector;
    const span = Math.max(...packet.bounds.max.map((value, i) => value - packet.bounds.min[i]), 1e-6);
    const cy = Math.cos(yaw*Math.PI/180), sy = Math.sin(yaw*Math.PI/180), cp = Math.cos(pitch*Math.PI/180), sp = Math.sin(pitch*Math.PI/180);
    const project = (point: Vector): Vector => {
      const [x, y, z] = subtract(point, center).map(value => value/span);
      const horizontal = cy*x-sy*y, depth = sy*x+cy*y;
      return [350+horizontal*330*zoom, 250-(cp*z-sp*depth)*330*zoom, cp*depth+sp*z];
    };
    return packet.groups.flatMap((group, gi) => (!selected || group === selected) ? group.triangles.map((value, ti) => {
      const triangle = value as Triangle, projected = triangle.map(project);
      const [a, b] = [subtract(triangle[1], triangle[0]), subtract(triangle[2], triangle[0])];
      const normal: Vector = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
      const length = Math.hypot(...normal);
      const midpoint = triangle[0].map((_, i) => triangle.reduce((sum, point) => sum+point[i], 0)/3) as Vector;
      return { key: `${group.id}:${ti}`, points: projected.map(point => `${point[0]},${point[1]}`).join(" "),
        depth: projected.reduce((sum, point) => sum+point[2], 0)/3, color: colors[gi%colors.length],
        normal: length > 1e-12 ? [project(midpoint), project(midpoint.map((v, i) => v+normal[i]/length*span*.045) as Vector)] : null };
    }) : []).sort((a, b) => b.depth-a.depth);
  }, [packet, safe, yaw, pitch, zoom, selected]);
  if (!safe) return <p role="alert">Collision mesh evidence is invalid or exceeds display limits.</p>;
  return <details open className="native-collision"><summary>Collision geometry & face normals</summary>
    <p>{packet.scope}</p>
    {draftDirty && <p role="status">Preview shows saved XML, not the unsaved draft.</p>}
    <p>{packet.displayed_triangles} / {packet.triangle_count} display triangles · {packet.groups.length} / {packet.group_count} groups · {packet.material_count} geometry material records</p>
    {packet.truncated && <p role="status">Display is sampled. Missing faces or groups do not imply missing collision in the source.</p>}
    <div className="native-collision-controls">
      <label>Yaw<input aria-label="Collision yaw" type="range" min="-180" max="180" value={yaw} onChange={e => setYaw(Number(e.target.value))} /></label>
      <label>Pitch<input aria-label="Collision pitch" type="range" min="-90" max="90" value={pitch} onChange={e => setPitch(Number(e.target.value))} /></label>
      <label>Zoom<input aria-label="Collision zoom" type="range" min="0.25" max="3" step="0.05" value={zoom} onChange={e => setZoom(Number(e.target.value))} /></label>
      <label><input type="checkbox" checked={wire} onChange={e => setWire(e.target.checked)} />Wireframe</label>
      <label><input type="checkbox" checked={normals} onChange={e => setNormals(e.target.checked)} />Face normals</label>
      <button className="quiet-button" onClick={() => { setYaw(35); setPitch(25); setZoom(1); setSelection(""); }}>Reset collision view</button>
    </div>
    <div className="native-collision-layout"><aside>
      <label>Visible group<select aria-label="Collision group" value={selected?.id ?? ""} onChange={e => setSelection(e.target.value)}>
        <option value="">All sampled groups</option>{packet.groups.map(group => <option key={group.id} value={group.id}>{group.id}: {group.name} · material {group.material_index ?? "unspecified"}</option>)}
      </select></label>
      {selected && <p>{selected.triangles.length} / {selected.source_triangle_count} group triangles. Material index {selected.material_index ?? "unspecified"} is local to the owning bound; it is not a global surface ID.</p>}
      <ul>{Object.entries(packet.primitive_counts).map(([name, value]) => <li key={name}>{name}: {value}</li>)}</ul>
      <p>Normals follow transformed triangle winding; they are not engine contact normals.</p>
    </aside><svg role="img" aria-label="Native collision geometry" viewBox="0 0 700 500">
      <title>Read-only collision surface inspection</title>
      {faces.map(face => <g key={face.key}><polygon points={face.points} fill={wire ? "none" : face.color} fillOpacity="0.3" stroke={face.color} strokeWidth="0.7" />
        {normals && face.normal && <line data-normal="true" x1={face.normal[0][0]} y1={face.normal[0][1]} x2={face.normal[1][0]} y2={face.normal[1][1]} stroke="#ffca6a" strokeWidth="1.2" />}</g>)}
    </svg></div>
  </details>;
}
