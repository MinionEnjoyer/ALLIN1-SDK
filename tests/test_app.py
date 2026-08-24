from pathlib import Path
from types import SimpleNamespace

from allin1_sdk.app import _launch_arguments


def test_frozen_graph_launcher_targets_packaged_desktop_sibling(tmp_path, monkeypatch):
    import allin1_sdk.cli as cli

    console = tmp_path / "allin1-sdk.exe"
    desktop = tmp_path / "ALLIN1-SDK-Desktop.exe"
    graph = tmp_path / "package-graph.json"
    console.write_bytes(b"MZconsole")
    desktop.write_bytes(b"MZdesktop")
    graph.write_text("{}", encoding="utf-8")
    launched = []

    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(console))
    monkeypatch.setattr(
        cli.RpfPackageGraph, "validate",
        lambda *_args, **_kwargs: {"nodes": {}},
    )
    monkeypatch.setattr(
        cli.subprocess, "Popen",
        lambda command, **options: (
            launched.append((command, options)) or SimpleNamespace(pid=4101)
        ),
    )

    assert cli._open_graph_window(graph) == 4101
    assert Path(launched[0][0][0]) == desktop.resolve()
    assert launched[0][0][1:] == ["--rpf-graph", str(graph.resolve())]


def test_frozen_workbench_launcher_targets_packaged_desktop_sibling(tmp_path, monkeypatch):
    import allin1_sdk.cli as cli

    agent = tmp_path / "ALLIN1-SDK-Agent.exe"
    desktop = tmp_path / "ALLIN1-SDK-Desktop.exe"
    package = tmp_path / "vehicle-package"
    agent.write_bytes(b"MZagent")
    desktop.write_bytes(b"MZdesktop")
    package.mkdir()
    launched = []
    scan = SimpleNamespace(vehicles=(object(),), weapons=(), peds=())

    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(agent))
    monkeypatch.setattr(
        cli, "AddonPackageInspector",
        lambda: SimpleNamespace(inspect=lambda _source: scan),
    )
    monkeypatch.setattr(
        cli.subprocess, "Popen",
        lambda command, **options: (
            launched.append((command, options)) or SimpleNamespace(pid=4102)
        ),
    )

    pid, counts = cli._open_workbench_window(package, "vehicles")
    assert pid == 4102
    assert counts == {"vehicles": 1, "weapons": 0, "peds": 0}
    assert Path(launched[0][0][0]) == desktop.resolve()
    assert launched[0][0][1:] == [
        "--workbench-package", str(package.resolve()),
        "--workbench-category", "vehicles",
    ]


def test_open_graph_cli_launches_desktop_through_shared_arguments(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    graph = tmp_path / "rpf-graph.json"
    graph.write_text("{}", encoding="utf-8")
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_graph_window",
        lambda selected, game: launched.append((selected, game)) or 7312,
    )
    result = CliRunner().invoke(main, ["open-rpf-graph", str(graph)])
    assert result.exit_code == 0
    assert '"operation": "open_rpf_graph"' in result.output
    assert '"pid": 7312' in result.output
    assert launched == [(graph, None)]


def test_desktop_accepts_direct_rpf_graph_launch_arguments(tmp_path):
    graph = tmp_path / "package-graph.json"
    game = tmp_path / "game"
    parsed = _launch_arguments([
        "--rpf-graph", str(graph), "--gta-path", str(game),
        "--graph-node", "vehicle_example",
    ])
    assert parsed.rpf_graph == Path(graph)
    assert parsed.gta_path == Path(game)
    assert parsed.graph_node == "vehicle_example"


def test_open_graph_cli_routes_a_focus_node(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    graph = tmp_path / "rpf-graph.json"
    graph.write_text("{}", encoding="utf-8")
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_graph_window",
        lambda selected, game, focus: launched.append((selected, game, focus)) or 7313,
    )
    result = CliRunner().invoke(main, [
        "open-rpf-graph", str(graph), "--focus-node", "graphcar@Enhanced",
    ])
    assert result.exit_code == 0
    assert launched == [(graph, None, "graphcar@Enhanced")]


def test_open_vehicle_workbench_cli_launches_desktop(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    package = tmp_path / "vehicle.zip"
    package.write_bytes(b"package")
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_vehicle_workbench_window",
        lambda selected, game: launched.append((selected, game)) or (8451, 2),
    )
    result = CliRunner().invoke(main, ["open-vehicle-workbench", str(package)])
    assert result.exit_code == 0
    assert '"operation": "open_vehicle_workbench"' in result.output
    assert '"vehicle_models": 2' in result.output
    assert '"pid": 8451' in result.output
    assert launched == [(package, None)]


def test_desktop_accepts_direct_vehicle_workbench_launch_arguments(tmp_path):
    package = tmp_path / "vehicle.rar"
    game = tmp_path / "game"
    parsed = _launch_arguments([
        "--vehicle-package", str(package), "--gta-path", str(game),
    ])
    assert parsed.vehicle_package == Path(package)
    assert parsed.rpf_graph is None
    assert parsed.gta_path == Path(game)


def test_open_unified_workbench_cli_routes_category_and_counts(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    package = tmp_path / "mixed-package"
    package.mkdir()
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_workbench_window",
        lambda selected, category, game: (
            launched.append((selected, category, game))
            or (8452, {"vehicles": 1, "weapons": 3, "peds": 2})
        ),
    )
    result = CliRunner().invoke(main, [
        "open-workbench", str(package), "--category", "weapons",
    ])
    assert result.exit_code == 0
    assert '"operation": "open_workbench"' in result.output
    assert '"weapons": 3' in result.output
    assert '"pid": 8452' in result.output
    assert launched == [(package, "weapons", None)]


def test_desktop_accepts_direct_unified_workbench_arguments(tmp_path):
    package = tmp_path / "mixed.zip"
    parsed = _launch_arguments([
        "--workbench-package", str(package),
        "--workbench-category", "peds",
    ])
    assert parsed.workbench_package == package
    assert parsed.workbench_category == "peds"
    assert parsed.rpf_graph is None


def test_desktop_accepts_direct_model_material_arguments(tmp_path):
    model = tmp_path / "example.yft"
    parsed = _launch_arguments([
        "--model-material-source", str(model),
    ])
    assert parsed.model_material_source == model
    assert parsed.workbench_package is None
    assert parsed.rpf_graph is None


def test_open_model_material_cli_routes_validated_source(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    model = tmp_path / "example.yft"
    model.write_bytes(b"RSC8" + b"\0" * 32)
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_model_material_window",
        lambda source, game: (launched.append((source, game)) or (9123, 1)),
    )

    result = CliRunner().invoke(main, [
        "open-model-material-workbench", str(model),
    ])

    assert result.exit_code == 0
    assert '"operation": "open_model_material_workbench"' in result.output
    assert '"model_assets": 1' in result.output
    assert launched == [(model, None)]


def test_inspect_workbench_exposes_ped_evidence_as_json(tmp_path):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    package = tmp_path / "ped-package"
    package.mkdir()
    (package / "peds.meta").write_text(
        "<CPedModelInfo__InitDataList><InitDatas><Item>"
        "<Name>ig_api_test</Name><Pedtype>CIVMALE</Pedtype>"
        "<ModelType>STANDARD</ModelType><PropsName>ig_api_test_p</PropsName>"
        "</Item></InitDatas></CPedModelInfo__InitDataList>",
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, [
        "inspect-workbench", str(package), "--category", "peds",
        "--gta-path", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert '"operation": "inspect_workbench"' in result.output
    assert '"vehicles": 0' in result.output
    assert '"name": "ig_api_test"' in result.output
    assert '"weapons": [' not in result.output


def test_open_package_graph_cli_routes_to_guarded_viewer(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    package = tmp_path / "vehicle.rar"
    package.write_bytes(b"package")
    graph = tmp_path / "workspace" / "package-graph.json"
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.cli._open_package_graph_window",
        lambda selected, game: (
            launched.append((selected, game)) or (9124, graph, 20, 2, False)
        ),
    )
    result = CliRunner().invoke(main, ["open-package-graph", str(package)])
    assert result.exit_code == 0
    assert '"operation": "open_package_graph"' in result.output
    assert '"package_members": 20' in result.output
    assert '"sealed_rpf_nodes": 2' in result.output
    assert '"workspace_reused": false' in result.output
    assert launched == [(package, None)]


def test_desktop_launch_arguments_default_to_workspace():
    parsed = _launch_arguments([])
    assert parsed.rpf_graph is None
    assert parsed.vehicle_package is None
    assert parsed.workbench_package is None
    assert parsed.workbench_category == "auto"
    assert parsed.gta_path is None
