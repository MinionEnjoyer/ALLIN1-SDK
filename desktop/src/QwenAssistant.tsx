import { FormEvent, useEffect, useRef, useState } from "react";
import type { AssistantPromptResult, AssistantStatusResult, DesktopClient, Envelope } from "./types";
import QwenSetup from "./QwenSetup";

function resultValue<T>(message: Envelope): T {
  const payload = message.payload as Record<string, unknown>;
  if (message.operation === "error") {
    throw new Error(String(payload.message ?? payload.error ?? "Assistant request failed."));
  }
  const result = payload.result;
  if (!result || typeof result !== "object") throw new Error("Assistant request returned no result.");
  return result as T;
}

export default function QwenAssistant({ client, visible }: { client: DesktopClient; visible: boolean }) {
  const [status, setStatus] = useState<AssistantStatusResult | null>(null);
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AssistantPromptResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeJob, setActiveJob] = useState("");
  const [setupOpen, setSetupOpen] = useState(false);
  const statusRequested = useRef(false);

  const runStatus = async () => {
    setError("");
    let finished = false;
    try {
      const started = await client.startJob("assistant_status", {}, "assistant-status", (message) => {
        if (!message.terminal) return;
        finished = true;
        setActiveJob("");
        try {
          setStatus(resultValue<AssistantStatusResult>(message));
        } catch (reason) {
          setError(String(reason).replace(/^Error:\s*/, ""));
        }
      });
      if (!finished) setActiveJob(started.job_id);
    } catch (reason) {
      setError(String(reason).replace(/^Error:\s*/, ""));
    }
  };

  useEffect(() => {
    if (!visible || statusRequested.current) return;
    statusRequested.current = true;
    void runStatus();
  }, [visible]);

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    if (!question.trim() || busy || status?.enabled !== true) return;
    setBusy(true);
    setError("");
    setResult(null);
    let finished = false;
    try {
      const revision = `assistant-${Date.now()}`;
      const started = await client.startJob(
        "assistant_prompt",
        { question: question.trim(), max_tokens: 640 },
        revision,
        (message) => {
          if (!message.terminal) return;
          finished = true;
          setBusy(false);
          setActiveJob("");
          try {
            setResult(resultValue<AssistantPromptResult>(message));
          } catch (reason) {
            setError(String(reason).replace(/^Error:\s*/, ""));
          }
        },
      );
      if (!finished) setActiveJob(started.job_id);
    } catch (reason) {
      setBusy(false);
      setError(String(reason).replace(/^Error:\s*/, ""));
    }
  };

  const cancel = async () => {
    if (!activeJob) return;
    try {
      await client.cancelJob(activeJob);
    } finally {
      setBusy(false);
      setActiveJob("");
      setError("Assistant request cancelled. The local runtime was stopped.");
    }
  };

  const advisory = result?.advisory;
  return (
    <section className="qwen-assistant" aria-label="Qwen assistant" hidden={!visible}>
      <header>
        <span><strong>Qwen Assistant</strong><small>Structured, grounded, advisory only</small></span>
        <span className={`status-pill ${status?.enabled ? "success" : status ? "warning" : ""}`}>{status?.enabled ? "Ready" : status ? "Not configured" : "Checking"}</span>
      </header>
      <div className="qwen-status">
        <span>{status?.model || "No model selected"}</span><span>{status?.mode?.replaceAll("_", " ") || "Loading provider status"}</span><button onClick={() => void runStatus()} disabled={busy || Boolean(activeJob)}>Refresh</button>
      </div>
      {status && !status.enabled && <p className="qwen-notice">{status.message || "Configure Qwen here in the SDK. No Launcher is required."}</p>}
      <button className="qwen-setup-toggle" disabled={busy || Boolean(activeJob)} aria-expanded={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>Standalone setup</button>
      {setupOpen ? <QwenSetup client={client} onClose={() => setSetupOpen(false)} onSaved={() => { setSetupOpen(false); setResult(null); void runStatus(); }} /> : <form onSubmit={(event) => void ask(event)}>
        <label htmlFor="qwen-question">Ask about SDK development, packages, or the selected repository</label>
        <textarea id="qwen-question" value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} placeholder="Ask a focused question…" disabled={busy} />
        <div><small>No commands or writes can be performed from an answer.</small>{busy ? <button type="button" className="danger-button" onClick={() => void cancel()}>Stop Qwen</button> : <button className="primary-button" disabled={!question.trim() || status?.enabled !== true}>Ask Qwen</button>}</div>
      </form>}
      {error && <p className="qwen-error" role="alert">{error}</p>}
      {result && <article className="qwen-result">
        <div className="qwen-result-heading"><strong>{advisory?.summary || result.text || "Assistant response"}</strong><small>{result.model} · {result.elapsed_seconds.toFixed(1)}s · {result.actual_output_tokens ?? "—"} output tokens</small></div>
        {advisory?.findings?.map((finding, index) => <div className="qwen-finding" key={`${finding.file}-${finding.line}-${index}`}><span className={`status-pill ${["critical", "blocker", "high"].includes(finding.severity) ? "danger" : finding.severity === "medium" ? "warning" : ""}`}>{finding.severity}</span><span><strong>{finding.status} · {Math.round(finding.confidence * 100)}%</strong><small>{finding.evidence}</small>{finding.file && <code>{finding.file}{finding.line ? `:${finding.line}` : ""}</code>}</span></div>)}
        {advisory?.missing_context?.length ? <div className="qwen-followup"><strong>Missing context</strong><span>{advisory.missing_context.join(" · ")}</span></div> : null}
        {result.safety_flags.length > 0 && <div className="qwen-followup"><strong>Safety notes</strong><span>{result.safety_flags.join(" · ")}</span></div>}
      </article>}
    </section>
  );
}
