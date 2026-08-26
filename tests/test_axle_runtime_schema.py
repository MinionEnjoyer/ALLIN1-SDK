from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    AxleConfiguration,
    apply_intentional_layout_override,
    detect_axle_configuration,
    joaat_hex,
)
from allin1_sdk.axle_runtime_bundler import (
    TARGET_STORY_LEGACY,
    VehicleAxleBuildInput,
    compatibility_configuration,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "runtime" / "VehicleWorkbenchAxles" / "schemas" / "axle-config.schema.json"


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _schema() -> dict[str, object]:
    payload = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return payload


def _custom_order_payload() -> dict[str, object]:
    bones = tuple(
        Bone(name, (x, y, -0.5))
        for left, right, y in (
            ("wheel_lm1", "wheel_rm1", 7.0),
            ("wheel_lf", "wheel_rf", 0.0),
            ("wheel_lr", "wheel_rr", -2.0),
        )
        for name, x in ((left, -1.2), (right, 1.2))
    )
    detected = detect_axle_configuration(
        "synthetic_custom_order_vehicle",
        bones,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    configured = apply_intentional_layout_override(
        detected,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
        reason="Synthetic custom wheel-family order for schema validation",
    )
    vehicle = VehicleAxleBuildInput(
        configuration=configured,
        configuration_id=configured.configuration_id,
        model_hash=joaat_hex(configured.vehicle_model),
        minimum_runtime_version=configured.minimum_runtime_version,
        steering_evidence_bones=bones,
    )
    return compatibility_configuration(vehicle, TARGET_STORY_LEGACY)


def _validation_messages(payload: dict[str, object]) -> list[str]:
    validator = Draft202012Validator(_schema())
    return [error.message for error in validator.iter_errors(payload)]


def _schema_three_payload(*, signed: bool) -> dict[str, object]:
    payload = deepcopy(_custom_order_payload())
    payload["schemaVersion"] = 3
    payload["minimumRuntimeVersion"] = "3.0.0"
    for axle in payload["axles"]:
        axle["steeringGain"] = 0.0
        axle["suspension"] = {"supportWeight": 1.0}
    if signed:
        payload["axles"][0]["steered"] = True
        payload["axles"][0]["steeringGain"] = -0.5
        override = payload["intentionalLayoutOverride"]
        payload["steeringCalculation"] = {
            "mode": "manual",
            "algorithmVersion": 1,
            "bonePositionSha256": override["bonePositionSha256"],
            "physicalBonePairs": deepcopy(override["physicalBonePairs"]),
        }
    else:
        payload.pop("steeringCalculation", None)
    return payload


def test_python_custom_order_payload_matches_native_runtime_schema() -> None:
    payload = _custom_order_payload()

    assert payload["intentionalLayoutOverride"] == {
        "mode": "visual_instancing_remap",
        "physicalBonePairs": [
            ["wheel_lm1", "wheel_rm1"],
            ["wheel_lf", "wheel_rf"],
            ["wheel_lr", "wheel_rr"],
        ],
        "bonePositionSha256": payload["intentionalLayoutOverride"][
            "bonePositionSha256"
        ],
        "reason": "Synthetic custom wheel-family order for schema validation",
    }
    assert _validation_messages(payload) == []
    imported = AxleConfiguration.from_dict(payload)
    assert imported.intentional_layout_override is not None
    assert imported.intentional_layout_override.physical_bone_pairs == (
        ("wheel_lm1", "wheel_rm1"),
        ("wheel_lf", "wheel_rf"),
        ("wheel_lr", "wheel_rr"),
    )


def test_python_runtime_import_accepts_tag_role_on_an_interior_axle() -> None:
    payload = _custom_order_payload()
    payload["axles"][1]["role"] = "tag"

    imported = AxleConfiguration.from_dict(payload)

    assert imported.axles[1].logical_role == "tag"


def test_schema_four_inverted_polarity_keeps_base_gains_explicit() -> None:
    payload = _custom_order_payload()
    payload["schemaVersion"] = 4
    payload["minimumRuntimeVersion"] = "4.0.0"
    payload["steeringCommandPolarity"] = "inverted"
    payload.pop("steeringCalculation", None)
    for row in payload["axles"]:
        row["steeringGain"] = 1.0 if row["steered"] else 0.0
    assert payload["schemaVersion"] == 4
    assert payload["steeringCommandPolarity"] == "inverted"
    assert "steeringCalculation" not in payload
    assert _validation_messages(payload) == []


@pytest.mark.parametrize("signed", (False, True))
def test_runtime_schema_accepts_valid_schema_three_steering_modes(
    signed: bool,
) -> None:
    assert _validation_messages(_schema_three_payload(signed=signed)) == []


def test_runtime_schema_requires_evidence_for_signed_schema_three() -> None:
    payload = _schema_three_payload(signed=True)
    payload.pop("steeringCalculation")

    assert _validation_messages(payload)


def test_runtime_schema_rejects_evidence_for_legacy_schema_three() -> None:
    payload = _schema_three_payload(signed=False)
    override = payload["intentionalLayoutOverride"]
    payload["steeringCalculation"] = {
        "mode": "manual",
        "algorithmVersion": 1,
        "bonePositionSha256": override["bonePositionSha256"],
        "physicalBonePairs": deepcopy(override["physicalBonePairs"]),
    }

    assert _validation_messages(payload)


def test_runtime_schema_binds_layout_pair_evidence_presence_both_ways() -> None:
    pairs_without_override = _schema_three_payload(signed=True)
    pairs_without_override.pop("intentionalLayoutOverride")
    assert _validation_messages(pairs_without_override)

    override_without_pairs = _schema_three_payload(signed=True)
    override_without_pairs["steeringCalculation"].pop("physicalBonePairs")
    assert _validation_messages(override_without_pairs)


@pytest.mark.parametrize(
    "override_update",
    (
        {"mode": "automatic"},
        {"physicalBonePairs": [["wheel_lf", "wheel_lr"]]},
        {
            "physicalBonePairs": [
                ["wheel_lm1", "wheel_rm1"],
                ["wheel_lm1", "wheel_rm1"],
                ["wheel_lr", "wheel_rr"],
            ],
        },
        {"bonePositionSha256": "A" * 64},
        {"reason": "invalid\nreason"},
        {"unexpected": True},
    ),
)
def test_runtime_schema_rejects_malformed_layout_override(
    override_update: dict[str, object],
) -> None:
    payload = deepcopy(_custom_order_payload())
    payload["intentionalLayoutOverride"].update(override_update)

    assert _validation_messages(payload)


def test_runtime_schema_rejects_override_pair_count_mismatched_to_axles() -> None:
    payload = deepcopy(_custom_order_payload())
    payload["intentionalLayoutOverride"]["physicalBonePairs"] = payload[
        "intentionalLayoutOverride"
    ]["physicalBonePairs"][:2]

    assert _validation_messages(payload)
