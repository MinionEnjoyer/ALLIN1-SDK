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

> **Current public release:** **0.6.3**. Install it from ALLIN1 Launcher or
> download the self-contained Windows package from
> [GitHub Releases](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases).

## Support

If ALLIN1 Launcher and SDK are useful to you, project support is available through
[Buy Me a Coffee](https://buymeacoffee.com/minionenjoyer).

## What's new in 0.6.3

- The Story controller builder now performs its complete compiler and x64
  toolchain preflight before enabling Build, exposes every detected version,
  and provides actionable setup guidance instead of failing late in CMake.
- Portable controller settings use package-specific configuration and log
  locations, include Browse controls, reject staging-path leakage, and ship a
  recipient-facing `VehicleWorkbenchAxles.Settings.exe` editor.
- The reviewed `metrobusxl2` workflow now preserves its intentional physical
  order (`lm1/rm1 -> lf/rf -> lr/rr`), inverted command polarity, middle-axle
  drive, and counter-steering through authoring, validation, and both edition
  packages.
- A guarded map-project contract, example garage project, CLI operations, Agent
  API surface, and package builder establish the first reusable map-authoring
  foundation without writing directly into a live game installation.
- VehicleWorkbenchAxles **4.5.0** distinguishes the new portable settings editor
  and hardened package contract from previously published 4.4.0 artifacts.

## What's new in 0.6.2

- The Vehicle Workbench can now build the shared native axle controller for
  GTA V Legacy, GTA V Enhanced, or both, then package each edition with its
  runtime settings and selected per-vehicle axle configurations.
- Controller paths are author-configurable, including the folder scanned for
  vehicle JSON sidecars and the destination used for axle diagnostics.
- The desktop Workbench, CLI, and Agent API share one guarded build contract,
  with toolchain checks, native tests, configuration parsing, PE/export checks,
  checksums, receipts, and protected live-game destinations.
- VehicleWorkbenchAxles **4.4.0** added edition-specific ScriptHookV hosts,
  signature-gated wheel access, physical-order and polarity support, detailed
  axle logging, and fail-closed build compatibility. Generated candidates still
  require explicit in-game acceptance before they are marked supported.

## What's new in 0.6.1

- The guided axle workflow now carries reviewed nonstandard physical wheel
  order through steering setup, validation, portable native configuration,
  reopening, and guarded Story/OIV staging without losing the authored layout.
- Story runtime exports now keep controller settings and release metadata in
  separate contracts, verify the native ScriptHook host boundary, and fail
  closed when the selected edition lacks an exact validated wheel profile.
- Desktop, CLI, and Agent API paths share the same native configuration and
  package checks, including protected live-game destinations and clearer
  capability reporting for Legacy and Enhanced targets.
- VehicleWorkbenchAxles remains at runtime version **4.1.0**; this SDK update
  improves its authoring and packaging path without changing that native
  runtime contract.

## What's new in 0.6.0

- **Built-in updates** let packaged SDK installations check GitHub Releases,
  verify the external archive checksum and complete internal file manifest,
  stage a replacement with rollback protection, and reopen the updated SDK.
- **Light, Dark, and System themes** are shared throughout the persistent SDK
  shell, workbenches, dialogs, menus, and native controls.
- **A guided axle happy path** recognizes reviewed nonstandard wheel-family
  layouts, preserves physical ordering, and offers one focused setup action
  before signed steering geometry is calculated and validated.
- Axle configurations can be loaded and saved directly from the Workbench, and
  validation findings expose readable detail without forcing another window.
- Packaged releases now include a dedicated updater host alongside the desktop,
  CLI, Agent API, and RPF tooling; the first updater-enabled SDK must still be
  installed once through ALLIN1 Launcher or from its release archive.

## What's new in 0.5.9

- **Vehicle-specific axle ordering** lets authors explicitly map an unusual
  front-to-rear bone layout without renaming bones or assuming that display
  order equals GTA's runtime wheel-slot order. Overrides remain bound to exact
  skeleton evidence and fail closed when the selected vehicle changes.
- **Configurable steering polarity** can invert the complete steering command
  once per vehicle while preserving the signed gain and physical order of every
  axle. The effective values are visible in validation and runtime diagnostics.
- **Per-axle suspension support** adds bounded relative support weights for all
  physical axle pairs. The runtime contract normalizes them against the
  vehicle's original total support and requires reversible readback, physics
  activation, rollback, and restoration capabilities before permitting writes.
- VehicleWorkbenchAxles 4.1.0 carries the schema-4 configuration, authoritative
  runtime geometry recomputation, target wheel
  mapping, capability, receipt, logging, CLI, Agent API, and desktop Workbench
  contracts. Story runtime exports remain fail-closed unless an exact build has
  an independently validated profile.
- The production suite uses generic synthetic regression fixtures; temporary
  F11 vehicle tests and private third-party vehicle assets are not distributed.

## What's new in 0.5.8

- **Direct RPF workbench input** lets authors open a `dlc.rpf` directly instead
  of wrapping it in a ZIP first. Vehicle, weapon, ped, model/material, asset,
  Quick Import, CLI, and Agent API paths share the same bounded reader.
- Large or multi-vehicle archives are indexed once and surfaced as selectable
  content, with clearer read-only states, size limits, validation errors, and a
  straightforward path from inspection to authoring.
- **Custom physical axle order** records an explicit, skeleton-bound override
  for vehicles that deliberately arrange canonical wheel families out of
  physical front-to-rear order. The SDK never infers or silently applies it.
- VehicleWorkbenchAxles 2.1.0 enforces that override contract across authoring,
  bundle selection, native parsing, validation, and restoration; older 2.0.0
  runtimes are rejected for custom layouts.
- Expanded package/RPF regression coverage verifies direct archives, nested
  assets, per-workbench input parity, safe limits, and custom-layout behavior.

## What's new in 0.5.7

- **Automatic steering geometry** derives a neutral pivot from decoded
  vehicle-local wheel-bone positions and proposes progressive signed gains for
  every explicitly steered axle. Positive values steer with the command and
  negative values counter-steer; meshes and tyre visuals never decide phase.
- Schema-2 axle configurations retain reproducible bone-position provenance,
  require a runtime that explicitly proves signed-gain support, and fail closed
  when evidence, wheel mappings, or target capabilities do not match.
- The same steering workflow is available through the Vehicle Workbench, CLI,
  typed Agent API, Story runtime bundle planner, and guarded OIV export path.
- Preset, prefab, and UI edits now preserve stronger authored runtime-version
  requirements instead of silently lowering them during geometry invalidation.
- Runtime restoration releases despawned or identity-mismatched vehicles safely
  while retaining only matching live entities whose restoration actually failed.
- The native example now carries a reproducible illustrative bone fixture and
  real provenance digest; generated native build trees are excluded from Git.

## What's new in 0.5.6

- **Axle Configurator** authors and validates variable-length vehicles with two
  through five physical axle pairs, including independent steering, drive,
  braking, fixed/tag-axle, and visual dual-tyre roles.
- Skeleton detection, canonical wheel-bone semantics, target-specific mappings,
  and game wheel-count checks prevent unsafe index assumptions.
- A data-driven prefab and visual-tyre library provides quick starting points
  for common cars, buses, trucks, cranes, and specialty configurations without
  imposing a six-wheel runtime limit.
- Axle projects can be inspected, previewed, validated, and exported through the
  desktop Workbench, console, and typed Agent API, including guarded Story Mode
  and OIV package planning.
- Release packages now include the axle catalogs, examples, OIV templates,
  schemas, documentation, and auditable native runtime source.
- Selective Story Mode axle behavior remains experimental and fail-closed until
  a compatible game-build profile has been independently validated; authoring
  and stock-compatible metadata exports remain available now.

## What's new in 0.5.5

- **Quick Import** turns a reviewed Legacy or Enhanced vehicle add-on into a
  validated ALLIN1 package with GBAY metadata, specialized storage, preview
  candidates, pricing, size tiers, and traffic disabled until explicitly chosen.
- Prepared packages can open directly in ALLIN1 Launcher's Packages workspace;
  the Launcher still shows its normal trust confirmation and owns every game write.
- A standalone Legacy OIV exporter is available from the desktop SDK, console,
  and typed Agent API for modders who do not use ALLIN1 Launcher. Export remains
  deterministic and does not modify GTA while authoring.
- Re-preparing an SDK-owned package now uses hash validation, staging, atomic
  replacement, and rollback instead of overwriting arbitrary folders.
- Vehicle catalogs now share one validated contract across the SDK, Launcher,
  GBAY runtime, official Story Mode listings, package storage, and opt-in traffic.
- Quick Import and Workbench navigation were consolidated and polished, with
  clearer review states, safer defaults, and expanded real-package regressions.

## What's new in 0.5.4

- The launcher and SDK now validate schema-1 and schema-2 packages through the
  same mirrored contract and regression fixtures.
- Weapons Workbench recognizes script-driven vanilla weapon/component systems
  without requiring replacement `weapons.meta` records.
- Packages can declare exact vanilla weapon hashes, component hashes, runtime
  entry points, and associated visual DLC progressions.
- Package-owned RPFs are inspected recursively inside the Workbench, including
  nested YDR, YTD, and YTYP assets.
- Material Progression renders decoded texture and approximate emissive/alpha
  tier strips, measures neighboring visual changes, resolves known shader
  hashes, and reports missing or non-monotonic progression evidence in both the
  desktop UI and structured JSON.
- Expanded model/material and ped authoring, assistant review hardening, CLI,
  Agent API, and desktop workspace coverage are included in this release.

## Code signing policy

The project has applied for free public release signing through SignPath Foundation.
Until that application and the verified build integration are approved, release files
must be treated as unsigned and verified with their published SHA-256 manifests.

The complete [code signing policy](CODE_SIGNING_POLICY.md) identifies the release
roles, build-origin controls, privacy behavior, and exact artifacts eligible for
signing. Once approved, signed releases will carry this disclosure: **Free code
signing provided by SignPath.io, certificate by SignPath Foundation.**

## Features

- **Package Linker** — validate `addon.json`, follow references across
  weapon, ammo, animation, native-text, HUD, storefront, vehicle, handling,
  tuning, streamed-asset, archive, and rollback fields, and export an ordered
  integration plan before changing the game.
- **Product workspaces** — open a data-only `allin1.workspace.json` for a large
  multi-package repository without treating the whole checkout as one mod. The
  SDK inventories only declared Git-tracked sources, separates hosts, runtimes,
  packages, tools, examples, and evidence into typed graph nodes, skips links
  and build artifacts, and exposes the same report through the console and API.
  Each component reports declared paths, matched and uniquely owned file/byte
  coverage, while bounded shared/unassigned evidence reveals overlap or gaps.
  Built-in content managed by the launcher is identified separately from a mod
  package that can be installed on its own. Opening or auditing a workspace
  never imports or executes any declared source file. A runtime component can
  also expose a versioned, machine-readable API contract. The same report then
  proves its public host surface and connects each consumer's API version,
  assembly, entry point, calls, capabilities, interfaces, settings, project
  reference, and authored Workbench relationships with exact source evidence.
  Semantic contract failures remain visible in the Linker instead of making the
  surrounding workspace impossible to inspect.
- **Package inspection and tools** — inventory loose DLC folders and OIV/ZIP/RAR/7z
  packages, classify scripts, plug-ins, shaders, replacements, and add-on DLC,
  detect Legacy/Enhanced compatibility, and surface incomplete or ambiguous
  content for review.
- **Package Recipes workspace** — inspect ordered OIV operations in a persistent
  desktop work area, separate blockers from supported instructions, and enable only
  outputs the SDK can prove safe. Export a managed package when every operation fits
  receipt ownership, and translate existing nested-RPF
  adds/replacements/deletes into payload-backed atomic batch manifests. Official
  OIV 2.1/2.2 XML add/replace/remove commands compile against an explicitly selected
  RPF into canonical-reparse-verified payloads and an inert hash-bound plan.
  Line-oriented text recipes support ordered append/insert/replace/delete with
  exact or prefix selectors that must match one line; encoding, BOM, and newline
  style are preserved and verified. Bounded newly created archives can replay
  declared adds, structured edits, and cleanup deletes before exact recursive
  verification and managed export. Native PSO/META recipes use the matching game
  keys, source-aware native rebuild, and a mandatory semantic reparse before an
  inert archive plan is emitted.
- **Asset Viewer** — browse authored text and images, parse bounded RAGE
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
- **Model & Materials Workbench** — open loose or packaged YDR/YDD/YFT models in
  one dedicated workspace with drawable/component hierarchy, geometry and LOD filters,
  shader assignments, typed texture samplers, related YTD/YBN/YTYP package context,
  and a responsive cached orbit viewport. Shaded, material-ID, and wireframe views use
  the same bounded native decoder, while the embedded Blender drawer compiles lit
  production renders outside the package and game. Editable copies retain an immutable
  native source, exact XML/dependencies, optimistic revisions, complete pre/post hashes,
  validation reports, and byte-exact undo. Edits may change only existing shader names,
  texture bindings, and local geometry ShaderIndex values; they cannot invent schema
  nodes. Verified builds recompile and reparse the native resource before publication.
  Desktop, console, and Agent API routes share this project and transaction model.
- **Content Workbench** — open a loose DLC folder or package archive once, then move
  between **Vehicles**, **Weapons**, and **Peds** without losing package context.
  Weapons link definitions, ammo pools, animations, shop registration, attachment
  bones, default components, component models, and streamed assets. Peds link
  `peds.meta` definitions to drawables, textures, props, movement clips, and
  expression sets. A copied Ped Author workspace safely edits existing type, props,
  clip, expression, movement, and creature-metadata fields with revision checks,
  validation, rollback, and undo while keeping identity locked during normal edits.
  A separate reviewed template builder copies a donor's complete metadata record only
  when the new model/texture/props asset family already exists. Explicit identity
  migration renames metadata and exact package-owned streamed files atomically. The
  embedded background preview keeps a diagnostic model view and actual texture sheet
  together. Shared readiness and finding panels keep missing evidence visible,
  and selected assets route into Asset Viewer inside the same application window.
  Schema-2 script-driven vanilla weapon enhancements also remain first-class even
  when they intentionally have no custom `weapons.meta`: packages may declare exact
  vanilla weapon/component hashes, controller entry points, and visual DLC assets.
  Recursive RPF inspection inventories nested YDR/YTD/YTYP resources, and the material
  progression view graphs real texture alpha, luminance, emissive scalars, resolved
  shaders, topology consistency, missing bindings, monotonicity, and neighboring-tier
  visual differences.
- **Quick Import** — use a separate guided workspace when the goal is packaging,
  not deep authoring. Its Vehicle tab detects validated Legacy/Enhanced branches,
  reviews typed GBAY listing fields for every model, keeps traffic disabled by
  default, and prepares a schema-2 package in the Launcher's per-user package
  library without writing GTA V. The Launcher remains responsible for the final
  install, backup, receipt, and rollback. Weapon and Ped tabs currently route to
  their advanced Content Workbench tools until equally complete guided importers
  are available.
  The mature Vehicles tab works with each vehicle as one resolved project instead
  of unrelated files. It links primary and high-detail YFT fragments, YTD textures, handling, variations,
  tuning kits, labels, and DLC registration evidence; unresolved relationships stay
  visible beside the selected model. Its embedded diagnostic viewport decodes each
  fragment once, then supports orbit, tilt, zoom, pan, fit, primary/high-detail fragment
  switching, LOD filtering, and individual drawable-component isolation. Each component
  exposes its shader/material and named texture references, while the linked YTD routes
  directly into the embedded Asset Viewer and texture-authoring workflow. **Build
  installable package** accepts one reviewed `dlc.rpf` or provenance-preserving
  `dlc.rpf.source`, writes the standard DLC destination and checksum, validates the
  generated `mod.toml`, and publishes atomically without touching the game. Portable
  JSON/Markdown project exports, the SDK Console, and the Agent API use the same resolver
  and guarded package builder. **Create authoring workspace** first copies every visible
  package member into a new project, preserving the download. The embedded Author tab can
  edit labels, texture links, class/type/layout/audio, common driving values, light
  settings, and tuning-kit selection. Its **Axles** work zone detects and validates
  two through five canonical axle pairs without moving or renaming bones. Steering,
  drive, braking intent, visual wheel families, and explicit target wheel indices
  remain separate. A versioned library supplies 27 behavior prefabs and 8 independent
  tyre packages; filters, generated schematics, compatibility badges, draft previews,
  validation, and undo/redo use the same authoring transaction boundary. Dual tyres
  are cosmetic geometry on an existing physical slot, never extra runtime wheels.
  Four-target planning keeps FiveM resource output, Legacy Story runtime output, and
  Enhanced conversion/profile requirements isolated. Story packages can be exported
  as verified OIV 2.2 vehicle-only, runtime-only, or explicitly confirmed
  self-contained transports around the already staged DLC. Enhanced OIV remains
  disabled pending an acceptance-tested profile and instead produces an
  OpenRPF-ready manual ZIP. ScriptHookV and third-party loaders are not redistributed.
  The dedicated **Tuning Builder** inventories
  streamed part assets and edits visible parts, linked companion parts, performance
  upgrades, category labels, arrays, booleans, and existing package-specific scalar
  fields. It can add, duplicate, remove, and reorder entries while identifying missing
  model files, duplicate identities, broken companion links, and malformed arrays.
  Each Apply snapshots every touched file, reparses
  the XML, rescans all cross-file relationships, and rolls the complete edit back if it
  introduces a broken link. Undo retains a recovery snapshot as well. Identity fields
  use a guarded migration that renames linked metadata and streamed files together.
  An edited workspace can publish only from `dlc.rpf.source`; the SDK refuses to hide
  changed loose metadata behind an unchanged prebuilt archive.
- **RPF Archives** — search root and nested RPFs as one hierarchy, inspect entry
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
- **Guarded binary workspaces** — open any non-directory RPF entry in an embedded
  hex workspace, highlight bytes changed from the immutable source, and page through
  bounded offsets without leaving RPF Archives. Every same-size patch requires the
  expected current bytes, is retained in hash-chained history, and can be recovered
  with an appended undo operation. Verified builds publish a changed-range report;
  archive-bound workspaces can create the normal inert replacement plan directly from
  the editor. Desktop, console, and Agent API routes share the same validation rules.
- **Visual RPF package graphs** — import an existing loose tree or author one from an
  empty root on a dark visual node canvas. Archive, directory, and source-file cards
  use input/output ports for validated containment links; cards can be positioned,
  searched, renamed, reparented, and removed without touching referenced files. The
  same graph document is fully scriptable through the console and agent API, tracks
  every source hash, emits nested `.rpf.source` trees, and builds through the exact
  recursive archive verifier. Validation reports identify every payload as either
  byte-exact or canonical RSC7 (identical resource header plus decompressed bytes).
  File cards receive non-blocking cached thumbnails for images, native visual assets,
  text/configuration files, and deterministic type fallbacks. **Create output >
  Export preview bundle** publishes the same hash-bound renderer as
  `render-rpf-graph-previews`, producing an atomic portable bundle and per-preview
  SHA-256 report through the desktop, console, or structured Agent API.
  Complete mod-package imports are retained as content-addressed projects and add a
  separate semantic overlay for vehicle systems. Pink vehicle cards link to their
  primary/high-detail models, texture dictionaries, handling, variations, tuning,
  registrations, text labels, edition, and install target. Typed colors, relationship
  filters, a legend, missing/mismatched/orphan findings, and direct Asset Viewer or
  Vehicle Workbench actions turn package structure into an explorable dependency map
  without changing the buildable containment tree. `analyze-package-graph`,
  `inspect-package-graph-relations`, and `open-rpf-graph --focus-node` expose the same
  evidence through the console and structured Agent API.
  An already-built RPF can also be recursively expanded into a retained external
  graph workspace: nested archives become editable `.rpf.source` branches, the
  untouched origin hash remains in the graph, and one import report accounts for
  every extracted payload. Imported graphs can build/diff against that origin and
  emit a normal inert multi-entry plan; changes inside a nested archive collapse to
  one reviewed parent-container replacement instead of an order-dependent deep edit.
  The embedded graph editor also includes a **Build Flow** workspace: a typed visual
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
- **Optional local assistant** — prompt a launcher-configured Qwen/GGUF model or
  compatible model API directly from the bottom SDK Console. Managed and custom
  local modes start a loopback-only llama.cpp server on demand and retain it for
  a short, bounded idle period for later prompts. Every question receives a focused
  host-built evidence bundle:
  repository roles and dirty state, available workspace roots, validated package
  metadata, the verified GTA path, and exact relevant SDK command contracts.
  Responses must follow a structured advisory schema. The dedicated code-review
  command discovers exact symbol definitions, ranks direct callers, state transitions,
  and nearby tests, reserves completion space before selecting context, and
  automatically chunks multi-symbol audits when one grounded request cannot fit.
  Compact schema repair can retain safe prose findings without accepting operations
  if a provider still returns malformed JSON. Unsupported operations and manual-copy
  or destructive guidance are rejected deterministically. Prompting is read-only and
  grants no install or archive authority to the model.

## Next roadmap milestone

The next Model & Materials depth pass will join resolved YTD texture previews, UV
coverage, material scalar/vector parameters, and YBN collision ownership into the same
project without guessing shared game dependencies. New edit types will follow the
current rule: preserve unknown XML, modify only proven existing fields, retain revision
and hash history, and compile/reparse outside GTA V before a result can be published.

## How it fits together

```text
ALLIN1 Launcher
  Install / update / repair the optional SDK
  Import and manage installable mod packages
                     |
                     v
ALLIN1 SDK
  Package Linker + Package Tools
  Asset Viewer + Content Workbench (Vehicles / Weapons / Peds)
  Model & Materials Workbench + verified studio rendering
  RPF Archives + Package Recipes
  OIV Workbench + DLC Inventory
  Vehicle Data Compiler
  Optional Qwen assistant (disabled by default)
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
- ALLIN1 Launcher 0.5.0 or newer is optional for managed installation, repair,
  and removal. Packaged SDK builds can check for and install their own updates.
- Python 3.10 or newer only when running the SDK from source.
- .NET 8 SDK only when rebuilding `RpfPatcher` from source.

The optional assistant is configured and installed from the ALLIN1 Launcher's
**SDK Manager → Optional assistant** tab. It is not required for any SDK feature.
Managed packs perform an x64 Windows, RAM, disk, and CPU preflight before download;
a dedicated GPU is not required.

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
`ALLIN1-SDK-Desktop.exe`.

After the first updater-enabled release is installed, use **Help → Check for
updates** inside the SDK. The SDK verifies the public release tag, archive
checksum, internal checksum manifest, product identity, and Windows entrypoints;
it stages the new build beside the current installation, closes cleanly, swaps
the directories with rollback protection, and reopens the updated SDK.

## Desktop SDK

The desktop application is one persistent developer window. Its sidebar moves
between **Package Linker**, **Asset Viewer**, **Content Workbench**, **Quick Import**,
**Models & Materials**, **RPF Archives**, **Package Recipes**, and **Help Center**.
Pass `--rpf-graph <graph.json>` to the desktop executable to open a validated
package graph directly; add `--gta-path <installation>` when its asset nodes need
encrypted or edition-specific native previews.
The SDK Console remains docked along the bottom and can expand over any context;
opening a tool no longer creates another independent workspace window. Package
graphs, build flows, visual change sets, GXT2 text editing, texture editing, and
transaction history stay in the primary application. Only file pickers,
confirmations, standalone compatibility entry points, and blocking transaction
progress use temporary dialogs. Common actions stay visible while advanced
commands are grouped by task:

- **Packages** opens manifests, packages, folders, and installed DLC sources.
- **Inspect & Export** validates links, explains fields, and exports reports.
- **Package Recipes** keeps ordered OIV inspection and every valid report, compile,
  batch, new-archive, or managed-package output in the main SDK window.
- **Package Tools** opens Package Recipes, DLC inventory, vehicle compiler, and structured
  META/XML tools.
- **Workbench** opens one package across Vehicles, Weapons, and Peds. The Weapons
  tab can turn a loose package into a separate guarded authoring workspace, edit
  existing weapon, ammo, and component fields plus an attachment's default state,
  revalidate the
  complete package after every change, and undo retained revisions without touching
  the original source. Its Integration panel retains exact animation-set and shop
  sources, can clone a complete known-good animation mapping for a missing weapon,
  and edits existing prices, labels, and single-player availability without changing
  native clip payloads or record structure. Complete weapon creation uses a guarded
  donor-bundle workflow: it copies one whole weapon record and its attachment offers,
  reuses component definitions, carries every animation-set and storefront record,
  and clones linked ammo or explicitly reuses an existing ammo pool. A read-only plan
  binds exact sources, revision, collisions, and a SHA-256 digest before the separate
  acknowledged clone can write. Unknown/raw schema, ordering, and text/value/ref
  representation stay copied and locked instead of being guessed from a partial form.
- **Quick Import** keeps fast packaging separate from those advanced workbenches.
  Prepare for Launcher creates a validated per-user package; it never installs into
  the game directly. Creators who do not use ALLIN1 Launcher can instead export a
  hash-verified **Legacy OIV package** containing only the vehicle DLC files and
  DLC-list registration. That standalone export requires an explicit author and
  intentionally excludes GBAY, traffic controls, receipts, backups, and rollback.
  The Peds tab exposes linked definitions, supporting metadata, assets, and
  readiness findings. Its guarded Author tab copies and verifies the source before
  enabling existing metadata fields, preserves unknown XML and scalar representation,
  rolls back validation regressions, and retains tamper-checked undo history. New from
  template is a two-step revision- and SHA-bound plan that preserves the donor's full
  raw record but refuses to relabel native bytes; the target asset family must already
  exist. A separate identity migration moves exact YDD/YDR/YTD/YMT members in the same
  reversible transaction as Name and PropsName. The Preview tab decodes the selected
  model and texture sheet off the UI thread. The
  Vehicles tab resolves a vehicle's linked model, textures,
  handling,
  variation, tuning, labels, and registration in one view. A copied authoring
  workspace can edit driving fields, spawn colors/liveries, selected light and
  siren profiles, tuning-kit metadata, local light definitions, and complete structured
  tuning-part collections. Candidate YFT/YTD assets and relationship findings remain
  visible beside the part builder. Transactional
  identity migration renames model/handling references and matching streamed
  files together, with validation and undo before package publication.
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

### Script-driven vanilla weapon enhancements

Schema-2 packages that enhance built-in weapons through a script do not need to
invent `weapons.meta` records. Their ALLIN1 content JSON can instead describe the
exact relationship the Weapons and Material Workbenches should inspect:

```json
{
  "workbench": {
    "weapon_enhancements": [
      {
        "id": "example.suppressor-heat",
        "name": "Suppressor heat materials",
        "mode": "scripted_vanilla_components",
        "weapon_components": [
          {
            "weapon_name": "WEAPON_CARBINERIFLE",
            "weapon_hash": "0x83BF0278",
            "component_name": "COMPONENT_AT_AR_SUPP",
            "component_hash": "0x837445AA"
          }
        ],
        "script_entry_points": ["Example.SuppressorController"],
        "visual_assets": [
          {
            "dlc_pack": "example_suppressor_heat",
            "archive": "x64/models/cdimages/example_suppressor_heat.rpf",
            "families": ["ar", "pi", "sr"],
            "levels": 24,
            "model_pattern": "example_{family}_{level:02d}.ydr",
            "base_model_pattern": "example_{family}.ydr",
            "texture_dictionary": "example_suppressor_heat.ytd",
            "texture_pattern": "example_heat_gradient_{level:02d}",
            "archetype_dictionary": "example_suppressor_heat.ytyp",
            "base_level_uses_unsuffixed": true
          }
        ]
      }
    ]
  }
}
```

The declared controller must also be present in `runtime.assemblies`. Paths are
package-relative, hashes are eight-digit hexadecimal values, and declarations
remain descriptive: opening a package never executes its scripts. Packages that
omit this optional block can still be recognized conservatively as script-driven
weapon systems, but exact vanilla component relationships will be reported as
undeclared instead of guessed.

## Command line

Source installations expose `allin1-sdk`. Official Windows packages also include a
standalone `allin1-sdk.exe` and register its managed install directory for the current
user, so a newly opened PowerShell can run the commands without Python. The desktop
app exposes the same commands through the bottom **SDK Console** dock:

```powershell
allin1-sdk assistant status
allin1-sdk assistant prompt What type of mod package is this?
allin1-sdk assistant prompt --source C:\Mods\Example\src\main.cpp --symbol InitializeMod --telemetry C:\Mods\Example\logs\latest.log Diagnose the failed initialization
allin1-sdk assistant review --repository-root C:\Code\EZ-GTA-V-R --symbols publish_rage_shadow_terminal_record,retire_rage_shadow_terminal_records_for_reset,observe_rage_shadow_terminal_execute --prioritize callers,tests,state-transitions --format structured --preserve-findings-on-schema-failure
allin1-sdk assistant stop
allin1-sdk inspect-source C:\Mods\Example\src\main.cpp --symbol InitializeMod
allin1-sdk inspect-log C:\Mods\Example\logs\latest.log --pattern error
allin1-sdk compare-telemetry C:\Mods\Example\logs\baseline.txt C:\Mods\Example\logs\current.txt
allin1-sdk list
allin1-sdk validate sdk/examples/colored_smokes/addon.json
allin1-sdk inspect-product-workspace C:\Code\ALLIN1
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
allin1-sdk inspect-model-materials C:\Mods\Example\model.yft --edition Enhanced --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk open-model-material-workbench C:\Mods\Example --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk inspect-workbench C:\Mods\Example\Package.zip --category weapons --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk create-material-workspace C:\Mods\Example\model.yft --edition Enhanced --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\model-materials
allin1-sdk inspect-material-workspace C:\Work\model-materials
allin1-sdk set-material-binding C:\Work\model-materials 0 --shader-name vehicle_paint --texture DiffuseSampler=example_d --expected-revision 0 --acknowledge-edit
allin1-sdk set-geometry-material C:\Work\model-materials 2 1 --expected-revision 1 --acknowledge-edit
allin1-sdk undo-material-edit C:\Work\model-materials --expected-revision 2 --acknowledge-edit
allin1-sdk build-material-workspace C:\Work\model-materials --gta-path "D:\Games\GTA V Enhanced" -o C:\Work\model-materials-built.yft
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
allin1-sdk inspect-vehicle-project C:\Mods\Example --model examplecar
allin1-sdk export-vehicle-project C:\Mods\Example -o C:\Work\examplecar-project
allin1-sdk create-vehicle-authoring C:\Mods\Example -o C:\Work\examplecar-authoring
allin1-sdk open-axle-configurator C:\Work\examplecar-authoring --model examplecar
allin1-sdk set-vehicle-fields C:\Work\examplecar-authoring examplecar --set handling.fMass=1600 --acknowledge-edit
allin1-sdk set-vehicle-appearance C:\Work\examplecar-authoring examplecar --colors-json C:\Work\colors.json --light-settings 18 --acknowledge-edit
allin1-sdk set-vehicle-tuning-kit C:\Work\examplecar-authoring examplecar 123_example_modkit --kit-type MKT_SPORT --acknowledge-edit
allin1-sdk inspect-vehicle-tuning C:\Work\examplecar-authoring examplecar --kit 123_example_modkit
allin1-sdk add-vehicle-tuning-entry C:\Work\examplecar-authoring examplecar 123_example_modkit visibleMods --set modelName=examplecar_spoiler_1 --set modShopLabel=EXAMPLE_SPOILER --set type=VMT_SPOILER --acknowledge-edit
allin1-sdk set-vehicle-tuning-entry C:\Work\examplecar-authoring examplecar 123_example_modkit visibleMods 0 --set bone=chassis --acknowledge-edit
allin1-sdk set-vehicle-light-profile C:\Work\examplecar-authoring examplecar 18 --set headLight.intensity=2.5 --acknowledge-edit
allin1-sdk migrate-vehicle-identity C:\Work\examplecar-authoring examplecar --new-model examplecar2 --new-handling EXAMPLECAR2 --acknowledge-edit
allin1-sdk build-vehicle-package C:\Mods\Example -o C:\Work\examplecar-package
allin1-sdk plan-managed-vehicle-package C:\Mods\Pagani.rar --edition enhanced --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk export-managed-vehicle-package C:\Mods\Pagani.rar C:\Work\pagani-enhanced --edition enhanced --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk publish-managed-vehicle-package C:\Work\pagani-enhanced C:\Releases\pagani-enhanced.zip --gta-path "D:\Games\GTA V Enhanced"
allin1-sdk inspect-vehicle-quick-import C:\Mods\Pagani.rar --gta-path "D:\Games\GTA V Enhanced" --preferred-edition enhanced
allin1-sdk prepare-vehicle-quick-import C:\Mods\Pagani.rar --edition enhanced --gta-path "D:\Games\GTA V Enhanced" --set lunga.name="Huayra Codalunga" --set lunga.manufacturer=Pagani --set lunga.price=2350000
allin1-sdk create-weapon-authoring C:\Mods\ExampleWeapon -o C:\Work\exampleweapon-authoring
allin1-sdk inspect-weapon-authoring C:\Work\exampleweapon-authoring --weapon WEAPON_EXAMPLE
allin1-sdk plan-weapon-clone C:\Work\exampleweapon-authoring WEAPON_EXAMPLE --weapon-name WEAPON_EXAMPLE2 --slot SLOT_EXAMPLE2 --ammo-info AMMO_EXAMPLE2 --model w_pi_example2 --human-name-hash WT_EXAMPLE2 --stat-name EXAMPLE2 --ammo-mode clone --ammo-name AMMO_EXAMPLE2
allin1-sdk clone-weapon-bundle C:\Work\exampleweapon-authoring WEAPON_EXAMPLE --weapon-name WEAPON_EXAMPLE2 --slot SLOT_EXAMPLE2 --ammo-info AMMO_EXAMPLE2 --model w_pi_example2 --human-name-hash WT_EXAMPLE2 --stat-name EXAMPLE2 --ammo-mode clone --ammo-name AMMO_EXAMPLE2 --expected-revision 0 --plan-sha256 <PLAN_SHA256> --acknowledge-edit
allin1-sdk set-weapon-fields C:\Work\exampleweapon-authoring WEAPON_EXAMPLE --set ammo.ammoMax=180 --expected-revision 0 --acknowledge-edit
allin1-sdk set-weapon-component C:\Work\exampleweapon-authoring COMPONENT_EXAMPLE_CLIP --set component.locDesc=WCD_EXAMPLE_CLIP --expected-revision 1 --acknowledge-edit
allin1-sdk set-weapon-attachment C:\Work\exampleweapon-authoring WEAPON_EXAMPLE COMPONENT_EXAMPLE_CLIP --set attachment.default=false --expected-revision 2 --acknowledge-edit
allin1-sdk undo-weapon-edit C:\Work\exampleweapon-authoring --expected-revision 3 --acknowledge-edit
```

Run `allin1-sdk --help` or `allin1-sdk <command> --help` for the complete command
surface and options.

### AI and tool integration

The bottom console accepts `assistant prompt <question>` without requiring quotes
around a normal multi-word question. The assistant can use a verified managed Qwen
install downloaded separately from its official upstream source, an existing
llama.cpp-compatible Windows runtime plus GGUF model, or a
compatible HTTP API. `assistant status` reports the active provider without
starting it, and `assistant stop` shuts down the local server retained by the SDK
process. A long-lived SDK Console or Agent process keeps the model warm for 120 idle
seconds, reuses compatible prompt prefixes, and caches unchanged source/log grounding.
A standalone command still releases the runtime when its process exits. The server
binds only to `127.0.0.1`; its runtime log is stored beside the assistant configuration
under `%LOCALAPPDATA%\ALLIN1\Assistant`.

`assistant context <question>` prints the exact evidence bundle without starting a
model. `--repository-root`, repeatable `--workspace-root`, `--manifest`, and
`--gta-path` can bind explicit evidence. Repeatable `--source`, `--symbol`,
`--telemetry`, and `--telemetry-pattern` options opt specific source definitions or
log sessions into the request. Explicitly named functions are brace-balanced and
preserved through their closing line. Counter reads in those definitions retrieve
bounded writer and reset evidence automatically. Telemetry patterns are aggregated
across the entire selected file (samples, first/last/min/max/sample sum, non-zero
samples, resets, observed cumulative-counter activity, and peak line) even when only
the newest matching lines fit in the excerpt.
Omitted facts remain listed as missing instead of being guessed. These readers are
bounded, reject binary and oversized inputs, and never write the selected files.

`assistant review --symbols ...` is the code-audit route. It can discover exact
definitions beneath the declared repository, retrieve direct callers, state mutation
sites, and nearby tests, and split a larger symbol set into bounded review chunks.
Discovery merges the normal source walk with a Git-reported, count- and size-bounded
inventory of untracked text sources, including files beneath normally skipped build
folders. Selected source receipts record whether each file was clean, modified,
staged, or untracked. Every chunk must retain every requested definition through its
closing line; if even a single definition cannot fit, that chunk abstains before the
model or local runtime starts.
The final response is a deterministic merge of host-validated chunk results rather
than a new ungrounded synthesis pass. It does not concatenate model summaries and
removes blocked operations or recommendations whose arguments were not grounded.
Every selected file, symbol, relationship range, worktree status, omission, chunk,
and repair attempt remains visible in the result or its receipt.

Before model startup, the host calculates a conservative input budget. It preserves
the requested completion allowance plus tokenizer headroom, the permanent safety
policy, and selected evidence identities, then prunes low-ranked command contracts,
generic declarations, repository metadata, or relationship prose when necessary.
Every omission is reported. A
request that still cannot fit returns a structured context-overflow result instead
of silently truncating a large or dirty repository.

Interactive prompts print startup, prefill, generation, and periodic heartbeat
progress to stderr. A compact UTF-8 JSON receipt is written under
`%LOCALAPPDATA%\ALLIN1\Assistant\receipts`; it records hashes, selected evidence
ranges, omissions, token/timing data, safety flags, and the structured result without
copying prompt text or full source excerpts. Receipts also identify the configured
context, reserved output, grounding/startup/primary/repair latency, repair attempts,
and relationship line ranges. Receipts are count- and size-bounded.

Prompting always retains the permanent ALLIN1 policy even when request-specific
system guidance is supplied. The model must return Summary, evidence-calibrated
Findings with separate engineering or security severity, exact Recommended operations,
advisory Proposed code changes, Missing context, and Abstentions. The host
corrects operation risk from the Agent API catalog, marks all recommendations as
not executed, requires acknowledgement for game writes, rejects invented commands,
and withholds manual-copy or destructive guidance. A proposal may describe a change
to an explicitly grounded file and symbol, but it is always marked advisory-only,
unauthorized, and unexecuted. Engineering defects cannot be labeled security-critical.
It cannot approve an install or turn a response into a write. The grouped `assistant`
command is available through the Agent API and remains classified `read_only`.

Source and telemetry recommendations are limited to the read-only
`inspect-source`, `inspect-log`, and `compare-telemetry` operations. A recommended
operation must exist in the live catalog, fit the request, include grounded
arguments and a rationale, or the host replaces it with an abstention. Every
recommendation is returned with `executed: false`. Source/log inspection commands
are removed after their evidence has already been grounded, and unrelated package,
RPF, or authoring operations are omitted from focused renderer diagnoses.

Package evidence is available through `validate-package`,
`inspect-package-receipt`, and `verify-package-ownership`. These read-only commands
let people and assistants validate payload hashes and inspect receipt ownership
before recommending the separately acknowledged `install-package` or
`uninstall-package` lifecycle.

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
{"id":"validate-package","action":"execute","command":"validate-package","args":["C:\\Mods\\Example\\mod.toml"]}
{"id":"verify-package","action":"execute","command":"verify-package-ownership","args":["example.mod","--gta-path","D:\\Games\\GTA V Enhanced"]}
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
  multi-entry plan without writing the archive. Wildcard text masks remain blocked.
  Native PSO/META commands are limited to supported native resource types in an
  existing archive: the exact resource is decoded with matching game keys, edited
  through the same bounded XPath engine, rebuilt against its immutable source, and
  required to reparse to semantically identical XML before entering the plan.
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
- OIV conversion stops when an operation cannot be represented safely; structured
  compilation additionally binds `assembly.xml`, the selected archive, every
  original target, decoded/edited PSO XML, and every resulting payload by SHA-256.
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

The in-app Help Center documents the Package Linker, Content Workbench, Quick Import,
package tools, asset previews, RPF Archives, replacement-plan boundary, and recovery paths.
The schema and complete colored-smoke example are maintained under [`sdk/`](sdk/).

ALLIN1 SDK is licensed under the [GNU General Public License v3.0 or later](LICENSE).
