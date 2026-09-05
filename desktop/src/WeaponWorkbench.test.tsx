import { act, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import WeaponWorkbench from "./WeaponWorkbench";
import App from "./App";
import { createPreviewClient } from "./previewClient";
import { weaponPreviewClonePlan, weaponPreviewReview, weaponPreviewSnapshot } from "./weaponPreview";
import type { WeaponCloneSpec } from "./WeaponClone";
import type { Envelope, LaunchRequest } from "./types";

const response = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "test", job_id: null,
  operation: "result", sequence: 0, risk: "authoring_write", terminal: true, payload: { result } });

it("reviews RPM as a weapon-only edit and requires confirmation before saving", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy");
  saved.revision = 1; saved.can_undo = true;
  saved.values!.values["weapon.roundsPerMinute"] = "1200";
  saved.values!.values["weapon.timeBetweenShots"] = "0.05";
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(saved));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  const rpm = await screen.findByLabelText("Rounds per minute (RPM)");
  await user.clear(rpm); await user.type(rpm, "1200");
  expect(screen.getByText("0.05")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByRole("button", { name: "Confirm save" })).toBeDisabled();
  expect(start).toHaveBeenLastCalledWith("review_weapon_authoring",
    expect.objectContaining({ updates: { "weapon.roundsPerMinute": "1200" } }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  expect(await screen.findByText("Editable copy · Revision 1")).toBeInTheDocument();
  expect(screen.getByLabelText("Rounds per minute (RPM)")).toHaveValue("1200");
});

it("routes scope offsets and flags through dirty guards and explicit review, save, and undo", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy");
  saved.revision = 1; saved.can_undo = true;
  saved.values!.values["weapon.firstPersonScopeOffset.z"] = "0.0180";
  saved.values!.values["weapon.weaponFlags"] = "CarriedInHand Gun";
  const restored = weaponPreviewSnapshot("C:\\SDK\\copy"); restored.revision = 2;
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(saved)).mockResolvedValueOnce(response(restored));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByLabelText("Scope position Z");
  await user.clear(screen.getByLabelText("Scope position Z"));
  await user.type(screen.getByLabelText("Scope position Z"), "0.0180");
  await user.click(screen.getByText("Weapon behavior flags"));
  await user.click(screen.getByLabelText("UseFPSAimIK"));
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  await screen.findByRole("region", { name: "Weapon change review" });
  expect(start).toHaveBeenLastCalledWith("review_weapon_authoring", expect.objectContaining({ updates: {
    "weapon.firstPersonScopeOffset.z": "0.0180", "weapon.weaponFlags": "CarriedInHand Gun" } }), expect.any(String), expect.any(Function));
  expect(screen.getByLabelText("Scope position Z")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Confirm save" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(screen.getByLabelText("Scope position Z")).toHaveValue("0.0180");
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  await screen.findByRole("button", { name: "Confirm restore" });
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm restore" }));
  await screen.findByText("Editable copy · Revision 2");
  expect(screen.getByLabelText("Scope position Z")).toHaveValue("-0.014");
});

const cloneSpec: WeaponCloneSpec = { donor_weapon: "WEAPON_DEMO", weapon_name: "WEAPON_NEW", slot: "SLOT_NEW",
  model: "w_pi_new", human_name_hash: "WT_NEW", stat_name: "ST_NEW", ammo_info: "AMMO_NEW", clone_ammo: true, ammo_name: "AMMO_NEW" };
async function fillCloneForm(user: ReturnType<typeof userEvent.setup>, reuse = false) {
  await user.click(screen.getByRole("button", { name: "New from template" }));
  for (const [label, value] of Object.entries({ "New weapon identity": "WEAPON_NEW", "New slot identity": "SLOT_NEW",
    "Target model name": "w_pi_new", "New display-name key": "WT_NEW", "New stat-name key": "ST_NEW" })) {
    await user.type(screen.getByLabelText(label), value);
  }
  if (reuse) await user.selectOptions(screen.getByLabelText("Ammo mode"), "reuse");
  await user.type(screen.getByLabelText(reuse ? "Existing ammo identity" : "New ammo identity"), reuse ? "AMMO_DEMO" : "AMMO_NEW");
}

it("reviews complete clone additions and confirms creation and undo independently", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob");
  const created = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_NEW");
  created.revision = 1; created.can_undo = true;
  created.project.weapons.push({ name: "WEAPON_NEW", model: "w_pi_new", ammo_info: "AMMO_NEW", source: "weapons.meta" });
  const restored = weaponPreviewSnapshot("C:\\SDK\\copy"); restored.revision = 2;
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(created)).mockResolvedValueOnce(response(restored));
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open weapon folder" }));
  await screen.findByDisplayValue("SLOT_PISTOL");
  expect(screen.queryByRole("button", { name: "New from template" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · Revision 0");
  await user.type(screen.getByLabelText("Filter weapons"), "DEMO");
  await fillCloneForm(user);
  expect(dirty).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeDisabled();
  expect(screen.getByLabelText("Browse definitions")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review clone plan" }));
  const review = await screen.findByRole("region", { name: "Weapon change review" });
  expect(start).toHaveBeenLastCalledWith("review_weapon_authoring", expect.objectContaining({ action: "clone", spec: cloneSpec, expected_revision: 0 }), expect.any(String), expect.any(Function));
  expect(within(review).getByText("Ready to create")).toBeInTheDocument();
  expect(within(review).getByRole("heading", { name: "Review new weapon bundle" })).toHaveFocus();
  expect(within(review).getByRole("region", { name: "Planned additions" })).toHaveTextContent("animation mapping");
  expect(screen.getByLabelText("New weapon identity")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Confirm clone" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm clone" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(apply).toHaveBeenLastCalledWith(expect.objectContaining({ action: "clone", spec: cloneSpec, authoring_confirmed: true }));
  expect(screen.getByLabelText("Filter weapons")).toHaveValue("");
  expect(screen.getByRole("button", { name: /^WEAPON_NEW / })).toHaveAttribute("aria-pressed", "true");
  expect(dirty).toHaveBeenLastCalledWith(false);
  start.mockImplementationOnce(async (_operation, _payload, _revision, onEvent) => {
    onEvent(response({ kind: "weapon_authoring_review", action: "undo", review_sha256: "e".repeat(64),
      subject: "WEAPON_NEW", removed_records: weaponPreviewClonePlan(cloneSpec).additions }));
    return { job_id: "undo-clone", accepted: response({}) };
  });
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  expect(await screen.findByRole("region", { name: "Records to remove" })).toHaveTextContent("WEAPON_NEW");
  expect(screen.getByRole("button", { name: "Confirm restore" })).toBeDisabled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm restore" }));
  await screen.findByText("Editable copy · Revision 2");
  expect(screen.queryByRole("button", { name: /^WEAPON_NEW / })).not.toBeInTheDocument();
});

it("shows blocked clone evidence, preserves the draft, and reviews explicit ammo reuse", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob");
  const apply = vi.spyOn(client, "applyWeaponAuthoring");
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · Revision 0");
  await fillCloneForm(user, true);
  start.mockImplementationOnce(async (_operation, payload, _revision, onEvent) => {
    const plan = weaponPreviewClonePlan(payload.spec as WeaponCloneSpec);
    plan.ready = false; plan.donor_complete = false; plan.donor_completeness.shop_record = false;
    plan.collisions = [{ field: "weapon_name", value: "WEAPON_NEW", existing: "WEAPON_OTHER", reason: "joaat", hash: "0x12345678" }];
    plan.findings = [{ severity: "error", code: "donor_shop_missing", message: "Donor shop record is missing." }];
    onEvent(response({ ...weaponPreviewReview(payload), clone_plan: plan }));
    return { job_id: "blocked-clone", accepted: response({}) };
  });
  await user.click(screen.getByRole("button", { name: "Review clone plan" }));
  expect(await screen.findByRole("region", { name: "Identity collisions" })).toHaveTextContent("0x12345678");
  expect(screen.getByRole("region", { name: "Clone findings" })).toHaveTextContent("Donor shop record is missing.");
  expect(screen.getByRole("button", { name: "Confirm clone" })).toBeDisabled();
  expect(screen.getByLabelText("I confirm this change to the editable copy only.")).toBeDisabled();
  expect(start).toHaveBeenLastCalledWith("review_weapon_authoring", expect.objectContaining({ spec: { ...cloneSpec, clone_ammo: false, ammo_info: "AMMO_DEMO", ammo_name: null } }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Cancel review" }));
  expect(screen.getByLabelText("New weapon identity")).toHaveValue("WEAPON_NEW");
  expect(screen.getByLabelText("Ammo mode")).toHaveValue("reuse");
  await user.click(screen.getByRole("button", { name: "Cancel new weapon" }));
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeEnabled();
});

it("keeps a clone draft after stale apply or malformed review and requires a fresh confirmation", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob");
  vi.spyOn(client, "applyWeaponAuthoring").mockRejectedValue(new Error("Clone plan is stale; review again"));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · Revision 0");
  await fillCloneForm(user);
  start.mockImplementationOnce(async (_op, _payload, _revision, onEvent) => {
    onEvent(response({ kind: "weapon_authoring_review", action: "clone", review_sha256: "c".repeat(64) }));
    return { job_id: "malformed-clone", accepted: response({}) };
  });
  await user.click(screen.getByRole("button", { name: "Review clone plan" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected weapon clone plan response");
  expect(screen.queryByRole("button", { name: "Confirm clone" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("New weapon identity")).toHaveValue("WEAPON_NEW");
  await user.click(screen.getByRole("button", { name: "Review clone plan" }));
  await screen.findByRole("button", { name: "Confirm clone" });
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm clone" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Clone plan is stale");
  expect(screen.getByLabelText("New weapon identity")).toHaveValue("WEAPON_NEW");
  await user.click(screen.getByRole("button", { name: "Review clone plan" }));
  expect(await screen.findByRole("button", { name: "Confirm clone" })).toBeDisabled();
});

it("keeps source read-only and confirms workspace creation separately", async () => {
  const client = createPreviewClient("weapons");
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValue(response(weaponPreviewSnapshot("C:\\SDK\\copy")));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open weapon folder" }));
  expect(await screen.findByDisplayValue("SLOT_PISTOL")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review editable copy" }));
  expect(await screen.findByRole("button", { name: "Confirm copy" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm copy" }));
  await waitFor(() => expect(screen.getByDisplayValue("SLOT_PISTOL")).toBeEnabled());
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "create", authoring_confirmed: true, review_sha256: "c".repeat(64) }));
});

it("requires shared-ammo acknowledgement and review before saving a new revision", async () => {
  const client = createPreviewClient("weapons");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy");
  saved.revision = 1; saved.can_undo = true; saved.values!.values["ammo.ammoMax"] = "300";
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValue(response(saved));
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  const ammo = await screen.findByLabelText("Maximum ammo");
  await user.clear(ammo); await user.type(ammo, "300");
  expect(screen.getByRole("button", { name: "Refresh weapons" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review changes" })).toBeDisabled();
  expect(dirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByLabelText("Apply ammo changes to every listed weapon."));
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByRole("button", { name: "Confirm save" })).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "edit", expected_revision: 0,
    updates: { "ammo.ammoMax": "300" }, acknowledge_shared: true, authoring_confirmed: true }));
  expect(screen.getByRole("button", { name: "Review undo" })).toBeEnabled();
  expect(dirty).toHaveBeenLastCalledWith(false);
});

it("rejects late job results after cancellation", async () => {
  const client = createPreviewClient("weapons");
  let deliver: ((message: Envelope) => void) | undefined;
  vi.spyOn(client, "startJob").mockImplementation(async (_operation, _payload, _revision, onEvent) => {
    deliver = onEvent; return { job_id: "pending-weapon", accepted: { ...response({}), terminal: false } };
  });
  const cancel = vi.spyOn(client, "cancelJob");
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open weapon folder" }));
  await user.click(await screen.findByRole("button", { name: "Cancel inspection" }));
  act(() => deliver?.(response(weaponPreviewSnapshot())));
  expect(cancel).toHaveBeenCalledWith("pending-weapon");
  expect(screen.queryByDisplayValue("SLOT_PISTOL")).not.toBeInTheDocument();
});

it("starts one source inspection under StrictMode and handles malformed results", async () => {
  const client = createPreviewClient("weapons");
  const start = vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, onEvent) => {
    onEvent(response({ kind: "vehicle_project_inspection" }));
    return { job_id: "unexpected", accepted: response({}) };
  });
  render(<StrictMode><WeaponWorkbench client={client} onDirtyChange={vi.fn()} initialSource="C:\\SDK\\source" /></StrictMode>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Unexpected weapon inspection response");
  expect(start).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Open weapon folder" })).toBeEnabled();
});

it("blocks content-type navigation while weapon fields are dirty", async () => {
  const client = createPreviewClient("weapons");
  let launch: ((request: LaunchRequest) => void) | undefined;
  vi.spyOn(client, "onLaunchRequest").mockImplementation(async (callback) => { launch = callback; return () => undefined; });
  const handshake = vi.spyOn(client, "handshake");
  render(<App client={client} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Content Workbench/ }));
  await user.click(screen.getByRole("tab", { name: /Weapons/ }));
  await user.click(await screen.findByRole("button", { name: "Open editable copy" }));
  await user.type(await screen.findByLabelText("Inventory slot"), "_EDIT");
  await user.click(screen.getByRole("tab", { name: /Peds/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("unsaved content fields");
  expect(screen.getByRole("tab", { name: /Weapons/ })).toHaveAttribute("aria-selected", "true");
  act(() => launch?.({ workspace: "workbench", category: "peds", source: "C:\\SDK\\other", selection: null, warning: null }));
  expect(screen.getByRole("tab", { name: /Weapons/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("before opening another source");
  expect(handshake).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Reset fields" }));
  await user.click(screen.getByRole("tab", { name: /Peds/ }));
  expect(await screen.findByRole("heading", { name: "Ped Workbench" })).toBeInTheDocument();
});

it("inspects components read-only and requires shared acknowledgement before a reviewed save and undo", async () => {
  const client = createPreviewClient("weapons");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "component");
  saved.revision = 1; saved.can_undo = true;
  saved.component_values!.values["component.locName"] = "WCT_CHANGED";
  const restored = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "component");
  restored.revision = 2;
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValueOnce(response(saved)).mockResolvedValueOnce(response(restored));
  const dirty = vi.fn();
  render(<WeaponWorkbench client={client} onDirtyChange={dirty} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open weapon folder" }));
  await screen.findByDisplayValue("SLOT_PISTOL");
  await user.selectOptions(screen.getByLabelText("Browse definitions"), "components");
  await user.click(screen.getByRole("button", { name: /^COMPONENT_DEMO_CLIP / }));
  expect(await screen.findByLabelText("Component name key")).toBeDisabled();
  expect(screen.getByLabelText("Component type (locked)")).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await screen.findByText("Editable copy · Revision 0");
  await user.selectOptions(screen.getByLabelText("Browse definitions"), "components");
  await user.click(screen.getByRole("button", { name: /^COMPONENT_DEMO_CLIP / }));
  const name = await screen.findByLabelText("Component name key");
  await user.clear(name); await user.type(name, "WCT_CHANGED");
  expect(screen.getByLabelText("Browse definitions")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review changes" })).toBeDisabled();
  expect(dirty).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByLabelText("Apply component changes to every listed weapon."));
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  const review = await screen.findByRole("region", { name: "Weapon change review" });
  expect(within(review).getByRole("heading", { name: "Review component changes" })).toBeInTheDocument();
  expect(review).toHaveTextContent("COMPONENT_DEMO_CLIP");
  expect(review).toHaveTextContent("WEAPON_DEMO_ALT");
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(apply).toHaveBeenLastCalledWith(expect.objectContaining({ action: "edit_component", component: "COMPONENT_DEMO_CLIP",
    expected_revision: 0, updates: { "component.locName": "WCT_CHANGED" }, acknowledge_shared: true }));
  expect(screen.getByLabelText("Component name key")).toHaveValue("WCT_CHANGED");
  await user.click(screen.getByRole("button", { name: "Review undo" }));
  expect(await screen.findByRole("button", { name: "Confirm restore" })).toBeDisabled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm restore" }));
  await screen.findByText("Editable copy · Revision 2");
  expect(screen.getByLabelText("Component name key")).toHaveValue("WCT_CLIP1");
  expect(dirty).toHaveBeenLastCalledWith(false);
  await user.type(screen.getByLabelText("Filter components"), "CLIP");
  await user.click(screen.getByRole("button", { name: "Inspect attachment WEAPON_DEMO / COMPONENT_DEMO_CLIP" }));
  await screen.findByLabelText("Attachment point (locked)");
  expect(screen.getByLabelText("Filter weapons")).toHaveValue("");
});

it("reviews an attachment default for one exact weapon/component pair without unlocking its point", async () => {
  const client = createPreviewClient("weapons");
  const saved = weaponPreviewSnapshot("C:\\SDK\\copy", "WEAPON_DEMO", "attachment", "COMPONENT_DEMO_SUPP");
  saved.revision = 1; saved.can_undo = true;
  saved.attachment_values!.values["attachment.default"] = "true";
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValue(response(saved));
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await user.click(await screen.findByRole("button", { name: "Inspect attachment WEAPON_DEMO / COMPONENT_DEMO_SUPP" }));
  expect(await screen.findByLabelText("Attachment point (locked)")).toBeDisabled();
  expect(screen.getByLabelText("Default attachment")).not.toBeChecked();
  await user.click(screen.getByLabelText("Default attachment"));
  expect(screen.getByRole("button", { name: "Inspect attachment WEAPON_DEMO / COMPONENT_DEMO_CLIP" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByRole("heading", { name: "Review attachment changes" })).toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByLabelText("I confirm this change to the editable copy only."));
  await user.click(screen.getByRole("button", { name: "Confirm save" }));
  await screen.findByText("Editable copy · Revision 1");
  expect(screen.getByLabelText("Default attachment")).toBeChecked();
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ action: "edit_attachment", weapon: "WEAPON_DEMO",
    component: "COMPONENT_DEMO_SUPP", updates: { "attachment.default": "true" }, expected_revision: 0 }));
});

it("shows conflicting defaults and preserves the draft when Python rejects the review", async () => {
  const client = createPreviewClient("weapons");
  const apply = vi.spyOn(client, "applyWeaponAuthoring");
  render(<WeaponWorkbench client={client} onDirtyChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open editable copy" }));
  await user.click(await screen.findByRole("button", { name: "Inspect attachment WEAPON_DEMO / COMPONENT_DEMO_SCOPE" }));
  expect(await screen.findByText(/Other defaults at this bone:/)).toHaveTextContent("COMPONENT_DEMO_CLIP");
  vi.spyOn(client, "startJob").mockImplementation(async (_op, _payload, _revision, onEvent) => {
    onEvent({ ...response({}), operation: "error", payload: { message: "Another component on this attachment point is already the default" } });
    return { job_id: "rejected-review", accepted: response({}) };
  });
  await user.click(screen.getByLabelText("Default attachment"));
  await user.click(screen.getByRole("button", { name: "Review changes" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("already the default");
  expect(screen.getByLabelText("Default attachment")).toBeChecked();
  expect(screen.queryByRole("button", { name: "Confirm save" })).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Reset fields" }));
  expect(screen.getByLabelText("Default attachment")).not.toBeChecked();
});
