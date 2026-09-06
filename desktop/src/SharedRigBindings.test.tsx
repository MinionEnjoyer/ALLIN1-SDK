import {render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import SharedRigBindings from "./SharedRigBindings";

it("requires both explicit owners and preserves hashed selections when collapsed",async()=>{
  const user=userEvent.setup(),change=vi.fn();
  const candidates=[{source:"package:torso.ydd",sha256:"a".repeat(64),drawable:2,name:"torso",bones:0},
    {source:"comparison:rig.yft",sha256:"b".repeat(64),drawable:0,name:"rig",bones:128}];
  render(<SharedRigBindings candidates={candidates} bindings={[]} locked={false} onChange={change}/>);
  await user.click(screen.getByText("Explicit shared-rig context · 0 selected"));
  expect(screen.getByRole("button",{name:"Use selected shared rig"})).toBeDisabled();
  for(const [label,row] of [["Model drawable",candidates[0]],["Shared skeleton drawable",candidates[1]]] as const)
    await user.selectOptions(screen.getByLabelText(label),JSON.stringify([row.source,row.drawable,row.sha256]));
  await user.click(screen.getByText("Explicit shared-rig context · 0 selected"));
  await user.click(screen.getByText("Explicit shared-rig context · 0 selected"));
  await user.click(screen.getByRole("button",{name:"Use selected shared rig"}));
  expect(change).toHaveBeenCalledWith([{model:candidates[0].source,drawable:2,rig:candidates[1].source,rig_drawable:0,model_sha256:candidates[0].sha256,rig_sha256:candidates[1].sha256}]);
});
