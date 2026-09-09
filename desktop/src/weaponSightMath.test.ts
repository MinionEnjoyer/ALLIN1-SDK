import { expect,it } from "vitest";
import {alignedRig,basis,candidateRig,pick,profiles,project,viewProjection,type Rig} from "./weaponSightMath";
const rig:Rig={eye:[0,0,0],yaw:0,pitch:0,roll:0,fov:60};
it("projects +X forward with correct vertical perspective and rejects behind-eye marks",()=>{
  expect(project([1,0,0],rig)).toEqual([480,270,1]);
  expect(project([-1,0,0],rig)).toBeNull();
  expect(project([1,0,.1],rig)![1]).toBeLessThan(270);
  expect(project([1,-.1,0],rig)![0]).toBeGreaterThan(480);
});
it("GPU projection agrees with measurement at arbitrary yaw/pitch/roll",()=>{
  const r={...rig,eye:[.1,-.3,.01] as [number,number,number],yaw:23,pitch:5,roll:10};
  const p=[1,.1,.2,1],m=viewProjection(r,960/540);
  const clip=[0,1,2,3].map(i=>p.reduce((s,v,j)=>s+m[j*4+i]*v,0));
  const screen=project([1,.1,.2],r)!;
  expect((clip[0]/clip[3]+1)*480).toBeCloseTo(screen[0],3);
  expect((1-clip[1]/clip[3])*270).toBeCloseTo(screen[1],3);
});
it("aligns rear and front geometrically without changing their model coordinates",()=>{
  const r=alignedRig([0,0,.1],[1,0,.11],.15,35);
  expect(project([0,0,.1],r)![1]).toBeCloseTo(270);
  expect(project([1,0,.11],r)![1]).toBeCloseTo(270);
  expect(()=>alignedRig([0,0,0],[0,0,0],.1,35)).toThrow();
});
it("applies only selected family deltas and leaves saved values untouched",()=>{
  const original={"weapon.firstPersonScopeOffset.z":"0.01","weapon.firstPersonLTOffset.z":"0.04"};
  const next=candidateRig(rig,original,{...original,"weapon.firstPersonScopeOffset.z":"0.02"},profiles[1]);
  expect(next.eye[2]).toBeCloseTo(.01);expect(original["weapon.firstPersonScopeOffset.z"]).toBe("0.01");
  expect(candidateRig(rig,original,{...original,"weapon.firstPersonScopeOffset.z":"0.02"},profiles[0])).toEqual(rig);
  expect(()=>candidateRig(rig,original,{...original,"weapon.firstPersonScopeOffset.z":"NaN"},profiles[1])).toThrow();
});
it("picks the closest actual surface rather than a hidden triangle",()=>{
  const meshes=[{positions:[2,-1,-1,2,1,-1,2,0,1,1,-1,-1,1,1,-1,1,0,1],triangles:[0,1,2,3,4,5]}];
  expect(pick(meshes,rig,480,270,960,540)).toEqual([1,0,0]);
});
it("rejects malformed camera values and preserves an orthonormal basis",()=>{
  expect(()=>basis({...rig,fov:NaN})).toThrow();expect(()=>basis({...rig,pitch:90})).toThrow();
  const b=basis({...rig,roll:48,yaw:92,pitch:38});
  expect(b.right.reduce((s,n,i)=>s+n*b.forward[i],0)).toBeCloseTo(0);
});
it("separates attached optic camera trials from the base sight family",()=>{
  const original={"weapon.firstPersonScopeOffset.z":"0", "weapon.firstPersonScopeAttachmentOffset.z":"0"};
  const draft={...original,"weapon.firstPersonScopeAttachmentOffset.z":"0.01"};
  expect(candidateRig(rig,original,draft,profiles[1])).toEqual(rig);
  expect(candidateRig(rig,original,draft,profiles[2]).eye[2]).toBeCloseTo(.01);
});
