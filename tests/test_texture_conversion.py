import io
import os
import json
import shutil
import struct

import pytest
from PIL import Image

from allin1_sdk.desktop_protocol import ProtocolError, _review_texture_edit, _apply_texture_edit
from allin1_sdk.texture_conversion import convert_dds, conversion_metadata
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace
from test_texture_workspace import _workspace


@pytest.mark.parametrize("format_name", ["RGBA8", "DXT1", "DXT3", "DXT5"])
@pytest.mark.parametrize("size", [(16, 8), (7, 3), (1, 1)])
def test_every_generated_mip_has_valid_size_and_decodes(tmp_path, format_name, size):
    source, destination = tmp_path / "source.dds", tmp_path / "output.dds"
    Image.new("RGBA", size, (40, 80, 120, 192)).save(source, format="DDS")
    count = max(size).bit_length()
    result = convert_dds(source, destination, format_name, count)
    assert result.mip_levels == count
    data = destination.read_bytes()
    header = bytearray(data[:128])
    offset = 128
    width, height = size
    for _ in range(count):
        length = width * height * 4 if format_name == "RGBA8" else max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * (8 if format_name == "DXT1" else 16)
        struct.pack_into("<II", header, 12, height, width)
        struct.pack_into("<I", header, 28, 1)
        with Image.open(io.BytesIO(header + data[offset:offset + length])) as decoded:
            decoded.load()
            assert decoded.size == (width, height)
        offset += length
        width, height = max(1, width // 2), max(1, height // 2)
    assert offset == len(data)


def test_reviewed_conversion_updates_xml_dependency_and_undo(tmp_path):
    root = _workspace(tmp_path)
    workspace = TextureDictionaryWorkspace(root)
    original = (root / "edit/assets/diffuse.dds").read_bytes()
    payload = {"workspace": str(root), "expected_state_sha256": workspace.state_sha256(), "action": "convert", "texture_name": "diffuse", "output_format": "DXT5", "mip_levels": 5}
    _, review = _review_texture_edit(payload)
    assert "regenerated" in review["warning"]
    assert review["changes"][-1]["after"] == "5"
    with pytest.raises(ProtocolError, match="after review"):
        _apply_texture_edit({**payload, "mip_levels": 1, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    _, result = _apply_texture_edit({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["edited_texture"]["format"] == "D3DFMT_DXT5"
    assert result["edited_texture"]["mip_levels"] == 5
    workspace.restore_latest()
    assert (root / "edit/assets/diffuse.dds").read_bytes() == original
    assert workspace.catalog().textures[0].mip_levels == 1


@pytest.mark.parametrize("format_name,mips", [("BC7", 1), ("DXT5", 0), ("DXT1", 20), ("RGBA8", True)])
def test_invalid_conversion_settings_are_rejected(format_name, mips):
    with pytest.raises(ValueError):
        conversion_metadata(16, 8, format_name, mips)


def test_native_validation_rejects_missing_or_changed_dds_despite_matching_xml(tmp_path):
    from allin1_sdk.native_assets import _validate_ytd_payloads
    root = _workspace(tmp_path)
    rebuilt = tmp_path / "reparsed"
    shutil.copytree(root / "edit", rebuilt)
    xml = root / "edit/vehicle.ytd.xml"
    assert _validate_ytd_payloads(xml, root / "edit/assets", rebuilt / xml.name, rebuilt / "assets") == 2
    dependency = rebuilt / "assets/diffuse.dds"
    original = dependency.read_bytes()
    dependency.write_bytes(original[:-1] + bytes([original[-1] ^ 255]))
    with pytest.raises(RuntimeError, match="payloads changed"):
        _validate_ytd_payloads(xml, root / "edit/assets", rebuilt / xml.name, rebuilt / "assets")
    dependency.unlink()
    with pytest.raises(RuntimeError, match="missing the DDS"):
        _validate_ytd_payloads(xml, root / "edit/assets", rebuilt / xml.name, rebuilt / "assets")


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires the built native RpfPatcher/CodeWalker toolchain")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_real_native_ytd_rebuild_preserves_converted_mips_and_formats(tmp_path, edition):
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    root = _workspace(tmp_path)
    inspector = NativeAssetInspector(project_root())
    source = tmp_path / "generated.ytd"
    command = run_hidden([str(inspector.patcher), "asset-from-xml", str(root / "edit/vehicle.ytd.xml"),
                          str(source), str(root / "edit/assets"), "legacy" if edition == "Legacy" else "gen9"],
                         capture_output=True, text=True, timeout=120)
    assert command.returncode == 0, command.stderr or command.stdout
    original = source.read_bytes()
    for format_name in ("RGBA8", "DXT1", "DXT3", "DXT5"):
        editable = inspector.export_workspace(source, tmp_path / f"native-{format_name}", edition=edition)
        workspace = TextureDictionaryWorkspace(editable)
        workspace.convert("diffuse", format_name, 5)
        texture_name = "diffuse"
        if format_name == "DXT5":
            workspace.rename("diffuse", "renamed_diffuse")
            texture_name = "renamed_diffuse"
        output, report_path = inspector.build_workspace(editable, tmp_path / f"rebuilt-{format_name}.ytd")
        report = json.loads(report_path.read_text())
        assert report["validation"]["reparsed"] and report["validation"]["semantic_xml_match"]
        reparsed = inspector.export_workspace(output, tmp_path / f"reparsed-{format_name}", edition=edition)
        after = TextureDictionaryWorkspace(reparsed)
        texture = next(t for t in after.catalog().textures if t.name == texture_name)
        assert texture.mip_levels == 5
        assert texture.format == conversion_metadata(16, 8, format_name, 5).format
        # CodeWalker normalizes the DDS header (e.g. depth 0 -> 1). All encoded
        # pixels/blocks at every mip must still survive byte-for-byte.
        assert after.texture_path(texture_name).read_bytes()[128:] == workspace.texture_path(texture_name).read_bytes()[128:]
    assert source.read_bytes() == original
