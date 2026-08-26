from __future__ import annotations

import math
from dataclasses import dataclass, replace

import pytest

from allin1_sdk.axle_configurator import (
    AxleConfiguration,
    LATEST_AXLE_SCHEMA_VERSION,
    PRESET_ALL_STEER,
    PRESET_STEER_DRIVE_REAR,
    VISUAL_FRONT,
    apply_axle_preset,
    detect_axle_configuration,
    validate_axle_configuration,
)
from allin1_sdk.axle_steering_geometry import (
    PIVOT_DERIVED_FIXED,
    PIVOT_EXPLICIT,
    PIVOT_SELECTED_FIXED,
    SteeringGeometryError,
    SteeringGeometryRequest,
    apply_manual_steering_gains_to_configuration,
    apply_steering_geometry_to_configuration,
    apply_steering_geometry_to_payload,
    canonical_bone_position_sha256,
    solve_automatic_steering_geometry,
)


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


PAIRS = (
    ("wheel_lf", "wheel_rf"),
    ("wheel_lm1", "wheel_rm1"),
    ("wheel_lm2", "wheel_rm2"),
    ("wheel_lm3", "wheel_rm3"),
    ("wheel_lr", "wheel_rr"),
)


def skeleton(positions: tuple[float, ...], *, extras: bool = False) -> tuple[Bone, ...]:
    pairs = PAIRS[:1] + PAIRS[4:5] if len(positions) == 2 else (
        PAIRS[: len(positions) - 1] + PAIRS[4:5]
    )
    result = [
        Bone(name, (x, y, 0.0))
        for (left, right), y in zip(pairs, positions)
        for name, x in ((left, -1.0), (right, 1.0))
    ]
    if extras:
        # Visual dual tyres and arbitrary mesh helpers are intentionally not
        # canonical physical slots and cannot change the steering solution.
        result.extend((
            Bone("wheel_lm1_inner", (-1.4, positions[1], 0.0)),
            Bone("wheel_rm1_inner", (1.4, positions[1], 0.0)),
            Bone("front_wheel_mesh_reused_at_rear", (0.0, positions[-1], 0.0)),
        ))
    return tuple(result)


def test_three_axle_solver_inverts_rear_and_uses_exact_lock_geometry() -> None:
    bones = skeleton((6.0, 0.0, -1.5))
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    solution = solve_automatic_steering_geometry(
        config, bones, SteeringGeometryRequest(reference_lock_degrees=35.0),
    )
    assert solution.pivot_source == PIVOT_DERIVED_FIXED
    assert solution.pivot_axle_orders == (2,)
    assert solution.pivot_longitudinal_position == pytest.approx(0.0)
    assert solution.reference_axle_order == 1
    assert solution.turn_radius == pytest.approx(6.0 / math.tan(math.radians(35.0)))
    front, middle, rear = solution.axles
    assert (front.phase, middle.phase, rear.phase) == ("same", "fixed", "counter")
    assert front.steering_gain == pytest.approx(1.0)
    assert middle.steering_gain == 0.0
    expected_rear_angle = math.atan(-1.5 / solution.turn_radius)
    assert rear.steering_angle_degrees == pytest.approx(math.degrees(expected_rear_angle))
    assert rear.steering_gain == pytest.approx(
        expected_rear_angle / math.radians(35.0)
    )


def test_measured_coach_fixture_pins_f11_countersteer_gain() -> None:
    bones = skeleton((3.678670, -2.123121, -3.254378))
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )

    solution = solve_automatic_steering_geometry(config, bones)

    assert solution.pivot_longitudinal_position == pytest.approx(-2.123121)
    assert [item.steering_gain for item in solution.axles] == pytest.approx(
        [1.0, 0.0, -0.222128], abs=1.0e-6,
    )


def test_two_axle_standard_uses_fixed_rear_as_neutral_pivot() -> None:
    bones = skeleton((3.5, -1.0))
    config = detect_axle_configuration("standard", bones)
    solution = solve_automatic_steering_geometry(config, bones)
    assert solution.pivot_axle_orders == (2,)
    assert solution.pivot_longitudinal_position == pytest.approx(-1.0)
    assert [item.steering_gain for item in solution.axles] == [1.0, 0.0]


def test_reused_front_mesh_family_does_not_change_rear_counter_phase() -> None:
    bones = skeleton((6.0, 0.0, -1.5), extras=True)
    base = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    reused_front_mesh = replace(
        base,
        axles=(*base.axles[:-1], replace(base.axles[-1], visual_family=VISUAL_FRONT)),
    )
    ordinary = solve_automatic_steering_geometry(base, bones)
    reused = solve_automatic_steering_geometry(reused_front_mesh, bones)
    assert ordinary.gain_by_physical_order == reused.gain_by_physical_order
    assert ordinary.bone_position_sha256 == reused.bone_position_sha256
    assert reused.axles[-1].steering_gain < 0.0


@pytest.mark.parametrize(
    ("positions", "steered_orders", "pivot_orders"),
    (
        ((9.0, 6.0, 2.0, -1.0), {1, 2, 4}, (3,)),
        ((12.0, 8.0, 4.0, 0.0, -3.0), {1, 2, 5}, (3, 4)),
    ),
)
def test_four_and_five_axles_support_selected_fixed_pivot_centroid(
    positions: tuple[float, ...],
    steered_orders: set[int],
    pivot_orders: tuple[int, ...],
) -> None:
    bones = skeleton(positions)
    base = detect_axle_configuration("heavy", bones)
    configured = replace(base, axles=tuple(
        replace(
            axle,
            steered=axle.physical_order in steered_orders,
            steering_gain=(
                1.0 if axle.physical_order in steered_orders else 0.0
            ),
        )
        for axle in base.axles
    ))
    solution = solve_automatic_steering_geometry(
        configured,
        bones,
        {"pivotAxleOrders": list(pivot_orders), "referenceLockDegrees": 32.0},
    )
    assert len(solution.axles) == len(positions)
    assert solution.pivot_source == PIVOT_SELECTED_FIXED
    assert solution.pivot_longitudinal_position == pytest.approx(
        sum(positions[order - 1] for order in pivot_orders) / len(pivot_orders)
    )
    assert all(
        solution.axles[order - 1].steering_gain == 0.0
        for order in pivot_orders
    )
    assert solution.axles[-1].steering_gain < 0.0


def test_all_steer_fails_closed_without_pivot_and_accepts_explicit_pivot() -> None:
    bones = skeleton((8.0, 3.0, -2.0))
    config = apply_axle_preset(
        detect_axle_configuration("allsteer", bones), PRESET_ALL_STEER,
    )
    with pytest.raises(SteeringGeometryError, match="All-steer layouts require"):
        solve_automatic_steering_geometry(config, bones)
    solution = solve_automatic_steering_geometry(
        config,
        bones,
        {"pivotLongitudinalPosition": 1.0, "referenceAxleOrder": 1},
    )
    assert solution.pivot_source == PIVOT_EXPLICIT
    assert solution.axles[0].steering_gain == pytest.approx(1.0)
    assert solution.axles[1].steering_gain > 0.0
    assert solution.axles[2].steering_gain < 0.0


def test_payload_helper_preserves_visual_and_dual_tyre_fields() -> None:
    bones = skeleton((6.0, 0.0, -1.5))
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    solution = solve_automatic_steering_geometry(config, bones)
    sdk_payload = config.to_dict()
    sdk_payload["visual_tyre_package"] = {"packageId": "dual_drive"}
    updated_sdk = apply_steering_geometry_to_payload(sdk_payload, solution)
    assert updated_sdk["visual_tyre_package"] == {"packageId": "dual_drive"}
    assert updated_sdk["axles"][0]["steering_gain"] == pytest.approx(1.0)
    assert updated_sdk["axles"][2]["steering_gain"] < 0.0
    assert updated_sdk["schema_version"] == LATEST_AXLE_SCHEMA_VERSION
    assert updated_sdk["minimum_runtime_version"] == "2.0.0"
    assert updated_sdk["steering_calculation"]["mode"] == "automatic_geometry"
    assert updated_sdk["steering_calculation"]["bone_position_sha256"] == (
        solution.bone_position_sha256
    )
    assert "steering_geometry" not in sdk_payload

    runtime_payload = {
        "schemaVersion": 1,
        "dualTyreGeometry": ["wheel_lm1_inner"],
        "dualTyresConsumePhysicalSlots": False,
        "axles": [
            {"order": order, "visualFamily": "front" if order == 2 else "shared"}
            for order in range(3)
        ],
    }
    updated_runtime = apply_steering_geometry_to_payload(runtime_payload, solution)
    assert updated_runtime["dualTyreGeometry"] == ["wheel_lm1_inner"]
    assert updated_runtime["dualTyresConsumePhysicalSlots"] is False
    assert updated_runtime["axles"][2]["visualFamily"] == "front"
    assert updated_runtime["axles"][2]["steeringGain"] < 0.0
    assert updated_runtime["schemaVersion"] == LATEST_AXLE_SCHEMA_VERSION
    assert updated_runtime["minimumRuntimeVersion"] == "2.0.0"
    assert updated_runtime["steeringCalculation"]["mode"] == "automaticGeometry"
    assert updated_runtime["steeringCalculation"]["bonePositionSha256"] == (
        solution.bone_position_sha256
    )
    assert "steeringGeometry" not in updated_runtime

    cross_runtime = dict(updated_sdk)
    cross_runtime["schemaVersion"] = cross_runtime.pop("schema_version")
    cross_runtime.pop("steering_calculation")
    cross_runtime["steeringCalculation"] = updated_runtime["steeringCalculation"]
    loaded_runtime = AxleConfiguration.from_dict(cross_runtime)
    assert loaded_runtime.steering_calculation is not None
    assert loaded_runtime.steering_calculation.pivot_axle_orders == (2,)
    assert loaded_runtime.steering_calculation.reference_axle_order == 1

    applied = apply_steering_geometry_to_configuration(config, solution)
    assert [item.steering_gain for item in applied.axles] == pytest.approx([
        1.0, 0.0, solution.axles[-1].steering_gain,
    ])
    assert applied.axles[-1].steering_gain < 0.0
    assert applied.schema_version == LATEST_AXLE_SCHEMA_VERSION
    assert applied.steering_calculation == solution.provenance()


def test_legacy_equivalent_solution_remains_schema_one_and_omits_gain() -> None:
    bones = skeleton((3.5, -1.0))
    config = detect_axle_configuration("standard", bones)
    solution = solve_automatic_steering_geometry(config, bones)
    applied = apply_steering_geometry_to_configuration(config, solution)
    assert applied.schema_version == 1
    assert applied.steering_calculation is None
    assert all("steering_gain" not in row for row in applied.to_dict()["axles"])
    runtime = apply_steering_geometry_to_payload({
        "schemaVersion": 2,
        "minimumRuntimeVersion": "2.0.0",
        "steeringCalculation": {"stale": True},
        "axles": [{"order": 0}, {"order": 1}],
    }, solution)
    assert runtime["schemaVersion"] == 1
    assert runtime["minimumRuntimeVersion"] == "2.0.0"
    assert "steeringCalculation" not in runtime
    assert all("steeringGain" not in row for row in runtime["axles"])


def test_near_pivot_steered_axle_keeps_schema_two_when_gain_quantizes_to_zero() -> None:
    bones = skeleton((100.0, 0.0, -0.0002))
    config = detect_axle_configuration(
        "long_coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )

    solution = solve_automatic_steering_geometry(config, bones)

    assert solution.axles[-1].phase == "neutral"
    assert solution.axles[-1].steering_gain == 0.0
    applied = apply_steering_geometry_to_configuration(config, solution)
    assert applied.schema_version == LATEST_AXLE_SCHEMA_VERSION
    assert applied.steering_calculation is not None
    assert applied.axles[-1].steered is True
    assert applied.axles[-1].steering_gain == 0.0


def test_geometry_records_custom_tolerances_and_preserves_stronger_runtime_floor() -> None:
    bones = skeleton((6.0, 0.0, -1.5))
    config = replace(
        detect_axle_configuration(
            "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
        ),
        minimum_runtime_version="3.1.0",
    )
    solution = solve_automatic_steering_geometry(
        config,
        bones,
        SteeringGeometryRequest(
            pair_position_tolerance=0.4,
            position_epsilon=0.00001,
        ),
    )

    applied = apply_steering_geometry_to_configuration(config, solution)
    assert applied.minimum_runtime_version == "3.1.0"
    assert applied.steering_calculation is not None
    assert applied.steering_calculation.pair_position_tolerance == 0.4
    assert applied.steering_calculation.position_epsilon == 0.00001
    assert not [
        finding for finding in validate_axle_configuration(applied, bones)
        if finding.code.startswith("steering_evidence")
        or finding.code == "stale_steering_geometry"
    ]

    manual = apply_manual_steering_gains_to_configuration(
        config, bones, {1: 0.9, 2: 0.0, 3: -0.2},
    )
    assert manual.minimum_runtime_version == "3.1.0"

    payload = apply_steering_geometry_to_payload(config.to_dict(), solution)
    assert payload["minimum_runtime_version"] == "3.1.0"


def test_bone_position_digest_excludes_visuals_but_tracks_canonical_xyz() -> None:
    bones = skeleton((6.0, 0.0, -1.5), extras=True)
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    changed_visuals = replace(config, axles=tuple(
        replace(axle, visual_family=VISUAL_FRONT) for axle in config.axles
    ))
    assert canonical_bone_position_sha256(config, bones) == (
        canonical_bone_position_sha256(changed_visuals, bones)
    )
    moved = tuple(
        replace(bone, position=(bone.position[0], bone.position[1] - 0.1, bone.position[2]))
        if bone.name == "wheel_lr" else bone
        for bone in bones
    )
    assert canonical_bone_position_sha256(config, bones) != (
        canonical_bone_position_sha256(config, moved)
    )
    signed = apply_steering_geometry_to_configuration(
        config, solve_automatic_steering_geometry(config, bones),
    )
    assert any(
        item.severity == "error" and item.code == "stale_steering_geometry"
        for item in validate_axle_configuration(signed, moved)
    )


def test_manual_nonlegacy_gain_promotes_with_evidence_and_round_trips() -> None:
    bones = skeleton((6.0, 0.0, -1.5))
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    manual = apply_manual_steering_gains_to_configuration(
        config, bones, {1: 0.9, 2: 0.0, 3: -0.25},
    )
    assert manual.schema_version == LATEST_AXLE_SCHEMA_VERSION
    assert manual.steering_calculation is not None
    assert manual.steering_calculation.mode == "manual"
    assert manual.steering_calculation.bone_position_sha256 == (
        canonical_bone_position_sha256(config, bones)
    )
    assert type(manual).from_dict(manual.to_dict()) == manual

    legacy = apply_manual_steering_gains_to_configuration(
        config, bones, {1: 1.0, 2: 0.0, 3: 1.0},
    )
    assert legacy.schema_version == 1
    assert legacy.steering_calculation is None


def test_ambiguous_or_invalid_geometry_is_rejected_before_calculation() -> None:
    bones = skeleton((6.0, 0.0, -1.5))
    config = detect_axle_configuration(
        "coach", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    with pytest.raises(SteeringGeometryError, match="only non-steered"):
        solve_automatic_steering_geometry(
            config, bones, {"pivotAxleOrders": [1]},
        )
    with pytest.raises(SteeringGeometryError, match="either an explicit"):
        SteeringGeometryRequest(
            pivot_longitudinal_position=0.0, pivot_axle_orders=(2,),
        )
    bad_pair = tuple(
        replace(bone, position=(bone.position[0], 1.0, bone.position[2]))
        if bone.name == "wheel_rm1" else bone
        for bone in bones
    )
    with pytest.raises(SteeringGeometryError, match="centres disagree"):
        solve_automatic_steering_geometry(config, bad_pair)

    # A near-pivot reference would require a farther axle to exceed the
    # runtime's normalized gain range, so it is rejected instead of clipped.
    all_steer = apply_axle_preset(config, PRESET_ALL_STEER)
    with pytest.raises(SteeringGeometryError, match="longest steering lever arm"):
        solve_automatic_steering_geometry(
            all_steer,
            bones,
            {
                "pivotLongitudinalPosition": 0.5,
                "referenceAxleOrder": 2,
            },
        )
