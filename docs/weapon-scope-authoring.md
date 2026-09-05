# Weapon camera, scope and behavior authoring

The Tauri v2 Weapon Workbench exposes existing first-person camera fields in a
copied weapon workspace. It does not make the entire `weapons.meta` schema
editable and does not synthesize absent nodes or vector axes.

## Controls

- Scope and attached-scope position: X/Y/Z, metres.
- Scope and attached-scope rotation: X/Y/Z, degrees.
- Aim, scope and attached-scope FOV: degrees.
- Other first-person offsets: run-and-gun, aimed, and third-person-style idle,
  run-and-gun, aim, scope and blocked positions; run-and-gun/aimed rotation.
- Complete `WeaponFlags` token list, with toggles for the flags present in the
  source/draft. Unknown source flags are retained. Names are syntax-checked, not
  certified against a GTA edition-specific flag catalog.

There are 42 supported camera scalar/axis fields and one behavior-flags list.
Only fields actually present in the selected source are shown. Position inputs
are bounded to ±10 metres, rotations to ±360 degrees and FOV to 1–179 degrees.
Non-finite numbers and duplicate/malformed flags are rejected before saving.
Flags allow up to 256 names/8,192 characters. Untouched axes, attributes, unknown
XML, comments and other weapon records are preserved.

Edits use the existing revision- and SHA-bound review/confirmation transaction.
Review does not write files. Save reparses and revalidates the copied package;
undo restores exact prior bytes. An external edit invalidates a stale review.
No Launcher or game installation is required for metadata authoring.

## Calibration helper

Use a working, already-aligned reference sight, then measure the reference and
custom sight centres in **the same already-oriented weapon-local coordinate
frame**. Account for any component/bone transforms before entering coordinates.
Choose metres or millimetres for those anchor measurements.

The translation proposal is:

`new offset = current offset + (custom anchor − reference anchor)`

Optional magnification is relative to the selected target's current FOV:

`new FOV = 2 × atan(tan(current FOV / 2) / magnification)`

Angles are converted between degrees and radians internally. Both FOVs must use
the same axis/convention. This is a perspective estimate, not a guarantee of a
particular scope's optical behavior in GTA V.

The helper does not read model geometry, infer eye relief, align untransformed
component bones, solve rotations, or inspect a running game. It requires complete
coordinates and an explicit coordinate-frame acknowledgement. It shows the
proposal before a separate **Use proposal in draft** action; saving still needs
the ordinary review/confirmation. Applying a proposal clears the measurements
to prevent accidentally applying the same delta twice.

For example, a current Z offset of `-0.014 m`, reference sight at `10 mm`, and
custom sight at `42 mm` yield `0.018 m`. This arithmetic example is not a
measurement of the KRISS Vector model.

## CLI example

```powershell
allin1-sdk set-weapon-fields C:\Work\vector-workspace WEAPON_A1_KRISS_VECTOR --set weapon.firstPersonScopeOffset.z=0.0180 --expected-revision 0 --acknowledge-edit
```

The CLI shares the same existing-node validation and undo implementation.
For behavior flags use `weapon.weaponFlags`; for attached scope position use
`weapon.firstPersonScopeAttachmentOffset.x` (or `.y`/`.z`). Related rotation
fields use `firstPersonScopeRotationOffset` and
`firstPersonScopeAttachmentRotationOffset`.

## Private KRISS Vector test

The local, ignored `output/kriss-vector-test` directory contains an authoring
workspace, a read-only prepared source, provenance, a native DLC archive and a
ZIP test package. Third-party assets and extracted game donor data are not
checked into the repository or uploaded anywhere.

The package uses the SMG variant of Equinox407's KRISS Vector 1.1, a separately
namespaced weapon/ammo/model/magazine, eight stock SMG animation mappings and an
authored test shop entry. Scope Z is the user's requested `0.0180`. See its own
README for credits, permissions, dependencies and the manual test checklist.

The recursive RPF verification checks archive payload fidelity, not game runtime
compatibility. HUD icon aliasing, pickups, storefront visibility/unlocks,
persistence, animation fit, Enhanced conversion and optical alignment are not
validated by that check. The loose source currently retains an edition-warning
in the generic package inspector; provenance and native build reports explicitly
identify Legacy. Native model XML decoding succeeds but this drawable currently
has no supported position buffers in the SDK preview renderer.

Two additional SDK gaps were recorded during preparation, not silently bypassed
as completed features: complete-bundle cloning currently over-requires
projectile-style fields for ordinary bullet ammo, and exact root RPF extraction
can report suffix ambiguity against DLC patch entries. The private builder uses
native SMG metadata from `common.rpf` and records its direct metadata derivation.
