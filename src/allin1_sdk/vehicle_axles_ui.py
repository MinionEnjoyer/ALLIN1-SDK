"""Compact Axle Configurator panel used by the Vehicle Workbench.

The panel edits a draft only.  Its host owns persistence and therefore keeps
the same revision, validation, and undo/redo boundary as every other vehicle
authoring operation.
"""

from __future__ import annotations

import json
import re
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Iterable

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    AXLE_PRESETS,
    EXPORT_FIVEM_RUNTIME,
    EXPORT_STOCK_METADATA,
    PRESET_CUSTOM,
    STEERING_GAIN_EPSILON,
    AxleFinding,
    AxleConfiguration,
    VehicleAxle,
    apply_axle_preset,
    apply_intentional_layout_override,
    clear_intentional_layout_override,
    detect_axle_configuration,
    retarget_axle_configuration,
    stock_metadata_flags,
    validate_axle_configuration,
)
from allin1_sdk.axle_steering_geometry import (
    SteeringGeometryError,
    SteeringGeometryRequest,
    SteeringGeometrySolution,
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.native_assets import NativeModelBone, NativeModelScene
from allin1_sdk.ui_foundation import BODY_BACKGROUND


TARGET_LABELS = {
    "story-legacy": "Story Legacy",
    "story-enhanced": "Story Enhanced",
    "fivem-legacy": "FiveM Legacy",
    "fivem-enhanced": "FiveM Enhanced",
}
TARGET_KEYS = {label: key for key, label in TARGET_LABELS.items()}
EXPORT_LABELS = {
    "Stock metadata": EXPORT_STOCK_METADATA,
    "Selective runtime": EXPORT_FIVEM_RUNTIME,
}


def _format_steering_gain(gain: float) -> str:
    """Return the compact signed form used by the resolved-axle table."""

    value = float(gain)
    return "0.00" if abs(value) < 0.0005 else f"{value:+.2f}"


def _steering_solution_summary(solution: SteeringGeometrySolution) -> str:
    """Summarize one geometry proposal without obscuring the axle editor."""

    source = {
        "explicit": "manual pivot",
        "selected_fixed_axles": "selected fixed axle",
        "derived_fixed_axles": "fixed axle",
    }.get(solution.pivot_source, solution.pivot_source.replace("_", " "))
    gains = " · ".join(
        f"A{item.physical_order} {_format_steering_gain(item.steering_gain)}"
        for item in solution.axles
    )
    return (
        f"Pivot Y {solution.pivot_longitudinal_position:.3f} ({source}) · "
        f"{gains}"
    )


def _current_gain_summary(config: AxleConfiguration) -> str:
    gains = " · ".join(
        f"A{axle.physical_order} {_format_steering_gain(axle.steering_gain)}"
        for axle in config.axles
    )
    return f"Current steering gains · {gains}"


def _requires_selective_steering_runtime(config: AxleConfiguration) -> bool:
    """Return whether signed/scaled gains exceed legacy boolean steering."""

    return any(
        abs(float(axle.steering_gain) - (1.0 if axle.steered else 0.0))
        > STEERING_GAIN_EPSILON
        for axle in config.axles
    )


def _edit_axle_controls(
    config: AxleConfiguration,
    index: int,
    *,
    steered: bool,
    powered: bool,
    service_brake: bool,
    handbrake: bool,
) -> tuple[AxleConfiguration, bool]:
    """Apply one editor row and invalidate geometry only when its role changes.

    Automatic steering evidence describes a specific set of steered/fixed
    axles.  Changing that set makes the old pivot/reference evidence stale, so
    the draft returns to safe schema-1 boolean steering until the author runs
    Calculate steering again.  Drive and brake edits do not affect steering
    geometry and therefore preserve signed gains and their evidence.
    """

    rows = list(config.axles)
    if not 0 <= index < len(rows):
        raise IndexError("Axle editor row is outside the configured axle array")
    steering_changed = bool(steered) != rows[index].steered
    rows[index] = replace(
        rows[index],
        steered=bool(steered),
        steering_gain=(1.0 if steered else 0.0)
        if steering_changed else rows[index].steering_gain,
        powered=bool(powered),
        service_brake=bool(service_brake),
        handbrake=bool(handbrake),
    )
    if steering_changed:
        rows = [
            replace(row, steering_gain=1.0 if row.steered else 0.0)
            for row in rows
        ]
        return (
            replace(
                config,
                schema_version=AXLE_SCHEMA_VERSION,
                preset=PRESET_CUSTOM,
                axles=tuple(rows),
                steering_calculation=None,
            ),
            True,
        )
    return replace(config, preset=PRESET_CUSTOM, axles=tuple(rows)), False


class _PhysicalAxleOrderDialog(simpledialog.Dialog):
    """Small, explicit front-to-rear ordering editor for unusual skeletons."""

    def __init__(
        self,
        parent: tk.Misc,
        pairs: Iterable[tuple[str, str]],
    ) -> None:
        self._pairs = list(pairs)
        self.listbox: tk.Listbox | None = None
        self.result: tuple[tuple[str, str], ...] | None = None
        super().__init__(parent, title="Physical axle order")

    def body(self, master: tk.Misc) -> tk.Widget | None:
        ttk.Label(
            master,
            text=(
                "Arrange the existing wheel-bone pairs from the physical front "
                "of the vehicle to the physical rear. This changes behavior "
                "roles only; it does not rename bones or replace wheel meshes."
            ),
            wraplength=430,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.listbox = tk.Listbox(
            master, height=max(3, len(self._pairs)), exportselection=False,
        )
        self.listbox.grid(row=1, column=0, sticky="nsew")
        controls = ttk.Frame(master)
        controls.grid(row=1, column=1, sticky="ns", padx=(8, 0))
        ttk.Button(
            controls, text="↑ Toward front", command=lambda: self._move(-1),
        ).pack(fill="x")
        ttk.Button(
            controls, text="↓ Toward rear", command=lambda: self._move(1),
        ).pack(fill="x", pady=(5, 0))
        ttk.Label(
            master,
            text=(
                "Use this only when the model deliberately reuses GTA's front "
                "and shared rear wheel-mesh families in a nonstandard order."
            ),
            foreground="#52635c", wraplength=430, justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        master.columnconfigure(0, weight=1)
        master.rowconfigure(1, weight=1)
        self._refresh(0)
        return self.listbox

    def _refresh(self, selected: int) -> None:
        if self.listbox is None:
            return
        self.listbox.delete(0, "end")
        count = len(self._pairs)
        for index, (left, right) in enumerate(self._pairs):
            role = "Front" if index == 0 else "Rear" if index == count - 1 else "Middle"
            self.listbox.insert("end", f"{index + 1}. {role} — {left} / {right}")
        if self._pairs:
            chosen = max(0, min(selected, len(self._pairs) - 1))
            self.listbox.selection_set(chosen)
            self.listbox.activate(chosen)

    def _move(self, direction: int) -> None:
        if self.listbox is None:
            return
        selected = self.listbox.curselection()
        if not selected:
            return
        current = int(selected[0])
        target = current + direction
        if not 0 <= target < len(self._pairs):
            return
        self._pairs[current], self._pairs[target] = self._pairs[target], self._pairs[current]
        self._refresh(target)

    def validate(self) -> bool:
        return len(self._pairs) == len(set(self._pairs))

    def apply(self) -> None:
        self.result = tuple(self._pairs)


class VehicleAxlesPanel(ttk.Frame):
    """Variable-length, target-aware axle editor for two to five pairs."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_apply: Callable[[AxleConfiguration], None],
        on_undo: Callable[[], None],
        on_redo: Callable[[], None],
        on_export: Callable[[AxleConfiguration], None],
    ) -> None:
        super().__init__(parent)
        self._on_apply = on_apply
        self._on_undo = on_undo
        self._on_redo = on_redo
        self._on_export = on_export
        self._model = ""
        self._bones: tuple[NativeModelBone, ...] = ()
        self._asset_names: tuple[str, ...] = ()
        self._handling_flags: int | None = None
        self._drive_bias_front: float | None = None
        self._draft: AxleConfiguration | None = None
        self._steering_solution: SteeringGeometrySolution | None = None
        self._preview_findings = ()
        self._preview_blocked = False
        self._editable = False
        self._loading_row = False
        self._prefab_ids: dict[str, str] = {}
        self._visual_ids: dict[str, str] = {}
        self._prefab_catalog = None
        self._wrap_labels: list[ttk.Label] = []
        self._action_layout: str | None = None
        self._filter_values = {
            "axle_count": tk.StringVar(value="Any"),
            "layout": tk.StringVar(value="Any"),
            "category": tk.StringVar(value="Any"),
            "steering": tk.StringVar(value="Any"),
            "drive": tk.StringVar(value="Any"),
            "lift": tk.StringVar(value="Any"),
            "target": tk.StringVar(value="Any"),
            "experimental": tk.StringVar(value="Any"),
        }
        self._build()

    def _build(self) -> None:
        # The inspector can be as narrow as 300 px and, at the supported
        # 1020x680 shell size, has substantially less vertical room than this
        # editor needs.  Keep every editing section at its natural height in a
        # scrollable surface instead of letting pack shrink the first
        # expandable Treeview down to a one-pixel dark heading.
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.editor_host = ttk.Frame(self)
        self.editor_host.grid(row=0, column=0, sticky="nsew")
        self.editor_host.rowconfigure(0, weight=1)
        self.editor_host.columnconfigure(0, weight=1)
        self.editor_canvas = tk.Canvas(
            self.editor_host, background=BODY_BACKGROUND, borderwidth=0,
            highlightthickness=0, takefocus=True,
        )
        self.editor_scrollbar = ttk.Scrollbar(
            self.editor_host, orient="vertical", command=self.editor_canvas.yview,
        )
        self.editor_canvas.configure(yscrollcommand=self.editor_scrollbar.set)
        self.editor_canvas.grid(row=0, column=0, sticky="nsew")
        self.editor_scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.editor_content = ttk.Frame(self.editor_canvas)
        self._editor_window = self.editor_canvas.create_window(
            (0, 0), window=self.editor_content, anchor="nw",
        )
        self.editor_content.bind("<Configure>", self._editor_content_configured)
        self.editor_canvas.bind("<Configure>", self._editor_viewport_configured)

        body = self.editor_content
        intro = ttk.Label(
            body,
            text=(
                "Map physical axle behavior to the vehicle's existing wheel "
                "bones. Skeleton names and positions stay unchanged."
            ),
            foreground="#52635c", wraplength=410, justify="left",
        )
        intro.pack(fill="x", pady=(0, 7))
        self._wrap_labels.append(intro)

        setup = ttk.LabelFrame(body, text="1 · Target and preset", padding=7)
        setup.pack(fill="x")
        self.target = tk.StringVar(value=TARGET_LABELS["story-legacy"])
        self.preset = tk.StringVar(value=AXLE_PRESETS[0])
        self.export_mode = tk.StringVar(value="Stock metadata")
        self.prefab = tk.StringVar()
        self.visual = tk.StringVar()
        self.visual_axle = tk.StringVar(value="All applicable")
        self._setup_combos: dict[str, ttk.Combobox] = {}
        for row, (label, variable, values) in enumerate((
            ("Target", self.target, tuple(TARGET_LABELS.values())),
            ("Quick preset", self.preset, AXLE_PRESETS),
            ("Export behavior", self.export_mode, tuple(EXPORT_LABELS)),
        )):
            ttk.Label(setup, text=label).grid(row=row, column=0, sticky="w", pady=2)
            combo = ttk.Combobox(
                setup, textvariable=variable, values=values, state="readonly", width=16,
            )
            combo.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=2)
            combo.bind("<<ComboboxSelected>>", self._configuration_changed)
            self._setup_combos[label] = combo
        self.target_combo = self._setup_combos["Target"]
        self.preset_combo = self._setup_combos["Quick preset"]
        self.export_combo = self._setup_combos["Export behavior"]
        setup.columnconfigure(1, weight=1)
        preset_actions = ttk.Frame(setup)
        preset_actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        self.detect_button = ttk.Button(
            preset_actions, text="Detect skeleton", command=self._detect,
        )
        self.detect_button.pack(side="left")
        self.preset_button = ttk.Button(
            preset_actions, text="Apply preset", command=self._preview_preset,
        )
        self.preset_button.pack(side="left", padx=(5, 0))
        self.order_button = ttk.Button(
            preset_actions, text="Physical order…",
            command=self._set_physical_axle_order,
        )
        self.order_button.pack(side="left", padx=(5, 0))

        prefab_frame = ttk.LabelFrame(body, text="2 · Prefab library", padding=7)
        prefab_frame.pack(fill="x", pady=(7, 0))
        ttk.Label(prefab_frame, text="Behavior").grid(row=0, column=0, sticky="w")
        self.prefab_combo = ttk.Combobox(
            prefab_frame, textvariable=self.prefab, state="readonly", width=16,
        )
        self.prefab_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.prefab_combo.bind("<<ComboboxSelected>>", self._show_prefab_details)
        ttk.Label(prefab_frame, text="Tyres").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.visual_combo = ttk.Combobox(
            prefab_frame, textvariable=self.visual, state="readonly", width=16,
        )
        self.visual_combo.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        ttk.Label(prefab_frame, text="Tyre axle").grid(
            row=2, column=0, sticky="w", pady=(4, 0),
        )
        self.visual_axle_combo = ttk.Combobox(
            prefab_frame, textvariable=self.visual_axle, state="readonly", width=16,
            values=("All applicable",),
        )
        self.visual_axle_combo.grid(
            row=2, column=1, sticky="ew", padx=(6, 0), pady=(4, 0),
        )
        self.prefab_details = tk.StringVar(
            value="Select a behavior prefab to see its schematic and target requirements."
        )
        prefab_detail_label = ttk.Label(
            prefab_frame, textvariable=self.prefab_details, foreground="#52635c",
            wraplength=380, justify="left",
        )
        prefab_detail_label.grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(5, 0),
        )
        self._wrap_labels.append(prefab_detail_label)
        self.prefab_actions = ttk.Frame(prefab_frame)
        self.prefab_actions.grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(5, 0),
        )
        self.filter_menu = tk.Menu(self.prefab_actions, tearoff=False)
        self.filter_button = ttk.Menubutton(
            self.prefab_actions, text="Filters ▾", menu=self.filter_menu,
        )
        self.prefab_button = ttk.Button(
            self.prefab_actions, text="Use behavior", command=self._preview_prefab,
        )
        self.visual_button = ttk.Button(
            self.prefab_actions, text="Preview tyres", command=self._preview_visual,
        )
        prefab_frame.columnconfigure(1, weight=1)
        self._load_catalogs()

        self.axle_section = ttk.LabelFrame(
            body, text="3 · Resolved physical axles", padding=6,
        )
        self.axle_section.pack(fill="x", pady=(7, 0))
        table = self.axle_section
        columns = (
            "order", "role", "bones", "steer", "gain", "drive", "brakes",
            "family", "indices",
        )
        self.tree = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="browse", height=5,
        )
        headings = {
            "order": "#", "role": "Role", "bones": "Canonical bones",
            "steer": "Steer", "gain": "Gain", "drive": "Drive", "brakes": "S/H",
            "family": "Visual family", "indices": "Runtime slots",
        }
        widths = {
            "order": 30, "role": 54, "bones": 148, "steer": 45, "drive": 45,
            "gain": 52, "brakes": 48, "family": 112, "indices": 78,
        }
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], minwidth=widths[key], stretch=key == "bones")
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._select_axle)

        row_editor = ttk.Frame(table)
        row_editor.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        row_editor.columnconfigure(0, weight=1)
        row_editor.columnconfigure(1, weight=1)
        self.row_steered = tk.BooleanVar()
        self.row_powered = tk.BooleanVar()
        self.row_service = tk.BooleanVar(value=True)
        self.row_handbrake = tk.BooleanVar()
        self.row_controls: list[tk.Widget] = []
        self._unsupported_brake_controls: list[tk.Widget] = []
        for index, (label, variable) in enumerate((
            ("Steered", self.row_steered), ("Powered", self.row_powered),
            ("Service brake*", self.row_service), ("Handbrake*", self.row_handbrake),
        )):
            widget = ttk.Checkbutton(row_editor, text=label, variable=variable)
            widget.grid(
                row=index // 2, column=index % 2, sticky="w",
                padx=(0, 6), pady=(0, 2),
            )
            self.row_controls.append(widget)
            if label.endswith("*"):
                widget.configure(state="disabled")
                self._unsupported_brake_controls.append(widget)
        self.row_apply = ttk.Button(
            row_editor, text="Update selected", command=self._update_selected_axle,
        )
        self.row_apply.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(3, 0),
        )
        self.row_controls.append(self.row_apply)
        brake_note = ttk.Label(
            table,
            text="* Service/handbrake columns are preserved in the schema; unsupported targets are reported rather than simulated.",
            foreground="#66756f", wraplength=400, justify="left",
        )
        brake_note.grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0),
        )
        self._wrap_labels.append(brake_note)

        self.geometry_summary = tk.StringVar(
            value="Uses wheel-bone Y only; tyre appearance stays independent."
        )
        geometry_bar = ttk.Frame(table)
        geometry_bar.grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(5, 0),
        )
        geometry_bar.columnconfigure(1, weight=1)
        self.geometry_button = ttk.Button(
            geometry_bar, text="Calculate steering", command=self._calculate_steering,
        )
        self.geometry_button.grid(row=0, column=0, sticky="w", padx=(0, 7))
        self.geometry_label = ttk.Label(
            geometry_bar, textvariable=self.geometry_summary, foreground="#52635c",
            wraplength=400, justify="left",
        )
        self.geometry_label.grid(row=0, column=1, sticky="ew")
        self._wrap_labels.append(self.geometry_label)

        self.handling_preview = tk.StringVar(
            value="Handling preview becomes available after metadata is linked."
        )
        handling_label = ttk.Label(
            table, textvariable=self.handling_preview, foreground="#52635c",
            wraplength=400, justify="left",
        )
        handling_label.grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(4, 0),
        )
        self._wrap_labels.append(handling_label)

        self.findings_section = ttk.LabelFrame(
            body, text="4 · Validation and compatibility", padding=6,
        )
        self.findings_section.pack(fill="x", pady=(7, 0))
        findings = self.findings_section
        self.finding_tree = ttk.Treeview(
            findings, columns=("severity", "message"), show="headings", height=4,
        )
        self.finding_tree.heading("severity", text="Level")
        self.finding_tree.heading("message", text="Finding")
        self.finding_tree.column("severity", width=65, stretch=False)
        self.finding_tree.column("message", width=300, stretch=True)
        finding_scroll = ttk.Scrollbar(
            findings, orient="vertical", command=self.finding_tree.yview,
        )
        self.finding_tree.configure(yscrollcommand=finding_scroll.set)
        self.finding_tree.grid(row=0, column=0, sticky="nsew")
        finding_scroll.grid(row=0, column=1, sticky="ns")
        findings.columnconfigure(0, weight=1)

        ttk.Separator(self).grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.footer = ttk.Frame(self)
        self.footer.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.footer.columnconfigure(0, weight=1)
        self.actions = ttk.Frame(self.footer)
        self.actions.grid(row=0, column=0, sticky="ew")
        style = ttk.Style(self)
        style.configure("Axle.Toolbar.TButton", padding=(6, 4))
        style.configure("Axle.Accent.TButton", padding=(8, 5))
        self.apply_button = ttk.Button(
            self.actions, text="Apply + validate", command=self._apply,
            style="Axle.Accent.TButton",
        )
        self.undo_button = ttk.Button(
            self.actions, text="Undo", command=self._on_undo,
            style="Axle.Toolbar.TButton",
        )
        self.redo_button = ttk.Button(
            self.actions, text="Redo", command=self._on_redo,
            style="Axle.Toolbar.TButton",
        )
        self.export_button = ttk.Button(
            self.actions, text="Export…", command=self._export,
            style="Axle.Toolbar.TButton",
        )
        self.more_menu = tk.Menu(self.actions, tearoff=False)
        self.more_menu.add_command(label="Undo", command=self._on_undo)
        self.more_menu.add_command(label="Redo", command=self._on_redo)
        self.more_menu.add_separator()
        self.more_menu.add_command(
            label="Set physical axle order…",
            command=self._set_physical_axle_order,
        )
        self.more_menu.add_command(
            label="Restore canonical axle order",
            command=self._restore_canonical_axle_order,
        )
        self.more_menu.add_separator()
        self.more_menu.add_command(label="Export…", command=self._export)
        self.more_button = ttk.Menubutton(
            self.actions, text="More…", menu=self.more_menu,
        )
        self.status = tk.StringVar(value="Select a vehicle to inspect its wheel skeleton.")
        self.status_display = tk.StringVar(value=self.status.get())
        self.status_label = ttk.Label(
            self.footer, textvariable=self.status_display, foreground="#52635c",
            wraplength=410, justify="left", anchor="w", width=1,
            font=("Segoe UI", 9),
        )
        self.status_label.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        self._wrap_labels.append(self.status_label)
        self.status.trace_add("write", self._status_changed)
        self.bind("<Configure>", self._panel_configured)
        self._layout_actions(500)
        self._bind_editor_wheel(self.editor_canvas)
        self._bind_editor_wheel(self.editor_content)
        for child in self.editor_content.winfo_children():
            self._bind_editor_wheel_tree(child)
        self._set_enabled(False)

    def _editor_content_configured(self, _event: tk.Event | None = None) -> None:
        bbox = self.editor_canvas.bbox(self._editor_window)
        if bbox is not None:
            self.editor_canvas.configure(scrollregion=bbox)

    def _editor_viewport_configured(self, event: tk.Event) -> None:
        width = max(1, int(event.width))
        self.editor_canvas.itemconfigure(self._editor_window, width=width)
        self._update_wraplengths(width)
        self._layout_actions(width)
        self.after_idle(self._editor_content_configured)

    def _panel_configured(self, event: tk.Event) -> None:
        self._layout_actions(max(1, int(event.width)))

    def _update_wraplengths(self, width: int) -> None:
        # Leave room for the canvas scrollbar plus LabelFrame borders/padding.
        wrap = max(150, int(width) - 38)
        for label in self._wrap_labels:
            label.configure(
                wraplength=0 if label is self.status_label and width < 300 else wrap,
            )
        self.geometry_label.configure(wraplength=max(90, int(width) - 190))
        self._refresh_status_display(width)

    def _status_changed(self, *_args: object) -> None:
        self._refresh_status_display(self.editor_canvas.winfo_width())

    def _refresh_status_display(self, width: int) -> None:
        message = self.status.get()
        if int(width) >= 300:
            self.status_display.set(message)
            return
        validation = re.fullmatch(
            r"(\d+) axle pairs · (\d+) errors · (\d+) warnings · .+\.",
            message,
        )
        if validation is not None:
            pairs, errors, warnings = validation.groups()
            self.status_display.set(
                f"{pairs} pairs · {errors} errors · {warnings} warnings"
            )
            return
        summary = message.split(".", 1)[0].strip()
        if len(summary) > 38:
            summary = summary[:37].rstrip() + "…"
        self.status_display.set(summary)

    def _layout_actions(self, width: int) -> None:
        width = int(width)
        layout = "narrow" if width < 300 else "compact" if width < 430 else "wide"
        if layout == self._action_layout:
            return
        self._action_layout = layout
        for widget in (
            self.filter_button, self.prefab_button, self.visual_button,
            self.apply_button, self.undo_button, self.redo_button,
            self.export_button, self.more_button,
        ):
            widget.grid_forget()
        for column in range(4):
            self.prefab_actions.columnconfigure(column, weight=0, minsize=0)
            self.actions.columnconfigure(column, weight=0, minsize=0)
        if layout == "narrow":
            self.prefab_actions.columnconfigure(0, weight=1)
            self.filter_button.grid(row=0, column=0, sticky="ew")
            self.prefab_button.grid(row=1, column=0, sticky="ew", pady=(5, 0))
            self.visual_button.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        elif layout == "compact":
            for column in range(2):
                self.prefab_actions.columnconfigure(column, weight=1)
            self.filter_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
            self.prefab_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
            self.visual_button.grid(
                row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0),
            )
        else:
            for column in range(3):
                self.prefab_actions.columnconfigure(column, weight=1)
            self.filter_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
            self.prefab_button.grid(row=0, column=1, sticky="ew", padx=3)
            self.visual_button.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        if layout != "wide":
            self.apply_button.configure(text="Apply")
            for column in range(2):
                self.actions.columnconfigure(column, weight=1)
            self.apply_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
            self.more_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
            return
        self.apply_button.configure(text="Apply + validate")
        for column in range(4):
            self.actions.columnconfigure(column, weight=1)
        self.apply_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.undo_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.redo_button.grid(row=0, column=2, sticky="ew", padx=3)
        self.export_button.grid(row=0, column=3, sticky="ew", padx=(3, 0))

    def _bind_editor_wheel_tree(self, widget: tk.Misc) -> None:
        self._bind_editor_wheel(widget)
        for child in widget.winfo_children():
            self._bind_editor_wheel_tree(child)

    def _bind_editor_wheel(self, widget: tk.Misc) -> None:
        # The findings tree keeps its native wheel behavior when validation
        # extends beyond four rows. Everywhere else, the wheel moves the body.
        if widget is self.finding_tree:
            return
        widget.bind("<MouseWheel>", self._scroll_editor, add="+")
        widget.bind("<Button-4>", self._scroll_editor, add="+")
        widget.bind("<Button-5>", self._scroll_editor, add="+")

    def _scroll_editor(self, event: tk.Event) -> str:
        number = getattr(event, "num", 0)
        if number == 4:
            units = -1
        elif number == 5:
            units = 1
        else:
            delta = int(getattr(event, "delta", 0))
            if not delta:
                return "break"
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
        self.editor_canvas.yview_scroll(units, "units")
        return "break"

    def _load_catalogs(self) -> None:
        try:
            from allin1_sdk.axle_prefabs import AxlePrefabCatalog, VisualTyreCatalog

            self._prefab_catalog = AxlePrefabCatalog.load_builtin()
            prefabs = self._prefab_catalog.list_prefabs()
            visuals = VisualTyreCatalog.load_builtin().list_packages()
            self._prefab_ids = {item.display_name: item.id for item in prefabs}
            self._visual_ids = {item.display_name: item.id for item in visuals}
        except (ImportError, OSError, TypeError, ValueError, AttributeError):
            self._prefab_ids = {}
            self._visual_ids = {}
        self.prefab_combo.configure(values=tuple(self._prefab_ids))
        self.visual_combo.configure(values=tuple(self._visual_ids))
        if self._prefab_ids:
            self.prefab.set(next(iter(self._prefab_ids)))
        if self._visual_ids:
            self.visual.set(next(iter(self._visual_ids)))
        self._build_filter_menu()
        self._show_prefab_details()

    def _build_filter_menu(self) -> None:
        self.filter_menu.delete(0, "end")
        catalog = self._prefab_catalog
        if catalog is None:
            self.filter_menu.add_command(label="Catalog unavailable", state="disabled")
            return
        values = {
            "axle_count": ("Any", "2", "3", "4", "5"),
            "layout": ("Any", *sorted({item.nominal_layout for item in catalog.prefabs})),
            "category": ("Any", *sorted({item.category for item in catalog.prefabs})),
            "steering": ("Any", "none", "front", "rear", "multi", "all"),
            "drive": ("Any", "none", "single", "multiple", "all"),
            "lift": ("Any", "Yes", "No"),
            "target": ("Any", *TARGET_LABELS.values()),
            "experimental": ("Any", "Yes", "No"),
        }
        labels = {
            "axle_count": "Axle count", "layout": "Nominal layout",
            "category": "Vehicle category", "steering": "Steering type",
            "drive": "Drive type", "lift": "Lift axle",
            "target": "Target compatibility", "experimental": "Experimental",
        }
        for key, choices in values.items():
            menu = tk.Menu(self.filter_menu, tearoff=False)
            for value in choices:
                menu.add_radiobutton(
                    label=value, value=value, variable=self._filter_values[key],
                    command=self._refresh_prefab_values,
                )
            self.filter_menu.add_cascade(label=labels[key], menu=menu)
        self.filter_menu.add_separator()
        self.filter_menu.add_command(label="Reset filters", command=self._reset_filters)

    def _reset_filters(self) -> None:
        for variable in self._filter_values.values():
            variable.set("Any")
        self._refresh_prefab_values()

    def _refresh_prefab_values(self) -> None:
        catalog = self._prefab_catalog
        if catalog is None:
            return
        def optional(key: str) -> str | None:
            value = self._filter_values[key].get()
            return None if value == "Any" else value

        def optional_bool(key: str) -> bool | None:
            value = optional(key)
            return None if value is None else value == "Yes"

        axle_text = optional("axle_count")
        target_label = optional("target")
        prefabs = catalog.list_prefabs(
            axle_count=int(axle_text) if axle_text else None,
            nominal_layout=optional("layout"), category=optional("category"),
            steering_type=optional("steering"), drive_type=optional("drive"),
            lift_axle=optional_bool("lift"),
            target=TARGET_KEYS.get(target_label, None) if target_label else None,
            experimental=optional_bool("experimental"),
        )
        self._prefab_ids = {item.display_name: item.id for item in prefabs}
        values = tuple(self._prefab_ids)
        self.prefab_combo.configure(values=values)
        self.prefab.set(values[0] if values else "")
        active = sum(value.get() != "Any" for value in self._filter_values.values())
        self.filter_button.configure(text=f"Filters ({active}) ▾" if active else "Filters ▾")
        self._show_prefab_details()

    def _show_prefab_details(self, _event=None) -> None:
        prefab_id = self._prefab_ids.get(self.prefab.get())
        catalog = self._prefab_catalog
        if prefab_id is None or catalog is None:
            self.prefab_details.set("No behavior prefabs match the current filters.")
            return
        try:
            from allin1_sdk.axle_prefabs import calculate_compatibility, schematic_text

            prefab = catalog.get(prefab_id)
            compatibility = calculate_compatibility(prefab, self._target_key())
            badges = [
                "Compatible" if compatibility.requirements_met else "Unsupported",
                compatibility.badge,
            ]
            if compatibility.experimental:
                badges.append("Experimental")
            if compatibility.design_only:
                badges.append("Design only")
            self.prefab_details.set(
                f"{schematic_text(prefab)}  ·  {prefab.pattern}  ·  "
                f"{prefab.common_use}\n{' · '.join(dict.fromkeys(badges))}"
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            self.prefab_details.set("Prefab details are unavailable.")

    def load(
        self,
        model: str,
        config: AxleConfiguration | None,
        *,
        bones: Iterable[NativeModelBone] = (),
        editable: bool,
        asset_names: Iterable[str] = (),
        handling_flags: int | None = None,
        drive_bias_front: float | None = None,
    ) -> None:
        self._model = model
        self._bones = tuple(bones)
        self._asset_names = tuple(asset_names)
        self._handling_flags = handling_flags
        self._drive_bias_front = drive_bias_front
        self._editable = editable
        self._draft = config
        self._steering_solution = None
        if config is not None:
            enabled_targets = [
                target for target, enabled in config.compatibility if enabled
            ]
            if len(enabled_targets) == 1 and enabled_targets[0] in TARGET_LABELS:
                self.target.set(TARGET_LABELS[enabled_targets[0]])
        self._preview_findings = ()
        self._preview_blocked = False
        if self._draft is None and self._bones:
            try:
                self._draft = detect_axle_configuration(
                    model, self._bones, target=self._target_key(),
                )
            except ValueError as exc:
                self.status.set(f"Skeleton detection unavailable: {exc}")
        self._sync_from_draft()
        self._set_enabled(bool(self._draft), editable=editable)

    def set_scene(self, scene: NativeModelScene | None) -> None:
        self._bones = tuple(scene.bones) if scene is not None else ()
        self._steering_solution = None
        if self._model and self._draft is None and self._bones:
            self._detect()
        else:
            self._sync_from_draft()
            self._set_enabled(bool(self._draft), editable=self._editable)

    def clear(self) -> None:
        self._model = ""
        self._bones = ()
        self._draft = None
        self._steering_solution = None
        self.tree.delete(*self.tree.get_children())
        self.finding_tree.delete(*self.finding_tree.get_children())
        self.geometry_summary.set(
            "Uses wheel-bone Y only; tyre appearance stays independent."
        )
        self.status.set("Select a vehicle to inspect its wheel skeleton.")
        self._set_enabled(False)

    def snapshot(self) -> str:
        return json.dumps(self._draft.to_dict(), sort_keys=True) if self._draft else ""

    def configuration(self) -> AxleConfiguration | None:
        return self._draft

    def target_key(self) -> str:
        return self._target_key()

    def _target_key(self) -> str:
        return TARGET_KEYS.get(self.target.get(), "story-legacy")

    def _set_enabled(self, available: bool, *, editable: bool = False) -> None:
        state = "normal" if available and editable else "disabled"
        readonly = "readonly" if available and editable else "disabled"
        for combo in self._setup_combos.values():
            combo.configure(state=readonly)
        for combo in (self.prefab_combo, self.visual_combo):
            combo.configure(state=readonly)
        self.visual_axle_combo.configure(state=readonly)
        for widget in (
            self.preset_button, self.order_button,
            self.prefab_button, self.visual_button,
            self.geometry_button,
            self.apply_button, *self.row_controls,
        ):
            widget.configure(state=state)
        for widget in self._unsupported_brake_controls:
            widget.configure(state="disabled")
        self.detect_button.configure(
            state="normal" if self._bones and editable else "disabled"
        )
        self.geometry_button.configure(
            state="normal" if available and editable and self._bones else "disabled"
        )
        self.order_button.configure(
            state="normal" if available and editable and self._bones else "disabled"
        )
        self.undo_button.configure(state="normal" if editable else "disabled")
        self.redo_button.configure(state="normal" if editable else "disabled")
        self.export_button.configure(state="disabled")
        history_state = "normal" if editable else "disabled"
        self.more_menu.entryconfigure("Undo", state=history_state)
        self.more_menu.entryconfigure("Redo", state=history_state)
        order_state = "normal" if available and editable and self._bones else "disabled"
        self.more_menu.entryconfigure("Set physical axle order…", state=order_state)
        self.more_menu.entryconfigure(
            "Restore canonical axle order",
            state=(
                "normal" if order_state == "normal" and self._draft is not None
                and self._draft.intentional_layout_override is not None
                else "disabled"
            ),
        )
        self.more_menu.entryconfigure("Export…", state="disabled")
        self.more_button.configure(state=history_state)
        if available and self._draft is not None:
            self._validate()

    def _detect(self) -> None:
        if not self._model or not self._bones:
            self.status.set("Load a native vehicle skeleton before axle detection.")
            return
        try:
            self._draft = detect_axle_configuration(
                self._model, self._bones, preset=self.preset.get(),
                export_mode=EXPORT_LABELS.get(self.export_mode.get(), EXPORT_STOCK_METADATA),
                target=self._target_key(),
            )
        except ValueError as exc:
            self.status.set(f"Detection rejected: {exc}")
            return
        self._preview_findings = ()
        self._preview_blocked = False
        self._steering_solution = None
        self._sync_from_draft()
        self._set_enabled(True, editable=self._editable)

    def _configuration_changed(self, event=None) -> None:
        if self._draft is None:
            return
        if event is not None and event.widget is self.preset_combo:
            self._preview_preset()
            return
        self._draft = retarget_axle_configuration(
            replace(self._draft,
            export_mode=EXPORT_LABELS.get(self.export_mode.get(), EXPORT_STOCK_METADATA),
            ), self._target_key(),
        )
        self._preview_findings = ()
        self._preview_blocked = False
        self._show_prefab_details()
        self._validate()

    def _preview_preset(self) -> None:
        if self._draft is None:
            return
        try:
            self._draft = apply_axle_preset(self._draft, self.preset.get())
        except ValueError as exc:
            self.preset.set(self._draft.preset)
            messagebox.showerror("Preset cannot be applied", str(exc), parent=self)
            return
        self._preview_findings = ()
        self._preview_blocked = False
        self._steering_solution = None
        self._sync_from_draft()
        self.status.set("Preset previewed. Review mappings and validation, then apply.")

    def _set_physical_axle_order(self) -> None:
        if self._draft is None or not self._bones or not self._editable:
            return
        current = tuple(
            (axle.left_bone, axle.right_bone)
            for axle in sorted(self._draft.axles, key=lambda item: item.physical_order)
        )
        dialog = _PhysicalAxleOrderDialog(self, current)
        selected = dialog.result
        if selected is None:
            return
        if (
            selected == current
            and self._draft.intentional_layout_override is not None
        ):
            return
        canonical = tuple(
            (axle.left_bone, axle.right_bone)
            for axle in sorted(self._draft.axles, key=lambda item: item.left_runtime_index)
        )
        try:
            if selected == canonical:
                proposed = clear_intentional_layout_override(self._draft)
                status = "Canonical front-to-rear axle order restored."
            else:
                proposed = apply_intentional_layout_override(
                    self._draft,
                    self._bones,
                    physical_bone_pairs=selected,
                    reason=(
                        "Author-confirmed physical order for intentional GTA "
                        "wheel-mesh family instancing"
                    ),
                )
                status = (
                    "Custom physical axle order previewed. Recalculate signed "
                    "steering, review validation, then apply."
                )
        except ValueError as exc:
            messagebox.showerror(
                "Physical order rejected", str(exc), parent=self,
            )
            return
        self._draft = proposed
        self._preview_findings = ()
        self._preview_blocked = False
        self._steering_solution = None
        self._sync_from_draft()
        self._set_enabled(True, editable=True)
        self.status.set(status)

    def _restore_canonical_axle_order(self) -> None:
        if self._draft is None or not self._editable:
            return
        self._draft = clear_intentional_layout_override(self._draft)
        self._preview_findings = ()
        self._preview_blocked = False
        self._steering_solution = None
        self._sync_from_draft()
        self._set_enabled(True, editable=True)
        self.status.set(
            "Canonical physical axle order restored. Review behavior before applying."
        )

    def _calculate_steering(self) -> None:
        """Preview geometry-derived, signed gains without changing tyre visuals."""

        if self._draft is None or not self._bones:
            self.status.set(
                "Load a native vehicle skeleton before calculating steering."
            )
            return

        request = SteeringGeometryRequest()
        if self._draft.axles and all(axle.steered for axle in self._draft.axles):
            pivot = simpledialog.askfloat(
                "Neutral steering pivot",
                "All physical axles steer, so a neutral pivot cannot be inferred.\n\n"
                "Enter the vehicle-local Y coordinate (the same coordinate system "
                "as the wheel bones):",
                parent=self,
            )
            if pivot is None:
                self.status.set(
                    "Steering calculation cancelled; no axle gains were changed."
                )
                return
            request = SteeringGeometryRequest(
                pivot_longitudinal_position=float(pivot)
            )

        try:
            solution = solve_automatic_steering_geometry(
                self._draft, self._bones, request,
            )
            proposed = apply_steering_geometry_to_configuration(
                self._draft, solution,
            )
        except SteeringGeometryError as exc:
            messagebox.showerror(
                "Steering cannot be calculated", str(exc), parent=self,
            )
            self.status.set(f"Steering calculation rejected: {exc}")
            return

        needs_runtime = _requires_selective_steering_runtime(proposed)
        self._draft = replace(
            proposed,
            preset=PRESET_CUSTOM,
            export_mode=(EXPORT_FIVEM_RUNTIME if needs_runtime else proposed.export_mode),
        )
        self._steering_solution = solution
        self._preview_findings = ()
        self._preview_blocked = False
        self.preset.set(PRESET_CUSTOM)
        if needs_runtime:
            self.export_mode.set("Selective runtime")
        self._sync_from_draft()
        self.status.set(
            "Longest steering lever arm normalized to 100%. "
            + (
                "Selective runtime selected for signed/scaled gains; target "
                "validation remains fail-closed."
                if needs_runtime
                else "Review the calculated gains, then apply."
            )
        )

    def _preview_prefab(self) -> None:
        prefab_id = self._prefab_ids.get(self.prefab.get())
        if not prefab_id or not self._model:
            return
        try:
            from allin1_sdk.axle_prefabs import apply_prefab

            preview = apply_prefab(
                prefab_id, self._model, self._bones, self._target_key(),
                EXPORT_LABELS.get(self.export_mode.get(), EXPORT_STOCK_METADATA),
                self._draft, handling_flags=self._handling_flags,
            )
            self._draft = preview.proposed
            self._preview_findings = preview.findings
            self._preview_blocked = not preview.can_apply
            self._steering_solution = None
            if preview.handling_flags_before is not None:
                self.handling_preview.set(
                    "Handling flags preview: "
                    f"0x{preview.handling_flags_before:X} → "
                    f"0x{(preview.handling_flags_after or 0):X}"
                )
        except (ImportError, OSError, TypeError, ValueError, AttributeError) as exc:
            messagebox.showerror("Prefab cannot be previewed", str(exc), parent=self)
            return
        self._sync_from_draft()
        self.status.set("Behavior prefab previewed. Nothing is saved until Apply + validate.")

    def _preview_visual(self) -> None:
        package_id = self._visual_ids.get(self.visual.get())
        if not package_id or self._draft is None:
            return
        try:
            from allin1_sdk.axle_prefabs import apply_visual_package

            selected_text = self.visual_axle.get()
            selected_axles = (
                (int(selected_text.removeprefix("Axle ")),)
                if selected_text.startswith("Axle ") else ()
            )
            preview = apply_visual_package(
                package_id, self._draft, selected_axles=selected_axles,
            )
            self._draft = preview.proposed
            self._preview_findings = preview.findings
            # Missing optional dual-tyre geometry may be preserved as explicit
            # design intent, but the preview never claims that geometry exists.
            self._preview_blocked = not preview.can_persist
        except (ImportError, OSError, TypeError, ValueError, AttributeError) as exc:
            messagebox.showerror("Tyre package cannot be previewed", str(exc), parent=self)
            return
        self._sync_from_draft()
        self.status.set(
            "Tyre package previewed without changing axle behavior. "
            + (
                "Geometry is design-only until verified YDR/YDD/YFT assets are bound."
                if preview.design_only else "Verified geometry bindings are ready."
            )
        )

    def _select_axle(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected or self._draft is None:
            return
        try:
            axle = self._draft.axles[int(selected[0])]
        except (IndexError, ValueError):
            return
        self._loading_row = True
        self.row_steered.set(axle.steered)
        self.row_powered.set(axle.powered)
        self.row_service.set(axle.service_brake)
        self.row_handbrake.set(axle.handbrake)
        self._loading_row = False

    def _update_selected_axle(self) -> None:
        selected = self.tree.selection()
        if not selected or self._draft is None:
            return
        index = int(selected[0])
        self._draft, steering_changed = _edit_axle_controls(
            self._draft,
            index,
            steered=self.row_steered.get(),
            powered=self.row_powered.get(),
            service_brake=self.row_service.get(),
            handbrake=self.row_handbrake.get(),
        )
        self._preview_findings = ()
        self._preview_blocked = False
        self._steering_solution = None
        self.preset.set(PRESET_CUSTOM)
        self._sync_from_draft(select=index)
        if steering_changed:
            self.geometry_summary.set(
                _current_gain_summary(self._draft)
                + " · role changed; calculate steering again"
            )

    def _sync_from_draft(self, *, select: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        if self._draft is None:
            self._validate()
            return
        self.preset.set(self._draft.preset)
        label = next(
            (name for name, value in EXPORT_LABELS.items() if value == self._draft.export_mode),
            "Stock metadata",
        )
        self.export_mode.set(label)
        for index, axle in enumerate(self._draft.axles):
            self.tree.insert("", "end", iid=str(index), values=(
                axle.physical_order,
                axle.logical_role.title(),
                f"{axle.left_bone} / {axle.right_bone}",
                "Yes" if axle.steered else "No",
                _format_steering_gain(axle.steering_gain),
                "Yes" if axle.powered else "No",
                f"{'Y' if axle.service_brake else '–'}/{'Y' if axle.handbrake else '–'}",
                axle.visual_family.replace("_", " "),
                f"{axle.left_runtime_index}, {axle.right_runtime_index}",
            ))
        self.geometry_summary.set(
            _steering_solution_summary(self._steering_solution)
            if self._steering_solution is not None
            else _current_gain_summary(self._draft)
        )
        if self._draft.intentional_layout_override is not None:
            self.geometry_summary.set(
                self.geometry_summary.get() + " · custom physical order"
            )
        if self._draft.axles:
            axle_choices = (
                "All applicable",
                *(f"Axle {item.physical_order}" for item in self._draft.axles),
            )
            self.visual_axle_combo.configure(values=axle_choices)
            if self.visual_axle.get() not in axle_choices:
                self.visual_axle.set("All applicable")
            chosen = select if select is not None else 0
            self.tree.selection_set(str(chosen))
            self.tree.focus(str(chosen))
            self._select_axle()
        self._validate()

    def _validate(self) -> None:
        self.finding_tree.delete(*self.finding_tree.get_children())
        if self._draft is None:
            return
        findings = list(validate_axle_configuration(
            self._draft, self._bones, handling_flags=self._handling_flags,
            asset_names=self._asset_names, target=self._target_key(),
        ))
        deployment_blocked = False
        if _requires_selective_steering_runtime(self._draft):
            try:
                from allin1_sdk.axle_runtime_bundler import target_capabilities

                capability = target_capabilities(self._target_key())
                deployment_blocked = not capability.supports_signed_steering_gain
            except (KeyError, TypeError, ValueError):
                deployment_blocked = True
            if deployment_blocked:
                findings.append(AxleFinding(
                    "warning",
                    "signed_steering_target_unavailable",
                    "Signed steering is saved as authoring data, but this target "
                    "has no validated steering-gain accessor and cannot be exported yet.",
                ))
        existing = {(item.severity, item.code, item.message) for item in findings}
        findings.extend(
            item for item in self._preview_findings
            if (item.severity, item.code, item.message) not in existing
        )
        for index, finding in enumerate(findings):
            self.finding_tree.insert(
                "", "end", iid=f"finding-{index}",
                values=(finding.severity.title(), finding.message),
            )
        errors = sum(item.severity == "error" for item in findings)
        warnings = sum(item.severity == "warning" for item in findings)
        runtime = "authoring only; target runtime unavailable" if deployment_blocked else "runtime required" if any(
            item.code.endswith("runtime_required") for item in findings
        ) else "stock-compatible pattern"
        self.status.set(
            f"{len(self._draft.axles)} axle pairs · {errors} errors · "
            f"{warnings} warnings · {runtime}."
        )
        self.apply_button.configure(
            state=(
                "normal"
                if self._editable and not errors and not self._preview_blocked
                else "disabled"
            )
        )
        export_state = "normal" if (
            not errors and not self._preview_blocked and not deployment_blocked
        ) else "disabled"
        self.export_button.configure(state=export_state)
        self.more_menu.entryconfigure("Export…", state=export_state)
        self.more_button.configure(
            state="normal"
            if self._editable or export_state == "normal"
            else "disabled"
        )
        if self._handling_flags is not None:
            result = stock_metadata_flags(self._draft, self._handling_flags)
            bias_note = ""
            powered_modes = {item.powered for item in self._draft.axles}
            if (
                self._draft.export_mode == EXPORT_FIVEM_RUNTIME
                and len(powered_modes) > 1
                and not (
                    self._drive_bias_front is not None
                    and 0.0 < self._drive_bias_front < 1.0
                )
            ):
                bias_note = " · fDriveBiasFront will be normalized to 0.5"
            self.handling_preview.set(
                f"Handling flags: 0x{self._handling_flags:X} → "
                f"0x{result.updated_flags:X}{bias_note}"
            )

    def _apply(self) -> None:
        if self._draft is not None:
            self._on_apply(self._draft)

    def _export(self) -> None:
        if self._draft is not None:
            self._on_export(self._draft)
