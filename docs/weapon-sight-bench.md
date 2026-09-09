# Offline sight bench (experimental)

In the Weapon Workbench select a weapon, then **Offline sights**. This source-level prototype displays real package geometry without starting GTA. It is a geometric inspection tool, **not a verified reconstruction of GTA's aiming camera or a ballistic zeroing tool**.

## Workflow

1. Select the exact body asset, decoder edition and LOD. Optionally select one declared component and its exact bundled asset. Press **Load sight model**. Base and `_hi` files remain distinct, explicit choices. Decoding reads the package and uses temporary files; it does not install or alter it. Missing/external components are not substituted.
2. Use **Side** and **Top** views to locate the sights. Expand **Sight landmarks & reference export**. Pick a visible mesh surface, then enter precise model-space coordinates for the rear aperture center and front-post tip. An aperture center is empty space, so surface picking alone cannot identify it.
3. **Align reference to marked sights** puts a geometric camera behind that line at the chosen eye relief. Alternatively enter a reference eye position, yaw, pitch, roll and vertical FOV to match a trusted reference image. This setup changes only the local view—not metadata. The initial camera merely faces model +X; no sight positions are inferred from the weapon name or bounds.
4. Switch to **Frozen aim**. Choose the existing LT, base-scope or attached-optic metadata family explicitly. The attached family is available only with an exact scope component loaded; it does not edit iron-sight fields. Change trial position/rotation with sliders or exact numeric inputs. Toggle **Show saved baseline camera** for immediate comparison. There is no sway, temporal jitter or live recoil.
5. **Review sight trial** uses the existing editable-copy metadata review, explicit confirmation, transactional save and undo. It does not bypass the workbench or automatically label a trial accepted. Read-only packages can use the view but cannot edit camera fields.
6. Export a reference JSON to retain the exact native/XML hashes, body and component paths, mount bones, drawable/LOD, component visibility, camera, metadata baseline/trial, landmarks and optional animation selection. The PNG exports untextured geometry only, without the SVG guides. JSON restore requires matching weapon/edition, body native/XML hashes, drawable/LOD and exact component identity/mounts. It restores the local reference, rebasing the camera relative to the newly saved metadata using the same hypothesis. It never imports or applies metadata changes.

## Camera mapping: explicit remaining work

The **reference** is a manually established model-space eye, not a runtime camera automatically derived from metadata. The trial visualizer applies changes relative to the currently saved baseline using this unqualified mapping:

| Metadata delta | Preview interpretation |
| --- | --- |
| Position X / Y / Z | Reference-camera right / forward / up |
| Rotation X / Y / Z | Pitch / roll / yaw in degrees |
| FOV | Independent reference vertical FOV; metadata FOV edits are not simulated |

This mapping needs controlled GTA validation for each supported aiming path/pose. Flags, game FOV conventions, aim IK, camera modifier stacking, recoil blending, lens effects and point of impact are **not reconstructed**. Choosing the wrong metadata family can produce a visually plausible preview with no corresponding runtime effect. The UI keeps this boundary visible. Existing [recorded calibration sessions](weapon-calibration.md) remain independent and retain their stronger evidence requirements; a rendered reference cannot masquerade as a captured, accepted game test.

## Motion inspection

Choose an exact bundled YCD, read its clips, then select one explicitly. Reading the inventory never auto-selects or samples its first clip. Unsupported animation layouts appear as unavailable choices with a reason; one unsupported clip does not hide other valid clips. Selecting an unsupported clip still fails rather than omitting its channels. The time slider seeks and freezes sampled animation frames. The view starts in bind pose; changing time enables sampled playback. One layer is evaluated at a time using the existing bone-tag/palette evaluator. Matched, missing and unsupported track counts are shown.

Matching tags do not prove that the selected dictionary/clip is the one GTA uses. Root motion, expressions, procedural effects, blends and hands are not reconstructed by this bench. An assembled component's bind pose follows the body's sampled mount bone; independent component animation and detached-magazine physics are not simulated. It is useful for spotting geometry intersections or unexpected bone displacement, not certifying animation compatibility. Static landmark guides stay fixed in model space while the model animates.

## Rendering and limits

- WebGL depth-buffered, double-sided, untextured diagnostic geometry with a fixed 960 × 540 perspective render. Geometry-based apertures are not painted over by a triangle-sort approximation. Material alpha cutouts and optic shaders are not reproduced.
- Mesh/skin buffers upload only when the sampled pose changes. Camera trials redraw the existing GPU buffers; they do not re-decode native assets or re-skin vertices.
- One explicit component is assembled through its declared parent mount and child attachment frame. A unique matching bone is required on each model. Body-only remains the default; this is not a full default-component loadout or a hand/IK reconstruction.
- Component visibility and per-mesh visibility are diagnostic controls. Shader labels help locate opaque lens geometry for manual hiding; there is no automatic lens detection, glass transparency or reticle simulation. Hiding geometry affects only the viewport/reference, never the package.
- The sight-specific high-detail profile permits 100,000 vertices, 150,000 triangles and 64 MiB decoded XML per model. Combined body/component geometry is capped at 120,000 vertices / 180,000 triangles. The existing 128-geometry and 512-bone per-model limits remain. Ordinary animation playback keeps its original 30,000-vertex / 40,000-triangle / 16-MiB limits. Clients cannot supply arbitrary budget increases. Oversized/unsupported selections report unavailable; no LOD fallback or silent mesh truncation is performed.
- Native source bytes (16 MiB per input) and metadata are checked again after decoding, including the component source and component definition. Stale revision, unrelated body/component paths and animation paths outside the package are rejected.
- Model/animation loads are explicit and cancellable. Late results are ignored after cancellation, selection changes or unmount. Loss of the graphics context reports an error rather than a successful empty view.

## API, CLI and automation

`allin1_sdk.weapon_sight.inspect(payload)` is read-only. The existing desktop `inspect_weapon_workbench` operation routes requests containing `sight_action` to the same handler. `inspect-weapon-sights --payload JSON` is registered as a read-only Agent API command.

```json
{
  "workspace": "C:/SDK/workspaces/example",
  "weapon": "WEAPON_EXAMPLE",
  "expected_revision": 0,
  "sight_action": "model",
  "entry": "dlc.rpf.source/stream/w_example.ydr",
  "edition": "enhanced",
  "lod": "High"
}
```

For read-only folders use `source` and `expected_revision: null`. To assemble one component add `component: "COMPONENT_EXAMPLE_SCOPE"` and its exact `component_entry` path from the weapon's `native_preview`; optional `component_drawable` disambiguates multi-drawable assets. For animation inventory use `sight_action: "animation"` and an exact bundled `.ycd` entry. Add its returned `selection` (for example `clip:12345678`) to sample a particular clip. No clip is inferred from its filename. The returned packet carries the exact native hash and source identity.

Use `summary_only: true` for Agent API reports: it performs the same native decoding and source validation but omits geometry/sample buffers, retaining hashes, dimensions/counts and selection identity. This avoids the Agent API's output-size cap. Summary packets explicitly cannot render a viewport; the desktop viewport or direct CLI/API request needs the full packet. Summary mode is not a cheaper validation shortcut.

`scripts/smoke_weapon_sights.py` exercises actual local copied packages without changing their bytes. `--high-detail` selects exact `_hi` assets, and repeatable `--component COMPONENT_NAME` adds exact owned-component cases. `scripts/smoke_weapon_sight_ui.mjs` bundles the read-only visual harness, runs Chromium, verifies nonempty geometry, attached-geometry visibility and exact baseline-frame restoration, and stores local screenshots/results. No retail assets are included in the repository or distribution by these scripts.
