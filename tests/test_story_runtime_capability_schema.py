from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "runtime" / "VehicleWorkbenchAxles" / "schemas"
RUNTIME_ROOT = ROOT / "runtime" / "VehicleWorkbenchAxles"


def _validator(name: str) -> Draft202012Validator:
    payload = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


def _profile() -> dict[str, object]:
    return {
        "profile_id": "allin1.story-legacy.fixture",
        "target_id": "story-legacy",
        "binary_path": "VehicleWorkbenchAxles.asi",
        "version": "1.0.0",
        "supported_game_builds": ["build-123"],
        "expected_sha256": "0" * 64,
        "package_eligible": True,
        "validation_receipt_path": "receipt.json",
        "expected_receipt_sha256": "1" * 64,
        "redistribution_allowed": True,
        "license": "Test fixture",
    }


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "receipt_id": "receipt-story-legacy",
        "profile_id": "allin1.story-legacy.fixture",
        "runtime_name": "VehicleWorkbenchAxles",
        "target_id": "story-legacy",
        "runtime_version": "1.0.0",
        "binary_sha256": "0" * 64,
        "binary_architecture": "x64",
        "supported_game_builds": ["build-123"],
        "maximum_axle_schema": 1,
        "descriptor_abi_version": 1,
        "required_exports": [
            "VehicleWorkbenchAxles_GetDescriptor",
            "VehicleWorkbenchAxles_HasValidatedProfile",
        ],
        "validated_profile_export_result": True,
        "acceptance_tests": {
            "front_steer": "passed",
            "selective_drive": "passed",
            "rear_steer": "passed",
            "unrelated_flags_preserved": "passed",
            "repair_reapplication": "passed",
            "unsupported_build_fail_closed": "passed",
            "online_session_guard": "passed",
        },
        "validation_authority": "Test fixture",
        "accepted_at": "2026-08-26T12:00:00Z",
        "package_eligible": True,
        "redistribution_allowed": True,
        "license": "Test fixture",
    }


def test_old_story_profile_and_receipt_shapes_remain_valid() -> None:
    assert list(_validator("story-runtime-profile.schema.json").iter_errors(
        _profile()
    )) == []
    assert list(_validator("story-runtime-receipt.schema.json").iter_errors(
        _receipt()
    )) == []


def test_schema_three_support_capability_requires_complete_acceptance_matrix() -> None:
    profile = _profile()
    profile.update({
        "maximum_axle_schema": 3,
        "capabilities": {
            "signed_steering_gain": False,
            "static_force": True,
            "physics_activation": True,
        },
    })
    assert list(_validator("story-runtime-profile.schema.json").iter_errors(
        profile
    )) == []

    receipt = _receipt()
    receipt.update({
        "maximum_axle_schema": 3,
        "capabilities": deepcopy(profile["capabilities"]),
    })
    assert list(_validator("story-runtime-receipt.schema.json").iter_errors(
        receipt
    ))
    receipt["acceptance_tests"].update({
        "support_bias_apply_readback": "passed",
        "support_bias_total_preserved": "passed",
        "support_bias_left_right_preserved": "passed",
        "support_bias_repair_reapplication": "passed",
        "support_bias_transaction_rollback": "passed",
        "support_bias_unload_restore": "passed",
        "support_bias_unsupported_fail_closed": "passed",
        "support_bias_physics_activation_fail_closed": "passed",
    })
    assert list(_validator("story-runtime-receipt.schema.json").iter_errors(
        receipt
    )) == []


def test_signed_steering_capability_requires_schema_two_and_mapping_tests() -> None:
    receipt = _receipt()
    receipt["capabilities"] = {
        "signed_steering_gain": True,
        "static_force": False,
        "physics_activation": False,
    }
    validator = _validator("story-runtime-receipt.schema.json")
    assert list(validator.iter_errors(receipt))
    receipt["maximum_axle_schema"] = 2
    receipt["acceptance_tests"].update({
        "signed_steering_gain_apply_readback": "passed",
        "intentional_layout_override_mapping": "passed",
    })
    assert list(validator.iter_errors(receipt)) == []


def test_asi_descriptor_uses_the_central_runtime_schema_version() -> None:
    header = (
        RUNTIME_ROOT
        / "include"
        / "vehicle_workbench_axles"
        / "configuration.hpp"
    ).read_text(encoding="utf-8")
    asi_source = (RUNTIME_ROOT / "src" / "asi_entry.cpp").read_text(
        encoding="utf-8"
    )
    package = json.loads(
        (RUNTIME_ROOT / "profiles" / "runtime-package.json").read_text(
            encoding="utf-8"
        )
    )
    match = re.search(r"kAxleSchemaVersion\s*=\s*(\d+)", header)

    assert match is not None
    assert "vwa::kAxleSchemaVersion" in asi_source
    assert int(match.group(1)) == package["runtime"]["maximumAxleSchemaVersion"]
