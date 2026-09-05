import type { ComponentProps } from "react";
import SliderField from "./SliderField";

type Range = Pick<ComponentProps<typeof SliderField>, "min" | "max" | "step" | "unit" | "endpoints">;
// Ergonomic adjustment ranges, not new authoring limits. The Python handling
// validator accepts finite custom values, which manual entry must preserve.
const bias: Range = { min: 0, max: 1, step: .01, endpoints: ["Rear · 0", "Front · 1"] };
const multiplier: Range = { min: 0, max: 5, step: .01, unit: "×" };
const ranges: Record<string, Range> = {
  fMass: { min: 100, max: 20000, step: 100, unit: "kg" },
  fInitialDragCoeff: { min: 0, max: 30, step: .1 },
  fDriveBiasFront: bias, fBrakeBiasFront: bias, fTractionBiasFront: bias,
  fSuspensionBiasFront: bias, fAntiRollBarBiasFront: bias,
  fInitialDriveForce: { min: 0, max: 2, step: .01 },
  fDriveInertia: multiplier,
  fInitialDriveMaxFlatVel: { min: 0, max: 400, step: 1, unit: "handling units" },
  fBrakeForce: multiplier, fHandBrakeForce: multiplier,
  fSteeringLock: { min: 0, max: 80, step: .5, unit: "degrees" },
  fTractionCurveMax: multiplier, fTractionCurveMin: multiplier,
  fTractionCurveLateral: { min: 0, max: 90, step: .5, unit: "degrees" },
  fLowSpeedTractionLossMult: multiplier, fTractionLossMult: multiplier,
  fSuspensionForce: multiplier, fSuspensionCompDamp: multiplier,
  fSuspensionReboundDamp: multiplier, fAntiRollBarForce: multiplier,
  fSuspensionUpperLimit: { min: -1, max: 1, step: .01, unit: "m" },
  fSuspensionLowerLimit: { min: -1, max: 1, step: .01, unit: "m" },
  fSuspensionRaise: { min: -.5, max: .5, step: .005, unit: "m" },
  fCollisionDamageMult: multiplier, fWeaponDamageMult: multiplier,
  fDeformationDamageMult: multiplier, fEngineDamageMult: multiplier,
};
export function handlingSlider(field: string): Range | undefined {
  return field.startsWith("handling.") ? ranges[field.slice(9)] : undefined;
}
