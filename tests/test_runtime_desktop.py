import json
from dataclasses import replace
from pathlib import Path

import pytest

from allin1_sdk import runtime_desktop as native, workspace_desktop as desktop
from allin1_sdk.story_axle_runtime_builder import NativeAxleToolchainReport, NativeBuildCommandRecord, StoryAxleRuntimeBuildResult
from allin1_sdk.desktop_protocol import dispatch_operation


@pytest.fixture
def host(tmp_path, monkeypatch):
    source = tmp_path / "Runtime source"
    source.mkdir()
    (source / "CMakeLists.txt").write_text("project(test)")
    report = NativeAxleToolchainReport(ready=True, platform="windows", source_root=source,
        cmake_path=tmp_path / "cmake.exe", cmake_version="4.0", ctest_path=tmp_path / "ctest.exe",
        visual_studio_path=tmp_path / "VS", cmake_generator="Visual Studio 17 2022", problems=(), selection_fingerprint="a" * 64)
    monkeypatch.setattr(native.runtime, "_runtime_source_root", lambda: source)
    monkeypatch.setattr(native.runtime, "inspect_native_axle_toolchain", lambda **kwargs: report)
    return source, report


def reviewed(tmp_path, **extra):
    state = desktop.inspect({"module": "runtime"})
    request = {"module": "runtime", "action": "build", "destination": str(tmp_path / "candidate"),
               "targets": ["story-enhanced"], "expected_state_sha256": state["state_sha256"], **extra}
    review = desktop.review(request)
    return {**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


def test_runtime_review_and_build_dispatch_preserve_candidate_status(host, tmp_path, monkeypatch):
    seen = []
    def build(request, **kwargs):
        seen.append(request)
        request.output_directory.mkdir()
        manifest = request.output_directory / "build.json"
        manifest.write_text(json.dumps({"status": "candidate"}))
        return StoryAxleRuntimeBuildResult(root=request.output_directory, runtime_version="0.1.0", built_targets=request.targets,
            archives=(), checksums={"fixture.asi": "c" * 64}, files=("fixture.asi",), manifest=manifest,
            commands=(NativeBuildCommandRecord("Native CTest", ("ctest", "--output-on-failure"), 0, 1.0, "passed", ""),))
    monkeypatch.setattr(native.runtime, "build_story_axle_runtime_candidate", build)
    request = reviewed(tmp_path, targets=["story-legacy", "story-enhanced"], settings={"discovery_interval_ms": 500})
    assert not (tmp_path / "candidate").exists()
    _, result = dispatch_operation("apply_workspace_action", request)
    assert result["runtime_build"]["commands"][0]["command"] == ["ctest", "--output-on-failure"]
    assert seen[0].settings.discovery_interval_ms == 500
    assert seen[0].toolchain_report.selection_fingerprint == "a" * 64
    assert result["runtime_build"]["candidate_status"] == {"supported": False, "game_acceptance": "not-tested"}
    assert result["live_acceptance"] == "NOT TESTED" and result["candidate_only"]
    assert not result["game_write_performed"]


def test_runtime_source_change_invalidates_confirmation(host, tmp_path):
    source, _ = host
    request = reviewed(tmp_path)
    (source / "CMakeLists.txt").write_text("project(changed)")
    with pytest.raises(ValueError, match="changed"):
        desktop.apply(request)
    assert not (tmp_path / "candidate").exists()


def test_runtime_missing_dependencies_fail_without_fabricated_ready(host, tmp_path, monkeypatch):
    _, report = host
    monkeypatch.setattr(native.runtime, "inspect_native_axle_toolchain", lambda **kwargs: replace(report, ready=False, problems=("CMake not found",)))
    assert desktop.inspect({"module": "runtime"})["toolchain"]["ready"] is False
    with pytest.raises(ValueError, match="not ready.*CMake"):
        reviewed(tmp_path)
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("extra", [
    {"targets": []}, {"targets": ["story-enhanced", "story-enhanced"]}, {"targets": ["online"]},
    {"targets": "story-enhanced"}, {"settings": []}, {"settings": {"discovery_interval_ms": True}},
    {"settings": {"log_file": "../outside.log"}}, {"configuration_files": "file.json"},
    {"configuration_files": ["missing.json"]}, {"create_archives": "yes"}, {"build_id": "../unsafe"},
])
def test_invalid_runtime_builds_do_not_create_outputs(host, tmp_path, extra):
    with pytest.raises((ValueError, TypeError)):
        reviewed(tmp_path, **extra)
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("toolchain", [[], {"command": "arbitrary"}, {"cmake_path": "relative.exe"}, {"cmake_path": 123}])
def test_runtime_toolchain_choices_reject_unknown_or_relative_execution(host, toolchain):
    with pytest.raises(ValueError):
        desktop.inspect({"module": "runtime", "toolchain": toolchain})
