# GBAY vanilla preview pack

Ready-rendered catalog artwork for the ALLIN1 launcher/GBAY. These are image
downloads, not an SDK update. The SDK's normal latest software release is unchanged.

| Download | Images | Size |
| --- | ---: | ---: |
| [Weapons](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases/download/gbay-previews-2026-09-07-2/gbay-weapons.zip) | 111 / 111 | 15.5 MiB |
| [Vehicles](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases/download/gbay-previews-2026-09-07-2/gbay-vehicles.zip) | 935 / 935 | 183.9 MiB |
| [Gear](https://github.com/MinionEnjoyer/ALLIN1-SDK/releases/download/gbay-previews-2026-09-07-2/gbay-gear.zip) | 10 / 10 | 1.4 MiB |

[Machine-readable download manifest](manifest.json) · [Coverage inventory](inventory.json)

Each ZIP contains `index.json` and 512×320 PNGs only. No custom/add-on weapon
previews, model files, texture dictionaries, game executables, or project source
are included. All 935 vanilla vehicle previews are included, including Tampa.

Use **Setup → GBAY default previews** in a preview-pack-enabled ALLIN1 launcher.
The launcher checks pinned archive size/SHA-256 and every image before publishing
to `plugins/ReactorV/ui/assets/allin1/default-weapons`, `default-vehicles`, or
`default-gear` inside the selected game installation. The same downloads work
with Legacy and Enhanced. Generated/custom artwork remains in separate folders
and takes priority. Missing-only generation reuses downloaded defaults.

For manual installation with a compatible ALLIN1 bridge, close GTA and extract
each ZIP's contents directly into its matching `default-*` folder above. Do not
replace the `generated-*` folders. Use `manifest.json` to verify the ZIP SHA-256.

The large image ZIPs are GitHub release assets rather than Git-tracked binaries.
This folder is the stable discovery point for clients. Rendered depictions retain
the rights of their respective game/vehicle artwork owners; the SDK's code license
does not grant ownership of the underlying game artwork.
