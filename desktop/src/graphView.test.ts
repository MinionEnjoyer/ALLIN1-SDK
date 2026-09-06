import { describe, expect, it } from "vitest";
import { ancestors, fitCamera, hiddenDescendants, NODE_HEIGHT, NODE_WIDTH, nodeColor, organizeNodes, searchNodes, sortNodes, wrapNodeName, zoomAt, type GraphNode } from "./graphView";
const node = (id: string, type = "file", name = id): GraphNode => ({ id, name, type, x: 0, y: 0 });
const nodes = [node("root", "archive"), node("folder", "directory"), node("a", "file", "car.yft"), node("b", "file", "car.ytd"), node("c", "file", "handling.meta")];
const edges = [{ parent: "root", child: "folder" }, { parent: "folder", child: "a" }, { parent: "folder", child: "b" }, { parent: "root", child: "c" }];
describe("view-only graph organization", () => {
  it("separates asset categories without pretending neutral nodes passed validation", () => {
    expect(nodes.map(n => nodeColor(n, "kind"))).toEqual(["archive", "directory", "model", "texture", "metadata"]);
    expect(nodeColor(nodes[2], "findings", [])).toBe("neutral");
    expect(nodeColor(nodes[2], "findings", [{ node_id: "a", severity: "warning", message: "review" }, { node_id: "a", severity: "error", message: "missing" }])).toBe("error");
    expect(nodeColor(nodes[2], "none")).toBe("plain");
  });
  it("supports color, natural-name and path sorting without reordering source nodes", () => {
    const original = JSON.stringify(nodes);
    expect(sortNodes(nodes, "color", "kind", []).map(n => n.id)).toEqual(["root", "folder", "a", "b", "c"]);
    expect(sortNodes([node("10"), node("2")], "name", "kind", []).map(n => n.id)).toEqual(["2", "10"]);
    expect(sortNodes([{ ...nodes[2], source: "z/car.yft" }, { ...nodes[3], source: "a/car.ytd" }], "source", "kind", [])[0].id).toBe("b");
    expect(JSON.stringify(nodes)).toBe(original);
  });
  it.each(["hierarchy", "groups"] as const)("%s places every node without overlaps or editing the input", layout => {
    const before = JSON.stringify(nodes), placed = organizeNodes(nodes, edges, layout, "kind", []);
    for (const a of placed) for (const b of placed) if (a.id !== b.id) {
      expect(a.x + NODE_WIDTH <= b.x || b.x + NODE_WIDTH <= a.x || a.y + NODE_HEIGHT <= b.y || b.y + NODE_HEIGHT <= a.y).toBe(true);
    }
    expect(JSON.stringify(nodes)).toBe(before);
    expect(organizeNodes(nodes, edges, "saved", "kind", [])).toBe(nodes);
  });
  it("keeps disconnected and cyclic drafts finite and navigable", () => {
    const draft = [...edges, { parent: "a", child: "root" }];
    const placed = organizeNodes([...nodes, node("loose")], draft, "hierarchy", "kind", []);
    expect(placed).toHaveLength(6); expect(placed.every(n => Number.isFinite(n.x) && Number.isFinite(n.y))).toBe(true);
    expect(ancestors("a", draft).has("folder")).toBe(true);
  });
  it("expands nested ancestry and hides all descendants, not just direct children", () => {
    expect([...ancestors("a", edges)]).toEqual(["folder", "root"]);
    expect([...hiddenDescendants(edges, new Set(["root", "folder"]))].sort()).toEqual(["a", "b", "c", "folder"]);
  });
  it("ranks an exact name before partial matches and searches source paths with multiple terms", () => {
    const values = [node("x", "file", "car_hi.yft"), { ...node("a", "file", "car.yft"), source: "C:/Test Pack/models/car.yft" }];
    expect(searchNodes(values, "CAR.YFT")[0].id).toBe("a");
    expect(searchNodes(values, "test models").map(n => n.id)).toEqual(["a"]);
    expect(searchNodes(values, "missing")).toEqual([]);
  });
  it("wraps names instead of silently dropping their suffix and preserves the full value", () => {
    const name = "27rs5b10_frontbumper_carbon_variant.yft";
    expect(wrapNodeName(name).join("")).toBe(name);
    expect(wrapNodeName("x".repeat(100))[1].endsWith("…")).toBe(true);
  });
  it("zooms around a fixed cursor position and clamps consistently at both ends", () => {
    const camera = { x: -200, y: 30, zoom: .5 }, next = zoomAt(camera, 1, 300, 200);
    expect((300 - camera.x) / camera.zoom).toBe((300 - next.x) / next.zoom);
    expect((200 - camera.y) / camera.zoom).toBe((200 - next.y) / next.zoom);
    expect(zoomAt(camera, .001, 0, 0).zoom).toBe(.1);
    expect(zoomAt(camera, 50, 0, 0).zoom).toBe(2);
  });
  it("fits actual bounds, not an arbitrary origin, and handles empty graphs", () => {
    const camera = fitCamera([{ ...nodes[0], x: 4000, y: 2000 }], 900, 600);
    expect(camera.x + (4000 + NODE_WIDTH / 2) * camera.zoom).toBe(450);
    expect(camera.y + (2000 + NODE_HEIGHT / 2) * camera.zoom).toBe(300);
    expect(fitCamera([], 0, 0).zoom).toBe(1);
  });
});
