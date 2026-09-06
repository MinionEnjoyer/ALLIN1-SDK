export type LogSelection={source:string;start_line:number;line_count:number};
export type LogSettings={logs:LogSelection[];redact_terms:string[]};
export type LogBundle={preview_sha256:string;privacy_scope:string;association:string;logs:{id:string;output:string;source_sha256:string;source_lines:number;included_lines:number;redacted_lines:number;truncated_lines:number;lines:{line:number;text:string}[]}[]};
export default function DiagnosticLogSelection({settings,preview,locked,reviewed,onReviewed,onChange,choose}:{settings:LogSettings;preview?:LogBundle;locked:boolean;reviewed:boolean;onReviewed:(value:boolean)=>void;onChange:(value:LogSettings)=>void;choose:()=>Promise<string|null>}) {
  return <details className="asset-validation"><summary>Selected diagnostic log excerpts · {settings.logs.length}</summary><div className="asset-validation-body">
    <p>Choose stable .log/.txt snapshots. Limits: 8 files, 2 MiB each / 8 MiB total, 512 selected lines, 96 KiB of redacted text. Nothing is scanned or uploaded automatically.</p>
    <fieldset disabled={locked}><legend>Explicit file and line selection</legend>
      <button disabled={settings.logs.length>=8} onClick={async()=>{const source=await choose();if(source&&!settings.logs.some(row=>row.source===source))onChange({...settings,logs:[...settings.logs,{source,start_line:1,line_count:50}]});}}>Choose diagnostic log</button>
      {settings.logs.map((row,i)=><section key={row.source}><p>Log {i+1}: {row.source}</p>
        <label>Start line<input aria-label={`Log ${i+1} start line`} type="number" min={1} value={row.start_line} onChange={e=>onChange({...settings,logs:settings.logs.map((r,n)=>n===i?{...r,start_line:Number(e.target.value)}:r)})}/></label>
        <label>Line count<input aria-label={`Log ${i+1} line count`} type="number" min={1} max={512} value={row.line_count} onChange={e=>onChange({...settings,logs:settings.logs.map((r,n)=>n===i?{...r,line_count:Number(e.target.value)}:r)})}/></label>
        <button onClick={()=>onChange({...settings,logs:settings.logs.filter((_,n)=>n!==i)})}>Remove diagnostic log {i+1}</button>
      </section>)}
      <label>Additional private terms (one per line)<textarea value={settings.redact_terms.join("\n")} onChange={e=>onChange({...settings,redact_terms:e.target.value.split("\n")})}/></label>
      <p>Review free-form text yourself. Add usernames, machine names or other private terms that pattern redaction missed; empty lines are ignored.</p>
    </fieldset>
    {preview&&preview.logs.length>0&&<section aria-label="Redacted diagnostic preview"><p>{preview.privacy_scope}</p><p>{preview.association}</p>
      {preview.logs.map(row=><details key={row.id} open><summary>{row.id} · {row.included_lines} included / {row.source_lines} source lines</summary>
        <p>Redacted lines: {row.redacted_lines}; truncated lines: {row.truncated_lines}. Original SHA-256 {row.source_sha256}</p>
        <pre>{row.lines.map(line=>`${line.line}: ${line.text}`).join("\n")}</pre>
      </details>)}
      <label><input type="checkbox" checked={reviewed} disabled={locked} onChange={e=>onReviewed(e.target.checked)}/>I reviewed these exact redacted excerpts and want them included in the local export.</label>
    </section>}
  </div></details>;
}
