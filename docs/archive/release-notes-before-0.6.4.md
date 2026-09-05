# Historical SDK release notes before 0.6.4

Preserved from the former release notes. These describe older versions, not the
current release or current QA evidence. Return to [current release notes](../../RELEASE_NOTES.md).

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
