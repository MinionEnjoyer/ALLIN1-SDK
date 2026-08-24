"""Integrated ped inspection and guarded authoring workbench."""

from __future__ import annotations

import io
import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageEntry,
    PackageScan,
    PedRecord,
)
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.native_assets import NativeAssetInspector, native_preview_limit
from allin1_sdk.ped_authoring import PedAuthoringWorkspace, PedClonePlan
from allin1_sdk.viewport_rendering import LatestOnlyRenderWorker, WeightedLruCache


PED_AUTHOR_FIELDS = (
    ("Ped type", "ped.pedType"),
    ("Model type", "ped.modelType"),
    ("Props name", "ped.propsName"),
    ("Clip dictionary", "ped.clipDictionary"),
    ("Expression set", "ped.expressionSet"),
    ("Movement clip set", "ped.movementClipSet"),
    ("Creature metadata", "ped.creatureMetadata"),
)


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


class PedWorkbenchFrame(ttk.Frame):
    """Review and safely author peds.meta records inside copied workspaces."""

    def __init__(
        self,
        parent: tk.Misc,
        project_root: str | Path | None = None,
        *,
        installation_roots: tuple[Path, ...] = (),
        on_open_asset=None,
        on_help=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None else Path(__file__).resolve().parents[2]
        )
        self.installation_roots = tuple(
            Path(item).expanduser().resolve() for item in installation_roots
        )
        self._on_open_asset = on_open_asset
        self._on_help = on_help
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.peds: dict[str, PedRecord] = {}
        self.selected_ped: PedRecord | None = None
        self._assets: dict[str, PackageEntry] = {}
        self.authoring_workspace: PedAuthoringWorkspace | None = None
        self.authoring_values: dict[str, tk.StringVar] = {}
        self.authoring_inputs: dict[str, ttk.Entry] = {}
        self._loaded_authoring_snapshot: tuple[str, ...] | None = None
        self._restoring_selection = False
        self._reviewed_clone_plan: PedClonePlan | None = None
        self._preview_photo_model: ImageTk.PhotoImage | None = None
        self._preview_photo_texture: ImageTk.PhotoImage | None = None
        self._preview_source_images: tuple[Image.Image | None, Image.Image | None] = (
            None, None,
        )
        self._preview_worker: LatestOnlyRenderWorker[
            tuple[str, str, str], tuple[bytes | None, bytes | None, str]
        ] = LatestOnlyRenderWorker(
            cache=WeightedLruCache(
                maximum_entries=8, maximum_weight=48 * 1024 * 1024,
                weigh=lambda value: sum(
                    len(item) for item in value[:2] if isinstance(item, bytes)
                ),
            ),
            thread_name="allin1-ped-preview",
        )
        self._preview_poll_id: str | None = None
        self.search = tk.StringVar()
        self.status = tk.StringVar(
            value="Open a package in Workbench to inspect its ped systems."
        )
        self.heading = tk.StringVar(value="No ped selected")
        self.summary = tk.StringVar(
            value="Definitions, drawable dictionaries, textures, props, and clips appear here."
        )
        self._build()
        self.bind("<Destroy>", self._destroyed, add="+")
        self.search.trace_add("write", lambda *_args: self._refresh_catalog())

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(12, 10, 12, 12))
        outer.pack(fill="both", expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 9))
        ttk.Label(toolbar, text="Search", style="FieldLabel.TLabel").pack(
            side="left", padx=(0, 6),
        )
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search, width=28)
        self.search_entry.pack(side="left")
        ttk.Button(toolbar, text="Clear", command=lambda: self.search.set("")).pack(
            side="left", padx=(5, 0),
        )
        self.author_button = ttk.Button(
            toolbar, text="Create authoring workspace…", state="disabled",
            command=self._create_authoring_workspace,
        )
        self.author_button.pack(side="left", padx=(10, 0))
        self.asset_button = ttk.Button(
            toolbar, text="Open selected asset", state="disabled",
            command=self._open_selected_asset,
        )
        self.asset_button.pack(side="left", padx=(10, 0))
        ttk.Button(toolbar, text="Help", command=self._show_help).pack(
            side="left", padx=(6, 0),
        )
        self.status_label = ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=1040, justify="left",
        )
        self.status_label.pack(fill="x", pady=(0, 8))

        panes = ttk.Panedwindow(outer, orient="horizontal")
        self.primary_panes = panes
        panes.pack(fill="both", expand=True)
        side_panes = CollapsibleSidePanes(
            panes, left_width=260, center_width=520, right_width=310,
            left_weight=2, center_weight=5, right_weight=3,
            left_label="Peds", right_label="Integration",
        )
        self.primary_side_panes = side_panes
        catalog = ttk.LabelFrame(side_panes.left_host, text="Peds", padding=8)
        project = ttk.LabelFrame(side_panes.center_host, text="Ped project", padding=8)
        integration = ttk.LabelFrame(
            side_panes.right_host, text="Integration", padding=8,
        )
        self.catalog_panel = catalog
        self.work_panel = project
        self.integration_panel = integration
        side_panes.set_contents(catalog, project, integration)

        catalog_table = ttk.Frame(catalog)
        catalog_table.pack(fill="both", expand=True)
        self.ped_tree = ttk.Treeview(
            catalog_table, columns=("type", "state"), show="tree headings",
            selectmode="browse",
        )
        self.ped_tree.heading("#0", text="Model")
        self.ped_tree.heading("type", text="Ped type")
        self.ped_tree.heading("state", text="Status")
        self.ped_tree.column("#0", width=210, minwidth=130)
        self.ped_tree.column("type", width=95, stretch=False)
        self.ped_tree.column("state", width=72, stretch=False)
        catalog_scroll = ttk.Scrollbar(
            catalog_table, orient="vertical", command=self.ped_tree.yview,
        )
        self.catalog_xscroll = ttk.Scrollbar(
            catalog_table, orient="horizontal", command=self.ped_tree.xview,
        )
        self.ped_tree.configure(
            yscrollcommand=catalog_scroll.set,
            xscrollcommand=self.catalog_xscroll.set,
        )
        self.ped_tree.grid(row=0, column=0, sticky="nsew")
        catalog_scroll.grid(row=0, column=1, sticky="ns")
        self.catalog_xscroll.grid(row=1, column=0, sticky="ew")
        catalog_table.rowconfigure(0, weight=1)
        catalog_table.columnconfigure(0, weight=1)
        self.ped_tree.bind("<<TreeviewSelect>>", self._select_ped)

        ttk.Label(
            project, textvariable=self.heading, style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            project, textvariable=self.summary, foreground="#52635c",
            wraplength=610, justify="left",
        ).pack(anchor="w", pady=(2, 8))
        project_tabs = ttk.Notebook(project)
        project_tabs.pack(fill="both", expand=True)
        definition_page = ttk.Frame(project_tabs, padding=8)
        author_page = ttk.Frame(project_tabs, padding=8)
        create_page = ttk.Frame(project_tabs, padding=8)
        preview_page = ttk.Frame(project_tabs, padding=8)
        asset_page = ttk.Frame(project_tabs, padding=8)
        project_tabs.add(definition_page, text="Definition")
        project_tabs.add(author_page, text="Author")
        project_tabs.add(create_page, text="New from template")
        project_tabs.add(preview_page, text="Preview")
        project_tabs.add(asset_page, text="Asset family")

        field_table = ttk.Frame(definition_page)
        field_table.pack(fill="both", expand=True)
        self.field_tree = ttk.Treeview(
            field_table, columns=("value",), show="tree headings",
        )
        self.field_tree.heading("#0", text="Field")
        self.field_tree.heading("value", text="Resolved value")
        self.field_tree.column("#0", width=190, stretch=False)
        self.field_tree.column("value", width=430)
        field_scroll = ttk.Scrollbar(
            field_table, orient="vertical", command=self.field_tree.yview,
        )
        self.field_xscroll = ttk.Scrollbar(
            field_table, orient="horizontal", command=self.field_tree.xview,
        )
        self.field_tree.configure(
            yscrollcommand=field_scroll.set,
            xscrollcommand=self.field_xscroll.set,
        )
        self.field_tree.grid(row=0, column=0, sticky="nsew")
        field_scroll.grid(row=0, column=1, sticky="ns")
        self.field_xscroll.grid(row=1, column=0, sticky="ew")
        field_table.rowconfigure(0, weight=1)
        field_table.columnconfigure(0, weight=1)

        self.authoring_name = tk.StringVar(value="No ped selected")
        ttk.Label(
            author_page, textvariable=self.authoring_name,
            style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            author_page,
            text=(
                "The ped identity stays locked. Existing fields are edited only in "
                "a copied workspace and the complete package is validated after apply."
            ),
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w", pady=(2, 8))
        author_grid = ttk.Frame(author_page)
        author_grid.pack(fill="x")
        author_grid.columnconfigure(1, weight=1)
        for row, (label, key) in enumerate(PED_AUTHOR_FIELDS):
            variable = tk.StringVar()
            ttk.Label(
                author_grid, text=label, style="FieldLabel.TLabel",
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            entry = ttk.Entry(author_grid, textvariable=variable, state="disabled")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            self.authoring_values[key] = variable
            self.authoring_inputs[key] = entry
        author_actions = ttk.Frame(author_page)
        author_actions.pack(fill="x", pady=(9, 5))
        self.save_author_button = ttk.Button(
            author_actions, text="Apply fields", state="disabled",
            command=self._save_authoring_fields,
        )
        self.save_author_button.pack(side="left")
        self.undo_author_button = ttk.Button(
            author_actions, text="Undo latest", state="disabled",
            command=self._undo_authoring_edit,
        )
        self.undo_author_button.pack(side="left", padx=(6, 0))
        self.authoring_status = tk.StringVar(
            value="Create an authoring workspace before editing ped metadata."
        )
        ttk.Label(
            author_page, textvariable=self.authoring_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w")

        identity = ttk.LabelFrame(
            author_page, text="Identity and streamed assets", padding=8,
        )
        identity.pack(fill="x", pady=(10, 0))
        identity.columnconfigure(1, weight=1)
        self.migrate_name = tk.StringVar()
        self.migrate_props = tk.StringVar()
        ttk.Label(identity, text="New model", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=3,
        )
        self.migrate_name_entry = ttk.Entry(
            identity, textvariable=self.migrate_name, state="disabled",
        )
        self.migrate_name_entry.grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(identity, text="New props", style="FieldLabel.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=3,
        )
        self.migrate_props_entry = ttk.Entry(
            identity, textvariable=self.migrate_props, state="disabled",
        )
        self.migrate_props_entry.grid(row=1, column=1, sticky="ew", pady=3)
        self.migrate_button = ttk.Button(
            identity, text="Migrate identity + assets", state="disabled",
            command=self._migrate_identity,
        )
        self.migrate_button.grid(row=2, column=1, sticky="e", pady=(6, 0))
        ttk.Label(
            identity,
            text=(
                "Renames only exact package-owned model/texture files. Existing "
                "destinations, incomplete asset families, or stale revisions stop "
                "the whole transaction."
            ),
            foreground="#52635c", wraplength=560, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(
            create_page, text="Create a complete ped record",
            style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            create_page,
            text=(
                "Clone the selected ped's complete metadata record, including "
                "unknown fields. The target drawable and texture files must already "
                "exist; the SDK never relabels native bytes as a shortcut."
            ),
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", pady=(2, 10))
        clone_grid = ttk.Frame(create_page)
        clone_grid.pack(fill="x")
        clone_grid.columnconfigure(1, weight=1)
        self.clone_donor = tk.StringVar(value="No ped selected")
        self.clone_name = tk.StringVar()
        self.clone_props = tk.StringVar()
        for row, (label, variable) in enumerate((
            ("Template", self.clone_donor),
            ("New model", self.clone_name),
            ("New props", self.clone_props),
        )):
            ttk.Label(
                clone_grid, text=label, style="FieldLabel.TLabel",
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            entry = ttk.Entry(clone_grid, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            if row == 0:
                entry.configure(state="readonly")
        clone_actions = ttk.Frame(create_page)
        clone_actions.pack(fill="x", pady=(10, 5))
        self.review_clone_button = ttk.Button(
            clone_actions, text="Review plan", state="disabled",
            command=self._review_clone,
        )
        self.review_clone_button.pack(side="left")
        self.apply_clone_button = ttk.Button(
            clone_actions, text="Create reviewed ped", state="disabled",
            command=self._apply_clone,
        )
        self.apply_clone_button.pack(side="left", padx=(6, 0))
        self.clone_status = tk.StringVar(
            value="Open a copied authoring workspace to build a new ped."
        )
        ttk.Label(
            create_page, textvariable=self.clone_status, foreground="#52635c",
            wraplength=610, justify="left",
        ).pack(fill="x", anchor="w")
        for variable in (self.clone_name, self.clone_props):
            variable.trace_add("write", lambda *_args: self._invalidate_clone_plan())

        preview_toolbar = ttk.Frame(preview_page)
        preview_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Label(
            preview_toolbar, text="Diagnostic asset preview",
            style="DialogTitle.TLabel",
        ).pack(side="left")
        self.refresh_preview_button = ttk.Button(
            preview_toolbar, text="Refresh", state="disabled",
            command=self._request_preview,
        )
        self.refresh_preview_button.pack(side="right")
        preview_split = ttk.Panedwindow(preview_page, orient="horizontal")
        preview_split.pack(fill="both", expand=True)
        model_host = ttk.LabelFrame(preview_split, text="Ped model", padding=5)
        texture_host = ttk.LabelFrame(
            preview_split, text="Texture dictionary", padding=5,
        )
        preview_split.add(model_host, weight=1)
        preview_split.add(texture_host, weight=1)
        self.model_preview = ttk.Label(
            model_host, text="Select a ped to render its model.", anchor="center",
        )
        self.model_preview.pack(fill="both", expand=True)
        self.texture_preview = ttk.Label(
            texture_host, text="Select a ped to inspect its textures.", anchor="center",
        )
        self.texture_preview.pack(fill="both", expand=True)
        self.model_preview.bind("<Configure>", self._fit_preview_images)
        self.texture_preview.bind("<Configure>", self._fit_preview_images)
        self.preview_status = tk.StringVar(
            value=(
                "Model geometry/material diagnostics and actual packaged texture "
                "sheets are shown separately."
            )
        )
        ttk.Label(
            preview_page, textvariable=self.preview_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", pady=(6, 0))

        asset_table = ttk.Frame(asset_page)
        asset_table.pack(fill="both", expand=True)
        self.asset_tree = ttk.Treeview(
            asset_table, columns=("role", "size"), show="tree headings",
            selectmode="browse",
        )
        self.asset_tree.heading("#0", text="Package path")
        self.asset_tree.heading("role", text="Role")
        self.asset_tree.heading("size", text="Size")
        self.asset_tree.column("#0", width=395)
        self.asset_tree.column("role", width=150, stretch=False)
        self.asset_tree.column("size", width=82, stretch=False, anchor="e")
        asset_scroll = ttk.Scrollbar(
            asset_table, orient="vertical", command=self.asset_tree.yview,
        )
        self.asset_xscroll = ttk.Scrollbar(
            asset_table, orient="horizontal", command=self.asset_tree.xview,
        )
        self.asset_tree.configure(
            yscrollcommand=asset_scroll.set,
            xscrollcommand=self.asset_xscroll.set,
        )
        self.asset_tree.grid(row=0, column=0, sticky="nsew")
        asset_scroll.grid(row=0, column=1, sticky="ns")
        self.asset_xscroll.grid(row=1, column=0, sticky="ew")
        asset_table.rowconfigure(0, weight=1)
        asset_table.columnconfigure(0, weight=1)
        self.asset_tree.bind("<<TreeviewSelect>>", self._asset_selected)
        self.asset_tree.bind("<Double-1>", self._open_selected_asset)
        self.asset_tree.bind("<Return>", self._open_selected_asset)

        tabs = ttk.Notebook(integration)
        tabs.pack(fill="both", expand=True)
        readiness_page = ttk.Frame(tabs, padding=7)
        findings_page = ttk.Frame(tabs, padding=7)
        tabs.add(readiness_page, text="Readiness")
        tabs.add(findings_page, text="Findings")

        readiness_table = ttk.Frame(readiness_page)
        readiness_table.pack(fill="both", expand=True)
        self.readiness_tree = ttk.Treeview(
            readiness_table, columns=("status", "evidence"), show="tree headings",
        )
        self.readiness_tree.heading("#0", text="System")
        self.readiness_tree.heading("status", text="Status")
        self.readiness_tree.heading("evidence", text="Evidence")
        self.readiness_tree.column("#0", width=125, stretch=False)
        self.readiness_tree.column("status", width=78, stretch=False)
        self.readiness_tree.column("evidence", width=255)
        readiness_scroll = ttk.Scrollbar(
            readiness_table, orient="vertical", command=self.readiness_tree.yview,
        )
        self.readiness_xscroll = ttk.Scrollbar(
            readiness_table, orient="horizontal", command=self.readiness_tree.xview,
        )
        self.readiness_tree.configure(
            yscrollcommand=readiness_scroll.set,
            xscrollcommand=self.readiness_xscroll.set,
        )
        self.readiness_tree.grid(row=0, column=0, sticky="nsew")
        readiness_scroll.grid(row=0, column=1, sticky="ns")
        self.readiness_xscroll.grid(row=1, column=0, sticky="ew")
        readiness_table.rowconfigure(0, weight=1)
        readiness_table.columnconfigure(0, weight=1)

        finding_table = ttk.Frame(findings_page)
        finding_table.pack(fill="both", expand=True)
        self.finding_tree = ttk.Treeview(
            finding_table, columns=("severity", "message"), show="tree headings",
        )
        self.finding_tree.heading("#0", text="Code")
        self.finding_tree.heading("severity", text="Level")
        self.finding_tree.heading("message", text="Message")
        self.finding_tree.column("#0", width=175, stretch=False)
        self.finding_tree.column("severity", width=70, stretch=False)
        self.finding_tree.column("message", width=305)
        finding_scroll = ttk.Scrollbar(
            finding_table, orient="vertical", command=self.finding_tree.yview,
        )
        self.finding_xscroll = ttk.Scrollbar(
            finding_table, orient="horizontal", command=self.finding_tree.xview,
        )
        self.finding_tree.configure(
            yscrollcommand=finding_scroll.set,
            xscrollcommand=self.finding_xscroll.set,
        )
        self.finding_tree.grid(row=0, column=0, sticky="nsew")
        finding_scroll.grid(row=0, column=1, sticky="ns")
        self.finding_xscroll.grid(row=1, column=0, sticky="ew")
        finding_table.rowconfigure(0, weight=1)
        finding_table.columnconfigure(0, weight=1)
        self._install_filter_shortcuts()

    def _install_filter_shortcuts(self) -> None:
        """Scope filter shortcuts to widgets inside this embedded workspace."""
        tag = f"PedWorkbenchFilter:{id(self)}"
        self.bind_class(tag, "<Control-f>", self._focus_search)
        self.bind_class(tag, "<Escape>", self._clear_search)
        pending = [self]
        while pending:
            widget = pending.pop()
            tags = widget.bindtags()
            if tag not in tags:
                widget.bindtags((tags[0], tag, *tags[1:]))
            pending.extend(widget.winfo_children())

    def _focus_search(self, _event: object | None = None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _clear_search(self, _event: object | None = None) -> str:
        self.search.set("")
        return "break"

    def open_source(
        self,
        source: str | Path,
        scan: PackageScan,
        *,
        authoring_workspace: PedAuthoringWorkspace | None = None,
    ) -> None:
        selected_name = self.selected_ped.name if self.selected_ped else None
        self.source = Path(source).expanduser().resolve()
        self.scan = scan
        self.authoring_workspace = authoring_workspace
        self.selected_ped = None
        self._loaded_authoring_snapshot = None
        self._refresh_catalog()
        self.author_button.configure(
            state=(
                "disabled"
                if authoring_workspace is not None or not scan.peds else "normal"
            ),
            text=(
                "Authoring workspace active" if authoring_workspace is not None
                else "Create authoring workspace…"
            ),
        )
        self.status.set(
            f"{len(scan.peds)} peds · {sum(entry.suffix in {'.ydd', '.ydr', '.ytd'} for entry in scan.entries)} "
            f"visible model assets · {scan.warning_count} package warnings"
        )
        if self.ped_tree.get_children():
            selected = next((
                item_id for item_id, ped in self.peds.items()
                if selected_name is not None
                and ped.name.casefold() == selected_name.casefold()
            ), self.ped_tree.get_children()[0])
            self.ped_tree.selection_set(selected)
            self.ped_tree.focus(selected)
            self._select_ped()
        else:
            self._clear_project("No peds.meta records were discovered in this package.")

    def select_ped(self, name: str) -> bool:
        for item_id, ped in self.peds.items():
            if ped.name.casefold() == name.casefold():
                self.ped_tree.selection_set(item_id)
                self.ped_tree.focus(item_id)
                self.ped_tree.see(item_id)
                return self._select_ped()
        return False

    def _refresh_catalog(self) -> None:
        if not hasattr(self, "ped_tree"):
            return
        selected_name = self.selected_ped.name if self.selected_ped else None
        keep_dirty_selection = bool(
            selected_name
            and self.authoring_workspace is not None
            and self._loaded_authoring_snapshot is not None
            and self._authoring_snapshot() != self._loaded_authoring_snapshot
        )
        self.ped_tree.delete(*self.ped_tree.get_children())
        self.peds.clear()
        if self.scan is None:
            return
        query = self.search.get().strip().casefold()
        entry_stems = {
            PurePosixPath(entry.path).stem.casefold()
            for entry in self.scan.entries
            if entry.suffix in {".ydd", ".ydr", ".ytd"}
        }
        restored: str | None = None
        for index, ped in enumerate(self.scan.peds):
            searchable = " ".join((
                ped.name, ped.ped_type, ped.model_type, ped.props_name,
                ped.clip_dictionary, ped.expression_set, ped.movement_clip_set,
            )).casefold()
            if (
                query and query not in searchable
                and not (
                    keep_dirty_selection
                    and selected_name is not None
                    and ped.name.casefold() == selected_name.casefold()
                )
            ):
                continue
            ready = ped.name.casefold() in entry_stems
            item_id = f"ped:{index}"
            self.peds[item_id] = ped
            self.ped_tree.insert(
                "", "end", iid=item_id, text=ped.name,
                values=(ped.ped_type or "—", "Ready" if ready else "Review"),
            )
            if selected_name and selected_name == ped.name:
                restored = item_id
        if restored:
            self.ped_tree.selection_set(restored)
            self.ped_tree.focus(restored)
            self._select_ped()
        elif selected_name is not None:
            self._clear_project(
                f"No peds match {self.search.get().strip()!r}."
                if query else "Select a ped to inspect its project."
            )

    def _select_ped(self, _event: object | None = None) -> bool:
        selection = self.ped_tree.selection()
        ped = self.peds.get(selection[0]) if selection else None
        if ped is None or self.scan is None:
            return False
        retain_dirty_fields = bool(
            self.selected_ped is not None
            and self.selected_ped.name.casefold() == ped.name.casefold()
            and self._loaded_authoring_snapshot is not None
            and self._authoring_snapshot() != self._loaded_authoring_snapshot
        )
        if (
            not self._restoring_selection
            and self.selected_ped is not None
            and self.selected_ped.name.casefold() != ped.name.casefold()
            and not self.confirm_navigation()
        ):
            previous = next((
                item_id for item_id, item in self.peds.items()
                if item.name.casefold() == self.selected_ped.name.casefold()
            ), None)
            if previous is not None:
                self._restoring_selection = True
                try:
                    self.ped_tree.selection_set(previous)
                    self.ped_tree.focus(previous)
                finally:
                    self._restoring_selection = False
            return False
        self.selected_ped = ped
        self.heading.set(ped.name)
        self.summary.set(
            f"{ped.ped_type or 'Unknown ped type'} · "
            f"{ped.model_type or 'Unknown model type'} · "
            f"{ped.movement_clip_set or 'No movement clip set'}"
        )
        self._populate_fields(ped)
        assets = self._matching_assets(ped)
        self._populate_assets(assets, ped)
        self._populate_readiness(ped, assets)
        self._populate_findings(ped)
        if not retain_dirty_fields:
            self._load_authoring_fields(ped)
            self._load_builder_fields(ped)
        self._request_preview()
        return True

    def _populate_fields(self, ped: PedRecord) -> None:
        self.field_tree.delete(*self.field_tree.get_children())
        for field, value in (
            ("Name", ped.name), ("Pedtype", ped.ped_type),
            ("ModelType", ped.model_type), ("PropsName", ped.props_name),
            ("ClipDictionaryName", ped.clip_dictionary),
            ("ExpressionSetName", ped.expression_set),
            ("MovementClipSet", ped.movement_clip_set),
            ("CreatureMetadataName", ped.creature_metadata),
            ("Source", ped.source),
        ):
            self.field_tree.insert("", "end", text=field, values=(value or "—",))

    def _matching_assets(self, ped: PedRecord) -> list[PackageEntry]:
        if self.scan is None:
            return []
        tokens = {
            ped.name.casefold(), ped.props_name.casefold(),
            f"{ped.name.casefold()}_p",
        }
        tokens.discard("")
        matches: list[PackageEntry] = []
        for entry in self.scan.entries:
            if entry.suffix not in {
                ".ydd", ".ydr", ".ytd", ".ymt", ".ycd", ".meta", ".xml",
            }:
                continue
            stem = PurePosixPath(entry.path).stem.casefold()
            name = PurePosixPath(entry.path).name.casefold()
            if entry.path == ped.source or any(
                token == stem or (len(token) >= 5 and token in name)
                for token in tokens
            ):
                matches.append(entry)
        return matches

    @staticmethod
    def _asset_role(entry: PackageEntry, ped: PedRecord) -> str:
        stem = PurePosixPath(entry.path).stem.casefold()
        if entry.path == ped.source:
            return "Ped metadata"
        if entry.suffix in {".ydd", ".ydr"}:
            return "Props drawable" if stem.endswith("_p") else "Ped drawable"
        if entry.suffix == ".ytd":
            return "Props textures" if stem.endswith("_p") else "Ped textures"
        if entry.suffix in {".ycd", ".ymt"}:
            return "Animation / expression data"
        return entry.category

    def _populate_assets(self, assets: list[PackageEntry], ped: PedRecord) -> None:
        self.asset_tree.delete(*self.asset_tree.get_children())
        self._assets.clear()
        for index, entry in enumerate(assets):
            item_id = f"asset:{index}"
            self._assets[item_id] = entry
            self.asset_tree.insert(
                "", "end", iid=item_id, text=entry.path,
                values=(self._asset_role(entry, ped), _human_size(entry.size)),
            )
        self.asset_button.configure(state="disabled")

    def _populate_readiness(self, ped: PedRecord, assets: list[PackageEntry]) -> None:
        self.readiness_tree.delete(*self.readiness_tree.get_children())
        stems_by_suffix: dict[str, set[str]] = {}
        for entry in assets:
            stems_by_suffix.setdefault(entry.suffix, set()).add(
                PurePosixPath(entry.path).stem.casefold()
            )
        model = ped.name.casefold()
        drawables = stems_by_suffix.get(".ydd", set()) | stems_by_suffix.get(".ydr", set())
        textures = stems_by_suffix.get(".ytd", set())
        props = ped.props_name.casefold() if ped.props_name else f"{model}_p"
        definition_ready = bool(ped.name and ped.ped_type)
        stages = (
            ("Definition", "Ready" if definition_ready else "Review",
             "Name and ped type declared" if definition_ready else "Missing core peds.meta fields"),
            ("Drawable", "Ready" if model in drawables else "External",
             ped.name if model in drawables else "Packed in RPF or missing"),
            ("Textures", "Ready" if model in textures else "External",
             ped.name if model in textures else "Packed in RPF or missing"),
            ("Props", "Ready" if props in drawables or props in textures else "Optional",
             ped.props_name or "No explicit props name"),
            ("Movement", "Declared" if ped.movement_clip_set else "Review",
             ped.movement_clip_set or "No MovementClipSet"),
            ("Expressions", "Declared" if ped.expression_set else "Review",
             ped.expression_set or "No ExpressionSetName"),
        )
        for index, (stage, state, evidence) in enumerate(stages):
            self.readiness_tree.insert(
                "", "end", iid=f"stage:{index}", text=stage,
                values=(state, evidence),
            )

    def _populate_findings(self, ped: PedRecord) -> None:
        assert self.scan is not None
        self.finding_tree.delete(*self.finding_tree.get_children())
        ped_codes = {
            "ped_model_asset_not_found", "ped_texture_asset_not_found",
            "duplicate_record", "xml_parse_failed",
        }
        findings = [
            item for item in self.scan.findings
            if ped.name in item.message or item.path == ped.source or item.code in ped_codes
        ]
        for index, finding in enumerate(findings):
            self.finding_tree.insert(
                "", "end", iid=f"finding:{index}", text=finding.code,
                values=(finding.severity.title(), finding.message),
            )
        if not findings:
            self.finding_tree.insert(
                "", "end", text="ready", values=("Info", "No ped-specific findings."),
            )

    def _create_authoring_workspace(self) -> None:
        if self.source is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for ped authoring workspace",
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.source.stem}-ped-authoring"
        selected_name = self.selected_ped.name if self.selected_ped else None
        self.status.set("Copying ped source into a safe authoring workspace…")
        self.update_idletasks()
        try:
            workspace = PedAuthoringWorkspace.create(self.source, destination)
            scan = AddonPackageInspector().inspect(workspace.source)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Ped authoring workspace failed", str(exc), parent=self,
            )
            self.status.set("Ped authoring workspace was not created.")
            return
        self.open_source(
            workspace.source, scan, authoring_workspace=workspace,
        )
        if selected_name:
            self.select_ped(selected_name)
        self.status.set(f"Authoring workspace active: {workspace.root}")

    def _load_authoring_fields(self, ped: PedRecord) -> None:
        workspace = self.authoring_workspace
        self.authoring_name.set(ped.name)
        if workspace is None:
            for key, variable in self.authoring_values.items():
                variable.set("")
                self.authoring_inputs[key].configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.undo_author_button.configure(state="disabled")
            self.authoring_status.set(
                "Create an authoring workspace to edit this copied package safely."
            )
            self._loaded_authoring_snapshot = None
            self.migrate_name_entry.configure(state="disabled")
            self.migrate_props_entry.configure(state="disabled")
            self.migrate_button.configure(state="disabled")
            return
        try:
            values = workspace.values(ped.name)
        except (OSError, RuntimeError, ValueError) as exc:
            for entry in self.authoring_inputs.values():
                entry.configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.authoring_status.set(f"Ped authoring unavailable: {exc}")
            self._loaded_authoring_snapshot = None
            self.migrate_name_entry.configure(state="disabled")
            self.migrate_props_entry.configure(state="disabled")
            self.migrate_button.configure(state="disabled")
            return
        for key, variable in self.authoring_values.items():
            variable.set(values.values.get(key, ""))
            self.authoring_inputs[key].configure(state="normal")
        self.save_author_button.configure(state="normal")
        self.undo_author_button.configure(
            state="normal" if self._has_authoring_history() else "disabled",
        )
        self.authoring_status.set(
            f"Revision {workspace.revision}. Apply is atomic and revalidates the "
            "complete copied package; failed edits roll back."
        )
        self.migrate_name.set("")
        self.migrate_props.set("")
        self.migrate_name_entry.configure(state="normal")
        self.migrate_props_entry.configure(state="normal")
        self.migrate_button.configure(state="normal")
        self._loaded_authoring_snapshot = self._authoring_snapshot()

    def _load_builder_fields(self, ped: PedRecord) -> None:
        self.clone_donor.set(ped.name)
        self.clone_name.set("")
        self.clone_props.set("")
        self._reviewed_clone_plan = None
        enabled = self.authoring_workspace is not None
        self.review_clone_button.configure(state="normal" if enabled else "disabled")
        self.apply_clone_button.configure(state="disabled")
        self.clone_status.set(
            "Enter a new model identity, then review exact metadata and asset evidence."
            if enabled else
            "Create an authoring workspace before cloning a ped record."
        )

    def _invalidate_clone_plan(self) -> None:
        if not hasattr(self, "apply_clone_button"):
            return
        self._reviewed_clone_plan = None
        self.apply_clone_button.configure(state="disabled")

    def _review_clone(self) -> None:
        workspace = self.authoring_workspace
        ped = self.selected_ped
        if workspace is None or ped is None:
            return
        try:
            updates = {}
            if self.clone_props.get().strip():
                updates["ped.propsName"] = self.clone_props.get().strip()
            plan = workspace.plan_ped_clone(
                ped.name, ped_name=self.clone_name.get(), updates=updates,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._reviewed_clone_plan = None
            self.apply_clone_button.configure(state="disabled")
            self.clone_status.set(f"Plan rejected: {exc}")
            messagebox.showerror("Ped clone plan rejected", str(exc), parent=self)
            return
        self._reviewed_clone_plan = plan
        blockers = [
            item.code for item in plan.findings if item.severity == "error"
        ]
        if blockers:
            self.apply_clone_button.configure(state="disabled")
            self.clone_status.set(
                "Not ready · " + ", ".join(blockers)
                + ". Add one exact target model/texture asset family, then review again."
            )
            return
        self.clone_name.set(plan.spec.ped_name)
        self.clone_props.set(plan.spec.updates.get("ped.propsName", ""))
        # Setting the normalized fields invalidates the plan through the trace;
        # retain the exact just-reviewed object after all UI normalization.
        self._reviewed_clone_plan = plan
        self.apply_clone_button.configure(state="normal")
        self.clone_status.set(
            f"Ready · revision {plan.revision} · {len(plan.selected_sources)} "
            f"hashed sources · plan {plan.plan_sha256[:12]}…"
        )

    def _apply_clone(self) -> None:
        workspace = self.authoring_workspace
        plan = self._reviewed_clone_plan
        if workspace is None or plan is None:
            return
        if not messagebox.askyesno(
            "Create reviewed ped?",
            f"Create {plan.spec.ped_name} from {plan.spec.donor_ped} using the "
            f"reviewed plan {plan.plan_sha256[:12]}…?",
            parent=self,
        ):
            return
        try:
            result = workspace.clone_ped_bundle(
                plan,
                expected_revision=plan.revision,
                expected_plan_sha256=plan.plan_sha256,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._invalidate_clone_plan()
            self.clone_status.set(f"Create rejected and rolled back: {exc}")
            messagebox.showerror("Ped create rejected", str(exc), parent=self)
            return
        target = plan.spec.ped_name
        self._reviewed_clone_plan = None
        self._reload_authoring_workspace(target)
        self.status.set(
            f"Created {target} from reviewed metadata · revision {result.revision}"
        )

    def _migrate_identity(self) -> None:
        workspace = self.authoring_workspace
        ped = self.selected_ped
        if workspace is None or ped is None:
            return
        target = self.migrate_name.get().strip()
        props_text = self.migrate_props.get().strip()
        if not messagebox.askyesno(
            "Migrate ped identity?",
            f"Rename {ped.name} and every exact package-owned streamed asset to "
            f"{target or '(missing identity)'}?",
            parent=self,
        ):
            return
        try:
            result = workspace.migrate_identity(
                ped.name,
                new_name=target,
                new_props=props_text or None,
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.authoring_status.set(f"Identity migration rejected: {exc}")
            messagebox.showerror("Ped identity migration rejected", str(exc), parent=self)
            return
        self._reload_authoring_workspace(result.ped)
        self.status.set(
            f"Migrated ped identity and streamed assets · revision {result.revision}"
        )

    def _save_authoring_fields(self) -> None:
        workspace = self.authoring_workspace
        ped = self.selected_ped
        if workspace is None or ped is None:
            return
        try:
            current = workspace.values(ped.name)
            updates = {
                key: variable.get()
                for key, variable in self.authoring_values.items()
                if variable.get().strip() != current.values.get(key, "")
            }
            if not updates:
                raise ValueError("No ped authoring fields have changed")
            if not messagebox.askyesno(
                "Apply ped metadata edit?",
                f"Apply {len(updates)} field change(s) to the copied workspace?",
                parent=self,
            ):
                return
            result = workspace.update(
                ped.name, updates, expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Ped edit rejected", str(exc), parent=self)
            self.authoring_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(ped.name)
        self.status.set(
            f"Applied {len(result.changes)} ped field(s) · revision {result.revision}"
        )

    def _undo_authoring_edit(self) -> None:
        workspace = self.authoring_workspace
        ped = self.selected_ped
        if workspace is None or ped is None:
            return
        if not messagebox.askyesno(
            "Undo latest ped edit?",
            "Restore the latest ped metadata edit from verified local history?",
            parent=self,
        ):
            return
        try:
            result = workspace.undo(expected_revision=workspace.revision)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Ped undo rejected", str(exc), parent=self)
            self.authoring_status.set(f"Undo rejected: {exc}")
            return
        self._reload_authoring_workspace(ped.name)
        self.status.set(f"Undid latest ped edit · revision {result.revision}")

    def _reload_authoring_workspace(self, ped_name: str) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            return
        scan = AddonPackageInspector().inspect(workspace.source)
        self.open_source(
            workspace.source, scan, authoring_workspace=workspace,
        )
        self.select_ped(ped_name)

    def _has_authoring_history(self) -> bool:
        workspace = self.authoring_workspace
        if workspace is None:
            return False
        history = workspace.root / "history"
        try:
            return any(
                path.is_dir()
                and (path / "edit.json").is_file()
                and not path.name.endswith((".undone", ".undo-recovery"))
                for path in history.iterdir()
            )
        except OSError:
            return False

    def _authoring_snapshot(self) -> tuple[str, ...]:
        return tuple(
            self.authoring_values[key].get() for _label, key in PED_AUTHOR_FIELDS
        )

    def confirm_navigation(self) -> bool:
        if (
            self._loaded_authoring_snapshot is None
            or self._authoring_snapshot() == self._loaded_authoring_snapshot
        ):
            return True
        return messagebox.askyesno(
            "Discard unapplied ped changes?",
            "The Ped Author tab has unapplied field changes. Discard them?",
            parent=self,
        )

    def _request_preview(self) -> None:
        source = self.source
        scan = self.scan
        ped = self.selected_ped
        if source is None or scan is None or ped is None:
            return
        model_matches = sorted(
            (
                entry for entry in scan.entries
                if entry.suffix in {".ydd", ".ydr"}
                and PurePosixPath(entry.path).stem.casefold()
                == ped.name.casefold()
            ),
            key=lambda item: (item.suffix != ".ydd", item.path.casefold()),
        )
        texture_matches = sorted(
            (
                entry for entry in scan.entries
                if entry.suffix == ".ytd"
                and PurePosixPath(entry.path).stem.casefold()
                == ped.name.casefold()
            ),
            key=lambda item: item.path.casefold(),
        )
        self.refresh_preview_button.configure(
            state="normal" if model_matches or texture_matches else "disabled",
        )
        if not model_matches and not texture_matches:
            self._preview_source_images = (None, None)
            self._fit_preview_images()
            self.model_preview.configure(text="No exact ped drawable was found.")
            self.texture_preview.configure(text="No exact ped texture dictionary was found.")
            self.preview_status.set(
                "Preview unavailable: the selected ped's native assets are external or missing."
            )
            return
        model = model_matches[0] if model_matches else None
        texture = texture_matches[0] if texture_matches else None
        key = (
            str(source),
            f"{model.path}:{model.size}" if model else "no-model",
            f"{texture.path}:{texture.size}" if texture else "no-texture",
        )
        edition = scan.edition_tag
        game_path = self.installation_roots[0] if self.installation_roots else None
        self.preview_status.set("Loading native ped preview in the background…")
        self._preview_worker.submit(
            key,
            lambda: self._render_preview_bundle(
                source, model, texture, edition, game_path,
            ),
        )
        if self._preview_poll_id is None:
            self._preview_poll_id = self.after(50, self._poll_preview)

    def _render_preview_bundle(
        self,
        source: Path,
        model: PackageEntry | None,
        texture: PackageEntry | None,
        edition: str,
        game_path: Path | None,
    ) -> tuple[bytes | None, bytes | None, str]:
        reader = PackageAssetReader(source)
        inspector = NativeAssetInspector(self.project_root, game_path)
        images: list[bytes | None] = []
        notes: list[str] = []
        for entry, label in ((model, "model"), (texture, "texture")):
            if entry is None:
                images.append(None)
                notes.append(f"No exact {label} asset")
                continue
            content = reader.read(
                entry.path, limit=native_preview_limit(entry.path, entry.size),
            )
            report = inspector.inspect_bytes(
                entry.path, content.data,
                edition=edition, truncated=content.truncated,
            )
            images.append(report.image_png)
            if report.image_png is None:
                notes.append(
                    f"{label.title()} preview unavailable"
                    + (f": {'; '.join(report.warnings)}" if report.warnings else "")
                )
        return images[0], images[1], " · ".join(notes) or (
            "Decoded diagnostic model view and packaged texture sheet."
        )

    def _poll_preview(self) -> None:
        self._preview_poll_id = None
        outcome = self._preview_worker.poll()
        if outcome is not None:
            if outcome.error is not None:
                self._preview_source_images = (None, None)
                self.preview_status.set(f"Preview unavailable: {outcome.error}")
            elif outcome.value is not None:
                model_png, texture_png, note = outcome.value
                try:
                    self._preview_source_images = (
                        self._decode_preview_image(model_png),
                        self._decode_preview_image(texture_png),
                    )
                    self.preview_status.set(note)
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    self._preview_source_images = (None, None)
                    self.preview_status.set(f"Preview image could not be decoded: {exc}")
                self._fit_preview_images()
        if self._preview_worker.busy and self.winfo_exists():
            self._preview_poll_id = self.after(50, self._poll_preview)

    @staticmethod
    def _decode_preview_image(data: bytes | None) -> Image.Image | None:
        if data is None:
            return None
        with Image.open(io.BytesIO(data)) as opened:
            return ImageOps.exif_transpose(opened).convert("RGBA")

    def _fit_preview_images(self, _event: object | None = None) -> None:
        for label, source, attribute, empty in (
            (
                self.model_preview, self._preview_source_images[0],
                "_preview_photo_model", "No renderable model preview.",
            ),
            (
                self.texture_preview, self._preview_source_images[1],
                "_preview_photo_texture", "No texture sheet preview.",
            ),
        ):
            if source is None:
                setattr(self, attribute, None)
                label.configure(image="", text=empty)
                continue
            width = max(80, label.winfo_width() - 12)
            height = max(80, label.winfo_height() - 12)
            rendered = ImageOps.contain(
                source, (width, height), Image.Resampling.LANCZOS,
            )
            photo = ImageTk.PhotoImage(rendered, master=self)
            setattr(self, attribute, photo)
            label.configure(image=photo, text="")

    def _destroyed(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._preview_poll_id is not None:
            try:
                self.after_cancel(self._preview_poll_id)
            except tk.TclError:
                pass
            self._preview_poll_id = None
        self._preview_worker.close(wait=False)

    def _asset_selected(self, _event: object | None = None) -> None:
        selected = self.asset_tree.selection()
        self.asset_button.configure(
            state="normal" if selected and selected[0] in self._assets else "disabled",
        )

    def _open_selected_asset(self, _event: object | None = None) -> str | None:
        selected = self.asset_tree.selection()
        entry = self._assets.get(selected[0]) if selected else None
        if entry is not None and self._on_open_asset is not None:
            self._on_open_asset(entry.path)
        return "break" if _event is not None else None

    def _clear_project(self, message: str) -> None:
        self.selected_ped = None
        self.heading.set("No ped selected")
        self.summary.set(message)
        for tree in (
            self.field_tree, self.asset_tree, self.readiness_tree, self.finding_tree,
        ):
            tree.delete(*tree.get_children())
        self._assets.clear()
        self.asset_button.configure(state="disabled")
        self.authoring_name.set("No ped selected")
        for key, variable in self.authoring_values.items():
            variable.set("")
            self.authoring_inputs[key].configure(state="disabled")
        self.save_author_button.configure(state="disabled")
        self.undo_author_button.configure(state="disabled")
        self.authoring_status.set("Select a ped before editing package metadata.")
        self.clone_donor.set("No ped selected")
        self.clone_name.set("")
        self.clone_props.set("")
        self.review_clone_button.configure(state="disabled")
        self.apply_clone_button.configure(state="disabled")
        self._reviewed_clone_plan = None
        self.migrate_name_entry.configure(state="disabled")
        self.migrate_props_entry.configure(state="disabled")
        self.migrate_button.configure(state="disabled")
        self.refresh_preview_button.configure(state="disabled")
        self._preview_worker.invalidate()
        self._preview_source_images = (None, None)
        self._fit_preview_images()
        self._loaded_authoring_snapshot = None

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("ped-workbench")
