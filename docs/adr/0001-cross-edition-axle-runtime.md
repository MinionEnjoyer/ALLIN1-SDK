# ADR 0001: Cross-edition axle runtime bundles

Status: Accepted for SDK implementation; every runtime target remains pending its
edition-specific in-game acceptance test.

## Context

The Vehicle Workbench needs one axle configuration to describe two through five
canonical physical axle pairs while producing deliberately different FiveM and
Story Mode packages. FiveM exposes wheel-state natives. Story Mode requires a
separately built and game-build-qualified ASI. Enhanced vehicle assets can also
require an explicitly configured external conversion pipeline.

The six-wheel `Steer -> Drive -> Rear Steer` bus is a regression fixture, not a
runtime shape. Dual tyres are visual geometry attached to an existing wheel slot
and never create another physical axle or runtime wheel index.

## Decision

1. `AxleConfiguration` remains the runtime-independent authoring contract.
2. `AxleRuntimeBundlePlanner` resolves four explicit target IDs through immutable
   capability records. UI and API consumers can use the same records; edition
   checks must not be duplicated in presentation code.
3. Runtime wheel maps are keyed by canonical bone semantics. Explicit exported
   bone-to-index evidence wins. Otherwise a target mapping rule filters its
   canonical semantic sequence to the configured pairs. No code derives an index
   using `axleOrder * 2`.
4. A full build is a staged, atomic publication. Unsupported targets are omitted
   with machine-readable reasons. Direct game installation is out of scope.
5. FiveM targets share the configurator's generated runtime implementation and
   are combined into one resource per target. ASI files are forbidden there.
6. Story targets require a real, checksum-verified ASI build profile. The SDK
   does not synthesize a binary, redistribute ScriptHookV, or claim runtime
   support from a configuration file alone.
7. Enhanced conversion is an adapter boundary. Invocation requires both a local
   executable path and explicit approval. The SDK never downloads a converter.
8. Dependencies carry source, license, bundling, and redistribution fields.
   Known third-party loaders and converters are link-only unless redistribution
   rights are explicitly recorded.
9. Runtime/config compatibility manifests record schema, minimum runtime,
   checksums, game-build declarations, mapping provenance, omissions, and an
   `awaiting_in_game_validation` acceptance state. Packaging success is not an
   in-game support claim.

## Safety consequences

- Cross-target contamination is checked after staging: FiveM cannot contain ASI
  binaries; Story cannot contain FiveM manifests or Lua; dependency binaries
  without redistribution rights cannot be bundled.
- Duplicate model hashes, wheel indices outside the reported count, unsupported
  schemas/builds, and incompatible runtime versions fail planning.
- Story Enhanced may stage a validated runtime/configuration while reporting
  vehicle asset installation as manual when no supported Story converter exists,
  as required by the architecture. FiveM Enhanced vehicle assets are omitted as
  a target when their approved converter is unavailable.
- Vehicles outside the recognized 2-5 canonical physical-pair envelope are
  reported as cosmetic-wheel/future-custom-physics cases.

## Rejected alternatives

- One generated ASI per vehicle: creates binary conflicts and unmaintainable
  upgrades.
- Executable snippets in vehicle JSON: violates the data-only trust boundary.
- Fixed memory offsets or a placeholder ASI: falsely implies Story support and
  risks unsafe writes.
- A six-wheel loop or ordinal index formula: fails valid four-, eight-, and
  ten-wheel canonical configurations.
- Treating dual tyres as wheel slots: conflicts with the engine's physical wheel
  model.
