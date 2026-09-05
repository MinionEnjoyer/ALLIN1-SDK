from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from allin1_sdk import story_axle_runtime_builder as builder


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "VehicleWorkbenchAxles"


def test_vswhere_accepts_build_tools_cpp_workload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Build Tools uses Workload.VCTools, not the full IDE workload id."""

    commands: list[list[object]] = []

    def completed(command: list[object], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=str(tmp_path / "BuildTools") + "\n", stderr="",
        )

    monkeypatch.setattr(builder, "run_hidden", completed)
    value, problem = builder._vswhere_property(
        tmp_path / "vswhere.exe", "installationPath", require_vc=True,
    )

    assert problem is None
    assert value == str(tmp_path / "BuildTools")
    assert builder._VC_WORKLOAD in commands[0]
    assert builder._NATIVE_DESKTOP_WORKLOAD not in commands[0]


def _file(path: Path, payload: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path.resolve()


def _native_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    visual_studio: Path | None = None,
    visual_studio_version: str = "17.10.12345.1",
    workload: bool = True,
    probe: bool = True,
) -> dict[str, Path]:
    vs = visual_studio or (
        tmp_path / "Microsoft Visual Studio" / "2022" / "BuildTools"
    )
    vs.mkdir(parents=True, exist_ok=True)
    toolset = vs / "VC" / "Tools" / "MSVC" / "14.44.35207"
    cl = _file(toolset / "bin" / "Hostx64" / "x64" / "cl.exe")
    _file(vs / "MSBuild" / "Current" / "Bin" / "MSBuild.exe")
    sdk = tmp_path / "Windows Kits" / "10"
    for fixture in (
        sdk / "Include" / "10.0.26100.0" / "um" / "Windows.h",
        sdk / "Include" / "10.0.26100.0" / "ucrt" / "stdlib.h",
        sdk / "Lib" / "10.0.26100.0" / "um" / "x64" / "kernel32.lib",
        sdk / "Lib" / "10.0.26100.0" / "ucrt" / "x64" / "ucrt.lib",
    ):
        _file(fixture)

    details = (
        vs.resolve(), visual_studio_version,
        "Visual Studio Build Tools 2022", workload, None,
    )
    monkeypatch.setattr(builder, "_visual_studio_details", lambda: details)
    monkeypatch.setattr(
        builder, "_configured_visual_studio_details", lambda _path: details,
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
        lambda: ("10.0.26100.0", sdk.resolve(), None),
    )
    monkeypatch.setattr(
        builder,
        "_run_cpp17_static_probe",
        lambda **_kwargs: (
            probe,
            "C++17 x64 configure/build/link and CTest passed"
            if probe else "simulated C++17/CTest failure",
        ),
    )
    return {
        "visual_studio": vs.resolve(),
        "toolset": toolset.resolve(),
        "cl": cl,
        "sdk": sdk.resolve(),
    }


def _versions(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cmake: str = "3.30.5",
    ctest: str = "3.30.5",
) -> None:
    monkeypatch.setattr(
        builder,
        "_executable_version",
        lambda _path, product: (
            cmake if product.casefold() == "cmake" else ctest,
            None,
        ),
    )


def test_auto_detects_visual_studio_bundled_cmake_before_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(
        tools["visual_studio"] / "Common7" / "IDE" / "CommonExtensions" /
        "Microsoft" / "CMake" / "CMake" / "bin" / "cmake.exe",
    )
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.setattr(
        builder, "_which_fresh",
        lambda _name: pytest.fail("PATH must not outrank Visual Studio CMake"),
    )

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    assert report.ready is True
    assert report.cmake_path == cmake
    assert report.ctest_path == ctest
    assert report.cmake_discovery_source == "Visual Studio bundled"
    assert report.ctest_discovery_source == "beside selected CMake"


def test_auto_detects_program_files_cmake_when_path_has_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _native_prerequisites(monkeypatch, tmp_path)
    program_files = tmp_path / "Program Files"
    cmake = _file(program_files / "CMake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramW6432", "")
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setattr(builder, "_which_fresh", lambda _name: None)

    report = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    assert report.ready is True
    assert report.cmake_path == cmake
    assert report.ctest_path == ctest
    assert report.cmake_discovery_source == "Program Files"


def test_manual_paths_with_spaces_are_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(tmp_path / "Manual Tools" / "CMake Bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.setattr(
        builder, "_which_fresh",
        lambda _name: pytest.fail("manual mode must not fall back to PATH"),
    )
    settings = builder.NativeAxleToolchainSettings(
        mode="manual",
        cmake_path=cmake,
        ctest_path=ctest,
        visual_studio_path=tools["visual_studio"],
    )

    report = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME, settings=settings,
    )

    assert report.ready is True
    assert report.settings_mode == "manual"
    assert report.cmake_path == cmake
    assert report.ctest_path == ctest
    assert report.cmake_discovery_source == "user-configured"
    assert report.ctest_discovery_source == "user-configured"
    assert report.visual_studio_discovery_source == "user-configured"


def test_manual_cl_executable_is_preserved_instead_of_selecting_newest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    selected_cl = tools["cl"]
    newer = _file(
        tools["visual_studio"] / "VC" / "Tools" / "MSVC" / "14.49.99999" /
        "bin" / "Hostx64" / "x64" / "cl.exe",
        b"different compiler",
    )
    cmake = _file(tmp_path / "Manual CMake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.setattr(
        builder, "_msvc_toolset",
        lambda _path: pytest.fail("explicit cl.exe must not select the newest toolset"),
    )

    report = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME,
        settings=builder.NativeAxleToolchainSettings(
            mode="manual",
            cmake_path=cmake,
            ctest_path=ctest,
            visual_studio_path=selected_cl,
        ),
    )

    assert newer.is_file()
    assert report.ready is True
    assert report.cl_path == selected_cl
    assert report.msvc_toolset_version == "14.44.35207"


def test_invalid_explicit_override_blocks_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    _versions(monkeypatch)
    fallback = _file(tmp_path / "fallback" / "cmake.exe")
    settings = builder.NativeAxleToolchainSettings(
        mode="auto",
        cmake_path=tmp_path / "missing" / "cmake.exe",
        ctest_path=fallback.with_name("ctest.exe"),
        visual_studio_path=tools["visual_studio"],
    )
    monkeypatch.setattr(
        builder, "_which_fresh",
        lambda _name: pytest.fail("an invalid override must fail closed"),
    )

    report = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME, settings=settings,
    )

    assert report.ready is False
    assert report.cmake_path is None
    cmake_check = next(check for check in report.checks if check.key == "cmake")
    assert "does not exist" in (cmake_check.detail or cmake_check.detected)
    assert cmake_check.guidance


def test_old_cmake_is_blocked_before_the_compile_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(tmp_path / "old-cmake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch, cmake="3.19.8", ctest="3.19.8")
    probe_called = False

    def probe(**_kwargs):
        nonlocal probe_called
        probe_called = True
        return True, "unexpected"

    monkeypatch.setattr(builder, "_run_cpp17_static_probe", probe)
    report = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME,
        settings=builder.NativeAxleToolchainSettings(
            mode="manual", cmake_path=cmake, ctest_path=ctest,
            visual_studio_path=tools["visual_studio"],
        ),
    )

    assert report.ready is False
    assert probe_called is False
    check = next(check for check in report.checks if check.key == "cmake")
    assert "older than required 3.20" in check.detail


def test_unreadable_component_identity_blocks_ready_and_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(tmp_path / "CMake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    original_identity = builder._file_identity

    def identity(path: Path) -> str:
        if path == cmake:
            raise PermissionError("simulated unreadable cmake")
        return original_identity(path)

    monkeypatch.setattr(builder, "_file_identity", identity)
    probe_called = False

    def probe(**_kwargs):
        nonlocal probe_called
        probe_called = True
        return True, "unexpected"

    monkeypatch.setattr(builder, "_run_cpp17_static_probe", probe)
    report = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME,
        settings=builder.NativeAxleToolchainSettings(
            mode="manual", cmake_path=cmake, ctest_path=ctest,
            visual_studio_path=tools["visual_studio"],
        ),
    )

    assert report.ready is False
    assert probe_called is False
    check = next(
        item for item in report.checks if item.key == "selection_identity"
    )
    assert check.ready is False
    assert "cmake" in check.detected


def test_missing_ctest_and_cross_install_ctest_are_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tools = _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(tmp_path / "selected" / "bin" / "cmake.exe")
    other_ctest = _file(tmp_path / "other" / "bin" / "ctest.exe")
    _versions(monkeypatch)

    monkeypatch.setattr(builder, "_which_fresh", lambda _name: None)
    missing = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME,
        settings=builder.NativeAxleToolchainSettings(
            mode="auto", cmake_path=cmake,
            visual_studio_path=tools["visual_studio"],
        ),
    )
    assert missing.ready is False
    assert missing.ctest_path is None

    mismatched = builder.inspect_native_axle_toolchain(
        source_root=RUNTIME,
        settings=builder.NativeAxleToolchainSettings(
            mode="manual", cmake_path=cmake, ctest_path=other_ctest,
            visual_studio_path=tools["visual_studio"],
        ),
    )
    assert mismatched.ready is False
    check = next(check for check in mismatched.checks if check.key == "ctest")
    assert "different installation" in check.detail


def test_recheck_observes_path_installed_after_process_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _native_prerequisites(monkeypatch, tmp_path)
    cmake = _file(tmp_path / "installed later" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.delenv("ALLIN1_TEST_NEW_PATH", raising=False)
    monkeypatch.setenv("ProgramFiles", "")
    monkeypatch.setenv("ProgramW6432", "")
    monkeypatch.setenv("ProgramFiles(x86)", "")

    def which(name: str) -> Path | None:
        if os.environ.get("ALLIN1_TEST_NEW_PATH") != "ready":
            return None
        return cmake if name.startswith("cmake") else ctest

    monkeypatch.setattr(builder, "_which_fresh", which)
    first = builder.inspect_native_axle_toolchain(source_root=RUNTIME)
    monkeypatch.setenv("ALLIN1_TEST_NEW_PATH", "ready")
    second = builder.inspect_native_axle_toolchain(source_root=RUNTIME)

    assert first.ready is False
    assert first.cmake_path is None
    assert second.ready is True
    assert second.cmake_path == cmake
    assert second.ctest_path == ctest
    assert second.cmake_discovery_source == "current machine/user PATH"


def test_fresh_path_lookup_rereads_process_path_on_every_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: list[str] = []

    def which(_name: str, *, path: str | None = None):
        seen.append(path or "")
        return None

    monkeypatch.setattr(builder.shutil, "which", which)
    first = str(tmp_path / "before-install")
    second = str(tmp_path / "after-install")
    monkeypatch.setenv("PATH", first)
    assert builder._which_fresh("cmake.exe") is None
    monkeypatch.setenv("PATH", second)
    assert builder._which_fresh("cmake.exe") is None

    assert first in seen[0]
    assert second in seen[1]
    assert seen[0] != seen[1]


def test_incompatible_generator_and_failed_probe_each_block_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    incompatible_vs = tmp_path / "Microsoft Visual Studio" / "2017" / "BuildTools"
    _native_prerequisites(
        monkeypatch, tmp_path, visual_studio=incompatible_vs,
        visual_studio_version="15.9.1",
    )
    cmake = _file(tmp_path / "cmake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    _versions(monkeypatch)
    monkeypatch.setattr(builder, "_which_fresh", lambda name: cmake if name.startswith("cmake") else ctest)

    incompatible = builder.inspect_native_axle_toolchain(source_root=RUNTIME)
    assert incompatible.ready is False
    generator = next(
        check for check in incompatible.checks if check.key == "visual_studio"
    )
    assert generator.ready is False

    valid_vs = tmp_path / "Microsoft Visual Studio" / "2022" / "BuildTools"
    _native_prerequisites(
        monkeypatch, tmp_path, visual_studio=valid_vs, probe=False,
    )
    failed_probe = builder.inspect_native_axle_toolchain(source_root=RUNTIME)
    assert failed_probe.ready is False
    check = next(
        check for check in failed_probe.checks if check.key == "compile_probe"
    )
    assert check.ready is False
    assert "simulated" in check.detected


def test_cpp17_probe_runs_selected_ctest_after_release_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cmake = _file(tmp_path / "CMake Tools" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    visual_studio = tmp_path / "Microsoft Visual Studio" / "2022" / "BuildTools"
    windows_sdk = tmp_path / "Windows Kits" / "10"
    windows_sdk.mkdir(parents=True)
    cl = _file(
        visual_studio / "VC" / "Tools" / "MSVC" / "14.44.35207" /
        "bin" / "Hostx64" / "x64" / "cl.exe",
    )
    commands: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        rendered = tuple(str(value) for value in command)
        commands.append(rendered)
        if len(rendered) > 1 and rendered[1] == "--build":
            executable = Path(rendered[2]) / "Release" / "allin1_axle_probe.exe"
            _file(executable, b"MZ probe")
        return subprocess.CompletedProcess(rendered, 0, stdout="passed", stderr="")

    monkeypatch.setattr(builder, "run_hidden", run)
    monkeypatch.setattr(
        builder, "_pe_imports_and_signature", lambda _path: ((), False),
    )

    ready, detail = builder._run_cpp17_static_probe(
        cmake=cmake,
        ctest=ctest,
        generator="Visual Studio 17 2022",
        visual_studio=visual_studio,
        cl_path=cl,
        toolset_version="14.44.35207",
        windows_sdk_version="10.0.26100.0",
        windows_sdk_path=windows_sdk,
    )

    assert ready is True, detail
    assert len(commands) == 3
    assert Path(commands[0][0]) == cmake
    assert "-A" in commands[0] and "x64" in commands[0]
    assert any("CMAKE_GENERATOR_INSTANCE" in value for value in commands[0])
    assert any("CMAKE_CXX_COMPILER" in value for value in commands[0])
    assert (
        f"-DCMAKE_WINDOWS_KITS_10_DIR:PATH={windows_sdk}" in commands[0]
    )
    assert commands[2][0] == str(ctest)
    assert commands[2][1] == "--test-dir"
    assert commands[2][3:] == ("-C", "Release", "--output-on-failure")


def _ready_snapshot(
    tmp_path: Path,
) -> builder.NativeAxleToolchainReport:
    cmake = _file(tmp_path / "Pinned Toolchain" / "CMake" / "bin" / "cmake.exe")
    ctest = _file(cmake.with_name("ctest.exe"))
    visual_studio = (
        tmp_path / "Pinned Toolchain" / "Microsoft Visual Studio" /
        "2022" / "BuildTools"
    )
    visual_studio.mkdir(parents=True)
    _file(visual_studio / "MSBuild" / "Current" / "Bin" / "MSBuild.exe")
    cl = _file(
        visual_studio / "VC" / "Tools" / "MSVC" / "14.44.35207" /
        "bin" / "Hostx64" / "x64" / "cl.exe",
    )
    sdk = tmp_path / "Pinned Toolchain" / "Windows Kits" / "10"
    for fixture in (
        sdk / "Include" / "10.0.26100.0" / "um" / "Windows.h",
        sdk / "Include" / "10.0.26100.0" / "ucrt" / "stdlib.h",
        sdk / "Lib" / "10.0.26100.0" / "um" / "x64" / "kernel32.lib",
        sdk / "Lib" / "10.0.26100.0" / "ucrt" / "x64" / "ucrt.lib",
    ):
        _file(fixture)
    versions = {
        "cmake": "3.30.5",
        "ctest": "3.30.5",
        "visual_studio": "17.10.12345.1",
        "cl": "19.44.35207",
        "msvc_toolset": "14.44.35207",
        "windows_sdk": "10.0.26100.0",
    }
    identities, fingerprint = builder._selection_fingerprint(
        cmake=cmake,
        ctest=ctest,
        visual_studio=visual_studio,
        cl_path=cl,
        windows_sdk_root=sdk,
        versions=versions,
        generator="Visual Studio 17 2022",
    )
    return builder.NativeAxleToolchainReport(
        ready=True,
        platform="nt",
        source_root=RUNTIME,
        cmake_path=cmake,
        cmake_version=versions["cmake"],
        ctest_path=ctest,
        visual_studio_path=visual_studio,
        cmake_generator="Visual Studio 17 2022",
        problems=(),
        ctest_version=versions["ctest"],
        visual_studio_version=versions["visual_studio"],
        visual_studio_name="Visual Studio Build Tools 2022",
        vc_workload_ready=True,
        cl_path=cl,
        cl_version=versions["cl"],
        msvc_toolset_version=versions["msvc_toolset"],
        windows_sdk_version=versions["windows_sdk"],
        host_architecture="x64",
        target_architecture="x64",
        probe_succeeded=True,
        probe_detail="passed",
        settings_mode="manual",
        cmake_discovery_source="user-configured",
        ctest_discovery_source="user-configured",
        visual_studio_discovery_source="user-configured",
        cmake_generator_architecture="x64",
        cmake_toolset="version=14.44.35207,host=x64",
        windows_sdk_path=sdk,
        visual_studio_instance_id="Visual Studio Build Tools 2022 17.10.12345.1",
        component_identities=identities,
        selection_fingerprint=fingerprint,
    )


def test_real_build_reuses_exact_preflight_selection_without_rediscovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _ready_snapshot(tmp_path)
    _versions(monkeypatch)
    monkeypatch.setattr(
        builder,
        "_compiler_banner",
        lambda _path: ("19.44.35207", "x64", None),
    )
    monkeypatch.setattr(
        builder,
        "inspect_native_axle_toolchain",
        lambda **_kwargs: pytest.fail("a displayed preflight must not be rediscovered"),
    )
    commands: list[tuple[str, tuple[str, ...]]] = []

    environments: list[dict[str, str] | None] = []

    def run(name, command, *, cwd, timeout, env=None):
        del cwd, timeout
        environments.append(env)
        rendered = tuple(str(value) for value in command)
        commands.append((name, rendered))
        if name == "Native CTest":
            raise builder.StoryAxleRuntimeBuildError("stop after command selection")
        return builder.NativeBuildCommandRecord(
            name=name,
            command=rendered,
            returncode=0,
            duration_seconds=0.01,
            stdout_tail="",
            stderr_tail="",
        )

    monkeypatch.setattr(builder, "_run_command", run)
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=tmp_path / "candidate",
        targets=(builder.TARGET_STORY_LEGACY,),
        toolchain_report=report,
    )

    with pytest.raises(
        builder.StoryAxleRuntimeBuildError, match="stop after command selection",
    ):
        builder.build_story_axle_runtime_candidate(request, source_root=RUNTIME)

    assert [name for name, _command in commands] == [
        "CMake configure", "Native build", "Native CTest",
    ]
    configure = commands[0][1]
    build = commands[1][1]
    ctest = commands[2][1]
    assert configure[0] == str(report.cmake_path)
    assert build[0] == str(report.cmake_path)
    assert ctest[0] == str(report.ctest_path)
    assert any(
        value == f"-DCMAKE_GENERATOR_INSTANCE:PATH={report.visual_studio_path}"
        for value in configure
    )
    assert any(
        value == f"-DCMAKE_CXX_COMPILER:FILEPATH={report.cl_path}"
        for value in configure
    )
    assert ("-T", "version=14.44.35207,host=x64") == (
        configure[configure.index("-T")], configure[configure.index("-T") + 1],
    )
    assert "-DCMAKE_SYSTEM_VERSION=10.0.26100.0" in configure
    assert any("CMAKE_WINDOWS_KITS_10_DIR" in value for value in configure)
    assert all(
        environment is not None
        and environment["WindowsSDKVersion"] == "10.0.26100.0\\"
        and environment["WindowsSdkDir"].rstrip("\\/")
        == str(report.windows_sdk_path).rstrip("\\/")
        for environment in environments
    )


def test_real_build_aborts_when_preflight_component_disappears(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = _ready_snapshot(tmp_path)
    assert report.ctest_path is not None
    report.ctest_path.unlink()
    monkeypatch.setattr(
        builder,
        "inspect_native_axle_toolchain",
        lambda **_kwargs: pytest.fail("a frozen preflight must not be replaced"),
    )
    monkeypatch.setattr(
        builder,
        "_run_command",
        lambda *_args, **_kwargs: pytest.fail("no build command may run after drift"),
    )
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=tmp_path / "candidate",
        targets=(builder.TARGET_STORY_LEGACY,),
        toolchain_report=report,
    )

    with pytest.raises(
        builder.StoryAxleRuntimeBuildError,
        match="changed or disappeared.*ctest.exe",
    ):
        builder.build_story_axle_runtime_candidate(request, source_root=RUNTIME)


def test_real_build_rejects_identityless_ready_report(tmp_path: Path) -> None:
    report = builder.NativeAxleToolchainReport(
        ready=True,
        platform="nt",
        source_root=RUNTIME,
        cmake_path=Path("cmake.exe"),
        cmake_version="3.30.5",
        ctest_path=Path("ctest.exe"),
        visual_studio_path=Path("Visual Studio"),
        cmake_generator="Visual Studio 17 2022",
        problems=(),
        probe_succeeded=True,
    )
    request = builder.StoryAxleRuntimeBuildRequest(
        output_directory=tmp_path / "candidate",
        targets=(builder.TARGET_STORY_LEGACY,),
        toolchain_report=report,
    )

    with pytest.raises(
        builder.StoryAxleRuntimeBuildError,
        match="no verified component identities",
    ):
        builder.build_story_axle_runtime_candidate(request, source_root=RUNTIME)
