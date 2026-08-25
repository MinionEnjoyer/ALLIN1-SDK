from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "VehicleWorkbenchAxles"


def test_story_runtime_profiles_are_explicitly_fail_closed() -> None:
    profile = json.loads(
        (RUNTIME / "profiles" / "compatibility.json").read_text(encoding="utf-8")
    )
    assert profile["policy"] == {
        "permanentOffsetsAllowed": False,
        "exactBuildMatchRequired": True,
        "signatureAndExecutablePageValidationRequired": True,
        "packageEligibleReceiptRequired": True,
        "x64PeExportInspectionRequired": True,
        "onlineSessionsAllowed": False,
    }
    for target in ("story-legacy", "story-enhanced"):
        assert profile["profiles"][target]["supportedGameBuilds"] == []
        assert profile["profiles"][target]["status"] == (
            "implemented-awaiting-validation"
        )

    package = json.loads(
        (RUNTIME / "profiles" / "runtime-package.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["binaryContract"]["packagingRequiresValidatedProfile"] is True
    assert package["binaryContract"]["validatedProfileExport"] == (
        "VehicleWorkbenchAxles_HasValidatedProfile"
    )
    assert all(
        not target["packageEligible"] and target["supportedGameBuilds"] == []
        for target in package["targets"].values()
    )

    adapter = (RUNTIME / "src" / "wheel_access_adapters.cpp").read_text(
        encoding="utf-8"
    )
    assert "No validated Legacy wheel-access profile" in adapter
    assert "No validated Enhanced wheel-access profile" in adapter
    assert "return false;" in adapter
    # The isolated adapter is not allowed to grow an address table unnoticed.
    assert not re.search(r"\b(?:offset|address)\s*=\s*0x[0-9a-f]{5,}", adapter, re.I)


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

    example = json.loads(
        (RUNTIME / "examples" / "example_bus.json").read_text(encoding="utf-8")
    )
    assert example["expectedWheelCount"] == 6
    assert len(example["axles"]) == 3
    assert [
        (axle["steered"], axle["powered"]) for axle in example["axles"]
    ] == [(True, False), (False, True), (True, False)]
    assert set(example["wheelIndexMapping"]["by_bone"]) == {
        "wheel_lf",
        "wheel_rf",
        "wheel_lm1",
        "wheel_rm1",
        "wheel_lr",
        "wheel_rr",
    }

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
