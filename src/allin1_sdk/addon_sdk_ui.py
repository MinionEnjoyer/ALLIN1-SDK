"""Desktop viewer for ALLIN1 add-on SDK manifests and linked fields."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import webbrowser
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import ImageTk

from allin1_sdk import __version__
from allin1_sdk.addon_importer import AddonDraftBuilder, AddonPackageInspector, PackageScan
from allin1_sdk.branding import apply_sdk_window_icon, load_sdk_banner_logo
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.sdk_console import SdkConsoleDialog
from allin1_sdk.processes import run_hidden
from allin1_sdk.paths import user_data_root
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip
from allin1_sdk.product_api_contract import (
    RuntimeApiCall,
    RuntimeContractReport,
    RuntimeHostAudit,
    RuntimeMemberAudit,
    RuntimePackageAudit,
)
from allin1_sdk.ui_foundation import (
    BODY_BACKGROUND,
    BRAND_DEEP_GREEN,
    SURFACE_BACKGROUND,
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    apply_native_widget_theme,
    apply_sdk_theme,
    current_effective_theme,
    current_theme_mode,
    place_window,
    shell_status_presentation,
)
from allin1_sdk.addon_sdk import (
    AddonInstallStep,
    AddonIssue,
    AddonLinkReport,
    AddonLinker,
    AddonManifest,
    AddonNode,
    AddonReference,
    AddonSdkCatalog,
    field_description,
)


def _display_value(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{key} = {item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if value == "":
        return "(empty)"
    return str(value)


@dataclass(frozen=True)
class _ApiContractDetail:
    """Present one canonical contract value in the existing field inspector."""

    heading: str
    description: str
    source: str | None
    line: int | None
    fields: tuple[tuple[str, Any], ...]


class AddonSdkDialog(tk.Toplevel):
    """Inspect add-on fields, resolved references, and a safe install plan."""

    NAVIGATION = (
        ("linker", "Package Linker", "Ctrl+1"),
        ("assets", "Asset Viewer", "Ctrl+2"),
        ("workbench", "Content Workbench", "Ctrl+3"),
        ("quick_import", "Quick Import", "Ctrl+I"),
        ("models", "Models & Materials", "Ctrl+4"),
        ("rpf", "RPF Archives", "Ctrl+5"),
        ("recipes", "Package Recipes", "Ctrl+6"),
        ("help", "Help Center", "Ctrl+7"),
    )

    def __init__(
        self, parent: tk.Misc, project_root: str | Path,
        installation_roots: tuple[Path, ...] = (),
        standalone: bool = False,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.catalog = AddonSdkCatalog(self.project_root, state_root=user_data_root())
        self.installation_roots = installation_roots
        self.linker = AddonLinker()
        self.manifests: list[AddonManifest] = []
        self.report: AddonLinkReport | None = None
        self.package_source: Path | None = None
        self.package_scan: PackageScan | None = None
        self._selection: dict[str, object] = {}
        self.review_menus: list[tk.Menu] = []
        self.package_tool_menus: list[tk.Menu] = []
        self._logo_photo: ImageTk.PhotoImage | None = None
        self._navigation_history: list[str] = []
        self.sidebar_visible = tk.BooleanVar(self, value=True)
        self.theme_mode = tk.StringVar(self, value=current_theme_mode(self))
        self.title("ALLIN1 SDK — Developer Workspace")
        self.configure(background=BODY_BACKGROUND)
        place_window(self, preferred=(1320, 840), minimum=(980, 640))
        if not standalone:
            self.transient(parent)
        apply_sdk_window_icon(self, self.project_root)
        self._build()
        # Classic Tk header/menu widgets retain explicit legacy colors. Apply
        # the startup palette before the window is presented to the user.
        apply_sdk_theme(self, self.theme_mode.get())
        self._load_examples()

    def _package_inspector(self) -> AddonPackageInspector:
        game = next(
            (path for path in self.installation_roots if path.is_dir()), None,
        )
        return AddonPackageInspector(self.project_root, game)

    def _build_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)

        file_menu = self._make_content_menu(menu)
        menu.add_cascade(label="File", menu=file_menu)
        self.file_menu = file_menu

        package_menu = tk.Menu(menu, tearoff=False)
        review = self._make_review_menu(menu)
        package_menu.add_cascade(label="Inspect & Export", menu=review)
        intelligence = self._make_intelligence_menu(menu)
        package_menu.add_cascade(label="Authoring & Utilities", menu=intelligence)
        menu.add_cascade(label="Package", menu=package_menu)
        self.package_menu = package_menu

        view = tk.Menu(menu, tearoff=False)
        view.add_command(
            label="Back", accelerator="Alt+Left", command=self._go_back,
        )
        view.add_separator()
        for key, label, shortcut in self.NAVIGATION:
            view.add_command(
                label=label, accelerator=shortcut,
                command=lambda selected=key: self._select_workspace(selected),
            )
        view.add_separator()
        view.add_command(
            label="Next workspace", accelerator="Ctrl+Tab",
            command=self._cycle_workspace,
        )
        view.add_command(
            label="Previous workspace", accelerator="Ctrl+Shift+Tab",
            command=lambda: self._cycle_workspace(delta=-1),
        )
        view.add_separator()
        view.add_checkbutton(
            label="Show workspace sidebar", accelerator="Ctrl+B",
            variable=self.sidebar_visible, onvalue=True, offvalue=False,
            command=lambda: self._set_sidebar_visible(
                self.sidebar_visible.get(),
            ),
        )
        # Some Tk builds initialize a checkbutton's variable while installing
        # the menu entry. Reassert the declared default after registration so
        # the first window is deterministic across themes/platform builds.
        self.sidebar_visible.set(True)
        view.add_separator()
        theme = tk.Menu(view, tearoff=False)
        for label, value in (
            ("Light", THEME_LIGHT),
            ("Dark", THEME_DARK),
            ("System", THEME_SYSTEM),
        ):
            theme.add_radiobutton(
                label=label, variable=self.theme_mode, value=value,
                command=self._set_theme_mode,
            )
        view.add_cascade(label="Theme", menu=theme)
        menu.add_cascade(label="View", menu=view)
        self.view_menu = view
        self.theme_menu = theme
        tools = tk.Menu(menu, tearoff=False)
        tools.add_command(
            label="Focus / expand SDK Console", accelerator="Ctrl+`",
            command=self._open_console,
        )
        menu.add_cascade(label="Tools", menu=tools)
        self.tools_menu = tools
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="SDK Help Center", accelerator="F1",
            command=lambda: self._open_help("sdk"),
        )
        help_menu.add_command(
            label="RPF Archives Help",
            command=lambda: self._open_help("rpf-explorer"),
        )
        help_menu.add_separator()
        help_menu.add_command(
            label="Keyboard shortcuts",
            command=lambda: self._open_help("input"),
        )
        help_menu.add_separator()
        help_menu.add_command(
            label="Check for updates…",
            command=self._open_updater,
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self.help_menu = help_menu
        self.application_menu = menu
        self.configure(menu=menu)
        self.bind("<F1>", lambda _event: self._open_context_help())
        self.bind("<F5>", lambda _event: self._refresh_audit())
        self.bind("<Control-o>", lambda _event: self._open_manifest())
        self.bind("<Control-KeyPress-grave>", lambda _event: self._open_console())

    def _make_content_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(
            label="Open manifest or product workspace…",
            accelerator="Ctrl+O",
            command=self._open_manifest,
        )
        menu.add_separator()
        menu.add_command(label="Open DLC RPF…", command=self._import_rpf)
        menu.add_command(label="Import DLC folder…", command=self._import_folder)
        menu.add_command(label="Import package archive…", command=self._import_archive)
        menu.add_command(label="Audit package folder…", command=self._audit_folder)
        return menu

    def _make_review_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Export link report…", command=self._export_report)
        menu.add_command(label="Open selected source", command=self._open_source)
        menu.add_command(
            label="Refresh current audit", accelerator="F5",
            command=self._refresh_audit,
            state="disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="Browse package assets…", command=self._open_asset_viewer,
            state="disabled",
        )
        menu.add_command(
            label="Open in Workbench…", command=self._open_workbench,
            state="disabled",
        )
        menu.add_command(
            label="Open in Models & Materials…",
            command=self._open_model_materials, state="disabled",
        )
        menu.add_command(
            label="Inspect package RPFs…", command=self._inspect_package_rpfs,
            state="disabled",
        )
        menu.add_command(label="Go to RPF Archives", command=self._open_rpf_explorer)
        self.review_menus.append(menu)
        return menu

    def _set_audit_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.review_menus:
            menu.entryconfigure("Refresh current audit", state=state)
        if hasattr(self, "refresh_audit_button"):
            self.refresh_audit_button.configure(state=state)
            if enabled and not self.refresh_audit_button.winfo_manager():
                self.refresh_audit_button.pack(side="right")
            elif not enabled:
                self.refresh_audit_button.pack_forget()

    def _make_intelligence_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open Package Recipes", command=self._open_oiv_workbench)
        menu.add_command(label="Inventory installed DLC…", command=self._inventory_dlc)
        menu.add_command(label="Compile vehicle data…", command=self._compile_vehicle_data)
        menu.add_separator()
        menu.add_command(label="Compare META/XML…", command=self._compare_meta)
        menu.add_command(
            label="Validate META/XML round trip…", command=self._validate_meta_roundtrip,
        )
        menu.add_separator()
        menu.add_command(
            label="Open vehicle in Quick Import — Legacy…",
            command=lambda: self._export_managed_vehicle_package("legacy"),
            state="disabled",
        )
        menu.add_command(
            label="Open vehicle in Quick Import — Enhanced…",
            command=lambda: self._export_managed_vehicle_package("enhanced"),
            state="disabled",
        )
        self.package_tool_menus.append(menu)
        return menu

    def _set_package_actions(
        self, *, assets: bool, rpfs: bool, workbench: bool = False,
    ) -> None:
        for menu in self.review_menus:
            menu.entryconfigure(
                "Browse package assets…", state="normal" if assets else "disabled",
            )
            menu.entryconfigure(
                "Inspect package RPFs…", state="normal" if rpfs else "disabled",
            )
            menu.entryconfigure(
                "Open in Workbench…",
                state="normal" if workbench else "disabled",
            )
            menu.entryconfigure(
                "Open in Models & Materials…",
                state="normal" if assets else "disabled",
            )
        editions = {
            item.edition.casefold() for item in self.package_scan.rpf_archives
        } if self.package_scan is not None and self.package_scan.vehicles else set()
        for menu in self.package_tool_menus:
            for edition in ("legacy", "enhanced"):
                menu.entryconfigure(
                    f"Open vehicle in Quick Import — {edition.title()}…",
                    state="normal" if edition in editions else "disabled",
                )

    def _export_managed_vehicle_package(self, edition: str) -> None:
        """Compatibility route from audited packages into guided Quick Import."""
        if self.package_source is None or self.package_scan is None:
            return
        game = next(
            (path for path in self.installation_roots if path.is_dir()), None,
        )
        if game is None:
            messagebox.showerror(
                "GTA V path required",
                "Configure the matching GTA V installation before converting a "
                "vehicle package.", parent=self,
            )
            return
        self._select_workspace("quick_import")
        self.quick_import_workspace.open_source(
            self.package_source, preferred_edition=edition,
        )
        self.status.set(
            f"Quick Import · {self.package_source.name} · {edition.title()}",
        )

    def _open_console(self) -> None:
        self.console_workspace.expand_and_focus()

    def _open_help(self, topic: str = "sdk") -> None:
        self._select_workspace("help")
        self.help_workspace.show_topic(topic)

    def _open_context_help(self) -> None:
        workbench_topic = "workbench"
        workbench = getattr(self, "workbench_workspace", None)
        if workbench is not None:
            category = workbench.current_category()
            workbench_topic = {
                "vehicles": "vehicle-workbench",
                "weapons": "weapon-workbench",
                "peds": "ped-workbench",
                "maps": "map-workbench",
            }.get(category, "workbench")
        topic = {
            "linker": "sdk", "assets": "asset-viewer",
            "workbench": workbench_topic, "rpf": "rpf-explorer",
            "quick_import": "quick-import",
            "models": "model-material-workbench",
            "recipes": "package-recipes", "help": "input",
        }.get(getattr(self, "current_workspace", "linker"), "sdk")
        self._open_help(topic)

    def _open_updater(self) -> None:
        from allin1_sdk.update_ui import SdkUpdateDialog

        existing = getattr(self, "update_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        self.update_dialog = SdkUpdateDialog(
            self, close_sdk=self._close_for_update,
        )

    def _close_for_update(self) -> bool:
        """Honor active-work guards, then let the detached updater take over."""
        parent = self.master
        if not self.request_close():
            return False
        # The updater is scheduled immediately after this returns. Give that
        # call a short window before ending the hidden Tk root/main loop.
        if parent is not None:
            parent.after(150, parent.destroy)
        return True

    def request_close(self) -> bool:
        """Close the SDK only when guarded authoring work is no longer active."""
        recipes = getattr(self, "recipe_workspace", None)
        if recipes is not None and recipes.has_active_work():
            self._select_workspace("recipes")
            messagebox.showinfo(
                "Package-recipe operation still running",
                "Wait for the current inspection or export to finish before closing the SDK.",
                parent=self,
            )
            return False
        rpf = getattr(self, "rpf_workspace", None)
        if rpf is not None and rpf.has_active_work():
            self._select_workspace("rpf")
            rpf.focus_active_work()
            messagebox.showinfo(
                "Authoring operation still running",
                "Wait for the current validation, build, or dry run to finish before "
                "closing the SDK.",
                parent=self,
            )
            return False
        console = getattr(self, "console_workspace", None)
        if console is not None and console.has_active_work():
            console.expand_and_focus()
            messagebox.showinfo(
                "SDK Console command still running",
                "Wait for the current command to finish before closing the SDK.",
                parent=self,
            )
            return False
        workbench = getattr(self, "workbench_workspace", None)
        if workbench is not None and not workbench.confirm_navigation():
            self._select_workspace("workbench")
            return False
        quick_import = getattr(self, "quick_import_workspace", None)
        if quick_import is not None and not quick_import.confirm_navigation():
            self._select_workspace("quick_import")
            return False
        models = getattr(self, "model_material_workspace", None)
        if models is not None and models.has_active_work():
            self._select_workspace("models")
            models.focus_active_work()
            messagebox.showinfo(
                "Model operation still running",
                "Wait for the current model decode or render to finish before "
                "closing the SDK.", parent=self,
            )
            return False
        self.destroy()
        return True

    @classmethod
    def _workspace_label(cls, key: str) -> str:
        return next(
            (label for name, label, _shortcut in cls.NAVIGATION if name == key),
            key.replace("_", " ").title(),
        )

    def _select_workspace(self, key: str, *, remember: bool = True) -> bool:
        pages = getattr(self, "workspace_pages", {})
        if key not in pages:
            return False
        previous = getattr(self, "current_workspace", None)
        if previous == "workbench" and key != previous:
            workbench = getattr(self, "workbench_workspace", None)
            if (
                workbench is not None
                and not workbench.confirm_navigation()
            ):
                return False
        if previous == "quick_import" and key != previous:
            quick_import = getattr(self, "quick_import_workspace", None)
            if (
                quick_import is not None
                and not quick_import.confirm_navigation()
            ):
                return False
        if remember and previous and previous != key:
            self._navigation_history.append(previous)
            self._navigation_history = self._navigation_history[-20:]
        self._ensure_workspace(key)
        pages[key].tkraise()
        self.current_workspace = key
        for name, button in self.workspace_buttons.items():
            button.configure(
                style="NavSelected.TButton" if name == key else "Nav.TButton",
            )
        self._update_context_navigation()
        self.after_idle(lambda selected=key: self._focus_workspace(selected))
        return True

    def _return_target(self) -> str | None:
        current = getattr(self, "current_workspace", "linker")
        for target in reversed(self._navigation_history):
            if target in self.workspace_pages and target != current:
                return target
        return "linker" if current != "linker" else None

    def _update_context_navigation(self) -> None:
        """Expose history as one compact header link, not a sidebar row."""
        context = getattr(self, "workspace_context", None)
        if context is not None:
            context.set(self._workspace_label(self.current_workspace))
        button = getattr(self, "context_back_button", None)
        if button is None:
            return
        target = self._return_target()
        if target is None:
            button.pack_forget()
            return
        button.configure(text=f"‹ {self._workspace_label(target)}")
        if not button.winfo_manager():
            button.pack(side="right", padx=(10, 0))

    def _go_back(self) -> str:
        while self._navigation_history:
            target = self._navigation_history[-1]
            if target not in self.workspace_pages or target == self.current_workspace:
                self._navigation_history.pop()
                continue
            # Keep the history entry until navigation succeeds. In particular,
            # cancelling the workbench's unsaved-edit warning must leave both
            # the current page and its return route intact.
            if self._select_workspace(target, remember=False):
                self._navigation_history.pop()
                self._update_context_navigation()
                return "break"
            self._update_context_navigation()
            return "break"
        if getattr(self, "current_workspace", "linker") != "linker":
            self._select_workspace("linker", remember=False)
            self._update_context_navigation()
            return "break"
        self._update_context_navigation()
        return "break"

    @staticmethod
    def _focusable_descendants(widget: tk.Misc):
        for child in widget.winfo_children():
            yield child
            yield from AddonSdkDialog._focusable_descendants(child)

    def _focus_workspace(self, key: str) -> None:
        """Never leave keyboard focus trapped in a page that was just hidden."""
        page = self.workspace_pages.get(key)
        if page is None or not page.winfo_ismapped():
            return
        candidates = []
        if key == "linker":
            candidates.append(self.example_list)
        candidates.extend(self._focusable_descendants(page))
        for widget in candidates:
            if not widget.winfo_ismapped():
                continue
            try:
                if str(widget.cget("state")) == "disabled":
                    continue
            except tk.TclError:
                pass
            if isinstance(widget, (
                ttk.Entry, ttk.Treeview, ttk.Combobox, ttk.Button,
                tk.Entry, tk.Text, tk.Listbox,
            )):
                widget.focus_set()
                return

    def _cycle_workspace(self, _event: object | None = None, delta: int = 1) -> str:
        keys = [item[0] for item in self.NAVIGATION]
        current = keys.index(self.current_workspace) if self.current_workspace in keys else 0
        self._select_workspace(keys[(current + delta) % len(keys)])
        return "break"

    def _set_sidebar_visible(self, visible: bool) -> str:
        """Collapse the navigation rail without changing the active workspace."""
        visible = bool(visible)
        self.sidebar_visible.set(visible)
        if visible:
            if not self.workspace_sidebar.winfo_manager():
                self.workspace_sidebar.pack(
                    side="left", fill="y", before=self.sidebar_toggle_rail,
                )
            self.sidebar_toggle_button.configure(
                text="<",
                command=lambda: self._set_sidebar_visible(False),
            )
            self.sidebar_toggle_button.accessible_name = (
                "Hide workspace sidebar (Ctrl+B)"
            )
        else:
            self.workspace_sidebar.pack_forget()
            self.sidebar_toggle_button.configure(
                text=">",
                command=lambda: self._set_sidebar_visible(True),
            )
            self.sidebar_toggle_button.accessible_name = (
                "Show workspace sidebar (Ctrl+B)"
            )
        return "break"

    def _toggle_sidebar(self, _event: object | None = None) -> str:
        return self._set_sidebar_visible(not self.sidebar_visible.get())

    def _set_theme_mode(self) -> None:
        """Apply and persist the shared Launcher/SDK appearance preference."""

        try:
            apply_sdk_theme(self, self.theme_mode.get(), persist=True)
        except OSError as exc:
            messagebox.showerror(
                "Could not save theme",
                f"The SDK could not update the shared appearance setting:\n{exc}",
                parent=self,
            )
            self.theme_mode.set(current_theme_mode(self))

    def _sync_status_presentation(self, *_args: object) -> None:
        """Keep routine progress and errors visible without another popup."""

        label = getattr(self, "activity_status_label", None)
        indicator = getattr(self, "activity_status_indicator", None)
        if label is None or indicator is None:
            return
        presentation = shell_status_presentation(self.status.get())
        label.configure(style=presentation.label_style)
        indicator.configure(
            text=presentation.glyph, style=presentation.indicator_style,
        )

    def _ensure_workspace(self, key: str) -> ttk.Frame | None:
        """Construct a specialist workspace only when the user first opens it."""
        if key == "linker":
            return None
        existing = self._workspace_instances.get(key)
        if existing is not None:
            return existing
        page = self.workspace_pages[key]
        if key == "assets":
            from allin1_sdk.asset_viewer import AssetViewerDialog
            workspace = AssetViewerDialog(
                page, project_root=self.project_root,
                installation_roots=self.installation_roots,
                embedded=True, on_help=self._open_help,
                on_close=self._go_back,
            )
            self.asset_workspace = workspace
        elif key == "workbench":
            from allin1_sdk.workbench import WorkbenchFrame
            workspace = WorkbenchFrame(
                page, self.project_root,
                installation_roots=self.installation_roots,
                on_help=self._open_help,
                on_close=self._go_back,
                on_open_asset=self._open_workbench_asset,
            )
            workspace.pack(fill="both", expand=True)
            self.workbench_workspace = workspace
            # Compatibility aliases keep existing graph routes and integrations
            # working while the visible navigation uses one unified workspace.
            self.vehicle_workspace = workspace.vehicle_workspace
            self.weapon_workspace = workspace.weapon_workspace
            self.ped_workspace = workspace.ped_workspace
            self.map_workspace = workspace.map_workspace
        elif key == "quick_import":
            from allin1_sdk.quick_import_ui import QuickImportFrame
            workspace = QuickImportFrame(
                page, self.project_root,
                installation_roots=self.installation_roots,
                on_help=self._open_help,
                on_open_workbench=self._open_quick_import_workbench,
                on_open_launcher=self._open_quick_import_launcher,
            )
            workspace.pack(fill="both", expand=True)
            self.quick_import_workspace = workspace
        elif key == "models":
            from allin1_sdk.model_material_workbench import ModelMaterialWorkbenchFrame
            workspace = ModelMaterialWorkbenchFrame(
                page, self.project_root,
                installation_roots=self.installation_roots,
                on_help=self._open_help, on_close=self._go_back,
                on_open_asset=self._open_workbench_asset,
            )
            workspace.pack(fill="both", expand=True)
            self.model_material_workspace = workspace
        elif key == "rpf":
            from allin1_sdk.rpf_explorer import RpfExplorerDialog
            workspace = RpfExplorerDialog(
                page, self.project_root,
                installation_roots=self.installation_roots,
                embedded=True, on_help=self._open_help,
                on_close=self._go_back,
                on_open_asset=self._open_graph_asset,
                on_open_vehicle=self._open_graph_vehicle,
            )
            self.rpf_workspace = workspace
        elif key == "recipes":
            from allin1_sdk.oiv_workbench_ui import OivWorkbenchFrame
            workspace = OivWorkbenchFrame(
                page, self.project_root,
                installation_roots=self.installation_roots,
                on_help=self._open_help,
                on_close=self._go_back,
            )
            self.recipe_workspace = workspace
        elif key == "help":
            from allin1_sdk.help_center import HelpCenterDialog
            workspace = HelpCenterDialog(
                page, initial_topic="sdk", embedded=True,
            )
            self.help_workspace = workspace
        else:  # Defensive guard for future navigation entries.
            return None
        if not workspace.winfo_manager():
            workspace.pack(fill="both", expand=True)
        apply_native_widget_theme(workspace, current_effective_theme(self))
        self._workspace_instances[key] = workspace
        return workspace

    def _build(self) -> None:
        self._build_menu()
        outer = ttk.Frame(
            self, padding=(16, 14, 16, 12), width=1, height=1,
        )
        # The header and hidden workspaces have intentionally generous
        # preferred widths. Do not let those requests keep the client at its
        # previous large geometry when the user shrinks the top-level window.
        outer.pack_propagate(False)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(
            outer, background=SURFACE_BACKGROUND, padx=0, pady=0,
        )
        header.pack(fill="x", pady=(0, 8))
        self._logo_photo = load_sdk_banner_logo(
            self, self.project_root, maximum=(180, 88),
        )
        if self._logo_photo is not None:
            tk.Label(
                header, image=self._logo_photo,
                background=SURFACE_BACKGROUND, borderwidth=0,
            ).pack(side="left", padx=(0, 16), anchor="center")

        # Reserve the fixed actions before packing the expanding heading so
        # the version and support affordances remain visible at minimum width.
        header_actions = tk.Frame(header, background=SURFACE_BACKGROUND)
        header_actions.pack(side="right", padx=(18, 4), fill="y")
        self.version_badge = tk.Label(
            header_actions, text=f"v{__version__}",
            background=BRAND_DEEP_GREEN, foreground="white",
            font=("Segoe UI Semibold", 10), padx=12, pady=5,
        )
        self.version_badge.pack(anchor="e")
        support_url = "https://buymeacoffee.com/minionenjoyer"
        self.support_button = tk.Label(
            header_actions, text="Support ALLIN1 ↗",
            background=SURFACE_BACKGROUND, foreground=BRAND_DEEP_GREEN,
            cursor="hand2", takefocus=True,
            font=("Segoe UI Semibold", 10, "underline"),
        )
        self.support_button.pack(anchor="e", pady=(10, 0))
        self.support_button.bind(
            "<Button-1>", lambda _event: webbrowser.open(support_url),
        )
        self.support_button.bind(
            "<Return>", lambda _event: webbrowser.open(support_url),
        )
        self.support_button.bind(
            "<space>", lambda _event: webbrowser.open(support_url),
        )

        header_text = tk.Frame(header, background=SURFACE_BACKGROUND)
        header_text.pack(side="left", fill="x", expand=True, anchor="center")
        header_title = tk.Frame(header_text, background=SURFACE_BACKGROUND)
        header_title.pack(fill="x")
        tk.Label(
            header_title, text="ALLIN1 · GTA V SDK",
            background=SURFACE_BACKGROUND, foreground="#173d32",
            font=("Segoe UI Semibold", 18),
        ).pack(side="left", anchor="w")
        self.workspace_context = tk.StringVar(value="Package Linker")
        tk.Label(
            header_title, textvariable=self.workspace_context,
            background=SURFACE_BACKGROUND, foreground="#52635c",
            font=("Segoe UI Semibold", 9),
        ).pack(side="right", padx=(8, 0))
        tk.Label(
            header_title, text="WORKSPACE",
            background=SURFACE_BACKGROUND, foreground="#76847e",
            font=("Segoe UI Semibold", 8),
        ).pack(side="right", padx=(12, 0))
        self.context_back_button = ttk.Button(
            header_title, text="", style="HeaderLink.TButton", cursor="hand2",
            command=self._go_back,
        )
        tk.Label(
            header_text,
            text=(
                "Developer workspace for package integration, native assets, "
                "archive inspection, and guarded authoring."
            ),
            background=SURFACE_BACKGROUND, foreground="#52635c",
            font=("Segoe UI", 10), justify="left",
        ).pack(anchor="w", pady=(3, 0))

        shell = ttk.Frame(outer)
        shell.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading SDK packages…")
        console_host = ttk.Frame(shell, style="Surface.TFrame")
        console_host.pack(side="bottom", fill="x", pady=(5, 0))
        activity = ttk.Frame(shell, style="StatusBar.TFrame", padding=(9, 5))
        activity.pack(side="bottom", fill="x")
        ttk.Label(
            activity, text="ACTIVITY", style="FieldLabel.TLabel",
            background=SURFACE_BACKGROUND,
        ).pack(side="left", padx=(0, 8))
        self.activity_status_indicator = ttk.Label(
            activity, text="◌", style="ActivityDot.Busy.TLabel",
        )
        self.activity_status_indicator.pack(side="left", padx=(0, 6))
        self.activity_status_label = ttk.Label(
            activity, textvariable=self.status, style="Activity.Busy.TLabel",
            anchor="w", width=1,
        )
        self.activity_status_label.pack(side="left", fill="x", expand=True)
        ttk.Label(
            activity, text="F1 help  ·  Ctrl+` console",
            style="StatusHint.TLabel",
        ).pack(side="right", padx=(12, 0))
        self.status.trace_add("write", self._sync_status_presentation)
        self._sync_status_presentation()
        content_shell = ttk.Frame(shell)
        content_shell.pack(fill="both", expand=True)
        self.navigation_shell = ttk.Frame(
            content_shell, padding=0,
        )
        self.navigation_shell.pack(side="left", fill="y")
        sidebar = ttk.Frame(
            self.navigation_shell, style="Surface.TFrame", padding=(8, 12),
        )
        self.workspace_sidebar = sidebar
        sidebar.pack(side="left", fill="y")
        self.sidebar_toggle_rail = tk.Frame(
            self.navigation_shell, width=16, background="#d5ded9",
            highlightthickness=0, borderwidth=0, cursor="hand2",
        )
        self.sidebar_toggle_rail.pack(side="left", fill="y")
        self.sidebar_toggle_rail.pack_propagate(False)
        self.sidebar_divider = tk.Frame(
            self.sidebar_toggle_rail, width=1, background="#aebdb5",
            highlightthickness=0, borderwidth=0,
        )
        self.sidebar_divider.place(relx=0.5, y=0, relheight=1, anchor="n")
        self.sidebar_toggle_button = tk.Button(
            self.sidebar_toggle_rail, text="<",
            background="#1f7f42", foreground="#ffffff",
            activebackground="#176b36", activeforeground="#ffffff",
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground="#1f7f42", highlightcolor="#ffffff",
            padx=0, pady=0, font=("Segoe UI Semibold", 9), cursor="hand2",
            takefocus=True,
            command=lambda: self._set_sidebar_visible(False),
        )
        self.sidebar_toggle_button.accessible_name = (
            "Hide workspace sidebar (Ctrl+B)"
        )
        self.sidebar_toggle_button.place(
            relx=0.5, rely=0.5, anchor="center", width=16, height=30,
        )
        for toggle_target in (self.sidebar_toggle_rail, self.sidebar_divider):
            toggle_target.bind("<Button-1>", self._toggle_sidebar)
        ttk.Label(
            sidebar, text="WORKSPACES", style="FieldLabel.TLabel",
            background=SURFACE_BACKGROUND,
        ).pack(anchor="w", padx=10, pady=(0, 7))
        workspace = ttk.Frame(
            content_shell, style="Workspace.TFrame", padding=(12, 0, 0, 0),
        )
        self.workspace_host = workspace
        workspace.pack(side="left", fill="both", expand=True)
        workspace.rowconfigure(0, weight=1)
        workspace.columnconfigure(0, weight=1)
        # Hidden workspaces must not force the main window wider than the
        # user's selected size. The active page still fills the allotted area.
        workspace.grid_propagate(False)

        linker_page = ttk.Frame(workspace)
        assets_page = ttk.Frame(workspace)
        workbench_page = ttk.Frame(workspace)
        quick_import_page = ttk.Frame(workspace)
        models_page = ttk.Frame(workspace)
        rpf_page = ttk.Frame(workspace)
        recipes_page = ttk.Frame(workspace)
        help_page = ttk.Frame(workspace)
        self.workspace_pages = {
            "linker": linker_page,
            "assets": assets_page,
            "workbench": workbench_page,
            "quick_import": quick_import_page,
            "models": models_page,
            "rpf": rpf_page,
            "recipes": recipes_page,
            "help": help_page,
        }
        self.workspace_buttons: dict[str, ttk.Button] = {}
        for key, label, shortcut in self.NAVIGATION:
            self.workspace_pages[key].grid(row=0, column=0, sticky="nsew")
            if key in {"rpf", "help"}:
                ttk.Separator(sidebar).pack(fill="x", padx=8, pady=(7, 5))
            button = ttk.Button(
                sidebar, text=label, style="Nav.TButton", width=18,
                command=lambda selected=key: self._select_workspace(selected),
            )
            button.pack(fill="x", pady=1)
            self.workspace_buttons[key] = button
            key_name = shortcut.removeprefix("Ctrl+").casefold()
            self.bind(
                f"<Control-Key-{key_name}>",
                lambda _event, selected=key: (
                    self._select_workspace(selected), "break"
                )[1],
            )
        ttk.Label(
            sidebar, text="Ctrl+Tab switch  ·  Ctrl+B hide",
            style="StatusHint.TLabel", background=SURFACE_BACKGROUND,
            wraplength=175, justify="left",
        ).pack(side="bottom", anchor="w", padx=10, pady=(10, 0))
        self.bind("<Alt-Left>", lambda _event: self._go_back())
        self.bind("<Control-b>", self._toggle_sidebar)
        self.bind("<Control-Tab>", self._cycle_workspace)
        self.bind(
            "<Control-Shift-Tab>",
            lambda event: self._cycle_workspace(event, -1),
        )

        linker_heading = ttk.Frame(linker_page)
        linker_heading.pack(fill="x", pady=(0, 8))
        ttk.Label(
            linker_heading, text="Package Linker", style="PageTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            linker_heading,
            text=(
                "Open a package or product workspace, trace its integration "
                "contract, then export reviewable evidence."
            ),
            style="PageIntro.TLabel", wraplength=860, justify="left",
        ).pack(anchor="w", pady=(2, 0))

        toolbar = ttk.Frame(
            linker_page, style="Surface.TFrame", padding=(8, 7),
        )
        toolbar.pack(fill="x", pady=(0, 10))
        content_menu = self._make_content_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Import or audit package", menu=content_menu,
            style="Accent.TMenubutton",
        ).pack(side="left")
        self.review_menu = self._make_review_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Inspect or export", menu=self.review_menu,
            style="Quiet.TMenubutton",
        ).pack(side="left", padx=(7, 0))
        intelligence_menu = self._make_intelligence_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Package tools", menu=intelligence_menu,
            style="Quiet.TMenubutton",
        ).pack(side="left", padx=(7, 0))
        self.refresh_audit_button = ttk.Button(
            toolbar, text="Refresh audit", command=self._refresh_audit,
            style="Quiet.TButton", state="disabled",
        )
        panes = ttk.Panedwindow(linker_page, orient="horizontal")
        self.linker_panes = panes
        panes.pack(fill="both", expand=True)

        side_panes = CollapsibleSidePanes(
            panes, left_width=250, center_width=430, right_width=440,
            left_weight=2, center_weight=4, right_weight=5,
            left_label="Packages", right_label="Field inspector",
        )
        self.linker_side_panes = side_panes
        examples = ttk.LabelFrame(
            side_panes.left_host, text="Packages", padding=8,
        )
        graph = ttk.LabelFrame(
            side_panes.center_host, text="Package links", padding=8,
        )
        inspector = ttk.LabelFrame(
            side_panes.right_host, text="Field inspector", padding=8,
        )
        self.linker_sections = (examples, graph, inspector)
        # Package names and compatibility tags need enough width to scan without
        # forcing users to resize the first pane every session.
        side_panes.set_contents(examples, graph, inspector)

        package_filter_row = ttk.Frame(examples)
        package_filter_row.pack(fill="x", pady=(0, 7))
        ttk.Label(package_filter_row, text="Filter").pack(side="left")
        self.manifest_filter = tk.StringVar()
        self.manifest_filter_entry = ttk.Entry(
            package_filter_row, textvariable=self.manifest_filter,
        )
        self.manifest_filter_entry.pack(
            side="left", fill="x", expand=True, padx=(6, 4),
        )
        ttk.Button(
            package_filter_row, text="Clear",
            command=lambda: self.manifest_filter.set(""),
        ).pack(side="left")
        self.manifest_filter.trace_add(
            "write", lambda *_args: self._populate_manifest_list(),
        )
        package_tree_host = ttk.Frame(examples)
        package_tree_host.pack(fill="both", expand=True)
        self.example_list = ttk.Treeview(
            package_tree_host, columns=("package", "edition", "nodes"),
            show="tree headings",
            selectmode="browse", height=16,
        )
        self.example_list.heading("#0", text="Status")
        self.example_list.heading("package", text="Package")
        self.example_list.heading("edition", text="Edition")
        self.example_list.heading("nodes", text="Nodes")
        self.example_list.column("#0", width=78, minwidth=68, stretch=False)
        self.example_list.column("package", width=205, minwidth=140, stretch=True)
        self.example_list.column("edition", width=90, minwidth=72, stretch=False)
        self.example_list.column(
            "nodes", width=58, minwidth=50, stretch=False, anchor="e",
        )
        example_scroll = ttk.Scrollbar(
            package_tree_host, orient="vertical", command=self.example_list.yview,
        )
        example_xscroll = ttk.Scrollbar(
            package_tree_host, orient="horizontal", command=self.example_list.xview,
        )
        self.example_list.configure(
            yscrollcommand=example_scroll.set, xscrollcommand=example_xscroll.set,
        )
        self.example_list.grid(row=0, column=0, sticky="nsew")
        example_scroll.grid(row=0, column=1, sticky="ns")
        example_xscroll.grid(row=1, column=0, sticky="ew")
        package_tree_host.rowconfigure(0, weight=1)
        package_tree_host.columnconfigure(0, weight=1)
        self.example_list.bind("<<TreeviewSelect>>", self._select_example)

        self.graph = ttk.Treeview(
            graph, columns=("type", "status"), show="tree headings", selectmode="browse",
        )
        self.graph.heading("#0", text="Field / integration")
        self.graph.heading("type", text="Type")
        self.graph.heading("status", text="Status")
        self.graph.column("#0", width=300, stretch=True)
        self.graph.column("type", width=115, stretch=False)
        self.graph.column("status", width=95, stretch=False)
        graph_scroll = ttk.Scrollbar(
            graph, orient="vertical", command=self.graph.yview,
        )
        graph_xscroll = ttk.Scrollbar(
            graph, orient="horizontal", command=self.graph.xview,
        )
        self.graph.configure(
            yscrollcommand=graph_scroll.set, xscrollcommand=graph_xscroll.set,
        )
        self.graph.grid(row=0, column=0, sticky="nsew")
        graph_scroll.grid(row=0, column=1, sticky="ns")
        graph_xscroll.grid(row=1, column=0, sticky="ew")
        graph.rowconfigure(0, weight=1)
        graph.columnconfigure(0, weight=1)
        self.graph.bind("<<TreeviewSelect>>", self._inspect_selection)

        self.heading = tk.StringVar(value="Select an integration node")
        ttk.Label(
            inspector, textvariable=self.heading,
            font=("Segoe UI Semibold", 12), foreground="#1f7f42",
        ).pack(anchor="w")
        detail_row = ttk.Frame(inspector)
        detail_row.pack(fill="x", pady=(6, 8))
        self.details = tk.Text(
            detail_row, height=6, wrap="word", relief="flat",
            background="#f4f7f5", foreground="#26332e", padx=7, pady=7,
        )
        detail_scroll = ttk.Scrollbar(
            detail_row, orient="vertical", command=self.details.yview,
        )
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.details.configure(state="disabled")

        self.fields = ttk.Treeview(
            inspector, columns=("value",), show="tree headings", height=12,
        )
        self.fields.heading("#0", text="Field")
        self.fields.heading("value", text="Linked value")
        self.fields.column("#0", width=140, stretch=False)
        self.fields.column("value", width=350, stretch=True)
        field_row = ttk.Frame(inspector)
        field_row.pack(fill="both", expand=True)
        field_scroll = ttk.Scrollbar(
            field_row, orient="vertical", command=self.fields.yview,
        )
        field_xscroll = ttk.Scrollbar(
            field_row, orient="horizontal", command=self.fields.xview,
        )
        self.fields.configure(
            yscrollcommand=field_scroll.set, xscrollcommand=field_xscroll.set,
        )
        self.fields.grid(in_=field_row, row=0, column=0, sticky="nsew")
        field_scroll.grid(row=0, column=1, sticky="ns")
        field_xscroll.grid(row=1, column=0, sticky="ew")
        field_row.rowconfigure(0, weight=1)
        field_row.columnconfigure(0, weight=1)
        self.fields.bind("<<TreeviewSelect>>", self._explain_field)

        self.field_help = tk.StringVar(
            value="Select a field to see why GTA V needs it."
        )
        ttk.Label(
            inspector, textvariable=self.field_help, wraplength=440,
            justify="left", foreground="#52635c",
        ).pack(fill="x", pady=(8, 0))
        inspector_actions = ttk.Frame(inspector)
        inspector_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(
            inspector_actions, text="Copy selected value",
            command=self._copy_selected_field,
        ).pack(side="left")
        ttk.Button(
            inspector_actions, text="Open source", command=self._open_source,
        ).pack(side="left", padx=(6, 0))

        self._workspace_instances: dict[str, ttk.Frame] = {}
        self.console_workspace = SdkConsoleDialog(
            console_host, self.project_root, embedded=True, docked=True,
        )
        self.current_workspace = "linker"
        self._select_workspace("linker")

    def _load_examples(self) -> None:
        try:
            self.manifests = self.catalog.discover(
                self.installation_roots, include_external=True,
            )
        except (OSError, ValueError) as exc:
            self.status.set(f"Could not load SDK packages · {exc}")
            return
        self._populate_manifest_list(select_first=True)
        if not self.manifests:
            self.status.set("No SDK examples, imports, packages, or receipts found")

    def _populate_manifest_list(self, *, select_first: bool = False) -> None:
        if not hasattr(self, "example_list"):
            return
        previous = self.example_list.selection()
        previous_id = previous[0] if previous else None
        query = (
            self.manifest_filter.get().strip().casefold()
            if hasattr(self, "manifest_filter") else ""
        )
        self.example_list.delete(*self.example_list.get_children())
        visible: list[str] = []
        for index, manifest in enumerate(self.manifests):
            searchable = " ".join((
                manifest.name, manifest.addon_id, manifest.catalog_state,
                manifest.catalog_origin, *manifest.editions,
            )).casefold()
            if query and query not in searchable:
                continue
            iid = str(index)
            visible.append(iid)
            self.example_list.insert(
                "", "end", iid=iid,
                text=self._catalog_state_label(manifest.catalog_state),
                values=(
                    manifest.name,
                    " / ".join(value.title() for value in manifest.editions),
                    len(manifest.nodes),
                ),
            )
        target = previous_id if previous_id in visible else (visible[0] if visible else None)
        if target is not None and (select_first or previous_id is not None):
            self.example_list.selection_set(target)
            self.example_list.focus(target)
            self.example_list.see(target)
            self._show_manifest(self.manifests[int(target)])
        elif query and not visible:
            self.status.set(f'No packages match “{self.manifest_filter.get().strip()}”')

    def _select_example(self, _event: object | None = None) -> None:
        selection = self.example_list.selection()
        if selection:
            self._show_manifest(self.manifests[int(selection[0])])

    @staticmethod
    def _catalog_state_label(value: str) -> str:
        normalized = value.casefold()
        for prefix, label in (
            ("installed", "Installed"),
            ("imported", "Draft"),
            ("available", "Available"),
            ("built-in", "Example"),
        ):
            if normalized.startswith(prefix):
                return label
        return value.replace("-", " ").title()

    def _open_manifest(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open ALLIN1 manifest or product workspace",
            filetypes=(
                ("ALLIN1 manifests", "*.json"),
                ("Add-on manifest", "addon.json"),
            ),
        )
        if not selected:
            return
        self.open_manifest_path(selected)

    def open_manifest_path(
        self, selected: str | Path, *, remember: bool = True,
    ) -> None:
        """Open a validated add-on or product workspace in the existing linker."""
        try:
            manifest = AddonManifest.load(selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid ALLIN1 manifest", str(exc), parent=self)
            return
        if remember:
            try:
                manifest = self.catalog.remember(selected)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not remember manifest", str(exc), parent=self)
                return
        self._append_manifest(manifest)

    def _append_manifest(self, manifest: AddonManifest) -> None:
        identity = manifest.catalog_identity
        index = next((
            number for number, existing in enumerate(self.manifests)
            if existing.catalog_identity == identity
        ), -1)
        if index >= 0:
            self.manifests[index] = manifest
        else:
            self.manifests.append(manifest)
            index = len(self.manifests) - 1
        self.manifest_filter.set("")
        self._populate_manifest_list()
        self.example_list.selection_set(str(index))
        self.example_list.focus(str(index))
        self.example_list.see(str(index))
        self._show_manifest(manifest)

    def _import_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a loose DLC or add-on package folder",
        )
        if selected:
            self._import_package(Path(selected))

    def _import_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select an OIV, ZIP, RAR, or 7z package",
            filetypes=(("GTA package", "*.oiv *.zip *.rar *.7z"),
                       ("All files", "*.*")),
        )
        if selected:
            self._import_package(Path(selected))

    def _import_rpf(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open a GTA V DLC RPF",
            filetypes=(("GTA V RPF", "*.rpf"), ("All files", "*.*")),
        )
        if not selected:
            return
        source = Path(selected).resolve()
        try:
            scan = self._package_inspector().inspect(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF inspection failed", str(exc), parent=self)
            return
        self.package_source = source
        self.package_scan = scan
        has_content = bool(
            scan.vehicles or scan.weapons or scan.peds
            or scan.weapon_enhancements or scan.scripted_weapon_systems
        )
        has_models = any(
            entry.suffix in {".ydr", ".ydd", ".yft"}
            for entry in scan.workbench_entries
        )
        self._set_package_actions(assets=True, rpfs=True, workbench=has_content)
        if has_content:
            self._select_workspace("workbench")
            self.workbench_workspace.open_source(source, scan)
        elif has_models:
            self._select_workspace("models")
            self.model_material_workspace.open_source(source, scan)
        else:
            self._select_workspace("rpf")
            self.rpf_workspace.open_archive(source)
        self.status.set(
            f"Direct RPF · {source.name} · "
            f"{len(scan.workbench_entries):,} indexed files"
        )

    def _audit_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a folder containing test/mod packages",
        )
        if not selected:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Save SDK package-folder audit",
            initialdir=selected, initialfile="allin1-sdk-audit.md",
            defaultextension=".md", filetypes=(("Markdown", "*.md"),),
        )
        if not destination:
            return
        self.status.set("Auditing package folder…")
        self.update_idletasks()
        completed = run_hidden(
            [
                sys.executable, "-m", "allin1_sdk.cli", "audit-folder",
                selected, "-o", destination,
            ],
            cwd=self.project_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "Unknown audit error").strip()
            self.status.set("Package-folder audit failed")
            messagebox.showerror("Audit failed", detail, parent=self)
            return
        self.status.set(
            f"Package audit written · {Path(destination).resolve()}",
        )

    def _import_package(self, source: Path) -> None:
        try:
            scan = self._package_inspector().inspect(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Package scan failed", str(exc), parent=self)
            return
        if not scan.valid:
            messagebox.showerror(
                "Unsafe or incomplete package",
                self._scan_summary(scan), parent=self,
            )
            return

        if source.is_dir():
            initial_dir = source
            initial_file = "addon.json"
        else:
            initial_dir = source.parent
            initial_file = f"{source.stem}.addon.json"
        selected = filedialog.asksaveasfilename(
            parent=self, title="Save generated SDK draft",
            initialdir=initial_dir, initialfile=initial_file,
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not selected:
            return
        destination = Path(selected).resolve()
        if source.is_dir() and destination.parent != source.resolve():
            messagebox.showerror(
                "Draft must stay with the folder",
                "A loose-folder draft must be saved at the package root so its "
                "relative source links remain valid.", parent=self,
            )
            return

        try:
            draft = AddonDraftBuilder().build(scan)
            written = draft.write(destination)
            manifest = self.catalog.remember(
                written, source_root=source if source.is_dir() else written.parent,
                package_source=source,
            )
            report = self.linker.link(manifest)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Draft generation failed", str(exc), parent=self)
            return
        self._append_manifest(manifest)
        self.status.set(
            f"SDK draft written · {written} · {report.error_count} errors · "
            f"{report.warning_count} warnings",
        )
        self.package_source = source
        self.package_scan = scan
        self._set_package_actions(
            assets=True,
            rpfs=any(entry.suffix == ".rpf" for entry in scan.entries),
            workbench=bool(
                scan.vehicles or scan.weapons or scan.peds
                or scan.weapon_enhancements or scan.scripted_weapon_systems
            ),
        )

    def _open_asset_viewer(self) -> None:
        self._select_workspace("assets")
        if self.package_source is not None:
            self.asset_workspace.open_source(
                self.package_source, self.package_scan,
            )

    def _open_workbench(self, category: str = "auto") -> None:
        self._select_workspace("workbench")
        if self.package_source is not None:
            opened = self.workbench_workspace.open_source(
                self.package_source, self.package_scan, category=category,
            )
            if opened:
                self.status.set(
                    f"Content Workbench · {self.package_source.name}",
                )

    def _open_quick_import_workbench(self, category: str) -> None:
        """Route a Quick Import category into the consolidated advanced tools."""
        self._select_workspace("workbench")
        self.workbench_workspace.select_category(category)
        self.status.set(
            f"Content Workbench · {category.replace('_', ' ').title()}",
        )

    def _open_quick_import_launcher(
        self, package_id: str, traffic_requested: bool,
    ) -> None:
        """Hand a prepared package to Launcher's non-mutating review route."""
        from allin1_sdk.launcher_bridge import open_launcher_packages

        open_launcher_packages(
            self.project_root,
            package_id,
            traffic_requested=traffic_requested,
        )
        self.status.set(f"Opened Launcher Packages · {package_id}")

    def _open_model_materials(self) -> None:
        """Route the current package into the integrated native model workspace."""
        self._select_workspace("models")
        if self.package_source is not None:
            self.model_material_workspace.open_source(
                self.package_source, self.package_scan,
            )
            self.status.set(
                f"Models & Materials · {self.package_source.name}",
            )

    def open_model_material_source(self, source: str | Path) -> bool:
        """Public desktop/automation route into Models & Materials."""
        try:
            resolved = Path(source).expanduser().resolve(strict=True)
        except OSError as exc:
            messagebox.showerror(
                "Could not open Models & Materials", str(exc), parent=self,
            )
            return False
        self.package_source = resolved
        self.package_scan = None
        self._select_workspace("models")
        self.model_material_workspace.open_source(resolved)
        self.status.set(f"Models & Materials · {resolved.name}")
        return True

    def _open_vehicle_workbench(self) -> None:
        """Compatibility route for existing vehicle-focused integrations."""
        self._open_workbench("vehicles")

    def open_workbench_package(
        self, source: str | Path, category: str = "auto",
    ) -> bool:
        """Load a package directly into the unified content Workbench."""
        try:
            resolved = Path(source).expanduser().resolve(strict=True)
            scan = self._package_inspector().inspect(resolved)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not open package in Workbench", str(exc), parent=self,
            )
            return False
        from allin1_sdk.map_workbench import looks_like_map_project

        available = {
            "vehicles": bool(scan.vehicles),
            "weapons": bool(
                scan.weapons or scan.weapon_enhancements
                or scan.scripted_weapon_systems
            ),
            "peds": bool(scan.peds),
            "maps": looks_like_map_project(resolved, scan),
        }
        if category not in {"auto", *available}:
            messagebox.showerror(
                "Unknown Workbench category", f"Unsupported category: {category}",
                parent=self,
            )
            return False
        if not any(available.values()):
            messagebox.showerror(
                "No Workbench content found",
                "The selected package does not contain vehicle, weapon, ped, map, "
                "or script-driven vanilla weapon relationships.",
                parent=self,
            )
            return False
        if category != "auto" and not available[category]:
            messagebox.showerror(
                f"No {category} found",
                f"The selected package does not contain linked {category} metadata.",
                parent=self,
            )
            return False
        self.package_source = resolved
        self.package_scan = scan
        self._set_package_actions(
            assets=True,
            rpfs=any(entry.suffix == ".rpf" for entry in scan.entries),
            workbench=True,
        )
        self._select_workspace("workbench")
        opened = self.workbench_workspace.open_source(
            resolved, scan, category=category,
        )
        if opened:
            self.status.set(f"Content Workbench · {resolved.name}")
        return opened

    def open_vehicle_package(self, source: str | Path) -> bool:
        """Compatibility alias for direct vehicle Workbench launches."""
        return self.open_workbench_package(source, "vehicles")

    def open_map_project(self, descriptor: str | Path) -> bool:
        """Open an explicit declarative map project in the unified Workbench."""

        try:
            resolved = Path(descriptor).expanduser().resolve(strict=True)
        except OSError as exc:
            messagebox.showerror(
                "Could not open map project", str(exc), parent=self,
            )
            return False
        self._select_workspace("workbench")
        opened = self.workbench_workspace.open_map_project(resolved)
        if opened:
            self.status.set(f"Map Workbench · {resolved.name}")
        return opened

    def open_axle_configurator(
        self, workspace_root: str | Path, model: str | None = None,
    ) -> bool:
        """Open a vehicle authoring workspace directly in its Axles editor."""
        from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

        try:
            workspace = VehicleAuthoringWorkspace(workspace_root)
            available = tuple(item.model for item in workspace.inspect().models)
            if not available:
                raise ValueError("Vehicle authoring workspace contains no models")
            selected = model or available[0]
            canonical = next(
                (item for item in available if item.casefold() == selected.casefold()),
                None,
            )
            if canonical is None:
                raise ValueError(
                    f"Vehicle model is not present in this authoring workspace: {selected}"
                )
            scan = self._package_inspector().inspect(workspace.source)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "Could not open Axle Configurator", str(exc), parent=self,
            )
            return False
        self.package_source = workspace.source
        self.package_scan = scan
        self._set_package_actions(
            assets=True,
            rpfs=any(entry.suffix == ".rpf" for entry in scan.entries),
            workbench=True,
        )
        self._select_workspace("workbench")
        opened = self.workbench_workspace.open_source(
            workspace.source, scan, category="vehicles",
            vehicle_authoring_workspace=workspace,
        )
        if not opened:
            return False
        vehicle = self.workbench_workspace.vehicle_workspace
        if not vehicle.show_axle_configurator(canonical):
            messagebox.showerror(
                "Could not open Axle Configurator",
                f"Vehicle model could not be selected: {canonical}",
                parent=self,
            )
            return False
        self.status.set(f"Axle Configurator · {canonical} · {workspace.root.name}")
        return True

    def _open_workbench_asset(self, path: str) -> None:
        if self.package_source is None:
            return
        self._select_workspace("assets")
        if self.asset_workspace.source != self.package_source.expanduser().resolve():
            self.asset_workspace.open_source(
                self.package_source, self.package_scan,
            )
        self.asset_workspace.select_asset(path)

    def _open_vehicle_asset(self, path: str) -> None:
        """Compatibility alias retained for external vehicle routes."""
        self._open_workbench_asset(path)

    def _open_graph_asset(self, source: str, root: str) -> None:
        source_path = Path(source).expanduser().resolve()
        root_path = Path(root).expanduser().resolve()
        try:
            relative = source_path.relative_to(root_path).as_posix()
            scan = self._package_inspector().inspect(root_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not route graph asset", str(exc), parent=self,
            )
            return
        self._select_workspace("assets")
        self.asset_workspace.open_source(root_path, scan)
        if not self.asset_workspace.select_asset(relative):
            messagebox.showerror(
                "Asset not found",
                "The selected graph source was not found in its retained package root.",
                parent=self,
            )

    def _open_graph_vehicle(self, root: str, model: str) -> None:
        root_path = Path(root).expanduser().resolve()
        try:
            scan = self._package_inspector().inspect(root_path)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Could not route vehicle system", str(exc), parent=self,
            )
            return
        self._select_workspace("workbench")
        self.workbench_workspace.open_source(root_path, scan, category="vehicles")
        self.workbench_workspace.select_vehicle(model)

    def _open_rpf_explorer(self) -> None:
        self._select_workspace("rpf")

    def _open_oiv_workbench(self) -> None:
        self._select_workspace("recipes")

    def _inspect_package_rpfs(self) -> None:
        if self.package_source is None:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a folder for read-only RPF reports",
        )
        if not destination:
            return
        self.status.set("Inspecting package RPFs…")
        self.update_idletasks()
        completed = run_hidden(
            [
                sys.executable, "-m", "allin1_sdk.cli",
                "inspect-package-rpfs", str(self.package_source),
                "-o", destination,
            ],
            cwd=self.project_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "Unknown RPF error").strip()
            self.status.set("Package RPF inspection failed")
            messagebox.showerror("RPF inspection failed", detail, parent=self)
            return
        self.status.set(
            f"Package RPF reports written · {Path(destination).resolve()}",
        )

    def _inventory_dlc(self) -> None:
        if not self.installation_roots:
            messagebox.showerror(
                "DLC inventory", "Configure a Legacy or Enhanced GTA V folder first.",
                parent=self,
            )
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a folder for DLC inventory reports",
        )
        if not destination:
            return
        try:
            from allin1_sdk.dlc_inventory import DlcInventory
            inventory = DlcInventory(self.project_root)
            reports = []
            for game in self.installation_roots:
                report = inventory.scan(game)
                output = Path(destination) / f"{report.edition.casefold()}-dlc-inventory.md"
                reports.append(report.write(output))
        except (OSError, ValueError) as exc:
            messagebox.showerror("DLC inventory failed", str(exc), parent=self)
            return
        self.status.set(
            f"Wrote {len(reports)} DLC inventory report(s) · "
            f"{Path(destination).resolve()}",
        )

    def _compile_vehicle_data(self) -> None:
        source = self.package_source
        if source is None:
            selected = filedialog.askopenfilename(
                parent=self, title="Select a vehicle package archive",
                filetypes=(("GTA package", "*.oiv *.zip *.rar *.7z"),
                           ("All files", "*.*")),
            )
            if not selected:
                return
            source = Path(selected)
        destination = filedialog.askdirectory(
            parent=self, title="Select a folder for compiled vehicle data",
        )
        if not destination:
            return
        try:
            from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler
            report = RageVehicleDataCompiler().compile(source)
            report.write_bundle(destination)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Vehicle compilation failed", str(exc), parent=self)
            return
        self.status.set(
            f"Compiled {len(report.vehicles)} vehicle(s): "
            f"{report.error_count} errors, {report.warning_count} warnings · "
            f"{Path(destination).resolve()}",
        )

    def _compare_meta(self) -> None:
        before = filedialog.askopenfilename(
            parent=self, title="Select original META/XML",
            filetypes=(("GTA metadata", "*.meta *.xml *.ymt"), ("All files", "*.*")),
        )
        if not before:
            return
        after = filedialog.askopenfilename(
            parent=self, title="Select modified META/XML",
            filetypes=(("GTA metadata", "*.meta *.xml *.ymt"), ("All files", "*.*")),
        )
        if not after:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save structured metadata diff",
            initialfile="meta-structured-diff.md", defaultextension=".md",
            filetypes=(("Markdown", "*.md"), ("JSON", "*.json")),
        )
        if not output:
            return
        try:
            report = diff_meta(before, after)
            written = report.write(output)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("META/XML comparison failed", str(exc), parent=self)
            return
        self.status.set(
            f"Structured META/XML diff · {len(report.changes)} changes · {written}",
        )

    def _validate_meta_roundtrip(self) -> None:
        source = filedialog.askopenfilename(
            parent=self, title="Select authored META/XML",
            filetypes=(("GTA metadata", "*.meta *.xml *.ymt"), ("All files", "*.*")),
        )
        if not source:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save META/XML round-trip report",
            initialfile=f"{Path(source).stem}-roundtrip.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            result = validate_meta_roundtrip(source)
            Path(output).write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("META/XML round trip failed", str(exc), parent=self)
            return
        self.status.set(
            "META/XML round trip is semantically equivalent · "
            f"{Path(output).resolve()}",
        )

    @staticmethod
    def _scan_summary(scan: PackageScan) -> str:
        lines = [
            f"Scanned {len(scan.entries):,} files ({scan.total_bytes:,} bytes).",
            f"Discovered {len(scan.weapons)} weapons, {len(scan.ammo)} ammo records, "
            f"{len(scan.weapon_components)} components, "
            f"{len(scan.animation_weapons)} animation mappings, and "
            f"{len(scan.shop_weapons)} shop mappings.",
            f"Discovered {len(scan.vehicles)} vehicles, {len(scan.handlings)} handling "
            f"records, {len(scan.variations)} variation records, and "
            f"{len(scan.kits)} tuning kits.",
            f"Discovered {len(scan.peds)} ped definitions and their visible streamed "
            "asset relationships.",
            f"Package shapes: {', '.join(scan.package_kinds)}; "
            f"{len(scan.binary_plugins)} compiled plug-ins, "
            f"{len(scan.replacement_assets)} replacement assets, and "
            f"{len(scan.shader_assets)} shaders.",
            f"Edition tag: {scan.edition_tag}.",
            f"Findings: {scan.error_count} errors and {scan.warning_count} warnings.",
        ]
        for finding in scan.findings[:10]:
            location = f" [{finding.path}]" if finding.path else ""
            lines.append(
                f"- {finding.severity.upper()} {finding.code}{location}: "
                f"{finding.message}"
            )
        if len(scan.findings) > 10:
            lines.append(f"- …and {len(scan.findings) - 10} more findings.")
        return "\n".join(lines)

    def _insert_api_contracts(self, contracts: RuntimeContractReport) -> None:
        """Render the canonical read-only API report in the existing graph."""
        call_count = sum(len(item.api_calls) for item in contracts.packages)
        verified_calls = sum(
            call.status == "verified"
            for item in contracts.packages for call in item.api_calls
        )
        root_id = "api:root"
        self.graph.insert(
            "", "end", iid=root_id, text="API contracts",
            values=(
                f"{len(contracts.hosts)} host · {len(contracts.packages)} packages",
                f"{verified_calls}/{call_count} calls",
            ),
            open=True,
        )
        self._selection[root_id] = contracts

        for host in contracts.hosts:
            host_id = f"api:host:{host.component_id}"
            self.graph.insert(
                root_id, "end", iid=host_id,
                text=f"{host.public_type} — API v{host.api_version}",
                values=(host.component_id, host.status), open=False,
            )
            self._selection[host_id] = host
            members_id = f"{host_id}:members"
            self.graph.insert(
                host_id, "end", iid=members_id, text="Public members",
                values=("host surface", str(len(host.members))), open=False,
            )
            self._selection[members_id] = host
            for index, member in enumerate(host.members, start=1):
                member_id = f"{members_id}:{index}:{member.name}"
                self.graph.insert(
                    members_id, "end", iid=member_id, text=member.name,
                    values=(member.kind, member.status),
                )
                self._selection[member_id] = member

        for package in contracts.packages:
            package_id = f"api:package:{package.component_id}"
            api_label = (
                f"API v{package.api_version}"
                if package.api_version is not None else "API unresolved"
            )
            self.graph.insert(
                root_id, "end", iid=package_id,
                text=package.package_id or package.component_id,
                values=(api_label, package.status), open=False,
            )
            self._selection[package_id] = package

            groups: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
                ("assemblies", "Runtime assemblies", "Runtime assembly",
                 package.runtime_assemblies,
                 "Receipt-owned runtime assembly declared by the content package."),
                ("entry-points", "Entry points", "Entry point", package.entry_points,
                 "Runtime type declared by a receipt-owned assembly."),
                ("capabilities", "Capabilities", "Capability", package.capabilities,
                 "Capability declared by the content package."),
                ("settings", "Settings", "Setting", package.settings,
                 "Typed setting declared by the content package."),
                ("interfaces", "Interfaces", "Interface", package.interfaces,
                 "Host API interface found in bounded package source."),
                ("requirements", "Package requirements", "Package requirement",
                 package.requirements,
                 "Managed package dependency required by this content package."),
                ("workbench", "Workbench relationships", "Workbench relationship",
                 package.workbench_relationships,
                 "Authored relationship connecting runtime behavior to Workbench evidence."),
                ("projects", "Project references", "Project reference",
                 package.project_references,
                 "Bounded project reference from the consumer to the shared runtime."),
            )
            for key, label, field_label, values, description in groups:
                if not values:
                    continue
                group_id = f"{package_id}:{key}"
                self.graph.insert(
                    package_id, "end", iid=group_id, text=label,
                    values=("contract details", str(len(values))), open=False,
                )
                self._selection[group_id] = package
                for index, value in enumerate(values, start=1):
                    source = package.manifest
                    if key == "entry-points" and index <= len(
                        package.entry_point_sources
                    ):
                        source = package.entry_point_sources[index - 1]
                    detail = _ApiContractDetail(
                        heading=value,
                        description=description,
                        source=source,
                        line=None,
                        fields=(
                            ("Package", package.package_id or package.component_id),
                            ("API provider", package.provider_component_id),
                            (field_label, value),
                            ("Contract status", package.status),
                        ),
                    )
                    detail_id = f"{group_id}:{index}"
                    self.graph.insert(
                        group_id, "end", iid=detail_id, text=value,
                        values=(label.casefold(), package.status),
                    )
                    self._selection[detail_id] = detail

            if package.api_calls:
                calls_id = f"{package_id}:calls"
                self.graph.insert(
                    package_id, "end", iid=calls_id, text="API calls",
                    values=("bounded source", str(len(package.api_calls))),
                    open=False,
                )
                self._selection[calls_id] = package
                for index, call in enumerate(package.api_calls, start=1):
                    call_id = f"{calls_id}:{index}:{call.member}"
                    self.graph.insert(
                        calls_id, "end", iid=call_id, text=call.member,
                        values=(call.capability or "public", call.status),
                    )
                    self._selection[call_id] = call

    def _show_manifest(
        self, manifest: AddonManifest, *, preferred_selection: str | None = None,
    ) -> None:
        # Product workspaces are bounded source/evidence graphs, not content
        # packages. Never offer an action that would recursively scan their
        # repository root as though it were a mod payload.
        self.package_source = (
            None if manifest.is_product_workspace else manifest.package_source
        )
        self.package_scan = None
        self._set_package_actions(
            assets=(self.package_source is not None and not manifest.is_product_workspace),
            rpfs=False,
            workbench=(
                self.package_source is not None and not manifest.is_product_workspace
            ),
        )
        self.report = self.linker.link(manifest)
        self._set_audit_actions(True)
        self._selection.clear()
        self.graph.delete(*self.graph.get_children())
        if manifest.workspace_summary:
            summary = dict(manifest.workspace_summary)
            summary_id = "workspace:summary"
            self.graph.insert(
                "", "end", iid=summary_id, text="Workspace evidence",
                values=(
                    f"{summary.get('Coverage', 'n/a')} coverage",
                    f"{summary.get('Tracked files', 0)} tracked files",
                ),
                open=True,
            )
            self._selection[summary_id] = summary
        if self.report.issues:
            diagnostics_root = self.graph.insert(
                "", "end", text="Diagnostics", values=("validation", ""),
                open=True,
            )
            for index, issue in enumerate(self.report.issues, start=1):
                item_id = f"issue:{index}"
                self.graph.insert(
                    diagnostics_root, "end", iid=item_id,
                    text=issue.code.replace("_", " ").title(),
                    values=(issue.severity, issue.subject or "workspace"),
                )
                self._selection[item_id] = issue
        if manifest.runtime_contracts is not None:
            self._insert_api_contracts(manifest.runtime_contracts)
        content_root = self.graph.insert(
            "", "end", text="Content fields", values=("graph", ""), open=True,
        )
        for kind in sorted({node.kind for node in manifest.nodes}):
            kind_root = self.graph.insert(
                content_root, "end", text=kind.replace("_", " ").title(),
                values=(kind, ""), open=True,
            )
            for node in (item for item in manifest.nodes if item.kind == kind):
                item_id = f"node:{node.node_id}"
                self.graph.insert(
                    kind_root, "end", iid=item_id, text=node.label,
                    values=(node.kind, "linked"),
                )
                self._selection[item_id] = node

        link_root = self.graph.insert(
            "", "end", text="Resolved references", values=("linker", ""), open=True,
        )
        for linked in self.report.references:
            ref = linked.reference
            item_id = f"ref:{ref.reference_id}"
            self.graph.insert(
                link_root, "end", iid=item_id,
                text=(
                    f"{ref.source} — {ref.relationship.replace('_', ' ')} "
                    f"→ {ref.target}"
                ),
                values=("relationship", "resolved" if linked.valid else "error"),
            )
            self._selection[item_id] = ref

        if manifest.install_steps:
            plan_root = self.graph.insert(
                "", "end", text="Install plan", values=("ordered", ""), open=True,
            )
            for step in manifest.install_steps:
                item_id = f"step:{step.step_id}"
                self.graph.insert(
                    plan_root, "end", iid=item_id,
                    text=f"{step.order}. {step.title}",
                    values=(step.strategy, "read-only plan"),
                )
                self._selection[item_id] = step

        state = "PASS" if self.report.valid else "FAIL"
        if manifest.workspace_summary:
            summary = manifest.workspace_summary
            self.status.set(
                f"{manifest.catalog_state} · {state} · "
                f"{summary.get('Components', len(manifest.nodes))} components · "
                f"{summary.get('Coverage', 'n/a')} coverage · "
                f"{summary.get('Tracked files', 0)} tracked files · "
                f"{summary.get('Unassigned files', 0)} unassigned · "
                f"{self.report.error_count} errors · "
                f"{self.report.warning_count} warnings"
            )
        else:
            self.status.set(
                f"{manifest.catalog_state} · {state} · {len(manifest.nodes)} nodes · "
                f"{sum(item.valid for item in self.report.references)}/"
                f"{len(self.report.references)} references · "
                f"{self.report.error_count} errors · {self.report.warning_count} warnings"
            )
        first_node = next(
            (key for key in self._selection if key.startswith("node:")), None,
        )
        target = (
            preferred_selection
            if preferred_selection in self._selection
            else first_node
        )
        if target:
            self.graph.selection_set(target)
            self.graph.see(target)
            self._inspect_selection()

    def _refresh_audit(self) -> None:
        if self.report is None:
            return
        current = self.report.manifest
        selection = self.graph.selection()
        preferred = selection[0] if selection else None
        try:
            refreshed = AddonManifest.load(
                current.manifest_path, source_root=current.source_root,
            )
            refreshed = replace(
                refreshed,
                catalog_state=current.catalog_state,
                catalog_origin=current.catalog_origin,
                package_source=current.package_source,
            )
        except (OSError, ValueError) as exc:
            self.status.set(f"Audit refresh failed · {exc}")
            return
        for index, candidate in enumerate(self.manifests):
            if candidate.manifest_path == current.manifest_path:
                self.manifests[index] = refreshed
                break
        self._show_manifest(refreshed, preferred_selection=preferred)
        self.status.set(f"Refreshed · {self.status.get()}")

    def _set_details(self, value: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", value)
        self.details.configure(state="disabled")

    def _inspect_selection(self, _event: object | None = None) -> None:
        selection = self.graph.selection()
        if not selection:
            return
        item = self._selection.get(selection[0])
        self.fields.delete(*self.fields.get_children())
        self.field_help.set("Select a field to see why GTA V needs it.")
        if isinstance(item, RuntimeContractReport):
            calls = sum(len(package.api_calls) for package in item.packages)
            verified = sum(
                call.status == "verified"
                for package in item.packages for call in package.api_calls
            )
            self.heading.set("Runtime API contracts")
            self._set_details(
                "Checked-in host API declarations linked to bounded content "
                "manifests and source evidence. Nothing is loaded or executed."
            )
            for field, value in (
                ("Contract status", "verified" if item.valid else "review required"),
                ("Hosts", len(item.hosts)),
                ("Packages", len(item.packages)),
                ("API calls", f"{verified}/{calls} verified"),
                ("Errors", item.error_count),
                ("Warnings", item.warning_count),
            ):
                self.fields.insert("", "end", text=field, values=(value,))
        elif isinstance(item, RuntimeHostAudit):
            self.heading.set(f"{item.public_type} — API v{item.api_version}")
            self._set_details(
                "Public Story Mode extension surface verified against its "
                "checked-in source declaration."
                f"\n\nSource: {item.source}"
            )
            for field, value in (
                ("Component", item.component_id),
                ("API version", item.api_version),
                ("Assembly", item.assembly),
                ("Public type", item.public_type),
                ("Source", item.source),
                ("Members", len(item.members)),
                ("Contract status", item.status),
            ):
                self.fields.insert("", "end", text=field, values=(value,))
        elif isinstance(item, RuntimeMemberAudit):
            location = (
                f"{item.evidence.path}:{item.evidence.line}"
                if item.evidence else "No declaration evidence"
            )
            excerpt = item.evidence.excerpt if item.evidence else ""
            self.heading.set(item.name)
            self._set_details(
                "Checked-in public API member."
                + (f"\n\n{excerpt}" if excerpt else "")
                + f"\n\nEvidence: {location}"
            )
            for field, value in (
                ("Name", item.name),
                ("Kind", item.kind),
                ("Required capability", item.capability or "none"),
                ("Required interfaces", item.requires or ("none",)),
                ("Expected signature", item.expected_signature or "not applicable"),
                ("Observed signature", item.actual_signature or "not found"),
                ("Contract status", item.status),
                ("Source line", location),
            ):
                self.fields.insert("", "end", text=field, values=(_display_value(value),))
        elif isinstance(item, RuntimePackageAudit):
            title = item.package_id or item.component_id
            self.heading.set(title)
            self._set_details(
                "Content-package declarations and bounded runtime usage linked "
                f"to {item.provider_component_id}.\n\nSource: {item.manifest}"
            )
            for field, value in (
                ("Component", item.component_id),
                ("Package", item.package_id or "unresolved"),
                ("Version", item.version or "unresolved"),
                ("API version", item.api_version if item.api_version is not None else "unresolved"),
                ("API provider", item.provider_component_id),
                ("Relationship", item.relation),
                ("Capabilities", item.capabilities or ("none",)),
                ("Runtime assemblies", item.runtime_assemblies or ("none",)),
                ("Entry points", item.entry_points or ("none",)),
                ("Settings", item.settings or ("none",)),
                ("Interfaces", item.interfaces or ("none",)),
                ("API calls", len(item.api_calls)),
                ("Workbench relationships", item.workbench_relationships or ("none",)),
                ("Package requirements", item.requirements or ("none",)),
                ("Project references", item.project_references or ("none",)),
                ("Contract status", item.status),
            ):
                self.fields.insert("", "end", text=field, values=(_display_value(value),))
        elif isinstance(item, RuntimeApiCall):
            self.heading.set(item.member)
            self._set_details(
                "Bounded package source call matched against the checked-in "
                "host API contract."
                f"\n\n{item.evidence.excerpt}"
                f"\n\nEvidence: {item.evidence.path}:{item.evidence.line}"
            )
            for field, value in (
                ("Member", item.member),
                ("Required capability", item.capability or "none"),
                ("Contract status", item.status),
                ("Source", item.evidence.path),
                ("Source line", item.evidence.line),
            ):
                self.fields.insert("", "end", text=field, values=(value,))
        elif isinstance(item, _ApiContractDetail):
            self.heading.set(item.heading)
            location = (
                f"\n\nSource: {item.source}"
                + (f":{item.line}" if item.line is not None else "")
                if item.source else ""
            )
            self._set_details(item.description + location)
            for field, value in item.fields:
                self.fields.insert(
                    "", "end", text=field, values=(_display_value(value),),
                )
        elif isinstance(item, AddonNode):
            self.heading.set(item.label)
            source = f"\n\nSource: {item.source}" if item.source else ""
            self._set_details((item.description or "Integration content node.") + source)
            for field, value in item.fields.items():
                self.fields.insert("", "end", iid=f"field:{field}", text=field,
                                   values=(_display_value(value),))
        elif isinstance(item, dict):
            self.heading.set("Workspace evidence")
            self._set_details(
                "Bounded tracked-file attribution for the selected product workspace."
            )
            for field, value in item.items():
                self.fields.insert(
                    "", "end", iid=f"field:{field}", text=field,
                    values=(_display_value(value),),
                )
        elif isinstance(item, AddonReference):
            self.heading.set(item.relationship.replace("_", " ").title())
            self._set_details(
                (item.description or "Cross-file reference.") +
                f"\n\n{item.source}.{item.source_field} → "
                f"{item.target}.{item.target_field}"
            )
            for field, value in (
                ("source", item.source), ("source_field", item.source_field),
                ("target", item.target), ("target_field", item.target_field),
                ("required", item.required),
            ):
                self.fields.insert("", "end", text=field, values=(value,))
        elif isinstance(item, AddonInstallStep):
            self.heading.set(item.title)
            source = f"\n\nSource: {item.source}" if item.source else ""
            self._set_details((item.description or "Install-plan stage.") + source)
            for field, value in (
                ("order", item.order), ("target", item.target),
                ("strategy", item.strategy),
            ):
                self.fields.insert("", "end", text=field, values=(value,))
        elif isinstance(item, AddonIssue):
            self.heading.set(item.code.replace("_", " ").title())
            subject = f"\n\nSubject: {item.subject}" if item.subject else ""
            self._set_details(item.message + subject)
            for field, value in (
                ("severity", item.severity), ("code", item.code),
                ("subject", item.subject or "workspace"),
            ):
                self.fields.insert("", "end", text=field, values=(value,))

    def _explain_field(self, _event: object | None = None) -> None:
        selection = self.fields.selection()
        if not selection:
            return
        field = self.fields.item(selection[0], "text")
        self.field_help.set(field_description(field))

    def _copy_selected_field(self) -> None:
        selection = self.fields.selection()
        if not selection:
            self.status.set("Select a field before copying its value.")
            return
        item = self.fields.item(selection[0])
        values = item.get("values", ())
        value = str(values[0]) if values else str(item.get("text", ""))
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.set(f"Copied {item.get('text', 'field')} to the clipboard")

    def _selected_source(self) -> Path | None:
        if not self.report:
            return None
        selection = self.graph.selection()
        item = self._selection.get(selection[0]) if selection else None
        if isinstance(item, (AddonNode, AddonInstallStep, AddonIssue)):
            source = item.source
        elif isinstance(item, RuntimeHostAudit):
            source = item.source
        elif isinstance(item, RuntimeMemberAudit):
            source = item.evidence.path if item.evidence else None
        elif isinstance(item, RuntimePackageAudit):
            source = item.manifest
        elif isinstance(item, RuntimeApiCall):
            source = item.evidence.path
        elif isinstance(item, _ApiContractDetail):
            source = item.source
        else:
            source = None
        if source:
            return (self.report.manifest.source_root / source).resolve()
        return self.report.manifest.manifest_path

    def _open_source(self) -> None:
        source = self._selected_source()
        if not source or not source.exists():
            messagebox.showwarning("Source unavailable", "The selected source was not found.", parent=self)
            return
        if os.name == "nt":
            os.startfile(source)  # type: ignore[attr-defined]
        else:  # pragma: no cover - desktop target is Windows
            import webbrowser
            webbrowser.open(source.as_uri())

    def _export_report(self) -> None:
        if not self.report:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Export linked integration report",
            defaultextension=".md", filetypes=(("Markdown", "*.md"),),
            initialfile=f"{self.report.manifest.addon_id}-link-report.md",
        )
        if not destination:
            return
        try:
            Path(destination).write_text(self.report.to_markdown(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status.set(f"Exported linked report: {destination}")
