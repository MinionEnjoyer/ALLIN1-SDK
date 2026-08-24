from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.model_materials import (
    MaterialAuthoringWorkspace,
    inspect_model_xml,
)


MODEL_XML = """<?xml version="1.0" encoding="utf-8"?>
<Drawable>
 <Name>fixture_model</Name>
 <ShaderGroup><Shaders>
  <Item><Name>vehicle_paint</Name><Parameters>
   <Item name="DiffuseSampler" type="Texture"><Name>fixture_d</Name></Item>
   <Item name="BumpSampler" type="Texture"><Name>fixture_n</Name></Item>
  </Parameters></Item>
  <Item><Name>vehicle_glass</Name><Parameters>
   <Item name="DiffuseSampler" type="Texture"><Name>fixture_glass</Name></Item>
  </Parameters></Item>
 </Shaders></ShaderGroup>
 <DrawableModelsHigh><Item><Name>Body</Name><Geometries>
  <Item><ShaderIndex value="0"/>
   <VertexBuffer><Layout><Position/><TexCoord0/></Layout><Data>
0 0 0 0 0
1 0 0 1 0
0 1 0 0 1
   </Data></VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer>
  </Item>
  <Item><ShaderIndex value="1"/>
   <VertexBuffer><Layout><Position/><TexCoord0/></Layout><Data>
0 0 1 0 0
1 0 1 1 0
0 1 1 0 1
   </Data></VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer>
  </Item>
 </Geometries></Item></DrawableModelsHigh>
</Drawable>
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "material-workspace"
    original = root / "original"
    edit = root / "edit"
    original.mkdir(parents=True)
    (edit / "assets").mkdir(parents=True)
    source = b"RSC8" + b"\0" * 32
    (original / "fixture.ydr").write_bytes(source)
    xml = (edit / "fixture.ydr.xml")
    xml.write_text(MODEL_XML, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "operation": "native_asset_workspace",
        "edition": "Enhanced",
        "source": {
            "name": "fixture.ydr", "suffix": ".ydr", "size": len(source),
            "sha256": _sha(source), "snapshot": "original/fixture.ydr",
        },
        "xml": {
            "path": "edit/fixture.ydr.xml", "size": xml.stat().st_size,
            "base_sha256": _sha(xml.read_bytes()),
        },
        "dependencies": [],
    }
    (root / "native-workspace.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return root


def test_model_material_project_preserves_shader_usage_and_texture_roles(tmp_path):
    xml = tmp_path / "fixture.ydr.xml"
    xml.write_text(MODEL_XML, encoding="utf-8")

    project = inspect_model_xml(
        xml, source_name="fixture.ydr", edition="Enhanced",
    )

    assert [item.shader for item in project.materials] == [
        "vehicle_paint", "vehicle_glass",
    ]
    assert [item.geometry_indices for item in project.materials] == [(0,), (1,)]
    assert [item.material_document_index for item in project.geometries] == [0, 1]
    assert project.materials[0].textures[0].role == "color"
    assert project.materials[0].textures[1].role == "normal"
    assert project.scene is not None
    assert project.to_dict()["summary"]["texture_bindings"] == 3


def test_material_workspace_edits_existing_bindings_with_revision_and_undo(tmp_path):
    workspace = MaterialAuthoringWorkspace.initialize(_workspace(tmp_path))
    original = workspace.xml_path.read_bytes()

    edited = workspace.set_material(
        0, expected_revision=0, shader_name="vehicle_paint2",
        textures={"DiffuseSampler": "fixture_respray_d"},
    )

    assert edited.revision == 1
    assert edited.project.materials[0].shader == "vehicle_paint2"
    assert edited.project.materials[0].textures[0].texture == "fixture_respray_d"
    assert edited.history.is_dir()
    assert (edited.history / "validation.json").is_file()
    with pytest.raises(ValueError, match="revision conflict"):
        workspace.set_material(
            0, expected_revision=0, shader_name="stale_edit",
        )

    undone = workspace.undo(expected_revision=1)

    assert undone.revision == 2
    assert workspace.xml_path.read_bytes() == original
    assert workspace.inspect().materials[0].shader == "vehicle_paint"
    assert undone.history.name.endswith(".undone")


def test_material_workspace_reassigns_geometry_only_inside_local_catalog(tmp_path):
    workspace = MaterialAuthoringWorkspace.initialize(_workspace(tmp_path))

    result = workspace.set_geometry_material(0, 1, expected_revision=0)

    assert result.project.geometries[0].material_index == 1
    assert result.project.geometries[0].material_name == "vehicle_glass"
    with pytest.raises(ValueError, match="local shader group"):
        workspace.set_geometry_material(1, 8, expected_revision=1)


def test_material_workspace_rejects_missing_slots_and_external_xml_drift(tmp_path):
    workspace = MaterialAuthoringWorkspace.initialize(_workspace(tmp_path))
    before = workspace.xml_path.read_bytes()

    with pytest.raises(ValueError, match="resolve exactly once"):
        workspace.set_material(
            0, expected_revision=0, textures={"InventedSampler": "unsafe"},
        )
    assert workspace.xml_path.read_bytes() == before

    workspace.xml_path.write_bytes(before + b"\n")
    with pytest.raises(ValueError, match="changed outside"):
        workspace.inspect()


def test_material_workspace_rejects_modified_native_snapshot(tmp_path):
    root = _workspace(tmp_path)
    (root / "original" / "fixture.ydr").write_bytes(b"changed")

    with pytest.raises(ValueError, match="source snapshot was modified"):
        MaterialAuthoringWorkspace.initialize(root)


def test_material_workspace_cli_inspection_edit_assignment_and_undo(tmp_path):
    workspace = MaterialAuthoringWorkspace.initialize(_workspace(tmp_path))
    runner = CliRunner()

    inspected = runner.invoke(main, [
        "inspect-material-workspace", str(workspace.root),
    ])
    assert inspected.exit_code == 0
    assert json.loads(inspected.output)["revision"] == 0

    edited = runner.invoke(main, [
        "set-material-binding", str(workspace.root), "0",
        "--texture", "DiffuseSampler=cli_fixture_d",
        "--expected-revision", "0", "--acknowledge-edit",
    ])
    assert edited.exit_code == 0, edited.output
    assert json.loads(edited.output)["revision"] == 1

    assigned = runner.invoke(main, [
        "set-geometry-material", str(workspace.root), "0", "1",
        "--expected-revision", "1", "--acknowledge-edit",
    ])
    assert assigned.exit_code == 0, assigned.output
    assert json.loads(assigned.output)["revision"] == 2

    undone = runner.invoke(main, [
        "undo-material-edit", str(workspace.root),
        "--expected-revision", "2", "--acknowledge-edit",
    ])
    assert undone.exit_code == 0, undone.output
    assert json.loads(undone.output)["revision"] == 3
