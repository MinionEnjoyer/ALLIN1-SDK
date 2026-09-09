import { multiply, poseModel, prepareModel, transform, type AnimationModel, type PreparedModel } from "./animationPose";
import type { AnimationPacket } from "./NativeAnimationView";

export type SightAttachment = {
  component: string; component_type: string; entry: string; native_sha256: string;
  parent_bone: string; child_bone: string; parent_index: number; child_index: number;
  packet: AnimationModel; scope: string;
};
const bind: AnimationPacket = {schema_version:1,read_only:true,selected:null,duration:1,sampled:false,scope:"Bind pose",choices:[],times:[0,1],tracks:[]};

export function prepareAttachment(parent: PreparedModel, attachment: SightAttachment) {
  if(!attachment || !/^[a-f0-9]{64}$/.test(attachment.native_sha256)
    || !Number.isInteger(attachment.parent_index) || !Number.isInteger(attachment.child_index)
    || parent.model.bones[attachment.parent_index]?.name!==attachment.parent_bone
    || attachment.packet?.bones?.[attachment.child_index]?.name!==attachment.child_bone)
    throw new Error("Invalid declared attachment frames");
  const child=prepareModel(attachment.packet,"sight");
  if(parent.model.vertex_count+child.model.vertex_count>120000 || parent.model.triangle_count+child.model.triangle_count>180000)
    throw new Error("Sight assembly exceeds its geometry budget");
  const pose=poseModel(child,bind,0,0,true);
  return {attachment,child,pose};
}

export function assemblePose(parent: ReturnType<typeof poseModel>, child: ReturnType<typeof prepareAttachment>, hidden: number[]=[]) {
  if(hidden.length>128||hidden.some(i=>!Number.isInteger(i)||i<0||i>=child.pose.meshes.length))throw new Error("Invalid hidden component mesh");
  const matrix=multiply(parent.matrices[child.attachment.parent_index],child.child.inverse[child.attachment.child_index]);
  const meshes=child.pose.meshes.filter((_,i)=>!hidden.includes(i)).map(mesh=>{
    const positions:number[]=[];
    for(let i=0;i<mesh.positions.length;i+=3) {
      const p=transform(matrix,mesh.positions.slice(i,i+3));
      if(!p.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6))throw new Error("Attachment transform exceeds numeric limits");
      positions.push(...p);
    }
    return {...mesh,positions};
  });
  return {...parent,meshes:[...parent.meshes,...meshes]};
}

export const attachmentIdentity = (a: SightAttachment|null|undefined) => a ? {
  component:a.component,entry:a.entry,native_sha256:a.native_sha256,xml_sha256:a.packet.source_sha256,
  drawable:a.packet.selected,lod:a.packet.lod,parent_bone:a.parent_bone,child_bone:a.child_bone,
} : null;
