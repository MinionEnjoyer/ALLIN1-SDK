import { describe, it, expect, vi } from "vitest";
import {render,screen,waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VehicleHitchEditor, { newHitch } from "./VehicleHitchEditor";
import {createPreviewClient} from "./previewClient";
import type {Envelope,VehicleAuthoringSession} from "./types";

describe("hitch drafts", () => {
  it("never invents a compatible trailer or a native front override", () => {
    expect(newHitch("rear")).toMatchObject({ mode: "native", bone: "attach_female", position: [0,0,0], compatible_models: [] });
    expect(newHitch("front")).toMatchObject({ mode: "physical", bone: "chassis", compatible_models: [] });
  });
  it("does not share mutable point arrays between slots", () => {
    const a = newHitch("front"), b = newHitch("front");
    a.position[0] = 5; a.compatible_models.push("trailers");
    expect(b.position).toEqual([0,0,0]); expect(b.compatible_models).toEqual([]);
  });
});

it("inspects a hitch draft and requires reviewed confirmation before saving",async()=>{
  const client=createPreviewClient("quick_import"),guard=vi.fn(),saved=vi.fn();
  const session={workspace:"C:/fixture/vehicle",selected_model:"bison",revision:2} as VehicleAuthoringSession;
  const base={module:"vehicle_hitches",schema_version:1,game_write_performed:false,workspace:session.workspace,model:session.selected_model,revision:2,state_sha256:"a".repeat(64)};
  const envelope=(result:unknown):Envelope=>({protocol_version:"1.0.0",request_id:"hitch",job_id:"hitch",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result}});
  const start=vi.spyOn(client,"startJob").mockImplementation(async(operation,payload,_revision,emit)=>{
    const result=operation==="inspect_authoring_workspace"?{...base,kind:"workspace_session",read_only:true,document:{schema_version:1,vehicle_model:"bison",points:[]}}:
      {...base,kind:"workspace_review",review_only:true,action:"configure",request_sha256:"b".repeat(64),review_sha256:"c".repeat(64),document:payload.document};
    const response=envelope(result);emit(response);return {job_id:"hitch",accepted:response};
  });
  const next={...session,kind:"vehicle_authoring_session",revision:3,game_write_performed:false};
  const apply=vi.spyOn(client,"applyWorkspaceAction").mockResolvedValue(envelope({...base,kind:"workspace_applied",action:"configure",review_sha256:"c".repeat(64),vehicle_session:next}));
  const user=userEvent.setup();
  render(<VehicleHitchEditor client={client} session={session} disabled={false} onGuardChange={guard} onSaved={saved}/>);
  await user.click(await screen.findByRole("button",{name:"Add rear hitch"}));
  await user.type(screen.getByRole("textbox",{name:"rear compatible trailers"}),"trailers");
  expect(guard).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole("button",{name:"Review hitches"}));
  expect(start).toHaveBeenLastCalledWith("review_workspace_action",expect.objectContaining({action:"configure",expected_revision:2,expected_state_sha256:"a".repeat(64),document:expect.objectContaining({points:[expect.objectContaining({id:"rear",compatible_models:["trailers"]})]})}),expect.any(String),expect.any(Function));
  expect(screen.getByRole("button",{name:"Apply reviewed change"})).toBeDisabled();
  expect(apply).not.toHaveBeenCalled();
  await user.click(screen.getByRole("checkbox",{name:"I reviewed these authoring changes"}));
  await user.click(screen.getByRole("button",{name:"Apply reviewed change"}));
  await waitFor(()=>expect(saved).toHaveBeenCalledWith(next));
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({module:"vehicle_hitches",review_sha256:"c".repeat(64),authoring_confirmed:true}));
});
