# ADR 0002: OIV is a final Story Mode transport

Status: Legacy transport implementation enabled with installer/in-game acceptance
still unclaimed; Enhanced OIV disabled pending a real installer/Gen9/runtime
acceptance test.

## Decision

The OIV exporter consumes files from a completed Story Mode staging directory.
It never rebuilds or converts vehicle assets. Three plans are available:
vehicle-only (recommended), generic axle-runtime-only, and explicitly confirmed
self-contained. All use one shared `VehicleWorkbenchAxles.asi`; vehicle packages
contribute only their own model configuration.

Legacy uses OpenIV Package Format 2.2 and declarative `add` plus `dlclist.xml`
operations. Enhanced uses a target-profile boundary. Until that profile records
a passed integration test, `.oiv` creation fails closed and a separately invoked
OpenRPF-ready ZIP is the only output.

Package GUIDs are persisted per project, target, and package mode. Content paths
are resolved beneath the supplied Story staging root, checksummed, collision
checked, written to a unique temporary archive, reopened and verified, then
published atomically. ScriptHookV, ASI loaders, OpenIV/OpenRPF, Alchemist, and
other third-party binaries are always omitted.

Vehicle archives require two hash-bound staging receipts: the shared vehicle
package-builder report and a native RPF index receipt. The desktop Workbench
creates the latter by opening the final staged archive through the SDK's pinned
RPF helper, checking the selected edition, required metadata, and each declared
model's YFT/YTD assets. A caller-provided edition string or an `RPF7` prefix is
not sufficient evidence.

Runtime and self-contained transports accept only an explicit Story runtime
profile with a pinned AMD64 PE checksum, required exports, redistribution grant,
supported-build mapping, and acceptance receipt. No runtime profile is trusted
implicitly and the repository ships no precompiled ASI.

Self-contained output warns that declarative replacement cannot prevent an older
ASI from overwriting a newer installed runtime and requires explicit confirmation.
No generated uninstall operation removes shared runtime state or broad folders.

## Consequences

- Two vehicle-only packages install different DLC/config files and do not replace
  the shared ASI.
- Runtime OIV ownership is separate from vehicle ownership; automatic runtime
  uninstall is intentionally not generated.
- Installer-level XML idempotence cannot be guaranteed by this SDK. Stable DLC
  names and package identity make repeat builds target one logical package, and
  the limitation is shown in manifests/documentation.
- Enhanced OIV support is a capability result, not an edition-name guess.
- Synthetic archives and PE files used by unit tests exercise the transport
  contract only; they are never published as installable example binaries.
