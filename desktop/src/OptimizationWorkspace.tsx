import {useEffect,useState} from "react";
import type {DesktopClient} from "./types";
import {AuthoringFeedback,useAuthoringWorkspace,type WorkspaceResult} from "./useAuthoringWorkspace";
import AssetValidationReport,{type AssetReport} from "./AssetValidationReport";
import OptimizationPixelPreview,{type PixelEvidence} from "./OptimizationPixelPreview";
import OptimizationCostEvidence,{validateOptimizationCosts,type OptimizationCost} from "./OptimizationCostEvidence";
import SharedRigBindings,{type DrawableCandidate,type RigBinding} from "./SharedRigBindings";

type Target={dictionary:string;texture:string;format:string;mips:number;role:string;material_usage?:{roles:string[];color_conversion_blocked:boolean;scope:string}};
function validatedChoices(value:unknown):Target[] {
  if(!Array.isArray(value)||value.length>1000||value.some(item=>{
    const usage=item?.material_usage;
    return !item||typeof item.dictionary!=="string"||typeof item.texture!=="string"||!usage||!Array.isArray(usage.roles)||usage.roles.length<1||usage.roles.length>5
      ||usage.roles.some((role:unknown)=>typeof role!=="string"||!["color_hint","normal","data","palette","unknown"].includes(role))
      ||typeof usage.scope!=="string"||usage.color_conversion_blocked!==usage.roles.some((role:string)=>["normal","data","palette"].includes(role));
  }))throw new Error("Invalid material-role evidence; inspect with a matching SDK before queuing candidates.");
  return value;
}
type Change=OptimizationCost & {dictionary:string;texture:string;preview_before:string;preview_after:string;preview_region?:PixelEvidence;quality_scope:string;quality:{maximum_absolute_error_rgba:number[]}};
export type OptimizationContext={source:string;comparison:string;edition:string;game:string;rigBindings:RigBinding[]};
export default function OptimizationWorkspace({client,onGuardChange,initialContext}:{client:DesktopClient;onGuardChange:(value:boolean)=>void;initialContext?:OptimizationContext}) {
  const [source,setSource]=useState(initialContext?.source??""),[edition,setEdition]=useState(initialContext?.edition??""),[game,setGame]=useState(initialContext?.game??"");
  const [comparison,setComparison]=useState(initialContext?.comparison??""),[rigBindings,setRigBindings]=useState<RigBinding[]>(initialContext?.rigBindings??[]),[rigCandidates,setRigCandidates]=useState<DrawableCandidate[]>([]);
  const [session,setSession]=useState<WorkspaceResult|null>(null),[choices,setChoices]=useState<Target[]>([]);
  const [queue,setQueue]=useState<Target[]>([]),[selected,setSelected]=useState("0"),[format,setFormat]=useState("DXT5"),[mips,setMips]=useState(1),[role,setRole]=useState("unknown"),[dirty,setDirty]=useState(false);
  const work=useAuthoringWorkspace(client,"optimization",value=>{const nextChoices=validatedChoices(value.choices??[]);validateOptimizationCosts(value.changes??[]);setSession(value);setChoices(nextChoices);setRigCandidates((value.before_report as {drawable_candidates?:DrawableCandidate[]}|undefined)?.drawable_candidates??[]);setDirty(false);});
  useEffect(()=>onGuardChange(work.locked||dirty),[work.locked,dirty,onGuardChange]);
  const request={source,comparison:comparison||undefined,edition:edition||undefined,gta_path:game||undefined,settings:{textures:queue,rig_bindings:rigBindings}};
  const changed=()=>{setSession(null);setDirty(true);};
  const resetRigs=()=>{setRigBindings([]);setRigCandidates([]);};
  const pick=async(kind:"package_folder"|"rpf"="package_folder")=>{const value=await work.choose(kind);if(value){setSource(value);setChoices([]);setQueue([]);setSelected("0");setRole("unknown");resetRigs();setSession(null);setDirty(false);}};
  const add=()=>{const choice=choices[Number(selected)];if(!choice||choice.material_usage?.color_conversion_blocked)return;setQueue([...queue.filter(item=>item.dictionary!==choice.dictionary||item.texture!==choice.texture),{dictionary:choice.dictionary,texture:choice.texture,format,mips,role}]);changed();};
  const exportPackage=async()=>{const parent=await work.choose("authoring_parent");if(parent&&session)void work.run("review_workspace_action",{...request,action:"export",destination:`${parent}/optimized-package`,expected_state_sha256:session.state_sha256});};
  const recover=async()=>{
    const folder=await work.choose("package_folder");if(!folder)return;
    void work.run("inspect_authoring_workspace",{workspace:folder});
  };
  const reviewRecovery=async()=>{const parent=await work.choose("authoring_parent");if(parent&&session)void work.run("review_workspace_action",{workspace:session.workspace,action:"recover",destination:`${parent}/recovered-package`,expected_state_sha256:session.state_sha256});};
  const changes=(session?.changes??[]) as Change[];
  const artifact=session?.artifact_manifest as {artifact_id:string;build:{build_fingerprint:string;mode:string;sdk_version:string}}|undefined;
  const rebuilt=(session?.archive_rebuilds??[]) as {container:string;before_sha256:string;after_sha256:string;changed_members:string[];preservation:string}[];
  return <section className="workspace-section" aria-label="Reversible optimization">
    <h2>Reversible package optimization</h2><p>Preview color-texture candidates, compare the same validation report, and export an optimized package alongside exact recovery originals. Game files are never changed here.</p>
    <fieldset disabled={work.locked}><legend>Package context</legend>
      <button disabled={dirty} onClick={()=>void pick()}>Choose optimization package</button><button disabled={dirty} onClick={()=>void pick("rpf")}>Choose optimization RPF</button><p>{source||"No source selected"}</p>
      {initialContext&&<p>Validation context copied from the asset report. Inspect again before choosing candidates or exporting; the previous report is not reused as proof.</p>}
      <label>Optimization edition<select value={edition} onChange={e=>{setEdition(e.target.value);resetRigs();changed();}}><option value="">XML dictionaries only</option><option>Legacy</option><option>Enhanced</option></select></label>
      <button onClick={async()=>{const value=await work.choose("gta_folder");if(value){setGame(value);resetRigs();changed();}}}>Choose optimization decoder context</button>
      {game&&<p>{game}</p>}
      <button disabled={!source} onClick={()=>void work.run("inspect_authoring_workspace",request)}>{queue.length?"Preview optimization candidates":"Inspect optimization inputs"}</button>
      <button disabled={dirty} onClick={()=>void recover()}>Open recovery package</button>
      {dirty&&<button onClick={()=>{setQueue([]);resetRigs();setSession(null);setDirty(false);}}>Discard optimization draft</button>}
    </fieldset>
    <details className="asset-validation"><summary>Optimization validation context</summary><div className="asset-validation-body"><fieldset disabled={work.locked}><legend>Explicit shared asset and metadata context</legend>
      <button onClick={async()=>{const value=await work.choose("package_folder");if(value){setComparison(value);resetRigs();changed();}}}>Choose optimization comparison folder</button>
      <button onClick={async()=>{const value=await work.choose("rpf");if(value){setComparison(value);resetRigs();changed();}}}>Choose optimization comparison RPF</button>
      {comparison&&<><p>{comparison}</p><button onClick={()=>{setComparison("");resetRigs();changed();}}>Clear optimization comparison</button></>}
      <p>Both reports use these same selected dependencies and metadata. They are not modified, included in the optimized payload, or treated as installed load-order proof.</p>
    </fieldset><SharedRigBindings candidates={rigCandidates} bindings={rigBindings} locked={work.locked} inspectionAction={queue.length?"Preview optimization candidates":"Inspect optimization inputs"} onChange={value=>{setRigBindings(value);changed();}}/></div></details>
    {!!choices.length&&<details className="asset-validation" open><summary>Texture candidate options · {queue.length} queued</summary><div className="asset-validation-body">
      <fieldset disabled={work.locked}><legend>Explicit candidate selection</legend>
        <label>Package texture<select value={selected} onChange={e=>{setSelected(e.target.value);setRole("unknown");}}>{choices.map((item,index)=><option key={index} value={index}>{item.dictionary} · {item.texture}</option>)}</select></label>
        <label>Candidate format<select value={format} onChange={e=>setFormat(e.target.value)}>{["RGBA8","DXT1","DXT3","DXT5"].map(value=><option key={value}>{value}</option>)}</select></label>
        <label>Candidate mip count<input type="number" min={1} max={15} value={mips} onChange={e=>setMips(Number(e.target.value))}/></label>
        <label>Declared material role<select value={role} onChange={e=>setRole(e.target.value)}><option value="unknown">Unknown — do not encode</option><option value="color">I identified this as a color texture</option></select></label>
        {choices[Number(selected)]?.material_usage&&<p>Observed sampler-role hints: {choices[Number(selected)].material_usage!.roles.join(", ")}. {choices[Number(selected)].material_usage!.color_conversion_blocked?"Color conversion blocked for this material usage.":"A color hint is not shader/channel certification; your declaration remains required."}</p>}
        <p>Normal, mask and packed-data textures require semantic-aware processing. DXT1 is blocked when alpha is not fully opaque. Lower mips are regenerated, not preserved.</p>
        <button disabled={role!=="color"||queue.length>=8||choices[Number(selected)]?.material_usage?.color_conversion_blocked} onClick={add}>Queue texture candidate</button>
        {!!queue.length&&<button onClick={()=>{setQueue([]);changed();}}>Discard candidates</button>}
        <ul>{queue.map((item,index)=><li key={index}>{item.dictionary} · {item.texture}: {item.format}, {item.mips} mips</li>)}</ul>
      </fieldset>
    </div></details>}
    {changes.map((item,index)=><details key={index} open><summary>{item.texture} · storage delta {item.storage_delta_bytes.toLocaleString()} bytes</summary>
      <div style={{display:"flex",gap:"1rem"}}><figure><img width={128} height={128} style={{objectFit:"contain",imageRendering:"pixelated"}} src={item.preview_before} alt={`Before ${item.texture}`}/><figcaption>Before thumbnail</figcaption></figure><figure><img width={128} height={128} style={{objectFit:"contain",imageRendering:"pixelated"}} src={item.preview_after} alt={`After ${item.texture}`}/><figcaption>Candidate thumbnail</figcaption></figure></div>
      <p>{item.quality_scope}</p><p>Maximum RGBA byte error: {item.quality.maximum_absolute_error_rgba.join(", ")}</p>
      <OptimizationCostEvidence texture={item.texture} cost={item}/>
      <OptimizationPixelPreview texture={item.texture} before={item.before} after={item.after} evidence={item.preview_region} locked={work.locked||dirty}
        inspect={region=>void work.run("inspect_authoring_workspace",{...request,expected_state_sha256:session?.state_sha256,preview_region:{dictionary:item.dictionary,texture:item.texture,...region}})}/>
    </details>)}
    {!!session?.before_report&&<details><summary>Before validation</summary><AssetValidationReport report={session.before_report as AssetReport}/></details>}
    {!!session?.after_report&&<details><summary>Candidate validation</summary><AssetValidationReport report={session.after_report as AssetReport}/></details>}
    {!!session?.preservation&&<p>{String(session.preservation)}</p>}
    {!!rebuilt.length&&<details className="asset-validation"><summary>Verified RPF rebuilds · {rebuilt.length}</summary><div className="asset-validation-body">{rebuilt.map((row,i)=><section key={i}><h4>{row.container}</h4><p>{row.preservation}</p><p>Before {row.before_sha256}<br/>Candidate {row.after_sha256}</p><ul>{row.changed_members.map(name=><li key={name}>{name}</li>)}</ul></section>)}</div></details>}
    {artifact&&<details><summary>Exact SDK build &amp; artifact identity</summary><p>SDK {artifact.build.sdk_version} · {artifact.build.mode}<br/>Build fingerprint: {artifact.build.build_fingerprint}<br/>Artifact: {artifact.artifact_id}</p><p>The exported package carries sdk-artifact.json. A compatible Launcher verifies those bytes and adds their lineage to its installation receipt. This is not a publisher signature or evidence that the game loaded them.</p></details>}
    {!!changes.length&&<button disabled={work.locked||dirty} onClick={()=>void exportPackage()}>Review optimized package export</button>}
    {!!session?.workspace&&<button disabled={work.locked} onClick={()=>void reviewRecovery()}>Review exact original recovery</button>}
    <AuthoringFeedback work={work}/>
  </section>;
}
