import { render, screen } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { calibratedOffset, magnifiedFov, WeaponCamera } from "./WeaponCamera";

const fields = ["x", "y", "z"].map(axis => ({ key: `weapon.firstPersonScopeOffset.${axis}`, tag: "FirstPersonScopeOffset",
  attribute: axis, label: `Scope position ${axis.toUpperCase()}`, group: "Scope position", unit: "metres", minimum: -10, maximum: 10, step: "0.00001" }));
const original = { "weapon.firstPersonScopeOffset.x": "0.00000", "weapon.firstPersonScopeOffset.y": "0.0000",
  "weapon.firstPersonScopeOffset.z": "-0.014", "weapon.firstPersonScopeFov": "30", "weapon.weaponFlags": "Automatic Gun FutureFlag" };

it("calculates anchor deltas and angular FOV without guessing, and rejects invalid inputs", () => {
  expect(calibratedOffset([0, 0, -.014], [0, 0, 42], [0, 0, 10], .001)[2]).toBeCloseTo(.018);
  expect(magnifiedFov(60, 2)).toBeCloseTo(32.2042275);
  expect(magnifiedFov(60, 1)).toBeCloseTo(60);
  expect(() => calibratedOffset([0, 0, 0], [0, 0, 0], [0, NaN, 0])).toThrow();
  expect(() => calibratedOffset([0, 0, 0], [0, 0, 0], [0, 0, 11])).toThrow();
  expect(() => magnifiedFov(30, 0)).toThrow();
  expect(() => magnifiedFov(1, 100)).toThrow();
});

it("stages an explicit calibration proposal and preserves unknown flags", async () => {
  const onChange = vi.fn();
  function Harness() {
    const [values, setValues] = useState<Record<string, string>>(original);
    return <WeaponCamera fields={fields} original={original} values={values} editable={Object.keys(original)} disabled={false}
      onChange={updates => { onChange(updates); setValues(previous => ({ ...previous, ...updates })); }} />;
  }
  render(<Harness />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Custom scope calibration"));
  await user.selectOptions(screen.getByLabelText("Calibration target"), "firstPersonScopeOffset");
  expect(screen.getByRole("button", { name: "Calculate scope proposal" })).toBeDisabled();
  await user.click(screen.getByLabelText(/The reference is aligned/));
  await user.click(screen.getByRole("button", { name: "Calculate scope proposal" }));
  expect(screen.getByRole("alert")).toHaveTextContent("blank values are not treated as zero");
  await user.selectOptions(screen.getByLabelText("Anchor units"), "mm");
  for (const label of ["Reference sight centre", "Custom sight centre"]) {
    for (const axis of ["X", "Y", "Z"]) await user.type(screen.getByLabelText(`${label} ${axis}`), axis === "Z" ? (label.startsWith("Reference") ? "42" : "10") : "0");
  }
  await user.click(screen.getByRole("button", { name: "Calculate scope proposal" }));
  expect(screen.getByRole("region", { name: "Scope calibration proposal" })).toHaveTextContent("0.01800");
  expect(onChange).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Use proposal in draft" }));
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ "weapon.firstPersonScopeOffset.z": "0.01800" }));
  expect(screen.getByLabelText("Scope position Z")).toHaveValue("0.01800");
  await user.click(screen.getByText("Weapon behavior flags"));
  await user.click(screen.getByLabelText("Automatic"));
  expect(screen.getByLabelText("Complete weapon flags")).toHaveValue("Gun FutureFlag");
});

it("keeps read-only inputs and calibration disabled", () => {
  render(<WeaponCamera fields={fields} original={original} values={original} editable={[]} disabled onChange={vi.fn()} />);
  expect(screen.getByLabelText("Scope position Z")).toBeDisabled();
  expect(screen.getByLabelText("Complete weapon flags")).toBeDisabled();
  expect(screen.getByLabelText("Reference sight centre Z")).toBeDisabled();
});
