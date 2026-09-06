import json
import os
from pathlib import Path

import pytest
from PIL import Image

from allin1_sdk import desktop_protocol as protocol, texture_batch
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace
from test_texture_workspace import _workspace


def request(root, **fields):
    return {"workspace": str(root), "expected_state_sha256": TextureDictionaryWorkspace(root).state_sha256(), "action": "batch", **fields}


def apply(payload):
    _, review = protocol.dispatch_operation("review_texture_edit", payload)
    _, result = protocol.dispatch_operation("apply_texture_edit", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    return result


def undo(root):
    return protocol.dispatch_operation("apply_texture_history", {"workspace": str(root), "expected_state_sha256": TextureDictionaryWorkspace(root).state_sha256(), "authoring_confirmed": True})[1]


@pytest.mark.parametrize("mode", ["remove", "convert"])
def test_selected_batch_is_one_revision_and_one_exact_undo(tmp_path, mode):
    root = _workspace(tmp_path)
    workspace = TextureDictionaryWorkspace(root)
    before = workspace.state_sha256()
    source = (root / "original/vehicle.ytd").read_bytes()
    canary = root / "edit/assets/notes.txt"
    canary.write_text("unrelated")
    result = apply(request(root, batch_action=mode, texture_names=["diffuse", "normal"], output_format="DXT5", mip_levels="full"))
    assert result["revision"] == 1 and result["batch_count"] == 2 and result["can_undo"]
    if mode == "convert":
        assert [item.mip_levels for item in workspace.catalog().textures] == [5, 3]
    else:
        assert not workspace.catalog().textures
    restored = undo(root)
    assert restored["revision"] == 2 and not restored["can_undo"]
    assert workspace.state_sha256() == before
    assert (root / "original/vehicle.ytd").read_bytes() == source
    assert canary.read_text() == "unrelated"


def test_import_maps_names_updates_adds_and_group_undo_removes_only_new_dependencies(tmp_path):
    root = _workspace(tmp_path)
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (8, 8)).save(folder / "diffuse.png")
    Image.new("RGBA", (2, 2)).save(folder / "badge.png")
    before = TextureDictionaryWorkspace(root).state_sha256()
    result = apply(request(root, batch_action="import", source_folder=str(folder), import_policy="upsert"))
    assert result["batch_count"] == 2 and result["texture_count"] == 3
    assert next(item for item in result["textures"] if item["name"] == "diffuse")["width"] == 8
    undo(root)
    assert TextureDictionaryWorkspace(root).state_sha256() == before
    assert not (root / "edit/assets/badge.dds").exists()


def test_batch_staging_failure_changes_no_workspace_files(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    workspace = TextureDictionaryWorkspace(root)
    before = workspace.state_sha256()
    original = TextureDictionaryWorkspace.convert
    def fail(self, name, *args):
        if name == "normal":
            raise ValueError("unsupported second texture")
        return original(self, name, *args)
    monkeypatch.setattr(TextureDictionaryWorkspace, "convert", fail)
    with pytest.raises(protocol.ProtocolError, match="unsupported second"):
        apply(request(root, batch_action="convert", texture_names=["diffuse", "normal"], output_format="DXT5", mip_levels="full"))
    assert workspace.state_sha256() == before
    assert workspace.revision == 0


def test_partial_commit_rolls_back_from_grouped_journal(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    before = TextureDictionaryWorkspace(root).state_sha256()
    original = texture_batch._install
    failed = False
    def fail(workspace, sources, expected):
        nonlocal failed
        if not failed:
            failed = True
            first = next(iter(sources))
            original(workspace, {first: sources[first]}, {first: expected[first]})
            raise OSError("simulated disk failure")
        return original(workspace, sources, expected)
    monkeypatch.setattr(texture_batch, "_install", fail)
    with pytest.raises(protocol.ProtocolError, match="disk failure"):
        apply(request(root, batch_action="remove", texture_names=["diffuse", "normal"]))
    assert TextureDictionaryWorkspace(root).state_sha256() == before
    assert not TextureDictionaryWorkspace(root).can_undo


def test_batch_undo_refuses_changed_backup_or_unrelated_concurrent_edit(tmp_path):
    root = _workspace(tmp_path)
    result = apply(request(root, batch_action="remove", texture_names=["diffuse"]))
    history = Path(result["batch_history"])
    backup = history / "before/edit/assets/diffuse.dds"
    backup.write_bytes(b"corrupt backup")
    with pytest.raises(protocol.ProtocolError, match="backup is missing or changed"):
        undo(root)
    assert not (root / "edit/assets/diffuse.dds").exists()


@pytest.mark.parametrize("fields", [
    {"batch_action": "remove", "texture_names": []},
    {"batch_action": "remove", "texture_names": ["diffuse", "DIFFUSE"]},
    {"batch_action": "remove", "texture_names": ["missing"]},
    {"batch_action": "convert", "texture_names": ["normal"], "output_format": "DXT1", "mip_levels": 16},
])
def test_invalid_batches_never_create_history(tmp_path, fields):
    root = _workspace(tmp_path)
    with pytest.raises(protocol.ProtocolError):
        protocol.dispatch_operation("review_texture_edit", request(root, **fields))
    assert not (root / "history").exists()


def test_import_source_change_invalidates_review(tmp_path):
    root = _workspace(tmp_path)
    folder = tmp_path / "images"
    folder.mkdir()
    image = folder / "diffuse.png"
    Image.new("RGBA", (8, 8)).save(image)
    payload = request(root, batch_action="import", source_folder=str(folder), import_policy="replace")
    _, reviewed = protocol.dispatch_operation("review_texture_edit", payload)
    Image.new("RGBA", (16, 16)).save(image)
    with pytest.raises(protocol.ProtocolError, match="changed after review"):
        protocol.dispatch_operation("apply_texture_edit", {**payload, "review_sha256": reviewed["review_sha256"], "authoring_confirmed": True})
    assert not (root / "history").exists()


def test_tampered_immutable_source_blocks_batch_before_any_workspace_write(tmp_path):
    root = _workspace(tmp_path)
    before = TextureDictionaryWorkspace(root).state_sha256()
    (root / "original/vehicle.ytd").write_bytes(b"tampered")
    with pytest.raises(protocol.ProtocolError, match="source identity|source snapshot"):
        apply(request(root, batch_action="remove", texture_names=["diffuse"]))
    assert TextureDictionaryWorkspace(root).state_sha256() == before
    assert not (root / "history").exists()


def test_interrupted_commit_can_be_recovered_after_reopening(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    before = TextureDictionaryWorkspace(root).state_sha256()
    original = texture_batch._install
    def interrupt(workspace, sources, expected):
        first = next(relative for relative in sources if relative.endswith(".dds"))
        original(workspace, {first: sources[first]}, {first: expected[first]})
        raise SystemExit("simulated process interruption")
    monkeypatch.setattr(texture_batch, "_install", interrupt)
    with pytest.raises(SystemExit):
        apply(request(root, batch_action="remove", texture_names=["diffuse", "normal"]))
    monkeypatch.setattr(texture_batch, "_install", original)
    reopened = TextureDictionaryWorkspace(root)
    assert reopened.can_undo and reopened.state_sha256() != before
    undo(root)
    assert reopened.state_sha256() == before


def test_import_collision_and_low_disk_space_are_non_mutating(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = _workspace(tmp_path)
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (8, 8)).save(folder / "diffuse.png")
    Image.new("RGB", (8, 8)).save(folder / "diffuse.jpg")
    with pytest.raises(protocol.ProtocolError, match="same texture name"):
        apply(request(root, batch_action="import", source_folder=str(folder), import_policy="replace"))
    monkeypatch.setattr(texture_batch.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(protocol.ProtocolError, match="free space"):
        apply(request(root, batch_action="remove", texture_names=["diffuse"]))
    assert not (root / "history").exists()


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires actual native YTD converter")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_native_rebuild_of_batch_import_conversion_and_removal(tmp_path, edition):
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    fixture = _workspace(tmp_path)
    inspector = NativeAssetInspector(project_root())
    source = tmp_path / "fixture.ytd"
    generated = run_hidden([str(inspector.patcher), "asset-from-xml", str(fixture / "edit/vehicle.ytd.xml"),
                            str(source), str(fixture / "edit/assets"), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=120)
    assert generated.returncode == 0, generated.stderr or generated.stdout
    original = source.read_bytes()
    root = inspector.export_workspace(source, tmp_path / "editable", edition=edition)
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGBA", (8, 4), (20, 40, 60, 128)).save(folder / "diffuse.png")
    Image.new("RGBA", (4, 2)).save(folder / "badge.png")
    apply(request(root, batch_action="import", source_folder=str(folder), import_policy="upsert"))
    apply(request(root, batch_action="convert", texture_names=["diffuse", "badge"], output_format="DXT5", mip_levels="full"))
    apply(request(root, batch_action="remove", texture_names=["normal"]))
    output, report_path = inspector.build_workspace(root, tmp_path / "batch.ytd")
    validation = json.loads(report_path.read_text())["validation"]
    assert validation["reparsed"] and validation["texture_payloads_match"] and validation["texture_payload_count"] == 2
    parsed = inspector.export_workspace(output, tmp_path / "reparsed", edition=edition)
    textures = TextureDictionaryWorkspace(parsed).catalog().textures
    assert {item.name: item.mip_levels for item in textures} == {"diffuse": 4, "badge": 3}
    assert source.read_bytes() == original
