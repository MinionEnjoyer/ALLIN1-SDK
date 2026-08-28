import json
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.map_workbench import MapWorkbenchFrame, looks_like_map_project


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


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk is unavailable")
    root.withdraw()
    yield root
    root.destroy()


def test_map_workbench_edits_validated_topology_and_saves_atomically(
    tmp_path, tk_root,
):
    descriptor = _descriptor(tmp_path)
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    assert frame.open_descriptor(descriptor)
    assert len(frame.topology_tree.get_children("root:levels")) == 1
    assert len(frame.topology_tree.get_children("root:portals")) == 1
    assert len(frame.topology_tree.get_children("root:garages")) == 1
    assert len(frame.topology_tree.get_children("garages:0")) == 1
    assert frame.topology_tree.exists("project:metadata")
    assert frame.topology_tree.exists("project:streaming")

    frame.topology_tree.selection_set("project:streaming")
    frame._select_topology()
    streaming = json.loads(frame.detail.get("1.0", "end-1c"))
    streaming["activation_radius"] = 275.0
    frame.detail.delete("1.0", "end")
    frame.detail.insert("1.0", json.dumps(streaming))
    frame._apply_selected_json()
    assert frame.project_data["streaming"]["activation_radius"] == 275.0

    frame.topology_tree.selection_set("levels:0")
    frame._select_topology()
    level = json.loads(frame.detail.get("1.0", "end-1c"))
    level["name"] = "Edited Garage Level"
    frame.detail.delete("1.0", "end")
    frame.detail.insert("1.0", json.dumps(level))
    frame._apply_selected_json()
    assert frame.dirty
    assert not frame.confirm_navigation()
    assert frame.save_descriptor()
    assert not frame.dirty
    assert json.loads(descriptor.read_text(encoding="utf-8"))["levels"][0]["name"] == (
        "Edited Garage Level"
    )
    frame.destroy()


def test_create_descriptor_keeps_the_already_selected_map_source(
    tmp_path, monkeypatch, tk_root,
):
    source = tmp_path / "map-source"
    source.mkdir()
    (source / "fixture.ymap").write_bytes(b"RSC7" + b"\0" * 64)
    destination = tmp_path / "descriptor" / "allin1.map.json"
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    assert frame.open_source(source)
    monkeypatch.setattr(
        "allin1_sdk.map_workbench.filedialog.asksaveasfilename",
        lambda **_kwargs: str(destination),
    )
    frame._create_template()
    assert frame.source == source.resolve()
    assert frame.descriptor == destination.resolve()
    frame.destroy()


def test_opening_a_new_map_source_clears_the_previous_descriptor_context(
    tmp_path, tk_root,
):
    descriptor = _descriptor(tmp_path)
    source = tmp_path / "replacement-map-source"
    source.mkdir()
    (source / "replacement.ymap").write_bytes(b"RSC7" + b"\0" * 64)
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    assert frame.open_descriptor(descriptor)
    assert frame.project_data is not None
    assert frame.descriptor == descriptor.resolve()

    assert frame.open_source(source)
    assert frame.source == source.resolve()
    assert frame.descriptor is None
    assert frame.project is None
    assert frame.project_data is None
    assert frame.descriptor_value.get() == "No descriptor selected"
    assert str(frame.build_button.cget("state")) == "disabled"
    frame.destroy()


def test_map_workbench_enables_build_only_for_ready_descriptor_and_source(
    tmp_path, tk_root,
):
    descriptor_root = tmp_path / "descriptor"
    descriptor_root.mkdir()
    descriptor = _descriptor(descriptor_root)
    source = _map_source(tmp_path)
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)

    assert frame.open_descriptor(descriptor)
    assert str(frame.build_button.cget("state")) == "disabled"
    assert "build unavailable" in frame.status.get().casefold()

    assert frame.open_source(source, descriptor=descriptor)
    assert frame._source_ready
    assert frame._descriptor_ready
    assert str(frame.build_button.cget("state")) == "normal"
    frame.destroy()


def test_map_workbench_surfaces_invalid_source_and_blocks_build(
    tmp_path, monkeypatch, tk_root,
):
    descriptor = _descriptor(tmp_path)
    source = _map_source(tmp_path, placement=False)
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)

    assert frame.open_source(source, descriptor=descriptor)
    assert not frame._source_ready
    assert str(frame.build_button.cget("state")) == "disabled"
    assert "source inspection reported 1 error" in frame.status.get().casefold()
    assert "missing_ymap" in frame.detail.get("1.0", "end-1c")

    prompted = False

    def choose_output(**_kwargs):
        nonlocal prompted
        prompted = True
        return str(tmp_path / "output")

    monkeypatch.setattr(
        "allin1_sdk.map_workbench.filedialog.askdirectory", choose_output,
    )
    frame._build_package()
    assert not prompted
    assert "not package-ready" in frame.status.get().casefold()
    frame.destroy()


def test_map_workbench_inspection_failure_clears_stale_build_readiness(
    tmp_path, monkeypatch, tk_root,
):
    descriptor = _descriptor(tmp_path)
    ready_source = _map_source(tmp_path, name="ready-source")
    failed_source = _map_source(tmp_path, name="failed-source")
    frame = MapWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    assert frame.open_source(ready_source, descriptor=descriptor)
    assert str(frame.build_button.cget("state")) == "normal"

    monkeypatch.setattr(
        "allin1_sdk.map_workbench.AddonPackageInspector.inspect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("native RPF inspection failed")
        ),
    )
    assert not frame.open_source(failed_source)
    assert frame.source == failed_source.resolve()
    assert frame.descriptor is None
    assert not frame._source_ready
    assert str(frame.build_button.cget("state")) == "disabled"
    assert "inspection failed" in frame.status.get().casefold()
    assert "native RPF inspection failed" in frame.detail.get("1.0", "end-1c")
    frame.destroy()


def test_map_help_and_unified_workbench_surface_are_documented():
    root = Path(__file__).resolve().parents[1]
    help_source = (root / "src/allin1_sdk/help_center.py").read_text(encoding="utf-8")
    workbench_source = (root / "src/allin1_sdk/workbench.py").read_text(encoding="utf-8")
    assert '"map-workbench", "Authoring", "Map Workbench"' in help_source
    assert 'self.tabs.add(map_page, text="Maps")' in workbench_source
    assert "MapWorkbenchFrame(" in workbench_source


def test_runtime_maps_descriptor_is_recognized_as_a_map_project(tmp_path):
    descriptor = tmp_path / "maps.json"
    descriptor.write_text(json.dumps(_map_payload()), encoding="utf-8")
    assert looks_like_map_project(descriptor)
