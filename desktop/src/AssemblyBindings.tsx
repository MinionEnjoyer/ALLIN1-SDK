import {useState} from "react";
import type {DrawableCandidate} from "./SharedRigBindings";

export type AssemblyBinding={parent:string;parent_drawable:number;parent_sha256:string;parent_bone:string;
  child:string;child_drawable:number;child_sha256:string;child_bone:string;offset:number[];rotation?:number[]};
export const validAssemblyRotation=(value:unknown):value is number[]=>Array.isArray(value)&&value.length===4&&value.every(v=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<=1)&&Math.abs(Math.hypot(...value)-1)<=1e-6;
export default function AssemblyBindings({candidates,bindings,locked,onChange,inspectionAction="Inspect data"}:{
  candidates:DrawableCandidate[];bindings:AssemblyBinding[];locked:boolean;onChange:(value:AssemblyBinding[])=>void;inspectionAction?:string}) {
  const [parent,setParent]=useState(""),[child,setChild]=useState("");
  const [parentBone,setParentBone]=useState(""),[childBone,setChildBone]=useState("");
  const [offset,setOffset]=useState(["0","0","0"]);
  const [rotation,setRotation]=useState(["0","0","0","1"]);
  const key=(row:DrawableCandidate)=>JSON.stringify([row.source,row.drawable,row.sha256]);
  const p=candidates.find(row=>key(row)===parent),c=candidates.find(row=>key(row)===child);
  const values=offset.map(Number),validOffset=offset.every(v=>v.trim()!=="")&&values.every(v=>Number.isFinite(v)&&Math.abs(v)<=1e6);
  const quaternion=rotation.map(Number),validRotation=rotation.every(v=>v.trim()!=="")&&validAssemblyRotation(quaternion);
  return <details className="asset-validation"><summary>Declared attachment placement · {bindings.length} pairs</summary><div className="asset-validation-body">
    <p>Declare independent bind-frame pairs. Blank bone names explicitly mean model origin; offsets are in the parent anchor's axes. This does not discover engine conventions, animated placement, clipping or fragment physics.</p>
    <fieldset className="assembly-binding-fields" disabled={locked}><legend>Exact parent and child frames</legend>
      <label>Assembly parent<select value={parent} onChange={e=>setParent(e.target.value)}><option value="">Choose parent owner</option>{candidates.map(row=><option key={key(row)} value={key(row)}>{row.source} · drawable {row.drawable}</option>)}</select></label>
      <label>Parent anchor bone<input value={parentBone} maxLength={160} onChange={e=>setParentBone(e.target.value)}/></label>
      <label>Assembly child<select value={child} onChange={e=>setChild(e.target.value)}><option value="">Choose child owner</option>{candidates.map(row=><option key={key(row)} value={key(row)}>{row.source} · drawable {row.drawable}</option>)}</select></label>
      <label>Child anchor bone<input value={childBone} maxLength={160} onChange={e=>setChildBone(e.target.value)}/></label>
      {offset.map((v,i)=><label key={i}>Local offset {"XYZ"[i]}<input type="number" step="any" min={-1e6} max={1e6} value={v} onChange={e=>setOffset(offset.map((n,j)=>i===j?e.target.value:n))}/></label>)}
      <details><summary>Local rotation offset · XYZW quaternion</summary><p>Rotation precedes translation in the local transform. Translation stays in parent-anchor axes. Enter a unit quaternion; values are never silently normalized.</p>
        {rotation.map((v,i)=><label key={i}>Local rotation {"XYZW"[i]}<input type="number" step="any" min={-1} max={1} value={v} onChange={e=>setRotation(rotation.map((n,j)=>i===j?e.target.value:n))}/></label>)}
        <button onClick={()=>setRotation(["0","0","0","1"])}>Reset local rotation</button>
        {!validRotation&&<p role="alert">Enter four finite unit-quaternion components (XYZW).</p>}
      </details>
      <button disabled={!p||!c||parent===child||!validOffset||!validRotation||bindings.length>=32} onClick={()=>{if(p&&c)onChange([...bindings,{parent:p.source,parent_drawable:p.drawable,parent_sha256:p.sha256,parent_bone:parentBone.trim(),child:c.source,child_drawable:c.drawable,child_sha256:c.sha256,child_bone:childBone.trim(),offset:values,...(quaternion.some((v,i)=>v!==[0,0,0,1][i])?{rotation:quaternion}:{})}]);}}>Add declared assembly pair</button>
      {bindings.map((row,i)=><section key={i}><p>{row.parent} · {row.parent_bone||"model origin"} → {row.child} · {row.child_bone||"model origin"}<br/>Local offset: {row.offset.join(", ")}<br/>Local rotation XYZW: {(row.rotation??[0,0,0,1]).join(", ")}</p><button onClick={()=>onChange(bindings.filter((_,n)=>n!==i))}>Remove assembly pair {i+1}</button></section>)}
    </fieldset>
    {!!bindings.length&&<p>Run {inspectionAction} again to check exact hashes, transforms and anchor agreement before report export.</p>}
  </div></details>;
}
