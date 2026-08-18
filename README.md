# ALLIN1 SDK — GTA V Mod Developer Tools

The standalone developer companion to ALLIN1 Launcher for authoring, inspecting,
auditing, and safely planning GTA V add-on content. ALLIN1 SDK connects package
files to the metadata, assets, registrations, and runtime expectations needed to
make weapons, vehicles, archives, and other add-ons work coherently.

ALLIN1 SDK supports GTA V Legacy and GTA V Enhanced. Its inspection and planning
workflows are designed for Story Mode mod development.

> **Current public release:** **0.4.8**. Install it from ALLIN1 Launcher or
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
  checksummed replacement plan without modifying the archive.
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
Reviewable manifests, inventories, reports, and safe install plans
```

The launcher owns player-facing setup and package lifecycle operations. The SDK
owns developer analysis and authoring workflows through its independent
`allin1_sdk` namespace, release cadence, test suite, user state, CodeWalker
submodule, and RPF helper.

## Requirements

- Windows 10 or Windows 11 for the self-contained desktop release.
- GTA V Legacy or GTA V Enhanced when inspecting installed game content.
- ALLIN1 Launcher 0.4.8 or newer for managed install, update, repair, and removal.
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

The desktop application organizes developer tasks into focused workspaces and
keeps dense commands in contextual menus instead of covering content with large
button rows:

- **Content** opens manifests, packages, folders, and installed DLC sources.
- **Review** validates links, explains fields, and exports reports.
- **Package Intelligence** opens OIV, DLC inventory, and vehicle compiler tools.
- **Archive / Entry** controls RPF search, metadata, preview, extraction, and
  replacement planning.
- **Help** provides contextual guidance for each workspace and its safety limits.

User-created projects and remembered paths are stored separately from the
application under `%LOCALAPPDATA%\ALLIN1-SDK`.

## Command line

Source installations also expose `allin1-sdk`:

```powershell
allin1-sdk list
allin1-sdk validate sdk/examples/colored_smokes/addon.json
allin1-sdk link sdk/examples/colored_smokes/addon.json -o integration.md
allin1-sdk import-package C:\Mods\Example -o C:\Mods\Example\addon.json
allin1-sdk audit-folder C:\Mods\TestMods -o package-audit.md
allin1-sdk dlc-inventory "D:\Games\GTA V Enhanced" -o dlc-inventory.md
allin1-sdk index-rpf C:\Mods\Example\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" -o index.json
allin1-sdk compile-vehicle-data C:\Mods\Example -o compiled-vehicle-data
```

Run `allin1-sdk --help` or `allin1-sdk <command> --help` for the complete command
surface and options.

## Safety model

- Package inspection does not execute DLL, ASI, script, or shader payloads.
- RPF exploration and extraction are read-only.
- RPF replacement remains plan-only until a transactional writer can guarantee
  backup, verification, rollback, and ownership boundaries.
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
- **Testing:** pytest with branch coverage, real-package canaries, release
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
