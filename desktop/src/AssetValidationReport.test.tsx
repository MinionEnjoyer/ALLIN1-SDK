import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import AssetValidationReport, {type AssetReport} from "./AssetValidationReport";

const report = ():AssetReport => ({schema_version:1, ruleset:"asset-validation/1", sdk_version:"test", read_only:true,
  source_sha256:"a".repeat(64), validator_sha256:"b".repeat(64), report_sha256:"c".repeat(64), static_status:"incomplete", runtime_status:"not_tested", scope:"Primary drawable XML only.",
  checks:["skeleton","attachments","skinning","textures","lods","metadata"].map(category=>({category,status:category==="attachments"?"not_checked":"pass", finding_count:0,truncated:false,findings:[]})),
  lod_metrics:[{drawable:0,lod:"High",vertices:3,triangles:1,complete:true}]});

it("shows separate native metadata hashes without promoting decoding to validation",async()=>{
  const value:AssetReport={...report(),metadata_evidence:[{source:"package:variation.ymt",source_sha256:"d".repeat(64),native:true,
    xml_sha256:"e".repeat(64),xml_bytes:400,status:"xml_available",definition_schema_supported:false,definition_count:0}]};
  const view=render(<AssetValidationReport report={value}/>);
  await userEvent.click(screen.getByText("Metadata decoding evidence · 1 sources"));
  expect(screen.getByRole("table",{name:"Metadata source and decoding coverage"})).toHaveTextContent("Definition schema not mapped");
  expect(screen.getByText("e".repeat(64))).toBeVisible();
  value.metadata_evidence![0].xml_sha256="not a checksum";
  view.rerender(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});

it("exposes separate authored fragment matrices and rejects malformed child evidence",async()=>{
  const value:AssetReport={...report(),fragment_scope:"Authored evidence, not physics simulation.",fragment_children:[{
    location:"Physics/LOD1/Children/0",source:"fixture.yft",child_index:0,physics_lod:"LOD1",null_child:false,
    bone_tag:42,bone_indices:[1],group_index:0,position_offset:[2,3,4],physics_matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[5,6,7,1]],
    drawables:[{variant:"Drawable",matrix:[[1,0,0],[0,1,0],[0,0,1],[8,9,10]],static_status:"incomplete",lod_metrics:[{drawable:0,lod:"High",vertices:3,triangles:1,complete:true}]}]}]};
  const view=render(<AssetValidationReport report={value}/>);
  await userEvent.click(screen.getByText("Fragment physics-child evidence · 1"));
  await userEvent.click(screen.getByText("fixture.yft · LOD1 child 0"));
  expect(screen.getByText(/Bone tag 42/)).toHaveTextContent("bone 1");
  expect(screen.getByRole("table",{name:"Physics matrix · serialized 4×4 rows"})).toHaveTextContent("5.00000");
  await userEvent.click(screen.getByText("Drawable · static incomplete"));
  expect(screen.getByRole("table",{name:"Drawable matrix · serialized four 3-value vectors"})).toHaveTextContent("8.00000");
  expect(screen.getByText(/Kept separate/)).toHaveTextContent("2, 3, 4");
  value.fragment_children![0].physics_matrix![0][0]=NaN;
  view.rerender(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});

it("separates runtime proof, partial coverage and geometry cost from an all-clear",async()=>{
  const user=userEvent.setup(), exportReport=vi.fn();
  render(<AssetValidationReport report={report()} onExport={exportReport}/>);
  expect(screen.getByRole("status")).toHaveTextContent("In-game behavior has not been tested");
  expect(screen.getByLabelText("Attachment placement")).toHaveTextContent("not checked");
  expect(screen.getByRole("table")).toHaveAccessibleName("Measured geometry counts—not GPU memory or visual quality");
  await user.click(screen.getByRole("button",{name:"Review asset report export"}));
  expect(exportReport).toHaveBeenCalledOnce();
});
it("blocks report export while draft evidence is stale or a review is active",()=>{
  const view=render(<AssetValidationReport report={report()} stale onExport={vi.fn()}/>);
  expect(screen.getByRole("status")).toHaveTextContent("saved XML, not your unsaved edits");
  expect(screen.getByRole("button")).toBeDisabled();
  view.rerender(<AssetValidationReport report={report()} locked onExport={vi.fn()}/>);
  expect(screen.getByRole("button")).toBeDisabled();
});
it("rejects missing categories and a forged runtime-pass claim",()=>{
  const value=report();value.checks.pop();
  const view=render(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
  view.rerender(<AssetValidationReport report={{...report(),runtime_status:"passed"}}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});

it("labels validated texture storage as a subtotal with explicit residency exclusions",()=>{
  const value={...report(),texture_storage_bytes:32,texture_memory_scope:"Missing payloads excluded; not measured GPU residency.",
    texture_costs:[{source:"paint.ytd",name:"diffuse",format:"DXT1",width:7,height:3,mip_levels:3,storage_bytes:32,sha256:"d".repeat(64)}]};
  render(<AssetValidationReport report={value}/>);
  expect(screen.getByText("Validated texture storage subtotal · 32 bytes")).toBeInTheDocument();
  expect(screen.getByRole("table",{name:"Verified texture payload costs"})).toHaveTextContent("paint.ytd");
  expect(screen.getByText(/Missing payloads excluded/)).toBeInTheDocument();
});

it("shows exact parent lookup and metadata hashes without implying load-order proof",async()=>{
  const value:AssetReport={...report(),texture_resolutions:[{sampler:"car/diffuse",texture:"diffuse",dictionary:"comparison:shared.ytd",payload_sha256:"d".repeat(64),dictionary_chain:[{dictionary:"paint",source:"package:paint.ytd"},{dictionary:"shared",source:"comparison:shared.ytd"}],parent_chain:[{child:"paint",parent:"shared"}]}],texture_parent_relationships:[{child:"paint",parent:"shared",source:"parents.meta",source_sha256:"e".repeat(64)}]};
  const view=render(<AssetValidationReport report={value}/>);
  await userEvent.click(screen.getByText("Resolved texture dependencies · 1"));
  await userEvent.click(screen.getByText("diffuse · comparison:shared.ytd"));
  expect(screen.getByRole("list",{name:"Selected texture lookup chain"})).toHaveTextContent("paint · package:paint.ytd");
  await userEvent.click(screen.getByText("Texture parent declaration provenance · 1"));
  expect(screen.getByRole("table",{name:"Selected texture parent metadata"})).toHaveTextContent("e".repeat(64));
  value.texture_parent_relationships![0].source_sha256="invalid";
  view.rerender(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});

it("shows explicit attachment sources and rejects malformed anchor matrices",async()=>{
  const user=userEvent.setup();
  const matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,1],[0,0,0,1]];
  const value={...report(),attachment_frame_convention:"Authored bind frames, not a game pose.",attachment_bindings:[{weapon:"WEAPON_TEST",component:"COMPONENT_TEST",bone:"tip",bone_index:1,bone_tag:42,parent_source:"body.ydr",child_source:"clip.ydr",local_matrix:matrix,skeleton_matrix:matrix}]};
  const view=render(<AssetValidationReport report={value}/>);
  await user.click(screen.getByText("Resolved attachment anchors · 1"));
  await user.click(screen.getByText("WEAPON_TEST → COMPONENT_TEST · tip"));
  expect(screen.getByRole("table",{name:"Authored skeleton anchor matrix · tip"})).toHaveTextContent("1.00000");
  expect(screen.getByText(/Parent: body.ydr/)).toHaveTextContent("Child: clip.ydr");
  value.attachment_bindings[0].skeleton_matrix=[[NaN]];
  view.rerender(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});

it("displays authored LOD values without claiming measured transitions and rejects invalid evidence",async()=>{
  const value={...report(),lod_distances:[{drawable:0,lod:"High",field:"LodDistHigh",distance:100,status:"valid_authored_value",models_present:true}]};
  const view=render(<AssetValidationReport report={value}/>);
  await userEvent.click(screen.getByText("Authored LOD distance evidence"));
  expect(screen.getByRole("table",{name:"Authored LOD values and populated geometry"})).toHaveTextContent("100");
  expect(screen.getByText(/not measured engine transition/)).toBeInTheDocument();
  value.lod_distances[0].distance=NaN;
  view.rerender(<AssetValidationReport report={value}/>);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid");
});
