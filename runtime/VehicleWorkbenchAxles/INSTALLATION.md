# Staging, installation, and removal policy

## Current source release

Do not install the generated native `.asi` host artifacts into GTA V.  Both
descriptors say that no validated wheel-access profile exists, and the Workbench
bundler must reject them.  They exist so the ScriptHook host boundary, shared
binary shape, edition isolation, and build pipeline can be tested without
inventing game-memory support.

## Future validated release

The default Workbench action creates a staged package.  It does not modify the
game directory.  A validated Story package contains only:

- one generic `VehicleWorkbenchAxles.asi` matching the exact target edition;
- `VehicleWorkbenchAxles/runtime.json`;
- `VehicleWorkbenchAxles/runtime-metadata.json` for release identity and validation metadata;
- one JSON file per model below `VehicleWorkbenchAxles/configs/`;
- a compatibility manifest, checksum, README, and uninstall receipt.

It never contains ScriptHookV, `dinput8.dll`, another ASI loader, a FiveM
resource, Cfx tools, or third-party files without confirmed redistribution
rights.  Dependency instructions must link to the official source.

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
