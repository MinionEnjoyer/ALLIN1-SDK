import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import QwenSetup from "./QwenSetup";
import { createPreviewClient } from "./previewClient";

it("saves explicit SDK settings without prompting or downloading", async () => {
  const client = createPreviewClient("default");
  const configure = vi.spyOn(client, "configureAssistant").mockResolvedValue({
    protocol_version: "1.0.0", request_id: "save", job_id: null,
    operation: "result", risk: "authoring_write", terminal: true, sequence: 0,
    payload: { result: { kind: "assistant_configuration" } },
  });
  const startJob = vi.spyOn(client, "startJob");
  const onSaved = vi.fn();
  render(<QwenSetup client={client} onSaved={onSaved} onClose={vi.fn()} />);
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Endpoint"), "https://provider.example/v1");
  await user.type(screen.getByLabelText("Provider model name"), "qwen");
  await user.click(screen.getByLabelText(/supports JSON-schema/));
  await user.click(screen.getByRole("button", { name: "Save SDK settings" }));
  expect(configure).toHaveBeenCalledWith(expect.objectContaining({
    authoring_confirmed: true,
    settings: expect.objectContaining({ mode: "compatible_api", model_name: "qwen", structured_output: true }),
  }));
  expect(startJob).not.toHaveBeenCalled();
  expect(onSaved).toHaveBeenCalledOnce();
});

it("reports preview refusal without pretending to save settings", async () => {
  const onSaved = vi.fn();
  render(<QwenSetup client={createPreviewClient("default")} onSaved={onSaved} onClose={vi.fn()} />);
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Provider"), "disabled");
  await user.click(screen.getByRole("button", { name: "Save SDK settings" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("cannot be saved in browser preview");
  expect(onSaved).not.toHaveBeenCalled();
});
