# SDK off-game hardening harness

`scripts/hardening_harness.py` is the SDK-owned aggregate source-validation
receipt. See [validation](validation.md#off-game-hardening-harness) for the
invocation, prerequisite modes, evidence rules and explicit non-claims.

It is not a release builder, installer test, GTA test, or replacement for live
acceptance. Every invocation requires a new safe run identifier and preserves
all existing evidence directories.
