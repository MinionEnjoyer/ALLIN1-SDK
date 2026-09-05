# Weapon shop and animation authoring in Tauri v2

In Content Workbench → Weapons, open an unpacked weapon folder or an editable
copy. The **Weapon section** selector separates weapon/ammo fields, GTA shop
metadata, and animation mappings. These operations do not install or publish
content and never write to GTA V.

## GTA shop metadata

Select the exact discovered shop source when a weapon appears in multiple files.
Read-only inspection exposes the same values as an editable copy. Existing
`cost`, `ammoCost`, `textLabel`, `weaponDesc`, `weaponTT`, `weaponUppercase`, and
`availableInSP` fields can be edited in the copy. Weapon identities are locked;
missing records and missing schema fields are not synthesized. Ambiguous scalar
representations, duplicate fields, and ambiguous records are refused.

Prices are whole numbers from 0 through 2147483647. Availability is a boolean;
text fields are localization identifiers, not a GXT2 text editor. Python retains
the original text/ref/value representation and unrelated XML. Every change has
a before/after review and a separate confirmation. Undo restores exact previous
file bytes and reselects the shop source.

**These are GTA shop metadata fields, not ALLIN1 GBAY catalog fields.** Editing
them does not change a GBAY listing or its prices. GBAY catalog authoring/export
is still a separate workflow.

## Animation mappings

The animation section lists the selected weapon's discovered set coverage and
source paths. For an unmapped weapon, choose a mapped template from this package
and an exact source. A template can be an animation-only identity; it does not
need its own weapon definition in the package.

The review lists every mapping that will be added. Python copies each complete
mapping, including heterogeneous per-set clip references, flags, and other
properties. Only the target weapon key is changed. The target must have no
existing mappings anywhere in the package; this action never overwrites or
merges an existing set. Duplicate donor mappings within a set are rejected.

This is reference-preserving mapping cloning, **not custom animation generation,
animation asset conversion, or proof of in-game compatibility**. It does not
create custom reloads. Undo removes added mappings and restores exact bytes,
leaving the donor intact and the target selected.

## Safety and verification

The desktop reuses the Tkinter authoring domain. Reviews parse the same changes
as saves, without writes. Review digests bind the workspace revision, all source
bytes, exact selected source, target, template, and normalized changes. Save
regenerates the review and rechecks it under the workspace lock. Incomplete
oversized review evidence is refused rather than silently truncated.

React locks selection/navigation while drafts or reviews are pending, retains
drafts on rejected saves, and requires a fresh confirmation after failure.
Source inspection remains read-only. Original package files and game files are
outside the authoring transaction.

Regression coverage lives in `tests/test_weapon_distribution_desktop.py`,
`tests/test_weapon_authoring_core.py`, `desktop/src/WeaponDistribution.test.tsx`,
and the packaged `scripts/smoke_desktop_sidecar.py` workflow.
