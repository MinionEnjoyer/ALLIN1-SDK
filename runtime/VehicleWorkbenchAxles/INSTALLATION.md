# Staging, installation, and removal policy

## Current source release

Do not install the generated native `.asi` host artifacts into GTA V. Both
artifacts now contain compiled, signature-gated wheel adapters, but neither
edition has a package-eligible in-game acceptance receipt. The Workbench bundler
must therefore continue to reject them. The profile-presence export reports the
binary's compiled accessor capability; it does not mean that any game build is
supported or approved for distribution.

One local Enhanced `1.0.1158.13` executable identity has been recorded for
future validation. It remains unaccepted. No Legacy executable has been
validated.

## Future validated release

The default Workbench action creates a staged package.  It does not modify the
game directory.  A validated Story package contains only:

- one generic `VehicleWorkbenchAxles.asi` matching the exact target edition;
- `VehicleWorkbenchAxles.Settings.exe` beside the ASI for recipient-safe path
  editing without hand-editing `runtime.json`;
- `VehicleWorkbenchAxles/runtime.json`;
- `VehicleWorkbenchAxles/runtime-metadata.json` for release identity and validation metadata;
- one JSON file per model below the guarded `configurationDirectory` selected
  by `runtime.json` (default `VehicleWorkbenchAxles/configs/`);
- a compatibility manifest, checksum, README, and uninstall receipt.

It never contains ScriptHookV, `dinput8.dll`, another ASI loader, a FiveM
resource, Cfx tools, or third-party files without confirmed redistribution
rights.  Dependency instructions must link to the official source.

The native ASI itself belongs in the GTA root. `configurationDirectory` and
`logFile` may point to a package-owned relative location such as
`scripts/ExamplePack/VehicleSettings` and `scripts/ExamplePack/Axles.log`.
They never authorize an absolute path or `..` traversal outside the game root.

If direct installation is enabled later, it must:

1. detect the exact Legacy/Enhanced executable and supported build;
2. require explicit confirmation;
3. refuse symlink/reparse escapes and cross-edition targets;
4. compare the installed runtime version and never downgrade it;
5. back up a replaced generic runtime and write files atomically;
6. merge only the requested model configs and reject duplicate model hashes;
7. record every created/replaced file, prior checksum, and package owner.

## Removal

Removal uses the install receipt.  It deletes only configuration files owned by
the selected package.  The shared ASI and runtime directory remain while any
other installed package depends on them.  The final dependent package may
remove the shared runtime only when its installed checksum matches the receipt.
Unrelated configs, ASIs, loaders, and ScriptHook files are never removed.

If a checksum differs, removal stops and reports the changed file for manual
review.  Backups are restored only to the exact edition and path recorded by the
receipt.
