import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect,it,vi } from "vitest";
import WeaponSightBench, { SightEditor } from "./WeaponSightBench";
import { weaponPreviewSnapshot } from "./weaponPreview";
import { createPreviewClient } from "./previewClient";
import fixture from "./nativeAnimationModelFixture.json";
import type { AnimationModel } from "./animationPose";
vi.mock("./weaponSightRenderer",()=>({createSightRenderer:()=>({upload:vi.fn(),draw:vi.fn(),dispose:vi.fn()})}));
const model={kind:"weapon_sight_model",weapon:"WEAPON_DEMO",source:"C:\\SDK\\weapons\\demo",entry:"stream/w_pi_demo.ydr",native_sha256:"a".repeat(64),edition:"enhanced",revision:0,packet:fixture as AnimationModel};
it("keeps reference controls separate and routes only camera trial edits through review",()=>{
  const snapshot=weaponPreviewSnapshot("copy"),change=vi.fn(),review=vi.fn();
  const props={model,animation:null,snapshot,draft:snapshot.values!.values,locked:false,onChange:change,onReview:review};
  const {rerender}=render(<SightEditor {...props}/>);
  fireEvent.change(screen.getByLabelText("Reference eye Z"),{target:{value:"0.3"}});
  expect(change).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Sight trial Scope position Z"),{target:{value:"0.02"}});
  expect(change).toHaveBeenCalledWith({"weapon.firstPersonScopeOffset.z":"0.02"});
  expect(review).not.toHaveBeenCalled();
  rerender(<SightEditor {...props} draft={{...props.draft,"weapon.firstPersonScopeOffset.z":"0.02"}}/>);
  fireEvent.click(screen.getByRole("button",{name:"Review sight trial"}));expect(review).toHaveBeenCalledOnce();
});
it("blocks read-only metadata fields but permits reference camera setup",()=>{
  const snapshot=weaponPreviewSnapshot(null);
  render(<SightEditor model={model} animation={null} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={vi.fn()} onReview={vi.fn()}/>);
  expect(screen.getByLabelText("Sight trial Scope position Z")).toBeDisabled();
  expect(screen.getByLabelText("Reference eye Z")).not.toBeDisabled();
});
it("does not silently load, and sends exact member/edition/revision through read-only operation",async()=>{
  const snapshot=weaponPreviewSnapshot("copy"),client=createPreviewClient("weapons"),start=vi.spyOn(client,"startJob");
  start.mockImplementation(async(_op,_payload,_rev,event)=>{event({protocol_version:"1.0.0",request_id:"s",job_id:"s",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result:model}});return {job_id:"s",accepted:true} as never;});
  render(<WeaponSightBench client={client} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={vi.fn()} onReview={vi.fn()}/>);
  expect(start).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Decoder edition"),{target:{value:"enhanced"}});
  fireEvent.click(screen.getByRole("button",{name:"Load sight model"}));
  await waitFor(()=>expect(screen.getByRole("img",{name:"Frozen weapon sight viewport"})).toBeInTheDocument());
  expect(start).toHaveBeenCalledWith("inspect_weapon_workbench",expect.objectContaining({workspace:"copy",expected_revision:0,entry:"stream/w_pi_demo.ydr",sight_action:"model",edition:"enhanced",lod:"High"}),expect.any(String),expect.any(Function));
});
it("refuses mismatched source evidence",async()=>{
  const snapshot=weaponPreviewSnapshot("copy"),client=createPreviewClient("weapons");
  vi.spyOn(client,"startJob").mockImplementation(async(_op,_payload,_rev,event)=>{event({protocol_version:"1.0.0",request_id:"s",job_id:"s",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result:{...model,weapon:"OTHER"}}});return {job_id:"s"} as never;});
  render(<WeaponSightBench client={client} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={vi.fn()} onReview={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText("Decoder edition"),{target:{value:"enhanced"}});
  fireEvent.click(screen.getByRole("button",{name:"Load sight model"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("does not match");
  expect(screen.queryByRole("img",{name:"Frozen weapon sight viewport"})).not.toBeInTheDocument();
});
it("reads clip inventory without automatically sampling the first clip",async()=>{
  const snapshot={...weaponPreviewSnapshot("copy"),animation_assets:["stream/demo.ycd"]},client=createPreviewClient("weapons");
  const inventory={...model,kind:"weapon_sight_animation",entry:"stream/demo.ycd",packet:{schema_version:1,read_only:true,selected:null,duration:0,
    sampled:false,scope:"Inventory",times:[],tracks:[],choices:[{key:"clip:11111111",name:"w_fire",kind:"clip",duration:1,error:null},
      {key:"clip:22222222",name:"unsupported_reload",kind:"clip",duration:2,error:"unsupported layout"}]}};
  const start=vi.spyOn(client,"startJob").mockImplementation(async(_op,payload,_rev,event)=>{
    event({protocol_version:"1.0.0",request_id:"s",job_id:"s",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result:payload.sight_action==="animation"?inventory:model}});
    return {job_id:"s",accepted:true} as never;
  });
  render(<WeaponSightBench client={client} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={vi.fn()} onReview={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText("Decoder edition"),{target:{value:"enhanced"}});
  fireEvent.click(screen.getByText("Load sight model"));
  await screen.findByRole("img",{name:"Frozen weapon sight viewport"});
  fireEvent.click(screen.getByText("Firing / reload inspection"));
  fireEvent.change(screen.getByLabelText("Animation dictionary"),{target:{value:"stream/demo.ycd"}});
  fireEvent.click(screen.getByText("Read animation clips"));
  expect(await screen.findByLabelText("Exact clip")).toHaveValue("");
  expect(screen.queryByLabelText("Frozen animation time")).not.toBeInTheDocument();
  expect(screen.getByRole("option",{name:/unsupported_reload/})).toBeDisabled();
  expect(start.mock.calls[1][1]).not.toHaveProperty("selection");
  fireEvent.change(screen.getByLabelText("Exact clip"),{target:{value:"clip:11111111"}});
  await waitFor(()=>expect(start.mock.calls[2][1]).toHaveProperty("selection","clip:11111111"));
});
it("does not offer attached optic calibration for an unassembled weapon",()=>{
  const snapshot=weaponPreviewSnapshot("copy");
  render(<SightEditor model={model} animation={null} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={vi.fn()} onReview={vi.fn()}/>);
  expect(screen.queryByRole("option",{name:"Attached optic camera"})).not.toBeInTheDocument();
});
it("exposes optic-only trials and geometry visibility without changing metadata",()=>{
  const snapshot=weaponPreviewSnapshot("copy"), change=vi.fn();
  const attached={...model,attachment:{component:"COMPONENT_SCOPE",component_type:"CWeaponComponentScopeInfo",entry:"scope.ydr",native_sha256:"b".repeat(64),
    parent_bone:"tip",child_bone:"root",parent_index:1,child_index:0,packet:fixture as AnimationModel,scope:"test"}};
  render(<SightEditor model={attached} animation={null} snapshot={snapshot} draft={snapshot.values!.values} locked={false} onChange={change} onReview={vi.fn()}/>);
  expect(screen.getByRole("option",{name:"Attached optic camera"})).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Show selected component"));
  fireEvent.click(screen.getByLabelText("Mesh 1"));
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Sight landmarks & reference export"));
  const reference={schema_version:1,kind:"offline_sight_reference",entry:model.entry,weapon:snapshot.selected_weapon,native_sha256:model.native_sha256,xml_sha256:model.packet.source_sha256,
    lod:model.packet.lod,drawable:model.packet.selected,edition:model.edition,reference:{eye:[-.4,0,.1],yaw:0,pitch:0,roll:0,fov:35},profile:"base",rear:null,front:null,original:snapshot.values!.values};
  fireEvent.change(screen.getByLabelText("Restore reference JSON (camera and landmarks only)"),{target:{value:JSON.stringify(reference)}});
  fireEvent.click(screen.getByText("Restore matching reference"));
  expect(screen.getByRole("alert")).toHaveTextContent("exact attachment");
});
