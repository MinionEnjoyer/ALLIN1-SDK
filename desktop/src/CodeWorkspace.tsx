import { useEffect, useState } from "react";
import CodeEditor from "./CodeEditor";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import type { DesktopClient } from "./types";
import "./code-editor.css";

type Validation = { valid: boolean; scope: string; diagnostics: { line: number; column: number; message: string }[] };
type Session = WorkspaceResult & { source: string | null; name: string; language: "xml" | "lua";
  chunks: string[]; validation: Validation; line_ending: "LF" | "CRLF"; can_save: boolean; draft_check: boolean };
const chunks = (text: string) => text.match(/[\s\S]{1,8192}/gu) ?? [];

export default function CodeWorkspace({ client, onGuardChange }: { client: DesktopClient; onGuardChange: (value: boolean) => void }) {
  const [session, setSession] = useState<Session | null>(null), [draft, setDraft] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null), [validatedDraft, setValidatedDraft] = useState("");
  const [filename, setFilename] = useState("document.xml");
  const work = useAuthoringWorkspace(client, "code", result => {
    const value = result as Session;
    setValidation(value.validation); setValidatedDraft(value.chunks.join(""));
    if (!value.draft_check) {
      setSession(value); setDraft(value.chunks.join("")); setFilename(value.name);
    }
  });
  const dirty = Boolean(session && (draft !== session.chunks.join("") || !session.source));
  useEffect(() => { onGuardChange(dirty || work.locked); }, [dirty, work.locked, onGuardChange]);
  const context = { ...(session?.source ? { source: session.source } : {}), document: { language: session?.language ?? "xml", chunks: chunks(draft) } };
  const open = async () => {
    const selected = await work.choose("code_source");
    if (selected) await work.run("inspect_authoring_workspace", { source: selected });
  };
  const save = async (copy: boolean) => {
    if (!session || work.locked) return;
    let destination: string | undefined;
    if (copy) {
      if (!/^[\w][\w .-]{0,100}\.(xml|meta|lua)$/i.test(filename)) { work.setError("Use a simple new .xml, .meta or .lua filename."); return; }
      const parent = await work.choose("authoring_parent");
      if (!parent) return;
      destination = parent.replace(/[\\/]$/, "") + "/" + filename;
    }
    await work.run("review_workspace_action", { ...context, action: copy ? "save_copy" : "save",
      expected_state_sha256: session.state_sha256, ...(destination ? { destination } : {}) });
  };
  return <section className="workspace-section code-workspace" aria-label="XML and Lua editor">
    <div className="section-heading"><div><span className="eyebrow">Source authoring</span><h2>XML &amp; Lua</h2>
      <p>Edit source, check syntax, then review exactly what will be saved. Scripts are never executed.</p></div></div>
    <div className="heading-actions">
      <button disabled={work.locked || dirty} onClick={() => void open()}>Open XML / Lua</button>
      {(["xml", "lua"] as const).map(language => <button key={language} disabled={work.locked || dirty}
        onClick={() => void work.run("inspect_authoring_workspace", { document: { language } })}>New {language.toUpperCase()}</button>)}
      <button disabled={work.locked || !session} onClick={() => void work.run("inspect_authoring_workspace", context)}>Check syntax</button>
      <button className="primary-button" disabled={work.locked || !dirty || !session?.can_save} onClick={() => void save(false)}>Review save</button>
      <button disabled={work.locked || !session} onClick={() => { setSession(null); setDraft(""); setValidation(null); }}>Close / discard draft</button>
    </div>
    {session ? <>
      <div className="code-document-strip"><span title={session.source ?? "New file"}>{session.source ?? session.name}</span>
        <span>{dirty ? "Unsaved draft" : "Saved"} · UTF-8 · {session.line_ending} · {session.language.toUpperCase()}</span></div>
      <CodeEditor key={session.source ?? session.name} value={draft} language={session.language} lineEnding={session.line_ending} locked={work.locked} onChange={setDraft} />
      <div className="code-save-copy"><label>New copy filename<input value={filename} disabled={work.locked} onChange={event => setFilename(event.target.value)} /></label>
        <button disabled={work.locked} onClick={() => void save(true)}>Review save a copy</button>
        {!session.can_save && <p>New documents and files inside GTA must be saved to a new file outside the game.</p>}</div>
      <section className="code-diagnostics" aria-label="Syntax diagnostics" aria-live="polite">
        <strong>{validatedDraft !== draft ? "Draft changed — check syntax again" : validation?.valid ? "Syntax check passed" : "Syntax errors"}</strong>
        <p>{validation?.scope}. This is not game compatibility or runtime certification.</p>
        {validatedDraft === draft && validation?.diagnostics.map((item, index) => <p key={index}>Line {item.line}, column {item.column}: {item.message}</p>)}
      </section>
    </> : <div className="pane-empty"><strong>Source files, with a deliberate save boundary</strong><p>Open text XML/META or Lua, or start a new document. UTF-8, up to 64 KiB and 2,000 lines. Compiled resources and FiveM-specific Lua syntax extensions are not supported here.</p></div>}
    {work.review && <section className="code-diff" aria-label="Code save diff"><h3>Save diff</h3>
      <pre>{String(work.review.value.diff) || "Unchanged content; a new copy will be created."}</pre>
      {work.review.value.diff_truncated === true && <p>Diff preview is abbreviated. The save is bound to the entire draft.</p>}
      {Boolean(work.review.value.backup) && <p>Previous bytes retained at {String(work.review.value.backup)}</p>}
    </section>}
    <AuthoringFeedback work={work} />
  </section>;
}
