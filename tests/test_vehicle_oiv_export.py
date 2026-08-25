from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main
from allin1_sdk.managed_package_conversion import (
    ManagedVehiclePackageConverter,
    ManagedVehiclePackagePlan,
)
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.vehicle_catalog import VehicleCatalog
from allin1_sdk.vehicle_oiv_export import LegacyVehicleOivExporter


RPF_BYTES = b"legacy-vehicle-dlc-rpf-fixture"


def _plan(tmp_path: Path, *, edition: str = "legacy") -> ManagedVehiclePackagePlan:
    source = tmp_path / f"source-{edition}"
    payload = source / edition.title() / "lunga" / "dlc.rpf"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(RPF_BYTES)
    package_id = f"fixture.lunga.{edition}"
    catalog = VehicleCatalog.from_dict({
        "schema_version": 1,
        "id": package_id,
        "name": f"Pagani Lunga ({edition.title()})",
        "vehicles": [{
            "model": "lunga",
            "name": "Lunga",
            "manufacturer": "Pagani",
            "category": "super",
            "price": 2_000_000,
            "storage": "garage",
            "source_pack": "lunga",
            "traffic": {"enabled": False, "weight": 1.0},
        }],
    })
    return ManagedVehiclePackagePlan(
        source=source,
        source_kind="folder",
        source_package_sha256=None,
        edition=edition,
        source_member=f"{edition.title()}/lunga/dlc.rpf",
        source_member_size=len(RPF_BYTES),
        source_member_sha256=hashlib.sha256(RPF_BYTES).hexdigest(),
        package_id=package_id,
        name=f"Pagani Lunga ({edition.title()})",
        version="1.2.3",
        dlc_pack="lunga",
        destination="mods/update/x64/dlcpacks/lunga/dlc.rpf",
        vehicles=("lunga",),
        handling_ids=("LUNGA",),
        registered_package_names=("dlc_lunga",),
        registration_sources=("Legacy/lunga/dlc.rpf!content.xml",),
        catalog=catalog,
    )


def _prepared(tmp_path: Path, *, edition: str = "legacy") -> Path:
    plan = _plan(tmp_path, edition=edition)
    project = tmp_path / f"project-{edition}"
    game = tmp_path / f"game-{edition}"
    project.mkdir()
    game.mkdir()
    converter = ManagedVehiclePackageConverter(project, game)
    return converter.export(plan, tmp_path / f"prepared-{edition}").package_root


def _assembly(archive: Path) -> tuple[ET.Element, bytes]:
    with zipfile.ZipFile(archive) as package:
        payload = package.read("assembly.xml")
    return ET.fromstring(payload), payload


def test_plan_export_is_deterministic_hash_bound_and_oiv_compatible(tmp_path):
    plan = _plan(tmp_path)
    exporter = LegacyVehicleOivExporter()
    first = exporter.export_plan(plan, tmp_path / "first.oiv", author="Fixture")
    second = exporter.export_plan(plan, tmp_path / "second.oiv", author="Fixture")

    assert first.archive_sha256 == second.archive_sha256
    assert first.members == (
        "assembly.xml", "content/dlcpacks/lunga/dlc.rpf",
    )
    with zipfile.ZipFile(first.archive) as package:
        assert tuple(item.filename for item in package.infolist()) == first.members
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in package.infolist())
        assert all(item.compress_type == zipfile.ZIP_STORED for item in package.infolist())
        installed_payload = package.read("content/dlcpacks/lunga/dlc.rpf")
    assert installed_payload == RPF_BYTES
    assert hashlib.sha256(installed_payload).hexdigest() == plan.source_member_sha256

    assembly, assembly_bytes = _assembly(first.archive)
    assert assembly.attrib["version"] == "2.2"
    assert assembly.attrib["target"] == "Five"
    assert re.fullmatch(
        r"\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}",
        assembly.attrib["id"],
    )
    assert assembly.findtext("metadata/author/displayName") == "Fixture"
    assert assembly.findtext("metadata/version/major") == "1"
    assert assembly.findtext("metadata/version/minor") == "2"
    assert assembly.findtext("metadata/version/tag") == "Patch 3"
    assert assembly.find("metadata/description") is not None
    assert assembly.findtext("colors/headerBackground") == "$FF2D9C50"
    assert assembly.find("colors/headerBackground").attrib == {
        "useBlackTextColor": "False",
    }
    assert assembly.findtext("colors/iconBackground") == "$FF1F7F42"
    file_copy = assembly.find("content/add")
    assert file_copy is not None
    assert file_copy.attrib == {"source": "dlcpacks/lunga/dlc.rpf"}
    assert file_copy.text == r"update\x64\dlcpacks\lunga\dlc.rpf"
    archive_node = assembly.find("content/archive")
    assert archive_node is not None
    assert archive_node.attrib == {
        "path": r"update\update.rpf",
        "createIfNotExist": "False",
        "type": "RPF7",
    }
    registration = assembly.find("content/archive/xml/add")
    assert registration is not None
    assert registration.attrib == {
        "xpath": "/SMandatoryPacksData/Paths", "append": "Last",
    }
    assert registration.findtext("Item") == "dlcpacks:/lunga/"
    assert hashlib.sha256(assembly_bytes).hexdigest() == first.assembly_sha256

    inspected = OivWorkbench().inspect(first.archive)
    assert inspected.format_version == "2.2"
    assert [(item.kind, item.supported) for item in inspected.operations] == [
        ("add", True), ("archive", True), ("xml", True),
    ]
    assert inspected.findings == ()

    result = first.to_dict()
    assert result["game_write_performed"] is False
    assert result["compatibility"] == {
        "edition": "legacy",
        "installs_vehicle_files": True,
        "registers_dlclist": True,
        "includes_gbay_catalog": False,
        "includes_traffic_preference": False,
        "includes_allin1_receipt": False,
        "includes_managed_backup_or_rollback": False,
        "notice": result["compatibility"]["notice"],
    }
    assert "Legacy DLC vehicle files only" in result["compatibility"]["notice"]


def test_prepared_export_revalidates_manifest_review_and_payload(tmp_path):
    prepared = _prepared(tmp_path)
    result = LegacyVehicleOivExporter().export_prepared(
        prepared, tmp_path / "prepared.oiv", author="Fixture",
    )
    assert result.package_id == "fixture.lunga.legacy"
    assert result.payload_sha256 == hashlib.sha256(RPF_BYTES).hexdigest()

    review = prepared / "allin1.review.json"
    evidence = json.loads(review.read_text(encoding="utf-8"))
    evidence["edition"] = "enhanced"
    review.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="review evidence does not match"):
        LegacyVehicleOivExporter().export_prepared(
            prepared, tmp_path / "bad-review.oiv", author="Fixture",
        )
    assert not (tmp_path / "bad-review.oiv").exists()


def test_export_refuses_enhanced_stale_overwrite_and_game_destinations(tmp_path):
    enhanced = _plan(tmp_path, edition="enhanced")
    with pytest.raises(ValueError, match="Legacy vehicle packages only"):
        LegacyVehicleOivExporter().export_plan(
            enhanced, tmp_path / "enhanced.oiv", author="Fixture",
        )
    prepared_enhanced = _prepared(tmp_path / "prepared-case", edition="enhanced")
    with pytest.raises(ValueError, match="Legacy vehicle packages only"):
        LegacyVehicleOivExporter().export_prepared(
            prepared_enhanced, tmp_path / "enhanced-prepared.oiv", author="Fixture",
        )
    cli_refusal = CliRunner().invoke(main, [
        "export-legacy-vehicle-oiv", str(prepared_enhanced),
        str(tmp_path / "enhanced-cli.oiv"),
        "--author", "Fixture",
    ])
    assert cli_refusal.exit_code != 0
    assert "Legacy vehicle packages only" in cli_refusal.output
    assert not (tmp_path / "enhanced-cli.oiv").exists()

    plan = _plan(tmp_path / "stale-case")
    payload = plan.source / Path(*Path(plan.source_member).parts)
    payload.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after"):
        LegacyVehicleOivExporter().export_plan(
            plan, tmp_path / "stale.oiv", author="Fixture",
        )
    assert not (tmp_path / "stale.oiv").exists()

    good = _plan(tmp_path / "boundaries")
    with pytest.raises(ValueError, match="Package id"):
        LegacyVehicleOivExporter().export_plan(
            replace(good, package_id="../unsafe"), tmp_path / "unsafe.oiv",
            author="Fixture",
        )
    with pytest.raises(ValueError, match=r"\.oiv filename"):
        LegacyVehicleOivExporter().export_plan(
            good, tmp_path / "wrong.zip", author="Fixture",
        )
    occupied = tmp_path / "occupied.oiv"
    occupied.write_bytes(b"do not replace")
    with pytest.raises(ValueError, match="already exists"):
        LegacyVehicleOivExporter().export_plan(
            good, occupied, author="Fixture",
        )
    assert occupied.read_bytes() == b"do not replace"

    game = tmp_path / "game"
    game.mkdir()
    with pytest.raises(ValueError, match="inside GTA V"):
        LegacyVehicleOivExporter(game).export_plan(
            good, game / "exports" / "blocked.oiv", author="Fixture",
        )
    assert not (game / "exports").exists()


def test_prepared_payload_hash_drift_is_refused_before_output(tmp_path):
    prepared = _prepared(tmp_path)
    (prepared / "payload" / "dlc.rpf").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        LegacyVehicleOivExporter().export_prepared(
            prepared, tmp_path / "tampered.oiv", author="Fixture",
        )
    assert not (tmp_path / "tampered.oiv").exists()


def test_legacy_oiv_cli_and_agent_api_share_typed_authoring_route(tmp_path):
    prepared = _prepared(tmp_path)
    cli_output = tmp_path / "cli.oiv"
    invoked = CliRunner().invoke(main, [
        "export-legacy-vehicle-oiv", str(prepared), str(cli_output),
        "--author", "Fixture Author",
    ])
    assert invoked.exit_code == 0, invoked.output
    payload = json.loads(invoked.output)
    assert payload["operation"] == "export_legacy_vehicle_oiv"
    assert payload["game_write_performed"] is False
    assert cli_output.is_file()

    catalog = {item["name"]: item for item in command_catalog()}
    command = catalog["export-legacy-vehicle-oiv"]
    assert command["risk"] == "authoring_write"
    assert {item["name"] for item in command["parameters"]} == {
        "package_root", "destination", "author", "gta_path",
    }
    author_parameter = next(
        item for item in command["parameters"] if item["name"] == "author"
    )
    assert author_parameter["required"] is True

    api_output = tmp_path / "api.oiv"
    response = execute_request({
        "id": "oiv-export",
        "action": "execute",
        "command": "export-legacy-vehicle-oiv",
        "args": [
            str(prepared), str(api_output), "--author", "Fixture Author",
        ],
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert response["ok"] is True
    assert response["risk"] == "authoring_write"
    api_payload = json.loads(response["result"]["output"])
    assert api_payload["archive"] == str(api_output.resolve())
    assert api_output.is_file()
