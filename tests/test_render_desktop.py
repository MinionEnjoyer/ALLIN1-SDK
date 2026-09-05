import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from allin1_sdk import render_desktop as render, workspace_desktop as desktop
from allin1_sdk.desktop_protocol import dispatch_operation
from allin1_sdk.compiled_render import BlenderInstallation, CompiledRenderResult


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    model = source / "fixture.ydr"
    model.write_bytes(b"owned model fixture")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"unit test executable identity")
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(render.compiled_render, "detect_blender", lambda executable: BlenderInstallation(blender, "4.5.13", "fixture"))
    monkeypatch.setattr(render, "NativeAssetInspector", lambda *args: SimpleNamespace(inspect_bytes=lambda *a, **k: SimpleNamespace(model_scene=object(), warnings=())))
    def compile(scene, output, settings, **kwargs):
        Image.new("RGB", (settings.width, settings.height), "green").save(output)
        return CompiledRenderResult(output_path=output, width=settings.width, height=settings.height, elapsed_seconds=0.2,
            metadata={"fidelity": "Unit fixture, not native render acceptance", "tuple": (1, 2), "settings": settings.engine})
    monkeypatch.setattr(render.compiled_render, "compile_vehicle_render", compile)
    return {"module": "render", "source": str(model), "edition": "Enhanced", "render": True, "blender_executable": str(blender), "settings": {"width": 256, "height": 256}}


def export_request(payload, tmp_path):
    _, frame = dispatch_operation("inspect_authoring_workspace", payload)
    request = {"module": "render", "action": "export", "render_id": frame["render_id"], "expected_state_sha256": frame["state_sha256"], "destination": str(tmp_path / "export.png")}
    _, review = dispatch_operation("review_workspace_action", request)
    return frame, {**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


def test_render_decode_job_review_and_exclusive_export(renderer, tmp_path):
    original = Path(renderer["source"]).read_bytes()
    frame, request = export_request(renderer, tmp_path)
    assert frame["read_only"] and frame["game_acceptance"] == "NOT TESTED"
    assert frame["render_record"]["metadata"]["tuple"] == [1, 2]
    assert not (tmp_path / "export.png").exists()
    _, saved = dispatch_operation("apply_workspace_action", request)
    assert saved["output_sha256"] == frame["render_record"]["output_sha256"]
    assert json.loads(Path(saved["receipt"]).read_text())["identity"]["source_sha256"]
    assert Path(renderer["source"]).read_bytes() == original
    with pytest.raises(ValueError, match="new destination"):
        desktop.apply(request)


@pytest.mark.parametrize("target", ["png", "json"])
def test_tampered_render_or_receipt_cannot_export(renderer, tmp_path, target):
    frame, request = export_request(renderer, tmp_path)
    cached = tmp_path / "cache" / "compiled-renders" / f"{frame['render_id']}.{target}"
    cached.write_bytes(b"changed" if target == "png" else b'{"schema_version":1}')
    with pytest.raises(ValueError, match="changed"):
        desktop.apply(request)
    assert not (tmp_path / "export.png").exists()


def test_mid_render_source_change_discards_frame(renderer, tmp_path, monkeypatch):
    original = render.compiled_render.compile_vehicle_render
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        Path(renderer["source"]).write_bytes(b"new model")
        return result
    monkeypatch.setattr(render.compiled_render, "compile_vehicle_render", changed)
    with pytest.raises(ValueError, match="changed while rendering"):
        desktop.inspect(renderer)
    assert not list((tmp_path / "cache").rglob("*.png"))


def test_missing_blender_is_not_a_ready_render(renderer, monkeypatch):
    monkeypatch.setattr(render.compiled_render, "detect_blender", lambda *a: None)
    assert desktop.inspect({"module": "render"})["render_ready"] is False
    with pytest.raises(ValueError, match="Blender was not found"):
        desktop.inspect(renderer)


@pytest.mark.parametrize("extra", [
    {"render": "yes"}, {"source": "../outside.ydr"}, {"edition": "Online"},
    {"settings": []}, {"settings": {"width": True}}, {"settings": {"samples": 0}},
    {"settings": {"transparent": "yes"}}, {"camera": []}, {"camera": {"command": "run"}},
    {"camera": {"pitch": 100}}, {"camera": {"yaw": float("inf")}}, {"camera": {"lod": 4}},
    {"camera": {"component": "x" * 513}}, {"texture_dictionary": "relative.ytd"},
])
def test_invalid_render_input_fails_before_cache_writes(renderer, tmp_path, extra):
    with pytest.raises((ValueError, TypeError)):
        desktop.inspect({**renderer, **extra})
    assert not (tmp_path / "cache").exists()


def test_missing_decode_and_wrong_executable_fail_before_cache(renderer, tmp_path, monkeypatch):
    wrong = tmp_path / "unknown.exe"
    wrong.write_bytes(b"not Blender")
    with pytest.raises(ValueError, match="Blender executable"):
        desktop.inspect({**renderer, "blender_executable": str(wrong)})
    monkeypatch.setattr(render, "NativeAssetInspector", lambda *args: SimpleNamespace(inspect_bytes=lambda *a, **k: SimpleNamespace(model_scene=None, warnings=("decode failed",))))
    with pytest.raises(ValueError, match="decode failed"):
        desktop.inspect(renderer)
    assert not (tmp_path / "cache").exists()


def test_export_sidecar_collision_preserves_canary(renderer, tmp_path):
    _, request = export_request(renderer, tmp_path)
    canary = tmp_path / "outside-canary"
    canary.write_bytes(b"preserve")
    (tmp_path / "export.png.render.json").hardlink_to(canary)
    with pytest.raises(ValueError):
        desktop.apply(request)
    assert canary.read_bytes() == b"preserve"
    assert not (tmp_path / "export.png").exists()


def test_frozen_renderer_binds_sidecar_without_loose_source(renderer, tmp_path, monkeypatch):
    frozen = tmp_path / "allin1-sdk-sidecar.exe"
    frozen.write_bytes(b"owned frozen executable identity")
    monkeypatch.setattr(render.sys, "frozen", True, raising=False)
    monkeypatch.setattr(render.sys, "executable", str(frozen))
    monkeypatch.setattr(render.compiled_render, "__file__", str(tmp_path / "nonexistent.py"))
    result = desktop.inspect(renderer)
    identity = result["render_record"]["identity"]
    assert identity["renderer_identity_kind"] == "frozen-sidecar"
    assert identity["renderer_sha256"] == render.file_hash(frozen)
