from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from allin1_sdk.app import _configure_style
from allin1_sdk.asset_viewer import AssetViewerDialog
from allin1_sdk.workbench import WorkbenchFrame


WEAPON_META = """<CWeaponInfoBlob><Infos>
<Item><Name>WEAPON_WORKBENCH</Name><Slot>SLOT_WORKBENCH</Slot>
<AmmoInfo ref="AMMO_WORKBENCH"/><Model>w_pi_workbench</Model>
<HumanNameHash>WT_WORK</HumanNameHash><StatName>WORKBENCH</StatName>
<AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components><Item>
<Name>COMPONENT_WORKBENCH_CLIP</Name><Default value="true"/>
</Item></Components></Item></AttachPoints></Item>
<Item><Name>AMMO_WORKBENCH</Name><Model>w_pi_workbench</Model>
<AmmoMax value="60"/><Explosion>NONE</Explosion><TrailFx/><PrimedFx/></Item>
</Infos></CWeaponInfoBlob>"""
COMPONENT_META = """<CWeaponComponentInfoBlob><Infos>
<Item type="CWeaponComponentClipInfo"><Name>COMPONENT_WORKBENCH_CLIP</Name>
<Model>w_at_workbench_clip</Model><LocName>WCT_CLIP1</LocName>
<AttachBone>WAPClip</AttachBone></Item></Infos></CWeaponComponentInfoBlob>"""
PED_META = """<CPedModelInfo__InitDataList><InitDatas><Item>
<Name>ig_workbench</Name><Pedtype>CIVMALE</Pedtype><ModelType>STANDARD</ModelType>
<PropsName>ig_workbench_p</PropsName><MovementClipSet>move_m@generic</MovementClipSet>
<ExpressionSetName>expr_set_ambient_male</ExpressionSetName>
</Item></InitDatas></CPedModelInfo__InitDataList>"""


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


def _package(root: Path) -> Path:
    package = root / "mixed-workbench-package"
    package.mkdir()
    (package / "weapons.meta").write_text(WEAPON_META, encoding="utf-8")
    (package / "weaponcomponents.meta").write_text(COMPONENT_META, encoding="utf-8")
    (package / "weaponanimations.meta").write_text(
        '<WeaponAnimations><Item key="WEAPON_WORKBENCH"/></WeaponAnimations>',
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        '<Shop><Item><nameHash>WEAPON_WORKBENCH</nameHash></Item></Shop>',
        encoding="utf-8",
    )
    (package / "peds.meta").write_text(PED_META, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    for name in (
        "w_pi_workbench.ydr", "w_at_workbench_clip.ydr",
        "ig_workbench.ydd", "ig_workbench.ytd",
    ):
        (stream / name).write_bytes(name.encode("ascii"))
    return package


def test_workbench_shares_one_scan_across_weapon_and_ped_tabs(tmp_path, tk_root):
    package = _package(tmp_path)
    routed: list[str] = []
    frame = WorkbenchFrame(
        tk_root, Path(__file__).resolve().parents[1],
        on_open_asset=routed.append,
    )
    frame.pack(fill="both", expand=True)
    assert frame.open_source(package)
    assert frame.scan is frame.vehicle_workspace.scan
    assert frame.scan is frame.weapon_workspace.scan
    assert frame.scan is frame.ped_workspace.scan
    assert frame.current_category() == "weapons"
    assert [frame.tabs.tab(page, "text") for page in frame.tabs.tabs()] == [
        "Vehicles (0)", "Weapons (1)", "Peds (1)",
    ]
    assert frame.select_weapon("WEAPON_WORKBENCH")
    assert frame.weapon_workspace.selected_weapon is not None
    assert len(frame.weapon_workspace.component_tree.get_children()) == 1
    assert frame.select_ped("ig_workbench")
    assert frame.current_category() == "peds"
    assert frame.ped_workspace.selected_ped is not None
    frame._route_asset("stream/ig_workbench.ytd")
    assert routed == ["stream/ig_workbench.ytd"]
    frame.destroy()


def test_specialist_filters_clear_stale_selection_and_keep_asset_accessible(
    tmp_path, tk_root,
):
    package = _package(tmp_path)
    routed: list[str] = []
    frame = WorkbenchFrame(
        tk_root, Path(__file__).resolve().parents[1], on_open_asset=routed.append,
    )
    frame.pack(fill="both", expand=True)
    assert frame.open_source(package)

    weapon = frame.weapon_workspace
    assert weapon.selected_weapon is not None
    assert weapon.asset_tree.get_children()
    weapon.search.set("definitely-not-a-weapon")
    assert weapon.selected_weapon is None
    assert weapon.heading.get() == "No weapon selected"
    assert not weapon.asset_tree.get_children()
    assert str(weapon.asset_button.cget("state")) == "disabled"
    assert "No weapons match" in weapon.summary.get()
    assert weapon._clear_search() == "break"
    assert frame.select_weapon("WEAPON_WORKBENCH")
    weapon_asset = weapon.asset_tree.get_children()[0]
    weapon.asset_tree.selection_set(weapon_asset)
    assert weapon._open_selected_asset(object()) == "break"

    ped = frame.ped_workspace
    assert frame.select_ped("ig_workbench")
    assert ped.selected_ped is not None
    ped.search.set("definitely-not-a-ped")
    assert ped.selected_ped is None
    assert ped.heading.get() == "No ped selected"
    assert not ped.asset_tree.get_children()
    assert str(ped.asset_button.cget("state")) == "disabled"
    assert "No peds match" in ped.summary.get()
    assert ped._clear_search() == "break"
    assert frame.select_ped("ig_workbench")
    ped_asset = ped.asset_tree.get_children()[0]
    ped.asset_tree.selection_set(ped_asset)
    assert ped._open_selected_asset(object()) == "break"

    assert routed
    for workspace, prefix, scrollbars in (
        (
            weapon, "Weapon",
            (
                weapon.catalog_xscroll, weapon.field_xscroll,
                weapon.component_xscroll, weapon.asset_xscroll,
                weapon.readiness_xscroll, weapon.finding_xscroll,
            ),
        ),
        (
            ped, "Ped",
            (
                ped.catalog_xscroll, ped.field_xscroll, ped.asset_xscroll,
                ped.readiness_xscroll, ped.finding_xscroll,
            ),
        ),
    ):
        assert all(
            str(scrollbar.cget("orient")) == "horizontal"
            for scrollbar in scrollbars
        )
        assert workspace.asset_tree.bind("<Double-1>")
        assert workspace.asset_tree.bind("<Return>")
        assert any(
            tag.startswith(f"{prefix}WorkbenchFilter:")
            for tag in workspace.asset_tree.bindtags()
        )
    frame.destroy()


def test_asset_viewer_filter_clears_hidden_selection_and_exposes_shortcuts(
    tmp_path, tk_root,
):
    package = _package(tmp_path)
    viewer = AssetViewerDialog(tk_root, package, embedded=True)
    viewer.pack(fill="both", expand=True)
    asset_id = next(iter(viewer.entries))
    viewer.tree.selection_set(asset_id)
    viewer._select_asset()
    assert viewer.selected_entry is not None

    viewer.search.set("definitely-not-an-asset")
    assert viewer.selected_entry is None
    assert not viewer.entries
    assert viewer.asset_title.get() == "No asset selected"
    assert "No package assets match" in viewer.asset_meta.get()
    assert str(viewer.export_native_button.cget("state")) == "disabled"
    assert str(viewer.asset_xscroll.cget("orient")) == "horizontal"
    assert viewer.tree.bind("<Return>")
    assert any(
        tag.startswith("AssetViewerFilter:") for tag in viewer.tree.bindtags()
    )
    assert viewer._clear_search() == "break"
    assert viewer.entries
    viewer.destroy()
