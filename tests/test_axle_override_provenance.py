from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    PRESET_STEER_DRIVE_REAR,
    AxleConfiguration,
    apply_axle_preset,
    apply_intentional_layout_override,
    detect_axle_configuration,
    joaat_hex,
    retarget_axle_configuration,
    validate_axle_configuration,
)
from allin1_sdk.axle_runtime_bundler import (
    RUNTIME_GEOMETRY_RUNTIME_VERSION,
    TARGET_CAPABILITIES,
    TARGET_FIVEM_LEGACY,
    VehicleAxleBuildInput,
    compatibility_configuration,
    story_native_runtime_configuration,
)
from allin1_sdk.axle_steering_geometry import (
    apply_manual_steering_gains_to_configuration,
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


def _metrobus_runtime_bones() -> tuple[Bone, ...]:
    """Anonymized positions captured from the six-wheel regression vehicle."""

    return tuple(
        Bone(name, (x, y, -0.54))
        for left, right, y in (
            ("wheel_lm1", "wheel_rm1", 4.4533),
            ("wheel_lf", "wheel_rf", -4.0748),
            ("wheel_lr", "wheel_rr", -5.4140),
        )
        for name, x in ((left, -1.17), (right, 1.17))
    )


def _metrobus_override_configuration() -> tuple[AxleConfiguration, tuple[Bone, ...]]:
    bones = _metrobus_runtime_bones()
    detected = detect_axle_configuration(
        "metrobusxl2", bones, export_mode=EXPORT_FIVEM_RUNTIME,
        target="story-legacy",
    )
    overridden = apply_intentional_layout_override(
        detected,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
        reason="Reviewed middle/front/rear visual-instancing workaround",
    )
    return apply_axle_preset(overridden, PRESET_STEER_DRIVE_REAR), bones


def test_custom_order_cannot_export_multi_steer_without_fresh_calculation() -> None:
    configured, bones = _metrobus_override_configuration()

    findings = validate_axle_configuration(
        configured, bones, target="story-legacy",
    )
    assert any(
        finding.severity == "error"
        and finding.code == "layout_override_steering_calculation_required"
        for finding in findings
    )
    with pytest.raises(ValueError, match="fresh automatic calculation"):
        story_native_runtime_configuration(configured, bones=bones)


def test_story_export_marks_automatic_authoring_gains_non_authoritative() -> None:
    configured, bones = _signed_override_configuration()
    configured = retarget_axle_configuration(configured, "story-legacy")
    # This authoring extraction normalizes the physically rear pair. The
    # loaded game's authoritative transforms may differ, so these gains must
    # never be silently treated as final by the native runtime.
    assert [axle.steering_gain for axle in configured.axles] == pytest.approx(
        [0.22776979648028195, 0.0, -1.0], abs=1.0e-9,
    )

    payload = story_native_runtime_configuration(configured, bones=bones)
    assert payload["minimumRuntimeVersion"] == RUNTIME_GEOMETRY_RUNTIME_VERSION

    schema = json.loads((
        Path(__file__).resolve().parents[1]
        / "runtime" / "VehicleWorkbenchAxles" / "schemas"
        / "axle-config.schema.json"
    ).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)

    assert [row["steeringGain"] for row in payload["axles"]] == pytest.approx(
        [0.22776979648028195, 0.0, -1.0], abs=1.0e-9,
    )
    evidence = payload["steeringCalculation"]
    assert evidence["runtimeRecompute"] is True
    assert evidence["referenceSelection"] == "farthest_steered_axle"
    assert evidence["referenceAxleOrder"] == 2
    assert evidence["pivotAxleOrders"] == [1]
    assert evidence["physicalBonePairs"] == [
        ["wheel_lm1", "wheel_rm1"],
        ["wheel_lf", "wheel_rf"],
        ["wheel_lr", "wheel_rr"],
    ]

    # Native transport hints are accepted on re-import, validated, and then
    # deliberately discarded from the portable authoring provenance.
    imported = AxleConfiguration.from_dict(payload)
    assert imported.export_mode == "selective_runtime"
    portable = imported.to_dict()["steering_calculation"]
    assert portable["mode"] == "automatic_geometry"
    assert "runtimeRecompute" not in portable
    assert "referenceSelection" not in portable
    assert portable["reference_axle_order"] == 3

    reexported = story_native_runtime_configuration(imported, bones=bones)
    assert reexported["modelName"] == payload["modelName"]
    assert reexported["wheelIndexMapping"] == payload["wheelIndexMapping"]
    assert reexported["steeringCalculation"]["runtimeRecompute"] is True
    assert reexported["steeringCalculation"]["referenceSelection"] == (
        "farthest_steered_axle"
    )

    with pytest.raises(ValueError, match="reviewed canonical wheel-bone positions"):
        story_native_runtime_configuration(configured)


def test_story_export_locks_reviewed_geometry_without_live_position_access() -> None:
    configured, bones = _signed_override_configuration()
    configured = retarget_axle_configuration(configured, "story-enhanced")

    payload = story_native_runtime_configuration(
        configured,
        bones=bones,
        runtime_geometry_recompute=False,
    )

    schema = json.loads((
        Path(__file__).resolve().parents[1]
        / "runtime" / "VehicleWorkbenchAxles" / "schemas"
        / "axle-config.schema.json"
    ).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)

    assert [row["steeringGain"] for row in payload["axles"]] == pytest.approx(
        [0.22776979648028195, 0.0, -1.0], abs=1.0e-9,
    )
    evidence = payload["steeringCalculation"]
    assert evidence["mode"] == "automaticGeometry"
    assert evidence["runtimeRecompute"] is False
    assert "referenceSelection" not in evidence
    assert evidence["referenceAxleOrder"] == 2
    assert evidence["pivotAxleOrders"] == [1]
    AxleConfiguration.from_dict(payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"runtimeRecompute": "yes"}, "must be a boolean"),
        (
            {
                "runtimeRecompute": False,
                "referenceSelection": "farthest_steered_axle",
            },
            "requires runtime recompute",
        ),
        (
            {
                "runtimeRecompute": True,
                "referenceSelection": "first_steered_axle",
            },
            "must be farthest_steered_axle",
        ),
    ),
)
def test_story_runtime_transport_hints_fail_closed(
    updates: dict[str, object], message: str,
) -> None:
    configured, bones = _signed_override_configuration()
    configured = retarget_axle_configuration(configured, "story-legacy")
    payload = story_native_runtime_configuration(configured, bones=bones)
    calculation = dict(payload["steeringCalculation"])
    calculation.update(updates)
    payload["steeringCalculation"] = calculation

    with pytest.raises(ValueError, match=message):
        AxleConfiguration.from_dict(payload)


def test_story_runtime_recompute_requires_reference_selection() -> None:
    configured, bones = _signed_override_configuration()
    configured = retarget_axle_configuration(configured, "story-legacy")
    payload = story_native_runtime_configuration(configured, bones=bones)
    payload["steeringCalculation"].pop("referenceSelection")

    with pytest.raises(
        ValueError,
        match="requires farthest-steered-axle reference selection",
    ):
        AxleConfiguration.from_dict(payload)


def test_story_export_preserves_explicit_manual_steering_gains() -> None:
    configured, bones = _metrobus_override_configuration()
    manual = apply_manual_steering_gains_to_configuration(
        configured, bones, {1: 0.42, 2: 0.0, 3: -0.18},
    )

    payload = story_native_runtime_configuration(manual, bones=bones)
    assert payload["minimumRuntimeVersion"] == manual.minimum_runtime_version

    assert [row["steeringGain"] for row in payload["axles"]] == pytest.approx(
        [0.42, 0.0, -0.18], abs=1.0e-9,
    )
    assert payload["steeringCalculation"] == {
        "mode": "manual",
        "algorithmVersion": 1,
        "bonePositionSha256": manual.steering_calculation.bone_position_sha256,
        "runtimeRecompute": False,
        "physicalBonePairs": [
            ["wheel_lm1", "wheel_rm1"],
            ["wheel_lf", "wheel_rf"],
            ["wheel_lr", "wheel_rr"],
        ],
    }
