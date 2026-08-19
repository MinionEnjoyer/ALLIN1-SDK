from pathlib import Path

from allin1_sdk.app import _launch_arguments


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
