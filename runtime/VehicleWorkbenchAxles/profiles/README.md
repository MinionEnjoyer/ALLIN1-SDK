# Validated wheel-access profiles

No profile ships in this source release.  Consequently the Legacy and Enhanced
adapters always fail closed before reading or writing game memory.

A future profile is accepted only after all of these are independently reviewed
and exercised in the target edition:

- exact numeric game build and executable fingerprint;
- edition-specific byte signatures for every wheel accessor;
- expected bytes around every resolved location;
- executable-page and module-range verification;
- confirmed 16-bit wheel-flag field behavior;
- vehicle and wheel-lifetime validation;
- maximum recognized physical axle count;
- automated mock tests and the full in-game acceptance matrix.

Profiles must not contain or fall back to permanent absolute addresses.  A
failure to resolve any required accessor disables the entire adapter, logs one
compatibility reason, and permits no partial memory access.
