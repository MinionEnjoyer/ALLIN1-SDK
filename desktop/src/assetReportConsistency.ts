const categories=["skeleton","attachments","skinning","textures","lods","metadata"];
const ranks:Record<string,number>={pass:0,warning:1,not_checked:2,fail:3};
const status=(v:unknown):v is string=>typeof v==="string"&&Object.hasOwn(ranks,v);
export function consistentStaticReport(value:unknown):boolean {
  if(!value||typeof value!=="object")return false;
  const r=value as Record<string,unknown>;
  return r.schema_version===1&&r.read_only===true&&r.runtime_status==="not_tested"
    &&[r.report_sha256,r.source_sha256,r.validator_sha256].every(v=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v))
    &&consistentAssetChecks(r.checks,r.static_status);
}
// Same severity/count contract as diagnostic_asset_evidence.summarize.
// Structural consistency only: the backend verifies content seals.
export function consistentAssetChecks(checks:unknown,overall:unknown):boolean {
  if(!Array.isArray(checks)||checks.length!==6)return false;
  const seen=new Set<string>();
  for(const c of checks){
    if(!c||typeof c.category!=="string"||!categories.includes(c.category)||seen.has(c.category)||!status(c.status))return false;
    seen.add(c.category);
    if(!Array.isArray(c.findings)||c.findings.length>40||!Number.isSafeInteger(c.finding_count)
      ||c.finding_count<c.findings.length||c.finding_count>1000000||c.truncated!==(c.finding_count>c.findings.length))return false;
    let maximum=0;
    for(const f of c.findings){
      if(!f||!status(f.status)||typeof f.code!=="string"||!/^[a-z][a-z0-9_]{0,79}$/.test(f.code)
        ||typeof f.message!=="string"||typeof f.location!=="string")return false;
      maximum=Math.max(maximum,ranks[f.status]);
    }
    if(maximum>ranks[c.status]||(!c.truncated&&maximum!==ranks[c.status]))return false;
  }
  return overall===["pass","warning","incomplete","fail"][Math.max(...checks.map(c=>ranks[c.status]))];
}
