import { useEffect, useState } from "react";
import ModelMaterialsWorkspace from "./ModelMaterialsWorkspace";
import TextureDictionaryWorkspace from "./TextureDictionaryWorkspace";
import type { DesktopClient } from "./types";

type ModelArea = "materials" | "textures";

export default function ModelsWorkspace({ client, initialSource = "", onGuardChange }: { client: DesktopClient; initialSource?: string; onGuardChange?: (guarded: boolean) => void }) {
  const [guarded, setGuarded] = useState(false);
  useEffect(() => { onGuardChange?.(guarded); }, [guarded, onGuardChange]);
  const [area, setArea] = useState<ModelArea>(initialSource.toLocaleLowerCase().endsWith(".ytd") ? "textures" : "materials");

  useEffect(() => {
    if (initialSource) setArea(initialSource.toLocaleLowerCase().endsWith(".ytd") ? "textures" : "materials");
  }, [initialSource]);

  return <section className="workspace-section models-workspace-shell" aria-label="Models and texture tools">
    <nav className="models-area-tabs" aria-label="Model asset area">
      <button type="button" disabled={guarded && area !== "materials"} className={area === "materials" ? "selected" : ""} aria-current={area === "materials" ? "page" : undefined} onClick={() => setArea("materials")}><span>Model surfaces</span><small>Shaders, bindings, geometry</small></button>
      <button type="button" disabled={guarded && area !== "textures"} className={area === "textures" ? "selected" : ""} aria-current={area === "textures" ? "page" : undefined} onClick={() => setArea("textures")}><span>Texture dictionaries</span><small>YTD preview and authoring</small></button>
    </nav>
    {area === "materials" ? <ModelMaterialsWorkspace client={client} initialSource={initialSource} onGuardChange={setGuarded} /> : <TextureDictionaryWorkspace client={client} initialSource={initialSource} onGuardChange={setGuarded} />}
  </section>;
}
