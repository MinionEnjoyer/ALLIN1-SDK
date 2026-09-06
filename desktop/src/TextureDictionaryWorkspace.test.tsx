import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import TextureDictionaryWorkspace from "./TextureDictionaryWorkspace";
import { createPreviewClient } from "./previewClient";

it("guards conversion settings, confirms exact format/mip values, and can undo", async () => {
  const user = userEvent.setup(), client = createPreviewClient("models"), guard = vi.fn();
  const jobs = vi.spyOn(client, "startJob"), apply = vi.spyOn(client, "textureAuthoringAction");
  render(<TextureDictionaryWorkspace client={client} onGuardChange={guard} />);
  await user.click(screen.getByRole("button", { name: "Open workspace" }));
  await screen.findByRole("listbox", { name: "Textures" });
  await user.click(screen.getByText("Format & mip levels"));
  await user.selectOptions(screen.getByLabelText("Converted texture format"), "DXT3");
  expect(guard).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Replace image" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open YTD" })).toBeDisabled();
  expect(screen.getByLabelText("New texture name")).toBeDisabled();
  await user.selectOptions(screen.getByLabelText("Converted texture mip levels"), "3");
  await user.click(screen.getByRole("button", { name: "Review format & mips" }));
  const dialog = await screen.findByRole("dialog", { name: "Convert reviewed texture" });
  expect(jobs).toHaveBeenCalledWith("review_texture_edit", expect.objectContaining({ action: "convert", output_format: "DXT3", mip_levels: 3, texture_name: "comet6_sign_1" }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Commit texture edit" }));
  expect(apply).toHaveBeenCalledWith("apply_texture_edit", expect.objectContaining({ action: "convert", output_format: "DXT3", mip_levels: 3, authoring_confirmed: true }));
  expect(await screen.findByText("Texture convert committed at revision 1.")).toBeInTheDocument();
  expect(screen.getByLabelText("Converted texture format")).toHaveValue("");
  await user.click(screen.getByRole("button", { name: "Undo edit" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Undo edit" }));
  expect(await screen.findByText("Previous texture state restored at revision 2.")).toBeInTheDocument();
});

it("preserves a rename through cancelled review and sends the reviewed target", async () => {
  const user = userEvent.setup(), client = createPreviewClient("models");
  const apply = vi.spyOn(client, "textureAuthoringAction");
  render(<TextureDictionaryWorkspace client={client} />);
  await user.click(screen.getByRole("button", { name: "Open workspace" }));
  await screen.findByRole("listbox", { name: "Textures" });
  await user.type(screen.getByLabelText("Rename selected texture"), "renamed_sign");
  await user.click(screen.getByRole("button", { name: "Review texture rename" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.getByLabelText("Rename selected texture")).toHaveValue("renamed_sign");
  await user.click(screen.getByRole("button", { name: "Review texture rename" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Commit texture edit" }));
  expect(apply).toHaveBeenCalledWith("apply_texture_edit", expect.objectContaining({ action: "rename", texture_name: "comet6_sign_1", new_name: "renamed_sign" }));
  expect(await screen.findByRole("option", { name: /renamed_sign/ })).toBeInTheDocument();
});
