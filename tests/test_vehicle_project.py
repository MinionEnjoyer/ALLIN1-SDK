from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main
from allin1_sdk.vehicle_project import VehicleProjectResolver


VEHICLES_META = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>projectcar</modelName><txdName>projectcar</txdName>
<handlingId>PROJECTHAND</handlingId><gameName>PROJECTCAR</gameName>
<vehicleMakeName>PROJECT</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
<vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""
HANDLING_META = """<CHandlingDataMgr><HandlingData><Item>
<handlingName>PROJECTHAND</handlingName></Item></HandlingData></CHandlingDataMgr>"""
VARIATIONS_META = """<CVehicleModelInfoVariation><variationData><Item>
<modelName>projectcar</modelName><kits><Item>456_projectkit</Item></kits>
</Item></variationData></CVehicleModelInfoVariation>"""
CARCOLS_META = """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>456_projectkit</kitName><id value="456" />
</Item></Kits></CVehicleModelInfoVarGlobal>"""
CONTENT_XML = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_projectcar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""


def _package(root: Path, *, complete: bool = True) -> Path:
    package = root / "vehicle-project-package"
    package.mkdir(parents=True)
    for name, content in (
        ("vehicles.meta", VEHICLES_META),
        ("handling.meta", HANDLING_META),
        ("carvariations.meta", VARIATIONS_META),
        ("carcols.meta", CARCOLS_META),
        ("content.xml", CONTENT_XML),
    ):
        (package / name).write_text(content, encoding="utf-8")
    if complete:
        stream = package / "stream"
        stream.mkdir()
        (stream / "projectcar.yft").write_bytes(b"primary-fragment")
        (stream / "projectcar_hi.yft").write_bytes(b"high-detail-fragment")
        (stream / "projectcar.ytd").write_bytes(b"texture-dictionary")
        (package / "american_rel.rpf.gxt2").write_bytes(b"labels")
    return package


def test_vehicle_project_resolves_native_and_metadata_roles(tmp_path):
    project = VehicleProjectResolver().inspect(_package(tmp_path))

    assert project.edition == "Unresolved"
    assert len(project.models) == 1
    model = project.model("PROJECTCAR")
    assert model.primary_model == "stream/projectcar.yft"
    assert model.high_detail_model == "stream/projectcar_hi.yft"
    assert model.texture_asset == "stream/projectcar.ytd"
    assert model.ready_for_preview and model.complete
    roles = {item.role for item in model.assets}
    assert {
        "primary_model", "high_detail_model", "texture_dictionary",
        "vehicle_metadata", "handling_metadata", "variation_metadata",
        "tuning_metadata", "registration", "text_labels",
    } <= roles
    assert len(project.inventory_fingerprint) == 64


def test_vehicle_project_reports_missing_primary_fragment_and_exports_atomically(tmp_path):
    source = _package(tmp_path, complete=False)
    project = VehicleProjectResolver().inspect(source, edition="Enhanced")
    model = project.models[0]
    codes = {item.code for item in model.findings}
    assert "missing_vehicle_fragment" in codes
    assert not model.ready_for_preview and not model.complete

    destination = tmp_path / "published-project"
    manifest = project.write(destination)
    assert manifest == destination / "vehicle-project.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["edition"] == "Enhanced"
    assert payload["summary"]["models"] == 1
    assert (destination / "vehicle-project.md").is_file()

    try:
        project.write(destination)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("existing project output was overwritten")


def test_vehicle_project_cli_console_and_api_surface_share_the_resolver(tmp_path):
    source = _package(tmp_path)
    runner = CliRunner()
    inspected = runner.invoke(main, [
        "inspect-vehicle-project", str(source), "--model", "projectcar",
    ])
    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.output)
    assert payload["model"]["primary_model"] == "stream/projectcar.yft"

    destination = tmp_path / "cli-project"
    exported = runner.invoke(main, [
        "sdk", "export-vehicle-project", str(source), "-o", str(destination),
    ])
    assert exported.exit_code == 0, exported.output
    assert (destination / "vehicle-project.json").is_file()

    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["inspect-vehicle-project"]["risk"] == "read_only"
    assert catalog["export-vehicle-project"]["risk"] == "authoring_write"
    api_result = execute_request({
        "id": "vehicle-project", "action": "execute",
        "command": "inspect-vehicle-project",
        "args": [str(source), "--model", "projectcar"],
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert api_result["ok"] is True
    api_payload = json.loads(api_result["result"]["output"])
    assert api_payload["model"]["texture_asset"] == "stream/projectcar.ytd"
