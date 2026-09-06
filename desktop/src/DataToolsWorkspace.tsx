import { useEffect, useState } from "react";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import type { DesktopClient } from "./types";
import CodeWorkspace from "./CodeWorkspace";
import AssetValidationReport, {type AssetReport} from "./AssetValidationReport";
import OptimizationWorkspace,{type OptimizationContext} from "./OptimizationWorkspace";
import SharedRigBindings,{type DrawableCandidate,type RigBinding} from "./SharedRigBindings";
import DiagnosticLogSelection,{type LogBundle,type LogSettings} from "./DiagnosticLogSelection";

const TOOLS = {
  diagnostic_trail: {title:"Trace build to game", detail:"Compare an SDK artifact manifest, Launcher install receipt, selected installation, and optional runtime session. File mismatches are evidence; crash causes require more than version labels or timestamps."},
  asset_validation: {title: "Validate asset package", detail: "Check a package folder's models and metadata identities with exact per-file coverage. Decoding alone is not game acceptance."},
  meta_diff: { title: "Compare metadata", detail: "Compare two META/XML files by values and records." },
  meta_roundtrip: { title: "Validate metadata round trip", detail: "Check that parsing and serialization preserve the metadata." },
  vehicle_data: { title: "Compile vehicle data", detail: "Join vehicle metadata and asset references into JSON, CSV and workbook reports." },
  dlc_inventory: { title: "Inventory installed DLC", detail: "Inspect one selected installation for registered, missing and externally owned DLC." },
};
type Tool = keyof typeof TOOLS;

export default function DataToolsWorkspace({ client, onGuardChange }: { client: DesktopClient; onGuardChange: (value: boolean) => void }) {
  const [area, setArea] = useState<"reports" | "code" | "optimization">("reports");
  const [guarded, setGuarded] = useState(false);
  const [optimizationContext,setOptimizationContext]=useState<OptimizationContext|undefined>();
  useEffect(() => { onGuardChange(guarded); }, [guarded, onGuardChange]);
  return <><nav className="models-area-tabs" aria-label="Data Tools area">
    <button aria-current={area === "reports" ? "page" : undefined} disabled={guarded} onClick={() => setArea("reports")}>Metadata reports</button>
    <button aria-current={area === "code" ? "page" : undefined} disabled={guarded} onClick={() => setArea("code")}>XML, JSON &amp; Lua editor</button>
    <button aria-current={area === "optimization" ? "page" : undefined} disabled={guarded} onClick={() => setArea("optimization")}>Optimize &amp; recover</button>
  </nav>{area === "optimization" ? <OptimizationWorkspace client={client} onGuardChange={setGuarded} initialContext={optimizationContext}/> : area === "code" ? <CodeWorkspace client={client} onGuardChange={setGuarded} /> : <DataReports client={client} onGuardChange={setGuarded} onOptimize={context=>{setOptimizationContext(context);setArea("optimization");}} />}</>;
}

function DataReports({ client, onGuardChange,onOptimize }: { client: DesktopClient; onGuardChange: (value: boolean) => void;onOptimize:(context:OptimizationContext)=>void }) {
  const [task, setTask] = useState<Tool>("meta_diff");
  const [source, setSource] = useState(""), [comparison, setComparison] = useState("");
  const [edition,setEdition] = useState(""), [game,setGame] = useState("");
  const [runtimeSession,setRuntimeSession]=useState("");
  const [crashEvent,setCrashEvent]=useState("");
  const [session, setSession] = useState<WorkspaceResult | null>(null);
  const [rigChoices,setRigChoices]=useState<DrawableCandidate[]>([]),[rigBindings,setRigBindings]=useState<RigBinding[]>([]);
  const [rigDirty,setRigDirty]=useState(false);
  const [logSettings,setLogSettings]=useState<LogSettings>({logs:[],redact_terms:[]}),[logsReviewed,setLogsReviewed]=useState(false),[logsDirty,setLogsDirty]=useState(false);
  const resetContext=()=>{setSession(null);setRigChoices([]);setRigBindings([]);setRigDirty(false);setLogsReviewed(false);};
  const work = useAuthoringWorkspace(client, "data_tools", value=>{setSession(value);setRigDirty(false);setLogsDirty(false);setLogsReviewed(false);const document=value.document as {drawable_candidates?:DrawableCandidate[]}|undefined;setRigChoices(document?.drawable_candidates??[]);});
  useEffect(() => { onGuardChange(work.locked||rigDirty||logsDirty); }, [work.locked,rigDirty,logsDirty,onGuardChange]);
  const choose = async (kind: "metadata" | "package" | "package_folder" | "gta_folder" | "code_source" | "rpf", other = false) => {
    if (work.locked) return;
    try {
      const selected = await work.choose(kind);
      if (selected) { (other ? setComparison : setSource)(selected); resetContext(); }
    } catch (error) { work.setError(String(error)); }
  };
  const request = { task, source, ...(["meta_diff","asset_validation","diagnostic_trail"].includes(task) && comparison ? {comparison} : {}),
    ...(task === "asset_validation" ? {edition:edition||undefined,gta_path:game||undefined,settings:{rig_bindings:rigBindings}} : {}),
    ...(task === "diagnostic_trail" ? {gta_path:game||undefined,document:runtimeSession||undefined,crash_event:crashEvent||undefined,settings:{...logSettings,redact_terms:logSettings.redact_terms.filter(term=>term.length>0)}} : {}) };
  const exportReport = async () => {
    if (!session || work.locked) return;
    const parent = await work.choose("authoring_parent");
    if (parent) void work.run("review_workspace_action", { ...request, action: "export",
      ...(task==="diagnostic_trail"&&logsReviewed ? {privacy_review_sha256:(session.document as {log_bundle?:LogBundle})?.log_bundle?.preview_sha256}:{}),
      expected_state_sha256: session.state_sha256, destination: `${parent}/${task.replaceAll("_", "-")}-report` });
  };
  const report = session?.document as Record<string, unknown> | undefined;
  const rows = (report?.changes ?? report?.vehicles ?? report?.packs) as Record<string, unknown>[] | undefined;
  return <section className="workspace-section" aria-label="Data tools">
    {task==="asset_validation"&&report&&<button disabled={work.locked||rigDirty} onClick={()=>onOptimize({source,comparison,edition,game,rigBindings})}>Optimize with this validation context</button>}
    <div className="section-heading"><div><span className="eyebrow">Metadata and reports</span><h2>Data Tools</h2><p>Inspect your inputs, then export the reviewed report to a new folder.</p></div></div>
    <nav className="models-area-tabs" aria-label="Data tool">
      {Object.entries(TOOLS).map(([key, value]) => <button key={key} className={task === key ? "selected" : ""} disabled={work.locked||rigDirty||logsDirty} aria-current={task === key ? "page" : undefined} onClick={() => { setTask(key as Tool); setSource(""); setComparison(""); resetContext(); }}><span>{value.title}</span></button>)}
    </nav>
    <p>{TOOLS[task].detail}</p>
    <div className="heading-actions">
      <button disabled={work.locked} onClick={() => void choose(task === "diagnostic_trail" ? "code_source" : task === "asset_validation" ? "package_folder" : task === "dlc_inventory" ? "gta_folder" : task === "vehicle_data" ? "package" : "metadata")}>Choose {task === "dlc_inventory" ? "installation" : task === "diagnostic_trail" ? "artifact manifest" : "source"}</button>
      {task === "diagnostic_trail" && <>
        <button disabled={work.locked} onClick={()=>void choose("code_source",true)}>Choose installation receipt</button>
        <button disabled={work.locked} onClick={async()=>{const value=await work.choose("gta_folder");if(value){setGame(value);setSession(null);}}}>Choose diagnostic installation</button>
        <button disabled={work.locked} onClick={async()=>{const value=await work.choose("code_source");if(value){setRuntimeSession(value);setSession(null);}}}>Choose runtime session</button>
        {runtimeSession&&<button disabled={work.locked} onClick={()=>{setRuntimeSession("");setSession(null);}}>Clear runtime session</button>}
        <button disabled={work.locked} onClick={async()=>{const value=await work.choose("metadata");if(value){setCrashEvent(value);setSession(null);}}}>Choose crash event XML</button>
        {crashEvent&&<button disabled={work.locked} onClick={()=>{setCrashEvent("");setSession(null);}}>Clear crash event</button>}
        <p>Installation: {game||"Not selected"}<br/>Runtime session: {runtimeSession||"Not supplied — runtime remains unverified"}</p>
        <p>Crash evidence: {crashEvent||"Optional — exported Windows Application Error event XML"}. Up to 32 events / 1 MiB; no dump, EVTX, registry changes or upload.</p>
      </>}
      {task === "asset_validation" && <>
        <button disabled={work.locked} onClick={()=>void choose("rpf")}>Choose RPF archive</button>
        <button disabled={work.locked} onClick={()=>void choose("package_folder",true)}>Choose comparison context</button>
        {comparison && <button disabled={work.locked} onClick={()=>{setComparison("");resetContext();}}>Clear comparison context</button>}
        <label>Asset edition<select aria-label="Package validation edition" value={edition} disabled={work.locked} onChange={e=>{setEdition(e.target.value);resetContext();}}><option value="">XML-only / native unverified</option><option>Legacy</option><option>Enhanced</option></select></label>
        <button disabled={work.locked} onClick={async()=>{const value=await work.choose("gta_folder");if(value){setGame(value);resetContext();}}}>Choose decoder installation</button>
        {game && <><span>{game}</span><button disabled={work.locked} onClick={()=>{setGame("");resetContext();}}>Clear decoder installation</button></>}
      </>}
      {task === "vehicle_data" && <button disabled={work.locked} onClick={() => void choose("package_folder")}>Choose package folder</button>}
      {task === "meta_diff" && <button disabled={work.locked} onClick={() => void choose("metadata", true)}>Choose comparison</button>}
      <button className="primary-button" disabled={work.locked || !source || (["meta_diff","diagnostic_trail"].includes(task) && !comparison) || (task === "diagnostic_trail" && !game)} onClick={() => void work.run("inspect_authoring_workspace", request)}>Inspect data</button>
      <button disabled={work.locked || !session || (task==="diagnostic_trail"&&logSettings.logs.length>0&&!logsReviewed)} onClick={() => void exportReport()}>Review report export</button>
    </div>
    <dl><div><dt>Source</dt><dd>{source || "No source selected"}</dd></div>{["meta_diff","asset_validation","diagnostic_trail"].includes(task) && <div><dt>Comparison</dt><dd>{comparison || "No comparison selected"}</dd></div>}</dl>
    <AuthoringFeedback work={work} />
    {task==="diagnostic_trail"&&<DiagnosticLogSelection settings={logSettings} preview={report?.log_bundle as LogBundle|undefined} locked={work.locked} reviewed={logsReviewed} onReviewed={setLogsReviewed} choose={()=>work.choose("binary_source")}
      onChange={value=>{setLogSettings(value);setSession(null);setLogsReviewed(false);setLogsDirty(true);}}/>}
    {logsDirty&&<button disabled={work.locked} onClick={()=>{setLogSettings({logs:[],redact_terms:[]});setSession(null);setLogsDirty(false);setLogsReviewed(false);}}>Discard diagnostic log draft</button>}
    {task==="asset_validation"&&rigChoices.length>0&&<SharedRigBindings candidates={rigChoices} bindings={rigBindings} locked={work.locked} onChange={value=>{setRigBindings(value);setSession(null);setRigDirty(true);}}/>}
    {rigDirty&&<button disabled={work.locked} onClick={()=>{setRigBindings([]);setRigDirty(false);setSession(null);}}>Discard shared-rig draft</button>}
    {Boolean(work.lastResult?.destination) && <p role="status">Reports saved to {String(work.lastResult?.destination)}</p>}
    {report && <section aria-label="Data report"><h3>{TOOLS[task].title}</h3>
      {task === "asset_validation" && <AssetValidationReport report={report as unknown as AssetReport} locked={work.locked} onExport={()=>void exportReport()}/>}
      {task === "diagnostic_trail" && <details className="asset-validation" open><summary>Build-to-game evidence · cause not established</summary><div className="asset-validation-body">
        <p>Artifact {String(report.artifact_id)}<br/>Build {String(report.build_fingerprint)}<br/>Session {String(report.session_id??"not supplied")}</p>
        <p>Private installation paths are redacted in the exported derived report. No source files or logs are uploaded.</p>
        {Boolean(report.crash_evidence)&&<details><summary>Correlated crash-event evidence</summary><pre>{JSON.stringify(report.crash_evidence,null,2)}</pre></details>}
        {Array.isArray(report.rpf_members)&&report.rpf_members.length>0&&<details><summary>Installed RPF member identities · {report.rpf_members.length}</summary><div className="data-report-scroll"><table><caption>Current installed RPF members</caption><thead><tr><th>Archive / member</th><th>Result</th><th>Extracted bytes</th></tr></thead><tbody>
          {(report.rpf_members as {archive:string;entry:string;status:string;actual_sha256:string|null;expected_sha256:string|null;reason?:string}[]).map((row,i)=><tr key={i}><th scope="row">{row.archive}<br/>{row.entry}</th><td>{row.status}{row.reason&&<p>{row.reason}</p>}</td><td>Actual {row.actual_sha256??"not verified"}<br/>Selected artifact {row.expected_sha256??"not included"}</td></tr>)}
        </tbody></table></div></details>}
        <ul>{((report.findings??[]) as {level:string;message:string;code:string;evidence:Record<string,unknown>}[]).map((finding,index)=><li key={index}><strong>{finding.level}</strong> · {finding.message}<details><summary>{finding.code}</summary><pre>{JSON.stringify(finding.evidence,null,2)}</pre></details></li>)}</ul>
      </div></details>}
      {report.semantically_equivalent !== undefined && <p>Semantic equivalence: {report.semantically_equivalent ? "PASS" : "FAIL"}</p>}
      {rows && <div className="data-report-scroll"><table><thead><tr><th>Record</th><th>Details</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index}><th scope="row">{String(row.path ?? row.model ?? row.name ?? index + 1)}</th><td>{task === "meta_diff" ? `${String(row.before ?? "(missing)")} → ${String(row.after ?? "(missing)")}` : JSON.stringify(row)}</td></tr>)}</tbody></table></div>}
      <details><summary>Complete structured report</summary><pre>{JSON.stringify(report, null, 2)}</pre></details>
    </section>}
  </section>;
}
