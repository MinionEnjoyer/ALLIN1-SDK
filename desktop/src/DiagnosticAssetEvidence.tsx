type Check={category:string;status:string;finding_count:number;shown_findings:number;truncated:boolean;codes:string[]};
export type DiagnosticAssetSummary={status:string;file_sha256:string;report_sha256:string;source_sha256:string;validator_sha256:string;static_status:string;source_relation:string;runtime_status:string;checks:Check[];scope:string};
const categories=["skeleton","attachments","skinning","textures","lods","metadata"];
const ranks:Record<string,number>={pass:0,warning:1,not_checked:2,fail:3};
const relations:Record<string,string>={inputs_inventory:"Input / baseline inventory",outputs_inventory:"Output / candidate inventory","inputs+outputs_inventory":"Identical input and output inventories",not_established:"Inventory relationship not established"};
const sha=(v:unknown)=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v);
const count=(v:unknown,limit:number)=>typeof v==="number"&&Number.isSafeInteger(v)&&v>=0&&v<=limit;
function valid(value:unknown):value is DiagnosticAssetSummary {
  if(!value||typeof value!=="object")return false;
  const v=value as DiagnosticAssetSummary;
  if(!["recorded","not_recorded"].includes(v.status)||v.runtime_status!=="not_tested"
    ||![v.file_sha256,v.report_sha256,v.source_sha256,v.validator_sha256].every(sha)
    ||typeof v.scope!=="string"||v.scope.length>2000||typeof v.source_relation!=="string"||!Object.hasOwn(relations,v.source_relation)
    ||(v.status==="not_recorded"&&v.source_relation!=="not_established")
    ||!Array.isArray(v.checks)||v.checks.length!==6)return false;
  if(v.checks.some(c=>!c||!categories.includes(c.category)||typeof c.status!=="string"||!Object.hasOwn(ranks,c.status)
    ||!count(c.finding_count,1000000)||!count(c.shown_findings,40)||c.shown_findings>c.finding_count
    ||c.truncated!==(c.finding_count>c.shown_findings)||!Array.isArray(c.codes)||c.codes.length>c.shown_findings
    ||(!c.truncated&&c.shown_findings===0&&c.status!=="pass")
    ||(c.shown_findings>0&&c.codes.length===0)||new Set(c.codes).size!==c.codes.length
    ||c.codes.some(code=>typeof code!=="string"||!/^[a-z][a-z0-9_]{0,79}$/.test(code))))return false;
  return new Set(v.checks.map(c=>c.category)).size===6
    &&v.static_status===["pass","warning","incomplete","fail"][Math.max(...v.checks.map(c=>ranks[c.status]))];
}
export default function DiagnosticAssetEvidence({value}:{value:unknown}) {
  if(!valid(value))return <p role="alert">Static diagnostic evidence is invalid.</p>;
  return <details><summary>Static asset evidence · {value.status==="recorded"?"report hash recorded":"not linked to selected artifact"}</summary><div className="asset-validation-body">
    <p>{relations[value.source_relation]} · static {value.static_status}</p>
    <p>Input reports describe the baseline, not the installed candidate. Report hashes are content links, not signatures or in-game proof. Crash cause remains unestablished.</p>
    {value.status==="not_recorded"&&<p role="status">This report is not recorded in the selected artifact. Do not attribute its findings to that build.</p>}
    <div className="data-report-scroll"><table><caption>Selected static report categories</caption><thead><tr><th>Category</th><th>Status</th><th>Findings</th><th>Codes from shown findings</th></tr></thead><tbody>{value.checks.map(c=><tr key={c.category}><th scope="row">{c.category}</th><td>{c.status}</td><td>{c.finding_count}{c.truncated&&` (${c.shown_findings} shown in source report)`}</td><td>{c.codes.join(", ")||"None"}</td></tr>)}</tbody></table></div>
    <details><summary>Exact report identities and scope</summary><p>Report {value.report_sha256}<br/>Source identity {value.source_sha256}<br/>Validator {value.validator_sha256}<br/>Selected file bytes {value.file_sha256}</p><p>{value.scope}</p></details>
  </div></details>;
}
