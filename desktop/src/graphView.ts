/** View-only graph organization. Never adds fields to the portable document. */
export interface GraphNode { id: string; type: string; name?: string; x: number; y: number; source?: string; size?: number; sha256?: string; config?: Record<string, string> }
export interface GraphEdge { parent?: string; child?: string; from?: string; to?: string; from_port?: string; to_port?: string }
export interface GraphFinding { node_id?: string; message: string; severity: string }
export type ColorMode = "kind" | "findings" | "none";
export type LayoutMode = "hierarchy" | "groups" | "saved";
export type SortMode = "name" | "color" | "source";
export const NODE_WIDTH = 264, NODE_HEIGHT = 92;
export const parentOf = (edge: GraphEdge) => edge.parent ?? edge.from!;
export const childOf = (edge: GraphEdge) => edge.child ?? edge.to!;
export const PALETTE = {
  archive: { label: "Archives", color: "#f6c76b" },
  directory: { label: "Folders", color: "#91baff" },
  model: { label: "Models", color: "#70dec6" },
  texture: { label: "Textures", color: "#d6a4ff" },
  metadata: { label: "Metadata", color: "#ffae86" },
  language: { label: "Game text", color: "#e7d985" },
  relationship: { label: "Relationships", color: "#90d7ed" },
  step: { label: "Build steps", color: "#9ee396" },
  other: { label: "Other files", color: "#bec9d6" },
  error: { label: "Reported errors", color: "#ff9da8" },
  warning: { label: "Reported warnings", color: "#f6c76b" },
  info: { label: "Reported information", color: "#91baff" },
  neutral: { label: "No reported finding", color: "#bec9d6" },
  plain: { label: "All nodes", color: "#bec9d6" },
} as const;
export type ColorKey = keyof typeof PALETTE;
export function nodeColor(node: GraphNode, mode: ColorMode, findings: GraphFinding[] = []): ColorKey {
  if (mode === "none") return "plain";
  if (mode === "findings") {
    const levels = findings.filter(f => f.node_id === node.id).map(f => f.severity.toLowerCase());
    return levels.some(s => ["error", "critical", "fatal"].includes(s)) ? "error"
      : levels.some(s => ["warning", "warn"].includes(s)) ? "warning" : levels.length ? "info" : "neutral";
  }
  if (["archive", "sealed_archive"].includes(node.type)) return "archive";
  if (node.type === "directory") return "directory";
  if (["vehicle", "handling", "material", "texture_binding", "archetype"].includes(node.type) || node.type.startsWith("vehicle_")) return "relationship";
  if (!["file", "sealed_archive"].includes(node.type)) return "step";
  const extension = (node.name || node.source || "").split(".").pop()!.toLowerCase();
  if (["yft", "ydr", "ydd", "ybn"].includes(extension)) return "model";
  if (["ytd", "dds", "png", "jpg", "jpeg"].includes(extension)) return "texture";
  if (["meta", "xml", "json", "toml", "ymt", "ytyp", "ymap"].includes(extension)) return "metadata";
  return extension === "gxt2" ? "language" : "other";
}
const compareName = (a: GraphNode, b: GraphNode) => (a.name || a.id).localeCompare(b.name || b.id, undefined, { numeric: true }) || a.id.localeCompare(b.id);
export function sortNodes(nodes: GraphNode[], sort: SortMode, mode: ColorMode, findings: GraphFinding[]) {
  const colors = Object.keys(PALETTE);
  return [...nodes].sort((a, b) => (sort === "color" ? colors.indexOf(nodeColor(a, mode, findings)) - colors.indexOf(nodeColor(b, mode, findings))
    : sort === "source" ? (a.source || "").localeCompare(b.source || "") : 0) || compareName(a, b));
}
export function searchNodes(nodes: GraphNode[], query: string) {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return nodes.filter(node => terms.every(term => `${node.id} ${node.name || ""} ${node.type} ${node.source || ""}`.toLowerCase().includes(term)))
    .sort((a, b) => Number((b.name || "").toLowerCase() === query.trim().toLowerCase()) - Number((a.name || "").toLowerCase() === query.trim().toLowerCase()) || compareName(a, b));
}
export function hiddenDescendants(edges: GraphEdge[], collapsed: Set<string>) {
  const children = new Map<string, string[]>();
  edges.forEach(edge => children.set(parentOf(edge), [...(children.get(parentOf(edge)) || []), childOf(edge)]));
  const hidden = new Set<string>(), queue = [...collapsed];
  for (let i = 0; i < queue.length; i++) for (const child of children.get(queue[i]) || []) {
    if (!hidden.has(child) && !collapsed.has(child)) { hidden.add(child); queue.push(child); }
    else if (collapsed.has(child)) hidden.add(child);
  }
  return hidden;
}
export function ancestors(id: string, edges: GraphEdge[]) {
  const result = new Set<string>(), queue = [id];
  for (let i = 0; i < queue.length; i++) for (const edge of edges) if (childOf(edge) === queue[i] && !result.has(parentOf(edge))) {
    result.add(parentOf(edge)); queue.push(parentOf(edge));
  }
  return result;
}
export function organizeNodes(nodes: GraphNode[], edges: GraphEdge[], layout: LayoutMode, mode: ColorMode, findings: GraphFinding[]): GraphNode[] {
  if (layout === "saved") return nodes;
  if (layout === "groups") {
    const ordered = sortNodes(nodes, "color", mode, findings), result: GraphNode[] = [];
    const groups = new Map<ColorKey, GraphNode[]>();
    for (const node of ordered) { const color = nodeColor(node, mode, findings); groups.set(color, [...(groups.get(color) || []), node]); }
    let bandY = 70, bandHeight = 0, groupColumn = 0;
    for (const group of groups.values()) {
      group.forEach((node, index) => result.push({ ...node, x: 40 + groupColumn * 990 + index % 3 * 310, y: bandY + Math.floor(index / 3) * 126 }));
      bandHeight = Math.max(bandHeight, Math.ceil(group.length / 3) * 126); groupColumn++;
      if (groupColumn === 3) { groupColumn = 0; bandY += bandHeight + 80; bandHeight = 0; }
    }
    return result;
  }
  const byId = new Map(nodes.map(node => [node.id, node])), children = new Map<string, GraphNode[]>(), incoming = new Set<string>();
  for (const edge of edges) if (byId.has(parentOf(edge)) && byId.has(childOf(edge))) {
    children.set(parentOf(edge), [...(children.get(parentOf(edge)) || []), byId.get(childOf(edge))!]); incoming.add(childOf(edge));
  }
  const positions = new Map<string, GraphNode>(); let row = 0;
  const visiting = new Set<string>();
  function place(node: GraphNode, depth: number): number {
    if (positions.has(node.id)) return positions.get(node.id)!.y;
    if (visiting.has(node.id)) return 40 + row * 126;
    visiting.add(node.id);
    const descendants = sortNodes(children.get(node.id) || [], "color", mode, findings).filter(child => !visiting.has(child.id) && !positions.has(child.id));
    const rows = descendants.map(child => place(child, depth + 1));
    const y = rows.length ? (rows[0] + rows[rows.length - 1]) / 2 : 40 + row++ * 126;
    visiting.delete(node.id); positions.set(node.id, { ...node, x: 40 + depth * 340, y }); return y;
  }
  for (const node of sortNodes(nodes.filter(node => !incoming.has(node.id)), "color", mode, findings)) place(node, 0);
  for (const node of nodes) if (!positions.has(node.id)) place(node, 0); // Disconnected/cyclic drafts remain navigable.
  return nodes.map(node => positions.get(node.id)!);
}
export interface Camera { x: number; y: number; zoom: number }
export const clampZoom = (zoom: number) => Math.max(.1, Math.min(2, Number.isFinite(zoom) ? zoom : 1));
export function zoomAt(camera: Camera, zoom: number, x: number, y: number): Camera {
  const next = clampZoom(zoom), ratio = next / camera.zoom;
  return { zoom: next, x: x - (x - camera.x) * ratio, y: y - (y - camera.y) * ratio };
}
export function fitCamera(nodes: GraphNode[], width: number, height: number, minimum = .1): Camera {
  if (!nodes.length) return { x: 32, y: 32, zoom: 1 };
  const minX = Math.min(...nodes.map(n => n.x)), minY = Math.min(...nodes.map(n => n.y));
  const maxX = Math.max(...nodes.map(n => n.x + NODE_WIDTH)), maxY = Math.max(...nodes.map(n => n.y + NODE_HEIGHT));
  const zoom = clampZoom(Math.max(minimum, Math.min(1, Math.max(1, width - 64) / (maxX - minX), Math.max(1, height - 64) / (maxY - minY))));
  return { zoom, x: width / 2 - (minX + maxX) / 2 * zoom, y: height / 2 - (minY + maxY) / 2 * zoom };
}
export function wrapNodeName(value: string): string[] {
  if (value.length <= 28) return [value];
  const split = Math.max(value.lastIndexOf("_", 27) + 1, value.lastIndexOf("/", 27) + 1, value.lastIndexOf(" ", 27) + 1);
  const at = split > 10 ? split : 28;
  return [value.slice(0, at), value.length - at > 28 ? `${value.slice(at, at + 25)}…` : value.slice(at)];
}
