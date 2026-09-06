import type { AnimationPacket } from "./NativeAnimationView";

export type AnimationModel = {
  schema_version: number; read_only: boolean; source?: string; source_sha256: string; scope: string;
  drawables: {key: string; name: string}[]; selected: string | null; lod: string | null; lods: string[];
  vertex_count: number; triangle_count: number;
  binding_required?: string;
  view_unavailable?: string;
  skeleton_binding?: {mode: "external"; source?: string; source_sha256: string; selected: string|null; drawables: {key:string; name:string}[]; scope:string};
  bones: {index: number; tag: number; parent: number; name: string; translation: number[]; rotation: number[]; scale: number[]}[];
  meshes: {skin: boolean; rigid_bone: number; positions: number[][]; triangles: number[][]; weights: number[][]; indices: number[][]}[];
};
export type ModelBindingSelection = {drawable?: string; lod?: string; skeleton_drawable?: string};
type Matrix = number[];
type Mesh = {skin: boolean; rigid: number; positions: number[]; triangles: number[]; weights: number[]; indices: number[]};
export type PreparedModel = {model: AnimationModel; bind: Matrix[]; inverse: Matrix[]; meshes: Mesh[]; center: number[]; radius: number};
const identity = () => [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1];
export function multiply(a: Matrix, b: Matrix): Matrix {
  return Array.from({length: 16}, (_, i) => [0,1,2,3].reduce((v,k) => v+a[Math.floor(i/4)*4+k]*b[k*4+i%4],0));
}
export function trs(t: number[], q: number[], s: number[]): Matrix {
  const length = Math.hypot(...q);
  if (!Number.isFinite(length) || length < 1e-9) throw new Error("Invalid bone quaternion");
  const [x,y,z,w] = q.map(v=>v/length), [sx,sy,sz] = s;
  return [(1-2*(y*y+z*z))*sx,2*(x*y-z*w)*sy,2*(x*z+y*w)*sz,t[0],
    2*(x*y+z*w)*sx,(1-2*(x*x+z*z))*sy,2*(y*z-x*w)*sz,t[1],
    2*(x*z-y*w)*sx,2*(y*z+x*w)*sy,(1-2*(x*x+y*y))*sz,t[2],0,0,0,1];
}
function inverse(matrix: Matrix): Matrix {
  const rows = Array.from({length:4},(_,i)=>[...matrix.slice(i*4,i*4+4), ...identity().slice(i*4,i*4+4)]);
  for(let i=0;i<4;i++) {
    let pivot=i;
    for(let k=i+1;k<4;k++) if(Math.abs(rows[k][i])>Math.abs(rows[pivot][i])) pivot=k;
    [rows[i],rows[pivot]]=[rows[pivot],rows[i]];
    const d=rows[i][i];
    if(Math.abs(d)<1e-12) throw new Error("Singular bind transform");
    rows[i]=rows[i].map(v=>v/d);
    for(let k=0;k<4;k++) if(k!==i) { const f=rows[k][i]; rows[k]=rows[k].map((v,j)=>v-f*rows[i][j]); }
  }
  return rows.flatMap(row=>row.slice(4));
}
export function transform(m: Matrix, p: number[]): number[] {
  return [0,1,2].map(row=>m[row*4]*p[0]+m[row*4+1]*p[1]+m[row*4+2]*p[2]+m[row*4+3]);
}
function world(model: AnimationModel, locals: Matrix[]): Matrix[] {
  const result: Matrix[] = [], visiting = new Set<number>();
  function resolve(i: number): Matrix {
    if(result[i]) return result[i];
    if(visiting.has(i)) throw new Error("Bone-parent cycle");
    visiting.add(i);
    const parent=model.bones[i].parent;
    result[i]=parent<0 ? locals[i] : multiply(resolve(parent),locals[i]);
    visiting.delete(i);
    if(!result[i].every(v=>Number.isFinite(v) && Math.abs(v)<=1e12)) throw new Error("Bone hierarchy exceeds numeric limits");
    return result[i];
  }
  model.bones.forEach((_,i)=>resolve(i));
  return result;
}
const numeric = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && Math.abs(v)<=1e6;
const integer = (v: unknown, max: number, min=0): v is number => typeof v === "number" && Number.isInteger(v) && v>=min && v<=max;
function vector(v: unknown, length: number): v is number[] { return Array.isArray(v) && v.length===length && v.every(numeric); }
function unpack(chunks: number[][], max: number): number[] {
  if(!Array.isArray(chunks) || chunks.length>Math.ceil(max/1500) || chunks.some(chunk=>!Array.isArray(chunk) || chunk.length>1500 || !chunk.every(numeric))) throw new Error("Invalid model buffer");
  const values=chunks.flat();
  if(values.length>max) throw new Error("Model buffer exceeds playback limits");
  return values;
}
export function prepareModel(model: AnimationModel): PreparedModel {
  if(!model || model.schema_version!==1 || model.read_only!==true || !Array.isArray(model.bones) || !model.bones.length || model.bones.length>512
    || !Array.isArray(model.meshes) || !model.meshes.length || model.meshes.length>128) throw new Error("Invalid animation model");
  const tags=new Set<number>();
  model.bones.forEach((bone,i)=>{
    if(!bone || bone.index!==i || !integer(bone.tag,65535) || tags.has(bone.tag) || !integer(bone.parent,model.bones.length-1,-1)
      || !vector(bone.translation,3) || !vector(bone.rotation,4) || !vector(bone.scale,3) || bone.scale.some(v=>Math.abs(v)<1e-6)) throw new Error("Invalid skeleton binding");
    tags.add(bone.tag);
  });
  const bind=world(model,model.bones.map(b=>trs(b.translation,b.rotation,b.scale)));
  let vertices=0, triangles=0;
  const meshes=model.meshes.map(raw=>{
    if(!raw || typeof raw.skin!=="boolean" || !integer(raw.rigid_bone,model.bones.length-1)) throw new Error("Invalid mesh binding");
    const positions=unpack(raw.positions,90000), faces=unpack(raw.triangles,120000), weights=unpack(raw.weights,120000), indices=unpack(raw.indices,120000), count=positions.length/3;
    vertices+=count; triangles+=faces.length/3;
    if(!count || !Number.isInteger(count) || !faces.length || faces.length%3 || faces.some(v=>!integer(v,count-1)) || vertices>30000 || triangles>40000
      || (raw.skin ? weights.length!==count*4 || indices.length!==count*4 || weights.some(v=>v<0||v>1) || indices.some(v=>!integer(v,model.bones.length-1)) : weights.length!==0 || indices.length!==0)) throw new Error("Invalid mesh vertex/skin buffers");
    if(raw.skin) for(let i=0;i<weights.length;i+=4) if(Math.abs(weights.slice(i,i+4).reduce((a,b)=>a+b,0)-1)>2.001/255) throw new Error("Invalid vertex weight total");
    return {skin:raw.skin,rigid:raw.rigid_bone,positions,triangles:faces,weights,indices};
  });
  if(vertices!==model.vertex_count || triangles!==model.triangle_count) throw new Error("Model buffer counts do not match");
  const low=[Infinity,Infinity,Infinity], high=[-Infinity,-Infinity,-Infinity];
  meshes.forEach(mesh=>{ for(let i=0;i<mesh.positions.length;i+=3) {
    const local=mesh.positions.slice(i,i+3), p=mesh.skin ? local : transform(bind[mesh.rigid],local);
    p.forEach((v,j)=>{low[j]=Math.min(low[j],v); high[j]=Math.max(high[j],v);});
  }});
  return {model,bind,inverse:bind.map(inverse),meshes,center:low.map((v,i)=>(v+high[i])/2),radius:Math.max(.01,Math.hypot(...high.map((v,i)=>v-low[i]))/2)};
}
function slerp(a: number[], b: number[], t: number): number[] {
  const al=Math.hypot(...a), bl=Math.hypot(...b);
  if(al<1e-9 || bl<1e-9) throw new Error("Zero animation quaternion");
  a=a.map(v=>v/al); b=b.map(v=>v/bl);
  let dot=a.reduce((v,n,i)=>v+n*b[i],0);
  if(dot<0) { b=b.map(v=>-v); dot=-dot; }
  const angle=Math.acos(Math.min(1,dot));
  const q=angle<1e-5 ? a.map((v,i)=>v+(b[i]-v)*t) : a.map((v,i)=>(v*Math.sin((1-t)*angle)+b[i]*Math.sin(t*angle))/Math.sin(angle));
  const length=Math.hypot(...q); return q.map(v=>v/length);
}
function quaternionProduct(a: number[], b: number[]): number[] {
  const [x,y,z,w]=a, [u,v,s,t]=b;
  const q=[w*u+x*t+y*s-z*v,w*v-x*s+y*t+z*u,w*s+x*v-y*u+z*t,w*t-x*u-y*v-z*s];
  const length=Math.hypot(...q);
  if(length<1e-9) throw new Error("Invalid composed root rotation");
  return q.map(v=>v/length);
}
export function poseModel(prepared: PreparedModel, animation: AnimationPacket, time: number, layer: number, bindPose=false, rootMotion=false) {
  const {model}=prepared;
  const locals=model.bones.map(b=>({t:[...b.translation],q:[...b.rotation],s:[...b.scale]}));
  const tags=new Map(model.bones.map((b,i)=>[b.tag,i]));
  const seen=new Set<string>(); let matched=0, missing=0, unsupported=0;
  let rootTranslation=[0,0,0], rootRotation=[0,0,0,1];
  const rootIndex=tags.get(0);
  const hi=Math.max(1,animation.times.findIndex(value=>value>=Math.max(0,Math.min(animation.duration,time))));
  const lo=hi-1, t=Math.max(0,Math.min(1,(time-animation.times[lo])/(animation.times[hi]-animation.times[lo])));
  for(const track of animation.tracks.filter(track=>track.layer===layer)) {
    const rootTrack=track.track===5 || track.track===6;
    // CodeWalker Renderable.UpdateAnim uses Unk0 for expression remapping,
    // not as a disable bit for ordinary TRS/root channels. Retail locomotion
    // routinely carries nonzero Unk0. Expression tracks remain unsupported.
    if(!integer(track.flags,255)) throw new Error("Invalid animation channel flags");
    if(![0,1,2].includes(track.track) && !(rootTrack && rootMotion && track.bone_tag===0 && rootIndex!==undefined && model.bones[rootIndex].parent===-1)) { unsupported++; continue; }
    const index=tags.get(track.bone_tag);
    if(index===undefined) { missing++; continue; }
    const key=`${index}:${track.track}`;
    if(seen.has(key)) throw new Error("Ambiguous animation channels target the same bone/track");
    seen.add(key); matched++;
    if(bindPose) continue;
    const a=track.values.slice(lo*4,lo*4+4), b=track.values.slice(hi*4,hi*4+4);
    if(track.track===5) rootTranslation=a.slice(0,3).map((v,i)=>v+(b[i]-v)*t);
    else if(track.track===6) rootRotation=slerp(a,b,t);
    else if(track.track===1) locals[index].q=slerp(a,b,t);
    else locals[index][track.track===0 ? "t" : "s"]=a.slice(0,3).map((v,i)=>v+(b[i]-v)*t);
  }
  if(!matched && !bindPose) throw new Error("No supported channels match this skeleton's bone tags");
  // Pinned CodeWalker Renderable applies sampled root motion to tag 0 after
  // local channels. Recompute per frame: never integrate deltas across seeks.
  if(rootMotion && !bindPose && rootIndex!==undefined && model.bones[rootIndex].parent===-1) {
    locals[rootIndex].t=transform(trs(rootTranslation,rootRotation,[1,1,1]),locals[rootIndex].t);
    locals[rootIndex].q=quaternionProduct(rootRotation,locals[rootIndex].q);
  }
  const matrices=world(model,locals.map(b=>trs(b.t,b.q,b.s)));
  const skin=matrices.map((m,i)=>multiply(m,prepared.inverse[i]));
  const meshes=prepared.meshes.map(mesh=>{
    const positions: number[]=[];
    for(let i=0;i<mesh.positions.length;i+=3) {
      const point=mesh.positions.slice(i,i+3);
      if(!mesh.skin) positions.push(...transform(matrices[mesh.rigid],point));
      else {
        const value=[0,0,0], base=i/3*4;
        for(let k=0;k<4;k++) { const weight=mesh.weights[base+k]; if(!weight) continue;
          transform(skin[mesh.indices[base+k]],point).forEach((v,j)=>value[j]+=v*weight);
        }
        if(!value.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e12)) throw new Error("Animated mesh exceeds numeric limits");
        positions.push(...value);
      }
    }
    return {positions,triangles:mesh.triangles};
  });
  return {meshes,bones:matrices.map(m=>[m[3],m[7],m[11]]),matched,missing,unsupported};
}
