export type MetadataSource = {source:string;source_sha256:string;native:boolean;xml_sha256:string|null;xml_bytes:number|null;
  status:"not_checked"|"xml_available"|"invalid_xml_or_definitions";definition_schema_supported?:boolean;definition_count?:number;reason?:string};
export function validMetadataEvidence(value:unknown):value is MetadataSource[] {
  const sha=(v:unknown)=>typeof v==="string"&&/^[a-f0-9]{64}$/.test(v);
  return Array.isArray(value)&&value.length<=2000&&value.every(row=>row&&typeof row.source==="string"&&row.source.length<=2048
    &&sha(row.source_sha256)&&typeof row.native==="boolean"&&["not_checked","xml_available","invalid_xml_or_definitions"].includes(row.status)
    &&(row.xml_sha256===null||sha(row.xml_sha256))&&(row.xml_bytes===null||(Number.isSafeInteger(row.xml_bytes)&&row.xml_bytes>=0&&row.xml_bytes<=8*1024**2))
    &&(row.status!=="xml_available"||(sha(row.xml_sha256)&&row.xml_bytes!==null))
    &&(row.definition_schema_supported===undefined||typeof row.definition_schema_supported==="boolean")
    &&(row.definition_count===undefined||(Number.isSafeInteger(row.definition_count)&&row.definition_count>=0&&row.definition_count<=2000))
    &&(row.reason===undefined||(typeof row.reason==="string"&&row.reason.length<=500)));
}
export default function MetadataEvidence({sources}:{sources:MetadataSource[]}) {
  return <details><summary>Metadata decoding evidence · {sources.length} sources</summary>
    <p>Original and decoded XML hashes identify separate bytes. Successful decoding is not schema validation, load-order proof or game acceptance.</p>
    <table><caption>Metadata source and decoding coverage</caption><thead><tr><th>Source</th><th>Coverage</th><th>Original SHA-256</th><th>Decoded XML SHA-256</th></tr></thead><tbody>
      {sources.map((row,i)=><tr key={i}><td>{row.source}<br/>{row.native?"Native resource":"XML input"}</td>
        <td>{row.status.replaceAll("_"," ")}<br/>{row.definition_schema_supported===true?`${row.definition_count??0} mapped definitions`:"Definition schema not mapped / unavailable"}{row.reason&&<p>{row.reason}</p>}</td>
        <td><code>{row.source_sha256}</code></td><td><code>{row.xml_sha256??"Unavailable"}</code>{row.xml_bytes!==null&&<p>{row.xml_bytes.toLocaleString()} XML bytes</p>}</td></tr>)}
    </tbody></table></details>;
}
