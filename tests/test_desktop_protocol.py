import io
import hashlib
import json
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import validate
from PIL import Image

from allin1_sdk import desktop_protocol
from allin1_sdk.desktop_protocol import (
    PROTOCOL_VERSION,
    DesktopProtocolService,
    envelope,
    run_job_worker,
    serve_stdio,
)
from allin1_sdk.axle_configurator import detect_axle_configuration


def request(operation, payload=None, request_id="request-1"):
    return envelope(
        operation, payload or {}, request_id=request_id, terminal=False,
    )


def handshake(service):
    response = service.handle(request("handshake", {
        "client": {"name": "desktop-test", "version": "0.1.0"},
        "supported_versions": [PROTOCOL_VERSION],
    }))[0]
    assert response["operation"] == "result"
    return response


def recipe_source(root: Path) -> Path:
    package = root / "recipe"
    content = package / "content"
    content.mkdir(parents=True)
    (package / "assembly.xml").write_text("""<package version="2.2">
<metadata><name>Desktop Recipe</name><version><major>1</major><minor>4</minor></version>
<author><displayName>ALLIN1 Test</displayName></author><gameversion>enhanced</gameversion></metadata>
<content>
  <add source="readme.txt">scripts/DesktopRecipe/readme.txt</add>
  <archive path="update/update.rpf">
    <add source="vehicles.meta">common/data/levels/gta5/vehicles.meta</add>
  </archive>
</content></package>""", encoding="utf-8")
    (content / "readme.txt").write_text("Desktop recipe", encoding="utf-8")
    (content / "vehicles.meta").write_text("<CVehicleModelInfo__InitDataList />", encoding="utf-8")
    return package


def lifecycle_source(root: Path) -> tuple[Path, Path, Path]:
    game = root / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    package = root / "managed-package"
    package.mkdir()
    payload = package / "DesktopLifecycle.dll"
    payload.write_bytes(b"desktop lifecycle payload")
    manifest = package / "mod.toml"
    manifest.write_text("\n".join((
        'schema_version = 1',
        'id = "desktop-lifecycle"',
        'name = "Desktop Lifecycle"',
        'version = "1.0.0"',
        'type = "script"',
        'editions = ["legacy"]',
        '[[files]]',
        'source = "DesktopLifecycle.dll"',
        'destination = "scripts/DesktopLifecycle.dll"',
    )), encoding="utf-8")
    return game, manifest, payload


def material_authoring_workspace(root: Path) -> Path:
    workspace = root / "material-workspace"
    original = workspace / "original"
    edit = workspace / "edit"
    original.mkdir(parents=True)
    edit.mkdir()
    source = b"RSC8" + b"\0" * 32
    (original / "fixture.ydr").write_bytes(source)
    xml = edit / "fixture.ydr.xml"
    xml.write_text("""<?xml version="1.0" encoding="utf-8"?>
<Drawable><Name>fixture_model</Name><ShaderGroup><Shaders>
<Item><Name>vehicle_paint</Name><Parameters>
<Item name="DiffuseSampler" type="Texture"><Name>fixture_d</Name></Item>
<Item name="specularIntensityMult" type="Vector" x="0.5" y="0" z="0" w="0"/>
<Item name="detailSettings" type="Array">
<Value x="1" y="0.72" z="0.18" w="0"/>
<Value x="4" y="2" z="1" w="0"/>
</Item>
</Parameters></Item>
<Item><Name>vehicle_glass</Name><Parameters>
<Item name="DiffuseSampler" type="Texture"><Name>fixture_glass</Name></Item>
</Parameters></Item>
</Shaders></ShaderGroup><DrawableModelsHigh><Item><Name>Body</Name><Geometries>
<Item><ShaderIndex value="0"/><VertexBuffer><Layout><Position/><TexCoord0/></Layout>
<Data>0 0 0 0 0
1 0 0 1 0
0 1 0 0 1</Data></VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer></Item>
</Geometries></Item></DrawableModelsHigh></Drawable>""", encoding="utf-8")
    (workspace / "native-workspace.json").write_text(json.dumps({
        "schema_version": 1,
        "operation": "native_asset_workspace",
        "edition": "Enhanced",
        "source": {
            "name": "fixture.ydr", "suffix": ".ydr", "size": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
            "snapshot": "original/fixture.ydr",
        },
        "xml": {
            "path": "edit/fixture.ydr.xml", "size": xml.stat().st_size,
            "base_sha256": hashlib.sha256(xml.read_bytes()).hexdigest(),
        },
        "dependencies": [],
    }, indent=2) + "\n", encoding="utf-8")
    from allin1_sdk.model_materials import MaterialAuthoringWorkspace

    return MaterialAuthoringWorkspace.initialize(workspace).root


def texture_authoring_workspace(root: Path) -> Path:
    workspace = root / "texture-workspace"
    original = workspace / "original"
    assets = workspace / "edit" / "assets"
    original.mkdir(parents=True)
    assets.mkdir(parents=True)
    source = original / "vehicle.ytd"
    source.write_bytes(b"RSC8 texture source")
    xml = workspace / "edit" / "vehicle.ytd.xml"
    xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<TextureDictionary><Item><Name>diffuse</Name><Unk32 value="0" />
<Usage>DEFAULT</Usage><UsageFlags>0</UsageFlags><ExtraFlags value="0" />
<Width value="16" /><Height value="8" /><MipLevels value="1" />
<Format>D3DFMT_A8R8G8B8</Format><FileName>diffuse.dds</FileName>
</Item></TextureDictionary>""", encoding="utf-8")
    Image.new("RGBA", (16, 8), (20, 90, 140, 255)).save(
        assets / "diffuse.dds", format="DDS",
    )
    (workspace / "native-workspace.json").write_text(json.dumps({
        "schema_version": 1, "operation": "native_asset_workspace",
        "edition": "Enhanced",
        "source": {
            "name": source.name, "suffix": ".ytd", "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "snapshot": "original/vehicle.ytd",
        },
        "xml": {"path": "edit/vehicle.ytd.xml"},
    }), encoding="utf-8")
    return workspace


def vehicle_authoring_source(root: Path) -> Path:
    source = root / "vehicle-authoring-source"
    source.mkdir()
    fixtures = {
        "vehicles.meta": """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>authorcar</modelName><txdName>authorcar</txdName>
<handlingId>AUTHORHAND</handlingId><gameName>AUTHORCAR</gameName>
<vehicleMakeName>AUTHOR</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
<vehicleClass>VC_SPORT</vehicleClass></Item></InitDatas></CVehicleModelInfo__InitDataList>""",
        "handling.meta": """<CHandlingDataMgr><HandlingData><Item>
<handlingName>AUTHORHAND</handlingName><fMass value=\"1500.0\" />
<fDriveBiasFront value=\"0.0\"/><strHandlingFlags>440010</strHandlingFlags>
<nInitialDriveGears value=\"6\"/><fInitialDriveForce value=\"0.30\"/>
<fInitialDriveMaxFlatVel value=\"160.0\"/><fBrakeForce value=\"0.8\"/>
<fSteeringLock value=\"40.0\"/></Item></HandlingData></CHandlingDataMgr>""",
        "carvariations.meta": """<CVehicleModelInfoVariation><variationData><Item>
<modelName>authorcar</modelName><colors/><kits><Item>123_authorkit</Item></kits>
<lightSettings value=\"1\"/><sirenSettings value=\"0\"/>
</Item></variationData></CVehicleModelInfoVariation>""",
        "carcols.meta": """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>123_authorkit</kitName><id value=\"123\"/><kitType>MKT_STANDARD</kitType>
<visibleMods><Item><modelName>author_spoiler</modelName>
<modShopLabel>AUTH_SPOILER_OLD</modShopLabel><type>VMT_SPOILER</type>
<bone>chassis</bone></Item></visibleMods><linkMods/><statMods/><slotNames/>
<liveryNames><Item>AUTH_LIVERY_1</Item></liveryNames>
</Item></Kits><Lights><Item><id value=\"1\"/><name>author_lights</name>
<headLight><intensity value=\"2.000000\"/></headLight>
</Item></Lights></CVehicleModelInfoVarGlobal>""",
        "content.xml": """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_authorcar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>""",
    }
    for name, content in fixtures.items():
        (source / name).write_text(content, encoding="utf-8")
    stream = source / "stream"
    stream.mkdir()
    (stream / "authorcar.yft").write_bytes(b"fragment")
    (stream / "authorcar.ytd").write_bytes(b"texture")
    (stream / "author_spoiler.yft").write_bytes(b"tuning-fragment")
    return source


def test_every_envelope_has_the_frozen_v1_shape():
    message = envelope(
        "result", {"value": 1}, request_id="shape", terminal=True,
    )
    assert set(message) == {
        "protocol_version", "request_id", "job_id", "operation", "payload",
        "sequence", "risk", "terminal",
    }
    assert message["protocol_version"] == "1.0.0"
    assert message["job_id"] is None


def test_frozen_schema_accepts_a_protocol_envelope():
    schema_path = Path(__file__).parents[1] / "docs" / "desktop-protocol-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validate(
        envelope("result", {"value": 1}, request_id="schema", terminal=True),
        schema,
    )


def test_handshake_is_required_and_versions_fail_closed():
    service = DesktopProtocolService()
    denied = service.handle(request("catalog"))[0]
    assert denied["operation"] == "error"
    assert "handshake" in denied["payload"]["message"]

    incompatible = service.handle(request("handshake", {
        "client": {"name": "old-client", "version": "0.0.1"},
        "supported_versions": ["0.9.0"],
    }))[0]
    assert incompatible["operation"] == "error"
    assert service.negotiated is False


def test_catalog_reuses_agent_risks_and_exposes_navigation():
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("catalog", request_id="catalog"))[0]
    payload = response["payload"]
    risks = {item["name"]: item["risk"] for item in payload["commands"]}
    assert risks["inspect-rpf"] == "read_only"
    assert risks["apply-rpf-plan"] == "game_write"
    assert [item["id"] for item in payload["navigation"]] == [
        "linker", "assets", "workbench", "receipts", "quick_import", "models", "rpf",
        "recipes", "data_tools", "help",
    ]
    assert any(item["key"] == "console" for item in payload["help_topics"])
    assert "preview_asset" in payload["operations"]
    assert "preview_asset" in payload["job_operations"]
    assert "inspect_rpf_archive" in payload["operations"]
    assert "inspect_rpf_archive" in payload["job_operations"]
    assert "inspect_vehicle_project" in payload["operations"]
    assert "inspect_vehicle_project" in payload["job_operations"]
    for operation in (
        "inspect_vehicle_authoring_workspace",
        "review_vehicle_authoring_workspace",
        "review_vehicle_authoring_edit",
        "review_vehicle_authoring_appearance",
        "inspect_vehicle_authoring_tuning",
        "review_vehicle_authoring_tuning",
        "review_vehicle_authoring_light_profile",
        "review_vehicle_authoring_axles",
        "inspect_vehicle_authoring_axle_skeleton",
        "review_vehicle_authoring_transmission",
    ):
        assert operation in payload["operations"]
        assert operation in payload["job_operations"]
    for operation in (
        "create_vehicle_authoring_workspace",
        "apply_vehicle_authoring_edit",
        "apply_vehicle_authoring_appearance",
        "apply_vehicle_authoring_tuning",
        "apply_vehicle_authoring_light_profile",
        "apply_vehicle_authoring_axles",
        "apply_vehicle_authoring_transmission",
        "apply_vehicle_authoring_history",
    ):
        assert operation in payload["operations"]
        assert operation not in payload["job_operations"]
    assert "inspect_recipe" in payload["operations"]
    assert "inspect_recipe" in payload["job_operations"]
    assert "inspect_package_receipts" in payload["operations"]
    assert "inspect_package_receipts" in payload["job_operations"]
    assert "review_package_lifecycle" in payload["operations"]
    assert "review_package_lifecycle" in payload["job_operations"]
    assert "apply_package_lifecycle" in payload["operations"]
    assert "apply_package_lifecycle" not in payload["job_operations"]
    assert "inspect_vehicle_quick_import" in payload["operations"]
    assert "inspect_vehicle_quick_import" in payload["job_operations"]
    assert "review_vehicle_quick_import" in payload["operations"]
    assert "review_vehicle_quick_import" in payload["job_operations"]
    assert "prepare_vehicle_quick_import" in payload["operations"]
    assert "prepare_vehicle_quick_import" not in payload["job_operations"]


def test_rpf_archive_inspection_returns_bounded_recursive_index(
    tmp_path, monkeypatch,
):
    from allin1_sdk.rpf_tools import (
        RpfArchiveRecord,
        RpfEntryRecord,
        RpfIndex,
    )

    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 fixture")

    class FakeRpfExplorerService:
        def __init__(self, _project_root, selected_game):
            assert Path(selected_game) == game

        def index(self, selected_archive):
            assert Path(selected_archive) == archive
            return RpfIndex(
                source=archive.resolve(),
                edition="enhanced",
                archive_size=archive.stat().st_size,
                archives=(
                    RpfArchiveRecord("", "update.rpf", 7, "none", 11, 3),
                    RpfArchiveRecord(
                        "x64/data.rpf", "data.rpf", 7, "none", 7, 1,
                    ),
                ),
                entries=(
                    RpfEntryRecord(
                        "::common", "", "common", "common", "directory",
                        0, 0, child_count=1,
                    ),
                    RpfEntryRecord(
                        "::common/data/handling.meta", "",
                        "common/data/handling.meta", "handling.meta", "binary",
                        128, 96, compressed=True,
                    ),
                    RpfEntryRecord(
                        "::x64/data.rpf", "", "x64/data.rpf", "data.rpf",
                        "archive", 7, 7,
                    ),
                    RpfEntryRecord(
                        "x64/data.rpf::textures/vehicle.ytd", "x64/data.rpf",
                        "textures/vehicle.ytd", "vehicle.ytd", "resource",
                        512, 400, resource_version=13,
                    ),
                ),
                warnings=("One nested archive was indexed recursively.",),
            )

    monkeypatch.setattr(
        "allin1_sdk.rpf_tools.RpfExplorerService", FakeRpfExplorerService,
    )
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("inspect_rpf_archive", {
        "archive": str(archive), "gta_path": str(game),
    }, "rpf-index"))[0]
    assert response["operation"] == "result", response["payload"]
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "rpf_archive_index"
    assert result["archive_count"] == 2
    assert result["entry_count"] == 4
    assert result["directory_count"] == 1
    assert result["file_count"] == 3
    assert result["suffix_counts"] == {".meta": 1, ".rpf": 1, ".ytd": 1}
    assert result["entries"][3]["id"] == (
        "x64/data.rpf::textures/vehicle.ytd"
    )
    assert result["read_only"] is True
    assert result["game_write_performed"] is False


def test_vehicle_project_inspection_returns_models_assets_and_findings(
    tmp_path, monkeypatch,
):
    from allin1_sdk.rage_data_compiler import VehicleDataFinding
    from allin1_sdk.vehicle_project import (
        VehicleAssetBinding,
        VehicleProject,
        VehicleProjectModel,
    )

    source = tmp_path / "vehicle-package"
    source.mkdir()
    model = VehicleProjectModel(
        model="comet6",
        display_name="Comet S2",
        make_name="Pfister",
        vehicle_class="Sports",
        vehicle_type="Automobile",
        handling_id="COMET6",
        layout="LAYOUT_LOW",
        audio_name_hash="COMET6",
        texture_dictionary="comet6",
        tuning_kits=("123_comet6_modkit",),
        assets=(
            VehicleAssetBinding(
                "primary_model", "x64/vehicles/comet6.yft", 2048, True, True,
            ),
            VehicleAssetBinding(
                "texture_dictionary", "x64/vehicles/comet6.ytd", 1024,
                True, True,
            ),
        ),
        findings=(
            VehicleDataFinding(
                "warning", "optional_hi_missing", "comet6",
                "No high-detail fragment was linked.",
            ),
        ),
    )
    project = VehicleProject(
        source=source.resolve(),
        source_kind="folder",
        edition="enhanced",
        inventory_fingerprint="a" * 64,
        models=(model,),
        findings=model.findings,
    )

    class FakeResolver:
        def inspect(self, selected_source, **kwargs):
            assert Path(selected_source) == source.resolve()
            assert kwargs["project_root"].is_dir()
            assert kwargs["gta_path"] is None
            assert kwargs["edition"] == "enhanced"
            return project

    monkeypatch.setattr(
        "allin1_sdk.vehicle_project.VehicleProjectResolver", FakeResolver,
    )
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("inspect_vehicle_project", {
        "source": str(source), "edition": "Enhanced",
    }, "vehicle-project"))[0]
    assert response["operation"] == "result", response["payload"]
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "vehicle_project_inspection"
    assert result["model_count"] == 1
    assert result["asset_count"] == 2
    assert result["previewable_count"] == 1
    assert result["complete_count"] == 1
    assert result["models"][0]["model"] == "comet6"
    assert result["models"][0]["assets"][1]["role"] == "texture_dictionary"
    assert result["models"][0]["findings"][0]["code"] == "optional_hi_missing"
    assert result["read_only"] is True
    assert result["package_write_performed"] is False
    assert result["game_write_performed"] is False


def test_vehicle_authoring_protocol_reviews_creates_edits_and_restores(tmp_path):
    source = vehicle_authoring_source(tmp_path)
    original_vehicle = (source / "vehicles.meta").read_bytes()
    service = DesktopProtocolService(allow_package_writes=True)
    handshake(service)
    workspace_payload = {
        "source": str(source),
        "parent": str(tmp_path),
        "name": "desktop-authoring-copy",
        "model": "authorcar",
    }

    reviewed = service.handle(request(
        "review_vehicle_authoring_workspace", workspace_payload, "authoring-review",
    ))[0]
    assert reviewed["operation"] == "result", reviewed["payload"]
    assert reviewed["risk"] == "read_only"
    workspace_review = reviewed["payload"]["result"]
    destination = Path(workspace_review["destination"])
    assert workspace_review["model_count"] == 1
    assert workspace_review["workspace_write_performed"] is False
    assert not destination.exists()

    denied = service.handle(request(
        "create_vehicle_authoring_workspace", {
            **workspace_payload,
            "review_sha256": workspace_review["review_sha256"],
        }, "authoring-create-denied",
    ))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "authoring_write"
    assert not destination.exists()

    created = service.handle(request(
        "create_vehicle_authoring_workspace", {
            **workspace_payload,
            "review_sha256": workspace_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-create",
    ))[0]
    assert created["operation"] == "result", created["payload"]
    session = created["payload"]["result"]
    assert session["kind"] == "vehicle_authoring_session"
    assert session["revision"] == 0
    assert session["selected_model"] == "authorcar"
    assert session["values"]["handling.fMass"] == "1500.0"
    assert session["appearance"]["colors"] == []
    assert session["appearance"]["kits"] == ["123_authorkit"]
    assert session["transmission"] is None
    assert session["game_write_performed"] is False

    edit_payload = {
        "workspace": str(destination),
        "model": "authorcar",
        "expected_revision": 0,
        "updates": {
            "vehicle.gameName": "AUTHORCAR_DESKTOP",
            "handling.fMass": "1625.5",
        },
    }
    edit_reviewed = service.handle(request(
        "review_vehicle_authoring_edit", edit_payload, "authoring-edit-review",
    ))[0]
    edit_review = edit_reviewed["payload"]["result"]
    assert edit_reviewed["risk"] == "read_only"
    assert len(edit_review["changes"]) == 2
    assert edit_review["changes"][0].keys() == {"field", "before", "after"}
    assert session["revision"] == 0

    stale = service.handle(request(
        "apply_vehicle_authoring_edit", {
            **edit_payload,
            "review_sha256": "0" * 64,
            "authoring_confirmed": True,
        }, "authoring-edit-stale",
    ))[0]
    assert stale["operation"] == "error"
    assert stale["risk"] == "authoring_write"

    applied = service.handle(request(
        "apply_vehicle_authoring_edit", {
            **edit_payload,
            "review_sha256": edit_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-edit-apply",
    ))[0]
    session = applied["payload"]["result"]
    assert session["revision"] == 1
    assert session["values"]["vehicle.gameName"] == "AUTHORCAR_DESKTOP"
    assert session["values"]["handling.fMass"] == "1625.5"
    assert session["can_undo"] is True
    assert (source / "vehicles.meta").read_bytes() == original_vehicle

    inspected = service.handle(request(
        "inspect_vehicle_authoring_workspace", {
            "workspace": str(destination), "model": "authorcar",
        }, "authoring-inspect",
    ))[0]
    assert inspected["payload"]["result"]["revision"] == 1

    undone = service.handle(request(
        "apply_vehicle_authoring_history", {
            "workspace": str(destination), "model": "authorcar",
            "expected_revision": 1, "direction": "undo",
            "authoring_confirmed": True,
        }, "authoring-undo",
    ))[0]
    session = undone["payload"]["result"]
    assert session["revision"] == 2
    assert session["values"]["vehicle.gameName"] == "AUTHORCAR"
    assert session["can_redo"] is True

    redone = service.handle(request(
        "apply_vehicle_authoring_history", {
            "workspace": str(destination), "model": "authorcar",
            "expected_revision": 2, "direction": "redo",
            "authoring_confirmed": True,
        }, "authoring-redo",
    ))[0]
    session = redone["payload"]["result"]
    assert session["revision"] == 3
    assert session["values"]["vehicle.gameName"] == "AUTHORCAR_DESKTOP"
    assert session["game_write_performed"] is False

    appearance_payload = {
        "workspace": str(destination),
        "model": "authorcar",
        "expected_revision": 3,
        "appearance": {
            "colors": [{
                "indices": [20, 21, 22, 23],
                "liveries": [False, True],
            }],
            "kits": ["123_authorkit"],
            "light_settings": "1",
            "siren_settings": "8",
        },
    }
    appearance_reviewed = service.handle(request(
        "review_vehicle_authoring_appearance", appearance_payload,
        "authoring-appearance-review",
    ))[0]
    assert appearance_reviewed["risk"] == "read_only"
    appearance_review = appearance_reviewed["payload"]["result"]
    assert appearance_review["kind"] == "vehicle_authoring_appearance_review"
    assert len(appearance_review["changes"]) == 2
    assert appearance_review["workspace_write_performed"] is False

    appearance_applied = service.handle(request(
        "apply_vehicle_authoring_appearance", {
            **appearance_payload,
            "review_sha256": appearance_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-appearance-apply",
    ))[0]
    assert appearance_applied["risk"] == "authoring_write"
    session = appearance_applied["payload"]["result"]
    assert session["revision"] == 4
    assert session["appearance"]["colors"][0]["indices"] == [20, 21, 22, 23]
    assert session["appearance"]["siren_settings"] == "8"
    assert (source / "carvariations.meta").read_bytes() != \
        (destination / "source" / "carvariations.meta").read_bytes()

    tuning_inspected = service.handle(request(
        "inspect_vehicle_authoring_tuning", {
            "workspace": str(destination), "model": "authorcar",
            "kit_name": "123_authorkit",
        }, "authoring-tuning-inspect",
    ))[0]
    tuning = tuning_inspected["payload"]["result"]
    assert tuning_inspected["risk"] == "read_only"
    assert tuning["entries"][0]["fields"]["modShopLabel"] == "AUTH_SPOILER_OLD"
    assert tuning["field_schemas"]["visibleMods"]["modelName"]["required"] is True

    tuning_payload = {
        "workspace": str(destination), "model": "authorcar",
        "expected_revision": 4,
        "mutation": {
            "action": "update_entry", "kit_name": "123_authorkit",
            "collection": "visibleMods", "index": 0,
            "values": {"modShopLabel": "AUTH_SPOILER_DESKTOP"},
        },
    }
    tuning_reviewed = service.handle(request(
        "review_vehicle_authoring_tuning", tuning_payload, "authoring-tuning-review",
    ))[0]
    tuning_review = tuning_reviewed["payload"]["result"]
    assert tuning_reviewed["risk"] == "read_only"
    assert tuning_review["changes"][0]["action"] == "update"
    tuning_applied = service.handle(request(
        "apply_vehicle_authoring_tuning", {
            **tuning_payload, "review_sha256": tuning_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-tuning-apply",
    ))[0]
    session = tuning_applied["payload"]["result"]
    assert session["revision"] == 5
    assert session["tuning_builder"]["entries"][0]["fields"]["modShopLabel"] == \
        "AUTH_SPOILER_DESKTOP"

    light_payload = {
        "workspace": str(destination), "model": "authorcar",
        "expected_revision": 5, "profile_id": "1",
        "updates": {"headLight.intensity": "3.250000"},
    }
    light_reviewed = service.handle(request(
        "review_vehicle_authoring_light_profile", light_payload,
        "authoring-light-review",
    ))[0]
    light_review = light_reviewed["payload"]["result"]
    assert light_reviewed["risk"] == "read_only"
    assert light_review["changes"][0]["before"] == "2.000000"
    light_applied = service.handle(request(
        "apply_vehicle_authoring_light_profile", {
            **light_payload, "review_sha256": light_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-light-apply",
    ))[0]
    session = light_applied["payload"]["result"]
    assert session["revision"] == 6
    assert session["appearance"]["light_profiles"][0]["values"][
        "headLight.intensity"
    ] == "3.250000"
    assert (source / "carcols.meta").read_bytes() != \
        (destination / "source" / "carcols.meta").read_bytes()

    axle_bones = (
        SimpleNamespace(name="wheel_lf", position=(-1.0, 3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_rf", position=(1.0, 3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_lr", position=(-1.0, -3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_rr", position=(1.0, -3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
    )
    axle_configuration = detect_axle_configuration(
        "authorcar", axle_bones,
    ).to_dict()
    axle_payload = {
        "workspace": str(destination), "model": "authorcar",
        "expected_revision": 6, "configuration": axle_configuration,
    }
    axle_reviewed = service.handle(request(
        "review_vehicle_authoring_axles", axle_payload,
        "authoring-axle-review",
    ))[0]
    assert axle_reviewed["risk"] == "read_only"
    axle_review = axle_reviewed["payload"]["result"]
    assert axle_review["kind"] == "vehicle_authoring_axle_review"
    assert axle_review["configuration"]["expected_wheel_count"] == 4
    assert axle_review["changes"][0]["field"] == "axles.configuration"
    assert axle_review["workspace_write_performed"] is False
    axle_applied = service.handle(request(
        "apply_vehicle_authoring_axles", {
            **axle_payload, "review_sha256": axle_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-axle-apply",
    ))[0]
    assert axle_applied["risk"] == "authoring_write"
    session = axle_applied["payload"]["result"]
    assert session["revision"] == 7
    assert session["project"]["axle_configurations"][0]["vehicle_model"] == \
        "authorcar"
    assert session["game_write_performed"] is False

    transmission_payload = {
        "workspace": str(destination), "model": "authorcar",
        "expected_revision": 7,
        "configuration": {
            "schema_version": 1,
            "vehicle_model": "authorcar",
            "transmission_type": "dual_clutch",
            "gear_ratios": [3.4, 2.3, 1.7, 1.3, 1.05, 0.84, 0.7],
            "reverse_gear_ratio": 3.1,
            "final_drive_ratio": 3.5,
        },
    }
    transmission_reviewed = service.handle(request(
        "review_vehicle_authoring_transmission", transmission_payload,
        "authoring-transmission-review",
    ))[0]
    assert transmission_reviewed["risk"] == "read_only"
    transmission_review = transmission_reviewed["payload"]["result"]
    assert transmission_review["configuration"]["transmission_type"] == \
        "dual_clutch"
    assert {change["field"] for change in transmission_review["changes"]} == {
        "transmission.configuration", "handling.nInitialDriveGears",
    }
    transmission_applied = service.handle(request(
        "apply_vehicle_authoring_transmission", {
            **transmission_payload,
            "review_sha256": transmission_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-transmission-apply",
    ))[0]
    session = transmission_applied["payload"]["result"]
    assert session["revision"] == 8
    assert session["transmission"]["gear_ratios"][-1] == 0.7
    assert session["values"]["handling.nInitialDriveGears"] == "7"

    distribution_payload = {
        "workspace": str(destination), "model": "authorcar",
        "expected_revision": 8,
        "updates": {
            "name": "Author Roadster", "price": 145000,
            "traffic_enabled": True, "traffic_weight": 0.6,
        },
    }
    distribution_reviewed = service.handle(request(
        "review_vehicle_authoring_distribution", distribution_payload,
        "authoring-distribution-review",
    ))[0]
    assert distribution_reviewed["risk"] == "read_only"
    distribution_review = distribution_reviewed["payload"]["result"]
    assert distribution_review["distribution"]["price"] == 145000
    assert distribution_review["workspace_write_performed"] is False
    distribution_applied = service.handle(request(
        "apply_vehicle_authoring_distribution", {
            **distribution_payload,
            "review_sha256": distribution_review["review_sha256"],
            "authoring_confirmed": True,
        }, "authoring-distribution-apply",
    ))[0]
    session = distribution_applied["payload"]["result"]
    assert session["revision"] == 9
    assert session["distribution"]["traffic_enabled"] is True
    assert session["distribution"]["traffic_weight"] == 0.6


def test_vehicle_package_build_is_reviewed_and_preserves_profiles(tmp_path):
    source = vehicle_authoring_source(tmp_path)
    pack = source / "authorcar-pack"
    pack.mkdir()
    (pack / "dlc.rpf").write_bytes(b"RPF7-desktop-vehicle")
    authored_rpf = source / "dlc.rpf.source"
    authored_rpf.mkdir()
    (authored_rpf / "content.xml").write_text("<content />", encoding="utf-8")
    from allin1_sdk.vehicle_authoring import (
        VehicleAuthoringWorkspace, VehicleTransmissionConfiguration,
    )

    workspace = VehicleAuthoringWorkspace.create(
        source, tmp_path / "package-authoring-copy",
    )
    workspace.set_transmission_configuration(VehicleTransmissionConfiguration(
        schema_version=1, vehicle_model="authorcar",
        transmission_type="manual",
        gear_ratios=(3.2, 2.0, 1.4, 1.05, 0.82, 0.68),
        reverse_gear_ratio=3.0, final_drive_ratio=3.5,
    ))
    destination = tmp_path / "desktop-built-package"
    payload = {
        "workspace": str(workspace.root),
        "expected_revision": 1,
        "destination": str(destination),
        "pack_name": "authorcar",
        "mod_id": "vehicle.authorcar",
        "name": "Author Roadster",
        "version": "1.2.0",
        "editions": ["enhanced"],
    }
    service = DesktopProtocolService(allow_package_writes=True)
    handshake(service)
    reviewed = service.handle(request(
        "review_vehicle_package_build", payload, "vehicle-build-review",
    ))[0]
    assert reviewed["operation"] == "result", reviewed["payload"]
    assert reviewed["risk"] == "read_only"
    review = reviewed["payload"]["result"]
    assert review["ready"] is True
    assert review["authoring_profiles"]["transmission_configurations"][
        "authorcar"
    ]["transmission_type"] == "manual"
    assert not destination.exists()

    built = service.handle(request(
        "apply_vehicle_package_build", {
            **payload, "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        }, "vehicle-build-apply",
    ))[0]
    assert built["risk"] == "authoring_write"
    result = built["payload"]["result"]
    assert result["package_write_performed"] is True
    assert result["game_write_performed"] is False
    assert Path(result["package"]["profiles"]).is_file()
    assert destination.is_dir()


def test_vehicle_axle_skeleton_protocol_detects_and_signs_steering(
    tmp_path, monkeypatch,
):
    source = vehicle_authoring_source(tmp_path)
    from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

    workspace = VehicleAuthoringWorkspace.create(
        source, tmp_path / "skeleton-authoring-copy",
    )
    bones = (
        SimpleNamespace(name="wheel_lf", position=(-1.0, 3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_rf", position=(1.0, 3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_lm1", position=(-1.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_rm1", position=(1.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_lr", position=(-1.0, -3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
        SimpleNamespace(name="wheel_rr", position=(1.0, -3.0, 0.0), scale=(1.0, 1.0, 1.0), rotation=(0.0, 0.0, 0.0, 1.0)),
    )
    skeleton = tmp_path / "authorcar.yft.xml"
    skeleton.write_text("<fixture/>", encoding="utf-8")
    monkeypatch.setattr(
        "allin1_sdk.native_assets.load_native_model_scene",
        lambda _path: (SimpleNamespace(bones=bones), {}, None),
    )
    service = DesktopProtocolService(allow_package_writes=True)
    handshake(service)
    base_payload = {
        "workspace": str(workspace.root),
        "model": "authorcar",
        "expected_revision": 0,
        "skeleton_xml": str(skeleton),
    }
    detected = service.handle(request(
        "inspect_vehicle_authoring_axle_skeleton",
        {
            **base_payload, "action": "detect",
            "preset": "Steer → Drive → Rear Steer",
        },
        "authoring-skeleton-detect",
    ))[0]
    assert detected["operation"] == "result", detected["payload"]
    evidence = detected["payload"]["result"]
    assert evidence["bone_count"] == 6
    assert len(evidence["configuration"]["axles"]) == 3
    assert len(evidence["bone_position_sha256"]) == 64

    signed = service.handle(request(
        "inspect_vehicle_authoring_axle_skeleton",
        {
            **base_payload,
            "action": "steering",
            "configuration": evidence["configuration"],
            "request": {"reference_lock_degrees": 35.0},
        },
        "authoring-skeleton-steering",
    ))[0]
    proposal = signed["payload"]["result"]
    assert proposal["solution"]["reference_lock_degrees"] == 35.0
    assert proposal["configuration"]["axles"][0]["steering_gain"] == 1.0
    assert proposal["configuration"]["axles"][2]["steering_gain"] < 0.0
    assert proposal["workspace_write_performed"] is False


def test_execute_delegates_to_the_existing_agent_api():
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("execute", {
        "command": "list-axle-prefabs", "args": [],
    }, "execute"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "read_only"
    assert response["payload"]["result"]["exit_code"] == 0


def test_desktop_process_cannot_enable_game_writes_from_a_request(tmp_path):
    service = DesktopProtocolService(allow_game_writes=False)
    handshake(service)
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    response = service.handle(request("execute", {
        "command": "apply-rpf-plan",
        "args": [str(plan), "--acknowledge-write"],
        "allow_game_writes": True,
    }, "mutation"))[0]
    assert response["operation"] == "error"
    assert response["risk"] == "game_write"
    assert "disabled" in response["payload"]["message"]


def test_manifest_inspection_uses_the_python_linker(tmp_path):
    manifest = tmp_path / "addon.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
            "id": "test.desktop",
        "name": "Desktop Test",
        "version": "1.0.0",
        "summary": "Protocol test",
        "editions": ["enhanced"],
        "nodes": [{
            "id": "package.main", "kind": "package", "label": "Package",
            "description": "", "source": None,
            "fields": {
                "Registration": "dlclist.xml", "Edition": "enhanced",
                "Safety": "reviewed",
            },
        }],
        "references": [],
        "install_steps": [],
    }), encoding="utf-8")
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request(
        "inspect_package", {"source": str(manifest)}, "inspect",
    ))[0]
    result = response["payload"]["result"]
    assert result["kind"] == "manifest"
    assert result["id"] == "test.desktop"
    assert result["valid"] is True
    assert result["nodes"][0]["kind"] == "package"


def test_package_receipt_inventory_and_selected_ownership_are_typed_and_read_only(
    tmp_path,
):
    game = tmp_path / "Grand Theft Auto V Enhanced"
    managed_file = game / "scripts" / "Demo.asi"
    receipt_root = game / "scripts" / ".allin1" / "mods"
    receipt_root.mkdir(parents=True)
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    managed_file.write_bytes(b"managed payload")
    digest = hashlib.sha256(managed_file.read_bytes()).hexdigest()
    (receipt_root / "allin1.demo.json").write_text(json.dumps({
        "id": "allin1.demo",
        "name": "Desktop Demo",
        "version": "1.2.0",
        "type": "mixed",
        "enabled": True,
        "installed_at": "2026-08-29T18:42:11+00:00",
        "files": [{
            "destination": "scripts/Demo.asi",
            "sha256": digest,
            "backup": None,
        }],
        "rpf_entries": [],
    }), encoding="utf-8")

    service = DesktopProtocolService()
    handshake(service)
    inventory = service.handle(request("inspect_package_receipts", {
        "gta_path": str(game),
    }, "receipt-list"))[0]
    assert inventory["operation"] == "result"
    assert inventory["risk"] == "read_only"
    listed = inventory["payload"]["result"]
    assert listed["kind"] == "package_receipt_inventory"
    assert listed["edition"] == "enhanced"
    assert listed["package_count"] == 1
    assert listed["packages"][0]["mod_id"] == "allin1.demo"
    assert listed["receipt"] is None
    assert listed["game_write_performed"] is False

    selected = service.handle(request("inspect_package_receipts", {
        "gta_path": str(game), "selected_id": "allin1.demo",
    }, "receipt-selected"))[0]["payload"]["result"]
    assert selected["receipt"]["installed_at"] == "2026-08-29T18:42:11+00:00"
    assert selected["verification"]["healthy"] is True
    assert selected["verification"]["ownership_verified"] is True
    assert selected["verification"]["checks"][0]["hash_matches"] is True

    managed_file.write_bytes(b"tampered")
    damaged = service.handle(request("inspect_package_receipts", {
        "gta_path": str(game), "selected_id": "allin1.demo",
    }, "receipt-damaged"))[0]["payload"]["result"]
    assert damaged["verification"]["healthy"] is False
    assert damaged["issue_count"] == 1
    assert "externally changed" in damaged["verification"]["issues"][0]


def test_package_lifecycle_review_is_digest_bound_and_performs_no_game_write(
    tmp_path,
):
    from allin1_sdk.mods import ModIntegrationService, ModManifest

    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    package = tmp_path / "package"
    package.mkdir()
    payload = package / "DesktopReview.dll"
    payload.write_bytes(b"reviewed package payload")
    manifest_path = package / "mod.toml"
    manifest_path.write_text("\n".join((
        'schema_version = 1',
        'id = "desktop-review"',
        'name = "Desktop Review"',
        'version = "1.0.0"',
        'type = "script"',
        'editions = ["legacy"]',
        '[[files]]',
        'source = "DesktopReview.dll"',
        'destination = "scripts/DesktopReview.dll"',
    )), encoding="utf-8")

    service = DesktopProtocolService()
    handshake(service)
    install_review = service.handle(request("review_package_lifecycle", {
        "action": "install", "gta_path": str(game),
        "source": str(manifest_path),
    }, "install-review"))[0]
    assert install_review["operation"] == "result", install_review["payload"]
    assert install_review["risk"] == "read_only"
    reviewed = install_review["payload"]["result"]
    assert reviewed["kind"] == "package_lifecycle_review"
    assert reviewed["action"] == "install"
    assert reviewed["ready"] is True
    assert reviewed["operations"][0]["disposition"] == "create"
    assert reviewed["review_only"] is True
    assert reviewed["game_write_required"] is True
    assert reviewed["game_write_performed"] is False
    assert len(reviewed["review_sha256"]) == 64
    assert not (game / "scripts" / "DesktopReview.dll").exists()
    assert not (game / "scripts" / ".allin1").exists()

    integration = ModIntegrationService(game)
    integration.install(ModManifest.load(manifest_path))
    uninstall_review = service.handle(request("review_package_lifecycle", {
        "action": "uninstall", "gta_path": str(game),
        "mod_id": "desktop-review",
    }, "uninstall-review"))[0]["payload"]["result"]
    assert uninstall_review["action"] == "uninstall"
    assert uninstall_review["ready"] is True
    assert uninstall_review["operations"][0]["disposition"] == "remove"
    assert (game / "scripts" / "DesktopReview.dll").is_file()

    (game / "scripts" / "DesktopReview.dll").write_bytes(b"tampered")
    blocked = service.handle(request("review_package_lifecycle", {
        "action": "uninstall", "gta_path": str(game),
        "mod_id": "desktop-review",
    }, "uninstall-blocked"))[0]["payload"]["result"]
    assert blocked["ready"] is False
    assert blocked["findings"][0]["code"] == "ownership_invalid"
    assert (game / "scripts" / "DesktopReview.dll").is_file()


def test_package_lifecycle_execution_revalidates_and_reports_rollback(
    tmp_path, monkeypatch,
):
    import allin1_sdk.rpf_tools as rpf_tools

    game, manifest, _payload = lifecycle_source(tmp_path)
    audit = tmp_path / "lifecycle-audit.jsonl"
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ())
    service = DesktopProtocolService(allow_package_writes=True, audit_path=audit)
    negotiated = handshake(service)
    assert negotiated["payload"]["package_writes_enabled"] is True
    assert negotiated["payload"]["game_writes_enabled"] is False

    install_payload = {
        "action": "install", "gta_path": str(game), "source": str(manifest),
    }
    install_review = service.handle(request(
        "review_package_lifecycle", install_payload, "execution-install-review",
    ))[0]["payload"]["result"]
    install = service.handle(request("apply_package_lifecycle", {
        **install_payload,
        "review_sha256": install_review["review_sha256"],
        "confirmation_id": "desktop-lifecycle",
        "game_write_confirmed": True,
        "replace_confirmed": False,
    }, "execution-install"))[0]
    assert install["operation"] == "result", install["payload"]
    assert install["risk"] == "game_write"
    installed = install["payload"]["result"]
    assert installed["kind"] == "package_lifecycle_execution"
    assert installed["status"] == "installed"
    assert installed["review_sha256"] == install_review["review_sha256"]
    assert installed["process_check"] == {
        "gta_closed": True, "running_processes": [],
    }
    assert installed["rollback"]["receipt_written"] is True
    assert installed["rollback"]["ownership_verified"] is True
    managed = game / "scripts" / "DesktopLifecycle.dll"
    assert managed.read_bytes() == b"desktop lifecycle payload"

    uninstall_payload = {
        "action": "uninstall", "gta_path": str(game),
        "mod_id": "desktop-lifecycle",
    }
    uninstall_review = service.handle(request(
        "review_package_lifecycle", uninstall_payload, "execution-uninstall-review",
    ))[0]["payload"]["result"]
    uninstall = service.handle(request("apply_package_lifecycle", {
        **uninstall_payload,
        "review_sha256": uninstall_review["review_sha256"],
        "confirmation_id": "desktop-lifecycle",
        "game_write_confirmed": True,
    }, "execution-uninstall"))[0]
    assert uninstall["operation"] == "result", uninstall["payload"]
    removed = uninstall["payload"]["result"]
    assert removed["status"] == "uninstalled"
    assert removed["rollback"]["receipt_removed"] is True
    assert removed["rollback"]["removed_payload_count"] == 1
    assert not managed.exists()
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [record["completed"] for record in records] == [True, True]


def test_package_lifecycle_enable_disable_uses_reviewed_transaction(
    tmp_path, monkeypatch,
):
    import allin1_sdk.rpf_tools as rpf_tools
    from allin1_sdk.mods import ModIntegrationService, ModManifest

    game, manifest, _payload = lifecycle_source(tmp_path)
    integration = ModIntegrationService(game)
    integration.install(ModManifest.load(manifest))
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ())
    service = DesktopProtocolService(allow_package_writes=True)
    handshake(service)
    managed = game / "scripts" / "DesktopLifecycle.dll"
    disabled_path = managed.with_name(managed.name + ".disabled")

    disable_payload = {
        "action": "disable", "gta_path": str(game),
        "mod_id": "desktop-lifecycle",
    }
    disable_review = service.handle(request(
        "review_package_lifecycle", disable_payload, "disable-review",
    ))[0]["payload"]["result"]
    assert disable_review["ready"] is True
    assert disable_review["current_enabled"] is True
    assert disable_review["target_enabled"] is False
    assert disable_review["operations"][0]["disposition"] == "disable_file"

    disabled = service.handle(request("apply_package_lifecycle", {
        **disable_payload,
        "review_sha256": disable_review["review_sha256"],
        "confirmation_id": "desktop-lifecycle",
        "game_write_confirmed": True,
    }, "disable-apply"))[0]
    assert disabled["operation"] == "result", disabled["payload"]
    disabled_result = disabled["payload"]["result"]
    assert disabled_result["status"] == "disabled"
    assert disabled_result["postcondition"]["enabled"] is False
    assert disabled_result["rollback"]["receipt_state_updated"] is True
    assert disabled_result["rollback"]["ownership_verified"] is True
    assert not managed.exists()
    assert disabled_path.is_file()

    enable_payload = {
        "action": "enable", "gta_path": str(game),
        "mod_id": "desktop-lifecycle",
    }
    enable_review = service.handle(request(
        "review_package_lifecycle", enable_payload, "enable-review",
    ))[0]["payload"]["result"]
    assert enable_review["ready"] is True
    assert enable_review["current_enabled"] is False
    assert enable_review["target_enabled"] is True
    assert enable_review["operations"][0]["disposition"] == "enable_file"

    enabled = service.handle(request("apply_package_lifecycle", {
        **enable_payload,
        "review_sha256": enable_review["review_sha256"],
        "confirmation_id": "desktop-lifecycle",
        "game_write_confirmed": True,
    }, "enable-apply"))[0]
    assert enabled["operation"] == "result", enabled["payload"]
    enabled_result = enabled["payload"]["result"]
    assert enabled_result["status"] == "enabled"
    assert enabled_result["postcondition"]["enabled"] is True
    assert enabled_result["rollback"]["ownership_verified"] is True
    assert managed.read_bytes() == b"desktop lifecycle payload"
    assert not disabled_path.exists()


def test_package_lifecycle_execution_fails_closed_on_drift_authority_and_process(
    tmp_path, monkeypatch,
):
    import allin1_sdk.rpf_tools as rpf_tools

    game, manifest, payload_file = lifecycle_source(tmp_path)
    review_service = DesktopProtocolService()
    handshake(review_service)
    base = {"action": "install", "gta_path": str(game), "source": str(manifest)}
    review = review_service.handle(request(
        "review_package_lifecycle", base, "guard-review",
    ))[0]["payload"]["result"]
    confirmed = {
        **base,
        "review_sha256": review["review_sha256"],
        "confirmation_id": "desktop-lifecycle",
        "game_write_confirmed": True,
        "replace_confirmed": False,
    }

    denied = review_service.handle(request(
        "apply_package_lifecycle", confirmed, "guard-authority",
    ))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "game_write"
    assert "process owner" in denied["payload"]["message"]

    writable = DesktopProtocolService(allow_package_writes=True)
    handshake(writable)
    payload_file.write_bytes(b"changed after review")
    drifted = writable.handle(request(
        "apply_package_lifecycle", confirmed, "guard-drift",
    ))[0]
    assert drifted["operation"] == "error"
    assert "changed after review" in drifted["payload"]["message"]
    assert not (game / "scripts" / "DesktopLifecycle.dll").exists()

    payload_file.write_bytes(b"desktop lifecycle payload")
    monkeypatch.setattr(
        rpf_tools, "_running_gta_processes", lambda: ("gta5.exe",),
    )
    running = writable.handle(request(
        "apply_package_lifecycle", confirmed, "guard-process",
    ))[0]
    assert running["operation"] == "error"
    assert "Close GTA V" in running["payload"]["message"]
    assert not (game / "scripts" / "DesktopLifecycle.dll").exists()

def test_asset_preview_returns_bounded_text_and_rejects_traversal(tmp_path, monkeypatch):
    package = tmp_path / "package"
    package.mkdir()
    authored = "Package notes\n" + ("bounded line\n" * 4_000)
    (package / "README.txt").write_text(authored, encoding="utf-8")
    cache = tmp_path / "preview-cache"
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(cache))
    service = DesktopProtocolService()
    handshake(service)

    response = service.handle(request("preview_asset", {
        "source": str(package), "entry": "README.txt", "edition": "Enhanced",
    }, "preview-text"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["display_kind"] == "text"
    assert result["text"].startswith("Package notes")
    assert len(result["text"]) == 32_000
    assert result["text_truncated"] is True
    assert result["artifact"] is None

    escaped = service.handle(request("preview_asset", {
        "source": str(package), "entry": "../README.txt",
    }, "preview-escape"))[0]
    assert escaped["operation"] == "error"
    assert escaped["risk"] == "read_only"
    assert "Unsafe package member path" in escaped["payload"]["message"]


def test_asset_preview_normalizes_images_to_hash_bound_cache_artifacts(
    tmp_path, monkeypatch,
):
    package = tmp_path / "package"
    package.mkdir()
    image_path = package / "preview.png"
    Image.new("RGB", (40, 24), (35, 120, 82)).save(image_path)
    cache = tmp_path / "preview-cache"
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(cache))
    service = DesktopProtocolService()
    handshake(service)

    response = service.handle(request("preview_asset", {
        "source": str(package), "entry": "preview.png",
    }, "preview-image"))[0]
    result = response["payload"]["result"]
    artifact = result["artifact"]
    rendered = Path(artifact["path"])
    assert response["risk"] == "read_only"
    assert result["display_kind"] == "image"
    assert rendered.parent == cache.resolve()
    assert rendered.name == f"{artifact['sha256']}.png"
    assert rendered.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(rendered.read_bytes()).hexdigest() == artifact["sha256"]
    assert artifact["media_type"] == "image/png"
    assert "base64" not in json.dumps(result).casefold()


def test_vehicle_viewport_protocol_uses_persistent_read_only_renderer(tmp_path):
    package = tmp_path / "vehicle-package"
    package.mkdir()
    (package / "authorcar.yft").write_bytes(b"RSC7 fixture")
    (package / "authorcar.ytd").write_bytes(b"RSC7 textures")
    (package / "authorcar.ybn").write_bytes(b"RSC7 collision")

    class _Renderer:
        def __init__(self):
            self.calls = []

        def render(self, source, entry, **options):
            self.calls.append((source, entry, options))
            return {
                "kind": "vehicle_model_viewport",
                "source": str(source),
                "path": entry,
                "sha256": "a" * 64,
                "artifact": {
                    "path": str(tmp_path / "frame.png"),
                    "sha256": "b" * 64,
                    "size": 128,
                    "media_type": "image/png",
                },
                "camera": {
                    "yaw": options["yaw"], "pitch": options["pitch"],
                    "lod": options["lod"] or "All",
                    "component": options["component"] or "All",
                    "render_mode": options["render_mode"],
                    "quality": options["quality"],
                },
                "read_only": True,
                "game_write_performed": False,
            }

    renderer = _Renderer()
    service = DesktopProtocolService()
    service._vehicle_viewport_renderer = renderer
    handshake(service)
    response = service.handle(request("render_vehicle_model", {
        "source": str(package),
        "entry": "authorcar.yft",
        "edition": "Enhanced",
        "yaw": 72.5,
        "pitch": -8,
        "lod": "High",
        "component": "Chassis",
        "material": "vehicle_paint1",
        "texture_entry": "authorcar.ytd",
        "collision_entry": "authorcar.ybn",
        "collision_visible": True,
        "render_mode": "materials",
        "quality": "interactive",
    }, "vehicle-viewport"))[0]

    assert response["operation"] == "result", response["payload"]
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "vehicle_model_viewport"
    assert result["camera"]["yaw"] == 72.5
    assert result["game_write_performed"] is False
    assert renderer.calls[0][1] == "authorcar.yft"
    assert renderer.calls[0][2]["quality"] == "interactive"
    assert renderer.calls[0][2]["material"] == "vehicle_paint1"
    assert renderer.calls[0][2]["texture_entry"] == "authorcar.ytd"
    assert renderer.calls[0][2]["collision_entry"] == "authorcar.ybn"
    assert renderer.calls[0][2]["collision_visible"] is True


def test_recipe_inspection_returns_ordered_bounded_plan_without_execution(tmp_path):
    source = recipe_source(tmp_path)
    service = DesktopProtocolService()
    handshake(service)

    response = service.handle(request("inspect_recipe", {
        "source": str(source),
    }, "inspect-recipe"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "recipe_plan"
    assert result["name"] == "Desktop Recipe"
    assert result["readiness"] == "managed_package_ready"
    assert result["operation_count"] == 3
    assert [item["number"] for item in result["operations"]] == [1, 2, 3]
    assert [item["kind"] for item in result["operations"]] == [
        "add", "archive", "add",
    ]
    assert result["error_count"] == 0
    assert not (source / "mod.toml").exists()

    wrong_type = tmp_path / "recipe.txt"
    wrong_type.write_text("not a package", encoding="utf-8")
    denied = service.handle(request("inspect_recipe", {
        "source": str(wrong_type),
    }, "inspect-wrong-type"))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "read_only"
    assert "OIV/ZIP" in denied["payload"]["message"]


def test_vehicle_quick_import_inspection_is_typed_and_read_only(
    tmp_path, monkeypatch,
):
    source = tmp_path / "vehicle-package.zip"
    source.write_bytes(b"fixture")
    gta_path = tmp_path / "Grand Theft Auto V"
    gta_path.mkdir()

    class FakeInspection:
        available_editions = ("legacy", "enhanced")

        def to_dict(self):
            return {
                "operation": "inspect_vehicle_quick_import",
                "source": str(source),
                "source_kind": "archive",
                "available_editions": ["legacy", "enhanced"],
                "suggested_edition": "enhanced",
                "edition_basis": "package_branches",
                "vehicles": [{
                    "model": "comet6", "edition": "enhanced",
                    "display_name": "Comet S2", "manufacturer": "Pfister",
                    "vehicle_class": "Sports",
                }],
                "errors": 0,
                "warnings": 1,
            }

    class FakeService:
        def __init__(self, _project_root, selected_gta_path):
            assert selected_gta_path == gta_path.resolve()

        def inspect(self, selected_source, *, preferred_edition=None):
            assert selected_source == source.resolve()
            assert preferred_edition == "enhanced"
            return FakeInspection()

    from allin1_sdk import vehicle_quick_import

    monkeypatch.setattr(
        vehicle_quick_import, "VehicleQuickImportService", FakeService,
    )
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("inspect_vehicle_quick_import", {
        "source": str(source),
        "gta_path": str(gta_path),
        "preferred_edition": "Enhanced",
    }, "quick-import"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "vehicle_quick_import_inspection"
    assert result["branch_count"] == 2
    assert result["vehicle_count"] == 1
    assert result["game_write_performed"] is False
    assert result["package_write_performed"] is False

    denied = service.handle(request("inspect_vehicle_quick_import", {
        "source": str(source),
        "gta_path": str(gta_path),
        "preferred_edition": "Both",
    }, "quick-import-invalid"))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "read_only"
    assert "Legacy or Enhanced" in denied["payload"]["message"]


def test_vehicle_quick_import_review_validates_an_in_memory_draft(
    tmp_path, monkeypatch,
):
    source = tmp_path / "vehicle-package.zip"
    source.write_bytes(b"fixture")
    gta_path = tmp_path / "Grand Theft Auto V Enhanced"
    gta_path.mkdir()
    captured = {}

    class FakeInspection:
        available_editions = ("enhanced",)

    class FakeCatalog:
        vehicles = (object(),)

    class FakePlan:
        edition = "enhanced"
        package_id = "vehicle.comet6"
        name = "Comet S2 Package"
        version = "2.0.0"
        catalog = FakeCatalog()

        def to_dict(self):
            return {
                "edition": self.edition,
                "package_id": self.package_id,
                "name": self.name,
                "version": self.version,
                "catalog": {"vehicles": [{"model": "comet6"}]},
            }

    class FakeReview:
        plan = FakePlan()
        warnings = ("Review the storefront price.",)

        def to_dict(self):
            return {
                "operation": "review_vehicle_quick_import",
                "plan": self.plan.to_dict(),
                "warnings": list(self.warnings),
                "acknowledged_free_models": [],
                "ready": True,
                "game_write_authorized": False,
            }

    class FakeService:
        def __init__(self, _project_root, selected_gta_path):
            assert selected_gta_path == gta_path.resolve()

        def inspect(self, selected_source, *, preferred_edition=None):
            assert selected_source == source.resolve()
            assert preferred_edition == "enhanced"
            return FakeInspection()

        def plan(self, _inspection, **identity):
            captured["identity"] = identity
            return FakeReview()

        def customize(self, plan, updates):
            assert plan is FakeReview.plan
            captured["updates"] = updates
            return FakeReview()

        def library_destination(self, plan):
            assert plan is FakeReview.plan
            return tmp_path / "library" / plan.package_id

        @staticmethod
        def validate_replaceable_destination(destination, package_id):
            captured["replacement_review"] = (destination, package_id)
            if captured.get("block_destination"):
                raise ValueError("destination does not prove SDK ownership")

        def prepare(self, review, destination):
            assert isinstance(review, FakeReview)
            captured["prepared_destination"] = destination

            class FakePrepared:
                @staticmethod
                def to_dict():
                    return {
                        "operation": "prepare_vehicle_quick_import",
                        "game_write_performed": False,
                        "launcher_install_required": True,
                        "launcher_library": True,
                        "replaced_existing": False,
                        "package": {"package_root": str(destination)},
                        "published": None,
                        "warnings": [],
                    }

            return FakePrepared()

    from allin1_sdk import vehicle_quick_import

    monkeypatch.setattr(
        vehicle_quick_import, "VehicleQuickImportService", FakeService,
    )
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("review_vehicle_quick_import", {
        "source": str(source),
        "gta_path": str(gta_path),
        "edition": "Enhanced",
        "package_id": "vehicle.comet6",
        "name": "Comet S2 Package",
        "version": "2.0.0",
        "updates": {"comet6": {
            "name": "Comet S2 Reviewed", "price": 185000,
        }},
    }, "quick-import-review"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "read_only"
    result = response["payload"]["result"]
    assert result["kind"] == "vehicle_quick_import_review"
    assert result["vehicle_count"] == 1
    assert result["warning_count"] == 1
    assert result["review_only"] is True
    assert result["game_write_performed"] is False
    assert result["package_write_performed"] is False
    assert result["destination_preview"].endswith("vehicle.comet6")
    assert result["destination_review"]["state"] == "new"
    assert result["destination_review"]["replaceable"] is True
    assert len(result["review_sha256"]) == 64
    assert captured["identity"] == {
        "edition": "enhanced", "package_id": "vehicle.comet6",
        "name": "Comet S2 Package", "version": "2.0.0",
    }
    assert captured["updates"]["comet6"]["name"] == "Comet S2 Reviewed"

    prepare_payload = {
        "source": str(source),
        "gta_path": str(gta_path),
        "edition": "enhanced",
        "package_id": "vehicle.comet6",
        "name": "Comet S2 Package",
        "version": "2.0.0",
        "updates": {"comet6": {
            "name": "Comet S2 Reviewed", "price": 185000,
        }},
        "review_sha256": result["review_sha256"],
    }
    unconfirmed = service.handle(request(
        "prepare_vehicle_quick_import", prepare_payload, "quick-import-unconfirmed",
    ))[0]
    assert unconfirmed["operation"] == "error"
    assert unconfirmed["risk"] == "authoring_write"
    assert "confirmation" in unconfirmed["payload"]["message"]

    prepared = service.handle(request("prepare_vehicle_quick_import", {
        **prepare_payload, "authoring_confirmed": True,
    }, "quick-import-prepare"))[0]
    assert prepared["operation"] == "result"
    assert prepared["risk"] == "authoring_write"
    prepared_result = prepared["payload"]["result"]
    assert prepared_result["kind"] == "vehicle_quick_import_prepared"
    assert prepared_result["package_write_performed"] is True
    assert prepared_result["game_write_performed"] is False
    assert prepared_result["launcher_install_required"] is True
    assert captured["prepared_destination"].name == "vehicle.comet6"

    stale = service.handle(request("prepare_vehicle_quick_import", {
        **prepare_payload,
        "review_sha256": "0" * 64,
        "authoring_confirmed": True,
    }, "quick-import-stale"))[0]
    assert stale["operation"] == "error"
    assert stale["risk"] == "authoring_write"
    assert "changed after review" in stale["payload"]["message"]

    captured["prepared_destination"].mkdir(parents=True)
    replacement_review = service.handle(request(
        "review_vehicle_quick_import",
        {key: value for key, value in prepare_payload.items() if key != "review_sha256"},
        "quick-import-replacement-review",
    ))[0]["payload"]["result"]
    assert replacement_review["destination_review"]["state"] == "managed_replacement"

    replacement_payload = {
        **prepare_payload,
        "review_sha256": replacement_review["review_sha256"],
        "authoring_confirmed": True,
    }
    replacement_denied = service.handle(request(
        "prepare_vehicle_quick_import", replacement_payload,
        "quick-import-replacement-unconfirmed",
    ))[0]
    assert replacement_denied["operation"] == "error"
    assert "requires explicit confirmation" in replacement_denied["payload"]["message"]

    replacement = service.handle(request("prepare_vehicle_quick_import", {
        **replacement_payload, "replace_confirmed": True,
    }, "quick-import-replacement"))[0]
    assert replacement["operation"] == "result"
    assert captured["replacement_review"][1] == "vehicle.comet6"

    captured["block_destination"] = True
    blocked_review = service.handle(request(
        "review_vehicle_quick_import",
        {key: value for key, value in prepare_payload.items() if key != "review_sha256"},
        "quick-import-blocked-review",
    ))[0]["payload"]["result"]
    assert blocked_review["destination_review"]["state"] == "blocked"
    blocked = service.handle(request("prepare_vehicle_quick_import", {
        **replacement_payload,
        "review_sha256": blocked_review["review_sha256"],
        "replace_confirmed": True,
    }, "quick-import-blocked"))[0]
    assert blocked["operation"] == "error"
    assert "does not prove SDK ownership" in blocked["payload"]["message"]

    denied = service.handle(request("review_vehicle_quick_import", {
        "source": str(source), "gta_path": str(gta_path),
        "edition": "enhanced", "updates": ["not", "an", "object"],
    }, "quick-import-review-invalid"))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "read_only"
    assert "updates must be an object" in denied["payload"]["message"]


def test_recipe_report_export_reuses_the_authoritative_agent_command(tmp_path):
    source = recipe_source(tmp_path)
    destination = tmp_path / "desktop-recipe-plan.md"
    service = DesktopProtocolService()
    handshake(service)

    denied = service.handle(request("execute", {
        "command": "oiv-plan",
        "args": [str(source), "--output", str(destination)],
    }, "export-recipe-unconfirmed"))[0]
    assert denied["operation"] == "error"
    assert denied["risk"] == "authoring_write"
    assert "confirmation" in denied["payload"]["message"]
    assert not destination.exists()

    response = service.handle(request("execute", {
        "command": "oiv-plan",
        "args": [str(source), "--output", str(destination)],
        "authoring_confirmed": True,
    }, "export-recipe"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "authoring_write"
    assert destination.is_file()
    assert destination.with_suffix(".json").is_file()
    assert "Desktop Recipe" in destination.read_text(encoding="utf-8")


def test_link_report_export_reuses_the_authoritative_agent_command(tmp_path):
    manifest = tmp_path / "addon.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
            "id": "test.desktop-export",
        "name": "Desktop Export Test",
        "version": "1.0.0",
        "summary": "Protocol export test",
        "editions": ["enhanced"],
        "nodes": [{
            "id": "package.main", "kind": "package", "label": "Package",
            "description": "", "source": None,
            "fields": {
                "Registration": "dlclist.xml", "Edition": "enhanced",
            },
        }],
        "references": [],
        "install_steps": [],
    }), encoding="utf-8")
    destination = tmp_path / "desktop-link-report.md"
    service = DesktopProtocolService()
    handshake(service)
    response = service.handle(request("execute", {
        "command": "link",
        "args": [
            str(manifest), "--output", str(destination),
            "--allow-failing-report",
        ],
        "authoring_confirmed": True,
    }, "export-report"))[0]
    assert response["operation"] == "result"
    assert response["risk"] == "authoring_write"
    assert destination.is_file()
    markdown = destination.read_text(encoding="utf-8")
    assert "Desktop Export Test" in markdown
    assert "Result: **FAIL**" in markdown


def test_job_worker_rejects_mutations_before_execution(tmp_path):
    source = io.StringIO(json.dumps({
        "operation": "execute",
        "payload": {"command": "install-package", "args": []},
        "allow_game_writes": False,
    }) + "\n")
    destination = io.StringIO()
    assert run_job_worker(source, destination) == 1
    response = json.loads(destination.getvalue())
    assert response["ok"] is False
    assert response["risk"] == "game_write"
    assert "read-only" in response["error"]


def test_read_only_job_streams_one_terminal_result():
    events = []
    completed = threading.Event()

    def emitted(message):
        events.append(message)
        if message["terminal"]:
            completed.set()

    service = DesktopProtocolService(emit=emitted)
    handshake(service)
    accepted = service.handle(request("start_job", {
        "job_id": "job-test",
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "revision": "view-7",
    }, "start"))[0]
    assert accepted["operation"] == "job_event"
    assert accepted["sequence"] == 0
    assert accepted["terminal"] is False
    assert completed.wait(20)
    assert len(events) == 1
    assert events[0]["operation"] == "result"
    assert events[0]["job_id"] == "job-test"
    assert events[0]["sequence"] == 1
    assert events[0]["payload"]["revision"] == "view-7"


def test_read_only_job_can_be_cancelled(monkeypatch):
    monkeypatch.setattr(
        desktop_protocol,
        "_worker_command",
        lambda: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    service = DesktopProtocolService()
    handshake(service)
    accepted = service.handle(request("start_job", {
        "job_id": "job-cancel",
        "operation": "execute",
        "payload": {"command": "list-axle-prefabs", "args": []},
        "revision": "view-cancel",
    }, "start-cancel"))[0]
    assert accepted["payload"]["state"] == "accepted"

    cancelled = service.handle(request(
        "cancel_job", {"job_id": "job-cancel"}, "cancel",
    ))[0]
    assert cancelled["operation"] == "job_event"
    assert cancelled["payload"]["state"] == "cancelled"
    assert cancelled["payload"]["revision"] == "view-cancel"
    assert cancelled["terminal"] is True


def test_model_material_inspection_returns_viewport_links_without_writes(
    tmp_path, monkeypatch,
):
    from allin1_sdk import model_materials

    source = tmp_path / "comet6.yft"
    source.write_bytes(b"native-model")
    (tmp_path / "COMET6.YTD").write_bytes(b"native-textures")
    (tmp_path / "comet6.ybn").write_bytes(b"native-collision")
    captured = {}

    def inspect(_project_root, selected, *, edition, gta_path=None):
        captured.update({"source": selected, "edition": edition, "gta_path": gta_path})
        return SimpleNamespace(to_dict=lambda: {
            "operation": "inspect_model_materials",
            "source": str(selected),
            "name": selected.name,
            "suffix": selected.suffix,
            "edition": edition,
            "size": selected.stat().st_size,
            "sha256": "a" * 64,
            "revision": None,
            "summary": {
                "materials": 1, "texture_bindings": 1, "geometries": 1,
                "components": 1, "errors": 0, "warnings": 0,
            },
            "materials": [], "geometries": [], "components": [],
            "lods": ["High"], "metadata": {}, "findings": [],
        })

    monkeypatch.setattr(model_materials, "inspect_model_file", inspect)
    risk, result = desktop_protocol.dispatch_operation(
        "inspect_model_materials", {"source": str(source), "edition": "Enhanced"},
    )
    assert risk == "read_only"
    assert captured["source"] == source.resolve()
    assert captured["edition"] == "Enhanced"
    assert result["viewport"] == {
        "source": str(tmp_path.resolve()),
        "entry": "comet6.yft",
        "texture_entry": "COMET6.YTD",
        "collision_entry": "comet6.ybn",
    }
    assert result["read_only"] is True
    assert result["workspace_write_performed"] is False
    assert result["game_write_performed"] is False


def test_model_material_workspace_edit_is_reviewed_revisioned_and_undoable(tmp_path):
    workspace = material_authoring_workspace(tmp_path)
    payload = {
        "workspace": str(workspace),
        "expected_revision": 0,
        "action": "material",
        "material_index": 0,
        "shader_name": "vehicle_paint2",
        "textures": {"DiffuseSampler": "fixture_respray_d"},
    }
    risk, review = desktop_protocol.dispatch_operation(
        "review_model_material_edit", payload,
    )
    assert risk == "read_only"
    assert review["ready"] is True
    assert review["changes"] == [
        {"field": "shader.name", "before": "vehicle_paint", "after": "vehicle_paint2"},
        {
            "field": "texture.DiffuseSampler", "before": "fixture_d",
            "after": "fixture_respray_d",
        },
    ]

    with pytest.raises(desktop_protocol.ProtocolError, match="confirmation"):
        desktop_protocol.dispatch_operation("apply_model_material_edit", {
            **payload, "review_sha256": review["review_sha256"],
        })
    risk, edited = desktop_protocol.dispatch_operation(
        "apply_model_material_edit", {
            **payload, "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        },
    )
    assert risk == "authoring_write"
    assert edited["revision"] == 1
    assert edited["materials"][0]["shader"] == "vehicle_paint2"
    assert edited["can_undo"] is True
    assert edited["package_write_performed"] is False
    assert edited["game_write_performed"] is False

    _risk, undone = desktop_protocol.dispatch_operation(
        "apply_model_material_history", {
            "workspace": str(workspace), "direction": "undo",
            "expected_revision": 1, "authoring_confirmed": True,
        },
    )
    assert undone["revision"] == 2
    assert undone["materials"][0]["shader"] == "vehicle_paint"
    assert undone["workspace_write_performed"] is True


def test_model_material_geometry_review_rejects_stale_or_noop_edits(tmp_path):
    workspace = material_authoring_workspace(tmp_path)
    base = {
        "workspace": str(workspace), "expected_revision": 0,
        "action": "geometry", "geometry_index": 0,
    }
    with pytest.raises(desktop_protocol.ProtocolError, match="already uses"):
        desktop_protocol.dispatch_operation(
            "review_model_material_edit", {**base, "material_index": 0},
        )
    _risk, review = desktop_protocol.dispatch_operation(
        "review_model_material_edit", {**base, "material_index": 1},
    )
    _risk, edited = desktop_protocol.dispatch_operation(
        "apply_model_material_edit", {
            **base, "material_index": 1,
            "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        },
    )
    assert edited["geometries"][0]["material_name"] == "vehicle_glass"
    with pytest.raises(desktop_protocol.ProtocolError, match="revision changed"):
        desktop_protocol.dispatch_operation(
            "review_model_material_edit", {**base, "material_index": 1},
        )


def test_model_material_parameter_edit_is_reviewed_bounded_and_revisioned(tmp_path):
    workspace = material_authoring_workspace(tmp_path)
    payload = {
        "workspace": str(workspace), "expected_revision": 0,
        "action": "parameter", "material_index": 0,
        "parameter_name": "detailSettings",
        "values": [["1", "0.8", "0.18", "0"], ["4", "2", "1.25", "0"]],
    }

    risk, review = desktop_protocol.dispatch_operation(
        "review_model_material_edit", payload,
    )
    assert risk == "read_only"
    assert review["action"] == "parameter"
    assert review["changes"] == [
        {
            "field": "parameter.detailSettings[0].y",
            "before": "0.72", "after": "0.8",
        },
        {
            "field": "parameter.detailSettings[1].z",
            "before": "1", "after": "1.25",
        },
    ]

    _risk, edited = desktop_protocol.dispatch_operation(
        "apply_model_material_edit", {
            **payload, "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        },
    )
    assert edited["revision"] == 1
    assert edited["materials"][0]["parameters"][1]["values"][1][2] == 1.25

    for invalid in (
        [["NaN", "0", "0", "0"], ["4", "2", "1", "0"]],
        [["1", "0", "0", "0"]],
    ):
        with pytest.raises(desktop_protocol.ProtocolError):
            desktop_protocol.dispatch_operation(
                "review_model_material_edit", {
                    **payload, "expected_revision": 1, "values": invalid,
                },
            )


def test_model_material_build_is_reviewed_reparsed_and_receipted(
    tmp_path, monkeypatch,
):
    from allin1_sdk import model_materials
    from allin1_sdk.model_materials import MaterialAuthoringWorkspace

    workspace = material_authoring_workspace(tmp_path)
    sdk_root = tmp_path / "sdk"
    patcher = sdk_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"MZ")
    monkeypatch.setenv("ALLIN1_SDK_HOME", str(sdk_root))
    output = tmp_path / "exports" / "fixture.ydr"
    output.parent.mkdir()

    def build(self, project_root, destination, *, gta_path=None):
        assert self.root == workspace
        assert project_root == sdk_root.resolve()
        assert gta_path is None
        destination.write_bytes(b"RSC8 verified build")
        report = destination.with_name(f"{destination.name}.allin1.json")
        report.write_text(json.dumps({
            "schema_version": 1,
            "operation": "native_asset_workspace_build",
            "output": {
                "path": str(destination), "size": destination.stat().st_size,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            },
            "validation": {
                "reparsed": True, "xml_sha256": "1" * 64,
                "edited_semantic_xml_sha256": "2" * 64,
                "reparsed_semantic_xml_sha256": "2" * 64,
                "semantic_xml_match": True, "dependency_count": 0,
            },
        }), encoding="utf-8")
        return destination, report

    def inspect(_project_root, selected, *, edition, gta_path=None):
        assert selected == output
        return SimpleNamespace(to_dict=lambda: {
            "operation": "inspect_model_materials", "source": str(selected),
            "name": selected.name, "suffix": selected.suffix,
            "edition": edition, "size": selected.stat().st_size,
            "sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
            "revision": None,
            "summary": {
                "materials": 2, "texture_bindings": 2, "geometries": 1,
                "components": 1, "errors": 0, "warnings": 0,
            },
            "materials": [], "geometries": [], "components": [],
            "lods": ["High"], "metadata": {}, "findings": [],
        })

    monkeypatch.setattr(MaterialAuthoringWorkspace, "build", build)
    monkeypatch.setattr(model_materials, "inspect_model_file", inspect)
    payload = {
        "workspace": str(workspace), "expected_revision": 0,
        "destination": str(output),
    }
    risk, review = desktop_protocol.dispatch_operation(
        "review_model_material_build", payload,
    )
    assert risk == "read_only"
    assert review["ready"] is True
    assert review["output_write_performed"] is False
    assert [check["key"] for check in review["checks"]] == [
        "revision", "toolchain", "reparse", "destination",
    ]
    assert not output.exists()

    with pytest.raises(desktop_protocol.ProtocolError, match="confirmation"):
        desktop_protocol.dispatch_operation("apply_model_material_build", {
            **payload, "review_sha256": review["review_sha256"],
        })
    risk, built = desktop_protocol.dispatch_operation(
        "apply_model_material_build", {
            **payload, "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        },
    )
    assert risk == "authoring_write"
    assert output.is_file()
    assert Path(built["validation_report"]).is_file()
    assert built["validation"]["reparsed"] is True
    assert built["validation"]["semantic_xml_match"] is True
    assert built["built_project"]["viewport"] == {
        "source": str(output.parent), "entry": output.name,
        "texture_entry": None, "collision_entry": None,
    }
    assert built["output_write_performed"] is True
    assert built["game_write_performed"] is False


def test_model_material_build_rejects_game_and_workspace_destinations(
    tmp_path, monkeypatch,
):
    workspace = material_authoring_workspace(tmp_path)
    sdk_root = tmp_path / "sdk"
    patcher = sdk_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"MZ")
    monkeypatch.setenv("ALLIN1_SDK_HOME", str(sdk_root))
    base = {"workspace": str(workspace), "expected_revision": 0}

    with pytest.raises(desktop_protocol.ProtocolError, match="outside the authoring workspace"):
        desktop_protocol.dispatch_operation("review_model_material_build", {
            **base, "destination": str(workspace / "fixture.ydr"),
        })

    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    with pytest.raises(desktop_protocol.ProtocolError, match="inside GTA V"):
        desktop_protocol.dispatch_operation("review_model_material_build", {
            **base, "destination": str(game / "fixture.ydr"),
        })


def test_texture_workspace_replacement_preview_and_undo_are_guarded(
    tmp_path, monkeypatch,
):
    workspace = texture_authoring_workspace(tmp_path)
    cache = tmp_path / "preview-cache"
    monkeypatch.setenv("ALLIN1_PREVIEW_DIR", str(cache))
    risk, opened = desktop_protocol.dispatch_operation(
        "inspect_texture_workspace", {"workspace": str(workspace)},
    )
    assert risk == "read_only"
    assert opened["texture_count"] == 1
    assert opened["revision"] == 0
    assert opened["can_undo"] is False

    _risk, preview = desktop_protocol.dispatch_operation(
        "preview_texture_workspace", {
            "workspace": str(workspace), "texture_name": "diffuse",
            "expected_state_sha256": opened["state_sha256"],
        },
    )
    assert Path(preview["artifact"]["path"]).is_file()
    assert preview["artifact"]["media_type"] == "image/png"
    assert preview["workspace_write_performed"] is False

    replacement = tmp_path / "replacement.png"
    Image.new("RGBA", (32, 12), (210, 50, 40, 200)).save(replacement)
    payload = {
        "workspace": str(workspace),
        "expected_state_sha256": opened["state_sha256"],
        "action": "replace", "texture_name": "diffuse",
        "source_image": str(replacement),
    }
    _risk, review = desktop_protocol.dispatch_operation("review_texture_edit", payload)
    assert review["source"]["converted_to_dds"] is True
    assert review["changes"][1] == {
        "field": "dimensions", "before": "16×8", "after": "32×12",
    }
    with pytest.raises(desktop_protocol.ProtocolError, match="confirmation"):
        desktop_protocol.dispatch_operation("apply_texture_edit", {
            **payload, "review_sha256": review["review_sha256"],
        })
    risk, edited = desktop_protocol.dispatch_operation("apply_texture_edit", {
        **payload, "review_sha256": review["review_sha256"],
        "authoring_confirmed": True,
    })
    assert risk == "authoring_write"
    assert edited["textures"][0]["width"] == 32
    assert edited["revision"] == 1
    assert edited["state_sha256"] != opened["state_sha256"]
    assert edited["can_undo"] is True

    with pytest.raises(desktop_protocol.ProtocolError, match="changed"):
        desktop_protocol.dispatch_operation("review_texture_edit", payload)
    _risk, restored = desktop_protocol.dispatch_operation("apply_texture_history", {
        "workspace": str(workspace),
        "expected_state_sha256": edited["state_sha256"],
        "authoring_confirmed": True,
    })
    assert restored["textures"][0]["width"] == 16
    assert restored["revision"] == 2
    assert restored["workspace_write_performed"] is True


def test_texture_workspace_creation_and_build_require_reviewed_new_destinations(
    tmp_path, monkeypatch,
):
    from allin1_sdk.native_assets import NativeAssetInspector

    template = texture_authoring_workspace(tmp_path / "template")
    sdk_root = tmp_path / "sdk"
    patcher = sdk_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"MZ")
    monkeypatch.setenv("ALLIN1_SDK_HOME", str(sdk_root))
    parent = tmp_path / "workspaces"
    parent.mkdir()
    source = template / "original" / "vehicle.ytd"

    def export_workspace(self, selected, destination, *, edition):
        assert self.patcher == patcher.resolve()
        assert selected == source.resolve()
        assert edition == "Enhanced"
        shutil.copytree(template, destination)
        return destination

    monkeypatch.setattr(NativeAssetInspector, "export_workspace", export_workspace)
    create_payload = {
        "source": str(source), "parent": str(parent),
        "name": "vehicle-textures", "edition": "Enhanced",
    }
    _risk, review = desktop_protocol.dispatch_operation(
        "review_texture_workspace", create_payload,
    )
    assert not Path(review["destination"]).exists()
    _risk, created = desktop_protocol.dispatch_operation(
        "create_texture_workspace", {
            **create_payload, "review_sha256": review["review_sha256"],
            "authoring_confirmed": True,
        },
    )
    created_workspace = Path(created["workspace"])
    assert created_workspace.is_dir()
    assert created["texture_count"] == 1

    output = tmp_path / "exports" / "vehicle.ytd"
    output.parent.mkdir()
    build_payload = {
        "workspace": str(created_workspace),
        "expected_state_sha256": created["state_sha256"],
        "destination": str(output),
    }
    _risk, build_review = desktop_protocol.dispatch_operation(
        "review_texture_build", build_payload,
    )
    assert build_review["output_write_performed"] is False

    def build_workspace(self, selected, destination):
        assert selected == created_workspace
        destination.write_bytes(b"RSC8 rebuilt YTD")
        report = destination.with_name(f"{destination.name}.allin1.json")
        report.write_text(json.dumps({
            "operation": "native_asset_workspace_build",
            "output": {
                "path": str(destination), "size": destination.stat().st_size,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            },
            "validation": {
                "reparsed": True, "semantic_xml_match": True,
                "dependency_count": 1,
            },
        }), encoding="utf-8")
        return destination, report

    monkeypatch.setattr(NativeAssetInspector, "build_workspace", build_workspace)
    risk, built = desktop_protocol.dispatch_operation("apply_texture_build", {
        **build_payload, "review_sha256": build_review["review_sha256"],
        "authoring_confirmed": True,
    })
    assert risk == "authoring_write"
    assert output.is_file()
    assert built["validation"]["reparsed"] is True
    assert built["output_write_performed"] is True

    mismatched = tmp_path / "exports" / "vehicle-mismatched.ytd"
    mismatch_payload = {**build_payload, "destination": str(mismatched)}
    _risk, mismatch_review = desktop_protocol.dispatch_operation(
        "review_texture_build", mismatch_payload,
    )

    def build_mismatch(self, selected, destination):
        destination.write_bytes(b"RSC8 semantically changed YTD")
        report = destination.with_name(f"{destination.name}.allin1.json")
        report.write_text(json.dumps({
            "operation": "native_asset_workspace_build",
            "output": {
                "path": str(destination), "size": destination.stat().st_size,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            },
            "validation": {
                "reparsed": True, "semantic_xml_match": False,
                "dependency_count": 1,
            },
        }), encoding="utf-8")
        return destination, report

    monkeypatch.setattr(NativeAssetInspector, "build_workspace", build_mismatch)
    with pytest.raises(desktop_protocol.ProtocolError, match="semantic validation"):
        desktop_protocol.dispatch_operation("apply_texture_build", {
            **mismatch_payload, "review_sha256": mismatch_review["review_sha256"],
            "authoring_confirmed": True,
        })
    assert not mismatched.exists()
    assert not mismatched.with_name(f"{mismatched.name}.allin1.json").exists()


def test_assistant_status_is_passive_and_unconfigured_state_is_reportable(monkeypatch):
    from allin1_sdk import assistant_client

    monkeypatch.setattr(assistant_client, "assistant_status", lambda: {
        "enabled": True, "mode": "managed_local", "model": "qwen.gguf",
        "local_runtime_running": False, "structured_output_ready": True,
        "provider_capabilities": ["json_schema"],
    })
    risk, result = desktop_protocol.dispatch_operation("assistant_status", {})
    assert risk == "read_only"
    assert result["configured"] is True
    assert result["runtime_started"] is False
    assert result["model"] == "qwen.gguf"

    def unavailable():
        raise ValueError("assistant settings do not exist")

    monkeypatch.setattr(assistant_client, "assistant_status", unavailable)
    _risk, result = desktop_protocol.dispatch_operation("assistant_status", {})
    assert result["configured"] is False
    assert result["enabled"] is False
    assert "settings" in result["message"]


def test_assistant_prompt_returns_only_bounded_advisory_evidence(tmp_path, monkeypatch):
    from allin1_sdk import assistant_client

    captured = {}

    def prompt(question, **kwargs):
        captured.update({"question": question, **kwargs})
        return SimpleNamespace(to_dict=lambda: {
            "text": "structured answer", "model": "qwen.gguf",
            "mode": "managed_local", "elapsed_seconds": 1.25,
            "advisory": {
                "summary": "Review the selected source.", "findings": [],
                "recommended_operations": [], "proposed_changes": [],
                "missing_context": [], "abstentions": [],
            },
            "context": {"selected_grounding": ["large private context"]},
            "safety_flags": ["advisory_only"], "receipt_path": "receipt.json",
        })

    monkeypatch.setattr(assistant_client, "prompt_assistant", prompt)
    risk, result = desktop_protocol.dispatch_operation("assistant_prompt", {
        "question": "Review this SDK boundary",
        "repository_root": str(tmp_path),
        "max_tokens": 512,
    })
    assert risk == "read_only"
    assert captured["question"] == "Review this SDK boundary"
    assert captured["repository_root"] == tmp_path.resolve()
    assert captured["operation_mode"] == "advisory"
    assert captured["compact_response"] is True
    assert "context" not in result
    assert result["advisory_only"] is True
    assert result["command_execution_performed"] is False
    assert result["question_sha256"] == hashlib.sha256(
        b"Review this SDK boundary"
    ).hexdigest()


def test_stdio_recovers_from_bad_json_and_stops_cleanly():
    source = io.StringIO(
        "{bad json}\n"
        + json.dumps(request("handshake", {
            "client": {"name": "stdio", "version": "1"},
            "supported_versions": [PROTOCOL_VERSION],
        }, "hello")) + "\n"
        + json.dumps(request("shutdown", {}, "stop")) + "\n"
    )
    destination = io.StringIO()
    serve_stdio(source, destination)
    responses = [json.loads(line) for line in destination.getvalue().splitlines()]
    assert responses[0]["operation"] == "error"
    assert responses[1]["payload"]["negotiated_version"] == PROTOCOL_VERSION
    assert responses[2]["payload"]["state"] == "stopped"


def test_unknown_fields_and_server_only_operations_are_rejected():
    service = DesktopProtocolService()
    bad = request("handshake", {
        "client": {"name": "test", "version": "1"},
        "supported_versions": [PROTOCOL_VERSION],
    })
    bad["surprise"] = True
    assert "unknown fields" in service.handle(bad)[0]["payload"]["message"]

    server_message = request("job_event")
    assert "not allowed" in service.handle(server_message)[0]["payload"]["message"]
