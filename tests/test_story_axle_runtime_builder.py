from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    PRESET_STEER_DRIVE_REAR,
    detect_axle_configuration,
)
from allin1_sdk.axle_runtime_bundler import (
    STORY_RUNTIME_REQUIRED_EXPORTS,
    TARGET_STORY_ENHANCED,
    TARGET_STORY_LEGACY,
    PeExportEvidence,
    VehicleAxleBuildInput,
)
from allin1_sdk import story_axle_runtime_builder as builder


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "VehicleWorkbenchAxles"


def _bones() -> tuple[SimpleNamespace, ...]:
    return tuple(
        SimpleNamespace(
            name=name,
            position=(x, y, 0.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            scale=(1.0, 1.0, 1.0),
        )
        for left, right, y in (
            ("wheel_lf", "wheel_rf", 8.0),
            ("wheel_lm1", "wheel_rm1", 4.0),
            ("wheel_lr", "wheel_rr", 0.0),
        )
        for name, x in ((left, -1.25), (right, 1.25))
    )


def _vehicle() -> VehicleAxleBuildInput:
    bones = _bones()
    configuration = detect_axle_configuration(
        "builder_bus",
        bones,
        preset=PRESET_STEER_DRIVE_REAR,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    return VehicleAxleBuildInput(
        configuration=configuration,
        configuration_id=configuration.configuration_id,
        model_hash=configuration.model_hash,
        minimum_runtime_version=configuration.minimum_runtime_version,
        steering_evidence_bones=bones,
    )


def _toolchain() -> builder.NativeAxleToolchainReport:
    return builder.NativeAxleToolchainReport(
        ready=True,
        platform="nt",
        source_root=RUNTIME,
        cmake_path=Path("cmake.exe"),
        cmake_version="3.30.0",
        ctest_path=Path("ctest.exe"),
        visual_studio_path=Path("C:/BuildTools"),
        cmake_generator="Visual Studio 17 2022",
        problems=(),
    )


def test_visual_studio_product_year_selects_matching_cmake_generator() -> None:
    for year_or_major, expected in (
        ("16", "Visual Studio 16 2019"),
        ("17", "Visual Studio 17 2022"),
        ("18", "Visual Studio 18 2026"),
        ("2019", "Visual Studio 16 2019"),
        ("2022", "Visual Studio 17 2022"),
        ("2026", "Visual Studio 18 2026"),
    ):
        generator, problem = builder._visual_studio_cmake_generator(
            Path(
                f"C:/Program Files/Microsoft Visual Studio/"
                f"{year_or_major}/BuildTools"
            ),
        )
        assert generator == expected
        assert problem is None

    generator, problem = builder._visual_studio_cmake_generator(
        Path("C:/BuildTools"),
    )
    assert generator is None
    assert problem is not None


def test_runtime_source_root_falls_back_to_wheel_data_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "installed" / "site-packages"
    prefix = tmp_path / "installed"
    wheel_runtime = (
        prefix / "share" / "allin1-sdk" / "runtime" / "VehicleWorkbenchAxles"
    )
    wheel_runtime.mkdir(parents=True)

    monkeypatch.setattr(builder, "project_root", lambda: checkout)
    monkeypatch.setattr(builder.sys, "prefix", str(prefix))

    assert builder._runtime_source_root() == wheel_runtime.resolve()


def test_runtime_paths_match_the_native_schema_two_contract() -> None:
    assert builder.validate_runtime_relative_path(
        r"scripts\ExampleTransitPack\VehicleSettings",
        "Configuration directory",
    ) == "scripts/ExampleTransitPack/VehicleSettings"

    invalid = (
        "",
        " scripts/pack",
        "scripts/pack ",
        "../outside",
        "scripts/../outside",
        "C:/outside",
        "//server/share",
        "scripts/Axles.log:stream",
        "scripts/NUL/Axles.log",
        "scripts/trailing./Axles.log",
        "scripts/trailing /Axles.log",
        "scripts/pack\n/Axles.log",
        "scripts/pack\x00/Axles.log",
    )
    for value in invalid:
        with pytest.raises(ValueError):
            builder.validate_runtime_relative_path(value, "Runtime path")
    with pytest.raises(ValueError, match="must be a string"):
        builder.validate_runtime_relative_path(None, "Runtime path")  # type: ignore[arg-type]


def test_runtime_settings_require_real_booleans_and_bounded_intervals() -> None:
    with pytest.raises(ValueError, match="Enabled"):
        builder.StoryAxleRuntimeSettings(enabled=1).validate()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Restore on unload"):
        builder.StoryAxleRuntimeSettings(
            restore_on_unload=1,  # type: ignore[arg-type]
        ).validate()
    with pytest.raises(ValueError, match="Discovery interval"):
        builder.StoryAxleRuntimeSettings(discovery_interval_ms=True).validate()
    with pytest.raises(ValueError, match="Recovery interval"):
        builder.StoryAxleRuntimeSettings(
            discovery_interval_ms=500,
            recovery_interval_ms=499,
        ).validate()


def test_run_command_translates_subprocess_timeout(monkeypatch, tmp_path: Path) -> None:
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["cmake"], 1)

    monkeypatch.setattr(builder, "run_hidden", timeout)
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="exceeded"):
        builder._run_command(
            "configure", ["cmake"], cwd=tmp_path, timeout=1,
        )


@pytest.mark.skipif(os.name != "nt", reason="Visual Studio discovery is Windows-only")
def test_empty_vswhere_result_is_not_mistaken_for_current_directory(
    monkeypatch, tmp_path: Path,
) -> None:
    vswhere = (
        tmp_path / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    )
    vswhere.parent.mkdir(parents=True)
    vswhere.write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setattr(
        builder,
        "run_hidden",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [str(vswhere)], 0, stdout="\n", stderr="",
        ),
    )

    installation, problem = builder._visual_studio_installation()

    assert installation is None
    assert problem is not None and "not installed" in problem


def test_malformed_pe_offsets_fail_with_a_controlled_error(tmp_path: Path) -> None:
    image = bytearray(64)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0xFFFF_FFF0)
    binary = tmp_path / "bad.asi"
    binary.write_bytes(image)

    with pytest.raises(
        builder.StoryAxleRuntimeBuildError, match="header is out of bounds",
    ):
        builder._pe_imports_and_signature(binary)


def test_failed_build_never_deletes_an_output_created_after_preflight(
    monkeypatch, tmp_path: Path,
) -> None:
    output = tmp_path / "candidate"
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=output,
        targets=(TARGET_STORY_LEGACY,),
    )
    monkeypatch.setattr(builder, "inspect_native_axle_toolchain", lambda **_: _toolchain())
    monkeypatch.setattr(builder, "_runtime_version", lambda _source: "4.4.0")

    def fail_after_external_creation(*_args, **_kwargs):
        output.mkdir()
        (output / "owned-by-someone-else.txt").write_text("keep", encoding="utf-8")
        raise builder.StoryAxleRuntimeBuildError("simulated build failure")

    monkeypatch.setattr(builder, "_run_command", fail_after_external_creation)

    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="simulated"):
        builder.build_story_axle_runtime_candidate(request, source_root=RUNTIME)
    assert (output / "owned-by-someone-else.txt").read_text(encoding="utf-8") == "keep"


def test_candidate_happy_path_stages_both_editions_and_custom_paths(
    monkeypatch, tmp_path: Path,
) -> None:
    output = tmp_path / "candidate"
    settings = builder.StoryAxleRuntimeSettings(
        configuration_directory="scripts/ExampleTransitPack/VehicleSettings",
        log_file="scripts/ExampleTransitPack/Axles.log",
    )
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=output,
        targets=(TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED),
        configurations=(_vehicle(),),
        settings=settings,
        build_id="focused-test",
    )
    monkeypatch.setattr(builder, "inspect_native_axle_toolchain", lambda **_: _toolchain())

    def fake_run(name, command, *, cwd, timeout):
        del cwd, timeout
        command = tuple(str(value) for value in command)
        if name == "Native build":
            build_root = Path(command[2])
            for target, edition in (
                (TARGET_STORY_LEGACY, "Legacy"),
                (TARGET_STORY_ENHANCED, "Enhanced"),
            ):
                binary = (
                    build_root / f"story-{edition}" / "Release" /
                    "VehicleWorkbenchAxles.asi"
                )
                binary.parent.mkdir(parents=True)
                binary.write_bytes(
                    b"MZ candidate VehicleWorkbenchAxles.BuildTarget="
                    + target.encode("ascii") + b"\0" + b"4.4.0\0"
                )
            validator = (
                build_root / "Release" / "VehicleWorkbenchAxlesConfigValidator.exe"
            )
            validator.parent.mkdir(parents=True, exist_ok=True)
            validator.write_bytes(b"MZ validator")
        if name == "Native configuration validation":
            assert len(command) == 3
            for value in command[1:]:
                payload = json.loads(Path(value).read_text(encoding="utf-8"))
                assert payload["modelName"] == "builder_bus"
        return builder.NativeBuildCommandRecord(
            name=name,
            command=command,
            returncode=0,
            duration_seconds=0.01,
            stdout_tail="",
            stderr_tail="",
        )

    def fake_inspect(path: Path) -> PeExportEvidence:
        data = path.read_bytes()
        return PeExportEvidence(
            architecture="x64",
            machine="AMD64",
            pe_format="PE32+",
            is_dll=True,
            file_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            exports=tuple(STORY_RUNTIME_REQUIRED_EXPORTS),
            required_exports=tuple(STORY_RUNTIME_REQUIRED_EXPORTS),
        )

    monkeypatch.setattr(builder, "_run_command", fake_run)
    monkeypatch.setattr(builder, "inspect_story_runtime_binary", fake_inspect)
    monkeypatch.setattr(
        builder, "_pe_imports_and_signature", lambda _path: (("kernel32.dll",), False),
    )

    result = builder.build_story_axle_runtime_candidate(request, source_root=RUNTIME)

    assert result.built_targets == (
        TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED,
    )
    assert len(result.archives) == 3
    assert all(path.is_file() for path in result.archives)
    for edition in ("Legacy", "Enhanced"):
        root = output / edition
        assert (root / "VehicleWorkbenchAxles.asi").is_file()
        runtime = json.loads(
            (root / "VehicleWorkbenchAxles" / "runtime.json").read_text("utf-8")
        )
        assert runtime["configurationDirectory"] == (
            "scripts/ExampleTransitPack/VehicleSettings"
        )
        assert runtime["logFile"] == "scripts/ExampleTransitPack/Axles.log"
        config = (
            root / "scripts" / "ExampleTransitPack" / "VehicleSettings" /
            "builder_bus.axles.json"
        )
        assert json.loads(config.read_text("utf-8"))["expectedWheelCount"] == 6
        receipt = json.loads(
            (root / "build-validation-receipt.json").read_text("utf-8")
        )
        assert receipt["supported"] is False
        assert receipt["game_acceptance"] == "not-tested"
        assert receipt["unsigned"] is True
    manifest = json.loads(result.manifest.read_text("utf-8"))
    assert manifest["validation"]["native_config_parser"] == "passed"
    assert manifest["validation"]["supported"] is False
    assert result.checksums.keys() == {
        "VehicleWorkbenchAxles-Legacy-4.4.0.zip",
        "VehicleWorkbenchAxles-Enhanced-4.4.0.zip",
        "VehicleWorkbenchAxles-4.4.0-Legacy-and-Enhanced.zip",
    }


def test_reserved_model_name_cannot_become_a_windows_config_file() -> None:
    with pytest.raises(ValueError, match="reserved Windows device"):
        builder._safe_config_name("con")
