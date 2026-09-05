import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import App from "./App";
import ModelMaterialsWorkspace from "./ModelMaterialsWorkspace";
import TextureDictionaryWorkspace from "./TextureDictionaryWorkspace";
import { createPreviewClient } from "./previewClient";
import type { Envelope, JobStart } from "./types";

it("model drafts block tool switches and global navigation until explicitly reset", async () => {
  const user = userEvent.setup(); render(<App client={createPreviewClient("models")} />);
  await user.click(await screen.findByRole("button", { name: "Open model" }));
  await user.click(screen.getByRole("button", { name: "Inspect model" }));
  await user.click(await screen.findByRole("button", { name: "Create editable copy" }));
  await user.click(await screen.findByRole("button", { name: "Create copy" }));
  const shader = await screen.findByRole("textbox", { name: "Shader name" });
  fireEvent.change(shader, { target: { value: "unsaved_shader" } });
  expect(screen.getByRole("button", { name: /Texture dictionaries/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open model" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Help Center/ }));
  expect(shader).toHaveValue("unsaved_shader");
  expect(screen.queryByRole("heading", { name: "Help Center" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Reset material drafts" }));
  await user.click(screen.getByRole("button", { name: /Texture dictionaries/ }));
  expect(await screen.findByRole("heading", { name: "Texture Dictionary" })).toBeInTheDocument();
});

it("texture add drafts and open confirmations preserve their current tool", async () => {
  const user = userEvent.setup(); render(<App client={createPreviewClient("models")} />);
  await user.click(await screen.findByRole("button", { name: /Texture dictionaries/ }));
  await user.click(screen.getByRole("button", { name: "Open YTD" }));
  await user.click(screen.getByRole("button", { name: "Create editable copy" }));
  const dialog = await screen.findByRole("dialog", { name: "Create editable texture copy" });
  expect(screen.getByRole("button", { name: /Model surfaces/ })).toBeDisabled();
  await user.click(within(dialog).getByRole("button", { name: "Create copy" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "New texture name" }), { target: { value: "unsaved_texture" } });
  expect(screen.getByRole("button", { name: /Model surfaces/ })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: /Help Center/ }));
  expect(screen.getByRole("textbox", { name: "New texture name" })).toHaveValue("unsaved_texture");
  await user.click(screen.getByRole("button", { name: "Reset texture draft" }));
  await user.click(screen.getByRole("button", { name: /Help Center/ }));
  expect(await screen.findByRole("heading", { name: "Help Center" })).toBeInTheDocument();
});

it.each(["materials", "textures"])("%s ignores a late cancelled read and releases its navigation guard", async area => {
  const user = userEvent.setup(), client = createPreviewClient("models"), guard = vi.fn();
  const original = client.startJob;
  let deliver: ((message: Envelope) => void) | undefined, terminal: Envelope | undefined;
  client.startJob = vi.fn(async (operation, payload, revision, callback) => {
    deliver = callback;
    await original(operation, payload, revision, message => { if (message.terminal) terminal = message; });
    return { job_id: "pending-inspection" } as JobStart;
  });
  const originalCancel = client.cancelJob;
  client.cancelJob = vi.fn(originalCancel);
  if (area === "materials") render(<ModelMaterialsWorkspace client={client} onGuardChange={guard} />);
  else render(<TextureDictionaryWorkspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open workspace" }));
  await waitFor(() => expect(terminal).toBeDefined());
  await user.click(await screen.findByRole("button", { name: "Cancel" }));
  await act(async () => deliver!(terminal!));
  expect(screen.queryByRole("button", { name: area === "materials" ? "Build verified asset" : "Build YTD" })).not.toBeInTheDocument();
  expect(client.cancelJob).toHaveBeenCalledWith("pending-inspection");
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
});

it("model inspection handles completion before the job-start promise", async () => {
  const user = userEvent.setup(), client = createPreviewClient("models"), guard = vi.fn();
  const original = client.startJob;
  client.startJob = vi.fn(async (operation, payload, revision, callback) => {
    await new Promise<void>((resolve, reject) => {
      void original(operation, payload, revision, message => { callback(message); if (message.terminal) resolve(); }).catch(reject);
    });
    return { job_id: "already-finished" } as JobStart;
  });
  render(<ModelMaterialsWorkspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open workspace" }));
  const build = await screen.findByRole("button", { name: "Build verified asset" });
  await waitFor(() => expect(build).toBeEnabled());
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  await waitFor(() => expect(guard).toHaveBeenLastCalledWith(false));
});
