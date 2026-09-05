"""Source-aware shop and animation parity through the desktop write boundary."""

import pytest
from lxml import etree

from allin1_sdk import weapon_desktop
from allin1_sdk.weapon_authoring import SHOP_FIELDS, WeaponAuthoringWorkspace
from test_weapon_authoring_core import _advanced_source, _advanced_workspace
from test_weapon_desktop import confirmed, tree_hashes


def shop_payload(workspace):
    return {"action": "edit_shop", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
            "metadata_source": "metadata/weapon_shop.meta", "expected_revision": 0,
            "updates": {"shop.cost": "900", "shop.ammoCost": "200", "shop.availableInSP": "true",
                        "shop.textLabel": "WT_REVIEWED"}}


def animation_payload(workspace):
    return {"action": "clone_animation", "workspace": str(workspace.root), "weapon": "WEAPON_TARGET",
            "template_weapon": "WEAPON_AUTHOR", "metadata_source": "metadata/weaponanimations.meta",
            "expected_revision": 0}


def test_shop_source_inspection_is_read_only_and_matches_the_copy(tmp_path):
    source = _advanced_source(tmp_path)
    before = tree_hashes(source)
    inspected = weapon_desktop.inspect({"source": str(source), "editor_kind": "shop", "weapon": "WEAPON_AUTHOR"})
    assert inspected["shop_sources"] == ["metadata/weapon_shop.meta"]
    assert inspected["shop_values"]["values"]["shop.cost"] == "750"
    assert inspected["shop_values"]["identity_field"] == "nameHash"
    assert inspected["relationship_editable_fields"] == []
    assert tree_hashes(source) == before
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    copied = weapon_desktop.inspect({"workspace": str(workspace.root), "editor_kind": "shop", "weapon": "WEAPON_AUTHOR"})
    assert copied["shop_values"] == inspected["shop_values"]
    assert set(copied["relationship_editable_fields"]) == set(SHOP_FIELDS)


def test_shop_review_save_and_exact_undo_preserve_representations_and_selection(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    before = tree_hashes(workspace.source)
    payload = shop_payload(workspace)
    reviewed = weapon_desktop.review(payload)
    assert reviewed["source"] == "metadata/weapon_shop.meta"
    assert reviewed["affected_weapons"] == ["WEAPON_AUTHOR"]
    assert len(reviewed["changes"]) == 4
    assert all(change["source"] == reviewed["source"] for change in reviewed["changes"])
    assert tree_hashes(workspace.source) == before
    saved = weapon_desktop.apply(confirmed(payload))
    assert saved["revision"] == 1 and saved["editor_kind"] == "shop"
    assert saved["shop_values"]["values"]["shop.cost"] == "900"
    root = etree.parse(str(workspace.source / "metadata/weapon_shop.meta"))
    assert root.xpath("string(//cost/@value)") == "900"
    assert root.xpath("string(//ammoCost/@ref)") == "200"
    assert root.xpath("string(//textLabel)") == "WT_REVIEWED"
    assert root.xpath("string(//UnknownShopNode/Nested/@value)") == "42"
    restored = weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}))
    assert restored["revision"] == 2 and restored["editor_kind"] == "shop"
    assert restored["shop_values"]["source"] == payload["metadata_source"]
    assert tree_hashes(workspace.source) == before
    assert tree_hashes(tmp_path / "weapon-source") == before


def test_shop_requires_exact_source_when_multiple_records_exist(tmp_path):
    source = _advanced_source(tmp_path)
    second = source / "other/weapon_shop.meta"
    second.parent.mkdir()
    second.write_bytes((source / "metadata/weapon_shop.meta").read_bytes())
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    inspected = weapon_desktop.inspect({"workspace": str(workspace.root), "editor_kind": "shop"})
    assert len(inspected["shop_sources"]) == 2
    assert inspected["shop_values"] is None
    payload = shop_payload(workspace)
    with pytest.raises(ValueError, match="ambiguous"):
        weapon_desktop.review({key: value for key, value in payload.items() if key != "metadata_source"})
    before_other = (workspace.source / "other/weapon_shop.meta").read_bytes()
    weapon_desktop.apply(confirmed(payload))
    assert (workspace.source / "other/weapon_shop.meta").read_bytes() == before_other
    restored = weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}))
    assert restored["shop_values"]["source"] == payload["metadata_source"]


@pytest.mark.parametrize("updates", [
    {"shop.cost": "-1"}, {"shop.cost": "2147483648"}, {"shop.ammoCost": "1.5"},
    {"shop.cost": True}, {"shop.availableInSP": "maybe"}, {"shop.textLabel": "<xml>"},
    {"shop.nameHash": "WEAPON_OTHER"}, {"weapon.slot": "SLOT_OTHER"}, {"shop.cost": "750"},
])
def test_invalid_shop_updates_never_write(tmp_path, updates):
    workspace = _advanced_workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({**shop_payload(workspace), "updates": updates})
    assert tree_hashes(workspace.root) == before


def test_missing_shop_nodes_and_records_are_not_synthesized(tmp_path):
    source = _advanced_source(tmp_path)
    path = source / "metadata/weapon_shop.meta"
    path.write_bytes(path.read_bytes().replace(b'<ammoCost ref="150"/>', b""))
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    inspected = weapon_desktop.inspect({"workspace": str(workspace.root), "editor_kind": "shop"})
    assert "shop.ammoCost" not in inspected["relationship_editable_fields"]
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="no ammoCost node"):
        weapon_desktop.review(shop_payload(workspace))
    assert tree_hashes(workspace.root) == before
    missing = weapon_desktop.inspect({"workspace": str(workspace.root), "editor_kind": "shop", "weapon": "WEAPON_TARGET"})
    assert missing["shop_values"] is None and missing["shop_sources"] == []


def test_animation_review_adds_all_heterogeneous_sets_and_undo_restores_bytes(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    before = tree_hashes(workspace.source)
    reviewed = weapon_desktop.review(animation_payload(workspace))
    assert [change["set"] for change in reviewed["changes"]] == ["PISTOL_SET", "THROW_SET"]
    assert reviewed["affected_weapons"] == ["WEAPON_TARGET"]
    assert tree_hashes(workspace.source) == before
    saved = weapon_desktop.apply(confirmed(animation_payload(workspace)))
    assert saved["editor_kind"] == "animation" and saved["selected_weapon"] == "WEAPON_TARGET"
    assert saved["revision"] == 1
    coverage = [record for record in saved["project"]["animation_records"] if record["weapon_name"] == "WEAPON_TARGET"]
    assert [record["set_name"] for record in coverage] == ["PISTOL_SET", "THROW_SET"]
    tree = etree.parse(str(workspace.source / "metadata/weaponanimations.meta"))
    assert tree.xpath("//WeaponAnimations/Item[@key='WEAPON_TARGET']/Clip/@ref") == ["clip_pistol", "clip_throw"]
    assert tree.xpath("//WeaponAnimations/Item[@key='WEAPON_TARGET']/Flags/Item/text()") == ["retain"]
    restored = weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}))
    assert restored["editor_kind"] == "animation" and restored["selected_weapon"] == "WEAPON_TARGET"
    assert tree_hashes(workspace.source) == before
    assert tree_hashes(tmp_path / "weapon-source") == before


@pytest.mark.parametrize("changes", [
    {"weapon": "WEAPON_AUTHOR"}, {"template_weapon": "WEAPON_MISSING"},
    {"metadata_source": "../weaponanimations.meta"}, {"metadata_source": "C:/outside.meta"},
    {"metadata_source": "missing.meta"}, {"metadata_source": 123}, {"expected_revision": True},
])
def test_animation_guards_reject_without_writes(tmp_path, changes):
    workspace = _advanced_workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({**animation_payload(workspace), **changes})
    assert tree_hashes(workspace.root) == before


def test_animation_duplicate_set_and_ambiguous_source_fail_closed(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    path = workspace.source / "metadata/weaponanimations.meta"
    duplicate = workspace.source / "other/weaponanimations.meta"
    duplicate.parent.mkdir()
    duplicate.write_bytes(path.read_bytes())
    payload = animation_payload(workspace)
    with pytest.raises(ValueError, match="ambiguous"):
        weapon_desktop.review({key: value for key, value in payload.items() if key != "metadata_source"})
    # An explicit source remains usable, but a duplicated target key in its set does not.
    assert len(weapon_desktop.review(payload)["changes"]) == 2
    path.write_bytes(path.read_bytes().replace(b'<Item key="WEAPON_UNRELATED">', b'<Item key="WEAPON_AUTHOR">'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="exactly once per animation set"):
        weapon_desktop.review(payload)
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("kind", ["shop", "animation"])
def test_confirmation_stale_bytes_and_in_lock_recheck(tmp_path, kind):
    workspace = _advanced_workspace(tmp_path)
    payload = shop_payload(workspace) if kind == "shop" else animation_payload(workspace)
    approved = confirmed(payload)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="confirmation"):
        weapon_desktop.apply({**approved, "authoring_confirmed": False})
    assert tree_hashes(workspace.root) == before
    domain = (workspace.review_shop_update("WEAPON_AUTHOR", payload["updates"], payload["metadata_source"]) if kind == "shop"
              else workspace.review_animation_clone("WEAPON_TARGET", "WEAPON_AUTHOR", payload["metadata_source"]))
    path = workspace.source / "metadata/weapon_shop.meta"
    path.write_bytes(path.read_bytes().replace(b'value="42"', b'value="43"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    with pytest.raises(ValueError, match="changed after review"):
        if kind == "shop":
            workspace.update_shop("WEAPON_AUTHOR", payload["updates"], payload["metadata_source"], expected_revision=0,
                                  expected_review_sha256=domain["review_sha256"])
        else:
            workspace.clone_animation_mappings("WEAPON_TARGET", "WEAPON_AUTHOR", payload["metadata_source"],
                                              expected_revision=0, expected_review_sha256=domain["review_sha256"])
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("kind", ["shop", "animation"])
def test_review_is_bound_to_exact_source_and_changes(tmp_path, kind):
    workspace = _advanced_workspace(tmp_path)
    payload = shop_payload(workspace) if kind == "shop" else animation_payload(workspace)
    relative = "weapon_shop.meta" if kind == "shop" else "weaponanimations.meta"
    duplicate = workspace.source / "other" / relative
    duplicate.parent.mkdir()
    duplicate.write_bytes((workspace.source / "metadata" / relative).read_bytes())
    approved = confirmed(payload)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply({**approved, "metadata_source": f"other/{relative}"})
    if kind == "shop":
        with pytest.raises(ValueError, match="changed after review"):
            weapon_desktop.apply({**approved, "updates": {"shop.cost": "1000"}})
    assert tree_hashes(workspace.root) == before


def test_full_animation_review_cannot_be_truncated_at_protocol_boundary(tmp_path, monkeypatch):
    workspace = _advanced_workspace(tmp_path)
    original = WeaponAuthoringWorkspace.review_animation_clone
    def oversized(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result["changes"] *= 1001
        return result
    monkeypatch.setattr(WeaponAuthoringWorkspace, "review_animation_clone", oversized)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="exceeds desktop review limits"):
        weapon_desktop.review(animation_payload(workspace))
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("node", [
    b'<cost value="750"/><cost value="800"/>', b'<cost value="750" ref="900"/>',
    b'<cost value="750">900</cost>', b'<cost><Nested value="750"/></cost>',
])
def test_ambiguous_shop_nodes_are_rejected_on_inspection_and_review(tmp_path, node):
    source = _advanced_source(tmp_path)
    path = source / "metadata/weapon_shop.meta"
    path.write_bytes(path.read_bytes().replace(b'<cost value="750"/>', node))
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="duplicated|ambiguous"):
        weapon_desktop.inspect({"source": str(source), "editor_kind": "shop"})
    with pytest.raises(ValueError, match="duplicated|ambiguous"):
        weapon_desktop.review(shop_payload(workspace))
    assert tree_hashes(workspace.root) == before
