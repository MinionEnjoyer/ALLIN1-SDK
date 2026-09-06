export type TextureResolution={sampler:string;texture:string;dictionary:string;payload_sha256:string;
  usages?:{shader:number;parameter:number;slot:string;role:string}[];
  dictionary_chain?:{dictionary:string;source:string}[];parent_chain?:{child:string;parent:string}[]};
export type TextureParent={child:string;parent:string;source:string;source_sha256:string};
const sha=(value:unknown)=>typeof value==="string"&&/^[a-f0-9]{64}$/.test(value);
export function validTextureResolutions(value:unknown):value is TextureResolution[]{
  return Array.isArray(value)&&value.length<=1000&&value.every(row=>row&&[row.sampler,row.texture,row.dictionary].every(v=>typeof v==="string")&&sha(row.payload_sha256)
    &&(row.usages===undefined||(Array.isArray(row.usages)&&row.usages.length<=64&&row.usages.every((u:{shader:number;parameter:number;slot:string;role:string})=>u&&Number.isSafeInteger(u.shader)&&u.shader>=0&&Number.isSafeInteger(u.parameter)&&u.parameter>=0&&typeof u.slot==="string"&&u.slot.length<=160&&["normal","data","palette","color_hint","unknown"].includes(u.role))))
    &&(row.dictionary_chain===undefined||(Array.isArray(row.dictionary_chain)&&row.dictionary_chain.length<=32&&row.dictionary_chain.every((item:{dictionary:string;source:string})=>item&&typeof item.dictionary==="string"&&typeof item.source==="string")))
    &&(row.parent_chain===undefined||(Array.isArray(row.parent_chain)&&row.parent_chain.length<=32&&row.parent_chain.every((item:{child:string;parent:string})=>item&&typeof item.child==="string"&&typeof item.parent==="string"))));
}
export function validTextureParents(value:unknown):value is TextureParent[]{
  return Array.isArray(value)&&value.length<=1000&&value.every(row=>row&&[row.child,row.parent,row.source].every(v=>typeof v==="string")&&sha(row.source_sha256));
}
export default function TextureDependencyEvidence({resolutions,parents}:{resolutions:TextureResolution[];parents:TextureParent[]}){
  return <details><summary>Resolved texture dependencies · {resolutions.length}</summary>
    <p>Exact selected dictionary context and validated DDS payloads. Parent declarations do not establish installed load order, sampler roles or in-game appearance.</p>
    {resolutions.map((row,i)=><details key={i}><summary>{row.texture} · {row.dictionary}</summary>
      <p>Sampler: {row.sampler}<br/>Payload SHA-256 {row.payload_sha256}</p>
      {!!row.usages?.length&&<ul aria-label="Authored shader slot usage">{row.usages.map((u,j)=><li key={j}>Shader {u.shader}, parameter {u.parameter}: {u.slot||"Unnamed"} · {u.role.replaceAll("_"," ")}</li>)}</ul>}
      {row.dictionary_chain&&<ol aria-label="Selected texture lookup chain">{row.dictionary_chain.map((item,j)=><li key={j}>{item.dictionary} · {item.source}</li>)}</ol>}
      {!!row.parent_chain?.length&&<p>Declared parent path: {row.parent_chain.map(item=>`${item.child} → ${item.parent}`).join("; ")}</p>}
    </details>)}
    {!!parents.length&&<details><summary>Texture parent declaration provenance · {parents.length}</summary><table><caption>Selected texture parent metadata</caption><thead><tr><th>Child → parent</th><th>Metadata source</th><th>Source SHA-256</th></tr></thead><tbody>{parents.map((row,i)=><tr key={i}><td>{row.child} → {row.parent}</td><td>{row.source}</td><td>{row.source_sha256}</td></tr>)}</tbody></table></details>}
  </details>;
}
