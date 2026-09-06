import {useState} from "react";

export type PixelRegion = {mip:number;x:number;y:number;channel:string};
export type PixelEvidence = PixelRegion & {width:number;height:number;mip_width:number;mip_height:number;before:string|null;after:string|null;difference:string|null;scope:string};
export default function OptimizationPixelPreview({texture,before,after,evidence,locked,inspect}:{
  texture:string;before:{width:number;height:number;mip_levels:number};after:{mip_levels:number};evidence?:PixelEvidence;
  locked:boolean;inspect:(region:PixelRegion)=>void;
}) {
  const [region,setRegion]=useState<PixelRegion>({mip:0,x:0,y:0,channel:"rgba"});
  const [zoom,setZoom]=useState(4);
  const width=Math.max(1,before.width>>region.mip),height=Math.max(1,before.height>>region.mip);
  const valid=Number.isInteger(region.x)&&Number.isInteger(region.y)&&region.x>=0&&region.x<width&&region.y>=0&&region.y<height;
  return <details className="asset-validation"><summary>Exact pixel comparison · {texture}</summary><div className="asset-validation-body">
    <fieldset disabled={locked}><legend>Synchronized region · up to 64 × 64 actual pixels</legend>
      <label>Mip level<select aria-label={`Pixel mip ${texture}`} value={region.mip} onChange={e=>setRegion({...region,mip:Number(e.target.value),x:0,y:0})}>
        {Array.from({length:Math.max(before.mip_levels,after.mip_levels)},(_,i)=><option value={i} key={i}>{i}{i>=before.mip_levels?" — candidate only":i>=after.mip_levels?" — original only":""}</option>)}
      </select></label>
      <label>X<input aria-label={`Pixel X ${texture}`} type="number" min={0} max={width-1} value={region.x} onChange={e=>setRegion({...region,x:Number(e.target.value)})}/></label>
      <label>Y<input aria-label={`Pixel Y ${texture}`} type="number" min={0} max={height-1} value={region.y} onChange={e=>setRegion({...region,y:Number(e.target.value)})}/></label>
      <label>Channels<select aria-label={`Pixel channels ${texture}`} value={region.channel} onChange={e=>setRegion({...region,channel:e.target.value})}><option value="rgba">RGBA</option><option value="rgb">RGB</option><option value="alpha">Alpha</option></select></label>
      <label>Display zoom<select aria-label={`Pixel zoom ${texture}`} value={zoom} onChange={e=>setZoom(Number(e.target.value))}>{[1,2,4,8].map(n=><option value={n} key={n}>{n}×</option>)}</select></label>
      <p>Mip dimensions: {width} × {height}. The same mip, crop, channels and zoom apply to both images. Newly generated or removed mips remain visibly absent on the other side.</p>
      <button disabled={!valid} onClick={()=>inspect(region)}>Inspect exact pixels {texture}</button>
    </fieldset>
    {evidence&&<><p>Displayed: mip {evidence.mip}, ({evidence.x}, {evidence.y}), {evidence.width} × {evidence.height}, {evidence.channel}. {evidence.scope}</p>
      <div style={{display:"flex",gap:16,overflow:"auto"}}>{(["before","after","difference"] as const).map(key=><figure key={key} style={{margin:0,flexShrink:0}}>
        {evidence[key]?<img src={evidence[key]} alt={`${key} exact pixels ${texture}`} width={evidence.width*zoom} height={evidence.height*zoom} style={{imageRendering:"pixelated",background:"repeating-conic-gradient(#ddd 0% 25%, #777 0% 50%) 0 / 16px 16px"}}/>:<p>{key}: mip not present; no synthesized comparison</p>}
        <figcaption>{key}</figcaption></figure>)}</div></>}
  </div></details>;
}
