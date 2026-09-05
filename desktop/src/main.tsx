import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const previewMode = import.meta.env.DEV ? new URLSearchParams(window.location.search).get("preview") : null;

async function mount() {
  const client = import.meta.env.DEV && previewMode
    ? (await import("./previewClient")).createPreviewClient(previewMode) : undefined;
  createRoot(document.getElementById("root")!).render(<StrictMode><App client={client} /></StrictMode>);
}
void mount();
