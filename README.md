<p align="center">
  <img src="assets/ALLIN1_SDK.png" alt="ALLIN1 SDK" width="260" />
</p>

# ALLIN1 SDK — GTA V Mod Developer Tools

The standalone developer companion to ALLIN1 Launcher for authoring, inspecting,
auditing, and safely planning GTA V add-on content. ALLIN1 SDK connects package
files to the metadata, assets, registrations, and runtime expectations needed to
make weapons, vehicles, archives, and other add-ons work coherently.

ALLIN1 SDK supports GTA V Legacy and GTA V Enhanced. Its inspection and planning
workflows are designed for Story Mode mod development.

> **Current public release:** **0.5.0**. Install it from ALLIN1 Launcher or
> download the self-contained Windows package from
> [GitHub Releases](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases).

## Support

If ALLIN1 Launcher and SDK are useful to you, project support is available through
[Buy Me a Coffee](https://buymeacoffee.com/minionenjoyer).

## Code signing policy

The project has applied for free public release signing through SignPath Foundation.
Until that application and the verified build integration are approved, release files
must be treated as unsigned and verified with their published SHA-256 manifests.

The complete [code signing policy](CODE_SIGNING_POLICY.md) identifies the release
roles, build-origin controls, privacy behavior, and exact artifacts eligible for
signing. Once approved, signed releases will carry this disclosure: **Free code
signing provided by SignPath.io, certificate by SignPath Foundation.**

## Features

- **Integration Linker** — validate `addon.json`, follow references across
  weapon, ammo, animation, native-text, HUD, storefront, vehicle, handling,
  tuning, streamed-asset, archive, and rollback fields, and export an ordered
  integration plan before changing the game.
- **Package Intelligence** — inventory loose DLC folders and OIV/ZIP/RAR/7z
  packages, classify scripts, plug-ins, shaders, replacements, and add-on DLC,
  detect Legacy/Enhanced compatibility, and surface incomplete or ambiguous
  content for review.
- **OIV workbench** — preview ordered OIV operations, export a managed package
  when every operation fits receipt ownership, and translate existing nested-RPF
  adds/replacements/deletes into payload-backed atomic batch manifests. Official
  OIV 2.2 XML add/replace/remove commands compile against an explicitly selected
  RPF into canonical-reparse-verified payloads and an inert hash-bound plan.
  Line-oriented text recipes support ordered append/insert/replace/delete with
  exact or prefix selectors that must match one line; encoding, BOM, and newline
  style are preserved and verified. Bounded newly created archives can replay
  declared adds, structured edits, and cleanup deletes before exact recursive
  verification and managed export.
- **Native Asset Viewer** — browse authored text and images, parse bounded RAGE
  resource headers, convert supported resources to structured CodeWalker XML,
  generate YTD texture contact sheets, render bounded indexed-geometry previews for
  YDR/YDD/YFT models with vertex, triangle, LOD, drawable, bounds, shader, texture,
  skin/bone-binding, skeleton, and light statistics, and
  visualize YBN collision meshes/primitives with geometry, material, and polygon counts.
  YMAP placement previews plot entities, archetypes, orientation, hierarchy links, and
  world extents in a bounded top-down scene. YNV navigation previews expose polygon
  surfaces, edge references, portals, points, flags, and bounds; YND path previews map
  vehicle/pedestrian nodes, internal and external links, junctions, street labels, and
  declared-count inconsistencies. YTYP previews turn archetype-to-asset and shared
  texture/drawable/physics/clip dictionary references into a typed dependency graph.
  The viewer can also
  export manifest-backed XML/dependency
  workspaces that rebuild only after the compiled result parses back through CodeWalker.
  The same read-only native inspection can publish a portable text/PNG/JSON bundle
  through the SDK Console or structured Agent API.
  The embedded YTD editor catalogs and previews every DDS, imports common raster formats,
  synchronizes dimensions/mips/formats, supports add/replace/remove, and retains undo history.
- **RPF Explorer** — search root and nested RPFs as one hierarchy, inspect entry
  metadata, export JSON/CSV indexes, extract an exact entry or complete directory
  subtree through one archive scan, compare two recursive archive trees by metadata
  or exact extracted-content hashes, and generate a
  checksummed file or directory-tree plan without modifying the archive. Reviewed root
  and recursively nested-entry plans targeting an exact `mods` or explicitly isolated
  workspace copy support up to eight archive levels and use full outer-archive staging,
  deepest-first parent reassembly, pre/post-write verification,
  durable receipts, guarded rollback, progress UI, transaction history, interrupted
  receipt recovery, and stale-lock inspection. Subtree exports are staged into a
  new folder and include a source and per-file SHA-256 manifest; an edited export
  can be reconciled back into one reviewed atomic workspace-sync plan. Multi-entry plans
  batch up to 1,000 root or deep changes, including directory create, empty-directory
  removal, same-parent entry rename, and exact nested-RPF deletion. They rebuild each
  nested container once and commit the outer archive once under one lock, backup,
  receipt, and rollback.
  Supported native entries can move directly from a selected root or nested RPF into
  an editable workspace, then back into a validated payload plus checksummed replacement
  plan without bypassing the normal review/apply boundary.
- **Encrypted AWC audio workspaces** — use keys from an explicitly selected matching
  Legacy or Enhanced installation to report stream names, codecs, sample rates,
  duration, loops, peaks, encryption flags, and exported WAV dependencies. A workspace
  keeps its original container immutable and does not store the GTA path. Rebuilds create
  a separate AWC, decrypt and parse it again, and are rejected unless edited and reparsed
  stream definitions have the same canonical semantic hash. Loose-file, RPF-entry,
  desktop, CLI, console, and Agent API routes share this implementation.
- **GXT2 game-text workspaces** — extract a text dictionary from an exact root or
  nested RPF entry into a searchable desktop hash/text editor, retain an immutable
  source and hash-chained undo history, add/remove/edit UTF-8 labels, and rebuild only
  after a strict semantic reparse. The rebuilt payload, validation report, outer-archive
  hash, virtual entry, and edition remain bound to an inert reviewed replacement plan.
- **Visual RPF package graphs** — import an existing loose tree or author one from an
  empty root on a dark visual node canvas. Archive, directory, and source-file cards
  use input/output ports for validated containment links; cards can be positioned,
  searched, renamed, reparented, and removed without touching referenced files. The
  same graph document is fully scriptable through the console and agent API, tracks
  every source hash, emits nested `.rpf.source` trees, and builds through the exact
  recursive archive verifier. Validation reports identify every payload as either
  byte-exact or canonical RSC7 (identical resource header plus decompressed bytes).
  File cards receive non-blocking cached thumbnails for images, native visual assets,
  text/configuration files, and deterministic type fallbacks. The same hash-bound
  renderer is available as `render-rpf-graph-previews`, producing an atomic portable
  bundle and per-preview SHA-256 report through the console or structured Agent API.
  An already-built RPF can also be recursively expanded into a retained external
  graph workspace: nested archives become editable `.rpf.source` branches, the
  untouched origin hash remains in the graph, and one import report accounts for
  every extracted payload. Imported graphs can build/diff against that origin and
  emit a normal inert multi-entry plan; changes inside a nested archive collapse to
  one reviewed parent-container replacement instead of an order-dependent deep edit.
  The graph window also includes an embedded **Build Flow** workspace: a typed visual
  operation canvas with typed artifact pins for package source, validation, loose-tree
  materialization, exact RPF build, verified defragmentation, imported-origin planning,
  and named outputs. Invalid type connections and cycles are rejected at edit time.
  Dry-run compilation binds the program JSON, package graph, source hashes, execution
  order, and every expected output without running a node. Explicit execution can only
  create new artifacts outside GTA V, removes outputs created by a failed run, and emits
  one verification report. Every node/configure/connect/disconnect/layout/plan/run action
  is available through the persistent console and structured Agent API. The Create flow
  menu provides reusable Validate, Loose tree, Verified build, Compact release, and
  Imported-origin plan scaffolds; `list-rpf-program-templates` exposes the same catalog
  to scripts and AI agents.
- **Verified RPF defragmentation** — recursively compact a new external archive copy,
  rescan both archive trees, compare preserved metadata, extract every non-container
  leaf, and require both raw and canonical logical hashes to match before publishing.
  The source is hash-checked before and after, and neither the desktop nor
  `defragment-rpf` command can place the authored copy inside a GTA V installation.
- **Resource-aware RPF comparison** — compare recursive archive trees as indexed
  metadata, byte-exact extracted payloads, or logical content. Logical mode fingerprints
  RSC7 headers plus decompressed resource bytes so compressor-level changes do not hide
  real edits or create noisy false positives; desktop, `diff-rpf --logical-content`, and
  the Agent API emit the same JSON and Markdown evidence.
- **Before/after RPF plan derivation** — select a clean base archive and a finished
  desired archive to automatically produce one reviewed, hash-bound multi-entry plan
  plus a portable folder containing only changed payloads. The planner understands deep
  nested archives, keeps newly added child RPFs as one payload, ignores harmless
  container repacking, refuses ambiguous case/type changes, and rechecks both complete
  source hashes before publishing. The desktop Archive Actions menu,
  `derive-rpf-plan`, persistent console, and Agent API share the same implementation.
- **Visual atomic RPF change sets** — stage replace, add, delete, directory create/
  remove, and same-parent rename actions in an embedded Explorer workspace. Change-set
  JSON binds the source archive and every payload by size and SHA-256, supports review
  ordering, and compiles into the existing guarded multi-entry plan without writing the
  archive. Create/inspect/stage/unstage/move/plan commands are mirrored by the SDK
  Console and Agent API; application still requires the separate receipt-owned writer.
- **Real-archive canary** — copy a genuine Legacy or Enhanced RPF outside the game,
  exercise real replace/add/delete writes, verify each exact entry, roll everything
  back, and prove the final archive SHA-256 matches the untouched source.
- **Structured META/XML tools** — compare authored metadata by element, attribute,
  and value rather than formatting, and validate parse/serialize/reparse semantic
  equivalence before packaging. Binary PSO/RBF assets remain routed through Native
  Asset Viewer instead of being misidentified as XML.
- **DLC inventory** — compare DLC folders with `dlclist.xml`, edition support,
  missing registrations, incomplete payloads, duplicates, and managed-package
  ownership.
- **Vehicle Data Compiler** — join `vehicles.meta`, `handling.meta`,
  `carvariations.meta`, `carcols.meta`, streamed models and textures, labels,
  and registrations into JSON, CSV, XLSX, Markdown, and unresolved-reference
  reports.
- **Example packages** — learn the complete integration graph from the bundled
  colored-smoke example and validate it against the same schema used by the
  linker.
- **Self-contained Windows releases** — use the GUI and RPF helper without a
  separate Python or .NET installation. Every release includes external and
  internal SHA-256 verification data for the ALLIN1 Launcher installer.
- **Agent automation API** — let local AI and developer tools discover SDK
  command schemas and submit structured JSON requests over stdio without shell
  evaluation. Game/archive writes are off by default, retain every existing
  safety check, and every execution request is audit-logged.

## How it fits together

```text
ALLIN1 Launcher
  Install / update / repair the optional SDK
  Import and manage installable mod packages
                     |
                     v
ALLIN1 SDK
  Integration Linker + Package Intelligence
  Native Asset Viewer + RPF Explorer
  OIV Workbench + DLC Inventory
  Vehicle Data Compiler
                     |
                     v
Reviewable manifests, reports, safe plans, and receipt-owned RPF transactions
```

The launcher owns player-facing setup and package lifecycle operations. The SDK
owns developer analysis and authoring workflows through its independent
`allin1_sdk` namespace, release cadence, test suite, user state, CodeWalker
submodule, and RPF helper.

## Requirements

- Windows 10 or Windows 11 for the self-contained desktop release.
- GTA V Legacy or GTA V Enhanced when inspecting installed game content.
- ALLIN1 Launcher 0.5.0 or newer for managed install, update, repair, and removal.
- Python 3.10 or newer only when running the SDK from source.
- .NET 8 SDK only when rebuilding `RpfPatcher` from source.

A GTA V installation is not required for package-only manifest linking, archive
audits, or vehicle metadata compilation.

## Windows installation

### Install through ALLIN1 Launcher

Open **SDK → Install / Manage SDK**. The launcher downloads the latest public
win-x64 release, verifies its published SHA-256 and internal checksum manifest,
then atomically installs it under `%LOCALAPPDATA%\ALLIN1\SDK`.

The same panel can open, update, repair, or uninstall the managed application.
**Install from package** accepts an already-downloaded official SDK ZIP for
offline installation and applies the same validation rules.

### Install directly

Download `ALLIN1-SDK-<version>-win-x64.zip` and its matching `.sha256` file from
[GitHub Releases](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases). Verify
the checksum, extract the archive to a fresh directory, and run
`ALLIN1-SDK.exe`.

## Desktop SDK

The desktop application is one persistent developer window. Its sidebar moves
between **Integration**, **Native Assets**, **RPF Explorer**, and **Help Center**.
Pass `--rpf-graph <graph.json>` to the desktop executable to open a validated
package graph directly; add `--gta-path <installation>` when its asset nodes need
encrypted or edition-specific native previews.
The SDK Console remains docked along the bottom and can expand over any context;
opening a tool no longer creates another independent workspace
window. Only file pickers, confirmations, and blocking transaction progress use
temporary dialogs. Dense commands stay in contextual menus instead of covering
content with large button rows:

- **Content** opens manifests, packages, folders, and installed DLC sources.
- **Review** validates links, explains fields, and exports reports.
- **Package Intelligence** opens OIV, DLC inventory, vehicle compiler, and structured
  META/XML tools.
- **The bottom SDK Console dock** keeps the complete CLI available from every
  workspace. Its completion list narrows as you type, suggests commands,
  options, paths, and history, and runs work off the UI thread. Press Ctrl+backtick to focus or expand it,
  `Tab` to accept a completion, arrows to navigate, and `Ctrl+L` to clear output.
- **Archive / Entry** controls RPF search, metadata, preview, exact-entry and
  recursive subtree extraction,
  native XML workspace export/rebuild, subtree workspace synchronization,
  replace/add/delete planning, guarded application,
  canaries, transaction history,
  receipt recovery, stale-lock review, verification, and rollback.
- **Help** provides contextual guidance for each workspace and its safety limits.

User-created projects and remembered paths are stored separately from the
application under `%LOCALAPPDATA%\ALLIN1-SDK`.

## Command line

Source installations also expose `allin1-sdk`. The packaged desktop app exposes
the same commands through the bottom **SDK Console** dock, so a separate Python terminal
is not required:

```powershell
allin1-sdk list
allin1-sdk validate sdk/examples/colored_smokes/addon.json
allin1-sdk link sdk/examples/colored_smokes/addon.json -o integration.md
allin1-sdk import-package C:\Mods\Example -o C:\Mods\Example\addon.json
allin1-sdk audit-folder C:\Mods\TestMods -o package-audit.md
allin1-sdk dlc-inventory "D:\Games\GTA V Enhanced" -o dlc-inventory.md
allin1-sdk index-rpf C:\Mods\Example\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" -o index.json
allin1-sdk catalog-rpfs "D:\Games\GTA V Enhanced\mods" --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Indexes\enhanced.sqlite
allin1-sdk search-rpf-catalog C:\Mods\Indexes\enhanced.sqlite police --suffix ytd -o C:\Mods\Indexes\police-textures.json
allin1-sdk build-rpf-tree C:\Mods\Example\dlc.rpf.source --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Build\dlc.rpf
allin1-sdk create-rpf-graph C:\Mods\Example\dlc.rpf.source --root-name dlc.rpf -o C:\Work\dlc-rpf-graph.json
allin1-sdk import-rpf-graph C:\Mods\Example\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\dlc-imported-graph
allin1-sdk add-rpf-graph-container C:\Work\dlc-rpf-graph.json root x64 --x 380 --y 100 --acknowledge-edit
allin1-sdk add-rpf-graph-file C:\Work\dlc-rpf-graph.json root C:\Art\readme.txt --name readme.txt --acknowledge-edit
allin1-sdk reparent-rpf-graph-node C:\Work\dlc-rpf-graph.json f_NODE_ID d_PARENT_ID --acknowledge-edit
allin1-sdk layout-rpf-graph C:\Work\dlc-rpf-graph.json --acknowledge-edit
allin1-sdk validate-rpf-graph C:\Work\dlc-rpf-graph.json -o C:\Work\dlc-rpf-graph-validation.json
allin1-sdk build-rpf-graph C:\Work\dlc-rpf-graph.json --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Build\dlc.rpf
allin1-sdk plan-rpf-graph-origin C:\Work\dlc-imported-graph\rpf-graph.json --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\dlc-origin-plan.json
allin1-sdk export-rpf-native-workspace C:\Mods\Example\dlc.rpf common/data/model.ydr --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\model-workspace
allin1-sdk plan-rpf-native-workspace C:\Mods\Example\dlc.rpf common/data/model.ydr C:\Work\model-workspace --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\model-plan.json
allin1-sdk inspect-native-asset C:\Audio\voice.awc --edition Enhanced --gta-path "D:\Games\GTA V Enhanced" --output-dir C:\Work\voice-report
allin1-sdk export-native-workspace C:\Audio\voice.awc --edition Enhanced --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\voice-workspace
allin1-sdk build-native-workspace C:\Work\voice-workspace --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\voice-rebuilt.awc
allin1-sdk export-rpf-binary-workspace C:\Mods\Example\dlc.rpf common/data/table.bin --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\table-binary
allin1-sdk inspect-binary-workspace C:\Work\table-binary --offset 0x100 --length 256
allin1-sdk patch-binary-workspace C:\Work\table-binary --offset 0x108 --expected-hex "01 02" --hex "03 04" --acknowledge-edit
allin1-sdk plan-rpf-binary-workspace C:\Mods\Example\dlc.rpf common/data/table.bin C:\Work\table-binary --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\table-plan.json
allin1-sdk export-rpf-gxt2-workspace C:\Mods\Example\dlc.rpf text/global.gxt2 --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\global-text
allin1-sdk list-gxt2-entries C:\Work\global-text -o C:\Work\global-text.json
allin1-sdk set-gxt2-text C:\Work\global-text 0x12345678 "Edited label" --acknowledge-edit
allin1-sdk add-gxt2-entry C:\Work\global-text 0x87654321 "New label" --acknowledge-edit
allin1-sdk undo-gxt2-edit C:\Work\global-text --acknowledge-edit
allin1-sdk plan-rpf-gxt2-workspace C:\Mods\Example\dlc.rpf text/global.gxt2 C:\Work\global-text --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\global-text-plan.json
allin1-sdk list-ytd-textures C:\Work\vehicle-ytd-workspace
allin1-sdk replace-ytd-texture C:\Work\vehicle-ytd-workspace diffuse C:\Art\diffuse.png --acknowledge-edit
allin1-sdk undo-ytd-texture-edit C:\Work\vehicle-ytd-workspace --acknowledge-edit
allin1-sdk extract-rpf-subtree C:\Mods\Example\dlc.rpf --archive-path x64\textures.rpf --directory vehicle --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Exports\vehicle
allin1-sdk plan-rpf-sync C:\Mods\Example\dlc.rpf C:\Mods\Exports\vehicle --gta-path "D:\Games\GTA V Enhanced" --workspace-root C:\Mods -o subtree-sync-plan.json
allin1-sdk diff-rpf C:\Mods\Before\dlc.rpf C:\Mods\After\dlc.rpf --exact-content --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Reports\archive-diff.json
allin1-sdk derive-rpf-plan C:\Mods\Before\dlc.rpf C:\Mods\After\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" --workspace-root C:\Mods\Before -o C:\Mods\Plans\dlc-change-plan.json
allin1-sdk verify-rpf-archive C:\Mods\Example\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" -o C:\Mods\Reports\dlc-integrity.json
allin1-sdk oiv-plan C:\Mods\Example.oiv -o oiv-plan.md --rpf-batches C:\Mods\Example-rpf-batches
allin1-sdk oiv-plan C:\Mods\NewDlc.oiv -o new-dlc-plan.md --created-rpf-package C:\Mods\NewDlc-managed --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk compile-oiv-xml C:\Mods\XmlRecipe.oiv C:\Mods\Workspace\update.rpf --gta-path "D:\Games\GTA V Enhanced" --workspace-root C:\Mods\Workspace -o C:\Mods\XmlRecipe-compiled
allin1-sdk compile-oiv-recipe C:\Mods\StructuredRecipe.oiv C:\Mods\Workspace\update.rpf --gta-path "D:\Games\GTA V Enhanced" --workspace-root C:\Mods\Workspace -o C:\Mods\StructuredRecipe-compiled
allin1-sdk plan-rpf-batch "D:\Games\GTA V Enhanced\mods\update\update.rpf" C:\Mods\Example-rpf-batches\01-update-xxxxxxxx\changes.json --gta-path "D:\Games\GTA V Enhanced" -o atomic-plan.json
allin1-sdk plan-rpf-replacement "D:\Games\GTA V Enhanced\mods\update\update.rpf" common/data/example.meta C:\Mods\example.meta --gta-path "D:\Games\GTA V Enhanced" -o replacement-plan.json
allin1-sdk plan-rpf-add "D:\Games\GTA V Enhanced\mods\update\update.rpf" common/data/new.meta C:\Mods\new.meta --gta-path "D:\Games\GTA V Enhanced" -o add-plan.json
allin1-sdk plan-rpf-delete "D:\Games\GTA V Enhanced\mods\update\update.rpf" common/data/old.meta --gta-path "D:\Games\GTA V Enhanced" -o delete-plan.json
allin1-sdk apply-rpf-plan replacement-plan.json --gta-path "D:\Games\GTA V Enhanced" --acknowledge-write
allin1-sdk verify-rpf-transaction receipt.json --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk rollback-rpf-transaction receipt.json --gta-path "D:\Games\GTA V Enhanced" --acknowledge-write
allin1-sdk canary-rpf-transaction "D:\Games\GTA V Enhanced\x64\audio\sfx\ANIMALS.rpf" --gta-path "D:\Games\GTA V Enhanced" --acknowledge-write
allin1-sdk diff-meta original.meta modified.meta -o structured-diff.md
allin1-sdk validate-meta-roundtrip handling.meta -o roundtrip.json
allin1-sdk compile-vehicle-data C:\Mods\Example -o compiled-vehicle-data
```

Run `allin1-sdk --help` or `allin1-sdk <command> --help` for the complete command
surface and options.

### AI and tool integration

`allin1-sdk agent-api` (source install) or `ALLIN1-SDK-Agent.exe` (self-contained
Windows release) exposes the same command registry as the embedded SDK Console
using newline-delimited JSON on standard input and output. It is local,
transport-neutral, and straightforward to host as a subprocess from an AI agent,
editor, build system, or custom mod manager.

```json
{"id":"hello","action":"ping"}
{"id":"commands","action":"catalog"}
{"id":"validate-1","action":"execute","command":"validate","args":["C:\\Mods\\Example\\addon.json"]}
{"id":"packages","action":"execute","command":"list-installed-packages","args":["--gta-path","D:\\Games\\GTA V Enhanced"]}
{"id":"install","action":"execute","command":"install-package","args":["C:\\Mods\\Example\\mod.toml","--gta-path","D:\\Games\\GTA V Enhanced","--acknowledge-write"]}
{"id":"remove","action":"execute","command":"uninstall-package","args":["example.mod","--gta-path","D:\\Games\\GTA V Enhanced","--acknowledge-write"]}
```

The `catalog` response includes parameter schemas and a `read_only`,
`authoring_write`, or `game_write` risk classification. Requests never enter a
system shell and cannot evaluate Python. An append-only request record is stored
at `%LOCALAPPDATA%\ALLIN1-SDK\agent-api-audit.jsonl`.

Game/archive mutation is refused unless the user explicitly starts the API with
`--allow-game-writes`. That process-level opt-in does not bypass the selected
command's `--acknowledge-write`, closed-game check, authorized target, hashes,
locks, backup, verification, receipt, or rollback rules. The API can list, install,
and uninstall validated receipt-backed packages through the same guarded lifecycle
service used by the launcher.

## Safety model

- Package inspection does not execute DLL, ASI, script, or shader payloads.
- RPF exploration and extraction are read-only. Subtree extraction scans the outer
  archive once, refuses existing output folders, verifies that the source hash did
  not change, and emits `.allin1-rpf-export.json` with every exported file hash.
- Before/after delta planning is also non-mutating. It writes a new plan and optional
  payload sidecar outside GTA V, never overwrites an artifact, hashes both recursive
  sources, and removes partial output if either archive drifts or extraction/planning
  fails. Its output remains inert until the normal guarded apply workflow is invoked.
- Global RPF catalogs atomically index up to 512 loose outer archives and all nested
  entries into a portable SQLite database outside the game. Incremental refresh
  reuses unchanged size/mtime records; `--refresh` reindexes and hashes everything.
  Failed archives remain visible in catalog statistics, while searches can filter
  entry names and virtual paths by kind or suffix. Desktop results open the exact
  outer archive and select the matching root or nested entry on double-click.
- New-archive authoring accepts only a loose source tree and a new `.rpf` output
  outside the GTA V installation. Installation remains a separate receipt-owned step.
  Directories ending in `.rpf.source` become nested archives, to eight levels. The
  builder refuses prebuilt nested RPF payloads, links, authored-name collisions,
  output overwrite, source mutation, and oversized inputs; it recursively indexes
  the completed archive, extracts and hashes every payload, and publishes only after
  its file, directory, archive, and logical-content trees match the source. Ordinary
  files remain byte-exact; recompressed RSC7 resources must retain the exact header
  and decompressed bytes. The report records both raw and canonical hashes per entry.
- RPF package graphs use one versioned JSON document for the canvas, CLI, and agent
  API. Validation requires one rooted containment tree, one parent per non-root node,
  no cycles, safe and case-unique sibling names, bounded coordinates, at most eight
  nested archives, and current size/SHA-256 values for every real source. Reparenting
  and recursive graph removal never move or delete source files. Materialization uses
  a new staged folder; graph builds recheck the graph and all sources after the normal
  exact RPF build, and discard the output if either changed during creation.
  Existing-archive import is read-only: it requires every nested RPF to be recursively
  indexed, extracts all leaf payloads in one bounded helper pass, rejects paths that
  cannot be safely materialized on Windows, and writes only to a new external
  workspace. The graph retains the origin archive path, edition, size, and SHA-256;
  rebuilding still creates a separate new archive outside the game installation.
  Origin-plan export first builds and exactly verifies that archive, compares ordinary
  files byte-exactly and RSC7 resources canonically, retains the desired archive plus
  payload evidence, and emits only the existing guarded multi-entry plan format.
  It refuses source drift, outer-archive renames, case-only paths, type changes, and
  no-op graphs; applying remains a separate receipt-owned operation.
- RPF build-flow programs use a separate versioned JSON document bound to a package
  graph. The only valid wire is an output artifact into a compatible typed input; every
  non-source node has at most one input, cycles are blocked, and incomplete configuration
  remains visible but cannot plan or run. Programs support branching external-authoring
  pipelines, preflight all primary and sidecar outputs, block path collisions and any
  output under a configured GTA root, and recheck both program and package-graph hashes
  after execution. The CLI/API surface includes reusable template discovery,
  `create-rpf-program`, inspect/add/configure/connect/disconnect/position/layout/remove
  operations, `plan-rpf-program`, and acknowledgement-gated `run-rpf-program`.
- RPF visual change sets are inert, versioned documents bound to one archive hash and
  edition. Payload actions retain absolute path, size, and SHA-256 evidence; documents
  and files are rechecked after plan compilation. They only emit the normal atomic plan,
  so game writes, closed-process enforcement, snapshots, post-write verification,
  receipts, recovery, and rollback remain centralized in the guarded transaction layer.
- Bounded OIV `createIfNotExist` containers are translated into retained loose
  `.rpf.source` workspaces and passed through that same builder. Safe top-level
  archives and new archives one level inside an existing RPF become validated,
  checksum-owned package payloads. Declared adds, XML edits, bounded line edits,
  and cleanup deletes inside those new archives replay in recipe order; edit and
  delete targets must already exist at that point unless a text recipe explicitly
  creates its file by adding the first line. XML output is reparsed and canonically
  verified. Text output retains UTF-8/UTF-16 encoding, BOM, newline style, and final
  newline state, then passes an encoding round trip; text-edited XML must still
  parse. The package retains source bindings and the ordered operation audit.
  Recipe code is never executed; ambiguous ancestry and deep existing-archive
  creation roots remain blocked. Official OIV 2.2 XML commands also support
  First/Last/Before/After adds, replacements, and removals when every XPath selects
  exactly one textual `.xml`/`.meta` entry inside one existing archive tree.
  Compilation preserves source encoding, blocks entities and unbounded XPath
  constructs, coalesces repeated edits per entry, and emits the normal guarded
  multi-entry plan without writing the archive. Wildcard text masks and PSO/META
  commands remain blocked.
- Native workspaces retain an immutable source snapshot, editable CodeWalker XML and
  dependencies, edition metadata, and hashes. Build refuses path escapes, links,
  collisions, source tampering, unsupported types, oversized inputs, and any result
  that CodeWalker cannot parse again. RPF-native planning stores the rebuilt payload
  and validation report beside the plan; it still performs no archive write.
- Binary patch workspaces bind an immutable source hash to one exact outer archive,
  nested archive path, entry, and edition. Offset edits are same-size, capped at
  64 KiB each, optionally require expected bytes, and append a hash-chained history;
  undo appends a recovery operation. Builds refuse source/editable/history tampering,
  output overwrite, size drift, unchanged results, and more than 4 MiB of auditable
  differences. The changed-range report and rebuilt payload hashes are then bound to
  a normal inert RPF replacement plan.
- GXT2 workspaces strictly validate both little-endian `2TXG` markers, the bounded
  index, unique 32-bit hashes, text offsets, UTF-8 and terminators. Mutations append a
  contiguous hash-chained snapshot history, while manual source, table, snapshot, or
  history changes are rejected. Rebuild sorts hashes, reparses the complete table, and
  binds its report to the exact source archive and virtual entry without writing it.
- YTD texture edits are limited to a validated native workspace. Dependency paths,
  names, DDS headers, dimensions, mip counts, texture formats, counts, image sizes,
  and collisions are checked before publication. Raster conversion emits an
  uncompressed DDS; supplying a DDS retains supported compression. Each mutation
  snapshots the prior XML and dependency, and undo keeps a second recovery snapshot.
- Subtree workspace synchronization revalidates that manifest, its selected archive
  and directory, the untouched source hash, every original payload hash, path
  containment, and case uniqueness. It turns edited, added, and removed loose files
  into a normal atomic multi-entry plan; plan creation is still read-only and a
  separate acknowledged apply is required.
- RPF diffing leaves both sources untouched. Metadata mode compares recursive entry
  and archive records; exact mode batch-extracts each side once into bounded temporary
  storage and hashes every payload to expose same-size content changes.
- Full archive integrity verification checks directory ancestry and bidirectional
  parent-entry/nested-archive relationships, exact-extracts and hashes every payload,
  rechecks the unchanged source hash, and reports size/compression totals, duplicate
  payload groups, helper warnings, and structural findings in portable JSON.
- Creating an RPF file or directory-tree plan is read-only and never authorizes a
  write. Batch manifests may use `add`, `replace`, `delete`, `mkdir`, `rmdir`,
  `rename` (with `new_entry`), or `upsert`; plan creation resolves each exact indexed
  target, parent dependency, and directory-removal precondition before hashing the
  reviewed plan. Directory removal is deliberately non-recursive: every child must
  be listed for deletion, and renames stay within one parent directory.
- Applying a plan is limited to the selected GTA V installation's `mods` directory
  or an external workspace explicitly authorized for that invocation. Workspace
  authorization cannot point at stock game folders. GTA V must be closed and the
  archive, original state, payload, edition, target scope, and plan identity must
  still match their reviewed hashes.
- Application copies the complete archive and payload into a transaction directory,
  modifies and verifies a same-volume staged archive, commits it, verifies it again,
  and retains a receipt-owned rollback snapshot. NG-encrypted archives retain their
  exact filename while staged because Rockstar's key selection is filename-sensitive.
  Failed post-commit checks restore the snapshot automatically.
- A per-archive exclusive lock prevents two ALLIN1 transactions from staging the same
  RPF concurrently. An interrupted lock is never guessed away; verify the associated
  receipt and archive state before removing it.
- Rollback is refused if the applied archive was subsequently changed by another
  tool. A recursively nested write extracts the bounded archive chain inside the
  staged copy, changes and verifies the deepest RPF, then verifies each child while
  reinserting it into its immediate parent. The complete outer archive is committed
  or rolled back as one transaction.
- Atomic multi-entry plans preflight every original, directory state, and payload,
  snapshot all inputs, order directory creation before file writes and explicit child
  deletion before directory removal, group changes by archive tree, update each nested
  container in one helper session, verify every result, and retain a single
  full-outer-archive rollback receipt.
- Canary mode never writes its selected source. It uses a generated external copy and
  is successful only after replace, add, delete, and exact final-hash rollback checks.
- OIV conversion stops when an operation cannot be represented safely; XML
  compilation additionally binds `assembly.xml`, the selected archive, every
  original target, and every resulting payload by SHA-256.
- Temporary archive extraction is bounded and removed after inspection.
- Edition uncertainty remains visible instead of silently selecting Legacy or
  Enhanced behavior.
- Managed SDK updates verify both the downloaded archive and every internal file
  before replacing the current installation.

## Tech stack

- **Desktop and CLI:** Python 3.10+, Tk/ttk, Click, Pillow, lxml, and openpyxl.
- **RAGE/RPF tooling:** .NET 8 and a pinned Enhanced-aware CodeWalker core.
- **Windows distribution:** PyInstaller one-directory application plus a
  self-contained `RpfPatcher` runtime.
- **Testing:** pytest with branch coverage, real-package and real-RPF canaries, release
  packaging contracts, and GitHub Actions on Windows.

## Repository layout

```text
src/allin1_sdk             Standalone GUI, CLI, linker, inspectors, and compilers
sdk                        Add-on schema and complete example packages
tools/RpfPatcher           RPF and native-resource helper source
tools/CodeWalker           Pinned Enhanced-aware CodeWalker submodule
scripts/package_release.py Reproducible Windows archive and checksum builder
tests                      SDK, package, RPF, compiler, and release-contract tests
.github/workflows          Windows CI and tagged public-release automation
runtools.ps1               Local self-contained RpfPatcher build
pyproject.toml             Python package, entry points, and test configuration
```

## Local development and testing

Create the environment and build the helper:

```powershell
git clone --recurse-submodules https://github.com/MinionEnjoyer/ALLIN1-SDK.git
cd ALLIN1-SDK
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\runtools.ps1
.\.venv\Scripts\allin1-sdk-gui.exe
```

Run the complete Python suite with coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=allin1_sdk --cov-report=term-missing
```

Tagged `v*` pushes run the same tests, build the frozen Windows application and
self-contained RPF helper, package their checksum manifests, and publish both
release assets automatically.

## Documentation

The in-app Help Center documents the Integration Linker, package intelligence,
native previews, RPF explorer, replacement-plan boundary, and recovery paths.
The schema and complete colored-smoke example are maintained under [`sdk/`](sdk/).

ALLIN1 SDK is licensed under the [GNU General Public License v3.0 or later](LICENSE).
