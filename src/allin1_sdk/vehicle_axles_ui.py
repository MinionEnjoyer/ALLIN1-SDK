"""Compact Axle Configurator panel used by the Vehicle Workbench.

The panel edits a draft only.  Its host owns persistence and therefore keeps
the same revision, validation, and undo/redo boundary as every other vehicle
authoring operation.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Iterable, Mapping

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    AXLE_SUPPORT_SCHEMA_VERSION,
    AXLE_SUPPORT_WEIGHT_DEFAULT,
    AXLE_SUPPORT_WEIGHT_MAXIMUM,
    AXLE_SUPPORT_WEIGHT_MINIMUM,
    AXLE_PRESETS,
    EXPORT_FIVEM_RUNTIME,
    EXPORT_STOCK_METADATA,
    PRESET_CUSTOM,
    PRESET_FRONT_STEER,
    PRESET_STEER_DRIVE_REAR,
    STEERING_CALCULATION_AUTOMATIC,
    STEERING_COMMAND_POLARITY_INVERTED,
    STEERING_COMMAND_POLARITY_NORMAL,
    STEERING_POLARITY_SCHEMA_VERSION,
    AxleFinding,
    AxleConfiguration,
    VehicleAxle,
    apply_axle_preset,
    apply_axle_support_weights,
    apply_intentional_layout_override,
    clear_axle_support_weights,
    clear_intentional_layout_override,
    detect_axle_configuration,
    retarget_axle_configuration,
    stock_metadata_flags,
    requires_axle_support_bias,
    requires_signed_steering_gain,
    set_steering_command_polarity,
    validate_axle_configuration,
)
from allin1_sdk.axle_runtime_bundler import story_native_runtime_configuration
from allin1_sdk.axle_steering_geometry import (
    SteeringGeometryError,
    SteeringGeometryRequest,
    SteeringGeometrySolution,
    apply_steering_geometry_to_configuration,
    solve_automatic_steering_geometry,
)
from allin1_sdk.native_assets import NativeModelBone, NativeModelScene
from allin1_sdk.story_axle_runtime_builder import (
    NativeAxleToolchainReport,
    StoryAxleRuntimeBuildRequest,
    StoryAxleRuntimeSettings,
    default_story_axle_runtime_settings,
    inspect_native_axle_toolchain,
    portable_runtime_path,
)
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

CONTROLLER_EDITION_TARGETS = {
    "Legacy + Enhanced": ("story-legacy", "story-enhanced"),
    "Legacy": ("story-legacy",),
    "Enhanced": ("story-enhanced",),
}


@dataclass(frozen=True)
class StoryControllerBuildOptions:
    """User-selected staging options for one validated controller build."""

    targets: tuple[str, ...]
    configuration_directory: str
    log_file: str
    output_directory: Path


def _native_story_export_ready(config: AxleConfiguration | None) -> bool:
    """Return whether the current draft matches the native Story serializer."""

    if config is None or config.export_mode != EXPORT_FIVEM_RUNTIME:
        return False
    story_targets = [
        target for target, enabled in config.compatibility
        if enabled and target in {"story-legacy", "story-enhanced"}
    ]
    return len(story_targets) == 1


def _format_steering_gain(gain: float) -> str:
    """Return the compact signed form used by the resolved-axle table."""

    value = float(gain)
    return "0.00" if abs(value) < 0.0005 else f"{value:+.2f}"


def _steering_solution_summary(
    solution: SteeringGeometrySolution, polarity: str = STEERING_COMMAND_POLARITY_NORMAL,
) -> str:
    """Summarize one geometry proposal without obscuring the axle editor."""

    source = {
        "explicit": "manual pivot",
        "selected_fixed_axles": "selected fixed axle",
        "derived_fixed_axles": "fixed axle",
    }.get(solution.pivot_source, solution.pivot_source.replace("_", " "))
    multiplier = -1.0 if polarity == STEERING_COMMAND_POLARITY_INVERTED else 1.0
    gains = " · ".join(
        f"A{item.physical_order} {_format_steering_gain(item.steering_gain)}"
        + (
            f" → {_format_steering_gain(item.steering_gain * multiplier)}"
            if multiplier < 0.0 else ""
        )
        for item in solution.axles
    )
    return (
        f"Pivot Y {solution.pivot_longitudinal_position:.3f} ({source}) · "
        f"{gains}"
    )


def _current_gain_summary(config: AxleConfiguration) -> str:
    base = " · ".join(
        f"A{axle.physical_order} {_format_steering_gain(axle.steering_gain)}"
        for axle in config.axles
    )
    if config.steering_command_polarity == STEERING_COMMAND_POLARITY_INVERTED:
        effective = " · ".join(
            f"A{axle.physical_order} {_format_steering_gain(-axle.steering_gain)}"
            for axle in config.axles
        )
        return f"Base gains · {base} · inverted effective · {effective}"
    return f"Base steering gains · {base} · normal polarity"


def _requires_selective_steering_runtime(config: AxleConfiguration) -> bool:
    """Return whether signed/scaled gains exceed legacy boolean steering."""

    return requires_signed_steering_gain(config)


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
        support_enabled = bool(rows) and all(
            row.suspension is not None for row in rows
        )
        return (
            replace(
                config,
                schema_version=(
                    STEERING_POLARITY_SCHEMA_VERSION
                    if config.steering_command_polarity
                    == STEERING_COMMAND_POLARITY_INVERTED
                    else AXLE_SUPPORT_SCHEMA_VERSION
                    if support_enabled else AXLE_SCHEMA_VERSION
                ),
                preset=PRESET_CUSTOM,
                axles=tuple(rows),
                steering_calculation=None,
            ),
            True,
        )
    return replace(config, preset=PRESET_CUSTOM, axles=tuple(rows)), False


def _physical_pairs(config: AxleConfiguration) -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.left_bone, item.right_bone)
        for item in sorted(config.axles, key=lambda value: value.physical_order)
    )


def _has_unreviewed_physical_layout(config: AxleConfiguration) -> bool:
    """Return whether detected wheel positions disagree with canonical roles."""

    if config.intentional_layout_override is not None:
        return False
    canonical = _physical_pairs(clear_intentional_layout_override(config))
    return _physical_pairs(config) != canonical


def _guided_physical_layout_configuration(
    config: AxleConfiguration,
    bones: Iterable[NativeModelBone],
) -> tuple[AxleConfiguration, SteeringGeometrySolution]:
    """Build the safe one-click draft for a spatially remapped skeleton.

    Three-axle visual-instancing layouts receive the common steer/drive/
    counter-steer behavior. Other supported layouts receive ordinary physical
    front steering. In both cases, signed geometry is calculated only after
    the exact physical order has been fingerprinted.
    """

    bone_rows = tuple(bones)
    if not _has_unreviewed_physical_layout(config):
        raise ValueError("The detected skeleton does not require a physical-order override")
    remapped = apply_intentional_layout_override(
        config,
        bone_rows,
        physical_bone_pairs=_physical_pairs(config),
        reason=(
            "Workbench-guided physical order for intentional GTA wheel-mesh "
            "family instancing"
        ),
    )
    behavior = (
        PRESET_STEER_DRIVE_REAR
        if len(remapped.axles) == 3 else PRESET_FRONT_STEER
    )
    remapped = replace(
        apply_axle_preset(remapped, behavior),
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    solution = solve_automatic_steering_geometry(remapped, bone_rows)
    configured = replace(
        apply_steering_geometry_to_configuration(remapped, solution),
        preset=behavior,
        export_mode=EXPORT_FIVEM_RUNTIME,
    )
    return configured, solution


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
        on_build_controller: Callable[
            [
                AxleConfiguration,
                tuple[NativeModelBone, ...],
                StoryControllerBuildOptions,
            ],
            None,
        ] | None = None,
        gta_roots: tuple[Path, ...] = (),
        controller_toolchain_inspector: Callable[
            [], NativeAxleToolchainReport
        ] = inspect_native_axle_toolchain,
    ) -> None:
        super().__init__(parent)
        self._on_apply = on_apply
        self._on_undo = on_undo
        self._on_redo = on_redo
        self._on_export = on_export
        self._on_build_controller = on_build_controller
        self._gta_roots = tuple(
            Path(root).expanduser().resolve(strict=False) for root in gta_roots
        )
        self._controller_toolchain_inspector = controller_toolchain_inspector
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
        self._finding_messages: dict[str, str] = {}
        self._wrap_labels: list[ttk.Label] = []
        self._action_layout: str | None = None
        self._controller_build_running = False
        self._controller_paths_model: str | None = None
        self._controller_preflight_running = False
        self._controller_preflight_report: NativeAxleToolchainReport | None = None
        self._controller_preflight_events: queue.SimpleQueue[
            tuple[str, object]
        ] = queue.SimpleQueue()
        self._controller_preflight_thread: threading.Thread | None = None
        self._controller_preflight_poll_job: str | None = None
        self._validation_error_count = 0
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
        self.steering_polarity = tk.StringVar(value="Normal")
        self.prefab = tk.StringVar()
        self.visual = tk.StringVar()
        self.visual_axle = tk.StringVar(value="All applicable")
        self._setup_combos: dict[str, ttk.Combobox] = {}
        for row, (label, variable, values) in enumerate((
            ("Target", self.target, tuple(TARGET_LABELS.values())),
            ("Quick preset", self.preset, AXLE_PRESETS),
            ("Export behavior", self.export_mode, tuple(EXPORT_LABELS)),
            ("Steering polarity", self.steering_polarity, ("Normal", "Inverted")),
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
        self.polarity_combo = self._setup_combos["Steering polarity"]
        setup.columnconfigure(1, weight=1)
        preset_actions = ttk.Frame(setup)
        preset_actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(5, 0))
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
        self.config_menu = tk.Menu(preset_actions, tearoff=False)
        self.config_menu.add_command(
            label="Load axle config…", command=self._import_configuration,
        )
        self.config_menu.add_command(
            label="Save workbench config…", command=self._save_configuration,
        )
        self.config_menu.add_command(
            label="Export native Story config…",
            command=self._save_runtime_configuration,
        )
        self.config_menu.add_separator()
        self.config_menu.add_command(
            label="Build Story controller package…",
            command=self._toggle_controller_builder,
        )
        self.config_button = ttk.Menubutton(
            preset_actions, text="Config ▾", menu=self.config_menu,
        )
        self.config_button.pack(side="left", padx=(5, 0))

        self.guided_setup = ttk.Frame(setup, padding=(6, 5))
        self.guided_setup.grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(7, 0),
        )
        self.guided_setup.columnconfigure(0, weight=1)
        self.guided_setup_text = tk.StringVar()
        guided_label = ttk.Label(
            self.guided_setup, textvariable=self.guided_setup_text,
            foreground="#246b43", wraplength=350, justify="left",
        )
        guided_label.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self._wrap_labels.append(guided_label)
        self.guided_setup_button = ttk.Button(
            self.guided_setup, text="Set up detected layout",
            command=self._configure_detected_layout,
        )
        self.guided_setup_button.grid(row=0, column=1, sticky="e")
        self.guided_setup.grid_remove()

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
            "order", "role", "bones", "steer", "gain", "effective", "drive", "brakes",
            "family", "indices",
        )
        self.tree = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="browse", height=5,
        )
        headings = {
            "order": "#", "role": "Role", "bones": "Canonical bones",
            "steer": "Steer", "gain": "Base gain", "effective": "Effective",
            "drive": "Drive", "brakes": "S/H",
            "family": "Visual family", "indices": "Runtime slots",
        }
        widths = {
            "order": 30, "role": 54, "bones": 148, "steer": 45, "drive": 45,
            "gain": 62, "effective": 62, "brakes": 48, "family": 112, "indices": 78,
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
        self.row_support_enabled = tk.BooleanVar()
        self.row_support_weight = tk.StringVar(
            value=f"{AXLE_SUPPORT_WEIGHT_DEFAULT:.2f}"
        )
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
        support_editor = ttk.Frame(row_editor)
        support_editor.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(3, 1),
        )
        self.row_support_toggle = ttk.Checkbutton(
            support_editor,
            text="Support bias (all axles)",
            variable=self.row_support_enabled,
            command=self._support_state_changed,
        )
        self.row_support_toggle.pack(side="left")
        ttk.Label(support_editor, text="Weight").pack(side="left", padx=(9, 4))
        self.row_support_spin = ttk.Spinbox(
            support_editor,
            from_=AXLE_SUPPORT_WEIGHT_MINIMUM,
            to=AXLE_SUPPORT_WEIGHT_MAXIMUM,
            increment=0.05,
            textvariable=self.row_support_weight,
            width=5,
            format="%.2f",
        )
        self.row_support_spin.pack(side="left")
        self.row_controls.append(self.row_support_toggle)
        self.row_apply = ttk.Button(
            row_editor, text="Update selected", command=self._update_selected_axle,
        )
        self.row_apply.grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(3, 0),
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
        self.finding_detail = tk.StringVar(
            value="Validation details appear here when an issue is selected.",
        )
        finding_detail_label = ttk.Label(
            findings, textvariable=self.finding_detail, foreground="#52635c",
            wraplength=400, justify="left",
        )
        finding_detail_label.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0),
        )
        self._wrap_labels.append(finding_detail_label)
        self.finding_tree.bind(
            "<<TreeviewSelect>>", self._show_selected_finding,
        )

        self.controller_builder = ttk.LabelFrame(
            body, text="5 · Story controller package", padding=7,
        )
        self.controller_builder.columnconfigure(1, weight=1)
        controller_intro = ttk.Label(
            self.controller_builder,
            text=(
                "Compile the global axle controller and include this vehicle's "
                "reviewed config. Build output is staged outside GTA V."
            ),
            foreground="#52635c", wraplength=400, justify="left",
        )
        controller_intro.grid(
            row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5),
        )
        self._wrap_labels.append(controller_intro)
        self.controller_edition = tk.StringVar(value="Legacy + Enhanced")
        self.controller_configuration_directory = tk.StringVar(
            value="VehicleWorkbenchAxles/configs",
        )
        self.controller_log_file = tk.StringVar(
            value="VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log",
        )
        self.controller_output_directory = tk.StringVar()
        for row, (label, variable) in enumerate((
            ("Edition", self.controller_edition),
            ("Config folder", self.controller_configuration_directory),
            ("Log file", self.controller_log_file),
            ("Output folder", self.controller_output_directory),
        ), start=1):
            ttk.Label(self.controller_builder, text=label).grid(
                row=row, column=0, sticky="w", pady=2,
            )
            if label == "Edition":
                control: tk.Widget = ttk.Combobox(
                    self.controller_builder, textvariable=variable,
                    values=tuple(CONTROLLER_EDITION_TARGETS),
                    state="readonly", width=18,
                )
            else:
                control = ttk.Entry(
                    self.controller_builder, textvariable=variable, width=24,
                )
            control.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=2)
            if label == "Config folder":
                self.controller_configuration_entry = control
            elif label == "Log file":
                self.controller_log_entry = control
            if label == "Output folder":
                self.controller_output_entry = control
        self.controller_configuration_button = ttk.Button(
            self.controller_builder, text="Browse…",
            command=self._browse_controller_configuration,
        )
        self.controller_configuration_button.grid(
            row=2, column=2, sticky="e", padx=(5, 0), pady=2,
        )
        self.controller_log_button = ttk.Button(
            self.controller_builder, text="Browse…",
            command=self._browse_controller_log,
        )
        self.controller_log_button.grid(
            row=3, column=2, sticky="e", padx=(5, 0), pady=2,
        )
        self.controller_output_button = ttk.Button(
            self.controller_builder, text="Browse…",
            command=self._browse_controller_output,
        )
        self.controller_output_button.grid(
            row=4, column=2, sticky="e", padx=(5, 0), pady=2,
        )

        preflight = ttk.LabelFrame(
            self.controller_builder, text="Toolchain readiness", padding=6,
        )
        preflight.grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(7, 0),
        )
        preflight.columnconfigure(0, weight=1)
        preflight.rowconfigure(1, weight=1)
        preflight_actions = ttk.Frame(preflight)
        preflight_actions.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.controller_preflight_summary = tk.StringVar(
            value="Not checked · open this panel or choose Recheck.",
        )
        ttk.Label(
            preflight_actions, textvariable=self.controller_preflight_summary,
            foreground="#52635c", anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.controller_recheck_button = ttk.Button(
            preflight_actions, text="Recheck", command=self._start_controller_preflight,
        )
        self.controller_recheck_button.pack(side="right", padx=(6, 0))
        preflight_table = ttk.Frame(preflight)
        preflight_table.grid(row=1, column=0, sticky="nsew")
        preflight_table.columnconfigure(0, weight=1)
        preflight_table.rowconfigure(0, weight=1)
        self.controller_preflight_tree = ttk.Treeview(
            preflight_table, columns=("status", "detected"),
            show="tree headings", height=6, selectmode="browse",
        )
        self.controller_preflight_tree.heading("#0", text="Check")
        self.controller_preflight_tree.heading("status", text="Result")
        self.controller_preflight_tree.heading("detected", text="Detected")
        self.controller_preflight_tree.column(
            "#0", width=105, minwidth=80, stretch=False,
        )
        self.controller_preflight_tree.column(
            "status", width=70, minwidth=62, stretch=False,
        )
        self.controller_preflight_tree.column(
            "detected", width=215, minwidth=120, stretch=True,
        )
        preflight_scroll = ttk.Scrollbar(
            preflight_table, orient="vertical",
            command=self.controller_preflight_tree.yview,
        )
        preflight_xscroll = ttk.Scrollbar(
            preflight_table, orient="horizontal",
            command=self.controller_preflight_tree.xview,
        )
        self.controller_preflight_tree.configure(
            yscrollcommand=preflight_scroll.set,
            xscrollcommand=preflight_xscroll.set,
        )
        self.controller_preflight_tree.grid(row=0, column=0, sticky="nsew")
        preflight_scroll.grid(row=0, column=1, sticky="ns")
        preflight_xscroll.grid(row=1, column=0, sticky="ew")
        self.controller_preflight_guidance = tk.StringVar(
            value=(
                "Build stays locked until the native source, CMake, CTest, and "
                "Visual Studio x64 toolchain all pass."
            ),
        )
        preflight_guidance = ttk.Label(
            preflight, textvariable=self.controller_preflight_guidance,
            foreground="#52635c", wraplength=400, justify="left",
        )
        preflight_guidance.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        self._wrap_labels.append(preflight_guidance)

        controller_actions = ttk.Frame(self.controller_builder)
        controller_actions.grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=(6, 0),
        )
        self.controller_build_button = ttk.Button(
            controller_actions, text="Build validated package",
            command=self._build_story_controller,
            style="Axle.Accent.TButton", state="disabled",
        )
        self.controller_build_button.pack(side="left")
        ttk.Button(
            controller_actions, text="Hide",
            command=self._toggle_controller_builder,
        ).pack(side="right")
        self.controller_build_status = tk.StringVar(
            value=(
                "Run the readiness check, select portable runtime paths, and "
                "resolve validation findings before building."
            ),
        )
        controller_status = ttk.Label(
            self.controller_builder, textvariable=self.controller_build_status,
            foreground="#52635c", wraplength=400, justify="left",
        )
        controller_status.grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(5, 0),
        )
        self._wrap_labels.append(controller_status)
        for variable in (
            self.controller_edition,
            self.controller_configuration_directory,
            self.controller_log_file,
            self.controller_output_directory,
        ):
            variable.trace_add("write", self._controller_form_changed)

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
        target: str | None = None,
    ) -> None:
        previous_defaults = default_story_axle_runtime_settings(
            self._controller_paths_model,
        )
        selected_defaults = default_story_axle_runtime_settings(model)
        if (
            self.controller_configuration_directory.get().strip()
            == previous_defaults.configuration_directory
        ):
            self.controller_configuration_directory.set(
                selected_defaults.configuration_directory,
            )
        if (
            self.controller_log_file.get().strip()
            == previous_defaults.log_file
        ):
            self.controller_log_file.set(selected_defaults.log_file)
        self._controller_paths_model = model
        self._model = model
        self._bones = tuple(bones)
        self._asset_names = tuple(asset_names)
        self._handling_flags = handling_flags
        self._drive_bias_front = drive_bias_front
        self._editable = editable
        self._draft = config
        self._steering_solution = None
        if target in TARGET_LABELS:
            self.target.set(TARGET_LABELS[target])
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
        self._finding_messages.clear()
        self.finding_detail.set(
            "Validation details appear here when an issue is selected."
        )
        self.guided_setup.grid_remove()
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

    def _load_configuration_path(self, path: str | Path) -> AxleConfiguration:
        source = Path(path).expanduser().resolve(strict=True)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Axle configuration could not be read: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Axle configuration root must be an object")
        try:
            from allin1_sdk.axle_prefabs import load_prefab_axle_configuration

            configuration = load_prefab_axle_configuration(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Axle configuration is invalid: {exc}") from exc
        if configuration.vehicle_model != self._model.casefold():
            raise ValueError(
                "Axle configuration belongs to "
                f"'{configuration.vehicle_model}', not '{self._model}'."
            )
        return configuration

    def _import_configuration(self) -> None:
        if not self._model or not self._editable:
            return
        selected = filedialog.askopenfilename(
            parent=self,
            title=f"Load axle configuration for {self._model}",
            filetypes=(
                ("ALLIN1 axle configuration", "*.json"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return
        try:
            configuration = self._load_configuration_path(selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Axle configuration could not be loaded", str(exc), parent=self,
            )
            return
        self._draft = configuration
        self._steering_solution = None
        self._preview_findings = ()
        self._preview_blocked = False
        enabled_targets = [
            key for key, enabled in configuration.compatibility if enabled
        ]
        if len(enabled_targets) == 1 and enabled_targets[0] in TARGET_LABELS:
            self.target.set(TARGET_LABELS[enabled_targets[0]])
        self._sync_from_draft()
        self._set_enabled(True, editable=True)
        self.status.set(
            f"Loaded {Path(selected).name}. Review validation, then Apply to "
            "keep it with this workbench session."
        )

    def _save_configuration(self) -> None:
        if self._draft is None:
            return
        selected = filedialog.asksaveasfilename(
            parent=self,
            title=f"Save Workbench configuration for {self._draft.vehicle_model}",
            defaultextension=".json",
            initialfile=f"{self._draft.vehicle_model}.sdk.axles.json",
            filetypes=(("ALLIN1 Workbench configuration", "*.json"),),
        )
        if not selected:
            return
        destination = Path(selected).expanduser().resolve(strict=False)
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._draft.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            messagebox.showerror(
                "Axle configuration could not be saved", str(exc), parent=self,
            )
            return
        self.status.set(f"Saved portable Workbench configuration: {destination}")

    def _save_runtime_configuration(self) -> None:
        if self._draft is None:
            return
        try:
            payload = story_native_runtime_configuration(
                self._draft, bones=self._bones,
            )
        except ValueError as exc:
            messagebox.showerror(
                "Runtime configuration could not be exported", str(exc), parent=self,
            )
            return
        selected = filedialog.asksaveasfilename(
            parent=self,
            title=(
                "Export native Story runtime configuration for "
                f"{self._draft.vehicle_model}"
            ),
            defaultextension=".json",
            initialfile=f"{self._draft.vehicle_model}.axles.json",
            filetypes=(("ALLIN1 native Story runtime configuration", "*.json"),),
        )
        if not selected:
            return
        destination = Path(selected).expanduser().resolve(strict=False)
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            messagebox.showerror(
                "Runtime configuration could not be saved", str(exc), parent=self,
            )
            return
        self.status.set(
            "Exported controller-ready native Story runtime configuration: "
            f"{destination}"
        )

    def _configure_detected_layout(self) -> None:
        if self._draft is None or not self._bones or not self._editable:
            return
        try:
            proposed, solution = _guided_physical_layout_configuration(
                self._draft, self._bones,
            )
        except (SteeringGeometryError, TypeError, ValueError) as exc:
            messagebox.showerror(
                "Detected layout could not be configured", str(exc), parent=self,
            )
            return
        self._draft = proposed
        self._steering_solution = solution
        self._preview_findings = ()
        self._preview_blocked = False
        self.preset.set(proposed.preset)
        self.export_mode.set("Selective runtime")
        if len(proposed.axles) == 3:
            label = next((
                name for name, prefab_id in self._prefab_ids.items()
                if prefab_id == "6x2_rear_steer_bus"
            ), None)
            if label is not None:
                self.prefab.set(label)
                self._show_prefab_details()
        self._sync_from_draft()
        self._set_enabled(True, editable=True)
        self.status.set(
            "Detected physical order configured with selective runtime and "
            "geometry-derived steering. Test direction in game; use Steering "
            "polarity only if the complete steering command is reversed."
        )

    def _update_guided_setup(self) -> None:
        available = (
            self._draft is not None
            and bool(self._bones)
            and _has_unreviewed_physical_layout(self._draft)
        )
        if available:
            pairs = " → ".join(
                f"{left.removeprefix('wheel_')}/{right.removeprefix('wheel_')}"
                for left, right in _physical_pairs(self._draft)
            )
            behavior = (
                "steer → drive → counter-steer"
                if len(self._draft.axles) == 3 else "physical-front steering"
            )
            self.guided_setup_text.set(
                f"Detected nonstandard physical order: {pairs}. Configure the "
                f"reviewed override, selective runtime, and {behavior} in one step."
            )
            self.guided_setup.grid()
        else:
            self.guided_setup.grid_remove()
        self.guided_setup_button.configure(
            state="normal" if available and self._editable else "disabled",
        )

    def _show_selected_finding(self, _event=None) -> None:
        selected = self.finding_tree.selection()
        if not selected:
            return
        self.finding_detail.set(
            self._finding_messages.get(selected[0], "Validation detail unavailable."),
        )

    @staticmethod
    def _available_controller_output(model: str) -> Path:
        desktop = Path.home() / "Desktop"
        parent = desktop if desktop.is_dir() else Path.home()
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model.strip()).strip("-._")
        stem = f"ALLIN1-Axle-Controller-{slug or 'Vehicle'}"
        candidate = parent / stem
        suffix = 2
        while candidate.exists() or candidate.is_symlink():
            candidate = parent / f"{stem}-{suffix}"
            suffix += 1
        return candidate

    def _toggle_controller_builder(self) -> None:
        if self.controller_builder.winfo_manager():
            self.controller_builder.pack_forget()
            return
        if not self.controller_output_directory.get().strip():
            self.controller_output_directory.set(
                str(self._available_controller_output(self._model)),
            )
        self.controller_builder.pack(
            fill="x", pady=(7, 0), after=self.findings_section,
        )
        self._start_controller_preflight()
        self.after_idle(self._editor_content_configured)

    def _controller_initial_location(self, value: str, *, file: bool) -> Path | None:
        root = next((path for path in self._gta_roots if path.is_dir()), None)
        if root is None:
            return None
        parts = tuple(
            part for part in value.replace("\\", "/").split("/") if part
        )
        candidate = root.joinpath(*parts) if parts else root
        folder = candidate.parent if file else candidate
        while folder != root and not folder.is_dir():
            folder = folder.parent
        return folder if folder.is_dir() else root

    def _apply_portable_runtime_selection(
        self, selection: str, variable: tk.StringVar, label: str,
    ) -> bool:
        try:
            portable = portable_runtime_path(
                Path(selection), self._gta_roots, label,
            )
        except (OSError, TypeError, ValueError) as exc:
            roots = "\n".join(f"• {root}" for root in self._gta_roots)
            messagebox.showerror(
                f"Invalid {label.casefold()}",
                f"{exc}\n\nChoose a location inside a configured GTA root."
                + (f"\n\nConfigured roots:\n{roots}" if roots else "\n\nNo GTA roots are configured."),
                parent=self,
            )
            return False
        variable.set(portable)
        return True

    def _browse_controller_configuration(self) -> None:
        initial = self._controller_initial_location(
            self.controller_configuration_directory.get(), file=False,
        )
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose the runtime config folder inside GTA V",
            initialdir=str(initial) if initial is not None else None,
            mustexist=True,
        )
        if selected:
            self._apply_portable_runtime_selection(
                selected, self.controller_configuration_directory,
                "Configuration directory",
            )

    def _browse_controller_log(self) -> None:
        value = self.controller_log_file.get().strip()
        initial = self._controller_initial_location(value, file=True)
        initial_name = Path(value.replace("\\", "/")).name or "Axles.log"
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Choose the runtime log file inside GTA V",
            initialdir=str(initial) if initial is not None else None,
            initialfile=initial_name,
            defaultextension=".log",
            filetypes=(("Log files", "*.log"), ("All files", "*.*")),
        )
        if selected:
            self._apply_portable_runtime_selection(
                selected, self.controller_log_file, "Log file",
            )

    def _browse_controller_output(self) -> None:
        selected = filedialog.askdirectory(
            parent=self,
            title="Choose a parent folder for the Story controller build",
            mustexist=True,
        )
        if not selected:
            return
        suggested = self._available_controller_output(self._model).name
        self.controller_output_directory.set(str(Path(selected) / suggested))

    def _controller_form_changed(self, *_args: object) -> None:
        self._refresh_controller_build_state()

    def _controller_options_error(self) -> str | None:
        targets = CONTROLLER_EDITION_TARGETS.get(self.controller_edition.get())
        output = self.controller_output_directory.get().strip()
        if targets is None:
            return "Choose Legacy, Enhanced, or both editions."
        if not output:
            return "Choose a new output folder outside GTA V."
        try:
            StoryAxleRuntimeBuildRequest(
                output_directory=Path(output),
                targets=targets,
                settings=StoryAxleRuntimeSettings(
                    configuration_directory=(
                        self.controller_configuration_directory.get().strip()
                    ),
                    log_file=self.controller_log_file.get().strip(),
                ),
                protected_gta_roots=self._gta_roots,
            ).validate()
        except (OSError, TypeError, ValueError) as exc:
            return str(exc)
        return None

    @staticmethod
    def _controller_check_mapping(value: object) -> dict[str, object]:
        if isinstance(value, Mapping):
            return dict(value)
        serializer = getattr(value, "to_dict", None)
        if callable(serializer):
            payload = serializer()
            if isinstance(payload, Mapping):
                return dict(payload)
        try:
            return dict(vars(value))
        except TypeError:
            return {}

    def _start_controller_preflight(self) -> None:
        if self._controller_preflight_running:
            return
        self._controller_preflight_running = True
        self._controller_preflight_report = None
        self._controller_preflight_events = queue.SimpleQueue()
        self.controller_preflight_tree.delete(
            *self.controller_preflight_tree.get_children(),
        )
        self.controller_preflight_tree.insert(
            "", "end", text="Toolchain", values=("CHECKING", "Running complete probe…"),
        )
        self.controller_preflight_summary.set("CHECKING · native toolchain preflight")
        self.controller_preflight_guidance.set(
            "Checking the bundled source, platform, CMake, CTest, and Visual Studio x64 toolchain."
        )
        self.controller_recheck_button.configure(state="disabled")
        self._refresh_controller_build_state()
        events = self._controller_preflight_events

        def worker() -> None:
            try:
                report = self._controller_toolchain_inspector()
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                events.put(("error", exc))
            else:
                events.put(("complete", report))

        self._controller_preflight_thread = threading.Thread(
            target=worker,
            name="allin1-story-controller-preflight",
            daemon=True,
        )
        self._controller_preflight_thread.start()
        self._schedule_controller_preflight_poll()

    def _schedule_controller_preflight_poll(self) -> None:
        if self._controller_preflight_poll_job is None:
            self._controller_preflight_poll_job = self.after(
                40, self._poll_controller_preflight,
            )

    def _poll_controller_preflight(self) -> None:
        self._controller_preflight_poll_job = None
        terminal = False
        while True:
            try:
                kind, value = self._controller_preflight_events.get_nowait()
            except queue.Empty:
                break
            terminal = True
            if kind == "complete":
                self._apply_controller_preflight_report(value)
            else:
                self._controller_preflight_report = None
                self.controller_preflight_tree.delete(
                    *self.controller_preflight_tree.get_children(),
                )
                self.controller_preflight_tree.insert(
                    "", "end", text="Toolchain",
                    values=("BLOCKED", f"Probe failed: {value}"),
                )
                self.controller_preflight_summary.set("BLOCKED · preflight failed")
                self.controller_preflight_guidance.set(
                    f"Recheck after repairing the SDK toolchain probe: {value}"
                )
        thread = self._controller_preflight_thread
        if not terminal and thread is not None and thread.is_alive():
            self._schedule_controller_preflight_poll()
            return
        self._controller_preflight_running = False
        self._controller_preflight_thread = None
        self.controller_recheck_button.configure(state="normal")
        self._refresh_controller_build_state()

    def _apply_controller_preflight_report(self, report: object) -> None:
        self._controller_preflight_report = report  # type: ignore[assignment]
        serializer = getattr(report, "to_dict", None)
        payload = serializer() if callable(serializer) else vars(report)
        if not isinstance(payload, Mapping):
            payload = {}
        self.controller_preflight_tree.delete(
            *self.controller_preflight_tree.get_children(),
        )
        raw_checks = payload.get("checks", getattr(report, "checks", ()))
        checks = raw_checks if isinstance(raw_checks, (list, tuple)) else ()
        rendered = 0
        for index, raw in enumerate(checks):
            check = self._controller_check_mapping(raw)
            label = str(
                check.get("label") or check.get("name")
                or check.get("id") or f"Check {index + 1}"
            )
            passed = check.get("passed", check.get("ready"))
            status = str(check.get("status") or check.get("result") or (
                "READY" if passed is True else "BLOCKED" if passed is False else "INFO"
            )).upper()
            detected_parts: list[str] = []
            for key in ("version", "detected", "path", "detail", "evidence"):
                value = check.get(key)
                if value not in (None, "", (), []):
                    text = str(value)
                    if text not in detected_parts:
                        detected_parts.append(text)
            self.controller_preflight_tree.insert(
                "", "end", iid=f"controller-check-{index}", text=label,
                values=(status, " · ".join(detected_parts) or "—"),
            )
            rendered += 1
        if not rendered:
            fallback = (
                (
                    "Bundled source",
                    bool(
                        payload.get("source_root")
                        and Path(str(payload["source_root"])).is_dir()
                    ),
                    payload.get("source_root"),
                ),
                ("Platform", payload.get("platform") == "nt", payload.get("platform")),
                (
                    "CMake", bool(payload.get("cmake_path") and payload.get("cmake_version")),
                    " · ".join(str(value) for value in (
                        payload.get("cmake_version"), payload.get("cmake_path"),
                    ) if value),
                ),
                (
                    "CTest", bool(payload.get("ctest_path")),
                    " · ".join(str(value) for value in (
                        payload.get("ctest_version"), payload.get("ctest_path"),
                    ) if value),
                ),
                (
                    "Visual Studio", bool(
                        payload.get("visual_studio_path") and payload.get("cmake_generator")
                    ),
                    " · ".join(str(value) for value in (
                        payload.get("visual_studio_version"),
                        payload.get("cmake_generator"),
                        payload.get("visual_studio_path"),
                    ) if value),
                ),
            )
            for index, (label, passed, detected) in enumerate(fallback):
                self.controller_preflight_tree.insert(
                    "", "end", iid=f"controller-check-{index}", text=label,
                    values=("READY" if passed else "BLOCKED", detected or "Not detected"),
                )
        ready = bool(getattr(report, "ready", payload.get("ready", False)))
        self.controller_preflight_tree.insert(
            "", "end", text="Overall",
            values=("READY" if ready else "BLOCKED", "All required checks passed" if ready else "Repair required"),
        )
        problems = tuple(getattr(report, "problems", payload.get("problems", ())) or ())
        guidance = getattr(report, "guidance", payload.get("guidance", ())) or ()
        if isinstance(guidance, str):
            guidance_lines = (guidance,)
        else:
            guidance_lines = tuple(str(value) for value in guidance)
        if not guidance_lines:
            guidance_lines = tuple(str(value) for value in problems)
        if not guidance_lines and ready:
            guidance_lines = (
                "Native build prerequisites are ready. In-game acceptance remains a separate test.",
            )
        self.controller_preflight_summary.set(
            "READY · complete toolchain passed"
            if ready else f"BLOCKED · {max(1, len(problems))} issue(s)"
        )
        self.controller_preflight_guidance.set("\n".join(guidance_lines))

    def _refresh_controller_build_state(self) -> None:
        report = self._controller_preflight_report
        toolchain_ready = bool(report is not None and report.ready)
        options_error = self._controller_options_error()
        ready = (
            self._on_build_controller is not None
            and self._draft is not None
            and _native_story_export_ready(self._draft)
            and self._validation_error_count == 0
            and not self._preview_blocked
            and toolchain_ready
            and options_error is None
            and not self._controller_preflight_running
            and not self._controller_build_running
        )
        self.controller_build_button.configure(
            state="normal" if ready else "disabled",
        )
        if self._controller_build_running:
            return
        if self._controller_preflight_running:
            self.controller_build_status.set(
                "Build locked while the complete toolchain preflight runs…",
            )
        elif report is None:
            self.controller_build_status.set(
                "Build locked: run Recheck and wait for every toolchain check to pass.",
            )
        elif not toolchain_ready:
            self.controller_build_status.set(
                "Build locked: repair the blocked toolchain checks shown above.",
            )
        elif options_error is not None:
            self.controller_build_status.set(f"Build locked: {options_error}")
        elif self._draft is None or not _native_story_export_ready(self._draft):
            self.controller_build_status.set(
                "Build locked: select a native Story target and Selective runtime behavior.",
            )
        elif self._validation_error_count or self._preview_blocked:
            self.controller_build_status.set(
                "Build locked: resolve the axle validation findings above.",
            )
        else:
            self.controller_build_status.set(
                "Ready to build a validated candidate outside GTA V. In-game acceptance remains separate.",
            )

    def _build_story_controller(self) -> None:
        self._refresh_controller_build_state()
        if self.controller_build_button.instate(["disabled"]):
            return
        configuration = self._draft
        callback = self._on_build_controller
        if configuration is None or callback is None:
            self.controller_build_status.set(
                "The Story controller builder is unavailable in this host.",
            )
            return
        target_label = self.controller_edition.get()
        targets = CONTROLLER_EDITION_TARGETS.get(target_label)
        output = self.controller_output_directory.get().strip()
        if targets is None or not output:
            self.controller_build_status.set(
                "Choose an edition and a new output folder before building.",
            )
            return
        options = StoryControllerBuildOptions(
            targets=targets,
            configuration_directory=(
                self.controller_configuration_directory.get().strip()
            ),
            log_file=self.controller_log_file.get().strip(),
            output_directory=Path(output).expanduser().resolve(strict=False),
        )
        self._controller_build_running = True
        self.controller_build_status.set("Preparing validated native build…")
        self._refresh_controller_build_state()
        try:
            callback(configuration, self._bones, options)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.controller_build_finished(False, f"Build rejected: {exc}")

    def controller_build_progress(self, message: str) -> None:
        if self._controller_build_running:
            self.controller_build_status.set(str(message))

    def controller_build_finished(self, succeeded: bool, message: str) -> None:
        self._controller_build_running = False
        self._refresh_controller_build_state()
        self.controller_build_status.set(str(message))
        if succeeded:
            self.status.set("Story controller package built and validated.")

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
        self._support_state_changed()
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
        self.config_button.configure(
            state="normal" if available and editable else "disabled",
        )
        self.config_menu.entryconfigure(
            "Load axle config…",
            state="normal" if self._model and editable else "disabled",
        )
        self.config_menu.entryconfigure(
            "Save workbench config…",
            state="normal" if available else "disabled",
        )
        self.config_menu.entryconfigure(
            "Export native Story config…",
            state=(
                "normal"
                if available and _native_story_export_ready(self._draft)
                else "disabled"
            ),
        )
        self.config_menu.entryconfigure(
            "Build Story controller package…",
            state=(
                "normal"
                if available and self._on_build_controller is not None
                else "disabled"
            ),
        )
        self._refresh_controller_build_state()
        self._update_guided_setup()
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
        if event is not None and event.widget is self.polarity_combo:
            try:
                self._draft = set_steering_command_polarity(
                    self._draft,
                    STEERING_COMMAND_POLARITY_INVERTED
                    if self.steering_polarity.get() == "Inverted"
                    else STEERING_COMMAND_POLARITY_NORMAL,
                )
            except ValueError as exc:
                messagebox.showerror(
                    "Steering polarity rejected", str(exc), parent=self,
                )
                self._sync_from_draft()
                return
            self._preview_findings = ()
            self._preview_blocked = False
            self._sync_from_draft()
            self.status.set(
                "Steering command polarity set to "
                f"{self._draft.steering_command_polarity}. Base gains and "
                "physical axle order were not changed."
            )
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
        canonical = _physical_pairs(
            clear_intentional_layout_override(self._draft)
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
        self.row_support_enabled.set(axle.suspension is not None)
        self.row_support_weight.set(
            f"{(axle.suspension.support_weight if axle.suspension else AXLE_SUPPORT_WEIGHT_DEFAULT):.2f}"
        )
        self._loading_row = False
        self._support_state_changed()

    def _support_state_changed(self) -> None:
        state = (
            "normal"
            if self._editable and self._draft is not None
            and self.row_support_enabled.get()
            else "disabled"
        )
        self.row_support_spin.configure(state=state)

    def _update_selected_axle(self) -> None:
        selected = self.tree.selection()
        if not selected or self._draft is None:
            return
        index = int(selected[0])
        try:
            weight = (
                float(self.row_support_weight.get())
                if self.row_support_enabled.get() else None
            )
            candidate, steering_changed = _edit_axle_controls(
                self._draft,
                index,
                steered=self.row_steered.get(),
                powered=self.row_powered.get(),
                service_brake=self.row_service.get(),
                handbrake=self.row_handbrake.get(),
            )
            if weight is not None:
                selected_order = candidate.axles[index].physical_order
                weights = {
                    axle.physical_order: (
                        axle.suspension.support_weight
                        if axle.suspension is not None
                        else AXLE_SUPPORT_WEIGHT_DEFAULT
                    )
                    for axle in candidate.axles
                }
                weights[selected_order] = weight
                candidate = apply_axle_support_weights(candidate, weights)
            elif requires_axle_support_bias(candidate):
                candidate = clear_axle_support_weights(candidate)
        except (TypeError, ValueError) as exc:
            messagebox.showerror(
                "Axle support weight is invalid", str(exc), parent=self,
            )
            self._sync_from_draft(select=index)
            return
        self._draft = candidate
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
        self.steering_polarity.set(
            "Inverted"
            if self._draft.steering_command_polarity
            == STEERING_COMMAND_POLARITY_INVERTED
            else "Normal"
        )
        for index, axle in enumerate(self._draft.axles):
            self.tree.insert("", "end", iid=str(index), values=(
                axle.physical_order,
                axle.logical_role.title(),
                f"{axle.left_bone} / {axle.right_bone}",
                "Yes" if axle.steered else "No",
                _format_steering_gain(axle.steering_gain),
                _format_steering_gain(
                    -axle.steering_gain
                    if self._draft.steering_command_polarity
                    == STEERING_COMMAND_POLARITY_INVERTED
                    else axle.steering_gain
                ),
                "Yes" if axle.powered else "No",
                f"{'Y' if axle.service_brake else '–'}/{'Y' if axle.handbrake else '–'}",
                axle.visual_family.replace("_", " "),
                f"{axle.left_runtime_index}, {axle.right_runtime_index}",
            ))
        self.geometry_summary.set(
            _steering_solution_summary(
                self._steering_solution,
                self._draft.steering_command_polarity,
            )
            if self._steering_solution is not None
            else _current_gain_summary(self._draft)
        )
        if self._draft.intentional_layout_override is not None:
            self.geometry_summary.set(
                self.geometry_summary.get() + " · custom physical order"
            )
        self._update_guided_setup()
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
        self._finding_messages.clear()
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
                    "has no validated steering-gain accessor, so a deployable "
                    "runtime bundle cannot be built yet. The portable native "
                    "configuration can still be exported for review.",
                ))
        if requires_axle_support_bias(self._draft):
            try:
                from allin1_sdk.axle_runtime_bundler import target_capabilities

                capability = target_capabilities(self._target_key())
                support_blocked = not capability.supports_axle_support_bias
            except (KeyError, TypeError, ValueError):
                support_blocked = True
            deployment_blocked = deployment_blocked or support_blocked
            if support_blocked:
                findings.append(AxleFinding(
                    "warning",
                    "axle_support_target_unavailable",
                    "Axle support bias is saved as experimental authoring data, "
                    "but this target has no validated per-wheel support accessor "
                    "so a deployable runtime bundle cannot be built yet. The "
                    "portable native configuration can still be exported for review.",
                ))
        calculation = self._draft.steering_calculation
        if (
            self._target_key().startswith("story-")
            and calculation is not None
            and calculation.mode == STEERING_CALCULATION_AUTOMATIC
        ):
            try:
                from allin1_sdk.axle_runtime_bundler import target_capabilities

                capability = target_capabilities(self._target_key())
                position_blocked = not capability.supports_wheel_local_position
            except (KeyError, TypeError, ValueError):
                position_blocked = True
            deployment_blocked = deployment_blocked or position_blocked
            if position_blocked:
                findings.append(AxleFinding(
                    "warning",
                    "wheel_local_position_target_unavailable",
                    "Automatic steering geometry is saved as authoring data, "
                    "but this Story target has no validated wheel-local-position "
                    "accessor, so a deployable runtime bundle cannot be built yet. "
                    "The portable native configuration can still be exported for "
                    "review.",
                ))
        existing = {(item.severity, item.code, item.message) for item in findings}
        findings.extend(
            item for item in self._preview_findings
            if (item.severity, item.code, item.message) not in existing
        )
        for index, finding in enumerate(findings):
            item_id = f"finding-{index}"
            detail = f"{finding.severity.title()}: {finding.message}"
            self._finding_messages[item_id] = detail
            self.finding_tree.insert(
                "", "end", iid=item_id,
                values=(finding.severity.title(), finding.message),
            )
        errors = sum(item.severity == "error" for item in findings)
        warnings = sum(item.severity == "warning" for item in findings)
        self._validation_error_count = errors
        runtime = "authoring only; target runtime unavailable" if deployment_blocked else "runtime required" if any(
            item.code.endswith("runtime_required") for item in findings
        ) else "stock-compatible pattern"
        summary = (
            f"{len(self._draft.axles)} axle pairs · {errors} errors · "
            f"{warnings} warnings · {runtime}."
        )
        first_error = next(
            (item for item in findings if item.severity == "error"), None,
        )
        self.status.set(
            summary
            + (f" First error: {first_error.message}" if first_error else "")
        )
        first_finding = next((
            f"finding-{index}" for index, item in enumerate(findings)
            if item.severity == "error"
        ), "finding-0" if findings else "")
        if first_finding:
            self.finding_tree.selection_set(first_finding)
            self.finding_tree.focus(first_finding)
            self.finding_tree.see(first_finding)
            self.finding_detail.set(self._finding_messages[first_finding])
        else:
            self.finding_detail.set("No validation findings for this axle draft.")
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
        self._refresh_controller_build_state()

    def _apply(self) -> None:
        if self._draft is not None:
            self._on_apply(self._draft)

    def _export(self) -> None:
        if self._draft is not None:
            self._on_export(self._draft)
