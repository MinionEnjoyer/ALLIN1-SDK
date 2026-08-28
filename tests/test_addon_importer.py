from __future__ import annotations

import json
import subprocess
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import allin1_sdk.addon_importer as importer
from allin1_sdk.addon_importer import (
    AddonDraftBuilder,
    AddonPackageInspector,
    PackageAssetReader,
    asset_category,
    asset_preview_kind,
    decode_text_preview,
    hex_preview,
)
from allin1_sdk.addon_sdk import AddonLinker, AddonManifest
from allin1_sdk.cli import main
from allin1_sdk.vehicle_project import VehicleProjectResolver


WEAPONS_META = """<?xml version="1.0" encoding="UTF-8"?>
<CWeaponInfoBlob>
  <Infos>
    <Item>
      <Name>WEAPON_TEST_SMOKE</Name>
      <Slot>SLOT_TEST_SMOKE</Slot>
      <AmmoInfo ref="AMMO_TEST_SMOKE" />
      <Model>w_ex_grenadesmoke</Model>
      <HumanNameHash>WT_TESTSMK</HumanNameHash>
      <StatName>TESTSMOKE</StatName>
    </Item>
    <Item>
      <Name>AMMO_TEST_SMOKE</Name>
      <Model>w_ex_grenadesmoke</Model>
      <AmmoMax value="5" />
      <AmmoMax50 value="5" />
      <Explosion>NONE</Explosion>
      <TrailFx />
      <PrimedFx />
    </Item>
  </Infos>
</CWeaponInfoBlob>
"""

ANIMATIONS_META = """<WeaponAnimations>
  <Item key="WEAPON_TEST_SMOKE"><Unarmed>default</Unarmed></Item>
</WeaponAnimations>
"""

SHOP_META = """<Shop><Item><nameHash>WEAPON_TEST_SMOKE</nameHash></Item></Shop>"""

VEHICLES_META = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
  <modelName>devcar</modelName><txdName>devcar</txdName>
  <handlingId>DEVCAR</handlingId><gameName>DEVCAR</gameName>
  <vehicleMakeName>DEV</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
  <layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
  <vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""

HANDLING_META = """<CHandlingDataMgr><HandlingData><Item type="CHandlingData">
  <handlingName>DEVCAR</handlingName>
</Item></HandlingData></CHandlingDataMgr>"""

VARIATIONS_META = """<CVehicleModelInfoVariation><variationData><Item>
  <modelName>devcar</modelName><kits><Item>123_devcar_modkit</Item></kits>
  <lightSettings value="7" />
</Item></variationData></CVehicleModelInfoVariation>"""

CARCOLS_META = """<CVehicleModelInfoVarGlobal><Kits><Item>
  <kitName>123_devcar_modkit</kitName><id value="123" />
  <visibleMods>
    <Item><modelName>devcar_bumper_a</modelName></Item>
    <Item><modelName>devcar_bumper_missing</modelName></Item>
  </visibleMods>
</Item></Kits></CVehicleModelInfoVarGlobal>"""

CONTENT_XML = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles>
  <Item><filename>dlc_devcar:/common/data/vehicles.meta</filename></Item>
  <Item><filename>dlc_devcar:/common/data/handling.meta</filename></Item>
</dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""

WEAPON_WITH_COMPONENT_META = """<CWeaponInfoBlob><Infos><Item>
  <Name>WEAPON_TEST_COMPONENT</Name><Slot>SLOT_TEST_COMPONENT</Slot>
  <AmmoInfo ref="AMMO_TEST_COMPONENT"/><Model>w_pi_test_component</Model>
  <HumanNameHash>WT_TESTCMP</HumanNameHash><StatName>TESTCMP</StatName>
  <AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components><Item>
    <Name>COMPONENT_TEST_CLIP_01</Name><Default value="true"/>
  </Item></Components></Item></AttachPoints>
</Item><Item><Name>AMMO_TEST_COMPONENT</Name><Model>w_pi_test_component</Model>
  <AmmoMax value="120"/><AmmoMax50 value="60"/><Explosion>NONE</Explosion>
  <TrailFx/><PrimedFx/>
</Item></Infos></CWeaponInfoBlob>"""

WEAPON_COMPONENT_META = """<CWeaponComponentInfoBlob><Infos>
  <Item type="CWeaponComponentClipInfo"><Name>COMPONENT_TEST_CLIP_01</Name>
    <Model>w_at_test_clip</Model><LocName>WCT_CLIP1</LocName>
    <LocDesc>WCD_CLIP1</LocDesc><AttachBone>WAPClip</AttachBone>
  </Item>
</Infos></CWeaponComponentInfoBlob>"""

PEDS_META = """<CPedModelInfo__InitDataList><InitDatas><Item>
  <Name>ig_testdeveloper</Name><Pedtype>CIVMALE</Pedtype>
  <ModelType>STANDARD</ModelType><PropsName>ig_testdeveloper_p</PropsName>
  <ClipDictionaryName>move_m@generic</ClipDictionaryName>
  <ExpressionSetName>expr_set_ambient_male</ExpressionSetName>
  <MovementClipSet>move_m@business@a</MovementClipSet>
  <CreatureMetadataName>MALE</CreatureMetadataName>
</Item></InitDatas></CPedModelInfo__InitDataList>"""


def _write_loose_package(root: Path) -> Path:
    package = root / "test_smoke"
    package.mkdir()
    (package / "weapons.meta").write_text(WEAPONS_META, encoding="utf-8")
    (package / "weaponanimations.meta").write_text(
        ANIMATIONS_META, encoding="utf-8"
    )
    (package / "weapon_shop.meta").write_text(SHOP_META, encoding="utf-8")
    (package / "stream").mkdir()
    (package / "stream" / "w_ex_test.ydr").write_bytes(b"model")
    return package


def _write_oiv(path: Path, *, assembly: bool = True) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        if assembly:
            archive.writestr("assembly.xml", "<package />")
        archive.writestr("content/weapons.meta", WEAPONS_META)
        archive.writestr("content/weaponanimations.meta", ANIMATIONS_META)
        archive.writestr("content/weapon_shop.meta", SHOP_META)
        archive.writestr("content/dlcpacks/test/dlc.rpf", b"opaque")
    return path


def _write_vehicle_package(root: Path) -> Path:
    package = root / "dev_vehicle"
    package.mkdir()
    for name, content in {
        "vehicles.meta": VEHICLES_META,
        "handling.meta": HANDLING_META,
        "carvariations.meta": VARIATIONS_META,
        "carcols.meta": CARCOLS_META,
        "content.xml": CONTENT_XML,
    }.items():
        (package / name).write_text(content, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    (stream / "devcar.yft").write_bytes(b"fragment")
    (stream / "devcar.ytd").write_bytes(b"textures")
    (stream / "devcar_bumper_a.yft").write_bytes(b"tuning fragment")
    return package


def _write_weapon_component_package(root: Path) -> Path:
    package = root / "component_weapon"
    package.mkdir()
    (package / "weapons.meta").write_text(
        WEAPON_WITH_COMPONENT_META, encoding="utf-8",
    )
    (package / "weaponcomponents.meta").write_text(
        WEAPON_COMPONENT_META, encoding="utf-8",
    )
    (package / "weaponanimations.meta").write_text(
        "<WeaponAnimations><Item key=\"WEAPON_TEST_COMPONENT\"/></WeaponAnimations>",
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<Shop><Item><nameHash>WEAPON_TEST_COMPONENT</nameHash></Item></Shop>",
        encoding="utf-8",
    )
    return package


def _write_ped_package(root: Path) -> Path:
    package = root / "developer_ped"
    package.mkdir()
    (package / "peds.meta").write_text(PEDS_META, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    (stream / "ig_testdeveloper.ydd").write_bytes(b"drawable")
    (stream / "ig_testdeveloper.ytd").write_bytes(b"textures")
    return package


def test_loose_package_scan_generates_a_linkable_review_draft(tmp_path):
    package = _write_loose_package(tmp_path)
    scan = AddonPackageInspector().inspect(package)

    assert scan.valid
    assert scan.source_kind == "folder"
    assert len(scan.entries) == 4
    assert [item.name for item in scan.weapons] == ["WEAPON_TEST_SMOKE"]
    assert [item.name for item in scan.ammo] == ["AMMO_TEST_SMOKE"]
    assert scan.animation_weapons == ("WEAPON_TEST_SMOKE",)
    assert scan.shop_weapons == ("WEAPON_TEST_SMOKE",)
    assert asdict(scan.weapon_animation_records[0]) == {
        "source": "weaponanimations.meta",
        "weapon_name": "WEAPON_TEST_SMOKE",
        "field_name": "key",
        "representation": "attribute",
        "set_name": "",
        "set_ordinal": 0,
        "ordinal": 0,
    }
    assert asdict(scan.weapon_shop_records[0]) == {
        "source": "weapon_shop.meta",
        "weapon_name": "WEAPON_TEST_SMOKE",
        "field_name": "nameHash",
        "representation": "text",
        "ordinal": 0,
    }
    assert {item.code for item in scan.findings} == {
        "edition_compatibility_unresolved",
    }
    assert scan.edition_tag == "Unresolved"

    destination = package / "addon.json"
    written = AddonDraftBuilder().build(scan).write(destination)
    manifest = AddonManifest.load(written, source_root=package)
    report = AddonLinker().link(manifest)

    assert manifest.addon_id == "imported.test_smoke"
    assert {node.kind for node in manifest.nodes} >= {
        "package", "weapon", "ammo", "animation", "storefront"
    }
    assert len(report.references) == 3
    assert all(item.valid for item in report.references)
    assert not report.valid
    incomplete = [
        item for item in report.issues
        if item.code == "incomplete_weapon_integration"
    ]
    assert len(incomplete) == 1
    assert "uses_label" in incomplete[0].message
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == 1


def test_weapon_component_links_are_retained_for_workbench_and_draft(tmp_path):
    scan = AddonPackageInspector().inspect(_write_weapon_component_package(tmp_path))

    assert [item.name for item in scan.weapon_components] == [
        "COMPONENT_TEST_CLIP_01"
    ]
    assert len(scan.weapon_component_links) == 1
    link = scan.weapon_component_links[0]
    assert link.weapon_name == "WEAPON_TEST_COMPONENT"
    assert link.component_name == "COMPONENT_TEST_CLIP_01"
    assert link.attach_bone == "WAPClip"
    assert link.default

    draft = AddonDraftBuilder().build(scan).manifest
    nodes = {item["id"]: item for item in draft["nodes"]}
    assert nodes["weapon-components.imported"]["fields"]["WeaponNames"] == [
        "WEAPON_TEST_COMPONENT"
    ]
    assert any(
        item["relationship"] == "offers_components"
        for item in draft["references"]
    )


def test_weapon_records_are_deduplicated_case_insensitively(tmp_path):
    package = _write_weapon_component_package(tmp_path)
    (package / "duplicate-weapons.meta").write_text(
        WEAPON_WITH_COMPONENT_META
        .replace("WEAPON_TEST_COMPONENT", "WEAPON_test_COMPONENT")
        .replace("AMMO_TEST_COMPONENT", "AMMO_test_COMPONENT"),
        encoding="utf-8",
    )
    (package / "duplicate-components.meta").write_text(
        WEAPON_COMPONENT_META.replace(
            "COMPONENT_TEST_CLIP_01", "COMPONENT_test_CLIP_01",
        ),
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert len(scan.weapons) == 1
    assert len(scan.ammo) == 1
    assert len(scan.weapon_components) == 1
    duplicate_findings = [
        item for item in scan.findings if item.code == "duplicate_record"
    ]
    assert len(duplicate_findings) >= 3


def test_weapon_registration_sources_retain_syntax_and_duplicate_evidence(
    tmp_path,
):
    package = _write_loose_package(tmp_path)
    (package / "weaponanimations.meta").write_text(
        "<WeaponAnimations>"
        '<Item key="WEAPON_TEST_SMOKE"/>'
        '<Item key="WEAPON_test_SMOKE"/>'
        "</WeaponAnimations>",
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<Shop><Item>"
        "<nameHash>WEAPON_TEST_SMOKE</nameHash>"
        '<nameHash value="WEAPON_test_SMOKE"/>'
        '<weaponName ref="WEAPON_TEST_SMOKE"/>'
        "</Item></Shop>",
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    # Compatibility projections are stable and compare game hashes by case.
    assert scan.animation_weapons == ("WEAPON_TEST_SMOKE",)
    assert scan.shop_weapons == ("WEAPON_TEST_SMOKE",)

    # Source records retain every ambiguous target so authoring can fail closed.
    assert [item.ordinal for item in scan.weapon_animation_records] == [0, 1]
    assert {
        (item.field_name, item.representation, item.ordinal)
        for item in scan.weapon_shop_records
    } == {
        ("nameHash", "text", 0),
        ("nameHash", "value", 1),
        ("weaponName", "ref", 2),
    }
    assert {
        item.code for item in scan.findings
    } >= {
        "duplicate_weapon_animation_record",
        "duplicate_weapon_shop_record",
    }


def test_weapon_animation_duplicates_are_scoped_to_the_native_set(tmp_path):
    package = _write_loose_package(tmp_path)
    (package / "weaponanimations.meta").write_text(
        "<CWeaponAnimationsSets><WeaponAnimationsSets>"
        '<Item key="Ballistic"><WeaponAnimations>'
        '<Item key="WEAPON_TEST_SMOKE"/>'
        '<Item key="WEAPON_test_SMOKE"/>'
        "</WeaponAnimations></Item>"
        '<Item key="FirstPerson"><WeaponAnimations>'
        '<Item key="WEAPON_TEST_SMOKE"/>'
        "</WeaponAnimations></Item>"
        "</WeaponAnimationsSets></CWeaponAnimationsSets>",
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert [
        (item.set_name, item.set_ordinal, item.ordinal)
        for item in scan.weapon_animation_records
    ] == [
        ("Ballistic", 0, 0),
        ("Ballistic", 0, 1),
        ("FirstPerson", 1, 2),
    ]
    duplicate_findings = [
        item for item in scan.findings
        if item.code == "duplicate_weapon_animation_record"
    ]
    assert len(duplicate_findings) == 1
    assert scan.animation_weapons == ("WEAPON_TEST_SMOKE",)


def test_native_weapon_shop_records_ignore_nested_and_unowned_identities(tmp_path):
    package = _write_loose_package(tmp_path)
    (package / "shop_weapon.meta").write_text(
        "<WeaponShopItemArray><weaponShopItems>"
        "<Item><nameHash>WEAPON_TEST_SMOKE</nameHash>"
        "<weaponComponents><Item>"
        "<weaponName>WEAPON_NESTED_COMPONENT_DECOY</weaponName>"
        "</Item></weaponComponents></Item>"
        "</weaponShopItems>"
        "<nameHash>WEAPON_UNOWNED_DECOY</nameHash>"
        "</WeaponShopItemArray>",
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert scan.shop_weapons == ("WEAPON_TEST_SMOKE",)
    assert [
        (item.weapon_name, item.source)
        for item in scan.weapon_shop_records
        if item.source == "shop_weapon.meta"
    ] == [("WEAPON_TEST_SMOKE", "shop_weapon.meta")]


def test_game_identifiers_are_case_insensitive_without_prefix_collisions(tmp_path):
    package = tmp_path / "cased-identities"
    package.mkdir()
    (package / "weapons.meta").write_text(
        "<CWeaponInfoBlob><Infos>"
        "<Item><Name>weapon_case</Name><Slot>SLOT_CASE</Slot>"
        '<AmmoInfo ref="">ammo_case</AmmoInfo><Model>w_case</Model>'
        "<HumanNameHash/><StatName>CASE</StatName></Item>"
        "<Item><Name>WEAPON_CASE_MK2</Name><Slot>SLOT_CASE_MK2</Slot>"
        '<AmmoInfo ref="AMMO_CASE_MK2"/><Model>w_case_mk2</Model>'
        "<HumanNameHash>WT_CASE2</HumanNameHash></Item>"
        "<Item><Name>ammo_case</Name><AmmoMax value=\"20\"/></Item>"
        "<Item><Name>AMMO_CASE_MK2</Name><AmmoMax value=\"30\"/></Item>"
        "</Infos></CWeaponInfoBlob>",
        encoding="utf-8",
    )
    (package / "weaponcomponents.meta").write_text(
        "<CWeaponComponentInfoBlob><Infos><Item>"
        "<Name>component_case_clip</Name><Model>w_case_clip</Model>"
        "</Item></Infos></CWeaponComponentInfoBlob>",
        encoding="utf-8",
    )
    (package / "weaponanimations.meta").write_text(
        "<WeaponAnimations><Item key=\"weapon_case\"/>"
        "<Item key=\"WEAPON_CASE_MK2\"/></WeaponAnimations>",
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<Shop><Item><nameHash>weapon_case</nameHash></Item>"
        "<Item><nameHash>WEAPON_CASE_MK2</nameHash></Item></Shop>",
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert [item.name for item in scan.weapons] == [
        "weapon_case", "WEAPON_CASE_MK2",
    ]
    assert [item.ammo_info for item in scan.weapons] == [
        "ammo_case", "AMMO_CASE_MK2",
    ]
    assert [item.name for item in scan.ammo] == ["ammo_case", "AMMO_CASE_MK2"]
    assert [item.name for item in scan.weapon_components] == [
        "component_case_clip",
    ]
    codes = {item.code for item in scan.findings}
    assert "duplicate_record" not in codes
    assert "ammo_definition_not_found" not in codes
    assert "animation_mapping_not_found" not in codes
    assert "storefront_mapping_not_found" not in codes


def test_nested_managed_manifest_is_authoritative_for_edition_detection(tmp_path):
    archive = tmp_path / "managed.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "source-repo/package/MOD.TOML",
            'schema_version = 1\nid = "test.managed"\n'
            'name = "Managed"\nversion = "1.0.0"\ntype = "config"\n'
            'editions = ["enhanced"]\n',
        )
        package.writestr(
            "source-repo/package/README.md",
            "Legacy is not supported. Built for GTA V Enhanced.",
        )
        package.writestr("source-repo/package/settings.ini", "enabled=true")

    scan = AddonPackageInspector().inspect(archive)

    assert scan.edition_hints == ("enhanced",)
    assert scan.edition_tag == "Enhanced"
    codes = {item.code for item in scan.findings}
    assert "edition_compatibility_unresolved" not in codes
    assert "managed_manifest_not_found" not in codes


def test_multiple_managed_manifests_are_advisory_and_identify_the_path(tmp_path):
    archive = tmp_path / "multi-package.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("one/mod.toml", 'editions = ["legacy"]')
        package.writestr("two/MOD.TOML", 'editions = ["enhanced"]')

    scan = AddonPackageInspector().inspect(archive)

    finding = next(
        item for item in scan.findings
        if item.code == "managed_manifest_ambiguous"
    )
    assert finding.severity == "warning"
    assert finding.path == "one/mod.toml"
    assert "two/MOD.TOML" in finding.message
    assert scan.valid
    assert "managed_manifest_not_found" not in {
        item.code for item in scan.findings
    }


def test_unrelated_loose_assets_do_not_flag_partial_vehicle_or_ped_metadata(
    tmp_path,
):
    package = tmp_path / "partial-mixed-package"
    package.mkdir()
    (package / "vehicles.meta").write_text(VEHICLES_META, encoding="utf-8")
    (package / "handling.meta").write_text(HANDLING_META, encoding="utf-8")
    (package / "carvariations.meta").write_text(VARIATIONS_META, encoding="utf-8")
    (package / "peds.meta").write_text(PEDS_META, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    (stream / "unrelated_world_prop.ytd").write_bytes(b"texture")
    (stream / "unrelated_vehicle.yft").write_bytes(b"fragment")

    scan = AddonPackageInspector().inspect(package)

    codes = {item.code for item in scan.findings}
    assert "vehicle_model_asset_not_found" not in codes
    assert "vehicle_texture_asset_not_found" not in codes
    assert "ped_model_asset_not_found" not in codes
    assert "ped_texture_asset_not_found" not in codes


def test_loose_assets_for_one_vehicle_do_not_scope_another_vehicles_tuning(
    tmp_path,
):
    package = tmp_path / "mixed-vehicle-storage"
    package.mkdir()
    other_vehicle = (
        "<Item><modelName>othercar</modelName><txdName>othercar</txdName>"
        "<handlingId>OTHERCAR</handlingId><gameName>OTHERCAR</gameName>"
        "<vehicleMakeName>OTHER</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>"
        "<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>"
        "<vehicleClass>VC_SPORT</vehicleClass></Item>"
    )
    (package / "vehicles.meta").write_text(
        VEHICLES_META.replace("</InitDatas>", other_vehicle + "</InitDatas>"),
        encoding="utf-8",
    )
    (package / "handling.meta").write_text(
        HANDLING_META.replace(
            "</HandlingData>",
            "<Item><handlingName>OTHERCAR</handlingName></Item></HandlingData>",
        ),
        encoding="utf-8",
    )
    (package / "carvariations.meta").write_text(
        VARIATIONS_META.replace(
            "</variationData>",
            "<Item><modelName>othercar</modelName><kits/></Item></variationData>",
        ),
        encoding="utf-8",
    )
    (package / "carcols.meta").write_text(CARCOLS_META, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    (stream / "othercar.yft").write_bytes(b"fragment")
    (stream / "othercar.ytd").write_bytes(b"texture")

    scan = AddonPackageInspector().inspect(package)

    assert "tuning_model_asset_not_found" not in {
        item.code for item in scan.findings
    }


def test_nested_archives_and_known_non_content_packages_are_informational(tmp_path):
    archive = tmp_path / "outer.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("optional/settings.ini", "enabled=true")
        package.writestr("optional/extras.zip", b"nested")

    scan = AddonPackageInspector().inspect(archive)

    nested = next(
        item for item in scan.findings
        if item.code == "nested_package_not_inspected"
    )
    assert (nested.severity, nested.path) == (
        "info", "optional/extras.zip",
    )
    no_records = next(
        item for item in scan.findings if item.code == "no_content_records"
    )
    assert no_records.severity == "info"


def test_explicit_edition_exclusion_is_not_counted_as_support(tmp_path):
    package = tmp_path / "enhanced-only"
    package.mkdir()
    (package / "README.txt").write_text(
        "Legacy is not supported. Built for GTA V Enhanced.", encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert scan.edition_hints == ("enhanced",)
    assert scan.edition_tag == "Enhanced"


def test_obsolete_edition_exclusion_does_not_hide_later_support(tmp_path):
    package = tmp_path / "support-added"
    package.mkdir()
    (package / "README.txt").write_text(
        "Version 1: Legacy was unsupported.\n"
        "Version 2: Legacy support added; Enhanced remains supported.",
        encoding="utf-8",
    )

    scan = AddonPackageInspector().inspect(package)

    assert scan.edition_hints == ("legacy", "enhanced")
    assert scan.edition_tag == "Legacy + Enhanced"


def test_ped_packages_are_discovered_and_linked_into_imported_drafts(tmp_path):
    scan = AddonPackageInspector().inspect(_write_ped_package(tmp_path))

    assert scan.package_kinds == ("ped_addon",)
    assert [item.name for item in scan.peds] == ["ig_testdeveloper"]
    ped = scan.peds[0]
    assert ped.ped_type == "CIVMALE"
    assert ped.props_name == "ig_testdeveloper_p"
    assert not any(
        item.code in {"ped_model_asset_not_found", "ped_texture_asset_not_found"}
        for item in scan.findings
    )

    draft = AddonDraftBuilder().build(scan).manifest
    nodes = {item["id"]: item for item in draft["nodes"]}
    assert nodes["peds.imported"]["fields"]["Names"] == ["ig_testdeveloper"]
    assert any(
        item["relationship"] == "streams_ped_assets"
        for item in draft["references"]
    )


def test_oiv_scan_requires_assembly_and_never_assigns_archive_member_sources(tmp_path):
    good = _write_oiv(tmp_path / "good.oiv")
    scan = AddonPackageInspector().inspect(good)
    assert scan.valid
    assert scan.source_kind == "oiv"
    assert any(item.code == "opaque_rpf" for item in scan.findings)

    manifest_path = AddonDraftBuilder().build(scan).write(tmp_path / "draft.json")
    manifest = AddonManifest.load(manifest_path)
    assert all(node.source is None for node in manifest.nodes)
    assert all(step.source is None for step in manifest.install_steps)

    missing = AddonPackageInspector().inspect(
        _write_oiv(tmp_path / "missing.oiv", assembly=False)
    )
    assert not missing.valid
    assert "oiv_assembly_missing" in {item.code for item in missing.findings}


def test_vehicle_package_links_metadata_and_exposes_missing_tuning_asset(tmp_path):
    package = _write_vehicle_package(tmp_path)
    scan = AddonPackageInspector().inspect(package)
    codes = {item.code for item in scan.findings}

    assert len(scan.vehicles) == 1
    assert scan.vehicles[0].model_name == "devcar"
    assert [item.name for item in scan.handlings] == ["DEVCAR"]
    assert scan.variations[0].kits == ("123_devcar_modkit",)
    assert scan.kits[0].model_names == (
        "devcar_bumper_a", "devcar_bumper_missing",
    )
    assert len(scan.registrations) == 1
    assert "tuning_model_asset_not_found" in codes
    assert "vehicle_registration_not_found" not in codes

    manifest_path = AddonDraftBuilder().build(scan).write(package / "addon.json")
    report = AddonLinker().link(AddonManifest.load(manifest_path, source_root=package))
    assert {node.kind for node in report.manifest.nodes} >= {
        "vehicle", "handling", "vehicle_variation", "tuning", "streaming",
        "dlc_registration",
    }
    tuning_link = next(
        item for item in report.references if item.reference.relationship ==
        "streams_tuning_assets"
    )
    assert not tuning_link.valid
    assert "devcar_bumper_missing" in str(tuning_link.source_value)


@pytest.mark.parametrize(
    "member",
    ["../evil.meta", "folder/../../evil.meta", "/absolute.meta", "C:/evil.meta"],
)
def test_archive_member_paths_cannot_escape_the_package(tmp_path, member):
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, "<root />")
    with pytest.raises(ValueError, match="Unsafe package member path"):
        AddonPackageInspector().inspect(path)


def test_archive_accepts_a_single_dot_prefix(tmp_path):
    path = tmp_path / "safe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("./metadata.xml", "<root />")
    scan = AddonPackageInspector().inspect(path)
    assert scan.entries[0].path == "metadata.xml"


def test_archive_rejects_duplicate_member_paths_case_insensitively(tmp_path):
    path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("content/WEAPONS.META", "<root />")
        archive.writestr("content/weapons.meta", "<root />")
    scan = AddonPackageInspector().inspect(path)
    assert not scan.valid
    assert "duplicate_member" in {item.code for item in scan.findings}


def test_invalid_zip_and_unsupported_source_are_rejected(tmp_path):
    invalid = tmp_path / "invalid.oiv"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="Invalid OIV archive"):
        AddonPackageInspector().inspect(invalid)

    text = tmp_path / "package.txt"
    text.write_text("content", encoding="utf-8")
    with pytest.raises(ValueError, match=r"DLC folder, direct \.rpf, or an \.oiv/\.zip/\.rar/\.7z"):
        AddonPackageInspector().inspect(text)


def test_external_archive_inventory_and_reads_are_bounded(tmp_path, monkeypatch):
    archive = tmp_path / "vehicle.rar"
    archive.write_bytes(b"synthetic archive marker")
    payload = VEHICLES_META.encode("utf-8")
    monkeypatch.setattr(
        importer, "_list_external_archive",
        lambda _path: [("metadata/vehicles.meta", len(payload))],
    )
    monkeypatch.setattr(
        importer, "_read_external_archive_member",
        lambda _path, member, limit: (payload[:limit], len(payload) > limit),
    )

    scan = AddonPackageInspector().inspect(archive)
    assert scan.source_kind == "rar"
    assert [item.model_name for item in scan.vehicles] == ["devcar"]
    content = PackageAssetReader(archive).read("METADATA/VEHICLES.META", limit=64)
    assert content.data == payload[:64]
    assert content.truncated


def test_external_archive_tool_and_listing_contracts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        importer.shutil, "which",
        lambda name: "C:/Windows/System32/tar.exe" if name == "tar" else None,
    )
    assert importer._external_archive_tool().endswith("tar.exe")
    monkeypatch.setattr(importer.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="requires bsdtar"):
        importer._external_archive_tool()

    archive = tmp_path / "package.rar"
    listing = (
        b"-rw-r--r--  0 user group       12 Jan 01 00:00 folder/file name.meta\n"
        b"drwxr-xr-x  0 user group        0 Jan 01 00:00 folder/\n"
    )
    monkeypatch.setattr(importer, "_run_archive_command", lambda *args, **kwargs: listing)
    assert importer._list_external_archive(archive) == [
        ("folder/file name.meta", 12),
    ]
    monkeypatch.setattr(importer, "_run_archive_command", lambda *args, **kwargs: b"bad\n")
    with pytest.raises(ValueError, match="unsupported format"):
        importer._list_external_archive(archive)
    bad_size = b"-rw-r--r-- 0 user group nope Jan 01 00:00 file.meta\n"
    monkeypatch.setattr(importer, "_run_archive_command", lambda *args, **kwargs: bad_size)
    with pytest.raises(ValueError, match="invalid file size"):
        importer._list_external_archive(archive)


def test_external_archive_command_and_streaming_errors_are_clear(tmp_path, monkeypatch):
    archive = tmp_path / "package.rar"
    monkeypatch.setattr(importer, "_external_archive_tool", lambda: "tar")
    monkeypatch.setattr(
        importer.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, b"ok", b""),
    )
    assert importer._run_archive_command(["-tf", str(archive)]) == b"ok"
    monkeypatch.setattr(
        importer.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 2, b"", b"corrupt archive",
        ),
    )
    with pytest.raises(ValueError, match="corrupt archive"):
        importer._run_archive_command(["-tf", str(archive)])

    class FakeStdout:
        def __init__(self, data: bytes):
            self.data = data

        def read(self, amount: int) -> bytes:
            return self.data[:amount]

    class FakeProcess:
        def __init__(self, data: bytes, returncode: int = 0, stderr: bytes = b""):
            self.stdout = FakeStdout(data)
            self.returncode = returncode
            self.stderr = stderr
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def communicate(self, timeout=None):
            return b"", self.stderr

    normal = FakeProcess(b"metadata")
    monkeypatch.setattr(importer.subprocess, "Popen", lambda *args, **kwargs: normal)
    assert importer._read_external_archive_member(
        archive, "file.meta", limit=20,
    ) == (b"metadata", False)

    truncated = FakeProcess(b"123456")
    monkeypatch.setattr(importer.subprocess, "Popen", lambda *args, **kwargs: truncated)
    assert importer._read_external_archive_member(
        archive, "file.meta", limit=3,
    ) == (b"123", True)
    assert truncated.terminated

    failed = FakeProcess(b"", returncode=2, stderr=b"member missing")
    monkeypatch.setattr(importer.subprocess, "Popen", lambda *args, **kwargs: failed)
    with pytest.raises(ValueError, match="member missing"):
        importer._read_external_archive_member(archive, "missing.meta", limit=3)

    captured: list[list[str]] = []

    def capture_pattern(command, **kwargs):
        captured.append(command)
        return FakeProcess(b"readme")

    monkeypatch.setattr(importer.subprocess, "Popen", capture_pattern)
    importer._read_external_archive_member(
        archive, "ReadMe [ENG]*?.txt", limit=20,
    )
    assert captured[0][-1] == "ReadMe [[]ENG][*][?].txt"


def test_vehicle_scan_reports_structural_gaps_and_resource_manifest(tmp_path):
    package = tmp_path / "partial_vehicle"
    package.mkdir()
    (package / "vehicles.meta").write_text(
        VEHICLES_META.replace("<handlingId>DEVCAR</handlingId>", "<handlingId />"),
        encoding="utf-8",
    )
    (package / "carvariations.meta").write_text(
        VARIATIONS_META.replace("123_devcar_modkit", "missing_modkit"),
        encoding="utf-8",
    )
    (package / "__resource.lua").write_text(
        "files { 'vehicles.meta', 'missing.meta' }",
        encoding="utf-8",
    )
    # A matching high-detail fragment proves this vehicle is being shipped as
    # loose assets, so the absent primary YFT and texture remain actionable.
    (package / "devcar_hi.yft").write_bytes(b"model")
    (package / "different.ytd").write_bytes(b"texture")
    for index in range(21):
        (package / f"nested-{index}.rpf").write_bytes(b"rpf")

    codes = {item.code for item in AddonPackageInspector().inspect(package).findings}
    assert {
        "vehicle_handling_reference_missing", "vehicle_model_asset_not_found",
        "vehicle_texture_asset_not_found", "vehicle_kit_not_found",
        "declared_metadata_file_not_found", "opaque_rpf_summary",
    } <= codes


def test_mixed_asi_reshade_package_is_classified_but_never_auto_approved(tmp_path):
    archive = tmp_path / "camera.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("SimpleCamera.asi", b"native")
        package.writestr("IgcsConnector.addon64", b"addon")
        package.writestr("SimpleCamera.ini", "MenuKey=F5")
        package.writestr("reshade-shaders/Shaders/IgcsDof.fx", "shader")
        package.writestr(
            "README.txt",
            "Requires ScriptHookV and optional ReShade with add-on support.",
        )

    scan = AddonPackageInspector().inspect(archive)
    assert scan.package_kinds == ("asi_plugin", "reshade_addon")
    assert scan.binary_plugins == ("SimpleCamera.asi", "IgcsConnector.addon64")
    assert scan.shader_assets == ("reshade-shaders/Shaders/IgcsDof.fx",)
    assert scan.dependency_hints == ("ScriptHookV", "ReShade")
    assert {
        "executable_payload_review_required", "mixed_package_layout",
        "managed_manifest_not_found",
    } <= {item.code for item in scan.findings}

    manifest_path = AddonDraftBuilder().build(scan).write(tmp_path / "camera.json")
    report = AddonLinker().link(AddonManifest.load(manifest_path))
    assert {node.kind for node in report.manifest.nodes} >= {
        "package", "asi_plugin", "reshade_addon",
    }
    assert "imported_draft_requires_review" in {item.code for item in report.issues}
    assert not report.valid


def test_plugin_headers_report_architecture_and_managed_hint(tmp_path):
    payload = bytearray(512)
    payload[:2] = b"MZ"
    payload[0x3C:0x40] = (0x80).to_bytes(4, "little")
    payload[0x80:0x84] = b"PE\0\0"
    payload[0x84:0x86] = (0x8664).to_bytes(2, "little")
    payload[0x100:0x104] = b"BSJB"
    archive = tmp_path / "script.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("ManagedScript.dll", payload)
    scan = AddonPackageInspector().inspect(archive)
    assert len(scan.plugin_details) == 1
    assert scan.plugin_details[0].architecture == "x64"
    assert scan.plugin_details[0].managed
    assert "plugin_header_unrecognized" not in {
        item.code for item in scan.findings
    }

    payload[0x84:0x86] = (0x014C).to_bytes(2, "little")
    x86 = tmp_path / "x86.zip"
    with zipfile.ZipFile(x86, "w") as package:
        package.writestr("OldPlugin.asi", payload)
    codes = {item.code for item in AddonPackageInspector().inspect(x86).findings}
    assert "plugin_architecture_incompatible" in codes


def test_replacement_package_infers_documented_nested_rpf_target(tmp_path):
    archive = tmp_path / "weapon-replacement.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("HK416/w_ar_carbinerifle.ydr", b"model")
        package.writestr("HK416/w_ar_carbinerifle.ytd", b"texture")
        package.writestr(
            "HK416/ReadMe.txt",
            "Install in:\nmods\\update\\x64\\dlcpacks\\patchday8ng\\dlc.rpf"
            "\\x64\\models\\cdimages\\weapons.rpf\n",
        )

    scan = AddonPackageInspector().inspect(archive)
    assert scan.package_kinds == ("replacement_assets",)
    assert scan.installation_targets == (
        "mods/update/x64/dlcpacks/patchday8ng/dlc.rpf/x64/models/cdimages/"
        "weapons.rpf",
    )
    draft_path = AddonDraftBuilder().build(scan).write(tmp_path / "replacement.json")
    manifest = AddonManifest.load(draft_path)
    replacement = next(node for node in manifest.nodes if node.kind == "replacement")
    assert replacement.fields["TargetArchives"] == list(scan.installation_targets)


def test_parser_refuses_entity_metadata_and_reports_malformed_xml(tmp_path):
    package = tmp_path / "unsafe_xml"
    package.mkdir()
    (package / "entity.meta").write_text(
        '<!DOCTYPE root [<!ENTITY x "expanded">]><root>&x;</root>',
        encoding="utf-8",
    )
    (package / "malformed.meta").write_text("<root>", encoding="utf-8")

    scan = AddonPackageInspector().inspect(package)
    failures = [item for item in scan.findings if item.code == "xml_parse_failed"]
    assert len(failures) == 2
    assert any("DTD/entity" in item.message for item in failures)


def test_missing_weapon_links_and_duplicate_records_become_findings(tmp_path):
    package = tmp_path / "partial"
    package.mkdir()
    partial = WEAPONS_META.replace(
        '<AmmoInfo ref="AMMO_TEST_SMOKE" />', "<AmmoInfo />"
    )
    (package / "one.meta").write_text(partial, encoding="utf-8")
    (package / "two.meta").write_text(partial, encoding="utf-8")

    scan = AddonPackageInspector().inspect(package)
    codes = {item.code for item in scan.findings}
    assert scan.valid
    assert {
        "duplicate_record", "weapon_ammo_reference_missing",
        "animation_mapping_not_found", "storefront_mapping_not_found",
    } <= codes


def test_empty_package_reports_no_content_records(tmp_path):
    package = tmp_path / "empty"
    package.mkdir()
    scan = AddonPackageInspector().inspect(package)
    assert scan.valid
    assert scan.entries == ()
    assert "no_content_records" in {item.code for item in scan.findings}
    draft = AddonDraftBuilder().build(scan)
    assert [item["kind"] for item in draft.manifest["nodes"]] == ["package"]


def test_loose_map_assets_are_classified_without_false_no_content_notice(tmp_path):
    package = tmp_path / "custom-map"
    package.mkdir()
    (package / "garage.ymap").write_bytes(b"map")
    (package / "garage.ytyp").write_bytes(b"type")

    scan = AddonPackageInspector().inspect(package)

    assert "map_addon" in scan.package_kinds
    assert "no_content_records" not in {item.code for item in scan.findings}


@pytest.mark.parametrize(
    ("readme", "hints", "tag"),
    [
        ("Requires the classic/legacy version of the game.", ("legacy",), "Legacy"),
        ("Built for GTA V Enhanced.", ("enhanced",), "Enhanced"),
        (
            "Supports GTA V Legacy and GTA V Enhanced.",
            ("legacy", "enhanced"),
            "Legacy + Enhanced",
        ),
    ],
)
def test_package_scan_assigns_visible_edition_tags(tmp_path, readme, hints, tag):
    package = tmp_path / tag.replace(" ", "-")
    package.mkdir()
    (package / "README.txt").write_text(readme, encoding="utf-8")

    scan = AddonPackageInspector().inspect(package)

    assert scan.edition_hints == hints
    assert scan.edition_tag == tag
    assert "edition_compatibility_unresolved" not in {
        item.code for item in scan.findings
    }


def test_scanner_enforces_xml_and_package_limits(tmp_path, monkeypatch):
    package = tmp_path / "limits"
    package.mkdir()
    metadata = package / "large.meta"
    metadata.write_text("<root />", encoding="utf-8")

    monkeypatch.setattr(importer, "MAX_XML_BYTES", 1)
    scan = AddonPackageInspector().inspect(package)
    assert scan.entries[0].content is None
    assert "xml_too_large" in {item.code for item in scan.findings}

    notes = package / "README.md"
    notes.write_text("documentation", encoding="utf-8")
    text_scan = AddonPackageInspector().inspect(package)
    text_finding = next(
        item for item in text_scan.findings
        if item.code == "inspection_text_too_large"
    )
    assert text_finding.path == "README.md"
    assert "XML" not in text_finding.message

    monkeypatch.setattr(importer, "MAX_PACKAGE_FILES", 0)
    with pytest.raises(ValueError, match="more than 0 files"):
        AddonPackageInspector().inspect(package)


def test_draft_write_replaces_existing_file_atomically(tmp_path):
    package = _write_loose_package(tmp_path)
    draft = AddonDraftBuilder().build(AddonPackageInspector().inspect(package))
    destination = package / "addon.json"
    destination.write_text("old", encoding="utf-8")
    draft.write(destination)
    assert json.loads(destination.read_text(encoding="utf-8"))["id"].startswith(
        "imported."
    )
    assert not destination.with_name("addon.json.tmp").exists()


def test_xml_parser_repairs_shipped_utf16_declaration_mismatch():
    payload = (
        b'<?xml version="1.0" encoding="UTF-16"?>\n'
        b"<FirstPerson><Item>valid ASCII payload</Item></FirstPerson>"
    )

    root = importer._parse_xml(payload, "first_person.meta")

    assert root.tag == "FirstPerson"
    assert root.findtext("Item") == "valid ASCII payload"


def test_xml_parser_does_not_repair_other_malformed_xml():
    with pytest.raises(importer.ET.ParseError):
        importer._parse_xml(b"<broken>", "broken.meta")


def test_weapon_archetype_init_data_is_not_promoted_as_a_vehicle():
    root = importer.ET.fromstring(
        "<CWeaponModelInfo__InitDataList><InitDatas><Item>"
        "<modelName>W_AR_TEST</modelName><txdName>weapons</txdName>"
        "<layout>LAYOUT_WEAPON</layout>"
        "</Item></InitDatas></CWeaponModelInfo__InitDataList>"
    )

    assert AddonPackageInspector._vehicle_records(
        "weaponarchetypes.meta", root,
    ) == []


def test_sdk_import_package_cli_writes_draft_and_reports_linker_work(tmp_path):
    package = _write_loose_package(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["sdk", "import-package", str(package)])
    assert result.exit_code == 0, result.output
    assert "Scanned 4 files" in result.output
    assert "Wrote review-only SDK draft" in result.output
    assert "Draft linker:" in result.output
    assert (package / "addon.json").is_file()


def test_sdk_import_package_cli_blocks_wrong_folder_destination_and_bad_oiv(tmp_path):
    package = _write_loose_package(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["sdk", "import-package", str(package), "-o", str(tmp_path / "elsewhere.json")],
    )
    assert result.exit_code == 1
    assert "package root" in result.output

    bad = _write_oiv(tmp_path / "bad.oiv", assembly=False)
    result = runner.invoke(main, ["sdk", "import-package", str(bad)])
    assert result.exit_code == 1
    assert "oiv_assembly_missing" in result.output
    assert "no SDK draft was written" in result.output


def test_sdk_inspect_rpf_uses_detected_edition_helper(tmp_path, monkeypatch):
    archive = tmp_path / "vehicle.rpf"
    archive.write_bytes(b"RPF7")
    game = tmp_path / "game"
    game.mkdir()
    tool = tmp_path / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"helper")
    monkeypatch.setattr("allin1_sdk.cli.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: game)
    monkeypatch.setattr(
        "allin1_sdk.cli.run_hidden",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "OPEN RPF inventory\n", "",
        ),
    )
    output = tmp_path / "inventory.txt"
    result = CliRunner().invoke(
        main, ["sdk", "inspect-rpf", str(archive), "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8") == "OPEN RPF inventory\n"

    not_rpf = tmp_path / "vehicle.zip"
    not_rpf.write_bytes(b"zip")
    rejected = CliRunner().invoke(main, ["sdk", "inspect-rpf", str(not_rpf)])
    assert rejected.exit_code == 1
    assert "requires a loose .rpf" in rejected.output


def test_sdk_inspect_rpf_reports_prerequisite_and_helper_failures(tmp_path, monkeypatch):
    archive = tmp_path / "vehicle.rpf"
    archive.write_bytes(b"RPF7")
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("allin1_sdk.cli.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: None)

    missing_game = CliRunner().invoke(main, ["sdk", "inspect-rpf", str(archive)])
    assert missing_game.exit_code == 1
    assert "GTA V was not detected" in missing_game.output

    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: game)
    missing_helper = CliRunner().invoke(main, ["sdk", "inspect-rpf", str(archive)])
    assert missing_helper.exit_code == 1
    assert "RpfPatcher.exe is missing" in missing_helper.output

    helper = tmp_path / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"helper")
    monkeypatch.setattr(
        "allin1_sdk.cli.run_hidden",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, "", "bad archive",
        ),
    )
    failed = CliRunner().invoke(main, ["sdk", "inspect-rpf", str(archive)])
    assert failed.exit_code == 1
    assert "RPF inspection failed: bad archive" in failed.output

    monkeypatch.setattr(
        "allin1_sdk.cli.run_hidden",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, "OPEN RPF inventory\n", "",
        ),
    )
    printed = CliRunner().invoke(main, ["sdk", "inspect-rpf", str(archive)])
    assert printed.exit_code == 0
    assert printed.output == "OPEN RPF inventory\n"


def test_sdk_inspect_package_rpfs_exports_structured_recursive_indexes(
    tmp_path, monkeypatch,
):
    package = tmp_path / "wheels.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("wheel_pack/dlc.rpf", b"RPF7")
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("allin1_sdk.cli.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: game)

    class FakeIndex:
        def export(self, destination):
            target = Path(destination)
            json_path = target.with_suffix(".json")
            csv_path = target.with_suffix(".csv")
            json_path.write_text('{"archives": [{"path": "nested.rpf"}]}', encoding="utf-8")
            csv_path.write_text("path\nnested.rpf\n", encoding="utf-8")
            return json_path, csv_path

    class FakeService:
        def __init__(self, root, selected_game):
            assert root == tmp_path and selected_game == game

        def index(self, extracted):
            assert Path(extracted).read_bytes() == b"RPF7"
            return FakeIndex()

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    output_dir = tmp_path / "reports"
    result = CliRunner().invoke(main, [
        "sdk", "inspect-package-rpfs", str(package), "-o", str(output_dir),
    ])
    assert result.exit_code == 0, result.output
    assert "1 package RPF member" in result.output
    assert '"nested.rpf"' in next(output_dir.glob("*.json")).read_text(encoding="utf-8")


def test_sdk_inspect_package_rpfs_reports_safety_prerequisites(tmp_path, monkeypatch):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("README.txt", "plain package")
    output = tmp_path / "reports"

    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: None)
    missing_game = CliRunner().invoke(main, [
        "sdk", "inspect-package-rpfs", str(package), "-o", str(output),
    ])
    assert missing_game.exit_code == 1
    assert "GTA V was not detected" in missing_game.output

    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: game)
    no_rpf = CliRunner().invoke(main, [
        "sdk", "inspect-package-rpfs", str(package), "-o", str(output),
    ])
    assert no_rpf.exit_code == 1
    assert "contains no loose RPF" in no_rpf.output

    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as archive:
        for index in range(65):
            archive.writestr(f"pack-{index}/dlc.rpf", b"RPF7")
    too_many = CliRunner().invoke(main, [
        "sdk", "inspect-package-rpfs", str(many), "-o", str(output),
    ])
    assert too_many.exit_code == 1
    assert "more than 64 RPF" in too_many.output


def test_workbench_recursively_inspects_more_than_twenty_small_rpfs(
    tmp_path, monkeypatch,
):
    package = tmp_path / "fleet.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for index in range(29):
            archive.writestr(f"x64/vehiclemods/pack-{index}.rpf", b"RPF7")
    game = tmp_path / "game"
    game.mkdir()

    class FakeIndex:
        edition = "legacy"
        archives = ()
        entries = ()
        warnings = ()

        @staticmethod
        def suffix_counts():
            return {}

    class FakeService:
        def __init__(self, *_args):
            pass

        def index(self, archive):
            assert Path(archive).read_bytes() == b"RPF7"
            return FakeIndex()

    monkeypatch.setattr(
        "allin1_sdk.rpf_tools.RpfExplorerService", FakeService,
    )
    scan = AddonPackageInspector(tmp_path, game).inspect(package)

    assert len(scan.rpf_archives) == 29
    assert not any(
        item.code == "rpf_inspection_limit" for item in scan.findings
    )


def test_sdk_inspect_package_rpfs_reports_index_failures(
    tmp_path, monkeypatch,
):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("content/dlc.rpf", b"RPF7")
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("allin1_sdk.cli.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("allin1_sdk.cli.detect_gta_path", lambda: game)

    class FailedService:
        def __init__(self, *_args):
            pass

        def index(self, _archive):
            raise ValueError("cannot index nested archive")

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FailedService)
    output = tmp_path / "reports"
    result = CliRunner().invoke(main, [
        "sdk", "inspect-package-rpfs", str(package), "-o", str(output),
    ])
    assert result.exit_code == 1
    assert "cannot index nested archive" in result.output


def test_nested_rpf_vehicle_metadata_is_promoted_with_edition_provenance(
    tmp_path, monkeypatch,
):
    package = tmp_path / "dual-edition-vehicle.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Legacy/lunga/dlc.rpf", b"legacy-rpf")
        archive.writestr("Enhanced/lunga/dlc.rpf", b"enhanced-rpf")

    vehicle_xml = VEHICLES_META.replace("devcar", "lunga").replace(
        "DEVCAR", "LUNGA",
    )
    handling_xml = HANDLING_META.replace("DEVCAR", "LUNGA")
    variations_xml = VARIATIONS_META.replace("devcar", "lunga").replace(
        "123_lunga_modkit", "0_default_modkit",
    )
    setup_xml = (
        "<SSetupData><deviceName>dlc_lunga</deviceName>"
        "<nameHash>lunga</nameHash></SSetupData>"
    )
    metadata = {
        "vehicles.meta": vehicle_xml,
        "handling.meta": handling_xml,
        "carvariations.meta": variations_xml,
        "setup2.xml": setup_xml,
    }

    def entry(path, *, archive_path="", kind="binary", size=None):
        content = metadata.get(path, "").encode("utf-8")
        logical_size = len(content) if size is None else size
        return SimpleNamespace(
            id=f"{archive_path}::{path}", archive_path=archive_path,
            path=path, kind=kind, size=logical_size,
            suffix=Path(path).suffix.casefold(),
        )

    class FakeIndex:
        # Deliberately report the active parser edition for both archives. The
        # package branch is the authoritative compatibility provenance.
        edition = "Enhanced"
        archives = (SimpleNamespace(path=""), SimpleNamespace(path="x64/vehicles.rpf"))
        warnings = ()
        entries = (
            entry("vehicles.meta"), entry("handling.meta"),
            entry("carvariations.meta"), entry("setup2.xml"),
            entry("lunga.yft", archive_path="x64/vehicles.rpf", kind="resource", size=8),
            entry("lunga_hi.yft", archive_path="x64/vehicles.rpf", kind="resource", size=8),
            entry("lunga.ytd", archive_path="x64/vehicles.rpf", kind="resource", size=8),
        )

        @staticmethod
        def suffix_counts():
            return {".meta": 3, ".xml": 1, ".yft": 2, ".ytd": 1}

    class FakeService:
        def __init__(self, project_root, gta_path):
            assert project_root == tmp_path
            assert gta_path == tmp_path / "game"

        def index(self, archive):
            assert Path(archive).read_bytes() in {b"legacy-rpf", b"enhanced-rpf"}
            return FakeIndex()

        def extract_many(self, _index, selected, destination):
            root = Path(destination)
            root.mkdir()
            paths = []
            for number, item in enumerate(selected, start=1):
                target = root / f"{number:04d}{item.suffix}"
                target.write_bytes(metadata[item.path].encode("utf-8"))
                paths.append(target)
            return tuple(paths)

    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr("allin1_sdk.rpf_tools.RpfExplorerService", FakeService)
    monkeypatch.setattr(importer, "audit_material_progressions", lambda *_a, **_k: ())

    scan = AddonPackageInspector(tmp_path, game).inspect(package)

    assert [(item.model_name, item.edition) for item in scan.vehicles] == [
        ("lunga", "legacy"), ("lunga", "enhanced"),
    ]
    assert {item.edition for item in scan.handlings} == {"legacy", "enhanced"}
    assert {item.edition for item in scan.variations} == {"legacy", "enhanced"}
    assert {item.edition for item in scan.rpf_archives} == {"legacy", "enhanced"}
    assert len(scan.rpf_native_assets) == 6
    assert "vehicle_addon" in scan.package_kinds
    assert all("dlc.rpf!" in item.source for item in scan.vehicles)
    codes = {item.code for item in scan.findings}
    assert "rpf_metadata_promoted" in codes
    assert "mixed_package_layout" not in codes
    assert "vehicle_kit_not_found" not in codes
    assert "vehicle_model_asset_not_found" not in codes
    assert "vehicle_texture_asset_not_found" not in codes
    assert "vehicle_registration_not_found" not in codes

    draft = AddonDraftBuilder().build(scan).manifest
    nodes = {item["id"]: item for item in draft["nodes"]}
    assert nodes["vehicles.imported"]["fields"]["Editions"] == [
        "legacy", "enhanced",
    ]
    assert nodes["streaming.imported"]["fields"]["ModelNames"] == [
        "lunga", "lunga_hi",
    ]
    assert len(nodes["streaming.imported"]["fields"]["Assets"]) == 6
    archives = [
        item for item in draft["nodes"] if item["kind"] == "archive"
    ]
    assert {item["fields"]["Edition"] for item in archives} == {
        "legacy", "enhanced",
    }
    assert all(item["label"].startswith("Inspected RPF:") for item in archives)
    assert all(item["fields"]["Entries"] == 7 for item in archives)


def test_direct_rpf_is_a_readable_shared_workbench_source(tmp_path, monkeypatch):
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"direct-rpf-fixture")
    game = tmp_path / "game"
    game.mkdir()
    metadata = {
        "vehicles.meta": VEHICLES_META,
        "handling.meta": HANDLING_META,
        "carvariations.meta": VARIATIONS_META,
        "setup2.xml": (
            "<SSetupData><deviceName>dlc_devcar</deviceName>"
            "<nameHash>devcar</nameHash></SSetupData>"
        ),
    }
    payloads = {
        **{name: value.encode("utf-8") for name, value in metadata.items()},
        "devcar.yft": b"fragment-bytes",
        "devcar_hi.yft": b"high-fragment-bytes",
        "devcar.ytd": b"texture-bytes",
    }

    def entry(path, *, archive_path=""):
        content = payloads[path]
        return SimpleNamespace(
            id=f"{archive_path}::{path}", archive_path=archive_path,
            path=path, kind=("resource" if Path(path).suffix in {".yft", ".ytd"}
                             else "binary"),
            # RSC resource sizes are logical allocations, not extracted lengths.
            size=(8 * 1024 * 1024 if Path(path).suffix in {".yft", ".ytd"}
                  else len(content)),
            suffix=Path(path).suffix.casefold(), name=Path(path).name,
            virtual_name=path,
        )

    fake_entries = (
        entry("vehicles.meta"), entry("handling.meta"),
        entry("carvariations.meta"), entry("setup2.xml"),
        entry("devcar.yft", archive_path="x64/levels/gta5/vehicles.rpf"),
        entry("devcar_hi.yft", archive_path="x64/levels/gta5/vehicles.rpf"),
        entry("devcar.ytd", archive_path="x64/levels/gta5/vehicles.rpf"),
    )

    class FakeIndex:
        edition = "Enhanced"
        archives = (
            SimpleNamespace(path=""),
            SimpleNamespace(path="x64/levels/gta5/vehicles.rpf"),
        )
        warnings = ()
        entries = ()

        @staticmethod
        def suffix_counts():
            return {".meta": 3, ".xml": 1, ".yft": 2, ".ytd": 1}

        def entry(self, entry_id):
            return next(item for item in self.entries if item.id == entry_id)

    FakeIndex.entries = fake_entries

    class FakeService:
        def __init__(self, project_root, gta_path):
            assert Path(project_root) == tmp_path
            assert Path(gta_path) == game

        def index(self, source):
            assert Path(source) == archive
            return FakeIndex()

        def extract_many(self, _index, selected, destination):
            root = Path(destination)
            root.mkdir(parents=True)
            extracted = []
            for number, item in enumerate(selected):
                target = root / f"{number:04d}{item.suffix}"
                target.write_bytes(payloads[item.path])
                extracted.append(target)
            return tuple(extracted)

        def extract(self, _index, selected, destination):
            target = Path(destination)
            target.write_bytes(payloads[selected.path])
            return target

    monkeypatch.setattr("allin1_sdk.rpf_tools.RpfExplorerService", FakeService)
    monkeypatch.setattr(importer, "audit_material_progressions", lambda *_a, **_k: ())

    scan = AddonPackageInspector(tmp_path, game).inspect(archive)
    assert scan.source_kind == "rpf"
    assert len(scan.rpf_indexed_entries) == 7
    assert len(scan.workbench_entries) == 8
    assert {item.name for item in scan.rpf_indexed_entries} >= {
        "devcar.yft", "devcar_hi.yft", "devcar.ytd",
    }
    assert any(item.code == "direct_rpf_target_edition" for item in scan.findings)

    project = VehicleProjectResolver.inspect_scan(scan)
    vehicle = project.model("devcar")
    assert vehicle.primary_model.endswith("::devcar.yft")
    assert vehicle.high_detail_model.endswith("::devcar_hi.yft")
    assert vehicle.texture_asset.endswith("::devcar.ytd")

    model_entry = next(
        item for item in scan.workbench_entries if item.name == "devcar.yft"
    )
    content = PackageAssetReader(
        archive, project_root=tmp_path, gta_path=game,
    ).read(model_entry.path, limit=64)
    assert content.data == b"fragment-bytes"
    assert content.size == len(b"fragment-bytes")
    assert not content.truncated

    monkeypatch.setattr("allin1_sdk.cli.PROJECT_ROOT", tmp_path)
    result = CliRunner().invoke(main, [
        "inspect-workbench", str(archive), "--category", "vehicles",
        "--gta-path", str(game),
    ])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["source_kind"] == "rpf"
    assert report["summary"]["vehicles"] == 1
    assert report["summary"]["rpf_indexed_entries"] == 7
    assert report["summary"]["indexed_files"] == 8


def test_sdk_audit_folder_reports_mixed_packages_and_partial_downloads(tmp_path):
    packages = tmp_path / "test mods"
    packages.mkdir()
    with zipfile.ZipFile(packages / "camera.zip", "w") as archive:
        archive.writestr("Camera.asi", b"native")
        archive.writestr("README.txt", "Requires ScriptHookV")
    (packages / "vehicle.rar.crdownload").write_bytes(b"partial")
    report = tmp_path / "audit.md"
    drafts = tmp_path / "drafts"

    result = CliRunner().invoke(main, [
        "sdk", "audit-folder", str(packages), "-o", str(report),
        "--draft-dir", str(drafts),
    ])
    assert result.exit_code == 0, result.output
    text = report.read_text(encoding="utf-8")
    assert "camera.zip" in text and "asi_plugin" in text
    assert "vehicle.rar.crdownload" in text and "INCOMPLETE DOWNLOAD" in text
    assert "imported_draft_requires_review" in text
    assert (drafts / "camera.addon.json").is_file()


def test_sdk_audit_folder_uses_temporary_drafts_and_reports_scan_errors(
    tmp_path, monkeypatch,
):
    packages = tmp_path / "test mods"
    packages.mkdir()
    package = packages / "camera.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Camera.asi", b"native")
    report = tmp_path / "audit.md"

    result = CliRunner().invoke(main, [
        "sdk", "audit-folder", str(packages), "-o", str(report),
    ])
    assert result.exit_code == 0, result.output
    assert "camera.zip" in report.read_text(encoding="utf-8")

    def invalid_scan(self, source):
        raise ValueError("deliberate package failure")

    monkeypatch.setattr(AddonPackageInspector, "inspect", invalid_scan)
    failed_report = tmp_path / "failed-audit.md"
    failed = CliRunner().invoke(main, [
        "sdk", "audit-folder", str(packages), "-o", str(failed_report),
    ])
    assert failed.exit_code == 0, failed.output
    failed_text = failed_report.read_text(encoding="utf-8")
    assert "SCAN ERROR" in failed_text
    assert "deliberate package failure" in failed_text


def test_sdk_audit_folder_rejects_an_empty_folder(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = CliRunner().invoke(main, [
        "sdk", "audit-folder", str(empty), "-o", str(tmp_path / "audit.md"),
    ])
    assert result.exit_code == 1
    assert "no supported package archives" in result.output


@pytest.mark.parametrize(
    ("path", "category", "preview"),
    [
        ("weapons.meta", "Metadata", "text"),
        ("preview.png", "Images", "image"),
        ("vehicle.yft", "Models & world", "binary"),
        ("textures.ytd", "Textures & UI", "binary"),
        ("audio.awc", "Audio", "binary"),
        ("dlc.rpf", "Archives", "binary"),
        ("plugin.dll", "Scripts", "binary"),
        ("notes.md", "Text", "text"),
        ("unknown.bin", "Other", "binary"),
    ],
)
def test_asset_classification_explains_preview_capability(path, category, preview):
    assert asset_category(path) == category
    assert asset_preview_kind(path) == preview


def test_asset_reader_reads_folder_members_with_digest_and_truncation(tmp_path):
    package = tmp_path / "assets"
    package.mkdir()
    (package / "small.txt").write_bytes(b"hello")
    (package / "large.bin").write_bytes(b"0123456789")
    reader = PackageAssetReader(package)

    small = reader.read("small.txt")
    assert small.data == b"hello"
    assert not small.truncated
    assert small.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert small.preview_kind == "text"

    large = reader.read("large.bin", limit=4)
    assert large.data == b"0123"
    assert large.truncated
    assert large.sha256 is None

    with pytest.raises(ValueError, match="limit must be positive"):
        reader.read("small.txt", limit=0)
    with pytest.raises(FileNotFoundError, match="not found"):
        reader.read("missing.txt")
    with pytest.raises(ValueError, match="Unsafe package member path"):
        reader.read("../escape.txt")


def test_asset_reader_reads_archives_and_rejects_ambiguous_members(tmp_path):
    archive_path = tmp_path / "assets.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("content/readme.txt", "package")
    content = PackageAssetReader(archive_path).read("CONTENT/README.TXT")
    assert content.data == b"package"
    assert content.size == 7

    duplicate = tmp_path / "duplicate-assets.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("same.txt", "first")
        archive.writestr("SAME.TXT", "second")
    with pytest.raises(ValueError, match="Ambiguous package asset"):
        PackageAssetReader(duplicate).read("same.txt")

    with pytest.raises(FileNotFoundError, match="not found"):
        PackageAssetReader(archive_path).read("missing.txt")


def test_loose_package_parallel_reads_keep_deterministic_entry_content(tmp_path):
    package = tmp_path / "many-readable-assets"
    package.mkdir()
    expected = {}
    for index in range(12):
        name = f"notes-{index:02d}.txt"
        content = f"package note {index}".encode()
        (package / name).write_bytes(content)
        expected[name] = content
    (package / "payload.dll").write_bytes(b"MZ" + (b"\0" * 200))

    scan = AddonPackageInspector().inspect(package)

    readable = {entry.path: entry.content for entry in scan.entries}
    assert [entry.path for entry in scan.entries] == sorted(readable)
    assert all(readable[path] == content for path, content in expected.items())
    assert readable["payload.dll"] == b"MZ" + (b"\0" * 200)


def test_asset_reader_rejects_unsupported_sources(tmp_path):
    source = tmp_path / "asset.bin"
    source.write_bytes(b"binary")
    with pytest.raises(ValueError, match=r"package folder, \.rpf, or \.oiv/\.zip/\.rar/\.7z"):
        PackageAssetReader(source)


def test_text_and_hex_previews_handle_common_package_encodings():
    assert decode_text_preview(b"\xef\xbb\xbfhello") == "hello"
    assert decode_text_preview("hello".encode("utf-16")) == "hello"
    assert decode_text_preview(b"caf\xe9") == "café"
    output = hex_preview(b"ABC\x00")
    assert "41 42 43 00" in output
    assert "ABC." in output
