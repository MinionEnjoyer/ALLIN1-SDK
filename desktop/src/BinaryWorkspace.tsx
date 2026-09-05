import { useEffect, useRef, useState } from "react";
import type { DesktopClient } from "./types";
import { AuthoringFeedback, useAuthoringWorkspace, type WorkspaceResult } from "./useAuthoringWorkspace";
import "./OfflineAuthoring.css";

export interface BinaryArchiveRequest { archive: string; entry_id: string; gta_path: string; requestId: number }
interface BinarySession extends WorkspaceResult {
  archive?: string | null; entry_id?: string | null; gta_path?: string | null;
  source: string; workspace: string | null; name: string; size: number; offset: number; length: number;
  bytes: number[]; original_bytes: number[]; revision: number; original_sha256: string; editable_sha256: string;
  history: { sequence: number; offset: number; length: number; created_utc: string }[];
}
const hex = (n: number, length = 2) => n.toString(16).toUpperCase().padStart(length, "0");
export default function BinaryWorkspace({ client, onGuardChange, archiveRequest }: { client: DesktopClient; onGuardChange: (guarded: boolean) => void; archiveRequest?: BinaryArchiveRequest | null }) {
  const [session, setSession] = useState<BinarySession | null>(null);
  const [offset, setOffset] = useState("0x0"), [expected, setExpected] = useState(""), [replacement, setReplacement] = useState("");
  const [name, setName] = useState("binary-copy"), [buildName, setBuildName] = useState("patched.bin"), [pageOffset, setPageOffset] = useState("0");
  const work = useAuthoringWorkspace(client, "binary", value => {
    const s = value as BinarySession;
    if (!Array.isArray(s.bytes) || !Array.isArray(s.original_bytes) || s.bytes.length !== s.original_bytes.length || !Array.isArray(s.history)
      || s.bytes.some(v => !Number.isInteger(v) || v < 0 || v > 255) || !Number.isSafeInteger(s.size) || s.size < 1)
      throw new Error("Invalid binary page evidence");
    setSession(s); setPageOffset(String(s.offset)); setExpected(""); setReplacement("");
  });
  const dirty = expected !== "" || replacement !== "";
  useEffect(() => { onGuardChange(dirty || work.locked); }, [dirty, work.locked, onGuardChange]);
  const context = session?.workspace ? { workspace: session.workspace } : session?.archive ? { archive: session.archive, entry_id: session.entry_id, gta_path: session.gta_path } : { source: session?.source };
  const handled = useRef(0);
  useEffect(() => {
    if (!archiveRequest || archiveRequest.requestId === handled.current) return;
    handled.current = archiveRequest.requestId;
    if (dirty || work.locked) { work.setError("Finish or discard the current binary draft before opening another archive member."); return; }
    const { requestId: _id, ...request } = archiveRequest;
    void work.run("inspect_authoring_workspace", request);
  }, [archiveRequest]);
  const open = async (kind: "binary_source" | "binary_workspace") => {
    const selected = await work.choose(kind);
    if (selected) await work.run("inspect_authoring_workspace", kind === "binary_workspace" ? { workspace: selected } : { source: selected });
  };
  const review = (action: string, extra = {}) => work.run("review_workspace_action", { ...context, expected_state_sha256: session?.state_sha256, action, ...extra });
  const destination = async (action: "create" | "build") => {
    const parent = await work.choose("authoring_parent");
    if (parent) await review(action, { destination: `${parent.replace(/[\\/]$/, "")}/${action === "create" ? name : buildName}` });
  };
  const page = (position: number) => work.run("inspect_authoring_workspace", { ...context, offset: position, length: 256 });
  return <section className="offline-workbench" aria-label="Binary editor"><div className="offline-toolbar"><div><h3>Binary editor</h3>
    <p>Exact bytes, same-size patches, and a verified change history. Originals remain untouched.</p></div><div className="heading-actions">
      <button className="primary-button" disabled={work.locked || dirty} onClick={() => void open("binary_source")}>Open binary</button>
      <button className="quiet-button" disabled={work.locked || dirty} onClick={() => void open("binary_workspace")}>Open binary workspace</button></div></div>
    <div className="source-strip"><strong>{session?.workspace ? `Editable copy · revision ${session.revision}` : "Read-only source"}</strong><span className="source-path">{session?.source || "No binary selected"}</span></div>
    <AuthoringFeedback work={work} />
    <div className="offline-panes binary-panes">
      <section><header><span className="pane-kicker">Asset</span><h4>Snapshot & history</h4></header><div className="offline-pane-body">
        {!session ? <p>Open an extracted asset or an existing binary workspace.</p> : <><h4>{session.name}</h4><p>{session.size.toLocaleString()} bytes · {session.revision} patches</p>
          <dl><dt>Original SHA-256</dt><dd className="hash-value">{session.original_sha256}</dd><dt>Current SHA-256</dt><dd className="hash-value">{session.editable_sha256}</dd></dl>
          {!session.workspace ? <><label>Workspace name<input value={name} disabled={work.locked} onChange={e => setName(e.target.value)} maxLength={100} /></label>
            <button className="quiet-button" disabled={work.locked || !name} onClick={() => void destination("create")}>Create binary copy</button></> : <>
              <button className="quiet-button" disabled={work.locked || dirty || !session.revision} onClick={() => void review("undo")}>Review undo latest patch</button>
              <p className="field-hint">Undo appends the inverse patch to the audit trail.</p>
              <ol className="binary-history">{session.history.map(row => <li key={row.sequence}>#{row.sequence} · 0x{hex(row.offset, 8)} · {row.length} bytes</li>)}</ol></>}
        </>}
      </div></section>
      <section><header><span className="pane-kicker">Bytes</span><h4>Hex & ASCII</h4></header><div className="offline-pane-body">
        {session ? <><div className="binary-pagination"><label>Page offset<input value={pageOffset} disabled={work.locked || dirty} onChange={e => setPageOffset(e.target.value)} /></label>
          <button className="quiet-button" disabled={work.locked || dirty || !Number.isSafeInteger(Number(pageOffset)) || Number(pageOffset) < 0 || Number(pageOffset) >= session.size} onClick={() => void page(Number(pageOffset))}>Go to offset</button>
          <button className="quiet-button" disabled={work.locked || dirty || !session.offset} onClick={() => void page(Math.max(0, session.offset - 256))}>Previous bytes</button>
          <button className="quiet-button" disabled={work.locked || dirty || session.offset + session.bytes.length >= session.size} onClick={() => void page(session.offset + 256)}>Next bytes</button></div>
          <p>Changed bytes are underlined. Select a byte to start a patch.</p>
          <div className="binary-hex-scroll"><table className="binary-hex"><thead><tr><th>Offset</th><th>Hexadecimal bytes</th><th>ASCII</th></tr></thead><tbody>
            {Array.from({ length: Math.ceil(session.bytes.length / 16) }, (_, row) => { const start = row * 16, bytes = session.bytes.slice(start, start + 16);
              return <tr key={start}><th>{hex(session.offset + start, 8)}</th><td>{bytes.map((value, i) => <button key={i} aria-label={`Byte ${hex(session.offset + start + i, 8)}: ${hex(value)}`} disabled={!session.workspace || work.locked || dirty}
                className={value !== session.original_bytes[start + i] ? "changed-byte" : ""} onClick={() => { setOffset(`0x${hex(session.offset + start + i)}`); setExpected(hex(value)); }}>{hex(value)}</button>)}</td>
                <td>{bytes.map(v => v >= 32 && v < 127 ? String.fromCharCode(v) : ".").join("")}</td></tr>; })}
          </tbody></table></div></> : <p>The selected byte range will appear here.</p>}
      </div></section>
      <section><header><span className="pane-kicker">Authoring</span><h4>Patch & build</h4></header><div className="offline-pane-body">
        <fieldset disabled={!session?.workspace || work.locked}><label>Patch offset<input value={offset} onChange={e => setOffset(e.target.value)} /></label>
          <label>Expected bytes<textarea value={expected} onChange={e => setExpected(e.target.value)} maxLength={24576} spellCheck={false} /></label>
          <label>Replacement bytes<textarea value={replacement} onChange={e => setReplacement(e.target.value)} maxLength={24576} spellCheck={false} /></label>
          <button className="primary-button" disabled={!expected || !replacement || !Number.isSafeInteger(Number(offset))} onClick={() => void review("patch", { offset: Number(offset), expected_hex: expected, replacement_hex: replacement })}>Review binary patch</button>
          {dirty && <button className="quiet-button" onClick={() => { setExpected(""); setReplacement(""); }}>Discard patch draft</button>}
        </fieldset>
        <fieldset disabled={!session?.workspace || work.locked || dirty}><label>Output filename<input value={buildName} onChange={e => setBuildName(e.target.value)} maxLength={100} /></label>
          <button className="quiet-button" disabled={!buildName || session?.editable_sha256 === session?.original_sha256} onClick={() => void destination("build")}>Review binary build</button>
          <p>A new binary and a SHA-256-bound diff report are written together. Nothing is installed.</p></fieldset>
      </div></section>
    </div>
  </section>;
}
