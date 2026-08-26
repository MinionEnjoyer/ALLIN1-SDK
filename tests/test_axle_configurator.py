from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    EXPORT_STOCK_METADATA,
    FLAG_IS_DRIVEN,
    FLAG_IS_STEERED,
    HF_STEER_ALL_WHEELS,
    HF_STEER_REARWHEELS,
    INTENTIONAL_LAYOUT_RUNTIME_VERSION,
    PRESET_ALL_STEER,
    PRESET_STANDARD,
    PRESET_STEER_DRIVE_REAR,
    RUNTIME_REQUIRED_MESSAGE,
    SHARED_VISUAL_WARNING,
    VISUAL_FRONT,
    AxleAddonGeometry,
    AxleConfiguration,
    apply_axle_preset,
    apply_intentional_layout_override,
    clear_intentional_layout_override,
    detect_axle_configuration,
    fivem_client_lua,
    fivem_server_lua,
    format_handling_flags,
    joaat_hex,
    parse_handling_flags,
    requires_signed_steering_gain,
    resolve_runtime_wheel_index_map,
    retarget_axle_configuration,
    steering_diagnostic,
    stock_metadata_flags,
    update_story_wheel_flags,
    update_wheel_flags,
    validate_axle_configuration,
    write_fivem_resource,
)


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def skeleton(count: int) -> tuple[Bone, ...]:
    pairs = (
        ("wheel_lf", "wheel_rf"),
        ("wheel_lm1", "wheel_rm1"),
        ("wheel_lm2", "wheel_rm2"),
        ("wheel_lm3", "wheel_rm3"),
        ("wheel_lr", "wheel_rr"),
    )
    selected = (pairs[:1] + pairs[-1:]) if count == 2 else pairs[: count - 1] + pairs[-1:]
    result = []
    for order, (left, right) in enumerate(selected):
        y = float((len(selected) - order) * 3)
        result.extend((Bone(left, (-1.0, y, 0.0)), Bone(right, (1.0, y, 0.0))))
    return tuple(result)


@pytest.mark.parametrize("axle_count", (2, 3, 4, 5))
def test_variable_axle_detection_uses_dense_semantic_target_mapping(axle_count: int) -> None:
    config = detect_axle_configuration("fixture", skeleton(axle_count))
    assert len(config.axles) == axle_count
    assert [
        value for axle in config.axles
        for value in (axle.left_runtime_index, axle.right_runtime_index)
    ] == list(range(axle_count * 2))
    assert not [
        item for item in validate_axle_configuration(
            config, skeleton(axle_count), target="fivem-legacy",
        ) if item.severity == "error"
    ]


def test_six_wheel_acceptance_preset() -> None:
    bones = skeleton(3)
    config = detect_axle_configuration(
        "example_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    assert [(item.steered, item.powered) for item in config.axles] == [
        (True, False), (False, True), (True, False),
    ]
    assert [(item.left_bone, item.right_bone) for item in config.axles] == [
        ("wheel_lf", "wheel_rf"),
        ("wheel_lm1", "wheel_rm1"),
        ("wheel_lr", "wheel_rr"),
    ]


def test_intentional_visual_instancing_override_supports_custom_physical_order() -> None:
    # This mirrors the reviewed bus layout: the shared-middle pair is the
    # physical front, GTA's front family is the physical middle, and the rear
    # pair remains the physical rear. Runtime slots stay canonical.
    bones = tuple(
        Bone(name, (x, y, 0.0))
        for name, x, y in (
            ("wheel_lf", -1.0, 6.0), ("wheel_rf", 1.0, 6.0),
            ("wheel_lm1", -1.0, 9.0), ("wheel_rm1", 1.0, 9.0),
            ("wheel_lr", -1.0, 3.0), ("wheel_rr", 1.0, 3.0),
        )
    )
    detected = detect_axle_configuration(
        "visual_flip_bus", bones, export_mode=EXPORT_FIVEM_RUNTIME,
    )
    assert any(
        item.code == "physical_order_semantics"
        for item in validate_axle_configuration(detected, bones)
    )

    remapped = apply_intentional_layout_override(
        detected,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
        reason="Author-reviewed single/dual/single visual wheel layout",
    )
    configured = apply_axle_preset(remapped, PRESET_STEER_DRIVE_REAR)

    assert configured.minimum_runtime_version == INTENTIONAL_LAYOUT_RUNTIME_VERSION
    assert [
        (axle.left_bone, axle.logical_role, axle.steered, axle.powered)
        for axle in configured.axles
    ] == [
        ("wheel_lm1", "front", True, False),
        ("wheel_lf", "middle", False, True),
        ("wheel_lr", "rear", True, False),
    ]
    assert [
        (axle.left_runtime_index, axle.right_runtime_index)
        for axle in configured.axles
    ] == [(2, 3), (0, 1), (4, 5)]
    findings = validate_axle_configuration(configured, bones)
    assert any(item.code == "intentional_layout_override" for item in findings)
    assert not [
        item for item in findings
        if item.severity == "error" and item.code in {
            "logical_role_semantics", "physical_order_semantics",
            "physical_order_position", "canonical_reassignment",
            "front_not_forwardmost", "rear_ahead_of_front",
        }
    ]
    assert AxleConfiguration.from_dict(configured.to_dict()) == configured
    flags = stock_metadata_flags(configured, HF_STEER_REARWHEELS | 0xA500)
    assert flags.updated_flags & HF_STEER_REARWHEELS == 0
    assert flags.updated_flags & HF_STEER_ALL_WHEELS == 0
    assert flags.updated_flags & ~0xE0 == 0xA500 & ~0xE0


def test_intentional_layout_override_enforces_and_preserves_runtime_floor() -> None:
    bones = skeleton(3)
    base = detect_axle_configuration("runtime_floor_bus", bones)
    remapped = apply_intentional_layout_override(
        base,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
    )
    lowered = remapped.to_dict()
    lowered["minimum_runtime_version"] = "2.0.0"
    with pytest.raises(ValueError, match="2.1.0 or newer"):
        AxleConfiguration.from_dict(lowered)

    stronger = replace(base, minimum_runtime_version="3.4.5")
    stronger_remap = apply_intentional_layout_override(
        stronger,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
    )
    assert stronger_remap.minimum_runtime_version == "3.4.5"


def test_intentional_layout_override_rejects_incomplete_or_stale_evidence() -> None:
    bones = skeleton(3)
    config = detect_axle_configuration("bus", bones)
    with pytest.raises(ValueError, match="every configured canonical pair"):
        apply_intentional_layout_override(
            config,
            bones,
            physical_bone_pairs=(
                ("wheel_lm1", "wheel_rm1"),
                ("wheel_lf", "wheel_rf"),
            ),
        )
    remapped = apply_intentional_layout_override(
        config,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
    )
    moved = tuple(
        replace(bone, position=(bone.position[0], bone.position[1] + 0.25, 0.0))
        if bone.name == "wheel_lm1" else bone
        for bone in bones
    )
    assert any(
        item.code == "stale_layout_override" and item.severity == "error"
        for item in validate_axle_configuration(remapped, moved)
    )


def test_intentional_layout_override_validation_fails_closed_after_tampering() -> None:
    bones = skeleton(3)
    remapped = apply_intentional_layout_override(
        detect_axle_configuration("tampered_bus", bones),
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
    )
    assert remapped.intentional_layout_override is not None

    mapping_tampered = replace(
        remapped,
        intentional_layout_override=replace(
            remapped.intentional_layout_override,
            physical_bone_pairs=(
                ("wheel_lf", "wheel_rf"),
                ("wheel_lm1", "wheel_rm1"),
                ("wheel_lr", "wheel_rr"),
            ),
        ),
    )
    assert any(
        item.code == "layout_override_mapping_mismatch"
        for item in validate_axle_configuration(mapping_tampered, bones)
    )

    role_tampered = replace(
        remapped,
        axles=(
            replace(remapped.axles[0], logical_role="middle"),
            *remapped.axles[1:],
        ),
    )
    assert any(
        item.code == "layout_override_role_mismatch"
        for item in validate_axle_configuration(role_tampered, bones)
    )

    assert any(
        item.code == "layout_override_evidence_unavailable"
        for item in validate_axle_configuration(remapped, ())
    )
    duplicate_bones = (*bones, bones[0])
    assert any(
        item.code == "layout_override_evidence_unavailable"
        for item in validate_axle_configuration(remapped, duplicate_bones)
    )


def test_restoring_canonical_order_removes_override_and_roles() -> None:
    bones = skeleton(3)
    config = detect_axle_configuration("bus", bones)
    remapped = apply_intentional_layout_override(
        config,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
    )

    restored = clear_intentional_layout_override(remapped)

    assert restored.intentional_layout_override is None
    assert [
        (item.physical_order, item.logical_role, item.left_bone)
        for item in restored.axles
    ] == [
        (1, "front", "wheel_lf"),
        (2, "middle", "wheel_lm1"),
        (3, "rear", "wheel_lr"),
    ]


def test_axle_preset_preserves_stronger_runtime_floor() -> None:
    config = replace(
        detect_axle_configuration("runtime_floor", skeleton(3)),
        minimum_runtime_version="3.1.0",
    )

    updated = apply_axle_preset(config, PRESET_STEER_DRIVE_REAR)

    assert updated.minimum_runtime_version == "3.1.0"


def test_spatial_reassignment_and_left_right_messages_are_exact() -> None:
    bones = list(skeleton(3))
    bones = [
        replace(item, position=(item.position[0], 20.0, item.position[2]))
        if item.name in {"wheel_lr", "wheel_rr"} else item
        for item in bones
    ]
    bones[0] = replace(bones[0], position=(2.0, bones[0].position[1], 0.0))
    findings = validate_axle_configuration(
        detect_axle_configuration("badbus", bones), bones,
    )
    messages = {item.message for item in findings}
    assert "wheel_lr/rr are positioned ahead of wheel_lf/rf. Rear-wheel steering semantics may cause inverted steering." in messages
    assert "wheel_lf/rf do not appear to be the forwardmost axle." in messages
    assert "Left and right wheel bones appear to be exchanged." in messages
    assert "Canonical wheel roles have been spatially reassigned. Restore canonical placement and configure behavior through axle settings." in messages


def test_visual_family_and_addon_wheel_mesh_are_separate_from_behavior() -> None:
    config = detect_axle_configuration("bus", skeleton(3))
    middle = replace(
        config.axles[1], visual_family=VISUAL_FRONT,
        addon_geometry=(AxleAddonGeometry("wheels/inner_lm1.ydr", "wheel_lm1", True),),
    )
    findings = validate_axle_configuration(
        replace(config, axles=(config.axles[0], middle, config.axles[2])), skeleton(3),
    )
    assert SHARED_VISUAL_WARNING in {item.message for item in findings}
    assert any(item.code == "addon_is_wheel_mesh" for item in findings)
    assert config.axles[1].steered == middle.steered
    assert config.axles[1].powered == middle.powered


def test_stock_handling_flags_preserve_unrelated_bits_and_warn_advanced() -> None:
    config = detect_axle_configuration(
        "bus", skeleton(3), preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_STOCK_METADATA,
    )
    original = 0xA500041F
    result = stock_metadata_flags(config, original)
    assert result.updated_flags & ~0xE0 == original & ~0xE0
    assert result.updated_flags & HF_STEER_ALL_WHEELS
    assert RUNTIME_REQUIRED_MESSAGE in result.warnings
    assert parse_handling_flags("0xA500041F") == original
    assert format_handling_flags(result.updated_flags, "0xA500041F").startswith("0x")


@pytest.mark.parametrize("flags", (0x0000, 0xFFFF, 0xA5A5, 0x1234))
def test_per_wheel_rmw_preserves_every_unrelated_bit(flags: int) -> None:
    steered = update_wheel_flags(flags, steered=True)
    fixed = update_wheel_flags(flags, steered=False)
    assert steered & ~FLAG_IS_STEERED == flags & ~FLAG_IS_STEERED
    assert fixed & ~FLAG_IS_STEERED == flags & ~FLAG_IS_STEERED
    story = update_story_wheel_flags(flags, steered=False, powered=True)
    assert story & ~(FLAG_IS_STEERED | FLAG_IS_DRIVEN) == flags & ~(
        FLAG_IS_STEERED | FLAG_IS_DRIVEN
    )
    assert story & FLAG_IS_DRIVEN


def test_wheel_flags_reject_values_outside_native_uint16() -> None:
    with pytest.raises(ValueError, match="16-bit"):
        update_wheel_flags(0x10000, steered=True)


def test_schema_migration_and_forward_rejection() -> None:
    config = detect_axle_configuration("bus", skeleton(3))
    loaded = AxleConfiguration.from_dict(config.to_dict())
    assert loaded == config
    legacy = config.to_dict()
    legacy.pop("schema_version")
    legacy["model"] = legacy.pop("vehicle_model")
    migrated = AxleConfiguration.from_dict(legacy)
    assert migrated.schema_version == 1
    bad = config.to_dict()
    bad["schema_version"] = 99
    with pytest.raises(ValueError, match="Unsupported"):
        AxleConfiguration.from_dict(bad)

    prerelease = config.to_dict()
    prerelease["minimum_runtime_version"] = "2.0.0-alpha"
    with pytest.raises(ValueError, match="major.minor.patch"):
        AxleConfiguration.from_dict(prerelease)


def test_gapped_canonical_middle_pairs_fail_closed() -> None:
    valid_bones = skeleton(3)
    gapped_bones = tuple(
        replace(
            bone,
            name=bone.name.replace("m1", "m2"),
        ) if "m1" in bone.name else bone
        for bone in valid_bones
    )
    with pytest.raises(ValueError, match="must be dense"):
        detect_axle_configuration("gapped", gapped_bones)

    config = detect_axle_configuration("bus", valid_bones)
    gapped_config = replace(config, axles=(
        config.axles[0],
        replace(
            config.axles[1],
            left_bone="wheel_lm2",
            right_bone="wheel_rm2",
        ),
        config.axles[2],
    ))
    assert any(
        finding.code == "canonical_pair_sequence"
        for finding in validate_axle_configuration(gapped_config, gapped_bones)
    )


def test_schema_one_omits_gain_and_schema_two_requires_signed_evidence() -> None:
    config = detect_axle_configuration(
        "bus", skeleton(3), preset=PRESET_STEER_DRIVE_REAR,
    )
    legacy = config.to_dict()
    assert legacy["schema_version"] == 1
    assert all("steering_gain" not in row for row in legacy["axles"])
    loaded = AxleConfiguration.from_dict(legacy)
    assert [item.steering_gain for item in loaded.axles] == [1.0, 0.0, 1.0]
    assert loaded.to_dict() == legacy

    invalid_v1 = config.to_dict()
    invalid_v1["axles"][-1]["steering_gain"] = -0.22
    with pytest.raises(ValueError, match="schema 2"):
        AxleConfiguration.from_dict(invalid_v1)

    from allin1_sdk.axle_steering_geometry import (
        apply_steering_geometry_to_configuration,
        solve_automatic_steering_geometry,
    )

    signed_config = apply_steering_geometry_to_configuration(
        config, solve_automatic_steering_geometry(config, skeleton(3)),
    )
    signed = signed_config.to_dict()
    assert signed["schema_version"] == 2
    assert all("steering_gain" in row for row in signed["axles"])
    assert signed["steering_calculation"]["bone_position_sha256"]
    assert AxleConfiguration.from_dict(signed) == signed_config

    invalid = signed.copy()
    invalid["axles"] = [dict(row) for row in signed["axles"]]
    invalid["axles"][0]["steering_gain"] = 1.01
    with pytest.raises(ValueError, match="finite number from -1 to 1"):
        AxleConfiguration.from_dict(invalid)

    missing_gain = signed.copy()
    missing_gain["axles"] = [dict(row) for row in signed["axles"]]
    missing_gain["axles"][0].pop("steering_gain")
    with pytest.raises(ValueError, match="explicit steering_gain"):
        AxleConfiguration.from_dict(missing_gain)

    missing_evidence = signed.copy()
    missing_evidence.pop("steering_calculation")
    with pytest.raises(ValueError, match="calculation evidence"):
        AxleConfiguration.from_dict(missing_evidence)

    downgraded_runtime = signed.copy()
    downgraded_runtime["minimum_runtime_version"] = "1.0.0"
    with pytest.raises(ValueError, match="2.0.0 or newer"):
        AxleConfiguration.from_dict(downgraded_runtime)

    tampered_rows = list(signed_config.axles)
    tampered_rows[-1] = replace(
        tampered_rows[-1],
        steering_gain=float(tampered_rows[-1].steering_gain) * 0.5,
    )
    tampered = replace(signed_config, axles=tuple(tampered_rows))
    assert any(
        item.code == "steering_evidence_mismatch"
        for item in validate_axle_configuration(tampered, skeleton(3))
    )

    changed_pivot_rows = list(signed_config.axles)
    changed_pivot_rows[1] = replace(
        changed_pivot_rows[1], steered=True, steering_gain=0.2,
    )
    changed_pivot = replace(signed_config, axles=tuple(changed_pivot_rows))
    assert any(
        item.code == "steering_pivot_evidence"
        for item in validate_axle_configuration(changed_pivot, skeleton(3))
    )


def test_legacy_migration_derives_indices_from_bones_not_row_order() -> None:
    payload = detect_axle_configuration("bus", skeleton(3)).to_dict()
    payload["schema_version"] = 0
    rows = payload["axles"]
    payload["axles"] = [rows[2], rows[0], rows[1]]
    for row in payload["axles"]:
        row.pop("physical_order")
        row.pop("logical_role")
        row.pop("visual_family")
        row.pop("left_runtime_index")
        row.pop("right_runtime_index")
    migrated = AxleConfiguration.from_dict(payload)
    indices = {
        axle.left_bone: (axle.left_runtime_index, axle.right_runtime_index)
        for axle in migrated.axles
    }
    assert indices == {
        "wheel_lf": (0, 1), "wheel_lm1": (2, 3), "wheel_lr": (4, 5),
    }
    migrated_by_bone = {item.left_bone: item for item in migrated.axles}
    assert migrated_by_bone["wheel_lf"].physical_order == 1
    assert migrated_by_bone["wheel_lf"].logical_role == "front"
    assert migrated_by_bone["wheel_lf"].visual_family == VISUAL_FRONT
    assert migrated_by_bone["wheel_lr"].physical_order == 3
    assert migrated_by_bone["wheel_lr"].logical_role == "rear"


def test_retarget_isolates_compatibility_and_checks_reported_wheel_count() -> None:
    config = detect_axle_configuration(
        "bus", skeleton(3), target="story-enhanced",
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    assert dict(config.compatibility) == {
        "fivem-legacy": False, "fivem-enhanced": False,
        "story-legacy": False, "story-enhanced": True,
    }
    assert not [
        item for item in validate_axle_configuration(
            config, skeleton(3), target="story-enhanced",
        ) if item.severity == "error"
    ]
    assert any(
        item.code == "target_compatibility"
        for item in validate_axle_configuration(config, target="fivem-legacy")
    )
    with pytest.raises(ValueError, match="Target reported 4 wheels"):
        retarget_axle_configuration(
            config, "fivem-legacy", reported_wheel_count=4,
        )
    retargeted = retarget_axle_configuration(
        config, "fivem-legacy", reported_wheel_count=6,
    )
    assert dict(retargeted.compatibility)["fivem-legacy"] is True
    assert dict(retargeted.compatibility)["story-enhanced"] is False


def test_validation_requires_semantic_roles_order_and_all_skeleton_pairs() -> None:
    config = detect_axle_configuration("bus", skeleton(3))
    reordered = replace(config, axles=(
        replace(config.axles[0], physical_order=2, logical_role="rear"),
        replace(config.axles[1], physical_order=1),
        config.axles[2],
    ))
    codes = {
        item.code for item in validate_axle_configuration(reordered, skeleton(3))
    }
    assert {
        "logical_role_semantics", "physical_order_semantics",
        "physical_order_position",
    }.issubset(codes)

    two_axles = detect_axle_configuration("bus", skeleton(2))
    omitted = validate_axle_configuration(two_axles, skeleton(3))
    assert any(item.code == "unconfigured_canonical_pair" for item in omitted)


def test_custom_brake_metadata_is_reported_as_runtime_unsupported() -> None:
    config = detect_axle_configuration("bus", skeleton(3), target="fivem-legacy")
    custom = replace(config, axles=(
        replace(config.axles[0], handbrake=True),
        replace(config.axles[1], service_brake=False),
        config.axles[2],
    ))
    codes = {
        item.code for item in validate_axle_configuration(
            custom, skeleton(3), target="fivem-legacy",
        )
    }
    assert "runtime_service_brake_unsupported" in codes
    assert "runtime_handbrake_unsupported" in codes


def test_fivem_export_rejects_story_only_configuration() -> None:
    config = detect_axle_configuration(
        "bus", skeleton(3), target="story-legacy",
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    with pytest.raises(ValueError, match="retargeted"):
        fivem_client_lua(config)


def test_signed_gain_never_silently_downgrades_to_stock_or_fivem_flags() -> None:
    base = detect_axle_configuration(
        "bus", skeleton(3), preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_STOCK_METADATA,
    )
    from allin1_sdk.axle_steering_geometry import (
        apply_steering_geometry_to_configuration,
        solve_automatic_steering_geometry,
    )

    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, skeleton(3)),
    )
    assert requires_signed_steering_gain(signed)
    stock_signed = replace(signed, export_mode=EXPORT_STOCK_METADATA)
    findings = validate_axle_configuration(stock_signed, skeleton(3))
    assert any(
        item.severity == "error"
        and item.code == "signed_steering_runtime_required"
        for item in findings
    )

    fivem = replace(signed, export_mode=EXPORT_FIVEM_RUNTIME)
    with pytest.raises(ValueError, match="cannot apply signed or scaled"):
        fivem_client_lua(fivem)


def test_canonical_index_resolver_does_not_leave_gaps_when_lm_pairs_are_absent() -> None:
    mapping = resolve_runtime_wheel_index_map(
        (("wheel_lf", "wheel_rf"), ("wheel_lr", "wheel_rr")),
        target="story-enhanced",
    )
    assert mapping == {
        "wheel_lf": 0, "wheel_rf": 1, "wheel_lr": 2, "wheel_rr": 3,
    }


def test_generated_fivem_resource_is_variable_event_driven_and_control_safe(tmp_path) -> None:
    config = detect_axle_configuration(
        "example_bus", skeleton(3), preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    client = fivem_client_lua(config)
    server = fivem_server_lua(config)
    assert "EXPECTED_WHEELS = 6" in client
    assert "NetworkHasControlOfEntity" in client
    assert "flags & 0xFFF7" in client
    assert "Wait(RECOVERY_MS)" in client
    assert "Wait(0)" not in client
    assert 'AddEventHandler("entityCreated"' not in client
    assert 'AddEventHandler("entityCreated"' in server
    output = write_fivem_resource(config, tmp_path / "bus-axles")
    assert (output / "server.lua").is_file()
    assert json.loads((output / "axle-config.json").read_text("utf-8"))[
        "expected_wheel_count"
    ] == 6


def test_invalid_runtime_index_count_blocks_export() -> None:
    config = detect_axle_configuration(
        "bus", skeleton(3), preset=PRESET_ALL_STEER,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    broken = replace(config.axles[2], left_runtime_index=2)
    invalid = replace(config, axles=(*config.axles[:2], broken))
    with pytest.raises(ValueError, match="Cannot generate"):
        fivem_client_lua(invalid)


def test_diagnostics_distinguish_response_from_visual_orientation() -> None:
    config = apply_axle_preset(detect_axle_configuration("bus", skeleton(3)), PRESET_STANDARD)
    reverse = steering_diagnostic(
        config, requested_input=1.0, vehicle_steering_angle=-0.2,
        runtime_wheel_flags={}, runtime_powered={},
    )
    visual = steering_diagnostic(
        config, requested_input=1.0, vehicle_steering_angle=0.2,
        runtime_wheel_flags={}, runtime_powered={}, visual_direction_mismatch=True,
    )
    assert "logical/physical axle reassignment" in reverse.outcome
    assert "bone roll" in visual.outcome
    assert all(wheel.configured_phase in {"same", "fixed"} for wheel in visual.wheels)


def test_joaat_is_stable_and_serialized() -> None:
    config = detect_axle_configuration("example_bus", skeleton(2))
    assert config.model_hash == joaat_hex("example_bus")
    assert config.to_dict()["model_hash"] == joaat_hex("example_bus")
