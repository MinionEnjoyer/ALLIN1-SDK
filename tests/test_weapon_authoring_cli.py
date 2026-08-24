from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.assistant_context import retrieve_operations
from allin1_sdk.cli import main


WEAPONS_META = """<CWeaponInfoBlob><Infos>
  <Item>
    <Name>WEAPON_CLI_TEST</Name><Slot ref="SLOT_CLI_TEST"/>
    <AmmoInfo ref="AMMO_CLI_TEST"/><Model>w_pi_cli_test</Model>
    <HumanNameHash>WT_CLI_TEST</HumanNameHash><StatName>CLI_TEST</StatName>
    <AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components><Item>
      <Name>COMPONENT_CLI_CLIP</Name><Default value="true"/>
    </Item></Components></Item></AttachPoints>
  </Item>
  <Item>
    <Name>AMMO_CLI_TEST</Name><Model>w_pi_cli_test</Model>
    <AmmoMax value="120"/><AmmoMax50 value="60"/><Explosion>NONE</Explosion>
    <TrailFx/><PrimedFx/>
  </Item>
</Infos></CWeaponInfoBlob>"""

COMPONENTS_META = """<CWeaponComponentInfoBlob><Infos>
  <Item type="CWeaponComponentClipInfo">
    <Name>COMPONENT_CLI_CLIP</Name><Model>w_at_cli_clip</Model>
    <LocName>WCT_CLI_CLIP</LocName><LocDesc>WCD_CLI_CLIP</LocDesc>
    <AttachBone>WAPClip</AttachBone>
  </Item>
</Infos></CWeaponComponentInfoBlob>"""


def _weapon_package(root: Path) -> Path:
    package = root / "weapon-package"
    package.mkdir()
    (package / "weapons.meta").write_text(WEAPONS_META, encoding="utf-8")
    (package / "weaponcomponents.meta").write_text(
        COMPONENTS_META, encoding="utf-8",
    )
    (package / "weaponanimations.meta").write_text(
        '<WeaponAnimations><Item key="WEAPON_CLI_TEST"/></WeaponAnimations>',
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<Shop><Item><nameHash>WEAPON_CLI_TEST</nameHash></Item></Shop>",
        encoding="utf-8",
    )
    return package


def _shared_weapon_package(root: Path) -> Path:
    package = _weapon_package(root)
    metadata = (package / "weapons.meta").read_text(encoding="utf-8")
    second_weapon = """  <Item>
    <Name>WEAPON_CLI_SECOND</Name><Slot ref="SLOT_CLI_SECOND"/>
    <AmmoInfo ref="AMMO_CLI_TEST"/><Model>w_pi_cli_second</Model>
    <HumanNameHash>WT_CLI_SECOND</HumanNameHash><StatName>CLI_SECOND</StatName>
    <AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components><Item>
      <Name>COMPONENT_CLI_CLIP</Name><Default value="true"/>
    </Item></Components></Item></AttachPoints>
  </Item>
"""
    marker = "  <Item>\n    <Name>AMMO_CLI_TEST</Name>"
    assert marker in metadata
    (package / "weapons.meta").write_text(
        metadata.replace(marker, second_weapon + marker), encoding="utf-8",
    )
    return package


def _integration_weapon_package(root: Path) -> Path:
    package = _weapon_package(root)
    (package / "weaponanimations.meta").write_text(
        "<CWeaponAnimationsSets><WeaponAnimationsSets>"
        '<Item key="Default"><WeaponAnimations>'
        '<Item key="WEAPON_CLI_TEMPLATE"><Clip ref="clip_default"/></Item>'
        "</WeaponAnimations></Item>"
        '<Item key="FirstPerson"><WeaponAnimations>'
        '<Item key="WEAPON_CLI_TEMPLATE"><Clip ref="clip_fp"/></Item>'
        "</WeaponAnimations></Item>"
        "</WeaponAnimationsSets></CWeaponAnimationsSets>",
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<WeaponShopItemArray><weaponShopItems><Item>"
        "<lockHash>LOCK_CLI</lockHash>"
        "<nameHash>WEAPON_CLI_TEST</nameHash>"
        '<cost value="750"/><ammoCost value="150"/>'
        "<textLabel>WT_CLI_TEST</textLabel>"
        "<weaponDesc>WTD_CLI_TEST</weaponDesc>"
        "<weaponTT>WTT_CLI_TEST</weaponTT>"
        "<weaponUppercase>WTU_CLI_TEST</weaponUppercase>"
        '<id value="32"/><weaponComponents/>'
        '<availableInSP value="false"/>'
        "</Item></weaponShopItems></WeaponShopItemArray>",
        encoding="utf-8",
    )
    return package


def _clone_bundle_weapon_package(root: Path) -> Path:
    package = _weapon_package(root)
    (package / "weapon_shop.meta").write_text(
        "<WeaponShopItemArray><weaponShopItems><Item>"
        "<lockHash>LOCK_CLI</lockHash>"
        "<nameHash>WEAPON_CLI_TEST</nameHash>"
        '<cost value="750"/><ammoCost value="150"/>'
        "<textLabel>WT_CLI_TEST</textLabel>"
        "<weaponDesc>WTD_CLI_TEST</weaponDesc>"
        "<weaponTT>WTT_CLI_TEST</weaponTT>"
        "<weaponUppercase>WTU_CLI_TEST</weaponUppercase>"
        '<id value="32"/><weaponComponents/>'
        '<availableInSP value="true"/>'
        "</Item></weaponShopItems></WeaponShopItemArray>",
        encoding="utf-8",
    )
    stream = package / "stream"
    stream.mkdir()
    (stream / "w_pi_cli_clone.ydr").write_bytes(b"target-model")
    return package


def test_weapon_authoring_cli_alias_edits_inspects_and_agent_undo(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "weapon-workspace"
    created = runner.invoke(main, [
        "sdk", "create-weapon-authoring", str(_weapon_package(tmp_path)),
        "--output-dir", str(workspace),
    ])
    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    assert created_payload["revision"] == 0
    assert created_payload["weapons"] == ["WEAPON_CLI_TEST"]
    assert created_payload["components"] == ["COMPONENT_CLI_CLIP"]

    weapon_edit = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "weapon.humanNameHash=WT_CLI_EDITED",
        "--set", "ammo.ammoMax=180",
        "--expected-revision", "0", "--acknowledge-edit",
    ])
    assert weapon_edit.exit_code == 0, weapon_edit.output
    assert json.loads(weapon_edit.output)["revision"] == 1

    component_edit = runner.invoke(main, [
        "sdk", "set-weapon-component", str(workspace), "COMPONENT_CLI_CLIP",
        "--set", "component.locDesc=WCD_CLI_EDITED",
        "--expected-revision", "1", "--acknowledge-edit",
    ])
    assert component_edit.exit_code == 0, component_edit.output
    assert json.loads(component_edit.output)["subject_kind"] == "component"

    attachment_edit = runner.invoke(main, [
        "set-weapon-attachment", str(workspace), "WEAPON_CLI_TEST",
        "COMPONENT_CLI_CLIP", "--set", "attachment.default=false",
        "--expected-revision", "2", "--acknowledge-edit",
    ])
    assert attachment_edit.exit_code == 0, attachment_edit.output
    assert json.loads(attachment_edit.output)["revision"] == 3

    inspected = runner.invoke(main, [
        "inspect-weapon-authoring", str(workspace),
        "--weapon", "WEAPON_CLI_TEST", "--component", "COMPONENT_CLI_CLIP",
    ])
    assert inspected.exit_code == 0, inspected.output
    inspected_payload = json.loads(inspected.output)
    assert inspected_payload["revision"] == 3
    assert (
        inspected_payload["weapon_authoring"]["values"]["ammo.ammoMax"]
        == "180"
    )
    assert (
        inspected_payload["component_authoring"]["values"]["component.locDesc"]
        == "WCD_CLI_EDITED"
    )
    link = inspected_payload["validation"]["attachments"][0]
    assert link["default"] is False

    response = execute_request({
        "id": "weapon-undo",
        "action": "execute",
        "command": "undo-weapon-edit",
        "args": [
            str(workspace), "--expected-revision", "3", "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert response["ok"] is True
    assert response["risk"] == "authoring_write"
    undo_payload = json.loads(response["result"]["output"])
    assert undo_payload["revision"] == 4
    assert undo_payload["validation"]["attachments"][0]["default"] is True


def test_weapon_authoring_cli_exposes_concurrency_and_shared_record_guards():
    catalog = {item["name"]: item for item in command_catalog()}
    parameters = {
        item["name"]: item
        for item in catalog["set-weapon-fields"]["parameters"]
    }
    assert parameters["expected_revision"]["kind"] == "option"
    assert parameters["acknowledge_shared"]["kind"] == "option"
    assert parameters["acknowledge_edit"]["required"] is True
    component_parameters = {
        item["name"]: item
        for item in catalog["set-weapon-component"]["parameters"]
    }
    assert "acknowledge_shared" in component_parameters
    attachment_parameters = {
        item["name"]: item
        for item in catalog["set-weapon-attachment"]["parameters"]
    }
    assert "expected_revision" in attachment_parameters
    assert "acknowledge_shared" not in attachment_parameters
    assert catalog["inspect-weapon-animation"]["risk"] == "read_only"
    assert catalog["inspect-weapon-shop"]["risk"] == "read_only"
    assert catalog["plan-weapon-clone"]["risk"] == "read_only"
    for command in ("clone-weapon-animation", "set-weapon-shop-fields"):
        assert catalog[command]["risk"] == "authoring_write"
        command_parameters = {
            item["name"]: item for item in catalog[command]["parameters"]
        }
        assert command_parameters["acknowledge_edit"]["required"] is True
        assert "expected_revision" in command_parameters
    clone_parameters = {
        item["name"]: item
        for item in catalog["clone-weapon-bundle"]["parameters"]
    }
    assert catalog["clone-weapon-bundle"]["risk"] == "authoring_write"
    assert clone_parameters["acknowledge_edit"]["required"] is True
    assert clone_parameters["expected_revision"]["required"] is True
    assert clone_parameters["plan_sha256"]["required"] is True

    operations = retrieve_operations(
        "Inspect and update this weapon's ammo and attachment",
        tuple(catalog.values()),
    )
    operation_names = {str(item["name"]) for item in operations}
    assert "inspect-weapon-authoring" in operation_names
    assert "set-weapon-fields" in operation_names
    assert "set-weapon-attachment" in operation_names
    assert not any(name.startswith("set-vehicle-") for name in operation_names)

    integration_operations = retrieve_operations(
        "Inspect and update the weapon shop price and animation mappings",
        tuple(catalog.values()),
    )
    integration_names = {str(item["name"]) for item in integration_operations}
    assert {
        "inspect-weapon-animation", "clone-weapon-animation",
        "inspect-weapon-shop", "set-weapon-shop-fields",
    } <= integration_names

    clone_operations = retrieve_operations(
        "Plan and clone a complete weapon bundle from a donor template",
        tuple(catalog.values()),
    )
    clone_names = {str(item["name"]) for item in clone_operations}
    assert {"plan-weapon-clone", "clone-weapon-bundle"} <= clone_names


def test_weapon_integration_cli_clones_animation_and_edits_shop(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "weapon-integration-workspace"
    created = runner.invoke(main, [
        "create-weapon-authoring", str(_integration_weapon_package(tmp_path)),
        "-o", str(workspace),
    ])
    assert created.exit_code == 0, created.output

    template = runner.invoke(main, [
        "sdk", "inspect-weapon-animation", str(workspace),
        "WEAPON_CLI_TEMPLATE",
    ])
    assert template.exit_code == 0, template.output
    template_payload = json.loads(template.output)
    assert template_payload["animation"]["weapon"] == "WEAPON_CLI_TEMPLATE"
    assert len(template_payload["animation"]["set_names"]) == 2

    cloned = runner.invoke(main, [
        "clone-weapon-animation", str(workspace), "WEAPON_CLI_TEST",
        "--template", "WEAPON_CLI_TEMPLATE", "--expected-revision", "0",
        "--acknowledge-edit",
    ])
    assert cloned.exit_code == 0, cloned.output
    cloned_payload = json.loads(cloned.output)
    assert cloned_payload["revision"] == 1
    assert cloned_payload["subject_kind"] == "animation"

    target = runner.invoke(main, [
        "inspect-weapon-animation", str(workspace), "WEAPON_CLI_TEST",
    ])
    assert target.exit_code == 0, target.output
    assert len(json.loads(target.output)["animation"]["records"]) == 2

    shop = runner.invoke(main, [
        "inspect-weapon-shop", str(workspace), "WEAPON_CLI_TEST",
    ])
    assert shop.exit_code == 0, shop.output
    assert json.loads(shop.output)["shop"]["values"]["shop.cost"] == "750"

    missing_ack = runner.invoke(main, [
        "set-weapon-shop-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "shop.cost=900",
    ])
    assert missing_ack.exit_code != 0
    assert "Missing option '--acknowledge-edit'" in missing_ack.output

    edited = runner.invoke(main, [
        "sdk", "set-weapon-shop-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "shop.cost=900", "--set", "shop.availableInSP=true",
        "--expected-revision", "1", "--acknowledge-edit",
    ])
    assert edited.exit_code == 0, edited.output
    edited_payload = json.loads(edited.output)
    assert edited_payload["revision"] == 2
    assert edited_payload["subject_kind"] == "shop"


def test_weapon_authoring_cli_rejects_missing_ack_and_stale_revision(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "weapon-workspace"
    created = runner.invoke(main, [
        "create-weapon-authoring", str(_weapon_package(tmp_path)),
        "-o", str(workspace),
    ])
    assert created.exit_code == 0, created.output

    missing_ack = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "ammo.ammoMax=180",
    ])
    assert missing_ack.exit_code != 0
    assert "Missing option '--acknowledge-edit'" in missing_ack.output

    first = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "ammo.ammoMax=180", "--expected-revision", "0",
        "--acknowledge-edit",
    ])
    assert first.exit_code == 0, first.output
    stale = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "ammo.ammoMax=200", "--expected-revision", "0",
        "--acknowledge-edit",
    ])
    assert stale.exit_code != 0
    assert "revision conflict" in stale.output


def test_weapon_authoring_cli_requires_explicit_shared_record_ack(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "weapon-workspace"
    created = runner.invoke(main, [
        "create-weapon-authoring", str(_shared_weapon_package(tmp_path)),
        "-o", str(workspace),
    ])
    assert created.exit_code == 0, created.output

    rejected_ammo = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "ammo.ammoMax=180", "--expected-revision", "0",
        "--acknowledge-edit",
    ])
    assert rejected_ammo.exit_code != 0
    assert "shared by multiple weapons" in rejected_ammo.output
    accepted_ammo = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "ammo.ammoMax=180", "--expected-revision", "0",
        "--acknowledge-shared", "--acknowledge-edit",
    ])
    assert accepted_ammo.exit_code == 0, accepted_ammo.output
    assert json.loads(accepted_ammo.output)["affected_weapons"] == [
        "WEAPON_CLI_TEST", "WEAPON_CLI_SECOND",
    ]

    rejected_component = runner.invoke(main, [
        "set-weapon-component", str(workspace), "COMPONENT_CLI_CLIP",
        "--set", "component.locDesc=WCD_SHARED", "--expected-revision", "1",
        "--acknowledge-edit",
    ])
    assert rejected_component.exit_code != 0
    assert "shared by multiple weapons" in rejected_component.output
    accepted_component = runner.invoke(main, [
        "set-weapon-component", str(workspace), "COMPONENT_CLI_CLIP",
        "--set", "component.locDesc=WCD_SHARED", "--expected-revision", "1",
        "--acknowledge-shared", "--acknowledge-edit",
    ])
    assert accepted_component.exit_code == 0, accepted_component.output


def test_weapon_bundle_clone_requires_reviewed_digest_revision_and_ack(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "weapon-bundle-workspace"
    created = runner.invoke(main, [
        "create-weapon-authoring", str(_clone_bundle_weapon_package(tmp_path)),
        "-o", str(workspace),
    ])
    assert created.exit_code == 0, created.output

    spec = [
        "--weapon-name", "WEAPON_CLI_CLONE",
        "--slot", "SLOT_CLI_CLONE",
        "--ammo-info", "AMMO_CLI_CLONE",
        "--model", "w_pi_cli_clone",
        "--human-name-hash", "WT_CLI_CLONE",
        "--stat-name", "CLI_CLONE",
        "--ammo-mode", "clone",
        "--ammo-name", "AMMO_CLI_CLONE",
    ]
    planned = runner.invoke(main, [
        "sdk", "plan-weapon-clone", str(workspace), "WEAPON_CLI_TEST",
        *spec,
    ])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["operation"] == "weapon_bundle_clone_plan"
    assert plan["ready"] is True
    assert plan["revision"] == 0
    assert len(plan["plan_sha256"]) == 64

    missing_ack = runner.invoke(main, [
        "clone-weapon-bundle", str(workspace), "WEAPON_CLI_TEST", *spec,
        "--expected-revision", "0",
        "--plan-sha256", plan["plan_sha256"],
    ])
    assert missing_ack.exit_code != 0
    assert "Missing option '--acknowledge-edit'" in missing_ack.output

    wrong_digest = runner.invoke(main, [
        "clone-weapon-bundle", str(workspace), "WEAPON_CLI_TEST", *spec,
        "--expected-revision", "0", "--plan-sha256", "0" * 64,
        "--acknowledge-edit",
    ])
    assert wrong_digest.exit_code != 0
    assert "plan digest mismatch" in wrong_digest.output

    changed = runner.invoke(main, [
        "set-weapon-fields", str(workspace), "WEAPON_CLI_TEST",
        "--set", "weapon.statName=CLI_TEST_REVISED",
        "--expected-revision", "0", "--acknowledge-edit",
    ])
    assert changed.exit_code == 0, changed.output
    current_plan_result = runner.invoke(main, [
        "plan-weapon-clone", str(workspace), "WEAPON_CLI_TEST", *spec,
    ])
    assert current_plan_result.exit_code == 0, current_plan_result.output
    current_plan = json.loads(current_plan_result.output)
    assert current_plan["revision"] == 1
    assert current_plan["plan_sha256"] != plan["plan_sha256"]
    agent_plan = execute_request({
        "id": "plan-weapon-clone",
        "action": "execute",
        "command": "plan-weapon-clone",
        "args": [str(workspace), "WEAPON_CLI_TEST", *spec],
    }, audit_path=tmp_path / "weapon-clone-agent-audit.jsonl")
    assert agent_plan["ok"] is True
    assert agent_plan["risk"] == "read_only"
    assert json.loads(agent_plan["result"]["output"])["plan_sha256"] == (
        current_plan["plan_sha256"]
    )

    stale = runner.invoke(main, [
        "clone-weapon-bundle", str(workspace), "WEAPON_CLI_TEST", *spec,
        "--expected-revision", "0",
        "--plan-sha256", current_plan["plan_sha256"],
        "--acknowledge-edit",
    ])
    assert stale.exit_code != 0
    assert "revision conflict" in stale.output

    cloned = runner.invoke(main, [
        "sdk", "clone-weapon-bundle", str(workspace), "WEAPON_CLI_TEST",
        *spec, "--expected-revision", "1",
        "--plan-sha256", current_plan["plan_sha256"],
        "--acknowledge-edit",
    ])
    assert cloned.exit_code == 0, cloned.output
    result = json.loads(cloned.output)
    assert result["subject_kind"] == "bundle"
    assert result["revision"] == 2

    inspected = runner.invoke(main, [
        "inspect-weapon-authoring", str(workspace),
        "--weapon", "WEAPON_CLI_CLONE",
    ])
    assert inspected.exit_code == 0, inspected.output
    validation = json.loads(inspected.output)["validation"]
    assert "WEAPON_CLI_CLONE" in {
        item["name"] for item in validation["weapons"]
    }
    assert "AMMO_CLI_CLONE" in {
        item["name"] for item in validation["ammo"]
    }
    assert "WEAPON_CLI_CLONE" in validation["animation_weapons"]
    assert "WEAPON_CLI_CLONE" in validation["shop_weapons"]
    assert any(
        item["weapon_name"] == "WEAPON_CLI_CLONE"
        and item["component_name"] == "COMPONENT_CLI_CLIP"
        for item in validation["attachments"]
    )
