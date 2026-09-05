# ALLIN1 SDK guide — 0.6.4

The SDK is an independent Story Mode authoring tool. Launcher and the ALLIN1
gameplay client are optional. Use the published 0.6.4 build and its validation
summary, not an older local installer. React/Tauri v2 is the only desktop interface;
Tkinter source and GUI build targets have been removed.

## Choose the right installation mode

For source development, follow [desktop setup](../desktop/README.md). The
`allin1-sdk-gui` command forwards to the native desktop, selected through
`ALLIN1_SDK_EXECUTABLE` or `allin1-sdk-desktop.exe` on PATH. Reinstall the editable
Python package to refresh old aliases. Python alone does not include the GUI.

The candidate pipeline produces an x64 NSIS installer and matching portable
payload. A standalone build includes its Python sidecar, self-contained RPF
helper and authoring resources; it must not import a sibling Launcher checkout.
WebView2 is required. A bare shell executable is not a complete distribution.

For any downloaded package, verify its exact product/version, archive checksum,
internal manifest and companion identity. Follow that release's installation
instructions. Do not rename files to accommodate Launcher v0.5.0's older checks.
Current Launcher code understands the Tauri portable metadata, but its clean
packaged lifecycle still needs qualification.

Package-only inspection/authoring does not require GTA. Native resource decoding
can require the matching Legacy/Enhanced files and RPF helper. Runtime compilation
requires its documented C++ toolchain; Blender studio renders need the configured
Blender executable. Missing tools must produce actionable errors, not fake output.

## The normal authoring loop

1. Open the exact source: manifest, product workspace, loose package, supported
   archive, native asset or an existing authoring workspace.
2. Inspect identity, edition, inventory and findings. Imported code is data, not
   something the SDK executes to inspect it.
3. Create/open a copied authoring workspace. Preserve the original source.
4. Edit a draft. Resolve validation failures and shared-record warnings.
5. Review the exact inputs and destination, then confirm the intended action.
6. Inspect the receipt/reparse/semantic verification and export a new artifact.
7. Install only as a separate explicit operation using a compatible package
   consumer. Exported plans do not execute themselves.

Source hashes, revision state and destination identity can make a review stale.
Reload and review again rather than bypassing that check. Undo/restore needs its
original evidence; do not delete history to force a change.

## Workspaces

### Package Linker and Receipts

Open `addon.json`, a product workspace, a package folder or supported bounded
archive. Inventory and linking describe sources, ownership, dependencies,
destinations and unresolved references. Product workspaces distinguish runtime,
host, content, examples, tests and documentation; documentation is not an install
candidate. Export the evidence when sharing a package issue.

Receipts inspect installed ownership and recovery state read-only. Installation
or removal is a separate reviewed action with its own authority and checks.

### Asset Viewer and Models & Materials

Inspect bounded text/images and supported Rockstar assets, including exact RPF
members. Native previews use the matching helper/edition. Models & Materials
supports copied material/texture authoring, validation, undo/history and new
verified native output. The texture dictionary workflow supports add/replace/
remove and rebuilt output; do not assume every legacy transform or direct-intake
route has completed migration.

### Content Workbench

| Tool | Workflow and important boundary |
| --- | --- |
| Vehicles | Identity, metadata, appearance/tuning, lighting, axle geometry, transmission type/ratios, verified package build and reviewed publication |
| Weapons | Existing/donor authoring, copied metadata, flags, RPM, optics offsets, components/attachment links, clone/distribution and undo |
| Peds | Copied field authoring, source validation, reviewed revisions and undo; runtime YMT expansion is a separate capability |
| Maps | Descriptor/source inspection, topology, entrances/exits/garage slots, validation and saved package-related data |
| Story runtime | Detect toolchain, select supported profile, build and retain native test evidence; unknown game builds stay blocked |
| Render | Real decoded-model/viewport work and optional Blender image exports; not a Reactor in-game frame |

Vehicle axle geometry uses canonical skeleton evidence, not tyre visuals as an
implicit steering-sign guess. Custom physical order and signed steering require
compatible runtime/profile evidence. See [axle prefabs](axle-prefabs.md).

Weapon RPM is derived from metadata timing; see [fire-rate authoring](weapon-fire-rate-authoring.md).
Scope offsets are distinct from iron-sight offsets. [Scope authoring](weapon-scope-authoring.md)
and [optics refinement](weapon-optics-refinement.md) describe bounded geometry
proposals and authoring limitations, not a guarantee of perfect in-game sight
alignment. Animation compatibility and actual component behavior need live tests.
See [shop/animation authoring](weapon-shop-animation-authoring.md).

The planned YMT limit-expansion tool is not enabled just because Ped Workbench
edits a descriptor. See the [ped migration/handoff](ped-workbench-migration-and-ymt-handoff.md)
for the implemented surface and deferred runtime work.

### RPF Archives

Select the correct game edition when keys are required. The tabs expose archive
inspection, GXT2 text, change sets, execution/restore, binary editing, package
layout and build flow. Existing tab mount lifetimes preserve the current guard
behavior; code splitting does not authorize extra writes or reset drafts.

Work on copied authoring data and create inert plans first. Execution/restore is
separately reviewed, scoped, hash-bound and receipt-backed. A whole-archive backup
does not authorize restoring it over unrelated changes.

- [Change sets and transactions](rpf-change-set-desktop.md): staged actions,
  ordering, compilation, verification, execution and recovery limits.
- [Schema-3 exact members](rpf-member-package-v3.md): member-only publication and
  compatible recipient requirements.
- [Schema-4 nested members](rpf-member-package-v4.md): explicit archive chains and
  unrelated-payload preservation.

General orphan/missing-archive recovery, large/encrypted production certification
and native-dialog coverage are still tracked separately. Do not infer those from
a small synthetic RPF lifecycle test.

### XML and Lua editing

**Data Tools → XML & Lua editor** opens raw text XML/META and Lua source.
It includes highlighting, search/replace, undo, syntax diagnostics and reviewed
saves with backups and stale-file protection. Lua is never executed. See the
[editor guide](code-editor.md) for supported syntax, size limits and recovery.

### Quick Import, Package Recipes and Data Tools

Quick Import is the guided vehicle package path: inspect, set identity/catalog
metadata, review, prepare and explicitly publish. Traffic is not implicitly
enabled by a Launcher handoff. Standalone Legacy OIV export is a separate output.

Package Recipes inspects OIV operations as inert data. Only supported bounded
transformations can become verified output; ambiguous/wildcard/unsupported
operations remain findings. See [Story OIV packages](oiv-story-packages.md).

Data Tools exposes metadata diff, metadata round-trip, DLC inventory and vehicle
data compilation through shared Python services. Choose new report destinations
outside GTA and retain the input/result evidence.

## Console, help and assistant

The SDK Console exposes the existing CLI/Agent API without a raw shell. Read-only
commands cannot approve authoring/game writes. Authoring confirmation and runtime
authority checks still apply. Use the [CLI reference](cli-reference.md) and the
command's `--help`; a command being listed does not mean it is safe to run on a
live installation.

F1 opens contextual Help; the topics and article scroll independently. Help data
and map-detection helpers no longer import Tkinter. Specialist panels load their
code on demand. A loading failure does not automatically restart the application
or discard pending work; the shell remains available.

Qwen Assistant → Standalone setup accepts Disabled, a compatible API, or an
existing trusted local llama.cpp/GGUF pair. Saving does not download, connect or
start inference. SDK settings live at
`%LOCALAPPDATA%\ALLIN1-SDK\Assistant\config.json`; an explicit Disabled setting
overrides the optional legacy Launcher fallback. API secrets remain in named
environment variables, not the configuration file. The assistant is advisory;
it cannot execute proposed actions or approve a review.

## Updates and recovery

React can check for updates. **Signed update installation/rollback is not enabled
until production signing identity and metadata are supplied.** Do not treat the
legacy updater description as a working React update button.

Keep configuration, authored projects, receipts and backups when replacing a
development build. The guarded services reject changed targets, invalid paths
and incompatible rollback receipts. If a native writer's outcome is uncertain,
do not force a restart; collect diagnostics and inspect the receipt first.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Empty/failed preview | Source type/size, exact member, edition and helper availability; no preview is not a successful render |
| Workspace cannot load | Preserve pending work, inspect installed artifact integrity, then reopen safely; do not mix assets from different builds |
| Review expired or changed | Reload current source/state and review again |
| Build is disabled | Read toolchain/profile/source findings; unsupported runtime capabilities remain fail-closed |
| SDK opens Tkinter | An older installation/shortcut is still in use; current source has no Tk UI. Verify the executable identity and refresh the source package/shortcut |
| Installer/version mismatch | Verify the complete package identity and intended release; do not rename or mix companions |
| Assistant is unavailable | Verify Disabled/configuration state, the chosen provider/runtime and environment-variable name; other authoring remains usable |

For a report, include the exact SDK build identity, source operation, edition and
redacted diagnostics/receipt. Private assets and API keys should not be posted.
See [validation](validation.md), [release scope](release-0.6.4.md) and the
[feature-parity matrix](tauri-feature-parity.md).
