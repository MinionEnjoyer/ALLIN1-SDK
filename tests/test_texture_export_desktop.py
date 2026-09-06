import json
from pathlib import Path

import pytest
from PIL import Image

from allin1_sdk.desktop_protocol import ProtocolError, _operation_risk, JOB_OPERATIONS
from allin1_sdk.texture_export_desktop import run
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace
from test_texture_workspace import _workspace


def setup(tmp_path, **overrides):
    root = _workspace(tmp_path)
    payload = {"workspace": str(root), "expected_state_sha256": TextureDictionaryWorkspace(root).state_sha256(),
               "destination": str(tmp_path / "export"), "format": "dds", "mode": "all", **overrides}
    return root, payload


@pytest.mark.parametrize("format_name", ["dds", "png"])
def test_review_and_export_all_preserves_workspace_and_writes_verified_manifest(tmp_path, format_name):
    root, payload = setup(tmp_path, format=format_name)
    risk, review = run("review_texture_export", payload)
    assert risk == "read_only" and review["texture_count"] == 2
    assert not (tmp_path / "export").exists()
    risk, result = run("apply_texture_export", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert risk == "authoring_write" and not result["workspace_write_performed"]
    assert TextureDictionaryWorkspace(root).state_sha256() == payload["expected_state_sha256"]
    manifest = json.loads(Path(result["receipt"]).read_text())
    assert len(manifest["outputs"]) == 2
    for output in manifest["outputs"]:
        path = tmp_path / "export" / output["file"]
        if format_name == "dds":
            assert path.read_bytes() == (root / "edit/assets" / output["file"]).read_bytes()
        else:
            with Image.open(path) as img:
                assert img.format == "PNG" and img.mode == "RGBA"


def test_selection_is_exact_and_bound_to_review(tmp_path):
    root, payload = setup(tmp_path, mode="selected", texture_names=["normal"])
    _, review = run("review_texture_export", payload)
    assert review["texture_count"] == 1
    with pytest.raises(ProtocolError, match="changed after review"):
        run("apply_texture_export", {**payload, "texture_names": ["diffuse"], "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
    assert not (tmp_path / "export").exists()
    _, result = run("apply_texture_export", {**payload, "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
    assert result["texture_count"] == 1
    assert not (tmp_path / "export/diffuse.dds").exists()


@pytest.mark.parametrize("overrides", [
    {"mode": "selected", "texture_names": []}, {"mode": "selected", "texture_names": ["normal", "normal"]},
    {"mode": "selected", "texture_names": ["missing"]}, {"format": "jpeg"},
    {"mode": "all", "texture_names": ["normal"]}, {"expected_state_sha256": "0" * 64},
])
def test_invalid_export_is_rejected_without_outputs(tmp_path, overrides):
    _, payload = setup(tmp_path, **overrides)
    with pytest.raises(ProtocolError):
        run("review_texture_export", payload)
    assert not (tmp_path / "export").exists()


def test_no_confirmation_and_changed_source_rejected(tmp_path):
    root, payload = setup(tmp_path)
    _, review = run("review_texture_export", payload)
    with pytest.raises(ProtocolError, match="confirmation"):
        run("apply_texture_export", {**payload, "review_sha256": review["review_sha256"]})
    TextureDictionaryWorkspace(root).rename("normal", "new_normal")
    with pytest.raises(ProtocolError, match="changed"):
        run("apply_texture_export", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})


def test_existing_workspace_and_game_destinations_rejected(tmp_path):
    root, payload = setup(tmp_path)
    for destination in [tmp_path, root / "export"]:
        with pytest.raises(ProtocolError, match="new and outside"):
            run("review_texture_export", {**payload, "destination": str(destination)})
    game = tmp_path / "game"
    game.mkdir()
    with pytest.raises(ProtocolError, match="outside GTA"):
        run("review_texture_export", {**payload, "gta_path": str(game), "destination": str(game / "export")})


def test_raced_destination_is_not_deleted(tmp_path, monkeypatch):
    _, payload = setup(tmp_path)
    _, review = run("review_texture_export", payload)
    import allin1_sdk.texture_export_desktop as module
    copy = module.shutil.copyfile
    def race(source, destination):
        result = copy(source, destination)
        raced = tmp_path / "export"
        raced.mkdir(exist_ok=True)
        (raced / "user.txt").write_text("keep")
        return result
    monkeypatch.setattr(module.shutil, "copyfile", race)
    with pytest.raises(ProtocolError, match="appeared"):
        run("apply_texture_export", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert (tmp_path / "export/user.txt").read_text() == "keep"
    assert not list(tmp_path.glob(".allin1-textures-*"))


def test_protocol_risks_and_jobs_are_registered():
    assert "review_texture_export" in JOB_OPERATIONS
    assert "apply_texture_export" not in JOB_OPERATIONS
    assert _operation_risk("review_texture_export", {}) == "read_only"
    with pytest.raises(ProtocolError, match="cannot run as a job"):
        _operation_risk("apply_texture_export", {})
