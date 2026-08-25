# OIV axle bundle examples

These templates demonstrate the three supported request shapes around an already
staged Story Legacy build. Replace every `<...>` token with a real path/value and
retain the identity-store JSON between versions.

- `vehicle-only-request.template.json` produces
  `MyBus_Legacy_v1.0.0.oiv` and never includes an ASI.
- `runtime-only-request.template.json` produces
  `VehicleWorkbenchAxles_Runtime_Legacy_v1.0.0.oiv` only when the referenced
  runtime profile and acceptance receipt pass the package gate.
- `self-contained-request.template.json` produces
  `MyBus_Legacy_SelfContained_v1.0.0.oiv` and requires the explicit overwrite
  acknowledgement.

The staging root must contain the build planner's `compatibility-manifest.json`,
native RPF validation receipt, vehicle build report, and files referenced by the
request. See `docs/oiv-story-packages.md` for the exact evidence contract.

No `.oiv`, ASI, ScriptHookV binary, loader, or third-party vehicle asset is
included in these examples. Automated tests generate all three archive layouts
inside temporary directories and verify them byte-for-byte.
