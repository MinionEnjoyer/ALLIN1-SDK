import {useState} from "react";

export type DrawableCandidate={source:string;sha256:string;drawable:number;name:string;bones:number};
export type RigBinding={model:string;drawable:number;rig:string;rig_drawable:number;model_sha256:string;rig_sha256:string};
export default function SharedRigBindings({candidates,bindings,locked,onChange,inspectionAction="Inspect data"}:{candidates:DrawableCandidate[];bindings:RigBinding[];locked:boolean;onChange:(value:RigBinding[])=>void;inspectionAction?:string}) {
  const [model,setModel]=useState(""),[rig,setRig]=useState("");
  const models=candidates.filter(row=>row.source.startsWith("package:")),rigs=candidates.filter(row=>row.bones>0&&row.bones<=512);
  const key=(row:DrawableCandidate)=>JSON.stringify([row.source,row.drawable,row.sha256]);
  const target=models.find(row=>key(row)===model),skeleton=rigs.find(row=>key(row)===rig);
  const label=(row:DrawableCandidate)=>`${row.source} · drawable ${row.drawable} (${row.name}) · ${row.bones} bones`;
  return <details className="asset-validation"><summary>Explicit shared-rig context · {bindings.length} selected</summary><div className="asset-validation-body">
    <p>Inspect the package and optional comparison folder to discover drawable owners. Choose each intended rig explicitly; no filename, first dictionary item or load-order winner is inferred. This checks static compatibility, not whether the game will use that rig.</p>
    <fieldset disabled={locked}><legend>Bind one package drawable to one supplied skeleton</legend>
      <label>Model drawable<select value={model} onChange={e=>setModel(e.target.value)}><option value="">Choose package drawable</option>{models.map(row=><option key={key(row)} value={key(row)}>{label(row)}</option>)}</select></label>
      <label>Shared skeleton drawable<select value={rig} onChange={e=>setRig(e.target.value)}><option value="">Choose exact skeleton</option>{rigs.map(row=><option key={key(row)} value={key(row)}>{label(row)}</option>)}</select></label>
      <button disabled={!target||!skeleton||bindings.length>=128} onClick={()=>{if(target&&skeleton)onChange([...bindings.filter(row=>row.model!==target.source||row.drawable!==target.drawable),{model:target.source,drawable:target.drawable,rig:skeleton.source,rig_drawable:skeleton.drawable,model_sha256:target.sha256,rig_sha256:skeleton.sha256}]);}}>Use selected shared rig</button>
      {bindings.map((row,i)=><section key={`${row.model}:${row.drawable}`}><p>{row.model} · drawable {row.drawable} → {row.rig} · drawable {row.rig_drawable}</p><p>Model {row.model_sha256}<br/>Rig {row.rig_sha256}</p><button onClick={()=>onChange(bindings.filter((_,n)=>n!==i))}>Remove shared rig {i+1}</button></section>)}
    </fieldset>
    {bindings.length>0&&<p>Run {inspectionAction} again to validate these bindings and enable reviewed export.</p>}
  </div></details>;
}
