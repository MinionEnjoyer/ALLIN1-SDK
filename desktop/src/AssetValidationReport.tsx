import "./AssetValidationReport.css";
import FragmentEvidence, {validFragmentChildren,type FragmentChild} from "./FragmentEvidence";
import MetadataEvidence,{validMetadataEvidence,type MetadataSource} from "./MetadataEvidence";
import TextureDependencyEvidence,{validTextureResolutions,validTextureParents,type TextureResolution,type TextureParent} from "./TextureDependencyEvidence";

type Status = "pass" | "warning" | "fail" | "not_checked";
export type AssetReport = {schema_version:number; ruleset:string; sdk_version:string; read_only:boolean;
  source_sha256:string; validator_sha256:string; report_sha256:string; static_status:string; runtime_status:string; scope:string;
  checks:{category:string; status:Status; finding_count:number; truncated:boolean;
    findings:{code:string; status:Status; location:string; message:string}[]}[];
  files?:{path:string; sha256:string; coverage:string}[];
  metadata_evidence?:MetadataSource[];
  fragment_children?:FragmentChild[];fragment_scope?:string;
  texture_resolutions?:TextureResolution[];texture_parent_relationships?:TextureParent[];
  shared_rigs?:{model?:string;drawable:number;rig?:string;rig_drawable?:number;model_sha256?:string;rig_sha256?:string;selected_skeleton_xml_sha256?:string;scope?:string}[];
  lod_distances?:{source?:string;drawable:number;lod:string;field:string;distance:number|null;status:string;models_present:boolean}[];
  texture_costs?:{source:string; name:string; format:string; width:number; height:number; mip_levels:number; storage_bytes:number; sha256:string}[];
  texture_storage_bytes?:number; texture_memory_scope?:string;
  attachment_frame_convention?:string;
  attachment_bindings?:{weapon:string; component:string; bone:string; bone_index:number; bone_tag:number; parent_source:string; child_source:string; local_matrix:number[][]; skeleton_matrix:number[][]}[];
  archive_provenance?:{package:{archives:{path:string;sha256:string;status:string;reason?:string}[];members:{path:string;container:string;entry_id:string;sha256:string;content_sha256?:string;content_hash_mode?:string}[]}};
  lod_metrics:{source?:string; drawable:number; lod:string; vertices:number; triangles:number; complete:boolean}[]};
const categories = ["skeleton","attachments","skinning","textures","lods","metadata"];
const labels:Record<string,string> = {skeleton:"Skeleton ambiguity & transforms",attachments:"Attachment placement",skinning:"Skin weights & palettes",textures:"Texture dependencies",lods:"LOD effectiveness",metadata:"Metadata collisions"};
export default function AssetValidationReport({report, stale=false, locked=false, onExport}:{report:AssetReport; stale?:boolean; locked?:boolean; onExport?:()=>void}) {
  const sha=(value:unknown)=>typeof value==="string"&&/^[a-f0-9]{64}$/.test(value);
  const count=(value:unknown)=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;
  const status=(value:unknown)=>typeof value==="string"&&["pass","warning","fail","not_checked"].includes(value);
  const matrix=(value:unknown)=>Array.isArray(value)&&value.length===4&&value.every(row=>Array.isArray(row)&&row.length===4&&row.every(v=>typeof v==="number"&&Number.isFinite(v)&&Math.abs(v)<=1e12));
  const provenance=report?.archive_provenance?.package;
  const validDistance=(row:NonNullable<AssetReport["lod_distances"]>[number])=>row&&count(row.drawable)&&typeof row.lod==="string"&&typeof row.field==="string"&&typeof row.status==="string"&&typeof row.models_present==="boolean"&&(row.distance===null||(typeof row.distance==="number"&&Number.isFinite(row.distance)));
  if (!report || report.schema_version!==1 || !report.read_only || report.runtime_status!=="not_tested"
    || (report.fragment_children!==undefined&&(!validFragmentChildren(report.fragment_children)||typeof report.fragment_scope!=="string"))
    || (report.texture_resolutions!==undefined&&!validTextureResolutions(report.texture_resolutions))
    || (report.metadata_evidence!==undefined&&!validMetadataEvidence(report.metadata_evidence))
    || (report.texture_parent_relationships!==undefined&&!validTextureParents(report.texture_parent_relationships))
    || (report.shared_rigs!==undefined && (!Array.isArray(report.shared_rigs)||report.shared_rigs.length>1000||report.shared_rigs.some(row=>!row||!count(row.drawable)||!sha(row.rig_sha256??row.selected_skeleton_xml_sha256)||(row.rig_drawable!==undefined&&!count(row.rig_drawable)))))
    || (report.lod_distances!==undefined && (!Array.isArray(report.lod_distances)||report.lod_distances.length>2000||report.lod_distances.some(row=>!validDistance(row))))
    || !sha(report.source_sha256) || !sha(report.validator_sha256) || !sha(report.report_sha256)
    || typeof report.scope!=="string" || typeof report.ruleset!=="string" || typeof report.sdk_version!=="string"
    || !["pass","warning","fail","incomplete"].includes(report.static_status) || !Array.isArray(report.checks) || report.checks.length!==6
    || !categories.every(category=>report.checks.filter(check=>check.category===category).length===1)
    || report.checks.some(check=>!status(check.status)
      || !Array.isArray(check.findings) || check.findings.length>40 || !count(check.finding_count) || check.finding_count<check.findings.length
      || check.truncated!==(check.finding_count>check.findings.length)
      || check.findings.some(f=>!f || !status(f.status) || typeof f.message!=="string" || typeof f.location!=="string"))
    || !Array.isArray(report.lod_metrics) || report.lod_metrics.length>512
    || report.lod_metrics.some(row=>!row || !count(row.drawable) || !count(row.vertices) || !count(row.triangles) || typeof row.lod!=="string" || typeof row.complete!=="boolean" || (row.source!==undefined && typeof row.source!=="string"))
    || (report.files!==undefined && (!Array.isArray(report.files) || report.files.length>1000 || report.files.some(file=>!file || typeof file.path!=="string" || !sha(file.sha256) || typeof file.coverage!=="string")))
    || (report.archive_provenance!==undefined && (!provenance || !Array.isArray(provenance.archives) || provenance.archives.length>1000 || !Array.isArray(provenance.members) || provenance.members.length>1000
      || provenance.archives.some(row=>!row || typeof row.path!=="string" || !sha(row.sha256) || !["expanded","unexpanded"].includes(row.status) || (row.reason!==undefined && typeof row.reason!=="string"))
      || provenance.members.some(row=>!row || ![row.path,row.container,row.entry_id].every(value=>typeof value==="string") || !sha(row.sha256) || (row.content_sha256!==undefined && !sha(row.content_sha256)) || (row.content_hash_mode!==undefined && typeof row.content_hash_mode!=="string"))))
    || (report.attachment_bindings!==undefined && (!Array.isArray(report.attachment_bindings) || report.attachment_bindings.length>1000 || typeof report.attachment_frame_convention!=="string"
      || report.attachment_bindings.some(row=>!row || ![row.weapon,row.component,row.bone,row.parent_source,row.child_source].every(v=>typeof v==="string") || !count(row.bone_index) || !count(row.bone_tag) || !matrix(row.local_matrix) || !matrix(row.skeleton_matrix))))
    || (report.texture_costs!==undefined && (!Array.isArray(report.texture_costs) || report.texture_costs.length>1000 || !count(report.texture_storage_bytes) || typeof report.texture_memory_scope!=="string"
      || report.texture_costs.some(row=>!row || typeof row.source!=="string" || typeof row.name!=="string" || typeof row.format!=="string" || !sha(row.sha256) || ![row.width,row.height,row.mip_levels,row.storage_bytes].every(count))))) return <p role="alert">Asset validation evidence is invalid.</p>;
  return <details className="asset-validation" open><summary>Asset validation report · static {report.static_status}</summary>
    <div className="asset-validation-body">
      <p role="status">{stale ? "Draft changed. This report describes saved XML, not your unsaved edits." : "Static evidence only. In-game behavior has not been tested."}</p>
      <p>{report.scope}</p>
      {report.checks.map(check=><section key={check.category} aria-label={labels[check.category]}>
        <h4>{labels[check.category]} · {check.status.replaceAll("_"," ")}</h4>
        {check.findings.length ? <ul>{check.findings.map((f,i)=><li key={i}><strong>{f.status.replaceAll("_"," ")}</strong> · {f.location}: {f.message}</li>)}</ul> : <p>No findings in the checked static scope. This is not runtime certification.</p>}
        {check.truncated && <p>Showing {check.findings.length} of {check.finding_count} findings. Coverage is abbreviated.</p>}
      </section>)}
      {!!report.lod_metrics.length && <table><caption>Measured geometry counts—not GPU memory or visual quality</caption><thead><tr><th>Drawable / LOD</th><th>Vertices</th><th>Triangles</th><th>Coverage</th></tr></thead><tbody>
        {report.lod_metrics.map((row,i)=><tr key={i}><td>{row.source && <>{row.source}<br/></>}{row.drawable} / {row.lod}</td><td>{row.vertices}</td><td>{row.triangles}</td><td>{row.complete?"Complete":"Partial"}</td></tr>)}
      </tbody></table>}
      {report.files && <details><summary>Per-file validation coverage · {report.files.length} files</summary><ul>{report.files.map((file,i)=><li key={i}><strong>{file.path}</strong> · {file.coverage.replaceAll("_"," ")}<br/>SHA-256 {file.sha256}</li>)}</ul></details>}
      {!!report.fragment_children?.length&&<FragmentEvidence children={report.fragment_children} scope={report.fragment_scope!}/>}
      {!!report.metadata_evidence?.length&&<MetadataEvidence sources={report.metadata_evidence}/>}
      {(!!report.texture_resolutions?.length||!!report.texture_parent_relationships?.length)&&<TextureDependencyEvidence resolutions={report.texture_resolutions??[]} parents={report.texture_parent_relationships??[]}/>}
      {!!report.lod_distances?.length&&<details><summary>Authored LOD distance evidence</summary><p>These are file values, not measured engine transition distances. Runtime multipliers, flags and visual acceptance remain separate.</p><table><caption>Authored LOD values and populated geometry</caption><thead><tr><th>Source / drawable / LOD</th><th>Field</th><th>Distance</th><th>Geometry</th></tr></thead><tbody>{report.lod_distances.map((row,i)=><tr key={i}><td>{row.source} · {row.drawable} / {row.lod}</td><td>{row.field}</td><td>{row.distance??row.status}</td><td>{row.models_present?"Populated":"Absent"}</td></tr>)}</tbody></table></details>}
      {!!report.shared_rigs?.length&&<details><summary>Selected shared-rig evidence · {report.shared_rigs.length}</summary><ul>{report.shared_rigs.map((row,i)=><li key={i}>{row.model??"Model"} · drawable {row.drawable} → {row.rig??"Selected rig"}{row.rig_drawable!==undefined&&` · drawable ${row.rig_drawable}`}<br/>Rig SHA-256 {row.rig_sha256??row.selected_skeleton_xml_sha256}<p>{row.scope??"Explicit selected skeleton XML; static bind checks do not prove game-intended rig or runtime behavior."}</p></li>)}</ul></details>}
      {!!report.attachment_bindings?.length && <details><summary>Resolved attachment anchors · {report.attachment_bindings.length}</summary>
        <p>{report.attachment_frame_convention}</p>
        {report.attachment_bindings.map((row,i)=><details key={i}><summary>{row.weapon} → {row.component} · {row.bone}</summary>
          <p>Parent: {row.parent_source}<br/>Child: {row.child_source}<br/>Bone index {row.bone_index} · tag {row.bone_tag}</p>
          <table><caption>Authored skeleton anchor matrix · {row.bone}</caption><tbody>{row.skeleton_matrix.map((values,r)=><tr key={r}>{values.map((v,c)=><td key={c}>{v.toPrecision(6)}</td>)}</tr>)}</tbody></table>
        </details>)}
      </details>}
      {report.archive_provenance&&<details><summary>Archive/member provenance · {report.archive_provenance.package.members.length} extracted members</summary>
        <p>Extracted-byte hashes and canonical resource hashes are distinct. Archive hashes bind the untouched source containers; extraction alone does not prove asset or in-game safety.</p>
        <ul>{report.archive_provenance.package.archives.map((row,i)=><li key={i}>{row.path} · {row.status}<br/>{row.sha256}{row.reason&&<p>{row.reason}</p>}</li>)}</ul>
        <table><caption>Exact archive member identities</caption><thead><tr><th>Container / member</th><th>Extracted bytes SHA-256</th><th>Content identity</th></tr></thead><tbody>
          {report.archive_provenance.package.members.map((row,i)=><tr key={i}><td>{row.container}<br/>{row.entry_id}</td><td>{row.sha256}</td><td>{row.content_hash_mode}<br/>{row.content_sha256??"Unavailable"}</td></tr>)}
        </tbody></table>
      </details>}
      {report.texture_costs && <details open><summary>Validated texture storage subtotal · {report.texture_storage_bytes?.toLocaleString()} bytes</summary>
        <p>{report.texture_memory_scope}</p><table><caption>Verified texture payload costs</caption><thead><tr><th>Texture / dictionary</th><th>Format / size</th><th>Mips</th><th>Storage bytes</th></tr></thead><tbody>
          {report.texture_costs.map((row,i)=><tr key={i}><td>{row.name}<br/>{row.source}</td><td>{row.format}<br/>{row.width} × {row.height}</td><td>{row.mip_levels}</td><td>{row.storage_bytes.toLocaleString()}</td></tr>)}
        </tbody></table></details>}
      <p className="asset-validation-hash">Report: {report.report_sha256}<br/>Source: {report.source_sha256}<br/>Validator: {report.validator_sha256}<br/>{report.ruleset} · SDK {report.sdk_version}</p>
      {onExport && <button className="quiet-button" disabled={stale || locked} onClick={onExport}>Review asset report export</button>}
    </div>
  </details>;
}
