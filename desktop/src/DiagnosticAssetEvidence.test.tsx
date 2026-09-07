import {render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import DiagnosticAssetEvidence,{type DiagnosticAssetSummary} from "./DiagnosticAssetEvidence";
import DataToolsWorkspace from "./DataToolsWorkspace";
import {createPreviewClient} from "./previewClient";
import type {Envelope} from "./types";

function evidence():DiagnosticAssetSummary{return {status:"recorded",file_sha256:"a".repeat(64),report_sha256:"b".repeat(64),source_sha256:"c".repeat(64),validator_sha256:"d".repeat(64),static_status:"pass",source_relation:"inputs_inventory",runtime_status:"not_tested",scope:"Bounded static evidence.",checks:["skeleton","attachments","skinning","textures","lods","metadata"].map(category=>({category,status:"pass",finding_count:0,shown_findings:0,truncated:false,codes:[]}))};}

it.each([["inputs_inventory","Input / baseline"],["outputs_inventory","Output / candidate"],["inputs+outputs_inventory","Identical input and output"]])("labels %s evidence without causal claims",async(relation,label)=>{
  render(<DiagnosticAssetEvidence value={{...evidence(),source_relation:relation}}/>);
  await userEvent.click(screen.getByText(/Static asset evidence/));
  expect(screen.getByText(new RegExp(label))).toBeVisible();
  expect(screen.getByText(/Crash cause remains unestablished/)).toBeVisible();
  expect(screen.getByRole("table")).toHaveTextContent("skinning");
});
it("keeps unrelated report findings separate from the selected artifact",async()=>{
  render(<DiagnosticAssetEvidence value={{...evidence(),status:"not_recorded",source_relation:"not_established"}}/>);
  await userEvent.click(screen.getByText(/Static asset evidence/));
  expect(screen.getByRole("status")).toHaveTextContent("Do not attribute its findings to that build");
});
it.each(["empty_failure","status_type"])("rejects contradictory summary %s",fault=>{
  const value=evidence();
  if(fault==="empty_failure"){value.checks[0].status="fail";value.static_status="fail";}
  else value.checks[0].status=["pass"] as never;
  render(<DiagnosticAssetEvidence value={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});
it.each([null,{...evidence(),runtime_status:"passed"},{...evidence(),file_sha256:"broken"},{...evidence(),status:"not_recorded"},{...evidence(),static_status:"fail"},{...evidence(),checks:[null]},{...evidence(),checks:Array(6).fill(evidence().checks[0])},{...evidence(),checks:evidence().checks.map(c=>({...c,shown_findings:41}))}])("rejects malformed summary %#",value=>{
  render(<DiagnosticAssetEvidence value={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("evidence is invalid");
});
it("selects and clears a static report through the diagnostic workflow, invalidating the previous inspection",async()=>{
  const client=createPreviewClient("quick_import");
  const select=vi.spyOn(client,"selectPath").mockResolvedValueOnce("C:/fixture/artifact.json").mockResolvedValueOnce("C:/fixture/receipt.json").mockResolvedValueOnce("C:/fixture/game").mockResolvedValueOnce("C:/fixture/after-report.json");
  const start=vi.spyOn(client,"startJob").mockImplementation(async(_operation,_payload,_request,emit)=>{
    const response:Envelope={protocol_version:"1.0.0",request_id:"test",job_id:"fixture",operation:"result",sequence:1,risk:"read_only",terminal:true,payload:{result:{kind:"workspace_session",module:"data_tools",schema_version:1,read_only:true,game_write_performed:false,state_sha256:"a".repeat(64),document:{artifact_id:"b".repeat(64),build_fingerprint:"c".repeat(64),findings:[],asset_validation:evidence()}}}};
    emit(response);
    return {job_id:"fixture",accepted:response};
  });
  const user=userEvent.setup();
  render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
  await user.click(screen.getByRole("button",{name:"Trace build to game"}));
  for(const name of ["Choose artifact manifest","Choose installation receipt","Choose diagnostic installation"])await user.click(screen.getByRole("button",{name}));
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(screen.getByRole("button",{name:"Review report export"})).toBeEnabled();
  await user.click(screen.getByText("Optional static-validation report"));
  await user.click(screen.getByRole("button",{name:"Choose static asset report"}));
  expect(select).toHaveBeenLastCalledWith("code_source");
  expect(screen.getByRole("button",{name:"Review report export"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(start).toHaveBeenLastCalledWith("inspect_authoring_workspace",expect.objectContaining({settings:expect.objectContaining({asset_report:"C:/fixture/after-report.json"})}),expect.any(String),expect.any(Function));
  await user.click(screen.getByRole("button",{name:"Clear static asset report"}));
  expect(screen.getByRole("button",{name:"Review report export"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Inspect data"}));
  expect(start.mock.calls.at(-1)?.[1].settings).not.toHaveProperty("asset_report");
});
