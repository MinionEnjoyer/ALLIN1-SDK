import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import WeaponWorkbench from "./WeaponWorkbench";
import { WeaponAnimations } from "./WeaponAnimations";
import { createPreviewClient } from "./previewClient";
import { weaponPreviewSnapshot } from "./weaponPreview";
import type { Envelope } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "test", job_id: null,
  operation: "result", sequence: 0, risk: "authoring_write", terminal: true, payload: { result } });

it("keeps shop inspection read-only and identifies the separate GBAY catalog", async () => {
  const user = userEvent.setup();
  render(<WeaponWorkbench client={createPreviewClient("weapons")} onDirtyChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Open weapon folder" }));
  await user.selectOptions(await screen.findByLabelText("Weapon section"), "shop");
  expect(await screen.findByLabelText("Weapon price (GTA shop)")).toBeDisabled();
  expect(screen.getByLabelText("Available in single-player shop")).toBeDisabled();
  expect(screen.getByText(/These are not GBAY catalog settings/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review changes" })).not.toBeInTheDocument();
});

it("binds shop changes to one source, requires confirmation and preserves selection through undo", async () => {
  const user = userEvent.setup();
  const client = createPreviewClient("weapons");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "shop");
  saved.revision = 1; saved.can_undo = true; saved.shop_values!.values["shop.cost"] = "8500";
  saved.shop_values!.values["shop.availableInSP"] = "false";
  const restored = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "shop");
  restored.revision = 2;
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(saved)).mockResolvedValueOnce(response(restored));
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await user.selectOptions(await screen.findByLabelText("Weapon section"), "shop");
  const cost = await screen.findByLabelText("Weapon price (GTA shop)");
  await user.clear(cost); await user.type(cost, "8500");
  await user.click(screen.getByLabelText("Available in single-player shop"));
  expect(screen.getByLabelText("Shop source")).toBeDisabled();
  expect(screen.getByLabelText("Weapon section")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review changes" })).toBeEnabled();
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  const review = await screen.findByRole("region", { name: "Weapon change review" });
  expect(review).toHaveTextContent("weapon_shop.meta");
  expect(review).toHaveTextContent("GBAY prices and listings will not change");
  expect(within(review).getByRole("button", { name: "Confirm save" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "edit_shop", weapon: "WEAPON_DEMO",
    metadata_source: "weapon_shop.meta", updates: { "shop.cost": "8500", "shop.availableInSP": "false" }, expected_revision: 0 }));
  expect(screen.getByLabelText("Weapon section")).toHaveValue("shop");
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  await screen.findByRole("button", { name: "Confirm restore" });
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm restore" }));
  await screen.findByText("Editable copy · Revision 2");
  expect(screen.getByLabelText("Weapon price (GTA shop)")).toHaveValue("7500");
  expect(dirty).toHaveBeenLastCalledWith(false);
});

it("requires explicit selection for ambiguous shop sources without inventing editable fields", async () => {
  const user = userEvent.setup();
  const client = createPreviewClient("weapons");
  const snapshot = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "shop");
  snapshot.shop_values = null; snapshot.shop_sources = ["a/weapon_shop.meta", "b/weapon_shop.meta"];
  snapshot.relationship_editable_fields = [];
  const start = vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, onEvent) => {
    onEvent(response(snapshot)); return { job_id: "sources", accepted: response({}) };
  });
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  const picker = await screen.findByLabelText("Shop source");
  expect(picker).toHaveValue("");
  expect(screen.queryByLabelText("Weapon price (GTA shop)")).not.toBeInTheDocument();
  await user.selectOptions(picker, "b/weapon_shop.meta");
  expect(start).toHaveBeenLastCalledWith("inspect_weapon_workbench", expect.objectContaining({
    editor_kind: "shop", metadata_source: "b/weapon_shop.meta", weapon: "WEAPON_DEMO",
  }), expect.any(String), expect.any(Function));
});

it("reviews every animation set and retains the target selection on save and undo", async () => {
  const user = userEvent.setup();
  const client = createPreviewClient("weapons");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO_ALT", "animation");
  saved.project.animation_records.push(...saved.project.animation_records.map(record => ({ ...record, weapon_name: "WEAPON_DEMO_ALT" })));
  saved.revision = 1; saved.can_undo = true;
  const restored = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO_ALT", "animation");
  restored.revision = 2;
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(saved)).mockResolvedValueOnce(response(restored));
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await user.click(await screen.findByRole("button", { name: /^WEAPON_DEMO_ALT / }));
  await waitFor(() => expect(screen.getByLabelText("Weapon section")).toBeEnabled());
  await user.selectOptions(screen.getByLabelText("Weapon section"), "animation");
  await user.selectOptions(await screen.findByLabelText("Animation template"), "WEAPON_DEMO");
  const coverage = screen.getByRole("region", { name: "Animation set coverage" });
  expect(coverage).toHaveTextContent("DEFAULT"); expect(coverage).toHaveTextContent("FIRST_PERSON");
  expect(screen.getByLabelText("Weapon section")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Open editable copy" })).toBeDisabled();
  expect(dirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button", { name: "Review animation mappings" }));
  const review = await screen.findByRole("region", { name: "Weapon change review" });
  expect(review).toHaveTextContent("DEFAULT"); expect(review).toHaveTextContent("FIRST_PERSON");
  expect(review).toHaveTextContent("Copy from template");
  expect(within(review).getByRole("button", { name: "Confirm save" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "clone_animation", weapon: "WEAPON_DEMO_ALT",
    template_weapon: "WEAPON_DEMO", metadata_source: "weaponanimations.meta", expected_revision: 0 }));
  expect(screen.getByText(/Existing mappings are protected/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review animation mappings" })).not.toBeInTheDocument();
  expect(dirty).toHaveBeenLastCalledWith(false);
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  await screen.findByRole("button", { name: "Confirm restore" });
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm restore" }));
  await screen.findByText("Editable copy · Revision 2");
  expect(screen.getByLabelText("Animation template")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Review animation mappings" })).toBeDisabled();
});

it("retains animation drafts on stale saves and requires a new confirmation", async () => {
  const user = userEvent.setup();
  const client = createPreviewClient("weapons");
  vi.spyOn(client, "applyWeaponAuthoring").mockRejectedValue(new Error("Weapon workspace changed after review"));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await user.click(await screen.findByRole("button", { name: /^WEAPON_DEMO_ALT / }));
  await waitFor(() => expect(screen.getByLabelText("Weapon section")).toBeEnabled());
  await user.selectOptions(screen.getByLabelText("Weapon section"), "animation");
  await user.selectOptions(await screen.findByLabelText("Animation template"), "WEAPON_DEMO");
  await user.click(screen.getByRole("button", { name: "Review animation mappings" }));
  await screen.findByRole("button", { name: "Confirm save" });
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed after review");
  expect(screen.getByLabelText("Animation template")).toHaveValue("WEAPON_DEMO");
  expect(screen.queryByRole("button", { name: "Confirm save" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Review animation mappings" }));
  expect(await screen.findByRole("button", { name: "Confirm save" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Cancel review" }));
  await user.click(screen.getByRole("button", { name: "Reset animation draft" }));
  expect(screen.getByLabelText("Weapon section")).toBeEnabled();
});

it("does not auto-select one of multiple animation sources", async () => {
  const records = weaponPreviewSnapshot().project.animation_records;
  const onChange = vi.fn();
  render(<WeaponAnimations weapon="WEAPON_TARGET" records={[...records, { ...records[0], source: "other.meta" }]}
    draft={null} editable disabled={false} onChange={onChange} onReview={vi.fn()} onReset={vi.fn()} />);
  await userEvent.setup().selectOptions(screen.getByLabelText("Animation template"), "WEAPON_DEMO");
  expect(onChange).toHaveBeenCalledWith({ template: "WEAPON_DEMO", source: "" });
});

it("displays numeric true availability without silently changing its source value", async () => {
  const client = createPreviewClient("weapons");
  const snapshot = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "shop");
  snapshot.shop_values!.values["shop.availableInSP"] = "1";
  vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, onEvent) => {
    onEvent(response(snapshot)); return { job_id: "availability", accepted: response({}) };
  });
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  await userEvent.setup().click(screen.getByRole("button", { name: "Open editable copy" }));
  expect(await screen.findByLabelText("Available in single-player shop")).toBeChecked();
  expect(screen.getByRole("button", { name: "Review changes" })).toBeDisabled();
  expect(dirty).toHaveBeenLastCalledWith(false);
});
