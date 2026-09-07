# Vehicle hitch profiles

Vehicles > Author > Hitches configures one front and one rear slot in a copied
vehicle authoring workspace. Add a slot, enter compatible trailer model names,
review, then confirm the save. Sliders also accept manual numeric input. Existing
workspace undo/redo includes these profiles; identity migration updates their
vehicle identifiers and compatible-model references.

Native towing requires existing `attach_female` and `attach_male` bones. Native
offsets and rotations must be zero. The slot label does not relocate a native
hitch. For custom placement, physical mode accepts a vehicle bone, bone-local
offset, trailer coupler bone/offset and rotation. This mode is experimental:
the SDK validates the configuration, not live physics or bone availability.
The offset preview is a schematic, not a decoded vehicle or collision preview.
This feature does not create or modify YFT bones.

## Shared schema

The workspace stores profiles under `hitch_configurations`, keyed by vehicle
model. Vehicle package export embeds each listed vehicle's profile in its
`payload/vehicles.json` entry as `hitches`, and retains the profiles in
`payload/vehicle-profiles.json`. An explicit catalog override does not remove a
reviewed workspace profile. An explicit empty `points` list disables hitches;
an absent profile permits the consuming runtime's native hitch detection.

```json
{
  "schema_version": 1,
  "vehicle_model": "exampletruck",
  "points": [{
    "id": "rear",
    "mode": "native",
    "bone": "attach_female",
    "position": [0, 0, 0],
    "rotation": [0, 0, 0],
    "coupler_bone": "attach_male",
    "coupler_offset": [0, 0, 0],
    "compatible_models": ["trailers"],
    "connect_distance": 1,
    "break_force": 10000
  }]
}
```

Identifiers must be lowercase, 1–64 characters (`a-z`, `0-9`, `_`, `-`).
Unknown fields, duplicate slots/models, self-coupling and wildcards are rejected.
Compatible models require 1–64 entries per slot. Numbers must be finite:
vehicle offsets ±20 m, coupler offsets ±5 m, rotation ±180 degrees, coupling
distance 0.25–2 m and physical break force 1,000–100,000. Offsets follow the
selected bone's local axes, not world axes. Only one native slot is permitted.

## CLI, API and agents

Use the existing `vehicle_hitches` authoring module through all three transports:

1. `inspect-authoring-workspace`: supply `module`, `workspace`, and `model`.
2. `review-authoring-action`: add `action: "configure"`, the profile `document`,
   and the returned `expected_revision` and `expected_state_sha256`.
3. `apply-authoring-action`: resubmit that exact request plus `review_sha256`
   with `--acknowledge-authoring` for CLI/agent execution. The Python API uses
   `authoring_confirmed: true`.

CLI requests use `--request-json`. Agent commands expose `request_json` and
`acknowledge_authoring`. Python callers use `automation.inspect_authoring`,
`review_authoring`, and `apply_authoring`. The common implementation verifies
the reviewed content identity before saving. Export and save do not install
into the game.

Live acceptance remains pending in Legacy and Enhanced. Test native rear
towing first, then custom/front physical joints separately, including turning,
braking, uneven ground, disconnection, and script shutdown.
