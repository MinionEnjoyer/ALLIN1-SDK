import { expect, it } from "vitest";
import fixture from "./nativeAnimationModelFixture.json";
import animation from "./nativeAnimationFixture.json";
import { poseModel, prepareModel, type AnimationModel } from "./animationPose";
import { assemblePose, prepareAttachment, type SightAttachment } from "./weaponSightAssembly";
import type { AnimationPacket } from "./NativeAnimationView";

const make=():SightAttachment=>({component:"COMPONENT_TEST",component_type:"CWeaponComponentScopeInfo",entry:"scope.ydr",native_sha256:"a".repeat(64),
  parent_bone:"tip",child_bone:"root",parent_index:1,child_index:0,packet:structuredClone(fixture) as AnimationModel,scope:"test"});
it("maps child bind geometry through the exact animated parent mount without double transforms",()=>{
  const parent=prepareModel(fixture as AnimationModel), child=prepareAttachment(parent,make());
  const posed=poseModel(parent,animation as AnimationPacket,.5,0);
  const assembled=assemblePose(posed,child);
  expect(assembled.meshes).toHaveLength(2);
  expect(assembled.meshes[0]).toBe(posed.meshes[0]);
  expect(assembled.meshes[1].positions.slice(0,3)).toEqual([1.5,0,1]);
  const bound=assemblePose(poseModel(parent,animation as AnimationPacket,0,0,true),child);
  expect(bound.meshes[1].positions.slice(0,3)).toEqual([0,0,1]);
});
it("compensates for a nonzero child anchor and rejects mismatched declared bones",()=>{
  const parent=prepareModel(fixture as AnimationModel), source=make();
  source.child_index=1;source.child_bone="tip";
  const result=assemblePose(poseModel(parent,animation as AnimationPacket,0,0,true),prepareAttachment(parent,source));
  expect(result.meshes[1].positions).toEqual(fixture.meshes[0].positions.flat());
  source.parent_bone="invented";
  expect(()=>prepareAttachment(parent,source)).toThrow("attachment frames");
});
it("allows bounded high-detail sight geometry without weakening normal playback",()=>{
  const model=structuredClone(fixture) as AnimationModel, count=30003, vertices=Array.from({length:count*3},(_,i)=>i%3===2?1:0);
  const chunk=(v:number[])=>Array.from({length:Math.ceil(v.length/1500)},(_,i)=>v.slice(i*1500,i*1500+1500));
  model.meshes=[{skin:false,rigid_bone:0,positions:chunk(vertices),triangles:[[0,1,2]],weights:[],indices:[]}];model.vertex_count=count;model.triangle_count=1;
  expect(()=>prepareModel(model)).toThrow();
  expect(prepareModel(model,"sight").model.vertex_count).toBe(count);
  model.meshes[0].positions=chunk(Array(100001*3).fill(0));model.vertex_count=100001;
  expect(()=>prepareModel(model,"sight")).toThrow();
});
it("hides only explicitly chosen diagnostic geometry and rejects invalid indices",()=>{
  const parent=prepareModel(fixture as AnimationModel), child=prepareAttachment(parent,make());
  const pose=poseModel(parent,animation as AnimationPacket,0,0,true);
  expect(assemblePose(pose,child,[0]).meshes).toEqual(pose.meshes);
  expect(()=>assemblePose(pose,child,[-1])).toThrow("hidden component");
  expect(()=>assemblePose(pose,child,[1])).toThrow("hidden component");
});
