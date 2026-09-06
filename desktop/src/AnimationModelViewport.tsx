import { useEffect, useMemo, useRef, useState } from "react";
import type { AnimationPacket } from "./NativeAnimationView";
import { poseModel, prepareModel, type AnimationModel, type ModelBindingSelection } from "./animationPose";

export default function AnimationModelViewport({model, animation, time, locked, onBind}: {
  model: AnimationModel; animation: AnimationPacket; time: number; locked: boolean;
  onBind: (selection: ModelBindingSelection)=>void;
}) {
  const canvas=useRef<HTMLCanvasElement>(null);
  const [layer,setLayer]=useState(0), [yaw,setYaw]=useState(30), [pitch,setPitch]=useState(15), [zoom,setZoom]=useState(1);
  const [bind,setBind]=useState(false), [skeleton,setSkeleton]=useState(true), [wire,setWire]=useState(false);
  const [expanded,setExpanded]=useState(true);
  const [rootMotion,setRootMotion]=useState(false);
  const [renderError,setRenderError]=useState("");
  const prepared=useMemo(()=>{try { return model.selected===null || model.binding_required || model.view_unavailable ? null : prepareModel(model); } catch(error) { return String(error); }},[model]);
  const layers=[...new Set(animation.tracks.map(track=>track.layer))];
  const chosenLayer=layers.includes(layer) ? layer : layers[0]??0;
  const pose=useMemo(()=>{if(!expanded || !prepared || typeof prepared==="string") return null;
    try { return poseModel(prepared,animation,time,chosenLayer,bind,rootMotion); } catch(error) { return String(error); }
  },[prepared,animation,time,chosenLayer,bind,expanded,rootMotion]);
  useEffect(()=>{
    if(!canvas.current || !prepared || typeof prepared==="string" || !pose || typeof pose==="string") return;
    const context=canvas.current.getContext("2d");
    if(!context) { setRenderError("Canvas rendering is unavailable on this device."); return; }
    setRenderError("");
    context.fillStyle="#131d23"; context.fillRect(0,0,900,520);
    const y=yaw*Math.PI/180,p=pitch*Math.PI/180, scale=210/prepared.radius*zoom, center=prepared.center;
    function project(point: number[]) {
      const [x,y0,z]=point.map((v,i)=>v-center[i]);
      const rx=x*Math.cos(y)-y0*Math.sin(y), depth=x*Math.sin(y)+y0*Math.cos(y);
      return [450+rx*scale,260-(z*Math.cos(p)-depth*Math.sin(p))*scale,depth*Math.cos(p)+z*Math.sin(p)];
    }
    const faces: {points:number[][]; depth:number; shade:number}[]=[];
    for(const mesh of pose.meshes) {
      const points=Array.from({length:mesh.positions.length/3},(_,i)=>project(mesh.positions.slice(i*3,i*3+3)));
      for(let i=0;i<mesh.triangles.length;i+=3) {
        const triangle=mesh.triangles.slice(i,i+3).map(index=>points[index]);
        const a=triangle[1].map((v,j)=>v-triangle[0][j]), b=triangle[2].map((v,j)=>v-triangle[0][j]);
        const normal=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
        const shade=35+Math.abs(normal[2])/Math.max(1e-9,Math.hypot(...normal))*35;
        faces.push({points:triangle,depth:triangle.reduce((v,p)=>v+p[2],0)/3,shade});
      }
    }
    faces.sort((a,b)=>b.depth-a.depth);
    for(const face of faces) {
      context.beginPath(); face.points.forEach((p,i)=>i ? context.lineTo(p[0],p[1]) : context.moveTo(p[0],p[1])); context.closePath();
      if(!wire) { context.fillStyle=`hsl(164 28% ${face.shade}%)`; context.fill(); }
      else { context.strokeStyle="#90dfc9"; context.lineWidth=.7; context.stroke(); }
    }
    if(skeleton) { context.strokeStyle="#ffbd70"; context.fillStyle="#ffbd70"; context.lineWidth=2;
      pose.bones.forEach((bone,i)=>{ const point=project(bone), parent=model.bones[i].parent;
        if(parent>=0) { const target=project(pose.bones[parent]); context.beginPath(); context.moveTo(point[0],point[1]); context.lineTo(target[0],target[1]); context.stroke(); }
        context.beginPath(); context.arc(point[0],point[1],3,0,2*Math.PI); context.fill();
      });
    }
  },[prepared,pose,yaw,pitch,zoom,skeleton,wire,model]);
  if(!model || typeof model.scope!=="string" || typeof model.source_sha256!=="string" || !/^[0-9a-f]{64}$/.test(model.source_sha256)
    || (model.binding_required!==undefined && typeof model.binding_required!=="string")
    || (model.view_unavailable!==undefined && typeof model.view_unavailable!=="string")
    || (model.source!==undefined && typeof model.source!=="string") || !Array.isArray(model.drawables) || model.drawables.length>128
    || model.drawables.some(item=>!item || typeof item.key!=="string" || typeof item.name!=="string")
    || (model.selected!==null && !model.drawables.some(item=>item.key===model.selected))
    || !Array.isArray(model.lods) || model.lods.length>4 || model.lods.some(lod=>typeof lod!=="string")) return <p role="alert">Invalid model selection evidence.</p>;
  const shared=model.skeleton_binding;
  if(shared && (shared.mode!=="external" || typeof shared.scope!=="string" || typeof shared.source_sha256!=="string" || !/^[0-9a-f]{64}$/.test(shared.source_sha256)
    || (shared.source!==undefined && typeof shared.source!=="string") || !Array.isArray(shared.drawables) || shared.drawables.length>128
    || shared.drawables.some(item=>!item || typeof item.key!=="string" || typeof item.name!=="string")
    || (shared.selected!==null && !shared.drawables.some(item=>item.key===shared.selected)))) return <p role="alert">Invalid shared skeleton evidence.</p>;
  const error=typeof prepared==="string" ? prepared : typeof pose==="string" ? pose : renderError;
  return <section className="animation-model" aria-label="Bound animation model"><details open={expanded} onToggle={event=>setExpanded(event.currentTarget.open)}>
    <summary role="button" aria-expanded={expanded}>Model playback · reconstructed XML bind pose</summary><div className="animation-model-body"><p>{model.scope}</p>
    <p className="animation-model-source">{model.source ?? "SDK-owned generated model fixture"} · SHA-256 {model.source_sha256}</p>
    {shared && <><p className="animation-model-source">Shared skeleton: {shared.source ?? "SDK-owned skeleton fixture"} · SHA-256 {shared.source_sha256}</p><p>{shared.scope}</p></>}
    {model.binding_required && <p role="status">{model.binding_required}</p>}
    {model.view_unavailable && <p role="status">{model.view_unavailable}</p>}
    <div className="animation-controls"><label>Drawable<select aria-label="Animation model drawable" disabled={locked} value={model.selected??""} onChange={e=>onBind({drawable:e.target.value})}>
      {model.selected===null && <option value="">Choose a drawable; no skeleton is inferred</option>}{model.drawables.map(item=><option key={item.key} value={item.key}>{item.name} · {item.key}</option>)}
    </select></label><label>LOD<select aria-label="Animation model LOD" disabled={locked || !model.lods.length} value={model.lod??""} onChange={e=>onBind({drawable:model.selected??undefined,lod:e.target.value})}>
      {model.lods.map(lod=><option key={lod}>{lod}</option>)}
    </select></label><label>Layer<select aria-label="Model animation layer" value={chosenLayer} onChange={e=>setLayer(Number(e.target.value))}>{layers.map(value=><option key={value} value={value}>Layer {value}</option>)}</select></label>
      <label><input type="checkbox" checked={bind} onChange={e=>setBind(e.target.checked)}/>Bind pose</label>
      <label><input type="checkbox" checked={rootMotion} onChange={e=>setRootMotion(e.target.checked)}/>Apply root motion</label>
      <label><input type="checkbox" checked={skeleton} onChange={e=>setSkeleton(e.target.checked)}/>Skeleton overlay</label>
      <label><input type="checkbox" checked={wire} onChange={e=>setWire(e.target.checked)}/>Wireframe model</label>
    </div>
    {shared && <label>Skeleton drawable<select aria-label="Shared skeleton drawable" disabled={locked} value={shared.selected??""} onChange={e=>onBind({skeleton_drawable:e.target.value})}>
      {shared.selected===null && <option value="">Choose the matching skeleton owner</option>}{shared.drawables.map(item=><option key={item.key} value={item.key}>{item.name} · {item.key}</option>)}
    </select></label>}
    <p>One layer at a time; local translation, rotation and scale. Root-motion tracks 5/6 can be applied to a parentless tag-0 root. Ordinary channels retain nonzero flags; expression tracks are not applied. Motion resets on each loop.</p>
    {error && <p role="alert">Model playback unavailable: {error}</p>}
    {pose && typeof pose!=="string" && !error && <><p aria-label="Model binding coverage">{pose.matched} matched channels · {pose.missing} unmatched bone tags · {pose.unsupported} unsupported or disabled tracks · {model.vertex_count} vertices · {model.triangle_count} triangles</p>
      {(pose.missing>0 || pose.unsupported>0) && <p role="status">Partial pose: some animation channels are not applied. This is not a complete clip reproduction.</p>}
    </>}
    {model.selected!==null && !model.binding_required && !model.view_unavailable && <><canvas hidden={!!error} ref={canvas} width="900" height="520" aria-label="Animated model viewport" role="img"/>
      <div className="animation-controls">{([
        ["Yaw",yaw,setYaw,-180,180,1], ["Pitch",pitch,setPitch,-85,85,1], ["Zoom",zoom,setZoom,.2,4,.05],
      ] as const).map(([name,value,setter,min,max,step])=><label key={name}>{name}<input aria-label={`Animation camera ${name.toLowerCase()}`} type="range" min={min} max={max} step={step} value={value} onChange={e=>setter(Number(e.target.value))}/></label>)}</div>
    </>}
  </div></details></section>;
}
