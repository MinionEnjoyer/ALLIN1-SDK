import { useEffect, useState } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";

const conversions = [
  ["managed", "Managed package", "Copy declared payloads into a validated ALLIN1 package."],
  ["batches", "RPF batch manifests", "Export ordered file changes and payloads. Does not execute them."],
  ["created", "Created RPF package", "Build newly declared archives and a managed package."],
  ["compile", "Compile recipe bundle", "Evaluate supported XML/text/PSO edits on copies and export an inert plan."],
] as const;
type Conversion = typeof conversions[number][0];
interface RecipeSession extends WorkspaceResult { capabilities: Record<Conversion, boolean> }

export default function RecipeConversionPanel({ client, source, disabled = false, onGuardChange }: {
  client: DesktopClient; source: string; disabled?: boolean; onGuardChange: (guarded: boolean) => void;
}) {
  const [session, setSession] = useState<RecipeSession | null>(null);
  const [action, setAction] = useState<Conversion>("managed");
  const [name, setName] = useState("converted-recipe"), [game, setGame] = useState(""), [archive, setArchive] = useState("");
  const work = useAuthoringWorkspace(client, "recipe", value => {
    const loaded = value as RecipeSession;
    if (loaded.source !== source || !loaded.capabilities || conversions.some(([key]) => typeof loaded.capabilities[key] !== "boolean"))
      throw new Error("Recipe conversion evidence does not match the selected source");
    setSession(loaded);
    const available = conversions.find(([key]) => loaded.capabilities[key]);
    if (available) setAction(available[0]);
  });
  useEffect(() => { onGuardChange(work.locked); }, [work.locked, onGuardChange]);
  const locked = disabled || work.locked;
  const needsDecoder = action === "created" || action === "compile";
  const review = async () => {
    if (!session || locked) return;
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._ -]{0,80}$/.test(name) || /[. ]$/.test(name)) { work.setError("Use a simple new output folder name, without path separators or trailing dots/spaces."); return; }
    const parent = await work.choose("authoring_parent");
    if (parent) void work.run("review_workspace_action", { source, action, destination: `${parent}/${name}`,
      expected_state_sha256: session.state_sha256, ...(needsDecoder ? { gta_path: game } : {}), ...(action === "compile" ? { archive } : {}) });
  };
  return <section className="offline-workbench recipe-conversion" aria-label="Recipe conversion">
    <div className="workspace-heading"><div><h3>Offline conversion</h3><p>New outputs only. Existing archives and game files remain unchanged.</p></div>
      <button className="quiet-button" disabled={locked || !source} onClick={() => void work.run("inspect_authoring_workspace", { source })}>Inspect conversion options</button></div>
    {session && <div className="offline-panes"><section><header><h4>Conversion</h4></header><div className="offline-pane-body">
      <label>Conversion type<select aria-label="Conversion type" disabled={locked} value={action} onChange={e => setAction(e.target.value as Conversion)}>
        {conversions.map(([key, title]) => <option key={key} value={key} disabled={!session.capabilities[key]}>{title}{!session.capabilities[key] ? " · unavailable" : ""}</option>)}
      </select></label><p>{conversions.find(([key]) => key === action)?.[2]}</p>
      {!conversions.some(([key]) => session.capabilities[key]) && <p role="status">No supported conversion. Resolve the recipe findings first.</p>}
      <label>New output folder<input aria-label="Recipe output folder" value={name} disabled={locked} onChange={e => setName(e.target.value)} /></label>
    </div></section><section><header><h4>Read-only inputs</h4></header><div className="offline-pane-body">
      {needsDecoder ? <><button className="quiet-button" disabled={locked} onClick={async () => { const p = await work.choose("gta_folder"); if (p) setGame(p); }}>Choose recipe decoder context</button><p className="source-path">{game || "No decoder context selected"}</p></> : <p>This conversion does not require a decoder context.</p>}
      {action === "compile" && <><button className="quiet-button" disabled={locked} onClick={async () => { const p = await work.choose("rpf"); if (p) setArchive(p); }}>Choose recipe outer archive</button><p className="source-path">{archive || "Select the exact existing outer archive named in the recipe"}</p></>}
      <button className="primary-button" disabled={locked || !session.capabilities[action] || (needsDecoder && !game) || (action === "compile" && !archive)} onClick={() => void review()}>Review recipe conversion</button>
    </div></section></div>}
    <AuthoringFeedback work={work} />
    {work.lastResult && <div role="status"><p>{String(work.lastResult.file_count)} output files verified by SHA-256.</p>
      {work.lastResult.inert_plan_only === true && <p>Inert plan only. Review it separately in the RPF tools; no archive was changed.</p>}
      <ul>{(work.lastResult.reports as string[] ?? []).map(report => <li key={report}><code>{report}</code></li>)}</ul></div>}
  </section>;
}
