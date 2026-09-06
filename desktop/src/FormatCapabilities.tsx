import { useState } from "react";
import catalog from "./formatCapabilities.json";
import "./FormatCapabilities.css";

export default function FormatCapabilities() {
  const [query, setQuery] = useState("");
  const [edition, setEdition] = useState<"Legacy" | "Enhanced">("Enhanced");
  const needle = query.trim().toLowerCase();
  const formats = catalog.formats.filter(format => Object.values(format).some(value => typeof value === "string" && value.toLowerCase().includes(needle)));
  return <section className="format-capabilities" aria-label="Format capabilities">
    <h2>Format capabilities</h2>
    <p>{catalog.scope}</p>
    <p>Matrix revision {catalog.revision} · schema {catalog.schemaVersion}. Rebuild still requires a successful file-specific validation receipt. Unsupported variants remain unsupported even when their extension is listed.</p>
    <div className="format-capability-controls">
      <label>Find a format or capability<input value={query} onChange={event => setQuery(event.target.value)} placeholder="YMT, audio, texture…" /></label>
      <label>Evidence edition<select value={edition} onChange={event => setEdition(event.target.value as typeof edition)}><option>Legacy</option><option>Enhanced</option></select></label>
    </div>
    <p role="status">{formats.length} of {catalog.formats.length} formats</p>
    <div className="format-capability-scroll" tabIndex={0} aria-label="Scrollable format matrix">
      <table><thead><tr><th scope="col">Format</th><th scope="col">Inspect / preview</th><th scope="col">Export / edit / rebuild</th><th scope="col">Limits & {edition} evidence</th></tr></thead><tbody>
        {formats.map(format => <tr key={format.suffix}>
          <th scope="row">{format.suffix}<small>{format.label}</small></th>
          <td><p>{format.inspection}</p><p>{format.preview}</p></td>
          <td><p><strong>Export:</strong> {format.export}</p><p><strong>Edit:</strong> {format.editing}</p><p><strong>Rebuild:</strong> {format.rebuild}</p></td>
          <td><p>{format.limitations}</p><p>{format.editionEvidence[edition]}</p><details><summary>Implementation & evidence references</summary><p><code>{format.implementation}</code></p>{format.evidence.length ? <ul>{format.evidence.map(path => <li key={path}><code>{path}</code></li>)}</ul> : <p>No edition-specific acceptance evidence recorded.</p>}</details></td>
        </tr>)}
      </tbody></table>
      {!formats.length && <p>No matching format. Unknown extensions have no implied semantic editing support.</p>}
    </div>
  </section>;
}
