import { expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readScopeRig, scopeHeightProposal } from "./scopeCalibration";
import { ScopeModelCalibration } from "./ScopeModelCalibration";

function bone(name: string, index: number, parent: number, z = 0) {
  return `<Item><Name>${name}</Name><Index value="${index}"/><ParentIndex value="${parent}"/>
    <Translation x="0" y="0" z="${z}"/><Rotation x="0" y="0" z="0" w="1"/><Scale x="1" y="1" z="1"/></Item>`;
}
const xml = (height: number, extra = "") => `<Drawable><Name>test</Name><Skeleton><Bones>${bone("Gun_GripR", 0, -1)}${bone("WAPScop", 1, 0, height)}${extra}</Bones></Skeleton></Drawable>`;

it("automatically measures nested bones and compensates downward mounts upward from a fixed baseline", () => {
  const reference = readScopeRig(xml(.072904));
  const custom = readScopeRig(xml(.02010998, bone("WAPScop_2", 2, 1, .00002728) + bone("WAPScop_2", 3, 2)));
  const proposal = scopeHeightProposal(reference, custom, "WAPScop", "WAPScop_2", -.028);
  expect(proposal.proposedZ).toBeCloseTo(.02476674);
  expect(proposal.correction).toBeCloseTo(.05276674);
  expect(scopeHeightProposal(reference, custom, "WAPScop", "WAPScop_2", -.028)).toEqual(proposal);
});

it("rejects missing, ambiguous, scaled, reoriented and cyclic evidence instead of guessing", () => {
  const reference = readScopeRig(xml(.1));
  expect(() => scopeHeightProposal(reference, reference, "WAPScop_2", "WAPScop", 0)).toThrow(/missing/);
  const ambiguous = readScopeRig(xml(.1, bone("WAPScop", 2, 0, .2)));
  expect(() => scopeHeightProposal(reference, ambiguous, "WAPScop", "WAPScop", 0)).toThrow(/ambiguous/);
  expect(() => readScopeRig(xml(.1).replace('<Scale x="1"', '<Scale x="2"'))).toThrow(/scaling/);
  expect(() => readScopeRig(xml(.1).replace('<ParentIndex value="-1"', '<ParentIndex value="1"'))).toThrow(/Cyclic/);
  expect(() => readScopeRig(xml(.1).replace('z="0.1"', 'z="NaN"'))).toThrow(/invalid/);
  expect(() => readScopeRig('<!DOCTYPE Drawable [<!ENTITY x "evil">]>' + xml(.1))).toThrow(/DTDs/);
  expect(() => readScopeRig('<CWeaponInfo/>')).toThrow(/YDR/);
  const turned = readScopeRig(xml(.1).replaceAll('z="0" w="1"', 'z="1" w="0"'));
  expect(() => scopeHeightProposal(reference, turned, "WAPScop", "WAPScop", 0)).toThrow(/Grip frames/);
});

it("applies only attached-scope Z to the draft, never iron sights or camera eye relief", async () => {
  const onChange = vi.fn(), user = userEvent.setup();
  const key = "weapon.firstPersonScopeAttachmentOffset.z";
  const values = { [key]: "-0.02800", "weapon.firstPersonScopeOffset.z": "0.0180" };
  render(<ScopeModelCalibration values={values} editable={Object.keys(values)} disabled={false} onChange={onChange} />);
  await user.click(screen.getByText("Automatic scope-height calculation"));
  expect(screen.getByRole("button", { name: "Calculate scope height" })).toBeDisabled();
  for (const [label, height] of [["Aligned reference weapon", .072904], ["Custom weapon", .02013726]] as const) {
    const file = new File([xml(height)], "weapon.ydr.xml", { type: "application/xml" });
    Object.defineProperty(file, "text", { value: () => Promise.resolve(xml(height)) });
    await user.upload(screen.getByLabelText(`${label} model XML`), file);
    await screen.findByLabelText(`${label} scope mount`);
  }
  await user.type(screen.getByLabelText("Reference attached-scope Z (metres)"), "-.028");
  await user.click(screen.getByLabelText(/Both weapons use the same scope geometry/));
  await user.click(screen.getByRole("button", { name: "Calculate scope height" }));
  expect(screen.getByRole("region", { name: "Automatic scope-height proposal" })).toHaveTextContent("0.02477");
  expect(onChange).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Use calculated height in draft" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith({ [key]: "0.02477" });
  expect(values["weapon.firstPersonScopeOffset.z"]).toBe("0.0180");
});
