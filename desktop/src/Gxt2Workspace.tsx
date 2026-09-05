import { useEffect, useRef, useState } from "react";
import type { DesktopClient, Envelope } from "./types";
import "./Gxt2Workspace.css";

export interface Gxt2ArchiveRequest { archive: string; entry_id: string; gta_path: string; requestId: number }
export interface Gxt2ArchiveBinding {
  outer_archive: string; outer_archive_sha256: string; entry_id: string; edition: string; gta_path?: string;
}
export interface Gxt2Session {
  kind: "gxt2_session"; workspace: string | null; source: string; name: string; state_sha256: string;
  original_sha256: string; revision: number; can_undo: boolean; entry_count: number; match_count: number;
  offset: number; page_size: number; query: string; read_only: boolean; game_write_performed: boolean;
  entries: { hash: number; hash_hex: string; preview: string }[];
  selected: { hash: number; hash_hex: string; text: string | null; editable: boolean; text_length: number } | null;
  history: { sequence: number; action: string; created_utc: string }[];
  source_binding: Gxt2ArchiveBinding | null;
}
export interface Gxt2Review {
  kind: "gxt2_review"; action: string; source: string; destination: string | null; revision: number;
  state_sha256: string; review_sha256: string; label_hash: number | null; before: string | null; after: string | null;
  entry_count: number; output_sha256: string | null; review_only: boolean; game_write_performed: boolean;
  source_binding: Gxt2ArchiveBinding | null;
  rpf_package?: Gxt2RpfReview;
  rpf_publication?: Gxt2RpfPublication;
}
export interface RpfPackageMetadata { id: string; name: string; version: string; author: string; target: string }
export type RpfPublicationMode = "whole_archive" | "member";
export interface Gxt2RpfPublication {
  source_package: string; metadata: RpfPackageMetadata; edition: string; archive_sha256: string;
  members: { path: string; size: number; sha256: string }[]; total_bytes: number; required_free_bytes: number;
  manifest_text: string; whole_archive_replacement: boolean; install_performed: boolean;
  dlc_registration_performed: boolean; upload_performed: boolean;
  publication_mode: RpfPublicationMode; manifest_schema_version: number; entry: string | null;
  original_sha256: string | null; payload_sha256: string;
}
const initialMetadata = (): RpfPackageMetadata => ({ id: "game-text-patch", name: "Game text patch", version: "1.0.0", author: "", target: "" });
const normalizedPath = (value: unknown) => typeof value === "string" ? value.replaceAll("\\", "/") : "";
const packageMember = (binding: Gxt2ArchiveBinding | null | undefined) => binding?.entry_id.startsWith("::") ? binding.entry_id.slice(2) : binding?.entry_id.replace("::", "!");
const memberSchema = (binding: Gxt2ArchiveBinding | null | undefined) => binding?.entry_id.startsWith("::") ? 3 : 4;
function validPublication(value: Gxt2RpfPublication | undefined, session: Gxt2Session, source: string, metadata: RpfPackageMetadata, mode: RpfPublicationMode) {
  const archiveName = session.source_binding?.outer_archive.split(/[\\/]/).pop();
  const memberOnly = mode === "member", payloadPath = memberOnly ? "payload/replacement.gxt2" : `payload/${archiveName}`;
  const expected = ["README.txt", "allin1.rpf-build.json", "mod.toml", payloadPath].sort();
  return !!value && normalizedPath(value.source_package) === normalizedPath(source)
    && value.publication_mode === mode && value.manifest_schema_version === (memberOnly ? memberSchema(session.source_binding) : 1)
    && (memberOnly ? packageMember(session.source_binding) === value.entry && value.original_sha256 === session.original_sha256
      : value.entry === null && value.original_sha256 === null && value.payload_sha256 === value.archive_sha256)
    && SHA.test(value.payload_sha256)
    && Object.entries(metadata).every(([key, entry]) => (key === "target" ? normalizedPath(value.metadata?.target) === normalizedPath(entry) : value.metadata?.[key as keyof RpfPackageMetadata] === entry))
    && value.edition === session.source_binding?.edition.toLowerCase() && SHA.test(value.archive_sha256)
    && Array.isArray(value.members) && value.members.length === 4
    && value.members.every((row, i) => row.path === expected[i] && Number.isSafeInteger(row.size) && row.size > 0 && SHA.test(row.sha256))
    && value.members.find(row => row.path === payloadPath)?.sha256 === value.payload_sha256
    && value.total_bytes === value.members.reduce((sum, row) => sum + row.size, 0)
    && Number.isSafeInteger(value.required_free_bytes) && value.required_free_bytes >= value.total_bytes
    && typeof value.manifest_text === "string" && value.manifest_text.length < 16384
    && value.whole_archive_replacement === !memberOnly && value.install_performed === false
    && value.dlc_registration_performed === false && value.upload_performed === false;
}
export interface Gxt2RpfReview {
  archive_name: string; archive_size: number; entry_id: string; entry_size_before: number; entry_size_after: number;
  payload_sha256: string; original_sha256: string; archive_sha256: string; edition: string; index_sha256: string;
  indexed_entries: number; verified_payloads: number; required_free_bytes: number; outputs: string[];
  game_must_be_closed: boolean; source_unchanged_required: boolean; new_output_only: boolean;
}
const SHA = /^[a-f0-9]{64}$/;
function validBinding(binding: Gxt2ArchiveBinding | null) {
  return binding === null || (binding && typeof binding.outer_archive === "string" && SHA.test(binding.outer_archive_sha256)
    && typeof binding.entry_id === "string" && binding.entry_id.includes("::") && typeof binding.edition === "string"
    && (binding.gta_path === undefined || typeof binding.gta_path === "string"));
}
function sameBinding(left: Gxt2ArchiveBinding | null, right: Gxt2ArchiveBinding | null) {
  return validBinding(left) && validBinding(right) && left?.outer_archive === right?.outer_archive
    && left?.outer_archive_sha256 === right?.outer_archive_sha256 && left?.entry_id === right?.entry_id
    && left?.edition === right?.edition && left?.gta_path === right?.gta_path;
}
function unwrap<T>(message: Envelope): T {
  if (message.operation === "error") throw new Error(String(message.payload.message ?? "GXT2 operation failed"));
  if (!message.terminal || !message.payload.result) throw new Error("Incomplete GXT2 response");
  return message.payload.result as T;
}
function validSession(s: Gxt2Session) {
  return s?.kind === "gxt2_session" && SHA.test(s.state_sha256) && SHA.test(s.original_sha256)
    && typeof s.source === "string" && (s.workspace === null || typeof s.workspace === "string")
    && validBinding(s.source_binding)
    && Number.isSafeInteger(s.revision) && s.revision >= 0 && Number.isSafeInteger(s.entry_count)
    && Number.isSafeInteger(s.match_count) && Number.isSafeInteger(s.offset) && s.offset >= 0 && s.page_size === 100
    && Array.isArray(s.entries) && s.entries.length <= 100 && s.entries.every(e => Number.isInteger(e.hash) && typeof e.preview === "string")
    && Array.isArray(s.history) && (!s.selected || (Number.isInteger(s.selected.hash) && typeof s.selected.editable === "boolean"
      && (s.selected.editable ? typeof s.selected.text === "string" && s.selected.text.length <= 16384 : s.selected.text === null)))
    && s.game_write_performed === false && s.read_only === true;
}
const actionLabel: Record<string, string> = { create: "Create editable copy", edit: "Save text", add: "Add label", remove: "Remove label", undo: "Undo last operation", build: "Build dictionary", package_rpf: "Build RPF package", publish_rpf: "Export ALLIN1 ZIP" };
function validRpfReview(value: Gxt2RpfReview | undefined, session: Gxt2Session) {
  const binding = session.source_binding;
  return !!value && !!binding && value.archive_name === binding.outer_archive.split(/[\\/]/).pop()
    && value.entry_id === binding.entry_id && value.archive_sha256 === binding.outer_archive_sha256
    && value.original_sha256 === session.original_sha256 && value.edition === binding.edition
    && SHA.test(value.payload_sha256) && SHA.test(value.index_sha256)
    && [value.archive_size, value.entry_size_before, value.entry_size_after, value.indexed_entries,
      value.verified_payloads, value.required_free_bytes].every(n => Number.isSafeInteger(n) && n >= 0)
    && value.verified_payloads > 0 && value.verified_payloads <= value.indexed_entries
    && value.outputs?.join("|") === [`archive/${value.archive_name}`, "payload/replacement.gxt2", "payload/replacement.gxt2.gxt2-validation.json", "rpf-package.json"].join("|")
    && value.game_must_be_closed === true && value.new_output_only === true && value.source_unchanged_required === true;
}

export default function Gxt2Workspace({ client, onGuardChange, archiveRequest }: {
  client: DesktopClient; onGuardChange: (guarded: boolean) => void; archiveRequest?: Gxt2ArchiveRequest | null;
}) {
  const [session, setSession] = useState<Gxt2Session | null>(null);
  const [text, setText] = useState("");
  const [hash, setHash] = useState("");
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [workspaceName, setWorkspaceName] = useState("game-text");
  const [packageName, setPackageName] = useState("text-rpf-package");
  const [publicationOpen, setPublicationOpen] = useState(false);
  const [sourcePackage, setSourcePackage] = useState("");
  const [packageMetadata, setPackageMetadata] = useState(initialMetadata);
  const [publicationMode, setPublicationMode] = useState<RpfPublicationMode>("whole_archive");
  const [phase, setPhase] = useState<"idle" | "choosing" | "reading" | "writing">("idle");
  const [review, setReview] = useState<{ value: Gxt2Review; payload: Record<string, unknown> } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const generation = useRef(0), job = useRef("");
  const inFlight = useRef(false);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const publicationHeading = useRef<HTMLHeadingElement>(null);
  const busy = phase !== "idle";
  const dirty = adding || Boolean(session?.workspace && session.selected?.editable && text !== session.selected.text);
  const locked = busy || !!review || publicationOpen;
  useEffect(() => { onGuardChange(locked || dirty); }, [locked, dirty, onGuardChange]);
  useEffect(() => {
    if (review) { reviewHeading.current?.scrollIntoView?.({ block: "start" }); reviewHeading.current?.focus({ preventScroll: true }); }
  }, [review]);
  useEffect(() => {
    if (publicationOpen && !review) { publicationHeading.current?.scrollIntoView?.({ block: "start" }); publicationHeading.current?.focus({ preventScroll: true }); }
  }, [publicationOpen, review]);
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current).catch(() => undefined); onGuardChange(false); }, [client, onGuardChange]);
  const context = session?.workspace ? { workspace: session.workspace } : session?.source_binding ? {
    archive: session.source_binding.outer_archive, entry_id: session.source_binding.entry_id,
    ...(session.source_binding.gta_path ? { gta_path: session.source_binding.gta_path } : {}),
  } : { source: session?.source };
  const load = (value: Gxt2Session) => {
    if (!validSession(value)) throw new Error("Invalid GXT2 evidence; no editor state was replaced.");
    if (value.source !== session?.source) { setSourcePackage(""); setPackageMetadata(initialMetadata()); setPublicationOpen(false); setPublicationMode("whole_archive"); }
    setSession(value); setText(value.selected?.text ?? ""); setHash(value.selected?.hash_hex ?? "");
    setAdding(false); setQuery(value.query); setNotice("");
  };
  const read = async (operation: "inspect_gxt2_workspace" | "review_gxt2_action", payload: Record<string, unknown>, onResult: (value: unknown) => void, version: number) => {
    setPhase("reading");
    let finished = false;
    const started = await client.startJob(operation, payload, `gxt2-${version}`, message => {
      if (generation.current !== version || !message.terminal) return;
      finished = true; job.current = ""; inFlight.current = false; setPhase("idle");
      try { onResult(unwrap(message)); } catch (reason) { setError(String(reason)); }
    });
    if (generation.current !== version) { if (!finished) void client.cancelJob(started.job_id).catch(() => undefined); return; }
    if (!finished) job.current = started.job_id;
  };
  const start = async (task: (version: number) => Promise<void>) => {
    if (inFlight.current) return;
    const version = ++generation.current;
    inFlight.current = true; setError(""); setNotice("");
    try { await task(version); }
    catch (reason) { if (generation.current === version) { setError(String(reason)); inFlight.current = false; setPhase("idle"); } }
  };
  useEffect(() => {
    if (!archiveRequest) return;
    let active = true;
    // Defer intake so a StrictMode setup/cleanup cannot strand a cancelled request.
    queueMicrotask(() => {
      if (!active) return;
      if (dirty || locked) { setError("Finish or reset the current text action before opening another archive member."); return; }
      const { archive, entry_id, gta_path } = archiveRequest;
      void start(version => read("inspect_gxt2_workspace", { archive, entry_id, gta_path }, result => {
        const value = result as Gxt2Session;
        if (!validSession(value) || value.workspace !== null || value.source !== archive
            || value.source_binding?.outer_archive !== archive || value.source_binding.entry_id !== entry_id
            || value.source_binding.gta_path !== gta_path) throw new Error("Archive text evidence does not match the selected member.");
        load(value);
        setWorkspaceName(`${value.name.replace(/\.gxt2$/i, "").replace(/[^A-Za-z0-9._-]/g, "-") || "game"}-text`);
      }, version));
    });
    return () => { active = false; };
    // Intake changes only through an explicit, navigation-guarded archive action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [archiveRequest, client]);
  const choose = (kind: "gxt2_source" | "gxt2_workspace") => {
    if (dirty || locked) return;
    void start(async version => {
      setPhase("choosing");
      const path = await client.selectPath(kind);
      if (generation.current !== version) return;
      if (!path) { inFlight.current = false; setPhase("idle"); return; }
      await read("inspect_gxt2_workspace", kind === "gxt2_workspace" ? { workspace: path } : { source: path }, value => {
        load(value as Gxt2Session);
        setWorkspaceName(`${path.split(/[\\/]/).pop()?.replace(/\.gxt2$/i, "") || "game"}-text`);
      }, version);
    });
  };
  const inspect = (extra: Record<string, unknown> = {}) => {
    if (!session || locked || dirty) return;
    void start(version => read("inspect_gxt2_workspace", { ...context, query: session.query, offset: session.offset, ...extra }, value => load(value as Gxt2Session), version));
  };
  const prepare = (action: string) => {
    if (!session || (locked && !(action === "publish_rpf" && publicationOpen && !busy && !review)) || (dirty && !["edit", "add"].includes(action))) return;
    void start(async version => {
      const payload: Record<string, unknown> = { ...context, action, expected_state_sha256: session.state_sha256 };
      if (action === "publish_rpf") {
        setPhase("choosing");
        const chosen = await client.selectPackageZipDestination(`${packageMetadata.id}-${packageMetadata.version}`.replace(/[^A-Za-z0-9._-]/g, "-") + ".zip");
        if (generation.current !== version) return;
        if (!chosen) { inFlight.current = false; setPhase("idle"); return; }
        payload.destination = chosen; payload.source_package = sourcePackage; payload.package_metadata = { ...packageMetadata };
        payload.publication_mode = publicationMode;
      } else if (action === "create" || action === "build" || action === "package_rpf") {
        setPhase("choosing");
        const chosen = action === "package_rpf" ? await client.selectPath("rpf_package_parent") : action === "create" ? await client.selectPath("gxt2_parent") : await client.selectGxt2BuildDestination(session.name);
        if (generation.current !== version) return;
        if (!chosen) { inFlight.current = false; setPhase("idle"); return; }
        payload.destination = action === "create" || action === "package_rpf" ? `${chosen.replace(/[\\/]+$/, "")}/${action === "create" ? workspaceName : packageName}` : chosen;
      } else if (["edit", "add", "remove"].includes(action)) {
        payload.label_hash = adding ? hash : session.selected?.hash;
        if (action !== "remove") payload.text = text;
      }
      await read("review_gxt2_action", payload, result => {
        const value = result as Gxt2Review;
        if (value?.kind !== "gxt2_review" || !SHA.test(value.review_sha256) || value.state_sha256 !== session.state_sha256
            || value.action !== action || value.source !== session.source || value.review_only !== true || value.game_write_performed !== false
            || !sameBinding(value.source_binding, session.source_binding)
            || !Number.isSafeInteger(value.revision) || value.revision !== session.revision
            || !(value.before === null || typeof value.before === "string") || !(value.after === null || typeof value.after === "string")
            || !(value.label_hash === null || Number.isInteger(value.label_hash))
            || !(value.destination === null || typeof value.destination === "string")
            || (["edit", "add"].includes(action) && value.after !== payload.text)
            || (action === "package_rpf" && (!validRpfReview(value.rpf_package, session) || value.destination?.replaceAll("\\", "/") !== String(payload.destination).replaceAll("\\", "/")))
            || (action === "publish_rpf" && (!validPublication(value.rpf_publication, session, sourcePackage, packageMetadata, publicationMode) || normalizedPath(value.destination) !== normalizedPath(payload.destination)))
            || (action === "build" && !SHA.test(value.output_sha256 ?? ""))) throw new Error("Unexpected GXT2 review; action was not authorized.");
        setReview({ value, payload }); setConfirmed(false);
      }, version);
    });
  };
  const cancelRead = () => {
    generation.current++; inFlight.current = false; setPhase("idle");
    if (job.current) void client.cancelJob(job.current).catch(reason => setError(String(reason)));
    job.current = "";
  };
  const apply = () => {
    if (!review || !confirmed || busy) return;
    void start(async version => {
      setPhase("writing");
      try {
        const result = unwrap<Record<string, unknown>>(await client.applyGxt2Action({ ...review.payload, review_sha256: review.value.review_sha256, authoring_confirmed: true }));
        if (generation.current !== version) return;
        if (result.review_sha256 !== review.value.review_sha256 || result.game_write_performed !== false || result.file_write_performed !== true) throw new Error("GXT2 write outcome could not be verified; inspect the workspace before retrying.");
        if (review.value.action === "publish_rpf") {
          const expected = review.value.rpf_publication!;
          if (result.kind !== "gxt2_rpf_published" || normalizedPath(result.archive) !== normalizedPath(review.value.destination)
              || !SHA.test(String(result.sha256)) || !Number.isSafeInteger(result.archive_size) || Number(result.archive_size) <= 0
              || result.package_id !== expected.metadata.id || result.edition !== expected.edition || result.target !== expected.metadata.target
              || result.payload_sha256 !== expected.payload_sha256 || result.publication_mode !== expected.publication_mode
              || result.manifest_schema_version !== expected.manifest_schema_version || result.entry !== expected.entry || result.original_sha256 !== expected.original_sha256
              || !Array.isArray(result.members) || result.members.length !== expected.members.length
              || !result.members.every((row, i) => row?.path === expected.members[i].path && row?.size === expected.members[i].size && row?.sha256 === expected.members[i].sha256)
              || result.install_performed !== false || result.upload_performed !== false) {
            throw new Error("ALLIN1 ZIP outcome could not be verified; inspect the destination before retrying.");
          }
          setNotice(`ALLIN1 ZIP exported and validated: ${result.archive}\nSHA-256: ${result.sha256}\nInstall target: ${result.target} (${result.edition})\n${expected.publication_mode === "member" ? `Exact member: ${expected.entry}. Requires schema-${expected.manifest_schema_version} Launcher support and a matching original checksum.` : "Whole-archive replacement."} Nothing was installed or uploaded.`);
          setPublicationOpen(false);
        } else if (review.value.action === "package_rpf") {
          const expected = review.value.rpf_package!;
          const relative = (value: unknown) => typeof value === "string" ? value.replaceAll("\\", "/") : "";
          if (result.kind !== "gxt2_rpf_packaged" || result.destination !== review.value.destination
              || relative(result.archive) !== `${relative(review.value.destination)}/archive/${expected.archive_name}`
              || relative(result.report) !== `${relative(review.value.destination)}/rpf-package.json`
              || !SHA.test(String(result.sha256 ?? "")) || !SHA.test(String(result.report_sha256 ?? ""))
              || result.payload_sha256 !== expected.payload_sha256 || result.verified_payloads !== expected.verified_payloads
              || !sameBinding(result.source_binding as Gxt2ArchiveBinding, review.value.source_binding)) {
            throw new Error("RPF package outcome could not be verified; inspect the destination before retrying.");
          }
          setNotice(`RPF package built and verified: ${result.archive}\nSHA-256: ${result.sha256}\nReport: ${result.report}\nOriginal archive unchanged. This is not an installable ALLIN1 package.`);
          setSourcePackage(String(result.destination));
        } else if (review.value.action === "build") {
          if (result.kind !== "gxt2_built" || result.archive !== review.value.destination || result.sha256 !== review.value.output_sha256) throw new Error("Built file does not match reviewed evidence.");
          setNotice(`Built and verified ${result.archive}\nSHA-256: ${result.sha256}\nValidation report: ${result.report}`);
        } else {
          if (result.kind !== "gxt2_applied" || result.action !== review.value.action) throw new Error("Unexpected GXT2 write response");
          const saved = result.session as Gxt2Session;
          const expectedPath = review.value.action === "create" ? review.value.destination : session?.workspace;
          if (!validSession(saved) || saved.workspace !== expectedPath || saved.source !== expectedPath
              || saved.original_sha256 !== session?.original_sha256 || !sameBinding(saved.source_binding, review.value.source_binding)) {
            throw new Error("Saved GXT2 evidence does not match the reviewed workspace; inspect the destination before retrying.");
          }
          load(saved);
          setNotice(`${actionLabel[review.value.action]} completed. The original dictionary was not changed.`);
        }
      } finally {
        if (generation.current === version) { inFlight.current = false; setPhase("idle"); setReview(null); setConfirmed(false); }
      }
    });
  };
  const reset = () => { setAdding(false); setHash(session?.selected?.hash_hex ?? ""); setText(session?.selected?.text ?? ""); setError(""); };

  return <section className="gxt-workspace" aria-labelledby="gxt-title">
    <div className="gxt-title"><div><span className="pane-kicker">Game text</span><h2 id="gxt-title">GXT2 text editor</h2><p>Edit a copied text dictionary. Original files and RPF archives stay unchanged.</p></div>
      <div className="gxt-actions"><button className="quiet-button" disabled={locked || dirty} onClick={() => choose("gxt2_source")}>Open GXT2</button><button className="quiet-button" disabled={locked || dirty} onClick={() => choose("gxt2_workspace")}>Open text workspace</button></div></div>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {notice && <p role="status" className="gxt-notice">{notice}</p>}
    {phase === "reading" && <div className="gxt-actions"><span role="status">Reading validated text evidence…</span><button className="quiet-button" onClick={cancelRead}>Cancel text review</button></div>}
    <div className="gxt-source"><span>{session ? `${session.name} · ${session.entry_count.toLocaleString()} labels · ${session.workspace ? `Revision ${session.revision}` : session.source_binding ? "Read-only archive member" : "Read-only source"}` : "Open a loose .gxt2 file, select a dictionary in Archive inspection, or open a text workspace"}</span><code>{session?.source}{session?.source_binding && !session.workspace ? ` → ${session.source_binding.entry_id}` : ""}</code></div>
    {session && !session.workspace && <div className="gxt-copy"><label>Workspace folder name<input value={workspaceName} maxLength={120} disabled={locked} onChange={e => setWorkspaceName(e.target.value)} /></label><button className="primary-button" disabled={locked || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(workspaceName)} onClick={() => prepare("create")}>Create editable copy</button></div>}
    <div className="gxt-panels">
      <section className="gxt-pane" aria-label="Text labels"><header><span className="pane-kicker">Dictionary</span><h3>Text labels</h3></header>
        <form className="gxt-search" onSubmit={e => { e.preventDefault(); inspect({ query, offset: 0 }); }}><label htmlFor="gxt-query">Find hash or text</label><div><input id="gxt-query" value={query} maxLength={256} disabled={locked || dirty || !session} onChange={e => setQuery(e.target.value)} /><button className="quiet-button" disabled={locked || dirty || !session}>Search</button></div></form>
        <div className="gxt-rows">{session?.entries.map(row => <button key={row.hash} className={session.selected?.hash === row.hash && !adding ? "selected" : ""} disabled={locked || dirty} onClick={() => inspect({ selected_hash: row.hash })}><code>{row.hash_hex}</code><span>{row.preview || "(empty text)"}</span></button>)}{!session?.entries.length && <p className="gxt-empty">{session ? "No labels match this search." : "No dictionary opened."}</p>}</div>
        {session && <div className="gxt-pagination"><span>{session.match_count ? `${session.offset + 1}–${Math.min(session.offset + session.page_size, session.match_count)} of ${session.match_count}` : "0 matches"}</span><button className="quiet-button" disabled={locked || dirty || session.offset === 0} onClick={() => inspect({ offset: Math.max(0, session.offset - session.page_size) })}>Previous</button><button className="quiet-button" disabled={locked || dirty || session.offset + session.page_size >= session.match_count} onClick={() => inspect({ offset: session.offset + session.page_size })}>Next</button></div>}
      </section>
      <section className="gxt-pane" aria-label="Label editor"><header><span className="pane-kicker">{session?.workspace ? "Authoring" : "Inspection"}</span><h3>{adding ? "New label" : "Selected label"}</h3></header>
        <div className="gxt-editor">{session?.selected || adding ? <><label>Label hash<input value={hash} readOnly={!adding} disabled={locked} onChange={e => setHash(e.target.value)} placeholder="0x12345678 or decimal" maxLength={16} /></label>
          <label>Game text<textarea value={text} maxLength={16384} readOnly={!session?.workspace || (!adding && !session.selected?.editable)} disabled={locked} onChange={e => setText(e.target.value)} rows={10} /></label>
          {!adding && session?.selected?.editable === false ? <p>Text exceeds the 16,384-character desktop limit. Editing and removal are disabled; no truncated value will be saved.</p> : <small>{text.length.toLocaleString()} / 16,384 characters · UTF-8 text; hashes are preserved</small>}
          </> : <p className="gxt-empty">Select a label to inspect its full text.</p>}
          {session?.workspace && <div className="gxt-actions"><button className="quiet-button" disabled={locked || !dirty} onClick={reset}>Reset text draft</button><button className="primary-button" disabled={locked || !dirty || (!adding && !session.selected?.editable)} onClick={() => prepare(adding ? "add" : "edit")}>Review text change</button></div>}
          {session?.workspace && <div className="gxt-actions"><button className="quiet-button" disabled={locked || dirty} onClick={() => { setAdding(true); setHash(""); setText(""); }}>New label</button><button className="quiet-button" disabled={locked || dirty || !session.selected?.editable} onClick={() => prepare("remove")}>Review removal</button></div>}
        </div>
      </section>
      <aside className="gxt-pane" aria-label="Validation and history"><header><span className="pane-kicker">Evidence</span><h3>Validation &amp; history</h3></header><div className="gxt-evidence">
        {session ? <><p>Dictionary parsed and validated{session.workspace ? "; original snapshot and history verified" : ""}.</p><small>Original SHA-256</small><code>{session.original_sha256}</code><small>Current state SHA-256</small><code>{session.state_sha256}</code>
          {session.source_binding && <><h4>Archive provenance</h4><small>Original archive</small><code>{session.source_binding.outer_archive}</code><small>Exact member</small><code>{session.source_binding.entry_id}</code><small>Archive SHA-256 at intake</small><code>{session.source_binding.outer_archive_sha256}</code><small>Edition: {session.source_binding.edition}</small><p>{session.workspace ? "This workspace is an independent copy. Its recorded archive binding is retained in the build report." : "Creating a copy rechecks the archive and dictionary. It does not modify the archive."}</p></>}
          <h4>Recent operations</h4>{session.history.length ? <ol>{session.history.map(h => <li key={h.sequence}>#{h.sequence} · {h.action.replaceAll("_", " ")}<small>{h.created_utc}</small></li>)}</ol> : <p>No saved edits.</p>}
          <button className="quiet-button" disabled={locked || dirty} onClick={() => inspect()}>Refresh text workspace</button>
          {session.workspace && <><button className="quiet-button" disabled={locked || dirty || !session.can_undo} onClick={() => prepare("undo")}>Review undo</button><button className="quiet-button" disabled={locked || dirty} onClick={() => prepare("build")}>Review GXT2 build</button></>}
        </> : <p className="gxt-empty">Validation and saved history appear here.</p>}
        <p className="gxt-note">Build creates a new dictionary and validation report. It does not replace an archive member or install anything.</p>
      </div></aside>
    </div>
    {session?.workspace && <section className="gxt-rpf-package" aria-label="RPF packaging">
      <div><span className="pane-kicker">Archive output</span><h3>Package edited text into an RPF</h3>
        <p>Build a new copy of the original archive with this dictionary replaced. Keep the original RPF filename; no game files are changed.</p></div>
      {session.source_binding?.gta_path ? <><div className="gxt-copy"><label>RPF package folder name<input value={packageName} maxLength={120} disabled={locked || dirty} onChange={e => setPackageName(e.target.value)} /></label>
        <button className="primary-button" disabled={locked || dirty || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(packageName)} onClick={() => prepare("package_rpf")}>Review RPF package</button></div>
        <p className="gxt-note">Save text edits first and close GTA V. Review checks the original archive, disk space and exact member. The build verifies other payloads and publishes only to a new folder.</p>
        <div className="gxt-copy"><button className="quiet-button" disabled={locked || dirty} onClick={() => setPublicationOpen(true)}>Configure ALLIN1 export</button><span>Wrap a verified build in an installable ZIP. Export is separate from installation.</span></div></>
        : <p className="gxt-note">Open a dictionary from Archive inspection and create an editable copy to retain the archive binding required for packaging.</p>}
    </section>}
    {publicationOpen && <section className="gxt-rpf-package" aria-label="ALLIN1 export settings">
      <span className="pane-kicker">Distribution</span><h3 ref={publicationHeading} tabIndex={-1}>{publicationMode === "member" ? "Export an exact dictionary patch" : "Export a whole-archive replacement"}</h3>
      <label className="gxt-export-scope">Export scope<select value={publicationMode} disabled={busy || !!review} onChange={e => setPublicationMode(e.target.value as RpfPublicationMode)}>
        <option value="whole_archive">Whole archive · schema 1</option>
        <option value="member">Selected dictionary only · schema {memberSchema(session?.source_binding)}</option>
      </select></label>
      {publicationMode === "member" ? <p>Only <code>{packageMember(session?.source_binding)}</code> is shipped. Requires a Launcher with schema-{memberSchema(session?.source_binding)} support and its matching native helper. Older Launchers reject this ZIP. Installation refuses a missing or changed original dictionary.</p>
        : <p>This ZIP replaces the entire destination RPF when installed. It can overwrite other archive edits. No DLC registration is added; choose the path of an existing archive.</p>}
      {publicationMode === "member" && memberSchema(session?.source_binding) === 4 && <p className="gxt-note">Nested patch: ! separates each archive layer. Install and restore rebuild the selected dictionary inside a verified archive copy, preserving unrelated members. No containing RPF is shipped.</p>}
      <div className="gxt-copy"><button className="quiet-button" disabled={busy || !!review} onClick={() => void start(async version => {
        setPhase("choosing"); const selected = await client.selectPath("rpf_package_source");
        if (generation.current !== version) return;
        if (selected) setSourcePackage(selected);
        inFlight.current = false; setPhase("idle");
      })}>Choose RPF build folder</button><code>{sourcePackage || "Choose a build containing rpf-package.json"}</code></div>
      <div className="gxt-publication-fields">{([ ["id", "Package ID", 64], ["name", "Package name", 120], ["version", "Package version", 64], ["author", "Author", 120], ["target", "GTA-relative archive destination", 512] ] as const).map(([key, label, limit]) =>
        <label key={key}>{label}<input value={packageMetadata[key]} maxLength={limit} disabled={busy || !!review} onChange={e => setPackageMetadata(current => ({ ...current, [key]: e.target.value }))} /></label>)}</div>
      <p className="gxt-note">Destination must start with mods/ and keep the original RPF filename. Edition is locked to {session?.source_binding?.edition}. Requires OpenRPF. Local workspace and GTA paths are excluded from the exported evidence.</p>
      <div className="gxt-copy"><button className="quiet-button" disabled={busy || !!review} onClick={() => setPublicationOpen(false)}>Close export settings</button>
        <button className="primary-button" disabled={busy || !!review || !sourcePackage || Object.values(packageMetadata).some(value => !value.trim())} onClick={() => prepare("publish_rpf")}>Review ALLIN1 ZIP</button></div>
    </section>}
    {review && <section className="gxt-review" aria-label="GXT2 action review"><h3 ref={reviewHeading} tabIndex={-1}>Review: {actionLabel[review.value.action]}</h3><p>{review.value.source} · revision {review.value.revision}</p>
      {review.value.source_binding && <p>Archive member: <code>{review.value.source_binding.entry_id}</code></p>}
      {review.value.destination && <p>Destination: <code>{review.value.destination}</code></p>}
      {review.value.rpf_package && <div className="gxt-rpf-review"><dl>
        <div><dt>Archive copy</dt><dd>{review.value.rpf_package.archive_name} · {(review.value.rpf_package.archive_size / 1024**2).toFixed(1)} MiB</dd></div>
        <div><dt>Changed member</dt><dd>{review.value.rpf_package.entry_id}</dd></div>
        <div><dt>Dictionary bytes</dt><dd>{review.value.rpf_package.entry_size_before.toLocaleString()} → {review.value.rpf_package.entry_size_after.toLocaleString()}</dd></div>
        <div><dt>Payloads verified</dt><dd>{review.value.rpf_package.verified_payloads.toLocaleString()} · unrelated content must match</dd></div>
        <div><dt>Free space required</dt><dd>{(review.value.rpf_package.required_free_bytes / 1024**2).toFixed(1)} MiB on the output drive</dd></div>
      </dl><h4>Package contents</h4><ul>{review.value.rpf_package.outputs.map(path => <li key={path}><code>{path}</code></li>)}</ul>
      <p>Build stages a private copy, applies the reviewed replacement, and verifies the archive before publishing. GTA V must remain closed. The original archive is not replaced.</p></div>}
      {review.value.rpf_publication && <div className="gxt-rpf-review"><dl>
        <div><dt>Package</dt><dd>{review.value.rpf_publication.metadata.name} · {review.value.rpf_publication.metadata.version}</dd></div>
        <div><dt>Edition / dependency</dt><dd>{review.value.rpf_publication.edition} / OpenRPF</dd></div>
        <div><dt>{review.value.rpf_publication.publication_mode === "member" ? "Member-patch archive target" : "Whole-archive install target"}</dt><dd><code>{review.value.rpf_publication.metadata.target}</code></dd></div>
        {review.value.rpf_publication.publication_mode === "member" && <><div><dt>Exact member</dt><dd><code>{review.value.rpf_publication.entry}</code></dd></div><div><dt>Required original SHA-256</dt><dd><code>{review.value.rpf_publication.original_sha256}</code></dd></div></>}
        <div><dt>Unpacked ZIP size</dt><dd>{review.value.rpf_publication.total_bytes < 1024**2 ? `${review.value.rpf_publication.total_bytes.toLocaleString()} bytes` : `${(review.value.rpf_publication.total_bytes / 1024**2).toFixed(1)} MiB`}</dd></div>
      </dl>{review.value.rpf_publication.publication_mode === "member" ? <p>Schema {review.value.rpf_publication.manifest_schema_version} · exact {review.value.rpf_publication.manifest_schema_version === 4 ? "nested" : "outer-archive"} replacement only. Older Launchers reject this package. The original dictionary must match the checksum above. Other members are not included; uninstall an existing version before updating. No DLC registration is included.</p>
        : <p>Installing this ZIP can replace unrelated edits in the destination archive. Review ownership and backups in ALLIN1 before installation. No DLC registration is included.</p>}
      <h4>ZIP contents</h4><ul>{review.value.rpf_publication.members.map(row => <li key={row.path}><code>{row.path}</code> · {row.size.toLocaleString()} bytes</li>)}</ul>
      <details><summary>Generated mod.toml</summary><pre>{review.value.rpf_publication.manifest_text}</pre></details>
      <p>Export rechecks the build hashes and opens the ZIP with the ALLIN1 package validator. No game files are changed and nothing is uploaded.</p></div>}
      {review.value.label_hash !== null && <p>Label: 0x{review.value.label_hash.toString(16).padStart(8, "0").toUpperCase()}</p>}
      {review.value.before !== null && <div><strong>Before</strong><pre>{review.value.before || "(empty text)"}</pre></div>}
      {review.value.after !== null && <div><strong>After</strong><pre>{review.value.after || "(empty text)"}</pre></div>}
      <p>{review.value.action === "remove" ? "Removes this label from the copied dictionary. Existing references may need updating. Undo remains available." : review.value.action === "undo" ? "Restores the state before the most recent saved operation and records the restoration in history." : "Python rechecks this exact review before writing."}</p>
      <label className="gxt-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />{review.value.action === "publish_rpf" ? review.value.rpf_publication?.publication_mode === "member" ? `Export this exact-member patch. I have reviewed the target, original checksum and schema-${review.value.rpf_publication.manifest_schema_version} compatibility requirement.` : "Export this whole-archive replacement package. I have reviewed its install target and overwrite risk." : "Apply this reviewed action. Original and game files stay unchanged."}</label>
      <div className="gxt-actions"><button className="quiet-button" disabled={busy} onClick={() => { setReview(null); setConfirmed(false); }}>{review.value.action === "publish_rpf" ? "Back to export settings" : "Back to text"}</button><button className="primary-button" disabled={busy || !confirmed} onClick={apply}>{phase === "writing" ? "Writing…" : actionLabel[review.value.action]}</button></div>
      <small>Writing cannot be cancelled once it starts.</small></section>}
  </section>;
}
