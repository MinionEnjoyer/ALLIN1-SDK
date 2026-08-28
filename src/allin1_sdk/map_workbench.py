"""Embedded map-project inspection and packaging workspace."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Mapping

from allin1_sdk.addon_importer import AddonPackageInspector, PackageScan
from allin1_sdk.map_contract import MapProject
from allin1_sdk.map_package import MapAddonPackageBuilder
from allin1_sdk.map_project import MapProjectResolver


MAP_ASSET_SUFFIXES = frozenset({
    ".ymap", ".ytyp", ".ybn", ".ydr", ".ydd", ".ytd", ".yft",
    ".ynv", ".ynd", ".ymf",
})
MAP_PRIMARY_SUFFIXES = frozenset({".ymap", ".ytyp", ".ybn", ".ynv", ".ynd", ".ymf"})
MAP_DESCRIPTOR_NAMES = frozenset({
    "allin1.map.json", "map-project.json", "map_project.json", "map.json",
    "maps.json",
})


def map_asset_entries(scan: PackageScan | None) -> tuple[object, ...]:
    """Return the bounded package entries that can participate in a map project."""

    if scan is None:
        return ()
    return tuple(
        entry for entry in scan.workbench_entries
        if entry.suffix.casefold() in MAP_ASSET_SUFFIXES
    )


def looks_like_map_project(source: str | Path, scan: PackageScan | None = None) -> bool:
    """Recognize an explicit map descriptor or a package with map-native assets."""

    path = Path(source)
    if path.is_file() and path.name.casefold() in MAP_DESCRIPTOR_NAMES:
        return True
    if scan is not None and any(
        entry.suffix.casefold() in MAP_PRIMARY_SUFFIXES
        for entry in scan.workbench_entries
    ):
        return True
    if path.is_dir():
        return any((path / name).is_file() for name in MAP_DESCRIPTOR_NAMES)
    return False


def _to_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        serialized = serializer()
        if isinstance(serialized, Mapping):
            return dict(serialized)
    return {}


def _items(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


class MapWorkbenchFrame(ttk.Frame):
    """Inspect map assets and author the descriptor that binds their runtime links."""

    def __init__(
        self,
        parent: tk.Misc,
        project_root: str | Path,
        *,
        installation_roots: tuple[Path, ...] = (),
        on_help=None,
        on_open_asset=None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = installation_roots
        self._on_help = on_help
        self._on_open_asset = on_open_asset
        self.source: Path | None = None
        self.descriptor: Path | None = None
        self.scan: PackageScan | None = None
        self.project: MapProject | None = None
        self.project_data: dict[str, Any] | None = None
        self.report: object | None = None
        self._source_ready = False
        self._source_inspection_error: str | None = None
        self._descriptor_ready = False
        self.dirty = False
        self.status = tk.StringVar(
            self,
            value=(
                "Open a map source or descriptor to review assets, levels, "
                "entrances, exits, and garages."
            ),
        )
        self.source_value = tk.StringVar(self, value="No map source selected")
        self.descriptor_value = tk.StringVar(self, value="No descriptor selected")
        self.output_value = tk.StringVar(self, value="No package built")
        self.edition_value = tk.StringVar(self, value="legacy")
        self._detail_payloads: dict[str, dict[str, Any]] = {}
        self._build()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(8, 5, 8, 7))
        outer.pack(fill="both", expand=True)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(toolbar, text="Map Workbench", style="DialogTitle.TLabel").pack(
            side="left",
        )
        ttk.Button(
            toolbar, text="Workbench help",
            command=lambda: self._on_help("map-workbench") if self._on_help else None,
        ).pack(side="right")
        self.save_button = ttk.Button(
            toolbar, text="Save descriptor", state="disabled", command=self.save_descriptor,
        )
        self.save_button.pack(side="right", padx=(0, 7))
        self.build_button = ttk.Button(
            toolbar, text="Build validated package…", state="disabled",
            command=self._build_package,
        )
        self.build_button.pack(side="right", padx=(0, 7))
        self.validate_button = ttk.Button(
            toolbar, text="Validate", state="disabled", command=self.validate,
        )
        self.validate_button.pack(side="right", padx=(0, 7))
        open_menu = tk.Menu(toolbar, tearoff=False)
        open_menu.add_command(label="Create map project…", command=self._create_template)
        open_menu.add_separator()
        open_menu.add_command(label="Open map source…", command=self._choose_source)
        open_menu.add_command(
            label="Open project descriptor…", command=self._choose_descriptor,
        )
        ttk.Menubutton(
            toolbar, text="Open map", style="Accent.TMenubutton", menu=open_menu,
        ).pack(side="right", padx=(0, 7))

        source_strip = ttk.Frame(outer, style="Surface.TFrame", padding=(8, 5))
        source_strip.pack(fill="x", pady=(0, 5))
        ttk.Label(source_strip, text="Source", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky="w",
        )
        ttk.Label(source_strip, textvariable=self.source_value).grid(
            row=0, column=1, sticky="ew", padx=(8, 18),
        )
        ttk.Label(source_strip, text="Descriptor", style="FieldLabel.TLabel").grid(
            row=1, column=0, sticky="w",
        )
        ttk.Label(source_strip, textvariable=self.descriptor_value).grid(
            row=1, column=1, sticky="ew", padx=(8, 18),
        )
        ttk.Label(source_strip, text="Output", style="FieldLabel.TLabel").grid(
            row=0, column=2, sticky="w",
        )
        ttk.Label(source_strip, textvariable=self.output_value).grid(
            row=0, column=3, rowspan=2, sticky="ew", padx=(8, 0),
        )
        ttk.Label(source_strip, text="Build edition", style="FieldLabel.TLabel").grid(
            row=2, column=2, sticky="w", pady=(3, 0),
        )
        self.edition_box = ttk.Combobox(
            source_strip, textvariable=self.edition_value,
            values=("legacy", "enhanced"), state="readonly", width=12,
        )
        self.edition_box.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(3, 0))
        source_strip.columnconfigure(1, weight=3)
        source_strip.columnconfigure(3, weight=2)

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        asset_panel = ttk.LabelFrame(panes, text="Map assets", padding=7)
        topology_panel = ttk.LabelFrame(panes, text="Project topology", padding=7)
        detail_panel = ttk.LabelFrame(panes, text="Selection and validation", padding=7)
        panes.add(asset_panel, weight=2)
        panes.add(topology_panel, weight=3)
        panes.add(detail_panel, weight=4)

        self.asset_tree = ttk.Treeview(
            asset_panel, columns=("type", "size"), show="tree headings",
            selectmode="browse",
        )
        self.asset_tree.heading("#0", text="Asset")
        self.asset_tree.heading("type", text="Type")
        self.asset_tree.heading("size", text="Size")
        self.asset_tree.column("#0", width=210, minwidth=120, stretch=True)
        self.asset_tree.column("type", width=58, minwidth=48, stretch=False)
        self.asset_tree.column("size", width=76, minwidth=62, stretch=False, anchor="e")
        asset_scroll = ttk.Scrollbar(
            asset_panel, orient="vertical", command=self.asset_tree.yview,
        )
        self.asset_tree.configure(yscrollcommand=asset_scroll.set)
        self.asset_tree.pack(side="left", fill="both", expand=True)
        asset_scroll.pack(side="right", fill="y")
        self.asset_tree.bind("<<TreeviewSelect>>", self._select_asset)
        self.asset_tree.bind("<Double-1>", self._open_selected_asset)

        self.topology_tree = ttk.Treeview(
            topology_panel, columns=("kind", "link"), show="tree headings",
            selectmode="browse",
        )
        self.topology_tree.heading("#0", text="Name")
        self.topology_tree.heading("kind", text="Kind")
        self.topology_tree.heading("link", text="Connects / contains")
        self.topology_tree.column("#0", width=180, minwidth=110, stretch=True)
        self.topology_tree.column("kind", width=75, minwidth=62, stretch=False)
        self.topology_tree.column("link", width=175, minwidth=100, stretch=True)
        topology_scroll = ttk.Scrollbar(
            topology_panel, orient="vertical", command=self.topology_tree.yview,
        )
        self.topology_tree.configure(yscrollcommand=topology_scroll.set)
        topology_actions = ttk.Frame(topology_panel)
        topology_actions.pack(fill="x", pady=(0, 5))
        add_menu = tk.Menu(topology_actions, tearoff=False)
        add_menu.add_command(label="Level", command=lambda: self._add_record("levels"))
        add_menu.add_command(label="Entrance / exit", command=lambda: self._add_record("portals"))
        add_menu.add_command(label="Garage", command=lambda: self._add_record("garages"))
        add_menu.add_command(label="Garage slot", command=self._add_slot)
        ttk.Menubutton(topology_actions, text="Add…", menu=add_menu).pack(side="left")
        self.remove_button = ttk.Button(
            topology_actions, text="Remove", state="disabled", command=self._remove_selected,
        )
        self.remove_button.pack(side="left", padx=(6, 0))
        topology_tree_host = ttk.Frame(topology_panel)
        topology_tree_host.pack(fill="both", expand=True)
        self.topology_tree.pack(in_=topology_tree_host, side="left", fill="both", expand=True)
        topology_scroll.pack(in_=topology_tree_host, side="right", fill="y")
        self.topology_tree.bind("<<TreeviewSelect>>", self._select_topology)

        detail_controls = ttk.Frame(detail_panel)
        detail_controls.pack(fill="x", pady=(0, 5))
        self.open_asset_button = ttk.Button(
            detail_controls, text="Open selected asset", state="disabled",
            command=self._open_selected_asset,
        )
        self.open_asset_button.pack(side="left")
        self.apply_json_button = ttk.Button(
            detail_controls, text="Apply JSON", state="disabled",
            command=self._apply_selected_json,
        )
        self.apply_json_button.pack(side="right")
        self.revert_json_button = ttk.Button(
            detail_controls, text="Revert", state="disabled",
            command=self._revert_selected_json,
        )
        self.revert_json_button.pack(side="right", padx=(0, 6))
        self.detail = tk.Text(
            detail_panel, wrap="word", relief="flat", borderwidth=0,
            padx=8, pady=8, state="disabled",
        )
        detail_scroll = ttk.Scrollbar(
            detail_panel, orient="vertical", command=self.detail.yview,
        )
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")

        ttk.Label(
            outer, textvariable=self.status, style="StatusHint.TLabel",
            anchor="w", justify="left",
        ).pack(fill="x", pady=(5, 0))

    def _choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Select a map RPF, package, or project descriptor",
            filetypes=(
                ("Map projects and packages", "*.json *.rpf *.zip *.oiv *.rar *.7z"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            selected = filedialog.askdirectory(
                parent=self, title="Or select an extracted map package folder",
            )
        if selected:
            self.open_source(selected)

    def _choose_descriptor(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select an ALLIN1 map project descriptor",
            filetypes=(("Map project JSON", "*.json"), ("All files", "*.*")),
        )
        if selected:
            self.open_descriptor(selected)

    def _create_template(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self, title="Create an ALLIN1 map project",
            defaultextension=".json", initialfile="allin1.map.json",
            filetypes=(("Map project JSON", "*.json"),),
        )
        if not destination:
            return
        path = Path(destination).expanduser().resolve()
        if path.exists() or path.is_symlink():
            self.status.set(f"Choose a new descriptor path · already exists: {path}")
            return
        template = {
            "schema_version": 1,
            "id": "custom.map",
            "package_id": "custom.map",
            "name": "Custom Map",
            "version": "1.0.0",
            "editions": ["legacy", "enhanced"],
            "streaming": {
                "pack_name": "custom_map", "content_group": None,
                "ipls": ["custom_map"], "activation_radius": 300.0,
                "release_radius": 500.0, "keep_resident": False,
            },
            "levels": [{
                "id": "interior", "name": "Custom Interior",
                "center": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0},
                "ipls": [],
            }],
            "portals": [{
                "id": "main.entrance", "name": "Main Entrance", "mode": "both",
                "from": {
                    "level": "world",
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0},
                },
                "to": {
                    "level": "interior",
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 180.0},
                },
                "radius": 3.0, "one_way": False,
            }],
            "garages": [{
                "id": "main.garage", "name": "Main Garage",
                "level_id": "interior", "entrance_portal_id": "main.entrance",
                "capacity": 10, "vehicle_types": ["land"],
                "slots": [{
                    "id": "slot.01",
                    "position": {"x": 0.0, "y": 5.0, "z": 0.0, "heading": 180.0},
                    "vehicle_types": ["land"],
                }],
                "rules": {
                    "allow_store": True, "allow_retrieve": True,
                    "save_policy": "story_save_only",
                },
            }],
        }
        try:
            project = MapProject.from_dict(template)
            project.write(path)
        except (OSError, TypeError, ValueError) as exc:
            self.status.set(f"Could not create map project · {exc}")
            return
        self.open_descriptor(path, source=self.source or path.parent)

    def _game_path(self) -> Path | None:
        return next((path for path in self.installation_roots if path.is_dir()), None)

    def _clear_project_context(self) -> None:
        """Drop descriptor state before binding a newly inspected asset source."""

        self.descriptor = None
        self.project = None
        self.project_data = None
        self.report = None
        self._source_ready = False
        self._source_inspection_error = None
        self._descriptor_ready = False
        self.dirty = False
        self.descriptor_value.set("No descriptor selected")
        self.output_value.set("No package built")
        self.edition_box.configure(values=("legacy", "enhanced"))
        self.edition_value.set("legacy")
        self.validate_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.build_button.configure(state="disabled")
        self._populate_topology({})

    def _refresh_action_states(self) -> None:
        """Keep package publication fail-closed until both inputs are ready."""

        descriptor_available = (
            self.descriptor is not None
            and self.project_data is not None
            and self._descriptor_ready
        )
        self.validate_button.configure(
            state="normal" if self.project_data is not None else "disabled",
        )
        self.save_button.configure(
            state="normal" if descriptor_available else "disabled",
        )
        self.build_button.configure(
            state=(
                "normal"
                if descriptor_available and self.source is not None
                and self._source_ready
                else "disabled"
            ),
        )

    def _record_source_inspection(
        self, report: object | None, *, error: Exception | None = None,
    ) -> None:
        self.report = report
        self._source_inspection_error = str(error) if error is not None else None
        self._source_ready = (
            error is None
            and report is not None
            and bool(getattr(report, "valid", False))
        )
        self._refresh_action_states()

    @staticmethod
    def _source_failure_summary(report: object | None) -> str:
        errors = int(getattr(report, "error_count", 0) or 0)
        if errors:
            return f"source inspection reported {errors} error{'s' if errors != 1 else ''}"
        return "source inspection did not produce package-ready map evidence"

    def open_source(
        self, source: str | Path, scan: PackageScan | None = None,
        *, descriptor: str | Path | None = None,
    ) -> bool:
        """Open an asset source and resolve its descriptor when one is present."""

        try:
            resolved = Path(source).expanduser().resolve(strict=True)
            # Selecting a new source immediately invalidates the previous
            # source/descriptor pair. Inspection must never fail while leaving
            # the old package's Build action enabled.
            self._clear_project_context()
            self.source = resolved
            self.scan = None
            self.source_value.set(str(resolved))
            self._populate_assets()
            loaded_scan = scan
            if loaded_scan is None and not (
                resolved.is_file() and resolved.suffix.casefold() == ".json"
            ):
                try:
                    loaded_scan = AddonPackageInspector(
                        self.project_root, self._game_path(),
                    ).inspect(resolved)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._record_source_inspection(None, error=exc)
                    self.status.set(f"Map source inspection failed · {exc}")
                    self._show_detail(
                        "The selected source could not be inspected and is not "
                        f"package-ready.\n\n{exc}"
                    )
                    return False
            self.scan = loaded_scan
            self._populate_assets()
            chosen_descriptor = Path(descriptor).expanduser().resolve(strict=True) if descriptor else None
            if chosen_descriptor is not None:
                return self.open_descriptor(chosen_descriptor, source=resolved)
            if resolved.is_dir():
                descriptors = [
                    resolved / name for name in MAP_DESCRIPTOR_NAMES
                    if (resolved / name).is_file()
                ]
                if len(descriptors) == 1:
                    return self.open_descriptor(descriptors[0], source=resolved)
            try:
                report = (
                    MapProjectResolver.inspect_scan(loaded_scan)
                    if loaded_scan is not None else
                    MapProjectResolver().inspect(
                        resolved, project_root=self.project_root,
                        gta_path=self._game_path(),
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                self._record_source_inspection(None, error=exc)
                self.status.set(f"Map source inspection failed · {exc}")
                self._show_detail(
                    "The selected source could not be inspected and is not "
                    f"package-ready.\n\n{exc}"
                )
                return False
            if report is not None:
                self._accept_report(report)
                return True
            if resolved.is_file() and resolved.suffix.casefold() == ".json":
                return self.open_descriptor(resolved, source=resolved.parent)
            self.project = None
            self.project_data = None
            self.report = None
            self._source_ready = False
            self._populate_topology({})
            self._refresh_action_states()
            self.status.set(
                f"Indexed {len(map_asset_entries(loaded_scan))} map assets · "
                "open a project descriptor to define levels, portals, and garages."
            )
            self._show_detail(
                "Map source is ready. Open a descriptor to bind these assets to "
                "streaming groups, named levels, pedestrian/vehicle portals, and garages."
            )
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not open map source", str(exc), parent=self)
            return False

    def open_descriptor(
        self, descriptor: str | Path, *, source: str | Path | None = None,
    ) -> bool:
        """Load and validate the declarative project independently of package assets."""

        try:
            selected = Path(descriptor).expanduser().resolve(strict=True)
            project = MapProject.load(selected)
            source_path = (
                Path(source).expanduser().resolve(strict=True)
                if source is not None else self.source or selected.parent
            )
            report = None
            inspection_error: Exception | None = None
            if not (source_path.is_file() and source_path.suffix.casefold() == ".json"):
                try:
                    report = (
                        MapProjectResolver.inspect_scan(self.scan)
                        if self.scan is not None and self.source == source_path else
                        MapProjectResolver().inspect(
                            source_path, project_root=self.project_root,
                            gta_path=self._game_path(),
                        )
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    inspection_error = exc
            self.source = source_path
            self.descriptor = selected
            self.project = project
            self.project_data = project.to_dict()
            self._descriptor_ready = True
            self.edition_box.configure(values=tuple(project.editions))
            if self.edition_value.get() not in project.editions:
                self.edition_value.set(project.editions[0])
            self._record_source_inspection(report, error=inspection_error)
            self.dirty = False
            self.source_value.set(str(source_path))
            self.descriptor_value.set(str(selected))
            payload = _to_mapping(report) if report is not None else project.to_dict()
            if "project" in payload and isinstance(payload["project"], Mapping):
                project_payload = dict(payload["project"])
            else:
                project_payload = project.to_dict()
            self._populate_topology(project_payload)
            self._refresh_action_states()
            project_summary = (
                f"Valid map project · {project_payload.get('name', project_payload.get('id', selected.stem))} · "
                f"{len(_items(project_payload.get('levels')))} levels · "
                f"{len(_items(project_payload.get('portals')))} portals · "
                f"{len(_items(project_payload.get('garages')))} garages"
            )
            if inspection_error is not None:
                self.status.set(
                    f"{project_summary} · source inspection failed: {inspection_error}"
                )
            elif not self._source_ready:
                self.status.set(
                    f"{project_summary} · build unavailable: "
                    f"{self._source_failure_summary(report)}"
                )
            else:
                self.status.set(project_summary)
            self._show_detail(json.dumps(payload, indent=2))
            return True
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.project = None
            self.project_data = None
            self._descriptor_ready = False
            self._refresh_action_states()
            messagebox.showerror("Invalid map project", str(exc), parent=self)
            self.status.set(f"Map project validation failed · {exc}")
            return False

    def _accept_report(self, report: object) -> None:
        payload = _to_mapping(report)
        project_object = getattr(report, "project", None)
        if isinstance(project_object, MapProject):
            self.project = project_object
            self.project_data = project_object.to_dict()
        descriptor = getattr(report, "descriptor", None) or getattr(
            report, "descriptor_path", None,
        )
        if descriptor:
            self.descriptor = Path(descriptor).resolve()
            self.descriptor_value.set(str(self.descriptor))
        self._record_source_inspection(report)
        project_payload = (
            self.project_data if self.project_data is not None else {}
        )
        self._populate_topology(project_payload)
        self._refresh_action_states()
        self.status.set(
            f"Inspected map source · {len(payload.get('assets', map_asset_entries(self.scan)))} map assets · "
            f"{len(_items(project_payload.get('levels')))} levels · "
            f"{len(_items(project_payload.get('portals')))} portals · "
            f"{len(_items(project_payload.get('garages')))} garages"
            + ("" if self.project_data is not None else " · descriptor required")
            + (
                "" if self._source_ready else
                f" · not package-ready: {self._source_failure_summary(report)}"
            )
        )
        self._show_detail(json.dumps(payload, indent=2))

    def _populate_assets(self) -> None:
        self.asset_tree.delete(*self.asset_tree.get_children())
        for index, entry in enumerate(map_asset_entries(self.scan)):
            item = f"asset:{index}"
            self.asset_tree.insert(
                "", "end", iid=item, text=entry.path,
                values=(entry.suffix.removeprefix(".").upper(), self._human_size(entry.size)),
            )
            self._detail_payloads[item] = {
                "kind": "map_asset", "path": entry.path,
                "type": entry.suffix, "size": entry.size,
            }

    def _populate_topology(self, project: Mapping[str, Any]) -> None:
        self.topology_tree.delete(*self.topology_tree.get_children())
        self._detail_payloads = {
            key: value for key, value in self._detail_payloads.items()
            if key.startswith("asset:")
        }
        if project:
            project_root = "root:project"
            self.topology_tree.insert(
                "", "end", iid=project_root, text="Project and streaming",
                values=("Group", ""), open=True,
            )
            metadata = {
                key: project[key] for key in (
                    "schema_version", "id", "package_id", "name", "version", "editions",
                ) if key in project
            }
            self.topology_tree.insert(
                project_root, "end", iid="project:metadata", text="Project identity",
                values=("Project", str(project.get("package_id", ""))),
            )
            self.topology_tree.insert(
                project_root, "end", iid="project:streaming", text="Streaming and loads",
                values=("Streaming", str(_to_mapping(project.get("streaming")).get("pack_name", ""))),
            )
            self._detail_payloads["project:metadata"] = metadata
            self._detail_payloads["project:streaming"] = _to_mapping(
                project.get("streaming"),
            )
        roots = {}
        for family, label in (
            ("levels", "Levels"), ("portals", "Entrances and exits"),
            ("garages", "Garages"),
        ):
            root = f"root:{family}"
            roots[family] = root
            self.topology_tree.insert(
                "", "end", iid=root, text=label, values=("Group", ""), open=True,
            )
            records = _items(project.get(family))
            for index, raw in enumerate(records):
                record = _to_mapping(raw)
                identifier = str(record.get("id") or record.get("name") or f"{family[:-1]} {index + 1}")
                link = self._topology_link(family, record)
                item = f"{family}:{index}"
                self.topology_tree.insert(
                    root, "end", iid=item, text=identifier,
                    values=(family[:-1].title(), link),
                )
                self._detail_payloads[item] = record
                if family == "garages":
                    for slot_index, slot_raw in enumerate(_items(record.get("slots"))):
                        slot = _to_mapping(slot_raw)
                        slot_item = f"garages:{index}:slots:{slot_index}"
                        self.topology_tree.insert(
                            item, "end", iid=slot_item,
                            text=str(slot.get("id") or f"slot {slot_index + 1}"),
                            values=("Slot", ", ".join(slot.get("vehicle_types", ()))),
                        )
                        self._detail_payloads[slot_item] = slot

    @staticmethod
    def _topology_link(family: str, record: Mapping[str, Any]) -> str:
        if family == "portals":
            start = record.get("from") or record.get("from_level") or "world"
            end = record.get("to") or record.get("to_level") or "world"
            if isinstance(start, Mapping):
                start = start.get("level", "world")
            if isinstance(end, Mapping):
                end = end.get("level", "world")
            return f"{start} → {end}"
        if family == "garages":
            return str(record.get("level_id") or record.get("level") or "world")
        return str(record.get("name") or record.get("streaming_group") or "")

    def _select_asset(self, _event: object | None = None) -> None:
        selected = self.asset_tree.selection()
        if not selected:
            return
        payload = self._detail_payloads.get(selected[0], {})
        self.open_asset_button.configure(state="normal")
        self.apply_json_button.configure(state="disabled")
        self.revert_json_button.configure(state="disabled")
        self.remove_button.configure(state="disabled")
        self._show_detail(json.dumps(payload, indent=2))

    def _select_topology(self, _event: object | None = None) -> None:
        selected = self.topology_tree.selection()
        if not selected:
            return
        payload = self._detail_payloads.get(selected[0])
        self.open_asset_button.configure(state="disabled")
        editable = payload is not None and not selected[0].startswith("root:")
        removable = editable and not selected[0].startswith("project:")
        self.apply_json_button.configure(state="normal" if editable else "disabled")
        self.revert_json_button.configure(state="normal" if editable else "disabled")
        self.remove_button.configure(state="normal" if removable else "disabled")
        if payload is not None:
            self._show_detail(json.dumps(payload, indent=2), editable=True)

    def _open_selected_asset(self, _event: object | None = None) -> None:
        selected = self.asset_tree.selection()
        if not selected or self._on_open_asset is None:
            return
        path = self._detail_payloads.get(selected[0], {}).get("path")
        if isinstance(path, str):
            self._on_open_asset(path)

    def validate(self) -> bool:
        if self.project_data is None:
            self.status.set("Select a map project descriptor before validation.")
            return False
        try:
            self.project = MapProject.from_dict(self.project_data)
            payload = self.project.to_dict()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._descriptor_ready = False
            self._refresh_action_states()
            self.status.set(f"Map project validation failed · {exc}")
            self._show_detail(str(exc))
            return False
        self.status.set(
            f"Validation passed · {payload.get('id', self.descriptor.stem)} · "
            f"{len(_items(payload.get('levels')))} levels · "
            f"{len(_items(payload.get('portals')))} portals · "
            f"{len(_items(payload.get('garages')))} garages"
        )
        self._descriptor_ready = True
        self._refresh_action_states()
        self._show_detail(json.dumps(payload, indent=2))
        return True

    def save_descriptor(self) -> bool:
        if self.descriptor is None or self.project_data is None:
            self.status.set("Select a descriptor before saving.")
            return False
        temporary = self.descriptor.with_name(self.descriptor.name + ".tmp")
        try:
            project = MapProject.from_dict(self.project_data)
            temporary.write_text(
                json.dumps(project.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            temporary.replace(self.descriptor)
        except (OSError, TypeError, ValueError) as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self.status.set(f"Descriptor was not saved · {exc}")
            return False
        self.project = project
        self.project_data = project.to_dict()
        self._descriptor_ready = True
        self.dirty = False
        self._refresh_action_states()
        self.status.set(f"Map descriptor saved and validated · {self.descriptor}")
        return True

    def _selected_topology_id(self) -> str | None:
        selected = self.topology_tree.selection()
        return selected[0] if selected else None

    @staticmethod
    def _unique_id(records: list[object], prefix: str) -> str:
        existing = {
            str(item.get("id", "")).casefold()
            for item in records if isinstance(item, Mapping)
        }
        index = 1
        while f"{prefix}.{index:02d}" in existing:
            index += 1
        return f"{prefix}.{index:02d}"

    def _add_record(self, family: str) -> None:
        if self.project_data is None:
            self.status.set("Create or open a map descriptor before adding topology.")
            return
        candidate = json.loads(json.dumps(self.project_data))
        records = candidate.setdefault(family, [])
        if not isinstance(records, list):
            return
        if family == "levels":
            identifier = self._unique_id(records, "level")
            record = {
                "id": identifier, "name": "New Level",
                "center": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0},
                "ipls": [],
            }
        elif family == "portals":
            levels = candidate.get("levels", [])
            if not levels:
                self.status.set("Add a level before adding an entrance or exit.")
                return
            level_id = str(levels[0].get("id"))
            identifier = self._unique_id(records, "portal")
            record = {
                "id": identifier, "name": "New Entrance", "mode": "both",
                "from": {"level": "world", "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0}},
                "to": {"level": level_id, "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 180.0}},
                "radius": 3.0, "one_way": False,
            }
        else:
            levels = candidate.get("levels", [])
            portals = candidate.get("portals", [])
            if not levels or not portals:
                self.status.set("Add a level and connecting portal before adding a garage.")
                return
            level_id = str(levels[0].get("id"))
            portal = next(
                (item for item in portals if level_id in {
                    item.get("from", {}).get("level"), item.get("to", {}).get("level"),
                }), None,
            )
            if portal is None:
                self.status.set("No portal connects the selected project level.")
                return
            identifier = self._unique_id(records, "garage")
            record = {
                "id": identifier, "name": "New Garage", "level_id": level_id,
                "entrance_portal_id": portal["id"], "capacity": 10,
                "vehicle_types": ["land"],
                "slots": [{
                    "id": "slot.01", "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0},
                    "vehicle_types": ["land"],
                }],
                "rules": {"allow_store": True, "allow_retrieve": True, "save_policy": "story_save_only"},
            }
        records.append(record)
        self._commit_candidate(candidate, f"Added {family[:-1]} '{record['id']}'")
        self.topology_tree.selection_set(f"{family}:{len(records) - 1}")
        self.topology_tree.see(f"{family}:{len(records) - 1}")
        self._select_topology()

    def _add_slot(self) -> None:
        if self.project_data is None:
            return
        selected = self._selected_topology_id() or ""
        parts = selected.split(":")
        if not parts or parts[0] != "garages" or len(parts) < 2:
            self.status.set("Select a garage before adding a spawn/store slot.")
            return
        garage_index = int(parts[1])
        candidate = json.loads(json.dumps(self.project_data))
        garage = candidate["garages"][garage_index]
        slots = garage["slots"]
        if len(slots) >= int(garage.get("capacity", 0)):
            self.status.set(
                "Increase the garage capacity before adding another spawn/store slot."
            )
            return
        identifier = self._unique_id(slots, "slot")
        slots.append({
            "id": identifier,
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "heading": 0.0},
            "vehicle_types": list(garage["vehicle_types"]),
        })
        self._commit_candidate(candidate, f"Added garage slot '{identifier}'")
        item = f"garages:{garage_index}:slots:{len(slots) - 1}"
        self.topology_tree.selection_set(item)
        self.topology_tree.see(item)
        self._select_topology()

    def _apply_selected_json(self) -> None:
        selected = self._selected_topology_id()
        if selected is None or self.project_data is None:
            return
        try:
            replacement = json.loads(self.detail.get("1.0", "end-1c"))
            if not isinstance(replacement, dict):
                raise ValueError("Selected topology JSON must be an object")
            candidate = json.loads(json.dumps(self.project_data))
            parts = selected.split(":")
            if selected == "project:metadata":
                allowed = {
                    "schema_version", "id", "package_id", "name", "version", "editions",
                }
                unknown = set(replacement) - allowed
                missing = allowed - set(replacement)
                if unknown or missing:
                    raise ValueError(
                        "Project identity must contain exactly: "
                        + ", ".join(sorted(allowed))
                    )
                for key in allowed:
                    candidate[key] = replacement[key]
            elif selected == "project:streaming":
                candidate["streaming"] = replacement
            elif len(parts) == 2:
                candidate[parts[0]][int(parts[1])] = replacement
            elif len(parts) == 4 and parts[2] == "slots":
                candidate["garages"][int(parts[1])]["slots"][int(parts[3])] = replacement
            else:
                raise ValueError("Select a level, portal, garage, or garage slot")
            self._commit_candidate(candidate, "Applied selected topology JSON")
            if self.topology_tree.exists(selected):
                self.topology_tree.selection_set(selected)
                self.topology_tree.see(selected)
                self._select_topology()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self.status.set(f"Edit was not applied · {exc}")

    def _revert_selected_json(self) -> None:
        selected = self._selected_topology_id()
        payload = self._detail_payloads.get(selected or "")
        if payload is not None:
            self._show_detail(json.dumps(payload, indent=2), editable=True)
            self.status.set("Reverted the inline editor to the validated project value.")

    def _remove_selected(self) -> None:
        selected = self._selected_topology_id()
        if selected is None or self.project_data is None:
            return
        candidate = json.loads(json.dumps(self.project_data))
        parts = selected.split(":")
        try:
            if len(parts) == 2:
                removed = candidate[parts[0]].pop(int(parts[1]))
            elif len(parts) == 4 and parts[2] == "slots":
                removed = candidate["garages"][int(parts[1])]["slots"].pop(int(parts[3]))
            else:
                return
            self._commit_candidate(candidate, f"Removed '{removed.get('id', 'selection')}'")
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            self.status.set(f"Selection was not removed · {exc}")

    def _commit_candidate(self, candidate: dict[str, Any], message: str) -> None:
        project = MapProject.from_dict(candidate)
        self.project = project
        self.project_data = project.to_dict()
        self._descriptor_ready = True
        self.dirty = True
        self._populate_topology(self.project_data)
        self._refresh_action_states()
        self.status.set(f"{message} · save descriptor to keep this change")

    def _build_package(self) -> None:
        if self.source is None or self.descriptor is None:
            self.status.set("Select both a map source and descriptor before building.")
            return
        if not self._descriptor_ready:
            self.status.set(
                "Build unavailable · validate the map project descriptor first."
            )
            return
        if not self._source_ready:
            reason = self._source_inspection_error or self._source_failure_summary(
                self.report,
            )
            self.status.set(f"Build unavailable · map source is not package-ready: {reason}")
            return
        if self.dirty and not self.save_descriptor():
            return
        output = filedialog.askdirectory(
            parent=self, title="Select the parent folder for the new map package",
        )
        if not output:
            return
        try:
            assert self.project is not None
            edition = self.edition_value.get().casefold()
            destination = Path(output).resolve() / (
                f"{self.project.package_id}-{edition}-{self.project.version}"
            )
            builder = MapAddonPackageBuilder(self.project_root, self._game_path())
            result = builder.build(
                self.source, self.descriptor, destination, edition=edition,
            )
            payload = _to_mapping(result)
            built_root = payload.get("root") or str(destination)
            self.output_value.set(str(built_root))
            self.status.set(f"Map package built and validated · {built_root}")
            self._show_detail(json.dumps(payload, indent=2))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.status.set(f"Map package build failed · {exc}")
            self._show_detail(str(exc))

    def confirm_navigation(self) -> bool:
        """Block navigation until explicitly authored JSON has been saved."""

        if not self.dirty:
            return True
        self.status.set("Save the map descriptor before leaving the Map Workbench.")
        return False

    def _show_detail(self, text: str, *, editable: bool = False) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="normal" if editable else "disabled")

    @staticmethod
    def _human_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{size} B"
