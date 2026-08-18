# ALLIN1 SDK

ALLIN1 SDK is the standalone developer companion to the ALLIN1 GTA V Launcher.
It focuses exclusively on add-on authoring, package analysis, native asset
inspection, and safe integration planning. It never launches GTA V or installs
the ALLIN1 gameplay client.

## Workspaces

- **Integration Linker** — validates `addon.json`, follows cross-file references,
  and explains every game-facing field.
- **Package Intelligence** — audits DLC folders, OIV/ZIP/RAR/7z packages,
  plug-in headers, edition compatibility, and installation targets.
- **Native Asset Viewer** — previews authored text/images and supported Rockstar
  resources through structured CodeWalker XML and texture contact sheets.
- **RPF Explorer** — searches root/nested archives, exposes resource metadata,
  extracts exact entries, and generates checksummed replacement plans.
- **Vehicle Data Compiler** — joins vehicles, handling, variations, tuning,
  streamed assets, and registration into reviewable reports.

RPF inspection and extraction are read-only. Replacement is deliberately
plan-only until a transactional writer with backup, verification, and rollback
guarantees is available.

## Run from source

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\runtools.ps1
.\.venv\Scripts\allin1-sdk-gui.exe
```

Command-line examples:

```powershell
allin1-sdk list
allin1-sdk validate sdk/examples/colored_smokes/addon.json
allin1-sdk import-package C:\Mods\Example -o C:\Mods\Example\addon.json
allin1-sdk index-rpf C:\Mods\Example\dlc.rpf --gta-path "D:\Games\GTA V Enhanced" -o index.json
```

## Repository relationship

This repository uses its own `allin1_sdk` Python namespace, release version,
test suite, and desktop entry point. The ALLIN1 Launcher can discover and start
the SDK, but the SDK never imports launcher code. Both products may be installed
and versioned independently.
