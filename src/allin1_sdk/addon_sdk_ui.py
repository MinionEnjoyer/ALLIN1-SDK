"""Desktop viewer for ALLIN1 add-on SDK manifests and linked fields."""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from allin1_sdk import __version__
from allin1_sdk.addon_importer import AddonDraftBuilder, AddonPackageInspector, PackageScan
from allin1_sdk.branding import apply_sdk_window_icon
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.sdk_console import SdkConsoleDialog
from allin1_sdk.processes import run_hidden
from allin1_sdk.paths import user_data_root
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip
from allin1_sdk.ui_foundation import place_window
from allin1_sdk.addon_sdk import (
    AddonInstallStep,
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
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if value == "":
        return "(empty)"
    return str(value)


class AddonSdkDialog(tk.Toplevel):
    """Inspect add-on fields, resolved references, and a safe install plan."""

    NAVIGATION = (
        ("linker", "Package Linker", "Ctrl+1"),
        ("assets", "Asset Viewer", "Ctrl+2"),
        ("workbench", "Content Workbench", "Ctrl+3"),
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
        self._logo_photo: ImageTk.PhotoImage | None = None
        self._navigation_history: list[str] = []
        self.sidebar_visible = tk.BooleanVar(self, value=True)
        self.title("ALLIN1 SDK — Developer Workspace")
        place_window(self, preferred=(1320, 840), minimum=(980, 640))
        if not standalone:
            self.transient(parent)
        apply_sdk_window_icon(self, self.project_root)
        self._build()
        self._load_examples()

    def _package_inspector(self) -> AddonPackageInspector:
        game = next(
            (path for path in self.installation_roots if path.is_dir()), None,
        )
        return AddonPackageInspector(self.project_root, game)

    def _build_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        content = self._make_content_menu(menu)
        menu.add_cascade(label="Packages", menu=content)
        review = self._make_review_menu(menu)
        menu.add_cascade(label="Inspect & Export", menu=review)
        intelligence = self._make_intelligence_menu(menu)
        menu.add_cascade(label="Package Tools", menu=intelligence)
        view = tk.Menu(menu, tearoff=False)
        for key, label, shortcut in self.NAVIGATION:
            view.add_command(
                label=label, accelerator=shortcut,
                command=lambda selected=key: self._select_workspace(selected),
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
        menu.add_cascade(label="View", menu=view)
        tools = tk.Menu(menu, tearoff=False)
        tools.add_command(
            label="Focus / expand SDK Console", accelerator="Ctrl+`",
            command=self._open_console,
        )
        menu.add_cascade(label="Tools", menu=tools)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="SDK Help Center", accelerator="F1",
            command=lambda: self._open_help("sdk"),
        )
        help_menu.add_command(
            label="RPF Archives Help",
            command=lambda: self._open_help("rpf-explorer"),
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self.bind("<F1>", lambda _event: self._open_context_help())
        self.bind("<Control-KeyPress-grave>", lambda _event: self._open_console())

    def _make_content_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open addon manifest…", command=self._open_manifest)
        menu.add_separator()
        menu.add_command(label="Import DLC folder…", command=self._import_folder)
        menu.add_command(label="Import package archive…", command=self._import_archive)
        menu.add_command(label="Audit package folder…", command=self._audit_folder)
        return menu

    def _make_review_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Export link report…", command=self._export_report)
        menu.add_command(label="Open selected source", command=self._open_source)
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
            }.get(category, "workbench")
        topic = {
            "linker": "sdk", "assets": "asset-viewer",
            "workbench": workbench_topic, "rpf": "rpf-explorer",
            "models": "model-material-workbench",
            "recipes": "package-recipes", "help": "input",
        }.get(getattr(self, "current_workspace", "linker"), "sdk")
        self._open_help(topic)

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
        else:
            self.workspace_sidebar.pack_forget()
            self.sidebar_toggle_button.configure(
                text=">",
                command=lambda: self._set_sidebar_visible(True),
            )
        return "break"

    def _toggle_sidebar(self, _event: object | None = None) -> str:
        return self._set_sidebar_visible(not self.sidebar_visible.get())

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
                page, installation_roots=self.installation_roots,
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
        self._workspace_instances[key] = workspace
        return workspace

    def _build(self) -> None:
        self._build_menu()
        outer = ttk.Frame(
            self, padding=(12, 9, 12, 10), width=1, height=1,
        )
        # The header and hidden workspaces have intentionally generous
        # preferred widths. Do not let those requests keep the client at its
        # previous large geometry when the user shrinks the top-level window.
        outer.pack_propagate(False)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        # Keep the developer application visually distinct from the launcher.
        # The adjacent heading describes the workspace instead of repeating
        # the product name already present in the supplied SDK badge.
        logo = self.project_root / "assets" / "ALLIN1_SDK.png"
        if logo.is_file():
            try:
                with Image.open(logo) as opened:
                    image = opened.convert("RGBA")
                    image.thumbnail((180, 88), Image.Resampling.LANCZOS)
                    self._logo_photo = ImageTk.PhotoImage(image.copy())
                ttk.Label(header, image=self._logo_photo).pack(
                    side="left", padx=(0, 14), anchor="center",
                )
            except (OSError, tk.TclError):
                self._logo_photo = None
        # Reserve the fixed actions before packing the expanding heading.
        # Otherwise a long localized description can push version/support
        # controls outside the window even though the top-level itself fits.
        header_actions = ttk.Frame(header)
        header_actions.pack(side="right", padx=(18, 4), fill="y")
        self.version_badge = tk.Label(
            header_actions, text=f"v{__version__}",
            background="#176b36", foreground="white",
            font=("Segoe UI Semibold", 10), padx=12, pady=5,
        )
        self.version_badge.pack(anchor="e")
        self.support_button = ttk.Button(
            header_actions, text="Support ALLIN1 ↗", style="Link.TButton",
            cursor="hand2", command=lambda: webbrowser.open(
                "https://buymeacoffee.com/minionenjoyer"
            ),
        )
        self.support_button.pack(anchor="e", pady=(10, 0))
        header_text = ttk.Frame(header)
        header_text.pack(side="left", fill="x", expand=True, anchor="center")
        header_title = ttk.Frame(header_text)
        header_title.pack(fill="x")
        self.context_back_button = ttk.Button(
            header_title, text="", style="Link.TButton", cursor="hand2",
            command=self._go_back,
        )
        ttk.Label(
            header_title, text="Developer Workspace",
            font=("Segoe UI Semibold", 18), foreground="#173d32",
        ).pack(side="left", anchor="w")
        ttk.Label(
            header_text,
            text=(
                "Developer workspace for package integration, native assets, "
                "archive inspection, compatibility, and safe authoring plans."
            ),
            wraplength=760, justify="left",
        ).pack(anchor="w", pady=(3, 0))

        shell = ttk.Frame(outer)
        shell.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Loading SDK packages…")
        console_host = ttk.Frame(shell, style="Surface.TFrame")
        console_host.pack(side="bottom", fill="x", pady=(5, 0))
        activity = ttk.Frame(shell, style="Surface.TFrame", padding=(9, 4))
        activity.pack(side="bottom", fill="x")
        ttk.Label(
            activity, text="ACTIVITY", style="FieldLabel.TLabel",
            background="#ffffff",
        ).pack(side="left", padx=(0, 8))
        ttk.Label(
            activity, textvariable=self.status, style="Muted.TLabel",
            background="#ffffff", anchor="w", width=1,
        ).pack(side="left", fill="x", expand=True)
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
            highlightthickness=0, borderwidth=0,
        )
        self.sidebar_toggle_rail.pack(side="left", fill="y")
        self.sidebar_toggle_rail.pack_propagate(False)
        tk.Frame(
            self.sidebar_toggle_rail, width=1, background="#aebdb5",
            highlightthickness=0, borderwidth=0,
        ).place(relx=0.5, y=0, relheight=1, anchor="n")
        self.sidebar_toggle_button = tk.Button(
            self.sidebar_toggle_rail, text="<",
            background="#1f7f42", foreground="#ffffff",
            activebackground="#176b36", activeforeground="#ffffff",
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=0, pady=0, font=("Segoe UI Semibold", 9), cursor="hand2",
            command=lambda: self._set_sidebar_visible(False),
        )
        self.sidebar_toggle_button.place(
            relx=0.5, rely=0.5, anchor="center", width=16, height=30,
        )
        ttk.Label(
            sidebar, text="DEVELOPER WORKSPACES", style="FieldLabel.TLabel",
            background="#ffffff",
        ).pack(anchor="w", padx=10, pady=(0, 7))
        workspace = ttk.Frame(content_shell)
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
        models_page = ttk.Frame(workspace)
        rpf_page = ttk.Frame(workspace)
        recipes_page = ttk.Frame(workspace)
        help_page = ttk.Frame(workspace)
        self.workspace_pages = {
            "linker": linker_page,
            "assets": assets_page,
            "workbench": workbench_page,
            "models": models_page,
            "rpf": rpf_page,
            "recipes": recipes_page,
            "help": help_page,
        }
        self.workspace_buttons: dict[str, ttk.Button] = {}
        for index, (key, label, _shortcut) in enumerate(self.NAVIGATION, start=1):
            self.workspace_pages[key].grid(row=0, column=0, sticky="nsew")
            button = ttk.Button(
                sidebar, text=label, style="Nav.TButton", width=19,
                command=lambda selected=key: self._select_workspace(selected),
            )
            button.pack(fill="x", pady=1)
            self.workspace_buttons[key] = button
            self.bind(
                f"<Control-Key-{index}>",
                lambda _event, selected=key: (
                    self._select_workspace(selected), "break"
                )[1],
            )
        self.bind("<Alt-Left>", lambda _event: self._go_back())
        self.bind("<Control-b>", self._toggle_sidebar)
        self.bind("<Control-Tab>", self._cycle_workspace)
        self.bind(
            "<Control-Shift-Tab>",
            lambda event: self._cycle_workspace(event, -1),
        )

        toolbar = ttk.Frame(linker_page)
        toolbar.pack(fill="x", pady=(0, 10))
        content_menu = self._make_content_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Import or audit package", menu=content_menu,
            style="Accent.TButton",
        ).pack(side="left")
        self.review_menu = self._make_review_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Inspect or export", menu=self.review_menu,
        ).pack(side="left", padx=(7, 0))
        intelligence_menu = self._make_intelligence_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Package tools", menu=intelligence_menu,
        ).pack(side="left", padx=(7, 0))
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
            messagebox.showerror("SDK example error", str(exc), parent=self)
            self.status.set("Could not load built-in examples")
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
            parent=self, title="Open ALLIN1 SDK manifest",
            filetypes=(("ALLIN1 add-on manifest", "addon.json"), ("JSON", "*.json")),
        )
        if not selected:
            return
        try:
            manifest = AddonManifest.load(selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid add-on manifest", str(exc), parent=self)
            return
        try:
            manifest = self.catalog.remember(selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remember manifest", str(exc), parent=self)
            return
        self._append_manifest(manifest)

    def _append_manifest(self, manifest: AddonManifest) -> None:
        identity = (
            manifest.addon_id.casefold(), manifest.catalog_origin.casefold(),
            str(manifest.package_source or manifest.manifest_path).casefold(),
        )
        index = next((
            number for number, existing in enumerate(self.manifests)
            if (
                existing.addon_id.casefold(), existing.catalog_origin.casefold(),
                str(existing.package_source or existing.manifest_path).casefold(),
            ) == identity
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
        self.status.set(f"Package audit written: {Path(destination).name}")
        messagebox.showinfo(
            "Package audit complete",
            f"The review report was written to:\n{Path(destination).resolve()}",
            parent=self,
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
        messagebox.showinfo(
            "SDK draft generated",
            self._scan_summary(scan) +
            f"\n\nDraft: {written}\n"
            f"Linker: {report.error_count} errors and {report.warning_count} warnings.\n"
            "Generated drafts are intentionally not installable until every "
            "required link and rollback step is resolved.",
            parent=self,
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

    def _open_model_materials(self) -> None:
        """Route the current package into the integrated native model workspace."""
        self._select_workspace("models")
        if self.package_source is not None:
            self.model_material_workspace.open_source(self.package_source)
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
        available = {
            "vehicles": bool(scan.vehicles),
            "weapons": bool(
                scan.weapons or scan.weapon_enhancements
                or scan.scripted_weapon_systems
            ),
            "peds": bool(scan.peds),
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
                "The selected package does not contain vehicle, weapon, ped, or "
                "script-driven vanilla weapon relationships.",
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
        self.status.set("Package RPF reports written")
        messagebox.showinfo(
            "RPF inspection complete",
            f"Read-only reports were written to:\n{Path(destination).resolve()}",
            parent=self,
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
        self.status.set(f"Wrote {len(reports)} DLC inventory report(s)")
        messagebox.showinfo(
            "DLC inventory complete",
            "Read-only Markdown and JSON reports were written to:\n"
            f"{Path(destination).resolve()}", parent=self,
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
            f"{report.error_count} errors, {report.warning_count} warnings"
        )
        messagebox.showinfo(
            "Vehicle data compiled",
            "JSON, CSV, XLSX, and Markdown reports were written to:\n"
            f"{Path(destination).resolve()}", parent=self,
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
        self.status.set(f"Structured META/XML diff: {len(report.changes)} changes")
        messagebox.showinfo(
            "META/XML comparison complete",
            f"Found {len(report.changes)} semantic change(s).\n\nReport: {written}",
            parent=self,
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
        self.status.set("META/XML round trip is semantically equivalent")
        messagebox.showinfo(
            "META/XML round trip passed",
            f"The serialized document reparsed with the same canonical structure.\n\n"
            f"Report: {Path(output).resolve()}", parent=self,
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

    def _show_manifest(self, manifest: AddonManifest) -> None:
        self.package_source = manifest.package_source
        self.package_scan = None
        self._set_package_actions(
            assets=self.package_source is not None, rpfs=False,
            workbench=self.package_source is not None,
        )
        self.report = self.linker.link(manifest)
        self._selection.clear()
        self.graph.delete(*self.graph.get_children())
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
                text=f"{ref.source_field} → {ref.target_field}",
                values=(ref.relationship, "resolved" if linked.valid else "error"),
            )
            self._selection[item_id] = ref

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
        self.status.set(
            f"{manifest.catalog_state} · {state} · {len(manifest.nodes)} nodes · "
            f"{sum(item.valid for item in self.report.references)}/"
            f"{len(self.report.references)} references · "
            f"{self.report.error_count} errors · {self.report.warning_count} warnings"
        )
        first_node = next((key for key in self._selection if key.startswith("node:")), None)
        if first_node:
            self.graph.selection_set(first_node)
            self.graph.see(first_node)
            self._inspect_selection()

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
        if isinstance(item, AddonNode):
            self.heading.set(item.label)
            source = f"\n\nSource: {item.source}" if item.source else ""
            self._set_details((item.description or "Integration content node.") + source)
            for field, value in item.fields.items():
                self.fields.insert("", "end", iid=f"field:{field}", text=field,
                                   values=(_display_value(value),))
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
        source = item.source if isinstance(item, (AddonNode, AddonInstallStep)) else None
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
