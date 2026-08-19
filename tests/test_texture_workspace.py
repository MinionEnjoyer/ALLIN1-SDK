from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from PIL import Image
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.texture_workspace import (
    TextureDictionaryWorkspace,
    inspect_dds,
)


def _dds(path: Path, size=(16, 8), color=(10, 20, 30, 255)) -> Path:
    Image.new("RGBA", size, color).save(path, format="DDS")
    return path


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "vehicle-workspace"
    original = root / "original"
    edit = root / "edit"
    assets = edit / "assets"
    original.mkdir(parents=True)
    assets.mkdir(parents=True)
    source = original / "vehicle.ytd"
    source.write_bytes(b"RSC8-source")
    xml = edit / "vehicle.ytd.xml"
    xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<TextureDictionary>
 <Item>
  <Name>diffuse</Name><Unk32 value="0" /><Usage>DEFAULT</Usage>
  <UsageFlags>0</UsageFlags><ExtraFlags value="0" />
  <Width value="16" /><Height value="8" /><MipLevels value="1" />
  <Format>D3DFMT_A8R8G8B8</Format><FileName>diffuse.dds</FileName>
 </Item>
 <Item>
  <Name>normal</Name><Unk32 value="0" /><Usage>NORMAL</Usage>
  <UsageFlags>0</UsageFlags><ExtraFlags value="0" />
  <Width value="4" /><Height value="4" /><MipLevels value="1" />
  <Format>D3DFMT_A8R8G8B8</Format><FileName>normal.dds</FileName>
 </Item>
</TextureDictionary>
""", encoding="utf-8")
    _dds(assets / "diffuse.dds")
    _dds(assets / "normal.dds", (4, 4))
    (root / "native-workspace.json").write_text(json.dumps({
        "schema_version": 1,
        "operation": "native_asset_workspace",
        "edition": "Enhanced",
        "source": {
            "name": "vehicle.ytd", "suffix": ".ytd", "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "snapshot": "original/vehicle.ytd",
        },
        "xml": {"path": "edit/vehicle.ytd.xml"},
    }), encoding="utf-8")
    return root


def test_texture_catalog_and_dds_metadata(tmp_path):
    workspace = _workspace(tmp_path)
    catalog = TextureDictionaryWorkspace(workspace).catalog()
    assert [item.name for item in catalog.textures] == ["diffuse", "normal"]
    assert catalog.textures[0].width == 16
    assert catalog.textures[0].format == "D3DFMT_A8R8G8B8"
    assert catalog.textures[0].sha256
    assert catalog.warnings == ()
    metadata = inspect_dds(workspace / "edit" / "assets" / "diffuse.dds")
    assert (metadata.width, metadata.height, metadata.mip_levels) == (16, 8, 1)


def test_replace_texture_from_raster_updates_xml_and_keeps_history(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "replacement.png"
    Image.new("RGBA", (32, 12), (200, 20, 40, 180)).save(source)
    editor = TextureDictionaryWorkspace(workspace)
    old = (workspace / "edit" / "assets" / "diffuse.dds").read_bytes()
    result = editor.replace("DIFFUSE", source)
    assert result.action == "replace"
    assert (result.texture.width, result.texture.height) == (32, 12)
    assert result.texture.format == "D3DFMT_A8R8G8B8"
    assert (result.history / "dependency.dds").read_bytes() == old
    assert (result.history / "workspace.xml").is_file()
    xml = (workspace / "edit" / "vehicle.ytd.xml").read_text(encoding="utf-8")
    assert 'Width value="32"' in xml and 'Height value="12"' in xml


def test_add_and_remove_texture_are_catalogued_and_recoverable(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "detail.webp"
    Image.new("RGB", (7, 9), "green").save(source)
    editor = TextureDictionaryWorkspace(workspace)
    added = editor.add("detail_layer", source)
    assert added.texture.file_name == "detail_layer.dds"
    assert (workspace / "edit" / "assets" / "detail_layer.dds").is_file()
    removed = editor.remove("detail_layer")
    assert removed.action == "remove"
    assert all(item.name != "detail_layer" for item in removed.catalog.textures)
    assert (removed.history / "dependency.dds").is_file()
    assert not (workspace / "edit" / "assets" / "detail_layer.dds").exists()


def test_restore_latest_reverses_replace_and_add(tmp_path):
    workspace = _workspace(tmp_path)
    source = tmp_path / "replacement.png"
    Image.new("RGBA", (30, 14), "purple").save(source)
    editor = TextureDictionaryWorkspace(workspace)
    original = (workspace / "edit" / "assets" / "diffuse.dds").read_bytes()
    editor.replace("diffuse", source)
    restored = editor.restore_latest()
    diffuse = next(item for item in restored.catalog.textures if item.name == "diffuse")
    assert (diffuse.width, diffuse.height) == (16, 8)
    assert (workspace / "edit" / "assets" / "diffuse.dds").read_bytes() == original
    assert restored.restored.name.endswith(".restored")
    assert restored.recovery_history.is_dir()

    editor.add("temporary", source)
    assert (workspace / "edit" / "assets" / "temporary.dds").is_file()
    restored_add = editor.restore_latest()
    assert all(item.name != "temporary" for item in restored_add.catalog.textures)
    assert not (workspace / "edit" / "assets" / "temporary.dds").exists()


def test_texture_workspace_reports_mismatch_and_refuses_unsafe_or_duplicate_data(tmp_path):
    workspace = _workspace(tmp_path)
    xml = workspace / "edit" / "vehicle.ytd.xml"
    text = xml.read_text(encoding="utf-8").replace(
        '<Width value="16" />', '<Width value="99" />', 1,
    )
    xml.write_text(text, encoding="utf-8")
    catalog = TextureDictionaryWorkspace(workspace).catalog()
    assert "dimensions" in catalog.textures[0].warnings[0]

    with pytest.raises(ValueError, match="unsafe filename"):
        TextureDictionaryWorkspace(workspace).add("../escape", tmp_path / "missing.png")
    duplicate = text.replace("<Name>normal</Name>", "<Name>diffuse</Name>")
    xml.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        TextureDictionaryWorkspace(workspace).catalog()


def test_dds_parser_supports_dxt_and_rejects_unknown_format(tmp_path):
    dds = bytearray(128)
    dds[:4] = b"DDS "
    struct.pack_into("<I", dds, 4, 124)
    struct.pack_into("<II", dds, 12, 8, 16)
    struct.pack_into("<I", dds, 28, 4)
    struct.pack_into("<I", dds, 76, 32)
    struct.pack_into("<I", dds, 80, 0x4)
    dds[84:88] = b"DXT5"
    path = tmp_path / "compressed.dds"
    path.write_bytes(dds)
    assert inspect_dds(path).format == "D3DFMT_DXT5"
    dds[84:88] = b"NOPE"
    path.write_bytes(dds)
    with pytest.raises(ValueError, match="cannot identify"):
        inspect_dds(path)


def test_texture_workspace_requires_ytd_manifest_and_safe_dependencies(tmp_path):
    workspace = _workspace(tmp_path)
    manifest = workspace / "native-workspace.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["source"]["suffix"] = ".ydr"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="requires a native .ytd"):
        TextureDictionaryWorkspace(workspace)

    workspace = _workspace(tmp_path / "second")
    xml = workspace / "edit" / "vehicle.ytd.xml"
    xml.write_text(
        xml.read_text(encoding="utf-8").replace("diffuse.dds", "../outside.dds"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="dependency path is unsafe"):
        TextureDictionaryWorkspace(workspace).catalog()


def test_texture_workspace_cli_lists_and_requires_explicit_edit_acknowledgement(tmp_path):
    workspace = _workspace(tmp_path)
    replacement = tmp_path / "replacement.png"
    Image.new("RGBA", (20, 10), "blue").save(replacement)
    runner = CliRunner()
    listed = runner.invoke(main, ["sdk", "list-ytd-textures", str(workspace)])
    assert listed.exit_code == 0, listed.output
    assert '"texture_count": 2' in listed.output

    denied = runner.invoke(main, [
        "sdk", "replace-ytd-texture", str(workspace), "diffuse", str(replacement),
    ])
    assert denied.exit_code != 0
    assert "--acknowledge-edit" in denied.output
    replaced = runner.invoke(main, [
        "sdk", "replace-ytd-texture", str(workspace), "diffuse", str(replacement),
        "--acknowledge-edit",
    ])
    assert replaced.exit_code == 0, replaced.output
    assert "20x10" in replaced.output
    undone = runner.invoke(main, [
        "sdk", "undo-ytd-texture-edit", str(workspace), "--acknowledge-edit",
    ])
    assert undone.exit_code == 0, undone.output

    added = runner.invoke(main, [
        "sdk", "add-ytd-texture", str(workspace), "overlay", str(replacement),
        "--acknowledge-edit",
    ])
    assert added.exit_code == 0, added.output
    removed = runner.invoke(main, [
        "sdk", "remove-ytd-texture", str(workspace), "overlay",
        "--acknowledge-edit",
    ])
    assert removed.exit_code == 0, removed.output
