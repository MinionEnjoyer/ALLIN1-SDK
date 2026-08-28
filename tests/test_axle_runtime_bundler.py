from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import execute_request
from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    AXLE_SUPPORT_RUNTIME_VERSION,
    AXLE_SUPPORT_SCHEMA_VERSION,
    SIGNED_STEERING_SCHEMA_VERSION,
    INTENTIONAL_LAYOUT_RUNTIME_VERSION,
    SIGNED_STEERING_RUNTIME_VERSION,
    STEERING_CALCULATION_MANUAL,
    EXPORT_FIVEM_RUNTIME,
    EXPORT_STOCK_METADATA,
    PRESET_CUSTOM,
    VISUAL_FRONT,
    VISUAL_SHARED_MIDDLE_REAR,
    AxleConfiguration,
    SteeringCalculationProvenance,
    VehicleAxle,
    apply_axle_support_weights,
    apply_intentional_layout_override,
    joaat_hex,
)
from allin1_sdk.axle_runtime_bundler import (
    ACCEPTANCE_PENDING,
    FIVEM_RUNTIME_NAME,
    RUNTIME_GEOMETRY_RUNTIME_VERSION,
    STATUS_OMITTED,
    STATUS_READY,
    STORY_RUNTIME_REQUIRED_EXPORTS,
    STORY_RUNTIME_NAME,
    TARGET_CAPABILITIES,
    TARGET_FIVEM_ENHANCED,
    TARGET_FIVEM_LEGACY,
    TARGET_IDS,
    TARGET_STORY_ENHANCED,
    TARGET_STORY_LEGACY,
    AxleRuntimeBundleBuilder,
    AxleRuntimeBundlePlanner,
    DependencyDeclaration,
    ExternalToolApproval,
    RuntimeDependency,
    StoryRuntimeProfile,
    VehicleAxleBuildInput,
    compatibility_configuration,
    inspect_story_runtime_binary,
    resolve_runtime_wheel_map,
    select_newest_compatible_runtime,
    story_runtime_profile_report,
    target_capabilities,
)
from allin1_sdk.axle_steering_geometry import (
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.cli import main


PAIRS = {
    "front": ("front", "wheel_lf", "wheel_rf"),
    "middle1": ("middle", "wheel_lm1", "wheel_rm1"),
    "middle2": ("middle", "wheel_lm2", "wheel_rm2"),
    "middle3": ("middle", "wheel_lm3", "wheel_rm3"),
    "rear": ("rear", "wheel_lr", "wheel_rr"),
}


@dataclass(frozen=True)
class Bone:
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


def _bones() -> tuple[Bone, ...]:
    return tuple(
        Bone(name, (x, y, 0.0))
        for left, right, y in (
            ("wheel_lf", "wheel_rf", 8.0),
            ("wheel_lm1", "wheel_rm1", 0.0),
            ("wheel_lr", "wheel_rr", -2.0),
        )
        for name, x in ((left, -1.25), (right, 1.25))
    )


def _config(
    names: tuple[str, ...] = ("front", "middle1", "rear"),
    *,
    model: str = "example_bus",
    steered: frozenset[int] = frozenset({1, 3}),
    powered: frozenset[int] = frozenset({2}),
) -> AxleConfiguration:
    axles = []
    for ordinal, name in enumerate(names, start=1):
        role, left, right = PAIRS[name]
        axles.append(VehicleAxle(
            physical_order=ordinal,
            logical_role=role,
            left_bone=left,
            right_bone=right,
            # Deliberately not derived from physical order. The target resolver
            # must replace these authoring placeholders.
            left_runtime_index=50 + (ordinal * 2),
            right_runtime_index=51 + (ordinal * 2),
            steered=ordinal in steered,
            powered=ordinal in powered,
            service_brake=True,
            handbrake=role == "rear",
            visual_family=(
                VISUAL_FRONT if role == "front" else VISUAL_SHARED_MIDDLE_REAR
            ),
        ))
    return AxleConfiguration(
        schema_version=AXLE_SCHEMA_VERSION,
        vehicle_model=model,
        preset=PRESET_CUSTOM,
        export_mode=EXPORT_STOCK_METADATA,
        axles=tuple(axles),
        compatibility=tuple((target, True) for target in TARGET_IDS),
    )


def _vehicle(
    names: tuple[str, ...] = ("front", "middle1", "rear"),
    *,
    model: str = "example_bus",
    model_hash: int | str | None = None,
    asset_source: Path | None = None,
    **config_kwargs,
) -> VehicleAxleBuildInput:
    return VehicleAxleBuildInput(
        configuration=_config(names, model=model, **config_kwargs),
        configuration_id=f"{model}-axles",
        model_hash=model_hash if model_hash is not None else joaat_hex(model),
        asset_source=asset_source,
    )


def _signed_vehicle() -> VehicleAxleBuildInput:
    vehicle = _vehicle()
    bones = _bones()
    solution = solve_automatic_steering_geometry(
        vehicle.configuration, bones,
    )
    configuration = apply_steering_geometry_to_configuration(
        vehicle.configuration, solution,
    )
    return replace(
        vehicle,
        configuration=configuration,
        minimum_runtime_version=configuration.minimum_runtime_version,
        steering_evidence_bones=bones,
    )


def _manual_signed_vehicle() -> VehicleAxleBuildInput:
    vehicle = _signed_vehicle()
    calculation = vehicle.configuration.steering_calculation
    assert calculation is not None
    return replace(
        vehicle,
        configuration=replace(
            vehicle.configuration,
            steering_calculation=SteeringCalculationProvenance(
                mode=STEERING_CALCULATION_MANUAL,
                algorithm_version=calculation.algorithm_version,
                bone_position_sha256=calculation.bone_position_sha256,
            ),
        ),
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_x64_asi(
    path: Path, exports: tuple[str, ...] = STORY_RUNTIME_REQUIRED_EXPORTS,
) -> None:
    """Write a deterministic minimal PE32+ DLL with real executable exports."""
    data = bytearray(0x600)
    data[:2] = b"MZ"
    pe_offset = 0x80
    struct.pack_into("<I", data, 0x3C, pe_offset)
    data[pe_offset:pe_offset + 4] = b"PE\0\0"
    coff = pe_offset + 4
    struct.pack_into("<HHIIIHH", data, coff, 0x8664, 2, 0, 0, 0, 0xF0, 0x2022)
    optional = coff + 20
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<II", data, optional + 4, 0x200, 0x200)
    struct.pack_into("<I", data, optional + 20, 0x2000)
    struct.pack_into("<Q", data, optional + 24, 0x180000000)
    struct.pack_into("<II", data, optional + 32, 0x1000, 0x200)
    struct.pack_into("<II", data, optional + 56, 0x3000, 0x200)
    struct.pack_into("<H", data, optional + 68, 2)
    struct.pack_into("<QQQQ", data, optional + 72, 0x100000, 0x1000, 0x100000, 0x1000)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 112, 0x1000, 0x200)
    sections = optional + 0xF0
    data[sections:sections + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, sections + 8, 0x200, 0x1000, 0x200, 0x200)
    struct.pack_into("<I", data, sections + 36, 0x40000040)
    text = sections + 40
    data[text:text + 8] = b".text\0\0\0"
    struct.pack_into("<IIII", data, text + 8, 0x200, 0x2000, 0x200, 0x400)
    struct.pack_into("<I", data, text + 36, 0x60000020)

    count = len(exports)
    functions_rva = 0x1028
    names_rva = functions_rva + count * 4
    ordinals_rva = names_rva + count * 4
    cursor_rva = ordinals_rva + count * 2
    dll_name_rva = cursor_rva
    dll_name = b"VehicleWorkbenchAxles.asi\0"
    cursor_rva += len(dll_name)
    name_rvas = []
    encoded_names = []
    for name in exports:
        encoded = name.encode("ascii") + b"\0"
        name_rvas.append(cursor_rva)
        encoded_names.append(encoded)
        cursor_rva += len(encoded)
    assert cursor_rva < 0x1200

    export_raw = 0x200
    struct.pack_into(
        "<IIHHIIIIIII", data, export_raw,
        0, 0, 0, 0, dll_name_rva, 1, count, count,
        functions_rva, names_rva, ordinals_rva,
    )
    for index in range(count):
        struct.pack_into("<I", data, 0x200 + (functions_rva - 0x1000) + index * 4,
                         0x2000 + index)
        struct.pack_into("<I", data, 0x200 + (names_rva - 0x1000) + index * 4,
                         name_rvas[index])
        struct.pack_into("<H", data, 0x200 + (ordinals_rva - 0x1000) + index * 2,
                         index)
        data[0x400 + index] = 0xC3
    dll_raw = 0x200 + (dll_name_rva - 0x1000)
    data[dll_raw:dll_raw + len(dll_name)] = dll_name
    for name_rva, encoded in zip(name_rvas, encoded_names):
        raw = 0x200 + (name_rva - 0x1000)
        data[raw:raw + len(encoded)] = encoded
    path.write_bytes(data)


def _story_profile(
    tmp_path: Path,
    target: str,
    *,
    version: str = "1.0.0",
    redistribution_allowed: bool = True,
    maximum_axle_schema: int = 1,
    supports_axle_support_bias: bool = False,
    supports_signed_steering_gain: bool = False,
    supports_wheel_local_position: bool = False,
) -> StoryRuntimeProfile:
    binary = tmp_path / f"{target}.asi"
    _write_x64_asi(binary)
    profile_id = f"allin1.{target}.fixture"
    license_name = "ALLIN1 Vehicle Workbench Axle Runtime"
    receipt = tmp_path / f"{target}.receipt.json"
    acceptance_tests = {
        "front_steer": "passed",
        "selective_drive": "passed",
        "rear_steer": "passed",
        "unrelated_flags_preserved": "passed",
        "repair_reapplication": "passed",
        "unsupported_build_fail_closed": "passed",
        "online_session_guard": "passed",
    }
    if supports_axle_support_bias:
        acceptance_tests.update({
            "support_bias_apply_readback": "passed",
            "support_bias_total_preserved": "passed",
            "support_bias_left_right_preserved": "passed",
            "support_bias_repair_reapplication": "passed",
            "support_bias_transaction_rollback": "passed",
            "support_bias_unload_restore": "passed",
            "support_bias_unsupported_fail_closed": "passed",
            "support_bias_physics_activation_fail_closed": "passed",
        })
    if supports_signed_steering_gain:
        acceptance_tests.update({
            "signed_steering_gain_apply_readback": "passed",
            "intentional_layout_override_mapping": "passed",
        })
    if supports_wheel_local_position:
        acceptance_tests.update({
            "wheel_local_position_readback": "passed",
            "runtime_geometry_recompute": "passed",
            "runtime_geometry_unsupported_fail_closed": "passed",
        })
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "receipt_id": f"receipt-{target}",
        "profile_id": profile_id,
        "runtime_name": STORY_RUNTIME_NAME,
        "target_id": target,
        "runtime_version": version,
        "binary_sha256": _digest(binary),
        "binary_architecture": "x64",
        "supported_game_builds": ["build-123"],
        "maximum_axle_schema": maximum_axle_schema,
        "capabilities": {
            "signed_steering_gain": supports_signed_steering_gain,
            "static_force": supports_axle_support_bias,
            "physics_activation": supports_axle_support_bias,
            "wheel_local_position": supports_wheel_local_position,
        },
        "descriptor_abi_version": 1,
        "required_exports": list(STORY_RUNTIME_REQUIRED_EXPORTS),
        "validated_profile_export_result": True,
        "acceptance_tests": acceptance_tests,
        "validation_authority": "ALLIN1 native acceptance fixture",
        "accepted_at": "2026-08-25T12:00:00Z",
        "package_eligible": True,
        "redistribution_allowed": redistribution_allowed,
        "license": license_name,
    }, sort_keys=True), encoding="utf-8")
    return StoryRuntimeProfile(
        profile_id=profile_id,
        target_id=target,
        binary_path=binary,
        version=version,
        supported_game_builds=("build-123",),
        expected_sha256=_digest(binary),
        package_eligible=True,
        validation_receipt_path=receipt,
        expected_receipt_sha256=_digest(receipt),
        redistribution_allowed=redistribution_allowed,
        license_name=license_name,
        maximum_axle_schema=maximum_axle_schema,
        supports_signed_steering_gain=supports_signed_steering_gain,
        supports_static_force=supports_axle_support_bias,
        supports_physics_activation=supports_axle_support_bias,
        supports_wheel_local_position=supports_wheel_local_position,
    )


def _write_profile_document(profile: StoryRuntimeProfile, path: Path) -> Path:
    path.write_text(json.dumps({
        "profile_id": profile.profile_id,
        "target_id": profile.target_id,
        "binary_path": profile.binary_path.name,
        "version": profile.version,
        "supported_game_builds": list(profile.supported_game_builds),
        "expected_sha256": profile.expected_sha256,
        "package_eligible": profile.package_eligible,
        "validation_receipt_path": profile.validation_receipt_path.name,
        "expected_receipt_sha256": profile.expected_receipt_sha256,
        "redistribution_allowed": profile.redistribution_allowed,
        "license": profile.license_name,
        "maximum_axle_schema": profile.maximum_axle_schema,
        "capabilities": {
            "signed_steering_gain": profile.supports_signed_steering_gain,
            "static_force": profile.supports_static_force,
            "physics_activation": profile.supports_physics_activation,
            "wheel_local_position": profile.supports_wheel_local_position,
        },
    }, sort_keys=True), encoding="utf-8")
    return path


class _FakeConverter:
    converter_id = "test-converter"
    supported_targets = frozenset({TARGET_FIVEM_ENHANCED})

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    def convert(self, source, destination, *, target_id, tool):
        self.calls.append((source, destination, target_id, tool.tool_id))
        if self.fail:
            raise RuntimeError("conversion failed")
        (destination / "converted.rpf").write_bytes(b"gen9")
        return {"converter": self.converter_id, "validated": True}


def _approval(tmp_path: Path) -> ExternalToolApproval:
    executable = tmp_path / "converter.exe"
    executable.write_bytes(b"external-tool-fixture")
    return ExternalToolApproval(
        tool_id="test-converter",
        executable=executable,
        approved=True,
        source_url="https://example.invalid/converter",
        license_name="Test-only external fixture",
    )


def test_four_explicit_capability_targets_are_pending_acceptance() -> None:
    assert tuple(TARGET_CAPABILITIES) == TARGET_IDS
    for target in TARGET_IDS:
        capabilities = target_capabilities(target)
        assert capabilities.supports_runtime_wheel_flags is True
        assert capabilities.supports_selective_steering is True
        assert capabilities.supports_selective_drive is True
        assert capabilities.supports_signed_steering_gain is False
        assert capabilities.supports_current_axle_schema is False
        assert capabilities.minimum_physical_axles == 2
        assert capabilities.maximum_physical_axles == 5
        assert capabilities.acceptance_status == ACCEPTANCE_PENDING
        assert capabilities.published_supported is False
    assert target_capabilities(TARGET_FIVEM_ENHANCED).requires_asset_conversion
    assert target_capabilities(TARGET_STORY_LEGACY).requires_scripthookv
    with pytest.raises(ValueError, match="Unknown axle bundle target"):
        target_capabilities("online")


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("front", "rear"), ("wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr")),
        (("front", "middle1", "rear"), (
            "wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr", "wheel_lm1", "wheel_rm1",
        )),
        (("front", "middle1", "middle2", "rear"), (
            "wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr",
            "wheel_lm1", "wheel_rm1", "wheel_lm2", "wheel_rm2",
        )),
        (("front", "middle1", "middle2", "middle3", "rear"), (
            "wheel_lf", "wheel_rf", "wheel_lr", "wheel_rr",
            "wheel_lm1", "wheel_rm1", "wheel_lm2", "wheel_rm2",
            "wheel_lm3", "wheel_rm3",
        )),
    ],
)
def test_semantic_mapping_supports_two_through_five_physical_axles(
    names: tuple[str, ...], expected: tuple[str, ...],
) -> None:
    resolved = resolve_runtime_wheel_map(_vehicle(names), TARGET_FIVEM_LEGACY)
    assert tuple(resolved.by_bone) == expected
    assert tuple(resolved.by_bone.values()) == tuple(range(len(expected)))
    assert resolved.reported_wheel_count == len(expected)
    assert "gta_canonical_slots_v2" in resolved.source


def test_explicit_export_mapping_wins_and_is_wheel_count_validated() -> None:
    vehicle = _vehicle()
    bones = (
        "wheel_lf", "wheel_rf", "wheel_lm1", "wheel_rm1", "wheel_lr", "wheel_rr",
    )
    exported = {bone: index for bone, index in zip(bones, (2, 3, 0, 1, 4, 5))}
    vehicle = replace(
        vehicle, exported_wheel_indices=exported, reported_wheel_count=6,
    )
    resolved = resolve_runtime_wheel_map(vehicle, TARGET_STORY_LEGACY)
    assert resolved.source == "exported_vehicle_information"
    assert resolved.by_bone["wheel_lm1"] == 0
    with pytest.raises(ValueError, match="game reports 5"):
        resolve_runtime_wheel_map(
            replace(vehicle, reported_wheel_count=5), TARGET_STORY_LEGACY,
        )
    with pytest.raises(ValueError, match="omitted wheel bones"):
        resolve_runtime_wheel_map(
            replace(vehicle, exported_wheel_indices={"wheel_lf": 0}),
            TARGET_STORY_LEGACY,
        )


def test_dual_tyres_remain_cosmetic_and_do_not_change_wheel_count() -> None:
    vehicle = replace(
        _vehicle(), dual_tyre_geometry=("rear_dual_left.ydr", "rear_dual_right.ydr"),
    )
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_LEGACY,),
    )
    assert plan.targets[0].status == STATUS_READY
    payload = plan.targets[0].configurations[0].runtime_payload
    assert payload["expectedWheelCount"] == 6
    assert payload["dualTyresConsumePhysicalSlots"] is False
    assert len(payload["dualTyreGeometry"]) == 2


def test_more_than_five_axles_is_a_cosmetic_or_future_physics_case() -> None:
    base = _config(("front", "middle1", "middle2", "middle3", "rear"))
    extra = replace(
        base.axles[-1], physical_order=6,
        left_bone="wheel_lm1", right_bone="wheel_rm1",
    )
    vehicle = VehicleAxleBuildInput(
        replace(base, axles=base.axles + (extra,)), "too-many", 1,
    )
    with pytest.raises(ValueError, match="cosmetic geometry or a future custom-physics"):
        resolve_runtime_wheel_map(vehicle, TARGET_FIVEM_LEGACY)


def test_six_wheel_fixture_exports_steer_drive_rear_steer_without_index_formula() -> None:
    payload = compatibility_configuration(_vehicle(), TARGET_FIVEM_LEGACY)
    assert payload["compatibility"] == {TARGET_FIVEM_LEGACY: True}
    assert [axle["wheelIndices"] for axle in payload["axles"]] == [[0, 1], [4, 5], [2, 3]]
    assert [axle["steered"] for axle in payload["axles"]] == [True, False, True]
    assert all("steeringGain" not in axle for axle in payload["axles"])
    assert [axle["visualFamily"] for axle in payload["axles"]] == [
        VISUAL_FRONT, VISUAL_SHARED_MIDDLE_REAR, VISUAL_SHARED_MIDDLE_REAR,
    ]
    assert [axle["powered"] for axle in payload["axles"]] == [False, True, False]
    assert payload["handling"]["setHandlingFlags"] == ["HF_STEER_ALL_WHEELS"]
    assert "fDriveBiasFront" in payload["handling"]["driveBiasRequirement"]
    assert all("0x08" not in json.dumps(axle) for axle in payload["axles"])


def test_custom_physical_order_is_evidence_bound_and_clears_global_steering_flags() -> None:
    bones = tuple(
        Bone(name, (x, y, 0.0))
        for left, right, y in (
            ("wheel_lm1", "wheel_rm1", 8.0),
            ("wheel_lf", "wheel_rf", 0.0),
            ("wheel_lr", "wheel_rr", -2.0),
        )
        for name, x in ((left, -1.25), (right, 1.25))
    )
    base = replace(_config(), export_mode=EXPORT_FIVEM_RUNTIME)
    remapped = apply_intentional_layout_override(
        base,
        bones,
        physical_bone_pairs=(
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lf", "wheel_rf"),
            ("wheel_lr", "wheel_rr"),
        ),
        reason="Author-reviewed single/dual/single wheel-family layout",
    )
    vehicle = VehicleAxleBuildInput(
        configuration=remapped,
        configuration_id="example-bus-remapped",
        model_hash=joaat_hex(remapped.vehicle_model),
        minimum_runtime_version=remapped.minimum_runtime_version,
        steering_evidence_bones=bones,
    )

    payload = compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)

    assert [row["leftBone"] for row in payload["axles"]] == [
        "wheel_lm1", "wheel_lf", "wheel_lr",
    ]
    assert [row["wheelIndices"] for row in payload["axles"]] == [
        [4, 5], [0, 1], [2, 3],
    ]
    assert payload["intentionalLayoutOverride"]["physicalBonePairs"] == [
        ["wheel_lm1", "wheel_rm1"],
        ["wheel_lf", "wheel_rf"],
        ["wheel_lr", "wheel_rr"],
    ]
    assert payload["handling"]["setHandlingFlags"] == []
    assert payload["handling"]["clearHandlingFlags"] == [
        "HF_STEER_REARWHEELS", "HF_STEER_ALL_WHEELS",
        "HF_HANDBRAKE_REARWHEELSTEER",
    ]
    assert payload["minimumRuntimeVersion"] == INTENTIONAL_LAYOUT_RUNTIME_VERSION


def test_story_runtime_payload_declares_only_its_exact_native_target() -> None:
    payload = compatibility_configuration(_vehicle(), TARGET_STORY_LEGACY)
    assert payload["compatibility"] == {TARGET_STORY_LEGACY: True}


def test_signed_gain_is_encoded_but_unsupported_targets_fail_closed() -> None:
    vehicle = _vehicle()
    axles = list(vehicle.configuration.axles)
    axles[-1] = replace(axles[-1], steering_gain=-0.22)
    vehicle = replace(
        vehicle,
        minimum_runtime_version=SIGNED_STEERING_RUNTIME_VERSION,
        configuration=replace(
            vehicle.configuration,
            schema_version=SIGNED_STEERING_SCHEMA_VERSION,
            minimum_runtime_version=SIGNED_STEERING_RUNTIME_VERSION,
            axles=tuple(axles),
            steering_calculation=SteeringCalculationProvenance(
                mode=STEERING_CALCULATION_MANUAL,
                bone_position_sha256="0" * 64,
            ),
        ),
    )
    with pytest.raises(ValueError, match="signed steering-gain accessor"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_LEGACY,),
    )
    assert plan.targets[0].status == STATUS_OMITTED
    assert any(
        "counter-steer or scaled steering was not packaged" in reason
        for reason in plan.targets[0].reasons
    )


def test_axle_support_bias_is_typed_and_requires_explicit_target_capability(
    monkeypatch,
) -> None:
    vehicle = _vehicle()
    supported = apply_axle_support_weights(
        vehicle.configuration, {1: 1.10, 2: 0.95, 3: 0.95},
    )
    vehicle = replace(
        vehicle,
        configuration=supported,
        minimum_runtime_version=AXLE_SUPPORT_RUNTIME_VERSION,
    )

    with pytest.raises(ValueError, match="suspension support accessor"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)
    omitted = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_LEGACY,),
    )
    assert omitted.targets[0].status == STATUS_OMITTED
    assert any(
        "support bias was not packaged" in reason
        for reason in omitted.targets[0].reasons
    )

    monkeypatch.setitem(
        TARGET_CAPABILITIES,
        TARGET_FIVEM_LEGACY,
        replace(
            TARGET_CAPABILITIES[TARGET_FIVEM_LEGACY],
            maximum_axle_schema=AXLE_SUPPORT_SCHEMA_VERSION,
            supports_axle_support_bias=True,
            runtime_implementation_version=AXLE_SUPPORT_RUNTIME_VERSION,
        ),
    )
    payload = compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)

    assert payload["schemaVersion"] == AXLE_SUPPORT_SCHEMA_VERSION
    assert payload["minimumRuntimeVersion"] == AXLE_SUPPORT_RUNTIME_VERSION
    assert [
        row["suspension"]["supportWeight"] for row in payload["axles"]
    ] == pytest.approx([1.10, 0.95, 0.95])
    assert all("steeringGain" in row for row in payload["axles"])
    assert "steeringCalculation" not in payload


def test_validated_signed_target_emits_schema_two_evidence(monkeypatch) -> None:
    vehicle = _signed_vehicle()
    monkeypatch.setitem(
        TARGET_CAPABILITIES,
        TARGET_FIVEM_LEGACY,
        replace(
            TARGET_CAPABILITIES[TARGET_FIVEM_LEGACY],
            maximum_axle_schema=2,
            supports_signed_steering_gain=True,
            runtime_implementation_version="2.0.0",
        ),
    )

    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_LEGACY,),
    )
    assert plan.targets[0].status == STATUS_READY
    payload = plan.targets[0].configurations[0].runtime_payload

    assert payload["schemaVersion"] == 2
    assert payload["minimumRuntimeVersion"] == "2.0.0"
    assert [item["steeringGain"] for item in payload["axles"]] == pytest.approx([
        axle.steering_gain for axle in vehicle.configuration.axles
    ])
    assert payload["steeringCalculation"]["mode"] == "automaticGeometry"
    assert payload["steeringCalculation"]["bonePositionSha256"] == (
        vehicle.configuration.steering_calculation.bone_position_sha256
    )
    assert payload["steeringCalculation"]["pairPositionTolerance"] == 0.25
    assert payload["steeringCalculation"]["positionEpsilon"] == 0.0001


def test_signed_target_rejects_missing_skeleton_evidence(monkeypatch) -> None:
    vehicle = replace(_signed_vehicle(), steering_evidence_bones=())
    monkeypatch.setitem(
        TARGET_CAPABILITIES,
        TARGET_FIVEM_LEGACY,
        replace(
            TARGET_CAPABILITIES[TARGET_FIVEM_LEGACY],
            maximum_axle_schema=2,
            supports_signed_steering_gain=True,
            runtime_implementation_version="2.0.0",
        ),
    )

    with pytest.raises(ValueError, match="skeleton-bone evidence"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)


def test_signed_target_rejects_forged_geometry_digest(monkeypatch) -> None:
    vehicle = _signed_vehicle()
    calculation = vehicle.configuration.steering_calculation
    assert calculation is not None
    vehicle = replace(
        vehicle,
        configuration=replace(
            vehicle.configuration,
            steering_calculation=replace(
                calculation, bone_position_sha256="0" * 64,
            ),
        ),
    )
    monkeypatch.setitem(
        TARGET_CAPABILITIES,
        TARGET_FIVEM_LEGACY,
        replace(
            TARGET_CAPABILITIES[TARGET_FIVEM_LEGACY],
            maximum_axle_schema=2,
            supports_signed_steering_gain=True,
            runtime_implementation_version="2.0.0",
        ),
    )

    with pytest.raises(ValueError, match="positions changed"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_LEGACY,),
    )
    assert plan.targets[0].status == STATUS_OMITTED
    assert any("positions changed" in reason for reason in plan.targets[0].reasons)


def test_build_input_cannot_lower_authored_minimum_runtime() -> None:
    vehicle = _vehicle()
    vehicle = replace(
        vehicle,
        configuration=replace(
            vehicle.configuration, minimum_runtime_version="2.0.0",
        ),
        minimum_runtime_version="1.0.0",
    )
    with pytest.raises(ValueError, match="cannot lower the authored"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)


def test_build_input_cannot_emit_native_incompatible_version_suffix() -> None:
    vehicle = replace(_vehicle(), minimum_runtime_version="2.0.0-alpha")
    with pytest.raises(ValueError, match="exact major.minor.patch"):
        compatibility_configuration(vehicle, TARGET_FIVEM_LEGACY)


def test_duplicate_model_hashes_fail_planning() -> None:
    vehicles = (
        _vehicle(model="bus_one", model_hash=0x10),
        _vehicle(model="bus_two", model_hash="0x00000010"),
    )
    with pytest.raises(ValueError, match="Duplicate model hash"):
        AxleRuntimeBundlePlanner().plan(vehicles, targets=(TARGET_FIVEM_LEGACY,))


def test_story_target_is_omitted_without_a_real_asi_profile() -> None:
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_STORY_LEGACY,),
    )
    assert not plan.ready_targets
    assert plan.targets[0].status == STATUS_OMITTED
    assert "no Story runtime binary was fabricated" in plan.targets[0].reasons[0]


def test_story_profile_requires_checksum_builds_and_redistribution(tmp_path: Path) -> None:
    profile = _story_profile(
        tmp_path, TARGET_STORY_LEGACY, redistribution_allowed=False,
    )
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )
    assert plan.targets[0].status == STATUS_OMITTED
    assert any("redistribution rights" in reason for reason in plan.targets[0].reasons)
    mismatched = replace(profile, expected_sha256="0" * 64, redistribution_allowed=True)
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: mismatched},
    )
    assert any("checksum" in reason for reason in plan.targets[0].reasons)


def test_story_profile_requires_real_x64_pe_exports_and_package_receipt(
    tmp_path: Path,
) -> None:
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    evidence = inspect_story_runtime_binary(profile.binary_path)
    assert evidence.architecture == "x64"
    assert set(STORY_RUNTIME_REQUIRED_EXPORTS).issubset(evidence.exports)

    dependency = profile.runtime_dependency()
    with pytest.raises(ValueError, match="pin the binary SHA-256"):
        replace(dependency, binary_sha256=None).validate()
    with pytest.raises(ValueError, match="not package eligible"):
        replace(dependency, package_eligible=1).validate()

    ascii_binary = tmp_path / "renamed-text.asi"
    ascii_binary.write_bytes(b"VehicleWorkbenchAxles_GetDescriptor ASCII fixture")
    ascii_profile = replace(
        profile, binary_path=ascii_binary, expected_sha256=_digest(ascii_binary),
    )
    with pytest.raises(ValueError, match="not a PE file"):
        ascii_profile.runtime_dependency()

    x86_binary = tmp_path / "x86.asi"
    x86_data = bytearray(profile.binary_path.read_bytes())
    struct.pack_into("<H", x86_data, 0x84, 0x014C)
    x86_binary.write_bytes(x86_data)
    x86_profile = replace(
        profile, binary_path=x86_binary, expected_sha256=_digest(x86_binary),
    )
    with pytest.raises(ValueError, match="x64 AMD64"):
        x86_profile.runtime_dependency()

    missing_export = tmp_path / "missing-export.asi"
    _write_x64_asi(missing_export, (STORY_RUNTIME_REQUIRED_EXPORTS[0],))
    missing_profile = replace(
        profile, binary_path=missing_export, expected_sha256=_digest(missing_export),
    )
    with pytest.raises(ValueError, match="required exports|export count"):
        missing_profile.runtime_dependency()

    with pytest.raises(ValueError, match="not package eligible"):
        replace(profile, package_eligible=False).runtime_dependency()

    receipt_payload = json.loads(profile.validation_receipt_path.read_text("utf-8"))
    untyped_receipt = tmp_path / "untyped.receipt.json"
    untyped_payload = dict(receipt_payload)
    untyped_payload["package_eligible"] = "true"
    untyped_receipt.write_text(
        json.dumps(untyped_payload, sort_keys=True), encoding="utf-8",
    )
    untyped = replace(
        profile, validation_receipt_path=untyped_receipt,
        expected_receipt_sha256=_digest(untyped_receipt),
    )
    with pytest.raises(ValueError, match="must be a boolean"):
        untyped.runtime_dependency()

    receipt_payload["validated_profile_export_result"] = False
    profile.validation_receipt_path.write_text(
        json.dumps(receipt_payload, sort_keys=True), encoding="utf-8",
    )
    disabled = replace(
        profile,
        expected_receipt_sha256=_digest(profile.validation_receipt_path),
    )
    with pytest.raises(ValueError, match="enabled build profile export"):
        disabled.runtime_dependency()


def test_old_story_profile_and_receipt_default_to_schema_one_capabilities(
    tmp_path: Path,
) -> None:
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    receipt_payload = json.loads(profile.validation_receipt_path.read_text("utf-8"))
    receipt_payload.pop("capabilities")
    profile.validation_receipt_path.write_text(
        json.dumps(receipt_payload, sort_keys=True), encoding="utf-8",
    )
    profile_path = _write_profile_document(
        replace(
            profile,
            expected_receipt_sha256=_digest(profile.validation_receipt_path),
        ),
        tmp_path / "old.profile.json",
    )
    profile_payload = json.loads(profile_path.read_text("utf-8"))
    profile_payload.pop("maximum_axle_schema")
    profile_payload.pop("capabilities")
    profile_path.write_text(
        json.dumps(profile_payload, sort_keys=True), encoding="utf-8",
    )

    dependency = StoryRuntimeProfile.load(profile_path).runtime_dependency()

    assert dependency.maximum_schema_version == 1
    assert dependency.supports_signed_steering_gain is False
    assert dependency.supports_static_force is False
    assert dependency.supports_physics_activation is False
    assert dependency.supports_axle_support_bias is False


def test_story_support_profile_unlocks_schema_three_only_from_attested_dependency(
    tmp_path: Path,
) -> None:
    profile = _story_profile(
        tmp_path,
        TARGET_STORY_LEGACY,
        version=AXLE_SUPPORT_RUNTIME_VERSION,
        maximum_axle_schema=AXLE_SUPPORT_SCHEMA_VERSION,
        supports_axle_support_bias=True,
    )
    dependency = profile.runtime_dependency()
    configured = apply_axle_support_weights(
        _vehicle().configuration, {1: 1.10, 2: 0.95, 3: 0.95},
    )
    vehicle = replace(
        _vehicle(),
        configuration=configured,
        minimum_runtime_version=AXLE_SUPPORT_RUNTIME_VERSION,
    )

    assert target_capabilities(TARGET_STORY_LEGACY).supports_axle_support_bias is False
    with pytest.raises(ValueError, match="suspension support accessor"):
        compatibility_configuration(vehicle, TARGET_STORY_LEGACY)
    payload = compatibility_configuration(
        vehicle, TARGET_STORY_LEGACY, runtime_dependency=dependency,
    )
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )

    assert dependency.supports_static_force is True
    assert dependency.supports_physics_activation is True
    assert dependency.supports_axle_support_bias is True
    assert payload["schemaVersion"] == AXLE_SUPPORT_SCHEMA_VERSION
    assert plan.targets[0].status == STATUS_READY
    assert plan.targets[0].capabilities.supports_axle_support_bias is True
    assert plan.targets[0].capabilities.supports_current_axle_schema is False


def test_story_signed_steering_requires_attested_profile_capability(
    tmp_path: Path,
) -> None:
    vehicle = _signed_vehicle()
    profile = _story_profile(
        tmp_path,
        TARGET_STORY_LEGACY,
        version=RUNTIME_GEOMETRY_RUNTIME_VERSION,
        maximum_axle_schema=SIGNED_STEERING_SCHEMA_VERSION,
        supports_signed_steering_gain=True,
        supports_wheel_local_position=True,
    )
    unsigned_root = tmp_path / "unsigned"
    unsigned_root.mkdir()

    rejected = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={
            TARGET_STORY_LEGACY: _story_profile(
                unsigned_root,
                TARGET_STORY_LEGACY,
                version=RUNTIME_GEOMETRY_RUNTIME_VERSION,
                maximum_axle_schema=SIGNED_STEERING_SCHEMA_VERSION,
            ),
        },
    )
    accepted = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )
    no_position_root = tmp_path / "no-position"
    no_position_root.mkdir()
    no_position = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={
            TARGET_STORY_LEGACY: _story_profile(
                no_position_root,
                TARGET_STORY_LEGACY,
                version=RUNTIME_GEOMETRY_RUNTIME_VERSION,
                maximum_axle_schema=SIGNED_STEERING_SCHEMA_VERSION,
                supports_signed_steering_gain=True,
            ),
        },
    )

    assert rejected.targets[0].status == STATUS_OMITTED
    assert any("signed steering-gain accessor" in item for item in rejected.targets[0].reasons)
    assert accepted.targets[0].status == STATUS_READY
    assert accepted.targets[0].capabilities.supports_signed_steering_gain is True
    assert accepted.targets[0].capabilities.supports_wheel_local_position is True
    assert accepted.targets[0].capabilities.supports_current_axle_schema is False
    assert accepted.targets[0].configurations[0].runtime_payload[
        "minimumRuntimeVersion"
    ] == RUNTIME_GEOMETRY_RUNTIME_VERSION
    assert no_position.targets[0].status == STATUS_OMITTED
    assert any(
        "wheel-local-position accessor" in item
        for item in no_position.targets[0].reasons
    )

    old_root = tmp_path / "old-runtime"
    old_root.mkdir()
    old_runtime = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={
            TARGET_STORY_LEGACY: _story_profile(
                old_root,
                TARGET_STORY_LEGACY,
                version="4.0.0",
                maximum_axle_schema=SIGNED_STEERING_SCHEMA_VERSION,
                supports_signed_steering_gain=True,
                supports_wheel_local_position=True,
            ),
        },
    )
    assert old_runtime.targets[0].status == STATUS_OMITTED
    assert any(
        "Runtime 4.0.0 is older" in item
        for item in old_runtime.targets[0].reasons
    )


def test_story_receipt_capabilities_must_match_and_pass_conditional_tests(
    tmp_path: Path,
) -> None:
    profile = _story_profile(
        tmp_path,
        TARGET_STORY_LEGACY,
        version=AXLE_SUPPORT_RUNTIME_VERSION,
        maximum_axle_schema=AXLE_SUPPORT_SCHEMA_VERSION,
        supports_axle_support_bias=True,
        supports_signed_steering_gain=True,
    )
    receipt = json.loads(profile.validation_receipt_path.read_text("utf-8"))
    receipt["capabilities"]["static_force"] = False
    profile.validation_receipt_path.write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8",
    )
    mismatched = replace(
        profile,
        expected_receipt_sha256=_digest(profile.validation_receipt_path),
    )
    with pytest.raises(ValueError, match="static-force capability does not match"):
        mismatched.runtime_dependency()

    receipt["capabilities"]["static_force"] = True
    receipt["acceptance_tests"].pop("support_bias_transaction_rollback")
    profile.validation_receipt_path.write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8",
    )
    incomplete = replace(
        profile,
        expected_receipt_sha256=_digest(profile.validation_receipt_path),
    )
    with pytest.raises(ValueError, match="incomplete or failed acceptance tests"):
        incomplete.runtime_dependency()


def test_story_manual_steering_retains_its_existing_runtime_minimum(
    tmp_path: Path,
) -> None:
    vehicle = _manual_signed_vehicle()
    profile = _story_profile(
        tmp_path,
        TARGET_STORY_LEGACY,
        version=RUNTIME_GEOMETRY_RUNTIME_VERSION,
        maximum_axle_schema=SIGNED_STEERING_SCHEMA_VERSION,
        supports_signed_steering_gain=True,
    )
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,),
        targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )
    assert plan.targets[0].status == STATUS_READY
    payload = plan.targets[0].configurations[0].runtime_payload
    assert payload["minimumRuntimeVersion"] == SIGNED_STEERING_RUNTIME_VERSION
    assert payload["steeringCalculation"]["runtimeRecompute"] is False


def test_fivem_dependency_cannot_claim_story_runtime_capabilities() -> None:
    dependency = RuntimeDependency(
        name=FIVEM_RUNTIME_NAME,
        version=AXLE_SUPPORT_RUNTIME_VERSION,
        maximum_schema_version=AXLE_SUPPORT_SCHEMA_VERSION,
        target_id=TARGET_FIVEM_LEGACY,
        supported_game_builds=("fivem-current",),
        configuration_destination="axle-runtime/configs",
        supports_axle_support_bias=True,
        supports_static_force=True,
        supports_physics_activation=True,
    )

    with pytest.raises(ValueError, match="FiveM runtime dependencies"):
        dependency.validate()


def test_story_profile_catalog_is_explicit_and_maps_exact_builds(tmp_path: Path) -> None:
    empty = story_runtime_profile_report()
    assert empty["implicit_profiles_loaded"] is False
    assert all(
        not target["package_eligible_for_build"]
        for target in empty["targets"].values()
    )
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    matching = story_runtime_profile_report(
        (profile,), requested_game_builds={TARGET_STORY_LEGACY: "build-123"},
    )
    assert matching["targets"][TARGET_STORY_LEGACY]["build_mapped"] is True
    assert matching["targets"][TARGET_STORY_LEGACY][
        "package_eligible_for_build"
    ] is True
    wrong = story_runtime_profile_report(
        (profile,), requested_game_builds={TARGET_STORY_LEGACY: "build-999"},
    )
    assert wrong["targets"][TARGET_STORY_LEGACY]["build_mapped"] is False
    assert "build-999" in wrong["targets"][TARGET_STORY_LEGACY]["reason"]


def test_story_profile_and_build_mapping_are_available_through_cli_and_api(
    tmp_path: Path,
) -> None:
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    profile_file = _write_profile_document(
        profile, tmp_path / "story-legacy.profile.json",
    )
    args = [
        "--story-profile", str(profile_file),
        "--game-build", f"{TARGET_STORY_LEGACY}=build-123",
    ]
    invoked = CliRunner().invoke(main, ["inspect-story-axle-runtimes", *args])
    assert invoked.exit_code == 0, invoked.output
    payload = json.loads(invoked.output)
    target = payload["targets"][TARGET_STORY_LEGACY]
    assert target["profile"]["verified"] is True
    assert target["package_eligible_for_build"] is True
    assert target["profile"]["binary_evidence"]["machine"] == "AMD64"

    response = execute_request({
        "id": "story-profile",
        "action": "execute",
        "command": "inspect-story-axle-runtimes",
        "args": args,
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert response["ok"] is True
    assert response["risk"] == "read_only"
    api_payload = json.loads(response["result"]["output"])
    assert api_payload["targets"][TARGET_STORY_LEGACY][
        "package_eligible_for_build"
    ] is True


def test_runtime_dependency_selection_deduplicates_to_newest_compatible() -> None:
    candidates = tuple(
        RuntimeDependency(
            name=FIVEM_RUNTIME_NAME,
            version=version,
            maximum_schema_version=1,
            target_id=TARGET_FIVEM_LEGACY,
            supported_game_builds=("fivem-current",),
            configuration_destination="axle-runtime/configs",
        )
        for version in ("1.0.0", "1.4.0", "1.2.0")
    )
    selected = select_newest_compatible_runtime(
        candidates,
        target_id=TARGET_FIVEM_LEGACY,
        minimum_version="1.1.0",
        schema_version=1,
        requested_game_build="fivem-current",
    )
    assert selected.version == "1.4.0"
    with pytest.raises(ValueError, match="No compatible"):
        select_newest_compatible_runtime(
            candidates,
            target_id=TARGET_FIVEM_LEGACY,
            minimum_version="2.0.0",
            schema_version=1,
        )


def test_custom_layout_runtime_floor_excludes_version_2_0() -> None:
    candidates = tuple(
        RuntimeDependency(
            name=FIVEM_RUNTIME_NAME,
            version=version,
            maximum_schema_version=1,
            target_id=TARGET_FIVEM_LEGACY,
            supported_game_builds=("fivem-current",),
            configuration_destination="axle-runtime/configs",
        )
        for version in ("2.0.0", INTENTIONAL_LAYOUT_RUNTIME_VERSION)
    )

    selected = select_newest_compatible_runtime(
        candidates,
        target_id=TARGET_FIVEM_LEGACY,
        minimum_version=INTENTIONAL_LAYOUT_RUNTIME_VERSION,
        schema_version=1,
        requested_game_build="fivem-current",
    )

    assert selected.version == INTENTIONAL_LAYOUT_RUNTIME_VERSION
    with pytest.raises(ValueError, match="No compatible"):
        select_newest_compatible_runtime(
            candidates[:1],
            target_id=TARGET_FIVEM_LEGACY,
            minimum_version=INTENTIONAL_LAYOUT_RUNTIME_VERSION,
            schema_version=1,
        )


def test_dependency_binary_cannot_be_bundled_without_rights(tmp_path: Path) -> None:
    binary = tmp_path / "third-party.dll"
    binary.write_bytes(b"not-redistributable")
    dependency = DependencyDeclaration(
        name="Third Party",
        version="1.0.0",
        source_url="https://example.invalid/tool",
        license_name="Proprietary",
        bundled=True,
        redistribution_allowed=False,
        binary_path=binary,
    )
    with pytest.raises(ValueError, match="confirmed redistribution rights"):
        dependency.validate()


def test_full_four_target_build_is_staged_and_has_no_cross_contamination(
    tmp_path: Path,
) -> None:
    profiles = {
        target: _story_profile(tmp_path, target)
        for target in (TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED)
    }
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),),
        story_profiles=profiles,
        requested_game_builds={
            TARGET_STORY_LEGACY: "build-123",
            TARGET_STORY_ENHANCED: "build-123",
        },
    )
    assert all(target.status == STATUS_READY for target in plan.targets)
    destination = tmp_path / "dist"
    result = AxleRuntimeBundleBuilder().build(plan, destination)
    assert result.built_targets == TARGET_IDS
    bundle = json.loads(result.manifest.read_text("utf-8"))
    assert bundle["game_write_performed"] is False
    assert bundle["acceptance_status"] == ACCEPTANCE_PENDING

    for target in (TARGET_FIVEM_LEGACY, TARGET_FIVEM_ENHANCED):
        root = destination / target
        assert (root / "axle-runtime" / "fxmanifest.lua").is_file()
        assert (root / "axle-runtime" / "server.lua").is_file()
        assert not list(root.rglob("*.asi"))
        client = next((root / "axle-runtime" / "models").glob("*.lua")).read_text("utf-8")
        assert "NetworkHasControlOfEntity" in client
        assert "& 0xFFFF" in client
        assert 'AddEventHandler("entityCreated"' not in client
    for target in (TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED):
        root = destination / target
        assert (root / f"{STORY_RUNTIME_NAME}.asi").is_file()
        assert not list(root.rglob("fxmanifest.lua"))
        assert not list(root.rglob("*.lua"))
        manifest = json.loads((root / "compatibility-manifest.json").read_text("utf-8"))
        assert manifest["published_supported"] is False
        assert manifest["runtime"]["supported_game_builds"] == ["build-123"]
        assert manifest["runtime"]["package_eligible"] is True
        assert manifest["runtime"]["binary_evidence"]["architecture"] == "x64"
        assert (root / STORY_RUNTIME_NAME / "validation-receipt.json").is_file()
        settings = json.loads(
            (root / STORY_RUNTIME_NAME / "runtime.json").read_text("utf-8")
        )
        assert settings["schemaVersion"] == 2
        assert settings["enabled"] is True
        assert "runtime_version" not in settings
        metadata = json.loads(
            (root / STORY_RUNTIME_NAME / "runtime-metadata.json").read_text(
                "utf-8"
            )
        )
        assert metadata["runtime_name"] == STORY_RUNTIME_NAME
        assert metadata["runtime_version"] == "1.0.0"
        assert metadata["target"] == target
        assert metadata["supported_game_builds"] == ["build-123"]
        assert metadata["binary_sha256"] == profiles[target].expected_sha256
        assert metadata["validation_receipt_sha256"] == (
            profiles[target].expected_receipt_sha256
        )
        assert metadata["architecture"] == "x64"
        assert metadata["scripthook_bundled"] is False
        assert metadata["online_loading_supported"] is False


def test_fivem_enhanced_assets_require_approved_converter(tmp_path: Path) -> None:
    assets = tmp_path / "vehicle-assets"
    assets.mkdir()
    (assets / "bus.yft").write_bytes(b"gen8")
    vehicle = _vehicle(asset_source=assets)
    missing = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_ENHANCED,),
    )
    assert missing.targets[0].status == STATUS_OMITTED
    assert any("Missing approved" in reason for reason in missing.targets[0].reasons)

    converter = _FakeConverter()
    approval = _approval(tmp_path)
    plan = AxleRuntimeBundlePlanner().plan(
        (vehicle,), targets=(TARGET_FIVEM_ENHANCED,),
        converter=converter, converter_approval=approval,
    )
    result = AxleRuntimeBundleBuilder().build(
        plan, tmp_path / "converted-dist", converter=converter,
    )
    assert result.built_targets == (TARGET_FIVEM_ENHANCED,)
    converted = (
        result.root / TARGET_FIVEM_ENHANCED / "vehicle-resource-gen9"
        / "example_bus-axles" / "converted.rpf"
    )
    assert converted.read_bytes() == b"gen9"
    assert len(converter.calls) == 1


def test_converter_failure_rolls_back_whole_staged_destination(tmp_path: Path) -> None:
    assets = tmp_path / "vehicle-assets"
    assets.mkdir()
    (assets / "bus.yft").write_bytes(b"gen8")
    converter = _FakeConverter(fail=True)
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(asset_source=assets),), targets=(TARGET_FIVEM_ENHANCED,),
        converter=converter, converter_approval=_approval(tmp_path),
    )
    destination = tmp_path / "failed-dist"
    with pytest.raises(RuntimeError, match="conversion failed"):
        AxleRuntimeBundleBuilder().build(plan, destination, converter=converter)
    assert not destination.exists()
    assert not list(tmp_path.glob(".failed-dist.axle-bundle-*"))


def test_story_enhanced_runtime_stages_with_manual_asset_warning(tmp_path: Path) -> None:
    assets = tmp_path / "story-assets"
    assets.mkdir()
    (assets / "bus.yft").write_bytes(b"legacy-shape")
    profile = _story_profile(tmp_path, TARGET_STORY_ENHANCED)
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(asset_source=assets),), targets=(TARGET_STORY_ENHANCED,),
        story_profiles={TARGET_STORY_ENHANCED: profile},
    )
    target = plan.targets[0]
    assert target.status == STATUS_READY
    assert target.asset_mode == "manual_asset_installation_required"
    assert any("not configured" in warning for warning in target.warnings)
    result = AxleRuntimeBundleBuilder().build(plan, tmp_path / "story-enhanced")
    readme = (
        result.root / TARGET_STORY_ENHANCED / "README.md"
    ).read_text("utf-8")
    assert "Vehicle assets were not converted" in readme


def test_story_binary_drift_after_planning_aborts_publication(tmp_path: Path) -> None:
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )
    profile.binary_path.write_bytes(b"drift")
    destination = tmp_path / "drift-dist"
    with pytest.raises(ValueError, match="changed after planning"):
        AxleRuntimeBundleBuilder().build(plan, destination)
    assert not destination.exists()


def test_story_receipt_drift_after_planning_aborts_publication(tmp_path: Path) -> None:
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_STORY_LEGACY,),
        story_profiles={TARGET_STORY_LEGACY: profile},
    )
    profile.validation_receipt_path.write_text("{}", encoding="utf-8")
    destination = tmp_path / "receipt-drift-dist"
    with pytest.raises(ValueError, match="receipt changed after planning"):
        AxleRuntimeBundleBuilder().build(plan, destination)
    assert not destination.exists()


def test_direct_install_and_existing_destination_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Direct GTA installation is not supported"):
        AxleRuntimeBundlePlanner().plan(
            (_vehicle(),), targets=(TARGET_FIVEM_LEGACY,), direct_install=True,
        )
    plan = AxleRuntimeBundlePlanner().plan(
        (_vehicle(),), targets=(TARGET_FIVEM_LEGACY,),
    )
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        AxleRuntimeBundleBuilder().build(plan, destination)

    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    live_destination = game / "staged-axle-bundle"
    with pytest.raises(ValueError, match="staged-only"):
        AxleRuntimeBundleBuilder().build(plan, live_destination)
    assert not live_destination.exists()

    declared_game = tmp_path / "declared-game-root"
    declared_game.mkdir()
    declared_destination = declared_game / "staged-axle-bundle"
    with pytest.raises(ValueError, match="staged-only"):
        AxleRuntimeBundleBuilder().build(
            plan, declared_destination,
            protected_gta_roots=(declared_game,),
        )
    assert not declared_destination.exists()
