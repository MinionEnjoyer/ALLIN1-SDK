from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from allin1_sdk.axle_configurator import AxleConfiguration, joaat_hex
from allin1_sdk.axle_steering_geometry import canonical_bone_position_sha256


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "VehicleWorkbenchAxles"


def test_native_build_stages_self_contained_runtime_contract() -> None:
    package = json.loads(
        (RUNTIME / "profiles" / "runtime-package.json").read_text(
            encoding="utf-8"
        )
    )
    build_script = (ROOT / "scripts" / "build_native_asi.ps1").read_text(
        encoding="utf-8"
    )

    for key in ("profileSchema", "receiptSchema"):
        relative_path = package["receiptContract"][key]
        source_path = (RUNTIME / "profiles" / relative_path).resolve()
        assert source_path.is_file()
        assert source_path.name in build_script

    assert "axle-config.schema.json" in build_script
    assert package["runtime"]["settingsEditorFileName"] == (
        "VehicleWorkbenchAxles.Settings.exe"
    )
    assert package["runtime"]["settingsEditorFileName"] in build_script


def test_story_runtime_profiles_are_compiled_but_distribution_stays_fail_closed(
) -> None:
    profile = json.loads(
        (RUNTIME / "profiles" / "compatibility.json").read_text(encoding="utf-8")
    )
    assert profile["runtimeVersion"] == "4.5.0"
    assert profile["policy"] == {
        "permanentOffsetsAllowed": False,
        "runtimeProfileIdentityPolicy": (
            "edition-plus-unique-signature-and-layout-canaries"
        ),
        "distributionExactBuildMatchRequired": True,
        "signatureAndExecutablePageValidationRequired": True,
        "packageEligibleReceiptRequired": True,
        "compiledProfilePresenceIsDistributionApproval": False,
        "x64PeExportInspectionRequired": True,
        "signedSteeringGainRequiresValidatedAccessor": True,
        "runtimeGeometryRequiresValidatedWheelLocalPositionAccessor": True,
        "staticForceRequiresValidatedAccessorAndPhysicsActivation": True,
        "onlineSessionsAllowed": False,
    }
    for target in ("story-legacy", "story-enhanced"):
        assert profile["profiles"][target]["supportedGameBuilds"] == []
        assert profile["profiles"][target]["status"] == (
            "compiled-awaiting-in-game-acceptance"
        )
        assert profile["profiles"][target]["wheelProfileStatus"] == (
            "compiled-signature-gated"
        )
        assert profile["profiles"][target]["capabilities"] == {
            "steeringFlags": True,
            "driveFlags": True,
            "wheelBoneIdVerification": True,
            "wheelGenerationToken": True,
            "signedSteeringGain": True,
            "wheelLocalPosition": False,
            "staticForce": True,
            "physicsActivation": True,
        }
    assert profile["profiles"]["story-legacy"][
        "observedUnacceptedExecutables"
    ] == []
    observed = profile["profiles"]["story-enhanced"][
        "observedUnacceptedExecutables"
    ]
    assert observed == [
        {
            "fileName": "GTA5_Enhanced.exe",
            "fileVersion": "1.0.1158.13",
            "build": 1158,
            "sha256": (
                "0C52864D4521D9C9D441348AA1156958792DDE8825D0297C851753F167336401"
            ),
            "acceptanceStatus": "observed-not-accepted",
        }
    ]

    package = json.loads(
        (RUNTIME / "profiles" / "runtime-package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["runtime"]["version"] == profile["runtimeVersion"]
    assert package["runtime"]["settingsSchemaVersion"] == 2
    assert package["binaryContract"]["packagingRequiresValidatedProfile"] is True
    assert package["binaryContract"]["validatedProfileExport"] == (
        "VehicleWorkbenchAxles_HasValidatedProfile"
    )
    assert package["binaryContract"]["hostBridge"][
        "wheelMemoryAccessIncluded"
    ] is True
    assert package["binaryContract"][
        "compiledProfilePresenceIsDistributionApproval"
    ] is False
    assert package["binaryContract"]["capabilities"][
        "signedSteeringGain"
    ] is True
    assert package["binaryContract"]["capabilities"][
        "wheelLocalPosition"
    ] is False
    assert "VehicleWorkbenchAxles_HasScriptHookHost" in package[
        "receiptContract"
    ]["executableExportsRequired"]
    assert package["runtime"]["maximumAxleSchemaVersion"] == 4
    assert package["binaryContract"]["capabilities"][
        "wheelBoneIdVerification"
    ] is True
    assert package["binaryContract"]["capabilities"][
        "wheelGenerationToken"
    ] is True
    assert package["binaryContract"]["capabilities"]["staticForce"] is True
    assert package["binaryContract"]["capabilities"]["physicsActivation"] is True
    assert all(
        not target["packageEligible"]
        and target["supportedGameBuilds"] == []
        and target["validationReceipt"] is None
        for target in package["targets"].values()
    )

    adapter = (RUNTIME / "src" / "wheel_access_adapters.cpp").read_text(
        encoding="utf-8"
    )
    assert "class SignatureWheelProfile" in adapter
    assert "DefaultProfiles(Edition::Legacy)" in adapter
    assert "DefaultProfiles(Edition::Enhanced)" in adapter
    assert "unique executable signatures" in (
        RUNTIME / "README.md"
    ).read_text(encoding="utf-8")
    # Derived private layout values are allowed; permanent address tables are not.
    assert not re.search(r"\b(?:offset|address)\s*=\s*0x[0-9a-f]{5,}", adapter, re.I)

    entry = (RUNTIME / "src" / "asi_entry.cpp").read_text(encoding="utf-8")
    assert "script-hook-host-ready-signature-gated-wheel-profile" in entry
    assert re.search(
        r"VehicleWorkbenchAxles_HasValidatedProfile\(\).*?return true;",
        entry,
        re.S,
    )


def test_story_runtime_schema_and_fixture_are_variable_length() -> None:
    schema = json.loads(
        (RUNTIME / "schemas" / "axle-config.schema.json").read_text(
            encoding="utf-8"
        )
    )
    axle_array = schema["properties"]["axles"]
    assert axle_array["minItems"] == 2
    assert axle_array["maxItems"] == 5
    assert schema["properties"]["expectedWheelCount"]["enum"] == [4, 6, 8, 10]
    gain_schema = axle_array["items"]["properties"]["steeringGain"]
    assert gain_schema["minimum"] == -1.0
    assert gain_schema["maximum"] == 1.0

    example = json.loads(
        (RUNTIME / "examples" / "example_bus.json").read_text(encoding="utf-8")
    )
    bone_fixture = json.loads(
        (RUNTIME / "examples" / "example_bus.bones.json").read_text(
            encoding="utf-8"
        )
    )
    assert example["expectedWheelCount"] == 6
    assert example["modelHash"] == joaat_hex(example["modelName"])
    assert len(example["axles"]) == 3
    assert [
        (axle["steered"], axle["powered"]) for axle in example["axles"]
    ] == [(True, False), (False, True), (True, False)]
    assert [axle["steeringGain"] for axle in example["axles"]] == [
        1.0, 0.0, -0.22,
    ]
    assert set(example["wheelIndexMapping"]["by_bone"]) == {
        "wheel_lf",
        "wheel_rf",
        "wheel_lm1",
        "wheel_rm1",
        "wheel_lr",
        "wheel_rr",
    }
    configuration = AxleConfiguration.from_dict(example)
    bones = tuple(
        SimpleNamespace(name=item["name"], position=tuple(item["position"]))
        for item in bone_fixture["bones"]
    )
    assert bone_fixture["deployable"] is False
    assert example["steeringCalculation"]["bonePositionSha256"] == (
        canonical_bone_position_sha256(configuration, bones)
    )

    core = (RUNTIME / "src" / "runtime.cpp").read_text(encoding="utf-8")
    assert "wheel_index_map.find" in core
    assert "expected_wheel_count" in core
    assert "kSteeredBit" in core and "kDrivenBit" in core
    assert "host.IsOnlineSession()" in core
    assert "axleOrder * 2" not in core


def test_story_runtime_build_contract_keeps_targets_and_dependencies_separate() -> None:
    cmake = (RUNTIME / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "VehicleWorkbenchAxlesCore" in cmake
    assert "VehicleWorkbenchAxlesLegacy" in cmake
    assert "VehicleWorkbenchAxlesEnhanced" in cmake
    assert 'SUFFIX ".asi"' in cmake
    assert "ScriptHookV" not in cmake
    assert "FiveM" not in cmake

    readme = (RUNTIME / "README.md").read_text(encoding="utf-8")
    assert "Neither Story Mode edition is deployable" in readme
    assert "never assumes `axleOrder * 2`" in readme
    assert "must not" in readme and "redistribute ScriptHookV" in readme
    assert "absolute user paths" in readme


def _compiler() -> str | None:
    configured = os.environ.get("CXX")
    if configured and shutil.which(configured):
        return shutil.which(configured)
    for candidate in ("clang++", "g++", "cl"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def test_platform_neutral_story_runtime_core_compiles_and_passes(tmp_path: Path) -> None:
    cmake = shutil.which("cmake")
    compiler = _compiler()
    if not cmake or not compiler:
        pytest.skip("CMake and a C++17 compiler are required for the native smoke test")

    build = tmp_path / "vwa-core-build"
    generator_args = ["-G", "Ninja"] if shutil.which("ninja") else []
    configure = subprocess.run(
        [
            cmake,
            "-S",
            str(RUNTIME),
            "-B",
            str(build),
            *generator_args,
            f"-DCMAKE_CXX_COMPILER={compiler}",
            "-DVWA_BUILD_ASI_SKELETONS=OFF",
            "-DVWA_BUILD_TESTS=ON",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert configure.returncode == 0, configure.stdout + configure.stderr

    compiled = subprocess.run(
        [cmake, "--build", str(build), "--config", "Debug"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    tested = subprocess.run(
        ["ctest", "--test-dir", str(build), "--output-on-failure", "-C", "Debug"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert tested.returncode == 0, tested.stdout + tested.stderr
    assert "100% tests passed" in tested.stdout
