from __future__ import annotations

from pathlib import Path

import pytest
import json
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.addon_importer import AddonPackageInspector
from allin1_sdk.cli import main
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace


VEHICLES = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>authorcar</modelName><txdName>authorcar</txdName>
<handlingId>AUTHORHAND</handlingId><gameName>AUTHORCAR</gameName>
<vehicleMakeName>AUTHOR</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
<vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""
HANDLING = """<CHandlingDataMgr><HandlingData><Item>
<handlingName>AUTHORHAND</handlingName><fMass value="1500.0" />
<nInitialDriveGears value="6"/><fInitialDriveForce value="0.30"/>
<fInitialDriveMaxFlatVel value="160.0"/><fBrakeForce value="0.8"/>
<fSteeringLock value="40.0"/>
</Item></HandlingData></CHandlingDataMgr>"""
VARIATIONS = """<CVehicleModelInfoVariation><variationData><Item>
<modelName>authorcar</modelName><colors><Item>
<indices content="char_array">0 1 2 3 4 5</indices>
<liveries><Item value="true"/><Item value="false"/></liveries>
</Item></colors><kits><Item>123_authorkit</Item></kits>
<lightSettings value="1" /><sirenSettings value="0"/>
</Item></variationData></CVehicleModelInfoVariation>"""
CARCOLS = """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>123_authorkit</kitName><id value="123"/><kitType>MKT_STANDARD</kitType>
<visibleMods><Item><modelName>author_spoiler</modelName>
<modShopLabel>AUTH_MOD</modShopLabel><linkedModels><Item>author_support</Item></linkedModels>
<turnOffBones><Item>boot</Item></turnOffBones><type>VMT_SPOILER</type>
<bone>chassis</bone><audioApply value="1.000000"/><weight value="0"/>
<minIntVars content="char_array">0 1</minIntVars>
<maxIntVars content="char_array">1 2</maxIntVars>
<customTuning><rate value="2.0"/></customTuning></Item></visibleMods>
<linkMods><Item><modelName>author_support</modelName><bone>chassis</bone>
<turnOffExtra value="false"/></Item></linkMods><statMods/><slotNames/>
<liveryNames><Item>AUTH_LIVERY_1</Item></liveryNames>
</Item></Kits><Lights><Item><id value="1"/><headLight>
<intensity value="2.000000"/><color value="0xFFFFFFFF"/>
</headLight><name>authorcar</name></Item></Lights></CVehicleModelInfoVarGlobal>"""
CONTENT = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_authorcar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""


def _source(root: Path, *, rpf_source: bool = False) -> Path:
    source = root / ("dlc.rpf.source" if rpf_source else "vehicle-source")
    source.mkdir(parents=True)
    for name, text in (
        ("vehicles.meta", VEHICLES), ("handling.meta", HANDLING),
        ("carvariations.meta", VARIATIONS), ("carcols.meta", CARCOLS),
        ("content.xml", CONTENT),
    ):
        (source / name).write_text(text, encoding="utf-8")
    stream = source / "stream"
    stream.mkdir()
    (stream / "authorcar.yft").write_bytes(b"fragment")
    (stream / "authorcar.ytd").write_bytes(b"textures")
    (stream / "author_spoiler.yft").write_bytes(b"spoiler")
    (stream / "author_support.yft").write_bytes(b"support")
    (stream / "author_bumper.yft").write_bytes(b"bumper")
    (source / "american_rel.rpf.gxt2").write_bytes(b"labels")
    return source


def test_vehicle_authoring_workspace_copies_edits_validates_and_undoes(tmp_path):
    original = _source(tmp_path)
    original_vehicle = (original / "vehicles.meta").read_bytes()
    workspace = VehicleAuthoringWorkspace.create(
        original, tmp_path / "authoring-workspace",
    )

    assert workspace.revision == 0
    values = workspace.values("authorcar")
    assert values.values["vehicle.gameName"] == "AUTHORCAR"
    assert values.values["handling.fMass"] == "1500.0"
    assert values.values["variation.lightSettings"] == "1"
    assert values.values["variation.sirenSettings"] == "0"
    assert values.values["variation.kits"] == "123_authorkit"

    result = workspace.update("authorcar", {
        "vehicle.gameName": "AUTHORCAR2",
        "handling.fMass": "1625.5",
        "handling.nInitialDriveGears": "7.0",
        "variation.lightSettings": "12",
        "variation.kits": "123_authorkit",
    })
    assert result.revision == 1
    assert result.history.is_dir()
    changed = workspace.values("AUTHORCAR")
    assert changed.values["vehicle.gameName"] == "AUTHORCAR2"
    assert changed.values["handling.fMass"] == "1625.5"
    assert changed.values["handling.nInitialDriveGears"] == "7"
    assert changed.values["variation.lightSettings"] == "12"
    assert (original / "vehicles.meta").read_bytes() == original_vehicle
    with pytest.raises(ValueError, match="dlc.rpf.source"):
        workspace.publish_source()

    undone = workspace.undo()
    assert undone.revision == 2
    restored = workspace.values("authorcar")
    assert restored.values["vehicle.gameName"] == "AUTHORCAR"
    assert restored.values["handling.fMass"] == "1500.0"
    assert restored.values["variation.lightSettings"] == "1"
    assert not any(
        path.name.endswith(".undo-recovery")
        for path in (workspace.root / "history").iterdir()
    )


def test_vehicle_edit_tolerates_preexisting_malformed_unrelated_xml(tmp_path):
    source = _source(tmp_path)
    (source / "unrelated.meta").write_text("<Unrelated>", encoding="utf-8")
    workspace = VehicleAuthoringWorkspace.create(
        source, tmp_path / "workspace",
    )

    result = workspace.update(
        "authorcar", {"vehicle.gameName": "AUTHORCAR_SAFE"},
    )

    assert result.revision == 1
    assert workspace.values("authorcar").values["vehicle.gameName"] \
        == "AUTHORCAR_SAFE"
    assert (workspace.source / "unrelated.meta").read_text(encoding="utf-8") \
        == "<Unrelated>"


def test_vehicle_undo_refuses_external_edits_to_touched_members(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    result = workspace.update(
        "authorcar", {"vehicle.gameName": "AUTHORCAR_EDITED"},
    )
    record = json.loads((result.history / "edit.json").read_text("utf-8"))
    assert set(record["sha256"]) == {"vehicles.meta", "handling.meta", "carvariations.meta"}
    assert set(record["sha256_after"]) == set(record["sha256"])

    path = workspace.source / "vehicles.meta"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "AUTHORCAR_EDITED", "AUTHORCAR_EXTERNAL",
        ),
        encoding="utf-8",
    )
    current = path.read_bytes()

    with pytest.raises(ValueError, match="changed after its edit: vehicles.meta"):
        workspace.undo()

    assert path.read_bytes() == current
    assert workspace.revision == 1
    assert result.history.is_dir()


def test_vehicle_undo_tracks_renamed_asset_post_state(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    result = workspace.migrate_identity("authorcar", new_model="renamedcar")
    record = json.loads((result.history / "edit.json").read_text("utf-8"))
    assert record["sha256_after"]["stream/authorcar.yft"]["path"] \
        == "stream/renamedcar.yft"

    renamed = workspace.source / "stream" / "renamedcar.yft"
    renamed.write_bytes(b"external fragment edit")

    with pytest.raises(
        ValueError, match="changed after its edit: stream/renamedcar.yft",
    ):
        workspace.undo()

    assert renamed.read_bytes() == b"external fragment edit"
    assert not (workspace.source / "stream" / "authorcar.yft").exists()
    assert workspace.revision == 1


def test_vehicle_undo_rejects_tampered_backup_before_mutating(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    result = workspace.update(
        "authorcar", {"vehicle.gameName": "AUTHORCAR_EDITED"},
    )
    (result.history / "files" / "vehicles.meta").write_bytes(b"tampered")
    current = (workspace.source / "vehicles.meta").read_bytes()

    with pytest.raises(ValueError, match="backup hash is invalid: vehicles.meta"):
        workspace.undo()

    assert (workspace.source / "vehicles.meta").read_bytes() == current
    assert workspace.revision == 1


def test_vehicle_appearance_tuning_and_light_profiles_are_structured_and_undoable(
    tmp_path,
):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    appearance = workspace.appearance("authorcar")
    assert appearance.colors[0].indices == (0, 1, 2, 3, 4, 5)
    assert appearance.colors[0].liveries == (True, False)
    assert appearance.available_kits[0].kit_type == "MKT_STANDARD"
    assert appearance.available_kits[0].visible_mods == 1
    assert appearance.available_kits[0].livery_names == ("AUTH_LIVERY_1",)
    assert appearance.light_profiles[0].values["headLight.intensity"] == "2.000000"

    result = workspace.update_appearance(
        "authorcar",
        colors=[{"indices": [10, 11, 12, 13], "liveries": [False, True, False]}],
        kits=["123_authorkit"], light_settings=1, siren_settings=7,
    )
    assert result.revision == 1
    changed = workspace.appearance("authorcar")
    assert changed.colors[0].indices == (10, 11, 12, 13)
    assert changed.colors[0].liveries == (False, True, False)
    assert changed.siren_settings == "7"

    kit = workspace.update_tuning_kit(
        "authorcar", "123_authorkit", kit_type="MKT_SPECIAL",
        livery_names=["AUTH_LIVERY_2"],
    )
    assert kit.revision == 2
    assert workspace.appearance("authorcar").available_kits[0].kit_type == "MKT_SPECIAL"

    light = workspace.update_light_profile(
        "authorcar", "1", {"headLight.intensity": "3.500000"},
    )
    assert light.revision == 3
    assert workspace.appearance(
        "authorcar"
    ).light_profiles[0].values["headLight.intensity"] == "3.500000"
    workspace.undo()
    assert workspace.appearance(
        "authorcar"
    ).light_profiles[0].values["headLight.intensity"] == "2.000000"


def test_vehicle_identity_migration_renames_references_assets_and_undoes(tmp_path):
    original = _source(tmp_path)
    workspace = VehicleAuthoringWorkspace.create(
        original, tmp_path / "workspace",
    )
    result = workspace.migrate_identity(
        "authorcar", new_model="renamedcar", new_handling="RENAMEDHAND",
    )
    assert result.model == "renamedcar"
    migrated = workspace.inspect().model("renamedcar")
    assert migrated.handling_id == "RENAMEDHAND"
    assert (workspace.source / "stream" / "renamedcar.yft").is_file()
    assert (workspace.source / "stream" / "renamedcar.ytd").is_file()
    assert not (workspace.source / "stream" / "authorcar.yft").exists()
    assert "renamedcar" in (workspace.source / "vehicles.meta").read_text()
    assert "RENAMEDHAND" in (workspace.source / "handling.meta").read_text()
    assert (original / "stream" / "authorcar.yft").is_file()

    undone = workspace.undo()
    assert undone.model == "authorcar"
    assert workspace.inspect().model("authorcar").handling_id == "AUTHORHAND"
    assert (workspace.source / "stream" / "authorcar.yft").is_file()
    assert not (workspace.source / "stream" / "renamedcar.yft").exists()
    assert workspace.manifest["models"] == ["authorcar"]


def test_vehicle_appearance_and_identity_reject_unsafe_changes_without_writes(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    before = (workspace.source / "carvariations.meta").read_bytes()
    with pytest.raises(ValueError, match="4 through 8"):
        workspace.update_appearance(
            "authorcar", colors=[{"indices": [1, 2], "liveries": []}],
        )
    with pytest.raises(ValueError, match="Unknown tuning kits"):
        workspace.update_appearance("authorcar", kits=["missing_kit"])
    with pytest.raises(ValueError, match="Unsupported light-profile"):
        workspace.update_light_profile("authorcar", "1", {"id": "2"})
    with pytest.raises(ValueError, match="letters, numbers, and underscores"):
        workspace.migrate_identity("authorcar", new_model="bad/name")
    assert workspace.revision == 0
    assert (workspace.source / "carvariations.meta").read_bytes() == before


def test_vehicle_authoring_rejects_broken_references_and_unsafe_values(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    before = (workspace.source / "vehicles.meta").read_bytes()

    with pytest.raises(ValueError, match="Unknown tuning kits"):
        workspace.update("authorcar", {"variation.kits": "missing_kit"})
    with pytest.raises(ValueError, match="introduced unresolved"):
        workspace.update("authorcar", {"vehicle.txdName": "missing_textures"})
    with pytest.raises(ValueError, match="must be finite"):
        workspace.update("authorcar", {"handling.fMass": "nan"})

    assert workspace.revision == 0
    assert (workspace.source / "vehicles.meta").read_bytes() == before
    assert workspace.values("authorcar").values["vehicle.txdName"] == "authorcar"


def test_vehicle_authoring_preserves_dlc_source_root_for_package_building(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path, rpf_source=True), tmp_path / "workspace",
    )
    assert workspace.source.name == "dlc.rpf.source"
    assert (workspace.source / "vehicles.meta").is_file()
    assert workspace.publish_source() == workspace.source


def test_vehicle_authoring_cli_console_and_agent_api_share_transactions(tmp_path):
    source = _source(tmp_path)
    destination = tmp_path / "cli-workspace"
    runner = CliRunner()
    created = runner.invoke(main, [
        "sdk", "create-vehicle-authoring", str(source), "-o", str(destination),
    ])
    assert created.exit_code == 0, created.output
    assert json.loads(created.output)["models"] == ["authorcar"]

    edited = runner.invoke(main, [
        "set-vehicle-fields", str(destination), "authorcar",
        "--set", "handling.fMass=1700", "--set", "variation.lightSettings=7",
        "--acknowledge-edit",
    ])
    assert edited.exit_code == 0, edited.output
    assert json.loads(edited.output)["revision"] == 1

    inspected = runner.invoke(main, [
        "inspect-vehicle-authoring", str(destination), "--model", "authorcar",
    ])
    assert inspected.exit_code == 0, inspected.output
    values = json.loads(inspected.output)["authoring"]["values"]
    assert values["handling.fMass"] == "1700"
    assert values["variation.lightSettings"] == "7"

    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["create-vehicle-authoring"]["risk"] == "authoring_write"
    assert catalog["inspect-vehicle-authoring"]["risk"] == "read_only"
    assert catalog["set-vehicle-fields"]["risk"] == "authoring_write"
    for command in (
        "set-vehicle-appearance", "set-vehicle-tuning-kit",
        "set-vehicle-light-profile", "migrate-vehicle-identity",
        "add-vehicle-tuning-entry", "set-vehicle-tuning-entry",
        "remove-vehicle-tuning-entry", "move-vehicle-tuning-entry",
    ):
        assert catalog[command]["risk"] == "authoring_write"
    assert catalog["inspect-vehicle-tuning"]["risk"] == "read_only"
    collection = next(
        item for item in catalog["add-vehicle-tuning-entry"]["parameters"]
        if item["name"] == "collection"
    )
    assert collection["choices"] == [
        "visibleMods", "linkMods", "statMods", "slotNames",
    ]
    response = execute_request({
        "id": "undo-vehicle", "action": "execute",
        "command": "undo-vehicle-edit",
        "args": [str(destination), "--acknowledge-edit"],
    }, audit_path=tmp_path / "audit.jsonl")
    assert response["ok"] is True
    assert VehicleAuthoringWorkspace(destination).values(
        "authorcar"
    ).values["handling.fMass"] == "1500.0"


def test_vehicle_appearance_and_identity_cli_use_the_guarded_workspace(tmp_path):
    destination = tmp_path / "workspace"
    workspace = VehicleAuthoringWorkspace.create(_source(tmp_path), destination)
    colors = tmp_path / "colors.json"
    colors.write_text(json.dumps([
        {"indices": [20, 21, 22, 23], "liveries": [True, False, True]},
    ]), encoding="utf-8")
    runner = CliRunner()
    appearance = runner.invoke(main, [
        "set-vehicle-appearance", str(destination), "authorcar",
        "--colors-json", str(colors), "--siren-settings", "9",
        "--acknowledge-edit",
    ])
    assert appearance.exit_code == 0, appearance.output
    light = runner.invoke(main, [
        "set-vehicle-light-profile", str(destination), "authorcar", "1",
        "--set", "headLight.intensity=4.0", "--acknowledge-edit",
    ])
    assert light.exit_code == 0, light.output
    migrated = runner.invoke(main, [
        "migrate-vehicle-identity", str(destination), "authorcar",
        "--new-model", "cli_car", "--new-handling", "CLI_HAND",
        "--acknowledge-edit",
    ])
    assert migrated.exit_code == 0, migrated.output
    assert workspace.inspect().model("cli_car").handling_id == "CLI_HAND"


def test_tuning_builder_inventories_parts_preserves_fields_and_edits_collections(
    tmp_path,
):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    builder = workspace.tuning_builder("authorcar")
    assert builder.kit_name == "123_authorkit"
    assert builder.error_count == 0
    assert builder.to_dict()["field_schemas"]["visibleMods"]["modelName"] == {
        "kind": "identifier", "required": True, "default": "",
    }
    assert [item.collection for item in builder.entries] == [
        "visibleMods", "linkMods",
    ]
    visible = builder.entries[0]
    assert visible.fields["linkedModels"] == "author_support"
    assert visible.fields["turnOffBones"] == "boot"
    assert visible.fields["customTuning.rate"] == "2.0"
    assets = {item.name: item for item in builder.assets}
    assert assets["author_spoiler"].referenced is True
    assert assets["author_bumper"].referenced is False

    updated = workspace.update_tuning_entry(
        "authorcar", "123_authorkit", "visibleMods", 0,
        {
            "modShopLabel": "AUTH_SPOILER",
            "minIntVars": "0, 2",
            "maxIntVars": "1, 4",
            "customTuning.rate": "3.5",
        },
    )
    assert updated.revision == 1
    changed = workspace.tuning_builder("authorcar").entries[0].fields
    assert changed["modShopLabel"] == "AUTH_SPOILER"
    assert changed["minIntVars"] == "0, 2"
    assert changed["maxIntVars"] == "1, 4"
    assert changed["customTuning.rate"] == "3.5"
    carcols = (workspace.source / "carcols.meta").read_text(encoding="utf-8")
    assert '<minIntVars content="char_array">' in carcols
    assert "<minIntVars content=\"char_array\"><Item>" not in carcols

    added = workspace.add_tuning_entry(
        "authorcar", "123_authorkit", "visibleMods", {
            "modelName": "author_bumper", "modShopLabel": "AUTH_BUMPER",
            "type": "VMT_BUMPER_F", "bone": "chassis",
        },
    )
    assert added.revision == 2
    workspace.add_tuning_entry(
        "authorcar", "123_authorkit", "statMods", {
            "identifier": "AUTH_ENGINE_1", "modifier": "20",
            "type": "VMT_ENGINE",
        },
    )
    workspace.add_tuning_entry(
        "authorcar", "123_authorkit", "slotNames", {
            "slot": "VMT_BUMPER_F", "name": "AUTH_BUMPERS",
        },
    )
    entries = workspace.tuning_builder("authorcar").entries
    assert any(item.collection == "statMods" for item in entries)
    assert any(item.collection == "slotNames" for item in entries)

    moved = workspace.move_tuning_entry(
        "authorcar", "123_authorkit", "visibleMods", 1, 0,
    )
    assert moved.revision == 5
    assert workspace.tuning_builder("authorcar").entries[0].summary == "author_bumper"
    removed = workspace.remove_tuning_entry(
        "authorcar", "123_authorkit", "visibleMods", 0,
    )
    assert removed.revision == 6
    workspace.undo()
    assert workspace.tuning_builder("authorcar").entries[0].summary == "author_bumper"


def test_tuning_builder_rejects_missing_assets_collisions_and_bad_values(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    before = (workspace.source / "carcols.meta").read_bytes()
    with pytest.raises(ValueError, match="YFT"):
        workspace.add_tuning_entry(
            "authorcar", "123_authorkit", "visibleMods", {
                "modelName": "missing_part", "modShopLabel": "MISSING_PART",
                "type": "VMT_SPOILER",
            },
        )
    with pytest.raises(ValueError, match="Unknown vehicle modification type"):
        workspace.update_tuning_entry(
            "authorcar", "123_authorkit", "visibleMods", 0,
            {"type": "VMT_NOT_REAL"},
        )
    with pytest.raises(ValueError, match="equal lengths"):
        workspace.update_tuning_entry(
            "authorcar", "123_authorkit", "visibleMods", 0,
            {"minIntVars": "1, 2", "maxIntVars": "3"},
        )
    with pytest.raises(ValueError, match="duplicate_tuning_entry"):
        workspace.add_tuning_entry(
            "authorcar", "123_authorkit", "visibleMods", {},
            duplicate_index=0,
        )
    assert workspace.revision == 0
    assert (workspace.source / "carcols.meta").read_bytes() == before
    active_history = [
        path for path in (workspace.root / "history").iterdir()
        if path.is_dir() and not path.name.endswith((".undone", ".undo-recovery"))
    ]
    assert active_history == []


def test_tuning_builder_resolves_numeric_links_and_preserves_xml_storage_forms(tmp_path):
    source = _source(tmp_path)
    variations = source / "carvariations.meta"
    variations.write_text(
        variations.read_text(encoding="utf-8").replace(
            "<Item>123_authorkit</Item>", "<Item>123</Item>",
        ),
        encoding="utf-8",
    )
    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "workspace")
    assert workspace.tuning_builder("authorcar", "123").kit_name == "123_authorkit"

    workspace.update_tuning_entry(
        "authorcar", "123", "visibleMods", 0,
        {"audioApply": "0.75", "turnOffExtra": "1"},
    )
    changed = workspace.tuning_builder("authorcar", "123").entries[0].fields
    assert changed["audioApply"] == "0.75"
    assert changed["turnOffExtra"] == "true"
    carcols = (workspace.source / "carcols.meta").read_text(encoding="utf-8")
    assert '<audioApply value="0.75"/>' in carcols
    assert '<turnOffExtra value="true"/>' in carcols
    assert '<minIntVars content="char_array">' in carcols


def test_tuning_builder_distinguishes_actionable_errors_from_companion_warnings(tmp_path):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    result = workspace.remove_tuning_entry(
        "authorcar", "123_authorkit", "linkMods", 0,
    )
    assert result.revision == 1
    builder = workspace.tuning_builder("authorcar")
    assert builder.error_count == 0
    assert builder.warning_count == 1
    assert builder.findings[0].code == "unregistered_linked_model"
    assert builder.findings[0].entry == "visibleMods:0"
    workspace.undo()
    assert workspace.tuning_builder("authorcar").warning_count == 0


def test_authoring_mutations_reuse_each_validation_scan(tmp_path, monkeypatch):
    workspace = VehicleAuthoringWorkspace.create(
        _source(tmp_path), tmp_path / "workspace",
    )
    original = AddonPackageInspector.inspect
    calls: list[Path] = []

    def tracked(inspector, source):
        calls.append(Path(source))
        return original(inspector, source)

    monkeypatch.setattr(AddonPackageInspector, "inspect", tracked)
    workspace.update("authorcar", {"vehicle.gameName": "AUTHORCAR_FAST"})
    assert calls == [workspace.source, workspace.source]

    calls.clear()
    workspace.update_tuning_entry(
        "authorcar", "123_authorkit", "visibleMods", 0,
        {"modShopLabel": "AUTH_FAST_PART"},
    )
    assert calls == [workspace.source, workspace.source]


def test_tuning_builder_cli_and_agent_api_share_the_guarded_operations(tmp_path):
    destination = tmp_path / "workspace"
    VehicleAuthoringWorkspace.create(_source(tmp_path), destination)
    runner = CliRunner()
    inspected = runner.invoke(main, [
        "inspect-vehicle-tuning", str(destination), "authorcar",
        "--kit", "123_authorkit",
    ])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["error_count"] == 0

    added = runner.invoke(main, [
        "add-vehicle-tuning-entry", str(destination), "authorcar",
        "123_authorkit", "visibleMods",
        "--set", "modelName=author_bumper",
        "--set", "modShopLabel=AUTH_BUMPER",
        "--set", "type=VMT_BUMPER_F", "--acknowledge-edit",
    ])
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["revision"] == 1

    changed = execute_request({
        "id": "edit-tuning", "action": "execute",
        "command": "set-vehicle-tuning-entry",
        "args": [
            str(destination), "authorcar", "123_authorkit", "visibleMods", "1",
            "--set", "minIntVars=0, 2", "--set", "maxIntVars=1, 4",
            "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert changed["ok"] is True
    moved = execute_request({
        "id": "move-tuning", "action": "execute",
        "command": "move-vehicle-tuning-entry",
        "args": [
            str(destination), "authorcar", "123_authorkit", "visibleMods",
            "1", "0", "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert moved["ok"] is True
    builder = VehicleAuthoringWorkspace(destination).tuning_builder("authorcar")
    assert builder.entries[0].summary == "author_bumper"
    assert builder.entries[0].fields["minIntVars"] == "0, 2"

    unacknowledged = execute_request({
        "id": "unsafe-tuning", "action": "execute",
        "command": "remove-vehicle-tuning-entry",
        "args": [
            str(destination), "authorcar", "123_authorkit", "visibleMods", "0",
        ],
    }, audit_path=tmp_path / "audit.jsonl")
    assert unacknowledged["ok"] is False
    assert unacknowledged["risk"] == "authoring_write"
    assert "--acknowledge-edit" in unacknowledged["result"]["output"]

    removed = runner.invoke(main, [
        "remove-vehicle-tuning-entry", str(destination), "authorcar",
        "123_authorkit", "visibleMods", "0", "--acknowledge-edit",
    ])
    assert removed.exit_code == 0, removed.output
    assert len([
        item for item in VehicleAuthoringWorkspace(destination).tuning_builder(
            "authorcar"
        ).entries if item.collection == "visibleMods"
    ]) == 1
