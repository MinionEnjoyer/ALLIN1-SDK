import { FormEvent, useState } from "react";
import type { DesktopClient } from "./types";

export default function QwenSetup({ client, onSaved, onClose }: {
  client: DesktopClient; onSaved: () => void; onClose: () => void;
}) {
  const [mode, setMode] = useState("compatible_api");
  const [fields, setFields] = useState({ endpoint: "", model_name: "", api_key_env: "", runtime_path: "", model_path: "" });
  const [structured, setStructured] = useState(false);
  const [trusted, setTrusted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = (key: keyof typeof fields, label: string, placeholder: string, required = true) => (
    <label>{label}<input value={fields[key]} placeholder={placeholder} required={required} spellCheck={false}
      onChange={(event) => setFields({ ...fields, [key]: event.target.value })} /></label>
  );
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await client.configureAssistant({
        settings: { mode, ...fields, structured_output: structured }, authoring_confirmed: true,
      });
      if (response.operation === "error") throw new Error(String(response.payload.message || "Could not save assistant settings."));
      if (!response.payload.result) throw new Error("No settings confirmation was returned.");
      onSaved();
    } catch (reason) {
      setError(String(reason).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };
  return <form className="qwen-setup" aria-label="Standalone Qwen setup" onSubmit={(event) => void save(event)}>
    <p>Save a new SDK-only configuration. This replaces any SDK settings; existing Launcher settings stay unchanged.</p>
    <fieldset disabled={busy}>
      <label>Provider<select value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="compatible_api">Compatible API</option>
        <option value="custom_local">Local llama.cpp + GGUF</option>
        <option value="disabled">Disabled</option>
      </select></label>
      {mode === "compatible_api" && <>
        {input("endpoint", "Endpoint", "http://127.0.0.1:8080/v1")}
        {input("model_name", "Provider model name", "Exact model ID from your provider")}
        {input("api_key_env", "API key environment variable (optional)", "QWEN_API_KEY — not the key itself", false)}
        <label className="qwen-check"><input type="checkbox" checked={structured} onChange={(event) => setStructured(event.target.checked)} />This provider supports JSON-schema structured output</label>
        <p>Asking sends your question and SDK context to this endpoint. Remote endpoints require HTTPS. Configure keys in your environment and restart the SDK.</p>
      </>}
      {mode === "custom_local" && <>
        {input("runtime_path", "llama-server.exe path", "C:\\AI\\llama.cpp\\llama-server.exe")}
        {input("model_path", "GGUF model path", "C:\\AI\\models\\qwen.gguf")}
        <label className="qwen-check"><input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} required />I trust this runtime. The SDK may start it when I ask Qwen.</label>
      </>}
      <p>Saving does not download files, start a runtime, or contact a provider. Qwen is optional; other SDK tools work without it.</p>
      <div className="qwen-setup-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary-button" type="submit">{busy ? "Saving…" : "Save SDK settings"}</button></div>
    </fieldset>
    {error && <p role="alert">{error}</p>}
  </form>;
}
