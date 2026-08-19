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
    ])
    assert parsed.rpf_graph == Path(graph)
    assert parsed.gta_path == Path(game)


def test_desktop_launch_arguments_default_to_workspace():
    parsed = _launch_arguments([])
    assert parsed.rpf_graph is None
    assert parsed.gta_path is None
