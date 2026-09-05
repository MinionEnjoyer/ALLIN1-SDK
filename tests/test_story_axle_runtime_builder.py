from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import zipfile
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


def _vehicle(model_name: str = "builder_bus") -> VehicleAxleBuildInput:
    bones = _bones()
    configuration = detect_axle_configuration(
        model_name,
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


def test_target_wheel_position_capability_is_read_from_exact_profile() -> None:
    assert builder._target_supports_wheel_local_position(
        RUNTIME, TARGET_STORY_LEGACY,
    ) is False
    assert builder._target_supports_wheel_local_position(
        RUNTIME, TARGET_STORY_ENHANCED,
    ) is False


def test_malformed_target_capability_blocks_native_build_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime"
    profiles = source / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "compatibility.json").write_text(
        json.dumps({
            "profiles": {
                TARGET_STORY_ENHANCED: {
                    "capabilities": {"wheelLocalPosition": "yes"},
                },
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        builder.StoryAxleRuntimeBuildError,
        match="wheelLocalPosition capability must be boolean",
    ):
        builder._target_supports_wheel_local_position(
            source, TARGET_STORY_ENHANCED,
        )


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


def test_browsed_runtime_paths_become_portable_gta_relative_values(
    tmp_path: Path,
) -> None:
    gta = tmp_path / "Grand Theft Auto V"
    config = gta / "scripts" / "TransitExpansionPack" / "VehicleSettings"
    log = gta / "scripts" / "TransitExpansionPack" / "Axles.log"

    assert builder.portable_runtime_path(
        config, (gta,), "Configuration directory",
    ) == "scripts/TransitExpansionPack/VehicleSettings"
    assert builder.portable_runtime_path(
        log, (gta,), "Log file",
    ) == "scripts/TransitExpansionPack/Axles.log"

    with pytest.raises(ValueError, match="configured GTA installations"):
        builder.portable_runtime_path(
            tmp_path / "Users" / "jessie" / "Axles.log", (gta,), "Log file",
        )


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


def test_selected_paths_are_normalized_below_the_declared_gta_root(
    tmp_path: Path,
) -> None:
    gta = tmp_path / "Grand Theft Auto V"
    configuration = gta / "scripts" / "TransitExpansionPack" / "VehicleSettings"
    log_file = gta / "scripts" / "TransitExpansionPack" / "Axles.log"
    settings = builder.StoryAxleRuntimeSettings(
        configuration_directory=str(configuration),
        log_file=str(log_file),
    ).normalize_selected_paths((gta,))

    assert settings.configuration_directory == (
        "scripts/TransitExpansionPack/VehicleSettings"
    )
    assert settings.log_file == "scripts/TransitExpansionPack/Axles.log"
    assert str(tmp_path) not in json.dumps(settings.to_runtime_json())


def test_selected_path_outside_declared_gta_roots_fails_closed(
    tmp_path: Path,
) -> None:
    gta = tmp_path / "Grand Theft Auto V"
    outside = tmp_path / "staging" / "VehicleSettings"
    with pytest.raises(ValueError, match="configured GTA installations"):
        builder.StoryAxleRuntimeSettings(
            configuration_directory=str(outside),
        ).normalize_selected_paths((gta,))


def test_metrobusxl2_uses_transit_expansion_runtime_paths_by_default(
    tmp_path: Path,
) -> None:
    defaults = builder.default_story_axle_runtime_settings("MetroBusXL2")
    assert defaults.configuration_directory == (
        "scripts/TransitExpansionPack/VehicleSettings"
    )
    assert defaults.log_file == "scripts/TransitExpansionPack/Axles.log"

    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=tmp_path / "candidate",
        targets=(TARGET_STORY_LEGACY,),
        configurations=(_vehicle("metrobusxl2"),),
    ).validate()

    assert request.settings.configuration_directory == (
        "scripts/TransitExpansionPack/VehicleSettings"
    )
    assert request.settings.log_file == "scripts/TransitExpansionPack/Axles.log"


def test_metrobusxl2_explicit_runtime_paths_are_not_overridden(
    tmp_path: Path,
) -> None:
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=tmp_path / "candidate",
        targets=(TARGET_STORY_LEGACY,),
        configurations=(_vehicle("metrobusxl2"),),
        settings=builder.StoryAxleRuntimeSettings(
            configuration_directory="scripts/OtherPack/configs",
            log_file="scripts/OtherPack/runtime.log",
        ),
    ).validate()

    assert request.settings.configuration_directory == "scripts/OtherPack/configs"
    assert request.settings.log_file == "scripts/OtherPack/runtime.log"


def test_publish_guard_rejects_local_paths_in_files_and_zip_members(
    tmp_path: Path,
) -> None:
    publish = tmp_path / "publish"
    publish.mkdir()
    leaked_root = Path(r"C:\Users\jessie\AppData\Local\Temp\axle-stage")
    loose = publish / "runtime.json"
    loose.write_text(
        json.dumps({"path": str(leaked_root / "runtime.json")}),
        encoding="utf-8",
    )
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="local"):
        builder._assert_no_local_path_leaks(publish, (leaked_root,))

    loose.unlink()
    archive_path = publish / "candidate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "VehicleWorkbenchAxles/runtime.json",
            json.dumps({"path": str(leaked_root / "runtime.json")}),
        )
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="local"):
        builder._assert_no_local_path_leaks(publish, (leaked_root,))

    archive_path.unlink()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(str(leaked_root / "runtime.json"), "{}")
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="local"):
        builder._assert_no_local_path_leaks(publish, (leaked_root,))


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


def _nominal_preflight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Path]:
    cmake = tmp_path / "CMake" / "bin" / "cmake.exe"
    ctest = cmake.with_name("ctest.exe")
    visual_studio = (
        tmp_path / "Microsoft Visual Studio" / "2022" / "BuildTools"
    )
    toolset = visual_studio / "VC" / "Tools" / "MSVC" / "14.44.35207"
    cl = toolset / "bin" / "Hostx64" / "x64" / "cl.exe"
    sdk = tmp_path / "Windows Kits" / "10"
    for fixture in (
        cmake,
        ctest,
        cl,
        visual_studio / "MSBuild" / "Current" / "Bin" / "MSBuild.exe",
        sdk / "Include" / "10.0.26100.0" / "um" / "Windows.h",
        sdk / "Include" / "10.0.26100.0" / "ucrt" / "stdlib.h",
        sdk / "Lib" / "10.0.26100.0" / "um" / "x64" / "kernel32.lib",
        sdk / "Lib" / "10.0.26100.0" / "ucrt" / "x64" / "ucrt.lib",
    ):
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(b"fixture")
    monkeypatch.setattr(
        builder.shutil,
        "which",
        lambda name: str(cmake if name == "cmake" else ctest),
    )
    monkeypatch.setattr(
        builder,
        "_executable_version",
        lambda _path, product: ("3.30.5", None),
    )
    monkeypatch.setattr(
        builder,
        "_visual_studio_details",
        lambda: (
            visual_studio, "17.10.12345.1", "Visual Studio Build Tools 2022",
            True, None,
        ),
    )
    monkeypatch.setattr(
        builder,
        "_msvc_toolset",
        lambda _path: (toolset, "14.44.35207", cl, None),
    )
    monkeypatch.setattr(
        builder,
        "_compiler_banner",
        lambda _path: ("19.44.35207", "x64", None),
    )
    monkeypatch.setattr(
        builder,
        "_windows_sdk",
        lambda: ("10.0.26100.0", sdk, None),
    )
    monkeypatch.setattr(
        builder,
        "_run_cpp17_static_probe",
        lambda **_kwargs: (
            True, "C++17 x64 compile/link passed with the static MSVC runtime",
        ),
    )
    return {
        "cmake": cmake,
        "ctest": ctest,
        "visual_studio": visual_studio,
        "toolset": toolset,
        "cl": cl,
        "sdk": sdk,
    }


def test_complete_toolchain_preflight_reports_every_required_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _nominal_preflight(monkeypatch, tmp_path)

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    assert report.ready is True
    assert report.cmake_version == "3.30.5"
    assert report.ctest_version == "3.30.5"
    assert report.visual_studio_version == "17.10.12345.1"
    assert report.vc_workload_ready is True
    assert report.cl_version == "19.44.35207"
    assert report.msvc_toolset_version == "14.44.35207"
    assert report.windows_sdk_version == "10.0.26100.0"
    assert report.host_architecture == "x64"
    assert report.target_architecture == "x64"
    assert report.probe_succeeded is True
    assert {check.key for check in report.checks} == {
        "runtime_source", "cmake", "ctest", "visual_studio", "vc_workload",
        "compiler", "msvc_toolset", "windows_sdk", "architecture",
        "selection_identity", "compile_probe",
    }
    assert all(check.ready for check in report.checks)


@pytest.mark.parametrize(
    ("missing", "failed_check"),
    (
        ("cmake", "cmake"),
        ("ctest", "ctest"),
        ("visual_studio", "visual_studio"),
        ("vc_workload", "vc_workload"),
        ("windows_sdk", "windows_sdk"),
    ),
)
def test_toolchain_preflight_fails_closed_for_clean_machine_dependencies(
    missing: str,
    failed_check: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _nominal_preflight(monkeypatch, tmp_path)
    if missing in {"cmake", "ctest"}:
        if missing == "ctest":
            paths["ctest"].unlink()
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            monkeypatch.setenv(variable, str(tmp_path / f"empty-{variable}"))
        monkeypatch.setattr(
            builder.shutil,
            "which",
            lambda name: (
                None if name == missing
                else str(paths["cmake"] if name == "cmake" else paths["ctest"])
            ),
        )
    elif missing == "visual_studio":
        monkeypatch.setattr(
            builder,
            "_visual_studio_details",
            lambda: (None, None, None, False, "Visual Studio is not installed"),
        )
    elif missing == "vc_workload":
        monkeypatch.setattr(
            builder,
            "_visual_studio_details",
            lambda: (
                paths["visual_studio"], "17.10.12345.1",
                "Visual Studio Build Tools 2022", False, None,
            ),
        )
    elif missing == "windows_sdk":
        monkeypatch.setattr(
            builder,
            "_windows_sdk",
            lambda: (None, None, "Windows SDK was not found"),
        )

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    assert report.ready is False
    by_key = {check.key: check for check in report.checks}
    assert by_key[failed_check].ready is False
    assert by_key[failed_check].guidance
    assert by_key["compile_probe"].ready is False
    assert report.guidance


def test_preflight_rejects_mismatched_ctest_x86_and_failed_static_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _nominal_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr(
        builder,
        "_executable_version",
        lambda _path, product: (
            "3.30.5" if product == "cmake" else "3.29.9", None,
        ),
    )
    x86_cl = (
        paths["toolset"] / "bin" / "Hostx86" / "x86" / "cl.exe"
    )
    monkeypatch.setattr(
        builder,
        "_msvc_toolset",
        lambda _path: (paths["toolset"], "14.44.35207", x86_cl, None),
    )
    monkeypatch.setattr(
        builder,
        "_compiler_banner",
        lambda _path: ("19.44.35207", "x86", None),
    )
    monkeypatch.setattr(
        builder,
        "_run_cpp17_static_probe",
        lambda **_kwargs: (False, "Static runtime probe failed"),
    )

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    checks = {check.key: check for check in report.checks}
    assert report.ready is False
    assert checks["ctest"].ready is False
    assert checks["architecture"].ready is False
    assert checks["compile_probe"].ready is False


def test_preflight_fails_closed_when_isolated_static_runtime_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _nominal_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr(
        builder,
        "_run_cpp17_static_probe",
        lambda **_kwargs: (False, "simulated static-runtime link failure"),
    )

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    checks = {check.key: check for check in report.checks}
    assert report.ready is False
    assert checks["compile_probe"].ready is False
    assert "static-runtime" in checks["compile_probe"].detected


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
    monkeypatch.setattr(
        builder, "_verify_preflight_selection",
        lambda report, **_kwargs: report,
    )
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
    monkeypatch.setattr(
        builder, "_verify_preflight_selection",
        lambda report, **_kwargs: report,
    )

    def fake_run(name, command, *, cwd, timeout, env=None):
        del cwd, timeout, env
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
                    + target.encode("ascii") + b"\0" + b"4.5.0\0"
                )
            validator = (
                build_root / "Release" / "VehicleWorkbenchAxlesConfigValidator.exe"
            )
            validator.parent.mkdir(parents=True, exist_ok=True)
            validator.write_bytes(b"MZ validator")
            (build_root / "Release" / "VehicleWorkbenchAxles.Settings.exe").write_bytes(
                b"MZ settings editor"
            )
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
            stdout_tail=(
                r"Built C:\Users\jessie\AppData\Local\Temp\axle-stage\output"
            ),
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
    runtime_recompute_choices: list[bool | None] = []
    real_story_configuration = builder.story_native_runtime_configuration

    def capture_story_configuration(*args, **kwargs):
        runtime_recompute_choices.append(
            kwargs.get("runtime_geometry_recompute")
        )
        return real_story_configuration(*args, **kwargs)

    monkeypatch.setattr(
        builder,
        "story_native_runtime_configuration",
        capture_story_configuration,
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
        assert (root / "VehicleWorkbenchAxles.Settings.exe").is_file()
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
        config_payload = json.loads(config.read_text("utf-8"))
        assert config_payload["expectedWheelCount"] == 6
        receipt = json.loads(
            (root / "build-validation-receipt.json").read_text("utf-8")
        )
        assert receipt["supported"] is False
        assert receipt["game_acceptance"] == "not-tested"
        assert receipt["unsigned"] is True
        assert receipt["settings_editor"]["artifact"] == (
            "VehicleWorkbenchAxles.Settings.exe"
        )
        assert len(receipt["settings_editor"]["sha256"]) == 64
    manifest = json.loads(result.manifest.read_text("utf-8"))
    assert manifest["validation"]["native_config_parser"] == "passed"
    assert manifest["validation"]["supported"] is False
    assert manifest["validation"]["settings_editor_pe_x64"] == "passed"
    assert manifest["toolchain"]["cmake"]["version"] == "3.30.0"
    assert manifest["toolchain"]["visual_studio"]["generator"] == (
        "Visual Studio 17 2022"
    )
    assert len(manifest["settings_editor_sha256"]) == 64
    assert result.checksums.keys() == {
        "VehicleWorkbenchAxles-Legacy-4.5.0.zip",
        "VehicleWorkbenchAxles-Enhanced-4.5.0.zip",
        "VehicleWorkbenchAxles-4.5.0-Legacy-and-Enhanced.zip",
    }
    assert runtime_recompute_choices == [False, False]
    manifest_text = result.manifest.read_text("utf-8")
    assert r"C:\Users\jessie" not in manifest_text
    assert "C:/Users/jessie" not in manifest_text
    assert str(RUNTIME) not in manifest_text
    assert RUNTIME.as_posix() not in manifest_text
    assert str(tmp_path) not in manifest_text
    assert tmp_path.as_posix() not in manifest_text
    assert r"C:\BuildTools" not in manifest_text
    assert "C:/BuildTools" not in manifest_text
    assert all(
        r"C:\Users\jessie" not in record.stdout_tail
        for record in result.commands
    )
    for archive_path in result.archives:
        with zipfile.ZipFile(archive_path) as archive:
            assert any(
                name.endswith("VehicleWorkbenchAxles.Settings.exe")
                for name in archive.namelist()
            )
            for name in archive.namelist():
                assert r"C:\Users\jessie" not in name
                payload = archive.read(name)
                assert b"C:\\Users\\jessie" not in payload
                assert b"C:/Users/jessie" not in payload
                if Path(name).suffix.casefold() in {".json", ".txt", ".md", ".xml"}:
                    assert r"C:\Users\jessie" not in payload.decode(
                        "utf-8", errors="replace",
                    )


def test_reserved_model_name_cannot_become_a_windows_config_file() -> None:
    with pytest.raises(ValueError, match="reserved Windows device"):
        builder._safe_config_name("con")
