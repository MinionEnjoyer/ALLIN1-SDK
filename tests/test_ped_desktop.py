from __future__ import annotations

import hashlib
import zipfile

import pytest
from lxml import etree

from allin1_sdk import ped_desktop
from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope
from allin1_sdk.ped_authoring import PedAuthoringWorkspace
from test_ped_authoring import _ped_package, _workspace


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name != ".authoring.lock"}


def confirmed(payload):
    return {**payload, "review_sha256": ped_desktop.review(payload)["review_sha256"], "authoring_confirmed": True}


def mutation(workspace, action, **extra):
    return {"workspace": str(workspace.root), "action": action, "ped": "ig_author",
            "expected_revision": workspace.revision, "expected_state_sha256": workspace.state_sha256(), **extra}


def test_copy_edit_and_undo_preserve_original_and_unknown_xml(tmp_path):
    source = _ped_package(tmp_path)
    original = hashes(source)
    payload = {"action": "create", "source": str(source), "parent": str(tmp_path), "name": "copy"}
    review = confirmed(payload)
    assert not (tmp_path / "copy").exists()
    result = ped_desktop.apply(review)
    assert result["workspace_write_performed"] and not result["game_write_performed"]
    workspace = PedAuthoringWorkspace(result["workspace"])
    assert len(result["editable_fields"]) == 7
    edited = ped_desktop.apply(confirmed(mutation(workspace, "edit", updates={"ped.modelType": "animal", "ped.clipDictionary": "move_m@brave"})))
    assert edited["revision"] == 1 and edited["can_undo"]
    tree = etree.parse(str(workspace.source / "peds.meta"))
    assert tree.xpath("string(//ModelType/@value)") == "animal"
    assert tree.xpath("string(//ClipDictionaryName/@ref)") == "move_m@brave"
    assert tree.xpath("string(//UnknownPedField/Nested/@value)") == "42"
    workspace = PedAuthoringWorkspace(workspace.root)
    undone = ped_desktop.apply(confirmed(mutation(workspace, "undo")))
    assert undone["revision"] == 2 and undone["selected_ped"]["name"] == "ig_author"
    assert hashes(source) == original
    assert hashes(workspace.source) == original


@pytest.mark.parametrize("action", ["migrate", "clone"])
def test_identity_and_clone_review_apply_undo(tmp_path, action):
    workspace = _workspace(tmp_path)
    original = hashes(workspace.source)
    target = "ig_renamed" if action == "migrate" else "ig_clone"
    payload = mutation(workspace, action, new_name=target, new_props=None)
    review = ped_desktop.review(payload)
    if action == "clone":
        assert review["clone_plan"]["ready"]
        assert len(review["clone_plan"]["source_sha256"]) == 5
    else:
        assert len(review["renames"]) == 4
    assert hashes(workspace.source) == original
    result = ped_desktop.apply(confirmed(payload))
    assert result["selected_ped"]["name"] == target
    assert result["values"]["ped.propsName"] == f"{target}_p"
    workspace = PedAuthoringWorkspace(workspace.root)
    undone = ped_desktop.apply(confirmed(mutation(workspace, "undo")))
    assert undone["selected_ped"]["name"] == "ig_author"
    assert hashes(workspace.source) == original


def test_missing_nodes_and_duplicate_identity_are_inspectable_not_editable(tmp_path):
    source = _ped_package(tmp_path, omit_expression=True)
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "copy")
    result = ped_desktop.inspect({"workspace": str(workspace.root)})
    assert "ped.expressionSet" not in result["editable_fields"]
    with pytest.raises(ValueError, match="no ExpressionSetName"):
        ped_desktop.review(mutation(workspace, "edit", updates={"ped.expressionSet": "expr_new"}))
    duplicate = workspace.source / "other"
    duplicate.mkdir()
    (duplicate / "peds.meta").write_bytes((workspace.source / "peds.meta").read_bytes())
    result = ped_desktop.inspect({"workspace": str(workspace.root), "ped": "ig_author", "metadata_source": "other/peds.meta"})
    assert not result["selection_unique"] and not result["editable_fields"]
    assert len(result["project"]["peds"]) == 2
    assert result["selected_ped"]["source"] == "other/peds.meta"


def test_missing_clone_assets_block_without_writes(tmp_path):
    workspace = _workspace(tmp_path)
    before = hashes(workspace.root)
    payload = mutation(workspace, "clone", new_name="ig_missing")
    review = ped_desktop.review(payload)
    assert not review["clone_plan"]["ready"]
    with pytest.raises(ValueError, match="not ready"):
        ped_desktop.apply(confirmed(payload))
    assert hashes(workspace.root) == before


@pytest.mark.parametrize("action", ["edit", "migrate", "clone", "undo"])
def test_same_size_external_change_invalidates_review(tmp_path, action):
    workspace = _workspace(tmp_path)
    if action == "undo":
        workspace.update("ig_author", {"ped.modelType": "animal"}, expected_revision=0)
    payload = mutation(workspace, action, updates={"ped.modelType": "animal"}, new_name="ig_clone" if action == "clone" else "ig_renamed")
    reviewed = confirmed(payload)
    asset = workspace.source / "stream/ig_author.ydd"
    asset.write_bytes(b"x" * asset.stat().st_size)
    before = hashes(workspace.root)
    with pytest.raises(ValueError, match="snapshot changed"):
        ped_desktop.apply(reviewed)
    assert hashes(workspace.root) == before


def test_domain_rechecks_state_inside_operation_lock(tmp_path):
    workspace = _workspace(tmp_path)
    state = workspace.state_sha256()
    (workspace.source / "stream/ig_author.ydd").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after review"):
        workspace.update("ig_author", {"ped.modelType": "animal"}, expected_revision=0, expected_state_sha256=state)


@pytest.mark.parametrize("value", [True, -1, "0", None])
def test_invalid_revision_rejected(tmp_path, value):
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="revision"):
        ped_desktop.review(mutation(workspace, "edit", expected_revision=value, updates={"ped.modelType": "animal"}))


def test_changed_request_requires_new_confirmation(tmp_path):
    workspace = _workspace(tmp_path)
    payload = confirmed(mutation(workspace, "edit", updates={"ped.modelType": "animal"}))
    before = hashes(workspace.root)
    with pytest.raises(ValueError, match="confirmation"):
        ped_desktop.apply({**payload, "authoring_confirmed": False})
    with pytest.raises(ValueError, match="changed after review"):
        ped_desktop.apply({**payload, "updates": {"ped.pedType": "CIVMALE"}})
    assert hashes(workspace.root) == before


@pytest.mark.parametrize("name", ["../escape", "CON", "already", "ped-package/child"])
def test_unsafe_or_existing_destinations_refused(tmp_path, name):
    source = _ped_package(tmp_path)
    (tmp_path / "already").mkdir()
    with pytest.raises(ValueError):
        ped_desktop.review({"action": "create", "source": str(source), "parent": str(tmp_path), "name": name})


def test_authoring_inside_game_is_refused(tmp_path):
    source = _ped_package(tmp_path)
    (tmp_path / "GTA5_Enhanced.exe").write_bytes(b"synthetic marker")
    with pytest.raises(ValueError, match="outside GTA"):
        ped_desktop.review({"action": "create", "source": str(source), "parent": str(tmp_path), "name": "copy"})


def test_archive_inspection_and_copy(tmp_path):
    source = _ped_package(tmp_path)
    archive = tmp_path / "peds.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in source.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())
    snapshot = ped_desktop.inspect({"source": str(archive)})
    assert snapshot["can_create"] and snapshot["selected_ped"]["name"] == "ig_author"
    created = ped_desktop.apply(confirmed({"action": "create", "source": str(archive), "parent": str(tmp_path), "name": "copy"}))
    assert created["revision"] == 0 and len(created["editable_fields"]) == 7


def test_name_candidates_are_not_resolved_dependencies(tmp_path):
    source = _ped_package(tmp_path)
    (source / "stream/ig_author_extra.ymt").write_bytes(b"not decoded")
    snapshot = ped_desktop.inspect({"source": str(source)})
    candidate = next(a for a in snapshot["assets"] if a["suffix"] == ".ymt")
    assert candidate["link"] == "name candidate"
    assert "not dependency-resolved" in candidate["role"]
    assert not snapshot["editable_fields"]
    assert snapshot["game_write_performed"] is False


def test_duplicate_records_in_one_file_retain_individual_inspection(tmp_path):
    source = _ped_package(tmp_path)
    path = source / "peds.meta"
    tree = etree.parse(str(path))
    original = tree.find(".//Item")
    duplicate = etree.fromstring(etree.tostring(original))
    duplicate.find("ModelType").set("value", "animal")
    original.addnext(duplicate)
    tree.write(str(path), encoding="utf-8")
    snapshot = ped_desktop.inspect({"source": str(source), "ped": "ig_author", "record_index": 1})
    assert len(snapshot["project"]["peds"]) == 2
    assert not snapshot["selection_unique"]
    assert snapshot["selected_index"] == 1
    assert snapshot["values"]["ped.modelType"] == "animal"
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "copy")
    with pytest.raises(ValueError, match="not found uniquely"):
        ped_desktop.review(mutation(workspace, "edit", updates={"ped.modelType": "new"}))


def test_desktop_never_truncates_a_consent_review(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    monkeypatch.setattr("allin1_sdk.desktop_protocol._MAX_STRING", 4)
    with pytest.raises(ValueError, match="exceeds desktop limits"):
        ped_desktop.review(mutation(workspace, "edit", updates={"ped.modelType": "animal"}))


def test_redirected_authoring_parent_is_refused(tmp_path):
    source = _ped_package(tmp_path)
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not available")
    with pytest.raises(ValueError, match="symbolic links or reparse"):
        ped_desktop.review({"action": "create", "source": str(source), "parent": str(link), "name": "copy"})


def test_protocol_risk_and_job_boundary(tmp_path):
    service = DesktopProtocolService()
    def call(operation, payload):
        return service.handle(envelope(operation, payload, request_id="ped-test", terminal=False))[0]
    call("handshake", {"client": {"name": "test", "version": "1"}, "supported_versions": ["1.0.0"]})
    catalog = call("catalog", {})["payload"]
    assert {"inspect_ped_workbench", "review_ped_authoring"} <= set(catalog["job_operations"])
    assert "apply_ped_authoring" not in catalog["job_operations"]
    result = call("inspect_ped_workbench", {"source": str(_ped_package(tmp_path))})
    assert result["operation"] == "result" and result["risk"] == "read_only"
    denied = call("apply_ped_authoring", {})
    assert denied["operation"] == "error" and denied["risk"] == "authoring_write"
    assert "confirmation" in denied["payload"]["message"]


def test_native_preview_retains_nested_identity_but_uses_safe_decoder_filename(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from allin1_sdk import asset_preview

    seen = []
    def read(_reader, entry, **kwargs):
        data = entry.encode()
        return SimpleNamespace(path=entry, data=data, size=len(data), truncated=False,
                               sha256=hashlib.sha256(data).hexdigest(), preview_kind="binary")
    def decode(_inspector, name, data, **kwargs):
        seen.append((name, data))
        return SimpleNamespace(summary=lambda: "Synthetic decoder", structured_text="", image_png=None,
                               format_name="YDD", metadata={}, warnings=[])
    monkeypatch.setattr(asset_preview.PackageAssetReader, "read", read)
    monkeypatch.setattr(asset_preview.NativeAssetInspector, "inspect_bytes", decode)
    service = asset_preview.AssetPreviewService(tmp_path)
    entries = ["a.rpf::ped.ydd", "nested/b.rpf::ped.ydd"]
    reports = [service.preview(tmp_path, entry) for entry in entries]
    assert [r["path"] for r in reports] == entries
    assert reports[0]["sha256"] != reports[1]["sha256"]
    assert seen == [("ped.ydd", entry.encode()) for entry in entries]
