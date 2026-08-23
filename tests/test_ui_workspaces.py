from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "allin1_sdk"


def _source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def test_primary_sdk_routes_are_plain_language_and_package_selection_works():
    source = _source("addon_sdk_ui.py")
    for label in (
        '"Package Linker"', '"Asset Viewer"', '"RPF Archives"',
        '"Package Recipes"', '"Help Center"', 'text="Import or audit package"',
        'text="Inspect or export"', 'text="Package tools"',
    ):
        assert label in source
    assert 'self.example_list.bind("<<TreeviewSelect>>"' in source
    assert "<<ListboxSelect>>\", self._select_example" not in source


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


def test_asset_viewer_separates_package_browsing_from_native_authoring():
    source = _source("asset_viewer.py")
    assert 'label="Open package folder…"' in source
    assert 'label="Open package archive…"' in source
    assert 'menu.add_cascade(label="Native authoring"' in source
    assert 'text="Export selected for editing…"' in source
    open_menu = source[source.index("def _open_menu"):source.index("def _action_menu")]
    assert "Build native workspace" not in open_menu
    assert "Open YTD texture workspace" not in open_menu


def test_vehicle_workbench_is_integrated_and_exposes_viewport_controls():
    shell = _source("addon_sdk_ui.py")
    workbench = _source("vehicle_workbench.py")
    assert '"vehicles": vehicles_page' in shell
    assert '("vehicles", "Vehicle Workbench")' in shell
    assert "from allin1_sdk.vehicle_workbench import VehicleWorkbenchFrame" in shell
    assert "self.vehicle_workspace = workspace" in shell
    assert 'workspace.pack(fill="both", expand=True)' in shell
    assert 'label="Open vehicle workbench…"' in shell
    assert "class VehicleWorkbenchFrame(ttk.Frame)" in workbench
    assert "tk.Toplevel" not in workbench
    for control in (
        'text="−"', 'text="100%"', 'text="+"', 'text="Fit"',
        'text="Fragment"', 'text="LOD"', 'text="Component"',
        'text="Reset camera"', 'text="Open texture dictionary"',
        'text="Build installable package…"',
        'text="Create authoring workspace…"',
        'text="Apply + validate"', 'text="Undo latest"',
        'text="Appearance"', 'text="Apply appearance + validate"',
        'text="Apply kit"', 'text="Apply field"',
        'text="Tuning Builder"', 'text="Add + validate"',
        'text="Duplicate"', 'text="Remove"', 'text="Apply field + validate"',
        'text="Use for new part"', 'text="Open in Asset Viewer"',
        'text="Migrate + validate"',
        '"<MouseWheel>"', '"<ButtonPress-1>"', '"<ButtonPress-3>"',
    ):
        assert control in workbench
    assert "report.model_scene" in workbench
    assert "scene.render(" in workbench
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
    assert "on_open_asset=self._open_vehicle_asset" in shell
    assert "self.asset_workspace.select_asset(path)" in shell
    assert "def select_asset(self, path: str) -> bool" in _source("asset_viewer.py")


def test_specialist_workspaces_are_lazy_loaded_inside_the_unified_shell():
    shell = _source("addon_sdk_ui.py")
    imports, _separator, body = shell.partition("class AddonSdkDialog")
    for module in (
        "asset_viewer", "vehicle_workbench", "rpf_explorer",
        "oiv_workbench_ui", "help_center",
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
