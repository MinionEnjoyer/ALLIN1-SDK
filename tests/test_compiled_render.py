from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image

from allin1_sdk.compiled_render import (
    CompiledRenderError,
    CompiledRenderSettings,
    compile_vehicle_render,
    detect_blender,
    export_render_interchange,
)
from allin1_sdk.native_assets import NativeModelScene, _ModelGeometry


def _scene() -> NativeModelScene:
    return NativeModelScene(
        "Test vehicle",
        (
            _ModelGeometry(
                vertices=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 1.0)),
                triangles=((0, 1, 2),), lod="High", component="Body",
                material_index=1, material_name="vehicle_paint",
                texture_names=("body_diffuse",),
            ),
            _ModelGeometry(
                vertices=((0.0, 0.0, 0.4), (1.0, 0.0, 0.4), (0.0, 1.0, 0.4)),
                triangles=((0, 1, 2),), lod="High", component="Glass",
                material_index=2, material_name="vehicle_glass",
                texture_names=("window_glass",),
            ),
        ),
    )


def _fake_blender(path: Path) -> Path:
    path.write_bytes(b"fake blender")
    return path


class FakeBlenderRunner:
    def __init__(self, *, fail_render: bool = False) -> None:
        self.commands: list[tuple[list[str], Path, float]] = []
        self.fail_render = fail_render

    def __call__(
        self, command, *, cwd: Path, timeout: float,
        cancel_event: threading.Event | None,
    ) -> subprocess.CompletedProcess[str]:
        command = list(command)
        self.commands.append((command, cwd, timeout))
        assert cancel_event is None or not cancel_event.is_set()
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Blender 4.3.2\n", "")
        if self.fail_render:
            return subprocess.CompletedProcess(command, 7, "", "render exploded")
        assert command[1:3] == ["--background", "--factory-startup"]
        assert command[-2] == "--"
        config = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        settings = config["settings"]
        Image.new(
            "RGBA" if settings["transparent"] else "RGB",
            (settings["width"], settings["height"]),
            (20, 30, 25, 0 if settings["transparent"] else 255),
        ).save(config["output"], format="PNG")
        Path(config["result"]).write_text(
            json.dumps({
                "schema": 1, "blender_version": "4.3.2",
                "engine": "BLENDER_EEVEE_NEXT", "device": "GPU raster",
                "samples": settings["samples"], "elapsed_seconds": 0.2,
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "rendered", "")


def test_compiled_render_settings_validate_ui_payload() -> None:
    settings = CompiledRenderSettings(
        width=2560, height=1440, quality="MAXIMUM", samples=512,
        engine="Cycles", device="GPU", light_rig="Dramatic",
        light_rotation_deg=42, light_strength=1.5,
        background="Custom", background_color="#a0b1c2", transparent=True,
    )

    assert settings.quality == "maximum"
    assert settings.engine == "cycles"
    assert settings.device == "gpu"
    assert settings.light_rig == "dramatic"
    assert settings.background == "custom"
    assert settings.background_color == "#A0B1C2"
    assert settings.effective_samples == 512

    assert CompiledRenderSettings(background="custom_color").background == "custom"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("quality", "cinema"),
        ("engine", "workbench"),
        ("device", "cuda"),
        ("light_rig", "none"),
        ("background", "sky"),
        ("background_color", "green"),
        ("samples", 0),
        ("width", 100),
    ),
)
def test_compiled_render_settings_reject_invalid_values(field: str, value) -> None:
    with pytest.raises(ValueError):
        CompiledRenderSettings(**{field: value})


def test_detect_blender_validates_explicit_executable(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")
    runner = FakeBlenderRunner()

    installation = detect_blender(executable, process_runner=runner)

    assert installation is not None
    assert installation.executable == executable.resolve()
    assert installation.version == "4.3.2"
    assert installation.source == "explicit"
    assert runner.commands[0][0] == [str(executable.resolve()), "--version"]


def test_detect_blender_returns_none_for_missing_explicit_path(tmp_path: Path) -> None:
    runner = FakeBlenderRunner()

    assert detect_blender(tmp_path / "missing.exe", process_runner=runner) is None
    assert runner.commands == []


def test_detect_blender_uses_configured_environment_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _fake_blender(tmp_path / "configured-blender.exe")
    monkeypatch.setenv("BLENDER_EXECUTABLE", str(executable))
    runner = FakeBlenderRunner()

    installation = detect_blender(process_runner=runner)

    assert installation is not None
    assert installation.executable == executable.resolve()
    assert installation.source == "environment"


def test_export_interchange_preserves_material_assignments(tmp_path: Path) -> None:
    exported = export_render_interchange(_scene(), tmp_path, lod="High")

    obj = exported.obj_path.read_text(encoding="utf-8")
    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    assert obj.count("usemtl ") == 2
    assert "usemtl mat_0000" in obj
    assert "usemtl mat_0001" in obj
    assert obj.count("\nf ") == 2
    assert exported.vertex_count == 6
    assert exported.triangle_count == 2
    assert exported.material_count == 2
    assert {record["semantic"] for record in manifest["materials"]} == {"paint", "glass"}
    assert manifest["counts"]["skipped_triangles"] == 0
    assert len(exported.sha256) == 64


def test_export_interchange_preserves_uvs_and_typed_texture_bindings(
    tmp_path: Path,
) -> None:
    texture = tmp_path / "body_diffuse.png"
    Image.new("RGBA", (4, 4), (220, 35, 24, 255)).save(texture)
    geometry = _ModelGeometry(
        vertices=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 1.0)),
        triangles=((0, 1, 2),), lod="High", component="Body",
        material_index=1, material_name="vehicle_paint",
        texture_names=("body_diffuse", "body_normal"),
        texcoords=((0.0, 0.25), (1.0, 0.25), (0.0, 0.75)),
        texture_parameters=(
            ("DiffuseSampler", "body_diffuse"),
            ("BumpSampler", "body_normal"),
        ),
    )

    exported = export_render_interchange(
        NativeModelScene("Textured vehicle", (geometry,)), tmp_path,
        texture_assets={"body_diffuse": texture},
    )

    obj = exported.obj_path.read_text(encoding="utf-8")
    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    assert "vt 0 0.75" in obj
    assert "f 1/1 2/2 3/3" in obj
    bindings = manifest["materials"][0]["texture_bindings"]
    assert bindings[0] == {
        "slot": "DiffuseSampler", "name": "body_diffuse",
        "role": "diffuse", "path": "body_diffuse.png",
    }
    assert bindings[1]["role"] == "normal"
    assert bindings[1]["path"] is None
    assert exported.texture_count == 1
    assert exported.textured_material_count == 1
    assert exported.unresolved_texture_names == ("body_normal",)


def test_export_interchange_ignores_invalid_optional_texture_assets_and_empty_slots(
    tmp_path: Path,
) -> None:
    texture = tmp_path / "BODY_DIFFUSE.PNG"
    Image.new("RGBA", (2, 2), (30, 80, 45, 255)).save(texture)
    empty = tmp_path / "EMPTY.PNG"
    empty.touch()
    geometry = _ModelGeometry(
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        triangles=((0, 1, 2),), lod="HIGH", component="BODY",
        texture_names=("BODY_DIFFUSE", "zero_texture", ""),
        texture_parameters=(
            ("DiffuseSampler", "BODY_DIFFUSE"),
            ("DetailSampler", "zero_texture"),
            ("OptionalSampler", ""),
        ),
    )

    exported = export_render_interchange(
        NativeModelScene("Optional textures", (geometry,)), tmp_path,
        texture_assets={
            "body_diffuse": texture,
            "zero_texture": empty,
            "removed_unused": tmp_path / "REMOVED.PNG",
        },
    )

    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))
    bindings = manifest["materials"][0]["texture_bindings"]
    assert [item["name"] for item in bindings] == ["BODY_DIFFUSE", "zero_texture"]
    assert bindings[0]["path"] == "BODY_DIFFUSE.PNG"
    assert bindings[1]["path"] is None
    assert exported.texture_count == 1
    assert exported.unresolved_texture_names == ("zero_texture",)


def test_compile_vehicle_render_uses_isolated_headless_pipeline(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")
    output = tmp_path / "vehicle.png"
    runner = FakeBlenderRunner()
    progress = []
    settings = CompiledRenderSettings(
        width=640, height=360, quality="preview", light_rig="neutral",
        background="transparent", transparent=True,
    )

    result = compile_vehicle_render(
        _scene(), output, settings=settings, blender_executable=executable,
        yaw=25.0, pitch=12.0, lod="High", progress=progress.append,
        process_runner=runner,
    )

    assert result.output_path == output.resolve()
    assert result.width == 640
    assert result.height == 360
    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == (640, 360)
        assert image.mode == "RGBA"
    assert result.metadata["backend"] == "Blender headless"
    assert result.metadata["actual_device"] == "GPU raster"
    assert result.metadata["triangle_count"] == 2
    assert result.metadata["camera"]["yaw"] == 25.0
    assert progress[0].stage == "validate"
    assert progress[-1].stage == "complete"
    render_command, render_cwd, _timeout = runner.commands[-1]
    assert render_command[1:3] == ["--background", "--factory-startup"]
    assert "--disable-autoexec" in render_command
    assert render_command[render_command.index("--python-exit-code") + 1] == "9"
    assert render_cwd.name.startswith("allin1-compiled-render-")
    assert not render_cwd.exists()


def test_compile_vehicle_render_refuses_protected_destination(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")
    protected = tmp_path / "game"
    protected.mkdir()

    with pytest.raises(CompiledRenderError) as caught:
        compile_vehicle_render(
            _scene(), protected / "vehicle.png", blender_executable=executable,
            protected_roots=(protected,), process_runner=FakeBlenderRunner(),
        )

    assert caught.value.code == "protected_output"
    assert not (protected / "vehicle.png").exists()


def test_compile_vehicle_render_reports_absent_blender(tmp_path: Path) -> None:
    with pytest.raises(CompiledRenderError) as caught:
        compile_vehicle_render(
            _scene(), tmp_path / "vehicle.png",
            blender_executable=tmp_path / "missing.exe",
            process_runner=FakeBlenderRunner(),
        )

    assert caught.value.as_dict()["code"] == "blender_not_found"
    assert "Install Blender" in caught.value.message


def test_compile_vehicle_render_surfaces_structured_blender_failure(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")

    with pytest.raises(CompiledRenderError) as caught:
        compile_vehicle_render(
            _scene(), tmp_path / "vehicle.png", blender_executable=executable,
            process_runner=FakeBlenderRunner(fail_render=True),
        )

    assert caught.value.code == "blender_render_failed"
    assert caught.value.details["returncode"] == 7
    assert "render exploded" in caught.value.details["stderr"]


def test_compile_vehicle_render_honors_pre_cancel(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")
    cancellation = threading.Event()
    cancellation.set()

    with pytest.raises(CompiledRenderError) as caught:
        compile_vehicle_render(
            _scene(), tmp_path / "vehicle.png", blender_executable=executable,
            cancel_event=cancellation, process_runner=FakeBlenderRunner(),
        )

    assert caught.value.code == "render_cancelled"


def test_default_runner_uses_argv_without_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allin1_sdk.compiled_render import _default_process_runner

    captured = {}

    class Process:
        returncode = 0

        def poll(self):
            return 0

        def communicate(self):
            return "ok", ""

        def kill(self):
            raise AssertionError("completed process should not be killed")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    malicious = "model; Remove-Item C:\\game"

    completed = _default_process_runner(
        ["blender.exe", "--", malicious], cwd=tmp_path, timeout=2,
        cancel_event=None,
    )

    assert completed.returncode == 0
    assert captured["command"] == ["blender.exe", "--", malicious]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL


def test_package_controlled_name_is_data_not_executable_code(tmp_path: Path) -> None:
    executable = _fake_blender(tmp_path / "blender.exe")
    output = tmp_path / "vehicle.png"
    runner = FakeBlenderRunner()
    marker = "'); __import__('os').system('bad') #"
    source = _scene()
    malicious_scene = NativeModelScene(marker, source.geometries)

    compile_vehicle_render(
        malicious_scene, output, blender_executable=executable,
        settings=CompiledRenderSettings(width=320, height=256, quality="preview"),
        process_runner=runner,
    )

    render_command = runner.commands[-1][0]
    assert marker not in " ".join(render_command)
    assert output.is_file()


def test_embedded_blender_script_has_guarded_modern_and_legacy_imports() -> None:
    from allin1_sdk.compiled_render import _BLENDER_COMPILE_SCRIPT

    compile(_BLENDER_COMPILE_SCRIPT, "compile_scene.py", "exec")
    assert "bpy.ops.wm.obj_import" in _BLENDER_COMPILE_SCRIPT
    assert "bpy.ops.import_scene.obj" in _BLENDER_COMPILE_SCRIPT
    assert 'scene.render.engine = "BLENDER_EEVEE_NEXT"' in _BLENDER_COMPILE_SCRIPT
    assert 'scene.render.engine = "BLENDER_EEVEE"' in _BLENDER_COMPILE_SCRIPT
    assert "GPU rendering was requested" in _BLENDER_COMPILE_SCRIPT
    assert "NEGATIVE_Y_FORWARD" in _BLENDER_COMPILE_SCRIPT
    assert "ShaderNodeNormalMap" in _BLENDER_COMPILE_SCRIPT
    assert "Specular IOR Level" in _BLENDER_COMPILE_SCRIPT
