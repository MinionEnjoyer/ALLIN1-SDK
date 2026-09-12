import {render,screen} from "@testing-library/react";
import {expect,it} from "vitest";
import AssemblyEvidence,{validAssemblyEvidence,type AssemblyRow} from "./AssemblyEvidence";

const row=():AssemblyRow=>({parent:"package:body.ydr.xml",child:"package:clip.ydr.xml",parent_drawable:0,child_drawable:0,
  parent_sha256:"a".repeat(64),child_sha256:"b".repeat(64),binding_sha256:"c".repeat(64),parent_bone:"tip",child_bone:"",offset:[2,0,0],
  status:"checked",message:"User-declared placement, not engine proof.",parent_anchor_matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,1],[0,0,0,1]],
  child_anchor_matrix:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],child_to_parent_matrix:[[1,0,0,2],[0,1,0,0],[0,0,1,1],[0,0,0,1]],
  relative_anchor_error:0,orientation_reversing:false});
it("shows declared placement with its evidence limits",()=>{
  expect(validAssemblyEvidence([row()])).toBe(true);
  render(<AssemblyEvidence rows={[row()]} scope="Bind frames only; no runtime proof."/>);
  expect(screen.getByText(/not engine proof/)).toBeInTheDocument();
  expect(screen.getByText(/Relative anchor agreement error: 0/)).toBeInTheDocument();
});
it.each(["matrix","error","status","hash","missing"])("rejects malformed %s evidence",kind=>{
  const value=row();
  if(kind==="matrix")value.child_to_parent_matrix![0][0]=NaN;
  if(kind==="error")value.relative_anchor_error=1;
  if(kind==="status")value.status="game_verified" as AssemblyRow["status"];
  if(kind==="hash")value.parent_sha256="unknown";
  if(kind==="missing")value.child_to_parent_matrix=null;
  expect(validAssemblyEvidence([value])).toBe(false);
});
it.each([
  { caseName: "zero length", rotation: [0,0,0,0] },
  { caseName: "non-unit length", rotation: [0,0,0,2] },
  { caseName: "wrong component count", rotation: [0,0,1] },
  { caseName: "non-finite component", rotation: [0,0,NaN,1] },
])("rejects invalid quaternion: $caseName",({rotation})=>{
  expect(validAssemblyEvidence([{...row(),rotation}])).toBe(false);
});
it("retains a declared rotation and labels its axes",()=>{
  const value={...row(),rotation:[1,0,0,0]};
  expect(validAssemblyEvidence([value])).toBe(true);
  render(<AssemblyEvidence rows={[value]} scope="Declared rigid offset only."/>);
  expect(screen.getByText(/Declared local rotation XYZW: 1, 0, 0, 0/)).toBeInTheDocument();
});
