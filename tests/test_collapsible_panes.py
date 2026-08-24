from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

from allin1_sdk.addon_sdk_ui import AddonSdkDialog
from allin1_sdk.app import _configure_style
from allin1_sdk.collapsible_panes import CollapsibleSidePanes, DIVIDER_WIDTH
from allin1_sdk.ped_workbench import PedWorkbenchFrame
from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame
from allin1_sdk.weapon_workbench import WeaponWorkbenchFrame


@pytest.fixture
def visible_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    _configure_style(root)
    root.geometry("1220x720+10+10")
    root.update()
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def _settle(root: tk.Tk) -> None:
    root.update_idletasks()
    root.update()
    root.update_idletasks()


def test_reusable_side_panes_keep_order_arrows_focus_and_restore_widths(visible_root):
    paned = ttk.Panedwindow(visible_root, orient="horizontal")
    paned.pack(fill="both", expand=True)
    sides = CollapsibleSidePanes(
        paned, left_width=260, center_width=560, right_width=300,
        left_label="Packages", right_label="Field inspector",
    )
    left = ttk.Frame(sides.left_host)
    center = ttk.Frame(sides.center_host)
    right = ttk.Frame(sides.right_host)
    focus_entry = ttk.Entry(left)
    focus_entry.pack()
    sides.set_contents(left, center, right)
    _settle(visible_root)
    paned.sashpos(0, 275)
    paned.sashpos(1, 900)
    _settle(visible_root)
    sides.remember_expanded_widths()
    order = sides.pane_order()
    left_width = sides.left_host.winfo_width()
    right_width = sides.right_host.winfo_width()
    center_width = sides.center_host.winfo_width()

    assert order == tuple(str(item) for item in (
        sides.left_host, sides.center_host, sides.right_host,
    ))
    assert sides.left_toggle.cget("text") == "<"
    assert sides.right_toggle.cget("text") == ">"
    assert str(sides.left_toggle.cget("takefocus")) == "1"
    assert sides.left_toggle.accessible_name == "Collapse Packages pane"
    assert sides.right_toggle.accessible_name == "Collapse Field inspector pane"
    assert sides.left_divider.winfo_width() == DIVIDER_WIDTH
    assert sides.left_toggle.winfo_height() == 30

    focus_entry.focus_force()
    _settle(visible_root)
    assert visible_root.focus_get() is focus_entry
    sides.left_toggle.invoke()
    _settle(visible_root)
    assert visible_root.focus_get() is sides.left_toggle
    assert sides.left_collapsed
    assert sides.left_toggle.cget("text") == ">"
    assert sides.left_toggle.accessible_name == "Expand Packages pane"
    assert not left.winfo_ismapped()
    assert sides.left_toggle.winfo_ismapped()
    assert sides.pane_order() == order
    assert sides.center_host.winfo_width() > center_width

    center_after_left = sides.center_host.winfo_width()
    sides.right_toggle.invoke()
    _settle(visible_root)
    assert sides.right_collapsed
    assert sides.right_toggle.cget("text") == "<"
    assert not right.winfo_ismapped()
    assert sides.right_toggle.winfo_ismapped()
    assert sides.center_host.winfo_width() > center_after_left
    for width in (980, 1100, 1320):
        visible_root.geometry(f"{width}x720+10+10")
        _settle(visible_root)
        sides.enforce_layout()
        _settle(visible_root)
        pane_left = paned.winfo_rootx()
        pane_right = pane_left + paned.winfo_width()
        for host, divider, button in (
            (sides.left_host, sides.left_divider, sides.left_toggle),
            (sides.right_host, sides.right_divider, sides.right_toggle),
        ):
            assert host.winfo_width() >= DIVIDER_WIDTH
            assert divider.winfo_width() == DIVIDER_WIDTH
            assert button.winfo_width() == DIVIDER_WIDTH
            assert pane_left <= button.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() <= pane_right
    # A dragged collapsed sash is returned to its 16px boundary.
    paned.sashpos(0, 180)
    sides._sash_released()
    _settle(visible_root)
    assert paned.sashpos(0) <= DIVIDER_WIDTH + 2

    sides.left_toggle.invoke()
    sides.right_toggle.invoke()
    _settle(visible_root)
    assert left.winfo_ismapped() and right.winfo_ismapped()
    assert sides.pane_order() == order
    assert abs(sides.left_host.winfo_width() - left_width) <= 8
    assert abs(sides.right_host.winfo_width() - right_width) <= 8


@pytest.mark.parametrize(
    ("kind", "left_label", "right_label"),
    (
        ("vehicle", "Vehicles", "Resolved project"),
        ("weapon", "Weapons", "Integration"),
        ("ped", "Peds", "Integration"),
    ),
)
def test_primary_workbenches_expand_center_and_restore_each_side(
    visible_root, tmp_path: Path, kind: str, left_label: str, right_label: str,
) -> None:
    if kind == "vehicle":
        frame = VehicleWorkbenchFrame(visible_root, tmp_path)
    elif kind == "weapon":
        frame = WeaponWorkbenchFrame(visible_root)
    else:
        frame = PedWorkbenchFrame(visible_root)
    frame.pack(fill="both", expand=True)
    _settle(visible_root)
    sides = frame.primary_side_panes
    sides.remember_expanded_widths()
    initial_left = sides.left_host.winfo_width()
    initial_right = sides.right_host.winfo_width()
    initial_center = sides.center_host.winfo_width()
    order = sides.pane_order()

    sides.toggle_left()
    _settle(visible_root)
    after_left = sides.center_host.winfo_width()
    assert after_left > initial_center
    assert sides.left_toggle.accessible_name == f"Expand {left_label} pane"
    sides.toggle_right()
    _settle(visible_root)
    assert sides.center_host.winfo_width() > after_left
    assert sides.right_toggle.accessible_name == f"Expand {right_label} pane"
    assert sides.pane_order() == order
    assert sides.left_toggle.winfo_ismapped()
    assert sides.right_toggle.winfo_ismapped()

    sides.toggle_left()
    sides.toggle_right()
    _settle(visible_root)
    assert sides.pane_order() == order
    assert abs(sides.left_host.winfo_width() - initial_left) <= 12
    assert abs(sides.right_host.winfo_width() - initial_right) <= 12
    frame.destroy()


def test_package_linker_keeps_sections_and_state_while_sides_collapse(
    visible_root, tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        AddonSdkDialog, "_load_examples", lambda self: None,
    )
    dialog = AddonSdkDialog(visible_root, tmp_path)
    dialog.geometry("1220x720+10+10")
    _settle(visible_root)
    sides = dialog.linker_side_panes
    sections = dialog.linker_sections
    order = sides.pane_order()
    initial_center = sides.center_host.winfo_width()
    dialog.heading.set("Retained selection")

    assert sections == (
        sides.left_content, sides.center_content, sides.right_content,
    )
    sides.toggle_left()
    sides.toggle_right()
    _settle(visible_root)
    assert sides.center_host.winfo_width() > initial_center
    assert dialog.heading.get() == "Retained selection"
    assert dialog.linker_sections == sections
    assert sides.pane_order() == order
    assert sides.left_toggle.accessible_name == "Expand Packages pane"
    assert sides.right_toggle.accessible_name == "Expand Field inspector pane"

    sides.toggle_left()
    sides.toggle_right()
    _settle(visible_root)
    assert all(section.winfo_ismapped() for section in sections)
    assert dialog.linker_sections == sections
    dialog.destroy()
