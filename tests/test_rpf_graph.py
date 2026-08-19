from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.rpf_graph import RpfPackageGraph


def _node_id(state, name):
    return next(node_id for node_id, node in state["nodes"].items() if node["name"] == name)


def test_imports_validates_and_materializes_nested_rpf_graph(tmp_path):
    source = tmp_path / "sample.rpf.source"
    (source / "common" / "data").mkdir(parents=True)
    (source / "common" / "empty").mkdir()
    (source / "common" / "data" / "setup.xml").write_text("setup", encoding="utf-8")
    nested = source / "x64" / "vehicles.rpf.source"
    nested.mkdir(parents=True)
    (nested / "vehicle.ytd").write_bytes(b"texture")

    graph = RpfPackageGraph.create_from_folder(source, tmp_path / "package-graph.json")
    state = RpfPackageGraph.validate(graph)
    report = RpfPackageGraph.describe(graph)
    assert state["nodes"][state["root_id"]]["name"] == "sample.rpf"
    assert report["summary"] == {
        "nodes": 8, "edges": 7, "archives": 2,
        "directories": 4, "files": 2, "source_bytes": 12,
    }
    materialized = RpfPackageGraph.materialize(graph, tmp_path / "materialized")
    assert (materialized / "common" / "empty").is_dir()
    assert (materialized / "common" / "data" / "setup.xml").read_text() == "setup"
    assert (
        materialized / "x64" / "vehicles.rpf.source" / "vehicle.ytd"
    ).read_bytes() == b"texture"
    assert hashlib.sha256(graph.read_bytes()).hexdigest() == state["graph_sha256"]


def test_graph_mutations_reparent_refresh_and_recursive_remove(tmp_path):
    graph = RpfPackageGraph.create_empty("dlc.rpf", tmp_path / "graph.json")
    payload = tmp_path / "vehicle.ytd"
    payload.write_bytes(b"first")
    directory = RpfPackageGraph.add_container(graph, "root", "x64", x=360, y=80)
    archive = RpfPackageGraph.add_container(
        graph, directory, "vehicles.rpf", archive=True, x=640, y=80,
    )
    file_node = RpfPackageGraph.add_file(graph, archive, payload, x=920, y=80)
    extra = RpfPackageGraph.add_container(graph, "root", "common", x=360, y=220)
    RpfPackageGraph.rename_node(graph, extra, "data")
    RpfPackageGraph.reparent_node(graph, extra, archive)
    RpfPackageGraph.set_position(graph, file_node, 940.5, 120.25)
    state = RpfPackageGraph.validate(graph)
    assert state["parents"][extra] == archive
    assert state["nodes"][file_node]["x"] == 940.5
    assert RpfPackageGraph.auto_layout(graph) == 5
    laid_out = RpfPackageGraph.validate(graph)
    assert laid_out["nodes"]["root"]["x"] == 80
    assert laid_out["nodes"][file_node]["x"] > laid_out["nodes"][archive]["x"]

    before = graph.read_bytes()
    with pytest.raises(ValueError, match="collision"):
        RpfPackageGraph.add_container(graph, "root", "X64")
    assert graph.read_bytes() == before

    payload.write_bytes(b"changed")
    with pytest.raises(ValueError, match="refresh it explicitly"):
        RpfPackageGraph.validate(graph)
    assert RpfPackageGraph.refresh_sources(graph) == 1
    assert RpfPackageGraph.validate(graph)["nodes"][file_node]["size"] == 7

    removed = RpfPackageGraph.remove_node(graph, archive)
    assert {archive, file_node, extra}.issubset(removed)
    remaining = RpfPackageGraph.validate(graph)
    assert set(remaining["nodes"]) == {"root", directory}


def test_graph_rejects_cycles_unsafe_sources_and_prebuilt_rpfs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested.rpf").write_bytes(b"RPF7")
    with pytest.raises(ValueError, match="Prebuilt RPF"):
        RpfPackageGraph.create_from_folder(source, tmp_path / "bad.json")

    graph = RpfPackageGraph.create_empty("root.rpf", tmp_path / "graph.json")
    first = RpfPackageGraph.add_container(graph, "root", "first")
    second = RpfPackageGraph.add_container(graph, first, "second")
    before = graph.read_bytes()
    with pytest.raises(ValueError, match="cycle"):
        RpfPackageGraph.reparent_node(graph, first, second)
    assert graph.read_bytes() == before
    with pytest.raises(ValueError, match="root cannot"):
        RpfPackageGraph.remove_node(graph, "root")
    with pytest.raises(ValueError, match="own JSON"):
        RpfPackageGraph.add_file(graph, first, graph)
    for unsafe in ("bad?name", "trailing. ", "NUL", "COM1.bin"):
        with pytest.raises(ValueError, match="non-materializable"):
            RpfPackageGraph.add_container(graph, first, unsafe)


def test_graph_build_binds_report_and_discards_output_on_graph_drift(tmp_path):
    source = tmp_path / "content.bin"
    source.write_bytes(b"content")
    graph = RpfPackageGraph.create_empty("dlc.rpf", tmp_path / "graph.json")
    RpfPackageGraph.add_file(graph, "root", source)

    class FakeBuilder:
        def build(self, loose, output):
            assert (Path(loose) / "content.bin").read_bytes() == b"content"
            archive = Path(output)
            archive.write_bytes(b"RPF7-built")
            report = archive.with_name(f"{archive.name}.validation.json")
            report.write_text(json.dumps({"status": "verified"}), encoding="utf-8")
            return archive, report

    archive, report_path = RpfPackageGraph.build(
        graph, FakeBuilder(), tmp_path / "built.rpf",
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert archive.read_bytes() == b"RPF7-built"
    assert report["source"] == str(graph)
    assert report["source_kind"] == "rpf_package_graph"
    assert report["materialized_source_ephemeral"] is True
    assert report["graph"]["path"] == str(graph)
    assert report["graph"]["sha256"] == hashlib.sha256(graph.read_bytes()).hexdigest()

    class DriftingBuilder(FakeBuilder):
        def build(self, loose, output):
            result = super().build(loose, output)
            RpfPackageGraph.set_position(graph, "root", 999, 999)
            return result

    output = tmp_path / "drift.rpf"
    with pytest.raises(RuntimeError, match="changed during archive creation"):
        RpfPackageGraph.build(graph, DriftingBuilder(), output)
    assert not output.exists()
    assert not output.with_name(f"{output.name}.validation.json").exists()


def test_import_archive_creates_retained_provenance_bound_graph_workspace(tmp_path):
    archive = tmp_path / "existing.rpf"
    archive.write_bytes(b"RPF7-existing")
    index = SimpleNamespace(source=archive)

    class FakeService:
        def extract_authoring_tree(self, loaded, destination):
            assert loaded is index
            source = Path(destination)
            (source / "common").mkdir(parents=True)
            (source / "common" / "setup.xml").write_text("setup", encoding="utf-8")
            nested = source / "x64" / "vehicles.rpf.source"
            nested.mkdir(parents=True)
            (nested / "model.yft").write_bytes(b"model")
            return source, {
                "schema_version": 1,
                "operation": "rpf_authoring_tree_export",
                "created_utc": "2026-08-19T00:00:00+00:00",
                "source": {
                    "path": str(archive.resolve()), "edition": "Enhanced",
                    "size": archive.stat().st_size,
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                },
                "summary": {
                    "archives": 2, "directories": 3,
                    "files": 2, "logical_bytes": 10,
                },
                "directories": ["common", "x64", "x64/vehicles.rpf.source"],
                "files": [],
            }

    workspace = tmp_path / "imported"
    graph = RpfPackageGraph.import_archive(index, FakeService(), workspace)
    state = RpfPackageGraph.validate(graph)
    report = json.loads((workspace / "rpf-graph-import.json").read_text(encoding="utf-8"))
    assert graph == workspace / "rpf-graph.json"
    assert state["archive_count"] == 2 and state["file_count"] == 2
    assert state["payload"]["origin"]["sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    assert all(
        Path(node["source"]).is_relative_to(workspace)
        for node in state["nodes"].values() if node["type"] == "file"
    )
    assert report["operation"] == "rpf_graph_archive_import"
    assert report["graph"]["sha256"] == hashlib.sha256(graph.read_bytes()).hexdigest()


def test_rpf_graph_cli_covers_create_mutate_inspect_materialize_and_build(
    tmp_path, monkeypatch,
):
    source_tree = tmp_path / "source"
    source_tree.mkdir()
    (source_tree / "base.bin").write_bytes(b"base")
    graph = tmp_path / "package.json"
    runner = CliRunner()
    created = runner.invoke(main, [
        "sdk", "create-rpf-graph", str(source_tree), "--root-name", "dlc.rpf",
        "--output", str(graph),
    ])
    assert created.exit_code == 0, created.output
    inspected = runner.invoke(main, ["inspect-rpf-graph", str(graph)])
    assert inspected.exit_code == 0 and '"operation": "rpf_package_graph_inspection"' in inspected.output

    refused = runner.invoke(main, [
        "add-rpf-graph-container", str(graph), "root", "common",
    ])
    assert refused.exit_code != 0 and "--acknowledge-edit" in refused.output
    added = runner.invoke(main, [
        "add-rpf-graph-container", str(graph), "root", "common",
        "--x", "300", "--y", "100", "--acknowledge-edit",
    ])
    assert added.exit_code == 0, added.output
    directory = added.output.strip().rsplit(" ", 1)[-1]
    extra = tmp_path / "extra.bin"
    extra.write_bytes(b"extra")
    file_added = runner.invoke(main, [
        "add-rpf-graph-file", str(graph), directory, str(extra),
        "--name", "renamed.bin", "--acknowledge-edit",
    ])
    assert file_added.exit_code == 0, file_added.output
    file_node = file_added.output.strip().rsplit(" ", 1)[-1]
    assert runner.invoke(main, [
        "position-rpf-graph-node", str(graph), file_node, "700", "180",
        "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "layout-rpf-graph", str(graph), "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "rename-rpf-graph-node", str(graph), directory, "data", "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "reparent-rpf-graph-node", str(graph), file_node, "root", "--acknowledge-edit",
    ]).exit_code == 0

    materialized = tmp_path / "loose"
    result = runner.invoke(main, [
        "materialize-rpf-graph", str(graph), "--output", str(materialized),
    ])
    assert result.exit_code == 0, result.output
    assert (materialized / "renamed.bin").read_bytes() == b"extra"

    game = tmp_path / "game"
    game.mkdir()

    class FakeBuilder:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game

        def build(self, loose, output):
            assert (Path(loose) / "renamed.bin").is_file()
            archive = Path(output)
            archive.write_bytes(b"RPF7")
            report = archive.with_name(f"{archive.name}.validation.json")
            report.write_text('{"status":"verified"}', encoding="utf-8")
            return archive, report

    monkeypatch.setattr("allin1_sdk.cli.RpfArchiveBuilder", FakeBuilder)
    built = runner.invoke(main, [
        "build-rpf-graph", str(graph), "--gta-path", str(game),
        "--output", str(tmp_path / "built.rpf"),
    ])
    assert built.exit_code == 0, built.output
    assert "Graph-bound validation report" in built.output

    removed = runner.invoke(main, [
        "remove-rpf-graph-node", str(graph), directory, "--acknowledge-edit",
    ])
    assert removed.exit_code == 0 and "source files unchanged" in removed.output


def test_import_rpf_graph_cli_and_agent_command_surface(tmp_path, monkeypatch):
    archive = tmp_path / "existing.rpf"
    archive.write_bytes(b"RPF7")
    game = tmp_path / "game"
    game.mkdir()
    index = SimpleNamespace(source=archive)

    class FakeService:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game

        def index(self, selected):
            assert Path(selected) == archive
            return index

        def extract_authoring_tree(self, loaded, destination):
            assert loaded is index
            source = Path(destination)
            source.mkdir(parents=True)
            (source / "content.bin").write_bytes(b"content")
            return source, {
                "schema_version": 1, "operation": "rpf_authoring_tree_export",
                "created_utc": "2026-08-19T00:00:00+00:00",
                "source": {
                    "path": str(archive.resolve()), "edition": "Legacy",
                    "size": 4, "sha256": hashlib.sha256(b"RPF7").hexdigest(),
                },
                "summary": {
                    "archives": 1, "directories": 0,
                    "files": 1, "logical_bytes": 7,
                },
                "directories": [], "files": [],
            }

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    result = CliRunner().invoke(main, [
        "sdk", "import-rpf-graph", str(archive), "--gta-path", str(game),
        "--output", str(tmp_path / "workspace"),
    ])
    assert result.exit_code == 0, result.output
    assert "Source archive unchanged" in result.output
    assert (tmp_path / "workspace" / "rpf-graph.json").is_file()


def test_rpf_graph_desktop_surface_uses_ports_and_shared_graph_model():
    source = (
        Path(__file__).parents[1] / "src" / "allin1_sdk" / "rpf_graph_ui.py"
    ).read_text(encoding="utf-8")
    assert "class RpfPackageGraphDialog" in source
    assert 'f"out:{node_id}"' in source and 'f"in:{node_id}"' in source
    assert "RpfPackageGraph.reparent_node" in source
    assert "RpfPackageGraph.materialize" in source
    assert "RpfPackageGraph.build" in source
    assert "RpfPackageGraph.import_archive" in (
        Path(__file__).parents[1] / "src" / "allin1_sdk" / "rpf_explorer.py"
    ).read_text(encoding="utf-8")
