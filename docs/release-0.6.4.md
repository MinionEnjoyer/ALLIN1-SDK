# ALLIN1 SDK 0.6.4 — prerelease guide

**Not release-qualified.** SDK and Launcher target 0.6.4 independently; neither
requires the other's source checkout at runtime. SDK package support does not
imply that a mod is included in the SDK distribution.

The owner explicitly approved publishing `v0.6.4-rc.1` as an unsigned prerelease
with native/live acceptance gaps disclosed. This does not mark the full-release
milestone complete. The manual-download prerelease does not replace the stable
Latest channel; its attached matrix and exact build identities describe the
tested candidate bytes.

## Mandatory 0.6.4 full-release milestone

**Both SDK and Launcher must be complete React/Tauri v2 replacements before the
full 0.6.4 release.** A mixed Tkinter/React distribution does not meet this milestone.
It is an unmet release gate, not a statement that migration is already complete.

- Replicate every retained Tkinter user workflow and secondary action in React,
  with happy-path, failure/recovery and state-preservation tests. Opening every
  module alone is insufficient.
- Switch all supported GUI entrypoints to Tauri. Remove Tkinter UI modules,
  adapters, imports and legacy GUI build jobs from active product source; remove
  Tcl/Tk and `_tkinter` from the distributed runtimes. Verify source, startup
  processes and extracted installer/portable contents. Keep shared Python domain
  services and CLI/Agent API compatibility.
- Qualify standalone SDK and Launcher builds, native dialogs, preferences,
  handoffs, cancellation, crash/close/restart and complete Windows lifecycle.
  Include missing dependencies, spaces/long paths and user-data preservation.
- Pass the full required automated gates and unchanged coverage thresholds
  (SDK 80%, Launcher 91%). Required skipped checks remain untested, not passes.
- Bind all package and approved Legacy/Enhanced acceptance evidence to the same
  reviewed source, final artifacts, dependencies and independent sessions.
- Publish only after separate approval, as explicitly unsigned manual downloads.
  Keep automatic-update signature verification enforced.

Tkinter source removal is complete following explicit approval: 26 SDK adapters
and the old GUI build jobs are removed. GUI aliases and CLI deep links target
Tauri; shared Python services remain. `scripts/tk_retirement.py` verifies source
absence/imports/entrypoints, separately from frozen-payload and live qualification.

## Distribution decision

0.6.4 targets **unsigned manual downloads**. No SignPath certificate, Azure
enrollment or certificate delivery date is a prerequisite for this release path.
Do not claim publisher-signed binaries; record actual signature status and ship
final SHA-256 checksums and build identity. Existing updater trust checks remain
enforced, and React update installation remains disabled. This decision does not
waive tests, review, packaged lifecycle or independently bound live acceptance.

## Assessment and scope

The SDK is in late functional migration. Its Python-backed authoring, review,
validation and receipts are the strongest part; React is now a substantial
working interface, not just a visual prototype. The outstanding risk is in the
secondary/native paths and release engineering, not the number of pages built.

0.6.4 includes the current vehicle/weapon/ped/map authoring work, native previews
and optional Blender rendering, standalone assistant configuration, RPF package
layout/build flow/text/binary/change-set/transaction tools, package recipes,
vehicle Quick Import/publication, receipts and Data Tools. See the [SDK manual](sdk-guide.md)
and exact [parity ledger](tauri-feature-parity.md) for limitations.

Hardening separates shared nonvisual helpers from Tkinter, validates archive and
manifest containment before writes, preserves recovery roots, binds package and
runtime identity and rejects incomplete/stale release evidence. Specialist React
workspaces are split from initial application code; loading errors never cause
automatic whole-app reloads during unresolved work.

The shared content-extension contract now matches Launcher read-only inspection:
inspecting authorization or a manifest does not publish registries, backups or
preload caches. Paired-source and filesystem-preservation regressions cover this
boundary without requiring a Launcher at SDK runtime.

## What this release does not claim

- Not every Tkinter secondary action or native dialog has been qualified.
- The planned YMT expansion runtime is not enabled by descriptor authoring.
- Optics/animation/runtime authoring does not prove live game appearance or behavior.
- SDK viewport/Blender images do not certify Reactor rendering in GTA.
- Signed React update installation remains blocked pending signing identity/metadata.
- Earlier local candidates, hashes and Tkinter test results do not qualify current source.

## Qualification matrix

| Gate | Requirement / current limit |
| --- | --- |
| React happy paths | Exact versioned 29-module inventory, including XML and Lua editors; named assertions and no required skips; native cases enabled |
| Python | Full suite at unchanged 80% coverage threshold; targeted protocol tests alone are insufficient |
| Native components | Correct pinned dependencies, helpers, Rust broker and runtime capabilities; preserve exact evidence |
| Package integrity | Fresh shell, sidecar, resources, portable ZIP and NSIS agree on one build identity |
| Clean Windows lifecycle | Install, upgrade, repair, uninstall, rollback, missing dependencies, spaces/long paths and user-data preservation still required for current candidate |
| Tkinter source removal | Complete; source and build-entrypoint guard enforced. Final packaged/process qualification remains separate |
| Live Legacy / Enhanced | Final-artifact/dependency-bound independent session acceptance; separate approval required |
| Manual publication | Reviewed source, explicit unsigned disclosure, exact checksums/build identity and separate approval; no automatic publishing |

The last pre-optimization baseline passed 221 React tests and 41 protocol/no-Tk
tests without skips. Those counts are historical checkpoint evidence, not a
current release PASS. Use the fresh test run's source digest and report for
exact counts after this documentation/optimization pass.

## Build and identity

Follow [validation](validation.md) and [desktop packaging](../desktop/README.md).
The candidate pipeline binds source commit/digest, version, build ID, locks,
toolchain, runtime/dependency identities and artifact hashes. A dirty-source test
candidate is not a reviewed release simply because it displays 0.6.4.

Portable metadata includes `release.json`, `checksums.json`, `build-identity.json`,
`resource-checksums.json`, `allin1-sdk-desktop.exe` and the frozen sidecar. Keep
their exact filenames and bytes. A compatible Launcher validates this contract;
it cannot install an arbitrary renamed shell as a complete SDK.

## Upgrade and recovery

Standalone use remains supported without Launcher/client registration. Do not
overwrite authored projects or remove receipts during an application upgrade.
Retained backup roots and hash-bound rollback reject wrong-target or modified
state. Exact installer lifecycle behavior must be tested against the new sealed
candidate, not inferred from service-level synthetic tests.

For manual publication, provision independently reviewed live evidence and its
identity/session pins. A matching log hash or valid PE header alone cannot
establish readiness. Use [unsigned release preparation](../RELEASE_SIGNING.md)
and the [distribution/signing policy](../CODE_SIGNING_POLICY.md).

## Final release-pass handoff

1. Finish outstanding secondary/native and frozen-process acceptance. Tk source
   is removed; do not label any historical legacy bundle as the new React SDK.
2. Freeze a reviewed source revision and generate fresh test/candidate evidence.
   Earlier dirty-source test reports and local installers are not transferable.
3. Verify installer and portable artifacts contain the same tested resources,
   current manual and release notes. Test the full disposable Windows lifecycle.
4. Obtain the required approved live acceptance and preserve exact identities.
5. Keep the GitHub overview to one current “What's new” section. Render only
   `RELEASE_NOTES.md` for this version; archive earlier release text separately.
6. After all gates and explicit publication approval, finalize the notes, disclose
   unsigned distribution, and publish the exact qualified assets with checksums.

Legacy Tk build/publishing jobs have been removed. Tauri CI uploads unsigned
candidates/evidence, including the complete portable ZIP; it does not publish
GitHub Releases.
