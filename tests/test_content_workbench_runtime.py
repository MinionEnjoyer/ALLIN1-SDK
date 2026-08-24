from __future__ import annotations

import time
import tkinter as tk
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from allin1_sdk.app import _configure_style
from allin1_sdk.addon_importer import AddonPackageInspector
from allin1_sdk.asset_viewer import AssetViewerDialog
from allin1_sdk.ped_authoring import PedAuthoringWorkspace
from allin1_sdk.ped_workbench import PedWorkbenchFrame
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from allin1_sdk.weapon_workbench import (
    AMMO_MODE_CLONE,
    AMMO_MODE_REUSE,
    WeaponWorkbenchFrame,
)
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
<ClipDictionaryName>move_m@generic</ClipDictionaryName>
<CreatureMetadataName>METADATA_HUMAN_MALE</CreatureMetadataName>
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
        "ig_workbench_p.ydd", "ig_workbench_p.ytd",
        "ig_clone.ydd", "ig_clone.ytd",
        "ig_clone_p.ydd", "ig_clone_p.ytd",
    ):
        (stream / name).write_bytes(name.encode("ascii"))
    return package


def _png_bytes(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGBA", (12, 8), color).save(output, format="PNG")
    return output.getvalue()


def _wait_for(tk_root, predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tk_root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for asynchronous UI state")


def _destroy_with_ped_preview(
    frame: PedWorkbenchFrame | WorkbenchFrame,
) -> None:
    """Drain preview work before Tk variables can be finalized off-thread."""
    ped_frame = (
        frame.ped_workspace if isinstance(frame, WorkbenchFrame) else frame
    )
    ped_frame._preview_worker.close(wait=True)
    frame.destroy()


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
    assert frame.scan.weapon_animation_records[0].source == (
        "weaponanimations.meta"
    )
    assert frame.scan.weapon_shop_records[0].source == "weapon_shop.meta"
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
    _destroy_with_ped_preview(frame)


def test_ped_authoring_controls_apply_only_inside_copied_workspace(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    original = (package / "peds.meta").read_bytes()
    frame = PedWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    frame.open_source(package, AddonPackageInspector().inspect(package))
    assert all(
        str(entry.cget("state")) == "disabled"
        for entry in frame.authoring_inputs.values()
    )
    assert str(frame.author_button.cget("state")) == "normal"

    workspace = PedAuthoringWorkspace.create(
        package, tmp_path / "ped-authoring",
    )
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )
    assert all(
        str(entry.cget("state")) == "normal"
        for entry in frame.authoring_inputs.values()
    )
    assert str(frame.author_button.cget("state")) == "disabled"

    monkeypatch.setattr(
        "allin1_sdk.ped_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    frame.authoring_values["ped.expressionSet"].set("expr_set_workbench")
    frame._save_authoring_fields()
    assert workspace.revision == 1
    assert workspace.values("ig_workbench").values["ped.expressionSet"] == (
        "expr_set_workbench"
    )
    assert (package / "peds.meta").read_bytes() == original

    frame.authoring_values["ped.expressionSet"].set("expr_not_applied")
    frame.search.set("definitely-not-this-ped")
    assert frame.selected_ped is not None
    assert frame.selected_ped.name == "ig_workbench"
    assert frame.authoring_values["ped.expressionSet"].get() == "expr_not_applied"
    monkeypatch.setattr(
        "allin1_sdk.ped_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: False,
    )
    assert not frame.confirm_navigation()
    _destroy_with_ped_preview(frame)


def test_ped_workbench_reviews_clone_and_migrates_identity(tmp_path, monkeypatch, tk_root):
    package = _package(tmp_path)
    workspace = PedAuthoringWorkspace.create(
        package, tmp_path / "ped-authoring",
    )
    frame = PedWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )
    monkeypatch.setattr(
        "allin1_sdk.ped_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )

    frame.clone_name.set("ig_clone")
    frame._review_clone()
    assert frame._reviewed_clone_plan is not None
    assert frame._reviewed_clone_plan.ready
    assert str(frame.apply_clone_button.cget("state")) == "normal"
    frame._apply_clone()
    assert workspace.revision == 1
    assert workspace.values("ig_clone").ped == "ig_clone"

    assert frame.select_ped("ig_workbench")
    frame.migrate_name.set("ig_migrated")
    frame.migrate_props.set("")
    frame._migrate_identity()
    assert workspace.revision == 2
    assert workspace.values("ig_migrated").values["ped.propsName"] \
        == "ig_migrated_p"
    assert (workspace.source / "stream" / "ig_migrated.ydd").is_file()
    assert (workspace.source / "stream" / "ig_migrated_p.ytd").is_file()
    _destroy_with_ped_preview(frame)


def test_ped_preview_worker_decodes_frames_and_surfaces_background_errors(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    model_png = _png_bytes("#2b8a57")
    texture_png = _png_bytes("#d7b348")
    monkeypatch.setattr(
        PedWorkbenchFrame, "_render_preview_bundle",
        lambda _self, *_args: (model_png, texture_png, "preview ready"),
    )
    frame = PedWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    frame.open_source(package, AddonPackageInspector().inspect(package))
    _wait_for(tk_root, lambda: frame.preview_status.get() == "preview ready")
    model, texture = frame._preview_source_images
    assert model is not None and model.size == (12, 8)
    assert texture is not None and texture.size == (12, 8)
    assert str(frame.refresh_preview_button.cget("state")) == "normal"

    def fail(*_args):
        raise RuntimeError("forced preview backend failure")

    frame._preview_worker.invalidate(clear_cache=True)
    monkeypatch.setattr(frame, "_render_preview_bundle", fail)
    frame._request_preview()
    _wait_for(
        tk_root,
        lambda: "forced preview backend failure" in frame.preview_status.get(),
    )
    assert frame._preview_source_images == (None, None)
    _destroy_with_ped_preview(frame)


def test_ped_preview_and_template_plan_explain_missing_exact_assets(
    tmp_path, tk_root,
):
    package = _package(tmp_path)
    (package / "stream" / "ig_workbench.ydd").unlink()
    (package / "stream" / "ig_workbench.ytd").unlink()
    workspace = PedAuthoringWorkspace.create(
        package, tmp_path / "ped-authoring",
    )
    (workspace.source / "stream" / "ig_clone.ytd").unlink()
    frame = PedWorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )
    assert str(frame.refresh_preview_button.cget("state")) == "disabled"
    assert "external or missing" in frame.preview_status.get()

    frame.clone_name.set("ig_clone")
    frame._review_clone()
    assert frame._reviewed_clone_plan is not None
    assert frame._reviewed_clone_plan.ready is False
    assert str(frame.apply_clone_button.cget("state")) == "disabled"
    assert "target_model_texture_not_unique" in frame.clone_status.get()
    _destroy_with_ped_preview(frame)


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


def test_weapon_authoring_controls_apply_only_inside_copied_workspace(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    frame = WeaponWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    original_scan = AddonPackageInspector().inspect(package)
    frame.open_source(package, original_scan)
    assert all(
        str(entry.cget("state")) == "disabled"
        for entry in frame.authoring_inputs.values()
    )
    assert str(frame.save_author_button.cget("state")) == "disabled"

    workspace = WeaponAuthoringWorkspace.create(
        package, tmp_path / "weapon-authoring",
    )
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )
    assert all(
        str(entry.cget("state")) == "normal"
        for entry in frame.authoring_inputs.values()
    )
    assert str(frame.attachment_bone_entry.cget("state")) == "disabled"
    assert str(frame.component_inputs["component.type"].cget("state")) == "disabled"
    assert str(frame.attachment_default_check.cget("state")) == "normal"

    frame.authoring_values["weapon.slot"].set("SLOT_WORKBENCH_EDITED")
    frame._save_authoring_fields()
    assert workspace.values("WEAPON_WORKBENCH").values["weapon.slot"] == (
        "SLOT_WORKBENCH_EDITED"
    )
    assert AddonPackageInspector().inspect(package).weapons[0].slot == "SLOT_WORKBENCH"

    frame.component_values["component.locName"].set("WCT_WORKBENCH_EDITED")
    frame._save_component_fields()
    assert workspace.component_values("COMPONENT_WORKBENCH_CLIP").values[
        "component.locName"
    ] == "WCT_WORKBENCH_EDITED"

    frame.attachment_default.set(False)
    frame._save_attachment_fields()
    authored_scan = AddonPackageInspector().inspect(workspace.source)
    assert authored_scan.weapon_component_links[0].default is False
    assert workspace.revision == 3

    frame.authoring_values["weapon.slot"].set("SLOT_NOT_APPLIED")
    frame.component_values["component.locName"].set("WCT_NOT_APPLIED")
    frame.attachment_default.set(True)
    frame.search.set("definitely-not-this-weapon")
    assert frame.selected_weapon is not None
    assert frame.authoring_values["weapon.slot"].get() == "SLOT_NOT_APPLIED"
    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    assert frame.confirm_navigation()
    assert frame.authoring_values["weapon.slot"].get() == "SLOT_WORKBENCH_EDITED"
    assert frame.component_values["component.locName"].get() == (
        "WCT_WORKBENCH_EDITED"
    )
    assert frame.attachment_default.get() is False
    frame.destroy()


def test_weapon_authoring_prompts_for_dirty_navigation_and_shared_ammo(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    weapons_path = package / "weapons.meta"
    shared = (
        "<Item><Name>WEAPON_WORKBENCH_SECOND</Name>"
        "<Slot>SLOT_WORKBENCH_SECOND</Slot>"
        '<AmmoInfo ref="AMMO_WORKBENCH"/><Model>w_pi_workbench</Model>'
        "<HumanNameHash>WT_WORK2</HumanNameHash><StatName>WORKBENCH2</StatName>"
        "</Item>"
    )
    weapons_path.write_text(
        weapons_path.read_text(encoding="utf-8").replace(
            "</Infos></CWeaponInfoBlob>", f"{shared}</Infos></CWeaponInfoBlob>",
        ).replace(
            '<AmmoMax value="60"/>',
            '<AmmoMax value="60"/><AmmoMax50 value="45"/>',
        ),
        encoding="utf-8",
    )
    workspace = WeaponAuthoringWorkspace.create(
        package, tmp_path / "shared-weapon-authoring",
    )
    frame = WeaponWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )
    frame.authoring_values["ammo.ammoMax"].set("90")

    prompts: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda title, message, **_kwargs: prompts.append((title, message)) or False,
    )
    assert not frame.select_weapon("WEAPON_WORKBENCH_SECOND")
    assert frame.selected_weapon is not None
    assert frame.selected_weapon.name == "WEAPON_WORKBENCH"
    assert prompts[-1][0] == "Discard unsaved weapon edits?"
    assert not frame.confirm_navigation()
    assert prompts[-1][0] == "Discard unsaved weapon edits?"
    frame._save_authoring_fields()
    assert not errors
    assert workspace.revision == 0
    assert prompts[-1][0] == "Edit shared ammo definition?"
    assert "WEAPON_WORKBENCH_SECOND" in prompts[-1][1]

    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    frame._save_authoring_fields()
    assert not errors
    assert workspace.revision == 1
    assert workspace.values("WEAPON_WORKBENCH_SECOND").values["ammo.ammoMax"] == "90"
    assert frame.select_weapon("WEAPON_WORKBENCH_SECOND")
    assert frame.selected_weapon is not None
    assert frame.selected_weapon.name == "WEAPON_WORKBENCH_SECOND"
    frame.destroy()


def test_weapon_integration_tab_clones_animation_and_edits_existing_shop_fields(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    weapons_path = package / "weapons.meta"
    target = (
        "<Item><Name>WEAPON_WORKBENCH_TARGET</Name>"
        "<Slot>SLOT_WORKBENCH_TARGET</Slot>"
        '<AmmoInfo ref="AMMO_WORKBENCH"/><Model>w_pi_workbench</Model>'
        "<HumanNameHash>WT_WORK_TARGET</HumanNameHash>"
        "<StatName>WORKBENCH_TARGET</StatName></Item>"
    )
    weapons_path.write_text(
        weapons_path.read_text(encoding="utf-8").replace(
            "</Infos></CWeaponInfoBlob>", f"{target}</Infos></CWeaponInfoBlob>",
        ),
        encoding="utf-8",
    )
    (package / "weaponanimations.meta").write_text(
        "<CWeaponAnimationsSets><Sets>"
        '<Item key="PISTOL"><WeaponAnimations>'
        '<Item key="WEAPON_WORKBENCH"><Clip ref="pistol"/></Item>'
        '<Item key="WEAPON_STOCK_ONLY"><Clip ref="stock"/></Item>'
        "</WeaponAnimations></Item>"
        "<Item><Name>THROW</Name><WeaponAnimations>"
        '<Item key="WEAPON_WORKBENCH"><Clip ref="throw"/></Item>'
        "</WeaponAnimations></Item>"
        "</Sets></CWeaponAnimationsSets>",
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<WeaponShopItemArray><weaponShopItems><Item>"
        "<nameHash>WEAPON_WORKBENCH</nameHash>"
        '<cost value="750"/><ammoCost ref="150"/>'
        "<textLabel>WT_WORK</textLabel>"
        '<weaponDesc value="WTD_WORK"/><weaponTT ref="WTT_WORK"/>'
        "<weaponUppercase>WTU_WORK</weaponUppercase>"
        '<availableInSP value="false"/>'
        "</Item></weaponShopItems></WeaponShopItemArray>",
        encoding="utf-8",
    )
    workspace = WeaponAuthoringWorkspace.create(
        package, tmp_path / "integration-authoring",
    )
    frame = WeaponWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )

    assert "Integration" in [
        frame.project_tabs.tab(page, "text")
        for page in frame.project_tabs.tabs()
    ]
    assert frame.select_weapon("WEAPON_WORKBENCH")
    assert "2 mapping record(s)" in frame.animation_summary.get()
    assert "2 set(s)" in frame.animation_summary.get()
    assert "weaponanimations.meta" in frame.animation_summary.get()
    assert str(frame.clone_animation_button.cget("state")) == "disabled"
    assert all(
        str(entry.cget("state")) == "normal"
        for entry in frame.shop_authoring_inputs.values()
    )
    assert "weapon_shop.meta" in frame.shop_summary.get()

    frame.shop_authoring_values["shop.cost"].set("900")
    assert frame._editor_snapshot() != frame._loaded_editor_snapshot
    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: False,
    )
    assert not frame.confirm_navigation()
    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    assert frame.confirm_navigation()
    assert frame.shop_authoring_values["shop.cost"].get() == "750"
    frame.shop_authoring_values["shop.cost"].set("900")
    frame._save_shop_fields()
    assert workspace.shop_values("WEAPON_WORKBENCH").values["shop.cost"] == "900"
    assert workspace.revision == 1

    assert frame.select_weapon("WEAPON_WORKBENCH_TARGET")
    assert frame.animation_template.get() == "WEAPON_WORKBENCH"
    assert "WEAPON_STOCK_ONLY" in frame.animation_template_combo.cget("values")
    assert "WEAPON_STOCK_ONLY" not in {
        item.name for item in frame.scan.weapons
    }
    assert str(frame.clone_animation_button.cget("state")) == "normal"
    frame._clone_animation_mappings()
    authored = workspace.animation_values("WEAPON_WORKBENCH_TARGET")
    assert len(authored.records) == 2
    assert authored.set_names == ("PISTOL", "THROW")
    assert workspace.revision == 2
    assert str(frame.clone_animation_button.cget("state")) == "disabled"
    frame.destroy()


def test_weapon_template_builder_requires_unchanged_plan_and_creates_bundle(
    tmp_path, monkeypatch, tk_root,
):
    package = _package(tmp_path)
    (package / "stream" / "w_pi_workbench_new.ydr").write_bytes(b"new-model")
    weapons_path = package / "weapons.meta"
    weapons_path.write_text(
        weapons_path.read_text(encoding="utf-8").replace(
            "<TrailFx/><PrimedFx/>",
            "<TrailFx>NONE</TrailFx><PrimedFx>NONE</PrimedFx>",
        ),
        encoding="utf-8",
    )
    (package / "weapon_shop.meta").write_text(
        "<WeaponShopItemArray><weaponShopItems><Item>"
        "<nameHash>WEAPON_WORKBENCH</nameHash><cost value=\"750\"/>"
        "</Item></weaponShopItems></WeaponShopItemArray>",
        encoding="utf-8",
    )
    workspace = WeaponAuthoringWorkspace.create(
        package, tmp_path / "clone-authoring",
    )
    frame = WeaponWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    frame.open_source(
        workspace.source, AddonPackageInspector().inspect(workspace.source),
        authoring_workspace=workspace,
    )

    assert "New from template" in [
        frame.project_tabs.tab(page, "text")
        for page in frame.project_tabs.tabs()
    ]
    assert frame.weapon_clone_donor.get() == "WEAPON_WORKBENCH"
    values = {
        "weapon_name": "WEAPON_WORKBENCH_NEW",
        "slot": "SLOT_WORKBENCH_NEW",
        "model": "w_pi_workbench_new",
        "human_name_hash": "WT_WORK_NEW",
        "stat_name": "WORKBENCH_NEW",
    }
    for key, value in values.items():
        frame.weapon_clone_values[key].set(value)
    frame.weapon_clone_ammo.set("AMMO_WORKBENCH_NEW")
    frame._reload_authoring_workspace("WEAPON_WORKBENCH")
    assert {
        key: variable.get() for key, variable in frame.weapon_clone_values.items()
    } == values
    assert frame.weapon_clone_ammo.get() == "AMMO_WORKBENCH_NEW"
    assert frame._editor_snapshot() != frame._loaded_editor_snapshot
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame._review_weapon_clone_plan()

    assert frame._weapon_clone_plan is not None
    assert len(frame._weapon_clone_plan_digest) == 64
    assert str(frame.create_weapon_clone_button.cget("state")) == "normal"
    assert len(frame.weapon_clone_preview_tree.get_children()) == 4
    reviewed_digest = frame._weapon_clone_plan_digest

    frame.weapon_clone_values["stat_name"].set("WORKBENCH_CHANGED")
    assert frame._weapon_clone_plan is None
    assert frame._weapon_clone_plan_digest == ""
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame.weapon_clone_values["stat_name"].set("WORKBENCH_NEW")
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame._review_weapon_clone_plan()
    assert frame._weapon_clone_plan_digest == reviewed_digest

    frame.weapon_clone_values["weapon_name"].set("WEAPON_WORKBENCH")
    frame._review_weapon_clone_plan()
    collision_row = frame.weapon_clone_preview_tree.item("clone-plan:2", "values")
    assert int(collision_row[0]) > 0
    assert "Blocked" in frame.weapon_clone_summary.get()
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame.weapon_clone_values["weapon_name"].set("WEAPON_WORKBENCH_NEW")
    frame._review_weapon_clone_plan()
    assert frame._weapon_clone_plan_digest == reviewed_digest

    frame.weapon_clone_ammo_mode.set(AMMO_MODE_REUSE)
    frame._weapon_clone_mode_selected()
    assert frame._weapon_clone_plan is None
    assert frame.weapon_clone_ammo.get() == "AMMO_WORKBENCH"
    assert "Existing ammo" in frame.weapon_clone_ammo_label.get()
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame.weapon_clone_ammo_mode.set(AMMO_MODE_CLONE)
    frame._weapon_clone_mode_selected()
    assert frame.weapon_clone_ammo.get() == ""
    frame.weapon_clone_ammo.set("AMMO_WORKBENCH_NEW")
    frame._review_weapon_clone_plan()
    assert frame._weapon_clone_plan_digest == reviewed_digest

    frame.weapon_clone_donor.set("WEAPON_UNKNOWN_DONOR")
    assert frame._weapon_clone_plan is None
    assert str(frame.create_weapon_clone_button.cget("state")) == "disabled"
    frame.weapon_clone_donor.set("WEAPON_WORKBENCH")
    frame._review_weapon_clone_plan()
    assert frame._weapon_clone_plan_digest == reviewed_digest

    monkeypatch.setattr(
        "allin1_sdk.weapon_workbench.messagebox.askyesno",
        lambda *_args, **_kwargs: True,
    )
    frame._create_weapon_from_plan()
    assert workspace.revision == 1
    assert frame.selected_weapon is not None
    assert frame.selected_weapon.name == "WEAPON_WORKBENCH_NEW"
    scan = AddonPackageInspector().inspect(workspace.source)
    assert "WEAPON_WORKBENCH_NEW" in {item.name for item in scan.weapons}
    assert "AMMO_WORKBENCH_NEW" in {item.name for item in scan.ammo}
    assert "WEAPON_WORKBENCH_NEW" in scan.animation_weapons
    assert "WEAPON_WORKBENCH_NEW" in scan.shop_weapons
    assert any(
        item.weapon_name == "WEAPON_WORKBENCH_NEW"
        and item.component_name == "COMPONENT_WORKBENCH_CLIP"
        for item in scan.weapon_component_links
    )

    workspace.undo(expected_revision=1)
    restored = AddonPackageInspector().inspect(workspace.source)
    assert "WEAPON_WORKBENCH_NEW" not in {item.name for item in restored.weapons}
    assert "AMMO_WORKBENCH_NEW" not in {item.name for item in restored.ammo}
    frame.destroy()


def test_weapon_integration_offers_mapping_only_stock_animation_templates(
    tmp_path, tk_root,
):
    package = _package(tmp_path)
    (package / "weaponanimations.meta").write_text(
        "<CWeaponAnimationsSets><Sets>"
        '<Item key="Default"><WeaponAnimations>'
        '<Item key="WEAPON_STOCK_TEMPLATE"><Clip ref="stock"/></Item>'
        "</WeaponAnimations></Item>"
        "</Sets></CWeaponAnimationsSets>",
        encoding="utf-8",
    )
    frame = WeaponWorkbenchFrame(tk_root)
    frame.pack(fill="both", expand=True)
    frame.open_source(package, AddonPackageInspector().inspect(package))

    assert frame.selected_weapon is not None
    assert frame.selected_weapon.name == "WEAPON_WORKBENCH"
    assert tuple(frame.animation_template_combo.cget("values")) == (
        "WEAPON_STOCK_TEMPLATE",
    )
    assert frame.animation_template.get() == "WEAPON_STOCK_TEMPLATE"
    assert str(frame.clone_animation_button.cget("state")) == "disabled"
    frame.destroy()


def test_shared_workbench_navigation_checks_vehicle_and_weapon_editors(
    tmp_path, monkeypatch, tk_root,
):
    frame = WorkbenchFrame(tk_root, Path(__file__).resolve().parents[1])
    frame.pack(fill="both", expand=True)
    assert frame.open_source(_package(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        frame.vehicle_workspace, "confirm_navigation",
        lambda: calls.append("vehicle") or True,
    )
    monkeypatch.setattr(
        frame.weapon_workspace, "confirm_navigation",
        lambda: calls.append("weapon") or False,
    )
    assert not frame.confirm_navigation()
    assert calls == ["vehicle", "weapon"]
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
