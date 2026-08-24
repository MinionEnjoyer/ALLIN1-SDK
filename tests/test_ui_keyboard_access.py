from __future__ import annotations

from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "allin1_sdk"


def _source(name: str) -> str:
    return (SOURCE_ROOT / name).read_text(encoding="utf-8")


def test_rpf_primary_activation_supports_keyboard_and_pointer_input():
    explorer = _source("rpf_explorer.py")
    graph = _source("rpf_graph_ui.py")

    assert 'self.tree.bind("<Double-1>", self._activate_tree_item)' in explorer
    assert 'self.tree.bind("<Return>", self._activate_tree_item)' in explorer
    assert (
        'tree.bind("<Double-1>", lambda _event: '
        'self._open_recent_package_graph(tree))'
    ) in explorer
    assert (
        'tree.bind("<Return>", lambda _event: '
        'self._open_recent_package_graph(tree))'
    ) in explorer
    assert 'self.canvas.bind("<Double-1>", self._activate_selected)' in graph
    assert 'self.canvas.bind("<Return>", self._activate_selected)' in graph
