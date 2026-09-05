import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.map_detection import looks_like_map_project


def _map_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "fixture.map",
        "package_id": "fixture.map",
        "name": "Fixture Map",
        "version": "1.0.0",
        "editions": ["legacy", "enhanced"],
        "streaming": {
            "pack_name": "fixture_map", "content_group": None,
            "ipls": ["fixture_map"], "activation_radius": 250.0,
            "release_radius": 450.0, "keep_resident": False,
        },
        "levels": [{
            "id": "garage.level", "name": "Garage Level",
            "center": {"x": 1.0, "y": 2.0, "z": 3.0, "heading": 90.0},
            "ipls": [],
        }],
        "portals": [{
            "id": "garage.entry", "name": "Garage Entry", "mode": "both",
            "from": {
                "level": "world",
                "position": {"x": 10.0, "y": 20.0, "z": 5.0, "heading": 0.0},
            },
            "to": {
                "level": "garage.level",
                "position": {"x": 1.0, "y": 2.0, "z": 3.0, "heading": 180.0},
            },
            "radius": 3.0, "one_way": False,
        }],
        "garages": [{
            "id": "garage.main", "name": "Main Garage",
            "level_id": "garage.level", "entrance_portal_id": "garage.entry",
            "capacity": 8, "vehicle_types": ["land"],
            "slots": [{
                "id": "slot.01",
                "position": {"x": 2.0, "y": 3.0, "z": 3.0, "heading": 180.0},
                "vehicle_types": ["land"],
            }],
            "rules": {
                "allow_store": True, "allow_retrieve": True,
                "save_policy": "story_save_only",
            },
        }],
    }


def _descriptor(tmp_path: Path) -> Path:
    path = tmp_path / "allin1.map.json"
    path.write_text(json.dumps(_map_payload()), encoding="utf-8")
    return path


def _map_source(tmp_path: Path, *, name: str = "map-source", placement: bool = True) -> Path:
    source = tmp_path / name
    source.mkdir()
    if placement:
        (source / "fixture.ymap").write_bytes(b"RSC7" + b"\0" * 64)
    (source / "fixture.ybn").write_bytes(b"RSC7" + b"\0" * 64)
    return source


def test_map_cli_validates_and_inspects_structured_sources(tmp_path):
    descriptor = _descriptor(tmp_path)
    result = CliRunner().invoke(main, ["validate-map-project", str(descriptor)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "validate_map_project"
    assert payload["summary"] == {
        "levels": 1, "portals": 1, "garages": 1, "garage_slots": 1,
    }

    package = tmp_path / "map-source"
    package.mkdir()
    (package / "fixture.ymap").write_bytes(b"RSC7" + b"\0" * 64)
    result = CliRunner().invoke(main, ["inspect-map-project", str(package)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["summary"]["assets"] == 1
    assert report["assets"][0]["role"] == "placement"


def test_detect_map_placements_uses_descriptor_and_requires_explicit_gta_path(
    tmp_path, monkeypatch,
):
    descriptor = _descriptor(tmp_path)
    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    calls = []

    def detect(_resolver, pack_name, **kwargs):
        calls.append((pack_name, kwargs))
        return SimpleNamespace(
            valid=True,
            to_dict=lambda: {
                "schema_version": 1,
                "operation": "detect_map_placements",
                "pack_name": pack_name,
                "summary": {"valid": True},
            },
        )

    monkeypatch.setattr(
        "allin1_sdk.cli.MapProjectResolver.detect_installed_dlc", detect,
    )
    missing_game = CliRunner().invoke(main, [
        "detect-map-placements", "--descriptor", str(descriptor),
    ])
    assert missing_game.exit_code == 2
    assert "--gta-path" in missing_game.output

    result = CliRunner().invoke(main, [
        "detect-map-placements", "--descriptor", str(descriptor),
        "--gta-path", str(game), "--expected-ipl", "fixture_extra",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "detect_map_placements"
    assert calls == [(
        "fixture_map",
        {
            "project_root": Path(__file__).resolve().parents[1],
            "gta_path": game,
            "expected_ipls": ["fixture_map", "fixture_extra"],
        },
    )]


def test_build_map_cli_uses_explicit_source_descriptor_output_and_edition(
    tmp_path, monkeypatch,
):
    descriptor = _descriptor(tmp_path)
    source = tmp_path / "source.rpf"
    source.write_bytes(b"RPF8fixture")
    output = tmp_path / "map-output"
    calls = []

    class Builder:
        def __init__(self, project_root, gta_path=None):
            calls.append(("init", Path(project_root), gta_path))

        def build(self, selected_source, selected_descriptor, destination, *, edition):
            calls.append((
                "build", selected_source, selected_descriptor, destination, edition,
            ))
            return SimpleNamespace(to_dict=lambda: {
                "root": str(destination), "edition": edition,
            })

    monkeypatch.setattr("allin1_sdk.cli.MapAddonPackageBuilder", Builder)
    result = CliRunner().invoke(main, [
        "build-map-package", str(source), str(descriptor), str(output),
        "--edition", "enhanced",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["edition"] == "enhanced"
    assert calls[-1] == ("build", source, descriptor, output, "enhanced")


def test_runtime_maps_descriptor_is_recognized_as_a_map_project(tmp_path):
    descriptor = tmp_path / "maps.json"
    descriptor.write_text(json.dumps(_map_payload()), encoding="utf-8")
    assert looks_like_map_project(descriptor)
