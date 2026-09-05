# ALLIN1 SDK documentation

**0.6.4 — unsigned prerelease, not release-qualified.** Start with the [release guide](release-0.6.4.md) and [SDK manual](sdk-guide.md).

Current manuals describe implemented behavior and explicit limits. Reference contracts and architecture proposals do not prove native/live acceptance. Historical evidence applies only to its named source/session. Independent mods retain separate release ownership.

## Current guides

- [Product overview and quick start](../README.md)
- [Release notes](../RELEASE_NOTES.md)
- [React/Tauri desktop setup and packaging](../desktop/README.md)
- [0.6.4 release guide and known limits](release-0.6.4.md)
- [SDK manual](sdk-guide.md)
- [Validation and acceptance](validation.md)

## Contracts and references

- [Code-signing and privacy policy](../CODE_SIGNING_POLICY.md)
- [Release-signing procedure](../RELEASE_SIGNING.md)
- [Axle prefabs](axle-prefabs.md)
- [CLI command reference](cli-reference.md)
- [Desktop protocol v1](desktop-protocol-v1.md)
- [OIV Story Mode packages](oiv-story-packages.md)
- [RPF change-set desktop workflow](rpf-change-set-desktop.md)
- [RPF member-package v3 contract](rpf-member-package-v3.md)
- [RPF member-package v4 contract](rpf-member-package-v4.md)
- [Tkinter/React feature-parity ledger](tauri-feature-parity.md)
- [Weapon fire-rate authoring](weapon-fire-rate-authoring.md)
- [Weapon scope authoring](weapon-scope-authoring.md)
- [Weapon shop and animation authoring](weapon-shop-animation-authoring.md)

## Architecture and proposals

- [ADR 0001: cross-edition axle runtime](adr/0001-cross-edition-axle-runtime.md)
- [ADR 0002: OIV Story Mode transport](adr/0002-oiv-story-transport.md)
- [ADR 0003: Tauri desktop shell](adr/0003-tauri-desktop-shell.md)
- [Ped Workbench migration and YMT handoff](ped-workbench-migration-and-ymt-handoff.md)

## Historical evidence — not current instructions

- [Earlier release notes](archive/release-notes-before-0.6.4.md)
- [SDK guide before 0.6.4](archive/sdk-guide-before-0.6.4.md)
- [September 4 release-hardening checkpoint](release-hardening-2026-09-04.md)
- [Tauri validation history](tauri-validation.md)
- [Vector optics refinement checkpoint](weapon-optics-refinement.md)

## Documentation maintenance

Runtime and example references (compiled capability is not live acceptance):

- [Runtime architecture](../runtime/VehicleWorkbenchAxles/README.md).
- [Installation policy](../runtime/VehicleWorkbenchAxles/INSTALLATION.md).
- [Supported-build gate](../runtime/VehicleWorkbenchAxles/SUPPORTED_BUILDS.md).
- [Profile contract](../runtime/VehicleWorkbenchAxles/profiles/README.md).
- [Cross-edition runtime ADR](../runtime/VehicleWorkbenchAxles/ADR-0001-cross-edition-story-runtime.md).
- [Axle prefab examples](../examples/axle-prefabs/README.md).
- [OIV bundle examples](../examples/oiv-axle-bundles/README.md).

The versioned [catalog](catalog.json) classifies every project manual in this index. Source-derived CLI references must match code. The offline audit checks local links/headings and uncategorized documents; it does not fetch external websites or certify historical claims.

The long former README is retained as a historical guide above, and version narratives are in release notes. Runtime-specific docs and examples remain beside their source; see [runtime source](../runtime/VehicleWorkbenchAxles/README.md) and [axle examples](../examples/axle-prefabs/README.md).
