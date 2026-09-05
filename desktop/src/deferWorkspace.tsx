import { Component, lazy, Suspense, type ComponentType, type ReactNode } from "react";

class WorkspaceLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    // Never reload the application automatically: other workspaces may own jobs
    // or unsaved drafts. The shell and its close/navigation guards stay mounted.
    return this.state.failed ? <section className="workspace-section" role="alert">
      <h2>This workspace could not load</h2>
      <p>The application has not restarted. Keep any pending work safe before closing and reopening the SDK.</p>
    </section> : this.props.children;
  }
}

/** Load specialist code on first render without changing its mount lifetime. */
export function deferWorkspace<P extends object>(loader: () => Promise<{ default: ComponentType<P> }>) {
  const Workspace = lazy(loader);
  return function DeferredWorkspace(props: P) {
    return <WorkspaceLoadBoundary><Suspense fallback={<section className="workspace-section" role="status" aria-live="polite">Loading workspace…</section>}>
      <Workspace {...props} />
    </Suspense></WorkspaceLoadBoundary>;
  };
}
