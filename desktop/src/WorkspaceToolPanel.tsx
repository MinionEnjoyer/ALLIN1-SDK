import { useId, useState, type ReactNode, type KeyboardEvent } from "react";
import "./WorkspaceToolPanel.css";

/** Secondary navigation for dense workspaces; collapsing never unmounts editors. */
export default function WorkspaceToolPanel({ title, storageKey, children }: {
  title: string; storageKey: string; children: ReactNode;
}) {
  const id = useId();
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(storageKey) === "collapsed"; } catch { return false; }
  });
  function toggle() {
    setCollapsed(value => {
      const next = !value;
      try { localStorage.setItem(storageKey, next ? "collapsed" : "expanded"); } catch { /* Optional preference. */ }
      return next;
    });
  }
  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[role="tab"]:not(:disabled)'));
    const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
    if (current < 0 || !tabs.length) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus();
  }
  return <aside className={`workspace-tool-panel${collapsed ? " is-collapsed" : ""}`} aria-label={`${title} panel`}>
    <div className="workspace-tool-panel-heading">
      {!collapsed && <strong>{title}</strong>}
      <button className="quiet-button" aria-expanded={!collapsed} aria-controls={id}
        aria-label={`${collapsed ? "Expand" : "Collapse"} ${title}`} title={`${collapsed ? "Expand" : "Collapse"} ${title}`} onClick={toggle}>
        <span aria-hidden="true">{collapsed ? "»" : "«"}</span>
      </button>
    </div>
    <div id={id} hidden={collapsed} className="workspace-tool-panel-options" role="tablist" aria-label={title} aria-orientation="vertical" onKeyDown={navigate}>
      {children}
    </div>
  </aside>;
}
