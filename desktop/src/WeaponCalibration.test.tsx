import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import WeaponCalibration from "./WeaponCalibration";
import { weaponPreviewSnapshot } from "./weaponPreview";
import { createPreviewClient } from "./previewClient";
import type { Envelope } from "./types";

const envelope = (result: unknown): Envelope => ({ protocol_version: "1.0.0", request_id: "test", job_id: null,
  operation: "result", sequence: 0, risk: "read_only", terminal: true, payload: { result } });
const screenshot = { width: 3840, height: 2160, sha256: "a".repeat(64) };
const measurement = { aim: [1900, 1000], sight: [1920, 1000], impacts: [], visual_error_px: [20, 0], impact_centroid_error_px: null };
const session = { id: "session-1", profile: "iron", edition: "Enhanced", accepted: false, captured_at: "2026-09-07T10:00:00Z", screenshot, measurements: measurement, diagnostic: null };
function setup() {
  const client = createPreviewClient("weapons");
  vi.spyOn(client, "selectPath").mockResolvedValue("C:/evidence/frame.png");
  const start = vi.spyOn(client, "startJob").mockImplementation(async (operation, payload, _revision, event) => {
    const result = operation === "preview_asset" ? { artifact: { ...screenshot, width: 1600, height: 900, media_type: "image/png", preview_url: "/frame.png" }, sha256: screenshot.sha256,
      truncated: false, metadata: { dimensions: "3840 × 2160" } }
      : operation === "review_weapon_authoring" ? { review_sha256: "b".repeat(64), session }
      : payload.calibration_action === "compare" ? { kind: "weapon_calibration_comparison", baseline: { ...session, id: "accepted", accepted: true }, trial: { ...session, id: "trial" }, comparison: { visual_error_change_px: [0, 0] } }
      : payload.calibration_action === "propose" ? { kind: "weapon_calibration_proposal", updates: { "weapon.firstPersonLTOffset.x": "0.002" } }
      : { kind: "weapon_calibration_sessions", sessions: [{ ...session, id: "accepted", accepted: true }, { ...session, id: "trial" }, { ...session, id: "scope-only", profile: "scope" }] };
    event(envelope(result));
    return { job_id: "test", accepted: envelope({}) };
  });
  const apply = vi.spyOn(client, "applyWeaponAuthoring").mockResolvedValue(envelope({ kind: "weapon_calibration_saved" }));
  const review = vi.fn(), pending = vi.fn();
  const snapshot = weaponPreviewSnapshot("C:/workspace");
  snapshot.camera_fields!.push({ key: "weapon.firstPersonLTOffset.x", tag: "FirstPersonLTOffset", attribute: "x", label: "Aimed position X", group: "Aimed position", unit: "metres", minimum: -10, maximum: 10, step: "0.00001" });
  render(<WeaponCalibration client={client} snapshot={snapshot} disabled={false} onPending={pending} onReview={review} />);
  return { client, start, apply, review, pending, user: userEvent.setup() };
}

it("marks source-resolution pixels and requires explicit review before recording", async () => {
  const { user, start, apply } = setup();
  await user.click(screen.getByRole("button", { name: "Choose screenshot" }));
  expect(screen.getByLabelText("Marked calibration screenshot")).toHaveAttribute("viewBox", "0 0 3840 2160");
  await user.type(screen.getByLabelText("Pixel X"), "1900"); await user.type(screen.getByLabelText("Pixel Y"), "1000");
  await user.click(screen.getByRole("button", { name: "Add coordinate mark" }));
  await user.selectOptions(screen.getByLabelText("Mark"), "sight");
  await user.clear(screen.getByLabelText("Pixel X")); await user.type(screen.getByLabelText("Pixel X"), "1920");
  await user.click(screen.getByRole("button", { name: "Add coordinate mark" }));
  await user.type(screen.getByLabelText("Reported capture time (ISO with timezone)"), "2026-09-07T10:00:00Z");
  await user.type(screen.getByLabelText("Recorded FOV (degrees)"), "30");
  await user.type(screen.getByLabelText("Target distance (metres)"), "10");
  await user.click(screen.getByLabelText(/I recorded the camera conditions/));
  await user.click(screen.getByRole("button", { name: "Review session recording" }));
  expect(start).toHaveBeenLastCalledWith("review_weapon_authoring", expect.objectContaining({ action: "calibration_session",
    session: expect.objectContaining({ profile: "iron", screenshot_sha256: screenshot.sha256, aim: [1900, 1000], sight: [1920, 1000] }) }), expect.any(String), expect.any(Function));
  expect(apply).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Confirm session recording" })).toBeDisabled();
  const region = screen.getByRole("region", { name: "Calibration session review" });
  expect(within(region).getByText(/unresolved — no installed build evidence/)).toBeInTheDocument();
  await user.click(screen.getByLabelText(/I confirm these observations/));
  await user.click(screen.getByRole("button", { name: "Confirm session recording" }));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({ authoring_confirmed: true, review_sha256: "b".repeat(64) }));
  expect(await screen.findByRole("status")).toHaveTextContent("Session and screenshot recorded");
});

it("filters independent profiles, never assumes acceptance and compares saved sessions", async () => {
  const { user, apply } = setup();
  expect(screen.getByLabelText(/Save as an accepted reference/)).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Load recorded sessions" }));
  const reference = screen.getByLabelText("Reference / controlled baseline");
  expect(within(reference).queryByRole("option", { name: /scope-only/ })).not.toBeInTheDocument();
  await user.selectOptions(reference, "accepted"); await user.selectOptions(screen.getByLabelText("Current trial"), "trial");
  await user.click(screen.getByRole("button", { name: "Compare sessions" }));
  expect(await screen.findAllByLabelText("Marked calibration screenshot")).toHaveLength(2);
  expect(apply).not.toHaveBeenCalled();
  await user.selectOptions(screen.getByLabelText("Sight profile"), "scope");
  expect(within(reference).getByRole("option", { name: /scope-only/ })).toBeInTheDocument();
  expect(within(reference).queryByRole("option", { name: /accepted$/ })).not.toBeInTheDocument();
});

it("passes empirical evidence to the existing edit review, never applies a proposal", async () => {
  const { user, review, apply, start } = setup();
  await user.click(screen.getByRole("button", { name: "Load recorded sessions" }));
  await user.selectOptions(screen.getByLabelText("Reference / controlled baseline"), "accepted");
  await user.selectOptions(screen.getByLabelText("Current trial"), "trial");
  const fields = screen.getByLabelText("Existing profile field");
  const options = within(fields).getAllByRole("option");
  // Fixture includes LT camera fields; scopes must not leak into the iron list.
  expect(options.every(o => !(o as HTMLOptionElement).value.includes("Scope"))).toBe(true);
  expect(options.length).toBeGreaterThan(1);
    await user.selectOptions(fields, (options[1] as HTMLOptionElement).value);
    await user.click(screen.getByLabelText(/These were controlled tests/));
    await user.click(screen.getByRole("button", { name: "Propose & review visual edit" }));
    expect(start).toHaveBeenLastCalledWith("inspect_weapon_workbench", expect.objectContaining({ calibration_action: "propose", controlled_trial_confirmed: true }), expect.any(String), expect.any(Function));
    expect(review).toHaveBeenCalledWith(expect.objectContaining({ action: "edit", calibration_evidence: expect.objectContaining({ baseline: "accepted", trial: "trial" }) }));
  expect(apply).not.toHaveBeenCalled();
});
