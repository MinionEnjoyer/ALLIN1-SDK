import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import NativeRelationships, { type RelationshipGraph } from "./NativeRelationships";
import relFixture from "./nativeRelRelationshipFixture.json";

const graph: RelationshipGraph = { schema_version: 1, format: ".ymap", read_only: true, scope: "Decoded document only.",
  node_count: 3, edge_count: 2, truncated: false, warnings: [],
  nodes: [{ id: "entity:0", label: "0: chair", kind: "entity", position: [10, 20, 30], fields: { index: 0 } },
    { id: "entity:1", label: "1: chair", kind: "entity", position: [20, 30, 70], fields: { index: 1 } },
    { id: "ref:chair", label: "prop_chair", kind: "archetype", search: "prop_chair", fields: {} }],
  edges: [{ source: "entity:1", target: "entity:0", label: "parent", resolution: "local" },
    { source: "entity:0", target: "ref:chair", label: "archetype", resolution: "external: unresolved" }] };

it("renders the actual typed REL fixture as a dependency graph with separate hash families", async () => {
  const user = userEvent.setup(), search = vi.fn();
  render(<NativeRelationships graph={relFixture} locked={false} onSearch={search} />);
  expect(screen.getByRole("img", { name: "Native dependency relationships" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Native dependency relationships" }).querySelectorAll("text")).toHaveLength(6);
  expect(screen.queryByLabelText("Relationship projection")).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: /demo_a/ }));
  expect(screen.getByText("sound · local", { selector: "small" })).toBeInTheDocument();
  expect(screen.getByText("category · external: unresolved", { selector: "small" })).toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: /audio\/demo.awc/ }));
  await user.click(screen.getByRole("button", { name: "Find reference in game browser" }));
  expect(search).toHaveBeenCalledExactlyOnceWith("demo.awc");
  expect(screen.getByText(/unresolved catalog entry/)).toBeInTheDocument();
});

it("navigates exact records, projects coordinates and follows connected references without writes", async () => {
  const user = userEvent.setup(), search = vi.fn();
  render(<NativeRelationships graph={graph} locked={false} onSearch={search} />);
  await user.click(screen.getByRole("option", { name: /0: chair/ }));
  expect(screen.getByText("Position: 10.000, 20.000, 30.000")).toBeInTheDocument();
  const svg = screen.getByRole("img", { name: "Native spatial relationships" });
  const first = svg.querySelector("circle")!.getAttribute("cx");
  await user.selectOptions(screen.getByLabelText("Relationship projection"), "XZ");
  expect(screen.getByLabelText("Relationship projection")).toHaveValue("XZ");
  // Geometry uses a shared scale and remains finite in each supported plane.
  expect(svg.querySelector("circle")!.getAttribute("cx")).not.toBe(first);
  expect(svg.innerHTML).not.toMatch(/NaN|Infinity/);
  await user.click(screen.getByRole("button", { name: "Focus selected record" }));
  expect(svg.getAttribute("viewBox")!.split(" ").slice(2)).toEqual(["175", "175"]);
  await user.click(screen.getByRole("button", { name: "Fit relationship view" }));
  expect(svg).toHaveAttribute("viewBox", "0 0 700 700");
  await user.click(screen.getByRole("button", { name: "prop_chair" }));
  await user.click(screen.getByRole("button", { name: "Find reference in game browser" }));
  expect(search).toHaveBeenCalledExactlyOnceWith("prop_chair");
  expect(screen.getByText(/external: unresolved/, { selector: "small" })).toBeInTheDocument();
});

it("keeps saved evidence inspectable but blocks handoff while an editor is guarded", async () => {
  const user = userEvent.setup(), search = vi.fn();
  render(<NativeRelationships graph={graph} locked onSearch={search} />);
  await user.click(screen.getByRole("option", { name: /prop_chair/ }));
  expect(screen.getByRole("button", { name: "Find reference in game browser" })).toBeDisabled();
  await user.type(screen.getByLabelText("Find relationship record"), "entity:1");
  expect(within(screen.getByRole("listbox")).getAllByRole("option")).toHaveLength(1);
  expect(search).not.toHaveBeenCalled();
});

it("shows truncation and missing endpoints instead of implying the graph is complete", async () => {
  const user = userEvent.setup();
  render(<NativeRelationships graph={{ ...graph, nodes: graph.nodes.slice(0, 1), truncated: true, warnings: ["Display limit reached"] }} locked={false} />);
  expect(screen.getByText(/3 nodes.*1 nodes displayed.*display truncated/)).toBeInTheDocument();
  await user.click(within(screen.getByRole("listbox")).getByRole("option"));
  expect(screen.getByRole("button", { name: "entity:1" })).toBeDisabled();
  expect(screen.getAllByText(/outside displayed subset or missing/)).toHaveLength(2);
});

it.each([
  { ...graph, read_only: false },
  { ...graph, nodes: [{ ...graph.nodes[0], position: [NaN, 1, 2] }] },
  { ...graph, nodes: [{ ...graph.nodes[0], vertices: [[1e10, 0, 0]] }] },
  { ...graph, nodes: [graph.nodes[0], graph.nodes[0]] },
])("rejects malformed relationship evidence", malformed => {
  render(<NativeRelationships graph={malformed} locked={false} />);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid evidence");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("resets selection and zoom when a refreshed document arrives", async () => {
  const user = userEvent.setup();
  const view = render(<NativeRelationships graph={graph} locked={false} />);
  await user.click(screen.getByRole("option", { name: /0: chair/ }));
  await user.click(screen.getByRole("button", { name: "Focus selected record" }));
  view.rerender(<NativeRelationships graph={{ ...graph, nodes: [] }} locked={false} />);
  expect(screen.getByRole("button", { name: "Focus selected record" })).toBeDisabled();
  expect(screen.getByRole("img")).toHaveAttribute("viewBox", "0 0 700 700");
});
