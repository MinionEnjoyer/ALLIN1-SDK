from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main
from allin1_sdk.package_graph import PackageGraphWorkspace
from allin1_sdk.package_relations import PackageRelationshipAnalyzer
from allin1_sdk.rpf_graph import RpfPackageGraph


VEHICLES = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>graphcar</modelName><txdName>graphcar</txdName>
<handlingId>GRAPHHAND</handlingId><gameName>GRAPHCAR</gameName>
<vehicleMakeName>GRAPH</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
<vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""
HANDLING = """<CHandlingDataMgr><HandlingData><Item>
<handlingName>GRAPHHAND</handlingName></Item></HandlingData></CHandlingDataMgr>"""
VARIATIONS = """<CVehicleModelInfoVariation><variationData><Item>
<modelName>graphcar</modelName><kits><Item>712_graphkit</Item></kits>
</Item></variationData></CVehicleModelInfoVariation>"""
CARCOLS = """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>712_graphkit</kitName><id value="712" />
</Item></Kits></CVehicleModelInfoVarGlobal>"""
CONTENT = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_graphcar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""


def _vehicle_package(root: Path) -> Path:
    package = root / "vehicle-package"
    stream = package / "stream"
    stream.mkdir(parents=True)
    for name, content in (
        ("vehicles.meta", VEHICLES), ("handling.meta", HANDLING),
        ("carvariations.meta", VARIATIONS), ("carcols.meta", CARCOLS),
        ("content.xml", CONTENT),
    ):
        (package / name).write_text(content, encoding="utf-8")
    (stream / "graphcar.yft").write_bytes(b"primary")
    (stream / "graphcar_hi.yft").write_bytes(b"high")
    (stream / "graphcar.ytd").write_bytes(b"texture")
    (stream / "graphcar_spoiler.yft").write_bytes(b"tuning")
    return package


def test_package_graph_persists_typed_vehicle_relationships(tmp_path):
    project = PackageGraphWorkspace(tmp_path / "projects").import_package(
        _vehicle_package(tmp_path)
    )
    state = RpfPackageGraph.validate(project.graph, verify_sources=True)
    report = state["semantic"]

    assert report is not None
    assert report["summary"]["entities"] == 1
    entity = report["entities"][0]
    assert entity["name"] == "graphcar"
    assert entity["metadata"]["handling_id"] == "GRAPHHAND"
    relations = {item["type"] for item in report["relations"]}
    assert {
        "primary_model", "high_detail_model", "texture_dictionary",
        "vehicle_metadata", "handling_metadata", "variation_metadata",
        "tuning_metadata", "tuning_asset", "registration", "install_target",
    } <= relations
    assert not any(
        item["code"] == "orphaned_vehicle_asset"
        and str(item.get("path", "")).endswith("graphcar_spoiler.yft")
        for item in report["findings"]
    )
    RpfPackageGraph.set_semantic_position(
        project.graph, entity["id"], 4321.0, 765.0,
    )
    refreshed = PackageRelationshipAnalyzer.analyze(project.graph)
    moved = refreshed["entities"][0]
    assert (moved["x"], moved["y"]) == (4321.0, 765.0)


def test_relationship_commands_share_api_and_validate_endpoints(tmp_path):
    project = PackageGraphWorkspace(tmp_path / "projects").import_package(
        _vehicle_package(tmp_path)
    )
    runner = CliRunner()
    analyzed = runner.invoke(main, ["analyze-package-graph", str(project.graph)])
    assert analyzed.exit_code == 0, analyzed.output
    assert json.loads(analyzed.output)["summary"]["relations"] >= 10

    inspected = execute_request({
        "id": "relations", "action": "execute",
        "command": "inspect-package-graph-relations",
        "args": [str(project.graph)],
    }, audit_path=tmp_path / "api-audit.jsonl")
    assert inspected["ok"] is True
    assert json.loads(inspected["result"]["output"])["entities"][0]["name"] == "graphcar"
    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["analyze-package-graph"]["risk"] == "authoring_write"
    assert catalog["inspect-package-graph-relations"]["risk"] == "read_only"

    payload = json.loads(project.graph.read_text(encoding="utf-8"))
    payload["semantic"]["relations"][0]["target"] = "missing_node"
    project.graph.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="endpoint"):
        RpfPackageGraph.validate(project.graph, verify_sources=False)


def test_relationship_inspection_requires_prior_analysis(tmp_path):
    source = tmp_path / "plain"
    source.mkdir()
    (source / "readme.txt").write_text("plain", encoding="utf-8")
    graph = RpfPackageGraph.create_from_folder(
        source, tmp_path / "plain-graph.json",
    )
    with pytest.raises(ValueError, match="has not been semantically analyzed"):
        PackageRelationshipAnalyzer.inspect(graph)
