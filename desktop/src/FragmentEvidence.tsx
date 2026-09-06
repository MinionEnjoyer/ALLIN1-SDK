export type FragmentChild = {
  source?:string; location:string; child_index:number; physics_lod:string; null_child:boolean;
  bone_tag:number|null; bone_indices:number[]; group_index:number|null;
  physics_matrix:number[][]|null; position_offset:number[]|null;
  drawables:{variant:string; matrix:number[][]|null; static_status:string;
    lod_metrics:{drawable:number;lod:string;vertices:number;triangles:number;complete:boolean}[]}[];
};
const index=(value:unknown,max:number)=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0&&value<=max;
const vector=(value:unknown,length:number)=>Array.isArray(value)&&value.length===length&&value.every(v=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<=1e12);
const matrix=(value:unknown,width:number)=>value===null||(Array.isArray(value)&&value.length===4&&value.every(row=>vector(row,width)));
export function validFragmentChildren(value:unknown):value is FragmentChild[]{
  return Array.isArray(value)&&value.length<=256&&value.every(row=>row&&typeof row.location==="string"&&(row.source===undefined||typeof row.source==="string")
    &&index(row.child_index,63)&&["LOD1","LOD2","LOD3"].includes(row.physics_lod)&&typeof row.null_child==="boolean"
    &&(row.bone_tag===null||index(row.bone_tag,65535))&&(row.group_index===null||index(row.group_index,65535))
    &&Array.isArray(row.bone_indices)&&row.bone_indices.length<=512&&row.bone_indices.every((i:unknown)=>index(i,511))
    &&matrix(row.physics_matrix,4)&&(row.position_offset===null||vector(row.position_offset,3))
    &&Array.isArray(row.drawables)&&row.drawables.length<=2&&row.drawables.every((item:FragmentChild["drawables"][number])=>item
      &&["Drawable","Drawable2"].includes(item.variant)&&matrix(item.matrix,3)&&["pass","warning","fail","incomplete"].includes(item.static_status)
      &&Array.isArray(item.lod_metrics)&&item.lod_metrics.length<=4&&item.lod_metrics.every(lod=>lod&&index(lod.drawable,0)&&typeof lod.lod==="string"
        &&index(lod.vertices,Number.MAX_SAFE_INTEGER)&&index(lod.triangles,Number.MAX_SAFE_INTEGER)&&typeof lod.complete==="boolean")));
}
function Matrix({rows,label}:{rows:number[][]|null;label:string}){
  return rows?<table><caption>{label}</caption><tbody>{rows.map((row,i)=><tr key={i}>{row.map((v,j)=><td key={j}>{v.toPrecision(6)}</td>)}</tr>)}</tbody></table>:<p>{label}: unavailable</p>;
}
export default function FragmentEvidence({children,scope}:{children:FragmentChild[];scope:string}){
  return <details><summary>Fragment physics-child evidence · {children.length}</summary><p>{scope}</p>
    {children.map((row,i)=><details key={i}><summary>{row.source??"Fragment"} · {row.physics_lod} child {row.child_index}{row.null_child?" · null slot":""}</summary>
      <p>{row.location}<br/>Bone tag {row.bone_tag??"unavailable"} → {row.bone_indices.length===1?`bone ${row.bone_indices[0]}`:row.bone_indices.length?`ambiguous: ${row.bone_indices.join(", ")}`:"unresolved"}<br/>Group index {row.group_index??"unavailable"}</p>
      <p>Authored position offset: {row.position_offset?.join(", ")??"unavailable"}. Kept separate; no composed game transform is inferred.</p>
      <Matrix rows={row.physics_matrix} label="Physics matrix · serialized 4×4 rows"/>
      {row.drawables.map((item,j)=><details key={j}><summary>{item.variant} · static {item.static_status}</summary>
        <Matrix rows={item.matrix} label="Drawable matrix · serialized four 3-value vectors"/>
        <table><caption>Child geometry counts · {item.variant}</caption><thead><tr><th>LOD</th><th>Vertices</th><th>Triangles</th><th>Coverage</th></tr></thead><tbody>{item.lod_metrics.map((lod,k)=><tr key={k}><td>{lod.lod}</td><td>{lod.vertices}</td><td>{lod.triangles}</td><td>{lod.complete?"Complete":"Partial"}</td></tr>)}</tbody></table>
      </details>)}
    </details>)}
  </details>;
}
