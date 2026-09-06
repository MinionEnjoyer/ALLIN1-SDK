import { describe, expect, it } from "vitest";
import { prepareModel, poseModel, trs, transform, type AnimationModel } from "./animationPose";
import type { AnimationPacket } from "./NativeAnimationView";
import modelFixture from "./nativeAnimationModelFixture.json";
import animationFixture from "./nativeAnimationFixture.json";

const model = () => structuredClone(modelFixture) as AnimationModel;
const animation = () => structuredClone(animationFixture) as AnimationPacket;
const close = (actual:number[],expected:number[])=>actual.forEach((value,i)=>expect(value).toBeCloseTo(expected[i],5));

describe("verified tag/palette pose evaluation",()=>{
  it("preserves the bind mesh and applies an animated root to all weighted descendants",()=>{
    const prepared=prepareModel(model()), anim=animation();
    const bind=poseModel(prepared,anim,0,0,true);
    close(bind.meshes[0].positions,prepared.meshes[0].positions);
    const pose=poseModel(prepared,anim,.5,0);
    close(pose.meshes[0].positions,[1.5,0,0, 2.5,0,1, 1.5,1,1]);
    close(pose.bones[1],[1.5,0,1]);
    expect(pose.matched).toBe(2);
  });
  it("rotates about a child's bind origin and interpolates byte weights",()=>{
    const anim=animation();
    anim.tracks=[{...anim.tracks[1],bone_tag:42,values:Array.from({length:240},()=>[0,0,1,0]).flat()}];
    const pose=poseModel(prepareModel(model()),anim,0,0);
    close(pose.meshes[0].positions,[0,0,0, -1,0,1, 0,-1/255,1]);
  });
  it("shortest-arc interpolation treats opposite-sign quaternions as the same pose",()=>{
    const anim=animation();
    anim.times=[0,1]; anim.duration=1;
    anim.tracks=[{...anim.tracks[1],values:[0,0,0,1, 0,0,0,-1]}];
    close(poseModel(prepareModel(model()),anim,.5,0).meshes[0].positions,model().meshes[0].positions.flat());
  });
  it("keeps clip layers isolated and reports missing/expression channels",()=>{
    const anim=animation();
    anim.tracks.push({...anim.tracks[0],id:"1:0",layer:1,values:Array(240).fill([10,0,0,0]).flat()},
      {...anim.tracks[0],id:"missing",bone_tag:1234}, {...anim.tracks[0],id:"expression",track:24,flags:1});
    const pose=poseModel(prepareModel(model()),anim,0,0);
    expect([pose.matched,pose.missing,pose.unsupported]).toEqual([2,1,1]);
    expect(pose.meshes[0].positions[0]).toBe(.5);
    expect(poseModel(prepareModel(model()),anim,0,1).meshes[0].positions[0]).toBe(10);
  });
  it("rejects tag mismatches and duplicate bone channel bindings",()=>{
    const anim=animation(); anim.tracks=anim.tracks.map(t=>({...t,bone_tag:999}));
    expect(()=>poseModel(prepareModel(model()),anim,0,0)).toThrow("No supported channels");
    const duplicate=animation();duplicate.tracks.push({...duplicate.tracks[0],id:"duplicate",flags:1});
    expect(()=>poseModel(prepareModel(model()),duplicate,0,0)).toThrow("Ambiguous");
  });
  it("supports local rigid meshes and nonidentity scaled bind transforms",()=>{
    const source=model(); source.bones[0].translation=[1,2,3];source.bones[0].scale=[2,2,2];
    const mesh=source.meshes[0]; mesh.skin=false;mesh.rigid_bone=1;mesh.indices=[];mesh.weights=[];
    const prepared=prepareModel(source), pose=poseModel(prepared,animation(),0,0,true);
    close(pose.meshes[0].positions.slice(0,3),[1,2,5]);
    close(transform(trs([1,2,3],[0,0,Math.SQRT1_2,Math.SQRT1_2],[2,3,4]),[1,0,0]),[1,4,3]);
    const skin=model();skin.bones=source.bones;
    close(poseModel(prepareModel(skin),animation(),0,0,true).meshes[0].positions,skin.meshes[0].positions.flat());
  });
  it("applies root motion after local channels, composes rotation and remains deterministic on seeks",()=>{
    const anim=animation(), prepared=prepareModel(model());
    anim.tracks.push({...anim.tracks[0],id:"root-t",track:5,values:Array(240).fill([10,0,0,0]).flat()},
      {...anim.tracks[1],id:"root-q",track:6,values:Array(240).fill([0,0,Math.SQRT1_2,Math.SQRT1_2]).flat()});
    const disabled=poseModel(prepared,anim,.5,0);
    expect(disabled.unsupported).toBe(2);
    close(disabled.bones[0],[1.5,0,0]);
    const enabled=poseModel(prepared,anim,.5,0,false,true);
    expect(enabled.matched).toBe(4);
    close(enabled.bones[0],[10,1.5,0]);
    close(enabled.meshes[0].positions,[10,1.5,0, 10,2.5,1, 9,1.5,1]);
    poseModel(prepared,anim,0,0,false,true);
    close(poseModel(prepared,anim,.5,0,false,true).meshes[0].positions,enabled.meshes[0].positions);
    close(poseModel(prepared,anim,.5,0,true,true).meshes[0].positions,prepared.meshes[0].positions);
  });
  it("accepts ordinary nonzero channel flags without enabling expression remapping",()=>{
    const anim=animation(), prepared=prepareModel(model());
    const expected=poseModel(prepared,anim,.5,0);
    anim.tracks=anim.tracks.map((track,i)=>({...track,flags:i+1}));
    const actual=poseModel(prepared,anim,.5,0);
    expect(actual.matched).toBe(2);
    close(actual.meshes[0].positions,expected.meshes[0].positions);
    anim.tracks[0].flags=256;
    expect(()=>poseModel(prepared,anim,0,0)).toThrow("Invalid animation channel flags");
  });
  it("does not misapply non-root motion tracks but accepts flagged root channels",()=>{
    const anim=animation();
    anim.tracks.push({...anim.tracks[0],id:"not-root",track:5,bone_tag:42},{...anim.tracks[1],id:"flagged-root",track:6,flags:1});
    const pose=poseModel(prepareModel(model()),anim,0,0,false,true);
    expect(pose.unsupported).toBe(1);
    expect(pose.matched).toBe(3);
    close(pose.bones[0],[.5,0,0]);
  });
  it.each([
    (m:AnimationModel)=>{m.bones[1].tag=0;},
    (m:AnimationModel)=>{m.bones[0].parent=1;},
    (m:AnimationModel)=>{m.bones[1].parent=99;},
    (m:AnimationModel)=>{m.bones[0].scale=[0,1,1];},
    (m:AnimationModel)=>{m.bones[0].rotation=[0,0,0,0];},
    (m:AnimationModel)=>{m.meshes[0].indices[0][0]=999;},
    (m:AnimationModel)=>{m.meshes[0].weights[0][0]=0;},
    (m:AnimationModel)=>{m.meshes[0].triangles[0][0]=99;},
    (m:AnimationModel)=>{m.meshes[0].positions[0][0]=NaN;},
    (m:AnimationModel)=>{m.vertex_count++;},
  ])("rejects malformed skeleton/mesh evidence %#",mutate=>{
    const source=model(); mutate(source);expect(()=>prepareModel(source)).toThrow();
  });
});
