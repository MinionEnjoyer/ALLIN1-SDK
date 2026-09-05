# ALLIN1 SDK 0.6.4

## What's new

- React/Tauri v2 workspaces for vehicle, weapon, ped and map authoring, native
  previews and optional Blender rendering.
- Expanded RPF inspection, editing, package building and reviewed recovery,
  alongside package recipes, Quick Import and Data Tools. Data Tools also adds
  an XML/Lua source editor with syntax diagnostics, reviewed saves, backups and
  external-change protection; Lua is never executed by the editor.
- Standalone SDK and optional assistant configuration without requiring Launcher
  or the gameplay client.
- Safer archive/manifest handling, retained recovery evidence and stronger
  build-identity checks.
- Lighter initial UI loading, preserved authoring drafts, expanded regression
  tests and reorganized documentation.
- Precise sliders with manual input, corrected workspace shortcuts, visible
  save-review confirmation, and safer recipe selection and Windows path handling.

## Download and trust

**Unsigned manual download.** Publisher code signing is not planned for 0.6.4.
No SignPath certificate or approval is promised. Windows may show an
unknown-publisher or reputation warning; do not disable security protections.

Use the official repository's release assets after publication. Verify SHA-256
checksums and the exact build identity before installing. Checksums detect
changed bytes; they do not authenticate a publisher or prove that code is safe.
Keep the complete installer or portable distribution together; do not rename or
mix its companions. Automatic-update signature verification remains enforced;
React update installation is not enabled by this unsigned release policy.

## Release status

**Maintainer-approved release `v0.6.4`.** Full automated results, build identity
and checksums accompany the downloads. Same-machine installer lifecycle and
native desktop checks were exercised during release preparation.

Four Windows symbolic-link privilege tests are explicitly waived, not passed.
Pristine-Windows dependency installation is outside this release's acceptance
scope. Full independent final-build native-dialog and Legacy/Enhanced in-game
acceptance remain unverified; authoring/export does not certify game behavior.
Very long installation paths are refused with relocation guidance. Install the
SDK in a short local folder and keep its companions together.

See the [SDK manual](docs/sdk-guide.md), [release checklist](docs/release-0.6.4.md)
and [earlier release history](docs/archive/release-notes-before-0.6.4.md).
