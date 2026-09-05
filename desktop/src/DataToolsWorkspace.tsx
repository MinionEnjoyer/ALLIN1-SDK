import { useEffect, useState } from "react";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import type { DesktopClient } from "./types";
import CodeWorkspace from "./CodeWorkspace";

const TOOLS = {
  meta_diff: { title: "Compare metadata", detail: "Compare two META/XML files by values and records." },
  meta_roundtrip: { title: "Validate metadata round trip", detail: "Check that parsing and serialization preserve the metadata." },
  vehicle_data: { title: "Compile vehicle data", detail: "Join vehicle metadata and asset references into JSON, CSV and workbook reports." },
  dlc_inventory: { title: "Inventory installed DLC", detail: "Inspect one selected installation for registered, missing and externally owned DLC." },
};
type Tool = keyof typeof TOOLS;

export default function DataToolsWorkspace({ client, onGuardChange }: { client: DesktopClient; onGuardChange: (value: boolean) => void }) {
  const [area, setArea] = useState<"reports" | "code">("reports");
  const [guarded, setGuarded] = useState(false);
  useEffect(() => { onGuardChange(guarded); }, [guarded, onGuardChange]);
  return <><nav className="models-area-tabs" aria-label="Data Tools area">
    <button aria-current={area === "reports" ? "page" : undefined} disabled={guarded} onClick={() => setArea("reports")}>Metadata reports</button>
    <button aria-current={area === "code" ? "page" : undefined} disabled={guarded} onClick={() => setArea("code")}>XML &amp; Lua editor</button>
  </nav>{area === "code" ? <CodeWorkspace client={client} onGuardChange={setGuarded} /> : <DataReports client={client} onGuardChange={setGuarded} />}</>;
}

function DataReports({ client, onGuardChange }: { client: DesktopClient; onGuardChange: (value: boolean) => void }) {
  const [task, setTask] = useState<Tool>("meta_diff");
  const [source, setSource] = useState(""), [comparison, setComparison] = useState("");
  const [session, setSession] = useState<WorkspaceResult | null>(null);
  const work = useAuthoringWorkspace(client, "data_tools", setSession);
  useEffect(() => { onGuardChange(work.locked); }, [work.locked, onGuardChange]);
  const choose = async (kind: "metadata" | "package" | "package_folder" | "gta_folder", other = false) => {
    if (work.locked) return;
    try {
      const selected = await work.choose(kind);
      if (selected) { (other ? setComparison : setSource)(selected); setSession(null); }
    } catch (error) { work.setError(String(error)); }
  };
  const request = { task, source, ...(task === "meta_diff" ? { comparison } : {}) };
  const exportReport = async () => {
    if (!session || work.locked) return;
    const parent = await work.choose("authoring_parent");
    if (parent) void work.run("review_workspace_action", { ...request, action: "export",
      expected_state_sha256: session.state_sha256, destination: `${parent}/${task.replaceAll("_", "-")}-report` });
  };
  const report = session?.document as Record<string, unknown> | undefined;
  const rows = (report?.changes ?? report?.vehicles ?? report?.packs) as Record<string, unknown>[] | undefined;
  return <section className="workspace-section" aria-label="Data tools">
    <div className="section-heading"><div><span className="eyebrow">Metadata and reports</span><h2>Data Tools</h2><p>Inspect your inputs, then export the reviewed report to a new folder.</p></div></div>
    <nav className="models-area-tabs" aria-label="Data tool">
      {Object.entries(TOOLS).map(([key, value]) => <button key={key} className={task === key ? "selected" : ""} disabled={work.locked} aria-current={task === key ? "page" : undefined} onClick={() => { setTask(key as Tool); setSource(""); setComparison(""); setSession(null); }}><span>{value.title}</span></button>)}
    </nav>
    <p>{TOOLS[task].detail}</p>
    <div className="heading-actions">
      <button disabled={work.locked} onClick={() => void choose(task === "dlc_inventory" ? "gta_folder" : task === "vehicle_data" ? "package" : "metadata")}>Choose {task === "dlc_inventory" ? "installation" : "source"}</button>
      {task === "vehicle_data" && <button disabled={work.locked} onClick={() => void choose("package_folder")}>Choose package folder</button>}
      {task === "meta_diff" && <button disabled={work.locked} onClick={() => void choose("metadata", true)}>Choose comparison</button>}
      <button className="primary-button" disabled={work.locked || !source || (task === "meta_diff" && !comparison)} onClick={() => void work.run("inspect_authoring_workspace", request)}>Inspect data</button>
      <button disabled={work.locked || !session} onClick={() => void exportReport()}>Review report export</button>
    </div>
    <dl><div><dt>Source</dt><dd>{source || "No source selected"}</dd></div>{task === "meta_diff" && <div><dt>Comparison</dt><dd>{comparison || "No comparison selected"}</dd></div>}</dl>
    <AuthoringFeedback work={work} />
    {Boolean(work.lastResult?.destination) && <p role="status">Reports saved to {String(work.lastResult?.destination)}</p>}
    {report && <section aria-label="Data report"><h3>{TOOLS[task].title}</h3>
      {report.semantically_equivalent !== undefined && <p>Semantic equivalence: {report.semantically_equivalent ? "PASS" : "FAIL"}</p>}
      {rows && <div className="data-report-scroll"><table><thead><tr><th>Record</th><th>Details</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index}><th scope="row">{String(row.path ?? row.model ?? row.name ?? index + 1)}</th><td>{task === "meta_diff" ? `${String(row.before ?? "(missing)")} → ${String(row.after ?? "(missing)")}` : JSON.stringify(row)}</td></tr>)}</tbody></table></div>}
      <details><summary>Complete structured report</summary><pre>{JSON.stringify(report, null, 2)}</pre></details>
    </section>}
  </section>;
}
