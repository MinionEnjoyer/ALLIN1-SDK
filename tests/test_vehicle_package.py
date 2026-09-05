from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main
from allin1_sdk.mods import ModManifest
from allin1_sdk.vehicle_package import VehicleAddonPackageBuilder
from allin1_sdk.vehicle_authoring import (
    VehicleAuthoringWorkspace,
    VehicleTransmissionConfiguration,
)


def _prebuilt_package(root: Path, *, second: bool = False) -> Path:
    source = root / "downloaded-vehicle"
    pack = source / "rs5b10"
    pack.mkdir(parents=True)
    (pack / "dlc.rpf").write_bytes(b"RPF7-vehicle-payload")
    (pack / "vehicles.meta").write_text("""<CVehicleModelInfo__InitDataList>
  <InitDatas><Item><modelName>rs5b10</modelName><txdName>rs5b10</txdName>
  <handlingId>RS5B10</handlingId><gameName>RS5 B10</gameName>
  <vehicleMakeName>AUDI</vehicleMakeName><type>VEHICLE_TYPE_CAR</type>
  <vehicleClass>VC_SPORT</vehicleClass></Item></InitDatas>
</CVehicleModelInfo__InitDataList>""", encoding="utf-8")
    if second:
        other = source / "other"
        other.mkdir()
        (other / "dlc.rpf").write_bytes(b"RPF7-other-payload")
    return source


def test_vehicle_package_builder_publishes_valid_manifest_atomically(tmp_path):
    source = _prebuilt_package(tmp_path)
    destination = tmp_path / "published"

    result = VehicleAddonPackageBuilder(tmp_path).build(source, destination)

    assert result.pack_name == "rs5b10"
    assert result.mod_id == "vehicle.rs5b10"
    assert result.source_mode == "prebuilt_dlc_rpf"
    assert result.payload.read_bytes() == b"RPF7-vehicle-payload"
    manifest = ModManifest.load(result.manifest)
    assert manifest.dlc_packs == ("rs5b10",)
    assert manifest.dependencies == ("openrpf",)
    assert manifest.schema_version == 2
    assert manifest.mod_type == "mixed"
    assert str(manifest.package_requirements[0]) == "allin1.online-content>=0.5.5"
    assert manifest.files[0].destination.as_posix() == (
        "mods/update/x64/dlcpacks/rs5b10/dlc.rpf"
    )
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["status"] == "validated"
    assert report["safety"]["stock_game_files_modified"] is False
    assert report["payload"]["sha256"] == result.payload_sha256
    assert result.catalog.is_file()
    catalog_payload = json.loads(result.catalog.read_text(encoding="utf-8"))
    assert catalog_payload["vehicles"][0]["model"] == "rs5b10"
    assert catalog_payload["vehicles"][0]["traffic"]["enabled"] is False

    with pytest.raises(FileExistsError, match="already exists"):
        VehicleAddonPackageBuilder(tmp_path).build(source, destination)


def test_vehicle_package_builder_rejects_ambiguous_or_unbuildable_sources(tmp_path):
    ambiguous = _prebuilt_package(tmp_path / "ambiguous", second=True)
    with pytest.raises(ValueError, match="multiple dlc.rpf"):
        VehicleAddonPackageBuilder(tmp_path).build(
            ambiguous, tmp_path / "ambiguous-output",
        )

    loose = tmp_path / "loose" / "dlc.rpf.source"
    loose.mkdir(parents=True)
    (loose / "content.xml").write_text("<content />", encoding="utf-8")
    with pytest.raises(ValueError, match="requires a GTA V path"):
        VehicleAddonPackageBuilder(tmp_path).build(
            loose, tmp_path / "loose-output",
        )


def test_vehicle_package_cli_and_agent_api_use_the_same_guarded_builder(tmp_path):
    source = _prebuilt_package(tmp_path)
    runner = CliRunner()
    cli_output = tmp_path / "cli-package"
    invoked = runner.invoke(main, [
        "sdk", "build-vehicle-package", str(source),
        "-o", str(cli_output), "--pack-name", "testcar",
        "--mod-id", "example.testcar", "--edition", "enhanced",
    ])
    assert invoked.exit_code == 0, invoked.output
    payload = json.loads(invoked.output)
    assert payload["pack_name"] == "testcar"
    manifest = ModManifest.load(cli_output / "mod.toml")
    assert manifest.editions == ("enhanced",)

    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["build-vehicle-package"]["risk"] == "authoring_write"
    api_output = tmp_path / "api-package"
    response = execute_request({
        "id": "vehicle-package",
        "action": "execute",
        "command": "build-vehicle-package",
        "args": [str(source), "-o", str(api_output)],
    }, audit_path=tmp_path / "audit.jsonl")
    assert response["ok"] is True
    assert (api_output / "mod.toml").is_file()


def test_vehicle_package_preserves_authoring_profiles(tmp_path):
    source = _prebuilt_package(tmp_path)
    (source / "handling.meta").write_text("""<CHandlingDataMgr><HandlingData><Item>
<handlingName>RS5B10</handlingName><nInitialDriveGears value="6" />
</Item></HandlingData></CHandlingDataMgr>""", encoding="utf-8")
    authored_rpf = source / "dlc.rpf.source"
    authored_rpf.mkdir()
    (authored_rpf / "content.xml").write_text("<content />", encoding="utf-8")
    workspace = VehicleAuthoringWorkspace.create(
        source, tmp_path / "authoring-workspace",
    )
    workspace.set_transmission_configuration(VehicleTransmissionConfiguration(
        schema_version=1,
        vehicle_model="rs5b10",
        transmission_type="sequential",
        gear_ratios=(3.3, 2.1, 1.5, 1.12, 0.9, 0.74),
        reverse_gear_ratio=3.0,
        final_drive_ratio=3.6,
    ))

    result = VehicleAddonPackageBuilder(tmp_path).build(
        workspace.root, tmp_path / "profile-package",
    )

    assert result.profiles is not None
    profiles = json.loads(result.profiles.read_text(encoding="utf-8"))
    assert profiles["transmission_configurations"]["rs5b10"][
        "transmission_type"
    ] == "sequential"
    manifest = ModManifest.load(result.manifest)
    assert any(
        item.destination.as_posix()
        == "scripts/ALLIN1/VehicleProfiles/vehicle.rs5b10.json"
        for item in manifest.files
    )
