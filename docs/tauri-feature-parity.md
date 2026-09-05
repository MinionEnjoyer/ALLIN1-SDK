# Tauri v2 migration feature-parity matrix

Status vocabulary:

- **complete**: implemented and verified against the existing behavior
- **partial**: a real backend path exists, but the mapped workflow is incomplete
- **experimental**: visible scaffold or unverified implementation
- **blocked**: an external prerequisite prevents meaningful progress
- **not started**: intentionally deferred to its migration phase

Tkinter source and build entrypoints have been removed following explicit approval.
The old-surface column below is a historical behavior reference, not active code.
The full 0.6.4 release requires complete React/Tauri v2 replacement and verified
Tkinter/Tcl/Tk removal in both products; a mixed-interface release is not the
milestone. Code parity and release qualification remain separate: a migrated
workflow can match Tkinter while packaged lifecycle or live acceptance is pending.
See the [mandatory release milestone](release-0.6.4.md#mandatory-064-full-release-milestone).

## 0.6.4 RC parity gate

`desktop/module-happy-paths.json` is the release-owned inventory for the React
application. It maps all 27 user-facing modules to an exact named happy-path
test, including four opt-in native workflows. Candidate qualification enables
those native tests and rejects any React run that merely exits successfully
while reporting skips. The pre-optimization no-skip baseline was 23 files and
221 tests; current counts belong to the new source-bound validation report.
See the [current release guide](release-0.6.4.md) and
[validation procedure](validation.md), not older artifact hashes, for readiness.

Specialist panels now load in separate chunks without changing their mount
lifetimes. Loading failures preserve the shell rather than auto-restarting it.
Shared Help data/search and map detection are independent of any UI toolkit;
legacy UI adapters are removed while native qualification continues.

The architecture review found four Tkinter menu utilities missing from the
previous inventory: metadata diff, metadata round-trip, DLC inventory, and
vehicle-data compilation. These now have a React Data Tools workspace, shared
Python operations, and a real-boundary happy-path test. The declared module
inventory does not prove parity for every secondary action or native dialog.
Live GTA behavior, signed update installation and clean-
machine installer lifecycle checks remain release-acceptance work; they are not
silently counted as migrated UI capability.

| Area | Current Tkinter / CLI surface | Tauri replacement and parity evidence | Phase | Status |
|---|---|---|---:|---|
| Application lifecycle | `app.py`, single-instance mutex, clean close | Rust broker, single-instance plugin, sidecar shutdown/crash event; packaged process-tree E2E pending | 2–3 | partial |
| Themes | `ui_foundation.py`, light/dark/system | React tokens, persisted light/dark/system, OS media-query sync | 2 | partial |
| Window and pane state | fitted geometry, collapsible panes | window-state plugin plus local pane/sidebar state | 2 | partial |
| Global navigation | `AddonSdkDialog.NAVIGATION` routes plus package ownership | grouped, responsive/collapsible React workspace sidebar with accessible SVG icons | 3–4 | partial |
| Keyboard shell | F1, F5, Ctrl+O, Ctrl+`, Ctrl+B, Ctrl+Tab, Alt+Left, Ctrl+1…8/I | mapped React shortcuts and deterministic focus | 3–4 | partial |
| Unsaved navigation | per-workspace close guards | shared route guard covers Quick Import, vehicle/weapon, model/material, texture, RPF, graph/program and recipe drafts/reviews plus in-flight authoring actions; direct-open requests cannot discard guarded state | 2–6 | partial |
| Native dialogs | Tk file/folder dialogs | typed Rust `select_path` with fixed filters and canonicalization | 2–3 | partial |
| Package Linker catalog | `AddonSdkCatalog` | desktop catalog and remembered selections | 3 | partial |
| Package Linker manifest graph | `AddonManifest`, `AddonLinker` | real Python `inspect_package` data with node, reference, and install-step views plus field inspection | 3 | partial |
| Package Linker package audit | `AddonPackageInspector` | real Python bounded scan summary | 3 | partial |
| Link report export | `AddonLinkReport.to_markdown` | constrained native Markdown destination plus the existing Python `link` writer; failing review reports are explicitly acknowledged | 3 | partial |
| Product workspace API contracts | `ProductWorkspaceInspector` | product-workspace nodes, references, install steps, and diagnostics through the shared linker views | 3–4 | partial |
| Package receipts | receipt/ownership CLI and UI actions | typed GTA selection, receipt inventory, live ownership checks, immutable evidence, digest-bound install/update/uninstall/enable/disable review, separate action-time confirmation, least-privilege native package authority, closed-game enforcement, synchronous transactional execution, audit, and rollback/postcondition results | 4 | partial |
| Asset Viewer inventory | `AssetViewerDialog` | real `workbench_entries` catalog with category/path filters, shared package intake, and compact three-pane React view | 4 | partial |
| Data Tools | metadata diff/round-trip, DLC inventory, vehicle-data compilation menu actions | React workspace delegates to existing Python services; reviewed new-output reports and actual JSONL happy-path evidence; XML/Lua code editing adds syntax checks, reviewed saves/copies, backups and stale-file/dirty guards ([scope](code-editor.md)) | 5 | partial |
| Asset previews | text/image/native previews | typed cancellable `preview_asset`; Python-owned containment/decoding, bounded text and binary headers, native inspection, hash-bound normalized PNG cache artifacts, no base64 | 4–5 | partial |
| Content Workbench shell | `WorkbenchFrame` tabs | React Vehicles/Weapons/Peds/Maps/Story Runtime/Render Studio navigation with active typed workflows | 3–5 | partial |
| Vehicle Workbench | `VehicleWorkbenchFrame` | real Python project inspection, aligned model/asset/evidence panes, conservative same-directory/same-stem YBN ownership, exact linked-asset preview, copied-workspace creation/opening, complete core identity/handling fields, structured color/livery presets, tuning-kit metadata and entry collections, linked-model inventory/findings, light-profile scalar editing, light/siren references, skeleton-backed axle detection/validation with signed steering and physical order, a selectable top-down axle schematic, transmission type and per-gear ALLIN1 profiles with stock gear-count synchronization, revisioned GBAY/traffic distribution settings, digest-bound validated managed-package output, and a native-model viewport; transactional model/handling identity migration with exact linked asset renames, review and undo; runtime compilation/preflight is available in Story Runtime. Live runtime activation remains a separate acceptance task | 5 | partial |
| Vehicle viewport | orbit/pan/zoom/render pipeline | React-owned orbit, pan, zoom, keyboard, LOD/component/surface isolation, shaded/bounded-textured/material-ID/UV-coverage/wireframe controls, an independent same-camera YBN collision overlay, a flattened texture-grouped UV0 island atlas, and aligned collapsible topology/collision/sampler/texture/Vector4 evidence backed by persistent digest-bound Python model/YTD/YBN/atlas caches and hash-named native render/contact-sheet artifacts; Python samples real linked YTD pixels through decoded UV0, spatially classifies resolved/UV-only/degenerate/missing triangles, connects bounded same-tile topology by shared mesh edges, reports cross-tile seams without folding them into false polygons, composites exact YBN triangles and labelled diagnostic box hulls while keeping other primitive types count-only, preserves bounded CodeWalker Vector/Array rows without scalar-shape guesses, and retains explicit diagnostic-fidelity metadata | 5 | partial |
| Weapon Workbench | `WeaponWorkbenchFrame` | unpacked-folder inspection, copied workspace create/open, existing weapon and linked-ammo fields, nominal RPM editing backed by the existing TimeBetweenShots interval, 42 existing first-person camera axes/FOV fields, full weapon behavior-flags list, measured-anchor scope calibration with optional FOV proposal, component model/localization/bone editing, shared-definition acknowledgement, exact attachment-default editing with conflict checks, content/revision-bound review and confirmation, complete weapon-bundle cloning from an existing template (explicit identities, clone/reuse ammo, donor completeness, collision/evidence/addition review), source-aware GTA shop pricing/availability/text-key editing (separate from GBAY catalogs), complete per-set animation-mapping cloning for unmapped targets with existing-map protection, exact undo with donor/target/shop-source reselection, aligned inventory/metadata/evidence panes, and dirty-navigation/stale-job guards through existing Python transactions; lazy native body/component previews with exact model selection, declared/shared YTD links, explicit edition choice, orbit/LOD/material/UV controls, missing-asset evidence, saved-snapshot isolation, and retry support. Package publication is available through Quick Import and Package Recipes; calibration remains an estimate and live-game sight alignment is an acceptance task | 5 | partial |
| Ped Workbench | `PedWorkbenchFrame` | folder/supported-archive inspection, full bounded definition catalog with duplicate retention and exact-record selection, search/shortcuts, aligned collapsible panes, copied workspace create/open, seven existing XML fields, content/revision-bound review and confirmation, exact identity/asset migration, complete metadata cloning with required target-asset evidence, verified undo with restored selection, integration findings, exact inline asset inspection and two-up native diagnostic model/texture previews. Real JSONL and frozen-sidecar authoring smoke passed; Enhanced nested-RPF ped drawable and texture sheet decoded and visually checked. Native-dialog/clean-machine E2E and broader edition coverage remain; YMT dependency/runtime contracts are a separate follow-on | 5 | partial |
| Map Workbench | `MapWorkbenchFrame` | React typed project/descriptor creation and editing, topology/portal/garage/slot records, source validation, installed-IPL evidence, reviewed save and native package build | 4–5 | partial |
| Quick Import | `QuickImportFrame` | typed inspection, edition-isolated canonical draft review, digest-bound confirmation, guarded Launcher-library preparation and SDK-ownership replacement checks; standalone Legacy OIV export with native output selection, author credit, cancellable review and separate confirmation; prepared-package ZIP publication with native save selection, exact member hashes, GBAY/traffic review, stale-source rejection, exclusive new output and real archive validation through the shared Python publisher. Exports do not write GTA or upload. Full native-dialog/clean-machine end-to-end parity remains pending | 4 | partial |
| Models & Materials | `ModelMaterialWorkbenchFrame`, `TextureDictionaryEditorFrame` | tabbed React model-surface and texture-dictionary workspaces; typed loose YDR/YDD/YFT inspection, aligned material/geometry/evidence panes, same-stem YTD/YBN discovery, shared native viewport, copied material-workspace create/open, reviewed shader/binding/geometry edits, exact existing Vector/Array parameter component editing, undo, native build/receipt, and two-up compiled comparison; plus copied YTD workspace create/open, aligned texture inventory/preview evidence, DDS and raster import preflight, reviewed add/replace/remove, undo, and semantic-match-gated native YTD build/receipt. Exact native RPF members can be exported to a bounded editable workspace from RPF Explorer before opening here. Clean-machine native-dialog validation remains | 5 | partial |
| Axle Configurator | `VehicleAxlesPanel` | responsive vehicle schematic, wheel-pair/runtime evidence, skeleton XML intake, authoritative axle detection, signed automatic steering geometry, intentional physical-order override, axle-role editing, and guarded Python review/apply; vehicle distribution and package output are integrated; persisted axle JSON export is represented in package output. Live runtime acceptance remains | 5 | partial |
| Story controller build | CLI/UI CMake + MSVC preflight | React preflight, exact compiler/toolchain identity, Legacy/Enhanced targets, CTest, configuration inputs, edition archives and combined archive; candidate-only receipt explicitly leaves live acceptance NOT TESTED | 5–6 | partial |
| Blender render | compiled render UI/services | React renderer with full quality/camera/light controls, real native decode, cancellable headless Blender, bounded digest-bound preview cache, stale-frame indicator and separately reviewed exclusive PNG export; frozen renderer identity binds the exact sidecar | 5–6 | partial |
| RPF loose inspection | `inspect-rpf`, RpfPatcher | typed cancellable `inspect_rpf_archive` job backed by the existing recursive Python indexer; bounded results and explicit no-write evidence | 3–5 | partial |
| RPF Explorer | `RpfExplorerDialog` | aligned archive-layer, recursive-entry, and exact-member evidence panes with path/type filtering and bounded text/image/native preview reuse; exact selected GXT2 handoff into the text editor with retained archive/text tab state; guarded exact-member/native-workspace/subtree/archive extraction, metadata/logical/exact comparison, integrity reporting and copy-only defragmentation | 5 | partial |
| RPF node graph | `RpfPackageGraphFrame` | folder, RPF and package import; accessible drag/pan/zoom/fit/collapse canvas; semantic vehicle relationships and handoffs; node/source refresh, sealed expansion, preview bundle, materialize/build/origin plans and guarded save | 5 | partial |
| RPF programs | `RpfProgramFrame` | five typed templates, node configuration and connections, readiness validation, reviewed inert plans and reviewed execution to new outputs | 5 | partial |
| RPF change sets | `RpfChangeSetFrame` | create/open source-bound JSON, exact-member handoff, six file/directory action types, reviewed reorder/remove, SHA-bound confirmation, stale/cancelled-job and draft-navigation guards, aligned action/draft/evidence panels, and new-file atomic-plan export through the existing Python root/nested planner; frozen native multi-entry compilation verified. Reviewed execution and rollback are available separately for external authoring copies and explicitly selected GTA mods archives; stock archives stay blocked | 5 | partial |
| RPF writes/rollback | existing CLI safety/receipts | reviewed GXT2 copy builds plus separately confirmed multi-entry execution, retained full-archive backup/receipt, native verification and exact rollback on explicit external copies or selected GTA mods archives; native-owner capability and dual live confirmation; stale document/payload/archive, open-game, lock and space guards. Bounded retained history and reviewed metadata-only interrupted-receipt reconciliation are available; reviewed stale-lock cleanup on local Windows volumes preserves exact evidence and leaves archives/receipts unchanged; general orphan/missing-archive recovery remains; no TS writer | 5–6 | partial |
| Package Recipes/OIV | `OivWorkbenchFrame` | cancellable planning and report export plus reviewed managed-package, complete nested batch-manifest, new-RPF managed-package and XML/text/PSO inert-plan conversion; real native RPF/compile tests preserve sources and existing archives | 4–6 | partial |
| Binary workspace | `BinaryWorkspaceFrame` | loose/exact-RPF-member intake, paged hex inspection, expected-byte patching, revisioned undo, immutable original, reviewed verified copy export and diff receipt | 4–5 | partial |
| GXT2 workspace | `Gxt2WorkspaceFrame` | RPF Archives text tab with loose dictionary and exact root/nested RPF member intake, copied workspace create/open, paged hash/text search, aligned label/editor/evidence panels, exact UTF-8 add/edit/remove review, revisioned undo, and native new-file build with semantic reparse and SHA-256 report through the shared Python domain. Archive-member copies retain outer archive hash, exact member identity and edition in workspace/build provenance; reviewed creation rechecks both archive and extracted bytes. State-bound confirmation, stale/cancelled-result and dirty-navigation guards, redirected-path rejection and exclusive outputs are covered. Archive-bound edits can now produce a reviewed new RPF package folder with the original basename, root/nested member replacement, reparse and unrelated-payload verification, source/workspace rechecks, closed-game enforcement and a hash/evidence report. A separate reviewed publication flow wraps verified builds in a schema-1 ALLIN1 ZIP with package metadata, exact mods/ target, edition lock, OpenRPF dependency, whole-archive risk acknowledgement, deterministic members, portable evidence and package-loader validation. Selected outer-archive dictionaries export as checksum-preconditioned schema-3 member-only ZIPs; nested dictionaries use explicit schema-4 chains with leaf-only restore and parent-ownership checks. Both require recipient compatibility review. Multi-entry staging/plan export is available in the adjacent Change sets tab; general orphan-lock recovery, large/encrypted archive certification, new DLC registration and native-dialog E2E remain | 4 | partial |
| Texture editor | `TextureDictionaryEditorFrame` | React texture inventory and bounded preview, copied YTD workspace create/open, DDS/raster add and replace, remove warnings, state-digest review/apply, revisioned undo, and new-output native build with reparse/hash/semantic receipt; this covers the actual Tkinter add/replace/remove/undo/build surface; direct RPF intake remains | 4–5 | partial |
| SDK Console catalog | `sdk_console.command_catalog` | Agent API command catalog | 3 | partial |
| SDK Console execution | `execute_console_command` | string tokenizer to typed command/args; no shell; game writes blocked and authoring writes require action-time confirmation in both React and the protocol | 3 | partial |
| SDK Console history | `%LOCALAPPDATA%` history | bounded local WebView history with clear action | 3 | partial |
| Help Center | `HELP_TOPICS`, search | Python-supplied topic catalog and React search | 3 | partial |
| Qwen assistant | console/assistant client | confirmed SDK-owned per-user configuration independent of Launcher, passive typed configuration status, and structured grounded advisory prompts in the docked console; isolated read-only jobs, bounded WebView results, whole-worker-tree cancellation, no command execution, and no writes; richer selected-workspace grounding controls remain | 5 | partial |
| Update check | `self_update.fetch_latest_release` | real sidecar check, version comparison | 3 | partial |
| Update install/rollback | Python staging/swap helper | disabled in React until a production signing identity/public key and signed Tauri metadata are supplied; enabling checksum-only install would violate the release-hardening gate | 6 | blocked |
| CLI launch arguments | direct-open routes plus selections | Rust allowlist parser, Launcher workspace handoff including Assets/RPF/Assistant, graph and axle routes, canonical paths, single-instance forwarding; packaged route E2E still needed | 3–5 | partial |
| Agent API | JSONL v1.0 and risk policy | unchanged; desktop protocol delegates to it | 1–3 | complete |
| Desktop protocol | n/a | schema v1, typed service, sequence/risk/terminal fields | 1–2 | partial |
| Streaming jobs | ad hoc Tk threads/progress dialogs | one read-only worker, ordered channel, bounded diagnostics | 2–3 | partial |
| Cancellation | per-tool behavior | force-cancellable read-only worker; mutations gated | 2–6 | partial |
| Crash recovery | process exit/hang risk | broker EOF detection, recoverable status, explicit restart | 2–3 | partial |
| React module happy paths | n/a | versioned inventory maps all 27 declared React modules to named tests; runtime, Blender and RPF native cases enabled. No-skip baseline: 23 files / 221 tests. These mix fixture-backed UI tests and real Python/native integrations, not 221 packaged E2E tests | 2–6 | complete |
| Windows packaged E2E | PyInstaller smoke tests | local release launch and real-sidecar navigation pass; clean-VM install suite pending | 2–6 | partial |
| NSIS and portable package | PyInstaller ZIP | source-bound x64 NSIS installer and deterministic portable ZIP are generated and content/hash checked together; clean-VM install verification remains | 6 | partial |
| Signing | Authenticode release policy | sign inner binaries, Tauri executable/installer, updater signature separately | 6 | not started |
| Tkinter source retirement | historical behavior reference | 26 adapters and legacy GUI build jobs removed; aliases/deep links target Tauri; source and frozen-payload gates enforced. Packaged/native acceptance remains separate | 1–6 | complete (source only) |

### RPF packaging: exact member identity and schema-3/4 export

The native SDK and Launcher helpers now resolve files/directories from the
selected archive root, without suffix fallback. Managed-member backups and
verification use a dedicated exact-path command; older helpers fail closed.
Build-time native checks and temporary native install/restore tests cover this
boundary. `publish_rpf` now offers a separate schema-3 export containing only
the selected outer-archive GXT2 dictionary. The manifest requires the exact
original SHA-256, and schema-1/2 readers reject it before installing. Matching
SDK/Launcher services recheck originals, backups and applied caches. Scope,
target, compatibility and hashes are reviewed before a new ZIP is created.
Whole-archive export remains available with its separate overwrite warning.

This does not complete release qualification for RPF. General orphan/missing-
archive recovery, large/encrypted production archives and clean-machine dialog
coverage remain. The node graph, program editor and archive utilities are migrated.
Frozen-sidecar export and native schema-3 install/disable/enable/uninstall
passed in an owned temporary game tree, including original-checksum refusal and
unrelated nested-dictionary preservation. Native-dialog and clean-machine
validation remain separate. See [schema-3 contract and remaining scope](rpf-member-package-v3.md).

Nested dictionaries now export separately as schema 4. Explicit archive-layer
identity is retained through review, manifest and receipt. The matching native
helper stages a full copy, rebuilds detached children bottom-up, verifies all
bounded file payloads and commits the outer copy; restore merges only the selected
dictionary into the current archive. Parent/child and whole-archive ownership
conflicts are rejected. This needs a compatible Launcher build; no published
Launcher release or clean-machine certification is implied. See
[schema-4 contract and native bounds](rpf-member-package-v4.md).

### RPF change sets: staging and verified plan export

Tauri now exposes the existing Python change-set domain: create/open, source and
payload hashes, exact root/nested member targets, six file/directory operations,
reordering/removal and one native-verified atomic plan. Separate reviews and
confirmation protect each document write. Compilation re-indexes originals and
checks duplicate targets, tree conflicts and explicit execution scope, but never
executes the plan. Frozen-sidecar tests created and exported a root/nested plan
against a generated RPF while preserving its original SHA-256.

The adjacent Execute & restore tab now provides separately confirmed execution,
receipt verification and rollback on explicit external authoring copies or selected
GTA mods archives. Full backups are retained, and staging-time changes fail closed.
Bounded receipt history and metadata-only interrupted-receipt reconciliation are
available. Reviewed stale-lock cleanup now retains exact evidence and removes only
a matching exited-owner lock on local Windows volumes, without rewriting archives
or receipts. General orphan/missing-archive recovery, large/encrypted archive
validation and native-dialog/clean-machine certification remain. See the
[change-set workflow and boundaries](rpf-change-set-desktop.md).

## UI callbacks that still mix orchestration and presentation

The following modules are the priority extraction list. Their domain classes are
already reusable, but the callbacks still choose paths, run work, map errors,
and mutate widgets in one method:

| UI module | Mixed callbacks observed | Extraction target |
|---|---|---|
| `addon_sdk_ui.py` | scan/import, draft/link, RPF inspection, report export | typed package-linker service facade |
| `asset_viewer.py` | native workspace export/build and texture-editor routing | member reads/previews extracted to `asset_preview.py`; YTD catalog/edit/history extracted to `texture_workspace.py` and exposed through the typed desktop protocol; remaining advanced texture transforms stay UI-bound |
| `quick_import_ui.py` | inspect, prepare, publish, legacy OIV export | typed inspection/preparation, OIV review/apply and prepared ZIP review/apply extracted; native end-to-end parity validation remains |
| `rpf_explorer.py` | index/extract/build/transaction jobs | cancellable RPF application service |
| `vehicle_workbench.py` | authoring revision lifecycle and renders | vehicle authoring session service |
| `weapon_workbench.py` | clone plans, edits, undo, preview | weapon authoring session service |
| `ped_workbench.py` | clone plans, edits, undo, preview | ped authoring session service |
| `model_material_workbench.py` | render/export/build orchestration | model/material session service |
| `vehicle_axles_ui.py` | geometry edits, build/export/preflight | axle authoring session service |
| `sdk_console.py` | tokenization/catalog/execution/history | reuse UI-free helpers; desktop delegates execution to Agent API |
| `update_ui.py` | release fetch, stage, restart | updater application service with Rust pre-exit hook |
