import { useEffect, useId, useRef, useState } from "react";
import type { DesktopClient } from "./types";
import type { WeaponSnapshot } from "./WeaponWorkbench";
import VehicleViewport from "./VehicleViewport";

export interface WeaponPreviewLinks {
  selected_part: string | null;
  parts: { id: string; kind: string; name: string; model: string; reason: string;
    attach_bones?: string[]; default?: boolean;
    assets: { path: string; texture_entry: string | null; texture_entries: string[] }[] }[];
  texture_entries: string[];
  warnings: string[];
}

export function WeaponNativePreview({ client, snapshot, epoch, dirty }: {
  client: DesktopClient; snapshot: WeaponSnapshot; epoch: number; dirty: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <section className="weapon-native-preview" aria-label="Weapon model preview">
    <header>
      <div><h4>Model preview</h4><p>Inspect the weapon body and attachment assets separately.</p></div>
      <button className="quiet-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>{open ? "Hide model preview" : "Show model preview"}</button>
    </header>
    <div id={id} hidden={!open}>{open && <PreviewSelection key={epoch} client={client} snapshot={snapshot} dirty={dirty} />}</div>
  </section>;
}

function PreviewSelection({ client, snapshot, dirty }: { client: DesktopClient; snapshot: WeaponSnapshot; dirty: boolean }) {
  const links = snapshot.native_preview;
  const [partId, setPartId] = useState(links?.selected_part ?? "");
  const part = links?.parts.find(item => item.id === partId);
  const [entry, setEntry] = useState(part?.assets.length === 1 ? part.assets[0].path : "");
  const asset = part?.assets.find(item => item.path === entry);
  const [texture, setTexture] = useState(asset?.texture_entry ?? "");
  const [edition, setEdition] = useState(["legacy", "enhanced"].includes(snapshot.project.edition.toLowerCase()) ? snapshot.project.edition.toLowerCase() : "");
  const [gtaPath, setGtaPath] = useState("");
  const [error, setError] = useState("");
  const [selecting, setSelecting] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const chooseGame = async () => {
    setSelecting(true); setError("");
    try {
      const selected = await client.selectPath("gta_folder");
      if (mounted.current && selected) setGtaPath(selected);
    } catch (reason) { if (mounted.current) setError(String(reason)); }
    finally { if (mounted.current) setSelecting(false); }
  };
  if (!links) return <p className="weapon-preview-empty">Preview links are unavailable. Refresh with the updated SDK sidecar.</p>;
  return <div className="weapon-preview-content">
    <p className="weapon-preview-note">Package geometry only—not an assembled weapon, animation, or in-game scope alignment test. Optional attachments are not equipped by this view.</p>
    {dirty && <p className="weapon-preview-note" role="status">Showing saved package assets. Unsaved metadata edits are not applied to this preview.</p>}
    <div className="weapon-preview-controls">
      <label>Preview part<select value={partId} onChange={event => {
        const next = links.parts.find(item => item.id === event.target.value);
        const nextAsset = next?.assets.length === 1 ? next.assets[0] : null;
        setPartId(event.target.value); setEntry(nextAsset?.path ?? ""); setTexture(nextAsset?.texture_entry ?? "");
      }}>{links.parts.map(item => <option key={item.id} value={item.id}>{item.kind === "weapon" ? "Body" : "Attachment"} · {item.name}</option>)}</select></label>
      <label>Model asset<select value={entry} disabled={!part?.assets.length} onChange={event => {
        setEntry(event.target.value); setTexture(part?.assets.find(item => item.path === event.target.value)?.texture_entry ?? "");
      }}><option value="">{part?.assets.length ? "Choose an exact model asset" : "No bundled model"}</option>{part?.assets.map(item => <option key={item.path}>{item.path}</option>)}</select></label>
      <label>Texture dictionary<select value={texture} disabled={!asset} onChange={event => setTexture(event.target.value)}>
        <option value="">No linked YTD · geometry only</option>{links.texture_entries.map(path => <option key={path}>{path}</option>)}
      </select></label>
      <label>Preview edition<select value={edition} onChange={event => setEdition(event.target.value)}>
        <option value="">Choose game edition</option><option value="legacy">Legacy</option><option value="enhanced">Enhanced</option>
      </select></label>
    </div>
    <div className="weapon-preview-context">
      <p>{part?.model && <code>{part.model}</code>}{part?.attach_bones?.length ? <span>Declared attachment point: {part.attach_bones.join(", ")} · {part.default ? "Default" : "Optional"}</span> : null}</p>
      <div className="heading-actions"><button className="quiet-button" disabled={selecting} onClick={() => void chooseGame()}>{selecting ? "Selecting…" : "Select decoder game folder (optional)"}</button>
        {gtaPath && <button className="quiet-button" onClick={() => setGtaPath("")}>Clear game folder</button>}</div>
    </div>
    {gtaPath && <p className="weapon-preview-note">Decoder resources only; no game files are changed.<code>{gtaPath}</code></p>}
    {error && <p role="alert">{error}</p>}
    {links.warnings.map(warning => <p className="weapon-preview-note" key={warning}>{warning}</p>)}
    {!part ? <p className="weapon-preview-empty">No weapon or component is selected.</p>
      : !part.assets.length ? <p className="weapon-preview-empty">{part.reason} Stock or external assets are not substituted automatically.</p>
      : !asset ? <p className="weapon-preview-empty">Choose the exact model path above. Base, high-detail, and duplicate-name files remain separate choices.</p>
      : !edition ? <p className="weapon-preview-empty">The package does not identify a single game edition. Choose Legacy or Enhanced to decode its native assets.</p>
      : <>
        {asset.texture_entries.length > 1 && !texture && <p className="weapon-preview-note">Multiple matching texture dictionaries exist. Choose the exact package path to use textures.</p>}
        {texture && !asset.texture_entries.includes(texture) && <p className="weapon-preview-note">Manually selected dictionary; no declared or exact-name link was found for this model.</p>}
        <VehicleViewport key={`${part.id}:${entry}:${texture}:${edition}:${gtaPath}`} client={client} source={snapshot.source}
          entry={entry} edition={edition} gtaPath={gtaPath} model={part.model} textureEntry={texture || null}
          ariaLabel="Interactive weapon viewport" meshLabel="Mesh part" />
      </>}
  </div>;
}
