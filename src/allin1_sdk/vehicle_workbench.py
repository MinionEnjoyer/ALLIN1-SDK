"""Integrated vehicle asset workbench and diagnostic viewport."""

from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk, UnidentifiedImageError

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageScan,
)
from allin1_sdk.native_assets import (
    NativeAssetInspector,
    NativeModelScene,
    native_preview_limit,
)
from allin1_sdk.vehicle_project import (
    VehicleProject,
    VehicleProjectModel,
    VehicleProjectResolver,
)
from allin1_sdk.vehicle_package import VehicleAddonPackageBuilder
from allin1_sdk.vehicle_authoring import (
    TUNING_COLLECTIONS,
    TUNING_FIELDS,
    VMT_TYPES,
    VehicleAuthoringWorkspace,
    VehicleTuningAsset,
    VehicleTuningEntry,
)


TUNING_COLLECTION_LABELS = {
    "Visible parts": "visibleMods",
    "Linked parts": "linkMods",
    "Performance": "statMods",
    "Category labels": "slotNames",
}
TUNING_COLLECTION_NAMES = {
    collection: label for label, collection in TUNING_COLLECTION_LABELS.items()
}


class VehicleWorkbenchFrame(ttk.Frame):
    """Resolve and inspect all assets belonging to one package vehicle."""

    def __init__(
        self,
        parent: tk.Misc,
        project_root: str | Path,
        *,
        installation_roots: tuple[Path, ...] = (),
        on_help=None,
        on_close=None,
        on_open_asset=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = tuple(
            Path(root).expanduser().resolve() for root in installation_roots
        )
        self._on_help = on_help
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.project: VehicleProject | None = None
        self.reader: PackageAssetReader | None = None
        self.models: dict[str, VehicleProjectModel] = {}
        self.project_assets: dict[str, str] = {}
        self.selected_model: VehicleProjectModel | None = None
        self.authoring_workspace: VehicleAuthoringWorkspace | None = None
        self.authoring_values: dict[str, tk.StringVar] = {}
        self.authoring_inputs: dict[str, ttk.Entry] = {}
        self.appearance_edit_inputs: list[ttk.Entry] = []
        self.appearance_edit_buttons: list[ttk.Button] = []
        self._appearance_colors: dict[str, dict[str, object]] = {}
        self._light_profiles: dict[str, dict[str, str]] = {}
        self._tuning_kits: dict[str, object] = {}
        self._tuning_entries: dict[str, VehicleTuningEntry] = {}
        self._tuning_assets: dict[str, VehicleTuningAsset] = {}
        self._tuning_findings: dict[str, str] = {}
        self._tuning_editable = False
        self._source_image: Image.Image | None = None
        self._model_scene: NativeModelScene | None = None
        self._scene_cache: dict[str, NativeModelScene] = {}
        self._viewport_photo: ImageTk.PhotoImage | None = None
        self._viewport_photo_zoom: float | None = None
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_origin: tuple[int, int] | None = None
        self._drag_pan: tuple[float, float] | None = None
        self._orbit_origin: tuple[int, int] | None = None
        self._orbit_camera: tuple[float, float] | None = None
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self._fragment_paths: dict[str, str] = {}
        self._render_job: str | None = None
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        title_row = ttk.Frame(outer)
        title_row.pack(fill="x")
        ttk.Label(
            title_row, text="Vehicle asset workbench", style="DialogTitle.TLabel",
        ).pack(side="left")
        if self._on_close is not None:
            ttk.Button(
                title_row, text="Back to Package Linker", command=self._on_close,
            ).pack(side="right")
        ttk.Label(
            outer,
            text=(
                "Work with a vehicle as one project: model fragments, textures, "
                "handling, variations, tuning kits, labels, and DLC registration. "
                "The viewport is diagnostic and never executes package code."
            ),
            wraplength=980, justify="left", foreground="#52635c",
        ).pack(anchor="w", pady=(3, 10))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Menubutton(
            toolbar, text="Open package", style="Accent.TButton",
            menu=self._open_menu(toolbar),
        ).pack(side="left")
        self.export_button = ttk.Button(
            toolbar, text="Export vehicle project…", state="disabled",
            command=self._export_project,
        )
        self.export_button.pack(side="left", padx=(7, 0))
        self.author_button = ttk.Button(
            toolbar, text="Create authoring workspace…", state="disabled",
            command=self._create_authoring_workspace,
        )
        self.author_button.pack(side="left", padx=(7, 0))
        self.package_button = ttk.Button(
            toolbar, text="Build installable package…", state="disabled",
            command=self._build_installable_package,
        )
        self.package_button.pack(side="left", padx=(7, 0))
        ttk.Button(
            toolbar, text="Help", command=self._show_help,
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(
            value="Open a package folder or archive to resolve its vehicle projects."
        )
        ttk.Label(
            toolbar, textvariable=self.status, foreground="#52635c",
        ).pack(side="right")

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        model_panel = ttk.LabelFrame(panes, text="Vehicles", padding=9)
        viewport_panel = ttk.LabelFrame(panes, text="Model viewport", padding=9)
        inspector_panel = ttk.LabelFrame(panes, text="Resolved project", padding=9)
        panes.add(model_panel, weight=2)
        panes.add(viewport_panel, weight=5)
        panes.add(inspector_panel, weight=3)

        self.model_tree = ttk.Treeview(
            model_panel, columns=("state",), show="tree headings", selectmode="browse",
        )
        self.model_tree.heading("#0", text="Model")
        self.model_tree.heading("state", text="Status")
        self.model_tree.column("#0", width=190, minwidth=130)
        self.model_tree.column("state", width=90, stretch=False)
        model_scroll = ttk.Scrollbar(
            model_panel, orient="vertical", command=self.model_tree.yview,
        )
        self.model_tree.configure(yscrollcommand=model_scroll.set)
        self.model_tree.pack(side="left", fill="both", expand=True)
        model_scroll.pack(side="right", fill="y")
        self.model_tree.bind("<<TreeviewSelect>>", self._select_model)

        viewport_toolbar = ttk.Frame(viewport_panel)
        viewport_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(
            viewport_toolbar, text="VIEW", style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 7))
        ttk.Button(
            viewport_toolbar, text="−", width=3,
            command=lambda: self._zoom_by(0.8),
        ).pack(side="left")
        self.zoom_label = ttk.Button(
            viewport_toolbar, text="100%", width=7, command=self._reset_zoom,
        )
        self.zoom_label.pack(side="left", padx=4)
        ttk.Button(
            viewport_toolbar, text="+", width=3,
            command=lambda: self._zoom_by(1.25),
        ).pack(side="left")
        ttk.Button(
            viewport_toolbar, text="Fit", command=self._fit_viewport,
        ).pack(side="left", padx=(7, 0))
        ttk.Label(
            viewport_toolbar, text="Wheel: zoom · left drag: pan · right drag: orbit",
            foreground="#52635c",
        ).pack(side="right")

        model_toolbar = ttk.Frame(viewport_panel)
        model_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(
            model_toolbar, text="MODEL", style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 7))
        ttk.Label(model_toolbar, text="Fragment").pack(side="left")
        self.fragment = tk.StringVar(value="Primary")
        self.fragment_combo = ttk.Combobox(
            model_toolbar, textvariable=self.fragment, state="readonly",
            width=12, values=(),
        )
        self.fragment_combo.pack(side="left", padx=(5, 10))
        self.fragment_combo.bind(
            "<<ComboboxSelected>>", self._select_fragment,
        )
        ttk.Label(model_toolbar, text="LOD").pack(side="left")
        self.lod = tk.StringVar(value="All")
        self.lod_combo = ttk.Combobox(
            model_toolbar, textvariable=self.lod, state="readonly",
            width=9, values=("All",),
        )
        self.lod_combo.pack(side="left", padx=(5, 10))
        self.lod_combo.bind("<<ComboboxSelected>>", self._select_lod)
        ttk.Label(model_toolbar, text="Component").pack(side="left")
        self.component = tk.StringVar(value="All")
        self.component_combo = ttk.Combobox(
            model_toolbar, textvariable=self.component, state="readonly",
            width=20, values=("All",),
        )
        self.component_combo.pack(side="left", padx=(5, 10))
        self.component_combo.bind(
            "<<ComboboxSelected>>", self._select_component,
        )
        camera_toolbar = ttk.Frame(viewport_panel)
        camera_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(
            camera_toolbar, text="CAMERA", style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 7))
        for label, yaw, pitch in (
            ("↶", -15.0, 0.0), ("↷", 15.0, 0.0),
            ("Tilt +", 0.0, 10.0), ("Tilt −", 0.0, -10.0),
        ):
            ttk.Button(
                camera_toolbar, text=label, width=6,
                command=lambda y=yaw, p=pitch: self._rotate_camera(y, p),
            ).pack(side="left", padx=(0, 4))
        ttk.Button(
            camera_toolbar, text="Reset camera", command=self._reset_camera,
        ).pack(side="left", padx=(3, 0))
        self.component_summary = tk.StringVar(
            value="Select a rendered component to inspect its material and texture links."
        )
        ttk.Label(
            viewport_panel, textvariable=self.component_summary,
            foreground="#7fae94", wraplength=700, justify="left",
        ).pack(fill="x", pady=(0, 7))

        self.viewport = tk.Canvas(
            viewport_panel, background="#101714", highlightthickness=0,
            cursor="fleur",
        )
        self.viewport.pack(fill="both", expand=True)
        self.viewport.bind("<Configure>", lambda _event: self._render_viewport())
        self.viewport.bind("<MouseWheel>", self._wheel_zoom)
        self.viewport.bind("<Control-MouseWheel>", self._wheel_zoom)
        self.viewport.bind("<ButtonPress-1>", self._begin_pan)
        self.viewport.bind("<B1-Motion>", self._continue_pan)
        self.viewport.bind("<ButtonRelease-1>", self._end_pan)
        self.viewport.bind("<ButtonPress-3>", self._begin_orbit)
        self.viewport.bind("<B3-Motion>", self._continue_orbit)
        self.viewport.bind("<ButtonRelease-3>", self._end_orbit)
        self.viewport_message = tk.StringVar(
            value="Select a vehicle to load its native model preview."
        )

        self.model_heading = tk.StringVar(value="No vehicle selected")
        self.model_summary = tk.StringVar(value="No package loaded")
        ttk.Label(
            inspector_panel, textvariable=self.model_heading,
            font=("Segoe UI Semibold", 13), foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            inspector_panel, textvariable=self.model_summary,
            foreground="#52635c", wraplength=330, justify="left",
        ).pack(fill="x", anchor="w", pady=(3, 9))

        inspector_tabs = ttk.Notebook(inspector_panel)
        inspector_tabs.pack(fill="both", expand=True)
        overview_tab = ttk.Frame(inspector_tabs, padding=7)
        author_tab = ttk.Frame(inspector_tabs, padding=7)
        appearance_tab = ttk.Frame(inspector_tabs, padding=7)
        tuning_builder_tab = ttk.Frame(inspector_tabs, padding=7)
        assets_tab = ttk.Frame(inspector_tabs, padding=7)
        inspector_tabs.add(overview_tab, text="Overview")
        inspector_tabs.add(author_tab, text="Author")
        inspector_tabs.add(appearance_tab, text="Appearance")
        inspector_tabs.add(tuning_builder_tab, text="Tuning Builder")
        inspector_tabs.add(assets_tab, text="Assets")

        self.details = tk.Text(
            overview_tab, wrap="word", relief="flat",
            background="#f4f7f5", foreground="#26332e", padx=8, pady=8,
        )
        self.details.pack(fill="both", expand=True)
        self.details.configure(state="disabled")

        author_fields = (
            ("Display label", "vehicle.gameName"),
            ("Make label", "vehicle.vehicleMakeName"),
            ("Texture dictionary", "vehicle.txdName"),
            ("Vehicle class", "vehicle.vehicleClass"),
            ("Vehicle type", "vehicle.type"),
            ("Layout", "vehicle.layout"),
            ("Audio profile", "vehicle.audioNameHash"),
            ("Mass", "handling.fMass"),
            ("Drive gears", "handling.nInitialDriveGears"),
            ("Drive force", "handling.fInitialDriveForce"),
            ("Max flat velocity", "handling.fInitialDriveMaxFlatVel"),
            ("Brake force", "handling.fBrakeForce"),
            ("Steering lock", "handling.fSteeringLock"),
            ("Light settings", "variation.lightSettings"),
            ("Tuning kits", "variation.kits"),
        )
        field_grid = ttk.Frame(author_tab)
        field_grid.pack(fill="x")
        for index, (label, key) in enumerate(author_fields):
            group = 0 if index < 8 else 1
            row = index if group == 0 else index - 8
            column = group * 2
            ttk.Label(field_grid, text=label).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2,
            )
            value = tk.StringVar()
            entry = ttk.Entry(
                field_grid, textvariable=value, width=18, state="disabled",
            )
            entry.grid(
                row=row, column=column + 1, sticky="ew",
                padx=(0 if group else 8, 0), pady=2,
            )
            self.authoring_values[key] = value
            self.authoring_inputs[key] = entry
        field_grid.columnconfigure(1, weight=1)
        field_grid.columnconfigure(3, weight=1)
        author_actions = ttk.Frame(author_tab)
        author_actions.pack(fill="x", pady=(8, 4))
        self.save_author_button = ttk.Button(
            author_actions, text="Apply + validate", state="disabled",
            command=self._save_authoring_fields,
        )
        self.save_author_button.pack(side="left")
        self.undo_author_button = ttk.Button(
            author_actions, text="Undo latest", state="disabled",
            command=self._undo_authoring_edit,
        )
        self.undo_author_button.pack(side="left", padx=(6, 0))
        self.authoring_status = tk.StringVar(
            value="Create an authoring workspace before editing package metadata."
        )
        ttk.Label(
            author_tab, textvariable=self.authoring_status,
            foreground="#52635c", wraplength=320, justify="left",
        ).pack(fill="x", anchor="w", pady=(4, 0))

        identity = ttk.LabelFrame(author_tab, text="Identity migration", padding=6)
        identity.pack(fill="x", pady=(8, 0))
        self.identity_model = tk.StringVar()
        self.identity_handling = tk.StringVar()
        ttk.Label(identity, text="Model").grid(row=0, column=0, sticky="w")
        self.identity_model_entry = ttk.Entry(
            identity, textvariable=self.identity_model, width=16, state="disabled",
        )
        self.identity_model_entry.grid(row=0, column=1, sticky="ew", padx=(4, 8))
        ttk.Label(identity, text="Handling").grid(row=0, column=2, sticky="w")
        self.identity_handling_entry = ttk.Entry(
            identity, textvariable=self.identity_handling, width=16, state="disabled",
        )
        self.identity_handling_entry.grid(row=0, column=3, sticky="ew", padx=(4, 8))
        self.identity_button = ttk.Button(
            identity, text="Migrate + validate", state="disabled",
            command=self._migrate_identity,
        )
        self.identity_button.grid(row=0, column=4)
        identity.columnconfigure(1, weight=1)
        identity.columnconfigure(3, weight=1)

        selection = ttk.LabelFrame(appearance_tab, text="Vehicle appearance", padding=6)
        selection.pack(fill="x")
        self.appearance_kits = tk.StringVar()
        self.appearance_light = tk.StringVar()
        self.appearance_siren = tk.StringVar()
        for row, (label, variable) in enumerate((
            ("Linked kits", self.appearance_kits),
            ("Light profile", self.appearance_light),
            ("Siren profile", self.appearance_siren),
        )):
            ttk.Label(selection, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(selection, textvariable=variable, state="disabled")
            entry.grid(
                row=row, column=1, sticky="ew", padx=(6, 0), pady=2,
            )
            self.appearance_edit_inputs.append(entry)
        selection.columnconfigure(1, weight=1)

        colors = ttk.LabelFrame(appearance_tab, text="Spawn colors and liveries", padding=6)
        colors.pack(fill="both", expand=True, pady=(7, 0))
        self.color_tree = ttk.Treeview(
            colors, columns=("indices", "liveries"), show="headings", height=5,
        )
        self.color_tree.heading("indices", text="Color indices")
        self.color_tree.heading("liveries", text="Enabled liveries")
        self.color_tree.column("indices", width=155, stretch=True)
        self.color_tree.column("liveries", width=105, stretch=True)
        self.color_tree.pack(fill="both", expand=True)
        self.color_tree.bind("<<TreeviewSelect>>", self._select_color_set)
        color_editor = ttk.Frame(colors)
        color_editor.pack(fill="x", pady=(5, 0))
        self.color_indices = tk.StringVar()
        self.color_liveries = tk.StringVar()
        ttk.Label(color_editor, text="Indices").grid(row=0, column=0, sticky="w")
        color_indices_entry = ttk.Entry(
            color_editor, textvariable=self.color_indices, state="disabled",
        )
        color_indices_entry.grid(
            row=0, column=1, sticky="ew", padx=(4, 7),
        )
        ttk.Label(color_editor, text="Livery numbers").grid(row=0, column=2, sticky="w")
        color_liveries_entry = ttk.Entry(
            color_editor, textvariable=self.color_liveries, state="disabled",
        )
        color_liveries_entry.grid(
            row=0, column=3, sticky="ew", padx=(4, 0),
        )
        self.appearance_edit_inputs.extend((
            color_indices_entry, color_liveries_entry,
        ))
        color_editor.columnconfigure(1, weight=1)
        color_editor.columnconfigure(3, weight=1)
        color_actions = ttk.Frame(colors)
        color_actions.pack(fill="x", pady=(5, 0))
        for label, command in (
            ("Add", self._add_color_set),
            ("Update selected", self._update_color_set),
            ("Remove", self._remove_color_set),
        ):
            button = ttk.Button(
                color_actions, text=label, command=command, state="disabled",
            )
            button.pack(side="left", padx=(5, 0) if label != "Add" else 0)
            self.appearance_edit_buttons.append(button)
        self.appearance_button = ttk.Button(
            color_actions, text="Apply appearance + validate", state="disabled",
            command=self._save_appearance,
        )
        self.appearance_button.pack(side="right")

        tuning = ttk.LabelFrame(appearance_tab, text="Tuning kit", padding=6)
        tuning.pack(fill="x", pady=(7, 0))
        self.tuning_kit = tk.StringVar()
        self.tuning_type = tk.StringVar()
        self.tuning_liveries = tk.StringVar()
        self.tuning_combo = ttk.Combobox(
            tuning, textvariable=self.tuning_kit, state="readonly", width=19,
        )
        self.tuning_combo.grid(row=0, column=0, sticky="ew")
        self.tuning_combo.bind("<<ComboboxSelected>>", self._select_tuning_kit)
        tuning_type_entry = ttk.Entry(
            tuning, textvariable=self.tuning_type, width=15, state="disabled",
        )
        tuning_type_entry.grid(
            row=0, column=1, sticky="ew", padx=(5, 0),
        )
        tuning_liveries_entry = ttk.Entry(
            tuning, textvariable=self.tuning_liveries, width=20, state="disabled",
        )
        tuning_liveries_entry.grid(
            row=0, column=2, sticky="ew", padx=(5, 0),
        )
        self.appearance_edit_inputs.extend((
            tuning_type_entry, tuning_liveries_entry,
        ))
        self.tuning_button = ttk.Button(
            tuning, text="Apply kit", state="disabled", command=self._save_tuning_kit,
        )
        self.tuning_button.grid(row=0, column=3, padx=(5, 0))
        for column in range(3):
            tuning.columnconfigure(column, weight=1)

        lights = ttk.LabelFrame(appearance_tab, text="Light profile definition", padding=6)
        lights.pack(fill="x", pady=(7, 0))
        self.light_profile = tk.StringVar()
        self.light_field = tk.StringVar()
        self.light_value = tk.StringVar()
        self.light_profile_combo = ttk.Combobox(
            lights, textvariable=self.light_profile, state="readonly", width=10,
        )
        self.light_profile_combo.grid(row=0, column=0, sticky="ew")
        self.light_profile_combo.bind("<<ComboboxSelected>>", self._select_light_profile)
        self.light_field_combo = ttk.Combobox(
            lights, textvariable=self.light_field, state="readonly", width=24,
        )
        self.light_field_combo.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        self.light_field_combo.bind("<<ComboboxSelected>>", self._select_light_field)
        light_value_entry = ttk.Entry(
            lights, textvariable=self.light_value, width=14, state="disabled",
        )
        light_value_entry.grid(
            row=0, column=2, sticky="ew", padx=(5, 0),
        )
        self.appearance_edit_inputs.append(light_value_entry)
        self.light_button = ttk.Button(
            lights, text="Apply field", state="disabled", command=self._save_light_field,
        )
        self.light_button.grid(row=0, column=3, padx=(5, 0))
        lights.columnconfigure(1, weight=1)

        self._build_tuning_builder(tuning_builder_tab)

        ttk.Label(
            assets_tab, text="PROJECT MEMBERS", style="FieldLabel.TLabel",
        ).pack(anchor="w", pady=(0, 4))
        asset_actions = ttk.Frame(assets_tab)
        asset_actions.pack(fill="x", pady=(0, 5))
        self.open_asset_button = ttk.Button(
            asset_actions, text="Open selected in Asset Viewer", state="disabled",
            command=self._open_selected_asset,
        )
        self.open_asset_button.pack(side="left")
        self.open_texture_button = ttk.Button(
            asset_actions, text="Open texture dictionary", state="disabled",
            command=self._open_texture_asset,
        )
        self.open_texture_button.pack(side="left", padx=(6, 0))
        asset_row = ttk.Frame(assets_tab)
        asset_row.pack(fill="both", expand=True)
        self.asset_tree = ttk.Treeview(
            asset_row, columns=("role",), show="tree headings", selectmode="browse",
        )
        self.asset_tree.heading("#0", text="Asset")
        self.asset_tree.heading("role", text="Role")
        self.asset_tree.column("#0", width=230, minwidth=140)
        self.asset_tree.column("role", width=120, stretch=False)
        asset_scroll = ttk.Scrollbar(
            asset_row, orient="vertical", command=self.asset_tree.yview,
        )
        self.asset_tree.configure(yscrollcommand=asset_scroll.set)
        self.asset_tree.pack(side="left", fill="both", expand=True)
        asset_scroll.pack(side="right", fill="y")
        self.asset_tree.bind("<<TreeviewSelect>>", self._select_project_asset)
        self.asset_tree.bind("<Double-1>", self._open_selected_asset)

        self._render_viewport()

    def _build_tuning_builder(self, parent: ttk.Frame) -> None:
        chooser = ttk.Frame(parent)
        chooser.pack(fill="x", pady=(0, 6))
        ttk.Label(chooser, text="Kit").grid(row=0, column=0, sticky="w")
        self.builder_kit = tk.StringVar()
        self.builder_kit_combo = ttk.Combobox(
            chooser, textvariable=self.builder_kit, state="readonly", width=18,
        )
        self.builder_kit_combo.grid(row=0, column=1, sticky="ew", padx=(5, 8))
        self.builder_kit_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_builder_kit,
        )
        ttk.Label(chooser, text="Group").grid(row=0, column=2, sticky="w")
        self.builder_collection = tk.StringVar(value="Visible parts")
        self.builder_collection_combo = ttk.Combobox(
            chooser, textvariable=self.builder_collection, state="readonly",
            values=tuple(TUNING_COLLECTION_LABELS), width=16,
        )
        self.builder_collection_combo.grid(row=0, column=3, sticky="ew", padx=(5, 0))
        self.builder_collection_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_collection,
        )
        chooser.columnconfigure(1, weight=1)
        chooser.columnconfigure(3, weight=1)
        self.tuning_builder_summary = tk.StringVar(
            value="Create an authoring workspace to build tuning parts."
        )
        ttk.Label(
            parent, textvariable=self.tuning_builder_summary,
            foreground="#52635c", wraplength=350, justify="left",
        ).pack(fill="x", pady=(0, 6))

        pages = ttk.Notebook(parent)
        pages.pack(fill="both", expand=True)
        self.tuning_pages = pages
        parts_page = ttk.Frame(pages, padding=5)
        validation_page = ttk.Frame(pages, padding=5)
        self.tuning_parts_page = parts_page
        self.tuning_validation_page = validation_page
        pages.add(parts_page, text="Parts and fields")
        pages.add(validation_page, text="Assets and checks")

        list_frame = ttk.Frame(parts_page)
        list_frame.pack(fill="both", expand=True)
        self.tuning_part_tree = ttk.Treeview(
            list_frame, columns=("type",), show="tree headings", height=6,
            selectmode="browse",
        )
        self.tuning_part_tree.heading("#0", text="Entry")
        self.tuning_part_tree.heading("type", text="Type")
        self.tuning_part_tree.column("#0", width=155, minwidth=100)
        self.tuning_part_tree.column("type", width=125, minwidth=80)
        part_scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.tuning_part_tree.yview,
        )
        self.tuning_part_tree.configure(yscrollcommand=part_scroll.set)
        self.tuning_part_tree.pack(side="left", fill="both", expand=True)
        part_scroll.pack(side="right", fill="y")
        self.tuning_part_tree.bind(
            "<<TreeviewSelect>>", self._select_tuning_builder_entry,
        )

        actions = ttk.Frame(parts_page)
        actions.pack(fill="x", pady=(5, 0))
        self.tuning_duplicate_button = ttk.Button(
            actions, text="Duplicate", state="disabled",
            command=self._duplicate_tuning_builder_entry,
        )
        self.tuning_duplicate_button.pack(side="left")
        self.tuning_remove_button = ttk.Button(
            actions, text="Remove", state="disabled",
            command=self._remove_tuning_builder_entry,
        )
        self.tuning_remove_button.pack(side="left", padx=(5, 0))
        self.tuning_up_button = ttk.Button(
            actions, text="↑", width=3, state="disabled",
            command=lambda: self._move_tuning_builder_entry(-1),
        )
        self.tuning_up_button.pack(side="right")
        self.tuning_down_button = ttk.Button(
            actions, text="↓", width=3, state="disabled",
            command=lambda: self._move_tuning_builder_entry(1),
        )
        self.tuning_down_button.pack(side="right", padx=(0, 4))

        create = ttk.LabelFrame(parts_page, text="Add entry", padding=5)
        create.pack(fill="x", pady=(7, 0))
        self.tuning_primary_label = ttk.Label(create, text="Model asset")
        self.tuning_primary_label.grid(row=0, column=0, sticky="w")
        self.tuning_new_primary = tk.StringVar()
        self.tuning_primary_entry = ttk.Entry(
            create, textvariable=self.tuning_new_primary, state="disabled",
        )
        self.tuning_primary_entry.grid(
            row=0, column=1, sticky="ew", padx=(5, 0),
        )
        self.tuning_secondary_label = ttk.Label(create, text="Shop label")
        self.tuning_secondary_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.tuning_new_secondary = tk.StringVar()
        self.tuning_secondary_entry = ttk.Entry(
            create, textvariable=self.tuning_new_secondary, state="disabled",
        )
        self.tuning_secondary_entry.grid(
            row=1, column=1, sticky="ew", padx=(5, 0), pady=(4, 0),
        )
        self.tuning_type_label = ttk.Label(create, text="Type")
        self.tuning_type_label.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.tuning_new_type = tk.StringVar(value="VMT_SPOILER")
        self.tuning_new_type_combo = ttk.Combobox(
            create, textvariable=self.tuning_new_type, state="disabled",
            values=VMT_TYPES,
        )
        self.tuning_new_type_combo.grid(
            row=2, column=1, sticky="ew", padx=(5, 0), pady=(4, 0),
        )
        self.tuning_add_button = ttk.Button(
            create, text="Add + validate", state="disabled",
            command=self._add_tuning_builder_entry,
        )
        self.tuning_add_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        create.columnconfigure(1, weight=1)

        fields = ttk.LabelFrame(parts_page, text="Selected entry fields", padding=5)
        fields.pack(fill="both", expand=True, pady=(7, 0))
        self.tuning_field_tree = ttk.Treeview(
            fields, columns=("value",), show="tree headings", height=6,
            selectmode="browse",
        )
        self.tuning_field_tree.heading("#0", text="Field")
        self.tuning_field_tree.heading("value", text="Value")
        self.tuning_field_tree.column("#0", width=125, minwidth=90)
        self.tuning_field_tree.column("value", width=170, minwidth=110)
        self.tuning_field_tree.pack(fill="both", expand=True)
        self.tuning_field_tree.bind(
            "<<TreeviewSelect>>", self._select_tuning_builder_field,
        )
        editor = ttk.Frame(fields)
        editor.pack(fill="x", pady=(5, 0))
        self.tuning_field = tk.StringVar()
        self.tuning_field_combo = ttk.Combobox(
            editor, textvariable=self.tuning_field, state="readonly", width=18,
        )
        self.tuning_field_combo.pack(side="left", fill="x", expand=True)
        self.tuning_field_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_builder_field,
        )
        self.tuning_field_value = tk.StringVar()
        self.tuning_field_value_entry = ttk.Entry(
            editor, textvariable=self.tuning_field_value, width=18,
            state="disabled",
        )
        self.tuning_field_value_entry.pack(
            side="left", fill="x", expand=True, padx=(5, 0),
        )
        self.tuning_field_button = ttk.Button(
            fields, text="Apply field + validate", state="disabled",
            command=self._apply_tuning_builder_field,
        )
        self.tuning_field_button.pack(fill="x", pady=(5, 0))

        asset_frame = ttk.LabelFrame(validation_page, text="Candidate assets", padding=5)
        asset_frame.pack(fill="both", expand=True)
        self.tuning_asset_tree = ttk.Treeview(
            asset_frame, columns=("kind", "status"), show="tree headings", height=8,
            selectmode="browse",
        )
        self.tuning_asset_tree.heading("#0", text="Asset")
        self.tuning_asset_tree.heading("kind", text="Kind")
        self.tuning_asset_tree.heading("status", text="Use")
        self.tuning_asset_tree.column("#0", width=160, minwidth=100)
        self.tuning_asset_tree.column("kind", width=100, minwidth=70)
        self.tuning_asset_tree.column("status", width=85, minwidth=60)
        self.tuning_asset_tree.pack(fill="both", expand=True)
        self.tuning_asset_tree.bind(
            "<<TreeviewSelect>>", self._select_tuning_asset,
        )
        self.tuning_asset_tree.bind("<Double-1>", self._open_tuning_asset)
        asset_actions = ttk.Frame(asset_frame)
        asset_actions.pack(fill="x", pady=(5, 0))
        self.tuning_use_asset_button = ttk.Button(
            asset_actions, text="Use for new part", state="disabled",
            command=self._use_tuning_asset,
        )
        self.tuning_use_asset_button.pack(side="left")
        self.tuning_open_asset_button = ttk.Button(
            asset_actions, text="Open in Asset Viewer", state="disabled",
            command=self._open_tuning_asset,
        )
        self.tuning_open_asset_button.pack(side="left", padx=(5, 0))

        checks = ttk.LabelFrame(validation_page, text="Validation", padding=5)
        checks.pack(fill="both", expand=True, pady=(7, 0))
        self.tuning_finding_tree = ttk.Treeview(
            checks, columns=("message",), show="tree headings", height=6,
        )
        self.tuning_finding_tree.heading("#0", text="Level")
        self.tuning_finding_tree.heading("message", text="Finding")
        self.tuning_finding_tree.column("#0", width=75, stretch=False)
        self.tuning_finding_tree.column("message", width=270, minwidth=170)
        self.tuning_finding_tree.pack(fill="both", expand=True)
        self.tuning_finding_tree.bind(
            "<Double-1>", self._open_tuning_finding,
        )
        self._change_tuning_collection()

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("vehicle-workbench")

    def _open_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open package folder…", command=self._choose_folder)
        menu.add_command(label="Open package archive…", command=self._choose_archive)
        return menu

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a loose vehicle DLC folder",
        )
        if selected:
            self.open_source(selected)

    def _choose_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select a vehicle package archive",
            filetypes=(("GTA package", "*.oiv *.zip *.rar *.7z"),
                       ("All files", "*.*")),
        )
        if selected:
            self.open_source(selected)

    def open_source(
        self, source: str | Path, scan: PackageScan | None = None,
        *, authoring_workspace: VehicleAuthoringWorkspace | None = None,
    ) -> None:
        self._cancel_scene_render()
        self.status.set("Resolving vehicle project…")
        self.update_idletasks()
        try:
            loaded_scan = scan or AddonPackageInspector().inspect(source)
            project = VehicleProjectResolver.inspect_scan(loaded_scan)
            reader = PackageAssetReader(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open vehicle package", str(exc), parent=self)
            self.status.set("Vehicle package could not be opened.")
            return
        self.source = Path(source).expanduser().resolve()
        self.scan = loaded_scan
        self.project = project
        self.reader = reader
        self.authoring_workspace = authoring_workspace
        self._scene_cache.clear()
        self._model_scene = None
        self.models.clear()
        self.model_tree.delete(*self.model_tree.get_children())
        for index, model in enumerate(project.models):
            item_id = f"model:{index}"
            self.models[item_id] = model
            state = "Ready" if model.complete else "Review"
            self.model_tree.insert("", "end", iid=item_id, text=model.model, values=(state,))
        self.export_button.configure(state="normal")
        self.author_button.configure(
            state="disabled" if authoring_workspace is not None else "normal",
            text=(
                "Authoring workspace active" if authoring_workspace is not None
                else "Create authoring workspace…"
            ),
        )
        self.package_button.configure(state="normal")
        self.status.set(
            f"{len(project.models)} vehicles · {project.error_count} errors · "
            f"{project.warning_count} warnings"
        )
        if project.models:
            self.model_tree.selection_set("model:0")
            self.model_tree.focus("model:0")
            self._select_model()
        else:
            self._clear_model("No vehicles.meta records were found in this package.")

    def _select_model(self, _event: object | None = None) -> None:
        selection = self.model_tree.selection()
        model = self.models.get(selection[0]) if selection else None
        if model is None:
            return
        self.selected_model = model
        self.model_heading.set(model.model)
        self.model_summary.set(
            f"{model.make_name or 'Unknown make'} · "
            f"{model.vehicle_class or 'Unknown class'} · "
            f"{'Complete links' if model.complete else 'Links need attention'}"
        )
        finding_text = "\n".join(
            f"{item.severity.upper()} {item.code}: {item.message}"
            for item in model.findings
        ) or "All visible vehicle links resolved."
        detail = (
            f"Display label: {model.display_name or '—'}\n"
            f"Handling: {model.handling_id or '—'}\n"
            f"Layout: {model.layout or '—'}\n"
            f"Audio: {model.audio_name_hash or '—'}\n"
            f"Texture dictionary: {model.texture_dictionary or '—'}\n"
            f"Tuning kits: {', '.join(model.tuning_kits) or 'None'}\n\n"
            f"{finding_text}"
        )
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", detail)
        self.details.configure(state="disabled")
        self.asset_tree.delete(*self.asset_tree.get_children())
        self.project_assets.clear()
        for index, asset in enumerate(model.assets):
            item_id = f"project-asset:{index}"
            self.project_assets[item_id] = asset.path
            self.asset_tree.insert(
                "", "end", iid=item_id, text=asset.path,
                values=(asset.role.replace("_", " ").title(),),
            )
        self.open_asset_button.configure(state="disabled")
        self._fragment_paths = {}
        self.open_texture_button.configure(
            state=(
                "normal" if model.texture_asset and self._on_open_asset is not None
                else "disabled"
            ),
        )
        if model.primary_model:
            self._fragment_paths["Primary"] = model.primary_model
        if model.high_detail_model:
            self._fragment_paths["High detail"] = model.high_detail_model
        fragment_values = tuple(self._fragment_paths)
        self.fragment_combo.configure(values=fragment_values)
        if fragment_values:
            self.fragment.set(fragment_values[0])
        self._load_authoring_fields(model)
        self._load_model_preview(model)

    def _clear_model(self, message: str) -> None:
        self.selected_model = None
        self._cancel_scene_render()
        self.model_heading.set("No vehicle selected")
        self.model_summary.set(message)
        self._source_image = None
        self._model_scene = None
        self._fragment_paths = {}
        self.fragment_combo.configure(values=())
        self.lod_combo.configure(values=("All",))
        self.lod.set("All")
        self.component_combo.configure(values=("All",))
        self.component.set("All")
        self.component_summary.set(
            "Select a rendered component to inspect its material and texture links."
        )
        self.open_texture_button.configure(state="disabled")
        for key, variable in self.authoring_values.items():
            variable.set("")
            self.authoring_inputs[key].configure(state="disabled")
        self.identity_model.set("")
        self.identity_handling.set("")
        self.identity_model_entry.configure(state="disabled")
        self.identity_handling_entry.configure(state="disabled")
        self.identity_button.configure(state="disabled")
        self._clear_appearance()
        self.save_author_button.configure(state="disabled")
        self.undo_author_button.configure(state="disabled")
        self._viewport_photo = None
        self._viewport_photo_zoom = None
        self.viewport_message.set(message)
        self._render_viewport()

    def _load_model_preview(self, model: VehicleProjectModel) -> None:
        self._cancel_scene_render()
        path = self._fragment_paths.get(
            self.fragment.get(), model.primary_model or model.high_detail_model or "",
        )
        if not path or self.reader is None or self.scan is None:
            self._source_image = None
            self._model_scene = None
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport_message.set("This vehicle has no visible YFT model to preview.")
            self._render_viewport()
            return
        cached = self._scene_cache.get(path.casefold())
        if cached is not None:
            self._activate_scene(path, cached)
            return
        entry = next(
            (item for item in self.scan.entries if item.path.casefold() == path.casefold()),
            None,
        )
        if entry is None:
            self._source_image = None
            self._model_scene = None
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport_message.set("The linked model is missing from the package inventory.")
            self._render_viewport()
            return
        self.viewport_message.set(f"Loading {path}…")
        self._source_image = None
        self._model_scene = None
        self._viewport_photo = None
        self._viewport_photo_zoom = None
        self._render_viewport()
        self.update_idletasks()
        try:
            content = self.reader.read(
                path, limit=native_preview_limit(path, entry.size),
            )
            report = NativeAssetInspector(
                self.project_root, self._native_game_path(),
            ).inspect_bytes(
                path, content.data, edition=self._native_edition(),
                truncated=content.truncated,
            )
            if report.model_scene is None:
                warning = "; ".join(report.warnings) or "No renderable geometry was found."
                raise ValueError(warning)
            self._scene_cache[path.casefold()] = report.model_scene
            while len(self._scene_cache) > 2:
                self._scene_cache.pop(next(iter(self._scene_cache)))
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            self._source_image = None
            self._model_scene = None
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport_message.set(f"Native preview unavailable: {exc}")
            self._render_viewport()
            return
        self._activate_scene(path, report.model_scene)

    def _activate_scene(self, path: str, scene: NativeModelScene) -> None:
        self._model_scene = scene
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self.lod_combo.configure(values=("All", *scene.lods))
        self.lod.set("All")
        component_names = tuple(dict.fromkeys(
            item.name for item in scene.components
        ))
        self.component_combo.configure(values=("All", *component_names))
        self.component.set("All")
        self._update_component_summary()
        self.viewport_message.set(path)
        self._render_model_scene(fit=True)

    def _render_model_scene(self, *, fit: bool = False) -> None:
        scene = self._model_scene
        if scene is None:
            return
        try:
            image_png, metadata = scene.render(
                yaw=self._camera_yaw, pitch=self._camera_pitch,
                lod=self.lod.get(), component=self.component.get(),
            )
            with Image.open(io.BytesIO(image_png)) as opened:
                self._source_image = opened.convert("RGBA").copy()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            self._source_image = None
            self.viewport_message.set(f"Model view unavailable: {exc}")
            self._render_viewport()
            return
        self._viewport_photo = None
        self._viewport_photo_zoom = None
        self.viewport_message.set(
            f"{scene.name} · yaw {metadata['model_camera_yaw']}° · "
            f"pitch {metadata['model_camera_pitch']}° · "
            f"LOD {metadata['model_camera_lod']} · "
            f"component {metadata['model_camera_component']}"
        )
        if fit:
            self._fit_viewport()
        else:
            self._render_viewport()

    def _native_edition(self) -> str:
        if self.project and self.project.edition.casefold() == "legacy":
            return "Legacy"
        return "Enhanced"

    def _native_game_path(self) -> Path | None:
        executable = "GTA5.exe" if self._native_edition() == "Legacy" else "GTA5_Enhanced.exe"
        matches = tuple(
            root for root in self.installation_roots if (root / executable).is_file()
        )
        if len(matches) == 1:
            return matches[0]
        if len(self.installation_roots) == 1:
            return self.installation_roots[0]
        return None

    def _export_project(self) -> None:
        if self.project is None or self.source is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for the vehicle project",
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.source.stem}-vehicle-project"
        try:
            manifest = self.project.write(destination)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not export vehicle project", str(exc), parent=self)
            return
        self.status.set(f"Exported vehicle project: {manifest}")

    def _create_authoring_workspace(self) -> None:
        if self.source is None or self.project is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for vehicle authoring workspace",
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.source.stem}-vehicle-authoring"
        self.status.set("Copying vehicle source into a safe authoring workspace…")
        self.update_idletasks()
        try:
            workspace = VehicleAuthoringWorkspace.create(self.source, destination)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Vehicle authoring workspace failed", str(exc), parent=self,
            )
            self.status.set("Vehicle authoring workspace was not created.")
            return
        self.open_source(
            workspace.source, authoring_workspace=workspace,
        )
        self.status.set(f"Authoring workspace active: {workspace.root}")

    def _load_authoring_fields(self, model: VehicleProjectModel) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            visible = {
                "vehicle.gameName": model.display_name,
                "vehicle.vehicleMakeName": model.make_name,
                "vehicle.txdName": model.texture_dictionary,
                "vehicle.vehicleClass": model.vehicle_class,
                "vehicle.type": model.vehicle_type,
                "vehicle.layout": model.layout,
                "vehicle.audioNameHash": model.audio_name_hash,
                "variation.kits": ", ".join(model.tuning_kits),
            }
            for key, variable in self.authoring_values.items():
                variable.set(visible.get(key, ""))
                self.authoring_inputs[key].configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.undo_author_button.configure(state="disabled")
            self.authoring_status.set(
                "Create an authoring workspace to edit this copied package safely."
            )
            self.identity_model.set(model.model)
            self.identity_handling.set(model.handling_id)
            self.identity_model_entry.configure(state="disabled")
            self.identity_handling_entry.configure(state="disabled")
            self.identity_button.configure(state="disabled")
            self._load_appearance(model, editable=False)
            return
        try:
            authored = workspace.values(model.model)
        except (OSError, ValueError) as exc:
            for key, variable in self.authoring_values.items():
                variable.set("")
                self.authoring_inputs[key].configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.undo_author_button.configure(state="disabled")
            self.authoring_status.set(f"Authoring unavailable: {exc}")
            self._clear_appearance()
            return
        for key, variable in self.authoring_values.items():
            variable.set(authored.values.get(key, ""))
            self.authoring_inputs[key].configure(state="normal")
        self.save_author_button.configure(state="normal")
        self.identity_model.set(model.model)
        self.identity_handling.set(model.handling_id)
        self.identity_model_entry.configure(state="normal")
        self.identity_handling_entry.configure(state="normal")
        self.identity_button.configure(state="normal")
        has_history = any(
            path.is_dir() and (path / "edit.json").is_file()
            and not path.name.endswith((".undone", ".undo-recovery"))
            for path in (workspace.root / "history").iterdir()
        )
        self.undo_author_button.configure(state="normal" if has_history else "disabled")
        self.authoring_status.set(
            f"Revision {workspace.revision}. Apply and identity migration revalidate "
            "all linked package metadata."
        )
        self._load_appearance(model, editable=True)

    def _clear_appearance(self) -> None:
        self._appearance_colors.clear()
        self._light_profiles.clear()
        self._tuning_kits.clear()
        self.color_tree.delete(*self.color_tree.get_children())
        for variable in (
            self.appearance_kits, self.appearance_light, self.appearance_siren,
            self.color_indices, self.color_liveries, self.tuning_kit,
            self.tuning_type, self.tuning_liveries, self.light_profile,
            self.light_field, self.light_value,
        ):
            variable.set("")
        self.tuning_combo.configure(values=())
        self.light_profile_combo.configure(values=())
        self.light_field_combo.configure(values=())
        self.appearance_button.configure(state="disabled")
        self.tuning_button.configure(state="disabled")
        self.light_button.configure(state="disabled")
        self._set_appearance_editor_state(False)
        self._clear_tuning_builder()

    def _set_appearance_editor_state(self, editable: bool) -> None:
        state = "normal" if editable else "disabled"
        for entry in self.appearance_edit_inputs:
            entry.configure(state=state)
        for button in self.appearance_edit_buttons:
            button.configure(state=state)

    def _load_appearance(
        self, model: VehicleProjectModel, *, editable: bool,
    ) -> None:
        self._clear_appearance()
        workspace = self.authoring_workspace
        if workspace is None:
            self.appearance_kits.set(", ".join(model.tuning_kits))
            return
        try:
            appearance = workspace.appearance(model.model)
        except (OSError, ValueError) as exc:
            self.authoring_status.set(f"Appearance authoring unavailable: {exc}")
            return
        self.appearance_kits.set(", ".join(appearance.kits))
        self.appearance_light.set(appearance.light_settings)
        self.appearance_siren.set(appearance.siren_settings)
        for color in appearance.colors:
            self._insert_color({
                "indices": list(color.indices), "liveries": list(color.liveries),
            })
        self._tuning_kits = {item.name: item for item in appearance.available_kits}
        linked_kits = {value.casefold() for value in appearance.kits}
        kit_names = tuple(
            item.name for item in appearance.available_kits
            if item.name.casefold() in linked_kits
            or item.kit_id.casefold() in linked_kits
        )
        self.tuning_combo.configure(values=kit_names)
        if kit_names:
            self.tuning_kit.set(kit_names[0])
            self._select_tuning_kit()
        self._light_profiles = {
            item.profile_id: dict(item.values) for item in appearance.light_profiles
        }
        profile_ids = tuple(self._light_profiles)
        self.light_profile_combo.configure(values=profile_ids)
        selected_profile = (
            appearance.light_settings
            if appearance.light_settings in self._light_profiles
            else (profile_ids[0] if profile_ids else "")
        )
        if selected_profile:
            self.light_profile.set(selected_profile)
            self._select_light_profile()
        if editable:
            self._set_appearance_editor_state(True)
            self.appearance_button.configure(state="normal")
            self.tuning_button.configure(state="normal" if kit_names else "disabled")
            self.light_button.configure(state="normal" if profile_ids else "disabled")
        self.builder_kit_combo.configure(values=kit_names)
        if kit_names:
            self.builder_kit.set(kit_names[0])
            self._load_tuning_builder(model.model, kit_names[0], editable=editable)

    def _clear_tuning_builder(self) -> None:
        self._tuning_entries.clear()
        self._tuning_assets.clear()
        self._tuning_findings.clear()
        self._tuning_editable = False
        for tree in (
            self.tuning_part_tree, self.tuning_field_tree,
            self.tuning_asset_tree, self.tuning_finding_tree,
        ):
            tree.delete(*tree.get_children())
        for variable in (
            self.builder_kit, self.tuning_new_primary,
            self.tuning_new_secondary, self.tuning_field,
            self.tuning_field_value,
        ):
            variable.set("")
        self.tuning_new_type.set("VMT_SPOILER")
        self.builder_kit_combo.configure(values=())
        self.tuning_field_combo.configure(values=())
        self.tuning_field_combo.configure(state="disabled")
        self.tuning_primary_entry.configure(state="disabled")
        self.tuning_secondary_entry.configure(state="disabled")
        self.tuning_new_type_combo.configure(state="disabled")
        self.tuning_field_value_entry.configure(state="disabled")
        for button in (
            self.tuning_add_button, self.tuning_duplicate_button,
            self.tuning_remove_button, self.tuning_up_button,
            self.tuning_down_button, self.tuning_field_button,
            self.tuning_use_asset_button, self.tuning_open_asset_button,
        ):
            button.configure(state="disabled")
        self.tuning_builder_summary.set(
            "Create an authoring workspace to build tuning parts."
        )

    def _current_tuning_collection(self) -> str:
        return TUNING_COLLECTION_LABELS.get(
            self.builder_collection.get(), TUNING_COLLECTIONS[0],
        )

    def _load_tuning_builder(
        self, model_name: str, kit_name: str, *, editable: bool,
        select_key: str = "",
    ) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            self._clear_tuning_builder()
            return
        try:
            builder = workspace.tuning_builder(model_name, kit_name)
        except (OSError, ValueError) as exc:
            self._clear_tuning_builder()
            self.tuning_builder_summary.set(f"Tuning Builder unavailable: {exc}")
            return
        self._tuning_editable = editable
        self.tuning_primary_entry.configure(
            state="normal" if editable else "disabled",
        )
        self.tuning_secondary_entry.configure(
            state="normal" if editable else "disabled",
        )
        self.builder_kit.set(builder.kit_name)
        self._tuning_entries = {item.key: item for item in builder.entries}
        self._tuning_assets.clear()
        self.tuning_asset_tree.delete(*self.tuning_asset_tree.get_children())
        for index, asset in enumerate(builder.assets):
            item_id = f"tuning-asset:{index}"
            self._tuning_assets[item_id] = asset
            self.tuning_asset_tree.insert(
                "", "end", iid=item_id, text=asset.name,
                values=(asset.kind, "Linked" if asset.referenced else "Available"),
            )
        self.tuning_finding_tree.delete(*self.tuning_finding_tree.get_children())
        self._tuning_findings.clear()
        if builder.findings:
            for index, finding in enumerate(builder.findings):
                item_id = f"finding:{index}"
                self._tuning_findings[item_id] = finding.entry
                self.tuning_finding_tree.insert(
                    "", "end", iid=item_id,
                    text=finding.severity.title(), values=(finding.message,),
                )
        else:
            self.tuning_finding_tree.insert(
                "", "end", iid="finding:ok", text="Ready",
                values=("No tuning relationship errors found.",),
            )
        self.tuning_builder_summary.set(
            f"{builder.kit_name} · {len(builder.entries)} entries · "
            f"{len(builder.assets)} candidate assets · "
            f"{builder.error_count} errors · {builder.warning_count} warnings"
        )
        self._change_tuning_collection(select_key=select_key)
        self.tuning_add_button.configure(
            state="normal" if editable else "disabled",
        )

    def _change_tuning_builder_kit(self, _event: object | None = None) -> None:
        model = self.selected_model
        if model is None or not self.builder_kit.get():
            return
        self._load_tuning_builder(
            model.model, self.builder_kit.get(), editable=self._tuning_editable,
        )

    def _change_tuning_collection(
        self, _event: object | None = None, *, select_key: str = "",
    ) -> None:
        collection = self._current_tuning_collection()
        self.tuning_part_tree.delete(*self.tuning_part_tree.get_children())
        self.tuning_field_tree.delete(*self.tuning_field_tree.get_children())
        for key, entry in self._tuning_entries.items():
            if entry.collection == collection:
                self.tuning_part_tree.insert(
                    "", "end", iid=key, text=entry.summary,
                    values=(entry.mod_type or "—",),
                )
        self.tuning_field.set("")
        self.tuning_field_value.set("")
        self.tuning_field_combo.configure(
            values=TUNING_FIELDS[collection], state="disabled",
        )
        self.tuning_field_value_entry.configure(state="disabled")
        self.tuning_duplicate_button.configure(state="disabled")
        self.tuning_remove_button.configure(state="disabled")
        self.tuning_up_button.configure(state="disabled")
        self.tuning_down_button.configure(state="disabled")
        self.tuning_field_button.configure(state="disabled")
        self.tuning_new_primary.set("")
        self.tuning_new_secondary.set("")
        if collection == "visibleMods":
            self.tuning_primary_label.configure(text="Model asset")
            self.tuning_secondary_label.configure(text="Shop label")
            self.tuning_secondary_label.grid()
            self.tuning_secondary_entry.grid()
            self.tuning_type_label.grid()
            self.tuning_new_type_combo.grid()
            self.tuning_new_type_combo.configure(
                state="readonly" if self._tuning_editable else "disabled",
            )
            self.tuning_new_type.set("VMT_SPOILER")
        elif collection == "linkMods":
            self.tuning_primary_label.configure(text="Model asset")
            self.tuning_secondary_label.configure(text="Attach bone")
            self.tuning_secondary_label.grid()
            self.tuning_secondary_entry.grid()
            self.tuning_type_label.grid_remove()
            self.tuning_new_type_combo.grid_remove()
            self.tuning_new_type_combo.configure(state="disabled")
            self.tuning_new_secondary.set("chassis")
        elif collection == "statMods":
            self.tuning_primary_label.configure(text="Identifier (optional)")
            self.tuning_secondary_label.configure(text="Modifier")
            self.tuning_secondary_label.grid()
            self.tuning_secondary_entry.grid()
            self.tuning_type_label.grid()
            self.tuning_new_type_combo.grid()
            self.tuning_new_type_combo.configure(
                state="readonly" if self._tuning_editable else "disabled",
            )
            self.tuning_new_secondary.set("25")
            self.tuning_new_type.set("VMT_ENGINE")
        else:
            self.tuning_primary_label.configure(text="Display label")
            self.tuning_secondary_label.grid_remove()
            self.tuning_secondary_entry.grid_remove()
            self.tuning_type_label.grid()
            self.tuning_new_type_combo.grid()
            self.tuning_new_type_combo.configure(
                state="readonly" if self._tuning_editable else "disabled",
            )
            self.tuning_new_type.set("VMT_SPOILER")
        if select_key and self.tuning_part_tree.exists(select_key):
            self.tuning_part_tree.selection_set(select_key)
            self.tuning_part_tree.focus(select_key)
            self._select_tuning_builder_entry()

    def _selected_tuning_entry(self) -> VehicleTuningEntry | None:
        selection = self.tuning_part_tree.selection()
        return self._tuning_entries.get(selection[0]) if selection else None

    def _select_tuning_builder_entry(self, _event: object | None = None) -> None:
        entry = self._selected_tuning_entry()
        self.tuning_field_tree.delete(*self.tuning_field_tree.get_children())
        if entry is None:
            return
        all_fields = tuple(dict.fromkeys((
            *TUNING_FIELDS[entry.collection], *entry.fields,
        )))
        self.tuning_field_combo.configure(values=all_fields)
        self.tuning_field_combo.configure(
            state="readonly" if self._tuning_editable else "disabled",
        )
        for index, (field, value) in enumerate(entry.fields.items()):
            self.tuning_field_tree.insert(
                "", "end", iid=f"tuning-field:{index}", text=field,
                values=(value or "—",),
            )
        state = "normal" if self._tuning_editable else "disabled"
        self.tuning_duplicate_button.configure(state=state)
        self.tuning_remove_button.configure(state=state)
        siblings = [
            item for item in self._tuning_entries.values()
            if item.collection == entry.collection
        ]
        self.tuning_up_button.configure(
            state=state if entry.index > 0 else "disabled",
        )
        self.tuning_down_button.configure(
            state=state if entry.index < len(siblings) - 1 else "disabled",
        )
        if all_fields:
            self.tuning_field.set(all_fields[0])
            self._change_tuning_builder_field()

    def _select_tuning_builder_field(self, _event: object | None = None) -> None:
        selection = self.tuning_field_tree.selection()
        if not selection:
            return
        field = self.tuning_field_tree.item(selection[0], "text")
        self.tuning_field.set(str(field))
        self._change_tuning_builder_field()

    def _change_tuning_builder_field(self, _event: object | None = None) -> None:
        entry = self._selected_tuning_entry()
        field = self.tuning_field.get()
        self.tuning_field_value.set(entry.fields.get(field, "") if entry else "")
        self.tuning_field_value_entry.configure(
            state=(
                "normal" if entry is not None and field and self._tuning_editable
                else "disabled"
            ),
        )
        self.tuning_field_button.configure(
            state=(
                "normal" if entry is not None and field and self._tuning_editable
                else "disabled"
            ),
        )

    def _tuning_operation_failed(self, title: str, exc: Exception) -> None:
        messagebox.showerror(title, str(exc), parent=self)
        self.status.set(f"Tuning edit rejected and rolled back: {exc}")

    def _reload_tuning_builder(self, select_key: str = "") -> None:
        model = self.selected_model
        if model is None:
            return
        kit = self.builder_kit.get()
        collection = self.builder_collection.get()
        self._reload_authoring_model(model.model)
        if kit in self._tuning_kits and self.builder_kit.get() != kit:
            self.builder_kit.set(kit)
            self._load_tuning_builder(model.model, kit, editable=True)
        self.builder_collection.set(collection)
        self._change_tuning_collection(select_key=select_key)

    def _add_tuning_builder_entry(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None or not self.builder_kit.get():
            return
        collection = self._current_tuning_collection()
        primary = self.tuning_new_primary.get().strip()
        secondary = self.tuning_new_secondary.get().strip()
        if collection == "visibleMods":
            values = {
                "modelName": primary, "modShopLabel": secondary,
                "type": self.tuning_new_type.get(), "bone": "chassis",
            }
        elif collection == "linkMods":
            values = {"modelName": primary, "bone": secondary or "chassis"}
        elif collection == "statMods":
            values = {
                "identifier": primary, "modifier": secondary,
                "type": self.tuning_new_type.get(),
            }
        else:
            values = {"name": primary, "slot": self.tuning_new_type.get()}
        try:
            result = workspace.add_tuning_entry(
                model.model, self.builder_kit.get(), collection, values,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._tuning_operation_failed("Tuning entry rejected", exc)
            return
        self._reload_tuning_builder()
        self.status.set(f"Added tuning entry · revision {result.revision}")

    def _duplicate_tuning_builder_entry(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        entry = self._selected_tuning_entry()
        if workspace is None or model is None or entry is None:
            return
        overrides: dict[str, str] = {}
        if entry.collection in {"visibleMods", "linkMods"}:
            replacement = self.tuning_new_primary.get().strip()
            if replacement and replacement != entry.fields.get("modelName"):
                overrides["modelName"] = replacement
        elif entry.collection == "statMods":
            replacement = self.tuning_new_primary.get().strip()
            if replacement and replacement != entry.fields.get("identifier"):
                overrides["identifier"] = replacement
        elif entry.collection == "slotNames":
            replacement = self.tuning_new_type.get().strip()
            if replacement and replacement != entry.fields.get("slot"):
                overrides["slot"] = replacement
        try:
            result = workspace.add_tuning_entry(
                model.model, self.builder_kit.get(), entry.collection,
                overrides, duplicate_index=entry.index,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._tuning_operation_failed(
                "Tuning duplicate rejected",
                ValueError(
                    f"{exc} Enter a unique asset, identifier, or slot in Add entry first."
                ),
            )
            return
        self._reload_tuning_builder()
        self.status.set(f"Duplicated tuning entry · revision {result.revision}")

    def _remove_tuning_builder_entry(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        entry = self._selected_tuning_entry()
        if workspace is None or model is None or entry is None:
            return
        if not messagebox.askyesno(
            "Remove tuning entry",
            f"Remove {entry.summary}? The edit can be undone from the Author tab.",
            parent=self,
        ):
            return
        try:
            result = workspace.remove_tuning_entry(
                model.model, self.builder_kit.get(), entry.collection, entry.index,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._tuning_operation_failed("Tuning removal rejected", exc)
            return
        self._reload_tuning_builder()
        self.status.set(f"Removed tuning entry · revision {result.revision}")

    def _move_tuning_builder_entry(self, direction: int) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        entry = self._selected_tuning_entry()
        if workspace is None or model is None or entry is None:
            return
        target = entry.index + direction
        try:
            result = workspace.move_tuning_entry(
                model.model, self.builder_kit.get(), entry.collection,
                entry.index, target,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._tuning_operation_failed("Tuning reorder rejected", exc)
            return
        self._reload_tuning_builder(f"{entry.collection}:{target}")
        self.status.set(f"Reordered tuning entry · revision {result.revision}")

    def _apply_tuning_builder_field(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        entry = self._selected_tuning_entry()
        field = self.tuning_field.get()
        if workspace is None or model is None or entry is None or not field:
            return
        try:
            result = workspace.update_tuning_entry(
                model.model, self.builder_kit.get(), entry.collection,
                entry.index, {field: self.tuning_field_value.get()},
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._tuning_operation_failed("Tuning field rejected", exc)
            return
        self._reload_tuning_builder(entry.key)
        self.status.set(f"Applied tuning field · revision {result.revision}")

    def _select_tuning_asset(self, _event: object | None = None) -> None:
        selection = self.tuning_asset_tree.selection()
        asset = self._tuning_assets.get(selection[0]) if selection else None
        self.tuning_use_asset_button.configure(
            state=(
                "normal" if asset is not None and asset.kind == "Model"
                and self._tuning_editable else "disabled"
            ),
        )
        self.tuning_open_asset_button.configure(
            state=(
                "normal" if asset is not None and self._on_open_asset is not None
                else "disabled"
            ),
        )

    def _use_tuning_asset(self) -> None:
        selection = self.tuning_asset_tree.selection()
        asset = self._tuning_assets.get(selection[0]) if selection else None
        if asset is None or asset.kind != "Model":
            return
        if self._current_tuning_collection() not in {"visibleMods", "linkMods"}:
            self.builder_collection.set("Visible parts")
            self._change_tuning_collection()
        self.tuning_new_primary.set(asset.name)
        self.tuning_pages.select(self.tuning_parts_page)

    def _open_tuning_finding(self, _event: object | None = None) -> None:
        selection = self.tuning_finding_tree.selection()
        entry_key = self._tuning_findings.get(selection[0], "") if selection else ""
        if not entry_key or ":" not in entry_key:
            return
        collection, _index = entry_key.split(":", 1)
        label = TUNING_COLLECTION_NAMES.get(collection)
        if label is None:
            return
        self.builder_collection.set(label)
        self._change_tuning_collection(select_key=entry_key)
        self.tuning_pages.select(self.tuning_parts_page)

    def _open_tuning_asset(self, _event: object | None = None) -> None:
        selection = self.tuning_asset_tree.selection()
        asset = self._tuning_assets.get(selection[0]) if selection else None
        if asset is not None and self._on_open_asset is not None:
            self._on_open_asset(asset.path)

    def _insert_color(self, color: dict[str, object]) -> str:
        indices = [int(value) for value in color.get("indices", [])]  # type: ignore[arg-type]
        liveries = [bool(value) for value in color.get("liveries", [])]  # type: ignore[arg-type]
        enabled = [str(index) for index, value in enumerate(liveries, start=1) if value]
        item = self.color_tree.insert(
            "", "end", values=(", ".join(map(str, indices)), ", ".join(enabled) or "None"),
        )
        self._appearance_colors[item] = {"indices": indices, "liveries": liveries}
        return item

    def _color_from_editor(self) -> dict[str, object]:
        try:
            indices = [
                int(value) for value in self.color_indices.get().replace(",", " ").split()
            ]
            selected = [
                int(value) for value in self.color_liveries.get().replace(",", " ").split()
            ]
        except ValueError as exc:
            raise ValueError("Color indices and livery numbers must be integers") from exc
        if not 4 <= len(indices) <= 8 or any(value < 0 or value > 255 for value in indices):
            raise ValueError("Enter 4 through 8 color indices, each from 0 through 255")
        if any(value < 1 or value > 64 for value in selected):
            raise ValueError("Livery numbers must be from 1 through 64")
        flags = [False] * (max(selected) if selected else 0)
        for number in selected:
            flags[number - 1] = True
        return {"indices": indices, "liveries": flags}

    def _select_color_set(self, _event: object | None = None) -> None:
        selection = self.color_tree.selection()
        if not selection:
            return
        color = self._appearance_colors[selection[0]]
        indices = color["indices"]
        liveries = color["liveries"]
        self.color_indices.set(", ".join(str(value) for value in indices))  # type: ignore[union-attr]
        self.color_liveries.set(", ".join(
            str(index) for index, enabled in enumerate(liveries, start=1) if enabled
        ))  # type: ignore[arg-type]

    def _add_color_set(self) -> None:
        try:
            item = self._insert_color(self._color_from_editor())
        except ValueError as exc:
            messagebox.showerror("Invalid color set", str(exc), parent=self)
            return
        self.color_tree.selection_set(item)

    def _update_color_set(self) -> None:
        selection = self.color_tree.selection()
        if not selection:
            return
        try:
            color = self._color_from_editor()
        except ValueError as exc:
            messagebox.showerror("Invalid color set", str(exc), parent=self)
            return
        item = selection[0]
        self._appearance_colors[item] = color
        indices = color["indices"]
        liveries = color["liveries"]
        enabled = [
            str(index) for index, value in enumerate(liveries, start=1) if value
        ]  # type: ignore[arg-type]
        self.color_tree.item(
            item, values=(", ".join(map(str, indices)), ", ".join(enabled) or "None"),
        )  # type: ignore[arg-type]

    def _remove_color_set(self) -> None:
        for item in self.color_tree.selection():
            self._appearance_colors.pop(item, None)
            self.color_tree.delete(item)

    def _save_appearance(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        try:
            result = workspace.update_appearance(
                model.model,
                colors=[
                    self._appearance_colors[item]
                    for item in self.color_tree.get_children()
                ],
                kits=[
                    value.strip() for value in self.appearance_kits.get().split(",")
                    if value.strip()
                ],
                light_settings=self.appearance_light.get(),
                siren_settings=self.appearance_siren.get(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Appearance edit rejected", str(exc), parent=self)
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Applied vehicle appearance · revision {result.revision}")

    def _select_tuning_kit(self, _event: object | None = None) -> None:
        kit = self._tuning_kits.get(self.tuning_kit.get())
        if kit is None:
            return
        self.tuning_type.set(str(getattr(kit, "kit_type", "")))
        self.tuning_liveries.set(", ".join(getattr(kit, "livery_names", ())))

    def _save_tuning_kit(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None or not self.tuning_kit.get():
            return
        try:
            result = workspace.update_tuning_kit(
                model.model, self.tuning_kit.get(), kit_type=self.tuning_type.get(),
                livery_names=[
                    value.strip() for value in self.tuning_liveries.get().split(",")
                    if value.strip()
                ],
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Tuning-kit edit rejected", str(exc), parent=self)
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Applied tuning-kit metadata · revision {result.revision}")

    def _select_light_profile(self, _event: object | None = None) -> None:
        values = self._light_profiles.get(self.light_profile.get(), {})
        fields = tuple(key for key in values if key != "id")
        self.light_field_combo.configure(values=fields)
        self.light_field.set(fields[0] if fields else "")
        self._select_light_field()

    def _select_light_field(self, _event: object | None = None) -> None:
        values = self._light_profiles.get(self.light_profile.get(), {})
        self.light_value.set(values.get(self.light_field.get(), ""))

    def _save_light_field(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None or not self.light_field.get():
            return
        try:
            result = workspace.update_light_profile(
                model.model, self.light_profile.get(),
                {self.light_field.get(): self.light_value.get()},
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Light-profile edit rejected", str(exc), parent=self)
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Applied light-profile field · revision {result.revision}")

    def _migrate_identity(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        target_model = self.identity_model.get().strip()
        target_handling = self.identity_handling.get().strip()
        if not messagebox.askyesno(
            "Migrate vehicle identity",
            "Rename linked metadata references and streamed model/texture files? "
            "A complete undo snapshot will be retained.", parent=self,
        ):
            return
        try:
            result = workspace.migrate_identity(
                model.model, new_model=target_model, new_handling=target_handling,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Identity migration rejected", str(exc), parent=self)
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Migrated vehicle identity · revision {result.revision}")

    def _save_authoring_fields(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        try:
            current = workspace.values(model.model)
            updates = {
                key: variable.get()
                for key, variable in self.authoring_values.items()
                if variable.get().strip() != current.values.get(key, "")
            }
            result = workspace.update(model.model, updates)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Vehicle edit rejected", str(exc), parent=self)
            self.authoring_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_model(model.model)
        self.status.set(
            f"Applied {len(result.changes)} vehicle fields · revision {result.revision}"
        )

    def _undo_authoring_edit(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        try:
            result = workspace.undo()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Vehicle undo failed", str(exc), parent=self)
            self.authoring_status.set(f"Undo failed: {exc}")
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Restored latest vehicle edit · revision {result.revision}")

    def _reload_authoring_model(self, model_name: str) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            return
        self.open_source(workspace.source, authoring_workspace=workspace)
        match = next((
            item_id for item_id, item in self.models.items()
            if item.model.casefold() == model_name.casefold()
        ), None)
        if match is not None:
            self.model_tree.selection_set(match)
            self.model_tree.focus(match)
            self._select_model()

    def _build_installable_package(self) -> None:
        if self.project is None or self.source is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for installable vehicle package",
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.source.stem}-allin1-package"
        self.status.set("Building and validating installable vehicle package…")
        self.update_idletasks()
        try:
            package_source = (
                self.authoring_workspace.publish_source()
                if self.authoring_workspace is not None else self.source
            )
            result = VehicleAddonPackageBuilder(
                self.project_root, self._native_game_path(),
            ).build(package_source, destination)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Vehicle package build failed", str(exc), parent=self,
            )
            self.status.set("Vehicle package was not published.")
            return
        self.status.set(
            f"Validated package {result.mod_id}: {result.manifest}"
        )

    def _select_fragment(self, _event: object | None = None) -> None:
        if self.selected_model is not None:
            self._load_model_preview(self.selected_model)

    def _select_project_asset(self, _event: object | None = None) -> None:
        selection = self.asset_tree.selection()
        enabled = bool(
            selection and selection[0] in self.project_assets
            and self._on_open_asset is not None
        )
        self.open_asset_button.configure(state="normal" if enabled else "disabled")

    def _open_selected_asset(self, _event: object | None = None) -> None:
        selection = self.asset_tree.selection()
        path = self.project_assets.get(selection[0]) if selection else None
        if path is not None and self._on_open_asset is not None:
            self._on_open_asset(path)

    def _open_texture_asset(self) -> None:
        model = self.selected_model
        if (
            model is not None and model.texture_asset is not None
            and self._on_open_asset is not None
        ):
            self._on_open_asset(model.texture_asset)

    def _select_lod(self, _event: object | None = None) -> None:
        if self._model_scene is not None:
            available = {
                item.name for item in self._model_scene.components
                if self.lod.get().casefold() == "all"
                or item.lod.casefold() == self.lod.get().casefold()
            }
            if self.component.get() != "All" and self.component.get() not in available:
                self.component.set("All")
        self._update_component_summary()
        self._render_model_scene()

    def _select_component(self, _event: object | None = None) -> None:
        self._update_component_summary()
        self._render_model_scene()

    def _update_component_summary(self) -> None:
        scene = self._model_scene
        selected = self.component.get()
        if scene is None or selected == "All":
            if scene is None:
                text = "No decoded model scene is loaded."
            else:
                texture_count = len({
                    name for item in scene.components for name in item.texture_names
                })
                text = (
                    f"{len(scene.components)} components · {len(scene.materials)} materials · "
                    f"{texture_count} named texture references"
                )
            self.component_summary.set(text)
            return
        matches = [
            item for item in scene.components
            if item.name == selected
            and (
                self.lod.get().casefold() == "all"
                or item.lod.casefold() == self.lod.get().casefold()
            )
        ]
        if not matches:
            self.component_summary.set("The selected component is not present in this LOD.")
            return
        materials = sorted({
            value for item in matches for value in item.material_names
        }, key=str.casefold)
        textures = sorted({
            value for item in matches for value in item.texture_names
        }, key=str.casefold)
        self.component_summary.set(
            f"{selected} · LOD {', '.join(dict.fromkeys(item.lod for item in matches))} · "
            f"{sum(item.geometry_count for item in matches)} geometries · "
            f"materials: {', '.join(materials) or 'unbound'} · "
            f"textures: {', '.join(textures) or 'none named'}"
        )

    def _rotate_camera(self, yaw: float, pitch: float) -> None:
        if self._model_scene is None:
            return
        self._camera_yaw = (self._camera_yaw + yaw) % 360.0
        self._camera_pitch = min(89.0, max(-89.0, self._camera_pitch + pitch))
        self._render_model_scene()

    def _reset_camera(self) -> None:
        if self._model_scene is None:
            return
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self.lod.set("All")
        self.component.set("All")
        self._update_component_summary()
        self._render_model_scene(fit=True)

    def _schedule_scene_render(self) -> None:
        self._cancel_scene_render()
        self._render_job = self.after(40, self._run_scheduled_scene_render)

    def _run_scheduled_scene_render(self) -> None:
        self._render_job = None
        self._render_model_scene()

    def _cancel_scene_render(self) -> None:
        if self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None

    def _wheel_zoom(self, event: tk.Event) -> str:
        self._zoom_by(1.12 if event.delta > 0 else 1 / 1.12)
        return "break"

    def _zoom_by(self, factor: float) -> None:
        if self._source_image is None:
            return
        self._zoom = min(4.0, max(0.08, self._zoom * factor))
        self._render_viewport()

    def _reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan_x = self._pan_y = 0.0
        self._render_viewport()

    def _fit_viewport(self) -> None:
        image = self._source_image
        if image is None:
            return
        width = max(1, self.viewport.winfo_width() - 28)
        height = max(1, self.viewport.winfo_height() - 28)
        self._zoom = min(width / image.width, height / image.height)
        self._zoom = min(4.0, max(0.08, self._zoom))
        self._pan_x = self._pan_y = 0.0
        self._render_viewport()

    def _begin_pan(self, event: tk.Event) -> None:
        self._drag_origin = (event.x, event.y)
        self._drag_pan = (self._pan_x, self._pan_y)

    def _continue_pan(self, event: tk.Event) -> None:
        if self._drag_origin is None or self._drag_pan is None:
            return
        self._pan_x = self._drag_pan[0] + event.x - self._drag_origin[0]
        self._pan_y = self._drag_pan[1] + event.y - self._drag_origin[1]
        self._render_viewport()

    def _end_pan(self, _event: tk.Event) -> None:
        self._drag_origin = None
        self._drag_pan = None

    def _begin_orbit(self, event: tk.Event) -> None:
        if self._model_scene is None:
            return
        self._orbit_origin = (event.x, event.y)
        self._orbit_camera = (self._camera_yaw, self._camera_pitch)

    def _continue_orbit(self, event: tk.Event) -> None:
        if self._orbit_origin is None or self._orbit_camera is None:
            return
        delta_x = event.x - self._orbit_origin[0]
        delta_y = event.y - self._orbit_origin[1]
        self._camera_yaw = (self._orbit_camera[0] + delta_x * 0.45) % 360.0
        self._camera_pitch = min(
            89.0, max(-89.0, self._orbit_camera[1] - delta_y * 0.3),
        )
        self._schedule_scene_render()

    def _end_orbit(self, _event: tk.Event) -> None:
        if self._orbit_origin is None:
            return
        self._orbit_origin = None
        self._orbit_camera = None
        self._cancel_scene_render()
        self._render_model_scene()

    def _render_viewport(self) -> None:
        self.viewport.delete("all")
        width = max(1, self.viewport.winfo_width())
        height = max(1, self.viewport.winfo_height())
        for x in range(0, width, 48):
            self.viewport.create_line(x, 0, x, height, fill="#18231e")
        for y in range(0, height, 48):
            self.viewport.create_line(0, y, width, y, fill="#18231e")
        if self._source_image is None:
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport.create_text(
                width / 2, height / 2, text=self.viewport_message.get(),
                fill="#afc5b9", width=max(200, width - 80), justify="center",
                font=("Segoe UI", 10),
            )
            self.zoom_label.configure(text="100%")
            return
        if self._viewport_photo is None or self._viewport_photo_zoom != self._zoom:
            scaled_width = max(1, round(self._source_image.width * self._zoom))
            scaled_height = max(1, round(self._source_image.height * self._zoom))
            rendered = self._source_image.resize(
                (scaled_width, scaled_height), Image.Resampling.LANCZOS,
            )
            self._viewport_photo = ImageTk.PhotoImage(rendered)
            self._viewport_photo_zoom = self._zoom
        self.viewport.create_image(
            width / 2 + self._pan_x, height / 2 + self._pan_y,
            image=self._viewport_photo, anchor="center",
        )
        self.viewport.create_text(
            12, height - 12, text=self.viewport_message.get(), anchor="sw",
            fill="#afc5b9", font=("Segoe UI", 9),
        )
        self.zoom_label.configure(text=f"{self._zoom * 100:.0f}%")
