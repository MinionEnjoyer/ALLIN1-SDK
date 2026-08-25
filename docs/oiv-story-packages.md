# Story Mode OIV packages

The Vehicle Workbench can wrap a reviewed Story staging build in an OIV 2.2
installer. The exporter does not rebuild vehicle files and never writes to GTA V.

## Modes

- **Vehicle Only — Recommended** installs one add-on DLC archive, its stable
  `dlclist.xml` entry, model-specific axle JSON, documentation, and the Workbench
  ownership manifest. It declares the shared axle runtime as a dependency and
  never contains an ASI.
- **Runtime Only** installs the shared `VehicleWorkbenchAxles.asi`, its pinned
  validation receipt and metadata, and cooperative `configs`/`logs` notices. It
  requires an explicit package-eligible Story runtime profile.
- **Self-Contained — Advanced** combines both plans. It requires an explicit
  acknowledgement because installation may replace a newer shared runtime.

ScriptHookV and ASI loaders are always user-provided. The generated dependency
document links to the official ScriptHookV page; the binaries are never bundled.

## Evidence required before export

Vehicle OIV modes consume a `compatibility-manifest.json` produced with the
Story staging directory. Every DLC archive is bound to:

1. its SHA-256 and edition/asset format;
2. the vehicle package builder's validated report; and
3. a native RPF index receipt proving the archive opens for the selected edition,
   contains `vehicles.meta`, `handling.meta`, and `carvariations.meta`, and has a
   YFT and YTD for every declared model.

Runtime modes accept only `StoryRuntimeProfile.load(...).runtime_dependency()`
evidence. The binary must be AMD64 PE32+, expose the generic axle runtime ABI,
match its pinned checksum and build map, permit redistribution, and carry a
matching acceptance receipt. Renaming an arbitrary DLL or writing a runtime JSON
does not pass this gate.

## Console and Agent API

```text
allin1-sdk plan-axle-oiv REQUEST.json --identity-store IDENTITIES.json
allin1-sdk build-axle-oiv REQUEST.json --identity-store IDENTITIES.json \
  --output MyBus_Legacy_v1.0.0.oiv --acknowledge-edit
```

Both commands use the same typed implementation as the desktop preview. The
identity store must be retained between releases so upgrades keep one logical
package GUID. Runtime request objects contain a `profile_path`; caller-authored
binary checksums, build lists, and redistribution claims are rejected.
An optional `diagnostic_report_path` in the request writes a new, non-overwriting
JSON failure report outside the staging tree; no partial installer is retained.

Templates for all three modes are under `examples/oiv-axle-bundles`. They are
input templates, not installable packages. The transport regression suite builds
and reopens all three example archive shapes using synthetic fixtures; no fake
runtime or third-party vehicle asset is distributed.

## Install and uninstall ownership

Install OIV output through a compatible package installer into the user's mods
folder. Do not load it in GTA Online.

A vehicle package owns only its exact DLC archive, axle configuration, manifest,
and documentation. Removing it must not delete the shared runtime, another
vehicle's config, ScriptHookV, an ASI loader, or a broad directory. Runtime
removal is intentionally not automated because other vehicle configurations may
still depend on it.

## Target status

- **Story Legacy:** OIV 2.2 generation, deterministic verification, native RPF
  evidence, and transport-level regression coverage are enabled. A real vehicle
  and runtime still require their own tested assets/profile.
- **Story Enhanced:** OIV remains disabled until a compatible installer, Gen9
  asset pipeline, Enhanced runtime, supported builds, and in-game acceptance
  receipt are supplied. Current output is an explicitly labelled OpenRPF-ready
  manual ZIP.
- **FiveM:** unchanged; FiveM targets export resource ZIPs and reject OIV/ASI
  content.

The SDK does not claim an in-game acceptance run from synthetic test fixtures.
