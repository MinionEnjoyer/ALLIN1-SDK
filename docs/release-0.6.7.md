# ALLIN1 SDK 0.6.7 — release guide

0.6.7 is a maintainer-approved **unsigned manual-download release**. SDK
publication is independent of the separate Launcher. Package support does not
mean that third-party mods or development fixtures are included in the SDK.

## Architecture and retained safeguards

React/Tauri v2 is the only desktop interface. Python domain services and
CLI/Agent API compatibility remain. The 0.6.7 interface refinement groups
related actions and evidence without changing typed operations, explicit
confirmation, path containment, ownership checks, stale-content rejection,
backups or rollback.

## Distribution decision

No publisher signing certificate is promised or required for these unsigned
manual downloads. Publish the exact checksum and build identity with the
portable archive. SHA-256 detects changed bytes but does not authenticate a
publisher. Do not disable Windows security protections.

Automatic-update signature verification remains enforced. Update installation
stays disabled until its trusted key and metadata workflow is ready. This
release does not enable unsigned automatic updates and does not promote the
candidate NSIS installer.

## Validation scope

| Check | Release policy / evidence |
| --- | --- |
| Python | Full canonical suite and unchanged 80% branch-coverage threshold |
| React | Public workspace assertion inventory; any excluded private fixture is reported as **NOT TESTED** |
| Native components | Rust broker tests, RPF helper checks and declared native candidates |
| Package integrity | One clean commit, build ID, shell, sidecar, embedded frontend, resources and portable inventory |
| Windows lifecycle | Only the exact lifecycle evidence in the candidate receipt may be claimed |
| Pristine Windows | Not required for this publication; first-time shared-dependency bootstrap remains untested |
| Live Legacy / Enhanced | Full independent final-build in-game acceptance remains unverified |

Any skipped in-scope public check remains untested and blocks public-suite
qualification. Explicitly unavailable private opt-in checks are recorded as
**NOT TESTED** and never contribute to a full-suite claim. No failed assertion,
coverage failure or runtime security check is waived.

## Known limits

- The release is a portable ZIP, not a promoted installer.
- Not every secondary/native dialog has independent final-build acceptance.
- Authoring support does not prove a model, animation, map or runtime works in GTA.
- SDK viewport and Blender previews are not an in-game rendering certification.
- Very long installation paths are refused with guidance to move the complete
  SDK to a shorter local path. User projects need not move.

## Build and recovery

Follow [validation](validation.md) and [desktop packaging](../desktop/README.md).
Use a clean source commit with matching Python, Cargo, frontend and Tauri
versions. The published ZIP must be a byte-identical alias of the qualified
portable candidate, accompanied by its checksum, build identity, candidate
validation and promotion record.

Keep the complete SDK folder together. Keep projects and receipts outside the
application folder, back up settings before upgrades, and retain the previous
portable archive for manual rollback. CI builds unsigned candidates and
evidence; publication remains an explicit maintainer action.
