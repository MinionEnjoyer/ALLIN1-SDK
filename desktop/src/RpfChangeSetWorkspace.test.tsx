import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import App from "./App";
import RpfChangeSetWorkspace, { type RpfChangeRequest, type RpfChangeSession } from "./RpfChangeSetWorkspace";
import { createPreviewClient } from "./previewClient";
import { rpfChangePreviewSession, rpfChangePreviewReview } from "./rpfChangePreview";
import type { Envelope, RpfArchiveResult } from "./types";

const response=(result:unknown):Envelope=>({protocol_version:"1.0.0",request_id:"rpf-test",job_id:"rpf-job",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result}});
function setup(app=false, target: RpfChangeRequest | null=null) {
  const client=createPreviewClient("rpf_changes");let current=rpfChangePreviewSession();
  const original=client.startJob.bind(client);
  client.startJob=vi.fn(async(op,payload,revision,event)=>{
    if(op!=="inspect_rpf_change_set" && op!=="review_rpf_change_set")return original(op,payload,revision,event);
    event(response(op==="inspect_rpf_change_set"?current:rpfChangePreviewReview(payload,current)));
    return {job_id:"rpf-job",accepted:response({})};
  });
  const apply=vi.spyOn(client,"applyRpfChangeSet");
  const saved=(payload:Record<string,unknown>)=>{
    const {review_sha256,authoring_confirmed,...request}=payload;void authoring_confirmed;
    const review=rpfChangePreviewReview(request,current), compile=payload.action==="compile";
    current={...current,change_set:payload.action==="create"?String(payload.destination):current.change_set,
      archive:review.archive,actions:review.after,state_sha256:compile?current.state_sha256:"f".repeat(64)};
    return response({kind:"rpf_change_set_applied",action:payload.action,review_sha256,
      output:payload.destination ?? current.change_set,output_sha256:compile?"d".repeat(64):current.state_sha256,
      session:current,file_write_performed:true,archive_write_performed:false,game_write_performed:false,plan_status:compile?"ready":null});
  };
  const guard=vi.fn(), user=userEvent.setup();
  if(app)render(<App client={client}/>);
  else render(<RpfChangeSetWorkspace client={client} indexed={{source:current.archive.path,gta_path:"C:\\Games\\Grand Theft Auto V Enhanced"} as RpfArchiveResult} onGuardChange={guard} targetRequest={target}/>);
  return {client,apply,saved,user,guard,get current(){return current;}};
}
async function open(user:ReturnType<typeof userEvent.setup>) {await user.click(screen.getByRole("button",{name:"Open change set"}));await screen.findByText("2 staged actions · enhanced");}
async function confirm(user:ReturnType<typeof userEvent.setup>,name:string) {const review=screen.getByRole("region",{name:"RPF change-set review"});await user.click(within(review).getByRole("checkbox"));await user.click(within(review).getByRole("button",{name}));}

it("opens a saved change set read-only and stages the exact reviewed payload",async()=>{
  const {user,client,apply,saved,guard}=setup();apply.mockImplementation(async p=>saved(p));await open(user);
  expect(apply).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText("Member path"),"text/new.gxt2");await user.click(screen.getByRole("button",{name:"Choose payload file"}));
  expect(guard).toHaveBeenLastCalledWith(true);expect(screen.getByRole("button",{name:"Open change set"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Review staged change"}));
  expect(await screen.findByRole("heading",{name:"Review: Stage change"})).toHaveFocus();
  expect(screen.getByRole("button",{name:"Stage change"})).toBeDisabled();
  await confirm(user,"Stage change");await screen.findByText("3 staged actions · enhanced");
  expect(apply).toHaveBeenCalledWith(expect.objectContaining({action:"stage",authoring_confirmed:true,change:{action:"replace",archive_path:"",entry:"text/new.gxt2",payload:"C:\\SDK\\imports\\replacement.gxt2"}}));
  expect(client.startJob).toHaveBeenCalledWith("review_rpf_change_set",expect.anything(),expect.any(String),expect.any(Function));
  expect(screen.getByLabelText("Member path")).toHaveValue("");expect(guard).toHaveBeenLastCalledWith(false);
});

it("reviews reordering, removal and compiled-plan export independently",async()=>{
  const {user,apply,saved}=setup();apply.mockImplementation(async p=>saved(p));await open(user);
  await user.click(screen.getByRole("button",{name:"Move down"}));await screen.findByRole("heading",{name:"Review: Reorder action"});await confirm(user,"Reorder action");
  await user.click(screen.getByRole("button",{name:"Remove staged"}));await screen.findByRole("heading",{name:"Review: Remove staged action"});
  expect(screen.getByRole("checkbox")).not.toBeChecked();await confirm(user,"Remove staged action");await screen.findByText("1 staged action · enhanced");
  await user.click(screen.getByRole("button",{name:"Review compiled plan"}));await screen.findByRole("heading",{name:"Review: Export compiled plan"});
  expect(screen.getByRole("checkbox")).not.toBeChecked();await confirm(user,"Export compiled plan");
  await screen.findByText(/Plan status: ready/);expect(apply).toHaveBeenCalledTimes(3);
});

it("requires confirmation to create a source-bound new change set",async()=>{
  const {user,apply,saved}=setup();apply.mockImplementation(async p=>saved(p));
  await user.click(screen.getByRole("button",{name:"Create change set"}));await screen.findByRole("heading",{name:"Review: Create change set"});
  expect(apply).not.toHaveBeenCalled();await confirm(user,"Create change set");await screen.findByText("0 staged actions · enhanced");
});

it("preserves the exact archive-member handoff when creating its change set",async()=>{
  const target={archive:rpfChangePreviewSession().archive.path,archive_path:"x64/data.rpf",entry:"text/global.gxt2",kind:"binary",requestId:1};
  const {user,apply,saved}=setup(false,target);apply.mockImplementation(async p=>saved(p));
  expect(screen.getByLabelText("Archive layer")).toHaveValue(target.archive_path);
  expect(screen.getByLabelText("Member path")).toHaveValue(target.entry);
  await user.click(screen.getByRole("button",{name:"Create change set"}));await screen.findByRole("heading",{name:"Review: Create change set"});await confirm(user,"Create change set");
  expect(screen.getByLabelText("Archive layer")).toHaveValue(target.archive_path);
  expect(screen.getByLabelText("Member path")).toHaveValue(target.entry);
  expect(screen.getByRole("heading",{name:"RPF change sets"})).toHaveFocus();
});

it("names the affected member and restores keyboard focus after leaving a review",async()=>{
  const {user,apply}=setup();await open(user);await user.click(screen.getByRole("button",{name:"Move down"}));
  const review=await screen.findByRole("region",{name:"RPF change-set review"});
  expect(within(review).getByText("Move to position 2").closest("p")).toHaveTextContent("x64/data.rpf → text/global.gxt2");
  await user.click(within(review).getByRole("button",{name:"Back to change set"}));
  expect(screen.getByRole("heading",{name:"RPF change sets"})).toHaveFocus();expect(apply).not.toHaveBeenCalled();
});

it.each(["case","member","payload","rename"])("validates compiled %s evidence against staged actions",async field=>{
  const {user,client,apply}=setup();await open(user);
  client.startJob=vi.fn(async(_op,payload,_revision,event)=>{
    const value=rpfChangePreviewReview(payload), row=value.plan!.changes[0];
    if(field==="case"){row.entry=String(row.entry).toUpperCase();row.archive_path=String(row.archive_path).toUpperCase();}
    if(field==="member")row.entry="different.gxt2";
    if(field==="payload")row.payload={...(row.payload as object),sha256:"0".repeat(64)};
    if(field==="rename")row.new_entry="unexpected.gxt2";
    event(response(value));return {job_id:"plan-test",accepted:response({})};
  });
  await user.click(screen.getByRole("button",{name:"Review compiled plan"}));
  if(field==="case")await screen.findByRole("heading",{name:"Review: Export compiled plan"});
  else expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was authorized");
  expect(apply).not.toHaveBeenCalled();
});

it("preserves the draft and drops confirmation after stale save failure",async()=>{
  const {user,apply}=setup();await open(user);await user.selectOptions(screen.getByLabelText("Change type"),"mkdir");await user.type(screen.getByLabelText("Member path"),"new");
  await user.click(screen.getByRole("button",{name:"Review staged change"}));await screen.findByRole("heading",{name:"Review: Stage change"});
  apply.mockResolvedValue({...response({}),operation:"error",payload:{message:"Change set changed after review"}});await confirm(user,"Stage change");
  expect(await screen.findByRole("alert")).toHaveTextContent("changed after review");expect(screen.getByLabelText("Member path")).toHaveValue("new");
  await user.click(screen.getByRole("button",{name:"Review staged change"}));await screen.findByRole("heading",{name:"Review: Stage change"});expect(screen.getByRole("checkbox")).not.toBeChecked();
});

it.each(["target","state","archive","after","risk"])("rejects malformed %s evidence before saving",async field=>{
  const {user,client,apply}=setup();await open(user);
  client.startJob=vi.fn(async(_op,payload,_revision,event)=>{
    const value=rpfChangePreviewReview(payload);
    if(field==="target")value.request={...value.request,action_id:"another"};
    if(field==="state")value.state_sha256="0".repeat(64);
    if(field==="archive")value.archive={...value.archive,path:"C:\\wrong.rpf"};
    if(field==="after")value.after[0].entry="wrong.bin";
    if(field==="risk")value.archive_write_performed=true;
    event(response(value));return {job_id:"bad-job",accepted:response({})};
  });
  await user.click(screen.getByRole("button",{name:"Move down"}));expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was authorized");expect(apply).not.toHaveBeenCalled();
});

it("handles picker cancellation and early completion without stale cancellation",async()=>{
  const {user,client}=setup();const cancel=vi.spyOn(client,"cancelJob");vi.spyOn(client,"selectRpfPlanDestination").mockResolvedValue(null);
  await user.click(screen.getByRole("button",{name:"Create change set"}));expect(screen.queryByRole("region",{name:"RPF change-set review"})).not.toBeInTheDocument();
  await open(user);expect(screen.queryByRole("button",{name:"Cancel review"})).not.toBeInTheDocument();expect(cancel).not.toHaveBeenCalled();
});

it("cancels a pending review and ignores its late terminal event",async()=>{
  const {user,client}=setup();await open(user);let deliver:((message:Envelope)=>void)|undefined;let pending:Record<string,unknown>={};
  client.startJob=vi.fn(async(_op,payload,_revision,event)=>{deliver=event;pending=payload;return {job_id:"late-job",accepted:response({})};});
  const cancel=vi.spyOn(client,"cancelJob");await user.click(screen.getByRole("button",{name:"Review compiled plan"}));await user.click(await screen.findByRole("button",{name:"Cancel review"}));
  await act(async()=>deliver?.(response(rpfChangePreviewReview(pending))));expect(cancel).toHaveBeenCalledWith("late-job");expect(screen.queryByRole("region",{name:"RPF change-set review"})).not.toBeInTheDocument();
});

it("prevents duplicate saves and refuses mismatched saved evidence",async()=>{
  const {user,apply,saved}=setup();await open(user);await user.click(screen.getByRole("button",{name:"Move down"}));await screen.findByRole("heading",{name:"Review: Reorder action"});
  let resolve:((value:Envelope)=>void)|undefined;let request:Record<string,unknown>={};apply.mockImplementation(p=>{request=p;return new Promise(r=>{resolve=r;});});
  await confirm(user,"Reorder action");expect(screen.getByRole("button",{name:"Saving…"})).toBeDisabled();expect(screen.getByRole("button",{name:"Back to change set"})).toBeDisabled();
  const result=saved(request);(result.payload.result as {session:RpfChangeSession}).session.archive={...rpfChangePreviewSession().archive,path:"C:\\wrong.rpf"};
  await act(async()=>resolve?.(result));expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");expect(apply).toHaveBeenCalledTimes(1);
});

it("guards RPF tabs and workspace navigation while a change draft is unsaved",async()=>{
  const {user}=setup(true);await user.click(await screen.findByRole("button",{name:/^RPF Archives/}));await user.click(screen.getByRole("tab",{name:"Change sets"}));await open(user);
  await user.type(screen.getByLabelText("Member path"),"pending.bin");expect(screen.getByRole("tab",{name:"GXT2 game text"})).toBeDisabled();expect(screen.getByRole("tab",{name:"Archive inspection"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:/^Help Center/}));expect(screen.getByRole("heading",{name:"RPF change sets"})).toBeVisible();
  await user.click(screen.getByRole("button",{name:"Reset change draft"}));await user.click(screen.getByRole("button",{name:/^Help Center/}));await screen.findByRole("heading",{name:"Help Center"});
});
