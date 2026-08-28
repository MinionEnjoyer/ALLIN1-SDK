# VehicleWorkbenchAxles Story Mode runtime source

This directory contains the native, data-driven Story Mode runtime architecture
used by the Vehicle Workbench axle bundler.  It supports variable-length arrays
of two through five canonical physical axle pairs.  The included six-wheel bus
is only an example.

## Safety and current support status

The platform-neutral core, JSON validator, lifecycle, transactional wheel-bit
updates, online guard, and mock tests are implemented. The two Windows targets
compile from shared source and now contain separate Legacy and Enhanced
signature-gated wheel adapters. Those adapters derive private layout values at
runtime, require unique executable signatures, validate live wheel counts and
canonical bone IDs, and reacquire the vehicle and wheel collection for every
operation. They do not contain permanent CVehicle or CWheel offsets.

**Neither Story Mode edition is deployable or marked supported yet.** The
compiled artifacts now export
`VehicleWorkbenchAxles_HasValidatedProfile() == true` to report that their
signature-gated accessor profile is present. That export does not grant
distribution approval. Both compatibility entries still have empty supported
build arrays, `packageEligible: false`, and no in-game acceptance receipt, so
the Workbench bundler continues to refuse them.

One Enhanced executable identity (`1.0.1158.13`, build `1158`) has been observed
locally, but it has not passed the required in-game matrix. No Legacy executable
has been validated. Compilation, signature resolution, or observing an
executable hash is not an acceptance result.

## Canonical physical axles

- `wheel_lf` / `wheel_rf`
- `wheel_lm1` / `wheel_rm1`
- `wheel_lm2` / `wheel_rm2`
- `wheel_lm3` / `wheel_rm3`
- `wheel_lr` / `wheel_rr`

Configuration arrays can contain 2–5 pairs. Steering, signed steering gain,
powered state, and visual family remain independent on every pair. Schema 1
omits `steeringGain` and retains the original boolean-only behavior: `+1` for a
steered axle and `0` for a fixed axle. Schema 2 requires an explicit bounded
gain on every axle, runtime 2.0 or newer, and calculation evidence; negative
values counter-steer and non-steered axles must use zero. Automatic evidence
also records the positive `pairPositionTolerance` and `positionEpsilon` inputs
so the calculation can be reproduced exactly. An automatic configuration can
set `runtimeRecompute: true` with
`referenceSelection: "farthest_steered_axle"`; a validated adapter then reads
the live vehicle-local wheel positions, rebuilds the pivot from the selected
fixed axles, and normalizes against the farthest physical steering axle. This
corrects stale authoring gains after an intentional physical-order override and
requires `minimumRuntimeVersion` 4.1.0 or newer.
Manual calculations always retain their authored gains. Schema 3 adds optional axle
support bias: every physical axle supplies a bounded `supportWeight`, and a
validated build profile must expose reversible per-wheel `StaticForce` access
plus a host physics-activation hook. The runtime normalizes those weights to
the vehicle's original total support and verifies or rolls back the complete
transaction. Schema 4 adds the vehicle-level
`steeringCommandPolarity` value (`normal` or `inverted`). Per-axle
`steeringGain` remains the base gain; runtime multiplies it once by `+1` or
`-1` to produce the effective gain without changing physical axle order. The
runtime maps bones through the exported
`wheelIndexMap`; it never assumes `axleOrder * 2`.  Before applying, it validates
the complete map against the game-reported wheel count and the adapter's
validated maximum physical axle count.

Dual tyres, decorative tag-wheel meshes, and other duplicated geometry remain
visual additions to one physical wheel slot.  They are not entries in the
runtime map.  Vehicles beyond the game's recognized slots require cosmetic
wheels or a future custom-physics extension.  GTA's stock front/shared-rear
visual instancing limit is unchanged.

## Expected installed layout (after acceptance, not today)

```text
VehicleWorkbenchAxles.asi
VehicleWorkbenchAxles/
  runtime.json
  runtime-metadata.json
  configs/
    example_bus.json
    example_bus.bones.json
  logs/
    VehicleWorkbenchAxles.log
```

`runtime.json` uses the strict schema-2 native settings contract shown in
[`examples/runtime.json`](examples/runtime.json). The generic controller is
enabled by default; set `"enabled": false` to leave it installed while
preventing configuration discovery, profile resolution, vehicle enumeration,
and wheel access on the next game launch. Invalid settings also stop the host
before those operations; a missing settings file alone uses the documented
enabled defaults.

`configurationDirectory` and `logFile` are guarded paths relative to the GTA
installation directory. This lets an existing pack keep its portable axle
sidecars and log beneath its own `scripts` subdirectory while the native ASI
stays in the GTA root. Absolute paths and parent traversal are rejected. The
startup log records the configured relative directory inspected; if ScriptHookV
binding fails before that log can open, the ASI rejects its own module load so
the failure appears in the ASI loader log instead of remaining silent.
Schema-1 settings remain readable with their original paths relative to
`VehicleWorkbenchAxles/`; root-relative custom paths require schema 2.

The bundled `example_bus` configuration is intentionally non-deployable. Its
companion bone fixture documents the illustrative vehicle-local positions used
to produce the provenance digest; real packages must recalculate that digest
from the selected model's decoded canonical wheel bones.

One generic binary loads multiple configurations.  Vehicle packages declare a
minimum runtime version and copy only their own configuration.  They must not
rename, duplicate, or downgrade the generic runtime; remove unrelated configs;
or redistribute ScriptHookV/ASI loaders.

## Build

The portable core and tests have no external dependencies:

```powershell
cmake -S . -B out/core -DVWA_BUILD_ASI_SKELETONS=OFF
cmake --build out/core
ctest --test-dir out/core --output-on-failure
```

On Windows, `VWA_BUILD_ASI_SKELETONS=ON` also creates separate Legacy and
Enhanced native `.asi` host artifacts. The historical option name is retained
for build compatibility. These remain unaccepted development artifacts. A true
compiled-profile export is necessary but not sufficient for staging; the exact
target build must also have a pinned, passing in-game receipt.

The installed SDK exposes this as **Config > Build Story controller package**
in the Axle Configurator and as `build-story-axle-runtime` in the console and
typed Agent API. Those paths can build either edition or both, place any number
of reviewed vehicle sidecars beneath a configurable GTA-root-relative folder,
and route logging to a configurable relative file. They run CTest, the native
configuration parser, PE/export checks, edition-marker checks, and deterministic
archive hashing before publishing a new output directory. Local results remain
explicitly marked `supported: false` and `game_acceptance: not-tested`.

## Workbench integration contract

The Workbench bundler integration supplies:

1. a schema-1 legacy, schema-2 signed-steering, schema-3 support-bias, or
   schema-4 steering-polarity JSON config in the directory selected by
   `runtime.json` (default `VehicleWorkbenchAxles/configs/`);
2. an explicit `wheelIndexMap` emitted from canonical bones and target vehicle
   information;
3. a runtime dependency record with target edition, minimum runtime version,
   max schema version, checksum, and validated game builds;
4. exactly one matching generic ASI per staged Story target;
5. a hard packaging gate that checks the ASI descriptor and compatibility
   manifest before inclusion.

[`profiles/runtime-package.json`](profiles/runtime-package.json) is the
machine-readable build/output contract.  Both targets currently have
`packageEligible: false` and empty supported-build arrays.  A builder must not
change that merely because compilation succeeded.

### Trusted Story profile gate

Story staging accepts no implicit profiles.  A release engineer supplies one
profile JSON per edition with:

- an exact runtime binary and pinned SHA-256;
- an exact validation receipt and pinned SHA-256;
- a package-eligible target/build list and confirmed redistribution terms;
- an exact maximum axle schema plus one explicit `capabilities` object for
  signed steering, static-force access, physics activation, and authoritative
  vehicle-local wheel positions (an omitted object defaults every capability
  to unavailable);
- a receipt showing every required in-game acceptance test passed;
- evidence that `VehicleWorkbenchAxles_HasValidatedProfile` returned true for
  that exact binary and build.

The SDK independently parses the `.asi` as an AMD64 PE32+ DLL, walks its export
table, and requires all runtime and ScriptHook-host evidence exports to resolve
into executable sections.
Renamed text files, ASCII fixtures, 32-bit binaries, forwarded exports, and
receipts whose hashes or fields drift are rejected.

Schema-2 signed steering/runtime geometry and schema-3 axle support are unlocked only by those
validated dependency capabilities, never by the immutable target defaults.
Enabling any capability adds its accessor, readback, reapplication or
rollback, and restoration tests to the required receipt matrix.

The machine-readable formats are
[`story-runtime-profile.schema.json`](schemas/story-runtime-profile.schema.json)
and
[`story-runtime-receipt.schema.json`](schemas/story-runtime-receipt.schema.json).
Profile paths are resolved relative to the profile JSON; logs and reports expose
only basenames and hashes.

Inspect the fail-closed distribution catalog or explicitly supplied accepted
profiles with:

```powershell
allin1-sdk inspect-story-axle-runtimes
allin1-sdk inspect-story-axle-runtimes `
  --story-profile .\validated-legacy-profile.json `
  --game-build story-legacy=BUILD_ID
```

The same `--story-profile` and `--game-build TARGET=BUILD` options are available
on `plan-axle-runtime-bundle` and `build-axle-runtime-bundle`, including through
the structured local Agent API.  Without an explicit verified profile, Story
targets remain omitted.

The native ScriptHook host bridge implements `IVehicleHost` and
`ISignatureResolver`. Separate Legacy and Enhanced adapters implement
`IWheelAccess`. They are compiled into edition-specific binaries and derive
their private wheel layout only after unique target signatures and live layout
canaries pass. The shared core never takes a ScriptHook dependency and never
retains a raw vehicle/wheel pointer.

The compiled adapters expose steering/drive flags, canonical live wheel-bone
verification, signed steering gain, reversible `StaticForce`, and physics
activation. This is compiled capability, not game-build acceptance or
distribution approval. Authoritative vehicle-local wheel-position reads are
not implemented yet, so a configuration requesting runtime geometry
recomputation is disabled before any vehicle write. Precomputed signed gains
remain representable in ordinary axle configurations.

## Lifecycle

- Startup: online guard, exact edition/build detection, unique signature and
  layout-canary resolution, bounded config loading, duplicate-model isolation,
  concise report. Distribution remains separately pinned to an accepted exact
  executable build and receipt.
- Gameplay: event application for create/ownership/repair/wheel recreation plus
  a configurable recovery verification. Because GTA rebuilds its steering-limit
  field during simulation, only already-tracked, explicitly steered wheel gains
  receive bounded maintenance on each host service tick. Flags, drive state,
  suspension, discovery, and full recovery remain event/interval driven.
- Apply: reacquire entity snapshot, validate generation/count/map, read all
  16-bit flags, modify only `0x08` and `0x10`, and—only with an explicit
  validated capability—apply signed gain; rollback partial failure.
- Shutdown: stop new work, reacquire matching entity generation, restore only
  managed bits when safe, and retain incomplete restoration state for an
  explicit retry rather than silently rebasing or releasing it.
- Online detection: immediately drop state and disable without restoration or
  further memory access.

Each configuration must explicitly opt into at least one recognized Story
target in `compatibility`; omission, unknown keys, and false-only declarations
fail closed.

Logs use model hashes, build numbers, target identifiers, config basenames, and
reason codes.  They do not include absolute user paths; repeated volatile-gain
maintenance is deduplicated instead of producing frame spam.

See [ADR-0001](ADR-0001-cross-edition-story-runtime.md) and
[SUPPORTED_BUILDS.md](SUPPORTED_BUILDS.md) before extending an adapter.  Staged
installation and ownership rules are in [INSTALLATION.md](INSTALLATION.md).
