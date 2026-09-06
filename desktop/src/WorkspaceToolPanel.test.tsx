import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WorkspaceToolPanel from "./WorkspaceToolPanel";

beforeEach(() => localStorage.clear());

it("collapses and remembers the preference without unmounting workspace state", async () => {
  const user = userEvent.setup();
  const view = render(<><WorkspaceToolPanel title="RPF tools" storageKey="panel-test">
    <button role="tab" aria-selected="true">Archive inspection</button>
  </WorkspaceToolPanel><input aria-label="Editor draft" defaultValue="unsaved" /></>);
  await user.click(screen.getByRole("button", { name: "Collapse RPF tools" }));
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Expand RPF tools" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByLabelText("Editor draft")).toHaveValue("unsaved");
  expect(localStorage.getItem("panel-test")).toBe("collapsed");
  view.unmount();
  render(<WorkspaceToolPanel title="RPF tools" storageKey="panel-test"><button role="tab">Archive inspection</button></WorkspaceToolPanel>);
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Expand RPF tools" }));
  expect(screen.getByRole("tablist", { name: "RPF tools" })).toHaveAttribute("aria-orientation", "vertical");
  expect(localStorage.getItem("panel-test")).toBe("expanded");
});

it("supports vertical keyboard navigation without activating guarded tools", async () => {
  const user = userEvent.setup(), choose = vi.fn();
  render(<WorkspaceToolPanel title="RPF tools" storageKey="panel-test">
    <button role="tab">First</button><button role="tab" disabled onClick={choose}>Guarded</button>
    <button role="tab" onClick={choose}>Last</button>
  </WorkspaceToolPanel>);
  screen.getByRole("tab", { name: "First" }).focus();
  await user.keyboard("{ArrowDown}");
  expect(screen.getByRole("tab", { name: "Last" })).toHaveFocus();
  expect(choose).not.toHaveBeenCalled();
  await user.keyboard("{Home}{ArrowUp}");
  expect(screen.getByRole("tab", { name: "Last" })).toHaveFocus();
  await user.keyboard("{Enter}");
  expect(choose).toHaveBeenCalledTimes(1);
});

it("keeps the toggle usable when preference storage is unavailable", async () => {
  const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable"); });
  try {
    render(<WorkspaceToolPanel title="RPF tools" storageKey="panel-test"><button role="tab">Archive inspection</button></WorkspaceToolPanel>);
    await userEvent.click(screen.getByRole("button", { name: "Collapse RPF tools" }));
    expect(screen.getByRole("button", { name: "Expand RPF tools" })).toBeEnabled();
  } finally { spy.mockRestore(); }
});
