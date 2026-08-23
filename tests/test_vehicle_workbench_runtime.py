from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

import allin1_sdk.addon_sdk_ui as sdk_ui
from allin1_sdk.app import _configure_style
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame


VEHICLES = """<CVehicleModelInfo__InitDataList><InitDatas><Item>
<modelName>runtimecar</modelName><txdName>runtimecar</txdName>
<handlingId>RUNTIMEHAND</handlingId><gameName>RUNTIMECAR</gameName>
<vehicleMakeName>RUNTIME</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash>
<layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type>
<vehicleClass>VC_SPORT</vehicleClass>
</Item></InitDatas></CVehicleModelInfo__InitDataList>"""
HANDLING = """<CHandlingDataMgr><HandlingData><Item>
<handlingName>RUNTIMEHAND</handlingName><fMass value="1500.0" />
<nInitialDriveGears value="6"/><fInitialDriveForce value="0.30"/>
<fInitialDriveMaxFlatVel value="160.0"/><fBrakeForce value="0.8"/>
<fSteeringLock value="40.0"/>
</Item></HandlingData></CHandlingDataMgr>"""
VARIATIONS = """<CVehicleModelInfoVariation><variationData><Item>
<modelName>runtimecar</modelName><colors><Item>
<indices content="char_array">0 1 2 3</indices><liveries/>
</Item></colors><kits><Item>321_runtimekit</Item></kits>
<lightSettings value="1"/><sirenSettings value="0"/>
</Item></variationData></CVehicleModelInfoVariation>"""
CARCOLS = """<CVehicleModelInfoVarGlobal><Kits><Item>
<kitName>321_runtimekit</kitName><id value="321"/><kitType>MKT_STANDARD</kitType>
<visibleMods><Item><modelName>runtime_spoiler</modelName>
<modShopLabel>RUNTIME_SPOILER</modShopLabel>
<linkedModels><Item>runtime_orphan</Item></linkedModels>
<type>VMT_SPOILER</type><bone>chassis</bone></Item></visibleMods>
<linkMods/><statMods/><slotNames/><liveryNames/>
</Item></Kits><Lights><Item><id value="1"/><name>runtimecar</name></Item></Lights>
</CVehicleModelInfoVarGlobal>"""
CONTENT = """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item>
<filename>dlc_runtimecar:/common/data/vehicles.meta</filename>
</Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>"""


def _source(root: Path) -> Path:
    source = root / "vehicle-source"
    source.mkdir()
    for name, text in (
        ("vehicles.meta", VEHICLES), ("handling.meta", HANDLING),
        ("carvariations.meta", VARIATIONS), ("carcols.meta", CARCOLS),
        ("content.xml", CONTENT),
    ):
        (source / name).write_text(text, encoding="utf-8")
    stream = source / "stream"
    stream.mkdir()
    for name in (
        "runtimecar.yft", "runtimecar.ytd", "runtime_spoiler.yft",
        "runtime_orphan.yft", "runtime_available.yft",
    ):
        (stream / name).write_bytes(name.encode("ascii"))
    return source


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


def test_sdk_shell_mounts_vehicle_workspace_at_supported_sizes(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        assert dialog._workspace_instances == {}
        assert not hasattr(dialog, "vehicle_workspace")
        dialog._select_workspace("vehicles")
        frame = dialog.vehicle_workspace
        assert frame.winfo_manager() == "pack"
        assert dialog._workspace_instances == {"vehicles": frame}
        dialog._select_workspace("linker")
        dialog._select_workspace("vehicles")
        assert dialog.vehicle_workspace is frame
        for width, height in ((1320, 840), (1020, 680)):
            dialog.geometry(f"{width}x{height}+0+0")
            dialog.update_idletasks()
            page = dialog.workspace_pages["vehicles"]
            assert frame.winfo_width() == page.winfo_width()
            assert frame.winfo_height() == page.winfo_height()
    finally:
        dialog.destroy()


def _widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _widgets(child)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "UI audit: toolbar, inspector tabs, and lower tuning controls still clip "
        "at the supported default/minimum window sizes"
    ),
)
def test_vehicle_workbench_primary_controls_fit_supported_window_sizes(
    tmp_path, monkeypatch, tk_root,
):
    source = _source(tmp_path)
    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "workspace")
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        frame = dialog.vehicle_workspace
        frame.open_source(workspace.source, authoring_workspace=workspace)
        dialog._select_workspace("vehicles")
        inspector = next(
            widget for widget in _widgets(frame)
            if isinstance(widget, ttk.Notebook)
            and "Tuning Builder" in [
                widget.tab(tab, "text") for tab in widget.tabs()
            ]
        )
        labels = [inspector.tab(tab, "text") for tab in inspector.tabs()]
        inspector.select(labels.index("Tuning Builder"))
        frame.tuning_pages.select(frame.tuning_parts_page)
        for width, height in ((1320, 840), (1020, 680)):
            dialog.geometry(f"{width}x{height}+0+0")
            dialog.update_idletasks()
            left = dialog.winfo_rootx()
            top = dialog.winfo_rooty()
            right = left + dialog.winfo_width()
            bottom = top + dialog.winfo_height()
            for control in (
                frame.package_button, frame.zoom_label,
                frame.tuning_add_button, frame.tuning_field_button,
            ):
                control_right = control.winfo_rootx() + control.winfo_width()
                control_bottom = control.winfo_rooty() + control.winfo_height()
                assert control.winfo_ismapped()
                assert left <= control.winfo_rootx() < control_right <= right
                assert top <= control.winfo_rooty() < control_bottom <= bottom
    finally:
        dialog.destroy()


def test_vehicle_authoring_controls_follow_workspace_state_and_route_findings(
    tmp_path, tk_root,
):
    source = _source(tmp_path)
    frame = VehicleWorkbenchFrame(
        tk_root, Path(__file__).resolve().parents[1],
    )
    frame.pack(fill="both", expand=True)
    frame.open_source(source)
    assert all(str(entry.cget("state")) == "disabled"
               for entry in frame.appearance_edit_inputs)
    assert all(str(button.cget("state")) == "disabled"
               for button in frame.appearance_edit_buttons)
    assert str(frame.tuning_primary_entry.cget("state")) == "disabled"

    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "workspace")
    frame.open_source(workspace.source, authoring_workspace=workspace)
    assert all(str(entry.cget("state")) == "normal"
               for entry in frame.appearance_edit_inputs)
    assert all(str(button.cget("state")) == "normal"
               for button in frame.appearance_edit_buttons)
    assert str(frame.tuning_primary_entry.cget("state")) == "normal"

    available = next(
        item_id for item_id, asset in frame._tuning_assets.items()
        if asset.name == "runtime_available"
    )
    frame.tuning_asset_tree.selection_set(available)
    frame.tuning_pages.select(frame.tuning_validation_page)
    frame._use_tuning_asset()
    assert frame.tuning_new_primary.get() == "runtime_available"
    assert frame.tuning_pages.select() == str(frame.tuning_parts_page)

    finding = next(
        item_id for item_id, entry in frame._tuning_findings.items() if entry
    )
    frame.tuning_finding_tree.selection_set(finding)
    frame.tuning_pages.select(frame.tuning_validation_page)
    frame._open_tuning_finding()
    assert frame.tuning_pages.select() == str(frame.tuning_parts_page)
    assert frame.tuning_part_tree.selection() == ("visibleMods:0",)
    frame.destroy()
