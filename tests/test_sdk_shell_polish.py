from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from allin1_sdk import addon_sdk_ui as sdk_ui
from allin1_sdk.app import _configure_style


ROOT = Path(__file__).resolve().parents[1]


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


def _texts(widget: tk.Misc) -> set[str]:
    values: set[str] = set()
    for child in widget.winfo_children():
        try:
            text = str(child.cget("text"))
        except tk.TclError:
            text = ""
        if text:
            values.add(text)
        values.update(_texts(child))
    return values


def _menu_labels(menu: tk.Menu) -> list[str]:
    end = menu.index("end")
    if end is None:
        return []
    return [
        str(menu.entrycget(index, "label"))
        for index in range(int(end) + 1)
        if menu.type(index) != "separator"
    ]


def test_sdk_shell_uses_unified_header_navigation_and_action_hierarchy(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        sdk_ui.AddonSdkDialog, "_load_examples",
        lambda self: self.status.set("SDK shell ready"),
    )
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, ROOT, standalone=True,
    )
    try:
        dialog.update()
        labels = _texts(dialog)
        assert "ALLIN1 · GTA V SDK" in labels
        assert "Support ALLIN1 ↗" in labels
        assert "WORKSPACES" in labels
        assert "Package Linker" in labels
        assert str(dialog.support_button.cget("takefocus")) == "1"
        assert _menu_labels(dialog.application_menu) == [
            "File", "Package", "View", "Tools", "Help",
        ]
        assert "Inspect & Export" in _menu_labels(dialog.package_menu)
        assert "Authoring & Utilities" in _menu_labels(dialog.package_menu)
        assert "Keyboard shortcuts" in _menu_labels(dialog.help_menu)
        assert dialog.bind("<Control-o>")
        assert dialog.bind("<F5>")
        assert dialog.refresh_audit_button.winfo_manager() == ""

        assert dialog.sidebar_toggle_button.accessible_name == (
            "Hide workspace sidebar (Ctrl+B)"
        )
        assert dialog.sidebar_toggle_rail.bind("<Button-1>")
        dialog._set_sidebar_visible(False)
        assert dialog.sidebar_toggle_button.accessible_name == (
            "Show workspace sidebar (Ctrl+B)"
        )

        dialog._select_workspace("help")
        dialog.update()
        assert dialog.workspace_context.get() == "Help Center"
        assert dialog.context_back_button.cget("text") == "‹ Package Linker"
    finally:
        dialog.destroy()

def test_sdk_shell_activity_strip_presents_success_warning_and_error(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        sdk_ui.AddonSdkDialog, "_load_examples",
        lambda self: self.status.set("SDK shell ready"),
    )
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, ROOT, standalone=True,
    )
    try:
        for message, style, glyph in (
            ("Inspecting package RPFs…", "Activity.Busy.TLabel", "◌"),
            ("Package RPF reports written", "Activity.Success.TLabel", "●"),
            ("No packages match filters", "Activity.Warning.TLabel", "●"),
            ("Package audit failed", "Activity.Error.TLabel", "●"),
        ):
            dialog.status.set(message)
            dialog.update_idletasks()
            assert str(dialog.activity_status_label.cget("style")) == style
            assert str(dialog.activity_status_indicator.cget("text")) == glyph
    finally:
        dialog.destroy()
