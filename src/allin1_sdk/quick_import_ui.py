"""Embedded guided-import workspace for common GTA add-on packages.

The quick path prepares validated content in the launcher's shared package
library.  It never installs into GTA V.  Advanced metadata and native-asset
authoring remain in the consolidated Content Workbench.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk
from typing import Callable

from allin1_sdk.vehicle_catalog import (
    ROAD_TRAFFIC_CATEGORIES,
    STORAGE_KINDS,
    VEHICLE_CATEGORIES,
)
from allin1_sdk.managed_package_conversion import storage_for_category
from allin1_sdk.vehicle_oiv_export import (
    LegacyVehicleOivExporter,
    LegacyVehicleOivResult,
)
from allin1_sdk.vehicle_quick_import import (
    PreparedVehicleQuickImport,
    VehicleQuickImportInspection,
    VehicleQuickImportReview,
    VehicleQuickImportService,
    launcher_package_library_root,
)


WorkbenchRoute = Callable[[str], None]
HelpRoute = Callable[[str], None]
LauncherRoute = Callable[[str, bool], None]

SIZE_TIER_LABELS = {
    0: "Standard — regular garage spaces",
    1: "Large — left-row garage spaces",
    2: "Oversize — Harmony floor garage only",
}
SIZE_TIER_VALUES = {label: value for value, label in SIZE_TIER_LABELS.items()}


class QuickImportFrame(ttk.Frame):
    """Prepare straightforward vehicle packages without leaving the SDK shell."""

    def __init__(
        self,
        parent: tk.Misc,
        project_root: str | Path,
        *,
        installation_roots: tuple[Path, ...] = (),
        on_help: HelpRoute | None = None,
        on_open_workbench: WorkbenchRoute | None = None,
        on_open_launcher: LauncherRoute | None = None,
        service: VehicleQuickImportService | None = None,
        library_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).expanduser().resolve()
        self.installation_roots = tuple(
            Path(item).expanduser().resolve() for item in installation_roots
        )
        self._on_help = on_help
        self._on_open_workbench = on_open_workbench
        self._on_open_launcher = on_open_launcher
        self._vehicle_service = service
        self._vehicle_services: dict[str, VehicleQuickImportService] = {}
        self.library_root = (
            Path(library_root).expanduser().resolve()
            if library_root is not None else None
        )

        self.source: Path | None = None
        self.inspection: VehicleQuickImportInspection | None = None
        self.review: VehicleQuickImportReview | None = None
        self.prepared: PreparedVehicleQuickImport | None = None
        self._plans_by_edition: dict[str, VehicleQuickImportReview] = {}
        self._drafts_by_edition: dict[str, dict[str, dict[str, object]]] = {}
        self._package_by_edition: dict[str, dict[str, str]] = {}
        self._active_edition = ""
        self._active_model = ""
        self._busy = False
        self._dirty = False
        self._dirty_editions: set[str] = set()
        self._loading_form = False
        self._restoring_selection = False
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.status = tk.StringVar(
            value="Open a vehicle DLC RPF, package archive, or extracted folder. "
            "GTA V will not be changed."
        )
        self.validation = tk.StringVar()
        self.source_summary = tk.StringVar(value="No package selected")
        self.destination_summary = tk.StringVar(
            value=f"Launcher library: {self._default_library_root()}"
        )
        self.edition = tk.StringVar()
        self.package_id = tk.StringVar()
        self.package_name = tk.StringVar()
        self.package_version = tk.StringVar(value="1.0.0")
        self.model_name = tk.StringVar(value="No vehicle selected")
        self.listing_name = tk.StringVar()
        self.manufacturer = tk.StringVar()
        self.category = tk.StringVar(value="special")
        self.price = tk.StringVar(value="0")
        self.free_price_confirmed = tk.BooleanVar(value=False)
        self.storage = tk.StringVar(value="garage")
        self.size_tier = tk.StringVar(value=SIZE_TIER_LABELS[0])
        self.custom_preview = tk.BooleanVar(value=False)
        self.preview_help = tk.StringVar(
            value="GBAY placeholder selected. No game texture needs to be authored."
        )
        self.preview_dictionary = tk.StringVar()
        self.preview_texture = tk.StringVar()
        self.traffic_enabled = tk.BooleanVar(value=False)
        self.traffic_weight = tk.StringVar(value="1.0")

        self._build()
        self._bind_dirty_tracking()
        self._refresh_control_states()

    def _default_library_root(self) -> Path:
        return self.library_root or launcher_package_library_root()

    def _matching_installation_root(self, edition: str | None = None) -> Path:
        roots = tuple(dict.fromkeys(
            item for item in self.installation_roots if item.is_dir()
        ))
        selected = (edition or "").strip().casefold()
        executable = {
            "legacy": "GTA5.exe", "enhanced": "GTA5_Enhanced.exe",
        }.get(selected)
        if executable:
            matches = tuple(item for item in roots if (item / executable).is_file())
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise ValueError(
                    f"More than one configured {selected.title()} GTA V root matches "
                    f"{executable}; choose one active installation in Launcher."
                )
            if roots:
                raise ValueError(
                    f"No configured {selected.title()} GTA V root contains {executable}."
                )
        if len(roots) == 1:
            return roots[0]
        if len(roots) > 1:
            enhanced = tuple(
                item for item in roots if (item / "GTA5_Enhanced.exe").is_file()
            )
            if len(enhanced) == 1:
                return enhanced[0]
            raise ValueError(
                "Multiple GTA V installations are configured. Select an edition "
                "before analyzing this package."
            )
        raise ValueError(
            "A matching GTA V installation is required to inspect vehicle archives."
        )

    def _preferred_configured_edition(self) -> str | None:
        roots = tuple(item for item in self.installation_roots if item.is_dir())
        enhanced = tuple(
            item for item in roots if (item / "GTA5_Enhanced.exe").is_file()
        )
        legacy = tuple(item for item in roots if (item / "GTA5.exe").is_file())
        if len(enhanced) == 1:
            return "enhanced"
        if len(legacy) == 1:
            return "legacy"
        return None

    def _service(self, edition: str | None = None) -> VehicleQuickImportService:
        if self._vehicle_service is not None:
            return self._vehicle_service
        selected = (edition or "auto").strip().casefold()
        cached = self._vehicle_services.get(selected)
        if cached is not None:
            return cached
        game = self._matching_installation_root(edition)
        service = VehicleQuickImportService(self.project_root, game)
        self._vehicle_services[selected] = service
        return service

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(12, 9, 12, 10))
        outer.pack(fill="both", expand=True)

        title_row = ttk.Frame(outer)
        title_row.pack(fill="x")
        ttk.Label(
            title_row, text="Quick Import", style="DialogTitle.TLabel",
        ).pack(side="left")
        self.help_button = ttk.Button(
            title_row, text="Quick Import help",
            command=lambda: self._on_help("quick-import") if self._on_help else None,
        )
        self.help_button.pack(side="right")
        ttk.Label(
            outer,
            text=(
                "Turn a straightforward add-on into a validated ALLIN1 package, "
                "or export its Legacy branch as an OIV installer. Neither route "
                "writes directly to GTA V."
            ),
            style="Muted.TLabel", wraplength=980, justify="left",
        ).pack(anchor="w", pady=(3, 7))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 6))
        open_menu = tk.Menu(toolbar, tearoff=False)
        open_menu.add_command(
            label="Open DLC RPF…", command=self._choose_rpf,
        )
        open_menu.add_command(
            label="Open package archive…", command=self._choose_archive,
        )
        open_menu.add_command(
            label="Open loose package folder…", command=self._choose_folder,
        )
        self.open_button = ttk.Menubutton(
            toolbar, text="Open content…", menu=open_menu, style="Accent.TButton",
        )
        self.open_button.pack(side="left")
        ttk.Label(
            toolbar, textvariable=self.source_summary, foreground="#37584d",
            font=("Segoe UI Semibold", 9),
        ).pack(side="left", fill="x", expand=True, padx=(12, 8))
        ttk.Label(toolbar, text="Edition").pack(side="left", padx=(5, 5))
        self.edition_combo = ttk.Combobox(
            toolbar, textvariable=self.edition, state="disabled", width=10,
        )
        self.edition_combo.pack(side="left")
        self.edition_combo.bind("<<ComboboxSelected>>", self._edition_changed)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")

        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        vehicle_page = self.vehicle_page = ttk.Frame(self.tabs, padding=8)
        weapon_page = self.weapon_page = ttk.Frame(self.tabs, padding=8)
        ped_page = self.ped_page = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(vehicle_page, text="Vehicles")
        self.tabs.add(weapon_page, text="Weapons")
        self.tabs.add(ped_page, text="Peds")
        self._build_vehicle_page()
        self.weapon_workbench_button = self._build_placeholder(
            self.weapon_page, "Weapon",
            "Guided weapon packaging is not available yet. Use the advanced "
            "Workbench to inspect definitions, ammo, components, animations, and shop data.",
            "weapons",
        )
        self.ped_workbench_button = self._build_placeholder(
            self.ped_page, "Ped",
            "Guided ped packaging is not available yet. Use the advanced Workbench "
            "to inspect metadata, drawables, textures, props, and movement links.",
            "peds",
        )

        activity = ttk.Frame(outer)
        activity.pack(fill="x", pady=(7, 0))
        ttk.Label(
            activity, textvariable=self.validation, style="Error.TLabel",
            justify="left",
        ).pack(fill="x", anchor="w")
        ttk.Label(
            activity, textvariable=self.status, foreground="#52635c",
            justify="left", wraplength=1050,
        ).pack(fill="x", anchor="w", pady=(2, 0))

    def _build_vehicle_page(self) -> None:
        actions = ttk.Frame(self.vehicle_page)
        actions.pack(fill="x", pady=(0, 7))
        self.advanced_vehicle_button = ttk.Button(
            actions, text="Open in advanced Workbench",
            command=lambda: self._route_workbench("vehicles"),
        )
        self.advanced_vehicle_button.pack(side="left")
        ttk.Label(
            actions, textvariable=self.destination_summary,
            foreground="#52635c", anchor="e",
        ).pack(side="right", fill="x", expand=True, padx=(10, 0))

        body = ttk.Panedwindow(self.vehicle_page, orient="horizontal")
        body.pack(fill="both", expand=True)
        catalog = ttk.LabelFrame(body, text="Vehicle records", padding=7, width=230)
        editor = ttk.Frame(body)
        body.add(catalog, weight=2)
        body.add(editor, weight=5)

        tree_host = ttk.Frame(catalog)
        tree_host.pack(fill="both", expand=True)
        self.model_tree = ttk.Treeview(
            tree_host, columns=("status",), show="tree headings",
            selectmode="browse", height=13,
        )
        self.model_tree.heading("#0", text="Model")
        self.model_tree.heading("status", text="GBAY")
        self.model_tree.column("#0", width=150, minwidth=110, stretch=True)
        self.model_tree.column("status", width=62, minwidth=55, stretch=False)
        model_scroll = ttk.Scrollbar(
            tree_host, orient="vertical", command=self.model_tree.yview,
        )
        self.model_xscroll = ttk.Scrollbar(
            tree_host, orient="horizontal", command=self.model_tree.xview,
        )
        self.model_tree.configure(
            yscrollcommand=model_scroll.set, xscrollcommand=self.model_xscroll.set,
        )
        self.model_tree.grid(row=0, column=0, sticky="nsew")
        model_scroll.grid(row=0, column=1, sticky="ns")
        self.model_xscroll.grid(row=1, column=0, sticky="ew")
        tree_host.rowconfigure(0, weight=1)
        tree_host.columnconfigure(0, weight=1)
        self.model_tree.bind("<<TreeviewSelect>>", self._model_selected)

        editor_canvas = self.editor_canvas = tk.Canvas(
            editor, highlightthickness=0, borderwidth=0, background="#f4f7f5",
        )
        editor_scroll = ttk.Scrollbar(
            editor, orient="vertical", command=editor_canvas.yview,
        )
        editor_canvas.configure(yscrollcommand=editor_scroll.set)
        editor_canvas.pack(side="left", fill="both", expand=True)
        editor_scroll.pack(side="right", fill="y")
        form = ttk.Frame(editor_canvas, padding=(8, 3, 11, 8))
        form_window = editor_canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind(
            "<Configure>",
            lambda _event: editor_canvas.configure(
                scrollregion=editor_canvas.bbox("all"),
            ),
        )
        editor_canvas.bind(
            "<Configure>",
            lambda event: editor_canvas.itemconfigure(form_window, width=event.width),
        )

        package = ttk.LabelFrame(form, text="Package", padding=8)
        package.pack(fill="x")
        self.package_entries: list[ttk.Entry] = []
        for row, (label, variable) in enumerate((
            ("Package ID", self.package_id),
            ("Package name", self.package_name),
            ("Version", self.package_version),
        )):
            ttk.Label(package, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(package, textvariable=variable, state="disabled")
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            self.package_entries.append(entry)
        package.columnconfigure(1, weight=1)

        listing = ttk.LabelFrame(form, text="GBAY listing", padding=8)
        listing.pack(fill="x", pady=(7, 0))
        ttk.Label(
            listing, textvariable=self.model_name,
            font=("Segoe UI Semibold", 11), foreground="#1f7f42",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.listing_entries: list[ttk.Entry] = []
        for row, (label, variable) in enumerate((
            ("Display name", self.listing_name),
            ("Manufacturer", self.manufacturer),
            ("Price", self.price),
        ), start=1):
            ttk.Label(listing, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(listing, textvariable=variable, state="disabled")
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            self.listing_entries.append(entry)

        self.free_price_check = ttk.Checkbutton(
            listing,
            text="This vehicle is intentionally free in GBAY",
            variable=self.free_price_confirmed,
            state="disabled",
        )
        self.free_price_check.grid(
            row=4, column=1, sticky="w", padx=(8, 0), pady=(1, 5),
        )

        self.category_combo = self._listing_combo(
            listing, 5, "Category", self.category,
            tuple(sorted(VEHICLE_CATEGORIES)),
        )
        self.storage_combo = self._listing_combo(
            listing, 6, "Storage", self.storage,
            tuple(sorted(STORAGE_KINDS)),
        )
        self.size_combo = self._listing_combo(
            listing, 7, "Vehicle size", self.size_tier,
            tuple(SIZE_TIER_LABELS.values()),
        )
        self.category_combo.bind("<<ComboboxSelected>>", self._category_changed)
        self.traffic_check = ttk.Checkbutton(
            listing,
            text="Include this road vehicle in ambient traffic",
            variable=self.traffic_enabled, state="disabled",
            command=self._traffic_changed,
        )
        self.traffic_check.grid(
            row=8, column=0, columnspan=2,
            sticky="w", pady=(7, 2),
        )
        ttk.Label(
            listing,
            text=(
                "Checked makes the vehicle eligible and passes that choice to Launcher. "
                "Leave it off for GBAY only; Launcher confirms settings during installation."
            ),
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(
            row=9, column=0, columnspan=2,
            sticky="ew", pady=(5, 0),
        )

        self.custom_preview_check = ttk.Checkbutton(
            listing,
            text="Use an existing streamed texture instead of the GBAY placeholder",
            variable=self.custom_preview,
            state="disabled",
            command=self._preview_mode_changed,
        )
        self.custom_preview_check.grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(8, 2),
        )
        ttk.Label(listing, text="Preview dictionary").grid(
            row=11, column=0, sticky="w", pady=2,
        )
        self.preview_dictionary_combo = ttk.Combobox(
            listing, textvariable=self.preview_dictionary, state="disabled",
        )
        self.preview_dictionary_combo.grid(
            row=11, column=1, sticky="ew", padx=(8, 0), pady=2,
        )
        ttk.Label(listing, text="Preview texture").grid(
            row=12, column=0, sticky="w", pady=2,
        )
        self.preview_texture_entry = ttk.Entry(
            listing, textvariable=self.preview_texture, state="disabled",
        )
        self.preview_texture_entry.grid(
            row=12, column=1, sticky="ew", padx=(8, 0), pady=2,
        )
        ttk.Label(
            listing, textvariable=self.preview_help,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(
            row=13, column=0, columnspan=2, sticky="ew", pady=(4, 0),
        )
        listing.columnconfigure(1, weight=1)

        findings = ttk.LabelFrame(form, text="Review", padding=7)
        findings.pack(fill="x", pady=(7, 0))
        self.warning_text = tk.Text(
            findings, height=4, wrap="word", relief="flat", state="disabled",
            background="#f4f7f5", foreground="#52635c", padx=5, pady=5,
        )
        self.warning_text.pack(fill="x")

        footer = ttk.Frame(form)
        footer.pack(fill="x", pady=(8, 0))
        self.discard_button = ttk.Button(
            footer, text="Reset draft", state="disabled", command=self._discard_draft,
        )
        self.discard_button.pack(side="left")
        self.export_oiv_button = ttk.Button(
            footer, text="Export Legacy OIV…", state="disabled",
            command=self._export_legacy_oiv,
        )
        self.export_oiv_button.pack(side="left", padx=(8, 0))
        self.prepare_button = ttk.Button(
            footer, text="Prepare for Launcher", style="Accent.TButton",
            state="disabled", command=self._prepare_for_launcher,
        )
        self.prepare_button.pack(side="right")
        self.open_launcher_button = ttk.Button(
            footer, text="Open in Launcher", command=self._open_in_launcher,
            state="disabled",
        )
        self._bind_editor_scrolling(editor_canvas, form)

    def _bind_editor_scrolling(self, canvas: tk.Canvas, root: tk.Misc) -> None:
        """Let the tall form follow the wheel without hijacking editable controls."""
        def scroll(event: tk.Event) -> str:
            delta = int(getattr(event, "delta", 0))
            if delta:
                units = max(1, abs(delta) // 120)
                canvas.yview_scroll(-units if delta > 0 else units, "units")
            return "break"

        canvas.bind("<MouseWheel>", scroll)
        pending = [root]
        passive = (ttk.Frame, ttk.Label, ttk.LabelFrame, tk.Frame, tk.Label)
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if isinstance(widget, passive):
                widget.bind("<MouseWheel>", scroll)

    def _listing_combo(
        self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar,
        values: tuple[str, ...],
    ) -> ttk.Combobox:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        combo = ttk.Combobox(
            parent, textvariable=variable, values=values, state="disabled",
        )
        combo.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        return combo

    def _build_placeholder(
        self, page: ttk.Frame, label: str, detail: str, category: str,
    ) -> ttk.Button:
        panel = ttk.Frame(page, padding=(28, 34))
        panel.pack(fill="both", expand=True)
        ttk.Label(
            panel, text=f"{label} Quick Import",
            font=("Segoe UI Semibold", 16), foreground="#1f7f42",
        ).pack(anchor="center")
        ttk.Label(
            panel, text=detail, foreground="#52635c", wraplength=620,
            justify="center",
        ).pack(anchor="center", pady=(8, 14))
        button = ttk.Button(
            panel, text=f"Open {label} Workbench",
            command=lambda selected=category: self._route_workbench(selected),
        )
        button.pack(anchor="center")
        return button

    def _bind_dirty_tracking(self) -> None:
        for variable in (
            self.package_id, self.package_name, self.package_version,
            self.listing_name, self.manufacturer, self.category, self.price,
            self.free_price_confirmed,
            self.storage, self.size_tier, self.preview_dictionary,
            self.preview_texture, self.custom_preview, self.traffic_enabled,
        ):
            variable.trace_add("write", self._form_changed)

    def _form_changed(self, *_args: object) -> None:
        if self._loading_form or self.review is None:
            return
        if self.price.get().strip() != "0" and self.free_price_confirmed.get():
            self._loading_form = True
            try:
                self.free_price_confirmed.set(False)
            finally:
                self._loading_form = False
        if self._active_edition:
            self._dirty_editions.add(self._active_edition)
        self._dirty = bool(self._dirty_editions)
        self.prepared = None
        self.validation.set("")
        self._refresh_control_states()

    def _choose_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select a vehicle add-on archive",
            filetypes=(
                ("GTA package", "*.oiv *.zip *.rar *.7z"),
                ("All files", "*.*"),
            ),
        )
        if selected:
            self.open_source(selected)

    def _choose_rpf(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select a vehicle DLC RPF",
            filetypes=(("GTA V RPF", "*.rpf"), ("All files", "*.*")),
        )
        if selected:
            self.open_source(selected)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a loose vehicle add-on folder",
        )
        if selected:
            self.open_source(selected)

    def open_source(
        self, source: str | Path, *, preferred_edition: str | None = None,
    ) -> bool:
        """Begin a bounded background inspection of one folder or archive."""
        if self._busy:
            self.validation.set("Wait for the current Quick Import operation to finish.")
            return False
        if self._dirty:
            self.validation.set(
                "Reset or prepare the current draft before opening another package."
            )
            return False
        try:
            resolved = Path(source).expanduser().resolve(strict=True)
            effective_preference = (
                preferred_edition or self._preferred_configured_edition()
            )
            service = self._service(effective_preference)
        except (OSError, ValueError) as exc:
            self.validation.set(str(exc))
            self.status.set("Package analysis did not start.")
            return False
        self._clear_package()
        self.source = resolved
        self.source_summary.set(f"Analyzing {resolved.name}…")
        self._run(
            "Scanning package metadata and recursively inspecting vehicle archives…",
            lambda: service.inspect(
                resolved, preferred_edition=effective_preference,
            ),
            self._inspection_loaded,
        )
        return True

    def _inspection_loaded(self, inspection: VehicleQuickImportInspection) -> None:
        self.inspection = inspection
        self.source = inspection.source
        self.edition_combo.configure(values=inspection.available_editions)
        self._loading_form = True
        try:
            self.edition.set(inspection.suggested_edition)
        finally:
            self._loading_form = False
        self.source_summary.set(
            f"{inspection.source.name} · "
            + (
                f"target: {inspection.suggested_edition.title()} · "
                if getattr(inspection, "edition_basis", "package_branches")
                == "selected_decoder_target"
                else f"{len(inspection.available_editions)} edition branch(es) · "
            )
            + f"{inspection.scan.error_count} errors / "
            f"{inspection.scan.warning_count} warnings"
        )
        self._load_edition(inspection.suggested_edition)

    def _load_edition(self, edition: str) -> None:
        if self.inspection is None:
            return
        cached = self._plans_by_edition.get(edition)
        if cached is not None:
            self._review_loaded(cached, edition)
            return
        try:
            service = self._service(edition)
        except ValueError as exc:
            self.validation.set(str(exc))
            self.status.set(f"The {edition.title()} branch needs a matching GTA V path.")
            if self._active_edition:
                self._loading_form = True
                try:
                    self.edition.set(self._active_edition)
                finally:
                    self._loading_form = False
            return
        inspection = self.inspection
        self._run(
            f"Validating the {edition.title()} branch and building its GBAY draft…",
            lambda: service.plan(inspection, edition=edition),
            lambda review: self._review_loaded(review, edition),
        )

    def _review_loaded(
        self, review: VehicleQuickImportReview, edition: str,
    ) -> None:
        self.review = review
        self._active_edition = edition
        self._plans_by_edition[edition] = review
        self._package_by_edition.setdefault(edition, {
            "id": review.plan.package_id,
            "name": review.plan.name,
            "version": review.plan.version,
        })
        self._drafts_by_edition.setdefault(
            edition,
            {
                entry.model.casefold(): self._entry_draft(entry)
                for entry in review.plan.catalog.vehicles
            },
        )
        self._loading_form = True
        try:
            package = self._package_by_edition[edition]
            self.package_id.set(package["id"])
            self.package_name.set(package["name"])
            self.package_version.set(package["version"])
        finally:
            self._loading_form = False
        self._populate_models()
        self._set_warnings(review.warnings)
        self.status.set(
            f"{len(review.plan.catalog.vehicles)} vehicle(s) ready for listing review."
        )
        self.validation.set("")
        self._update_destination()
        self._refresh_control_states()

    @staticmethod
    def _entry_draft(entry) -> dict[str, object]:
        return {
            "name": entry.display_name,
            "manufacturer": entry.manufacturer,
            "category": entry.category,
            "price": entry.price,
            "free_price_confirmed": False,
            "storage": entry.storage,
            "size_tier": entry.size_tier,
            "preview_dictionary": entry.preview_dictionary or "",
            "preview_texture": entry.preview_texture or "",
            "traffic_enabled": entry.traffic.enabled,
            "traffic_weight": entry.traffic.weight,
        }

    def _populate_models(self) -> None:
        assert self.review is not None
        previous = self._active_model
        self.model_tree.delete(*self.model_tree.get_children())
        for entry in self.review.plan.catalog.vehicles:
            iid = entry.model.casefold()
            self.model_tree.insert("", "end", iid=iid, text=entry.model, values=("Listed",))
        # Treeview treats the empty string as its invisible root, so guard the
        # initial empty selection before asking whether it exists.
        target = previous if previous and self.model_tree.exists(previous) else (
            self.model_tree.get_children()[0] if self.model_tree.get_children() else ""
        )
        if target:
            self._restoring_selection = True
            try:
                self.model_tree.selection_set(target)
                self.model_tree.focus(target)
                self.model_tree.see(target)
            finally:
                self._restoring_selection = False
            self._load_model_form(target)

    def _model_selected(self, _event: object | None = None) -> None:
        if self._restoring_selection or self._busy:
            return
        selection = self.model_tree.selection()
        target = selection[0] if selection else ""
        if not target or target == self._active_model:
            return
        if self._active_model and not self._store_current_form():
            self._restoring_selection = True
            try:
                self.model_tree.selection_set(self._active_model)
                self.model_tree.focus(self._active_model)
            finally:
                self._restoring_selection = False
            return
        self._load_model_form(target)

    def _load_model_form(self, model: str) -> None:
        draft = self._drafts_by_edition.get(self._active_edition, {}).get(model)
        if draft is None:
            return
        self._active_model = model
        self._loading_form = True
        try:
            self.model_name.set(model)
            self.listing_name.set(str(draft["name"]))
            self.manufacturer.set(str(draft["manufacturer"]))
            self.category.set(str(draft["category"]))
            self.price.set(str(draft["price"]))
            self.free_price_confirmed.set(bool(draft["free_price_confirmed"]))
            self.storage.set(str(draft["storage"]))
            self.size_tier.set(
                SIZE_TIER_LABELS.get(int(draft["size_tier"]), SIZE_TIER_LABELS[0])
            )
            self.preview_dictionary.set(str(draft["preview_dictionary"]))
            self.preview_texture.set(str(draft["preview_texture"]))
            self.custom_preview.set(bool(
                draft["preview_dictionary"] or draft["preview_texture"]
            ))
            self.traffic_enabled.set(bool(draft["traffic_enabled"]))
            self.traffic_weight.set("1.0")
        finally:
            self._loading_form = False
        self._update_preview_candidates()
        self._refresh_control_states()

    def _store_current_form(self) -> bool:
        if not self._active_model or self.review is None:
            return True
        try:
            display = self.listing_name.get().strip()
            if not display:
                raise ValueError("Display name must not be empty.")
            try:
                price = int(self.price.get().strip())
            except ValueError as exc:
                raise ValueError("Price must be a whole number.") from exc
            if not 0 <= price <= 2_000_000_000:
                raise ValueError("Price must be between 0 and 2,000,000,000.")
            if price == 0 and not self.free_price_confirmed.get():
                raise ValueError(
                    "Confirm that this vehicle is intentionally free, or set a price."
                )
            raw_size = self.size_tier.get().strip()
            if raw_size in SIZE_TIER_VALUES:
                size = SIZE_TIER_VALUES[raw_size]
            else:
                try:
                    size = int(raw_size)
                except ValueError as exc:
                    raise ValueError("Choose Standard, Large, or Oversize.") from exc
            if size not in {0, 1, 2}:
                raise ValueError("Choose Standard, Large, or Oversize.")
            category = self.category.get().strip().casefold()
            storage = self.storage.get().strip().casefold()
            if category not in VEHICLE_CATEGORIES:
                raise ValueError("Choose a supported GBAY category.")
            if storage not in STORAGE_KINDS:
                raise ValueError("Choose a supported storage location.")
            expected_storage = storage_for_category(category)
            if storage != expected_storage:
                raise ValueError(
                    f"{category.title()} vehicles must use {expected_storage} storage."
                )
            if self.traffic_enabled.get() and category not in ROAD_TRAFFIC_CATEGORIES:
                raise ValueError("Only road vehicles can be offered for ambient traffic.")
            preview_dictionary = (
                self.preview_dictionary.get().strip() if self.custom_preview.get() else ""
            )
            preview_texture = (
                self.preview_texture.get().strip() if self.custom_preview.get() else ""
            )
            if preview_texture and not preview_dictionary:
                raise ValueError("Preview texture requires a preview dictionary.")
            if preview_dictionary and not preview_texture:
                raise ValueError("Preview dictionary requires an exact texture name.")
        except ValueError as exc:
            self.validation.set(str(exc))
            self.status.set(f"Review {self._active_model} before continuing.")
            return False
        draft = {
            "name": display,
            "manufacturer": self.manufacturer.get().strip(),
            "category": category,
            "price": price,
            "free_price_confirmed": bool(
                price == 0 and self.free_price_confirmed.get()
            ),
            "storage": storage,
            "size_tier": size,
            "preview_dictionary": preview_dictionary,
            "preview_texture": preview_texture,
            "traffic_enabled": bool(self.traffic_enabled.get()),
            "traffic_weight": 1.0,
        }
        existing = self._drafts_by_edition[self._active_edition][self._active_model]
        if draft != existing:
            self._drafts_by_edition[self._active_edition][self._active_model] = draft
            self._dirty_editions.add(self._active_edition)
            self._dirty = True
        self.validation.set("")
        self._refresh_control_states()
        return True

    def _edition_changed(self, _event: object | None = None) -> None:
        selected = self.edition.get().strip().casefold()
        if (
            self._loading_form or self._busy or self.inspection is None
            or selected == self._active_edition
        ):
            return
        if self._active_model and not self._store_current_form():
            self._loading_form = True
            try:
                self.edition.set(self._active_edition)
            finally:
                self._loading_form = False
            return
        if self._active_edition:
            self._package_by_edition[self._active_edition] = {
                "id": self.package_id.get().strip(),
                "name": self.package_name.get().strip(),
                "version": self.package_version.get().strip(),
            }
        self._load_edition(selected)

    def _category_changed(self, _event: object | None = None) -> None:
        if self._loading_form:
            return
        category = self.category.get().strip().casefold()
        self.storage.set(storage_for_category(category))
        if category not in ROAD_TRAFFIC_CATEGORIES:
            self.traffic_enabled.set(False)
        self._traffic_changed()

    def _traffic_changed(self) -> None:
        self._refresh_control_states()

    def _preview_mode_changed(self) -> None:
        self._update_preview_candidates()
        self._refresh_control_states()

    def _update_preview_candidates(self) -> None:
        candidates: tuple[str, ...] = ()
        if self.inspection is not None and self._active_edition:
            try:
                candidates = VehicleQuickImportService.preview_dictionary_candidates(
                    self.inspection,
                    edition=self._active_edition,
                    model=self._active_model,
                )
            except (AttributeError, TypeError):
                # Lightweight embedded hosts may provide an older scan shape.
                candidates = ()
        self.preview_dictionary_combo.configure(values=candidates)
        if not self.custom_preview.get():
            self.preview_help.set(
                "GBAY placeholder selected. No game texture needs to be authored."
            )
        elif candidates:
            shown = ", ".join(candidates[:4])
            suffix = f" (+{len(candidates) - 4} more)" if len(candidates) > 4 else ""
            self.preview_help.set(
                f"Package-owned YTD candidates: {shown}{suffix}. Choose only a "
                "dictionary you know contains the exact texture name; nothing is auto-filled."
            )
        else:
            self.preview_help.set(
                "No package-owned YTD dictionary was detected. Use the GBAY placeholder "
                "unless you can verify an existing streamed dictionary and texture."
            )

    def _open_in_launcher(self) -> None:
        if self.prepared is None or self._on_open_launcher is None:
            return
        plan = self.prepared.result.plan
        try:
            self._on_open_launcher(plan.package_id, plan.traffic_opt_in)
        except Exception as exc:
            self.validation.set(f"Could not open Launcher: {exc}")
            self.status.set(
                "The prepared package remains safely available in the Launcher library."
            )

    def _export_legacy_oiv(self) -> None:
        """Export the exact Legacy DLC payload without requiring Launcher."""
        if self.inspection is None or self._busy:
            return
        if "legacy" not in self.inspection.available_editions:
            self.validation.set(
                "This package has no verified Legacy vehicle branch to export as OIV."
            )
            return
        author = simpledialog.askstring(
            "OIV package author",
            "Enter the vehicle package author's display name:",
            parent=self,
        )
        if author is None:
            return
        author = author.strip()
        if not author:
            self.validation.set("An explicit author name is required for OIV export.")
            return
        cached = self._plans_by_edition.get("legacy")
        package = self._package_by_edition.get("legacy", {})
        if self._active_edition == "legacy":
            package_id = self.package_id.get().strip()
            package_name = self.package_name.get().strip()
            version = self.package_version.get().strip()
        elif cached is not None:
            package_id = str(package.get("id") or cached.plan.package_id)
            package_name = str(package.get("name") or cached.plan.name)
            version = str(package.get("version") or cached.plan.version)
        else:
            package_id = package_name = version = ""
        initial_name = (
            (package_id or "vehicle-addon").replace(".", "-") + ".oiv"
        )
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Export Legacy OIV package",
            defaultextension=".oiv",
            filetypes=(("OIV package", "*.oiv"),),
            initialfile=initial_name,
        )
        if not selected:
            return
        destination = Path(selected).expanduser().resolve(strict=False)
        inspection = self.inspection
        try:
            # Planning from the service that performed the bounded scan avoids
            # requiring a separately installed Legacy copy merely to package
            # an already verified Legacy branch.
            service = self._vehicle_service or next(iter(self._vehicle_services.values()))
        except StopIteration:
            self.validation.set("The verified package scan service is unavailable.")
            return

        def work() -> LegacyVehicleOivResult:
            review = service.plan(
                inspection,
                edition="legacy",
                package_id=package_id or None,
                name=package_name or None,
                version=version or "1.0.0",
            )
            return LegacyVehicleOivExporter(service.gta_path).export_plan(
                review.plan, destination, author=author,
            )

        self._run(
            "Revalidating the Legacy payload and building the OIV package…",
            work,
            self._oiv_exported,
        )

    def _oiv_exported(self, result: LegacyVehicleOivResult) -> None:
        self.validation.set("")
        self.status.set(
            f"Exported Legacy OIV package: {result.archive}. "
            "GBAY, traffic, receipts, backups, and rollback are not included."
        )

    def _prepare_for_launcher(self) -> None:
        if self.review is None or self.inspection is None or self._busy:
            return
        if not self._store_current_form():
            return
        package_id = self.package_id.get().strip()
        package_name = self.package_name.get().strip()
        version = self.package_version.get().strip()
        if not package_id or not package_name or not version:
            self.validation.set("Package ID, package name, and version are required.")
            return
        edition = self._active_edition
        updates = {
            model: dict(values)
            for model, values in self._drafts_by_edition[edition].items()
        }
        try:
            service = self._service(edition)
        except ValueError as exc:
            self.validation.set(str(exc))
            return
        inspection = self.inspection
        library = self.library_root

        def work() -> PreparedVehicleQuickImport:
            base = service.plan(
                inspection, edition=edition, package_id=package_id,
                name=package_name, version=version,
            )
            customized = service.customize(base.plan, updates)
            destination = service.library_destination(
                customized.plan, library_root=library,
            )
            return service.prepare(
                customized, destination, library_root=library,
            )

        self._run(
            "Revalidating source bytes and preparing the package for Launcher review…",
            work, self._prepared,
        )

    def _prepared(self, prepared: PreparedVehicleQuickImport) -> None:
        self.prepared = prepared
        acknowledged_free = tuple(sorted(
            model for model, draft in self._drafts_by_edition.get(
                self._active_edition, {},
            ).items()
            if draft.get("price") == 0 and draft.get("free_price_confirmed") is True
        ))
        self.review = VehicleQuickImportReview(
            prepared.result.plan, prepared.warnings, acknowledged_free,
        )
        self._dirty_editions.discard(self._active_edition)
        self._dirty = bool(self._dirty_editions)
        manifest = prepared.result.manifest_path
        self.destination_summary.set(f"Prepared: {manifest.parent}")
        action = "Updated" if prepared.replaced_existing else "Prepared"
        self.status.set(
            f"{action} {prepared.result.plan.name}. Review and install it in Launcher; "
            "GTA V was not changed."
        )
        self.validation.set("")
        self._set_warnings(prepared.warnings)
        self._refresh_control_states()

    def _discard_draft(self) -> None:
        review = self._plans_by_edition.get(self._active_edition)
        if review is None:
            return
        self._drafts_by_edition[self._active_edition] = {
            entry.model.casefold(): self._entry_draft(entry)
            for entry in review.plan.catalog.vehicles
        }
        self._package_by_edition[self._active_edition] = {
            "id": review.plan.package_id,
            "name": review.plan.name,
            "version": review.plan.version,
        }
        self.review = review
        self.prepared = None
        self._dirty_editions.discard(self._active_edition)
        self._dirty = bool(self._dirty_editions)
        self.validation.set("")
        self._review_loaded(review, self._active_edition)
        self.status.set("Draft reset to the values inferred from the package.")

    def _set_warnings(self, warnings: tuple[str, ...]) -> None:
        self.warning_text.configure(state="normal")
        self.warning_text.delete("1.0", "end")
        self.warning_text.insert(
            "1.0",
            "\n".join(f"• {item}" for item in warnings)
            if warnings else "No storefront review warnings.",
        )
        self.warning_text.configure(state="disabled")

    def _update_destination(self) -> None:
        if self.review is None:
            self.destination_summary.set(
                f"Launcher library: {self._default_library_root()}"
            )
            return
        try:
            destination = self._service(self._active_edition).library_destination(
                self.review.plan, library_root=self.library_root,
            )
        except (OSError, ValueError):
            destination = self._default_library_root()
        self.destination_summary.set(f"Launcher library: {destination}")

    def _route_workbench(self, category: str) -> None:
        if self._on_open_workbench is None:
            self.status.set(
                "Advanced Workbench routing is unavailable in this SDK host."
            )
            return
        self._on_open_workbench(category)

    def _clear_package(self) -> None:
        self.inspection = None
        self.review = None
        self.prepared = None
        self._plans_by_edition.clear()
        self._drafts_by_edition.clear()
        self._package_by_edition.clear()
        self._active_edition = ""
        self._active_model = ""
        self._dirty_editions.clear()
        self._dirty = False
        self.validation.set("")
        self.model_tree.delete(*self.model_tree.get_children())
        self._loading_form = True
        try:
            self.edition.set("")
            self.package_id.set("")
            self.package_name.set("")
            self.package_version.set("1.0.0")
            self.model_name.set("No vehicle selected")
            for variable in (
                self.listing_name, self.manufacturer, self.price,
                self.preview_dictionary, self.preview_texture,
            ):
                variable.set("")
            self.category.set("special")
            self.storage.set("garage")
            self.size_tier.set(SIZE_TIER_LABELS[0])
            self.free_price_confirmed.set(False)
            self.custom_preview.set(False)
            self.traffic_enabled.set(False)
            self.traffic_weight.set("1.0")
        finally:
            self._loading_form = False
        self._set_warnings(())
        self._update_destination()
        self._refresh_control_states()

    def _refresh_control_states(self) -> None:
        loaded = self.review is not None and bool(self._active_model)
        general_state = "disabled" if self._busy else "normal"
        self.open_button.configure(state=general_state)
        self.edition_combo.configure(
            state=("readonly" if self.inspection is not None and not self._busy else "disabled"),
        )
        self.model_tree.configure(selectmode="none" if self._busy else "browse")
        for entry in self.package_entries + self.listing_entries:
            entry.configure(state="normal" if loaded and not self._busy else "disabled")
        for combo in (self.category_combo, self.storage_combo, self.size_combo):
            combo.configure(state="readonly" if loaded and not self._busy else "disabled")
        self.traffic_check.configure(
            state=(
                "normal" if loaded and not self._busy
                and self.category.get().strip().casefold()
                in ROAD_TRAFFIC_CATEGORIES
                else "disabled"
            ),
        )
        self.free_price_check.configure(
            state=(
                "normal" if loaded and not self._busy
                and self.price.get().strip() == "0"
                else "disabled"
            ),
        )
        self.custom_preview_check.configure(
            state="normal" if loaded and not self._busy else "disabled",
        )
        preview_state = (
            "normal" if loaded and not self._busy and self.custom_preview.get()
            else "disabled"
        )
        self.preview_dictionary_combo.configure(state=preview_state)
        self.preview_texture_entry.configure(state=preview_state)
        self.prepare_button.configure(
            state=(
                "normal" if loaded and not self._busy
                and not (self.prepared is not None and not self._dirty)
                else "disabled"
            ),
        )
        self.discard_button.configure(
            state=(
                "normal" if self._active_edition in self._dirty_editions
                and not self._busy else "disabled"
            ),
        )
        self.export_oiv_button.configure(
            state=(
                "normal" if self.inspection is not None and not self._busy
                and "legacy" in self.inspection.available_editions
                else "disabled"
            ),
        )
        if self.prepared is not None and self._on_open_launcher is not None:
            if not self.open_launcher_button.winfo_manager():
                self.open_launcher_button.pack(
                    side="right", padx=(0, 8), before=self.prepare_button,
                )
            self.open_launcher_button.configure(
                state="normal" if not self._busy else "disabled",
            )
        elif self.open_launcher_button.winfo_manager():
            self.open_launcher_button.pack_forget()

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if busy:
            self.status.set(message)
            self.validation.set("")
            if not self.progress.winfo_manager():
                self.progress.pack(fill="x", pady=(0, 7), before=self.tabs)
            self.progress.start(12)
        else:
            self.progress.stop()
            if self.progress.winfo_manager():
                self.progress.pack_forget()
        self._refresh_control_states()

    def _run(self, message: str, work, completed) -> None:
        if self._busy:
            return
        self._set_busy(True, message)

        def runner() -> None:
            try:
                self._events.put(("result", (completed, work())))
            except Exception as exc:  # Background boundary reports safely in-frame.
                self._events.put(("error", exc))

        threading.Thread(
            target=runner, daemon=True, name="allin1-quick-import",
        ).start()
        self.after(70, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            kind, payload = self._events.get_nowait()
        except queue.Empty:
            self.after(70, self._poll)
            return
        self._set_busy(False)
        if kind == "error":
            self.validation.set(str(payload))
            self.status.set(
                "Quick Import stopped safely. No package was installed and GTA V was not changed."
            )
            return
        completed, result = payload
        completed(result)

    def has_active_work(self) -> bool:
        return self._busy

    def confirm_navigation(self) -> bool:
        """Keep background work and unprepared edits from being discarded silently."""
        if self._busy:
            self.validation.set(
                "Wait for the current Quick Import operation before leaving this workspace."
            )
            return False
        if self._dirty:
            self.validation.set(
                "Prepare the package or choose Reset draft before leaving Quick Import."
            )
            return False
        return True


__all__ = ["QuickImportFrame"]
