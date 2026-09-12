# Edition-specific component bundles

Schema 6 is a collection of independent schema-1 through schema-4 packages,
not a merged manifest or automatic multi-package transaction.

```powershell
allin1-sdk build-component-bundle --legacy Base.zip --legacy Vehicles.zip --legacy ReShade.zip --enhanced Enhanced.zip --id corefx-singleplayer --name "CoreFX Singleplayer" --version 2026.09.09 --output CoreFX-Both.zip
allin1-sdk validate-package CoreFX-Both.zip --edition legacy
allin1-sdk validate-package CoreFX-Both.zip --edition enhanced
```

Pass 1–32 managed inputs per edition, in installation order. Use the OIV/RPF
workbench first for sources needing compilation. Nested edition bundles are
rejected. Component manifests and payloads remain unchanged, including IDs,
versions, descriptors, artifact provenance and exact RPF rollback hashes.
The collection version is independent of each upstream component version.

The root declares schema_version = 6, type = "collection", and both editions.
Each [[variants.legacy.components]] / [[variants.enhanced.components]] record
contains only manifest = "<edition>/<component>/mod.toml" and its lowercase
SHA-256. Duplicate IDs within one edition, path overlap, internal conflicts,
and misordered or incompatible package dependencies fail before publication.
All components of all editions must pass validation.

The updated launcher imports one ZIP and displays only the selected edition's
components. Each component has its own reviewed install and independent
Content controls, receipt, enable/disable and uninstall. Existing identities
are retained. Installed RPF components require explicit uninstall before
replacement; no receipt adoption or automatic migration occurs.

There is no batch-install button or collection receipt. A component failure
does not undo components already installed, and no action is automatically
replayed. After a successful install, the same ZIP inspection refreshes.

API: allin1_sdk.component_bundle.build_component_bundle(output, legacy=[...],
enhanced=[...], mod_id=..., name=..., version=...). The agent API exposes
build-component-bundle as authoring_write, with array-valued edition parameters.
It grants no game-write authority. CLI installation requires an explicit
--component package.id plus the existing --acknowledge-write and target options.

Exports are outside GTA, atomic and non-overwriting. Unrelated source files
are excluded. Hashes provide integrity, not publisher authentication.
Old readers reject schema 6; update both SDK and launcher before use.
