from decimal import Decimal

import pytest
from lxml import etree

from allin1_sdk import weapon_desktop
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from allin1_sdk.weapon_fire_rate import RPM_KEY, INTERVAL_KEY, interval_for_rpm
from test_weapon_authoring_core import _source
from test_weapon_desktop import confirmed, tree_hashes


def rate_workspace(tmp_path, interval="0.118000"):
    source = _source(tmp_path)
    path = source / "weapons.meta"
    tree = etree.parse(str(path))
    weapon = tree.xpath(".//Item[Name='WEAPON_AUTHOR']")[0]
    etree.SubElement(weapon, "TimeBetweenShots", value=interval)
    path.write_bytes(etree.tostring(tree, encoding="utf-8", xml_declaration=True))
    return source, WeaponAuthoringWorkspace.create(source, tmp_path / "copy")


def test_rpm_review_save_exact_undo_and_source_readonly(tmp_path):
    source, workspace = rate_workspace(tmp_path)
    before_source, before_copy = tree_hashes(source), tree_hashes(workspace.source)
    snapshot = weapon_desktop.inspect({"source": str(source)})
    assert snapshot["values"]["values"][RPM_KEY] == "508.474576"
    assert snapshot["values"]["values"][INTERVAL_KEY] == "0.118000"
    assert not snapshot["editable_fields"]
    inspection = weapon_desktop.inspect({"workspace": str(workspace.root)})
    assert RPM_KEY in inspection["editable_fields"] and INTERVAL_KEY not in inspection["editable_fields"]
    request = {"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
               "expected_revision": 0, "updates": {RPM_KEY: "1200.00"}}
    review = weapon_desktop.review(request)
    changes = {item["field"]: item for item in review["changes"]}
    assert changes[RPM_KEY]["after"] == "1200"
    assert changes[INTERVAL_KEY] == {"field": INTERVAL_KEY, "before": "0.118000", "after": "0.05"}
    assert tree_hashes(workspace.source) == before_copy
    result = weapon_desktop.apply(confirmed(request))
    assert result["values"]["values"][RPM_KEY] == "1200"
    assert result["values"]["values"][INTERVAL_KEY] == "0.05"
    assert tree_hashes(source) == before_source
    weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}))
    assert tree_hashes(workspace.source) == before_copy


@pytest.mark.parametrize("rpm", ["", "0", "-1", "nan", "Infinity", "60001", "0.99999", "1e99999", "abc", True])
def test_invalid_rpm_never_writes(tmp_path, rpm):
    _, workspace = rate_workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review({"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
                              "expected_revision": 0, "updates": {RPM_KEY: rpm}})
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("rpm", ["1", "60000", "950", "508.474576", "1234.123456", "1200.123456789"])
def test_repeating_intervals_roundtrip(tmp_path, rpm):
    _, workspace = rate_workspace(tmp_path, "0.1")
    result = workspace.update("WEAPON_AUTHOR", {RPM_KEY: rpm}, expected_revision=0)
    values = workspace.values("WEAPON_AUTHOR").values
    assert abs(Decimal(values[RPM_KEY]) - Decimal(rpm)) < Decimal("0.000001")
    assert values[INTERVAL_KEY] == interval_for_rpm(rpm)
    assert result.revision == 1


def test_display_rounding_does_not_rewrite_an_unchanged_rate(tmp_path):
    _, workspace = rate_workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="no changed values"):
        workspace.update("WEAPON_AUTHOR", {RPM_KEY: "508.474576"}, expected_revision=0)
    assert tree_hashes(workspace.root) == before


def test_missing_node_not_synthesized_and_raw_interval_not_an_edit_alias(tmp_path):
    source = _source(tmp_path)
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    before = tree_hashes(workspace.root)
    for updates in ({RPM_KEY: "600"}, {INTERVAL_KEY: "0.1"}):
        with pytest.raises(ValueError):
            workspace.update("WEAPON_AUTHOR", updates, expected_revision=0)
    assert tree_hashes(workspace.root) == before


def test_invalid_source_interval_can_be_repaired_and_stale_review_rejected(tmp_path):
    _, workspace = rate_workspace(tmp_path, "0")
    assert workspace.values("WEAPON_AUTHOR").values[RPM_KEY] == ""
    review = confirmed({"action": "edit", "workspace": str(workspace.root), "weapon": "WEAPON_AUTHOR",
                        "expected_revision": 0, "updates": {RPM_KEY: "600"}})
    path = workspace.source / "weapons.meta"
    path.write_bytes(path.read_bytes().replace(b'TimeBetweenShots value="0"', b'TimeBetweenShots value="0.2"'))
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(review)
    assert tree_hashes(workspace.root) == before
