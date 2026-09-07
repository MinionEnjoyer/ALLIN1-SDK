# Weapon Calibration & Testing

Source implementation, not a released or in-game-qualified feature. Open an editable weapon copy and choose **Calibration & Testing** beside Authoring. This first increment records imported sight-test evidence; it does not launch, control, inject into, or capture GTA.

## Record and compare

1. Select the weapon and edition. Iron and scope sessions are separate. Mark the intended aim point, visible sight position, and optional impact group in one fixed-camera PNG/JPEG frame. Coordinates use original image pixels even when the display thumbnail is smaller. The selected source hash is checked at review and save. EXIF-rotated images must be normalized before marking.
2. Enter the actual reported capture time with timezone, camera state, FOV and its known/unknown axis, target distance, pose and equipped components. No attachment is assumed equipped because metadata makes it the default.
3. Run the same aim/fire/reload/camera-switch/attachment-stability checklist. Record pass/fail/not-tested, movement notes, and optionally selected frame-time samples in milliseconds. These are operator observations, not SDK-certified outcomes or automatic telemetry.
4. Optionally select the exact SDK artifact JSON, Launcher receipt, GTA folder and runtime-session JSON. Existing diagnostic-trail checks hash installed files and compare artifact/build identities. They run again when recording is confirmed. Conflicting bytes invalidate an outstanding review. Missing evidence remains unresolved.
5. Review and explicitly confirm. The SDK copies the image and writes a checksum-sealed session under `<workspace>/.weapon-calibration/<session-id>/`. Sessions are never overwritten. All checklist checks must pass to designate an accepted reference. Records retain their own profile and full saved weapon values.
6. Load sessions and explicitly choose an accepted reference (or controlled baseline) and a trial. Comparisons require the same weapon, edition, profile, resolution, camera/FOV, distance, pose and equipped attachments. Visual displacement, impact-centroid displacement, checklist changes and maximum frame time remain separate. Missing impact data is not zero error. Frame-time differences do not establish causality.

The local records contain capture notes and supplied diagnostic identities; inspect them before sharing. Checksums establish content consistency, not signatures, authorship or authenticity. Keep the workspace's calibration folder with its screenshots when backing up. Up to 256 stored entries, 64 impacts/image, 1000 frame-time samples, 16 MiB/image and 32 million pixels are supported. Package comparison is bounded to 4096 files / 2 GiB.

## From evidence to a reviewable edit

Record a baseline. Use the existing camera editor to make one small trial change, build/install/test it, then record the trial. Select the exact changed field and the measured screen axis; explicitly confirm that these were controlled tests of those saved workspaces.

The proposal checks that package content other than the selected weapon's camera values is unchanged, and that exactly one existing value changed. Current workspace bytes must still match the trial. Iron profiles can propose existing `FirstPersonLTOffset` / `FirstPersonLTRotationOffset` axes; scope profiles can propose existing scope/attached-scope position and rotation axes. FOV, model bones, flags and the other sight profile are not changed by a calibration proposal.

For the selected screen axis, with visual errors `e0` and `e1` and a known field step `d`, the empirical candidate is `current - e1*d/(e1-e0)`. It is an **unproven local visual hypothesis**, not a geometric reconstruction or ballistic correction. Responses smaller than two source pixels are rejected. The candidate correction cannot exceed the measured field step, 0.01 metres for position, or one degree for rotation; invalid native bounds and rounded-zero changes are rejected. An accepted current trial cannot be tuned directly. Other axes, animation, parallax, lens effects, recoil and runtime behavior still require retesting.

The candidate goes through the existing weapon change review with exact before/after values, baseline/trial evidence hashes, action-time confirmation, transactional save, and exact undo. Evidence and workspace state are recomputed at apply. The original source, installed game and accepted session files remain unchanged. Existing metadata undo restores the edit, not the historical observation that motivated it.

## UI, CLI and API parity

Public Python functions are `weapon_calibration.inspect(payload)` plus `weapon_desktop.review(payload)` and `weapon_desktop.apply(payload)`. Desktop operations reuse `inspect_weapon_workbench`, `review_weapon_authoring`, and `apply_weapon_authoring`; calibration is part of the weapon domain, not a separate application.

CLI commands accept the same bounded JSON as a `--payload` argument:

- `inspect-weapon-calibration`: read-only `calibration_action` of `list`, `compare`, or `propose`.
- `review-weapon-calibration`: read-only review of `action: "calibration_session"`, or `action: "edit"` with `calibration_evidence`.
- `apply-weapon-calibration`: authoring-write, requiring the reviewed `review_sha256` and `authoring_confirmed: true`. Agent API risk gating still applies.

Session request shape (substitute actual observed values and the screenshot's SHA-256):

```json
{
  "action": "calibration_session",
  "workspace": "C:/SDK/workspaces/my-weapon",
  "weapon": "WEAPON_EXAMPLE",
  "expected_revision": 0,
  "session": {
    "id": "iron-baseline-001",
    "captured_at": "2026-09-07T10:00:00Z",
    "profile": "iron",
    "edition": "Enhanced",
    "screenshot": "C:/Tests/baseline.png",
    "screenshot_sha256": "REPLACE_WITH_64_HEX_SOURCE_HASH",
    "conditions": {
      "camera": "first_person_aim",
      "fov_degrees": 30,
      "fov_axis": "vertical",
      "distance_m": 10,
      "pose": "stationary standing at marked target",
      "attachments": []
    },
    "aim": [960, 540],
    "sight": [970, 540],
    "impacts": [[962, 541], [959, 539]],
    "checks": {
      "aim": "pass", "fire": "pass", "reload": "not_tested",
      "camera_switch": "not_tested", "attachment_stability": "not_tested"
    },
    "accepted": false,
    "notes": "Visual sight error and impact group are different observations.",
    "frame_times_ms": []
  }
}
```

An optional `diagnostic` object uses `source` (artifact JSON), `comparison` (receipt JSON), `gta_path`, and optional `document` (runtime-session JSON). A comparison supplies `workspace`, `calibration_action: "compare"`, `baseline` and `trial` session IDs. A proposal adds `calibration_action: "propose"`, `field`, `axis: "x"` or `"y"`, and `controlled_trial_confirmed: true`. To review/apply that proposal, pass its exact `updates` and `weapon`, the current `expected_revision`, and the proposal request as `calibration_evidence` with `action: "edit"`.

## Explicit remaining boundary

This increment does **not** close the live capture loop. A matching installation receipt and file hashes do not establish that an imported screenshot depicts that build, that the current editable workspace produced the installed artifact, or that the engine used those bytes. Records therefore retain `runtime_asset_use: "not_proven"` and `workspace_to_installed_asset: "not_proven"`. The selected build is identifiable, but runtime use and capture attribution still need a trusted session/capture bridge and a verified workspace-to-export link. No automatic adjustment is made from a screenshot or impact group alone. G3/QBZ live acceptance remains outstanding.
