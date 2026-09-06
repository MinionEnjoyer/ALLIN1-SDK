import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import NativeCollisionView, { type CollisionPacket } from "./NativeCollisionView";

const packet: CollisionPacket = {
  schema_version: 1, read_only: true, scope: "Composite transforms applied. Curves use display tessellation.",
  bounds: { min: [0, 0, 0], max: [1, 1, 1], size: [1, 1, 1] }, triangle_count: 2, displayed_triangles: 2,
  group_count: 2, truncated: false, primitive_counts: { Triangle: 2 }, material_count: 2,
  groups: [{ id: "0", name: "Triangle mesh", material_index: 0, source_triangle_count: 1, triangles: [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]] },
    { id: "1", name: "Triangle mesh", material_index: 1, source_triangle_count: 1, triangles: [[[0, 0, 1], [1, 0, 1], [0, 1, 1]]] }],
};

it("renders, orbits, isolates materials and displays derived normals without backend actions", async () => {
  const user = userEvent.setup();
  render(<NativeCollisionView packet={packet} />);
  const svg = screen.getByRole("img", { name: "Native collision geometry" });
  expect(svg.querySelectorAll("polygon")).toHaveLength(2);
  const first = svg.querySelector("polygon")!.getAttribute("points");
  fireEvent.change(screen.getByLabelText("Collision yaw"), { target: { value: "90" } });
  expect(svg.querySelector("polygon")!.getAttribute("points")).not.toBe(first);
  fireEvent.change(screen.getByLabelText("Collision pitch"), { target: { value: "60" } });
  fireEvent.change(screen.getByLabelText("Collision zoom"), { target: { value: "2" } });
  await user.click(screen.getByLabelText("Face normals"));
  expect(svg.querySelectorAll("line[data-normal]")).toHaveLength(2);
  await user.click(screen.getByLabelText("Wireframe"));
  expect(svg.querySelector("polygon")).toHaveAttribute("fill", "none");
  await user.selectOptions(screen.getByLabelText("Collision group"), "1");
  expect(svg.querySelectorAll("polygon")).toHaveLength(1);
  expect(screen.getByText(/Material index 1 is local/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reset collision view" }));
  expect(svg.querySelectorAll("polygon")).toHaveLength(2);
  expect(screen.getByLabelText("Collision yaw")).toHaveValue("35");
  expect(svg.innerHTML).not.toMatch(/NaN|Infinity/);
});

it("labels sampling and saved-state evidence while allowing read-only inspection of dirty drafts", () => {
  render(<NativeCollisionView packet={{ ...packet, triangle_count: 2000, group_count: 110, truncated: true }} draftDirty />);
  expect(screen.getByText(/Preview shows saved XML/)).toBeInTheDocument();
  expect(screen.getByText(/Display is sampled/)).toBeInTheDocument();
  expect(screen.getByText(/2 \/ 2000 display triangles/)).toBeInTheDocument();
});

it.each([
  { schema_version: 2 }, { read_only: false }, { groups: null }, { groups: {} },
  { displayed_triangles: 1501 }, { triangle_count: 1 }, { truncated: true },
  { groups: [packet.groups[0], packet.groups[0]] }, { primitive_counts: { Triangle: -1 } },
  { bounds: { ...packet.bounds, min: [NaN, 0, 0] } },
  { groups: [{ ...packet.groups[0], triangles: [[[Infinity, 0, 0], [1, 0, 0], [0, 1, 0]]] }] },
])("rejects malformed or oversized mesh evidence %#", replacement => {
  render(<NativeCollisionView packet={{ ...packet, ...replacement } as CollisionPacket} />);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid or exceeds display limits");
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("does not manufacture normals for degenerate triangles", async () => {
  render(<NativeCollisionView packet={{ ...packet, triangle_count: 1, displayed_triangles: 1, group_count: 1,
    groups: [{ ...packet.groups[0], triangles: [[[0, 0, 0], [0, 0, 0], [0, 0, 0]]] }] }} />);
  await userEvent.click(screen.getByLabelText("Face normals"));
  expect(screen.getByRole("img").querySelectorAll("line")).toHaveLength(0);
});
