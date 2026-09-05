# Scope attachment refinement — 2026-09-04

> Historical fixture study: the Vector values and installation/build observations
> below are not universal defaults or current release acceptance. Use the
> [scope authoring contract](weapon-scope-authoring.md) for current tool behavior.

The user's Vector iron sights are aligned. Do not change their base aim offset:
`FirstPersonScopeOffset = (0, 0, 0.0180)` or the working base rotation.

The React camera editor now defaults manual calibration to attached scopes.
Its separate model-based calculator reads bounded CodeWalker YDR XML, resolves
full parent/rotation transforms, rejects conflicting duplicate bones, cycles,
missing parents, non-unit scales, differing grip frames and differing mount
rotations. Coincident parent/child aliases are accepted. Files remain local.
It stages only `weapon.firstPersonScopeAttachmentOffset.z`; saving still uses
the existing Python-owned review/confirmation/revision/undo flow.

For the **same optic geometry and aiming animation in a shared Z-up frame**:

    new attached Z = aligned reference attached Z + reference mount Z − custom mount Z

This compensates a lower sight by raising the weapon. The automatic calculation
uses a fixed reference value, not a repeatedly adjusted draft. The manual
translation calculator's sign was corrected to the same compensation direction.
Neither method infers an optical centre from a model bounding box. A different
custom optic still needs optical-centre/axis evidence; this is not a universal
automatic eye-relief, rotation, or optical-ray solver.

## Local Vector evidence

The private Vector scripts and assets named below belong to an independent mod
experiment. They are not distributed in the public SDK repository or package.

`tools/audit_kriss_scope.mjs` now uses the newer `patchday8ng` stock SMG,
which actually has the `WAPScop_2` named in donor metadata. Its model-frame
mount Z is 0.06884314 m; the Vector is 0.02013618 m. From donor attached Z
−0.028 m, the revised candidate is **+0.02071 m**. Scope X/Y, FOV, rotations
and iron-sight fields stay unchanged. The former 0.3.0 estimate (+0.02477)
used the older x64e SMG's WAPScop and is superseded, not silently overwritten.
Matching mount names improves the evidence but does not prove in-game aiming.

`tools/build_kriss_vector_refinement.py` creates a new private 0.3.0 candidate,
adds the stock `COMPONENT_AT_PI_SUPP` to the actual WAPSupp mount, and populates
native shop rows for the magazine, scope and suppressor. Recursive RPF validation
proves preservation of all model, texture, animation and unrelated file bytes.
It includes the hash-paired GBAY runtime beside, not inside, the mod ZIP.
The builder does not install, publish, or modify the existing managed 0.2.0 copy.

## Conditional iron-sight visibility — 0.4.0 test candidate

`tools/audit_kriss_sight_mesh.py` welds positions/skin influences for read-only
topology inspection. Manually inspected islands 48–51 and 58–62 are the complete
front/rear assemblies (1,842 triangles); sling hardware 44/45 stays. Selection is
bound to exact source XML SHA-256s, never applied as a height-based mesh cut.

`tools/kriss_sight_component.py` removes just those draw indices from both main
and hi models. All main-model vertex buffers, skeletons, shaders and unrelated
triangles are retained. The sights become `w_at_a1_krissvector_irons` / `_hi`,
reusing the gun's texture dictionary. Inverse mount transforms reconstruct their
original rest position; native re-import/export checks vertices, indices, bone
maps, skeleton and shaders. No new animation or whole-weapon swap is used.

`COMPONENT_A1_KRISS_VECTOR_IRONS` is a default **CWeaponComponentInfo**, not a
scope-info class, sharing WAPScop_2 with the optional scope. Its AAPScop root uses
the stock optic schema. It has no camera/zoom/stat modifiers and a zero-cost
native shop entry. Scope equipped: sight component replaced. Scope removed:
default sights restored. This is implemented for testing, not yet gameplay-proven.

GBAY reads native `bActiveByDefault` metadata (no Vector hash hard-coding or
English-label matching) to make defaults owned/free and non-removable. Removing
an optional component restores its unambiguous default only if the slot is empty;
inventory restoration also repairs empty default sight slots. Other active
components are never replaced. Native reads are bounded and cached.

Build with `--separate-sights --scope-z 0.02071` to produce the private 0.4.0
candidate. Its recursive RPF validation verifies 19 payloads across 3 archives
(11 byte-exact, 8 canonical resources). Native-readback comparison is at
`output/kriss-vector-allin1-0.4.0-candidate/model-evidence/sight-states.png`.

The first managed installation attempt stopped at the initial closed-game guard
before backup creation or any game-file writes. After the user closed GTA,
`.work/install_kriss_optics.py` in ALLIN1 completed the managed update to 0.4.0
and installed the hash-paired GBAY runtime. **Do not rerun this one-shot update.**
The receipt is enabled and still owns `a1_krissvector`; its GBAY weapon catalog
is registered. All unrelated stored root payloads were preserved and the DLC
registration list/order is unchanged. No stock archive or Story save was changed.
The standalone SDK installation remains unchanged.

External verified backup:
`D:/ALLIN1-SDK-Backups/kriss-vector-optics/20260904_085209_413107`.
Installed DLC SHA-256:
`6129db9d1692e61e1fcea4076fd9b4bb67989cc6ec91f498299942adb3fb7e76`.
Core: `66e1b25d38623888a399d45a3f8d3a3ee50771375d19cff8676fe6a7eef88478`.
Bridge: `963464ad7f093adf3b2026da917594e4a6a4c2b22183b07e27aeecee5741f840`.
Read-back evidence: `output/kriss-vector-allin1-0.4.0-candidate/installed.json`.

In-game acceptance still required: original unscoped sight alignment, visible
sights → scope with no sights → unequip restoring sights, holster/re-equip,
Story save/reload, suppressor fit/removal, GBAY SMG sorting and absence of fake
tints. If unscoped aiming changes, stop and restore the backed-up 0.2.0 package;
do not compensate by changing the user's working base camera values.

## Verification

The current pass has 1,044 passing runtime tests and 107 focused Python tests.
The runtime package-manager suite also passed 80 tests (3 skipped); pytest
reported an existing cache-write permission warning, not a test failure.
The preceding frontend pass had 74 passing tests (no React changes this turn), including
scope-only staging, unsafe skeleton rejection, sorting and ownership-preserving
attachment removal. Candidate metadata tests separately protect iron sights,
scope X/Y and fire rate. Browser preview checked at 1280×720 in light and dark.
Actual scope alignment, suppressor fit, attachment removal after Story saves,
and conditional sight visibility still require in-game acceptance.

The production Tauri/NSIS bundle builds successfully. No SDK installation was
performed in this pass. A dev-watcher regression surfaced during native build:
the old glob exclusion still traversed src-tauri and hit EBUSY on a locked EXE.
The exclusion now matches the directory itself and either slash style with a
regular expression. `tools/verify_desktop_watcher.mjs` confirms the real Vite
watcher still watches frontend files and excludes the entire Rust tree.
