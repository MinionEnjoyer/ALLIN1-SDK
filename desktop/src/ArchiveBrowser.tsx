import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import { formatBytes } from "./tokenize";
import type { AssetPreviewResult, DesktopClient, RpfEntryRecord } from "./types";
import "./ArchiveBrowser.css";
import { isNativeResource } from "./nativeFormats";
import RpfArchiveUtilities from "./RpfArchiveUtilities";

export type BrowserLocation = { path: string; layer: string; directory: string };
export type BrowserEntry = {
  id: string; name: string; path: string; kind: RpfEntryRecord["kind"] | "file"; size: number;
  source: string; archive: string | null; entry_id: string | null;
  member_path?: string; archive_path?: string; origin: string; edition?: string;
  location: BrowserLocation | null;
};
type Listing = {
  kind: "archive_browser"; root: string; gta_path: string | null;
  location: BrowserLocation; parent: BrowserLocation | null; entries: BrowserEntry[];
  offset: number; page_size: number; matched_count: number; has_more: boolean;
  scan_complete: boolean; warnings: string[]; edition?: string; query?: string; scope?: string;
};
type Bookmark = { root: string; gta_path: string; location: BrowserLocation };
const ROOT: BrowserLocation = { path: "", layer: "", directory: "" };
const KEY = "allin1.sdk.archive-browser.v1";

function saved(): { favorites: Bookmark[]; recent: Bookmark[] } {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    const valid = (rows: unknown): Bookmark[] => Array.isArray(rows) ? rows.filter(row =>
      row && typeof row.root === "string" && typeof row.gta_path === "string" && row.location &&
      [row.location.path, row.location.layer, row.location.directory].every(item => typeof item === "string")
    ).slice(0, 20) : [];
    return { favorites: valid(raw.favorites), recent: valid(raw.recent) };
  } catch { return { favorites: [], recent: [] }; }
}
function same(a: Bookmark, b: Bookmark) { return JSON.stringify(a) === JSON.stringify(b); }
function label(place: Bookmark) {
  return [place.root, place.location.path, place.location.layer, place.location.directory].filter(Boolean).join(" / ");
}

export default function ArchiveBrowser({ client, onOpen, onStage, onJob, onGuardChange, referenceRequest }: {
  client: DesktopClient;
  onOpen: (entry: BrowserEntry, gtaPath: string) => void;
  onStage?: (entry: BrowserEntry, gtaPath: string) => void;
  onJob: (job: string | null) => void;
  onGuardChange?: (value: boolean) => void;
  referenceRequest?: { query: string; gta_path: string; requestId: number } | null;
}) {
  const [root, setRoot] = useState("");
  const [game, setGame] = useState("");
  const [edition, setEdition] = useState("");
  const [address, setAddress] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");
  const [listing, setListing] = useState<Listing | null>(null);
  const [selected, setSelected] = useState<BrowserEntry | null>(null);
  const [preview, setPreview] = useState<AssetPreviewResult | null>(null);
  const [error, setError] = useState("");
  const [referenceNotice, setReferenceNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [utilityGuarded, setUtilityGuarded] = useState(false);
  const [checked, setChecked] = useState<BrowserEntry[]>([]);
  const locked = busy || utilityGuarded;
  useEffect(() => { onGuardChange?.(utilityGuarded); }, [utilityGuarded, onGuardChange]);
  const [places, setPlaces] = useState(saved);
  const [history, setHistory] = useState<Bookmark[]>([]);
  const [cursor, setCursor] = useState(-1);
  const generation = useRef(0);
  const job = useRef<string | null>(null);
  const handledReference = useRef(0);
  useEffect(() => {
    if (!referenceRequest || referenceRequest.requestId === handledReference.current) return;
    handledReference.current = referenceRequest.requestId;
    if (locked) { setError("Finish the current browser operation before searching a reference."); return; }
    setQuery(referenceRequest.query); setScope("all"); setListing(null); setSelected(null); setPreview(null); setChecked([]);
    if (referenceRequest.gta_path) { setRoot(referenceRequest.gta_path); setGame(referenceRequest.gta_path); setAddress(""); }
    setError("");
    setReferenceNotice("Reference search prepared. Select a root if needed, then Search root. A matching filename is only a candidate; hashed references are not automatically resolved.");
  }, [referenceRequest]);

  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(places)); } catch { /* Browsing works without storage. */ } }, [places]);
  useEffect(() => () => { generation.current++; if (job.current) void client.cancelJob(job.current); }, [client]);

  async function run(place: Bookmark, search = false, offset = 0, remember = true) {
    if (utilityGuarded) return;
    const version = ++generation.current;
    const previousJob = job.current;
    job.current = null;
    const searchQuery = search && !remember ? listing?.query || query : query;
    const searchScope = search && !remember ? listing?.scope || scope : scope;
    setBusy(true); setError(""); setSelected(null); setPreview(null); setListing(null); setChecked([]);
    setRoot(place.root); setGame(place.gta_path); setAddress(place.location.path);
    let terminal = false;
    try {
      if (previousJob) await client.cancelJob(previousJob);
      if (version !== generation.current) return;
      const started = await client.startJob(search ? "search_game_files" : "browse_game_files", {
        root: place.root, gta_path: place.gta_path || undefined, ...place.location,
        query: search ? searchQuery : "", scope: searchScope, offset,
      }, `browser-${version}`, message => {
        if (!message.terminal || version !== generation.current) return;
        terminal = true; job.current = null; setBusy(false); onJob(null);
        if (message.operation === "error") { setError(String(message.payload.message || "Unable to browse this location")); return; }
        const data = message.payload.result as Listing | undefined;
        if (!data || data.kind !== "archive_browser") { setError("The browser received an invalid response."); return; }
        setListing(data);
        const actual = { root: data.root, gta_path: data.gta_path || "", location: data.location };
        setRoot(actual.root); setGame(actual.gta_path); setAddress(actual.location.path);
        if (!search && remember) {
          setHistory(old => [...old.slice(0, cursor + 1), actual].slice(-50));
          setCursor(Math.min(cursor + 1, 49));
          setPlaces(old => ({ ...old, recent: [actual, ...old.recent.filter(item => !same(item, actual))].slice(0, 20) }));
        }
      });
      if (!terminal && version === generation.current) { job.current = started.job_id; onJob(started.job_id); }
    } catch (reason) {
      if (version === generation.current) { setBusy(false); setError(String(reason)); onJob(null); }
    }
  }
  const current: Bookmark = { root, gta_path: game, location: listing?.location || ROOT };
  async function choose(isGame: boolean) {
    try {
      const path = await client.selectPath(isGame ? "gta_folder" : "package_folder");
      if (path) await run({ root: path, gta_path: isGame ? path : game, location: ROOT });
    } catch (reason) { setError(String(reason)); }
  }
  async function chooseDecoder() {
    try { const path = await client.selectPath("gta_folder"); if (path) {
      if (root) await run({ ...current, gta_path: path });
      else setGame(path);
    } }
    catch (reason) { setError(String(reason)); }
  }
  function open(entry: BrowserEntry) {
    if (entry.location) void run({ ...current, location: entry.location });
    else void showPreview(entry);
  }
  async function showPreview(entry: BrowserEntry) {
    setSelected(entry); setPreview(null); setError("");
    if (entry.location) return;
    const selectedEdition = entry.edition || listing?.edition || edition;
    if (!selectedEdition) { setError("Choose Legacy or Enhanced before previewing loose files."); return; }
    const version = ++generation.current;
    let terminal = false;
    setBusy(true);
    try {
      const started = await client.startJob("preview_asset", {
        source: entry.archive || root, entry: entry.entry_id || entry.path,
        gta_path: game || undefined, edition: selectedEdition,
      }, `browser-preview-${version}`, message => {
        if (!message.terminal || version !== generation.current) return;
        terminal = true; setBusy(false); job.current = null; onJob(null);
        if (message.operation === "error") setError(String(message.payload.message || "Preview failed"));
        else setPreview(message.payload.result as AssetPreviewResult);
      });
      if (!terminal && version === generation.current) { job.current = started.job_id; onJob(started.job_id); }
    } catch (reason) { if (version === generation.current) { setBusy(false); setError(String(reason)); onJob(null); } }
  }
  function travel(next: number) {
    const place = history[next]; if (!place) return;
    setCursor(next); void run(place, false, 0, false);
  }
  async function cancel() {
    generation.current++;
    const active = job.current; job.current = null;
    setBusy(false); onJob(null);
    if (active) { try { await client.cancelJob(active); } catch (reason) { setError(String(reason)); } }
  }
  const utilityArchive = checked[0]?.archive || selected?.archive;
  const utilityEntry = selected && selected.archive === utilityArchive && selected.entry_id && selected.kind !== "file" ? { id: selected.entry_id, name: selected.name, kind: selected.kind } : null;
  return <section className="archive-browser" aria-label="Game and archive browser">
    <div className="workspace-heading"><div><h2>Game & archive browser</h2><p>Browse a game or working folder, open nested archives, and search filenames across the selected root.</p></div>
      <div className="heading-actions"><button className="quiet-button" disabled={locked} onClick={() => void choose(true)}>Open GTA V</button><button className="quiet-button" disabled={locked} onClick={() => void choose(false)}>Open folder</button><button className="quiet-button" disabled={locked} onClick={() => void chooseDecoder()}>Decoder installation</button></div>
    </div>
    <div className="browser-context"><span title={root}>Root: {root || "Choose a game or folder"}</span><span title={game}>Decoder: {game || "Not selected"}{listing?.edition ? ` · ${listing.edition}` : ""}</span><select aria-label="Loose asset edition" value={edition} onChange={event => setEdition(event.target.value)} disabled={locked}><option value="">Choose asset edition</option><option value="Legacy">Legacy</option><option value="Enhanced">Enhanced</option></select></div>
    <form className="browser-address" onSubmit={event => { event.preventDefault(); if (root) void run({ root, gta_path: game, location: { ...ROOT, path: address } }); }}>
      <button className="quiet-button" type="button" aria-label="Back" disabled={locked || cursor < 1} onClick={() => travel(cursor - 1)}>←</button>
      <button className="quiet-button" type="button" aria-label="Forward" disabled={locked || cursor >= history.length - 1} onClick={() => travel(cursor + 1)}>→</button>
      <button className="quiet-button" type="button" disabled={locked || !listing?.parent} onClick={() => listing?.parent && void run({ ...current, location: listing.parent })}>Up</button>
      <input aria-label="Folder or RPF path relative to root" value={address} onChange={event => setAddress(event.target.value)} placeholder="update/update.rpf" disabled={locked} />
      <button className="quiet-button" disabled={locked || !root}>Go</button><button className="quiet-button" type="button" disabled={locked || !listing} onClick={() => setPlaces(old => ({ ...old, favorites: [current, ...old.favorites.filter(item => !same(item, current))].slice(0, 20) }))}>Favorite</button>
    </form>
    {listing && <div className="browser-location">{[listing.location.path || "Root", listing.location.layer, listing.location.directory].filter(Boolean).join(" › ")}</div>}
    <form className="browser-search" onSubmit={event => { event.preventDefault(); if (root && query.trim()) void run({ ...current, location: ROOT }, true); }}>
      <input aria-label="Search files and RPF members" value={query} onChange={event => setQuery(event.target.value)} placeholder="Filename or path, including nested RPF members" disabled={locked} />
      <select aria-label="Search scope" value={scope} onChange={event => setScope(event.target.value)} disabled={locked}><option value="all">Source + mods</option><option value="source">Source only</option><option value="mods">Mods only</option></select>
      <button className="quiet-button" disabled={locked || !root || !query.trim()}>Search root</button>{busy && <button className="quiet-button" type="button" onClick={() => void cancel()}>Cancel</button>}
    </form>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {referenceNotice && <p role="status">{referenceNotice}</p>}
    <div className="browser-layout"><aside className="browser-places">
      <button className="quiet-button" disabled={locked || !root} onClick={() => void run({ ...current, location: ROOT })}>Root</button>
      <button className="quiet-button" disabled={locked || !root} onClick={() => void run({ ...current, location: { ...ROOT, path: "mods" } })}>Mods folder</button>
      <h3>Favorites</h3>{places.favorites.map((place, i) => <div className="browser-place" key={label(place)}><button className="quiet-button" disabled={locked} title={label(place)} onClick={() => void run(place)}>{place.location.directory || place.location.layer || place.location.path || place.root}</button><button className="quiet-button" aria-label={`Remove favorite ${i + 1}`} onClick={() => setPlaces(old => ({ ...old, favorites: old.favorites.filter((_, index) => index !== i) }))}>×</button></div>)}
      <h3>Recent</h3>{places.recent.map(place => <button className="quiet-button" disabled={locked} key={label(place)} title={label(place)} onClick={() => void run(place)}>{place.location.directory || place.location.layer || place.location.path || place.root}</button>)}
    </aside><div className="browser-main">
      <p role="status">{busy ? "Reading archive locations…" : listing ? `${listing.matched_count} matches${listing.scan_complete ? "" : " · incomplete coverage"}` : "Open a location to begin."}</p>
      <div className="browser-table-wrap"><table><thead><tr><th>Select</th><th>Name</th><th>Type</th><th>Size</th><th>Location</th></tr></thead><tbody>
        {listing?.entries.map(entry => <tr key={entry.id} className={selected?.id === entry.id ? "selected" : ""}>
          <td><input type="checkbox" aria-label={`Select ${entry.path}`} checked={checked.some(item => item.id === entry.id)} disabled={locked || !entry.archive || entry.kind === "directory" || (!!checked.length && checked[0].archive !== entry.archive) || (checked.length >= 128 && !checked.some(item => item.id === entry.id))} onChange={event => setChecked(items => event.target.checked ? [...items, entry] : items.filter(item => item.id !== entry.id))} /></td>
          <td><button className="quiet-button" disabled={locked} onClick={() => void showPreview(entry)} onDoubleClick={() => open(entry)} title={entry.path}>{entry.name}</button></td><td>{entry.kind}</td><td>{formatBytes(entry.size)}</td><td title={entry.path}>{entry.path}</td>
        </tr>)}
      </tbody></table></div>
      {selected && <div className="browser-selection"><strong>{selected.name}</strong><span>{selected.path}</span><button className="quiet-button" disabled={locked} onClick={() => open(selected)}>{selected.location ? "Open location" : "Open selected file"}</button></div>}
      {selected && !selected.location && (selected.archive || isNativeResource(selected.name)) && <button className="quiet-button" disabled={locked} onClick={() => onOpen({ ...selected, edition: selected.edition || listing?.edition || edition }, game)}>{isNativeResource(selected.name) ? "Open native workspace" : /\.gxt2$/i.test(selected.name) ? "Edit game text" : "Open binary workspace"}</button>}
      {selected?.archive && selected.entry_id && selected.member_path && onStage && <button className="quiet-button" disabled={locked} onClick={() => onStage(selected, game)}>Stage selected archive member</button>}
      {utilityArchive && game && <RpfArchiveUtilities key={utilityArchive} client={client} result={{ source: utilityArchive, gta_path: game }} entry={utilityEntry} selectedIds={checked.flatMap(item => item.entry_id ? [item.entry_id] : [])} disabled={busy} onGuardChange={setUtilityGuarded} />}
      {checked.length > 0 && <p>{checked.length} files selected from one archive. Selection applies to this page; output folders preserve separate archive layers.</p>}
      {preview?.text && <pre className="browser-preview-text">{preview.text}</pre>}
      {preview?.artifact && <img className="browser-preview-image" src={preview.artifact.preview_url || convertFileSrc(preview.artifact.path)} alt={`Preview of ${selected?.name}`} />}
      {preview?.warnings?.length ? <p role="status">{preview.warnings.join(" · ")}</p> : null}
      {listing && <div className="browser-pagination"><button className="quiet-button" disabled={locked || listing.offset === 0} onClick={() => void run(current, Boolean(listing.query), Math.max(0, listing.offset - listing.page_size), false)}>Previous page</button><span>{listing.offset + (listing.entries.length ? 1 : 0)}–{listing.offset + listing.entries.length} of {listing.matched_count}</span><button className="quiet-button" disabled={locked || !listing.has_more} onClick={() => void run(current, Boolean(listing.query), listing.offset + listing.page_size, false)}>Next page</button></div>}
      {listing?.warnings.length ? <details open={!listing.scan_complete}><summary>Coverage and decoder findings ({listing.warnings.length})</summary><ul>{listing.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details> : null}
    </div></div>
  </section>;
}
