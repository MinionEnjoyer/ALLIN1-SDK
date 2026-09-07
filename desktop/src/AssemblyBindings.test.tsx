import {render,screen,fireEvent} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import AssemblyBindings from "./AssemblyBindings";

const candidates=[{source:"package:body.ydr.xml",sha256:"a".repeat(64),drawable:0,name:"body",bones:2},
  {source:"package:clip.ydr.xml",sha256:"b".repeat(64),drawable:0,name:"clip",bones:2}];
it("rejects invalid rotation drafts and retains valid rotations through collapse",async()=>{
  const user=userEvent.setup(),change=vi.fn();
  render(<AssemblyBindings candidates={candidates} bindings={[]} locked={false} onChange={change}/>);
  const title="Declared attachment placement · 0 pairs";
  await user.click(screen.getByText(title));
  for(const [label,row] of [["Assembly parent",candidates[0]],["Assembly child",candidates[1]]] as const)
    await user.selectOptions(screen.getByLabelText(label),JSON.stringify([row.source,row.drawable,row.sha256]));
  await user.click(screen.getByText("Local rotation offset · XYZW quaternion"));
  fireEvent.change(screen.getByLabelText("Local rotation W"),{target:{value:"0"}});
  expect(screen.getByRole("button",{name:"Add declared assembly pair"})).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Local rotation X"),{target:{value:"1"}});
  await user.click(screen.getByText(title));await user.click(screen.getByText(title));
  await user.click(screen.getByRole("button",{name:"Add declared assembly pair"}));
  expect(change).toHaveBeenLastCalledWith([expect.objectContaining({rotation:[1,0,0,0],offset:[0,0,0]})]);
  await user.click(screen.getByRole("button",{name:"Reset local rotation"}));
  await user.click(screen.getByRole("button",{name:"Add declared assembly pair"}));
  expect(change.mock.calls.at(-1)![0][0]).not.toHaveProperty("rotation");
});
it("locks rotation inputs during an existing operation",()=>{
  render(<AssemblyBindings candidates={candidates} bindings={[]} locked onChange={vi.fn()}/>);
  expect(screen.getByLabelText("Local rotation X")).toBeDisabled();
  expect(screen.getByRole("button",{name:"Reset local rotation",hidden:true})).toBeDisabled();
});
