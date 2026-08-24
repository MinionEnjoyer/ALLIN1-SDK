"""Integrated model hierarchy, materials, viewport, and safe authoring UI."""

from __future__ import annotations

import io
import json
import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageScan,
    RpfNativeEntryRecord,
)
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.compiled_render import (
    CompiledRenderError,
    CompiledRenderProgress,
    CompiledRenderResult,
    CompiledRenderSettings,
    compile_vehicle_render,
    detect_blender,
)
from allin1_sdk.compiled_render_ui import CompiledRenderPanel, RenderSettings
from allin1_sdk.model_materials import (
    MaterialAuthoringWorkspace,
    ModelGeometryRecord,
    ModelMaterialProject,
    ModelMaterialRecord,
    inspect_model_bytes,
    inspect_model_file,
)
from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES, MODEL_PREVIEW_SUFFIXES
from allin1_sdk.paths import user_data_root
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.viewport_rendering import (
    LatestOnlyRenderWorker,
    ViewportRenderKey,
    WeightedLruCache,
)


_MODEL_SUFFIXES = MODEL_PREVIEW_SUFFIXES
_CONTEXT_SUFFIXES = frozenset({".ytd", ".ybn", ".ytyp"})
_INTERACTIVE_TRIANGLES = 4_000


def _image_weight(value: tuple[Image.Image, dict[str, object]]) -> int:
    image = value[0]
    return image.width * image.height * len(image.getbands())


class ModelMaterialWorkbenchFrame(ttk.Frame):
    """One non-modal workspace for cross-content native model authoring."""

    def __init__(
        self, parent: tk.Misc, project_root: str | Path,
        *, installation_roots: tuple[Path, ...] = (),
        on_help=None, on_close=None, on_open_asset=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = tuple(Path(item).resolve() for item in installation_roots)
        self._on_help = on_help
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.reader: PackageAssetReader | None = None
        self.asset_paths: dict[str, str] = {}
        self.rpf_asset_paths: dict[str, RpfNativeEntryRecord] = {}
        self.project: ModelMaterialProject | None = None
        self.authoring_workspace: MaterialAuthoringWorkspace | None = None
        self.selected_asset = ""
        self._selected_material: ModelMaterialRecord | None = None
        self._selected_geometry: ModelGeometryRecord | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._progression_photo: ImageTk.PhotoImage | None = None
        self._source_image: Image.Image | None = None
        self._zoom = 1.0
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self._orbit_origin: tuple[int, int] | None = None
        self._orbit_camera: tuple[float, float] | None = None
        self._load_generation = 0
        self._render_generation = 0
        self._render_poll: str | None = None
        self._load_events: queue.SimpleQueue[tuple[int, object]] = queue.SimpleQueue()
        self._render_worker = LatestOnlyRenderWorker(
            cache=WeightedLruCache(
                maximum_entries=10, maximum_weight=72 * 1024 * 1024,
                weigh=_image_weight,
            ),
            thread_name="allin1-model-material-viewport",
        )
        self._compiled_render_executable = self._load_blender_path()
        self._compiled_render_installation = None
        self._compiled_render_thread: threading.Thread | None = None
        self._compiled_render_cancel: threading.Event | None = None
        self._compiled_events: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self._compiled_poll: str | None = None
        self.compiled_render_panel: CompiledRenderPanel | None = None

        self.edition = tk.StringVar(value="Enhanced")
        self.render_mode = tk.StringVar(value="Shaded")
        self.lod = tk.StringVar(value="All")
        self.component = tk.StringVar(value="All")
        self.status = tk.StringVar(
            value="Open a model, package, or material workspace to begin."
        )
        self.summary = tk.StringVar(value="No model loaded")
        self.shader_name = tk.StringVar()
        self.texture_slot = tk.StringVar()
        self.texture_value = tk.StringVar()
        self.geometry_material = tk.StringVar()

        self._build()
        self.bind("<Destroy>", self._destroyed, add="+")

    def _build(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 7))
        ttk.Button(
            toolbar, text="Open…", style="Accent.TButton",
            command=self._choose_source,
        ).pack(side="left")
        ttk.Button(
            toolbar, text="Material workspace…",
            command=self._choose_workspace,
        ).pack(side="left", padx=(6, 0))
        self.create_button = ttk.Button(
            toolbar, text="Create editable copy", command=self._create_workspace,
            state="disabled",
        )
        self.create_button.pack(side="left", padx=(6, 0))
        self.undo_button = ttk.Button(
            toolbar, text="Undo", command=self._undo, state="disabled",
        )
        self.undo_button.pack(side="left", padx=(6, 0))
        self.build_button = ttk.Button(
            toolbar, text="Build verified asset…", command=self._build_asset,
            state="disabled",
        )
        self.build_button.pack(side="left", padx=(6, 0))
        if self._on_help is not None:
            ttk.Button(
                toolbar, text="Help", style="Link.TButton",
                command=lambda: self._on_help("model-material-workbench"),
            ).pack(side="right")
        if self._on_close is not None:
            ttk.Button(
                toolbar, text="‹ Back", style="Link.TButton",
                command=self._on_close,
            ).pack(side="right", padx=(0, 8))

        header = ttk.Frame(self, style="Surface.TFrame", padding=(10, 7))
        header.pack(fill="x", pady=(0, 7))
        ttk.Label(
            header, text="MODEL & MATERIALS", style="FieldLabel.TLabel",
        ).pack(side="left")
        ttk.Label(header, textvariable=self.summary).pack(side="left", padx=(12, 0))
        ttk.Label(header, text="Edition").pack(side="right", padx=(8, 4))
        self.edition_combo = ttk.Combobox(
            header, textvariable=self.edition, values=("Enhanced", "Legacy"),
            state="readonly", width=10,
        )
        self.edition_combo.pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.side_panes = CollapsibleSidePanes(
            panes, left_width=245, center_width=660, right_width=375,
            left_weight=2, center_weight=6, right_weight=4,
            left_label="model navigator", right_label="material inspector",
        )
        navigator = ttk.Frame(self.side_panes.left_host, padding=(6, 5))
        viewport_host = ttk.Frame(self.side_panes.center_host)
        inspector = ttk.Frame(self.side_panes.right_host, padding=(6, 5))
        self.side_panes.set_contents(navigator, viewport_host, inspector)

        ttk.Label(
            navigator, text="MODEL ASSETS", style="FieldLabel.TLabel",
        ).pack(anchor="w", pady=(0, 5))
        self.asset_tree = ttk.Treeview(
            navigator, columns=("type", "size"), show="tree headings",
            selectmode="browse", height=16,
        )
        self.asset_tree.heading("#0", text="Asset")
        self.asset_tree.heading("type", text="Type")
        self.asset_tree.heading("size", text="Size")
        self.asset_tree.column("#0", width=165, minwidth=110)
        self.asset_tree.column("type", width=44, stretch=False)
        self.asset_tree.column("size", width=64, stretch=False, anchor="e")
        asset_scroll = ttk.Scrollbar(
            navigator, orient="vertical", command=self.asset_tree.yview,
        )
        self.asset_tree.configure(yscrollcommand=asset_scroll.set)
        tree_host = ttk.Frame(navigator)
        tree_host.pack(fill="both", expand=True)
        self.asset_tree.pack(in_=tree_host, side="left", fill="both", expand=True)
        asset_scroll.pack(in_=tree_host, side="right", fill="y")
        self.asset_tree.bind("<<TreeviewSelect>>", self._select_asset)
        ttk.Separator(navigator).pack(fill="x", pady=7)
        ttk.Label(
            navigator, text="PACKAGE CONTEXT", style="FieldLabel.TLabel",
        ).pack(anchor="w")
        self.context_tree = ttk.Treeview(
            navigator, columns=("kind",), show="tree headings", height=8,
        )
        self.context_tree.heading("#0", text="Related asset")
        self.context_tree.heading("kind", text="Role")
        self.context_tree.column("#0", width=175)
        self.context_tree.column("kind", width=70, stretch=False)
        self.context_tree.pack(fill="both", expand=False, pady=(4, 0))
        self.context_tree.bind("<Double-1>", self._open_context_asset)

        viewport_host.configure(style="Dark.TFrame")
        viewport_toolbar = tk.Frame(viewport_host, background="#121a16")
        viewport_toolbar.pack(fill="x")
        tk.Label(
            viewport_toolbar, text="VIEWPORT", background="#121a16",
            foreground="#dfeae3", font=("Segoe UI Semibold", 9),
        ).pack(side="left", padx=(9, 8), pady=7)
        for label, variable, values, callback, width in (
            ("", self.render_mode, ("Shaded", "Materials", "Wireframe"), self._render_final, 10),
            ("LOD", self.lod, ("All",), self._render_final, 12),
            ("Part", self.component, ("All",), self._render_final, 18),
        ):
            if label:
                tk.Label(
                    viewport_toolbar, text=label, background="#121a16",
                    foreground="#8fa59a", font=("Segoe UI", 8),
                ).pack(side="left", padx=(7, 3))
            combo = ttk.Combobox(
                viewport_toolbar, textvariable=variable, values=values,
                state="readonly", width=width,
            )
            combo.pack(side="left", padx=(0, 3), pady=4)
            combo.bind("<<ComboboxSelected>>", lambda _event, fn=callback: fn())
            if variable is self.lod:
                self.lod_combo = combo
            elif variable is self.component:
                self.component_combo = combo
        tk.Button(
            viewport_toolbar, text="Fit", command=self._fit_view,
            background="#1b2c22", foreground="#dce9e1", relief="flat",
            activebackground="#2a4b36", activeforeground="white", padx=8,
        ).pack(side="right", padx=(3, 7), pady=4)
        tk.Button(
            viewport_toolbar, text="Render…", command=self._show_compiled_render,
            background="#238746", foreground="white", relief="flat",
            activebackground="#2b9d54", activeforeground="white", padx=9,
        ).pack(side="right", pady=4)
        self.viewport = tk.Canvas(
            viewport_host, background="#0d1210", highlightthickness=0,
            takefocus=True,
        )
        self.viewport.pack(fill="both", expand=True)
        self.viewport_message = self.viewport.create_text(
            20, 20, anchor="nw", fill="#8da197",
            font=("Segoe UI", 10), text="Open a model to inspect its materials.",
        )
        self.viewport_image = self.viewport.create_image(0, 0, anchor="center")
        self.viewport.bind("<Configure>", lambda _event: self._display_image())
        self.viewport.bind("<ButtonPress-1>", self._begin_orbit)
        self.viewport.bind("<B1-Motion>", self._continue_orbit)
        self.viewport.bind("<ButtonRelease-1>", self._end_orbit)
        self.viewport.bind("<MouseWheel>", self._wheel_zoom)

        notebook = ttk.Notebook(inspector)
        notebook.pack(fill="both", expand=True)
        materials_tab = ttk.Frame(notebook, padding=6)
        geometry_tab = ttk.Frame(notebook, padding=6)
        diagnostics_tab = ttk.Frame(notebook, padding=6)
        progression_tab = ttk.Frame(notebook, padding=6)
        notebook.add(materials_tab, text="Materials")
        notebook.add(geometry_tab, text="Geometry")
        notebook.add(progression_tab, text="Progression")
        notebook.add(diagnostics_tab, text="Checks")

        self.progression_summary = tk.StringVar(
            value="No material progression was detected in this package."
        )
        ttk.Label(
            progression_tab, textvariable=self.progression_summary,
            foreground="#52635c", wraplength=350, justify="left",
        ).pack(fill="x", pady=(0, 5))
        self.progression_canvas = tk.Canvas(
            progression_tab, height=150, background="#0d1210",
            highlightthickness=0,
        )
        self.progression_canvas.pack(fill="x")
        ttk.Label(
            progression_tab,
            text=(
                "Top: decoded texture over transparency · Bottom: approximate "
                "alpha and emissive blend over a dark weapon surface"
            ),
            foreground="#52635c", wraplength=350, justify="left",
        ).pack(fill="x", pady=(3, 2))
        progression_table = ttk.Frame(progression_tab)
        progression_table.pack(fill="both", expand=True, pady=(6, 0))
        self.progression_tree = ttk.Treeview(
            progression_table,
            columns=("texture", "emissive", "alpha", "difference"),
            show="tree headings", height=8,
        )
        self.progression_tree.heading("#0", text="Tier")
        self.progression_tree.heading("texture", text="Texture")
        self.progression_tree.heading("emissive", text="Emissive")
        self.progression_tree.heading("alpha", text="Mean alpha")
        self.progression_tree.heading("difference", text="Δ neighbor")
        self.progression_tree.column("#0", width=45, stretch=False)
        self.progression_tree.column("texture", width=175)
        self.progression_tree.column("emissive", width=70, stretch=False)
        self.progression_tree.column("alpha", width=70, stretch=False)
        self.progression_tree.column("difference", width=70, stretch=False)
        progression_scroll = ttk.Scrollbar(
            progression_table, orient="vertical",
            command=self.progression_tree.yview,
        )
        self.progression_tree.configure(yscrollcommand=progression_scroll.set)
        self.progression_tree.grid(row=0, column=0, sticky="nsew")
        progression_scroll.grid(row=0, column=1, sticky="ns")
        progression_table.rowconfigure(0, weight=1)
        progression_table.columnconfigure(0, weight=1)

        progression_findings = ttk.LabelFrame(
            progression_tab, text="Progression checks", padding=4,
        )
        progression_findings.pack(fill="x", pady=(6, 0))
        self.progression_finding_tree = ttk.Treeview(
            progression_findings,
            columns=("severity", "level", "message"),
            show="tree headings", height=4,
        )
        self.progression_finding_tree.heading("#0", text="Check")
        self.progression_finding_tree.heading("severity", text="Status")
        self.progression_finding_tree.heading("level", text="Tier")
        self.progression_finding_tree.heading("message", text="Evidence")
        self.progression_finding_tree.column("#0", width=135, stretch=False)
        self.progression_finding_tree.column("severity", width=64, stretch=False)
        self.progression_finding_tree.column("level", width=42, stretch=False)
        self.progression_finding_tree.column("message", width=390)
        finding_scroll = ttk.Scrollbar(
            progression_findings, orient="vertical",
            command=self.progression_finding_tree.yview,
        )
        self.progression_finding_tree.configure(yscrollcommand=finding_scroll.set)
        self.progression_finding_tree.pack(side="left", fill="x", expand=True)
        finding_scroll.pack(side="right", fill="y")

        self.material_tree = ttk.Treeview(
            materials_tab, columns=("uses", "textures"), show="tree headings",
            selectmode="browse", height=9,
        )
        self.material_tree.heading("#0", text="Shader")
        self.material_tree.heading("uses", text="Uses")
        self.material_tree.heading("textures", text="Maps")
        self.material_tree.column("#0", width=180)
        self.material_tree.column("uses", width=45, stretch=False, anchor="e")
        self.material_tree.column("textures", width=45, stretch=False, anchor="e")
        self.material_tree.pack(fill="both", expand=True)
        self.material_tree.bind("<<TreeviewSelect>>", self._select_material)
        edit = ttk.LabelFrame(materials_tab, text="Existing binding", padding=7)
        edit.pack(fill="x", pady=(7, 0))
        ttk.Label(edit, text="Shader").grid(row=0, column=0, sticky="w")
        ttk.Entry(edit, textvariable=self.shader_name).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(6, 0),
        )
        ttk.Label(edit, text="Texture slot").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.texture_combo = ttk.Combobox(
            edit, textvariable=self.texture_slot, state="readonly", width=18,
        )
        self.texture_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(5, 0))
        self.texture_combo.bind("<<ComboboxSelected>>", self._select_texture_slot)
        ttk.Label(edit, text="Texture name").grid(row=2, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(edit, textvariable=self.texture_value).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(5, 0),
        )
        self.apply_material_button = ttk.Button(
            edit, text="Apply to editable copy", command=self._apply_material,
            state="disabled",
        )
        self.apply_material_button.grid(row=3, column=1, columnspan=2, sticky="e", pady=(7, 0))
        edit.columnconfigure(1, weight=1)

        self.geometry_tree = ttk.Treeview(
            geometry_tab, columns=("lod", "material"), show="tree headings",
            selectmode="browse", height=14,
        )
        self.geometry_tree.heading("#0", text="Component")
        self.geometry_tree.heading("lod", text="LOD")
        self.geometry_tree.heading("material", text="Material")
        self.geometry_tree.column("#0", width=140)
        self.geometry_tree.column("lod", width=55, stretch=False)
        self.geometry_tree.column("material", width=110)
        self.geometry_tree.pack(fill="both", expand=True)
        self.geometry_tree.bind("<<TreeviewSelect>>", self._select_geometry)
        geometry_edit = ttk.Frame(geometry_tab)
        geometry_edit.pack(fill="x", pady=(7, 0))
        self.geometry_material_combo = ttk.Combobox(
            geometry_edit, textvariable=self.geometry_material,
            state="readonly",
        )
        self.geometry_material_combo.pack(side="left", fill="x", expand=True)
        self.apply_geometry_button = ttk.Button(
            geometry_edit, text="Assign", command=self._apply_geometry,
            state="disabled",
        )
        self.apply_geometry_button.pack(side="left", padx=(5, 0))

        self.diagnostics = tk.Text(
            diagnostics_tab, wrap="word", state="disabled", borderwidth=0,
            padx=8, pady=8,
        )
        self.diagnostics.pack(fill="both", expand=True)

        activity = ttk.Frame(self, style="Surface.TFrame", padding=(8, 4))
        activity.pack(fill="x", pady=(7, 0))
        ttk.Label(activity, text="ACTIVITY", style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(activity, textvariable=self.status).pack(
            side="left", fill="x", expand=True, padx=(9, 0),
        )

    def _native_game_path(self) -> Path | None:
        return next((path for path in self.installation_roots if path.is_dir()), None)

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open model or add-on package",
            filetypes=(
                ("Models and packages", "*.ydr *.ydd *.yft *.zip *.oiv *.rar *.7z"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            self.open_source(Path(selected))
            return
        selected_folder = filedialog.askdirectory(
            parent=self, title="Or select an extracted package folder",
        )
        if selected_folder:
            self.open_source(Path(selected_folder))

    def _choose_workspace(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Open material workspace")
        if selected:
            self.open_workspace(Path(selected))

    def open_source(self, source: str | Path) -> None:
        path = Path(source).expanduser().resolve()
        self.authoring_workspace = None
        self.source = path
        self.scan = None
        self.reader = None
        self.asset_paths.clear()
        self.rpf_asset_paths.clear()
        self.selected_asset = ""
        self._clear_trees()
        if path.is_file() and path.suffix.casefold() in _MODEL_SUFFIXES:
            item = self.asset_tree.insert(
                "", "end", text=path.name,
                values=(path.suffix[1:].upper(), f"{path.stat().st_size / 1024:.0f} KiB"),
            )
            self.asset_paths[item] = str(path)
            self.asset_tree.selection_set(item)
            self.asset_tree.focus(item)
            self.create_button.configure(state="normal")
            self._load_loose_model(path)
            return
        try:
            scan = AddonPackageInspector(
                self.project_root, self._native_game_path(),
            ).inspect(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open package", str(exc), parent=self)
            self.status.set("Package was not opened.")
            return
        self.scan = scan
        self.reader = PackageAssetReader(path)
        if scan.edition_tag in {"Legacy", "Enhanced"}:
            self.edition.set(scan.edition_tag)
        model_entries = [item for item in scan.entries if item.suffix in _MODEL_SUFFIXES]
        context_entries = [item for item in scan.entries if item.suffix in _CONTEXT_SUFFIXES]
        for entry in model_entries:
            item = self.asset_tree.insert(
                "", "end", text=PurePosixPath(entry.path).name,
                values=(entry.suffix[1:].upper(), f"{entry.size / 1024:.0f} KiB"),
            )
            self.asset_paths[item] = entry.path
        for number, entry in enumerate(
            item for item in scan.rpf_native_assets if item.suffix in _MODEL_SUFFIXES
        ):
            item = self.asset_tree.insert(
                "", "end", text=PurePosixPath(entry.path).name,
                values=(f"RPF {entry.suffix[1:].upper()}", f"{entry.size / 1024:.0f} KiB"),
            )
            token = f"rpf-model:{number}:{entry.entry_id}"
            self.asset_paths[item] = token
            self.rpf_asset_paths[token] = entry
        for entry in context_entries:
            role = {".ytd": "Textures", ".ybn": "Collision", ".ytyp": "Archetypes"}[entry.suffix]
            item = self.context_tree.insert(
                "", "end", text=PurePosixPath(entry.path).name, values=(role,),
            )
            self.context_tree.set(item, "kind", role)
            self.asset_paths[item] = entry.path
        for number, entry in enumerate(
            item for item in scan.rpf_native_assets if item.suffix in _CONTEXT_SUFFIXES
        ):
            role = {".ytd": "RPF textures", ".ybn": "RPF collision", ".ytyp": "RPF archetypes"}[entry.suffix]
            item = self.context_tree.insert(
                "", "end", text=PurePosixPath(entry.path).name, values=(role,),
            )
            token = f"rpf-context:{number}:{entry.entry_id}"
            self.asset_paths[item] = token
            self.rpf_asset_paths[token] = entry
        self._apply_progression_report(
            scan.material_progressions[0] if scan.material_progressions else None,
        )
        self.summary.set(
            f"{path.name} · {len(model_entries) + sum(item.suffix in _MODEL_SUFFIXES for item in scan.rpf_native_assets)} models · "
            f"{len(context_entries) + sum(item.suffix in _CONTEXT_SUFFIXES for item in scan.rpf_native_assets)} related assets · {scan.edition_tag}"
        )
        self.create_button.configure(state="normal" if model_entries else "disabled")
        self.status.set(
            "Choose a model asset. Package context is advisory until exact bindings decode."
        )
        if self.asset_tree.get_children():
            first = self.asset_tree.get_children()[0]
            self.asset_tree.selection_set(first)
            self.asset_tree.focus(first)
            self._select_asset()

    def open_workspace(self, workspace: str | Path) -> None:
        try:
            authoring = MaterialAuthoringWorkspace(workspace)
            project = authoring.inspect()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not open material workspace", str(exc), parent=self)
            return
        self.authoring_workspace = authoring
        self.source = authoring.root
        self.scan = None
        self.reader = None
        self.selected_asset = str(authoring.xml_path)
        self._clear_trees()
        item = self.asset_tree.insert(
            "", "end", text=project.name,
            values=(project.suffix[1:].upper(), f"r{authoring.revision}"),
        )
        self.asset_paths[item] = str(authoring.xml_path)
        self.asset_tree.selection_set(item)
        self.edition.set(project.edition)
        self.create_button.configure(state="disabled")
        self.undo_button.configure(state="normal" if authoring.revision else "disabled")
        self.build_button.configure(state="normal")
        self.apply_material_button.configure(state="normal")
        self.apply_geometry_button.configure(state="normal")
        self._apply_project(project)
        self.status.set(f"Editable material workspace · revision {authoring.revision}")

    def _clear_trees(self) -> None:
        for tree in (getattr(self, "asset_tree", None), getattr(self, "context_tree", None),
                     getattr(self, "material_tree", None), getattr(self, "geometry_tree", None)):
            if tree is not None:
                tree.delete(*tree.get_children())

    def _select_asset(self, _event: object | None = None) -> None:
        selection = self.asset_tree.selection()
        path = self.asset_paths.get(selection[0], "") if selection else ""
        if not path:
            return
        self.selected_asset = path
        nested = self.rpf_asset_paths.get(path)
        if nested is not None:
            self.create_button.configure(state="disabled")
            self._load_rpf_model(nested)
            return
        if self.reader is None:
            loose = Path(path)
            if loose.suffix.casefold() in _MODEL_SUFFIXES:
                self._load_loose_model(loose)
            return
        reader = self.reader
        source = self.source
        edition = self.edition.get()
        self._load_generation += 1
        generation = self._load_generation
        self.status.set(f"Decoding {PurePosixPath(path).name}…")

        def worker() -> None:
            try:
                content = reader.read(path, limit=MAX_NATIVE_PREVIEW_BYTES)
                if content.truncated:
                    raise ValueError("Model exceeds the guarded 128 MiB decode limit")
                project = inspect_model_bytes(
                    self.project_root, PurePosixPath(path).name, content.data,
                    edition=edition, gta_path=self._native_game_path(),
                    source=f"{source}!/{path}",
                )
            except (OSError, RuntimeError, ValueError) as exc:
                self._load_events.put((generation, exc))
            else:
                self._load_events.put((generation, project))

        threading.Thread(target=worker, daemon=True, name="allin1-model-decode").start()
        self.after(40, self._poll_load)

    def _load_rpf_model(self, record: RpfNativeEntryRecord) -> None:
        reader = self.reader
        source = self.source
        game = self._native_game_path()
        if reader is None or source is None or game is None:
            self.status.set("Nested RPF model requires a configured GTA installation.")
            return
        outer = next(
            (item for item in (self.scan.entries if self.scan else ())
             if item.path.casefold() == record.source.casefold()),
            None,
        )
        if outer is None:
            self.status.set("The package-owned outer RPF could not be resolved.")
            return
        self._load_generation += 1
        generation = self._load_generation
        edition = self.edition.get()
        self.status.set(f"Extracting {PurePosixPath(record.path).name} from nested RPF…")

        def worker() -> None:
            try:
                content = reader.read(outer.path, limit=outer.size + 1)
                if content.truncated or len(content.data) != outer.size:
                    raise ValueError("Package RPF could not be read completely")
                with tempfile.TemporaryDirectory(prefix="allin1-material-rpf-") as temporary:
                    archive = Path(temporary) / "package.rpf"
                    archive.write_bytes(content.data)
                    service = RpfExplorerService(self.project_root, game)
                    index = service.index(archive)
                    entry = index.entry(record.entry_id)
                    extracted = service.extract(
                        index, entry, Path(temporary) / PurePosixPath(entry.path).name,
                    )
                    project = inspect_model_bytes(
                        self.project_root, entry.name, extracted.read_bytes(),
                        edition=edition, gta_path=game,
                        source=f"{source}!/{record.source}!/{entry.virtual_name}",
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                self._load_events.put((generation, exc))
            else:
                self._load_events.put((generation, project))

        threading.Thread(
            target=worker, daemon=True, name="allin1-rpf-model-decode",
        ).start()
        self.after(40, self._poll_load)

    def _apply_progression_report(self, report) -> None:
        self.progression_tree.delete(*self.progression_tree.get_children())
        self.progression_finding_tree.delete(
            *self.progression_finding_tree.get_children()
        )
        self.progression_canvas.delete("all")
        self._progression_photo = None
        if report is None:
            self.progression_summary.set(
                "No multi-tier YDR/YTD material progression was detected."
            )
            return
        self.progression_summary.set(
            f"{report.model_count} models across {len(report.families)} families · "
            f"{report.texture_count} textures · {report.archetype_count} archetypes · "
            f"{report.error_count} errors · {report.warning_count} warnings · "
            f"{'inferred' if report.inferred else 'declared'} relationship"
        )
        for tier in report.tiers:
            self.progression_tree.insert(
                "", "end", text=f"{tier.level:02d}",
                values=(
                    tier.texture,
                    "—" if tier.emissive_multiplier is None else f"{tier.emissive_multiplier:.6f}",
                    "—" if tier.alpha_mean is None else f"{tier.alpha_mean:.3f}",
                    "—" if tier.neighboring_visual_difference is None else f"{tier.neighboring_visual_difference:.4f}",
                ),
            )
        if not report.findings:
            self.progression_finding_tree.insert(
                "", "end", text="progression_valid",
                values=("PASS", "—", "No material progression problems detected."),
            )
        for finding in report.findings:
            self.progression_finding_tree.insert(
                "", "end", text=finding.code,
                values=(
                    finding.severity.upper(),
                    "—" if finding.level is None else f"{finding.level:02d}",
                    finding.message,
                ),
            )
        if report.preview_png:
            image = Image.open(io.BytesIO(report.preview_png)).convert("RGB")
            maximum = max(100, self.progression_canvas.winfo_width() or 350)
            scale = min(1.0, maximum / image.width)
            shown = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            self._progression_photo = ImageTk.PhotoImage(shown)
            self.progression_canvas.create_image(
                4, 4, anchor="nw", image=self._progression_photo,
            )

    def _load_loose_model(self, path: Path) -> None:
        self._load_generation += 1
        generation = self._load_generation
        edition = self.edition.get()
        self.status.set(f"Decoding {path.name}…")

        def worker() -> None:
            try:
                project = inspect_model_file(
                    self.project_root, path, edition=edition,
                    gta_path=self._native_game_path(),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                self._load_events.put((generation, exc))
            else:
                self._load_events.put((generation, project))

        threading.Thread(target=worker, daemon=True, name="allin1-model-decode").start()
        self.after(40, self._poll_load)

    def _poll_load(self) -> None:
        latest: tuple[int, object] | None = None
        while True:
            try:
                latest = self._load_events.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            self.after(40, self._poll_load)
            return
        generation, value = latest
        if generation != self._load_generation:
            self.after(0, self._poll_load)
            return
        if isinstance(value, BaseException):
            self.status.set(f"Model decode failed: {value}")
            self.viewport.itemconfigure(self.viewport_message, text=str(value))
            return
        self._apply_project(value)  # type: ignore[arg-type]

    def _apply_project(self, project: ModelMaterialProject) -> None:
        self.project = project
        self._selected_material = None
        self._selected_geometry = None
        self.material_tree.delete(*self.material_tree.get_children())
        self.geometry_tree.delete(*self.geometry_tree.get_children())
        for material in project.materials:
            self.material_tree.insert(
                "", "end", iid=f"m:{material.index}", text=material.shader,
                values=(len(material.geometry_indices), len(material.textures)),
            )
        for geometry in project.geometries:
            self.geometry_tree.insert(
                "", "end", iid=f"g:{geometry.index}", text=geometry.component,
                values=(geometry.lod, geometry.material_name or "Unresolved"),
            )
        self.lod_combo.configure(values=("All", *project.lods))
        component_names = tuple(dict.fromkeys(
            str(item.get("name", "")) for item in project.components if item.get("name")
        ))
        self.component_combo.configure(values=("All", *component_names))
        self.lod.set("All")
        self.component.set("All")
        self._write_diagnostics(project)
        self.summary.set(
            f"{project.name} · {len(project.materials)} materials · "
            f"{len(project.geometries)} geometry records · {project.edition}"
        )
        if project.scene is None:
            self.viewport.itemconfigure(
                self.viewport_message, text="No renderable geometry decoded.",
            )
        else:
            self._camera_yaw, self._camera_pitch = 34.0, 24.0
            self._zoom = 1.0
            self._render_final()
        self.status.set(
            f"Decoded {project.name}; {project.warning_count} warnings, "
            f"{project.error_count} errors."
        )
        if project.materials:
            first = "m:0"
            if self.material_tree.exists(first):
                self.material_tree.selection_set(first)
                self._select_material()

    def _write_diagnostics(self, project: ModelMaterialProject) -> None:
        lines = [
            f"Source: {project.source}", f"SHA-256: {project.sha256}",
            f"LODs: {', '.join(project.lods) or 'None'}", "",
        ]
        if not project.findings:
            lines.append("No model/material findings.")
        for item in project.findings:
            subject = f" [{item.subject}]" if item.subject else ""
            lines.append(f"{item.severity.upper()} · {item.code}{subject}\n{item.message}\n")
        self.diagnostics.configure(state="normal")
        self.diagnostics.delete("1.0", "end")
        self.diagnostics.insert("1.0", "\n".join(lines))
        self.diagnostics.configure(state="disabled")

    def _select_material(self, _event: object | None = None) -> None:
        selection = self.material_tree.selection()
        if not selection or self.project is None:
            return
        index = int(selection[0].split(":", 1)[1])
        material = next((item for item in self.project.materials if item.index == index), None)
        if material is None:
            return
        self._selected_material = material
        self.shader_name.set(material.shader)
        slots = tuple(item.slot for item in material.textures)
        self.texture_combo.configure(values=slots)
        self.texture_slot.set(slots[0] if slots else "")
        self._select_texture_slot()

    def _select_texture_slot(self, _event: object | None = None) -> None:
        material = self._selected_material
        slot = self.texture_slot.get()
        binding = next((item for item in material.textures if item.slot == slot), None) if material else None
        self.texture_value.set(binding.texture if binding else "")

    def _select_geometry(self, _event: object | None = None) -> None:
        selection = self.geometry_tree.selection()
        if not selection or self.project is None:
            return
        index = int(selection[0].split(":", 1)[1])
        geometry = next((item for item in self.project.geometries if item.index == index), None)
        if geometry is None:
            return
        self._selected_geometry = geometry
        self.geometry_material_combo.configure(values=geometry.available_materials)
        if geometry.material_index is not None and geometry.material_index < len(geometry.available_materials):
            self.geometry_material.set(geometry.available_materials[geometry.material_index])
        else:
            self.geometry_material.set("")

    def _render_final(self) -> None:
        self._submit_render("final")

    def _submit_render(self, quality: str) -> None:
        project = self.project
        scene = project.scene if project is not None else None
        if scene is None:
            return
        mode = self.render_mode.get().casefold()
        lod = None if self.lod.get().casefold() == "all" else self.lod.get()
        component = None if self.component.get().casefold() == "all" else self.component.get()
        yaw = self._camera_yaw
        pitch = self._camera_pitch
        key = ViewportRenderKey.create(
            (project.sha256, id(scene)), yaw=yaw,
            pitch=pitch, lod=lod, component=component,
            render_mode=mode, quality=quality,  # type: ignore[arg-type]
        )
        self._render_generation = self._render_worker.submit(
            key,
            lambda: scene.render_image(
                yaw=yaw, pitch=pitch,
                lod=lod, component=component, render_mode=mode,
                quality=quality,
                triangle_budget=(_INTERACTIVE_TRIANGLES if quality == "interactive" else None),
            ),
            cache_result=quality != "interactive",
        )
        self.viewport.itemconfigure(self.viewport_message, text="Rendering model…")
        if self._render_poll is None:
            self._render_poll = self.after(25, self._poll_render)

    def _poll_render(self) -> None:
        self._render_poll = None
        outcome = self._render_worker.poll()
        if outcome is not None and outcome.generation == self._render_generation:
            if outcome.error is not None:
                self.viewport.itemconfigure(self.viewport_message, text=str(outcome.error))
            elif outcome.value is not None:
                self._source_image = outcome.value[0]
                self.viewport.itemconfigure(self.viewport_message, text="")
                self._display_image()
        if self._render_worker.busy:
            self._render_poll = self.after(25, self._poll_render)

    def _display_image(self) -> None:
        image = self._source_image
        if image is None:
            return
        width = max(80, self.viewport.winfo_width() - 24)
        height = max(80, self.viewport.winfo_height() - 24)
        fit = min(width / image.width, height / image.height) * self._zoom
        size = (max(1, int(image.width * fit)), max(1, int(image.height * fit)))
        shown = image.resize(size, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(shown)
        self.viewport.coords(
            self.viewport_image, self.viewport.winfo_width() / 2,
            self.viewport.winfo_height() / 2,
        )
        self.viewport.itemconfigure(self.viewport_image, image=self._photo)

    def _fit_view(self) -> None:
        self._zoom = 1.0
        self._display_image()

    def _wheel_zoom(self, event: tk.Event) -> str:
        self._zoom = min(4.0, max(0.15, self._zoom * (1.12 if event.delta > 0 else 1 / 1.12)))
        self._display_image()
        return "break"

    def _begin_orbit(self, event: tk.Event) -> None:
        if self.project is None or self.project.scene is None:
            return
        self._orbit_origin = (event.x, event.y)
        self._orbit_camera = (self._camera_yaw, self._camera_pitch)

    def _continue_orbit(self, event: tk.Event) -> None:
        if self._orbit_origin is None or self._orbit_camera is None:
            return
        self._camera_yaw = (self._orbit_camera[0] + (event.x - self._orbit_origin[0]) * 0.35) % 360
        self._camera_pitch = min(89.0, max(-89.0, self._orbit_camera[1] - (event.y - self._orbit_origin[1]) * 0.3))
        if not self._render_worker.busy:
            self._submit_render("interactive")

    def _end_orbit(self, _event: tk.Event) -> None:
        self._orbit_origin = None
        self._orbit_camera = None
        self._render_final()

    def _create_workspace(self) -> None:
        if not self.selected_asset:
            messagebox.showinfo("Choose a model", "Select a model asset first.", parent=self)
            return
        if self.selected_asset in self.rpf_asset_paths:
            messagebox.showinfo(
                "Extract before authoring",
                "Nested RPF assets are previewed read-only. Export the exact native "
                "entry from RPF Explorer before creating an editable copy.",
                parent=self,
            )
            return
        parent = filedialog.askdirectory(parent=self, title="Choose workspace parent folder")
        if not parent:
            return
        name = PurePosixPath(self.selected_asset).name
        destination = Path(parent) / f"{Path(name).stem}-materials"
        try:
            if self.reader is None:
                workspace = MaterialAuthoringWorkspace.create(
                    self.project_root, Path(self.selected_asset), destination,
                    edition=self.edition.get(), gta_path=self._native_game_path(),
                )
            else:
                content = self.reader.read(self.selected_asset, limit=MAX_NATIVE_PREVIEW_BYTES)
                if content.truncated:
                    raise ValueError("Model exceeds the guarded native workspace limit")
                workspace = MaterialAuthoringWorkspace.create_bytes(
                    self.project_root, name, content.data, destination,
                    edition=self.edition.get(), gta_path=self._native_game_path(),
                    source_path=self.source,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create material workspace", str(exc), parent=self)
            return
        self.open_workspace(workspace.root)

    def _apply_material(self) -> None:
        workspace = self.authoring_workspace
        material = self._selected_material
        if workspace is None or material is None:
            return
        updates = {}
        if self.texture_slot.get():
            updates[self.texture_slot.get()] = self.texture_value.get()
        try:
            result = workspace.set_material(
                material.index, expected_revision=workspace.revision,
                shader_name=self.shader_name.get(), textures=updates,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Material edit rejected", str(exc), parent=self)
            return
        self._apply_project(result.project)
        self.undo_button.configure(state="normal")
        self.status.set(f"Material edit committed · revision {result.revision}")

    def _apply_geometry(self) -> None:
        workspace = self.authoring_workspace
        geometry = self._selected_geometry
        if workspace is None or geometry is None:
            return
        try:
            material_index = geometry.available_materials.index(self.geometry_material.get())
            result = workspace.set_geometry_material(
                geometry.index, material_index,
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Geometry assignment rejected", str(exc), parent=self)
            return
        self._apply_project(result.project)
        self.undo_button.configure(state="normal")
        self.status.set(f"Geometry assignment committed · revision {result.revision}")

    def _undo(self) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            return
        try:
            result = workspace.undo(expected_revision=workspace.revision)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Material undo failed", str(exc), parent=self)
            return
        self._apply_project(result.project)
        self.undo_button.configure(state="normal" if workspace.revision else "disabled")
        self.status.set(f"Restored prior material state · revision {result.revision}")

    def _build_asset(self) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            return
        suffix = Path(str(workspace.manifest["source_name"])).suffix
        selected = filedialog.asksaveasfilename(
            parent=self, title="Build verified native model",
            defaultextension=suffix, filetypes=((f"{suffix.upper()} model", f"*{suffix}"),),
        )
        if not selected:
            return
        try:
            asset, report = workspace.build(
                self.project_root, Path(selected), gta_path=self._native_game_path(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Model build failed", str(exc), parent=self)
            return
        self.status.set(f"Built and reparsed {asset.name} · report {report.name}")

    def _open_context_asset(self, _event: object | None = None) -> None:
        selection = self.context_tree.selection()
        path = self.asset_paths.get(selection[0]) if selection else None
        if path and self._on_open_asset is not None:
            self._on_open_asset(path)

    def _show_compiled_render(self) -> None:
        if self.compiled_render_panel is None:
            self.compiled_render_panel = CompiledRenderPanel(
                self.side_panes.center_host,
                backend_status=self._compiled_backend_status,
                on_render=self._start_compiled_render,
                on_cancel=self._cancel_compiled_render,
                on_locate_backend=self._locate_blender,
            )
        name = Path(self.project.name).stem if self.project is not None else "model"
        output_root = Path.home() / "Pictures"
        if not output_root.is_dir():
            output_root = Path.home()
        self.compiled_render_panel.set_scene_available(
            self.project is not None and self.project.scene is not None
        )
        self.compiled_render_panel.show(suggested_output=output_root / f"{name}.png")

    def _compiled_backend_status(self) -> dict[str, object]:
        installation = detect_blender(self._compiled_render_executable)
        self._compiled_render_installation = installation
        if installation is None:
            return {
                "available": False, "name": "Blender not detected",
                "detail": "Locate Blender to compile a lit production render.",
            }
        return {
            "available": True, "name": f"Blender {installation.version}",
            "detail": f"Detected from {installation.source}; headless render ready.",
            "device": "Eevee + Cycles",
        }

    @staticmethod
    def _blender_config() -> Path:
        return user_data_root() / "compiled-render.json"

    @classmethod
    def _load_blender_path(cls) -> Path | None:
        try:
            payload = json.loads(cls._blender_config().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = payload.get("blender_executable") if isinstance(payload, dict) else None
        return Path(value) if isinstance(value, str) else None

    def _locate_blender(self, path: Path) -> None:
        installation = detect_blender(path)
        if installation is None:
            return
        self._compiled_render_executable = installation.executable
        config = self._blender_config()
        config.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.with_name(f".{config.name}.tmp")
        temporary.write_text(json.dumps({
            "schema": 1, "blender_executable": str(installation.executable),
        }, indent=2) + "\n", encoding="utf-8")
        temporary.replace(config)

    def _texture_dictionary_for_render(self) -> Path | None:
        if self.source is None or not self.source.is_dir() or not self.selected_asset:
            return None
        stem = PurePosixPath(self.selected_asset).stem
        if stem.casefold().endswith("_hi"):
            stem = stem[:-3]
        candidates = [
            self.source.joinpath(*PurePosixPath(entry.path).parts)
            for entry in (self.scan.entries if self.scan is not None else ())
            if entry.suffix == ".ytd" and PurePosixPath(entry.path).stem.casefold() == stem.casefold()
        ]
        return candidates[0].resolve() if len(candidates) == 1 and candidates[0].is_file() else None

    def _start_compiled_render(self, settings: RenderSettings) -> bool:
        panel = self.compiled_render_panel
        project = self.project
        scene = project.scene if project else None
        if panel is None or scene is None:
            return False
        if self._compiled_render_thread is not None and self._compiled_render_thread.is_alive():
            panel.set_running(True, message="A compiled render is already running.")
            return False
        installation = self._compiled_render_installation or detect_blender(
            self._compiled_render_executable
        )
        if installation is None:
            panel.set_running(False, message="Blender is not available.")
            return False
        raw = dict(settings)
        try:
            output = Path(raw.pop("output_path"))
            configured = CompiledRenderSettings(**raw)
        except (KeyError, TypeError, ValueError) as exc:
            panel.set_running(False, message=str(exc))
            return False
        cancel = threading.Event()
        self._compiled_render_cancel = cancel
        lod = None if self.lod.get().casefold() == "all" else self.lod.get()
        component = None if self.component.get().casefold() == "all" else self.component.get()
        protected = list(self.installation_roots)
        if self.source is not None:
            protected.append(self.source if self.source.is_dir() else self.source.parent)

        def progress(value: CompiledRenderProgress) -> None:
            self._compiled_events.put(("progress", value))

        def worker() -> None:
            try:
                result = compile_vehicle_render(
                    scene, output, settings=configured,
                    blender_executable=installation.executable,
                    texture_dictionary=self._texture_dictionary_for_render(),
                    edition=project.edition, gta_path=self._native_game_path(),
                    yaw=self._camera_yaw, pitch=self._camera_pitch,
                    lod=lod, component=component,
                    protected_roots=tuple(protected), cancel_event=cancel,
                    progress=progress,
                )
            except (CompiledRenderError, OSError, RuntimeError, ValueError) as exc:
                self._compiled_events.put(("error", exc))
            else:
                self._compiled_events.put(("complete", result))

        self._compiled_render_thread = threading.Thread(
            target=worker, daemon=True, name="allin1-material-compiled-render",
        )
        self._compiled_render_thread.start()
        panel.set_running(True, message="Preparing compiled render…")
        self._compiled_poll = self.after(40, self._poll_compiled_render)
        return True

    def _cancel_compiled_render(self) -> None:
        if self._compiled_render_cancel is not None:
            self._compiled_render_cancel.set()

    def _poll_compiled_render(self) -> None:
        self._compiled_poll = None
        panel = self.compiled_render_panel
        terminal = False
        while True:
            try:
                kind, value = self._compiled_events.get_nowait()
            except queue.Empty:
                break
            if panel is None:
                continue
            if kind == "progress" and isinstance(value, CompiledRenderProgress):
                panel.set_progress(value.fraction, value.message)
            elif kind == "complete" and isinstance(value, CompiledRenderResult):
                panel.set_output(value.output_path, message=f"Render complete in {value.elapsed_seconds:.1f} seconds.")
                terminal = True
            elif kind == "error":
                panel.set_output(None, message=(value.message if isinstance(value, CompiledRenderError) else str(value)))
                terminal = True
        if terminal or self._compiled_render_thread is None or not self._compiled_render_thread.is_alive():
            self._compiled_render_thread = None
            self._compiled_render_cancel = None
        else:
            self._compiled_poll = self.after(40, self._poll_compiled_render)

    def has_active_work(self) -> bool:
        return bool(
            self._render_worker.busy
            or (self._compiled_render_thread is not None and self._compiled_render_thread.is_alive())
        )

    def focus_active_work(self) -> None:
        self.viewport.focus_set()

    def confirm_navigation(self) -> bool:
        # Every mutation is committed transactionally as soon as Apply succeeds;
        # entries do not hold an invisible dirty buffer.
        return True

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        self._load_generation += 1
        if self._render_poll is not None:
            self.after_cancel(self._render_poll)
        if self._compiled_poll is not None:
            self.after_cancel(self._compiled_poll)
        if self._compiled_render_cancel is not None:
            self._compiled_render_cancel.set()
        self._render_worker.close(wait=False)


__all__ = ["ModelMaterialWorkbenchFrame"]
