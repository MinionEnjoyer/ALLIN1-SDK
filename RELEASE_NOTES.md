# ALLIN1 SDK 0.6.4 — unsigned prerelease

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

**Prerelease `v0.6.4-rc.1`, not release-qualified.** Published by explicit
prerelease approval; final native-window, installer/lifecycle and Legacy/Enhanced
live acceptance remain untested. Privilege-dependent skipped checks are not
passes. Use the attached validation matrix and exact build identity, not an
older report. The stable release and its full acceptance gates remain pending.

See the [SDK manual](docs/sdk-guide.md), [release checklist](docs/release-0.6.4.md)
and [earlier release history](docs/archive/release-notes-before-0.6.4.md).
