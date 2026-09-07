import {render,screen} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {expect,it,vi} from "vitest";
import DataToolsWorkspace from "./DataToolsWorkspace";
import OptimizationWorkspace from "./OptimizationWorkspace";
import {createPreviewClient} from "./previewClient";
import type {Envelope} from "./types";

it.each(["data_tools","optimization"])("does not adopt contradictory report evidence in %s",async module=>{
  const client=createPreviewClient("quick_import");
  vi.spyOn(client,"selectPath").mockResolvedValue("C:/fixture/package");
  const report={schema_version:1,read_only:true,runtime_status:"not_tested",source_sha256:"a".repeat(64),validator_sha256:"b".repeat(64),report_sha256:"c".repeat(64),static_status:"pass",
    checks:["skeleton","attachments","skinning","textures","lods","metadata"].map(category=>({category,status:"fail",findings:[],finding_count:0,truncated:false}))};
  vi.spyOn(client,"startJob").mockImplementation(async(_operation,_payload,_request,emit)=>{
    const response:Envelope={protocol_version:"1.0.0",request_id:"fixture",job_id:"fixture",operation:"result",sequence:1,risk:"read_only",terminal:true,
      payload:{result:{kind:"workspace_session",module,schema_version:1,game_write_performed:false,read_only:true,state_sha256:"a".repeat(64),document:report,before_report:report,after_report:report,choices:[],changes:[]}}};
    emit(response);return {job_id:"fixture",accepted:response};
  });
  const user=userEvent.setup();
  if(module==="data_tools"){
    render(<DataToolsWorkspace client={client} onGuardChange={()=>{}}/>);
    await user.click(screen.getByRole("button",{name:"Validate asset package"}));
    await user.click(screen.getByRole("button",{name:"Choose source"}));
    await user.click(screen.getByRole("button",{name:"Inspect data"}));
    expect(screen.getByRole("button",{name:"Review report export"})).toBeDisabled();
  }else{
    render(<OptimizationWorkspace client={client} onGuardChange={()=>{}}/>);
    await user.click(screen.getByRole("button",{name:"Choose optimization package"}));
    await user.click(screen.getByRole("button",{name:"Inspect optimization inputs"}));
    expect(screen.queryByRole("button",{name:"Review optimized package export"})).not.toBeInTheDocument();
  }
  expect(screen.getByRole("alert")).toHaveTextContent("validation evidence");
});
