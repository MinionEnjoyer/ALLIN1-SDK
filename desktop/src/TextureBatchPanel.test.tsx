import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import TextureDictionaryWorkspace from "./TextureDictionaryWorkspace";
import { createPreviewClient } from "./previewClient";
import type { Envelope } from "./types";

async function setup() {
  const user = userEvent.setup(), client = createPreviewClient("models"), guard = vi.fn();
  const apply = vi.spyOn(client, "textureAuthoringAction"), jobs = vi.spyOn(client, "startJob");
  render(<TextureDictionaryWorkspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open workspace" }));
  await screen.findByRole("listbox", { name: "Textures" });
  await user.click(screen.getByText(/Bulk texture edits/));
  return { user, client, guard, apply, jobs };
}

it("guards a selected batch, preserves it on review cancellation, commits once and undoes together", async () => {
  const { user, guard, apply, jobs } = await setup();
  await user.click(screen.getByRole("button", { name: "Select all for batch" }));
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Open workspace" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Replace image" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Review all .* textures/ })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  await user.click(within(await screen.findByRole("dialog", { name: "Review texture batch" })).getByRole("button", { name: "Back to batch" }));
  expect(within(screen.getByRole("group", { name: "Textures to edit in batch" })).getAllByRole("checkbox").every(item => (item as HTMLInputElement).checked)).toBe(true);
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  await user.click(within(await screen.findByRole("dialog", { name: "Review texture batch" })).getByRole("button", { name: "Commit texture batch" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(apply).toHaveBeenCalledWith("apply_texture_edit", expect.objectContaining({ action: "batch", batch_action: "convert", mip_levels: "full", authoring_confirmed: true }));
  expect(await screen.findByText(/Texture batch committed at revision 1/)).toBeInTheDocument();
  expect(jobs).toHaveBeenCalledWith("review_texture_edit", expect.objectContaining({ texture_names: expect.any(Array) }), expect.any(String), expect.any(Function));
  await user.click(screen.getByRole("button", { name: "Undo edit" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Undo edit" }));
  expect(await screen.findByText("Previous texture state restored at revision 2.")).toBeInTheDocument();
});

it("keeps a stale failed batch as a draft but requires a new review", async () => {
  const { user, apply } = await setup();
  apply.mockRejectedValueOnce(new Error("Source changed after review"));
  await user.click(screen.getByRole("button", { name: "Select all for batch" }));
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Commit texture batch" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Source changed");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Discard batch draft" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Discard batch draft" }));
  expect(screen.getByRole("button", { name: "Open workspace" })).toBeEnabled();
});

it("ignores a cancelled late review and cancels its late job acknowledgement", async () => {
  const { user, client, jobs } = await setup();
  let complete: (value: { job_id: string; accepted: Envelope }) => void = () => {};
  const cancel = vi.spyOn(client, "cancelJob");
  jobs.mockImplementationOnce(async () => new Promise(resolve => { complete = resolve; }));
  await user.click(screen.getByRole("button", { name: "Select all for batch" }));
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  await user.click(screen.getByRole("button", { name: "Cancel batch review" }));
  await act(async () => complete({ job_id: "late-batch", accepted: {} as Envelope }));
  expect(cancel).toHaveBeenCalledWith("late-batch");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("captures an explicit import folder and policy without writing during review", async () => {
  const { user, client, jobs, apply } = await setup();
  vi.spyOn(client, "selectPath").mockResolvedValueOnce("C:\\images");
  await user.selectOptions(screen.getByLabelText("Texture batch action"), "import");
  await user.selectOptions(screen.getByLabelText("Texture import policy"), "upsert");
  await user.click(screen.getByRole("button", { name: "Choose image folder" }));
  expect(screen.getByRole("button", { name: "Open workspace" })).toBeDisabled();
  // The browser fixture refuses real folder inspection; still verify the exact request.
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  expect(jobs).toHaveBeenCalledWith("review_texture_edit", expect.objectContaining({ action: "batch", batch_action: "import", source_folder: "C:\\images", import_policy: "upsert" }), expect.any(String), expect.any(Function));
  expect(await screen.findByRole("alert")).toHaveTextContent("review evidence is incomplete");
  expect(apply).not.toHaveBeenCalled();
});

it("blocks double submission while the batch writes and rejects a mismatched receipt", async () => {
  const { user, apply } = await setup();
  let resolveWrite: (value: Envelope) => void = () => {};
  apply.mockImplementationOnce(async () => new Promise(resolve => { resolveWrite = resolve; }));
  await user.click(screen.getByRole("button", { name: "Select all for batch" }));
  await user.click(screen.getByRole("button", { name: "Review texture batch" }));
  await user.dblClick(within(await screen.findByRole("dialog")).getByRole("button", { name: "Commit texture batch" }));
  expect(apply).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Back to batch" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Discard batch draft" })).toBeDisabled();
  await act(async () => resolveWrite({ protocol_version: "1.0.0", request_id: "test", job_id: null, sequence: 1, risk: "authoring_write", terminal: true, operation: "result", payload: { result: { kind: "invalid" } } }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Invalid batch completion receipt");
  expect(screen.queryByText(/Texture batch committed/)).not.toBeInTheDocument();
});
