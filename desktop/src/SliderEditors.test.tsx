import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { VehicleTransmissionEditor } from "./VehicleTransmissionEditor";
import { VehicleAxleEditor } from "./VehicleAxleEditor";
import { WeaponFireRate, rpmKey, intervalKey } from "./WeaponFireRate";
import { WeaponCamera } from "./WeaponCamera";
import RenderWorkbench from "./RenderWorkbench";
import { createPreviewClient } from "./previewClient";
import type { VehicleTransmissionConfiguration, VehicleAxleConfiguration } from "./types";

it("transmission drafts block review while blank and preserve exact ratios", () => {
  const review = vi.fn();
  function Harness() {
    const [configuration, setConfiguration] = useState<VehicleTransmissionConfiguration>({ vehicle_model: "bus", schema_version: 1, transmission_type: "automatic", gear_ratios: [3, 2, 1], reverse_gear_ratio: 3, final_drive_ratio: 3.5 });
    return <VehicleTransmissionEditor configuration={configuration} stockGearCount={3} dirty busy={false} onCreate={vi.fn()} onConfiguration={setConfiguration} onReset={vi.fn()} onReview={review} />;
  }
  render(<Harness />);
  const input = screen.getByRole("textbox", { name: "Final drive" });
  fireEvent.change(input, { target: { value: "" } });
  expect(input).toHaveValue(""); expect(screen.getByRole("button", { name: "Review transmission" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "3.712345" } });
  expect(input).toHaveValue("3.712345"); expect(screen.getByRole("button", { name: "Review transmission" })).toBeEnabled();
  fireEvent.change(screen.getByRole("slider", { name: "Gear 1 slider" }), { target: { value: "4.1" } });
  expect(screen.getByRole("textbox", { name: "Gear 1" })).toHaveValue("4.1");
  expect(review).not.toHaveBeenCalled();
});
it("weapon sliders preserve source precision and only stage RPM/FOV changes", () => {
  const change = vi.fn();
  const original = { [rpmKey]: "508.474576", [intervalKey]: "0.118000", "weapon.cameraFov": "60.125" };
  render(<><WeaponFireRate values={original} original={original} editable={[rpmKey]} disabled={false} onChange={change} />
    <WeaponCamera fields={[{ key: "weapon.cameraFov", tag: "CameraFov", attribute: "value", label: "Aim FOV", group: "Field of view", unit: "degrees", minimum: 1, maximum: 179, step: ".01" }]}
      values={original} original={original} editable={["weapon.cameraFov"]} disabled={false} onChange={change} /></>);
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("slider", { name: "Rounds per minute (RPM) slider" }), { target: { value: "900" } });
  expect(change).toHaveBeenLastCalledWith({ [rpmKey]: "900" });
  fireEvent.change(screen.getByRole("slider", { name: "Aim FOV slider" }), { target: { value: "75" } });
  expect(change).toHaveBeenLastCalledWith({ "weapon.cameraFov": "75" });
});
it("signed axle configurations keep sliders locked without skeleton evidence", () => {
  const change = vi.fn(), calculate = vi.fn();
  const configuration = { vehicle_model: "bus", schema_version: 4, expected_wheel_count: 6, export_mode: "selective_runtime", preset: "Custom",
    intentional_layout_override: { enabled: true }, axles: [{ physical_order: 1, logical_role: "front", left_bone: "wheel_lm1", right_bone: "wheel_rm1", left_runtime_index: 4, right_runtime_index: 5,
      steered: true, steering_gain: -1, powered: false, service_brake: true, handbrake: false, visual_family: "shared_middle_rear", suspension: { support_weight: 1 } }] } as unknown as VehicleAxleConfiguration;
  render(<VehicleAxleEditor configuration={configuration} skeleton={null} selectedOrder={1} dirty busy={false} onSelect={vi.fn()} onConfiguration={change}
    onChooseSkeleton={vi.fn()} onMoveOrder={vi.fn()} onRestoreCanonical={vi.fn()} onCalculateSteering={calculate} onReset={vi.fn()} onReview={vi.fn()} />);
  expect(screen.getByRole("slider", { name: "Support weight slider" })).toBeDisabled();
  expect(screen.getByRole("slider", { name: "Reference lock slider" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Calculate gains" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Review axle changes" })).toBeDisabled();
  expect(change).not.toHaveBeenCalled(); expect(calculate).not.toHaveBeenCalled();
});
it("render sliders never launch Blender on change, block invalid drafts, and retain automatic samples", async () => {
  const client = createPreviewClient("rpf"); client.startJob = vi.fn();
  client.selectPath = vi.fn(async () => "C:/test/model.ydr");
  render(<RenderWorkbench client={client} onDirtyChange={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: "Choose render model" }));
  fireEvent.change(screen.getByRole("slider", { name: "Light strength slider" }), { target: { value: "2.5" } });
  expect(screen.getByRole("textbox", { name: "Light strength" })).toHaveValue("2.5");
  fireEvent.change(screen.getByRole("textbox", { name: "Camera yaw" }), { target: { value: "720.123456" } });
  expect(screen.getByRole("textbox", { name: "Camera yaw" })).toHaveValue("720.123456");
  expect(screen.getByLabelText("Samples (blank: quality default)")).toHaveValue(null);
  expect(client.startJob).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Render frame" })).toBeEnabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Light strength" }), { target: { value: "" } });
  expect(screen.getByRole("textbox", { name: "Light strength" })).toHaveValue("");
  expect(screen.getByRole("button", { name: "Render frame" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Reset Light strength" }));
  expect(screen.getByRole("button", { name: "Render frame" })).toBeEnabled();
  expect(client.startJob).not.toHaveBeenCalled();
});
