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
from allin1_sdk.asset_viewer import AssetViewerDialog
from allin1_sdk.rpf_explorer import RpfExplorerDialog
from allin1_sdk.sdk_console import SdkConsoleDialog
from allin1_sdk.processes import run_hidden
from allin1_sdk.help_center import HelpCenterDialog
from allin1_sdk.paths import user_data_root
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip
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
        self.title("ALLIN1 SDK — Developer Workspace")
        self.geometry("1320x840")
        self.minsize(1020, 680)
        if not standalone:
            self.transient(parent)
        apply_sdk_window_icon(self, self.project_root)
        self._build()
        self._load_examples()

    def _build_menu(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        content = self._make_content_menu(menu)
        menu.add_cascade(label="Content", menu=content)
        review = self._make_review_menu(menu)
        menu.add_cascade(label="Review", menu=review)
        intelligence = self._make_intelligence_menu(menu)
        menu.add_cascade(label="Package Intelligence", menu=intelligence)
        view = tk.Menu(menu, tearoff=False)
        for index, (key, label) in enumerate((
            ("linker", "Integration"), ("assets", "Native Assets"),
            ("rpf", "RPF Explorer"), ("help", "Help Center"),
        ), start=1):
            view.add_command(
                label=label, accelerator=f"Ctrl+{index}",
                command=lambda selected=key: self._select_workspace(selected),
            )
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
            label="RPF Explorer Help",
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
            label="Inspect package RPFs…", command=self._inspect_package_rpfs,
            state="disabled",
        )
        menu.add_command(label="Open RPF Explorer…", command=self._open_rpf_explorer)
        self.review_menus.append(menu)
        return menu

    def _make_intelligence_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Preview OIV recipe…", command=self._preview_oiv)
        menu.add_command(label="Inventory installed DLC…", command=self._inventory_dlc)
        menu.add_command(label="Compile vehicle data…", command=self._compile_vehicle_data)
        menu.add_separator()
        menu.add_command(label="Compare META/XML…", command=self._compare_meta)
        menu.add_command(
            label="Validate META/XML round trip…", command=self._validate_meta_roundtrip,
        )
        return menu

    def _set_package_actions(self, *, assets: bool, rpfs: bool) -> None:
        for menu in self.review_menus:
            menu.entryconfigure(
                "Browse package assets…", state="normal" if assets else "disabled",
            )
            menu.entryconfigure(
                "Inspect package RPFs…", state="normal" if rpfs else "disabled",
            )

    def _open_console(self) -> None:
        self.console_workspace.toggle()

    def _open_help(self, topic: str = "sdk") -> None:
        self._select_workspace("help")
        self.help_workspace.show_topic(topic)

    def _open_context_help(self) -> None:
        topic = {
            "linker": "sdk", "assets": "asset-viewer",
            "rpf": "rpf-explorer", "help": "input",
        }.get(getattr(self, "current_workspace", "linker"), "sdk")
        self._open_help(topic)

    def _select_workspace(self, key: str) -> None:
        pages = getattr(self, "workspace_pages", {})
        if key not in pages:
            return
        pages[key].tkraise()
        self.current_workspace = key
        for name, button in self.workspace_buttons.items():
            button.configure(
                style="NavSelected.TButton" if name == key else "Nav.TButton",
            )

    def _build(self) -> None:
        self._build_menu()
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        # The full SDK badge is release/README artwork. In the application
        # header it duplicates the adjacent title and becomes illegible at
        # shell scale, so use the supplied A1 product mark instead.
        logo = self.project_root / "assets" / "ALLIN1.png"
        if logo.is_file():
            try:
                with Image.open(logo) as opened:
                    image = opened.convert("RGBA")
                    image.thumbnail((145, 82), Image.Resampling.LANCZOS)
                    self._logo_photo = ImageTk.PhotoImage(image.copy())
                ttk.Label(header, image=self._logo_photo).pack(
                    side="left", padx=(0, 14), anchor="center",
                )
            except (OSError, tk.TclError):
                self._logo_photo = None
        header_text = ttk.Frame(header)
        header_text.pack(side="left", fill="x", expand=True, anchor="center")
        ttk.Label(
            header_text, text="ALLIN1 SDK",
            font=("Segoe UI Semibold", 20), foreground="#173d32",
        ).pack(anchor="w")
        ttk.Label(
            header_text,
            text=(
                "Developer workspace for package integration, native assets, "
                "archive inspection, compatibility, and safe authoring plans."
            ),
            wraplength=1080, justify="left",
        ).pack(anchor="w", pady=(3, 0))
        header_actions = ttk.Frame(header)
        header_actions.pack(side="right", padx=(18, 4), fill="y")
        tk.Label(
            header_actions, text=f"v{__version__}",
            background="#176b36", foreground="white",
            font=("Segoe UI Semibold", 10), padx=12, pady=5,
        ).pack(anchor="e")
        support = ttk.Label(
            header_actions, text="Support ALLIN1 ↗", foreground="#176b36",
            cursor="hand2", font=("Segoe UI Semibold", 9, "underline"),
        )
        support.pack(anchor="e", pady=(10, 0))
        support.bind(
            "<Button-1>",
            lambda _event: webbrowser.open(
                "https://buymeacoffee.com/minionenjoyer"
            ),
        )

        shell = ttk.Frame(outer)
        shell.pack(fill="both", expand=True)
        console_host = ttk.Frame(shell, style="Surface.TFrame")
        console_host.pack(side="bottom", fill="x", pady=(10, 0))
        content_shell = ttk.Frame(shell)
        content_shell.pack(fill="both", expand=True)
        sidebar = ttk.Frame(content_shell, style="Surface.TFrame", padding=(8, 12))
        sidebar.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(
            sidebar, text="DEVELOPER WORKSPACES", style="FieldLabel.TLabel",
            background="#ffffff",
        ).pack(anchor="w", padx=10, pady=(0, 7))
        workspace = ttk.Frame(content_shell)
        workspace.pack(side="left", fill="both", expand=True)
        workspace.rowconfigure(0, weight=1)
        workspace.columnconfigure(0, weight=1)

        linker_page = ttk.Frame(workspace)
        assets_page = ttk.Frame(workspace)
        rpf_page = ttk.Frame(workspace)
        help_page = ttk.Frame(workspace)
        self.workspace_pages = {
            "linker": linker_page,
            "assets": assets_page,
            "rpf": rpf_page,
            "help": help_page,
        }
        self.workspace_buttons: dict[str, ttk.Button] = {}
        for index, (key, label) in enumerate((
            ("linker", "Integration"),
            ("assets", "Native Assets"),
            ("rpf", "RPF Explorer"),
            ("help", "Help Center"),
        ), start=1):
            self.workspace_pages[key].grid(row=0, column=0, sticky="nsew")
            button = ttk.Button(
                sidebar, text=label, style="Nav.TButton", width=19,
                command=lambda selected=key: self._select_workspace(selected),
            )
            button.pack(fill="x", pady=1)
            self.workspace_buttons[key] = button
            self.bind(
                f"<Control-Key-{index}>",
                lambda _event, selected=key: self._select_workspace(selected),
            )

        toolbar = ttk.Frame(linker_page)
        toolbar.pack(fill="x", pady=(0, 10))
        content_menu = self._make_content_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Import content", menu=content_menu,
            style="Accent.TButton",
        ).pack(side="left")
        self.review_menu = self._make_review_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Review actions", menu=self.review_menu,
        ).pack(side="left", padx=(7, 0))
        intelligence_menu = self._make_intelligence_menu(toolbar)
        ttk.Menubutton(
            toolbar, text="Package intelligence", menu=intelligence_menu,
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(value="Loading SDK examples…")
        ttk.Label(toolbar, textvariable=self.status).pack(side="right")

        panes = ttk.Panedwindow(linker_page, orient="horizontal")
        panes.pack(fill="both", expand=True)

        examples = ttk.LabelFrame(panes, text="Packages", padding=8, width=250)
        graph = ttk.LabelFrame(panes, text="Integration graph", padding=8)
        inspector = ttk.LabelFrame(panes, text="Field inspector", padding=8)
        # Package names and compatibility tags need enough width to scan without
        # forcing users to resize the first pane every session.
        panes.add(examples, weight=2)
        panes.add(graph, weight=4)
        panes.add(inspector, weight=5)

        self.example_list = ttk.Treeview(
            examples, columns=("package",), show="tree headings",
            selectmode="browse", height=16,
        )
        self.example_list.heading("#0", text="Status")
        self.example_list.heading("package", text="Package")
        self.example_list.column("#0", width=78, minwidth=68, stretch=False)
        self.example_list.column("package", width=205, minwidth=140, stretch=True)
        example_scroll = ttk.Scrollbar(
            examples, orient="vertical", command=self.example_list.yview,
        )
        self.example_list.configure(yscrollcommand=example_scroll.set)
        self.example_list.pack(side="left", fill="both", expand=True)
        example_scroll.pack(side="right", fill="y")
        self.example_list.bind("<<ListboxSelect>>", self._select_example)

        self.graph = ttk.Treeview(
            graph, columns=("type", "status"), show="tree headings", selectmode="browse",
        )
        self.graph.heading("#0", text="Field / integration")
        self.graph.heading("type", text="Type")
        self.graph.heading("status", text="Status")
        self.graph.column("#0", width=300, stretch=True)
        self.graph.column("type", width=115, stretch=False)
        self.graph.column("status", width=95, stretch=False)
        graph_scroll = ttk.Scrollbar(graph, orient="vertical", command=self.graph.yview)
        self.graph.configure(yscrollcommand=graph_scroll.set)
        self.graph.pack(side="left", fill="both", expand=True)
        graph_scroll.pack(side="right", fill="y")
        self.graph.bind("<<TreeviewSelect>>", self._inspect_selection)

        self.heading = tk.StringVar(value="Select an integration node")
        ttk.Label(
            inspector, textvariable=self.heading,
            font=("Segoe UI Semibold", 12), foreground="#1f7f42",
        ).pack(anchor="w")
        self.details = tk.Text(
            inspector, height=6, wrap="word", relief="flat",
            background="#f4f7f5", foreground="#26332e", padx=7, pady=7,
        )
        self.details.pack(fill="x", pady=(6, 8))
        self.details.configure(state="disabled")

        self.fields = ttk.Treeview(
            inspector, columns=("value",), show="tree headings", height=12,
        )
        self.fields.heading("#0", text="Field")
        self.fields.heading("value", text="Linked value")
        self.fields.column("#0", width=140, stretch=False)
        self.fields.column("value", width=350, stretch=True)
        field_scroll = ttk.Scrollbar(inspector, orient="vertical", command=self.fields.yview)
        self.fields.configure(yscrollcommand=field_scroll.set)
        field_row = ttk.Frame(inspector)
        field_row.pack(fill="both", expand=True)
        self.fields.pack(in_=field_row, side="left", fill="both", expand=True)
        field_scroll.pack(in_=field_row, side="right", fill="y")
        self.fields.bind("<<TreeviewSelect>>", self._explain_field)

        self.field_help = tk.StringVar(
            value="Select a field to see why GTA V needs it."
        )
        ttk.Label(
            inspector, textvariable=self.field_help, wraplength=440,
            justify="left", foreground="#52635c",
        ).pack(fill="x", pady=(8, 0))

        self.asset_workspace = AssetViewerDialog(
            assets_page, embedded=True, on_help=self._open_help,
            on_close=lambda: self._select_workspace("linker"),
        )
        self.rpf_workspace = RpfExplorerDialog(
            rpf_page, self.project_root, installation_roots=self.installation_roots,
            embedded=True, on_help=self._open_help,
            on_close=lambda: self._select_workspace("linker"),
        )
        self.console_workspace = SdkConsoleDialog(
            console_host, self.project_root, embedded=True, docked=True,
        )
        self.help_workspace = HelpCenterDialog(
            help_page, initial_topic="sdk", embedded=True,
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
        self.example_list.delete(*self.example_list.get_children())
        for index, manifest in enumerate(self.manifests):
            self.example_list.insert(
                "", "end", iid=str(index),
                text=self._catalog_state_label(manifest.catalog_state),
                values=(manifest.name,),
            )
        if self.manifests:
            self.example_list.selection_set("0")
            self._show_manifest(self.manifests[0])
        else:
            self.status.set("No SDK examples, imports, packages, or receipts found")

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
        self.manifests.append(manifest)
        index = len(self.manifests) - 1
        self.example_list.insert(
            "", "end", iid=str(index),
            text=self._catalog_state_label(manifest.catalog_state),
            values=(manifest.name,),
        )
        self.example_list.selection_set(str(index))
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
            scan = AddonPackageInspector().inspect(source)
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
        )

    def _open_asset_viewer(self) -> None:
        if self.package_source is not None:
            self.asset_workspace.open_source(
                self.package_source, self.package_scan,
            )
        self._select_workspace("assets")

    def _open_rpf_explorer(self) -> None:
        self._select_workspace("rpf")

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

    def _preview_oiv(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select an OIV package",
            filetypes=(("OpenIV package", "*.oiv *.zip"), ("All files", "*.*")),
        )
        if not selected:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Save the read-only OIV operation plan",
            initialdir=str(Path(selected).parent),
            initialfile=f"{Path(selected).stem}-oiv-plan.md",
            defaultextension=".md", filetypes=(("Markdown", "*.md"),),
        )
        if not destination:
            return
        try:
            from allin1_sdk.oiv_workbench import OivWorkbench
            workbench = OivWorkbench()
            plan = workbench.inspect(selected)
            report = plan.write_report(destination)
        except (OSError, ValueError) as exc:
            messagebox.showerror("OIV inspection failed", str(exc), parent=self)
            return
        self.status.set(f"OIV plan written: {report.name}")
        if not plan.translatable:
            messagebox.showinfo(
                "OIV review required",
                f"The operation plan was written to:\n{report}\n\n"
                "At least one delete, merge, nested archive, missing source, or "
                "unknown operation must be resolved manually.", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Managed export available",
            "Every operation can be represented by ALLIN1 ownership and rollback. "
            "Export a validated mod.toml package now?", parent=self,
        ):
            return
        package_dir = filedialog.askdirectory(
            parent=self, title="Select a new or empty managed-package folder",
            mustexist=False,
        )
        if not package_dir:
            return
        try:
            manifest = workbench.export_managed_package(plan, package_dir)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Managed export failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Managed package exported",
            f"Validated package manifest:\n{manifest}\n\n"
            "Review it before using Import & install.", parent=self,
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
            f"{len(scan.animation_weapons)} animation mappings, and "
            f"{len(scan.shop_weapons)} shop mappings.",
            f"Discovered {len(scan.vehicles)} vehicles, {len(scan.handlings)} handling "
            f"records, {len(scan.variations)} variation records, and "
            f"{len(scan.kits)} tuning kits.",
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
