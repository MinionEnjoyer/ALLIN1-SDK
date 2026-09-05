# ALLIN1 Tauri desktop

> 0.6.4 development, not release-qualified. For current scope and known limits,
> see the [release guide](../docs/release-0.6.4.md) and
> [validation procedure](../docs/validation.md). Existing local installers may
> predate current source; a checksum alone is not release approval.

0.6.4 is being prepared for unsigned manual downloads. The workflow uploads
candidate installers, complete portable packages, checksums and evidence; it
does not publish a release. See [release preparation](../RELEASE_SIGNING.md).

This directory contains the Tauri v2 + React desktop. Tkinter adapters and GUI
build jobs have been removed; Python remains the CLI/API and domain-service
backend. `allin1-sdk-gui` and CLI workbench deep links now launch only the native
desktop. Source users should reinstall the editable Python package, then set
`ALLIN1_SDK_EXECUTABLE` to a complete candidate's `allin1-sdk-desktop.exe` or put
it on PATH. A Python-only installation has no graphical fallback. Source removal
does not qualify old installers or waive the remaining release gates.

## Standalone installation

After a new candidate has passed its required gates, use
`src-tauri/target/release/bundle/nsis/ALLIN1 SDK_<version>_x64-setup.exe`
and verify its companion `.sha256`. Install for the current user and launch
**ALLIN1 SDK** from the Start menu. The Launcher and ALLIN1 game client are
optional; there is no registration or first-run dependency on either.
Game inspection still requires the relevant GTA V files, but package-only
authoring, validation, previews, and linking do not require the game.

The installer carries Python, RpfPatcher with its .NET runtime, SDK schemas,
assets, examples, documentation, and runtime authoring source. Native axle
compilation still needs the separately configured C++ toolchain. WebView2 is
handled by the NSIS installer if missing (network access may be needed).

The Qwen panel's **Standalone setup** saves SDK-only settings for a compatible
API or local llama.cpp + GGUF pair. It never downloads, starts a runtime, or
contacts a provider while saving. Existing Launcher configurations are read only
as a fallback; saving even a Disabled SDK configuration overrides that fallback.

## Development

Prerequisites are Node.js, pnpm 11.19, the stable Rust MSVC toolchain, WebView2,
Visual Studio Build Tools, .NET 8 SDK (for packaging the RPF helper), and a Python environment with this repository
installed editable.

```powershell
python -m pip install -e ".[test,release]"
pnpm --dir desktop install --frozen-lockfile
$env:ALLIN1_DESKTOP_PYTHON = (Get-Command python).Source
pnpm --dir desktop tauri dev
```

The development broker starts
`python -m allin1_sdk.desktop_sidecar_host`. `ALLIN1_DESKTOP_SIDECAR` may point
to a fixed test executable in debug builds. Neither override is honored by a
release build.

## Validation and packaging

Run the complete Windows validation and NSIS build with:

```powershell
./scripts/build_tauri_desktop.ps1 -PythonExecutable (Get-Command python).Source
```

Use `-SidecarOnly` to create and executable-smoke-test the PyInstaller sidecar
and stage its self-contained resources without Rust. Use `-SkipInstaller` to validate the sidecar, React tests, and
Rust broker without creating the NSIS installer.

For isolated debugging when qualification is blocked, run
`python -m scripts.desktop_smoke_candidate --with-shell` from the repository root.
It builds a fresh frozen service, native shell and complete diagnostic portable
ZIP in a unique `build/tauri-candidates/` folder; it does not replace development
staging or run NSIS. `--pnpm <path>` selects an explicit package manager.
Production shells must embed the frontend through `tauri/custom-protocol`.
The read-only `--verify-embedded-frontend` probe verifies the actual compiled
HTML/JavaScript/CSS inventory and build identity without opening a window.
Diagnostic results cannot satisfy full-suite, installer or live acceptance gates.

The packaged executable is a generated resource and is intentionally ignored
by Git. Release builds resolve only
`src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe`; the WebView cannot choose a
process or access shell/filesystem plugins. Resources are staged into the ignored
`src-tauri/standalone-resources` directory with a SHA-256 manifest. Rebuilds replace
only that generated directory to exclude stale files and compiled runtime output.
Freezing explicitly excludes Tk adapters, Tcl/Tk and Pillow's Tk bindings. Both
the freshly frozen service and the extracted installer payload must pass the
frozen archive/PYZ/ZIP inspection; a passing source test is not enough.
The pipeline is intended to retain the complete NSIS installer and checksum,
not a bare executable missing its sidecar and resources. Artifact upload is not
publication or release qualification; current hosted CI has not been certified
by this documentation pass.

The packaged smoke test starts from an unrelated directory with fresh user data,
no Launcher configuration, and a system-only PATH. Run it independently with:

```powershell
python scripts/smoke_desktop_sidecar.py desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe --resource-home desktop/src-tauri/standalone-resources
```

See [architecture](../docs/adr/0003-tauri-desktop-shell.md),
[protocol](../docs/desktop-protocol-v1.md), [parity](../docs/tauri-feature-parity.md),
[current validation](../docs/validation.md), and
[historical evidence](../docs/tauri-validation.md).
