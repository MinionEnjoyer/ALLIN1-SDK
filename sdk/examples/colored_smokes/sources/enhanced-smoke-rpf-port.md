# Enhanced smoke RPF port

Source investigated: [Realistic Explosions, Teargas, Flare, Water Hydrant And
More 1.1](https://www.gta5-mods.com/misc/better-teargas-better-explosions-explosion-ymt-meta-1-0).
The downloaded RAR is not itself an RPF. It contains complete 2015 replacements
for these two entries in `update/update.rpf`:

- `x64/data/metadata/explosion.ymt`
- `common/data/effects/explosionfx.dat`

## Archived payload audit

| File | Variant | Bytes | SHA-256 |
|---|---:|---:|---|
| `explosion.ymt` | original | 49,423 | `574cec71502ebaf9c6c0762ff208ab4c8392182100713634b07ad16a22dbb4ac` |
| `explosion.ymt` | mod | 48,148 | `d001ca779ca25334945336681fbec1ecfa0a341c5a9ae83d1d1bcb5ae814797c` |
| `explosionfx.dat` | original | 7,185 | `7892b6161a035e73a9c47947e127d7c2de98a5dce17887b04d99ac8055065713` |
| `explosionfx.dat` | mod | 7,313 | `f10954698db7beb126e08ddc0f170b50b35976dd65ba5c19a2a910098bb62fa6` |

The mod changes every explosion record, raises a global VFX value from 5 to 12,
and documents several damage/force bugs. ALLIN1 must never install those full
files. The smoke-specific source changes were:

- BZ gas radius `3 -> 9`
- BZ gas lifetime `25 -> 40`
- BZ gas no-occlusion `false -> true`
- BZ gas VFX scale `1.0 -> 2.0`
- BZ gas damage `0.15/0.075 -> 0.3/0.1`
- tiny BZ gas force/ragdoll values
- separate changes to native smoke grenade and smoke-launcher records

## Current-game compatibility findings

The inspected GTA V Enhanced archive stores `explosion.ymt` as a 7,336-byte
compiled PSO with 36 records and eight fields per record that the old XML does
not contain. Stock and `mods` copies matched before this port:

- `explosion.ymt`: `c18c24b3bf23a009ffb77befd99f5ff65874407979d729d03b912ae851117166`
- `explosionfx.dat`: `80c10442882140599f731ef7252fdfc6fc4426422451f1b241754e1942517d63`

CodeWalker's XML-to-PSO builder drops the newer fields, so ALLIN1 does not
rebuild the PSO. `RpfPatcher` discovers the current schema offsets and writes
only the audited bytes in the existing `EXP_TAG_SMOKEGRENADE` record. A safety check
rejects any output that changes file length or unrelated content.

## ALLIN1 derivative tuning

Enhanced ships `WEAPON_SMOKEGRENADE` and `WEAPON_BZGAS` as separate weapons.
ALLIN1 uses only the former as its custom-smoke carrier, leaving native Tear
Gas untouched. The smoke-grenade record receives:

- damage centre/edge: `0 / 0`
- radius: `9`
- lifetime: `40`
- force, ragdoll force, self force: `0`
- continuous damage: `false`
- no occlusion: `true`
- smoke-grenade VFX scale: `2.0`

Native `EXP_TAG_BZGAS`, `EXP_TAG_SMOKEGRENADELAUNCHER`, and
`EXP_VFXTAG_BZGAS` remain byte-identical. Runtime looped-particle tinting adds
selectable player colours and dedicated orange CASEVAC smoke without changing
the Tear Gas effect.

The runtime field uses two bounded particle backends. Three persistent
`core/exp_grd_bzgas_smoke` columns provide the long-lived body of the cloud.
During the initial 4.5-second bloom, four colour-tinted
`scr_carsteal4/scr_carsteal4_wheel_burnout` particles are emitted every 140 ms;
the supplemental layer then settles to two particles every 360 ms. This pattern
is derived from the particle technique published with Stunt Plane Smoke and is
rate-limited, capped at eight active fields, and sampled in telemetry rather
than logged on every emission. Non-white colours now bloom at scale `2.90` and
settle at `2.20`, exactly twice the white layer's `1.45`/`1.10`, while all
colours spread over a `2.15-3.05` metre emitter radius so the result is
visually comparable to the gray primary cloud without increasing emitter count
or cadence. If the primary asset loads late, the controller keeps retrying it
instead of permanently falling back after its first request.

## M18 carrier audits

The Enhanced `patchday8ng` weapon archive already contains
`w_ex_grenadesmoke.ydr` and `w_ex_grenadesmoke_hi.ydr`, with textures named
`w_ex_m18_s`, `w_ex_m18_s_s`, and `w_ex_m18_s_n`. The stock smoke-grenade
carrier is therefore already an M18-shaped canister.

The later-audited
[`[INS2] M18 Smoke Grenade`](https://www.gta5-mods.com/weapons/ins2-m18-smoke-grenade)
package contains two YDR models and two YTD texture dictionaries. All four
resources have the Legacy `RSC7` header; they are not Enhanced-ready assets.
Its supplied `weapons.meta` is also a complete replacement that keeps the
native `WEAPON_SMOKEGRENADE`, `SLOT_SMOKEGRENADE`, and
`AMMO_SMOKEGRENADE` identities. It therefore replaces the native carrier
rather than defining independently selectable colors. ALLIN1 does not install
the raw package: the YDRs require a verified Gen9 conversion path and the full
metadata replacement would undo current-build fields and affect the native
smoke grenade globally. Native Tear Gas keeps its own weapon behavior and
appearance.

## Add-on weapon architecture

A registered DLC pack is the standard way to define non-replacing weapons.
The separate `allin1_smoke` artifact follows that architecture: it lives below
`mods/update/x64/dlcpacks`, contains `content.xml` and `setup2.xml`, registers
its definitions as `WEAPONINFO_FILE`, registers a matching
`shop_weapon.meta` as `WEAPON_SHOP_INFO_METADATA_FILE`, and is added to
`dlclist.xml`. The second registration is required for the weapons to appear
in `GET_DLC_WEAPON_DATA`; `IS_WEAPON_VALID` by itself only proves that a hash
resolved to a loaded `CWeaponInfo` definition.

[`AddonWeapons`](https://www.gta5-mods.com/scripts/addonweapons) is a discovery
and shop script for weapons that an add-on DLC has already loaded; it is not
the loader for the DLC metadata itself. ALLIN1 does not install that script
because GBAY owns purchasing, per-character inventory, and savegame-gated
persistence. Running both purchase systems would create a second inventory
that can save outside the GBAY transaction policy.

The page links the public
[`sruckstar/AddonWeapons2`](https://github.com/sruckstar/AddonWeapons2)
repository. Commit `39da663330c4ae2b338cc22da3ae37458d0bf76d` is titled
`Release of Version 3.0`. Its useful runtime contracts were independently
reimplemented rather than copying its menu or persistence code:

- enumerate the loaded add-on catalog with `GET_NUM_DLC_WEAPONS` and
  `GET_DLC_WEAPON_DATA` instead of treating `IS_WEAPON_VALID` as proof that a
  weapon is registered for normal add-on handling;
- give a newly acquired thrown weapon one loaded round, then reconcile its
  desired ammo pool and select it explicitly;
- verify ownership, ammunition, and current selection after the grant.

Runtime availability now requires both a valid `CWeaponInfo` hash and presence
in GTA's enumerated DLC weapon catalog. A hash that passes `IS_WEAPON_VALID`
but is absent from `GET_DLC_WEAPON_DATA` is quarantined: ALLIN1 removes that
known custom hash from the live ped, refuses new GBAY purchases, and preserves
previously paid color stock for recovery after a corrected pack is installed.
Unrelated native weapons are not touched. Invalid entries already present in
ALLIN1's per-character purchased-weapon list are removed with their ammo and
customization records, staged in memory, and made permanent only by the next
Story Mode save.

The repository contains no license file, so ALLIN1 treats it as behavioral
reference material and does not redistribute its source or binaries.

The companion AddonWeapons Builder 1.2 is useful as a packaging reference, but
its author documents that packed RPF output currently supports Legacy only.
On Enhanced it requires loose output plus Easy Mod Loader. ALLIN1 instead keeps
its Enhanced-aware OpenRPF/CodeWalker pipeline and validates generated RPF7
archives itself. Both the original `allin1_smoke` DLC and a replacement that
mirrored the current Rockstar `shop_weapon.meta` contract produced repeatable
Story Mode startup hangs on Enhanced. In the latter test ScriptHookV completed
initialization, but ScriptHookVDotNet and every ALLIN1 script remained unloaded;
this isolates the failure to native extra-content mounting. The pack is again
quarantined and removed from `dlclist.xml`.

The active canary therefore uses the boot-tested base `CWeaponInfoBlob` merge.
GBAY is the only purchase system, so presence in `GET_DLC_WEAPON_DATA` is kept
as diagnostic telemetry rather than an availability requirement. The engine's
`IS_WEAPON_VALID` result, live ownership, ammo, and selection are verified
directly. This does not expose the colors through Ammu-Nation.

Weapon definitions alone are insufficient for a new hash. The first equip
test exposed a full-body T-pose because `weaponanimations.meta` keyed its
throwable mappings only to `WEAPON_SMOKEGRENADE`. The merge now clones that
stock item for every color in all six sets where it occurs: `Default`,
`FirstPerson`, `FirstPersonAiming`, `FirstPersonRNG`, `Female`, and
`GangFemale`. Validation normalizes every cloned key back to the native hash
and requires the remainder of the item to be XML-identical. The installed
seven-color payload therefore contains exactly 42 appended animation mappings.
The merge also appends seven hashes to the already-loaded American
`global.gxt2`, which gives the throwables real native weapon-wheel names. GTA's
weapon wheel selects artwork from `INT<signed weapon hash>` frame labels inside
`hud.gfx`; unknown add-on hashes fall back to the C4 image. The installer adds
each custom hash as an alias on both stock BZ Gas HUD frames while preserving
the native BZ Gas labels and every other byte in `hud.gfx`. The two metadata
files, language archive, and Scaleform archive have exact-entry backups and are
also covered by the full pre-merge `update.rpf` rollback snapshot.

GBAY presents seven five-pack products with independent per-character stock:
white, red, orange, yellow, green, blue, and purple. Installation generates a
current-build base-metadata merge from the installed `WEAPON_SMOKEGRENADE`
record. Each colour has its own `WEAPON_ALLIN1_SMOKE_*` weapon hash,
`AMMO_ALLIN1_SMOKE_*` ammo pool, weapon-wheel label, and native ammo count.
Both native ammo and ALLIN1's per-character stock are capped at five per colour.
Colours are selected through GTA's normal throwable slot; Reload is not
intercepted and ALLIN1 does not draw over the weapon wheel.

The generated ammo definitions retain the M18 model and throwing behavior but
remove `AddSmokeOnExplosion`, the stock trail/primed effects, and the native
smoke explosion. Their fuse is extended to 30 seconds so the physical canister
can land. Runtime smoke activates only after at least 300 ms of consecutive
grounded, collided, low-speed stability. This prevents an untinted native plume
from mixing with the selected colour and prevents a timeout from deploying
smoke while the grenade is still airborne. A throw consumes one unit only from
the matching color's inventory pool.
Purchases, selection, and consumption are staged in the character inventory
and become permanent only when the Story Mode save is written. Script fallback
activation locks the final settled position, starts the smoke field, and then
deletes the physical projectile. Retiring that entity prevents GTA's rolling
canister sound loop from persisting under an active smoke cloud.

Install and removal are transactional. The tool saves exact originals for
`weapons.meta`, `weaponanimations.meta`, `american_rel.rpf`, and
`scaleform_generic.rpf` under `scripts/ALLIN1_backups/smoke_weapon_merge`,
verifies all four live entries after the write, and writes a hash-complete
schema-5 marker. Removal restores those exact entries. A complete pre-merge
`update.rpf` snapshot is reserved for emergency rollback if an archive write
fails.

The separate `allin1_smoke` DLC remains quarantined and unregistered because it
caused Enhanced Story Mode startup hangs. Native `WEAPON_BZGAS` and its Tear
Gas ammo, effect, damage, and text label are never cloned or patched; only its
existing HUD icon mapping is reused.
