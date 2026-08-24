from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path

import pytest

from allin1_sdk.app import _configure_style
from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.binary_workspace_ui import BinaryWorkspaceFrame
from allin1_sdk.oiv_workbench_ui import OivWorkbenchFrame
from allin1_sdk.rpf_change_set_ui import RpfChangeSetFrame
from allin1_sdk.rpf_tools import RpfEntryRecord
from allin1_sdk.texture_editor import TextureDictionaryEditorFrame


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    _configure_style(root)
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def _assert_horizontal(tree, scrollbar) -> None:
    assert str(scrollbar.cget("orient")) == "horizontal"
    assert str(tree.cget("xscrollcommand"))


def _texture_workspace(root: Path) -> Path:
    workspace = root / "texture-workspace"
    assets = workspace / "edit" / "assets"
    assets.mkdir(parents=True)
    xml = workspace / "edit" / "sample.ytd.xml"
    xml.write_text("<TextureDictionary />\n", encoding="utf-8")
    (workspace / "native-workspace.json").write_text(
        json.dumps({
            "schema_version": 1,
            "operation": "native_asset_workspace",
            "source": {"name": "sample.ytd", "suffix": ".ytd"},
            "xml": {"path": "edit/sample.ytd.xml"},
        }),
        encoding="utf-8",
    )
    return workspace


def test_recipe_tables_keep_wide_operations_and_findings_accessible(tmp_path, tk_root):
    frame = OivWorkbenchFrame(tk_root, tmp_path)
    _assert_horizontal(frame.operations, frame.operation_xscroll)
    _assert_horizontal(frame.findings, frame.finding_xscroll)
    frame.destroy()


def test_binary_history_keeps_wide_records_accessible(tmp_path, tk_root):
    workspace = BinaryPatchWorkspace().export_bytes(
        "sample.bin", bytes(range(64)), tmp_path / "binary-workspace",
    )
    frame = BinaryWorkspaceFrame(tk_root, workspace)
    _assert_horizontal(frame.history_tree, frame.history_xscroll)
    frame.destroy()


def test_texture_inventory_keeps_wide_metadata_accessible(tmp_path, tk_root):
    frame = TextureDictionaryEditorFrame(
        tk_root, _texture_workspace(tmp_path), tmp_path, on_close=lambda: None,
    )
    _assert_horizontal(frame.tree, frame.texture_xscroll)
    frame.destroy()


def test_visual_change_set_uses_an_explicit_visible_target(tk_root):
    entry = RpfEntryRecord(
        id="root::common/data/item.meta", archive_path="root.rpf",
        path="common/data/item.meta", name="item.meta", kind="file",
        size=128, stored_size=96,
    )
    selection = [entry]
    frame = RpfChangeSetFrame(
        tk_root, get_index=lambda: None, get_service=lambda: None,
        get_selected=lambda: selection[0],
    )

    assert all(str(button.cget("state")) == "disabled"
               for button in frame.target_actions)
    frame.capture_target()
    assert frame.target is entry
    assert "common/data/item.meta" in frame.target_text.get()
    assert all(str(button.cget("state")) == "normal"
               for button in frame.target_actions)

    selection[0] = None
    assert frame.target is entry  # selection changes cannot silently retarget an action
    frame.capture_target()
    assert frame.target is None
    assert all(str(button.cget("state")) == "disabled"
               for button in frame.target_actions)
    frame.destroy()
