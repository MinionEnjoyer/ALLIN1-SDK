from __future__ import annotations

import pytest
from lxml import etree

from allin1_sdk import weapon_desktop
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from allin1_sdk.weapon_camera import CAMERA_FIELDS
from test_weapon_authoring_core import _source
from test_weapon_desktop import confirmed, tree_hashes


def camera_workspace(tmp_path):
    source = _source(tmp_path)
    file = source / "weapons.meta"
    tree = etree.parse(str(file))
    weapon = tree.xpath(".//Item[Name='WEAPON_AUTHOR']")[0]
    for tag in dict.fromkeys(spec["tag"] for spec in CAMERA_FIELDS.values()):
        specs = [spec for spec in CAMERA_FIELDS.values() if spec["tag"] == tag]
        node = etree.SubElement(weapon, tag)
        for spec in specs:
            node.set(spec["attribute"], "30.00000" if spec["attribute"] == "value" else "0.00000")
    weapon.find("FirstPersonScopeOffset").set("z", "-0.014")
    weapon.find("FirstPersonScopeOffset").set("custom", "preserved")
    etree.SubElement(weapon, "WeaponFlags").text = "Automatic Gun UseFPSAimIK FutureFlag"
    file.write_bytes(etree.tostring(tree, encoding="utf-8", xml_declaration=True))
    return source, WeaponAuthoringWorkspace.create(source, tmp_path / "copy")


def test_scope_flag_review_save_and_exact_undo(tmp_path):
    source, workspace = camera_workspace(tmp_path)
    source_before = tree_hashes(source)
    copy_before = tree_hashes(workspace.source)
    readonly = weapon_desktop.inspect({"source": str(source)})
    assert readonly["values"]["values"]["weapon.firstPersonScopeOffset.z"] == "-0.014"
    assert len(readonly["camera_fields"]) == len(CAMERA_FIELDS)
    assert readonly["editable_fields"] == []
    edit = {"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR", "expected_revision": 0,
            "updates": {"weapon.firstPersonScopeOffset.z": "0.0180", "weapon.firstPersonScopeFov": "20.00000",
                        "weapon.weaponFlags": "Gun UseFPSAimIK FutureFlag"}}
    review = confirmed(edit)
    assert tree_hashes(workspace.source) == copy_before
    saved = weapon_desktop.apply(review)
    assert saved["values"]["values"]["weapon.firstPersonScopeOffset.z"] == "0.0180"
    assert saved["revision"] == 1 and saved["can_undo"]
    tree = etree.parse(str(workspace.source / "weapons.meta"))
    node = tree.xpath(".//Item[Name='WEAPON_AUTHOR']/FirstPersonScopeOffset")[0]
    assert dict(node.attrib) == {"x": "0.00000", "y": "0.00000", "z": "0.0180", "custom": "preserved"}
    assert tree_hashes(source) == source_before
    weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}))
    assert tree_hashes(workspace.source) == copy_before


@pytest.mark.parametrize("key,value", [
    ("weapon.firstPersonScopeOffset.x", "nan"), ("weapon.firstPersonScopeOffset.y", "inf"),
    ("weapon.firstPersonScopeOffset.z", "10.001"), ("weapon.firstPersonScopeOffset.z", ""),
    ("weapon.firstPersonScopeFov", "0"), ("weapon.firstPersonScopeFov", "180"),
    ("weapon.firstPersonScopeRotationOffset.x", "361"),
    ("weapon.weaponFlags", "Gun gun"), ("weapon.weaponFlags", "Gun <Unknown/>"),
    ("weapon.weaponFlags", "a" * 8193),
])
def test_invalid_camera_changes_do_not_write(tmp_path, key, value):
    _, workspace = camera_workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
                               "expected_revision": 0, "updates": {key: value}})
    assert tree_hashes(workspace.root) == before


def test_missing_vector_axis_cannot_be_created(tmp_path):
    source = _source(tmp_path)
    file = source / "weapons.meta"
    tree = etree.parse(str(file))
    etree.SubElement(tree.xpath(".//Item[Name='WEAPON_AUTHOR']")[0], "FirstPersonScopeOffset", z="0")
    file.write_bytes(etree.tostring(tree))
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    inspection = weapon_desktop.inspect({"workspace": str(workspace.root)})
    assert "weapon.firstPersonScopeOffset.z" in inspection["editable_fields"]
    assert "weapon.firstPersonScopeOffset.x" not in inspection["editable_fields"]
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="not synthesized"):
        workspace.update("WEAPON_AUTHOR", {"weapon.firstPersonScopeOffset.x": "0.018"}, expected_revision=0)
    assert tree_hashes(workspace.root) == before


def test_all_camera_axes_and_long_flags_fit_one_review(tmp_path):
    _, workspace = camera_workspace(tmp_path)
    updates = {key: "45" if spec["attribute"] == "value" else "0.018" for key, spec in CAMERA_FIELDS.items()}
    updates["weapon.weaponFlags"] = " ".join(f"FutureFlag{i}" for i in range(50))
    payload = {"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
               "expected_revision": 0, "updates": updates}
    result = weapon_desktop.apply(confirmed(payload))
    assert all(result["values"]["values"][key] == value for key, value in updates.items())


def test_camera_review_detects_external_drift(tmp_path):
    _, workspace = camera_workspace(tmp_path)
    approved = confirmed({"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
                          "expected_revision": 0, "updates": {"weapon.firstPersonScopeOffset.z": "0.018"}})
    file = workspace.source / "weapons.meta"
    file.write_bytes(file.read_bytes().replace(b'z="-0.014"', b'z="-0.015"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    assert tree_hashes(workspace.root) == before
