# Ped Workbench migration and YMT follow-on

## Scope and sequence

The current focus is the Ped Workbench, not the remaining RPF graph/program UI.
Bring the existing Tkinter workflow into Tauri v2/React first. The requested
YMT dependency inspector and diagnostic contract follow this baseline; none of
the ped authoring/preview checks below certify a YMT limit expansion.

This task does not authorize Launcher edits, live GTA writes, engine patches,
asset rewrites to make diagnostics pass, or distribution of a placeholder ASI.

## Ped capability map

| Tkinter capability | React / shared backend |
|---|---|
| Folder and supported bounded archive intake | Ped folder/archive dialogs, `inspect_ped_workbench`, existing `AddonPackageInspector` |
| Catalog, search, Ctrl+F, Escape | Searchable catalog, scoped shortcuts, clear filter, individually collapsible side panes |
| Definition fields and integration findings | Seven metadata fields plus identity/source, asset-presence and declaration evidence, complete bounded package findings |
| Safe authoring workspace | Review destination/source digest, confirm new copied workspace; reopen existing workspace |
| Seven existing XML fields | `PedAuthoringWorkspace.update`; absent nodes disabled, unknown XML/text/value/ref representations preserved |
| Identity and asset migration | Reviewed exact metadata changes and asset renames; existing family/collision validation; one transaction |
| New from template | Complete donor metadata clone plan, explicit target identity/props, additions, exact sources and hashes, blockers, fresh confirmation |
| Undo latest | Verified original domain history; reviewed changes/renames; restored identity/donor selected after undo |
| Diagnostic model and texture sheet | Two independent exact-asset `preview_asset` reads through the existing native decoder/cache; explicit edition, refresh and unavailable evidence |
| Open selected related asset | Asset-family selection opens exact bounded text/image/native evidence inline |
| Dirty navigation | Field, identity and clone drafts plus in-flight reviews/writes guard catalog, categories, shell navigation and direct-open requests |
| Help | Existing Help Center and ped topic; desktop-specific section below |

`ped_desktop.py` coordinates the GUI but delegates mutations to the same domain
used by Tkinter, CLI and Agent API. It does not introduce a TypeScript writer.
Read-only inspect/review operations run in the cancellable sidecar worker;
apply is a separate synchronous native command requiring a digest and fresh
`authoring_confirmed: true`.

Reviews bind actual copied-content hashes and manifest state, not only file
sizes or revisions. Edit, rename, clone and undo recheck that state inside the
existing cross-process workspace lock. Clone also retains its domain plan hash.
The scanner now retains duplicate ped records rather than silently choosing the
first one. Same-name records are inspectable, including individual records in
one source file, but cannot become ambiguous authoring targets.

Direct RPF authoring still requires extraction into a reviewed source tree,
matching the existing boundary. Very large evidence is refused explicitly when
it exceeds desktop response limits, not truncated into a consent screen. The
model view remains diagnostic, not assembled clothing, skinning/animation,
shader fidelity, or an in-game acceptance test.

## YMT infrastructure audit

| Existing infrastructure | Reuse / needed extension |
|---|---|
| `addon_importer.py`, `PackageAssetReader`, `RpfExplorerService` | Reuse bounded reads and explicit nested member IDs. Add a separate complete YMT catalog, content-based classifiers and evidence-backed dependency/mount graph. Ped filename candidates are not this graph. |
| `native_assets.py`, native RPF helper | Native inspection/export exists. Confirm decoded YMT schema coverage before deriving relationships; unsupported content stays unsupported. No runtime residency assumptions. |
| `ped_authoring.py`, `authoring_core.py` | Existing copy, lock, transaction, validation and undo. The YMT inspector remains read-only and must not invoke authoring to make a report pass. |
| `story_axle_runtime_builder.py` | Existing CMake/CTest/VS/MSVC/Windows SDK discovery, strict overrides, fingerprint checks, isolated C++17/CTest probe, x64 artifact/build receipts. Extract project-contract-based requirements only after regression coverage; do not copy axle-specific assumptions. |
| `native_toolchain_settings.py`, Tkinter controller builder | Existing workstation-only choices and recheck behavior. A general build contract must preserve strict invalid overrides and use exactly the preflighted toolchain. |
| CLI, `agent_api.py`, desktop protocol | Existing typed command risk policy and shared domain validation. YMT inspect/report/import/export must share one implementation and schemas across all three. |
| `assistant_client.py` and desktop Qwen | Reuse advisory prompts. Findings explanation/support summaries only; no compatibility, acceptance or patch authority. |
| Managed package contract / publication / receipts | Reuse portable paths and verified hashes. Document future Launcher ownership/repair/rollback handoff without editing Launcher. |

No YMT expansion native project has been identified in this SDK source audit.
No new third-party dependency was added or rebundled for the ped implementation.
Any future decoder/native dependency requires a separate redistribution audit.

## Next implementation tranche: read-only YMT inspector and contracts

1. Define independent evidence states: archive structure, metadata decoding,
   dependency resolution, target runtime/build compatibility and in-game
   acceptance. Static estimates, observations and unknown/unsupported values
   have separate provenance. Unavailable is `null`/unsupported, never zero.
2. Inspect loose metadata and supported package/RPF sources, including exact
   nested layers and proposed packs without installation. A selected GTA
   installation provides edition/build context, not implicit compatibility.
3. Classify ped variation, creature and other YMT metadata from decoded content.
   Preserve source identity, full member path, fingerprint, ped/model and
   male/female/shared evidence. Distinguish resolved, missing, ambiguous and
   unsupported references, competing definitions, cycles and dependency reasons.
4. Preserve the complete discovered catalog separately from the resolved graph.
   Registration/mounting evidence distinguishes present-on-disk from expected
   participation. Unknown overrides, load order and residency stay explicit.
5. Version diagnostic JSON schemas and clearly synthetic examples before a
   native ASI exists. Include SDK/report/runtime versions, executable/build/hash,
   edition, adapter/profile, session, metadata identities, observable state and
   reference ownership, queues/failures and unsupported fields. Import and
   explanation must work without runtime code.
6. Define a separate acceptance-receipt schema/import trust boundary. Logs,
   signatures, successful compilation and runtime self-reports cannot create
   trusted acceptance or support for unknown game builds.
7. Add an explicitly bounded, redacted support bundle. Exclude proprietary assets,
   executable dumps, credentials and unneeded personal/local paths. Use the same
   inspector/schema validation in desktop, CLI, typed Agent API and Qwen grounding.

These YMT operations and schemas are **not implemented by the ped migration**.
Do not expose a functional-looking YMT UI or report runtime expansion on that basis.

## Subsequent build and package work

The general native project contract must derive architecture/compiler features
and tool versions from the actual target. Refresh discovery on Recheck; invalid
explicit CMake/CTest/VS overrides fail rather than silently switching. Require
the real isolated compile/link/run/CTest probe, selected tool fingerprints,
x64 PE/export checks, hashes and reproducible build receipts. Without a real
project, ship only contracts and fixtures—not a working-looking ASI.

Package contracts must keep built, packaged, installed and session-loaded
identities separate. Keep SDK and runtime versions distinct and include exact
hashes/session evidence. Portable edition-specific settings and bounded logs
must not leak developer paths. Build/stage output may not target live GTA.
Prebuilt installation must not need CMake or a compiler. An experimental
candidate is not an accepted release; unknown builds inherit no support.

Launcher handoff: define manifest/dependency and edition checks, owned paths,
original/replacement hashes, install evidence, repair preconditions, retained
backups and exact rollback. Do not implement these in Launcher under this task.

## Native-runtime research handoff

Still required, with independent build-scoped evidence:

- Metadata registration, mounting, override order and dependency resolution
  behavior; what is requested, loading, ready, released or unobservable.
- Runtime reference ownership, residency/lifetime transitions and trustworthy
  session observations; queue/failure semantics and measurement limitations.
- Separate structural field/index limits, dependency-list limits, metadata pools
  and memory-pressure behavior. No universal maximum or global file-count to
  free-slot arithmetic is permitted.
- Adapter/profile compatibility per exact executable identity, failure behavior
  on unknown builds, and separately reviewed in-game acceptance scenarios.
- Acceptance-receipt authority and provenance distinct from ordinary logs.

Required future fixtures include same-named nested members, malformed/oversized
and unsupported metadata, ambiguous/missing/cyclic dependencies, duplicate and
competing definitions, unregistered content, unknown builds/mounting, invalid
tool overrides and mismatched CMake/CTest, failed probes, package/report
version/hash mismatches, and redaction/portability/size bounds. Existing axle
and package tests remain mandatory.

## Verification boundary

`tests/test_ped_authoring.py`, `tests/test_ped_desktop.py`,
`desktop/src/PedWorkbench.test.tsx` and `scripts/smoke_ped_desktop.py` cover this
migration. The smoke runs the real JSONL process/worker and copy/edit/rename/
clone/undo workflow in an owned temporary fixture tree with no GTA write
capabilities. Its deliberately invalid native bytes verify an honest unavailable
preview, not real-ped render quality. Browser fixture data is visibly synthetic
and cannot apply writes. A separate read-only Enhanced check decoded and visually
inspected `x64v.rpf` → `models/cdimages/componentpeds_a_f_o.rpf` →
`a_f_o_genstreet_01.ydd` and `.ytd`: diagnostic geometry and the packaged texture
contact sheet both rendered. This caught and fixed virtual `::` identities being
used as Windows decoder temporary filenames. The full exact member identity
remains in the result; only the decoder filename is a leaf. Native-dialog E2E,
broader real-ped/edition coverage and clean-machine installation remain distinct
validation work. No in-game runtime behavior was tested.
