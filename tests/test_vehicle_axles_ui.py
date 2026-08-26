from __future__ import annotations

from types import SimpleNamespace

from dataclasses import dataclass, replace

from allin1_sdk.axle_steering_geometry import (
    AxleSteeringGain,
    SteeringGeometrySolution,
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.axle_configurator import (
    PRESET_STEER_DRIVE_REAR,
    detect_axle_configuration,
)
from allin1_sdk.vehicle_axles_ui import (
    _edit_axle_controls,
    _format_steering_gain,
    _requires_selective_steering_runtime,
    _steering_solution_summary,
)


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]


def _three_axle_bones() -> tuple[Bone, ...]:
    return (
        Bone("wheel_lf", (-1.0, 4.0, 0.0)),
        Bone("wheel_rf", (1.0, 4.0, 0.0)),
        Bone("wheel_lm1", (-1.0, 0.0, 0.0)),
        Bone("wheel_rm1", (1.0, 0.0, 0.0)),
        Bone("wheel_lr", (-1.0, -2.0, 0.0)),
        Bone("wheel_rr", (1.0, -2.0, 0.0)),
    )


def test_signed_steering_gain_format_is_compact_and_unambiguous() -> None:
    assert _format_steering_gain(1.0) == "+1.00"
    assert _format_steering_gain(-0.219) == "-0.22"
    assert _format_steering_gain(0.0) == "0.00"


def test_geometry_summary_shows_pivot_and_every_physical_axle_gain() -> None:
    solution = SteeringGeometrySolution(
        pivot_longitudinal_position=-2.123121,
        pivot_source="derived_fixed_axles",
        pivot_axle_orders=(2,),
        reference_axle_order=1,
        reference_lock_degrees=35.0,
        turn_radius=8.286,
        bone_position_sha256="0" * 64,
        axles=(
            AxleSteeringGain(1, 3.67867, 5.801791, 35.0, 1.0, "same"),
            AxleSteeringGain(2, -2.123121, 0.0, 0.0, 0.0, "fixed"),
            AxleSteeringGain(3, -3.254378, -1.131257, -7.77, -0.22, "counter"),
        ),
    )

    assert _steering_solution_summary(solution) == (
        "Pivot Y -2.123 (fixed axle) · A1 +1.00 · A2 0.00 · A3 -0.22"
    )


def test_signed_or_scaled_gain_requires_selective_runtime() -> None:
    legacy = SimpleNamespace(axles=(
        SimpleNamespace(steered=True, steering_gain=1.0),
        SimpleNamespace(steered=False, steering_gain=0.0),
    ))
    signed = SimpleNamespace(axles=(
        *legacy.axles,
        SimpleNamespace(steered=True, steering_gain=-0.22),
    ))

    assert not _requires_selective_steering_runtime(legacy)
    assert _requires_selective_steering_runtime(signed)


def test_steering_role_edit_safely_invalidates_old_geometry() -> None:
    bones = _three_axle_bones()
    base = detect_axle_configuration(
        "fixture_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, bones),
    )
    signed = replace(signed, minimum_runtime_version="3.1.0")
    assert signed.schema_version == 2
    assert signed.axles[2].steering_gain < 0.0

    edited, invalidated = _edit_axle_controls(
        signed,
        2,
        steered=False,
        powered=signed.axles[2].powered,
        service_brake=signed.axles[2].service_brake,
        handbrake=signed.axles[2].handbrake,
    )

    assert invalidated
    assert edited.schema_version == 1
    assert edited.minimum_runtime_version == "3.1.0"
    assert edited.steering_calculation is None
    assert [axle.steering_gain for axle in edited.axles] == [1.0, 0.0, 0.0]


def test_nonsteering_row_edit_preserves_signed_geometry_evidence() -> None:
    bones = _three_axle_bones()
    base = detect_axle_configuration(
        "fixture_bus", bones, preset=PRESET_STEER_DRIVE_REAR,
    )
    signed = apply_steering_geometry_to_configuration(
        base, solve_automatic_steering_geometry(base, bones),
    )

    edited, invalidated = _edit_axle_controls(
        signed,
        1,
        steered=signed.axles[1].steered,
        powered=not signed.axles[1].powered,
        service_brake=signed.axles[1].service_brake,
        handbrake=signed.axles[1].handbrake,
    )

    assert not invalidated
    assert edited.schema_version == 2
    assert edited.steering_calculation == signed.steering_calculation
    assert [axle.steering_gain for axle in edited.axles] == [
        axle.steering_gain for axle in signed.axles
    ]
