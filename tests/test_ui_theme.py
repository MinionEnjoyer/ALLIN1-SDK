from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

from allin1_sdk import ui_foundation as theme
from allin1_sdk.ui_foundation import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    apply_sdk_theme,
    load_ui_theme,
    normalize_theme_mode,
    save_ui_theme,
    shared_ui_settings_path,
    start_system_theme_polling,
    system_theme_from_apps_use_light_theme,
)


def test_shared_theme_contract_uses_allin1_local_appdata_path():
    path = shared_ui_settings_path({"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"})

    assert path == Path(
        r"C:\Users\Test\AppData\Local\ALLIN1\ui-settings.json"
    )


def test_theme_values_are_case_insensitive_and_fail_closed_to_system():
    assert normalize_theme_mode("LIGHT") == THEME_LIGHT
    assert normalize_theme_mode(" Dark ") == THEME_DARK
    assert normalize_theme_mode("system") == THEME_SYSTEM
    assert normalize_theme_mode("unsupported") == THEME_SYSTEM


def test_shared_theme_round_trip_preserves_future_settings(tmp_path):
    path = tmp_path / "ALLIN1" / "ui-settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "theme": "light",
        "future_launcher_setting": True,
    }), encoding="utf-8")

    assert save_ui_theme("DARK", path) == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "future_launcher_setting": True,
        "schema_version": 1,
        "theme": "dark",
    }
    assert load_ui_theme(path) == THEME_DARK
    assert not tuple(path.parent.glob(f".{path.name}.*.tmp"))


def test_missing_or_corrupt_shared_theme_defaults_to_system(tmp_path):
    path = tmp_path / "ui-settings.json"
    assert load_ui_theme(path) == THEME_SYSTEM
    path.write_text("not json", encoding="utf-8")
    assert load_ui_theme(path) == THEME_SYSTEM
    path.write_text("[]", encoding="utf-8")
    assert load_ui_theme(path) == THEME_SYSTEM


def test_windows_apps_use_light_theme_translation_is_deterministic():
    assert system_theme_from_apps_use_light_theme(1) == THEME_LIGHT
    assert system_theme_from_apps_use_light_theme(0) == THEME_DARK
    assert system_theme_from_apps_use_light_theme("0") == THEME_DARK
    assert system_theme_from_apps_use_light_theme("bad") == THEME_LIGHT


def test_system_theme_polling_reapplies_only_when_windows_mode_changes(monkeypatch):
    class FakeRoot:
        def __init__(self):
            self._allin1_theme_mode = THEME_SYSTEM
            self._allin1_effective_theme = THEME_LIGHT
            self.callbacks = []

        def _root(self):
            return self

        def after(self, delay, callback):
            self.callbacks.append((delay, callback))
            return f"after-{len(self.callbacks)}"

    root = FakeRoot()
    applied = []
    monkeypatch.setattr(theme, "detect_system_theme", lambda: THEME_DARK)
    monkeypatch.setattr(
        theme, "apply_sdk_theme",
        lambda widget, mode: applied.append((widget, mode)),
    )

    start_system_theme_polling(root, interval_ms=10)
    assert root.callbacks[0][0] == 250
    root.callbacks.pop(0)[1]()
    assert applied == [(root, THEME_SYSTEM)]
    assert root.callbacks[0][0] == 250


def test_dark_theme_restyles_ttk_and_native_chrome_but_not_dark_canvas():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    try:
        native = tk.Label(
            root, background="#ffffff", foreground="#173d32", text="Native",
        )
        native.pack()
        explicit_ttk = ttk.Label(root, foreground="#52635c", text="Muted")
        explicit_ttk.pack()
        fixed_canvas = tk.Canvas(root, background="#111714")
        fixed_canvas.pack()
        technical = tk.Frame(root, background="#101714")
        technical.pack()
        fixed_button = tk.Button(
            technical, background="#141f1a", foreground="#dce8e1",
            activebackground="#234b34", activeforeground="#ffffff",
            text="Fit",
        )
        fixed_button.pack()
        fixed_colors = {
            option: fixed_button.cget(option)
            for option in (
                "background", "foreground", "activebackground", "activeforeground",
            )
        }
        accent_button = tk.Button(
            root, background="#2d9c50", foreground="#ffffff",
            activebackground="#1f7f42", activeforeground="#ffffff",
            highlightbackground="#1f7f42", highlightcolor="#ffffff",
            text="Apply",
        )
        accent_button.pack()
        apply_sdk_theme(root, THEME_LIGHT)
        assert native.cget("background").casefold() == LIGHT_PALETTE.surface

        apply_sdk_theme(root, THEME_DARK)
        style = ttk.Style(root)
        assert style.lookup("TFrame", "background").casefold() == DARK_PALETTE.body
        assert style.lookup("Treeview", "fieldbackground").casefold() == (
            DARK_PALETTE.surface
        )
        assert native.cget("background").casefold() == DARK_PALETTE.surface
        assert native.cget("foreground").casefold() == DARK_PALETTE.primary
        assert str(explicit_ttk.cget("foreground")).casefold() == DARK_PALETTE.muted
        assert fixed_canvas.cget("background").casefold() == "#111714"
        assert accent_button.cget("background").casefold() == DARK_PALETTE.brand
        assert accent_button.cget("foreground").casefold() == (
            DARK_PALETTE.inverse_text
        )
        assert accent_button.cget("activeforeground").casefold() == (
            DARK_PALETTE.inverse_text
        )
        assert accent_button.cget("highlightcolor").casefold() == (
            DARK_PALETTE.inverse_text
        )
        assert {
            option: fixed_button.cget(option) for option in fixed_colors
        } == fixed_colors

        apply_sdk_theme(root, THEME_LIGHT)
        assert native.cget("background").casefold() == LIGHT_PALETTE.surface
        assert native.cget("foreground").casefold() == LIGHT_PALETTE.primary
        assert str(explicit_ttk.cget("foreground")).casefold() == LIGHT_PALETTE.muted
        assert fixed_canvas.cget("background").casefold() == "#111714"
        assert accent_button.cget("background").casefold() == LIGHT_PALETTE.brand
        assert accent_button.cget("foreground").casefold() == (
            LIGHT_PALETTE.inverse_text
        )
        assert accent_button.cget("activeforeground").casefold() == (
            LIGHT_PALETTE.inverse_text
        )
        assert accent_button.cget("highlightcolor").casefold() == (
            LIGHT_PALETTE.inverse_text
        )
        assert {
            option: fixed_button.cget(option) for option in fixed_colors
        } == fixed_colors
    finally:
        root.destroy()
