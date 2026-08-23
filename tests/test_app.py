from pathlib import Path

from allin1_sdk.app import _launch_arguments


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
    assert parsed.gta_path is None
