# Multi-Axle Prefab Library

The Vehicle Workbench keeps axle behavior and tyre appearance as two separate
choices. A behavior prefab controls steering, drive, tag/pusher roles, and lift
intent. A visual tyre package controls singles, duals, super-singles, and
bone-bound inner-wheel geometry. Neither catalog moves, rotates, renames, or
reparents a vehicle bone.

## Built-in behavior catalog

`assets/axle-prefabs.json` is a versioned, localized catalog containing 27
immutable layouts. It covers two through five canonical physical axle pairs and
includes a separate trailer category. Pattern tokens are labels only:

| Token | Meaning |
|---|---|
| `S` | Steered, unpowered |
| `D` | Powered, fixed steering |
| `SD` | Steered and powered |
| `T` | Fixed tag/carrier |
| `RS` | Rear-steered, unpowered |
| `LT` | Liftable fixed axle |
| `LS` | Liftable steered axle |

Runtime behavior always comes from each axle's explicit Boolean and role
fields. No prefab stores runtime wheel indices.

After choosing a behavior, **Calculate steering** can derive signed gains from
the canonical wheel-bone positions. Fixed axles define the neutral pivot; an
all-steer layout must provide an explicit vehicle-local pivot and is never
guessed. The solver uses a bounded center-line geometry estimate: positive is
same-phase, negative is counter-phase, and zero is fixed. The longest steering
lever arm is normalized to `1.0`. This is steering geometry, not a dynamic
understeer/oversteer tyre model.

**Steering polarity** is a separate vehicle-level config choice. `normal`
keeps each base gain in phase; `inverted` multiplies every base gain by `-1`
exactly once at runtime. It does not reorder axles or rewrite the saved gains.
Inverted configs use axle schema 4 and runtime 4.0.0 or newer.

The canonical pair map is:

- Two axles: `wheel_lf/rf`, `wheel_lr/rr`
- Three axles: add `wheel_lm1/rm1`
- Four axles: add `wheel_lm1/rm1` and `wheel_lm2/rm2`
- Five axles: add all three middle pairs through `wheel_lm3/rm3`

The target resolver builds the index map from these bone semantics and validates
it against the game-reported wheel count. Visual dual tyres never enter that
map.

## Visual tyre catalog

`assets/visual-tyre-packages.json` contains eight composable packages. Apply a
behavior prefab first, then select a tyre package. Changing tyre appearance does
not change steering, power, braking, or runtime wheel indices.

The selected package is persisted in the axle JSON as the optional,
backward-compatible `visual_tyre_package` extension. It records a versioned
package ID, selected physical axle orders, and bounded scalar parameters. This
means `Singles All Around` survives save/reload even though it adds no geometry.
The ordinary `AxleConfiguration` parser can still read the behavior portion;
`load_prefab_axle_configuration()` preserves both behavior and visual intent.

GTA provides one front wheel-template family and one shared middle/rear family.
When one shared-family axle needs duals and another needs singles, ALLIN1 keeps
the outer template single and uses a verified ordinary inner wheel bound rigidly
to the affected wheel bone. The generated inner object has **Is Wheel Mesh
disabled**, so the last axle does not receive an unwanted copy.

Dual packages do not invent asset names. Without verified source geometry they
are explicitly **Design Only**: the choice can be saved, but it cannot be
confirmed as applied and no placeholder YDR is emitted. A caller enabling real
geometry supplies `VisualGeometryAsset` records containing an existing,
non-symlink source file and its safe package-relative YDR/YDD/YFT identity. The
portable identity is preserved with the selection. `Axle-Specific Inner Wheel`
also requires at least one explicit axle order; an empty selection is rejected
and leaves the previous configuration unchanged.

## Python API

```python
from allin1_sdk.axle_configurator import EXPORT_FIVEM_RUNTIME
from allin1_sdk.axle_prefabs import (
    AxlePrefabCatalog,
    VisualTyreCatalog,
    apply_prefab,
    apply_visual_package,
    confirm_prefab_application,
)

prefabs = AxlePrefabCatalog.load_builtin(project_root)
tyres = VisualTyreCatalog.load_builtin(project_root)

preview = apply_prefab(
    "6x2_rear_steer_bus",
    vehicle_model,
    skeleton_bones,
    "fivem-legacy",
    EXPORT_FIVEM_RUNTIME,
    catalog=prefabs,
    reported_wheel_count=6,
)

if preview.can_apply:
    config = confirm_prefab_application(preview, confirmed=True)
    visual_preview = apply_visual_package("mixed_tag", config, catalog=tyres)
```

For automatic geometry, call
`solve_automatic_steering_geometry(config, skeleton_bones)` and then
`apply_steering_geometry_to_configuration(config, solution)`. The latter
promotes only nonlegacy gains to schema 2 and records a digest of canonical
bone positions plus the pivot/reference evidence. The console and typed Agent
API expose the same read-only proposal through `preview-axle-steering`; saving
still uses the acknowledged `set-vehicle-axles` command.

The selected-axle editor can also author an experimental relative support
weight from 0.75 through 1.25. Enabling it seeds every physical axle at 1.0;
partial arrays are rejected. This promotes the configuration to schema 3 with
minimum runtime 3.0.0 and emits each runtime row as
`suspension: { "supportWeight": number }`. Production export stays disabled
until the chosen target explicitly advertises a validated support-bias
accessor; saving the authoring draft does not claim runtime compatibility.

If `visual_preview.design_only` is true, use
`persist_visual_design(..., confirmed=True)` only to record the design intent.
Use `confirm_visual_package()` only when `can_apply` is true and verified
geometry is available.

`PrefabApplicationPreview.to_dict()` exposes the previous/proposed
configuration, bone and runtime mapping, changed fields, handling flag result,
capability status, warnings, and validation findings. UI cards can use
`schematic_text(prefab)` for a color-independent accessible diagram.

## Custom project prefabs

Use `create_custom_prefab()` to derive a project-only layout. It records the
built-in base ID and only the user's axle overrides, while also serializing the
fully resolved result. `ProjectPrefabCatalog` writes these custom entries to a
separate schema, so an SDK update cannot overwrite them. Built-in dataclasses
are frozen and catalog validation rejects duplicate IDs.

## Adding a built-in prefab

1. Add a localized name and common-use string to the catalog's `localization`
   object.
2. Add a prefab with a unique lowercase ID, 2–5 explicit axle rows, and a
   display pattern whose tokens exactly match those rows.
3. Use a nominal layout whose first number is the physical wheel-slot count and
   whose second number is twice the number of powered axles. Trailer entries use
   `trailer`.
4. Declare the capabilities calculated from the exact axle state:
   `selectiveSteering`, `selectiveDrive`, `liftAxle`, and/or
   `trailerSteering`.
5. Add tags and a localized common-use description.
6. Run `tests/test_axle_prefabs.py`. Strict loading rejects mismatched patterns,
   drive notation, capabilities, localization, IDs, and axle order.
7. Do not mark a runtime accepted until its exact mapping, steering, drive, and
   visual behavior have passed an in-game target fixture.

## Compatibility and remaining limits

- Ordinary stock-compatible layouts receive the **Stock** badge.
- Selective wheel behavior is labeled **FiveM Runtime** or **Story ASI** from
  the shared target capability registry.
- A runtime with available but unpublished acceptance remains
  **Experimental**; capability presence alone is not claimed as support.
- Lift intent can be authored, but remains **Design Only / Lift Runtime** until
  suspension animation support exists.
- Trailer steering is experimental until target-specific mapping tests pass.
- A skeleton missing a required canonical pair, with invalid physical order, or
  with a game-reported wheel count mismatch is blocked before application.
- Axles beyond the five canonical GTA pairs require cosmetic wheels or a future
  custom-physics extension.
- Dual tyres are always visual additions to an existing physical wheel slot.
- Dual and inner-wheel packages remain design-only until their actual geometry
  sources are verified; ALLIN1 never emits a fabricated YDR reference.

Runnable acceptance data lives in `examples/axle-prefabs/` for a three-axle
rear-steer bus, a four-axle twin-steer truck, and a five-axle crane.
