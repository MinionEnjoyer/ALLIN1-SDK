import type {AssemblyBinding} from "./AssemblyBindings";
import {validAssemblyRotation} from "./AssemblyBindings";

export type AssemblyRow=AssemblyBinding & {binding_sha256:string;status:"checked"|"fail"|"not_checked";message:string;
  parent_anchor_matrix:number[][]|null;child_anchor_matrix:number[][]|null;child_to_parent_matrix:number[][]|null;
  relative_anchor_error:number|null;orientation_reversing:boolean|null};
export function validAssemblyEvidence(value:unknown):value is AssemblyRow[] {
  const sha=(v:unknown)=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v);
  const matrix=(v:unknown)=>v===null||(Array.isArray(v)&&v.length===4&&v.every(row=>Array.isArray(row)&&row.length===4&&row.every(n=>typeof n==="number"&&Number.isFinite(n)&&Math.abs(n)<=1e12)));
  return Array.isArray(value)&&value.length<=32&&value.every((row:AssemblyRow)=>row&&
    [row.parent,row.child,row.parent_bone,row.child_bone,row.message].every(v=>typeof v==="string")&&
    [row.parent_drawable,row.child_drawable].every(v=>Number.isSafeInteger(v)&&v>=0)&&
    [row.parent_sha256,row.child_sha256,row.binding_sha256].every(sha)&&["checked","fail","not_checked"].includes(row.status)&&
    Array.isArray(row.offset)&&row.offset.length===3&&row.offset.every(v=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<=1e6)&&
    (row.rotation===undefined||validAssemblyRotation(row.rotation))&&
    [row.parent_anchor_matrix,row.child_anchor_matrix,row.child_to_parent_matrix].every(matrix)&&
    (row.relative_anchor_error===null||(typeof row.relative_anchor_error==="number"&&Number.isFinite(row.relative_anchor_error)&&row.relative_anchor_error>=0&&row.relative_anchor_error<=1e-7))&&
    (row.orientation_reversing===null||typeof row.orientation_reversing==="boolean")&&
    (row.status!=="checked"||(row.parent_anchor_matrix!==null&&row.child_anchor_matrix!==null&&row.child_to_parent_matrix!==null&&row.relative_anchor_error!==null&&row.orientation_reversing!==null)));
}
export default function AssemblyEvidence({rows,scope}:{rows:AssemblyRow[];scope:string}) {
  return <details><summary>Declared assembly evidence · {rows.length} pairs</summary><p>{scope}</p>
    {rows.map((row,i)=><details key={row.binding_sha256}><summary>Pair {i+1} · {row.status.replaceAll("_"," ")}</summary>
      <p>{row.parent} ({row.parent_bone||"origin"}) → {row.child} ({row.child_bone||"origin"})</p><p>{row.message}</p>
      {row.rotation&&<p>Declared local rotation XYZW: {row.rotation.join(", ")}. Translation is in parent-anchor axes.</p>}
      {row.child_to_parent_matrix&&<table><caption>Declared child-to-parent matrix · pair {i+1}</caption><tbody>{row.child_to_parent_matrix.map((values,r)=><tr key={r}>{values.map((v,c)=><td key={c}>{v.toPrecision(6)}</td>)}</tr>)}</tbody></table>}
      {row.relative_anchor_error!==null&&<p>Relative anchor agreement error: {row.relative_anchor_error}. Orientation reversing: {String(row.orientation_reversing)}.</p>}
    </details>)}
  </details>;
}
