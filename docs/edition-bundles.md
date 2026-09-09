# Edition-aware release bundles

One launcher ZIP can now contain separate Legacy and Enhanced managed packages.
The launcher uses the **selected GTA installation**, not the host machine or
archive filename, to select exactly one variant. Both editions installed on the
same PC keep independent receipts, backups, dependencies and uninstall ownership.

This requires the updated schema-5 reader in the launcher and SDK. Older readers
reject schema 5; they must not be given a schema-1 manifest containing conditional
fields that they would ignore. The code change alone does not update an already
installed launcher.

## Build from a ZIP containing two OIVs

Run the updated SDK:

```powershell
allin1-sdk build-edition-bundle --source-zip "MyMod.zip" --legacy "Legacy/install.oiv" --enhanced "Enhanced/install.oiv" --id "author.my-mod" --name "My Mod" --version "1.0.0" --output "MyMod-ALLIN1.zip"
```

Member paths are explicit paths inside the source ZIP. Their filenames are not
used to guess compatibility. If an OIV declares an incompatible game edition,
export fails. When the OIV has no edition declaration, these explicit mappings
are the author's assertion of compatibility; this is not native asset conversion.

For separate files or previously compiled managed packages:

```powershell
allin1-sdk build-edition-bundle --legacy "Legacy.oiv" --enhanced "Enhanced.oiv" --id "author.my-mod" --name "My Mod" --version "1.0.0" --output "MyMod-ALLIN1.zip"
```

Each input without --source-zip may be an OIV, a managed ZIP, a package folder,
or its mod.toml. Managed inputs must already have the same id, name and version
as the bundle, and declare exactly their own edition. Existing schema-1 through
schema-4 packages remain supported as variants.

The authoring API is
`allin1_sdk.edition_bundle.build_edition_bundle(output, legacy=..., enhanced=...,
mod_id=..., name=..., version=..., source_zip=...)`.
The agent API exposes `build-edition-bundle` as an `authoring_write` command,
using those parameter names. It does not enable game writes.

## What is converted

Supported OIV file additions and supported RPF-member additions are converted
to normal managed installation operations. OpenIV is not launched and arbitrary
OIV code is not executed. The output contains the generated plans and payloads,
not a runtime instruction to run the OIVs.

Recipes requiring XML/text/PSO compilation, nested archive preparation, archive
creation, deletion, or unsupported operations must first pass through the SDK's
appropriate OIV/RPF workbench workflow and be exported as managed packages.
Bundle export stops if either input cannot be translated; it never omits an
unsupported operation or publishes a partial bundle.

Only declared installation payloads, the content descriptor and validated SDK
artifact outputs are copied from managed packages. Unrelated project files,
source trees and original OIV extras are not swept into the release. The output
path must be a new ZIP outside GTA, in an existing directory. Publication uses
an atomic, non-overwriting hard link on the staging/output filesystem; a
filesystem without hard-link support fails without publishing a partial ZIP.

## Generated TOML

The builder computes the actual SHA-256 values; the placeholders below are
illustrative, not valid hashes.

```toml
schema_version = 5
id = "author.my-mod"
name = "My Mod"
version = "1.0.0"
type = "bundle"
editions = ["legacy", "enhanced"]

[variants.legacy]
manifest = "legacy/mod.toml"
sha256 = "<SHA-256 of legacy/mod.toml>"

[variants.enhanced]
manifest = "enhanced/mod.toml"
sha256 = "<SHA-256 of enhanced/mod.toml>"
```

Each variant manifest lives one directory below the bundle root and refers
only to payloads within its own directory. It must have the same identity as
the bundle, one matching edition, and checksums for every installation payload.
Variant trees cannot overlap. Nested bundles and undeclared extra mod.toml
files in an archive are rejected. The low-level schema also permits a bundle
with just one edition; attempting to install it on the other edition fails.

Schemas 1–4 remain the actual child install/receipt schemas. Bundles do not
merge file lists or let one edition satisfy another edition's dependencies.
Hashes establish local integrity, not publisher authenticity.

## Validate and install

```powershell
allin1-sdk validate-package "MyMod-ALLIN1.zip"
allin1-sdk validate-package "MyMod-ALLIN1.zip" --edition enhanced
allin1 content validate "MyMod-ALLIN1.zip" --edition legacy
```

Without --edition, validation checks the complete bundle and both payloads.
With --edition, it also resolves and reports that edition's installation plan.

In the launcher, select the game installation, import the generated ZIP, and
review the package. The inspector displays which variant was selected and its
file destinations. No game selection means no bundle installation review.
Installation still requires normal confirmation. Changed source files invalidate
the review, and missing/corrupt variants stop installation with no fallback.

CLI installation and SDK package lifecycle review/install also resolve the
edition through their selected game installation. Enable, disable, update and
uninstall operate through the existing per-game managed-package lifecycle.
