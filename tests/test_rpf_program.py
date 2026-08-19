from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import rpf_program
from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_program import NODE_SPECS, PROGRAM_TEMPLATES, RpfPackageProgram


def _graph(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "content.bin").write_bytes(b"package content")
    graph = RpfPackageGraph.create_from_folder(
        source, tmp_path / "package-graph.json", root_name="package.rpf",
    )
    return source, graph


def test_rpf_program_reusable_templates_have_typed_connected_scaffolds(tmp_path):
    _source, graph = _graph(tmp_path)
    expected = {
        "validate": (2, 1, True),
        "loose-export": (4, 3, False),
        "verified-build": (4, 3, False),
        "compact-release": (5, 4, False),
        "origin-change-plan": (4, 3, False),
    }
    assert set(PROGRAM_TEMPLATES) == set(expected)
    for template, (nodes, links, ready) in expected.items():
        program = RpfPackageProgram.create(
            graph, tmp_path / f"{template}.json", template=template,
        )
        report = RpfPackageProgram.describe(program)
        assert report["template"] == template
        assert report["summary"]["nodes"] == nodes
        assert report["summary"]["links"] == links
        assert report["summary"]["ready"] is ready
        assert not any("input is not connected" in issue for issue in report["issues"])
    with pytest.raises(ValueError, match="Unknown RPF program template"):
        RpfPackageProgram.create(graph, tmp_path / "invalid.json", template="other")


def test_rpf_program_create_edit_validate_plan_and_typed_links(tmp_path):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    initial = RpfPackageProgram.describe(program, verify_graph=True)
    assert initial["status"] == "ready"
    assert initial["execution_order"] == ["package", "validate"]

    build = RpfPackageProgram.add_node(program, "build_rpf", x=700, y=100)
    output = RpfPackageProgram.add_node(program, "artifact_output", x=1000, y=100)
    incomplete = RpfPackageProgram.describe(program)
    assert incomplete["status"] == "incomplete"
    assert any("missing configuration" in item for item in incomplete["issues"])
    assert any("not connected" in item for item in incomplete["issues"])

    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "artifacts" / "package.rpf"
    RpfPackageProgram.configure_node(program, build, {
        "gta_path": str(game), "output": str(archive),
    })
    RpfPackageProgram.connect(program, "validate", build)
    RpfPackageProgram.connect(program, build, output)
    RpfPackageProgram.configure_node(program, output, {"label": "Release RPF"})
    ready = RpfPackageProgram.describe(program, verify_graph=True)
    assert ready["status"] == "ready"
    assert ready["execution_order"][-2:] == [build, output]

    plan_path, plan = RpfPackageProgram.plan(program, tmp_path / "program-plan.json")
    assert plan_path.is_file() and plan["status"] == "ready"
    assert plan["safety"]["stock_game_files_modified"] is False
    assert str(archive.resolve()) in plan["outputs"]
    assert str(archive.with_name(f"{archive.name}.validation.json").resolve()) in plan["outputs"]

    before = program.read_bytes()
    with pytest.raises(ValueError, match="Incompatible"):
        RpfPackageProgram.connect(program, "package", build)
    assert program.read_bytes() == before

    RpfPackageProgram.disconnect(program, output)
    assert any(
        issue.startswith(f"{output}: input is not connected")
        for issue in RpfPackageProgram.validate(program)["issues"]
    )
    RpfPackageProgram.set_position(program, output, 1234.5, 678.25)
    assert RpfPackageProgram.validate(program)["nodes"][output]["x"] == 1234.5
    assert RpfPackageProgram.auto_layout(program) == 4
    RpfPackageProgram.remove_node(program, output)
    with pytest.raises(ValueError, match="not found"):
        RpfPackageProgram.remove_node(program, output)
    with pytest.raises(ValueError, match="cannot be removed"):
        RpfPackageProgram.remove_node(program, "package")


def test_rpf_program_rejects_cycles_unknown_config_and_unsafe_output(tmp_path):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    game = tmp_path / "game"
    game.mkdir()
    first = RpfPackageProgram.add_node(program, "defragment_rpf")
    second = RpfPackageProgram.add_node(program, "defragment_rpf")
    with pytest.raises(ValueError, match="unsupported config"):
        RpfPackageProgram.configure_node(program, first, {"mystery": "value"})
    RpfPackageProgram.connect(program, first, second)
    before = program.read_bytes()
    with pytest.raises(ValueError, match="cycle"):
        RpfPackageProgram.connect(program, second, first)
    assert program.read_bytes() == before
    RpfPackageProgram.remove_node(program, second)
    RpfPackageProgram.remove_node(program, first)

    build = RpfPackageProgram.add_node(program, "build_rpf")
    RpfPackageProgram.configure_node(program, build, {
        "gta_path": str(game),
        "output": str(game / "mods" / "unsafe.rpf"),
    })
    RpfPackageProgram.connect(program, "validate", build)
    with pytest.raises(ValueError, match="only author outside GTA V"):
        RpfPackageProgram.plan(program, tmp_path / "unsafe-plan.json")


def test_rpf_program_detects_gta_ancestors_and_nested_output_collisions(tmp_path):
    _source, graph = _graph(tmp_path)
    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="created outside GTA V"):
        RpfPackageProgram.create(graph, game / "tools" / "program.json")

    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    materialize = RpfPackageProgram.add_node(program, "materialize_tree")
    build = RpfPackageProgram.add_node(program, "build_rpf")
    loose = tmp_path / "release"
    RpfPackageProgram.configure_node(program, materialize, {"output": str(loose)})
    RpfPackageProgram.configure_node(program, build, {
        "gta_path": str(game), "output": str(loose / "nested.rpf"),
    })
    RpfPackageProgram.connect(program, "validate", materialize)
    RpfPackageProgram.connect(program, "validate", build)
    with pytest.raises(ValueError, match="cannot contain one another"):
        RpfPackageProgram.plan(program, tmp_path / "plan.json")


def test_rpf_program_executes_external_materialization_and_binds_sources(tmp_path):
    source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    materialize = RpfPackageProgram.add_node(program, "materialize_tree")
    output_node = RpfPackageProgram.add_node(program, "artifact_output")
    materialized = tmp_path / "release" / "loose-tree"
    RpfPackageProgram.configure_node(
        program, materialize, {"output": str(materialized)},
    )
    RpfPackageProgram.connect(program, "validate", materialize)
    RpfPackageProgram.connect(program, materialize, output_node)
    source_sha256 = hashlib.sha256((source / "content.bin").read_bytes()).hexdigest()

    report_path, report = RpfPackageProgram.execute(
        program, tmp_path, tmp_path / "execution.json",
    )

    assert report_path.is_file() and report["status"] == "verified"
    assert (materialized / "content.bin").read_bytes() == b"package content"
    assert report["safety"]["stock_game_files_modified"] is False
    assert report["nodes"][-1]["artifact"] == str(materialized.resolve())
    assert hashlib.sha256((source / "content.bin").read_bytes()).hexdigest() == source_sha256


def test_rpf_program_execution_cleans_exact_outputs_when_report_fails(
    tmp_path, monkeypatch,
):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    materialize = RpfPackageProgram.add_node(program, "materialize_tree")
    materialized = tmp_path / "release" / "loose-tree"
    RpfPackageProgram.configure_node(
        program, materialize, {"output": str(materialized)},
    )
    RpfPackageProgram.connect(program, "validate", materialize)

    monkeypatch.setattr(
        rpf_program, "_write_json_new",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("report blocked")),
    )
    with pytest.raises(OSError, match="report blocked"):
        RpfPackageProgram.execute(program, tmp_path, tmp_path / "execution.json")
    assert not materialized.exists()
    assert not (tmp_path / "execution.json").exists()


def test_rpf_program_executes_build_defragment_and_origin_plan_nodes(
    tmp_path, monkeypatch,
):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    game = tmp_path / "game"
    game.mkdir()
    built = tmp_path / "built.rpf"
    compact = tmp_path / "compact.rpf"
    compact_report = tmp_path / "compact.json"
    origin_plan = tmp_path / "origin-plan.json"
    build = RpfPackageProgram.add_node(program, "build_rpf")
    defrag = RpfPackageProgram.add_node(program, "defragment_rpf")
    plan = RpfPackageProgram.add_node(program, "plan_origin")
    release = RpfPackageProgram.add_node(program, "artifact_output")
    review = RpfPackageProgram.add_node(program, "artifact_output")
    RpfPackageProgram.configure_node(program, build, {
        "gta_path": str(game), "output": str(built),
    })
    RpfPackageProgram.configure_node(program, defrag, {
        "gta_path": str(game), "output": str(compact),
        "report": str(compact_report),
    })
    RpfPackageProgram.configure_node(program, plan, {
        "gta_path": str(game), "output": str(origin_plan),
    })
    RpfPackageProgram.connect(program, "validate", build)
    RpfPackageProgram.connect(program, build, defrag)
    RpfPackageProgram.connect(program, defrag, release)
    RpfPackageProgram.connect(program, "validate", plan)
    RpfPackageProgram.connect(program, plan, review)

    class FakeBuilder:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game
            self.service = object()

        @staticmethod
        def validation_path(archive):
            archive = Path(archive)
            return archive.with_name(f"{archive.name}.validation.json")

    class FakeService:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game

        def index(self, archive):
            assert Path(archive) == built
            return "indexed-built-rpf"

        def defragment_verified_copy(self, index, output, report):
            assert index == "indexed-built-rpf"
            Path(output).write_bytes(b"compact")
            Path(report).write_text("{}", encoding="utf-8")
            return Path(output).resolve(), Path(report).resolve(), {"status": "verified"}

    def fake_build(_graph, _builder, output):
        archive = Path(output).resolve()
        validation = FakeBuilder.validation_path(archive)
        archive.write_bytes(b"built")
        validation.write_text("{}", encoding="utf-8")
        return archive, validation

    def fake_origin(_graph, _builder, service, output):
        assert service is not None
        planned = Path(output).resolve()
        payloads = planned.with_name(f"{planned.stem}.payload")
        planned.write_text("{}", encoding="utf-8")
        payloads.mkdir()
        return planned, payloads

    monkeypatch.setattr(rpf_program, "RpfArchiveBuilder", FakeBuilder)
    monkeypatch.setattr(rpf_program, "RpfExplorerService", FakeService)
    monkeypatch.setattr(rpf_program.RpfPackageGraph, "build", staticmethod(fake_build))
    monkeypatch.setattr(
        rpf_program.RpfPackageGraph, "plan_origin_changes", staticmethod(fake_origin),
    )

    report_path, report = RpfPackageProgram.execute(
        program, tmp_path, tmp_path / "execution.json",
    )
    assert report_path.is_file() and report["status"] == "verified"
    assert built.is_file() and compact.read_bytes() == b"compact"
    assert compact_report.is_file() and origin_plan.is_file()
    assert origin_plan.with_name("origin-plan.payload").is_dir()
    assert {item["type"] for item in report["nodes"]} >= {
        "build_rpf", "defragment_rpf", "plan_origin", "artifact_output",
    }


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda payload: payload.update(schema_version=99), "schema"),
        (lambda payload: payload.update(nodes="bad"), "nodes and links"),
        (lambda payload: payload.update(nodes=[]), "node limit"),
        (lambda payload: payload["nodes"].append("bad"), "nodes must be objects"),
        (lambda payload: payload["nodes"].append({
            "id": "INVALID SPACE", "type": "validate_graph",
            "x": 0, "y": 0, "config": {},
        }), "Unsafe"),
        (lambda payload: payload["nodes"].append({
            "id": "unknown", "type": "unknown", "x": 0, "y": 0, "config": {},
        }), "Unknown"),
        (lambda payload: payload["nodes"].append({
            "id": "bad-config", "type": "validate_graph", "x": 0, "y": 0,
            "config": [],
        }), "config must be an object"),
        (lambda payload: payload["links"][0].update(from_port="wrong"), "artifact to input"),
        (lambda payload: payload["links"].append(dict(payload["links"][0])), "Duplicate"),
    ],
)
def test_rpf_program_rejects_malformed_documents(tmp_path, mutate, message):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    payload = json.loads(program.read_text(encoding="utf-8"))
    mutate(payload)
    program.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        RpfPackageProgram.validate(program)


def test_rpf_program_refuses_invalid_files_ids_coordinates_and_configs(tmp_path):
    _source, graph = _graph(tmp_path)
    with pytest.raises(ValueError, match="extension"):
        RpfPackageProgram.create(graph, tmp_path / "program.txt")
    missing = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        RpfPackageProgram.validate(missing)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid"):
        RpfPackageProgram.validate(invalid)
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        RpfPackageProgram.validate(invalid)

    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    with pytest.raises(ValueError, match="Unsupported addable"):
        RpfPackageProgram.add_node(program, "package_source")
    with pytest.raises(ValueError, match="numeric"):
        RpfPackageProgram.add_node(program, "build_rpf", x=True)
    with pytest.raises(ValueError, match="coordinate"):
        RpfPackageProgram.add_node(program, "build_rpf", x=float("inf"))
    with pytest.raises(ValueError, match="no editable"):
        RpfPackageProgram.configure_node(program, "package", {})
    with pytest.raises(ValueError, match="must be text"):
        RpfPackageProgram.configure_node(program, "validate", {"output": 4})
    with pytest.raises(ValueError, match="no input link"):
        node = RpfPackageProgram.add_node(program, "artifact_output")
        RpfPackageProgram.disconnect(program, node)


def test_rpf_program_preflights_incomplete_existing_overlapping_and_report_paths(
    tmp_path,
):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    incomplete = RpfPackageProgram.add_node(program, "materialize_tree")
    with pytest.raises(ValueError, match="incomplete"):
        RpfPackageProgram.plan(program, tmp_path / "incomplete-plan.json")
    RpfPackageProgram.remove_node(program, incomplete)

    loose = tmp_path / "release"
    first = RpfPackageProgram.add_node(program, "materialize_tree")
    second = RpfPackageProgram.add_node(program, "materialize_tree")
    for node in (first, second):
        RpfPackageProgram.configure_node(program, node, {"output": str(loose)})
        RpfPackageProgram.connect(program, "validate", node)
    with pytest.raises(ValueError, match="output collision"):
        RpfPackageProgram.plan(program, tmp_path / "collision-plan.json")
    RpfPackageProgram.remove_node(program, second)

    loose.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        RpfPackageProgram.plan(program, tmp_path / "existing-plan.json")
    loose.rmdir()
    with pytest.raises(ValueError, match="collides with a configured artifact"):
        RpfPackageProgram.plan(program, loose / "nested-plan.json")
    with pytest.raises(ValueError, match="must use .json"):
        RpfPackageProgram.execute(program, tmp_path, tmp_path / "report.txt")
    existing_report = tmp_path / "existing-report.json"
    existing_report.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="report exists"):
        RpfPackageProgram.execute(program, tmp_path, existing_report)
    with pytest.raises(ValueError, match="collides with a configured artifact"):
        RpfPackageProgram.execute(program, tmp_path, loose / "execution.json")

    game = tmp_path / "Grand Theft Auto V"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="outside GTA V"):
        RpfPackageProgram.plan(program, game / "plan.json")
    with pytest.raises(ValueError, match="outside GTA V"):
        RpfPackageProgram.execute(program, tmp_path, game / "execution.json")


def test_rpf_program_dry_run_detects_program_and_graph_drift(tmp_path, monkeypatch):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    original_hash = rpf_program._sha256_file
    calls = 0

    def program_drift(path):
        nonlocal calls
        result = original_hash(path)
        if Path(path).resolve() == program.resolve():
            calls += 1
            if calls >= 3:
                return "0" * 64
        return result

    monkeypatch.setattr(rpf_program, "_sha256_file", program_drift)
    with pytest.raises(RuntimeError, match="program changed"):
        RpfPackageProgram.plan(program, tmp_path / "program-drift.json")
    monkeypatch.setattr(rpf_program, "_sha256_file", original_hash)

    original_validate = rpf_program.RpfPackageGraph.validate
    graph_calls = 0

    def graph_drift(selected, *, verify_sources=False):
        nonlocal graph_calls
        result = original_validate(selected, verify_sources=verify_sources)
        graph_calls += 1
        if graph_calls >= 3:
            result = dict(result)
            result["graph_sha256"] = "f" * 64
        return result

    monkeypatch.setattr(
        rpf_program.RpfPackageGraph, "validate", staticmethod(graph_drift),
    )
    with pytest.raises(RuntimeError, match="graph changed"):
        RpfPackageProgram.plan(program, tmp_path / "graph-drift.json")


def test_rpf_program_cleans_built_files_when_downstream_node_fails(
    tmp_path, monkeypatch,
):
    _source, graph = _graph(tmp_path)
    program = RpfPackageProgram.create(graph, tmp_path / "program.json")
    game = tmp_path / "game"
    game.mkdir()
    built = tmp_path / "built.rpf"
    validation = tmp_path / "built.rpf.validation.json"
    build = RpfPackageProgram.add_node(program, "build_rpf")
    defrag = RpfPackageProgram.add_node(program, "defragment_rpf")
    RpfPackageProgram.configure_node(program, build, {
        "gta_path": str(game), "output": str(built),
    })
    RpfPackageProgram.configure_node(program, defrag, {
        "gta_path": str(game), "output": str(tmp_path / "compact.rpf"),
        "report": str(tmp_path / "compact.json"),
    })
    RpfPackageProgram.connect(program, "validate", build)
    RpfPackageProgram.connect(program, build, defrag)

    class FakeBuilder:
        def __init__(self, *_args):
            pass

        @staticmethod
        def validation_path(archive):
            archive = Path(archive)
            return archive.with_name(f"{archive.name}.validation.json")

    class FailingService:
        def __init__(self, *_args):
            pass

        def index(self, _archive):
            return object()

        def defragment_verified_copy(self, *_args):
            raise ValueError("downstream failure")

    def fake_build(_graph, _builder, output):
        Path(output).write_bytes(b"built")
        validation.write_text("{}", encoding="utf-8")
        return Path(output).resolve(), validation.resolve()

    monkeypatch.setattr(rpf_program, "RpfArchiveBuilder", FakeBuilder)
    monkeypatch.setattr(rpf_program, "RpfExplorerService", FailingService)
    monkeypatch.setattr(
        rpf_program.RpfPackageGraph, "build", staticmethod(fake_build),
    )
    with pytest.raises(ValueError, match="downstream failure"):
        RpfPackageProgram.execute(program, tmp_path, tmp_path / "execution.json")
    assert not built.exists() and not validation.exists()


def test_rpf_program_node_specs_expose_typed_authoring_pipeline():
    assert NODE_SPECS["package_source"].output_type == "package"
    assert NODE_SPECS["validate_graph"].output_type == "validated_package"
    assert "validated_package" in NODE_SPECS["build_rpf"].input_types
    assert "rpf" in NODE_SPECS["defragment_rpf"].input_types
    assert set(NODE_SPECS["artifact_output"].input_types) == {
        "rpf", "directory", "plan",
    }


def test_rpf_program_cli_console_alias_and_agent_catalog(tmp_path):
    _source, graph = _graph(tmp_path)
    program = tmp_path / "program.json"
    runner = CliRunner()
    created = runner.invoke(main, [
        "sdk", "create-rpf-program", str(graph), "--output", str(program),
    ])
    assert created.exit_code == 0, created.output
    added = runner.invoke(main, [
        "add-rpf-program-node", str(program), "materialize_tree",
        "--config-json", json.dumps({"output": str(tmp_path / "loose")}),
        "--acknowledge-edit",
    ])
    assert added.exit_code == 0, added.output
    node_id = added.output.split(": ", 1)[1].split(" ", 1)[0]
    connected = runner.invoke(main, [
        "connect-rpf-program-nodes", str(program), "validate", node_id,
        "--acknowledge-edit",
    ])
    assert connected.exit_code == 0, connected.output
    planned = runner.invoke(main, [
        "plan-rpf-program", str(program), "--output", str(tmp_path / "plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    assert "No program operation was executed" in planned.output

    templates = runner.invoke(main, ["sdk", "list-rpf-program-templates"])
    assert templates.exit_code == 0, templates.output
    template_report = json.loads(templates.output)
    assert {item["id"] for item in template_report["templates"]} == set(
        PROGRAM_TEMPLATES
    )
    compact = tmp_path / "compact-program.json"
    created_compact = runner.invoke(main, [
        "create-rpf-program", str(graph), "--output", str(compact),
        "--template", "compact-release",
    ])
    assert created_compact.exit_code == 0, created_compact.output
    assert RpfPackageProgram.describe(compact)["template"] == "compact-release"

    catalog = {item["name"]: item for item in command_catalog()}
    for command in (
        "create-rpf-program", "add-rpf-program-node",
        "configure-rpf-program-node", "connect-rpf-program-nodes",
        "disconnect-rpf-program-node", "position-rpf-program-node",
        "layout-rpf-program", "remove-rpf-program-node",
        "plan-rpf-program", "run-rpf-program",
    ):
        assert catalog[command]["risk"] == "authoring_write"
    assert catalog["inspect-rpf-program"]["risk"] == "read_only"
    assert catalog["list-rpf-program-templates"]["risk"] == "read_only"


def test_rpf_program_desktop_is_embedded_typed_pin_canvas():
    root = Path(__file__).parents[1] / "src" / "allin1_sdk"
    source = (root / "rpf_program_ui.py").read_text(encoding="utf-8")
    graph_ui = (root / "rpf_graph_ui.py").read_text(encoding="utf-8")
    assert "class RpfProgramFrame" in source
    assert 'f"pin:{node_id}"' in source and 'f"pout:{node_id}"' in source
    assert "RpfPackageProgram.connect" in source
    assert "RpfPackageProgram.plan" in source
    assert "RpfPackageProgram.execute" in source
    assert "PROGRAM_TEMPLATES.items()" in source
    assert 'notebook.add(program_tab, text="Build Flow")' in graph_ui
    assert "RpfProgramFrame(" in graph_ui
