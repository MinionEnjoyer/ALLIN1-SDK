import { useEffect, useRef, useState } from "react";
import { convertFileSrc } from "@tauri-apps/api/core";
import type { AssetPreviewResult, DesktopClient } from "./types";
import { pedResult } from "./PedWorkbench";
import type { PedSnapshot } from "./PedWorkbench";

export function PedPreview({ client, snapshot, asset, gtaPath }: {
  client: DesktopClient; snapshot: PedSnapshot; asset: string; gtaPath: string;
}) {
  const ped = snapshot.selected_ped!;
  const models = snapshot.assets.filter(a => [".ydd", ".ydr"].includes(a.suffix) && a.stem.toLowerCase() === ped.name.toLowerCase());
  const textures = snapshot.assets.filter(a => a.suffix === ".ytd" && a.stem.toLowerCase() === ped.name.toLowerCase());
  const [model, setModel] = useState(models.length === 1 ? models[0].path : "");
  const [texture, setTexture] = useState(textures.length === 1 ? textures[0].path : "");
  const [edition, setEdition] = useState(["legacy", "enhanced"].includes(snapshot.decoder_edition.toLowerCase()) ? snapshot.decoder_edition.toLowerCase() : "");
  const [refresh, setRefresh] = useState(0);
  const [results, setResults] = useState<Record<string, AssetPreviewResult>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const job = useRef("");
  const paths = asset ? [asset] : [model, texture].filter(Boolean);
  const identity = JSON.stringify(paths);
  useEffect(() => {
    let cancelled = false;
    setResults({}); setErrors({}); setBusy(false);
    if (!edition || !paths.length) return;
    const request = async (entry: string) => new Promise<AssetPreviewResult>((resolve, reject) => {
      let finished = false;
      client.startJob("preview_asset", { source: snapshot.source, entry, edition, ...(gtaPath ? { gta_path: gtaPath } : {}) },
        `ped-preview:${refresh}:${entry}`, message => {
          if (cancelled || finished || !message.terminal) return;
          finished = true; job.current = "";
          try {
            const result = pedResult<AssetPreviewResult>(message);
            if (result.path !== entry || !Array.isArray(result.warnings) || !["image", "text", "metadata"].includes(result.display_kind)) throw new Error("Preview returned evidence for a different or invalid asset");
            resolve(result);
          } catch (reason) { reject(reason); }
        }).then(started => {
          if (cancelled) { if (!finished) void client.cancelJob(started.job_id); }
          else if (!finished) job.current = started.job_id;
        }).catch(reject);
    });
    const timer = window.setTimeout(() => {
      setBusy(true);
      void (async () => {
        // The broker has one read-only worker; finish each request before the next.
        for (const path of paths) {
          if (cancelled) return;
          try { const result = await request(path); if (!cancelled) setResults(current => ({ ...current, [path]: result })); }
          catch (reason) { if (!cancelled) setErrors(current => ({ ...current, [path]: String(reason) })); }
        }
        if (!cancelled) setBusy(false);
      })();
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timer); if (job.current) { void client.cancelJob(job.current); job.current = ""; } };
  }, [client, snapshot.source, identity, edition, gtaPath, refresh]);
  const cards = asset ? [{ label: "Selected asset", path: asset, count: 1 }] : [
    { label: "Diagnostic model", path: model, count: models.length },
    { label: "Texture contact sheet", path: texture, count: textures.length },
  ];
  return <section className="ped-preview" aria-label="Ped native preview">
    <div className="ped-toolbar"><div><h4>{asset ? "Exact asset inspection" : "Native ped evidence"}</h4><p>Saved package assets only. No assembled outfit, animation, load-limit estimate or runtime acceptance is implied.</p></div>
      <button className="quiet-button" disabled={busy || !edition || !paths.length} onClick={() => setRefresh(r => r + 1)}>Refresh preview</button></div>
    <div className="ped-preview-controls"><label>Decoder edition<select value={edition} onChange={e => setEdition(e.target.value)}><option value="">Choose game edition</option><option value="legacy">Legacy</option><option value="enhanced">Enhanced</option></select></label>
      {!asset && <><label>Exact model asset<select value={model} onChange={e => setModel(e.target.value)}><option value="">{models.length ? "Choose model path" : "No exact drawable found"}</option>{models.map(a => <option key={a.path}>{a.path}</option>)}</select></label>
        <label>Exact texture asset<select value={texture} onChange={e => setTexture(e.target.value)}><option value="">{textures.length ? "Choose texture path" : "No exact texture found"}</option>{textures.map(a => <option key={a.path}>{a.path}</option>)}</select></label></>}
    </div>
    {!edition && <p>Choose the decoder edition explicitly. This does not certify compatibility.</p>}
    <div className={`ped-preview-panes ${asset ? "single" : ""}`}>{cards.map(card => {
      const result = results[card.path];
      const url = result?.artifact ? result.artifact.preview_url ?? convertFileSrc(result.artifact.path) : null;
      return <section key={card.label} aria-label={card.label}><header><h5>{card.label}</h5><code>{card.path || "No asset selected"}</code></header><div className="ped-preview-body">
        {!card.path && <p>{card.count ? "Multiple candidates remain separate. Choose an exact package path above." : "No exact package-owned asset was found. External assets are not substituted."}</p>}
        {card.path && !result && !errors[card.path] && <p>{busy ? "Reading and decoding package bytes…" : "No preview loaded."}</p>}
        {errors[card.path] && <p role="alert">{errors[card.path]}</p>}
        {result?.display_kind === "image" && url && <img src={url} alt={`${card.label}: ${card.path}`} />}
        {result?.display_kind === "text" && <pre>{result.text}</pre>}
        {result?.display_kind === "metadata" && <p>No renderable image was produced. See the decoder evidence below.</p>}
        {result && <><p>{result.bytes_read.toLocaleString()} / {result.size.toLocaleString()} bytes read{result.truncated || result.text_truncated ? " · bounded / truncated" : ""}</p><code>SHA-256 · {result.sha256 || "Unavailable for partial read"}</code>
          {result.warnings.map((warning, i) => <p key={i}>{warning}</p>)}<details><summary>Decoder evidence</summary><pre>{JSON.stringify(result.metadata, null, 2)}</pre></details></>}
      </div></section>;
    })}</div>
  </section>;
}
