from __future__ import annotations

import hashlib
import json
import struct
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner
from lxml import etree as ET
from PIL import Image

from allin1_sdk.axle_oiv_export import (
    COMPRESSION_DEFLATED,
    COMPRESSION_STORED,
    ENHANCED_UNVALIDATED_MESSAGE,
    MODE_RUNTIME_ONLY,
    MODE_SELF_CONTAINED,
    MODE_VEHICLE_ONLY,
    NEWER_RUNTIME_WARNING,
    SELF_CONTAINED_WARNING,
    EnhancedOivTargetProfile,
    JsonOivIdentityStore,
    LegacyOivTargetProfile,
    OivContentPlanner,
    OivExportRequest,
    OivPackageBuilder,
    OivPackageMetadata,
    OivPackageValidator,
    StagedAxleConfiguration,
    StagedRuntime,
    StagedVehicleDlc,
)
from allin1_sdk.axle_runtime_bundler import (
    STORY_RUNTIME_NAME,
    STORY_RUNTIME_REQUIRED_EXPORTS,
    TARGET_STORY_ENHANCED,
    TARGET_STORY_LEGACY,
)
from allin1_sdk.axle_configurator import joaat_hex
from allin1_sdk.cli import main


MODEL_HASH = joaat_hex("mybus")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_x64_asi(path: Path) -> None:
    """Create a deterministic PE32+ DLL with the runtime contract exports."""
    exports = STORY_RUNTIME_REQUIRED_EXPORTS
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
    struct.pack_into(
        "<IIHHIIIIIII", data, 0x200,
        0, 0, 0, 0, dll_name_rva, 1, count, count,
        functions_rva, names_rva, ordinals_rva,
    )
    for index in range(count):
        struct.pack_into("<I", data, 0x200 + functions_rva - 0x1000 + index * 4,
                         0x2000 + index)
        struct.pack_into("<I", data, 0x200 + names_rva - 0x1000 + index * 4,
                         name_rvas[index])
        struct.pack_into("<H", data, 0x200 + ordinals_rva - 0x1000 + index * 2,
                         index)
        data[0x400 + index] = 0xC3
    raw = 0x200 + dll_name_rva - 0x1000
    data[raw:raw + len(dll_name)] = dll_name
    for name_rva, encoded in zip(name_rvas, encoded_names):
        raw = 0x200 + name_rva - 0x1000
        data[raw:raw + len(encoded)] = encoded
    path.write_bytes(data)


def _stage(tmp_path: Path, *, target: str = TARGET_STORY_LEGACY) -> Path:
    root = tmp_path / f"stage-{target}"
    (root / "vehicle" / "vwb_mybus").mkdir(parents=True)
    (root / STORY_RUNTIME_NAME / "configs").mkdir(parents=True)
    archive = root / "vehicle" / "vwb_mybus" / "dlc.rpf"
    archive.write_bytes(b"RPF7fixture")
    (root / STORY_RUNTIME_NAME / "configs" / "mybus.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "configurationId": "mybus-axles",
            "modelName": "mybus",
            "modelHash": MODEL_HASH,
            "minimumRuntimeVersion": "1.0.0",
            "compatibility": {target: True},
            "expectedWheelCount": 6,
            "wheelIndexMapping": {"by_bone": {
                "wheel_lf": 0, "wheel_rf": 1,
                "wheel_lm1": 2, "wheel_rm1": 3,
                "wheel_lr": 4, "wheel_rr": 5,
            }},
            "axles": [
                {"order": 0, "role": "front", "leftBone": "wheel_lf", "rightBone": "wheel_rf", "wheelIndices": [0, 1], "steered": True, "powered": False},
                {"order": 1, "role": "middle", "leftBone": "wheel_lm1", "rightBone": "wheel_rm1", "wheelIndices": [2, 3], "steered": False, "powered": True},
                {"order": 2, "role": "rear", "leftBone": "wheel_lr", "rightBone": "wheel_rr", "wheelIndices": [4, 5], "steered": True, "powered": False},
            ],
        }),
        encoding="utf-8",
    )
    report = root / "vehicle-validation-report.json"
    edition = "legacy" if target == TARGET_STORY_LEGACY else "enhanced"
    report.write_text(json.dumps({
        "schema_version": 1,
        "operation": "vehicle_addon_package_build",
        "status": "validated",
        "editions": [edition],
        "payload": {"path": "vehicle/vwb_mybus/dlc.rpf", "sha256": _sha(archive)},
        "safety": {
            "source_unchanged": True,
            "output_was_new": True,
            "stock_game_files_modified": False,
            "manifest_payload_validated": True,
        },
    }), encoding="utf-8")
    native_report = root / "native-rpf-validation.json"
    native_report.write_text(json.dumps({
        "schema_version": 1,
        "operation": "validate_story_vehicle_rpf",
        "status": "validated",
        "archive_sha256": _sha(archive),
        "edition": edition,
        "archive_count": 1,
        "entry_count": 5,
        "model_assets": {"mybus": {"yft": True, "ytd": True}},
        "required_metadata": {
            "vehicles.meta": True,
            "handling.meta": True,
            "carvariations.meta": True,
        },
        "game_write_performed": False,
    }), encoding="utf-8")
    (root / "compatibility-manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "target": target,
            "game_write_performed": False,
            "vehicle_artifacts": [{
                "path": "vehicle/vwb_mybus/dlc.rpf",
                "sha256": _sha(archive),
                "asset_edition": edition,
                "asset_format": (
                    "legacy-rpf7-gen8" if edition == "legacy" else "gen9-required"
                ),
                "validation_status": "validated",
                "validation_report": "vehicle-validation-report.json",
                "validation_report_sha256": _sha(report),
                "native_validation_report": "native-rpf-validation.json",
                "native_validation_report_sha256": _sha(native_report),
            }],
        }),
        encoding="utf-8",
    )
    _write_x64_asi(root / f"{STORY_RUNTIME_NAME}.asi")
    return root


def _metadata(*, package_id: str = "com.example.mybus") -> OivPackageMetadata:
    return OivPackageMetadata(
        project_id="com.example.mybus-project",
        package_id=package_id,
        name="My Bus & Axles",
        version="1.0.0",
        author="A <Builder>",
        description="Installs the My Bus add-on & axle behavior.",
        workbench_version="0.5.5",
        support_url="https://example.invalid/support?project=mybus&mode=oiv",
        license_name="Example license",
    )


def _dlc(*, edition: str = "legacy") -> StagedVehicleDlc:
    return StagedVehicleDlc(
        dlc_pack_name="vwb_mybus",
        archive_path="vehicle/vwb_mybus/dlc.rpf",
        vehicle_models=("mybus",),
        asset_edition=edition,
    )


def _config(
    *, model: str = "mybus", model_hash: str = MODEL_HASH,
) -> StagedAxleConfiguration:
    return StagedAxleConfiguration(
        model_name=model,
        model_hash=model_hash,
        source_path=f"{STORY_RUNTIME_NAME}/configs/{model}.json",
        schema_version=1,
        minimum_runtime_version="1.0.0",
    )


def _runtime(stage: Path, target: str) -> StagedRuntime:
    binary = stage / f"{STORY_RUNTIME_NAME}.asi"
    profile_id = f"allin1.{target}.oiv-fixture"
    license_name = "ALLIN1 Vehicle Workbench Axle Runtime"
    receipt = stage / "runtime-validation-receipt.json"
    receipt.write_text(json.dumps({
        "schema_version": 1,
        "receipt_id": f"receipt-{target}",
        "profile_id": profile_id,
        "runtime_name": STORY_RUNTIME_NAME,
        "target_id": target,
        "runtime_version": "1.0.0",
        "binary_sha256": _sha(binary),
        "binary_architecture": "x64",
        "supported_game_builds": ["build-123"],
        "maximum_axle_schema": 1,
        "descriptor_abi_version": 1,
        "required_exports": list(STORY_RUNTIME_REQUIRED_EXPORTS),
        "validated_profile_export_result": True,
        "acceptance_tests": {
            "front_steer": "passed", "selective_drive": "passed",
            "rear_steer": "passed", "unrelated_flags_preserved": "passed",
            "repair_reapplication": "passed",
            "unsupported_build_fail_closed": "passed",
            "online_session_guard": "passed",
        },
        "validation_authority": "ALLIN1 OIV regression fixture",
        "accepted_at": "2026-08-25T12:00:00Z",
        "package_eligible": True,
        "redistribution_allowed": True,
        "license": license_name,
    }, sort_keys=True), encoding="utf-8")
    return StagedRuntime(
        asi_path=f"{STORY_RUNTIME_NAME}.asi",
        version="1.0.0",
        target_id=target,
        supported_game_builds=("build-123",),
        maximum_schema_version=1,
        binary_sha256=_sha(binary),
        build_date="2026-08-25",
        profile_id=profile_id,
        validation_receipt_path=receipt.relative_to(stage).as_posix(),
        validation_receipt_sha256=_sha(receipt),
        package_eligible=True,
        redistribution_allowed=True,
        license_name=license_name,
        architecture="x64",
        required_scripthook_version="current compatible release",
    )


def _vehicle_request(
    tmp_path: Path,
    *,
    compression: str = COMPRESSION_DEFLATED,
    metadata: OivPackageMetadata | None = None,
) -> OivExportRequest:
    stage = _stage(tmp_path)
    return OivExportRequest(
        staging_root=stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_VEHICLE_ONLY,
        metadata=metadata or _metadata(),
        vehicle_dlcs=(_dlc(),),
        axle_configurations=(_config(),),
        compression=compression,
    )


def _builder(tmp_path: Path) -> OivPackageBuilder:
    return OivPackageBuilder(JsonOivIdentityStore(tmp_path / "project" / "oiv-identities.json"))


def test_legacy_transport_support_does_not_claim_real_in_game_acceptance() -> None:
    profile = LegacyOivTargetProfile()
    assert profile.supports_oiv is True
    assert profile.transport_validated is True
    assert profile.integration_validated is False
    assert any("in-game Legacy acceptance" in item for item in profile.limitations)


def test_guid_is_persisted_by_project_target_and_mode(tmp_path: Path) -> None:
    store = JsonOivIdentityStore(tmp_path / "identities.json")
    first = store.resolve("com.example.project", "story-legacy/vehicle-only")
    second = store.resolve("com.example.project", "story-legacy/vehicle-only")
    runtime = store.resolve("com.example.project", "story-legacy/runtime-only")
    assert first == second
    assert first != runtime
    payload = json.loads((tmp_path / "identities.json").read_text("utf-8"))
    assert len(payload["identities"]) == 2


@pytest.mark.parametrize("compression", [COMPRESSION_STORED, COMPRESSION_DEFLATED])
def test_vehicle_only_oiv_22_contains_dlc_config_manifest_and_dependency(
    tmp_path: Path, compression: str,
) -> None:
    request = _vehicle_request(tmp_path, compression=compression)
    output = tmp_path / f"MyBus-{compression}.oiv"
    result = _builder(tmp_path).build(request, output)
    assert result.archive == output
    assert any("Runtime not included" in warning for warning in result.warnings)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "assembly.xml" in archive.namelist()
        assert "content/" in archive.namelist()
        assert not any(name.casefold().endswith(".asi") for name in archive.namelist())
        assembly = ET.fromstring(archive.read("assembly.xml"))
        assert assembly.attrib["version"] == "2.2"
        assert assembly.attrib["target"] == "Five"
        assert assembly.findtext("metadata/name") == "My Bus & Axles"
        assert assembly.findtext("metadata/author/displayName") == "A <Builder>"
        assert assembly.findtext("metadata/author/web").startswith("https://")
        assert assembly.findtext("metadata/licence") == "Example license"
        assert assembly.find("content/archive/xml/add/Item").text == "dlcpacks:/vwb_mybus/"
        manifest_name = "content/manifests/com.example.mybus.manifest.json"
        manifest = json.loads(archive.read(manifest_name))
        assert manifest["packageType"] == MODE_VEHICLE_ONLY
        assert manifest["dependencies"] == [{
            "id": "vehicle-workbench-axle-runtime",
            "minimumVersion": "1.0.0",
            "target": TARGET_STORY_LEGACY,
            "required": True,
            "bundled": False,
        }]
        assert f"{STORY_RUNTIME_NAME}\\configs\\mybus.json" in manifest["ownedFiles"]
        assert not any("ScriptHookV" in name for name in archive.namelist())
        expected_type = (
            zipfile.ZIP_STORED if compression == COMPRESSION_STORED
            else zipfile.ZIP_DEFLATED
        )
        assert all(item.compress_type == expected_type for item in archive.infolist())


def test_logical_output_is_deterministic_with_persisted_identity(tmp_path: Path) -> None:
    request = _vehicle_request(tmp_path)
    builder = _builder(tmp_path)
    first = builder.build(request, tmp_path / "one.oiv")
    second = builder.build(request, tmp_path / "two.oiv")
    assert first.package_guid == second.package_guid
    assert first.archive_sha256 == second.archive_sha256


def test_runtime_only_oiv_contains_generic_asi_and_no_vehicle_content(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    request = OivExportRequest(
        staging_root=stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_RUNTIME_ONLY,
        metadata=_metadata(package_id="com.example.axle-runtime"),
        runtime=_runtime(stage, TARGET_STORY_LEGACY),
    )
    output = tmp_path / "runtime.oiv"
    result = _builder(tmp_path).build(request, output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert f"content/runtime/{STORY_RUNTIME_NAME}.asi" in names
        assert not any("dlcpacks/" in name for name in names)
        assert not any("configs/mybus.json" in name for name in names)
        runtime_meta = json.loads(archive.read("content/runtime/runtime.json"))
        assert runtime_meta["binary_sha256"] == _runtime(stage, TARGET_STORY_LEGACY).binary_sha256
        assert runtime_meta["scripthook_bundled"] is False
    assert result.installation_preview["files_replaced"] == [
        f"{STORY_RUNTIME_NAME}.asi",
        f"{STORY_RUNTIME_NAME}\\validation-receipt.json",
        f"{STORY_RUNTIME_NAME}\\runtime.json",
    ]


def test_runtime_only_cli_accepts_only_the_validated_profile_contract(
    tmp_path: Path,
) -> None:
    stage = _stage(tmp_path)
    runtime = _runtime(stage, TARGET_STORY_LEGACY)
    profile = stage / "story-runtime.profile.json"
    profile.write_text(json.dumps({
        "profile_id": runtime.profile_id,
        "target_id": runtime.target_id,
        "binary_path": runtime.asi_path,
        "version": runtime.version,
        "supported_game_builds": list(runtime.supported_game_builds),
        "expected_sha256": runtime.binary_sha256,
        "package_eligible": True,
        "validation_receipt_path": runtime.validation_receipt_path,
        "expected_receipt_sha256": runtime.validation_receipt_sha256,
        "redistribution_allowed": True,
        "license": runtime.license_name,
    }), encoding="utf-8")
    request = stage / "runtime-oiv-request.json"
    request.write_text(json.dumps({
        "staging_root": str(stage),
        "target": TARGET_STORY_LEGACY,
        "mode": MODE_RUNTIME_ONLY,
        "metadata": {
            "project_id": "vehicle-workbench-axle-runtime",
            "package_id": "vehicle-workbench-axle-runtime",
            "name": "Vehicle Workbench Axle Runtime",
            "version": "1.0.0",
            "author": "ALLIN1 test",
            "description": "Validated profile CLI fixture.",
            "workbench_version": "0.5.5"
        },
        "runtime": {"profile_path": profile.name},
    }), encoding="utf-8")
    output = tmp_path / "runtime-cli.oiv"
    result = CliRunner().invoke(main, [
        "build-axle-oiv", str(request),
        "--identity-store", str(tmp_path / "runtime-identities.json"),
        "--output", str(output), "--acknowledge-edit",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["target"] == TARGET_STORY_LEGACY
    assert output.is_file()
    with zipfile.ZipFile(output) as archive:
        assert f"content/runtime/{STORY_RUNTIME_NAME}.asi" in archive.namelist()


def test_self_contained_requires_confirmation_and_warns_about_downgrade(
    tmp_path: Path,
) -> None:
    stage = _stage(tmp_path)
    request = OivExportRequest(
        staging_root=stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_SELF_CONTAINED,
        metadata=_metadata(),
        vehicle_dlcs=(_dlc(),),
        axle_configurations=(_config(),),
        runtime=_runtime(stage, TARGET_STORY_LEGACY),
    )
    with pytest.raises(ValueError, match="explicit confirmation"):
        _builder(tmp_path).build(request, tmp_path / "unconfirmed.oiv")
    confirmed = replace(request, confirm_self_contained=True)
    result = _builder(tmp_path).build(confirmed, tmp_path / "self-contained.oiv")
    assert SELF_CONTAINED_WARNING in result.warnings
    assert NEWER_RUNTIME_WARNING in result.warnings
    with zipfile.ZipFile(result.archive) as archive:
        description = ET.fromstring(archive.read("assembly.xml")).findtext("metadata/description")
        assert "May replace" in description
        assert "older bundled ASI" in description


def test_enhanced_oiv_fails_closed_and_openrpf_fallback_is_explicit(tmp_path: Path) -> None:
    stage = _stage(tmp_path, target=TARGET_STORY_ENHANCED)
    request = OivExportRequest(
        staging_root=stage,
        target_profile=EnhancedOivTargetProfile(),
        mode=MODE_VEHICLE_ONLY,
        metadata=_metadata(),
        vehicle_dlcs=(_dlc(edition="enhanced"),),
        axle_configurations=(_config(),),
    )
    builder = _builder(tmp_path)
    with pytest.raises(ValueError, match="Enhanced OIV export is not validated"):
        builder.build(request, tmp_path / "bad-enhanced.oiv")
    caller_asserted_profile = EnhancedOivTargetProfile(
        installer_name="Caller asserted",
        integration_validated=True,
        supported_game_builds=("build-123",),
        archive_paths=("update/x64/dlcpacks",),
        installation_rules=("caller asserted",),
        runtime_profile_id="caller.asserted",
        acceptance_receipt_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="Enhanced OIV export is not validated"):
        builder.build(
            replace(request, target_profile=caller_asserted_profile),
            tmp_path / "caller-asserted-enhanced.oiv",
        )
    fallback = builder.build_enhanced_fallback(
        request, tmp_path / "MyBus_Enhanced_OpenRPF.zip",
    )
    with zipfile.ZipFile(fallback) as archive:
        assert "assembly.xml" not in archive.namelist()
        assert "openrpf-manifest.json" in archive.namelist()
        manifest = json.loads(archive.read("openrpf-manifest.json"))
        assert manifest["oiv_supported"] is False
        assert manifest["warning"] == ENHANCED_UNVALIDATED_MESSAGE


@pytest.mark.parametrize(
    "unsafe",
    ["../outside.rpf", "C:/outside.rpf", "/absolute.rpf", "vehicle/CON/file.rpf"],
)
def test_staging_path_traversal_and_reserved_names_are_rejected(
    tmp_path: Path, unsafe: str,
) -> None:
    request = _vehicle_request(tmp_path)
    request = replace(
        request,
        vehicle_dlcs=(replace(request.vehicle_dlcs[0], archive_path=unsafe),),
    )
    with pytest.raises(ValueError, match="safe relative|reserved"):
        _builder(tmp_path).build(request, tmp_path / "unsafe.oiv")


def test_duplicate_configuration_hash_and_filename_are_rejected(tmp_path: Path) -> None:
    request = _vehicle_request(tmp_path)
    native_report_path = request.staging_root / "native-rpf-validation.json"
    native_report = json.loads(native_report_path.read_text("utf-8"))
    native_report["model_assets"]["other"] = {"yft": True, "ytd": True}
    native_report_path.write_text(json.dumps(native_report), encoding="utf-8")
    manifest_path = request.staging_root / "compatibility-manifest.json"
    stage_manifest = json.loads(manifest_path.read_text("utf-8"))
    stage_manifest["vehicle_artifacts"][0][
        "native_validation_report_sha256"
    ] = _sha(native_report_path)
    manifest_path.write_text(json.dumps(stage_manifest), encoding="utf-8")
    duplicate_hash = replace(
        _config(model="other", model_hash=MODEL_HASH),
        source_path=f"{STORY_RUNTIME_NAME}/configs/mybus.json",
    )
    request = replace(
        request,
        vehicle_dlcs=(replace(request.vehicle_dlcs[0], vehicle_models=("mybus", "other")),),
        axle_configurations=(_config(), duplicate_hash),
    )
    with pytest.raises(ValueError, match="Duplicate axle configuration model hash"):
        _builder(tmp_path).build(request, tmp_path / "duplicate.oiv")


def test_duplicate_vehicle_model_declarations_are_rejected(tmp_path: Path) -> None:
    request = _vehicle_request(tmp_path)
    request = replace(
        request,
        vehicle_dlcs=(replace(
            request.vehicle_dlcs[0], vehicle_models=("mybus", "mybus"),
        ),),
    )
    with pytest.raises(ValueError, match="Duplicate vehicle model declaration"):
        _builder(tmp_path).build(request, tmp_path / "duplicate-model.oiv")


def test_vehicle_archive_requires_rpf7_and_hash_bound_build_evidence(
    tmp_path: Path,
) -> None:
    invalid_archive = _vehicle_request(tmp_path / "invalid-archive")
    archive = invalid_archive.staging_root / "vehicle" / "vwb_mybus" / "dlc.rpf"
    archive.write_bytes(b"not-an-rpf")
    with pytest.raises(ValueError, match="not a Rockstar RPF7 archive"):
        _builder(tmp_path).build(invalid_archive, tmp_path / "invalid-archive.oiv")

    forged_evidence = _vehicle_request(tmp_path / "forged-evidence")
    manifest = forged_evidence.staging_root / "compatibility-manifest.json"
    payload = json.loads(manifest.read_text("utf-8"))
    payload["vehicle_artifacts"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="build evidence does not match"):
        _builder(tmp_path).build(forged_evidence, tmp_path / "forged-evidence.oiv")


def test_stage_manifest_target_joaat_and_config_compatibility_are_required(
    tmp_path: Path,
) -> None:
    missing_manifest = _vehicle_request(tmp_path / "missing-manifest")
    (missing_manifest.staging_root / "compatibility-manifest.json").unlink()
    with pytest.raises(ValueError, match="compatibility-manifest.json is required"):
        _builder(tmp_path).build(missing_manifest, tmp_path / "missing-manifest.oiv")

    wrong_hash = _vehicle_request(tmp_path / "wrong-hash")
    config_path = (
        wrong_hash.staging_root / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    )
    payload = json.loads(config_path.read_text("utf-8"))
    payload["modelHash"] = "0x12345678"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    request = replace(
        wrong_hash,
        axle_configurations=(replace(_config(), model_hash="0x12345678"),),
    )
    with pytest.raises(ValueError, match="joaat hash"):
        _builder(tmp_path).build(request, tmp_path / "wrong-hash.oiv")

    disabled = _vehicle_request(tmp_path / "disabled")
    config_path = disabled.staging_root / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    payload = json.loads(config_path.read_text("utf-8"))
    payload["compatibility"] = {TARGET_STORY_LEGACY: False}
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="does not explicitly enable story-legacy"):
        _builder(tmp_path).build(disabled, tmp_path / "disabled.oiv")

    incomplete = _vehicle_request(tmp_path / "incomplete")
    config_path = incomplete.staging_root / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    payload = json.loads(config_path.read_text("utf-8"))
    del payload["axles"][1]["powered"]
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime schema and mapping"):
        _builder(tmp_path).build(incomplete, tmp_path / "incomplete.oiv")

    schema_one_signed = _vehicle_request(tmp_path / "schema-one-signed")
    config_path = (
        schema_one_signed.staging_root
        / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    )
    payload = json.loads(config_path.read_text("utf-8"))
    payload["axles"][-1]["steeringGain"] = -0.22
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="runtime schema and mapping"):
        _builder(tmp_path).build(
            schema_one_signed, tmp_path / "schema-one-signed.oiv",
        )


def test_config_runtime_evidence_and_mapping_must_match_declaration_exactly(
    tmp_path: Path,
) -> None:
    runtime_mismatch = _vehicle_request(tmp_path / "runtime-mismatch")
    config_path = (
        runtime_mismatch.staging_root
        / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    )
    payload = json.loads(config_path.read_text("utf-8"))
    payload["minimumRuntimeVersion"] = "9.0.0"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="minimumRuntimeVersion does not match"):
        _builder(tmp_path).build(
            runtime_mismatch, tmp_path / "runtime-mismatch.oiv",
        )

    extra_mapping = _vehicle_request(tmp_path / "extra-mapping")
    config_path = (
        extra_mapping.staging_root
        / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    )
    payload = json.loads(config_path.read_text("utf-8"))
    payload["wheelIndexMapping"]["by_bone"]["wheel_fake"] = 6
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly the configured canonical wheel bones"):
        _builder(tmp_path).build(extra_mapping, tmp_path / "extra-mapping.oiv")


def test_runtime_edition_and_checksum_mismatches_are_rejected(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    base = OivExportRequest(
        staging_root=stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_RUNTIME_ONLY,
        metadata=_metadata(package_id="com.example.runtime"),
        runtime=_runtime(stage, TARGET_STORY_ENHANCED),
    )
    with pytest.raises(ValueError, match="edition does not match"):
        _builder(tmp_path).build(base, tmp_path / "wrong-edition.oiv")
    base = replace(base, runtime=replace(
        _runtime(stage, TARGET_STORY_LEGACY), binary_sha256="0" * 64,
    ))
    with pytest.raises(ValueError, match="checksum"):
        _builder(tmp_path).build(base, tmp_path / "wrong-hash.oiv")


def test_known_newer_runtime_and_config_runtime_mismatch_fail_closed(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    request = OivExportRequest(
        staging_root=stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_SELF_CONTAINED,
        metadata=_metadata(),
        vehicle_dlcs=(_dlc(),),
        axle_configurations=(_config(),),
        runtime=_runtime(stage, TARGET_STORY_LEGACY),
        confirm_self_contained=True,
        known_existing_runtime_version="2.0.0",
    )
    with pytest.raises(ValueError, match="older than the known"):
        _builder(tmp_path).build(request, tmp_path / "downgrade.oiv")
    incompatible = replace(
        request,
        known_existing_runtime_version=None,
        axle_configurations=(replace(_config(), minimum_runtime_version="2.0.0"),),
    )
    config_path = stage / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    payload = json.loads(config_path.read_text("utf-8"))
    payload["minimumRuntimeVersion"] = "2.0.0"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="incompatible with axle configurations"):
        _builder(tmp_path).build(incompatible, tmp_path / "incompatible.oiv")


def test_icon_must_be_exactly_128_png(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    Image.new("RGBA", (64, 64), "green").save(bad)
    request = replace(_vehicle_request(tmp_path), icon_path=bad)
    with pytest.raises(ValueError, match="128x128 PNG"):
        _builder(tmp_path).build(request, tmp_path / "bad-icon.oiv")

    good = tmp_path / "good.png"
    Image.new("RGBA", (128, 128), "green").save(good)
    request = replace(request, icon_path=good)
    output = tmp_path / "good-icon.oiv"
    _builder(tmp_path).build(request, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("icon.png") == good.read_bytes()


def test_invalid_xml_and_assembly_sources_are_rejected(tmp_path: Path) -> None:
    metadata = replace(_metadata(), description="bad\x01description")
    request = _vehicle_request(tmp_path, metadata=metadata)
    with pytest.raises(ValueError, match="valid XML"):
        _builder(tmp_path).build(request, tmp_path / "invalid-xml.oiv")

    valid = _vehicle_request(tmp_path / "valid")
    plan = OivContentPlanner(
        JsonOivIdentityStore(tmp_path / "valid" / "identities.json")
    ).plan(valid)
    malicious = (
        b'<?xml version="1.0"?><!DOCTYPE package [<!ENTITY x SYSTEM "file:///x">]>'
        b'<package version="2.2" id="{00000000-0000-0000-0000-000000000000}" target="Five">'
        b'<metadata/><colors/><content/></package>'
    )
    with pytest.raises(ValueError, match="DTD|sources"):
        OivPackageValidator.validate_assembly(malicious, plan)


def test_two_vehicle_packages_own_distinct_configs_and_no_shared_runtime(
    tmp_path: Path,
) -> None:
    first = _vehicle_request(tmp_path / "first")
    second_stage = _stage(tmp_path / "second")
    # Add a second independent model into its own staged logical package.
    (second_stage / STORY_RUNTIME_NAME / "configs" / "otherbus.json").write_text(
        json.dumps({
            "schemaVersion": 1,
            "configurationId": "otherbus-axles",
            "modelName": "otherbus",
            "modelHash": joaat_hex("otherbus"),
            "minimumRuntimeVersion": "1.0.0",
            "expectedWheelCount": 6,
            "compatibility": {TARGET_STORY_LEGACY: True},
            "wheelIndexMapping": {"by_bone": {
                "wheel_lf": 0, "wheel_rf": 1,
                "wheel_lm1": 2, "wheel_rm1": 3,
                "wheel_lr": 4, "wheel_rr": 5,
            }},
            "axles": [
                {"order": 0, "role": "front", "leftBone": "wheel_lf", "rightBone": "wheel_rf", "wheelIndices": [0, 1], "steered": True, "powered": False},
                {"order": 1, "role": "middle", "leftBone": "wheel_lm1", "rightBone": "wheel_rm1", "wheelIndices": [2, 3], "steered": False, "powered": True},
                {"order": 2, "role": "rear", "leftBone": "wheel_lr", "rightBone": "wheel_rr", "wheelIndices": [4, 5], "steered": True, "powered": False},
            ],
        }), encoding="utf-8",
    )
    native_report_path = second_stage / "native-rpf-validation.json"
    native_report = json.loads(native_report_path.read_text("utf-8"))
    native_report["model_assets"] = {
        "otherbus": {"yft": True, "ytd": True},
    }
    native_report_path.write_text(json.dumps(native_report), encoding="utf-8")
    manifest_path = second_stage / "compatibility-manifest.json"
    stage_manifest = json.loads(manifest_path.read_text("utf-8"))
    stage_manifest["vehicle_artifacts"][0][
        "native_validation_report_sha256"
    ] = _sha(native_report_path)
    manifest_path.write_text(json.dumps(stage_manifest), encoding="utf-8")
    second = OivExportRequest(
        staging_root=second_stage,
        target_profile=LegacyOivTargetProfile(),
        mode=MODE_VEHICLE_ONLY,
        metadata=_metadata(package_id="com.example.otherbus"),
        vehicle_dlcs=(StagedVehicleDlc(
            "vwb_otherbus", "vehicle/vwb_mybus/dlc.rpf", ("otherbus",), "legacy",
        ),),
        axle_configurations=(StagedAxleConfiguration(
            "otherbus", joaat_hex("otherbus"),
            f"{STORY_RUNTIME_NAME}/configs/otherbus.json",
        ),),
    )
    builder = _builder(tmp_path)
    one = builder.build(first, tmp_path / "first.oiv")
    two = builder.build(second, tmp_path / "second.oiv")
    with zipfile.ZipFile(one.archive) as archive:
        names_one = set(archive.namelist())
    with zipfile.ZipFile(two.archive) as archive:
        names_two = set(archive.namelist())
    assert f"content/configs/mybus.json" in names_one
    assert f"content/configs/otherbus.json" in names_two
    assert not any(name.endswith(".asi") for name in names_one | names_two)


def test_failure_leaves_no_partial_oiv(tmp_path: Path) -> None:
    diagnostic = tmp_path / "failure-diagnostic.json"
    request = replace(
        _vehicle_request(tmp_path), diagnostic_report_path=diagnostic,
    )
    # Plan validation sees a now-invalid source checksum/config payload.
    config_path = request.staging_root / STORY_RUNTIME_NAME / "configs" / "mybus.json"
    config_path.write_text("not json", encoding="utf-8")
    output = tmp_path / "partial.oiv"
    with pytest.raises(ValueError, match="configuration.*is invalid"):
        _builder(tmp_path).build(request, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".partial-*.oiv"))
    report = json.loads(diagnostic.read_text("utf-8"))
    assert report["status"] == "failed"
    assert report["game_write_performed"] is False
    assert report["partial_output_retained"] is False
    assert report["requested_output_name"] == "partial.oiv"
