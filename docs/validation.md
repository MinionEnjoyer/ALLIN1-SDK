# SDK validation for 0.6.4

This is the current validation procedure. [Tauri validation history](tauri-validation.md)
and [the dated hardening audit](release-hardening-2026-09-04.md) preserve earlier
observations; neither certifies current artifacts.

## Source tests

Use an isolated Python environment with this checkout installed editable:

```powershell
python -m pytest tests --cov=allin1_sdk
pnpm --dir desktop build
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
```

Coverage remains 80%. Report required skips and warnings separately, including
privileged-link or private-fixture cases. Retired Tk widget tests are removed,
not counted as passes; domain tests and React/protocol replacements remain.
Do not lower a threshold or substitute a narrower test selection for the full gate.

React qualification requires all native workflows:

```powershell
$env:ALLIN1_SDK_TEST_PYTHON = (Get-Command python).Source
$env:ALLIN1_NATIVE_RUNTIME_TEST = '1'
$env:ALLIN1_NATIVE_RPF_TEST = '1'
$env:ALLIN1_BLENDER_EXECUTABLE = (Resolve-Path build/dependencies/blender-4.5.13-windows-x64/blender.exe).Path
pnpm --dir desktop test
```

Supply the real pinned native helper/C++/.NET toolchain as required by the test.
Missing prerequisites are not passed checks. Test fixtures create isolated
authoring/game-like directories; synthetic executables are never launched.

The adjacent Launcher developer harness can explicitly run both products and
validate the exact module/test names against fresh Vitest JSON:

```powershell
# From a separately checked-out Launcher repository with both projects installed
python tools/react_release_harness.py --product both --sdk-source ../ALLIN1-SDK
python tools/documentation_audit.py --product sdk --sdk-source ../ALLIN1-SDK
```

This is optional developer coordination, never a shipped SDK dependency. The
test report records source-before/after, tools/dependencies, timestamps and raw
evidence. Its default Python scope is targeted; use `--full-python` for coverage.
External documentation URLs are not fetched by the documentation audit.

## Candidate packaging

```powershell
./scripts/build_tauri_desktop.ps1 -PythonExecutable (Get-Command python).Source
```

Follow [desktop setup](../desktop/README.md) for prerequisites and sidecar-only
validation. The build performs source identity, resource checksums, Python,
React/native and packaged-service gates. The NSIS/portable outputs must be sealed
and compared to the tested sidecar/resources. A production frontend build alone
is not a complete SDK installer.

To debug a frozen service while release gates remain blocked, run
`python -m scripts.desktop_smoke_candidate --pnpm <pnpm-path>`. This creates an
exclusive diagnostic folder, publishes its own RPF helper, freezes a Tk-free
service and runs isolated authoring smoke tests. It never replaces release
staging, creates an installer, seals a candidate or reports release readiness.

Candidate gate schema 2 accepts only the complete canonical test/build commands
and the exact tool executables recorded during preparation. It records and
revalidates pytest XML/branch-coverage JSON, Vitest assertions and module mappings,
Cargo test binaries/results and production frontend hashes. Missing checks or
skipped tests fail even when a command exits zero. Earlier schema-1 gate receipts
must be regenerated; neither a successful log line nor a log checksum qualifies
the current candidate. Frozen CArchive/PYZ/ZIP inventories must also exclude all
Tk adapters, Tcl/Tk and Pillow Tk bindings.

## Acceptance after a new candidate

Use disposable Windows environments for clean install, upgrade, repair,
uninstall and rollback. Include missing WebView2/native dependencies, spaces,
long paths, outside-root/user-data canaries, independent no-Launcher operation,
native dialogs, dirty navigation, cancellation and sidecar crash/close/restart.
Test the exact packaged process tree and console visibility.

When no VM is available, use new disposable folders on the development machine
with isolated user-state paths, before/after inventories and outside-root canaries.
Label results **same-machine**, not clean-machine certification. Do not uninstall
WebView2 or other shared dependencies to simulate their absence. Running NSIS can
change the existing product registration and shortcuts even with a different
destination: qualify that collision risk before running it. Directory-only
portable lifecycle checks do not establish NSIS registration or fresh-machine
dependency acceptance, and a backup alone does not provide that isolation.

The final release path is explicitly unsigned manual download; no publisher
certificate is promised. Keep automatic-update verification unchanged. Follow
[manual release preparation](../RELEASE_SIGNING.md), including curated current
notes, final checksums and accurate signature disclosure.

Only with approval, run Legacy and Enhanced live acceptance with the final
binary/dependency hashes and independently anchored session identity. Keep SDK
previews and Reactor in-game rendering in their distinct acceptance suites.
Report package integrity, automated tests and live acceptance separately.

See [release guide](release-0.6.4.md), [protocol](desktop-protocol-v1.md) and
[feature-parity ledger](tauri-feature-parity.md).
