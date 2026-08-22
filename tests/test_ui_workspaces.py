from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "allin1_sdk"


def _source(name: str) -> str:
    return (SOURCE / name).read_text(encoding="utf-8")


def test_primary_sdk_routes_are_plain_language_and_package_selection_works():
    source = _source("addon_sdk_ui.py")
    for label in (
        '"Package Linker"', '"Asset Viewer"', '"RPF Archives"',
        '"Help Center"', 'text="Import or audit package"',
        'text="Inspect or export"', 'text="Package tools"',
    ):
        assert label in source
    assert 'self.example_list.bind("<<TreeviewSelect>>"' in source
    assert "<<ListboxSelect>>\", self._select_example" not in source


def test_graph_and_gxt2_authoring_are_embedded_with_compatibility_hosts():
    graph = _source("rpf_graph_ui.py")
    explorer = _source("rpf_explorer.py")
    assert "class RpfPackageGraphFrame(ttk.Frame)" in graph
    assert "class RpfPackageGraphDialog(tk.Toplevel)" in graph
    assert "self.editor = RpfPackageGraphFrame(" in graph
    assert "class Gxt2WorkspaceFrame(ttk.Frame)" in explorer
    assert "class Gxt2WorkspaceDialog(tk.Toplevel)" in explorer
    assert 'self.workspace_tabs.add(self.graph_tab, text="Package Graph")' in explorer
    assert 'self.workspace_tabs.add(self.gxt2_tab, text="GXT2 Text")' in explorer
    assert "self._graph_editor = RpfPackageGraphFrame(" in explorer
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
        'text="GXT2", command=self._export_gxt2_workspace',
    ):
        assert button in source


def test_asset_viewer_separates_package_browsing_from_native_authoring():
    source = _source("asset_viewer.py")
    assert 'label="Open package folder…"' in source
    assert 'label="Open package archive…"' in source
    assert 'menu.add_cascade(label="Native authoring"' in source
    assert 'text="Export selected for editing…"' in source
    open_menu = source[source.index("def _open_menu"):source.index("def _action_menu")]
    assert "Build native workspace" not in open_menu
    assert "Open YTD texture workspace" not in open_menu


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
