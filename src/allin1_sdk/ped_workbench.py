"""Integrated read-only ped project workbench."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import ttk

from allin1_sdk.addon_importer import PackageEntry, PackageScan, PedRecord
from allin1_sdk.collapsible_panes import CollapsibleSidePanes


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


class PedWorkbenchFrame(ttk.Frame):
    """Review peds.meta records and their visible streamed asset families."""

    def __init__(self, parent: tk.Misc, *, on_open_asset=None, on_help=None) -> None:
        super().__init__(parent)
        self._on_open_asset = on_open_asset
        self._on_help = on_help
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.peds: dict[str, PedRecord] = {}
        self.selected_ped: PedRecord | None = None
        self._assets: dict[str, PackageEntry] = {}
        self.search = tk.StringVar()
        self.status = tk.StringVar(
            value="Open a package in Workbench to inspect its ped systems."
        )
        self.heading = tk.StringVar(value="No ped selected")
        self.summary = tk.StringVar(
            value="Definitions, drawable dictionaries, textures, props, and clips appear here."
        )
        self._build()
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
        asset_page = ttk.Frame(project_tabs, padding=8)
        project_tabs.add(definition_page, text="Definition")
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

    def open_source(self, source: str | Path, scan: PackageScan) -> None:
        self.source = Path(source).expanduser().resolve()
        self.scan = scan
        self.selected_ped = None
        self._refresh_catalog()
        self.status.set(
            f"{len(scan.peds)} peds · {sum(entry.suffix in {'.ydd', '.ydr', '.ytd'} for entry in scan.entries)} "
            f"visible model assets · {scan.warning_count} package warnings"
        )
        if self.ped_tree.get_children():
            first = self.ped_tree.get_children()[0]
            self.ped_tree.selection_set(first)
            self.ped_tree.focus(first)
            self._select_ped()
        else:
            self._clear_project("No peds.meta records were discovered in this package.")

    def select_ped(self, name: str) -> bool:
        for item_id, ped in self.peds.items():
            if ped.name.casefold() == name.casefold():
                self.ped_tree.selection_set(item_id)
                self.ped_tree.focus(item_id)
                self.ped_tree.see(item_id)
                self._select_ped()
                return True
        return False

    def _refresh_catalog(self) -> None:
        if not hasattr(self, "ped_tree"):
            return
        selected_name = self.selected_ped.name if self.selected_ped else None
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
            if query and query not in searchable:
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

    def _select_ped(self, _event: object | None = None) -> None:
        selection = self.ped_tree.selection()
        ped = self.peds.get(selection[0]) if selection else None
        if ped is None or self.scan is None:
            return
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

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("ped-workbench")
