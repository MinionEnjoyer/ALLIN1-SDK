from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

import allin1_sdk.cli as sdk_cli
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.compiled_render import (
    CompiledRenderError,
    CompiledRenderResult,
    CompiledRenderSettings,
)
from allin1_sdk.native_assets import NativeAssetReport, NativeModelScene, _ModelGeometry


def _scene() -> NativeModelScene:
    return NativeModelScene(
        "vehicle.yft",
        (_ModelGeometry(
            vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 1.0)),
            triangles=((0, 1, 2),), lod="High", component="Body",
            material_index=1, material_name="vehicle_paint",
        ),),
    )


def _install_fake_decoder(monkeypatch, scene: NativeModelScene | None = None) -> None:
    def inspect_bytes(self, name, data, *, edition="Enhanced", truncated=False):
        del self, truncated
        return NativeAssetReport(
            name=name, suffix=Path(name).suffix.casefold(), format_name="RAGE fragment",
            size=len(data), sha256="a" * 64,
            metadata={"model_triangle_count": 1, "interpreted_edition": edition},
            warnings=(), model_scene=scene,
        )

    monkeypatch.setattr(sdk_cli.NativeAssetInspector, "inspect_bytes", inspect_bytes)


def test_render_native_model_cli_routes_all_settings_and_protected_roots(
    tmp_path: Path, monkeypatch,
) -> None:
    source_dir = tmp_path / "package"
    output_dir = tmp_path / "renders"
    gta = tmp_path / "game"
    source_dir.mkdir()
    output_dir.mkdir()
    gta.mkdir()
    source = source_dir / "vehicle.yft"
    source.write_bytes(b"RSC7 model")
    texture = source_dir / "vehicle.ytd"
    texture.write_bytes(b"RSC7 textures")
    output = output_dir / "vehicle.png"
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"fake")
    scene = _scene()
    _install_fake_decoder(monkeypatch, scene)
    received = {}

    def compile_model(model, destination, **kwargs):
        received.update(model=model, destination=destination, **kwargs)
        output.write_bytes(b"PNG")
        return CompiledRenderResult(
            output.resolve(), 2560, 1440, 1.25,
            {"engine": "cycles", "actual_device": "OPTIX GPU"},
        )

    monkeypatch.setattr(sdk_cli, "compile_vehicle_render", compile_model)
    result = CliRunner().invoke(sdk_cli.main, [
        "render-native-model", str(source), "--output", str(output),
        "--edition", "Legacy", "--gta-path", str(gta),
        "--blender", str(blender), "--yaw", "75", "--pitch", "12",
        "--lens-mm", "62", "--lod", "High", "--component", "Body",
        "--engine", "cycles", "--device", "gpu", "--quality", "maximum",
        "--width", "2560", "--height", "1440", "--samples", "512",
        "--light-rig", "dramatic", "--light-rotation", "40",
        "--light-strength", "1.6", "--background", "custom",
        "--background-color", "#203028", "--transparent",
        "--no-ground-plane", "--no-contact-shadows",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "render_native_model"
    assert payload["source"] == str(source.resolve())
    assert payload["output"] == str(output.resolve())
    assert payload["edition"] == "Legacy"
    assert payload["render_metadata"]["actual_device"] == "OPTIX GPU"
    assert payload["decode_metadata"]["model_triangle_count"] == 1
    settings = received["settings"]
    assert isinstance(settings, CompiledRenderSettings)
    assert (settings.width, settings.height, settings.samples) == (2560, 1440, 512)
    assert (settings.engine, settings.device, settings.quality) == (
        "cycles", "gpu", "maximum",
    )
    assert settings.light_rig == "dramatic"
    assert settings.background == "custom"
    assert settings.transparent is True
    assert settings.ground_plane is False
    assert settings.contact_shadows is False
    assert received["model"] is scene
    assert received["destination"] == output
    assert received["blender_executable"] == blender
    assert received["texture_dictionary"] == texture.resolve()
    assert received["edition"] == "Legacy"
    assert received["gta_path"] == gta.resolve()
    assert (received["yaw"], received["pitch"]) == (75.0, 12.0)
    assert received["lod"] == "High"
    assert received["component"] == "Body"
    assert received["protected_roots"] == (source_dir.resolve(), gta.resolve())


def test_render_native_model_cli_discovers_uppercase_texture_companion(
    tmp_path: Path, monkeypatch,
) -> None:
    source_dir = tmp_path / "package"
    output_dir = tmp_path / "renders"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "VEHICLE_HI.YFT"
    source.write_bytes(b"RSC7 model")
    texture = source_dir / "VEHICLE.YTD"
    texture.write_bytes(b"RSC7 textures")
    output = output_dir / "VEHICLE.PNG"
    _install_fake_decoder(monkeypatch, _scene())
    received: dict[str, object] = {}

    def compile_model(model, destination, **kwargs):
        received.update(model=model, destination=destination, **kwargs)
        output.write_bytes(b"PNG")
        return CompiledRenderResult(
            output.resolve(), 1920, 1080, 0.1, {"engine": "eevee"},
        )

    monkeypatch.setattr(sdk_cli, "compile_vehicle_render", compile_model)
    result = CliRunner().invoke(sdk_cli.main, [
        "render-native-model", str(source), "--output", str(output),
    ])

    assert result.exit_code == 0, result.output
    assert received["texture_dictionary"] == texture.resolve()


def test_render_native_model_agent_api_is_output_authoring_not_game_write(
    tmp_path: Path, monkeypatch,
) -> None:
    source_dir = tmp_path / "package"
    output_dir = tmp_path / "renders"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "vehicle.ydr"
    source.write_bytes(b"RSC7 drawable")
    output = output_dir / "vehicle.png"
    _install_fake_decoder(monkeypatch, _scene())

    def compile_model(_scene_value, destination, **_kwargs):
        resolved = Path(destination).resolve()
        resolved.write_bytes(b"PNG")
        return CompiledRenderResult(resolved, 1920, 1080, 0.5, {"engine": "eevee"})

    monkeypatch.setattr(sdk_cli, "compile_vehicle_render", compile_model)
    catalog = {item["name"]: item for item in command_catalog()}

    assert catalog["render-native-model"]["risk"] == "authoring_write"
    parameters = {item["name"]: item for item in catalog["render-native-model"]["parameters"]}
    assert parameters["source"]["kind"] == "argument"
    assert parameters["output"]["required"] is True
    response = execute_request({
        "id": "render-1", "action": "execute", "command": "render-native-model",
        "args": [str(source), "--output", str(output)],
    }, allow_game_writes=False, audit_path=tmp_path / "audit.jsonl")

    assert response["ok"] is True
    assert response["risk"] == "authoring_write"
    command_payload = json.loads(response["result"]["output"])
    assert command_payload["output"] == str(output.resolve())
    audit = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert audit["risk"] == "authoring_write"
    assert audit["allowed"] is True


def test_render_native_model_cli_rejects_undecodable_or_non_model_source(
    tmp_path: Path, monkeypatch,
) -> None:
    output_dir = tmp_path / "renders"
    output_dir.mkdir()
    source = tmp_path / "empty.yft"
    source.write_bytes(b"not decodable")
    _install_fake_decoder(monkeypatch, None)

    result = CliRunner().invoke(sdk_cli.main, [
        "render-native-model", str(source), "--output", str(output_dir / "x.png"),
    ])
    assert result.exit_code != 0
    assert "did not decode into renderable geometry" in result.output

    wrong = tmp_path / "vehicle.ytd"
    wrong.write_bytes(b"texture")
    result = CliRunner().invoke(sdk_cli.main, [
        "render-native-model", str(wrong), "--output", str(output_dir / "x.png"),
    ])
    assert result.exit_code != 0
    assert "must be one of" in result.output


def test_render_native_model_cli_preserves_structured_compiler_error(
    tmp_path: Path, monkeypatch,
) -> None:
    source_dir = tmp_path / "package"
    output_dir = tmp_path / "renders"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "vehicle.ydd"
    source.write_bytes(b"RSC7 dictionary")
    _install_fake_decoder(monkeypatch, _scene())

    def fail(*_args, **_kwargs):
        raise CompiledRenderError(
            "blender_not_found", "Blender is unavailable", {"searched": 3},
        )

    monkeypatch.setattr(sdk_cli, "compile_vehicle_render", fail)
    result = CliRunner().invoke(sdk_cli.main, [
        "render-native-model", str(source),
        "--output", str(output_dir / "vehicle.png"),
    ])

    assert result.exit_code != 0
    assert '"code": "blender_not_found"' in result.output
    assert '"searched": 3' in result.output
