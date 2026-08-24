from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from lxml import etree

from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace


WEAPONS = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponInfoBlob>
  <!-- retain-this-comment -->
  <Infos>
    <Item type="CWeaponInfo">
      <Name>WEAPON_AUTHOR</Name>
      <Model>w_pi_author</Model>
      <Slot ref="SLOT_AUTHOR" />
      <AmmoInfo ref="AMMO_AUTHOR" />
      <HumanNameHash>WT_AUTHOR</HumanNameHash>
      <StatName>WT_AUTHOR</StatName>
      <UnknownWeaponField custom="keep"><Nested value="77" /></UnknownWeaponField>
      <AttachPoints>
        <Item>
          <AttachBone>WAPClip</AttachBone>
          <Components>
            <Item><Name>COMPONENT_AUTHOR_CLIP</Name><Default value="true" />
              <UnknownLink value="keep" /></Item>
            <Item><Name>COMPONENT_AUTHOR_SCOPE</Name><Default value="false" /></Item>
          </Components>
        </Item>
        <Item>
          <AttachBone>WAPSupp</AttachBone>
          <Components>
            <Item><Name>COMPONENT_AUTHOR_SUPP</Name><Default value="false" /></Item>
          </Components>
        </Item>
      </AttachPoints>
    </Item>
    <Item type="CWeaponInfo">
      <Name>WEAPON_AUTHOR_ALT</Name>
      <Model>w_pi_author_alt</Model>
      <Slot ref="SLOT_AUTHOR_ALT" />
      <AmmoInfo ref="AMMO_AUTHOR" />
      <HumanNameHash>WT_AUTHOR_ALT</HumanNameHash>
      <StatName>WT_AUTHOR_ALT</StatName>
      <AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components>
        <Item><Name>COMPONENT_AUTHOR_CLIP</Name><Default value="true" /></Item>
      </Components></Item></AttachPoints>
    </Item>
  </Infos>
</CWeaponInfoBlob>
"""

AMMO = """<CWeaponInfoBlob><AmmoInfos>
  <Item type="CAmmoInfo">
    <Name>AMMO_AUTHOR</Name><Model>w_ammo_author</Model>
    <AmmoMax value="240" /><AmmoMax50 value="120" />
    <Explosion>NONE</Explosion><TrailFx>NULL</TrailFx><PrimedFx>NULL</PrimedFx>
    <UnknownAmmoField mode="preserve">unchanged</UnknownAmmoField>
  </Item>
</AmmoInfos></CWeaponInfoBlob>"""

COMPONENTS = """<CWeaponComponentInfoBlob><Infos>
  <Item type="CWeaponComponentClipInfo"><Name>COMPONENT_AUTHOR_CLIP</Name>
    <Model>w_pi_author_clip</Model><LocName>WCT_CLIP1</LocName>
    <LocDesc>WCD_CLIP1</LocDesc><AttachBone>WAPClip</AttachBone>
    <UnknownComponentField flag="keep" />
  </Item>
  <Item type="CWeaponComponentScopeInfo"><Name>COMPONENT_AUTHOR_SCOPE</Name>
    <Model>w_at_author_scope</Model><LocName>WCT_SCOPE</LocName>
    <LocDesc>WCD_SCOPE</LocDesc><AttachBone>WAPScop</AttachBone>
  </Item>
  <Item type="CWeaponComponentSuppressorInfo"><Name>COMPONENT_AUTHOR_SUPP</Name>
    <Model>w_at_author_supp</Model><LocName>WCT_SUPP</LocName>
    <LocDesc>WCD_SUPP</LocDesc><AttachBone>WAPSupp</AttachBone>
  </Item>
</Infos></CWeaponComponentInfoBlob>"""

ANIMATIONS = """<WeaponAnimations><Item key="WEAPON_AUTHOR" />
<Item key="WEAPON_AUTHOR_ALT" /></WeaponAnimations>"""
SHOP = """<Shop><Item><weaponName>WEAPON_AUTHOR</weaponName></Item>
<Item><weaponName>WEAPON_AUTHOR_ALT</weaponName></Item></Shop>"""

MULTI_SET_ANIMATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponAnimationsSets>
  <!-- animation-comment-must-survive -->
  <Sets>
    <Item key="PISTOL_SET">
      <WeaponAnimations>
        <Item key="WEAPON_AUTHOR" variant="pistol">
          <Clip ref="clip_pistol"/><Blend value="0.25"/>
        </Item>
        <Item key="WEAPON_UNRELATED"><Clip ref="other"/></Item>
      </WeaponAnimations>
    </Item>
    <Item>
      <Name>THROW_SET</Name>
      <WeaponAnimations>
        <Item key="WEAPON_AUTHOR" variant="throw">
          <Clip ref="clip_throw"/><Flags><Item>retain</Item></Flags>
        </Item>
      </WeaponAnimations>
    </Item>
  </Sets>
</CWeaponAnimationsSets>
"""

FULL_SHOP = """<?xml version="1.0" encoding="UTF-8"?>
<WeaponShopItemArray>
  <!-- shop-comment-must-survive -->
  <weaponShopItems>
    <Item category="keep">
      <nameHash>WEAPON_AUTHOR</nameHash>
      <cost value="750"/>
      <ammoCost ref="150"/>
      <textLabel>WT_AUTHOR</textLabel>
      <weaponDesc value="WTD_AUTHOR"/>
      <weaponTT ref="WTT_AUTHOR"/>
      <weaponUppercase>WTU_AUTHOR</weaponUppercase>
      <availableInSP value="false"/>
      <UnknownShopNode mode="keep"><Nested value="42"/></UnknownShopNode>
    </Item>
  </weaponShopItems>
</WeaponShopItemArray>
"""


def _source(root: Path, *, rpf_source: bool = False) -> Path:
    source = root / ("dlc.rpf.source" if rpf_source else "weapon-source")
    source.mkdir(parents=True)
    for name, text in (
        ("weapons.meta", WEAPONS),
        ("ammo.meta", AMMO),
        ("weaponcomponents.meta", COMPONENTS),
        ("weaponanimations.meta", ANIMATIONS),
        ("weapon_shop.meta", SHOP),
    ):
        (source / name).write_text(text, encoding="utf-8")
    stream = source / "stream"
    stream.mkdir()
    for name in (
        "w_pi_author.ydr", "w_pi_author_alt.ydr", "w_ammo_author.ydr",
        "w_pi_author_clip.ydr", "w_at_author_scope.ydr", "w_at_author_supp.ydr",
        "w_pi_replacement.ydr", "w_at_replacement.ydr",
    ):
        (stream / name).write_bytes(b"native-model:" + name.encode("ascii"))
    return source


def _workspace(tmp_path: Path) -> WeaponAuthoringWorkspace:
    return WeaponAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "weapon-workspace",
    )


def _advanced_source(root: Path) -> Path:
    source = _source(root)
    weapons_path = source / "weapons.meta"
    weapons = weapons_path.read_text(encoding="utf-8").replace(
        "</Infos>",
        """  <Item type="CWeaponInfo">
      <Name>WEAPON_TARGET</Name><Model>w_pi_target</Model>
      <Slot ref="SLOT_TARGET"/><AmmoInfo ref="AMMO_AUTHOR"/>
      <HumanNameHash>WT_TARGET</HumanNameHash><StatName>WT_TARGET</StatName>
    </Item>
  </Infos>""",
    )
    weapons_path.write_text(weapons, encoding="utf-8")
    (source / "stream" / "w_pi_target.ydr").write_bytes(b"target")
    (source / "weaponanimations.meta").unlink()
    (source / "weapon_shop.meta").unlink()
    metadata = source / "metadata"
    metadata.mkdir()
    (metadata / "weaponanimations.meta").write_text(
        MULTI_SET_ANIMATIONS, encoding="utf-8",
    )
    (metadata / "weapon_shop.meta").write_text(FULL_SHOP, encoding="utf-8")
    return source


def _advanced_workspace(tmp_path: Path) -> WeaponAuthoringWorkspace:
    return WeaponAuthoringWorkspace.create(
        _advanced_source(tmp_path), tmp_path / "advanced-workspace",
    )


def test_weapon_workspace_copies_cross_file_edits_and_undoes(tmp_path):
    original = _source(tmp_path)
    original_weapon = (original / "weapons.meta").read_bytes()
    original_ammo = (original / "ammo.meta").read_bytes()
    workspace = WeaponAuthoringWorkspace.create(original, tmp_path / "workspace")

    assert workspace.revision == 0
    assert len(workspace.manifest["source_content_fingerprint"]) == 64
    values = workspace.values("weapon_author")
    assert values.values["weapon.slot"] == "SLOT_AUTHOR"
    assert values.values["ammo.ammoMax"] == "240"
    assert values.affected_weapons == ("WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT")

    with pytest.raises(ValueError, match="shared by multiple weapons"):
        workspace.update("WEAPON_AUTHOR", {"ammo.ammoMax": "300"})

    result = workspace.update(
        "WEAPON_AUTHOR",
        {
            "weapon.statName": "WT_AUTHOR_EDITED",
            "ammo.ammoMax": "300",
            "ammo.ammoMax50": "150",
        },
        expected_revision=0,
        acknowledge_shared=True,
    )
    assert result.revision == 1
    assert result.affected_weapons == ("WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT")
    assert result.history.is_dir()
    history = json.loads((result.history / "edit.json").read_text(encoding="utf-8"))
    assert set(history["sha256"]) == {"ammo.meta", "weapons.meta"}
    assert workspace.values("WEAPON_AUTHOR").values["ammo.ammoMax"] == "300"
    assert (original / "weapons.meta").read_bytes() == original_weapon
    assert (original / "ammo.meta").read_bytes() == original_ammo

    undone = workspace.undo(expected_revision=1)
    assert undone.revision == 2
    restored = workspace.values("WEAPON_AUTHOR")
    assert restored.values["weapon.statName"] == "WT_AUTHOR"
    assert restored.values["ammo.ammoMax"] == "240"


def test_weapon_edits_preserve_unknown_xml_comments_and_scalar_styles(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.update(
        "WEAPON_AUTHOR",
        {
            "weapon.slot": "SLOT_AUTHOR_EDITED",
            "ammo.ammoMax": "260",
            "ammo.ammoMax50": "130",
        },
        acknowledge_shared=True,
    )
    weapon_tree = etree.parse(str(workspace.source / "weapons.meta"))
    ammo_tree = etree.parse(str(workspace.source / "ammo.meta"))
    assert weapon_tree.xpath("string(//UnknownWeaponField/@custom)") == "keep"
    assert weapon_tree.xpath("string(//UnknownWeaponField/Nested/@value)") == "77"
    assert weapon_tree.xpath("count(//comment()[contains(., 'retain-this-comment')])") == 1
    assert weapon_tree.xpath("string(//Slot/@ref)") == "SLOT_AUTHOR_EDITED"
    assert not weapon_tree.xpath("//Slot/@value")
    assert ammo_tree.xpath("string(//AmmoMax/@value)") == "260"
    assert ammo_tree.xpath("string(//UnknownAmmoField/@mode)") == "preserve"
    assert ammo_tree.xpath("string(//UnknownAmmoField)") == "unchanged"


def test_weapon_relationship_failure_rolls_back_every_touched_file(tmp_path):
    workspace = _workspace(tmp_path)
    weapon_before = (workspace.source / "weapons.meta").read_bytes()
    ammo_before = (workspace.source / "ammo.meta").read_bytes()

    with pytest.raises(ValueError, match="unresolved package relationships"):
        workspace.update(
            "WEAPON_AUTHOR",
            {"weapon.ammoInfo": "AMMO_DOES_NOT_EXIST"},
            expected_revision=0,
        )

    assert workspace.revision == 0
    assert (workspace.source / "weapons.meta").read_bytes() == weapon_before
    assert (workspace.source / "ammo.meta").read_bytes() == ammo_before
    assert not list((workspace.root / "history").iterdir())


def test_preexisting_malformed_unrelated_xml_does_not_block_safe_edit(tmp_path):
    source = _source(tmp_path)
    (source / "unrelated.meta").write_text("<Unrelated>", encoding="utf-8")
    workspace = WeaponAuthoringWorkspace.create(
        source, tmp_path / "weapon-workspace",
    )
    before_findings = {
        (item.code, item.path) for item in workspace.inspect().findings
    }
    assert ("xml_parse_failed", "unrelated.meta") in before_findings

    result = workspace.update(
        "WEAPON_AUTHOR", {"weapon.statName": "WT_SAFE_EDIT"},
        expected_revision=0,
    )

    assert result.revision == 1
    assert workspace.values("WEAPON_AUTHOR").values["weapon.statName"] \
        == "WT_SAFE_EDIT"
    assert (workspace.source / "unrelated.meta").read_text(encoding="utf-8") \
        == "<Unrelated>"


def test_cross_file_post_commit_failure_restores_atomic_snapshot(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    weapon_before = (workspace.source / "weapons.meta").read_bytes()
    ammo_before = (workspace.source / "ammo.meta").read_bytes()

    def reject_round_trip(*_args, **_kwargs):
        raise RuntimeError("forced cross-file validation failure")

    monkeypatch.setattr(workspace, "_verify_weapon_values", reject_round_trip)
    with pytest.raises(RuntimeError, match="forced cross-file"):
        workspace.update(
            "WEAPON_AUTHOR",
            {"weapon.statName": "WT_TEMP", "ammo.ammoMax": "280"},
            acknowledge_shared=True,
        )

    assert (workspace.source / "weapons.meta").read_bytes() == weapon_before
    assert (workspace.source / "ammo.meta").read_bytes() == ammo_before
    assert workspace.revision == 0
    assert not list((workspace.root / "history").iterdir())


def test_weapon_semantic_guards_reject_without_mutation(tmp_path):
    workspace = _workspace(tmp_path)
    ammo_before = (workspace.source / "ammo.meta").read_bytes()
    weapon_before = (workspace.source / "weapons.meta").read_bytes()

    with pytest.raises(ValueError, match="cannot exceed"):
        workspace.update(
            "WEAPON_AUTHOR", {"ammo.ammoMax50": "241"},
            acknowledge_shared=True,
        )
    with pytest.raises(ValueError, match="one exact package model asset"):
        workspace.update("WEAPON_AUTHOR", {"weapon.model": "w_pi_missing"})
    with pytest.raises(ValueError, match="one exact package model asset"):
        workspace.update(
            "WEAPON_AUTHOR", {"ammo.model": "w_ammo_missing"},
            acknowledge_shared=True,
        )
    with pytest.raises(ValueError, match="Unsupported weapon authoring fields"):
        workspace.update("WEAPON_AUTHOR", {"weapon.Name": "WEAPON_RENAMED"})
    assert (workspace.source / "ammo.meta").read_bytes() == ammo_before
    assert (workspace.source / "weapons.meta").read_bytes() == weapon_before
    assert workspace.revision == 0

    result = workspace.update(
        "WEAPON_AUTHOR", {"weapon.model": "w_pi_replacement"},
    )
    assert result.revision == 1
    assert workspace.values("WEAPON_AUTHOR").values["weapon.model"] \
        == "w_pi_replacement"


def test_optional_ammo_max_50_may_be_absent_when_editing_ammo_max(tmp_path):
    source = _source(tmp_path)
    ammo_path = source / "ammo.meta"
    tree = etree.parse(str(ammo_path))
    max_50 = tree.xpath("//AmmoMax50")[0]
    max_50.getparent().remove(max_50)
    tree.write(str(ammo_path), encoding="utf-8")
    workspace = WeaponAuthoringWorkspace.create(
        source, tmp_path / "weapon-workspace",
    )

    result = workspace.update(
        "WEAPON_AUTHOR", {"ammo.ammoMax": "300"}, acknowledge_shared=True,
    )

    assert result.revision == 1
    values = workspace.values("WEAPON_AUTHOR").values
    assert values["ammo.ammoMax"] == "300"
    assert values["ammo.ammoMax50"] == ""


def test_component_edits_report_blast_radius_and_keep_type_locked(tmp_path):
    workspace = _workspace(tmp_path)
    values = workspace.component_values("COMPONENT_AUTHOR_CLIP")
    assert values.values["component.type"] == "CWeaponComponentClipInfo"
    assert values.affected_weapons == ("WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT")

    with pytest.raises(ValueError, match="shared by multiple weapons"):
        workspace.update_component(
            "COMPONENT_AUTHOR_CLIP", {"component.locName": "WCT_EDITED"},
        )
    with pytest.raises(ValueError, match="Unsupported weapon-component fields"):
        workspace.update_component(
            "COMPONENT_AUTHOR_CLIP",
            {"component.type": "CWeaponComponentScopeInfo"},
            acknowledge_shared=True,
        )
    with pytest.raises(ValueError, match="one exact package model asset"):
        workspace.update_component(
            "COMPONENT_AUTHOR_CLIP", {"component.model": "w_at_missing"},
            acknowledge_shared=True,
        )

    result = workspace.update_component(
        "COMPONENT_AUTHOR_CLIP",
        {"component.model": "w_at_replacement", "component.locName": "WCT_EDITED"},
        acknowledge_shared=True,
    )
    assert result.affected_weapons == ("WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT")
    changed = workspace.component_values("COMPONENT_AUTHOR_CLIP")
    assert changed.values["component.model"] == "w_at_replacement"
    assert changed.values["component.locName"] == "WCT_EDITED"
    tree = etree.parse(str(workspace.source / "weaponcomponents.meta"))
    assert tree.xpath("string(//UnknownComponentField/@flag)") == "keep"
    assert tree.xpath("string(//Item[Name='COMPONENT_AUTHOR_CLIP']/@type)") \
        == "CWeaponComponentClipInfo"


def test_shared_record_blast_radius_deduplicates_case_aliases(tmp_path):
    workspace = _workspace(tmp_path)
    component = SimpleNamespace(
        name="COMPONENT_SHARED", model="", loc_name="", loc_desc="",
        attach_bone="", component_type="CWeaponComponentInfo",
        source="weaponcomponents.meta",
    )
    scan = SimpleNamespace(
        weapon_components=(component,),
        weapon_component_links=(
            SimpleNamespace(
                weapon_name="WEAPON_CASE", component_name="COMPONENT_SHARED",
            ),
            SimpleNamespace(
                weapon_name="weapon_case", component_name="component_shared",
            ),
        ),
        weapons=(
            SimpleNamespace(name="WEAPON_CASE", ammo_info="AMMO_SHARED"),
            SimpleNamespace(name="weapon_case", ammo_info="ammo_shared"),
        ),
    )

    values = workspace.component_values("component_shared", _scan=scan)

    assert values.affected_weapons == ("WEAPON_CASE",)
    assert workspace._weapons_using_ammo(scan, "ammo_shared") == ("WEAPON_CASE",)


def test_attachment_default_is_bounded_and_bone_is_locked(tmp_path):
    workspace = _workspace(tmp_path)
    before = (workspace.source / "weapons.meta").read_bytes()
    with pytest.raises(ValueError, match="Unsupported attachment fields"):
        workspace.update_attachment(
            "WEAPON_AUTHOR", "COMPONENT_AUTHOR_SUPP",
            {"attachment.attachBone": "WAPScop"},
        )
    with pytest.raises(ValueError, match="already the default"):
        workspace.update_attachment(
            "WEAPON_AUTHOR", "COMPONENT_AUTHOR_SCOPE",
            {"attachment.default": True},
        )
    assert (workspace.source / "weapons.meta").read_bytes() == before

    result = workspace.update_attachment(
        "WEAPON_AUTHOR", "COMPONENT_AUTHOR_SUPP",
        {"attachment.default": True}, expected_revision=0,
    )
    assert result.subject_kind == "attachment"
    link = next(
        item for item in result.project.attachments
        if item.weapon_name == "WEAPON_AUTHOR"
        and item.component_name == "COMPONENT_AUTHOR_SUPP"
    )
    assert link.default is True


def test_missing_scalar_nodes_are_not_synthesized(tmp_path):
    workspace = _workspace(tmp_path)
    component_path = workspace.source / "weaponcomponents.meta"
    tree = etree.parse(str(component_path))
    node = tree.xpath("//Item[Name='COMPONENT_AUTHOR_SUPP']/LocDesc")[0]
    node.getparent().remove(node)
    tree.write(str(component_path), encoding="utf-8")
    before = component_path.read_bytes()

    with pytest.raises(ValueError, match="does not synthesize schema fields"):
        workspace.update_component(
            "COMPONENT_AUTHOR_SUPP", {"component.locDesc": "WCD_NEW"},
        )
    assert component_path.read_bytes() == before
    assert workspace.revision == 0


def test_stale_workspace_revision_is_rejected_under_lock(tmp_path):
    workspace = _workspace(tmp_path)
    stale = WeaponAuthoringWorkspace(workspace.root)
    workspace.update(
        "WEAPON_AUTHOR", {"weapon.statName": "WT_REV_ONE"},
        expected_revision=0,
    )
    with pytest.raises(ValueError, match="revision conflict"):
        stale.update(
            "WEAPON_AUTHOR_ALT", {"weapon.statName": "WT_STALE"},
            expected_revision=0,
        )
    assert stale.revision == 1
    assert stale.values("WEAPON_AUTHOR_ALT").values["weapon.statName"] \
        == "WT_AUTHOR_ALT"


def test_tampered_history_backup_is_refused_without_overwrite(tmp_path):
    workspace = _workspace(tmp_path)
    result = workspace.update(
        "WEAPON_AUTHOR", {"weapon.statName": "WT_CHANGED"},
    )
    backup = result.history / "files" / "weapons.meta"
    backup.write_bytes(b"tampered")
    current = (workspace.source / "weapons.meta").read_bytes()
    with pytest.raises(ValueError, match="backup hash is invalid"):
        workspace.undo(expected_revision=1)
    assert (workspace.source / "weapons.meta").read_bytes() == current
    assert workspace.revision == 1


def test_undo_refuses_to_overwrite_an_external_post_edit_change(tmp_path):
    workspace = _workspace(tmp_path)
    result = workspace.update(
        "WEAPON_AUTHOR", {"weapon.statName": "WT_CHANGED"},
    )
    history = json.loads((result.history / "edit.json").read_text("utf-8"))
    assert set(history["sha256_after"]) == {"weapons.meta"}

    authored = workspace.source / "weapons.meta"
    external = authored.read_text(encoding="utf-8").replace(
        "WT_CHANGED", "WT_EXTERNAL",
    )
    authored.write_text(external, encoding="utf-8")
    current = authored.read_bytes()

    with pytest.raises(ValueError, match="changed after its edit: weapons.meta"):
        workspace.undo(expected_revision=1)

    assert authored.read_bytes() == current
    assert workspace.revision == 1
    assert result.history.is_dir()
    assert not result.history.with_name(f"{result.history.name}.undone").exists()


def test_undo_refuses_history_without_a_verified_post_edit_state(tmp_path):
    workspace = _workspace(tmp_path)
    result = workspace.update(
        "WEAPON_AUTHOR", {"weapon.statName": "WT_CHANGED"},
    )
    record_path = result.history / "edit.json"
    record = json.loads(record_path.read_text("utf-8"))
    record.pop("sha256_after")
    record_path.write_text(json.dumps(record), encoding="utf-8")
    current = (workspace.source / "weapons.meta").read_bytes()

    with pytest.raises(ValueError, match="no verified post-edit state"):
        workspace.undo(expected_revision=1)

    assert (workspace.source / "weapons.meta").read_bytes() == current
    assert workspace.revision == 1


def test_undo_ignores_unrelated_external_members(tmp_path):
    workspace = _workspace(tmp_path)
    workspace.update("WEAPON_AUTHOR", {"weapon.statName": "WT_CHANGED"})
    unrelated = workspace.source / "notes.txt"
    unrelated.write_text("external user note", encoding="utf-8")

    result = workspace.undo(expected_revision=1)

    assert result.revision == 2
    assert unrelated.read_text(encoding="utf-8") == "external user note"


def test_weapon_authoring_preserves_dlc_source_root_for_publication(tmp_path):
    workspace = WeaponAuthoringWorkspace.create(
        _source(tmp_path, rpf_source=True), tmp_path / "workspace",
    )
    assert workspace.source.name == "dlc.rpf.source"
    assert workspace.publish_source() == workspace.source


def test_animation_clone_copies_complete_heterogeneous_mappings_and_undoes(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    before = (workspace.source / "metadata" / "weaponanimations.meta").read_bytes()
    values = workspace.animation_values(
        "WEAPON_AUTHOR", "metadata\\weaponanimations.meta",
    )
    assert values.source == "metadata/weaponanimations.meta"
    assert values.set_names == ("PISTOL_SET", "THROW_SET")
    assert len(values.records) == 2
    assert len(workspace.inspect().to_dict()["animation_records"]) == 3

    result = workspace.clone_animation_mappings(
        "weapon_target", "weapon_author",
        source="metadata/weaponanimations.meta", expected_revision=0,
    )

    assert result.subject_kind == "animation"
    assert result.affected_weapons == ("WEAPON_TARGET",)
    assert result.revision == 1
    history = json.loads((result.history / "edit.json").read_text("utf-8"))
    assert history["operation"] == "weapon_animation_clone"
    assert history["files"] == ["metadata/weaponanimations.meta"]
    tree = etree.parse(str(workspace.source / "metadata" / "weaponanimations.meta"))
    groups = tree.xpath("//*[local-name()='WeaponAnimations']")
    assert [item.get("key") for item in groups[0] if isinstance(item.tag, str)] == [
        "WEAPON_AUTHOR", "WEAPON_TARGET", "WEAPON_UNRELATED",
    ]
    assert [item.get("key") for item in groups[1] if isinstance(item.tag, str)] == [
        "WEAPON_AUTHOR", "WEAPON_TARGET",
    ]
    for group in groups:
        template = next(item for item in group if item.get("key") == "WEAPON_AUTHOR")
        clone = next(item for item in group if item.get("key") == "WEAPON_TARGET")
        clone.set("key", "WEAPON_AUTHOR")
        template.tail = None
        clone.tail = None
        assert etree.tostring(template, method="c14n") == etree.tostring(
            clone, method="c14n",
        )
    assert tree.xpath(
        "count(//comment()[contains(., 'animation-comment-must-survive')])",
    ) == 1

    undone = workspace.undo(expected_revision=1)
    assert undone.subject_kind == "animation"
    assert undone.affected_weapons == ("WEAPON_TARGET",)
    assert (workspace.source / "metadata" / "weaponanimations.meta").read_bytes() \
        == before


def test_animation_clone_guards_existing_missing_duplicate_source_and_revision(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    animation_path = workspace.source / "metadata" / "weaponanimations.meta"
    before = animation_path.read_bytes()

    with pytest.raises(ValueError, match="already has mappings"):
        workspace.clone_animation_mappings("WEAPON_AUTHOR", "WEAPON_AUTHOR_ALT")
    with pytest.raises(ValueError, match="was not found"):
        workspace.clone_animation_mappings("WEAPON_TARGET", "WEAPON_AUTHOR_ALT")
    with pytest.raises(ValueError, match="Unsafe animation template source"):
        workspace.clone_animation_mappings(
            "WEAPON_TARGET", "WEAPON_AUTHOR", source="../weaponanimations.meta",
        )
    with pytest.raises(ValueError, match="was not found exactly"):
        workspace.clone_animation_mappings(
            "WEAPON_TARGET", "WEAPON_AUTHOR", source="weaponanimations.meta",
        )
    assert animation_path.read_bytes() == before
    assert workspace.revision == 0

    stale = WeaponAuthoringWorkspace(workspace.root)
    workspace.clone_animation_mappings("WEAPON_TARGET", "WEAPON_AUTHOR")
    with pytest.raises(ValueError, match="revision conflict"):
        stale.update_shop(
            "WEAPON_AUTHOR", {"shop.cost": "800"}, expected_revision=0,
        )

    second = _advanced_workspace(tmp_path / "duplicate")
    path = second.source / "metadata" / "weaponanimations.meta"
    tree = etree.parse(str(path))
    group = tree.xpath("//*[local-name()='WeaponAnimations']")[0]
    template = next(item for item in group if item.get("key") == "WEAPON_AUTHOR")
    group.insert(1, etree.fromstring(etree.tostring(template)))
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    duplicate_before = path.read_bytes()
    with pytest.raises(ValueError, match="exactly once per animation set"):
        second.clone_animation_mappings("WEAPON_TARGET", "WEAPON_AUTHOR")
    assert path.read_bytes() == duplicate_before
    assert second.revision == 0


def test_shop_update_preserves_representations_unknown_data_and_undoes(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    shop_path = workspace.source / "metadata" / "weapon_shop.meta"
    before = shop_path.read_bytes()
    values = workspace.shop_values(
        "weapon_author", "metadata\\weapon_shop.meta",
    )
    assert values.identity_field == "nameHash"
    assert values.identity_representation == "text"
    assert values.representations == {
        "shop.cost": "value",
        "shop.ammoCost": "ref",
        "shop.textLabel": "text",
        "shop.weaponDesc": "value",
        "shop.weaponTT": "ref",
        "shop.weaponUppercase": "text",
        "shop.availableInSP": "value",
    }
    assert len(workspace.inspect().to_dict()["shop_records"]) == 1

    result = workspace.update_shop(
        "WEAPON_AUTHOR",
        {
            "shop.cost": 1250,
            "shop.ammoCost": "250",
            "shop.textLabel": "WT_AUTHOR_NEW",
            "shop.weaponDesc": "WTD_AUTHOR_NEW",
            "shop.weaponTT": "WTT_AUTHOR_NEW",
            "shop.weaponUppercase": "WTU_AUTHOR_NEW",
            "shop.availableInSP": True,
        },
        expected_revision=0,
    )

    assert result.subject_kind == "shop"
    assert result.affected_weapons == ("WEAPON_AUTHOR",)
    assert json.loads((result.history / "edit.json").read_text("utf-8"))[
        "operation"
    ] == "weapon_shop_edit"
    tree = etree.parse(str(shop_path))
    item = tree.xpath(
        "//*[local-name()='weaponShopItems']/*[local-name()='Item']",
    )[0]
    assert item.xpath("string(./cost/@value)") == "1250"
    assert not item.xpath("./cost/@ref")
    assert item.xpath("string(./ammoCost/@ref)") == "250"
    assert not item.xpath("./ammoCost/@value")
    assert item.xpath("string(./weaponDesc/@value)") == "WTD_AUTHOR_NEW"
    assert item.xpath("string(./weaponTT/@ref)") == "WTT_AUTHOR_NEW"
    assert item.xpath("string(./availableInSP/@value)") == "true"
    assert item.xpath("string(./UnknownShopNode/@mode)") == "keep"
    assert item.xpath("string(./UnknownShopNode/Nested/@value)") == "42"
    assert tree.xpath("count(//comment()[contains(., 'shop-comment-must-survive')])") \
        == 1

    undone = workspace.undo(expected_revision=1)
    assert undone.subject_kind == "shop"
    assert undone.affected_weapons == ("WEAPON_AUTHOR",)
    assert shop_path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("shop.cost", "-1", "between 0 and 2147483647"),
        ("shop.ammoCost", "2147483648", "between 0 and 2147483647"),
        ("shop.cost", True, "must be an integer"),
        ("shop.availableInSP", "maybe", "must be true or false"),
        ("shop.textLabel", "label with spaces", "letters, numbers"),
    ],
)
def test_shop_update_rejects_invalid_bounded_values(
    tmp_path, field, value, message,
):
    workspace = _advanced_workspace(tmp_path)
    path = workspace.source / "metadata" / "weapon_shop.meta"
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message):
        workspace.update_shop("WEAPON_AUTHOR", {field: value})
    assert path.read_bytes() == before
    assert workspace.revision == 0


def test_shop_update_allows_optional_display_labels_to_be_cleared(tmp_path):
    workspace = _advanced_workspace(tmp_path)

    result = workspace.update_shop(
        "WEAPON_AUTHOR",
        {
            "shop.textLabel": "",
            "shop.weaponDesc": "",
            "shop.weaponTT": "",
            "shop.weaponUppercase": "",
        },
        expected_revision=0,
    )

    assert result.revision == 1
    values = workspace.shop_values("WEAPON_AUTHOR").values
    assert values["shop.textLabel"] == ""
    assert values["shop.weaponDesc"] == ""
    assert values["shop.weaponTT"] == ""
    assert values["shop.weaponUppercase"] == ""
    tree = etree.parse(
        str(workspace.source / "metadata" / "weapon_shop.meta"),
    )
    item = tree.xpath(
        "//*[local-name()='weaponShopItems']/*[local-name()='Item']",
    )[0]
    assert item.xpath("count(./textLabel)") == 1
    assert item.xpath("count(./weaponDesc/@value)") == 1
    assert item.xpath("count(./weaponTT/@ref)") == 1
    assert item.xpath("count(./weaponUppercase)") == 1


def test_shop_update_rejects_missing_nodes_non_direct_and_ambiguous_sources(tmp_path):
    workspace = _advanced_workspace(tmp_path)
    path = workspace.source / "metadata" / "weapon_shop.meta"
    tree = etree.parse(str(path))
    node = tree.xpath("//availableInSP")[0]
    node.getparent().remove(node)
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="does not synthesize schema fields"):
        workspace.update_shop("WEAPON_AUTHOR", {"shop.availableInSP": True})
    assert path.read_bytes() == before

    ambiguous = _advanced_workspace(tmp_path / "ambiguous")
    other = ambiguous.source / "other"
    other.mkdir()
    (other / "shop.meta").write_text(FULL_SHOP, encoding="utf-8")
    with pytest.raises(ValueError, match="source is ambiguous"):
        ambiguous.shop_values("WEAPON_AUTHOR")
    selected = ambiguous.shop_values(
        "WEAPON_AUTHOR", "metadata/weapon_shop.meta",
    )
    assert selected.source == "metadata/weapon_shop.meta"
    with pytest.raises(ValueError, match="Unsafe weapon shop source"):
        ambiguous.update_shop(
            "WEAPON_AUTHOR", {"shop.cost": "900"}, source="../shop.meta",
        )

    non_direct = _advanced_workspace(tmp_path / "non-direct")
    non_direct_path = non_direct.source / "metadata" / "weapon_shop.meta"
    non_direct_path.write_text(
        "<Root><Wrapper><Item><nameHash>WEAPON_AUTHOR</nameHash>"
        "<cost value='1'/></Item></Wrapper></Root>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Direct weaponShopItems record"):
        non_direct.shop_values("WEAPON_AUTHOR")
