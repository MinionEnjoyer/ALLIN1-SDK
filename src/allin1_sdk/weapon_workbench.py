"""Integrated weapon inspection and guarded authoring workbench."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    AmmoRecord,
    PackageEntry,
    PackageScan,
    WeaponComponentLink,
    WeaponComponentRecord,
    WeaponRecord,
)
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace


WEAPON_AUTHOR_FIELDS = (
    ("Slot", "weapon.slot"),
    ("Ammo info", "weapon.ammoInfo"),
    ("Model", "weapon.model"),
    ("Display label", "weapon.humanNameHash"),
    ("Stat name", "weapon.statName"),
    ("Ammo model", "ammo.model"),
    ("Max ammo", "ammo.ammoMax"),
    ("Max ammo at 50%", "ammo.ammoMax50"),
    ("Explosion", "ammo.explosion"),
    ("Trail effect", "ammo.trailFx"),
    ("Primed effect", "ammo.primedFx"),
)

COMPONENT_AUTHOR_FIELDS = (
    ("Model", "component.model"),
    ("Display label", "component.locName"),
    ("Description label", "component.locDesc"),
    ("Attach bone", "component.attachBone"),
    ("Component type", "component.type"),
)

SHOP_AUTHOR_FIELDS = (
    ("Purchase cost", "shop.cost"),
    ("Ammo cost", "shop.ammoCost"),
    ("Display label", "shop.textLabel"),
    ("Description label", "shop.weaponDesc"),
    ("Tooltip label", "shop.weaponTT"),
    ("Uppercase label", "shop.weaponUppercase"),
    ("Available in Story Mode", "shop.availableInSP"),
)

WEAPON_CLONE_FIELDS = (
    ("Weapon identity", "weapon_name"),
    ("Wheel slot", "slot"),
    ("Model", "model"),
    ("Display label", "human_name_hash"),
    ("Stat name", "stat_name"),
)

AMMO_MODE_CLONE = "Clone linked ammo"
AMMO_MODE_REUSE = "Reuse existing ammo"


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024.0 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{value} B"


class WeaponWorkbenchFrame(ttk.Frame):
    """Review and safely author weapon metadata inside copied workspaces."""

    def __init__(self, parent: tk.Misc, *, on_open_asset=None, on_help=None) -> None:
        super().__init__(parent)
        self._on_open_asset = on_open_asset
        self._on_help = on_help
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.weapons: dict[str, WeaponRecord] = {}
        self.selected_weapon: WeaponRecord | None = None
        self._assets: dict[str, PackageEntry] = {}
        self.authoring_workspace: WeaponAuthoringWorkspace | None = None
        self.authoring_values: dict[str, tk.StringVar] = {}
        self.authoring_inputs: dict[str, ttk.Entry] = {}
        self.component_values: dict[str, tk.StringVar] = {}
        self.component_inputs: dict[str, ttk.Entry] = {}
        self.shop_authoring_values: dict[str, tk.StringVar] = {}
        self.shop_authoring_inputs: dict[str, ttk.Entry] = {}
        self.weapon_clone_values: dict[str, tk.StringVar] = {
            key: tk.StringVar() for _label, key in WEAPON_CLONE_FIELDS
        }
        self.new_weapon_values = self.weapon_clone_values
        self.weapon_clone_donor = tk.StringVar()
        self.weapon_clone_ammo_mode = tk.StringVar(value=AMMO_MODE_CLONE)
        self.weapon_clone_ammo = tk.StringVar()
        self.weapon_clone_ammo_label = tk.StringVar(
            value="New ammo identity/reference"
        )
        self.weapon_clone_summary = tk.StringVar(
            value="Review a deterministic plan before creating any records."
        )
        self.weapon_clone_digest = tk.StringVar(value="Plan digest: —")
        self.weapon_clone_status = tk.StringVar(
            value="Create an authoring workspace to use this guarded builder."
        )
        self._weapon_clone_plan = None
        self._weapon_clone_plan_signature: tuple[object, ...] | None = None
        self._weapon_clone_plan_digest = ""
        self._suspend_weapon_clone_trace = False
        self.animation_source = tk.StringVar()
        self.animation_template = tk.StringVar()
        self.animation_summary = tk.StringVar(
            value="Select a weapon to inspect its animation coverage."
        )
        self.animation_status = tk.StringVar(
            value="Animation clip payloads and weapon identity stay locked."
        )
        self.shop_source = tk.StringVar()
        self.shop_summary = tk.StringVar(
            value="Select a weapon to inspect its existing store listing."
        )
        self.shop_status = tk.StringVar(
            value="Only fields already present in the copied package can be edited."
        )
        self._component_items: dict[
            str, tuple[WeaponComponentLink, WeaponComponentRecord | None]
        ] = {}
        self._selected_component_item: str | None = None
        self._loaded_shop_source = ""
        self._loaded_editor_snapshot: tuple[object, ...] | None = None
        self._restoring_weapon_selection = False
        self._restoring_component_selection = False
        self._preserving_dirty_catalog = False
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
        for variable in (
            *self.weapon_clone_values.values(), self.weapon_clone_donor,
            self.weapon_clone_ammo_mode, self.weapon_clone_ammo,
        ):
            variable.trace_add("write", self._weapon_clone_input_changed)

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
        self.author_button = ttk.Button(
            toolbar, text="Create authoring workspace…", state="disabled",
            command=self._create_authoring_workspace,
        )
        self.author_button.pack(side="left", padx=(10, 0))
        self.asset_button = ttk.Button(
            toolbar, text="Open selected asset", state="disabled",
            command=self._open_selected_asset,
        )
        self.asset_button.pack(side="left", padx=(6, 0))
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
        author_page = ttk.Frame(self.project_tabs, padding=8)
        clone_page = ttk.Frame(self.project_tabs, padding=8)
        component_page = ttk.Frame(self.project_tabs, padding=8)
        component_author_page = ttk.Frame(self.project_tabs, padding=8)
        integration_page = ttk.Frame(self.project_tabs, padding=8)
        enhancement_page = ttk.Frame(self.project_tabs, padding=8)
        asset_page = ttk.Frame(self.project_tabs, padding=8)
        self.project_tabs.add(definition_page, text="Definition + ammo")
        self.project_tabs.add(author_page, text="Author")
        self.project_tabs.add(clone_page, text="New from template")
        self.project_tabs.add(component_page, text="Attachments")
        self.project_tabs.add(component_author_page, text="Component author")
        self.project_tabs.add(integration_page, text="Integration")
        self.project_tabs.add(enhancement_page, text="Script enhancements")
        self.project_tabs.add(asset_page, text="Assets")
        self.enhancement_page = enhancement_page

        self.enhancement_summary = tk.StringVar(
            value="No script-driven vanilla weapon enhancement was declared."
        )
        ttk.Label(
            enhancement_page, textvariable=self.enhancement_summary,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", pady=(0, 6))
        enhancement_tabs = ttk.Notebook(enhancement_page)
        enhancement_tabs.pack(fill="both", expand=True)
        relationship_page = ttk.Frame(enhancement_tabs, padding=5)
        runtime_page = ttk.Frame(enhancement_tabs, padding=5)
        visual_page = ttk.Frame(enhancement_tabs, padding=5)
        enhancement_tabs.add(relationship_page, text="Vanilla links")
        enhancement_tabs.add(runtime_page, text="Runtime")
        enhancement_tabs.add(visual_page, text="Visual assets")
        self.enhancement_tree = ttk.Treeview(
            relationship_page,
            columns=("weapon_hash", "component", "component_hash"),
            show="tree headings",
        )
        self.enhancement_tree.heading("#0", text="Vanilla weapon")
        self.enhancement_tree.heading("weapon_hash", text="Weapon hash")
        self.enhancement_tree.heading("component", text="Vanilla component")
        self.enhancement_tree.heading("component_hash", text="Component hash")
        self.enhancement_tree.column("#0", width=180)
        self.enhancement_tree.column("weapon_hash", width=95, stretch=False)
        self.enhancement_tree.column("component", width=210)
        self.enhancement_tree.column("component_hash", width=105, stretch=False)
        enhancement_scroll = ttk.Scrollbar(
            relationship_page, orient="vertical",
            command=self.enhancement_tree.yview,
        )
        self.enhancement_tree.configure(yscrollcommand=enhancement_scroll.set)
        self.enhancement_tree.pack(side="left", fill="both", expand=True)
        enhancement_scroll.pack(side="right", fill="y")
        self.enhancement_runtime_tree = ttk.Treeview(
            runtime_page, columns=("kind", "value"), show="tree headings",
        )
        self.enhancement_runtime_tree.heading("#0", text="System")
        self.enhancement_runtime_tree.heading("kind", text="Relationship")
        self.enhancement_runtime_tree.heading("value", text="Declared value")
        self.enhancement_runtime_tree.column("#0", width=175)
        self.enhancement_runtime_tree.column("kind", width=105, stretch=False)
        self.enhancement_runtime_tree.column("value", width=330)
        self.enhancement_runtime_tree.pack(fill="both", expand=True)
        self.enhancement_visual_tree = ttk.Treeview(
            visual_page, columns=("role", "count", "archive"),
            show="tree headings",
        )
        self.enhancement_visual_tree.heading("#0", text="Asset")
        self.enhancement_visual_tree.heading("role", text="Role")
        self.enhancement_visual_tree.heading("count", text="Count")
        self.enhancement_visual_tree.heading("archive", text="Nested archive")
        self.enhancement_visual_tree.column("#0", width=220)
        self.enhancement_visual_tree.column("role", width=95, stretch=False)
        self.enhancement_visual_tree.column("count", width=62, stretch=False)
        self.enhancement_visual_tree.column("archive", width=300)
        self.enhancement_visual_tree.pack(fill="both", expand=True)

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

        self.authoring_name = tk.StringVar(value="No weapon selected")
        ttk.Label(
            author_page, textvariable=self.authoring_name,
            style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            author_page,
            text=(
                "Names stay locked in this milestone. Apply changes to the copied "
                "workspace, then revalidate every visible weapon relationship."
            ),
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w", pady=(2, 7))
        author_grid = ttk.Frame(author_page)
        author_grid.pack(fill="x")
        for index, (label, key) in enumerate(WEAPON_AUTHOR_FIELDS):
            group = 0 if index < 5 else 1
            row = index if group == 0 else index - 5
            column = group * 2
            ttk.Label(author_grid, text=label).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2,
            )
            variable = tk.StringVar()
            entry = ttk.Entry(
                author_grid, textvariable=variable, width=18, state="disabled",
            )
            entry.grid(
                row=row, column=column + 1, sticky="ew",
                padx=(0 if group else 8, 0), pady=2,
            )
            self.authoring_values[key] = variable
            self.authoring_inputs[key] = entry
        author_grid.columnconfigure(1, weight=1)
        author_grid.columnconfigure(3, weight=1)
        author_actions = ttk.Frame(author_page)
        author_actions.pack(fill="x", pady=(8, 3))
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
            value="Create an authoring workspace before editing weapon metadata."
        )
        ttk.Label(
            author_page, textvariable=self.authoring_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w", pady=(4, 0))

        clone_inputs = ttk.LabelFrame(
            clone_page, text="Template and identity", padding=7,
        )
        clone_inputs.pack(fill="x")
        ttk.Label(clone_inputs, text="Donor weapon").grid(
            row=0, column=0, sticky="w", padx=(0, 5), pady=2,
        )
        self.weapon_clone_donor_combo = ttk.Combobox(
            clone_inputs, textvariable=self.weapon_clone_donor,
            state="disabled", width=25,
        )
        self.weapon_clone_donor_combo.grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=2,
        )
        self.weapon_clone_donor_combo.bind(
            "<<ComboboxSelected>>", self._weapon_clone_donor_selected,
        )
        clone_fields = ttk.Frame(clone_inputs)
        clone_fields.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0),
        )
        self.weapon_clone_inputs: dict[str, ttk.Entry] = {}
        for index, (label, key) in enumerate(WEAPON_CLONE_FIELDS):
            group = 0 if index < 3 else 1
            row = index if group == 0 else index - 3
            column = group * 2
            ttk.Label(clone_fields, text=label).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2,
            )
            entry = ttk.Entry(
                clone_fields, textvariable=self.weapon_clone_values[key],
                width=18, state="disabled",
            )
            entry.grid(
                row=row, column=column + 1, sticky="ew",
                padx=(0 if group else 8, 0), pady=2,
            )
            self.weapon_clone_inputs[key] = entry
        clone_fields.columnconfigure(1, weight=1)
        clone_fields.columnconfigure(3, weight=1)
        ttk.Label(clone_inputs, text="Ammo mode").grid(
            row=2, column=0, sticky="w", padx=(0, 5), pady=(6, 2),
        )
        self.weapon_clone_ammo_mode_combo = ttk.Combobox(
            clone_inputs, textvariable=self.weapon_clone_ammo_mode,
            values=(AMMO_MODE_CLONE, AMMO_MODE_REUSE),
            state="disabled", width=22,
        )
        self.weapon_clone_ammo_mode_combo.grid(
            row=2, column=1, sticky="ew", pady=(6, 2),
        )
        self.weapon_clone_ammo_mode_combo.bind(
            "<<ComboboxSelected>>", self._weapon_clone_mode_selected,
        )
        self.weapon_clone_ammo_label_widget = ttk.Label(
            clone_inputs, textvariable=self.weapon_clone_ammo_label,
        )
        self.weapon_clone_ammo_label_widget.grid(
            row=2, column=2, sticky="w", padx=(8, 5), pady=(6, 2),
        )
        self.weapon_clone_ammo_entry = ttk.Entry(
            clone_inputs, textvariable=self.weapon_clone_ammo,
            width=18, state="disabled",
        )
        self.weapon_clone_ammo_entry.grid(
            row=2, column=3, sticky="ew", pady=(6, 2),
        )
        self.weapon_clone_ammo_help = tk.StringVar(
            value=(
                "Clone mode creates a copied ammo record with this identity and "
                "links the new weapon to it."
            )
        )
        ttk.Label(
            clone_inputs, textvariable=self.weapon_clone_ammo_help,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="ew", pady=(3, 0))
        clone_inputs.columnconfigure(1, weight=1)
        clone_inputs.columnconfigure(3, weight=1)

        clone_review = ttk.LabelFrame(
            clone_page, text="Reviewed plan", padding=7,
        )
        clone_review.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(
            clone_review, textvariable=self.weapon_clone_summary,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x")
        preview_table = ttk.Frame(clone_review)
        preview_table.pack(fill="both", expand=True, pady=(5, 0))
        self.weapon_clone_preview_tree = ttk.Treeview(
            preview_table, columns=("state", "detail"), show="tree headings",
            height=4, selectmode="none",
        )
        self.weapon_clone_preview_tree.heading("#0", text="Check")
        self.weapon_clone_preview_tree.heading("state", text="State")
        self.weapon_clone_preview_tree.heading("detail", text="Preview")
        self.weapon_clone_preview_tree.column("#0", width=110, stretch=False)
        self.weapon_clone_preview_tree.column("state", width=82, stretch=False)
        self.weapon_clone_preview_tree.column("detail", width=330)
        clone_preview_scroll = ttk.Scrollbar(
            preview_table, orient="vertical",
            command=self.weapon_clone_preview_tree.yview,
        )
        self.weapon_clone_preview_tree.configure(
            yscrollcommand=clone_preview_scroll.set,
        )
        self.weapon_clone_preview_tree.grid(row=0, column=0, sticky="nsew")
        clone_preview_scroll.grid(row=0, column=1, sticky="ns")
        preview_table.rowconfigure(0, weight=1)
        preview_table.columnconfigure(0, weight=1)
        ttk.Label(
            clone_review, textvariable=self.weapon_clone_digest,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", pady=(4, 0))
        clone_actions = ttk.Frame(clone_review)
        clone_actions.pack(fill="x", pady=(7, 0))
        self.review_weapon_clone_button = ttk.Button(
            clone_actions, text="Review plan", state="disabled",
            command=self._review_weapon_clone_plan,
        )
        self.review_weapon_clone_button.pack(side="left")
        self.create_weapon_clone_button = ttk.Button(
            clone_actions, text="Create + validate", state="disabled",
            command=self._create_weapon_from_plan,
        )
        self.create_weapon_clone_button.pack(side="left", padx=(6, 0))
        ttk.Label(
            clone_review, textvariable=self.weapon_clone_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", pady=(5, 0))

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
        self.component_tree.bind("<<TreeviewSelect>>", self._select_component)

        self.component_author_name = tk.StringVar(value="No component selected")
        ttk.Label(
            component_author_page, textvariable=self.component_author_name,
            style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            component_author_page,
            text=(
                "Edit an existing attachment link and its package-owned component "
                "definition. New links are never invented."
            ),
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w", pady=(2, 7))

        attachment_group = ttk.LabelFrame(
            component_author_page, text="Attachment link", padding=6,
        )
        attachment_group.pack(fill="x")
        self.attachment_bone = tk.StringVar()
        self.attachment_default = tk.BooleanVar(value=False)
        ttk.Label(attachment_group, text="Attach bone (locked)").grid(
            row=0, column=0, sticky="w", padx=(0, 5), pady=2,
        )
        self.attachment_bone_entry = ttk.Entry(
            attachment_group, textvariable=self.attachment_bone,
            width=24, state="disabled",
        )
        self.attachment_bone_entry.grid(row=0, column=1, sticky="ew", pady=2)
        self.attachment_default_check = ttk.Checkbutton(
            attachment_group, text="Default component",
            variable=self.attachment_default, state="disabled",
        )
        self.attachment_default_check.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 4),
        )
        self.save_attachment_button = ttk.Button(
            attachment_group, text="Apply attachment + validate", state="disabled",
            command=self._save_attachment_fields,
        )
        self.save_attachment_button.grid(
            row=2, column=0, columnspan=2, sticky="w",
        )
        attachment_group.columnconfigure(1, weight=1)

        component_group = ttk.LabelFrame(
            component_author_page, text="Component definition", padding=6,
        )
        component_group.pack(fill="x", pady=(8, 0))
        for index, (label, key) in enumerate(COMPONENT_AUTHOR_FIELDS):
            group = 0 if index < 3 else 1
            row = index if group == 0 else index - 3
            column = group * 2
            visible_label = f"{label} (locked)" if key == "component.type" else label
            ttk.Label(component_group, text=visible_label).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2,
            )
            variable = tk.StringVar()
            entry = ttk.Entry(
                component_group, textvariable=variable, width=18,
                state="disabled",
            )
            entry.grid(
                row=row, column=column + 1, sticky="ew",
                padx=(0 if group else 8, 0), pady=2,
            )
            self.component_values[key] = variable
            self.component_inputs[key] = entry
        component_group.columnconfigure(1, weight=1)
        component_group.columnconfigure(3, weight=1)
        self.save_component_button = ttk.Button(
            component_group, text="Apply component + validate", state="disabled",
            command=self._save_component_fields,
        )
        self.save_component_button.grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0),
        )
        self.component_author_status = tk.StringVar(
            value="Select a package-owned component from Attachments."
        )
        ttk.Label(
            component_author_page, textvariable=self.component_author_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).pack(fill="x", anchor="w", pady=(5, 0))

        animation_group = ttk.LabelFrame(
            integration_page, text="Animation mapping", padding=7,
        )
        animation_group.pack(fill="x")
        ttk.Label(
            animation_group, textvariable=self.animation_summary,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        self.animation_source_label = ttk.Label(
            animation_group, text="Source record",
        )
        self.animation_source_combo = ttk.Combobox(
            animation_group, textvariable=self.animation_source,
            state="disabled", width=32,
        )
        self.animation_source_combo.bind(
            "<<ComboboxSelected>>", self._animation_source_selected,
        )
        ttk.Label(animation_group, text="Mapped template").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=2,
        )
        self.animation_template_combo = ttk.Combobox(
            animation_group, textvariable=self.animation_template,
            state="disabled", width=32,
        )
        self.animation_template_combo.grid(
            row=2, column=1, sticky="ew", pady=2,
        )
        self.animation_template_combo.bind(
            "<<ComboboxSelected>>", self._animation_template_selected,
        )
        self.clone_animation_button = ttk.Button(
            animation_group, text="Clone mappings + validate", state="disabled",
            command=self._clone_animation_mappings,
        )
        self.clone_animation_button.grid(
            row=2, column=2, sticky="e", padx=(7, 0), pady=2,
        )
        ttk.Label(
            animation_group, textvariable=self.animation_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        animation_group.columnconfigure(1, weight=1)

        shop_group = ttk.LabelFrame(
            integration_page, text="Store listing", padding=7,
        )
        shop_group.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(
            shop_group, textvariable=self.shop_summary,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 5))
        self.shop_source_label = ttk.Label(shop_group, text="Source record")
        self.shop_source_combo = ttk.Combobox(
            shop_group, textvariable=self.shop_source,
            state="disabled", width=32,
        )
        self.shop_source_combo.bind(
            "<<ComboboxSelected>>", self._shop_source_selected,
        )
        shop_fields = ttk.Frame(shop_group)
        shop_fields.grid(row=2, column=0, columnspan=4, sticky="ew")
        for index, (label, key) in enumerate(SHOP_AUTHOR_FIELDS):
            column_group = 0 if index < 4 else 1
            row = index if column_group == 0 else index - 4
            column = column_group * 2
            ttk.Label(shop_fields, text=label).grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2,
            )
            variable = tk.StringVar()
            entry = ttk.Entry(
                shop_fields, textvariable=variable, width=18, state="disabled",
            )
            entry.grid(
                row=row, column=column + 1, sticky="ew",
                padx=(0 if column_group else 8, 0), pady=2,
            )
            self.shop_authoring_values[key] = variable
            self.shop_authoring_inputs[key] = entry
        shop_fields.columnconfigure(1, weight=1)
        shop_fields.columnconfigure(3, weight=1)
        self.save_shop_button = ttk.Button(
            shop_group, text="Apply listing + validate", state="disabled",
            command=self._save_shop_fields,
        )
        self.save_shop_button.grid(row=3, column=0, sticky="w", pady=(7, 0))
        ttk.Label(
            shop_group, textvariable=self.shop_status,
            foreground="#52635c", wraplength=610, justify="left",
        ).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        shop_group.columnconfigure(1, weight=1)
        self._set_source_selector(
            self.animation_source_label, self.animation_source_combo,
            self.animation_source, (),
        )
        self._set_source_selector(
            self.shop_source_label, self.shop_source_combo,
            self.shop_source, (),
        )

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

    def open_source(
        self, source: str | Path, scan: PackageScan, *,
        authoring_workspace: WeaponAuthoringWorkspace | None = None,
    ) -> None:
        selected_name = self.selected_weapon.name if self.selected_weapon else None
        self.source = Path(source).expanduser().resolve()
        self.scan = scan
        self.authoring_workspace = authoring_workspace
        self.selected_weapon = None
        self._selected_component_item = None
        self._loaded_editor_snapshot = None
        self._refresh_catalog()
        self._populate_script_enhancements(scan)
        self.author_button.configure(
            state=(
                "disabled"
                if authoring_workspace is not None
                or not scan.weapons
                or scan.source_kind == "rpf"
                else "normal"
            ),
            text=(
                "Authoring workspace active" if authoring_workspace is not None
                else "Extract RPF before authoring" if scan.source_kind == "rpf"
                else "Create authoring workspace…"
            ),
        )
        self.status.set(
            f"{len(scan.weapons)} custom weapons · "
            f"{len(scan.weapon_enhancements) + len(scan.scripted_weapon_systems)} "
            f"script enhancements · {len(scan.weapon_components)} component "
            f"definitions · {scan.warning_count} package warnings"
        )
        if self.weapon_tree.get_children():
            selected = next((
                item_id for item_id, weapon in self.weapons.items()
                if selected_name is not None
                and weapon.name.casefold() == selected_name.casefold()
            ), self.weapon_tree.get_children()[0])
            self.weapon_tree.selection_set(selected)
            self.weapon_tree.focus(selected)
            self._select_weapon()
        elif scan.weapon_enhancements or scan.scripted_weapon_systems:
            system = (
                scan.weapon_enhancements[0].name
                if scan.weapon_enhancements else scan.scripted_weapon_systems[0].name
            )
            self.heading.set(system)
            self.summary.set(
                "Script-driven vanilla weapon/component enhancement; no custom "
                "weapons.meta record is required."
            )
            self.project_tabs.select(self.enhancement_page)
        else:
            self._clear_project(
                "No weapons.meta records were discovered in this package."
            )

    def _populate_script_enhancements(self, scan: PackageScan) -> None:
        for tree in (
            self.enhancement_tree, self.enhancement_runtime_tree,
            self.enhancement_visual_tree,
        ):
            tree.delete(*tree.get_children())
        relationship_count = 0
        for enhancement in scan.weapon_enhancements:
            for link in enhancement.weapon_components:
                relationship_count += 1
                self.enhancement_tree.insert(
                    "", "end", text=link.weapon_name,
                    values=(
                        link.weapon_hash, link.component_name, link.component_hash,
                    ),
                )
            for entry_point in enhancement.script_entry_points:
                self.enhancement_runtime_tree.insert(
                    "", "end", text=enhancement.name,
                    values=("Entry point", entry_point),
                )
            for visual in enhancement.visual_assets:
                self.enhancement_visual_tree.insert(
                    "", "end", text=visual.texture_dictionary,
                    values=("Texture tiers", visual.levels, visual.archive),
                )
                self.enhancement_visual_tree.insert(
                    "", "end", text=visual.archetype_dictionary,
                    values=(
                        "Archetypes", len(visual.families) * visual.levels,
                        visual.archive,
                    ),
                )
        for system in scan.scripted_weapon_systems:
            for entry_point in system.script_entry_points:
                self.enhancement_runtime_tree.insert(
                    "", "end", text=system.name,
                    values=("Entry point", entry_point),
                )
            for capability in system.capabilities:
                self.enhancement_runtime_tree.insert(
                    "", "end", text=system.name,
                    values=("Capability", capability),
                )
        for report in scan.material_progressions:
            self.enhancement_visual_tree.insert(
                "", "end", text=report.texture_dictionary,
                values=("Decoded textures", report.texture_count, report.archive_path),
            )
            self.enhancement_visual_tree.insert(
                "", "end", text=report.archetype_dictionary,
                values=("Decoded archetypes", report.archetype_count, report.archive_path),
            )
        if scan.scripted_weapon_systems or scan.weapon_enhancements:
            self.enhancement_summary.set(
                f"{len(scan.scripted_weapon_systems)} runtime weapon system(s) · "
                f"{relationship_count} exact vanilla weapon/component link(s) · "
                f"{len(scan.material_progressions)} material progression audit(s)."
            )
        else:
            self.enhancement_summary.set(
                "No script-driven vanilla weapon enhancement was declared."
            )

    def select_weapon(self, name: str) -> bool:
        for item_id, weapon in self.weapons.items():
            if weapon.name.casefold() == name.casefold():
                self.weapon_tree.selection_set(item_id)
                self.weapon_tree.focus(item_id)
                self.weapon_tree.see(item_id)
                return self._select_weapon()
        return False

    def _refresh_catalog(self) -> None:
        if not hasattr(self, "weapon_tree"):
            return
        selected_name = self.selected_weapon.name if self.selected_weapon else None
        keep_dirty_selection = bool(
            selected_name
            and self.authoring_workspace is not None
            and self._loaded_editor_snapshot is not None
            and self._editor_snapshot() != self._loaded_editor_snapshot
        )
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
            if (
                query and query not in searchable
                and not (
                    keep_dirty_selection
                    and selected_name is not None
                    and weapon.name.casefold() == selected_name.casefold()
                )
            ):
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
            self._preserving_dirty_catalog = keep_dirty_selection
            self.weapon_tree.selection_set(restored)
            self.weapon_tree.focus(restored)
            if keep_dirty_selection:
                self.after_idle(self._release_dirty_catalog_guard)
            else:
                self._select_weapon()
        elif selected_name is not None:
            self._clear_project(
                f"No weapons match {self.search.get().strip()!r}."
                if query else "Select a weapon to inspect its project."
            )

    def _select_weapon(self, _event: object | None = None) -> bool:
        selection = self.weapon_tree.selection()
        weapon = self.weapons.get(selection[0]) if selection else None
        if weapon is None or self.scan is None:
            return False
        if (
            self._preserving_dirty_catalog
            and self.selected_weapon is not None
            and self.selected_weapon.name.casefold() == weapon.name.casefold()
        ):
            return True
        previous = self.selected_weapon
        if (
            previous is not None
            and previous.name.casefold() != weapon.name.casefold()
            and not self.confirm_navigation()
        ):
            previous_id = next((
                item_id for item_id, candidate in self.weapons.items()
                if candidate.name.casefold() == previous.name.casefold()
            ), None)
            if previous_id is not None and not self._restoring_weapon_selection:
                self._restoring_weapon_selection = True
                try:
                    self.weapon_tree.selection_set(previous_id)
                    self.weapon_tree.focus(previous_id)
                finally:
                    self._restoring_weapon_selection = False
            return False
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
        self._load_weapon_clone_builder(weapon)
        self._load_integration_fields(weapon)
        self._load_authoring_fields(weapon)
        return True

    def _release_dirty_catalog_guard(self) -> None:
        self._preserving_dirty_catalog = False

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
        self._component_items.clear()
        self._selected_component_item = None
        for index, link in enumerate(links):
            component = definitions.get(link.component_name.casefold())
            item_id = f"component:{index}"
            self._component_items[item_id] = (link, component)
            self.component_tree.insert(
                "", "end", iid=item_id, values=(
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
        elif self.component_tree.get_children():
            first = self.component_tree.get_children()[0]
            self.component_tree.selection_set(first)
            self.component_tree.focus(first)
            self._select_component()

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
        for entry in self.scan.workbench_entries:
            if entry.suffix not in {
                ".ydr", ".ydd", ".yft", ".ytd", ".ybn", ".meta", ".xml",
            }:
                continue
            stem = entry.stem.casefold()
            name = entry.name.casefold()
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

    def _weapon_clone_builder_signature(self) -> tuple[object, ...]:
        return (
            self.weapon_clone_donor.get(),
            tuple(
                (key, variable.get())
                for key, variable in self.weapon_clone_values.items()
            ),
            self.weapon_clone_ammo_mode.get(),
            self.weapon_clone_ammo.get(),
        )

    def _weapon_clone_input_changed(self, *_args: object) -> None:
        mode = self.weapon_clone_ammo_mode.get()
        if mode == AMMO_MODE_REUSE:
            self.weapon_clone_ammo_label.set("Existing ammo identity/reference")
            self.weapon_clone_ammo_help.set(
                "Reuse mode links the new weapon to this existing ammo record; "
                "no ammo definition is copied."
            )
        else:
            self.weapon_clone_ammo_label.set("New ammo identity/reference")
            self.weapon_clone_ammo_help.set(
                "Clone mode creates a copied ammo record with this identity and "
                "links the new weapon to it."
            )
        if self._suspend_weapon_clone_trace:
            return
        if (
            self._weapon_clone_plan is not None
            and self._weapon_clone_builder_signature()
            != self._weapon_clone_plan_signature
        ):
            self._invalidate_weapon_clone_plan(
                "Inputs changed. Review the plan again before creating records."
            )

    def _invalidate_weapon_clone_plan(self, message: str = "") -> None:
        self._weapon_clone_plan = None
        self._weapon_clone_plan_signature = None
        self._weapon_clone_plan_digest = ""
        self.create_weapon_clone_button.configure(state="disabled")
        self.weapon_clone_digest.set("Plan digest: —")
        self.weapon_clone_preview_tree.delete(
            *self.weapon_clone_preview_tree.get_children()
        )
        self.weapon_clone_summary.set(
            "Review a deterministic plan before creating any records."
        )
        if message:
            self.weapon_clone_status.set(message)

    def _load_weapon_clone_builder(self, weapon: WeaponRecord) -> None:
        workspace = self.authoring_workspace
        weapon_names = self._unique_strings(
            item.name for item in getattr(self.scan, "weapons", ())
        ) if self.scan is not None else ()
        self._suspend_weapon_clone_trace = True
        try:
            self.weapon_clone_donor_combo.configure(values=weapon_names)
            self.weapon_clone_donor.set(weapon.name)
            for variable in self.weapon_clone_values.values():
                variable.set("")
            self.weapon_clone_ammo_mode.set(AMMO_MODE_CLONE)
            self.weapon_clone_ammo.set("")
        finally:
            self._suspend_weapon_clone_trace = False
        self._weapon_clone_input_changed()
        self._invalidate_weapon_clone_plan()
        planner = getattr(workspace, "plan_weapon_clone", None) if workspace else None
        enabled = workspace is not None and callable(planner)
        self.weapon_clone_donor_combo.configure(
            state="readonly" if enabled and weapon_names else "disabled",
        )
        for entry in self.weapon_clone_inputs.values():
            entry.configure(state="normal" if enabled else "disabled")
        self.weapon_clone_ammo_mode_combo.configure(
            state="readonly" if enabled else "disabled",
        )
        self.weapon_clone_ammo_entry.configure(
            state="normal" if enabled else "disabled",
        )
        self.review_weapon_clone_button.configure(
            state="normal" if enabled else "disabled",
        )
        self.weapon_clone_status.set(
            (
                f"Revision {workspace.revision}. Fill the new identities, then "
                "review a collision-checked plan."
            )
            if enabled and workspace is not None else
            "Create an authoring workspace to use this guarded builder."
        )

    def _restore_weapon_clone_builder_state(
        self, state: tuple[object, ...], *, invalidate_message: str = "",
    ) -> None:
        donor, values, mode, ammo = state
        self._suspend_weapon_clone_trace = True
        try:
            self.weapon_clone_donor.set(str(donor))
            value_map = dict(values) if isinstance(values, tuple) else {}
            for key, variable in self.weapon_clone_values.items():
                variable.set(str(value_map.get(key, "")))
            self.weapon_clone_ammo_mode.set(str(mode))
            self.weapon_clone_ammo.set(str(ammo))
        finally:
            self._suspend_weapon_clone_trace = False
        self._weapon_clone_input_changed()
        self._invalidate_weapon_clone_plan(invalidate_message)

    def _weapon_clone_donor_selected(self, _event: object | None = None) -> None:
        if self.weapon_clone_ammo_mode.get() != AMMO_MODE_REUSE:
            return
        donor_name = self.weapon_clone_donor.get()
        donor = next((
            item for item in getattr(self.scan, "weapons", ())
            if item.name.casefold() == donor_name.casefold()
        ), None) if self.scan is not None else None
        if donor is not None:
            self.weapon_clone_ammo.set(donor.ammo_info)

    def _weapon_clone_mode_selected(self, _event: object | None = None) -> None:
        if self.weapon_clone_ammo_mode.get() == AMMO_MODE_REUSE:
            self._weapon_clone_donor_selected()
        else:
            donor_name = self.weapon_clone_donor.get()
            donor = next((
                item for item in getattr(self.scan, "weapons", ())
                if item.name.casefold() == donor_name.casefold()
            ), None) if self.scan is not None else None
            if donor is not None and (
                self.weapon_clone_ammo.get().casefold()
                == donor.ammo_info.casefold()
            ):
                self.weapon_clone_ammo.set("")
        self._weapon_clone_input_changed()

    @staticmethod
    def _plan_collection(data: dict, *names: str):
        for name in names:
            value = data.get(name)
            if value is not None:
                return value
        return ()

    @staticmethod
    def _plan_collection_summary(value) -> tuple[int, str]:
        if isinstance(value, dict):
            items = list(value)
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        elif value:
            items = [value]
        else:
            items = []
        labels: list[str] = []
        for item in items:
            if isinstance(item, dict):
                if item.get("reason") and item.get("field"):
                    label = (
                        f"{item.get('field')}={item.get('value', '')}: "
                        f"{item.get('reason')}"
                    )
                else:
                    label = next((
                        str(item.get(key, "")) for key in (
                            "message", "subject", "name", "kind", "field",
                            "source",
                        ) if item.get(key)
                    ), str(item))
            else:
                label = str(item)
            if label and label not in labels:
                labels.append(label)
        detail = ", ".join(labels[:4])
        if len(labels) > 4:
            detail += f" (+{len(labels) - 4} more)"
        return len(items), detail or "None"

    @staticmethod
    def _weapon_clone_plan_is_valid(plan, data: dict) -> bool:
        explicit = getattr(plan, "ready", data.get("ready"))
        if explicit is None:
            explicit = getattr(plan, "valid", data.get("valid"))
        if explicit is None:
            explicit = getattr(plan, "complete", data.get("complete"))
        collisions = WeaponWorkbenchFrame._plan_collection(
            data, "collisions", "conflicts",
        )
        errors = WeaponWorkbenchFrame._plan_collection(data, "errors")
        findings = WeaponWorkbenchFrame._plan_collection(data, "findings")
        has_error_finding = any(
            isinstance(item, dict)
            and str(item.get("severity", "")).casefold() == "error"
            for item in findings if isinstance(findings, (list, tuple, set))
        )
        return bool(
            (True if explicit is None else explicit)
            and not collisions and not errors and not has_error_finding
        )

    @staticmethod
    def _weapon_clone_plan_hash(plan, data: dict) -> str:
        digest = getattr(plan, "plan_sha256", "") or data.get("plan_sha256", "")
        if callable(digest):
            digest = digest()
        return str(digest or "")

    def _review_weapon_clone_plan(self) -> None:
        workspace = self.authoring_workspace
        planner = getattr(workspace, "plan_weapon_clone", None) if workspace else None
        if not callable(planner):
            return
        clone_ammo = self.weapon_clone_ammo_mode.get() == AMMO_MODE_CLONE
        ammo_identity = self.weapon_clone_ammo.get().strip()
        try:
            plan = planner(
                self.weapon_clone_donor.get().strip(),
                weapon_name=self.weapon_clone_values["weapon_name"].get().strip(),
                slot=self.weapon_clone_values["slot"].get().strip(),
                ammo_info=ammo_identity,
                model=self.weapon_clone_values["model"].get().strip(),
                human_name_hash=self.weapon_clone_values[
                    "human_name_hash"
                ].get().strip(),
                stat_name=self.weapon_clone_values["stat_name"].get().strip(),
                clone_ammo=clone_ammo,
                ammo_name=ammo_identity if clone_ammo else None,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._invalidate_weapon_clone_plan()
            self.weapon_clone_status.set(f"Plan rejected: {exc}")
            return
        data = plan.to_dict() if callable(getattr(plan, "to_dict", None)) else {}
        digest = self._weapon_clone_plan_hash(plan, data)
        valid = self._weapon_clone_plan_is_valid(plan, data)
        additions = self._plan_collection(
            data, "additions", "records", "operations", "records_to_add",
        )
        collisions = self._plan_collection(data, "collisions", "conflicts")
        completeness = dict(data.get("donor_completeness", {}) or {})
        donor_complete = bool(data.get("donor_complete", valid))
        completeness_text = ", ".join(
            f"{key.replace('_', ' ')}={value}"
            for key, value in list(completeness.items())[:4]
        ) or "No donor coverage reported"
        if len(completeness) > 4:
            completeness_text += f" (+{len(completeness) - 4} more)"
        findings = self._plan_collection(data, "findings")
        additions_count, additions_text = self._plan_collection_summary(additions)
        collisions_count, collisions_text = self._plan_collection_summary(collisions)
        findings_count, findings_text = self._plan_collection_summary(findings)
        self.weapon_clone_preview_tree.delete(
            *self.weapon_clone_preview_tree.get_children()
        )
        rows = (
            (
                "Donor coverage", "Complete" if donor_complete else "Incomplete",
                completeness_text,
            ),
            (
                "Additions", str(additions_count), additions_text,
            ),
            (
                "Collisions", str(collisions_count), collisions_text,
            ),
            (
                "Validation", str(findings_count), findings_text,
            ),
        )
        for index, (label, state, detail) in enumerate(rows):
            self.weapon_clone_preview_tree.insert(
                "", "end", iid=f"clone-plan:{index}", text=label,
                values=(state, detail),
            )
        self._weapon_clone_plan = plan
        self._weapon_clone_plan_signature = self._weapon_clone_builder_signature()
        self._weapon_clone_plan_digest = digest
        self.weapon_clone_summary.set(
            f"{'Complete' if valid else 'Blocked'} · {additions_count} planned "
            f"addition(s) · {collisions_count} collision(s)."
        )
        self.weapon_clone_digest.set(f"Plan digest: {digest or 'Unavailable'}")
        can_create = valid and bool(digest)
        self.create_weapon_clone_button.configure(
            state="normal" if can_create else "disabled",
        )
        self.weapon_clone_status.set(
            (
                f"Revision {workspace.revision}. Plan is unchanged and ready for "
                "explicit confirmation."
            )
            if can_create else
            "Plan is not complete. Resolve the previewed blockers and review again."
        )

    def _create_weapon_from_plan(self) -> None:
        workspace = self.authoring_workspace
        plan = self._weapon_clone_plan
        creator = getattr(workspace, "clone_weapon_bundle", None) if workspace else None
        if not callable(creator) or plan is None:
            return
        signature = self._weapon_clone_builder_signature()
        if signature != self._weapon_clone_plan_signature:
            self._invalidate_weapon_clone_plan(
                "Inputs changed. Review the plan again before creating records."
            )
            return
        weapon_name = self.weapon_clone_values["weapon_name"].get().strip()
        if not messagebox.askyesno(
            "Create weapon records?",
            f"Create and validate {weapon_name} from "
            f"{self.weapon_clone_donor.get()}?\n\n"
            f"Plan: {self._weapon_clone_plan_digest}",
            parent=self, icon="warning",
        ):
            self.weapon_clone_status.set("Weapon creation cancelled.")
            return
        try:
            result = creator(
                plan, expected_revision=workspace.revision,
                expected_plan_sha256=self._weapon_clone_plan_digest,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Weapon creation rejected", str(exc), parent=self,
            )
            self.weapon_clone_status.set(
                f"Creation rejected and rolled back: {exc}"
            )
            return
        self._reload_authoring_workspace(
            weapon_name, preserve_weapon_clone_draft=False,
        )
        revision = getattr(result, "revision", workspace.revision)
        self.status.set(f"Created and validated {weapon_name} · revision {revision}")

    @staticmethod
    def _unique_strings(values) -> tuple[str, ...]:
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            text = str(value or "").strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            unique.append(text)
        return tuple(unique)

    @staticmethod
    def _set_source_selector(
        label: ttk.Label, combo: ttk.Combobox, variable: tk.StringVar,
        sources, *, preferred: str = "",
    ) -> str:
        choices = WeaponWorkbenchFrame._unique_strings(sources)
        selected = next((
            item for item in choices if item.casefold() == preferred.casefold()
        ), choices[0] if choices else "")
        variable.set(selected)
        combo.configure(values=choices)
        if len(choices) > 1:
            label.grid(row=1, column=0, sticky="w", padx=(0, 6), pady=2)
            combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
            combo.configure(state="readonly")
        else:
            label.grid_remove()
            combo.grid_remove()
            combo.configure(state="disabled")
        return selected

    def _animation_records_for(self, weapon_name: str):
        if self.scan is None:
            return ()
        return tuple(
            item for item in getattr(self.scan, "weapon_animation_records", ())
            if str(getattr(item, "weapon_name", "")).casefold()
            == weapon_name.casefold()
        )

    def _shop_records_for(self, weapon_name: str):
        if self.scan is None:
            return ()
        return tuple(
            item for item in getattr(self.scan, "weapon_shop_records", ())
            if str(getattr(item, "weapon_name", "")).casefold()
            == weapon_name.casefold()
        )

    def _load_integration_fields(self, weapon: WeaponRecord) -> None:
        self._load_animation_fields(weapon)
        self._load_shop_authoring_fields(weapon)

    def _load_animation_fields(self, weapon: WeaponRecord) -> None:
        target_records = self._animation_records_for(weapon.name)
        template_names = self._unique_strings(
            getattr(item, "weapon_name", "")
            for item in getattr(self.scan, "weapon_animation_records", ())
            if str(getattr(item, "weapon_name", "")).casefold()
            != weapon.name.casefold()
        ) if self.scan is not None else ()
        preferred_template = self.animation_template.get()
        selected_template = "" if target_records else next((
            item for item in template_names
            if item.casefold() == preferred_template.casefold()
        ), template_names[0] if template_names else "")
        self.animation_template.set(selected_template)
        self.animation_template_combo.configure(values=template_names)
        template_records = self._animation_records_for(selected_template)
        source_records = target_records or template_records
        selected_source = self._set_source_selector(
            self.animation_source_label, self.animation_source_combo,
            self.animation_source,
            (getattr(item, "source", "") for item in source_records),
            preferred=self.animation_source.get(),
        )

        set_keys = {
            (
                str(getattr(item, "source", "")),
                int(getattr(item, "set_ordinal", 0)),
                str(getattr(item, "set_name", "")),
            )
            for item in target_records
        }
        target_sources = self._unique_strings(
            getattr(item, "source", "") for item in target_records
        )
        source_count = len(target_sources)
        revision_prefix = (
            f"Revision {self.authoring_workspace.revision}. "
            if self.authoring_workspace is not None else ""
        )
        if target_records:
            source_text = ", ".join(target_sources)
            self.animation_summary.set(
                f"{len(target_records)} mapping record(s) across "
                f"{len(set_keys) or 1} set(s) in {source_count or 1} source file(s). "
                f"Source: {source_text}."
            )
            self.animation_status.set(
                f"{revision_prefix}Mapping exists. Animation clip payloads and "
                "weapon identity are visible for review but remain locked."
            )
        else:
            self.animation_summary.set(
                "No animation mapping is registered for this weapon. Choose a "
                "mapped weapon as the reviewed template."
            )
            self.animation_status.set(
                f"{revision_prefix}Cloning copies exact mapping records; clip "
                "payloads and weapon identity remain locked."
            )

        can_clone = bool(
            self.authoring_workspace is not None
            and not target_records
            and selected_template
            and source_records
            and selected_source
            and callable(getattr(
                self.authoring_workspace, "clone_animation_mappings", None,
            ))
        )
        self.animation_template_combo.configure(
            state="readonly" if template_names and not target_records else "disabled",
        )
        self.clone_animation_button.configure(
            state="normal" if can_clone else "disabled",
        )

    def _animation_template_selected(self, _event: object | None = None) -> None:
        weapon = self.selected_weapon
        if weapon is not None:
            self._load_animation_fields(weapon)

    def _animation_source_selected(self, _event: object | None = None) -> None:
        weapon = self.selected_weapon
        if weapon is not None:
            self._load_animation_fields(weapon)

    def _clone_animation_mappings(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        template = self.animation_template.get().strip()
        if workspace is None or weapon is None or not template:
            return
        clone = getattr(workspace, "clone_animation_mappings", None)
        if not callable(clone):
            self.animation_status.set(
                "This SDK build does not expose guarded animation cloning."
            )
            return
        source = self.animation_source.get().strip() or None
        try:
            result = clone(
                weapon.name, template, source=source,
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Animation mapping rejected", str(exc), parent=self,
            )
            self.animation_status.set(f"Clone rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(weapon.name)
        revision = getattr(result, "revision", workspace.revision)
        self.status.set(
            f"Cloned animation mappings from {template} · revision {revision}"
        )

    def _load_shop_authoring_fields(
        self, weapon: WeaponRecord, *, preferred_source: str = "",
    ) -> None:
        records = self._shop_records_for(weapon.name)
        selected_source = self._set_source_selector(
            self.shop_source_label, self.shop_source_combo, self.shop_source,
            (getattr(item, "source", "") for item in records),
            preferred=preferred_source or self.shop_source.get(),
        )
        self._loaded_shop_source = selected_source
        for key, variable in self.shop_authoring_values.items():
            variable.set("")
            self.shop_authoring_inputs[key].configure(state="disabled")
        self.save_shop_button.configure(state="disabled")
        if not records:
            self.shop_summary.set("No store registration was discovered for this weapon.")
            self.shop_status.set(
                "New store records are not invented by this editor. Weapon identity "
                "remains locked."
            )
            return
        sources = self._unique_strings(getattr(item, "source", "") for item in records)
        self.shop_summary.set(
            f"Existing registration in {len(sources)} source file(s)."
        )
        workspace = self.authoring_workspace
        getter = getattr(workspace, "shop_values", None) if workspace else None
        if not callable(getter):
            self.shop_status.set(
                "Create an authoring workspace to edit existing listing fields. "
                "Weapon identity remains locked."
            )
            return
        try:
            current = getter(weapon.name, source=selected_source or None)
        except (OSError, RuntimeError, ValueError) as exc:
            self.shop_status.set(f"Store listing authoring unavailable: {exc}")
            return
        values = dict(getattr(current, "values", {}) or {})
        representations = dict(getattr(current, "representations", {}) or {})
        existing_fields = {
            key for key in values
            if representations.get(key, "missing") != "missing"
        }
        for key, variable in self.shop_authoring_values.items():
            variable.set(str(values.get(key, "")))
            self.shop_authoring_inputs[key].configure(
                state="normal" if key in existing_fields else "disabled",
            )
        editable_count = sum(
            key in existing_fields for _label, key in SHOP_AUTHOR_FIELDS
        )
        identity_field = str(getattr(current, "identity_field", "") or "identity")
        representation = str(
            getattr(current, "identity_representation", "") or "preserved"
        )
        self.shop_summary.set(
            f"{editable_count} editable field(s) in {selected_source}; "
            f"{identity_field} uses {representation} representation."
        )
        self.shop_status.set(
            f"Revision {workspace.revision}. Only existing listing fields are "
            "editable; registration identity stays locked."
        )
        self.save_shop_button.configure(
            state="normal" if editable_count else "disabled",
        )

    def _shop_source_selected(self, _event: object | None = None) -> None:
        weapon = self.selected_weapon
        if weapon is None:
            return
        selected = self.shop_source.get()
        if (
            self._loaded_editor_snapshot is not None
            and self._editor_snapshot() != self._loaded_editor_snapshot
        ):
            self.shop_source.set(self._loaded_shop_source)
            if not self.confirm_navigation():
                return
            self.shop_source.set(selected)
        self._load_shop_authoring_fields(weapon, preferred_source=selected)
        if self.authoring_workspace is not None:
            self._loaded_editor_snapshot = self._editor_snapshot()

    def _save_shop_fields(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        updater = getattr(workspace, "update_shop", None) if workspace else None
        getter = getattr(workspace, "shop_values", None) if workspace else None
        if weapon is None or not callable(updater) or not callable(getter):
            return
        source = self.shop_source.get().strip() or None
        try:
            current = getter(weapon.name, source=source)
            current_values = dict(getattr(current, "values", {}) or {})
            representations = dict(
                getattr(current, "representations", {}) or {}
            )
            updates = {
                key: variable.get()
                for key, variable in self.shop_authoring_values.items()
                if representations.get(key, "missing") != "missing"
                and variable.get() != str(current_values.get(key, ""))
            }
            if not updates:
                self.shop_status.set("No store listing fields changed.")
                return
            result = updater(
                weapon.name, updates, source=source,
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Store listing edit rejected", str(exc), parent=self)
            self.shop_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(weapon.name)
        revision = getattr(result, "revision", workspace.revision)
        self.status.set(
            f"Applied {len(updates)} store listing field(s) · revision {revision}"
        )

    def _create_authoring_workspace(self) -> None:
        if self.source is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for weapon authoring workspace",
        )
        if not parent:
            return
        destination = Path(parent) / f"{self.source.stem}-weapon-authoring"
        selected_name = self.selected_weapon.name if self.selected_weapon else None
        self.status.set("Copying weapon source into a safe authoring workspace…")
        self.update_idletasks()
        try:
            workspace = WeaponAuthoringWorkspace.create(self.source, destination)
            scan = AddonPackageInspector().inspect(workspace.source)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Weapon authoring workspace failed", str(exc), parent=self,
            )
            self.status.set("Weapon authoring workspace was not created.")
            return
        self.open_source(
            workspace.source, scan, authoring_workspace=workspace,
        )
        if selected_name:
            self.select_weapon(selected_name)
        self.status.set(f"Authoring workspace active: {workspace.root}")

    def _load_authoring_fields(self, weapon: WeaponRecord) -> None:
        workspace = self.authoring_workspace
        self.authoring_name.set(weapon.name)
        if workspace is None:
            for key, variable in self.authoring_values.items():
                variable.set("")
                self.authoring_inputs[key].configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.undo_author_button.configure(state="disabled")
            self.authoring_status.set(
                "Create an authoring workspace to edit this copied package safely."
            )
            self._disable_component_authoring(
                "Create an authoring workspace before editing attachments."
            )
            self._loaded_editor_snapshot = None
            return
        try:
            values = workspace.values(weapon.name)
        except (OSError, RuntimeError, ValueError) as exc:
            for entry in self.authoring_inputs.values():
                entry.configure(state="disabled")
            self.save_author_button.configure(state="disabled")
            self.authoring_status.set(f"Weapon authoring unavailable: {exc}")
            self._loaded_editor_snapshot = None
            return
        for key, variable in self.authoring_values.items():
            variable.set(values.values.get(key, ""))
            self.authoring_inputs[key].configure(
                state=(
                    "disabled"
                    if key.startswith("ammo.") and "ammo" not in values.sources
                    else "normal"
                ),
            )
        self.save_author_button.configure(state="normal")
        self.undo_author_button.configure(
            state="normal" if self._has_authoring_history() else "disabled",
        )
        affected = tuple(getattr(values, "affected_weapons", ()))
        shared = (
            f" Ammo changes also affect {len(affected) - 1} linked weapon(s)."
            if len(affected) > 1 else ""
        )
        self.authoring_status.set(
            f"Revision {workspace.revision}. Apply revalidates the copied package; "
            f"failed edits roll back.{shared}"
        )
        selection = self.component_tree.selection()
        if selection and selection[0] in self._component_items:
            self._load_component_authoring(selection[0])
        else:
            self._disable_component_authoring(
                "Select an existing attachment to edit its link or definition."
            )
        self._loaded_editor_snapshot = self._editor_snapshot()

    def _select_component(self, _event: object | None = None) -> None:
        selection = self.component_tree.selection()
        item_id = selection[0] if selection else None
        if item_id not in self._component_items:
            self._selected_component_item = None
            self._disable_component_authoring(
                "Select an existing attachment to edit its link or definition."
            )
            return
        discarded = (
            self._selected_component_item is not None
            and self._selected_component_item != item_id
            and self._loaded_editor_snapshot is not None
            and self._editor_snapshot() != self._loaded_editor_snapshot
        )
        if discarded and not self.confirm_navigation():
            if not self._restoring_component_selection:
                self._restoring_component_selection = True
                try:
                    self.component_tree.selection_set(self._selected_component_item)
                    self.component_tree.focus(self._selected_component_item)
                finally:
                    self._restoring_component_selection = False
            return
        if discarded:
            self._restore_weapon_authoring_values()
        self._selected_component_item = item_id
        self._load_component_authoring(item_id)
        self._loaded_editor_snapshot = self._editor_snapshot()

    def _restore_weapon_authoring_values(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        if workspace is None or weapon is None:
            return
        try:
            values = workspace.values(weapon.name)
        except (OSError, RuntimeError, ValueError):
            return
        for key, variable in self.authoring_values.items():
            variable.set(values.values.get(key, ""))

    def _restore_integration_authoring_values(self) -> None:
        weapon = self.selected_weapon
        if weapon is None:
            return
        self._load_shop_authoring_fields(
            weapon, preferred_source=self._loaded_shop_source,
        )

    def _load_component_authoring(self, item_id: str) -> None:
        link, component = self._component_items[item_id]
        component_name = str(getattr(link, "component_name", ""))
        self.component_author_name.set(component_name or "No component selected")
        self.attachment_bone.set(str(getattr(link, "attach_bone", "")))
        self.attachment_default.set(bool(getattr(link, "default", False)))
        workspace = self.authoring_workspace
        if workspace is None or self.selected_weapon is None:
            self._disable_component_controls()
            self.component_author_status.set(
                "Create an authoring workspace before editing attachments."
            )
            return
        # Moving an attachment bone can affect sibling link topology; milestone
        # one exposes the resolved value but keeps it locked.
        self.attachment_bone_entry.configure(state="disabled")
        self.attachment_default_check.configure(state="normal")
        self.save_attachment_button.configure(state="normal")
        if component is None:
            for key, variable in self.component_values.items():
                variable.set("")
                self.component_inputs[key].configure(state="disabled")
            self.save_component_button.configure(state="disabled")
            self.component_author_status.set(
                "The attachment link is editable, but its component definition is "
                "stock or external to this package."
            )
            return
        try:
            values = workspace.component_values(component_name)
        except (OSError, RuntimeError, ValueError) as exc:
            for entry in self.component_inputs.values():
                entry.configure(state="disabled")
            self.save_component_button.configure(state="disabled")
            self.component_author_status.set(
                f"Component-definition authoring unavailable: {exc}"
            )
            return
        for key, variable in self.component_values.items():
            variable.set(values.values.get(key, ""))
            self.component_inputs[key].configure(
                state="disabled" if key == "component.type" else "normal",
            )
        self.save_component_button.configure(state="normal")
        affected = tuple(getattr(values, "affected_weapons", ()))
        self.component_author_status.set(
            f"Package definition linked by {len(affected) or 1} weapon(s). "
            "Each apply is revision-checked and validated."
        )

    def _disable_component_controls(self) -> None:
        self.attachment_bone_entry.configure(state="disabled")
        self.attachment_default_check.configure(state="disabled")
        self.save_attachment_button.configure(state="disabled")
        for entry in self.component_inputs.values():
            entry.configure(state="disabled")
        self.save_component_button.configure(state="disabled")

    def _disable_component_authoring(self, message: str) -> None:
        self._selected_component_item = None
        self.component_author_name.set("No component selected")
        self.attachment_bone.set("")
        self.attachment_default.set(False)
        for variable in self.component_values.values():
            variable.set("")
        self._disable_component_controls()
        self.component_author_status.set(message)

    def _has_authoring_history(self) -> bool:
        workspace = self.authoring_workspace
        if workspace is None:
            return False
        history = Path(workspace.root) / "history"
        try:
            return any(
                path.is_dir()
                and (path / "edit.json").is_file()
                and not path.name.endswith((".undone", ".undo-recovery"))
                for path in history.iterdir()
            )
        except OSError:
            return False

    def _editor_snapshot(self) -> tuple[object, ...]:
        return (
            tuple(
                (key, variable.get())
                for key, variable in self.authoring_values.items()
            ),
            self._selected_component_item,
            self.attachment_bone.get(),
            self.attachment_default.get(),
            tuple(
                (key, variable.get())
                for key, variable in self.component_values.items()
            ),
            tuple(
                (key, variable.get())
                for key, variable in self.shop_authoring_values.items()
            ),
            self._weapon_clone_builder_signature(),
        )

    def confirm_navigation(self) -> bool:
        """Prevent package and item navigation from discarding unapplied edits."""
        if (
            self.authoring_workspace is None
            or self._loaded_editor_snapshot is None
            or self._editor_snapshot() == self._loaded_editor_snapshot
        ):
            return True
        discard = messagebox.askyesno(
            "Discard unsaved weapon edits?",
            "This weapon has changes that have not been applied.\n\n"
            "Choose No to return and apply them, or Yes to discard them.",
            parent=self, icon="warning",
        )
        if not discard:
            return False
        self._restore_weapon_authoring_values()
        self._restore_integration_authoring_values()
        if self.selected_weapon is not None:
            self._load_weapon_clone_builder(self.selected_weapon)
        item_id = self._selected_component_item
        if item_id is not None and item_id in self._component_items:
            self._load_component_authoring(item_id)
        self._loaded_editor_snapshot = self._editor_snapshot()
        return True

    @staticmethod
    def _shared_edit_confirmed(
        *, parent: tk.Misc, subject: str, affected: tuple[str, ...],
    ) -> bool:
        if len(affected) <= 1:
            return False
        return messagebox.askyesno(
            f"Edit shared {subject}?",
            f"This {subject} is used by {len(affected)} weapons:\n\n"
            f"{', '.join(affected)}\n\nApply the change to all of them?",
            parent=parent, icon="warning",
        )

    def _save_authoring_fields(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        if workspace is None or weapon is None:
            return
        try:
            current = workspace.values(weapon.name)
            updates = {
                key: variable.get()
                for key, variable in self.authoring_values.items()
                if variable.get() != current.values.get(key, "")
            }
            if not updates:
                self.authoring_status.set("No weapon or ammo fields changed.")
                return
            affected = tuple(getattr(current, "affected_weapons", ()))
            shared_ammo = any(key.startswith("ammo.") for key in updates)
            acknowledge_shared = False
            if shared_ammo and len(affected) > 1:
                acknowledge_shared = self._shared_edit_confirmed(
                    parent=self, subject="ammo definition", affected=affected,
                )
                if not acknowledge_shared:
                    self.authoring_status.set("Shared ammo edit cancelled.")
                    return
            result = workspace.update(
                weapon.name, updates, expected_revision=workspace.revision,
                acknowledge_shared=acknowledge_shared,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Weapon edit rejected", str(exc), parent=self)
            self.authoring_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(weapon.name)
        self.status.set(
            f"Applied {len(result.changes)} weapon fields · revision {result.revision}"
        )

    def _save_attachment_fields(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        item = self._component_items.get(self._selected_component_item or "")
        if workspace is None or weapon is None or item is None:
            return
        link, _component = item
        component_name = str(getattr(link, "component_name", ""))
        updates: dict[str, str] = {}
        default = bool(self.attachment_default.get())
        if default != bool(getattr(link, "default", False)):
            updates["attachment.default"] = "true" if default else "false"
        if not updates:
            self.component_author_status.set("No attachment fields changed.")
            return
        try:
            result = workspace.update_attachment(
                weapon.name, component_name, updates,
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Attachment edit rejected", str(exc), parent=self)
            self.component_author_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(weapon.name, component_name)
        self.status.set(f"Applied attachment link · revision {result.revision}")

    def _save_component_fields(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        item = self._component_items.get(self._selected_component_item or "")
        if workspace is None or weapon is None or item is None:
            return
        link, component = item
        if component is None:
            return
        component_name = str(getattr(link, "component_name", ""))
        try:
            current = workspace.component_values(component_name)
            updates = {
                key: variable.get()
                for key, variable in self.component_values.items()
                if key != "component.type"
                and variable.get() != current.values.get(key, "")
            }
            if not updates:
                self.component_author_status.set("No component fields changed.")
                return
            affected = tuple(getattr(current, "affected_weapons", ()))
            acknowledge_shared = False
            if len(affected) > 1:
                acknowledge_shared = self._shared_edit_confirmed(
                    parent=self, subject="component definition", affected=affected,
                )
                if not acknowledge_shared:
                    self.component_author_status.set("Shared component edit cancelled.")
                    return
            result = workspace.update_component(
                component_name, updates, expected_revision=workspace.revision,
                acknowledge_shared=acknowledge_shared,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Component edit rejected", str(exc), parent=self)
            self.component_author_status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_workspace(weapon.name, component_name)
        self.status.set(
            f"Applied {len(result.changes)} component fields · revision "
            f"{result.revision}"
        )

    def _undo_authoring_edit(self) -> None:
        workspace = self.authoring_workspace
        weapon = self.selected_weapon
        if workspace is None or weapon is None:
            return
        component_name = ""
        item = self._component_items.get(self._selected_component_item or "")
        if item is not None:
            component_name = str(getattr(item[0], "component_name", ""))
        try:
            result = workspace.undo(expected_revision=workspace.revision)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Weapon undo failed", str(exc), parent=self)
            self.authoring_status.set(f"Undo failed: {exc}")
            return
        self._reload_authoring_workspace(weapon.name, component_name)
        self.status.set(f"Restored latest weapon edit · revision {result.revision}")

    def _reload_authoring_workspace(
        self, weapon_name: str, component_name: str = "",
        *, preserve_weapon_clone_draft: bool = True,
    ) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            return
        clone_state = self._weapon_clone_builder_signature()
        loaded_clone_state = (
            self._loaded_editor_snapshot[-1]
            if self._loaded_editor_snapshot else None
        )
        preserve_clone = bool(
            preserve_weapon_clone_draft and clone_state != loaded_clone_state
        )
        scan = AddonPackageInspector().inspect(workspace.source)
        self.open_source(workspace.source, scan, authoring_workspace=workspace)
        self.select_weapon(weapon_name)
        if component_name:
            match = next((
                item_id for item_id, (link, _component) in self._component_items.items()
                if str(getattr(link, "component_name", "")).casefold()
                == component_name.casefold()
            ), None)
            if match is not None:
                self.component_tree.selection_set(match)
                self.component_tree.focus(match)
                self.component_tree.see(match)
                self._select_component()
        clean_snapshot = self._editor_snapshot()
        if preserve_clone:
            self._restore_weapon_clone_builder_state(
                clone_state,
                invalidate_message=(
                    "Workspace revision changed. Draft inputs were preserved; "
                    "review a new plan before creating records."
                ),
            )
            self._loaded_editor_snapshot = clean_snapshot
        else:
            self._loaded_editor_snapshot = clean_snapshot

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
        self.authoring_name.set("No weapon selected")
        for key, variable in self.authoring_values.items():
            variable.set("")
            self.authoring_inputs[key].configure(state="disabled")
        self.save_author_button.configure(state="disabled")
        self.undo_author_button.configure(state="disabled")
        self.authoring_status.set("Select a weapon before editing package metadata.")
        self._component_items.clear()
        self._disable_component_authoring(
            "Select a weapon with existing attachment links."
        )
        self._suspend_weapon_clone_trace = True
        try:
            self.weapon_clone_donor.set("")
            self.weapon_clone_donor_combo.configure(values=(), state="disabled")
            for key, variable in self.weapon_clone_values.items():
                variable.set("")
                self.weapon_clone_inputs[key].configure(state="disabled")
            self.weapon_clone_ammo_mode.set(AMMO_MODE_CLONE)
            self.weapon_clone_ammo.set("")
        finally:
            self._suspend_weapon_clone_trace = False
        self.weapon_clone_ammo_mode_combo.configure(state="disabled")
        self.weapon_clone_ammo_entry.configure(state="disabled")
        self.review_weapon_clone_button.configure(state="disabled")
        self._weapon_clone_input_changed()
        self._invalidate_weapon_clone_plan()
        self.weapon_clone_status.set(
            "Select a weapon inside an authoring workspace to use this builder."
        )
        self.animation_source.set("")
        self.animation_template.set("")
        self.animation_template_combo.configure(values=(), state="disabled")
        self.clone_animation_button.configure(state="disabled")
        self.animation_summary.set(
            "Select a weapon to inspect its animation coverage."
        )
        self.animation_status.set(
            "Animation clip payloads and weapon identity stay locked."
        )
        self.shop_source.set("")
        self._loaded_shop_source = ""
        for key, variable in self.shop_authoring_values.items():
            variable.set("")
            self.shop_authoring_inputs[key].configure(state="disabled")
        self.save_shop_button.configure(state="disabled")
        self.shop_summary.set(
            "Select a weapon to inspect its existing store listing."
        )
        self.shop_status.set(
            "Only fields already present in the copied package can be edited."
        )
        self._set_source_selector(
            self.animation_source_label, self.animation_source_combo,
            self.animation_source, (),
        )
        self._set_source_selector(
            self.shop_source_label, self.shop_source_combo,
            self.shop_source, (),
        )
        self._loaded_editor_snapshot = None

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("weapon-workbench")
