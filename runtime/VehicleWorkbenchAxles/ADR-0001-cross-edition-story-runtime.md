# ADR-0001: One fail-closed, data-driven Story Mode axle runtime

- Status: accepted for source scaffolding
- Date: 2026-08-25
- Scope: GTA V Story Mode Legacy and Enhanced

## Context

Vehicle authors need selective steering and drive state on two through five
canonical physical axle pairs.  A six-wheel bus is an acceptance fixture, not
the runtime shape.  GTA's two visual wheel-template families and physical wheel
slots are separate concepts: dual tyres and other bone-bound geometry are
cosmetic and never become runtime wheel indices.

FiveM exposes wheel natives.  Story Mode does not.  A Story Mode implementation
therefore needs an edition- and build-specific bridge to internal wheel state.
That bridge is the only component allowed to know signatures or layouts.

## Decision

Build one generic runtime from shared C++ source and load any number of
versioned JSON vehicle configurations.  Build Legacy and Enhanced artifacts
from that source, but keep their wheel-access adapters isolated.

Runtime contract 2.0 keeps schema 1 as the legacy boolean `+1/0` format and
accepts schema 2 only when every gain is explicit, minimum runtime 2.0 is
declared, and calculation evidence is present. Exact-build adapters still have
to attest the separate signed-gain accessor before such a config is deployable.
Runtime contract 3.0 adds schema-3 axle support bias. Every physical axle must
declare a bounded support weight, and the exact-build bridge must independently
attest reversible `StaticForce` access and physics activation. Total original
support is preserved through normalization, readback verification, rollback,
and unload restoration.

The runtime core depends only on `IWheelAccess`, `IVehicleHost`, and `ILogSink`.
It maps canonical bone semantics to wheel indices from the exported
`wheelIndexMap`; it never calculates an index as `axleOrder * 2`.

Each adapter accepts only an exact game edition/build with a separately
validated signature profile.  A profile must resolve callable accessors through
validated signatures and executable-page checks.  Raw permanent offsets and
unguarded structure dereferences are forbidden.  This source release contains
no validated profiles, so both adapters intentionally disable before any write.

The runtime:

1. checks the online-session guard before resolution and on every service pass;
2. validates all configurations and disables only conflicting model hashes;
3. reacquires an entity snapshot for each operation and retains no game pointer;
4. verifies game-reported wheel count and the complete unique index map;
5. read-modify-writes only bits `0x08` (steered) and `0x10` (driven);
6. treats signed per-axle steering gain as a distinct, opt-in profile
   capability and rejects scaled/counter-steer configurations before writes
   when the exact-build adapter cannot read, write, and restore it;
7. rolls back a partial application when the adapter reports failure and
   retains the original pre-application baseline when recovery must retry;
8. reapplies on explicit lifecycle events or a bounded recovery pass, never an
   unconditional frame write loop;
9. rechecks the online guard immediately before every mutating adapter call;
10. restores modified bits and any capability-backed gain only when the entity
   and wheel generation can be
   safely reacquired during shutdown, retaining failed reads/writes for a safe
   follow-up shutdown attempt.

## Consequences

- One binary can serve multiple vehicle packages without executable content in
  configuration files.
- New game builds require a reviewed adapter profile and in-game acceptance
  run; source compilation alone never marks a build supported.
- Story Mode runtime packaging must remain separate from vehicle asset
  installation and from FiveM resources.
- Vehicles beyond the target's recognized physical slots are rejected and must
  use cosmetic wheels until a future custom-physics extension exists.
- The host/ScriptHook bridge and validated game profiles remain intentionally
  unimplemented deployment gates.  The platform-neutral core, contracts,
  validator, lifecycle, and mock behavior are testable now.

## Rejected alternatives

- Per-vehicle ASIs: causes binary conflicts and unmaintainable upgrades.
- Fixed absolute offsets: unsafe across builds and editions.
- Bone swapping, left/right inversion, or negative steering lock: corrupts
  vehicle semantics and player steering.
- Full wheel-flag replacement: destroys unknown game state.
- A per-frame write loop: unnecessary, expensive, and warning-prone.
