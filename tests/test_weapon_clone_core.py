from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

import allin1_sdk.weapon_authoring as weapon_authoring
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace


WEAPONS = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponInfoBlob><Infos>
  <!-- weapon-comment -->
  <Item type="CWeaponInfo" donor="keep">
    <Name>WEAPON_DONOR</Name><Model>w_pi_donor</Model>
    <Slot ref="SLOT_DONOR"/><AmmoInfo ref="AMMO_DONOR"/>
    <HumanNameHash>WT_DONOR</HumanNameHash><StatName>ST_DONOR</StatName>
    <UnknownWeapon mode="keep"><Nested value="77"/></UnknownWeapon>
    <AttachPoints>
      <Item><AttachBone>WAPClip</AttachBone><Components>
        <Item><Name>COMPONENT_DONOR_CLIP</Name><Default value="true"/></Item>
        <Item><Name>COMPONENT_DONOR_SCOPE</Name><Default value="false"/></Item>
      </Components></Item>
    </AttachPoints>
  </Item>
  <Item type="CWeaponInfo">
    <Name>WEAPON_OTHER</Name><Model>w_pi_other</Model>
    <Slot ref="SLOT_OTHER"/><AmmoInfo ref="AMMO_DONOR"/>
    <HumanNameHash>WT_OTHER</HumanNameHash><StatName>ST_OTHER</StatName>
  </Item>
</Infos></CWeaponInfoBlob>
"""

AMMO = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponInfoBlob><AmmoInfos>
  <!-- ammo-comment -->
  <Item type="CAmmoInfo" donor="keep">
    <Name>AMMO_DONOR</Name><Model>w_ammo_donor</Model>
    <AmmoMax value="240"/><AmmoMax50 value="120"/>
    <Explosion>NONE</Explosion><TrailFx>NULL</TrailFx><PrimedFx>NULL</PrimedFx>
    <UnknownAmmo flag="keep"><Nested value="88"/></UnknownAmmo>
  </Item>
</AmmoInfos></CWeaponInfoBlob>
"""

COMPONENTS = """<CWeaponComponentInfoBlob><Infos>
  <Item type="CWeaponComponentClipInfo">
    <Name>COMPONENT_DONOR_CLIP</Name><Model>w_at_donor_clip</Model>
    <LocName>WCT_CLIP</LocName><LocDesc>WCD_CLIP</LocDesc>
    <AttachBone>WAPClip</AttachBone><Unknown value="keep"/>
  </Item>
  <Item type="CWeaponComponentScopeInfo">
    <Name>COMPONENT_DONOR_SCOPE</Name><Model>w_at_donor_scope</Model>
    <LocName>WCT_SCOPE</LocName><LocDesc>WCD_SCOPE</LocDesc>
    <AttachBone>WAPScop</AttachBone>
  </Item>
</Infos></CWeaponComponentInfoBlob>"""

ANIMATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponAnimationsSets><Sets>
  <!-- animation-comment -->
  <Item key="DEFAULT_SET"><WeaponAnimations>
    <Item key="WEAPON_DONOR" mode="default">
      <Clip ref="clip_default"/><Unknown value="keep"/>
    </Item>
    <Item key="WEAPON_OTHER"><Clip ref="other"/></Item>
  </WeaponAnimations></Item>
  <Item><Name>FIRST_PERSON</Name><WeaponAnimations>
    <Item key="WEAPON_DONOR" mode="fp">
      <Clip ref="clip_fp"/><Payload><Item>preserve</Item></Payload>
    </Item>
  </WeaponAnimations></Item>
</Sets></CWeaponAnimationsSets>
"""

SHOP = """<?xml version="1.0" encoding="UTF-8"?>
<WeaponShopItemArray>
  <!-- shop-comment -->
  <weaponShopItems>
    <Item donor="keep">
      <lockHash>LOCK_DONOR</lockHash><nameHash>WEAPON_DONOR</nameHash>
      <cost value="900"/><ammoCost ref="120"/>
      <textLabel>WT_DONOR</textLabel><weaponDesc>WTD_DONOR</weaponDesc>
      <weaponTT>WTT_DONOR</weaponTT><weaponUppercase>WTU_DONOR</weaponUppercase>
      <id value="32"/><weaponComponents/><availableInSP value="true"/>
      <UnknownShop mode="keep"><Nested value="99"/></UnknownShop>
    </Item>
  </weaponShopItems>
</WeaponShopItemArray>
"""


def _source(root: Path) -> Path:
    source = root / "weapon-bundle-source"
    source.mkdir(parents=True)
    for name, content in (
        ("weapons.meta", WEAPONS),
        ("ammo.meta", AMMO),
        ("weaponcomponents.meta", COMPONENTS),
        ("weaponanimations.meta", ANIMATIONS),
        ("weapon_shop.meta", SHOP),
    ):
        (source / name).write_text(content, encoding="utf-8")
    stream = source / "stream"
    stream.mkdir()
    for name in (
        "w_pi_donor.ydr", "w_pi_other.ydr", "w_ammo_donor.ydr",
        "w_at_donor_clip.ydr", "w_at_donor_scope.ydr", "w_pi_bundle.ydr",
    ):
        (stream / name).write_bytes(b"asset:" + name.encode("ascii"))
    return source


def _workspace(tmp_path: Path) -> tuple[Path, WeaponAuthoringWorkspace]:
    source = _source(tmp_path)
    workspace = WeaponAuthoringWorkspace.create(
        source, tmp_path / "weapon-bundle-workspace",
    )
    return source, workspace


def _plan(workspace: WeaponAuthoringWorkspace, **overrides):
    values = {
        "weapon_name": "WEAPON_BUNDLE",
        "slot": "SLOT_BUNDLE",
        "ammo_info": "AMMO_BUNDLE",
        "model": "w_pi_bundle",
        "human_name_hash": "WT_BUNDLE",
        "stat_name": "ST_BUNDLE",
        "clone_ammo": True,
        "ammo_name": "AMMO_BUNDLE",
    }
    values.update(overrides)
    return workspace.plan_weapon_clone("WEAPON_DONOR", **values)


def test_plan_weapon_clone_is_complete_deterministic_and_hash_bound(tmp_path):
    _original, workspace = _workspace(tmp_path)
    first = _plan(workspace)
    second = _plan(workspace)

    assert first.ready is True
    assert first.donor_complete is True
    assert first.plan_sha256 == second.plan_sha256
    assert len(first.plan_sha256) == 64
    assert first.to_dict() == second.to_dict()
    assert first.revision == 0
    assert first.donor_completeness == {
        "weapon_record": True,
        "ammo_record": True,
        "animation_mappings": 2,
        "animation_sets": ["DEFAULT_SET", "FIRST_PERSON"],
        "shop_record": True,
        "attachment_links": 2,
        "component_definitions": 2,
        "authorable_source": True,
    }
    assert first.selected_sources["weapon"] == "weapons.meta"
    assert first.selected_sources["ammo"] == "ammo.meta"
    assert first.selected_sources["animation"] == "weaponanimations.meta"
    assert first.selected_sources["shop"] == "weapon_shop.meta"
    assert first.selected_sources["model_asset"] == "stream/w_pi_bundle.ydr"
    assert set(first.reused_components) == {
        "COMPONENT_DONOR_CLIP", "COMPONENT_DONOR_SCOPE",
    }
    assert {item.kind for item in first.additions} == {
        "weapon", "ammo", "attachment_link", "animation_mapping", "shop",
    }
    assert json.loads(json.dumps(first.to_dict()))["ready"] is True


def test_clone_weapon_bundle_copies_every_native_record_and_undoes(tmp_path):
    original, workspace = _workspace(tmp_path)
    original_bytes = {
        name: (original / name).read_bytes()
        for name in (
            "weapons.meta", "ammo.meta", "weaponanimations.meta",
            "weapon_shop.meta",
        )
    }
    before = {
        name: (workspace.source / name).read_bytes() for name in original_bytes
    }
    plan = _plan(workspace)

    result = workspace.clone_weapon_bundle(
        plan,
        expected_revision=0,
        expected_plan_sha256=plan.plan_sha256,
    )

    assert result.revision == 1
    assert result.subject == "WEAPON_BUNDLE"
    assert result.subject_kind == "bundle"
    assert result.affected_weapons == ("WEAPON_BUNDLE",)
    history = json.loads((result.history / "edit.json").read_text("utf-8"))
    assert history["operation"] == "weapon_bundle_clone"
    assert set(history["files"]) == set(original_bytes)
    created = workspace.manifest["created_records"]
    assert created == [item.to_dict() for item in plan.additions]
    assert "WEAPON_BUNDLE" in workspace.manifest["weapons"]

    weapon_tree = etree.parse(str(workspace.source / "weapons.meta"))
    target = weapon_tree.xpath("//Item[Name='WEAPON_BUNDLE']")[0]
    assert target.xpath("string(./Slot/@ref)") == "SLOT_BUNDLE"
    assert target.xpath("string(./AmmoInfo/@ref)") == "AMMO_BUNDLE"
    assert target.xpath("string(./UnknownWeapon/@mode)") == "keep"
    assert target.xpath("string(./UnknownWeapon/Nested/@value)") == "77"
    assert target.getprevious().xpath("string(./Name)") == "WEAPON_DONOR"
    assert [
        item.xpath("string(./Name)")
        for item in target.xpath("./AttachPoints/Item/Components/Item")
    ] == ["COMPONENT_DONOR_CLIP", "COMPONENT_DONOR_SCOPE"]

    ammo_tree = etree.parse(str(workspace.source / "ammo.meta"))
    ammo = ammo_tree.xpath("//Item[Name='AMMO_BUNDLE']")[0]
    assert ammo.xpath("string(./UnknownAmmo/@flag)") == "keep"
    assert ammo.xpath("string(./AmmoMax/@value)") == "240"
    assert ammo.getprevious().xpath("string(./Name)") == "AMMO_DONOR"

    animation_tree = etree.parse(str(workspace.source / "weaponanimations.meta"))
    mappings = animation_tree.xpath("//WeaponAnimations/Item[@key='WEAPON_BUNDLE']")
    assert len(mappings) == 2
    assert [item.get("mode") for item in mappings] == ["default", "fp"]
    assert mappings[0].xpath("string(./Unknown/@value)") == "keep"
    assert mappings[1].xpath("string(./Payload/Item)") == "preserve"

    shop_tree = etree.parse(str(workspace.source / "weapon_shop.meta"))
    shop = shop_tree.xpath("//weaponShopItems/Item[nameHash='WEAPON_BUNDLE']")[0]
    assert shop.xpath("string(./cost/@value)") == "900"
    assert shop.xpath("string(./ammoCost/@ref)") == "120"
    assert shop.xpath("string(./UnknownShop/@mode)") == "keep"
    assert shop.getprevious().xpath("string(./nameHash)") == "WEAPON_DONOR"
    assert len(result.project.components) == 2

    for name, content in original_bytes.items():
        assert (original / name).read_bytes() == content

    undone = workspace.undo(expected_revision=1)
    assert undone.revision == 2
    assert undone.subject_kind == "bundle"
    assert undone.affected_weapons == ("WEAPON_BUNDLE",)
    assert workspace.manifest["created_records"] == []
    assert "WEAPON_BUNDLE" not in workspace.manifest["weapons"]
    for name, content in before.items():
        assert (workspace.source / name).read_bytes() == content


def test_clone_weapon_bundle_rebuilds_serialized_plan_and_rejects_drift(tmp_path):
    _original, workspace = _workspace(tmp_path)
    plan = _plan(workspace)
    serialized = plan.to_dict()

    bad_sha = "0" * 64
    with pytest.raises(ValueError, match="does not match the reviewed plan"):
        workspace.clone_weapon_bundle(
            serialized, expected_revision=0, expected_plan_sha256=bad_sha,
        )
    assert workspace.revision == 0

    serialized["spec"]["stat_name"] = "ST_TAMPERED"
    with pytest.raises(ValueError, match="stale"):
        workspace.clone_weapon_bundle(
            serialized,
            expected_revision=0,
            expected_plan_sha256=plan.plan_sha256,
        )
    assert workspace.revision == 0

    fresh = _plan(workspace)
    (workspace.source / "weapon_shop.meta").write_text(
        SHOP.replace("900", "901"), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="stale"):
        workspace.clone_weapon_bundle(
            fresh,
            expected_revision=0,
            expected_plan_sha256=fresh.plan_sha256,
        )
    assert workspace.revision == 0


@pytest.mark.parametrize(
    "relative",
    ["stream/w_pi_bundle.ydr", "weaponcomponents.meta"],
)
def test_clone_plan_rejects_external_drift_in_every_selected_dependency(
    tmp_path, relative,
):
    _original, workspace = _workspace(tmp_path)
    plan = _plan(workspace)
    path = workspace.source / Path(*relative.split("/"))
    path.write_bytes(path.read_bytes() + b"\nexternal-edit")

    with pytest.raises(ValueError, match="stale"):
        workspace.clone_weapon_bundle(
            plan,
            expected_revision=0,
            expected_plan_sha256=plan.plan_sha256,
        )

    assert workspace.revision == 0
    assert not list((workspace.root / "history").iterdir())


def test_clone_plan_ignores_preexisting_malformed_unrelated_xml(tmp_path):
    _original, workspace = _workspace(tmp_path)
    unrelated = workspace.source / "unrelated.meta"
    unrelated.write_text("<Unrelated>", encoding="utf-8")

    plan = _plan(workspace)

    assert plan.ready is True
    result = workspace.clone_weapon_bundle(
        plan,
        expected_revision=0,
        expected_plan_sha256=plan.plan_sha256,
    )
    assert result.revision == 1
    assert unrelated.read_text(encoding="utf-8") == "<Unrelated>"


def test_clone_weapon_bundle_requires_revision_and_ready_plan(tmp_path):
    _original, workspace = _workspace(tmp_path)
    plan = _plan(workspace)
    workspace.update_shop("WEAPON_DONOR", {"shop.cost": "901"})
    with pytest.raises(ValueError, match="revision conflict"):
        workspace.clone_weapon_bundle(
            plan, expected_revision=0, expected_plan_sha256=plan.plan_sha256,
        )

    _other, collision_workspace = _workspace(tmp_path / "collision")
    collision = _plan(
        collision_workspace,
        weapon_name="weapon_other",
        slot="slot_other",
        human_name_hash="wt_other",
        stat_name="st_other",
    )
    assert collision.ready is False
    assert {item.field for item in collision.collisions} >= {
        "weapon_name", "slot", "human_name_hash", "stat_name",
    }
    with pytest.raises(ValueError, match="not ready"):
        collision_workspace.clone_weapon_bundle(
            collision,
            expected_revision=0,
            expected_plan_sha256=collision.plan_sha256,
        )


def test_plan_rejects_missing_assets_incomplete_and_ambiguous_donors(tmp_path):
    _original, workspace = _workspace(tmp_path)
    missing_asset = _plan(workspace, model="w_pi_missing")
    assert missing_asset.ready is False
    assert "target_model_asset_not_unique" in {
        item.code for item in missing_asset.findings
    }

    animation_path = workspace.source / "weaponanimations.meta"
    animation_path.write_text(
        ANIMATIONS.replace("WEAPON_DONOR", "WEAPON_NOT_DONOR"),
        encoding="utf-8",
    )
    incomplete = _plan(workspace)
    assert incomplete.donor_complete is False
    assert "donor_animation_missing" in {item.code for item in incomplete.findings}

    _other, ambiguous = _workspace(tmp_path / "ambiguous")
    duplicate = ambiguous.source / "other"
    duplicate.mkdir()
    (duplicate / "weaponanimations.meta").write_text(
        ANIMATIONS, encoding="utf-8",
    )
    ambiguous_plan = _plan(ambiguous)
    assert ambiguous_plan.ready is False
    assert "donor_animation_source_ambiguous" in {
        item.code for item in ambiguous_plan.findings
    }


def test_plan_rejects_opaque_sources_duplicates_and_joaat_collisions(
    tmp_path, monkeypatch,
):
    _original, workspace = _workspace(tmp_path)
    (workspace.source / "opaque.rpf").write_bytes(b"not-an-authoring-tree")
    opaque = _plan(workspace)
    assert opaque.ready is False
    assert "opaque_authoring_source" in {item.code for item in opaque.findings}

    _other, duplicate = _workspace(tmp_path / "duplicate")
    extra = duplicate.source / "duplicate.meta"
    extra.write_text(WEAPONS, encoding="utf-8")
    duplicated = _plan(duplicate)
    assert duplicated.donor_complete is False
    assert "donor_weapon_duplicated" in {
        item.code for item in duplicated.findings
    }

    _third, hashed = _workspace(tmp_path / "hashed")
    monkeypatch.setattr(weapon_authoring, "joaat", lambda _value: 0x12345678)
    collision = _plan(hashed)
    assert collision.ready is False
    assert any(item.reason == "joaat" for item in collision.collisions)
    assert all(item.hash == "0x12345678" for item in collision.collisions)


def test_donor_duplicate_detection_is_exact_and_attachment_links_are_unambiguous(
    tmp_path,
):
    _original, prefix = _workspace(tmp_path / "prefix")
    unrelated = """<CWeaponInfoBlob><Infos>
      <Item><Name>WEAPON_DONOR_EXTRA</Name><Model>w_extra</Model>
        <Slot ref="SLOT_EXTRA"/><AmmoInfo ref="AMMO_DONOR"/>
        <HumanNameHash>WT_EXTRA</HumanNameHash><StatName>ST_EXTRA</StatName>
      </Item>
      <Item><Name>WEAPON_DONOR_EXTRA</Name><Model>w_extra</Model>
        <Slot ref="SLOT_EXTRA"/><AmmoInfo ref="AMMO_DONOR"/>
        <HumanNameHash>WT_EXTRA</HumanNameHash><StatName>ST_EXTRA</StatName>
      </Item>
    </Infos></CWeaponInfoBlob>"""
    (prefix.source / "unrelated.meta").write_text(unrelated, encoding="utf-8")
    prefix_plan = _plan(prefix)
    assert prefix_plan.ready is True
    assert "donor_weapon_duplicated" not in {
        item.code for item in prefix_plan.findings
    }

    _other, links = _workspace(tmp_path / "links")
    path = links.source / "weapons.meta"
    tree = etree.parse(str(path))
    components = tree.xpath(
        "//Item[Name='WEAPON_DONOR']/AttachPoints/Item/Components",
    )[0]
    first = components[0]
    components.insert(1, etree.fromstring(etree.tostring(first)))
    tree.write(str(path), encoding="utf-8", xml_declaration=True)
    link_plan = _plan(links)
    assert link_plan.ready is False
    assert "donor_attachment_xml_ambiguous" in {
        item.code for item in link_plan.findings
    }


def test_plan_rejects_component_offers_the_importer_cannot_resolve(tmp_path):
    _original, workspace = _workspace(tmp_path)
    path = workspace.source / "weapons.meta"
    tree = etree.parse(str(path))
    components = tree.xpath(
        "//Item[Name='WEAPON_DONOR']/AttachPoints/Item/Components",
    )[0]
    malformed = etree.fromstring(
        b"<Item><Name>NOT_A_COMPONENT</Name><Default value='false'/>"
        b"<Unknown mode='preserve'/></Item>"
    )
    components.append(malformed)
    tree.write(str(path), encoding="utf-8", xml_declaration=True)

    plan = _plan(workspace)

    assert plan.ready is False
    assert "donor_component_offer_malformed" in {
        item.code for item in plan.findings
    }


def test_clone_weapon_bundle_supports_explicit_existing_ammo_reuse(tmp_path):
    _original, workspace = _workspace(tmp_path)
    plan = _plan(
        workspace,
        ammo_info="AMMO_DONOR",
        clone_ammo=False,
        ammo_name=None,
    )
    assert plan.ready is True
    assert not any(item.kind == "ammo" for item in plan.additions)

    result = workspace.clone_weapon_bundle(
        plan,
        expected_revision=0,
        expected_plan_sha256=plan.plan_sha256,
    )
    assert result.revision == 1
    assert len([
        item for item in result.project.ammo if item.name == "AMMO_DONOR"
    ]) == 1
    assert result.project.weapon("WEAPON_BUNDLE").ammo_info == "AMMO_DONOR"


def test_bundle_post_commit_failure_rolls_back_all_sources_and_manifest(
    tmp_path, monkeypatch,
):
    _original, workspace = _workspace(tmp_path)
    plan = _plan(workspace)
    paths = {
        name: (workspace.source / name).read_bytes()
        for name in (
            "weapons.meta", "ammo.meta", "weaponanimations.meta",
            "weapon_shop.meta",
        )
    }
    manifest = workspace.manifest_path.read_bytes()

    def reject(*_args, **_kwargs):
        raise RuntimeError("forced bundle verification failure")

    monkeypatch.setattr(workspace, "_verify_weapon_bundle_clone", reject)
    with pytest.raises(RuntimeError, match="forced bundle"):
        workspace.clone_weapon_bundle(
            plan,
            expected_revision=0,
            expected_plan_sha256=plan.plan_sha256,
        )

    assert workspace.revision == 0
    assert workspace.manifest_path.read_bytes() == manifest
    assert not list((workspace.root / "history").iterdir())
    for name, content in paths.items():
        assert (workspace.source / name).read_bytes() == content


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"weapon_name": "BUNDLE"}, "must begin with WEAPON_"),
        ({"slot": "BUNDLE"}, "must begin with SLOT_"),
        ({"ammo_info": "AMMO_DIFFERENT"}, "must be identical"),
        ({"clone_ammo": False, "ammo_name": "AMMO_BUNDLE"}, "only valid"),
    ],
)
def test_plan_weapon_clone_requires_explicit_native_identities(
    tmp_path, overrides, message,
):
    _original, workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match=message):
        _plan(workspace, **overrides)
