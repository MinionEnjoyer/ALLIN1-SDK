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

> **Current public release:** **0.4.9**. Install it from ALLIN1 Launcher or
> download the self-contained Windows package from
> [GitHub Releases](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases).

## Support

If ALLIN1 Launcher and SDK are useful to you, project support is available through
[Buy Me a Coffee](https://buymeacoffee.com/minionenjoyer).

## Features

- **Integration Linker** — validate `addon.json`, follow references across
  weapon, ammo, animation, native-text, HUD, storefront, vehicle, handling,
  tuning, streamed-asset, archive, and rollback fields, and export an ordered
  integration plan before changing the game.
- **Package Intelligence** — inventory loose DLC folders and OIV/ZIP/RAR/7z
  packages, classify scripts, plug-ins, shaders, replacements, and add-on DLC,
  detect Legacy/Enhanced compatibility, and surface incomplete or ambiguous
  content for review.
- **OIV workbench** — preview ordered OIV operations and export a managed package
  only when every operation can be represented by an owned, reversible action.
- **Native Asset Viewer** — browse authored text and images, parse bounded RAGE
  resource headers, convert supported resources to structured CodeWalker XML,
  and generate YTD texture contact sheets without executing package code.
- **RPF Explorer** — search root and nested RPFs as one hierarchy, inspect entry
  metadata, export JSON/CSV indexes, extract an exact entry, and generate a
  checksummed replace/add/delete plan without modifying the archive. Reviewed root
  and one-level nested-entry plans targeting an exact `mods` or explicitly isolated
  workspace copy use full outer-archive staging, pre/post-write verification,
  durable receipts, guarded rollback, progress UI, transaction history, interrupted
  receipt recovery, and stale-lock inspection.
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
- ALLIN1 Launcher 0.4.9 or newer for managed install, update, repair, and removal.
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
  workspace. Its Source-style completion list narrows as you type, suggests commands,
  options, paths, and history, and runs work off the UI thread. Press Ctrl+backtick to focus or expand it,
  `Tab` to accept a completion, arrows to navigate, and `Ctrl+L` to clear output.
- **Archive / Entry** controls RPF search, metadata, preview, extraction,
  replace/add/delete planning, guarded application, canaries, transaction history,
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
```

The `catalog` response includes parameter schemas and a `read_only`,
`authoring_write`, or `game_write` risk classification. Requests never enter a
system shell and cannot evaluate Python. An append-only request record is stored
at `%LOCALAPPDATA%\ALLIN1-SDK\agent-api-audit.jsonl`.

Game/archive mutation is refused unless the user explicitly starts the API with
`--allow-game-writes`. That process-level opt-in does not bypass the selected
command's `--acknowledge-write`, closed-game check, authorized target, hashes,
locks, backup, verification, receipt, or rollback rules. Full-package lifecycle
installation remains owned by ALLIN1 Launcher; the API currently provides the
inspection, validation, authoring, and guarded RPF primitives used to build a
reviewable install workflow.

## Safety model

- Package inspection does not execute DLL, ASI, script, or shader payloads.
- RPF exploration and extraction are read-only.
- Creating an RPF replace/add/delete plan is read-only and never authorizes a write.
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
  tool. A one-level nested write extracts and changes the nested RPF inside the staged
  copy, replaces it in the staged parent, verifies the nested entry through the outer
  archive, and commits or rolls back the complete outer archive as one transaction.
- Canary mode never writes its selected source. It uses a generated external copy and
  is successful only after replace, add, delete, and exact final-hash rollback checks.
- OIV conversion stops when an operation cannot be represented safely.
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
