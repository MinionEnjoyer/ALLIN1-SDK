import hashlib

import pytest
from lxml import etree

from allin1_sdk import weapon_desktop
from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from test_weapon_authoring_core import _source


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name != ".authoring.lock"}


def confirmed(payload):
    result = weapon_desktop.review(payload)
    return {**payload, "review_sha256": result["review_sha256"], "authoring_confirmed": True}


def test_copied_weapon_edit_and_undo_preserve_source_and_unknown_xml(tmp_path):
    source = _source(tmp_path)
    original = tree_hashes(source)
    inspected = weapon_desktop.inspect({"source": str(source)})
    assert inspected["project"]["summary"]["weapons"] == 2
    assert inspected["workspace"] is None
    assert inspected["values"]["values"]["ammo.ammoMax"] == "240"
    assert len(inspected["values"]["affected_weapons"]) == 2
    assert inspected["editable_fields"] == []
    create = {"action": "create", "source": str(source), "parent": str(tmp_path), "name": "copy"}
    review = confirmed(create)
    assert not (tmp_path / "copy").exists()
    copied = weapon_desktop.apply(review)
    assert copied["revision"] == 0 and not copied["can_undo"]
    workspace = WeaponAuthoringWorkspace(copied["workspace"])
    before = tree_hashes(workspace.source)
    edit = {"action": "edit", "workspace": copied["workspace"], "weapon": "WEAPON_AUTHOR", "expected_revision": 0,
            "updates": {"weapon.slot": "SLOT_TEST"}}
    edit_review = confirmed(edit)
    assert tree_hashes(workspace.source) == before
    saved = weapon_desktop.apply(edit_review)
    assert saved["revision"] == 1 and saved["can_undo"]
    assert saved["values"]["values"]["weapon.slot"] == "SLOT_TEST"
    assert "UnknownWeaponField" in (workspace.source / "weapons.meta").read_text()
    undone = weapon_desktop.apply(confirmed({"action": "undo", "workspace": copied["workspace"], "expected_revision": 1}))
    assert undone["revision"] == 2
    assert tree_hashes(workspace.source) == before
    assert tree_hashes(source) == original


def test_shared_ammo_requires_acknowledgement_and_reviews_affected_weapons(tmp_path):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    payload = {"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR", "expected_revision": 0,
               "updates": {"ammo.ammoMax": "300"}}
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="shared"):
        weapon_desktop.review(payload)
    assert tree_hashes(workspace.root) == before
    payload["acknowledge_shared"] = True
    review = weapon_desktop.review(payload)
    assert set(review["affected_weapons"]) == {"WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT"}
    saved = weapon_desktop.apply(confirmed(payload))
    assert saved["values"]["values"]["ammo.ammoMax"] == "300"


def test_same_size_external_edit_invalidates_review_even_without_revision_change(tmp_path):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    payload = {"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR", "expected_revision": 0,
               "updates": {"weapon.slot": "SLOT_TEST"}}
    approved = confirmed(payload)
    metadata = workspace.source / "weapons.meta"
    metadata.write_bytes(metadata.read_bytes().replace(b'value="77"', b'value="78"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("updates", [
    {"weapon.Name": "RENAMED"}, {"weapon.slot": 123}, {"ammo.ammoMax": "-1"},
    {"ammo.ammoMax": "100", "ammo.ammoMax50": "150"},
    {"weapon.model": "missing_model"},
])
def test_bad_edits_do_not_write(tmp_path, updates):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
                               "expected_revision": 0, "updates": updates, "acknowledge_shared": True})
    assert tree_hashes(workspace.root) == before


def test_copy_rejects_stale_source_existing_destination_and_game_paths(tmp_path):
    source = _source(tmp_path)
    payload = {"action": "create", "source": str(source), "parent": str(tmp_path), "name": "copy"}
    approved = confirmed(payload)
    path = source / "weapons.meta"
    path.write_bytes(path.read_bytes().replace(b'value="77"', b'value="78"'))
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    assert not (tmp_path / "copy").exists()
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="outside GTA"):
        weapon_desktop.review({**payload, "parent": str(game)})
    (tmp_path / "copy").mkdir()
    with pytest.raises(ValueError, match="new workspace"):
        weapon_desktop.review(payload)


def test_protocol_catalog_confirmation_and_job_boundary(tmp_path):
    service = DesktopProtocolService()
    def call(operation, payload):
        return service.handle(envelope(operation, payload, request_id="weapon-test", terminal=False))[0]
    call("handshake", {"client": {"name": "test", "version": "1"}, "supported_versions": ["1.0.0"]})
    catalog = call("catalog", {})["payload"]
    assert {"inspect_weapon_workbench", "review_weapon_authoring"} <= set(catalog["job_operations"])
    assert "apply_weapon_authoring" not in catalog["job_operations"]
    source = _source(tmp_path)
    inspected = call("inspect_weapon_workbench", {"source": str(source)})
    assert inspected["operation"] == "result" and inspected["risk"] == "read_only"
    denied = call("apply_weapon_authoring", {"action": "create", "source": str(source)})
    assert denied["operation"] == "error" and denied["risk"] == "authoring_write"
    assert "confirmation" in denied["payload"]["message"]


def test_component_inspection_shared_edit_and_exact_undo(tmp_path):
    source = _source(tmp_path)
    original = tree_hashes(source)
    selection = {"editor_kind": "component", "component": "COMPONENT_AUTHOR_CLIP"}
    inspected = weapon_desktop.inspect({"source": str(source), **selection})
    assert inspected["component_values"]["values"]["component.type"] == "CWeaponComponentClipInfo"
    assert inspected["relationship_editable_fields"] == []
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    context = {"workspace": str(workspace.root)}
    inspected = weapon_desktop.inspect({**context, **selection})
    assert set(inspected["relationship_editable_fields"]) == {
        "component.model", "component.locName", "component.locDesc", "component.attachBone",
    }
    before = tree_hashes(workspace.root)
    payload = {**context, "action": "edit_component", "component": "COMPONENT_AUTHOR_CLIP",
               "expected_revision": 0, "updates": {"component.locName": "WCT_EDITED"}}
    with pytest.raises(ValueError, match="shared"):
        weapon_desktop.review(payload)
    payload["acknowledge_shared"] = True
    reviewed = weapon_desktop.review(payload)
    assert reviewed["subject_kind"] == "component"
    assert set(reviewed["affected_weapons"]) == {"WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT"}
    assert tree_hashes(workspace.root) == before
    saved = weapon_desktop.apply(confirmed(payload))
    assert saved["editor_kind"] == "component" and saved["revision"] == 1
    assert saved["component_values"]["component"] == "COMPONENT_AUTHOR_CLIP"
    assert saved["component_values"]["values"]["component.locName"] == "WCT_EDITED"
    assert b"UnknownComponentField" in (workspace.source / "weaponcomponents.meta").read_bytes()
    undone = weapon_desktop.apply(confirmed({**context, "action": "undo", "expected_revision": 1}))
    assert undone["editor_kind"] == "component" and undone["revision"] == 2
    assert tree_hashes(workspace.source) == original
    assert tree_hashes(source) == original


def test_attachment_edit_is_specific_to_one_weapon_and_undo_restores_selection(tmp_path):
    source = _source(tmp_path)
    original = tree_hashes(source)
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    context = {"workspace": str(workspace.root)}
    target = {"weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_CLIP"}
    inspected = weapon_desktop.inspect({**context, **target, "editor_kind": "attachment"})
    assert inspected["relationship_editable_fields"] == ["attachment.default"]
    assert inspected["attachment_values"]["values"]["attachment.attachBone"] == "WAPClip"
    payload = {**context, **target, "action": "edit_attachment", "expected_revision": 0,
               "updates": {"attachment.default": "false"}}
    before = tree_hashes(workspace.root)
    reviewed = weapon_desktop.review(payload)
    assert reviewed["affected_weapons"] == ["WEAPON_AUTHOR"]
    assert tree_hashes(workspace.root) == before
    saved = weapon_desktop.apply(confirmed(payload))
    assert saved["editor_kind"] == "attachment" and saved["revision"] == 1
    assert saved["attachment_values"]["values"]["attachment.default"] == "false"
    alternate = next(link for link in saved["project"]["attachments"] if link["weapon_name"] == "WEAPON_AUTHOR_ALT")
    assert alternate["default"] is True
    assert (workspace.source / "weaponcomponents.meta").read_bytes() == (source / "weaponcomponents.meta").read_bytes()
    undone = weapon_desktop.apply(confirmed({**context, "action": "undo", "expected_revision": 1}))
    assert undone["attachment_values"]["weapon"] == "WEAPON_AUTHOR"
    assert undone["attachment_values"]["component"] == "COMPONENT_AUTHOR_CLIP"
    assert tree_hashes(workspace.source) == original
    assert tree_hashes(source) == original


def test_attachment_conflict_and_missing_nodes_fail_without_writes(tmp_path):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    context = {"workspace": str(workspace.root)}
    payload = {**context, "action": "edit_attachment", "weapon": "WEAPON_AUTHOR",
               "component": "COMPONENT_AUTHOR_SCOPE", "expected_revision": 0,
               "updates": {"attachment.default": "true"}}
    inspection = weapon_desktop.inspect({**context, "editor_kind": "attachment",
        "weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_SCOPE"})
    assert inspection["attachment_values"]["other_defaults"] == ["COMPONENT_AUTHOR_CLIP"]
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="already the default"):
        weapon_desktop.review(payload)
    assert tree_hashes(workspace.root) == before
    path = workspace.source / "weapons.meta"
    tree = etree.parse(str(path))
    node = tree.xpath(".//Components/Item[Name='COMPONENT_AUTHOR_SUPP']/Default")[0]
    node.getparent().remove(node)
    tree.write(str(path), encoding="utf-8")
    inspected = weapon_desktop.inspect({**context, "editor_kind": "attachment",
        "weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_SUPP"})
    assert inspected["relationship_editable_fields"] == []
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="no Default node"):
        weapon_desktop.review({**payload, "component": "COMPONENT_AUTHOR_SUPP"})
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize(("action", "updates"), [
    ("edit_component", {"component.type": "CWeaponComponentScopeInfo"}),
    ("edit_component", {"component.Name": "COMPONENT_NEW"}),
    ("edit_component", {"component.model": "w_at_missing"}),
    ("edit_component", {"component.locName": "<invalid>"}),
    ("edit_attachment", {"attachment.attachBone": "WAPScop"}),
    ("edit_attachment", {"attachment.default": "maybe"}),
    ("edit_attachment", {"attachment.default": True}),
])
def test_relationship_input_guards_do_not_write(tmp_path, action, updates):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({"workspace": str(workspace.root), "action": action,
            "weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_CLIP",
            "expected_revision": 0, "updates": updates, "acknowledge_shared": True})
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("action", ["edit_component", "edit_attachment"])
def test_relationship_reviews_reject_stale_content_and_action_tampering(tmp_path, action):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    payload = {"workspace": str(workspace.root), "action": action, "weapon": "WEAPON_AUTHOR",
        "component": "COMPONENT_AUTHOR_CLIP", "expected_revision": 0, "acknowledge_shared": True,
        "updates": {"component.locName": "WCT_CHANGED"} if action == "edit_component" else {"attachment.default": "false"}}
    approved = confirmed(payload)
    # The same change on another component/link must not reuse this approval.
    tampered = {**approved, "component": "COMPONENT_AUTHOR_SUPP"} if action == "edit_component" else {**approved, "weapon": "WEAPON_AUTHOR_ALT"}
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(tampered)
    assert tree_hashes(workspace.root) == before
    path = workspace.source / "weaponcomponents.meta"
    path.write_bytes(path.read_bytes().replace(b'flag="keep"', b'flag="held"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    assert tree_hashes(workspace.root) == before


def test_ambiguous_attachment_is_not_selected_or_edited(tmp_path):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    path = workspace.source / "weapons.meta"
    path.write_bytes(path.read_bytes().replace(b"COMPONENT_AUTHOR_SCOPE", b"COMPONENT_AUTHOR_CLIP"))
    target = {"workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_CLIP"}
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="not found uniquely"):
        weapon_desktop.inspect({**target, "editor_kind": "attachment"})
    with pytest.raises(ValueError, match="not found uniquely"):
        weapon_desktop.review({**target, "action": "edit_attachment", "expected_revision": 0, "updates": {"attachment.default": "false"}})
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("kind", ["component", "attachment"])
def test_relationship_save_rechecks_review_under_workspace_lock(tmp_path, kind):
    workspace = WeaponAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    updates = {"component.locName": "WCT_CHANGED"} if kind == "component" else {"attachment.default": "false"}
    reviewed = workspace.review_component_update("COMPONENT_AUTHOR_CLIP", updates, acknowledge_shared=True) if kind == "component" else workspace.review_attachment_update("WEAPON_AUTHOR", "COMPONENT_AUTHOR_CLIP", updates)
    path = workspace.source / "weaponcomponents.meta"
    path.write_bytes(path.read_bytes().replace(b'flag="keep"', b'flag="held"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        if kind == "component":
            workspace.update_component("COMPONENT_AUTHOR_CLIP", updates, acknowledge_shared=True, expected_revision=0, expected_review_sha256=reviewed["review_sha256"])
        else:
            workspace.update_attachment("WEAPON_AUTHOR", "COMPONENT_AUTHOR_CLIP", updates, expected_revision=0, expected_review_sha256=reviewed["review_sha256"])
    assert tree_hashes(workspace.root) == before
