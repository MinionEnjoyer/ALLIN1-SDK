import { useState } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NodeCanvas from "./NodeCanvas";
import GraphWorkbench from "./GraphWorkbench";
import { createPreviewClient } from "./previewClient";
import type { LayoutMode } from "./graphView";
import type { Envelope } from "./types";

const nodes = [
  { id: "root", type: "archive", name: "dlc.rpf", x: 40, y: 40 },
  { id: "folder", type: "directory", name: "models", x: 400, y: 40 },
  { id: "car", type: "file", name: "car.yft", x: 800, y: 40, source: "C:/fixture/car.yft" },
  { id: "texture", type: "file", name: "car.ytd", x: 800, y: 180 },
];
const edges = [{ parent: "root", child: "folder" }, { parent: "folder", child: "car" }, { parent: "folder", child: "texture" }];
const empty: [] = [];
beforeAll(() => {
  // jsdom omits PointerEvent. Supply event fields only; no gesture behavior is mocked.
  class TestPointerEvent extends MouseEvent { pointerId: number; constructor(type: string, init: PointerEventInit) { super(type, init); this.pointerId = init.pointerId || 1; } }
  vi.stubGlobal("PointerEvent", TestPointerEvent);
});
afterAll(() => vi.unstubAllGlobals());
function canvas(locked = false) {
  const move = vi.fn(), select = vi.fn();
  function Harness() {
    const [layout, setLayout] = useState<LayoutMode>("saved");
    return <NodeCanvas nodes={nodes} edges={edges} containment={edges} findings={empty} selected="root" select={select} move={move} locked={locked}
      layout={layout} setLayout={setLayout} colorMode="kind" colorFilter="all" focusRequest={null} matches={null} />;
  }
  render(<Harness />);
  return { move, select, viewport: screen.getByRole("region", { name: "Graph viewport" }), camera: () => screen.getByTestId("graph-camera").getAttribute("transform") };
}
describe("graph navigation", () => {
  it.each([false, true])("middle-drag over a node pans without selection or document edits (locked=%s)", locked => {
    const test = canvas(locked), before = test.camera(), node = screen.getByRole("button", { name: "Select node car.yft" });
    fireEvent.pointerDown(node, { button: 1, pointerId: 7, clientX: 400, clientY: 200 });
    fireEvent.pointerMove(test.viewport, { buttons: 4, pointerId: 7, clientX: 530, clientY: 270 });
    fireEvent.pointerUp(test.viewport, { button: 1, pointerId: 7, clientX: 530, clientY: 270 });
    expect(test.camera()).not.toBe(before); expect(test.move).not.toHaveBeenCalled(); expect(test.select).not.toHaveBeenCalled();
    expect(test.viewport).not.toHaveClass("is-panning");
  });
  it("right button never starts a node edit or pan", () => {
    const test = canvas(), before = test.camera();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Select node car.yft" }), { button: 2, pointerId: 1 });
    fireEvent.pointerMove(test.viewport, { pointerId: 1, clientX: 80 }); fireEvent.pointerUp(test.viewport, { pointerId: 1, button: 2 });
    expect(test.camera()).toBe(before); expect(test.move).not.toHaveBeenCalled(); expect(test.select).not.toHaveBeenCalled();
  });
  it("pointer cancellation, lost capture, and window blur release panning", () => {
    const test = canvas();
    for (const cancel of [() => fireEvent.pointerCancel(test.viewport), () => fireEvent.lostPointerCapture(test.viewport), () => fireEvent(window, new Event("blur"))]) {
      fireEvent.pointerDown(test.viewport, { button: 1, pointerId: 1, clientX: 50 });
      cancel(); const after = test.camera();
      fireEvent.pointerMove(test.viewport, { pointerId: 1, clientX: 300 });
      expect(test.camera()).toBe(after); expect(test.viewport).not.toHaveClass("is-panning");
    }
  });
  it("left dragging requires explicit Arrange mode, and a stationary click does not create a draft", async () => {
    const test = canvas(), node = screen.getByRole("button", { name: "Select node car.yft" });
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 200 });
    fireEvent.pointerMove(test.viewport, { pointerId: 1, clientX: 240 }); fireEvent.pointerUp(test.viewport, { pointerId: 1, clientX: 240 });
    expect(test.move).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Arrange nodes" }));
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 200 }); fireEvent.pointerUp(test.viewport, { pointerId: 1, clientX: 200 });
    expect(test.move).not.toHaveBeenCalled();
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 200, clientY: 50 });
    fireEvent.pointerMove(test.viewport, { pointerId: 1, clientX: 240, clientY: 80 }); fireEvent.pointerUp(test.viewport, { pointerId: 1, clientX: 240, clientY: 80 });
    expect(test.move).toHaveBeenCalledTimes(1); expect(test.move.mock.calls[0][0]).toBe("car");
  });
  it("Space-left-drag over a node navigates rather than editing", () => {
    const test = canvas(), before = test.camera();
    fireEvent.keyDown(test.viewport, { key: " " });
    fireEvent.pointerDown(screen.getByRole("button", { name: "Select node car.yft" }), { button: 0, pointerId: 1, clientX: 10 });
    fireEvent.pointerMove(test.viewport, { pointerId: 1, clientX: 100 }); fireEvent.pointerUp(test.viewport, { pointerId: 1, clientX: 100 });
    fireEvent.keyUp(test.viewport, { key: " " });
    expect(test.camera()).not.toBe(before); expect(test.move).not.toHaveBeenCalled(); expect(test.select).not.toHaveBeenCalled();
  });
  it("wheel zoom prevents page scroll and uses the same lower bound as toolbar zoom", async () => {
    const test = canvas(), before = test.camera();
    const event = new WheelEvent("wheel", { deltaY: -120, clientX: 150, clientY: 100, cancelable: true, bubbles: true });
    act(() => test.viewport.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true); expect(test.camera()).not.toBe(before);
    await userEvent.click(screen.getByText("Exact zoom"));
    fireEvent.change(screen.getByRole("textbox", { name: "Graph zoom" }), { target: { value: "10" } });
    expect(screen.getByRole("button", { name: "Zoom out graph" })).toBeDisabled();
    expect(test.move).not.toHaveBeenCalled();
  });
  it.each(["Fit graph", "Focus selected"])("%s consumes a pending resize without a second camera shift", command => {
    let notifyResize = () => {};
    const original = globalThis.ResizeObserver;
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { notifyResize = callback; }
      observe() {} disconnect() {}
    });
    try {
      const test = canvas();
      Object.defineProperties(test.viewport, { clientWidth: { value: 1200, configurable: true }, clientHeight: { value: 800, configurable: true } });
      fireEvent.click(screen.getByRole("button", { name: command }));
      const fitted = test.camera();
      act(() => notifyResize());
      expect(test.camera()).toBe(fitted);
      Object.defineProperty(test.viewport, "clientWidth", { value: 1400, configurable: true });
      act(() => notifyResize());
      expect(test.camera()).not.toBe(fitted);
      expect(test.move).not.toHaveBeenCalled();
    } finally { vi.stubGlobal("ResizeObserver", original); }
  });
});

function workbench(initialFocus = "") {
  const client = createPreviewClient("rpf"), guard = vi.fn();
  client.startJob = vi.fn(async (_operation, _payload, _revision, event) => {
    event({ protocol_version: "1.0.0", request_id: "fixture", job_id: "fixture", sequence: 1, risk: "read_only", operation: "result", terminal: true, payload: { result: { kind: "workspace_session", module: "graph", schema_version: 1, game_write_performed: false, read_only: true,
      state_sha256: "a".repeat(64), workspace: "C:/fixture/graph.json", issues: [], document: { schema_version: 1, operation: "rpf_package_graph", root_id: "root", nodes, edges } } } } as Envelope);
    return { job_id: "fixture" } as Awaited<ReturnType<typeof client.startJob>>;
  });
  const view = render(<GraphWorkbench client={client} module="graph" initialSource="C:/fixture/graph.json" initialFocus={initialFocus} onGuardChange={guard} />);
  return { client, guard, view };
}
describe("graph organization and search", () => {
  it("honors CLI/API focus after loading and repeated same-document launch requests", async () => {
    const { client, guard, view } = workbench("car");
    await waitFor(() => expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("aria-pressed", "true"));
    await userEvent.click(screen.getByRole("button", { name: "Select node car.ytd" }));
    expect(screen.getByRole("button", { name: "Select node car.ytd" })).toHaveAttribute("aria-pressed", "true");
    view.rerender(<GraphWorkbench client={client} module="graph" initialSource="C:/fixture/graph.json" initialFocus="car" initialFocusSerial={2} onGuardChange={guard} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("aria-pressed", "true"));
    expect(guard).toHaveBeenLastCalledWith(false);
  });
  it("focuses a search match, expands hidden ancestors, and never dirties the document", async () => {
    const { guard } = workbench();
    await screen.findByRole("button", { name: "Select node car.yft" });
    await userEvent.click(screen.getByRole("button", { name: "Collapse selected branch" }));
    expect(screen.queryByRole("button", { name: "Select node car.yft" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Find node" }), { target: { value: "car.yft" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText("1 match")).toBeInTheDocument();
    expect(screen.getByLabelText("Node name")).toHaveValue("car.yft"); expect(guard).toHaveBeenLastCalledWith(false);
  });
  it("cycles search matches, handles no results and clears an incompatible color filter", async () => {
    workbench(); await screen.findByRole("button", { name: "Select node car.yft" });
    await userEvent.click(screen.getByRole("button", { name: "Textures · 1" }));
    expect(screen.queryByRole("button", { name: "Select node car.yft" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Find node"), { target: { value: "car" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("aria-pressed", "true"));
    await userEvent.click(screen.getByRole("button", { name: "Next match" }));
    expect(screen.getByRole("button", { name: "Select node car.ytd" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(screen.getByLabelText("Find node"), { key: "Enter", shiftKey: true });
    expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByLabelText("Find node"), { target: { value: "does-not-exist" } });
    expect(screen.getByText("0 matches")).toBeInTheDocument(); expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
  });
  it("color/layout/list and expanded-canvas controls remain view-only", async () => {
    const { guard } = workbench(); await screen.findByRole("button", { name: "Select node car.yft" });
    fireEvent.change(screen.getByLabelText("View layout"), { target: { value: "groups" } });
    fireEvent.change(screen.getByLabelText("Color nodes by"), { target: { value: "findings" } });
    expect(screen.getByRole("button", { name: "Select node car.yft" })).toHaveAttribute("data-color", "neutral");
    fireEvent.change(screen.getByLabelText("Sort node list"), { target: { value: "name" } });
    await userEvent.click(screen.getByRole("button", { name: "Expand canvas" }));
    expect(screen.getByRole("region", { name: "Package layout" })).toHaveClass("graph-canvas-only");
    expect(guard).toHaveBeenLastCalledWith(false);
  });
  it("does not replace unapplied inspector changes when searching", async () => {
    workbench(); await screen.findByRole("button", { name: "Select node car.yft" });
    fireEvent.change(screen.getByLabelText("Node name"), { target: { value: "pending.rpf" } });
    expect(screen.getByLabelText("Find node")).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Arrange nodes" })).toBeDisabled();
    expect(screen.getByLabelText("Node name")).toHaveValue("pending.rpf");
  });
});
