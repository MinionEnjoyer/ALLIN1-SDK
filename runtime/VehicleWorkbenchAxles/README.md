# VehicleWorkbenchAxles Story Mode runtime source

This directory contains the native, data-driven Story Mode runtime architecture
used by the Vehicle Workbench axle bundler.  It supports variable-length arrays
of two through five canonical physical axle pairs.  The included six-wheel bus
is only an example.

## Safety and current support status

The platform-neutral core, JSON validator, lifecycle, transactional wheel-bit
updates, online guard, and mock tests are implemented.  The two Windows targets
compile from shared source.

**Neither Story Mode edition is deployable or marked supported yet.**  The
Legacy and Enhanced adapters intentionally contain no game signatures, fixed
offsets, layouts, or callable accessors.  `Resolve()` always fails before game
memory access, and the compatibility manifest lists zero supported builds.  The
compiled skeleton exports `VehicleWorkbenchAxles_HasValidatedProfile() == false`
so the bundler can refuse it.

This is intentional.  CitizenFX behavior does not establish a safe Story Mode
memory ABI.  Each edition needs a separately reviewed signature profile,
ScriptHookV host bridge, and in-game acceptance run before packaging.

## Canonical physical axles

- `wheel_lf` / `wheel_rf`
- `wheel_lm1` / `wheel_rm1`
- `wheel_lm2` / `wheel_rm2`
- `wheel_lm3` / `wheel_rm3`
- `wheel_lr` / `wheel_rr`

Configuration arrays can contain 2–5 pairs.  Steering and powered state are
independent booleans on every pair.  The runtime maps bones through the exported
`wheelIndexMap`; it never assumes `axleOrder * 2`.  Before applying, it validates
the complete map against the game-reported wheel count and the adapter's
validated maximum physical axle count.

Dual tyres, decorative tag-wheel meshes, and other duplicated geometry remain
visual additions to one physical wheel slot.  They are not entries in the
runtime map.  Vehicles beyond the game's recognized slots require cosmetic
wheels or a future custom-physics extension.  GTA's stock front/shared-rear
visual instancing limit is unchanged.

## Expected installed layout (after validation, not today)

```text
VehicleWorkbenchAxles.asi
VehicleWorkbenchAxles/
  runtime.json
  configs/
    example_bus.json
  logs/
    VehicleWorkbenchAxles.log
```

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
Enhanced `.asi` skeleton artifacts.  They are development artifacts only and
must not be staged into a game or release bundle while the descriptor reports
no validated profile.

## Workbench integration contract

The future bundler integration supplies:

1. a schema-1 JSON config in `VehicleWorkbenchAxles/configs/`;
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
- a receipt showing every required in-game acceptance test passed;
- evidence that `VehicleWorkbenchAxles_HasValidatedProfile` returned true for
  that exact binary and build.

The SDK independently parses the `.asi` as an AMD64 PE32+ DLL, walks its export
table, and requires both runtime exports to resolve into executable sections.
Renamed text files, ASCII fixtures, 32-bit binaries, forwarded exports, and
receipts whose hashes or fields drift are rejected.

The machine-readable formats are
[`story-runtime-profile.schema.json`](schemas/story-runtime-profile.schema.json)
and
[`story-runtime-receipt.schema.json`](schemas/story-runtime-receipt.schema.json).
Profile paths are resolved relative to the profile JSON; logs and reports expose
only basenames and hashes.

Inspect the empty fail-closed catalog or explicitly supplied profiles with:

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

The future ScriptHook host bridge implements `IVehicleHost` and
`ISignatureResolver`.  A validated edition adapter implements `IWheelAccess`.
The shared core never takes a ScriptHook dependency and never retains a raw
vehicle/wheel pointer.

## Lifecycle

- Startup: online guard, exact edition/build detection, fail-closed adapter
  resolution, bounded config loading, duplicate-model isolation, concise report.
- Gameplay: event application for create/ownership/repair/wheel recreation plus
  a configurable 2-second recovery verification.  No per-frame write loop.
- Apply: reacquire entity snapshot, validate generation/count/map, read all
  16-bit flags, modify only `0x08` and `0x10`, rollback partial failure.
- Shutdown: stop new writes, reacquire matching entity generation, restore only
  managed bits when safe, release all tracking.
- Online detection: immediately drop state and disable without restoration or
  further memory access.

Logs use model hashes, build numbers, target identifiers, config basenames, and
reason codes.  They do not include absolute user paths or repeated frame spam.

See [ADR-0001](ADR-0001-cross-edition-story-runtime.md) and
[SUPPORTED_BUILDS.md](SUPPORTED_BUILDS.md) before extending an adapter.  Staged
installation and ownership rules are in [INSTALLATION.md](INSTALLATION.md).
