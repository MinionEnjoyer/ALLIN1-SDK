from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "allin1_sdk"


def _source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def test_primary_sdk_routes_are_plain_language_and_package_selection_works():
    source = _source("addon_sdk_ui.py")
    for label in (
        '"Package Linker"', '"Asset Viewer"', '"RPF Archives"',
        '"Quick Import"', '"Models & Materials"', '"Package Recipes"',
        '"Help Center"',
        'text="Import or audit package"',
        'text="Inspect or export"', 'text="Package tools"',
    ):
        assert label in source
    assert 'self.example_list.bind("<<TreeviewSelect>>"' in source
    assert "<<ListboxSelect>>\", self._select_example" not in source
    assert 'label="Show workspace sidebar", accelerator="Ctrl+B"' in source
    assert 'self.bind("<Control-b>", self._toggle_sidebar)' in source
    assert 'text="← Back"' not in source
    assert 'style="HeaderLink.TButton", cursor="hand2"' in source
    assert 'button.configure(text=f"‹ {self._workspace_label(target)}")' in source
    assert "cancelling the workbench's unsaved-edit warning" in source
    assert 'label="Open vehicle in Quick Import — Legacy…"' in source
    assert 'label="Open vehicle in Quick Import — Enhanced…"' in source
    assert 'self._select_workspace("quick_import")' in source
    assert "self.quick_import_workspace.open_source(" in source
    assert "ManagedVehiclePackageConverter(" not in source


def test_graph_binary_and_gxt2_authoring_are_embedded_with_compatibility_hosts():
    graph = _source("rpf_graph_ui.py")
    binary = _source("binary_workspace_ui.py")
    explorer = _source("rpf_explorer.py")
    assert "class RpfPackageGraphFrame(ttk.Frame)" in graph
    assert "class RpfPackageGraphDialog(tk.Toplevel)" in graph
    assert "self.editor = RpfPackageGraphFrame(" in graph
    assert "class BinaryWorkspaceFrame(ttk.Frame)" in binary
    assert "tk.Toplevel" not in binary
    assert "class Gxt2WorkspaceFrame(ttk.Frame)" in explorer
    assert "class Gxt2WorkspaceDialog(tk.Toplevel)" in explorer
    assert 'self.workspace_tabs.add(self.graph_tab, text="Package Graph")' in explorer
    assert 'self.workspace_tabs.add(self.binary_tab, text="Binary Workspace")' in explorer
    assert 'self.workspace_tabs.add(self.gxt2_tab, text="GXT2 Text")' in explorer
    assert "self._graph_editor = RpfPackageGraphFrame(" in explorer
    assert 'text="Recent package projects"' in explorer
    assert "PackageGraphWorkspace().import_package" in explorer
    assert 'text="Expand sealed RPF into nodes…"' in graph
    assert 'text="Analyze links"' in graph
    assert '"vehicle_entity"' in graph
    assert "RELATION_FILTERS" in graph
    assert 'text="Open in Asset Viewer"' in graph
    assert 'text="Open in Vehicle Workbench"' in graph
    assert "_toggle_selected_collapse" in graph
    assert "self._binary_editor = BinaryWorkspaceFrame(" in explorer
    assert "self._gxt2_editor = Gxt2WorkspaceFrame(" in explorer
    assert "RpfPackageGraphDialog(\n            self" not in explorer
    assert "Gxt2WorkspaceDialog(self, workspace)" not in explorer


def test_rpf_actions_are_grouped_and_common_entry_actions_are_visible():
    source = _source("rpf_explorer.py")
    for menu in (
        'label="Build & Author"', 'label="Inspect & Verify"',
        'label="Catalog"', 'label="Plan Changes"',
        'label="Transactions & Recovery"', 'label="Preview"',
        'label="Export Workspace"', 'label="Plan Change"',
    ):
        assert menu in source
    for button in (
        'text="Preview", command=self._preview_selected',
        'text="Extract", command=self._extract_selected',
        'text="Plan", command=self._plan_replacement',
        'text="Edit bytes", command=self._export_binary_workspace',
        'text="GXT2", command=self._export_gxt2_workspace',
    ):
        assert button in source


def test_binary_workspace_exposes_guarded_patch_and_archive_plan_controls():
    binary = _source("binary_workspace_ui.py")
    explorer = _source("rpf_explorer.py")
    for control in (
        'text="Expected current bytes"',
        'text="Replacement bytes"',
        'text="Read current bytes"',
        'text="Apply patch…"',
        '("Undo latest", self._undo)',
        '("Build verified…", self._build)',
        '("Create RPF plan…", self._plan)',
    ):
        assert control in binary
    assert "BinaryPatchWorkspace.validate(self.workspace)" in binary
    assert "expected_hex=expected.hex()" in binary
    assert "self._open_binary_editor(workspace)" in explorer
    assert "self._plan_binary_workspace_from_editor" in explorer
    assert "Use the SDK Console's inspect-" not in explorer


def test_package_recipes_replace_the_oiv_prompt_cascade_with_one_workspace():
    shell = _source("addon_sdk_ui.py")
    recipes = _source("oiv_workbench_ui.py")
    assert "class OivWorkbenchFrame(ttk.Frame)" in recipes
    assert "tk.Toplevel" not in recipes
    assert '"recipes": recipes_page' in shell
    assert "from allin1_sdk.oiv_workbench_ui import OivWorkbenchFrame" in shell
    assert "self.recipe_workspace = workspace" in shell
    assert 'label="Open Package Recipes"' in shell
    assert "def _preview_oiv" not in shell
    assert "RpfProgressDialog" not in shell
    for action in (
        'text="Open recipe…"',
        '("report", "Export inspection report…", self._export_report)',
        '("compile", "Compile against existing RPF…", self._compile_existing)',
        '("batches", "Export atomic RPF batches…", self._export_batches)',
        '("created", "Build declared new archives…", self._build_created)',
        '("managed", "Export managed package…", self._export_managed)',
    ):
        assert action in recipes
    assert "plan.rpf_recipe_compilable" in recipes
    assert "plan.translatable and plan.created_archive_operations" in recipes
    assert "plan.managed_exportable" in recipes
    assert "recipes.has_active_work()" in shell
    assert 'text="Back to Package Linker"' not in recipes


def test_asset_viewer_separates_package_browsing_from_native_authoring():
    source = _source("asset_viewer.py")
    assert 'label="Open package folder…"' in source
    assert 'label="Open package archive…"' in source
    assert 'menu.add_cascade(label="Native authoring"' in source
    assert 'text="Export selected for editing…"' in source
    open_menu = source[source.index("def _open_menu"):source.index("def _action_menu")]
    assert "Build native workspace" not in open_menu
    assert "Open YTD texture workspace" not in open_menu


def test_rpf_large_preview_warning_formats_the_actual_cap():
    explorer = _source("rpf_explorer.py")
    assert 'f"are capped at {_human_size(MAX_NATIVE_PREVIEW_BYTES)}."' in explorer
    assert '"are capped at {_human_size(MAX_NATIVE_PREVIEW_BYTES)}."' not in explorer.replace(
        'f"are capped at {_human_size(MAX_NATIVE_PREVIEW_BYTES)}."', "",
    )


def test_unified_workbench_is_integrated_and_exposes_vehicle_viewport_controls():
    shell = _source("addon_sdk_ui.py")
    unified = _source("workbench.py")
    workbench = _source("vehicle_workbench.py")
    assert '"workbench": workbench_page' in shell
    assert '("workbench", "Content Workbench", "Ctrl+3")' in shell
    assert "from allin1_sdk.workbench import WorkbenchFrame" in shell
    assert "self.workbench_workspace = workspace" in shell
    assert "class WorkbenchFrame(ttk.Frame)" in unified
    assert "VehicleWorkbenchFrame(" in unified
    assert "WeaponWorkbenchFrame(" in unified
    assert "PedWorkbenchFrame(" in unified
    assert "MapWorkbenchFrame(" in unified
    weapon = _source("weapon_workbench.py")
    for control in (
        'text="Create authoring workspace…"',
        'text="Apply + validate"',
        'text="Undo latest"',
        'text="Apply attachment + validate"',
        'text="Apply component + validate"',
    ):
        assert control in weapon
    for operation in (
        "WeaponAuthoringWorkspace.create(",
        "workspace.update(",
        "workspace.update_attachment(",
        "workspace.update_component(",
        "workspace.undo(",
    ):
        assert operation in weapon
    assert 'expected_revision=workspace.revision' in weapon
    assert "acknowledge_shared=acknowledge_shared" in weapon
    ped = _source("ped_workbench.py")
    for control in (
        'text="Create authoring workspace…"',
        'text="Apply fields"',
        'text="Undo latest"',
        'text="New from template"',
        'text="Review plan"',
        'text="Create reviewed ped"',
        'text="Migrate identity + assets"',
        'text="Preview"',
        'text="Refresh"',
    ):
        assert control in ped
    for operation in (
        "PedAuthoringWorkspace.create(",
        "workspace.plan_ped_clone(",
        "workspace.clone_ped_bundle(",
        "workspace.migrate_identity(",
        "workspace.update(",
        "workspace.undo(",
        "expected_revision=workspace.revision",
        "LatestOnlyRenderWorker(",
        "NativeAssetInspector(",
    ):
        assert operation in ped
    assert "workbench.confirm_navigation()" in shell
    assert 'self.tabs.add(vehicle_page, text="Vehicles")' in unified
    assert 'self.tabs.add(weapon_page, text="Weapons")' in unified
    assert 'self.tabs.add(ped_page, text="Peds")' in unified
    assert 'self.tabs.add(map_page, text="Maps")' in unified
    assert 'workspace.pack(fill="both", expand=True)' in shell
    assert 'label="Open in Workbench…"' in shell
    assert "class VehicleWorkbenchFrame(ttk.Frame)" in workbench
    assert "tk.Toplevel" not in workbench
    for control in (
        '"Shaded", "Materials", "Wireframe"',
        'text="Model ▾"', 'label="Fragment"', 'label="LOD"',
        'label="Component"', 'text="View ▾"', 'text="Fit"',
        '("Perspective", 34.0, 24.0)', '("Front", 0.0, 0.0)',
        '("Top", 0.0, 89.0)',
        'label="Reset camera"', 'text="Open texture dictionary"',
        'text="Build installable package…"',
        'text="Create authoring workspace…"',
        'text="Apply + validate"', 'text="Undo latest"',
        'text="Appearance"', 'text="Apply appearance + validate"',
        'text="Apply kit"', 'text="Apply field"',
        'text="Tuning Builder"', 'text="Add + validate"',
        'text="Entry actions…"', 'label="New entry"', 'label="Copy selected"',
        'label="Delete selected"', 'label="Move up"', 'label="Move down"',
        'text="Use for new part"', 'text="Open in Asset Viewer"',
        'text="Migrate + validate"',
        '"<MouseWheel>"', '"<ButtonPress-1>"', '"<ButtonPress-3>"',
        '"<Double-Button-1>"', '"<KeyPress-f>"', '"<KeyPress-0>"',
    ):
        assert control in workbench
    assert "report.model_scene" in workbench
    assert "scene.render(" in workbench
    assert "render_mode=render_mode, quality=quality" in workbench
    assert 'quality="interactive"' in workbench
    assert "scene.components" in workbench
    assert "VehicleAddonPackageBuilder(" in workbench
    assert "VehicleAuthoringWorkspace.create(" in workbench
    assert "workspace.update(" in workbench
    assert "workspace.update_appearance(" in workbench
    assert "workspace.update_tuning_kit(" in workbench
    assert "workspace.tuning_builder(" in workbench
    assert "workspace.add_tuning_entry(" in workbench
    assert "workspace.update_tuning_entry(" in workbench
    assert "workspace.remove_tuning_entry(" in workbench
    assert "workspace.move_tuning_entry(" in workbench
    assert "workspace.update_light_profile(" in workbench
    assert "workspace.migrate_identity(" in workbench
    assert "workspace.undo()" in workbench
    assert 'text="Open selected in Asset Viewer"' in workbench
    assert "on_open_asset=self._open_workbench_asset" in shell
    assert "self.asset_workspace.select_asset(path)" in shell
    assert "def select_asset(self, path: str) -> bool" in _source("asset_viewer.py")


def test_quick_import_is_separate_from_the_consolidated_content_workbench():
    shell = _source("addon_sdk_ui.py")
    quick_import = _source("quick_import_ui.py")

    assert '("quick_import", "Quick Import", "Ctrl+I")' in shell
    assert '"quick_import": quick_import_page' in shell
    assert "from allin1_sdk.quick_import_ui import QuickImportFrame" in shell
    assert "self.quick_import_workspace = workspace" in shell
    assert "class QuickImportFrame(ttk.Frame)" in quick_import
    assert "tk.Toplevel" not in quick_import
    assert 'self.tabs.add(vehicle_page, text="Vehicles")' in quick_import
    assert 'self.tabs.add(weapon_page, text="Weapons")' in quick_import
    assert 'self.tabs.add(ped_page, text="Peds")' in quick_import
    assert "on_open_workbench=self._open_quick_import_workbench" in shell
    assert 'quick_import.confirm_navigation()' in shell


def test_model_material_workbench_is_integrated_and_guarded():
    shell = _source("addon_sdk_ui.py")
    ui = _source("model_material_workbench.py")
    core = _source("model_materials.py")
    assert '("models", "Models & Materials", "Ctrl+4")' in shell
    assert '"models": models_page' in shell
    assert "from allin1_sdk.model_material_workbench import ModelMaterialWorkbenchFrame" in shell
    assert "self.model_material_workspace = workspace" in shell
    assert 'label="Open in Models & Materials…"' in shell
    assert "class ModelMaterialWorkbenchFrame(ttk.Frame)" in ui
    assert "tk.Toplevel" not in ui
    for control in (
        'text="Create editable copy"', 'text="Build verified asset…"',
        'text="Undo"', 'text="Render…"',
        '("Shaded", "Materials", "Wireframe")',
        'text="Apply to editable copy"', 'text="Assign"',
        '"<MouseWheel>"', '"<ButtonPress-1>"',
    ):
        assert control in ui
    for operation in (
        "LatestOnlyRenderWorker(", "CompiledRenderPanel(",
        "compile_vehicle_render(", "MaterialAuthoringWorkspace.create(",
        "workspace.set_material(", "workspace.set_geometry_material(",
        "workspace.undo(", "workspace.build(",
    ):
        assert operation in ui
    for invariant in (
        "GuardedXmlWorkspace(", "expected_revision", "xml_sha256",
        "record_post_edit_state", "verify_post_edit_state",
        "synthesize schema fields", "load_native_model_scene",
    ):
        assert invariant in core


def test_specialist_workspaces_are_lazy_loaded_inside_the_unified_shell():
    shell = _source("addon_sdk_ui.py")
    imports, _separator, body = shell.partition("class AddonSdkDialog")
    for module in (
        "asset_viewer", "workbench", "model_material_workbench", "rpf_explorer",
        "oiv_workbench_ui", "quick_import_ui", "help_center",
    ):
        assert f"from allin1_sdk.{module} import" not in imports
        assert f"from allin1_sdk.{module} import" in body
    assert "self._workspace_instances: dict[str, ttk.Frame] = {}" in shell
    assert "self._ensure_workspace(key)" in shell


def test_both_visual_canvases_offer_matching_zoom_controls():
    package_graph = _source("rpf_graph_ui.py")
    build_flow = _source("rpf_program_ui.py")
    for source in (package_graph, build_flow):
        assert "_zoom_by" in source
        assert "_fit_graph" in source
        assert "_reset_zoom" in source
        assert 'text="100%"' in source
        assert "<Control-MouseWheel>" in source


def test_graph_exports_previews_and_guards_background_work_lifecycle():
    graph = _source("rpf_graph_ui.py")
    build_flow = _source("rpf_program_ui.py")
    sdk_shell = _source("addon_sdk_ui.py")
    explorer = _source("rpf_explorer.py")
    app = _source("app.py")
    assert 'label="Export preview bundle…"' in graph
    assert "render_graph_preview_bundle(" in graph
    assert "self._preview_stop = threading.Event()" in graph
    assert "stop.set()" in graph
    assert "def has_active_work(self)" in graph
    assert "on_busy_change=self._set_program_busy" in graph
    assert "def busy(self)" in build_flow
    assert "self._set_busy(True)" in build_flow
    assert "self._set_busy(False)" in build_flow
    assert "def request_close(self)" in sdk_shell
    assert "rpf.has_active_work()" in sdk_shell
    assert "def has_active_work(self)" in explorer
    assert 'dialog.protocol("WM_DELETE_WINDOW", dialog.request_close)' in app
    assert 'dialog.protocol("WM_DELETE_WINDOW", close_sdk)' in app
