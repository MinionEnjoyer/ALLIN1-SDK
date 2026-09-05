# ADR 0003: Tauri v2 desktop shell over the Python SDK

- Status: accepted for incremental delivery
- Date: 2026-08-31
- Owners: ALLIN1 SDK maintainers
- Supersedes: none

## Context

The shipping desktop is a mature Tkinter application. Its UI modules call a
large, well-tested Python domain layer and expose the same validation, package
ownership, archive transaction, rollback, and risk policies as the CLI and
Agent API. Reimplementing those policies in TypeScript or Rust would create a
second authority and make parity difficult to prove.

The desktop also needs a responsive, accessible UI, persistent panes and window
state, bounded streaming work, clean cancellation, single-instance routing,
and an updater that owns the complete application process tree.

## Decision

Add a new `desktop/` application with this trust boundary:

```text
React + TypeScript
  -> allowlisted Tauri commands and ordered channels
  -> Rust sidecar broker and native-dialog boundary
  -> one persistent ALLIN1 Python desktop sidecar over JSONL stdio
  -> existing Python CLI and domain services
  -> existing RpfPatcher, Blender, CMake/MSVC, Qwen, and GTA integrations
```

The Tkinter entry point and its release artifacts remain intact throughout the
beta. The Tauri application is an additional entry point until every row in the
feature-parity matrix is verified.

### Ownership

React owns presentation, focus, navigation, view state, dirty-state prompts,
and stale-view suppression. It never receives an unrestricted shell, local
HTTP endpoint, or filesystem capability.

Rust owns process startup and shutdown, protocol negotiation, bounded request
validation, native file/folder dialogs, path canonicalization, single-instance
argument forwarding, window state, crash detection, recovery initiation, and
updater pre-exit coordination. A failed in-flight request is surfaced to the
user and is never replayed automatically.

Python remains authoritative for command discovery, risk classification,
package and archive validation, containment, receipts, acknowledgements,
atomic writes, backup/rollback, stale revisions, and tool integration. The new
desktop protocol delegates command execution to the existing Agent API rather
than bypassing it.

### Protocol

`allin1-sdk-desktop-sidecar` is a persistent, display-free process. Every line
is one UTF-8 JSON envelope conforming to `docs/desktop-protocol-v1.schema.json`.
Protocol data is written only to stdout; diagnostics are written only to
stderr. The maximum inbound envelope is 256 KiB and command output remains
bounded by the Agent API.

The v1 operations are `handshake`, `catalog`, `execute`, `inspect_package`,
`preview_asset`, `check_update`, `start_job`, `cancel_job`, `job_event`,
`result`, `error`, and `shutdown`. `start_job` initially accepts read-only work
only. This gives the vertical slice real cancellation without terminating a
process that may be in the middle of an authoring or game/archive transaction.
Cooperative mutation cancellation must be implemented in the owning Python
service before that operation can be made cancellable.

Only one heavy job runs at a time. Each job carries a caller revision and
monotonic sequence. React accepts events only for its current job and revision.
Cancelling a read-only job terminates its isolated worker and emits one terminal
event. No interrupted mutation is automatically replayed.

### Capabilities and content security

The main WebView receives only the generated permissions for the small desktop
command set and the core event/window permissions it needs. It receives no
shell, filesystem, HTTP, process, updater, or dialog plugin permission.
Selection dialogs are typed Rust commands with fixed filters. Rust
canonicalizes selected paths; Python resolves and validates them again.

Production content uses a local-only content security policy. Package text is
rendered as React text nodes, never raw HTML. The broker creates one dedicated
`allin1-previews` application-cache directory and passes it to Python through a
process-owned environment variable. Python normalizes untrusted images to PNG,
names each artifact by its SHA-256 digest, writes atomically, and prunes the
bounded cache. Tauri's asset protocol is enabled only for
`$APPCACHE/allin1-previews/**/*`; React receives no filesystem permission and
cannot use the protocol outside that static scope. Package bytes and preview
images are not transported as base64 protocol payloads.

### Development and packaged discovery

In development the Rust broker launches
`python -m allin1_sdk.desktop_sidecar_host` using the configured developer
Python. A packaged build resolves
`sidecar/ALLIN1-SDK-Desktop-Sidecar.exe` beneath the Tauri resource directory
and sets `ALLIN1_SDK_HOME` to that resource directory before launch. React
cannot choose either executable or its arguments.

## Production vertical slice

The first slice contains the persistent shell and navigation, Package Linker
inspection, loose RPF inspection through the existing CLI/RpfPatcher path, the
docked SDK Console, Help Center catalog, update check, and recognized legacy
CLI launch arguments. Unsupported launch targets route to their destination
workspace with a clear experimental state; they do not silently fall back to a
different tool.

## Consequences

- The Python CLI and Agent API remain backward compatible.
- Existing caches can survive ordinary UI actions in the persistent sidecar.
- Read-only jobs are cancellable now; safe mutation cancellation remains gated
  on explicit domain support.
- Tauri adds a Rust/Node toolchain and a separately signed executable/installer.
- During beta, release automation must build and test both desktop entry points.
- The legacy updater cannot replace the Tauri install layout unchanged. The
  verified download/staging logic remains reusable, but final swap and relaunch
  coordination moves to Rust after the sidecar process tree is stopped.

## Rejected alternatives

- **Rewrite the SDK in Rust.** This would duplicate mature safety and native
  integration behavior without a measured requirement.
- **Run a localhost Python server.** It adds network origin, authentication,
  lifecycle, and port-management concerns without benefit for a local desktop.
- **Expose Tauri shell or filesystem plugins to React.** That would make the
  WebView a broad local execution boundary and violate least privilege.
- **Launch one Python process per click.** It prevents cache reuse and makes
  lifecycle, crash reporting, and shutdown less deterministic.
- **Remove Tkinter during the port.** It eliminates the only complete parity
  oracle before the replacement is proven.
