from __future__ import annotations

import tkinter as tk
from dataclasses import replace
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


def test_sdk_shell_mounts_unified_workbench_at_supported_sizes(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        assert dialog._workspace_instances == {}
        assert not hasattr(dialog, "workbench_workspace")
        for width, height in ((1320, 840), (1020, 680)):
            dialog.geometry(f"{width}x{height}+0+0")
            dialog.update()
            left = dialog.winfo_rootx()
            top = dialog.winfo_rooty()
            right = left + dialog.winfo_width()
            bottom = top + dialog.winfo_height()
            for control in (
                dialog.version_badge, dialog.support_button,
                *dialog.linker_sections,
            ):
                assert control.winfo_ismapped()
                assert left <= control.winfo_rootx()
                assert control.winfo_rootx() + control.winfo_width() <= right
                assert top <= control.winfo_rooty()
                assert control.winfo_rooty() + control.winfo_height() <= bottom
        dialog._select_workspace("workbench")
        frame = dialog.workbench_workspace
        assert frame.winfo_manager() == "pack"
        assert dialog._workspace_instances == {"workbench": frame}
        dialog._select_workspace("linker")
        dialog._select_workspace("workbench")
        assert dialog.workbench_workspace is frame
        assert dialog.vehicle_workspace is frame.vehicle_workspace
        for width, height in ((1320, 840), (1020, 680)):
            dialog.geometry(f"{width}x{height}+0+0")
            dialog.update()
            page = dialog.workspace_pages["workbench"]
            assert frame.winfo_width() == page.winfo_width()
            assert frame.winfo_height() == page.winfo_height()
    finally:
        dialog.destroy()


def test_workspace_sidebar_toggle_preserves_context_and_expands_workspace(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        dialog.geometry("1100x720+0+0")
        dialog._select_workspace("workbench")
        dialog.update()
        expanded_width = dialog.workspace_host.winfo_width()

        assert dialog.sidebar_visible.get() is True
        assert dialog.workspace_sidebar.winfo_ismapped()
        assert dialog.sidebar_toggle_button.winfo_rootx() >= (
            dialog.workspace_sidebar.winfo_rootx()
            + dialog.workspace_sidebar.winfo_width()
        )
        assert dialog.current_workspace == "workbench"

        assert dialog._toggle_sidebar() == "break"
        dialog.update()
        assert dialog.sidebar_visible.get() is False
        assert not dialog.workspace_sidebar.winfo_ismapped()
        assert dialog.sidebar_toggle_button.winfo_ismapped()
        assert dialog.sidebar_toggle_button.cget("text") == ">"
        assert dialog.workspace_host.winfo_width() > expanded_width
        assert dialog.current_workspace == "workbench"

        assert dialog._toggle_sidebar() == "break"
        dialog.update()
        assert dialog.sidebar_visible.get() is True
        assert dialog.workspace_sidebar.winfo_ismapped()
        assert dialog.sidebar_toggle_button.cget("text") == "<"
        assert dialog.current_workspace == "workbench"
    finally:
        dialog.destroy()


def test_context_return_link_survives_sidebar_collapse_and_cancelled_guard(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        dialog._select_workspace("workbench")
        dialog.update()
        assert dialog.context_back_button.winfo_ismapped()
        assert dialog.context_back_button.cget("text") == "‹ Package Linker"

        dialog._set_sidebar_visible(False)
        dialog.update()
        assert not dialog.workspace_sidebar.winfo_ismapped()
        assert dialog.context_back_button.winfo_ismapped()

        history = list(dialog._navigation_history)
        monkeypatch.setattr(
            dialog.workbench_workspace.vehicle_workspace,
            "confirm_navigation", lambda: False,
        )
        assert dialog._go_back() == "break"
        assert dialog.current_workspace == "workbench"
        assert dialog._navigation_history == history
        assert dialog.context_back_button.cget("text") == "‹ Package Linker"

        monkeypatch.setattr(
            dialog.workbench_workspace.vehicle_workspace,
            "confirm_navigation", lambda: True,
        )
        assert dialog._go_back() == "break"
        dialog.update()
        assert dialog.current_workspace == "linker"
        assert not dialog.context_back_button.winfo_ismapped()
    finally:
        dialog.destroy()


def _widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _widgets(child)


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
        dialog._select_workspace("workbench")
        frame = dialog.vehicle_workspace
        frame.open_source(workspace.source, authoring_workspace=workspace)
        dialog.workbench_workspace.select_category("vehicles")
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
        selected_entry = frame.tuning_part_tree.get_children()[0]
        frame.tuning_part_tree.selection_set(selected_entry)
        frame._select_tuning_builder_entry()
        assert [
            frame.render_mode_menu.entrycget(index, "label")
            for index in range(3)
        ] == ["Shaded", "Materials", "Wireframe"]
        assert frame.render_mode.get() == "Shaded"
        assert [
            frame.model_filter_menu.entrycget(index, "label")
            for index in range(frame.model_filter_menu.index("end") + 1)
        ] == ["Fragment", "LOD", "Component"]
        assert frame.render_mode_menu.entrycget(
            "Render full-quality frame", "label",
        ) == "Render full-quality frame"
        assert str(frame.tuning_entry_actions_button.cget("state")) == "normal"
        assert frame.tuning_entry_action_menu.entrycget(
            "New entry", "state",
        ) == "normal"
        assert frame.tuning_entry_action_menu.entrycget(
            "Copy selected", "state",
        ) == "normal"
        assert frame.tuning_entry_action_menu.entrycget(
            "Delete selected", "state",
        ) == "normal"
        assert frame.tuning_entry_action_menu.entrycget("Move up", "state") == "disabled"
        assert frame.tuning_entry_action_menu.entrycget("Move down", "state") == "disabled"
        for width, height in ((1320, 840), (1020, 680)):
            dialog.geometry(f"{width}x{height}+0+0")
            # A real window resize delivers Configure events before child
            # geometry settles; process those events rather than inspecting
            # the previous 1320px layout after only idle callbacks.
            dialog.update()
            left = dialog.winfo_rootx()
            top = dialog.winfo_rooty()
            right = left + dialog.winfo_width()
            bottom = top + dialog.winfo_height()
            for control in (
                frame.package_button, frame.render_mode_button,
                frame.model_filter_button, frame.camera_menu_button,
                frame.fit_button, frame.zoom_label,
                frame.tuning_entry_actions_button,
            ):
                control_right = control.winfo_rootx() + control.winfo_width()
                control_bottom = control.winfo_rooty() + control.winfo_height()
                assert control.winfo_ismapped(), f"Primary control clipped: {control}"
                assert left <= control.winfo_rootx() < control_right <= right
                assert top <= control.winfo_rooty() < control_bottom <= bottom
            for page, control in (
                (frame.tuning_create_page, frame.tuning_add_button),
                (frame.tuning_fields_page, frame.tuning_field_button),
            ):
                frame.tuning_editor_tabs.select(page)
                dialog.update_idletasks()
                control_right = control.winfo_rootx() + control.winfo_width()
                control_bottom = control.winfo_rooty() + control.winfo_height()
                assert control.winfo_ismapped(), (
                    f"{frame.tuning_editor_tabs.tab(page, 'text')} editor is clipped "
                    f"at {width}x{height}: {control}; tabs="
                    f"{frame.tuning_editor_tabs.winfo_width()}x"
                    f"{frame.tuning_editor_tabs.winfo_height()}, page="
                    f"{page.winfo_width()}x{page.winfo_height()}, "
                    f"requested={page.winfo_reqwidth()}x{page.winfo_reqheight()}"
                    f", selected={frame.tuning_editor_tabs.select()}, control="
                    f"{control.winfo_geometry()}, parent="
                    f"{control.master.winfo_geometry()}/mapped"
                    f"{control.master.winfo_ismapped()}, page-mapped="
                    f"{page.winfo_ismapped()}, tabs-mapped="
                    f"{frame.tuning_editor_tabs.winfo_ismapped()}, split-mapped="
                    f"{frame.tuning_parts_split.winfo_ismapped()}, inspector="
                    f"{inspector.select()}/{inspector.winfo_geometry()}/"
                    f"mapped{inspector.winfo_ismapped()}, builder="
                    f"{frame.tuning_builder_tab.winfo_geometry()}/mapped"
                    f"{frame.tuning_builder_tab.winfo_ismapped()}, pages="
                    f"{frame.tuning_pages.winfo_geometry()}/mapped"
                    f"{frame.tuning_pages.winfo_ismapped()}, primary="
                    f"{frame.primary_panes.winfo_geometry()}, frame="
                    f"{frame.winfo_geometry()}, workbench="
                    f"{dialog.workbench_workspace.winfo_geometry()}"
                )
                assert left <= control.winfo_rootx() < control_right <= right, (
                    f"{frame.tuning_editor_tabs.tab(page, 'text')} is outside "
                    f"{width}x{height}: {control.winfo_rootx()}..{control_right}, "
                    f"window={left}..{right}; primary="
                    f"{frame.primary_panes.winfo_geometry()}@"
                    f"{frame.primary_panes.winfo_rootx()}, outer="
                    f"{frame.primary_panes.master.winfo_geometry()}@"
                    f"{frame.primary_panes.master.winfo_rootx()}, frame="
                    f"{frame.winfo_geometry()}@{frame.winfo_rootx()}, page="
                    f"{frame.master.winfo_geometry()}@{frame.master.winfo_rootx()}, "
                    f"notebook={frame.master.master.winfo_geometry()}@"
                    f"{frame.master.master.winfo_rootx()}/req"
                    f"{frame.master.master.winfo_reqwidth()}, workbench="
                    f"{frame.master.master.master.master.winfo_geometry()}@"
                    f"{frame.master.master.master.master.winfo_rootx()}/req"
                    f"{frame.master.master.master.master.winfo_reqwidth()}, host="
                    f"{frame.master.master.master.master.master.master.winfo_geometry()}@"
                    f"{frame.master.master.master.master.master.master.winfo_rootx()}, "
                    f"content={frame.master.master.master.master.master.master.master.winfo_geometry()}@"
                    f"{frame.master.master.master.master.master.master.master.winfo_rootx()}, "
                    f"outer={frame.master.master.master.master.master.master.master.master.master.winfo_geometry()}@"
                    f"{frame.master.master.master.master.master.master.master.master.master.winfo_rootx()}, tuning="
                    f"{frame.tuning_parts_split.winfo_geometry()}@"
                    f"{frame.tuning_parts_split.winfo_rootx()}, editor="
                    f"{frame.tuning_editor_tabs.winfo_geometry()}@"
                    f"{frame.tuning_editor_tabs.winfo_rootx()}"
                )
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
    assert str(frame.save_distribution_button.cget("state")) == "disabled"

    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "workspace")
    frame.open_source(workspace.source, authoring_workspace=workspace)
    assert all(str(entry.cget("state")) == "normal"
               for entry in frame.appearance_edit_inputs)
    assert all(str(button.cget("state")) == "normal"
               for button in frame.appearance_edit_buttons)
    assert str(frame.tuning_primary_entry.cget("state")) == "normal"
    assert str(frame.save_distribution_button.cget("state")) == "normal"
    assert frame.distribution_values["listed"].get() is True
    assert frame.distribution_values["traffic_enabled"].get() is False
    assert frame.distribution_values["category"].get() == "sports"

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


def test_vehicle_workbench_warns_before_discarding_unapplied_form_edits(
    tmp_path, monkeypatch, tk_root,
):
    source = _source(tmp_path)
    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "workspace")
    frame = VehicleWorkbenchFrame(
        tk_root, Path(__file__).resolve().parents[1],
    )
    frame.pack(fill="both", expand=True)
    frame.open_source(workspace.source, authoring_workspace=workspace)
    frame.authoring_values["vehicle.gameName"].set("UNAPPLIED_LABEL")

    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "allin1_sdk.vehicle_workbench.messagebox.askyesno",
        lambda title, message, **_kwargs: prompts.append((title, message)) or False,
    )
    assert frame.selected_model is not None
    original = frame.selected_model
    routed = replace(original, model="runtimecar_second")
    frame.models["model:routed"] = routed
    frame.model_tree.insert(
        "", "end", iid="model:routed", text=routed.model,
        values=("Complete",),
    )
    assert not frame.select_model("runtimecar_second")
    assert frame.selected_model is original
    assert not frame.confirm_navigation()
    assert prompts and prompts[0][0] == "Discard unsaved vehicle edits?"
    frame.destroy()


def test_vehicle_empty_package_clears_prior_inspector_and_asset_actions(
    tmp_path, tk_root,
):
    source = _source(tmp_path)
    empty = tmp_path / "empty-vehicle-package"
    empty.mkdir()
    frame = VehicleWorkbenchFrame(
        tk_root, Path(__file__).resolve().parents[1],
        on_open_asset=lambda _path: None,
    )
    frame.pack(fill="both", expand=True)
    frame.open_source(source)
    assert frame.selected_model is not None
    assert frame.project_assets
    assert frame.asset_tree.get_children()

    frame.open_source(empty)

    assert frame.selected_model is None
    assert not frame.project_assets
    assert not frame.asset_tree.get_children()
    assert str(frame.open_asset_button.cget("state")) == "disabled"
    assert str(frame.open_texture_button.cget("state")) == "disabled"
    assert frame.details.get("1.0", "end-1c") == (
        "No vehicles.meta records were found in this package."
    )
    assert frame.authoring_status.get() == (
        "Select a vehicle before editing package metadata."
    )
    assert frame.asset_tree.bind("<Return>")
    assert frame.tuning_asset_tree.bind("<Return>")
    frame.destroy()
