from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    PRESET_STEER_DRIVE_REAR,
    AxleConfiguration,
    apply_axle_preset,
    apply_intentional_layout_override,
    detect_axle_configuration,
    joaat_hex,
    validate_axle_configuration,
)
from allin1_sdk.axle_runtime_bundler import (
    TARGET_CAPABILITIES,
    TARGET_FIVEM_LEGACY,
    VehicleAxleBuildInput,
    compatibility_configuration,
)
from allin1_sdk.axle_steering_geometry import (
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _synthetic_override_bones() -> tuple[Bone, ...]:
    # Synthetic regression geometry: model-local front-to-rear order increases
    # in Y while the visual-instancing workaround uses middle, front, rear
    # canonical pairs in physical order. No third-party model data is retained.
    return tuple(
        Bone(name, (x, y, -0.54))
        for left, right, y in (
            ("wheel_lm1", "wheel_rm1", -2.0),
            ("wheel_lf", "wheel_rf", 0.0),
            ("wheel_lr", "wheel_rr", 10.0),
        )
        for name, x in ((left, -1.17), (right, 1.17))
    )


def _signed_override_configuration() -> tuple[AxleConfiguration, tuple[Bone, ...]]:
    bones = _synthetic_override_bones()
    detected = detect_axle_configuration(
        "synthetic_layout_bus", bones, export_mode=EXPORT_FIVEM_RUNTIME,
    )
    overridden = apply_intentional_layout_override(
        detected,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
        reason="Synthetic single/dual/single wheel-family layout",
    )
    patterned = apply_axle_preset(overridden, PRESET_STEER_DRIVE_REAR)
    solution = solve_automatic_steering_geometry(patterned, bones)
    return apply_steering_geometry_to_configuration(patterned, solution), bones


def test_override_steering_provenance_binds_exact_physical_pair_order() -> None:
    configured, bones = _signed_override_configuration()
    calculation = configured.steering_calculation
    assert calculation is not None
    assert calculation.physical_bone_pairs == (
        ("wheel_lm1", "wheel_rm1"),
        ("wheel_lf", "wheel_rf"),
        ("wheel_lr", "wheel_rr"),
    )
    assert [axle.steering_gain for axle in configured.axles] == pytest.approx(
        [0.22776979648028195, 0.0, -1.0], abs=1.0e-9,
    )
    assert not [
        item for item in validate_axle_configuration(configured, bones)
        if item.severity == "error"
    ]
    assert AxleConfiguration.from_dict(configured.to_dict()) == configured


def test_runtime_payload_preserves_override_bound_steering_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured, bones = _signed_override_configuration()
    monkeypatch.setitem(
        TARGET_CAPABILITIES,
        TARGET_FIVEM_LEGACY,
        replace(
            TARGET_CAPABILITIES[TARGET_FIVEM_LEGACY],
            maximum_axle_schema=2,
            supports_signed_steering_gain=True,
            runtime_implementation_version=configured.minimum_runtime_version,
        ),
    )
    vehicle = VehicleAxleBuildInput(
        configuration=configured,
        configuration_id=configured.configuration_id,
        model_hash=joaat_hex(configured.vehicle_model),
        minimum_runtime_version=configured.minimum_runtime_version,
        steering_evidence_bones=bones,
    )
    payload = compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)
    assert payload["steeringCalculation"]["physicalBonePairs"] == [
        ["wheel_lm1", "wheel_rm1"],
        ["wheel_lf", "wheel_rf"],
        ["wheel_lr", "wheel_rr"],
    ]
    assert payload["wheelIndexMapping"]["by_bone"] == {
        "wheel_lf": 0,
        "wheel_rf": 1,
        "wheel_lr": 2,
        "wheel_rr": 3,
        "wheel_lm1": 4,
        "wheel_rm1": 5,
    }


@pytest.mark.parametrize(
    "stale_pairs",
    (
        (),
        (
            ("wheel_lf", "wheel_rf"),
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lr", "wheel_rr"),
        ),
    ),
)
def test_override_rejects_missing_or_pre_override_steering_order_evidence(
    stale_pairs: tuple[tuple[str, str], ...],
) -> None:
    configured, bones = _signed_override_configuration()
    calculation = configured.steering_calculation
    assert calculation is not None
    stale = replace(
        configured,
        steering_calculation=replace(
            calculation, physical_bone_pairs=stale_pairs,
        ),
    )
    findings = validate_axle_configuration(stale, bones)
    assert any(
        item.severity == "error"
        and item.code == "steering_layout_override_evidence"
        for item in findings
    )
