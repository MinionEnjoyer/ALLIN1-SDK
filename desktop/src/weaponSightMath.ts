/** Explicit model-space camera hypothesis, never a reconstruction of GTA's aim IK. */
import type { PreparedModel } from "./animationPose";
export type Vec = [number, number, number];
export type Rig = { eye: Vec; yaw: number; pitch: number; roll: number; fov: number };
export type SightMesh = { positions: number[]; triangles: number[] };
export const add = (a: Vec,b: Vec): Vec => a.map((v,i)=>v+b[i]) as Vec;
export const sub = (a: Vec,b: Vec): Vec => a.map((v,i)=>v-b[i]) as Vec;
export const mul = (a: Vec,k: number): Vec => a.map(v=>v*k) as Vec;
export const dot = (a: Vec,b: Vec) => a.reduce((v,n,i)=>v+n*b[i],0);
export const cross = (a: Vec,b: Vec): Vec => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
export function unit(a: Vec): Vec { const l=Math.hypot(...a); if(l<1e-9 || !Number.isFinite(l)) throw new Error("Degenerate sight direction"); return mul(a,1/l); }
const rad = (n: number) => n*Math.PI/180;
export function basis(rig: Rig) {
  if(![...rig.eye,rig.yaw,rig.pitch,rig.roll,rig.fov].every(Number.isFinite) || rig.eye.some(v=>Math.abs(v)>10) || rig.fov<1 || rig.fov>170 || Math.abs(rig.pitch)>89 || Math.abs(rig.yaw)>720 || Math.abs(rig.roll)>720) throw new Error("Invalid reference camera");
  const y=rad(rig.yaw), p=rad(rig.pitch), r=rad(rig.roll);
  const forward: Vec=[Math.cos(p)*Math.cos(y),Math.cos(p)*Math.sin(y),Math.sin(p)];
  const right=unit(cross(forward,[0,0,1])), up=unit(cross(right,forward));
  return {forward,right:add(mul(right,Math.cos(r)),mul(up,Math.sin(r))),up:add(mul(up,Math.cos(r)),mul(right,-Math.sin(r)))};
}
export function defaultRig(prepared: PreparedModel): Rig {
  // +X is merely the initial viewing axis, not a detected sight or runtime pose.
  return {eye:[prepared.center[0]-prepared.radius*1.2,prepared.center[1],prepared.center[2]+prepared.radius*.2].map(v=>Number(v.toFixed(6))) as Vec,yaw:0,pitch:0,roll:0,fov:35};
}
export const profiles = [
  {id:"lt", label:"Aimed / LT",position:"firstPersonLTOffset",rotation:"firstPersonLTRotationOffset"},
  {id:"base",label:"Base sights / scope camera",position:"firstPersonScopeOffset",rotation:"firstPersonScopeRotationOffset"},
  {id:"attached",label:"Attached optic camera",position:"firstPersonScopeAttachmentOffset",rotation:"firstPersonScopeAttachmentRotationOffset"},
] as const;
export function candidateRig(reference: Rig, original: Record<string,string>, draft: Record<string,string>, profile: typeof profiles[number]): Rig {
  const delta=(field: string,axis: string) => {
    const k=`weapon.${field}.${axis}`;
    if(!(k in original)) return 0;
    const a=Number(original[k]), b=Number(draft[k]??original[k]);
    if(!original[k].trim() || !(draft[k]??original[k]).trim() || !Number.isFinite(a) || !Number.isFinite(b)) throw new Error("Camera field draft must be finite");
    return b-a;
  };
  // Documented hypothesis: metadata XYZ -> camera right/forward/up;
  // rotation XYZ -> pitch/roll/yaw. Real runtime mapping remains unqualified.
  const b=basis(reference);
  return {...reference,eye:add(reference.eye,add(add(mul(b.right,delta(profile.position,"x")),mul(b.forward,delta(profile.position,"y"))),mul(b.up,delta(profile.position,"z")))),
    pitch:reference.pitch+delta(profile.rotation,"x"),roll:reference.roll+delta(profile.rotation,"y"),yaw:reference.yaw+delta(profile.rotation,"z")};
}
export function project(point: Vec, rig: Rig, width=960,height=540): Vec | null {
  const b=basis(rig), relative=sub(point,rig.eye), depth=dot(relative,b.forward);
  if(depth<=.001) return null;
  const f=height/(2*Math.tan(rad(rig.fov)/2));
  return [width/2+dot(relative,b.right)*f/depth,height/2-dot(relative,b.up)*f/depth,depth];
}
export function viewProjection(rig: Rig,aspect: number): Float32Array {
  const b=basis(rig), f=1/Math.tan(rad(rig.fov)/2), n=.001, far=100;
  const rows=[...mul(b.right,f/aspect),-dot(b.right,rig.eye)*f/aspect,
    ...mul(b.up,f),-dot(b.up,rig.eye)*f,
    ...mul(b.forward,(far+n)/(far-n)),-dot(b.forward,rig.eye)*(far+n)/(far-n)-2*far*n/(far-n),
    ...b.forward,-dot(b.forward,rig.eye)];
  return new Float32Array(Array.from({length:16},(_,i)=>rows[i%4*4+Math.floor(i/4)]));
}
export function pick(meshes: SightMesh[], rig: Rig,x: number,y: number,width: number,height: number): Vec | null {
  const b=basis(rig), f=height/(2*Math.tan(rad(rig.fov)/2));
  const direction=unit(add(b.forward,add(mul(b.right,(x-width/2)/f),mul(b.up,-(y-height/2)/f))));
  let nearest=Infinity, point: Vec|null=null;
  for(const mesh of meshes) for(let i=0;i<mesh.triangles.length;i+=3) {
    const vertices=mesh.triangles.slice(i,i+3).map(k=>mesh.positions.slice(k*3,k*3+3) as Vec);
    const a=sub(vertices[1],vertices[0]),c=sub(vertices[2],vertices[0]),h=cross(direction,c),d=dot(a,h);
    if(Math.abs(d)<1e-10) continue;
    const s=sub(rig.eye,vertices[0]),u=dot(s,h)/d,q=cross(s,a),v=dot(direction,q)/d,t=dot(c,q)/d;
    if(u>=0 && v>=0 && u+v<=1 && t>.001 && t<nearest) {nearest=t;point=add(rig.eye,mul(direction,t));}
  }
  return point;
}
export function alignedRig(rear: Vec,front: Vec,eyeRelief: number,fov: number): Rig {
  if(eyeRelief<.01 || eyeRelief>2) throw new Error("Eye relief must be 0.01–2 metres");
  const forward=unit(sub(front,rear));
  return {eye:sub(rear,mul(forward,eyeRelief)),yaw:Math.atan2(forward[1],forward[0])*180/Math.PI,
    pitch:Math.asin(forward[2])*180/Math.PI,roll:0,fov};
}
