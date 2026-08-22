from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.dlc_inventory import DlcInventory
from allin1_sdk.mods import ModManifest
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler


OIV_ASSEMBLY = """<package>
<metadata><name>Test OIV</name><version><major>2</major><minor>1</minor></version>
<author><displayName>Test Author</displayName></author><gameversion>enhanced</gameversion></metadata>
<content>
  <add source="plugin.dll">scripts/Test/plugin.dll</add>
  <archive path="update/update.rpf">
    <add source="data.xml">common/data/test.xml</add>
  </archive>
</content></package>"""


def _oiv_folder(root: Path, assembly: str = OIV_ASSEMBLY) -> Path:
    package = root / "oiv"
    content = package / "content"
    content.mkdir(parents=True)
    (package / "assembly.xml").write_text(assembly, encoding="utf-8")
    (content / "plugin.dll").write_bytes(b"plugin")
    (content / "data.xml").write_text("<data />", encoding="utf-8")
    return package


def _oiv_archive(root: Path) -> Path:
    archive = root / "test.oiv"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("assembly.xml", OIV_ASSEMBLY)
        package.writestr("content/plugin.dll", b"plugin")
        package.writestr("content/data.xml", b"<data />")
    return archive


def test_oiv_plan_and_managed_export_cover_files_and_exact_rpf_entries(tmp_path):
    source = _oiv_folder(tmp_path)
    workbench = OivWorkbench()
    plan = workbench.inspect(source)

    assert plan.name == "Test OIV"
    assert plan.version == "2.1"
    assert plan.author == "Test Author"
    assert plan.editions == ("enhanced",)
    assert plan.translatable
    assert [item.kind for item in plan.operations] == ["add", "archive", "add"]
    assert plan.add_operations[1].archives == ("mods/update/update.rpf",)
    assert "MANAGED EXPORT READY" in plan.to_markdown()

    report = plan.write_report(tmp_path / "plan.md")
    assert report.is_file()
    assert json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))[
        "translatable"
    ]
    manifest_path = workbench.export_managed_package(plan, tmp_path / "managed")
    manifest = ModManifest.load(manifest_path)
    assert manifest.mod_type == "mixed"
    assert manifest.editions == ("enhanced",)
    assert manifest.dependencies == ("openrpf",)
    assert len(manifest.files) == len(manifest.rpf_entries) == 1


def test_oiv_archive_export_streams_only_declared_sources(tmp_path):
    plan = OivWorkbench().inspect(_oiv_archive(tmp_path))
    manifest = OivWorkbench().export_managed_package(plan, tmp_path / "export")
    assert ModManifest.load(manifest).mod_id == "test-oiv"
    with pytest.raises(ValueError, match="must be empty"):
        OivWorkbench().export_managed_package(plan, tmp_path / "export")


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        ("<delete>scripts/old.dll</delete>", "unsupported_delete"),
        ("<text path=\"x.txt\"><add>x</add></text>", "unsupported_text"),
        ("<xml path=\"x.xml\"><remove xpath=\"/x\" /></xml>", "unsupported_xml"),
        ("<pso path=\"x.ymt\"><remove xpath=\"/x\" /></pso>", "unsupported_pso"),
        ("<defragmentation archive=\"update.rpf\" />", "unsupported_defragmentation"),
        ("<mystery />", "unknown_operation"),
    ],
)
def test_oiv_plan_blocks_destructive_merge_and_unknown_operations(
    tmp_path, operation, code,
):
    assembly = f"<package><content>{operation}</content></package>"
    plan = OivWorkbench().inspect(_oiv_folder(tmp_path, assembly))
    assert not plan.translatable
    assert code in {item.code for item in plan.findings}
    with pytest.raises(ValueError, match="unsupported or unsafe"):
        OivWorkbench().export_managed_package(plan, tmp_path / "blocked")


def test_oiv_plan_blocks_missing_sources_but_recognizes_safe_archive_creation(tmp_path):
    assembly = """<package><content>
      <add source="missing.bin">scripts/missing.bin</add>
      <archive path="update/update.rpf"><archive path="nested.rpf">
        <add source="data.xml">data.xml</add></archive></archive>
      <archive path="new.rpf" createIfNotExist="true" />
    </content></package>"""
    plan = OivWorkbench().inspect(_oiv_folder(tmp_path, assembly))
    codes = {item.code for item in plan.findings}
    assert "missing_oiv_source" in codes
    assert "archive_creation_target" not in codes
    assert plan.created_archive_operations[0].target == "mods/new.rpf"
    assert plan.created_archive_operations[0].supported
    assert "nested_archive" not in codes
    assert not plan.translatable


def test_oiv_builds_safe_created_archive_tree_into_managed_package(
    tmp_path, monkeypatch,
):
    assembly = """<package><metadata><name>Created DLC</name><gameversion>enhanced</gameversion></metadata><content>
      <archive path="update/x64/dlcpacks/created/dlc.rpf" createIfNotExist="true">
        <add source="data.xml">common/data/setup.xml</add>
        <archive path="x64/models.rpf" createIfNotExist="true">
          <add source="plugin.dll">vehicles/created.yft</add>
        </archive>
      </archive>
    </content></package>"""
    source = _oiv_folder(tmp_path, assembly)
    plan = OivWorkbench().inspect(source)
    assert plan.translatable
    assert not plan.managed_exportable
    assert len(plan.created_archive_operations) == 2
    assert not plan.rpf_batch_operations

    seen: dict[str, Path] = {}

    class FakeBuilder:
        def __init__(self, project_root, gta_path):
            seen["project"] = Path(project_root)
            seen["game"] = Path(gta_path)

        def build(self, loose, output):
            loose = Path(loose)
            assert (loose / "common" / "data" / "setup.xml").is_file()
            assert (
                loose / "x64" / "models.rpf.source" / "vehicles" / "created.yft"
            ).is_file()
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"RPF7-verified")
            report = output.with_name(f"{output.name}.validation.json")
            report.write_text("{}", encoding="utf-8")
            return output, report

    monkeypatch.setattr("allin1_sdk.rpf_builder.RpfArchiveBuilder", FakeBuilder)
    game = tmp_path / "game"
    game.mkdir()
    manifest_path = OivWorkbench().export_created_rpf_package(
        plan, tmp_path / "managed-created", project_root=tmp_path / "project",
        gta_path=game,
    )
    manifest = ModManifest.load(manifest_path)
    assert manifest.mod_type == "rpf"
    assert manifest.dlc_packs == ("created",)
    assert manifest.files[0].destination.as_posix() == (
        "mods/update/x64/dlcpacks/created/dlc.rpf"
    )
    assert manifest.files[0].sha256
    assert (manifest_path.parent / "rpf-sources").is_dir()
    assert seen["game"] == game


def test_oiv_created_parent_requires_nested_archive_creation_declaration(tmp_path):
    assembly = """<package><content>
      <archive path="update/x64/dlcpacks/created/dlc.rpf" createIfNotExist="true">
        <archive path="x64/models.rpf">
          <add source="data.xml">setup.xml</add>
        </archive>
      </archive>
    </content></package>"""
    plan = OivWorkbench().inspect(_oiv_folder(tmp_path, assembly))
    assert not plan.translatable
    assert "missing_archive_creation" in {item.code for item in plan.findings}


def test_oiv_created_rpf_cli_routes_game_and_package_destination(tmp_path, monkeypatch):
    assembly = """<package><content>
      <archive path="mods/update/x64/dlcpacks/created/dlc.rpf" createIfNotExist="true">
        <add source="data.xml">setup.xml</add>
      </archive>
    </content></package>"""
    source = _oiv_folder(tmp_path, assembly)
    game = tmp_path / "game"
    game.mkdir()
    destination = tmp_path / "created-package"

    def fake_export(self, plan, selected, *, project_root, gta_path):
        assert plan.created_archive_operations
        assert Path(selected) == destination
        assert Path(gta_path) == game
        destination.mkdir()
        manifest = destination / "mod.toml"
        manifest.write_text("routed", encoding="utf-8")
        return manifest

    monkeypatch.setattr(OivWorkbench, "export_created_rpf_package", fake_export)
    result = CliRunner().invoke(main, [
        "sdk", "oiv-plan", str(source), "-o", str(tmp_path / "plan.md"),
        "--created-rpf-package", str(destination), "--gta-path", str(game),
    ])
    assert result.exit_code == 0, result.output
    assert "created RPF export ready" in result.output
    assert (destination / "mod.toml").read_text(encoding="utf-8") == "routed"


def test_oiv_nested_add_and_exact_delete_export_atomic_rpf_batch(tmp_path):
    assembly = """<package><metadata><name>Nested batch</name></metadata><content>
      <archive path="update/update.rpf"><archive path="x64/data/nested.rpf">
        <add source="data.xml">common/data/new.xml</add>
        <delete>common/data/old.xml</delete>
      </archive></archive>
    </content></package>"""
    source = _oiv_folder(tmp_path, assembly)
    plan = OivWorkbench().inspect(source)
    assert plan.translatable
    assert not plan.managed_exportable
    assert len(plan.rpf_batch_operations) == 2
    manifests = OivWorkbench().export_rpf_batch_manifests(
        plan, tmp_path / "batches",
    )
    assert len(manifests) == 1
    authored = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert authored["outer_archive"] == "mods/update/update.rpf"
    assert [item["action"] for item in authored["changes"]] == ["upsert", "delete"]
    assert {item["archive_path"] for item in authored["changes"]} == {
        "x64/data/nested.rpf"
    }
    payload = manifests[0].parent / authored["changes"][0]["payload"]
    assert payload.read_text(encoding="utf-8") == "<data />"
    with pytest.raises(ValueError, match="atomic nested-RPF"):
        OivWorkbench().export_managed_package(plan, tmp_path / "managed-nested")

    result = CliRunner().invoke(main, [
        "sdk", "oiv-plan", str(source), "-o", str(tmp_path / "nested-plan.md"),
        "--rpf-batches", str(tmp_path / "cli-batches"),
    ])
    assert result.exit_code == 0, result.output
    assert "1 atomic RPF batch manifest(s)" in result.output


def test_oiv_plan_blocks_destinations_outside_managed_roots(tmp_path):
    assembly = """<package><content>
      <add source="data.xml">unowned/root/file.bin</add>
    </content></package>"""
    plan = OivWorkbench().inspect(_oiv_folder(tmp_path, assembly))
    assert "unsupported_destination" in {item.code for item in plan.findings}
    assert not plan.translatable


def test_oiv_rejects_missing_bad_or_entity_assembly(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="assembly.xml"):
        OivWorkbench().inspect(empty)
    bad = _oiv_folder(tmp_path / "bad", "<wrong />")
    with pytest.raises(ValueError, match="root must"):
        OivWorkbench().inspect(bad)
    entity = _oiv_folder(
        tmp_path / "entity",
        '<!DOCTYPE package [<!ENTITY x "bad">]><package><content /></package>',
    )
    with pytest.raises(ValueError, match="DTD/entity"):
        OivWorkbench().inspect(entity)


def _dlc_xml(*names: str) -> str:
    items = "".join(f"<Item>dlcpacks:/{name}/</Item>" for name in names)
    return f"<SMandatoryPacksData><Paths>{items}</Paths></SMandatoryPacksData>"


def test_dlc_inventory_reconciles_stock_mod_receipt_and_registration_states(tmp_path):
    game = tmp_path / "game"
    (game / "GTA5_Enhanced.exe").parent.mkdir(parents=True)
    (game / "GTA5_Enhanced.exe").write_bytes(b"exe")
    stock = game / "update" / "x64" / "dlcpacks"
    mods = game / "mods" / "update" / "x64" / "dlcpacks"
    for root, name, payload in (
        (stock, "stock", True), (mods, "ready", True),
        (mods, "disabled", True), (mods, "incomplete", False),
    ):
        folder = root / name
        folder.mkdir(parents=True)
        if payload:
            (folder / "dlc.rpf").write_bytes(b"rpf")
    receipts = game / "scripts" / ".allin1" / "mods"
    receipts.mkdir(parents=True)
    (receipts / "owned.json").write_text(json.dumps({
        "dlc_packs": ["ready", "receiptlost"], "owned_dlc_packs": ["ready"],
    }), encoding="utf-8")
    (receipts / "broken.json").write_text("{", encoding="utf-8")

    report = DlcInventory(tmp_path).scan(
        game, dlclist_xml=_dlc_xml("stock", "ready", "duplicate", "duplicate", "missing"),
    )
    states = {item.name: item.state for item in report.packs}
    assert report.edition == "Enhanced"
    assert states == {
        "disabled": "Unregistered", "duplicate": "Duplicate registration",
        "incomplete": "Incomplete payload", "missing": "Registration only",
        "ready": "Ready", "receiptlost": "Missing payload", "stock": "Ready",
    }
    ready = next(item for item in report.packs if item.name == "ready")
    assert ready.allin1_owned and ready.ownership == "ALLIN1 managed"
    assert "invalid_receipt" in {item.code for item in report.findings}
    written = report.write(tmp_path / "dlc.md")
    assert written.with_suffix(".json").is_file()
    assert "Duplicate registration" in written.read_text(encoding="utf-8")


def test_dlc_inventory_reports_unavailable_dlclist_without_losing_folders(tmp_path):
    game = tmp_path / "game"
    pack = game / "mods" / "update" / "x64" / "dlcpacks" / "test"
    pack.mkdir(parents=True)
    (pack / "dlc.rpf").write_bytes(b"rpf")
    report = DlcInventory(tmp_path).scan(game)
    assert report.packs[0].state == "Unregistered"
    assert report.findings[0].code == "dlclist_unavailable"
    with pytest.raises(ValueError, match="Invalid dlclist"):
        DlcInventory(tmp_path).scan(game, dlclist_xml="<broken")


VEHICLES_META = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>devcar</modelName><txdName>devcar</txdName><handlingId>DEVHAND</handlingId>
<gameName>DEVCAR</gameName><vehicleMakeName>DEV</vehicleMakeName>
<audioNameHash>TAILGATER</audioNameHash><layout>LAYOUT_STANDARD</layout>
<type>VEHICLE_TYPE_CAR</type><vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""
HANDLING_META = """<CHandlingDataMgr><HandlingData><Item>
<handlingName>DEVHAND</handlingName></Item></HandlingData></CHandlingDataMgr>"""
VARIATIONS_META = """<CVehicleModelInfoVariation><variationData><Item>
<modelName>devcar</modelName><kits><Item>123_devkit</Item></kits>
<lightSettings value="1" /></Item></variationData></CVehicleModelInfoVariation>"""
CARCOLS_META = """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>123_devkit</kitName><id value="123" /></Item></Kits></CVehicleModelInfoVarGlobal>"""
CONTENT_XML = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_devcar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""


def _vehicle_package(root: Path, *, complete: bool = True) -> Path:
    package = root / "vehicle"
    package.mkdir(parents=True)
    files = {
        "vehicles.meta": VEHICLES_META, "handling.meta": HANDLING_META,
        "carvariations.meta": VARIATIONS_META, "carcols.meta": CARCOLS_META,
        "content.xml": CONTENT_XML,
    }
    for name, content in files.items():
        (package / name).write_text(content, encoding="utf-8")
    if complete:
        stream = package / "stream"
        stream.mkdir()
        (stream / "devcar.yft").write_bytes(b"model")
        (stream / "devcar.ytd").write_bytes(b"texture")
        (package / "american_rel.rpf.gxt2").write_bytes(b"labels")
    return package


def test_vehicle_compiler_resolves_cross_file_graph_and_writes_all_formats(tmp_path):
    report = RageVehicleDataCompiler().compile(_vehicle_package(tmp_path))
    assert report.error_count == 0
    assert report.warning_count == 0
    vehicle = report.vehicles[0]
    assert vehicle.handling_resolved
    assert vehicle.tuning_kits == ("123_devkit",)
    assert vehicle.model_assets == ("stream/devcar.yft",)
    assert vehicle.texture_assets == ("stream/devcar.ytd",)
    assert vehicle.registration_sources == ("content.xml",)

    written = report.write_bundle(tmp_path / "compiled")
    assert len(written) == 5 and all(path.is_file() for path in written)
    with zipfile.ZipFile(tmp_path / "compiled" / "vehicles.xlsx") as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        assert b"devcar" in workbook.read("xl/worksheets/sheet1.xml")


def test_vehicle_compiler_reports_every_missing_relationship_and_empty_package(tmp_path):
    package = _vehicle_package(tmp_path, complete=False)
    (package / "handling.meta").unlink()
    (package / "carvariations.meta").unlink()
    (package / "content.xml").unlink()
    report = RageVehicleDataCompiler().compile(package)
    codes = {item.code for item in report.findings}
    assert {
        "missing_handling", "missing_variation", "missing_model_asset",
        "missing_texture_asset", "missing_registration", "missing_text_dictionary",
    } <= codes

    empty = tmp_path / "empty-vehicle"
    empty.mkdir()
    empty_report = RageVehicleDataCompiler().compile(empty)
    assert empty_report.findings[0].code == "no_vehicles"
    outputs = empty_report.write_bundle(tmp_path / "empty-output")
    assert all(path.is_file() for path in outputs)


def test_new_sdk_cli_tools_generate_reports(tmp_path):
    runner = CliRunner()
    oiv = runner.invoke(main, [
        "sdk", "oiv-plan", str(_oiv_archive(tmp_path)),
        "-o", str(tmp_path / "cli-oiv.md"),
        "--managed-package", str(tmp_path / "cli-managed"),
    ])
    assert oiv.exit_code == 0, oiv.output
    assert "managed export ready" in oiv.output

    game = tmp_path / "cli-game"
    game.mkdir()
    dlc = runner.invoke(main, [
        "sdk", "dlc-inventory", str(game), "-o", str(tmp_path / "cli-dlc.md"),
    ])
    assert dlc.exit_code == 0, dlc.output
    assert "DLC packages" in dlc.output

    vehicle = runner.invoke(main, [
        "sdk", "compile-vehicle-data", str(_vehicle_package(tmp_path / "cli")),
        "-o", str(tmp_path / "cli-compiled"),
    ])
    assert vehicle.exit_code == 0, vehicle.output
    assert "Compiled 1 vehicles" in vehicle.output
