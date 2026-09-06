import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import TextureExportPanel from "./TextureExportPanel";
import type { DesktopClient, Envelope, TextureWorkspaceSession } from "./types";

const envelope = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "r", job_id: "j", operation: "result", sequence: 1, risk: "read_only", terminal: true, payload: { result } });
const session = { workspace: "C:/work/textures", state_sha256: "a".repeat(64), texture_count: 2, textures: [{ name: "diffuse" }, { name: "normal" }] } as TextureWorkspaceSession;
const review = { kind: "texture_export_review", ready: true, review_sha256: "b".repeat(64), destination: "C:/out/texture-export", texture_count: 1, source_bytes: 1024, format: "dds", entries_preview: [{ texture: "normal", file: "normal.dds" }], warning: "Original DDS bytes." };
function setup() {
  let emit: (value: Envelope) => void = () => {};
  const client = { selectPath: vi.fn(async () => "C:/out"), cancelJob: vi.fn(async () => envelope({})),
    startJob: vi.fn(async (_op, _payload, _rev, callback) => { emit = callback; return { job_id: "j", accepted: envelope({}) }; }),
    textureAuthoringAction: vi.fn(async () => envelope({ kind: "texture_export_result", texture_count: 1, destination: "C:/out/texture-export", receipt: "C:/out/texture-export/allin1-texture-export.json", receipt_sha256: "c".repeat(64) }))
  } as unknown as DesktopClient;
  const guard = vi.fn();
  const view = render(<TextureExportPanel client={client} session={session} gtaPath="C:/game" locked={false} onGuardChange={guard} />);
  return { client, guard, ...view, emit: async (value: unknown) => act(async () => emit(envelope(value))) };
}

it("reviews exact selected export and writes only after confirmation", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByText("Bulk texture export · 0 selected"));
  await user.click(screen.getByRole("checkbox", { name: "normal" }));
  await user.click(screen.getByRole("button", { name: "Review selected export" }));
  expect(test.client.startJob).toHaveBeenCalledWith("review_texture_export", expect.objectContaining({ mode: "selected", texture_names: ["normal"], format: "dds", destination: "C:/out/texture-export", expected_state_sha256: session.state_sha256 }), expect.any(String), expect.any(Function));
  await test.emit(review);
  expect(test.guard).toHaveBeenLastCalledWith(true);
  expect(test.client.textureAuthoringAction).not.toHaveBeenCalled();
  // Closing the dense-options panel must not hide the action-time confirmation.
  await user.click(screen.getByText("Bulk texture export · 1 selected"));
  expect(screen.getByRole("dialog", { name: "Review texture export" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Export textures" }));
  expect(test.client.textureAuthoringAction).toHaveBeenCalledWith("apply_texture_export", expect.objectContaining({ review_sha256: review.review_sha256, authoring_confirmed: true }));
  expect(test.guard).toHaveBeenLastCalledWith(false);
});

it("exports all independently from the current selection and supports PNG", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByText("Bulk texture export · 0 selected"));
  await user.click(screen.getByRole("checkbox", { name: "normal" }));
  await user.selectOptions(screen.getByLabelText("Texture export format"), "png");
  await user.click(screen.getByRole("button", { name: "Review all 2 textures" }));
  const payload = vi.mocked(test.client.startJob).mock.calls[0][1];
  expect(payload).toMatchObject({ mode: "all", format: "png" });
  expect(payload).not.toHaveProperty("texture_names");
  await user.click(screen.getByRole("button", { name: "Cancel export review" }));
  await test.emit(review);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(test.client.cancelJob).toHaveBeenCalledWith("j");
});

it("unlocks after a native dialog error and rejects malformed reviews", async () => {
  const user = userEvent.setup(), test = setup();
  await user.click(screen.getByText("Bulk texture export · 0 selected"));
  vi.mocked(test.client.selectPath).mockRejectedValueOnce(new Error("dialog failed"));
  await user.click(screen.getByRole("button", { name: "Review all 2 textures" }));
  expect(screen.getByRole("alert")).toHaveTextContent("dialog failed");
  expect(test.guard).toHaveBeenLastCalledWith(false);
  await user.click(screen.getByRole("button", { name: "Review all 2 textures" }));
  await test.emit({ ...review, ready: false });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("not ready");
});
