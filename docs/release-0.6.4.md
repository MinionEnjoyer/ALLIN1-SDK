# ALLIN1 SDK 0.6.4 — release guide

0.6.4 is a maintainer-approved **unsigned manual-download release**. SDK
publication is independent of the separate Launcher. Package support does not
mean that third-party mods or development fixtures are included in the SDK.

## Architecture and retained safeguards

React/Tauri v2 is the only desktop interface. Tkinter adapters, GUI build jobs,
Tcl/Tk and `_tkinter` are excluded; Python domain services and CLI/Agent API
compatibility remain. Source and frozen-package checks enforce this boundary.

Authoring still requires typed operations, reviewed destinations and explicit
confirmation. Path containment, ownership, stale-content rejection, retained
backups and rollback are not relaxed by the release validation decisions.

## Distribution decision

The maintainer approved publication on September 5, 2026. No publisher signing
certificate is promised or required for these unsigned manual downloads.
Publish exact checksums and build identity. SHA-256 detects changed bytes but
does not authenticate a publisher. Do not disable Windows security protections.

Automatic-update signature verification remains enforced. React update
installation is disabled until its trusted key/metadata workflow is ready.
This release does not enable unsigned automatic updates.

## Validation scope

| Check | Release policy / evidence |
| --- | --- |
| Python | Full canonical suite and unchanged 80% branch-coverage threshold |
| React | Complete workspace assertion inventory, including actual native workflows; no skipped checks |
| Native components | Rust broker tests, RPF helper checks, both-edition controller builds and optional Blender test renderer |
| Package integrity | One clean commit, build ID, shell, sidecar, embedded frontend, resources, installer and portable inventory |
| Windows lifecycle | Same-machine SDK-only fresh install, upgrade, missing-file repair, uninstall, data preservation and manual rollback |
| Desktop observations | Native startup, navigation, reviewed save, crash/restart recovery and clean shutdown exercised during preparation |
| Pristine Windows | Not required for this publication; first-time shared-dependency bootstrap remains untested |
| Live Legacy / Enhanced | Full independent final-build in-game acceptance remains unverified; no claim is inferred from exported files or rendered previews |

The four Windows tests blocked only by unavailable symbolic-link privileges
have an explicit, version-scoped maintainer waiver. They stay in the suite and
are reported as **WAIVED_NOT_PASSED**. No failed assertion, unrelated skip,
coverage failure, or runtime security check is waived.

The build accepts that specific exception only with
`-AllowWindowsSymlinkSkips`. Its identity records the exception; framework
evidence validates each exact test name and privilege-related skip reason.
Without the flag, the existing no-skipped-tests rule remains in effect.

Installer/lifecycle observations apply to the tested build IDs. The attached
release validation summary distinguishes final-byte tests from earlier native
observations; earlier dirty-source reports do not certify a new package.

## Known limits

- Not every secondary/native dialog has independent final-build acceptance.
- Authoring support does not prove a model, animation, map or runtime works in GTA.
- SDK viewport and Blender previews are not an in-game rendering certification.
- Very long installation paths are refused with guidance to move the complete
  SDK to a shorter local path. User projects need not move.
- Missing shared dependencies on a pristine Windows system were not simulated
  by uninstalling shared runtimes from the development machine.

## Build and recovery

Follow [validation](validation.md) and [desktop packaging](../desktop/README.md).
Use a clean source commit with matching Python, Cargo, frontend and Tauri
versions. Build and verify the full installer and portable distribution.
The current notes are in [RELEASE_NOTES.md](../RELEASE_NOTES.md); historical
releases remain in the archive, not repeated in the main README.

Portable files include `release.json`, `checksums.json`, `build-identity.json`,
`resource-checksums.json`, `allin1-sdk-desktop.exe` and the frozen sidecar.
Do not mix companions from different builds. Keep projects and receipts outside
the application folder, back up settings before upgrades, and retain the
previous complete installer or portable archive for manual rollback.

CI builds unsigned candidates and evidence. Publication remains an explicit
maintainer action, not an automatic side effect of tagging or a successful test.
