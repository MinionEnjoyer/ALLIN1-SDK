import { useEffect, useMemo, useRef, useState } from "react";
import type { DesktopClient } from "./types";
import type { WeaponSnapshot } from "./WeaponWorkbench";
import { prepareModel, poseModel, type AnimationModel } from "./animationPose";
import { validAnimationPacket, type AnimationPacket } from "./NativeAnimationView";
import SliderField from "./SliderField";
import { alignedRig, candidateRig, defaultRig, pick, profiles, project, type Rig, type Vec } from "./weaponSightMath";
import { createSightRenderer } from "./weaponSightRenderer";
import { assemblePose, prepareAttachment, attachmentIdentity, type SightAttachment } from "./weaponSightAssembly";
import "./weapon-sight.css";

type AssetPacket<T> = {kind: string; weapon: string; source: string; entry: string; native_sha256: string; edition: string; revision: number|null; packet: T; attachment?: SightAttachment|null};
const bindAnimation: AnimationPacket = {schema_version:1,read_only:true,selected:null,duration:1,sampled:false,scope:"Bind pose",choices:[],times:[0,1],tracks:[]};
const save = (name: string, blob: Blob) => { const url=URL.createObjectURL(blob), a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); };

export default function WeaponSightBench({client,snapshot,draft,locked,onChange,onReview}: {
  client: DesktopClient; snapshot: WeaponSnapshot; draft: Record<string,string>; locked: boolean;
  onChange: (values: Record<string,string>)=>void; onReview: ()=>void;
}) {
  const assets=snapshot.native_preview?.parts.find(p=>p.kind==="weapon")?.assets??[];
  const [entry,setEntry]=useState(assets.length===1 ? assets[0].path : "");
  const [edition,setEdition]=useState(["enhanced","legacy"].includes(snapshot.project.edition.toLowerCase()) ? snapshot.project.edition.toLowerCase() : "");
  const [lod,setLod]=useState("High"),[drawable,setDrawable]=useState("");
  const [component,setComponent]=useState(""),[componentEntry,setComponentEntry]=useState("");
  const components=snapshot.native_preview?.parts.filter(p=>p.kind==="component")??[];
  const componentAssets=components.find(p=>p.name===component)?.assets??[];
  const [animationEntry,setAnimationEntry]=useState(""),[filter,setFilter]=useState("");
  const [model,setModel]=useState<AssetPacket<AnimationModel>|null>(null),[animation,setAnimation]=useState<AssetPacket<AnimationPacket>|null>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false);
  const generation=useRef(0), job=useRef("");
  useEffect(()=>()=>{generation.current++;if(job.current) void client.cancelJob(job.current);},[client]);
  const load=async(action:"model"|"animation",selection?:string)=>{
    const version=++generation.current;let finished=false;setBusy(true);setError("");
    if(action==="model") setModel(null);else setAnimation(null);
    try {
      const started=await client.startJob("inspect_weapon_workbench",{
        ...(snapshot.workspace?{workspace:snapshot.workspace}:{source:snapshot.source}),weapon:snapshot.selected_weapon,
        expected_revision:snapshot.revision,sight_action:action,edition,entry:action==="model"?entry:animationEntry,
        ...(action==="model"?{lod,...(drawable?{drawable}:{}),...(component?{component,component_entry:componentEntry}:{})}:selection?{selection}:{}),
      },`sight-${version}`,message=>{
        if(!message.terminal || version!==generation.current) return;
        finished=true;job.current="";setBusy(false);
        try {
          if(message.operation==="error") throw new Error(String(message.payload.message??"Sight inspection failed"));
          const result=message.payload.result as AssetPacket<AnimationModel|AnimationPacket>;
          if(!result || result.kind!==`weapon_sight_${action}` || result.weapon!==snapshot.selected_weapon || result.source!==snapshot.source
            || result.entry!==(action==="model"?entry:animationEntry) || result.edition!==edition || result.revision!==snapshot.revision
            || !/^[a-f0-9]{64}$/.test(result.native_sha256)) throw new Error("Sight response does not match the selected package asset");
          if(action==="model") {
            if(component ? result.attachment?.component!==component || result.attachment?.entry!==componentEntry : result.attachment!=null)
              throw new Error("Sight attachment does not match the selected component");
            setModel(result as AssetPacket<AnimationModel>);
          }
          else {if(!validAnimationPacket(result.packet as AnimationPacket)) throw new Error("Invalid animation samples");setAnimation(result as AssetPacket<AnimationPacket>);}
        }catch(reason){setError(String(reason));}
      });
      if(version!==generation.current) {if(!finished) void client.cancelJob(started.job_id);return;}
      if(!finished) job.current=started.job_id;
    } catch(reason){if(version===generation.current){setError(String(reason));setBusy(false);}}
  };
  const stop=()=>{generation.current++;if(job.current) void client.cancelJob(job.current);job.current="";setBusy(false);setError("Inspection cancelled.");};
  return <section className="weapon-sight" aria-label="Offline sight bench">
    <header><h4>Offline sight bench <small>Experimental</small></h4><strong>{snapshot.selected_weapon}</strong><p>Frozen geometry, live camera trials and sampled firing/reload motion. No game launch or installation.</p></header>
    <p className="sight-boundary">Model-space reference—not a verified GTA aiming camera. No sway or runtime IK is simulated. One exact bundled component can follow its declared mount; no hands or detached-magazine simulation.</p>
    <div className="sight-fields">
      <label>Exact body asset<select value={entry} disabled={busy||locked} onChange={e=>{setEntry(e.target.value);setModel(null);setDrawable("");}}><option value="">Choose base or high-detail model</option>{assets.map(a=><option key={a.path}>{a.path}</option>)}</select></label>
      <label>Decoder edition<select value={edition} disabled={busy||locked} onChange={e=>{setEdition(e.target.value);setModel(null);setAnimation(null);}}><option value="">Choose edition</option><option value="enhanced">Enhanced</option><option value="legacy">Legacy</option></select></label>
      <label>Sight LOD<select value={lod} disabled={busy||locked} onChange={e=>{setLod(e.target.value);setModel(null);}}>{["High","Medium","Low","VeryLow"].map(v=><option key={v}>{v}</option>)}</select></label>
      <label>Optional component<select value={component} disabled={busy||locked} onChange={e=>{setComponent(e.target.value);setComponentEntry("");setModel(null);}}><option value="">Body only</option>{components.map(p=><option key={p.id} value={p.name} disabled={!p.assets.length||p.attach_bones?.length!==1}>{p.name}{!p.assets.length?" · not bundled":""}</option>)}</select></label>
      {component&&<label>Exact component asset<select value={componentEntry} disabled={busy||locked} onChange={e=>{setComponentEntry(e.target.value);setModel(null);}}><option value="">Choose base or high-detail component</option>{componentAssets.map(a=><option key={a.path}>{a.path}</option>)}</select></label>}
      <button disabled={!entry||!edition||(!!component&&!componentEntry)||busy||locked} onClick={()=>void load("model")}>Load sight model</button>
      {busy&&<button onClick={stop}>Cancel sight inspection</button>}
    </div>
    {error&&<p role="alert">{error}</p>}
    {model?.packet.selected===null&&<label>Exact drawable<select value={drawable} onChange={e=>setDrawable(e.target.value)}><option value="">Choose drawable, then load</option>{model.packet.drawables.map(d=><option key={d.key} value={d.key}>{d.name}</option>)}</select></label>}
    <details><summary>Firing / reload inspection</summary><p>Choose a bundled YCD explicitly. Matching bone tags do not prove this is the clip GTA uses. One layer at a time; no blends or expression tracks.</p>
      <div className="sight-fields"><label>Filter bundled animations<input value={filter} onChange={e=>setFilter(e.target.value)}/></label>
      <label>Animation dictionary<select value={animationEntry} disabled={busy||locked} onChange={e=>{setAnimationEntry(e.target.value);setAnimation(null);}}><option value="">Bind pose only</option>{(snapshot.animation_assets??[]).filter(p=>p===animationEntry||p.toLowerCase().includes(filter.toLowerCase())).map(p=><option key={p}>{p}</option>)}</select></label>
      <button disabled={!model||!animationEntry||busy||locked} onClick={()=>void load("animation")}>Read animation clips</button></div>
      {animation&&<label>Exact clip<select value={animation.packet.selected??""} disabled={busy||locked} onChange={e=>void load("animation",e.target.value)}><option value="">Choose clip</option>{animation.packet.choices.map(c=><option key={c.key} value={c.key} disabled={!!c.error}>{c.name} · {c.key}{c.error?` · unavailable: ${c.error}`:""}</option>)}</select></label>}
    </details>
    {model&&<SightEditor key={`${model.native_sha256}:${model.packet.lod}:${model.packet.selected}:${JSON.stringify(attachmentIdentity(model.attachment))}`} model={model} animation={animation} snapshot={snapshot} draft={draft} locked={locked||busy} onChange={onChange} onReview={onReview}/>}
  </section>;
}

export function SightEditor({model,animation,snapshot,draft,locked,onChange,onReview}:{model:AssetPacket<AnimationModel>;animation:AssetPacket<AnimationPacket>|null;snapshot:WeaponSnapshot;draft:Record<string,string>;locked:boolean;onChange:(values:Record<string,string>)=>void;onReview:()=>void}) {
  const prepared=useMemo(()=>{try{return prepareModel(model.packet,"sight");}catch(e){return String(e);}},[model]);
  const child=useMemo(()=>{try{return model.attachment&&typeof prepared!=="string"?prepareAttachment(prepared,model.attachment):null;}catch(e){return String(e);}},[model,prepared]);
  const [showAttachment,setShowAttachment]=useState(true);
  const [hiddenMeshes,setHiddenMeshes]=useState<number[]>([]);
  const initial=typeof prepared==="string"?{eye:[-.4,0,.1] as Vec,yaw:0,pitch:0,roll:0,fov:35}:defaultRig(prepared);
  const [reference,setReference]=useState<Rig>(initial),[profileId,setProfileId]=useState("base"),[view,setView]=useState("side");
  const [baseline,setBaseline]=useState(false),[time,setTime]=useState(0),[layer,setLayer]=useState(0),[bind,setBind]=useState(true);
  const [rear,setRear]=useState<Vec|null>(null),[front,setFront]=useState<Vec|null>(null),[mark,setMark]=useState("");
  const [relief,setRelief]=useState(.15),[error,setError]=useState(""),[skeleton,setSkeleton]=useState(false);
  const [viewZoom,setViewZoom]=useState(1);
  const [referenceText,setReferenceText]=useState("");
  const canvas=useRef<HTMLCanvasElement>(null), renderer=useRef<ReturnType<typeof createSightRenderer>|null>(null);
  const profile=profiles.find(p=>p.id===profileId)!;
  const original=snapshot.values?.values??{};
  const layers=[...new Set(animation?.packet.tracks.map(t=>t.layer)??[])];
  const chosenLayer=layers.includes(layer)?layer:layers[0]??0;
  useEffect(()=>{setTime(0);setBind(true);},[animation]);
  const sampledPose=useMemo(()=>{
    if(typeof prepared==="string")return prepared;
    if(typeof child==="string")return child;
    try{
      const pose=poseModel(prepared,animation?.packet.selected?animation.packet:bindAnimation,time,chosenLayer,bind||!animation?.packet.selected);
      return child&&showAttachment?assemblePose(pose,child,hiddenMeshes):pose;
    }
    catch(e){return String(e);}
  },[prepared,child,showAttachment,hiddenMeshes,animation,time,chosenLayer,bind]);
  const result=useMemo(()=>{
    if(typeof prepared==="string") return prepared;
    if(typeof sampledPose==="string")return sampledPose;
    try {
      const trial=candidateRig(reference,original,draft,profile),current=baseline?reference:trial;
      const center=prepared.center as Vec, radius=prepared.radius;
      const rig=view==="aim"?current:view==="side"?{eye:[center[0],center[1]-radius*3.5,center[2]] as Vec,yaw:90,pitch:0,roll:0,fov:35/viewZoom}
        :{eye:[center[0],center[1]-.01,center[2]+radius*3.5] as Vec,yaw:90,pitch:-89,roll:0,fov:35/viewZoom};
      return {pose:sampledPose,rig,current};
    } catch(e){return String(e);}
  },[prepared,sampledPose,reference,baseline,draft,original,profile,view,viewZoom]);
  useEffect(()=>{
    if(!canvas.current)return;
    const target=canvas.current;
    const lost=(event:Event)=>{event.preventDefault();setError("Graphics context lost. Reload the sight model to resume inspection.");};
    target.addEventListener("webglcontextlost",lost);
    try{renderer.current=createSightRenderer(canvas.current);setError("");}catch(e){setError(String(e));}
    return()=>{target.removeEventListener("webglcontextlost",lost);renderer.current?.dispose();renderer.current=null;};
  },[prepared]);
  useEffect(()=>{if(typeof sampledPose!=="string")try{renderer.current?.upload(sampledPose.meshes);}catch(e){setError(String(e));}},[sampledPose]);
  useEffect(()=>{if(typeof result!=="string")try{renderer.current?.draw(result.rig);}catch(e){setError(String(e));}},[result]);
  if(model.packet.view_unavailable||model.packet.binding_required||typeof prepared==="string") return <p role="alert">{model.packet.view_unavailable||model.packet.binding_required||(typeof prepared==="string"?prepared:"Model unavailable")}. No simplified geometry has been substituted.</p>;
  const projected=(p:Vec|null)=>p&&typeof result!=="string"?project(p,result.rig):null;
  const rp=projected(rear), fp=projected(front);
  const fields=(snapshot.camera_fields??[]).filter(f=>f.tag[0].toLowerCase()+f.tag.slice(1)===profile.position||f.tag[0].toLowerCase()+f.tag.slice(1)===profile.rotation);
  const changed=fields.some(f=>draft[f.key]!==original[f.key]);
  const evidence=()=>({schema_version:1,kind:"offline_sight_reference",qualification:"model_space_only",weapon:snapshot.selected_weapon,
    native_sha256:model.native_sha256,xml_sha256:model.packet.source_sha256,entry:model.entry,lod:model.packet.lod,drawable:model.packet.selected,edition:model.edition,
    reference,profile:profileId,rear,front,original,trial:draft,attachment:attachmentIdentity(model.attachment),show_attachment:showAttachment,hidden_component_meshes:hiddenMeshes,
    animation:animation?{native_sha256:animation.native_sha256,entry:animation.entry,selection:animation.packet.selected,time,layer:chosenLayer,bind}:null,
    mapping:"Metadata XYZ -> camera right/forward/up; rotation XYZ -> pitch/roll/yaw. Hypothesis only; FOV independent.",runtime_asset_use:"not_proven"});
  const loadReference=()=>{try{
    const parsed=JSON.parse(referenceText);
    if(parsed.schema_version!==1||parsed.kind!=="offline_sight_reference"||parsed.entry!==model.entry||parsed.native_sha256!==model.native_sha256||parsed.xml_sha256!==model.packet.source_sha256||parsed.weapon!==snapshot.selected_weapon||parsed.lod!==model.packet.lod||parsed.drawable!==model.packet.selected||parsed.edition!==model.edition
      ||!profiles.some(p=>p.id===parsed.profile)||!parsed.reference||!Array.isArray(parsed.reference.eye)||parsed.reference.eye.length!==3) throw new Error("Reference must match this exact weapon, edition, model and LOD");
    const expectedAttachment=attachmentIdentity(model.attachment),savedAttachment=parsed.attachment??null;
    if(expectedAttachment===null?savedAttachment!==null:!savedAttachment||Object.entries(expectedAttachment).some(([k,v])=>savedAttachment[k]!==v))throw new Error("Reference must match this exact attachment and mount");
    if(parsed.profile==="attached"&&model.attachment?.component_type!=="CWeaponComponentScopeInfo")throw new Error("Attached optic camera requires an assembled scope");
    if(parsed.show_attachment!==undefined&&typeof parsed.show_attachment!=="boolean")throw new Error("Invalid attachment visibility");
    const hidden=parsed.hidden_component_meshes??[];
    if(!Array.isArray(hidden)||hidden.length>128||hidden.some((i:unknown)=>!Number.isInteger(i)||Number(i)<0||Number(i)>=(model.attachment?.packet.meshes.length??0)))throw new Error("Invalid hidden component mesh");
    for(const k of ["rear","front"])if(parsed[k]!==null&&(!Array.isArray(parsed[k])||parsed[k].length!==3||!parsed[k].every((v:unknown)=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<100)))throw new Error("Invalid landmark");
    if(![...parsed.reference.eye,parsed.reference.yaw,parsed.reference.pitch,parsed.reference.roll,parsed.reference.fov].every((v:unknown)=>typeof v==="number"&&Number.isFinite(v))) throw new Error("Invalid camera");
    if(!parsed.original||typeof parsed.original!=="object")throw new Error("Missing reference metadata baseline");
    const savedProfile=profiles.find(p=>p.id===parsed.profile)!;
    for(const field of [savedProfile.position,savedProfile.rotation])for(const axis of "xyz"){
      const k=`weapon.${field}.${axis}`;
      if((k in original)!==(k in parsed.original)||(k in original&&typeof parsed.original[k]!=="string"))throw new Error("Reference camera fields do not match this weapon");
    }
    const rebased=candidateRig(parsed.reference,parsed.original,original,savedProfile);
    project([0,0,0],rebased);setReference(rebased);setProfileId(parsed.profile);setRear(parsed.rear);setFront(parsed.front);setShowAttachment(parsed.show_attachment??true);setHiddenMeshes(hidden);setError("");
  }catch(e){setError(String(e));}};
  return <div className="sight-editor">
    <div className="sight-toolbar"><label>View<select aria-label="View" value={view} onChange={e=>setView(e.target.value)}><option value="side">Side · identify sights</option><option value="top">Top · identify centerline</option><option value="aim">Frozen aim</option></select></label>
      <label>Metadata family<select aria-label="Metadata family" value={profileId} disabled={locked||changed} onChange={e=>setProfileId(e.target.value)}>{profiles.filter(p=>p.id!=="attached"||model.attachment?.component_type==="CWeaponComponentScopeInfo").map(p=><option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
      {model.attachment&&<label><input type="checkbox" checked={showAttachment} onChange={e=>setShowAttachment(e.target.checked)}/>Show selected component</label>}
      <label><input type="checkbox" checked={baseline} onChange={e=>setBaseline(e.target.checked)}/>Show saved baseline camera</label>
      <label><input type="checkbox" checked={skeleton} onChange={e=>setSkeleton(e.target.checked)}/>Bone overlay</label>
      <SliderField numeric label="Inspection zoom" value={viewZoom} min={.5} max={3} hardMin={.5} hardMax={3} step={.1} commitValidOnly disabled={view==="aim"} onChange={setViewZoom}/>
    </div>
    <p>Solid surfaces are depth-tested. Guides are an x-ray overlay. Marks stay fixed in model space; return to bind pose before measuring. View FOV is vertical. Measurements use the fixed 960 × 540 render, independent of display scaling. Transparent optic shaders and reticles are not reconstructed.</p>
    {model.attachment&&<p>{model.attachment.component} · {model.attachment.parent_bone} → {model.attachment.child_bone}. Child bind pose follows the parent mount; independent attachment motion is not simulated.</p>}
    {model.attachment&&<details><summary>Component geometry visibility · {hiddenMeshes.length} hidden</summary><p>Hide an individual lens surface to inspect the physical aperture. This affects this diagnostic view only, not the game model. No lens or reticle is auto-detected.</p>
      {model.attachment.packet.meshes.map((_,i)=><label key={i}><input type="checkbox" checked={!hiddenMeshes.includes(i)} onChange={e=>setHiddenMeshes(e.target.checked?hiddenMeshes.filter(n=>n!==i):[...hiddenMeshes,i])}/>{model.attachment!.packet.mesh_labels?.[i]??`Mesh ${i+1}`}</label>)}
    </details>}
    {(error||typeof result==="string")&&<p role="alert">{error||result as string}</p>}
    <div className="sight-canvas" onClick={e=>{
      if(!mark||!bind||typeof result==="string"||!canvas.current)return;
      const box=canvas.current.getBoundingClientRect();if(!box.width||!box.height)return;
      const point=pick(result.pose.meshes,result.rig,(e.clientX-box.left)*960/box.width,(e.clientY-box.top)*540/box.height,960,540);
      if(point){(mark==="rear"?setRear:setFront)(point);setMark("");setError("");}else setError("No mesh surface under the pointer. Pick an edge, then enter the aperture center coordinates.");
    }}>
      <canvas hidden={!!error||typeof result==="string"} ref={canvas} width={960} height={540} aria-label="Frozen weapon sight viewport" role="img"/>
      <svg viewBox="0 0 960 540" aria-label="Sight alignment guides"><path d="M460 270H500M480 250V290" stroke="white" strokeWidth="1"/>
        {rp&&fp&&<path d={`M${rp[0]} ${rp[1]}L${fp[0]} ${fp[1]}`} stroke="#f9cc75" strokeDasharray="5 4"/>}
        {[rp,fp].map((p,i)=>p&&<g key={i} stroke={i?"#ffbc69":"#65e1ca"}><circle cx={p[0]} cy={p[1]} r="7" fill="none"/><text x={p[0]+10} y={p[1]-10} fill="white" stroke="none">{i?"Front":"Rear"}</text></g>)}
        {skeleton&&typeof result!=="string"&&result.pose.bones.map((b,i)=>{const p=project(b as Vec,result.rig);return p&&<circle key={i} cx={p[0]} cy={p[1]} r="3" fill="#ea85d1"><title>{model.packet.bones[i].name}</title></circle>;})}
      </svg>
    </div>
    <output className="sight-measurement" aria-label="Sight measurement">{view!=="aim"?"Choose Frozen aim to measure projection.":rp&&fp?`Front − rear: ${(fp[0]-rp[0]).toFixed(2)} px horizontal / ${(fp[1]-rp[1]).toFixed(2)} px vertical. Front − camera center: ${(fp[0]-480).toFixed(2)} / ${(fp[1]-270).toFixed(2)} px.`:"Mark both sights to measure their projected separation; no marks are inferred."}</output>
    {animation?.packet.selected&&<div className="sight-fields"><label><input type="checkbox" checked={bind} onChange={e=>setBind(e.target.checked)}/>Freeze bind pose</label>
      <label>Motion layer<select value={chosenLayer} onChange={e=>setLayer(Number(e.target.value))}>{layers.map(v=><option key={v} value={v}>Layer {v}</option>)}</select></label>
      <SliderField numeric label="Frozen animation time" unit="seconds" value={time} min={0} max={animation.packet.duration} hardMin={0} hardMax={animation.packet.duration} endpoints={["0",animation.packet.duration.toFixed(3)]} step={animation.packet.duration/(animation.packet.times.length-1)} commitValidOnly onChange={v=>{setTime(v);setBind(false);}}/>
      <output>{typeof result!=="string"&&`${result.pose.matched} matched / ${result.pose.missing} missing / ${result.pose.unsupported} unsupported channels`}</output></div>}
    <div className="sight-columns"><section><h5>Reference camera · setup only</h5><p>Model XYZ coordinates; initial view faces +X. Match a trusted view here. Reference changes do not edit metadata.</p>
      <div className="sight-fields">{(["x","y","z"] as const).map((a,i)=><SliderField key={a} numeric label={`Reference eye ${a.toUpperCase()}`} unit="m" value={reference.eye[i]} min={-1} max={1} hardMin={-10} hardMax={10} step={.001} commitValidOnly onChange={v=>setReference({...reference,eye:reference.eye.map((n,j)=>j===i?v:n) as Vec})}/>)}
      {(["yaw","pitch","roll","fov"] as const).map(a=><SliderField key={a} numeric label={`Reference ${a}`} value={reference[a]} unit="degrees" min={a==="fov"?5:-89} max={a==="fov"?90:89} hardMin={a==="fov"?1:a==="pitch"?-89:-360} hardMax={a==="fov"?170:a==="pitch"?89:360} step={.1} commitValidOnly onChange={v=>setReference({...reference,[a]:v})}/>)}</div>
    </section><section><h5>Camera trial · editable metadata</h5><p>Relative to the saved baseline: XYZ moves the eye right/forward/up; rotation XYZ is pitch/roll/yaw. This mapping is an unqualified hypothesis, not GTA aim IK. FOV edits are not simulated.</p>
      <div className="sight-fields">{fields.map(f=><SliderField key={f.key} label={`Sight trial ${f.label}`} unit={f.unit} value={draft[f.key]??original[f.key]} resetValue={original[f.key]} min={Number((Number(original[f.key])-(f.unit==="metres"?.05:5)).toFixed(6))} max={Number((Number(original[f.key])+(f.unit==="metres"?.05:5)).toFixed(6))} hardMin={f.minimum} hardMax={f.maximum} step={f.unit==="metres"?.0001:.01} disabled={locked||!snapshot.editable_fields.includes(f.key)} onChange={v=>onChange({[f.key]:v})}/>)}</div>
      {!fields.length&&<p>No existing camera fields in this family. Nothing will be synthesized.</p>}
      <button disabled={locked||!changed||!!error||typeof result==="string"} onClick={onReview}>Review sight trial</button>
      <button disabled={locked||!changed} onClick={()=>onChange(Object.fromEntries(fields.map(f=>[f.key,original[f.key]])))}>Reset sight trial</button>
    </section></div>
    <details><summary>Sight landmarks &amp; reference export</summary><p>Click a visible surface in Side/Top, then correct its coordinates. An aperture center is empty space, so it needs manual entry. Align reference only constructs a geometric sight line; it does not zero the game weapon.</p>
      <div className="sight-fields">{(["rear","front"] as const).map(kind=>{const point=kind==="rear"?rear:front,set=kind==="rear"?setRear:setFront;return <fieldset key={kind}><legend>{kind==="rear"?"Rear aperture center":"Front post tip"}</legend><button disabled={!bind} aria-pressed={mark===kind} onClick={()=>setMark(mark===kind?"":kind)}>Pick {kind} surface</button>{["x","y","z"].map((axis,i)=><SliderField key={axis} numeric label={`${kind} ${axis}`} value={point?.[i]??0} min={-1} max={1} hardMin={-10} hardMax={10} step={.0001} commitValidOnly onChange={v=>set((point??[0,0,0]).map((n,j)=>j===i?v:n) as Vec)}/>)}<button onClick={()=>set(null)}>Clear {kind}</button></fieldset>;})}</div>
      <SliderField numeric label="Eye relief" unit="m" value={relief} min={.02} max={.5} hardMin={.01} hardMax={2} step={.005} commitValidOnly onChange={setRelief}/>
      <button disabled={!rear||!front} onClick={()=>{try{setReference(alignedRig(rear!,front!,relief,reference.fov));setView("aim");setError("");}catch(e){setError(String(e));}}}>Align reference to marked sights</button>
      <button onClick={()=>save("offline-sight-reference.json",new Blob([JSON.stringify(evidence(),null,2)],{type:"application/json"}))}>Export sight reference JSON</button>
      <button onClick={()=>canvas.current?.toBlob(blob=>{if(blob)save("offline-sight-geometry.png",blob);})}>Export geometry PNG</button>
      <label>Restore reference JSON (camera and landmarks only)<textarea value={referenceText} maxLength={100000} onChange={e=>setReferenceText(e.target.value)}/></label><button disabled={!referenceText} onClick={loadReference}>Restore matching reference</button>
      <p className="sight-source">{model.entry} · {model.packet.lod} · {model.packet.vertex_count} vertices<br/>Native SHA-256: {model.native_sha256}</p>
    </details>
  </div>;
}
