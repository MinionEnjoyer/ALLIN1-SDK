"""Integrated read-only weapon project workbench."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import ttk

from allin1_sdk.addon_importer import (
    AmmoRecord,
    PackageEntry,
    PackageScan,
    WeaponComponentRecord,
    WeaponRecord,
)
from allin1_sdk.collapsible_panes import CollapsibleSidePanes


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


class WeaponWorkbenchFrame(ttk.Frame):
    """Review weapon definitions, ammo, components, assets, and integration links."""

    def __init__(self, parent: tk.Misc, *, on_open_asset=None, on_help=None) -> None:
        super().__init__(parent)
        self._on_open_asset = on_open_asset
        self._on_help = on_help
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.weapons: dict[str, WeaponRecord] = {}
        self.selected_weapon: WeaponRecord | None = None
        self._assets: dict[str, PackageEntry] = {}
        self.search = tk.StringVar()
        self.status = tk.StringVar(
            value="Open a package in Workbench to inspect its weapon systems."
        )
        self.heading = tk.StringVar(value="No weapon selected")
        self.summary = tk.StringVar(
            value="Definitions, attachments, ammo, animations, and shop links appear here."
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
        search = ttk.Entry(toolbar, textvariable=self.search, width=28)
        self.search_entry = search
        search.pack(side="left")
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
            left_label="Weapons", right_label="Integration",
        )
        self.primary_side_panes = side_panes
        catalog = ttk.LabelFrame(side_panes.left_host, text="Weapons", padding=8)
        project = ttk.LabelFrame(
            side_panes.center_host, text="Weapon project", padding=8,
        )
        integration = ttk.LabelFrame(
            side_panes.right_host, text="Integration", padding=8,
        )
        self.catalog_panel = catalog
        self.work_panel = project
        self.integration_panel = integration
        side_panes.set_contents(catalog, project, integration)

        catalog_table = ttk.Frame(catalog)
        catalog_table.pack(fill="both", expand=True)
        self.weapon_tree = ttk.Treeview(
            catalog_table, columns=("state", "parts"), show="tree headings",
            selectmode="browse",
        )
        self.weapon_tree.heading("#0", text="Weapon")
        self.weapon_tree.heading("state", text="Status")
        self.weapon_tree.heading("parts", text="Parts")
        self.weapon_tree.column("#0", width=210, minwidth=130)
        self.weapon_tree.column("state", width=76, stretch=False)
        self.weapon_tree.column("parts", width=52, stretch=False, anchor="center")
        catalog_scroll = ttk.Scrollbar(
            catalog_table, orient="vertical", command=self.weapon_tree.yview,
        )
        self.catalog_xscroll = ttk.Scrollbar(
            catalog_table, orient="horizontal", command=self.weapon_tree.xview,
        )
        self.weapon_tree.configure(
            yscrollcommand=catalog_scroll.set,
            xscrollcommand=self.catalog_xscroll.set,
        )
        self.weapon_tree.grid(row=0, column=0, sticky="nsew")
        catalog_scroll.grid(row=0, column=1, sticky="ns")
        self.catalog_xscroll.grid(row=1, column=0, sticky="ew")
        catalog_table.rowconfigure(0, weight=1)
        catalog_table.columnconfigure(0, weight=1)
        self.weapon_tree.bind("<<TreeviewSelect>>", self._select_weapon)

        ttk.Label(
            project, textvariable=self.heading, style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            project, textvariable=self.summary, foreground="#52635c",
            wraplength=610, justify="left",
        ).pack(anchor="w", pady=(2, 8))
        self.project_tabs = ttk.Notebook(project)
        self.project_tabs.pack(fill="both", expand=True)
        definition_page = ttk.Frame(self.project_tabs, padding=8)
        component_page = ttk.Frame(self.project_tabs, padding=8)
        asset_page = ttk.Frame(self.project_tabs, padding=8)
        self.project_tabs.add(definition_page, text="Definition + ammo")
        self.project_tabs.add(component_page, text="Attachments")
        self.project_tabs.add(asset_page, text="Assets")

        field_table = ttk.Frame(definition_page)
        field_table.pack(fill="both", expand=True)
        self.field_tree = ttk.Treeview(
            field_table, columns=("value",), show="tree headings",
        )
        self.field_tree.heading("#0", text="Field")
        self.field_tree.heading("value", text="Resolved value")
        self.field_tree.column("#0", width=170, stretch=False)
        self.field_tree.column("value", width=390)
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

        component_table = ttk.Frame(component_page)
        component_table.pack(fill="both", expand=True)
        self.component_tree = ttk.Treeview(
            component_table,
            columns=("component", "bone", "default", "model", "definition"),
            show="headings", selectmode="browse",
        )
        for name, label, width in (
            ("component", "Component", 255), ("bone", "Bone", 105),
            ("default", "Default", 65), ("model", "Model", 170),
            ("definition", "Definition", 88),
        ):
            self.component_tree.heading(name, text=label)
            self.component_tree.column(name, width=width, minwidth=55)
        component_scroll = ttk.Scrollbar(
            component_table, orient="vertical", command=self.component_tree.yview,
        )
        self.component_xscroll = ttk.Scrollbar(
            component_table, orient="horizontal", command=self.component_tree.xview,
        )
        self.component_tree.configure(
            yscrollcommand=component_scroll.set,
            xscrollcommand=self.component_xscroll.set,
        )
        self.component_tree.grid(row=0, column=0, sticky="nsew")
        component_scroll.grid(row=0, column=1, sticky="ns")
        self.component_xscroll.grid(row=1, column=0, sticky="ew")
        component_table.rowconfigure(0, weight=1)
        component_table.columnconfigure(0, weight=1)

        asset_table = ttk.Frame(asset_page)
        asset_table.pack(fill="both", expand=True)
        self.asset_tree = ttk.Treeview(
            asset_table, columns=("kind", "size"), show="tree headings",
            selectmode="browse",
        )
        self.asset_tree.heading("#0", text="Package path")
        self.asset_tree.heading("kind", text="Type")
        self.asset_tree.heading("size", text="Size")
        self.asset_tree.column("#0", width=390)
        self.asset_tree.column("kind", width=110, stretch=False)
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

        integration_tabs = ttk.Notebook(integration)
        integration_tabs.pack(fill="both", expand=True)
        readiness_page = ttk.Frame(integration_tabs, padding=7)
        findings_page = ttk.Frame(integration_tabs, padding=7)
        integration_tabs.add(readiness_page, text="Readiness")
        integration_tabs.add(findings_page, text="Findings")

        readiness_table = ttk.Frame(readiness_page)
        readiness_table.pack(fill="both", expand=True)
        self.readiness_tree = ttk.Treeview(
            readiness_table, columns=("status", "evidence"), show="tree headings",
        )
        self.readiness_tree.heading("#0", text="System")
        self.readiness_tree.heading("status", text="Status")
        self.readiness_tree.heading("evidence", text="Evidence")
        self.readiness_tree.column("#0", width=120, stretch=False)
        self.readiness_tree.column("status", width=78, stretch=False)
        self.readiness_tree.column("evidence", width=260)
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
        self.finding_tree.column("#0", width=170, stretch=False)
        self.finding_tree.column("severity", width=70, stretch=False)
        self.finding_tree.column("message", width=310)
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
        tag = f"WeaponWorkbenchFilter:{id(self)}"
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
        self.selected_weapon = None
        self._refresh_catalog()
        self.status.set(
            f"{len(scan.weapons)} weapons · {len(scan.weapon_components)} component "
            f"definitions · {scan.warning_count} package warnings"
        )
        if self.weapon_tree.get_children():
            first = self.weapon_tree.get_children()[0]
            self.weapon_tree.selection_set(first)
            self.weapon_tree.focus(first)
            self._select_weapon()
        else:
            self._clear_project(
                "No weapons.meta records were discovered in this package."
            )

    def select_weapon(self, name: str) -> bool:
        for item_id, weapon in self.weapons.items():
            if weapon.name.casefold() == name.casefold():
                self.weapon_tree.selection_set(item_id)
                self.weapon_tree.focus(item_id)
                self.weapon_tree.see(item_id)
                self._select_weapon()
                return True
        return False

    def _refresh_catalog(self) -> None:
        if not hasattr(self, "weapon_tree"):
            return
        selected_name = self.selected_weapon.name if self.selected_weapon else None
        self.weapon_tree.delete(*self.weapon_tree.get_children())
        self.weapons.clear()
        if self.scan is None:
            return
        query = self.search.get().strip().casefold()
        component_counts: dict[str, int] = {}
        for link in self.scan.weapon_component_links:
            key = link.weapon_name.casefold()
            component_counts[key] = component_counts.get(key, 0) + 1
        ammo_names = {item.name.casefold() for item in self.scan.ammo}
        animations = {item.casefold() for item in self.scan.animation_weapons}
        shops = {item.casefold() for item in self.scan.shop_weapons}
        restored: str | None = None
        for index, weapon in enumerate(self.scan.weapons):
            searchable = " ".join((
                weapon.name, weapon.slot, weapon.ammo_info, weapon.model,
                weapon.human_name_hash, weapon.stat_name,
            )).casefold()
            if query and query not in searchable:
                continue
            ready = bool(
                weapon.slot and weapon.ammo_info.casefold() in ammo_names
                and weapon.name.casefold() in animations
                and weapon.name.casefold() in shops
            )
            item_id = f"weapon:{index}"
            self.weapons[item_id] = weapon
            self.weapon_tree.insert(
                "", "end", iid=item_id, text=weapon.name,
                values=(
                    "Ready" if ready else "Review",
                    component_counts.get(weapon.name.casefold(), 0),
                ),
            )
            if selected_name and weapon.name == selected_name:
                restored = item_id
        if restored:
            self.weapon_tree.selection_set(restored)
            self.weapon_tree.focus(restored)
            self._select_weapon()
        elif selected_name is not None:
            self._clear_project(
                f"No weapons match {self.search.get().strip()!r}."
                if query else "Select a weapon to inspect its project."
            )

    def _select_weapon(self, _event: object | None = None) -> None:
        selection = self.weapon_tree.selection()
        weapon = self.weapons.get(selection[0]) if selection else None
        if weapon is None or self.scan is None:
            return
        self.selected_weapon = weapon
        self.heading.set(weapon.name)
        self.summary.set(
            f"{weapon.model or 'No model'} · {weapon.slot or 'No wheel slot'} · "
            f"{weapon.human_name_hash or 'No display label'}"
        )
        ammo = next((
            item for item in self.scan.ammo
            if item.name.casefold() == weapon.ammo_info.casefold()
        ), None)
        self._populate_fields(weapon, ammo)
        definitions = {
            item.name.casefold(): item for item in self.scan.weapon_components
        }
        links = [
            item for item in self.scan.weapon_component_links
            if item.weapon_name.casefold() == weapon.name.casefold()
        ]
        self._populate_components(links, definitions)
        assets = self._matching_assets(weapon, links, definitions)
        self._populate_assets(assets)
        self._populate_readiness(weapon, ammo, links, assets)
        self._populate_findings(weapon)

    def _populate_fields(
        self, weapon: WeaponRecord, ammo: AmmoRecord | None,
    ) -> None:
        self.field_tree.delete(*self.field_tree.get_children())
        rows = (
            ("Weapon", "Name", weapon.name),
            ("Weapon", "Slot", weapon.slot),
            ("Weapon", "AmmoInfo", weapon.ammo_info),
            ("Weapon", "Model", weapon.model),
            ("Weapon", "HumanNameHash", weapon.human_name_hash),
            ("Weapon", "StatName", weapon.stat_name),
            ("Weapon", "Source", weapon.source),
        )
        groups: dict[str, str] = {}
        for group, field, value in rows:
            parent = groups.get(group)
            if parent is None:
                parent = self.field_tree.insert("", "end", text=group, open=True)
                groups[group] = parent
            self.field_tree.insert(parent, "end", text=field, values=(value or "—",))
        ammo_parent = self.field_tree.insert("", "end", text="Ammo", open=True)
        ammo_rows = (
            ("Name", ammo.name if ammo else weapon.ammo_info),
            ("Model", ammo.model if ammo else ""),
            ("AmmoMax", ammo.ammo_max if ammo else ""),
            ("AmmoMax50", ammo.ammo_max_50 if ammo else ""),
            ("Explosion", ammo.explosion if ammo else ""),
            ("TrailFx", ammo.trail_fx if ammo else ""),
            ("PrimedFx", ammo.primed_fx if ammo else ""),
            ("Source", ammo.source if ammo else ""),
        )
        for field, value in ammo_rows:
            self.field_tree.insert(
                ammo_parent, "end", text=field, values=(value or "—",),
            )

    def _populate_components(self, links, definitions: dict[str, WeaponComponentRecord]) -> None:
        self.component_tree.delete(*self.component_tree.get_children())
        for index, link in enumerate(links):
            component = definitions.get(link.component_name.casefold())
            self.component_tree.insert(
                "", "end", iid=f"component:{index}", values=(
                    link.component_name,
                    link.attach_bone or (component.attach_bone if component else "—"),
                    "Yes" if link.default else "No",
                    component.model if component and component.model else "—",
                    "Package" if component else "Stock / external",
                ),
            )
        if not links:
            self.component_tree.insert(
                "", "end", values=("No attachment points declared", "—", "—", "—", "—"),
            )

    def _matching_assets(self, weapon, links, definitions) -> list[PackageEntry]:
        if self.scan is None:
            return []
        tokens = {
            weapon.model.casefold(),
            weapon.name.removeprefix("WEAPON_").casefold(),
        }
        for link in links:
            component = definitions.get(link.component_name.casefold())
            if component and component.model:
                tokens.add(component.model.casefold())
        tokens.discard("")
        source_paths = {weapon.source}
        source_paths.update(link.source for link in links)
        source_paths.update(
            component.source for component in definitions.values()
            if component.name.casefold() in {
                link.component_name.casefold() for link in links
            }
        )
        matches: list[PackageEntry] = []
        for entry in self.scan.entries:
            if entry.suffix not in {
                ".ydr", ".ydd", ".yft", ".ytd", ".ybn", ".meta", ".xml",
            }:
                continue
            stem = PurePosixPath(entry.path).stem.casefold()
            name = PurePosixPath(entry.path).name.casefold()
            if entry.path in source_paths or any(
                token == stem or (len(token) >= 6 and token in name)
                for token in tokens
            ):
                matches.append(entry)
        return matches

    def _populate_assets(self, assets: list[PackageEntry]) -> None:
        self.asset_tree.delete(*self.asset_tree.get_children())
        self._assets.clear()
        for index, entry in enumerate(assets):
            item_id = f"asset:{index}"
            self._assets[item_id] = entry
            self.asset_tree.insert(
                "", "end", iid=item_id, text=entry.path,
                values=(entry.category, _human_size(entry.size)),
            )
        self.asset_button.configure(state="disabled")

    def _populate_readiness(self, weapon, ammo, links, assets) -> None:
        assert self.scan is not None
        self.readiness_tree.delete(*self.readiness_tree.get_children())
        animation_names = {
            item.casefold() for item in self.scan.animation_weapons
        }
        shop_names = {item.casefold() for item in self.scan.shop_weapons}
        has_animation = weapon.name.casefold() in animation_names
        has_shop = weapon.name.casefold() in shop_names
        definition_ready = all((
            weapon.name, weapon.slot, weapon.ammo_info, weapon.model,
            weapon.human_name_hash, weapon.stat_name,
        ))
        stages = (
            ("Definition", "Ready" if definition_ready else "Review",
             "All required fields present" if definition_ready else "Missing required fields"),
            ("Ammo", "Ready" if ammo else "Missing",
             ammo.name if ammo else (weapon.ammo_info or "No AmmoInfo")),
            ("Animations", "Ready" if has_animation else "Missing",
             "Mapping discovered" if has_animation else "No weaponanimations mapping"),
            ("Shop", "Ready" if has_shop else "Missing",
             "Registration discovered" if has_shop else "No weapon_shop entry"),
            ("Attachments", "Ready" if links else "Optional",
             f"{len(links)} attachment choices" if links else "None declared"),
            ("Package assets", "Found" if assets else "External",
             f"{len(assets)} related files" if assets else "Stock or packed inside an RPF"),
        )
        for index, (stage, state, evidence) in enumerate(stages):
            self.readiness_tree.insert(
                "", "end", iid=f"stage:{index}", text=stage,
                values=(state, evidence),
            )

    def _populate_findings(self, weapon: WeaponRecord) -> None:
        assert self.scan is not None
        self.finding_tree.delete(*self.finding_tree.get_children())
        relevant_codes = {
            "weapon_ammo_reference_missing", "ammo_definition_not_found",
            "animation_mapping_not_found", "storefront_mapping_not_found",
            "weapon_component_definition_not_found",
            "duplicate_record", "xml_parse_failed",
        }
        findings = [
            item for item in self.scan.findings
            if weapon.name in item.message
            or item.path == weapon.source
            or item.code in relevant_codes
        ]
        for index, finding in enumerate(findings):
            self.finding_tree.insert(
                "", "end", iid=f"finding:{index}", text=finding.code,
                values=(finding.severity.title(), finding.message),
            )
        if not findings:
            self.finding_tree.insert(
                "", "end", text="ready", values=("Info", "No weapon-specific findings."),
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
        self.selected_weapon = None
        self.heading.set("No weapon selected")
        self.summary.set(message)
        for tree in (
            self.field_tree, self.component_tree, self.asset_tree,
            self.readiness_tree, self.finding_tree,
        ):
            tree.delete(*tree.get_children())
        self._assets.clear()
        self.asset_button.configure(state="disabled")

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("weapon-workbench")
