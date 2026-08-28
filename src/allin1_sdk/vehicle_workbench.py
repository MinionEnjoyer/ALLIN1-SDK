"""Integrated vehicle asset workbench and diagnostic viewport."""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk, UnidentifiedImageError

from allin1_sdk import __version__

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageScan,
    package_member_path,
)
from allin1_sdk.collapsible_panes import CollapsibleSidePanes
from allin1_sdk.compiled_render_ui import CompiledRenderPanel, RenderSettings
from allin1_sdk.compiled_render import (
    BlenderInstallation,
    CompiledRenderError,
    CompiledRenderProgress,
    CompiledRenderResult,
    CompiledRenderSettings,
    compile_vehicle_render,
    detect_blender,
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
from allin1_sdk.axle_configurator import (
    AxleConfiguration,
    parse_handling_flags,
    write_fivem_resource,
)
from allin1_sdk.axle_prefabs import load_prefab_axle_configuration
from allin1_sdk.vehicle_axles_ui import VehicleAxlesPanel
from allin1_sdk.vehicle_oiv_ui import VehicleOivExportDialog, VehicleOivForm
from allin1_sdk.axle_oiv_export import (
    MODE_RUNTIME_ONLY,
    MODE_SELF_CONTAINED,
    EnhancedOivTargetProfile,
    JsonOivIdentityStore,
    LegacyOivTargetProfile,
    OivContentPlanner,
    OivExportRequest,
    OivPackageBuilder,
    OivPackageMetadata,
    StagedAxleConfiguration,
    StagedRuntime,
    StagedVehicleDlc,
)
from allin1_sdk.axle_runtime_bundler import (
    StoryRuntimeProfile,
    VehicleAxleBuildInput,
    compatibility_configuration,
)
from allin1_sdk.viewport_rendering import (
    LatestOnlyRenderWorker,
    ViewportRenderKey,
    WeightedLruCache,
    encoded_image_weight,
)
from allin1_sdk.paths import user_data_root
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.vehicle_authoring import (
    TUNING_COLLECTIONS,
    TUNING_FIELDS,
    VMT_TYPES,
    VehicleAuthoringWorkspace,
    VehicleTuningAsset,
    VehicleTuningEntry,
)
from allin1_sdk.vehicle_catalog import (
    STORAGE_KINDS,
    VEHICLE_CATEGORIES,
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
INTERACTIVE_ORBIT_TRIANGLE_BUDGET = 4_000
ORBIT_FINAL_SETTLE_MS = 60


@dataclass(frozen=True)
class _DecodedNativeModel:
    reader: PackageAssetReader
    scene: NativeModelScene


@dataclass(frozen=True)
class _PreparedViewportFrame:
    """Fully decoded viewport pixels safe to hand from a worker to Tk.

    ``display_image`` is an optional, interaction-only resize prepared off the
    UI thread.  Static/final frames retain ``source_image`` so zooming still
    has the complete renderer output available.
    """

    source_image: Image.Image
    metadata: dict[str, object]
    display_image: Image.Image | None = None
    display_zoom: float | None = None


class _PreparedStoryExport:
    """Own temporary staging until a preview is built or discarded."""

    def __init__(
        self,
        temporary: tempfile.TemporaryDirectory[str],
        builder: OivPackageBuilder,
        request: OivExportRequest,
        output: Path,
        *,
        enhanced_fallback: bool,
    ) -> None:
        self.temporary = temporary
        self.builder = builder
        self.request = request
        self.output = output
        self.enhanced_fallback = enhanced_fallback

    def __call__(self) -> Path:
        try:
            if self.enhanced_fallback:
                return self.builder.build_enhanced_fallback(
                    self.request, self.output,
                )
            return self.builder.build(self.request, self.output).archive
        finally:
            self.temporary.cleanup()

    def __del__(self) -> None:
        try:
            self.temporary.cleanup()
        except (OSError, PermissionError):
            pass


def _model_render_view_box(
    image: Image.Image, metadata: dict[str, object],
) -> tuple[int, int, int, int] | None:
    """Return the renderer's validated chrome-free view box, when present."""
    box = metadata.get("model_render_view_box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in box):
        return None
    left, top, right, bottom = box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        return None
    return left, top, right, bottom


def _decode_native_model_scene(
    reader: PackageAssetReader | None, source: Path, path: str, entry_size: int, *,
    project_root: Path, game_path: Path | None, edition: str,
) -> _DecodedNativeModel:
    """Read and decode one package model without depending on Tk state."""
    loaded_reader = reader or (
        PackageAssetReader(
            source, project_root=project_root, gta_path=game_path,
        )
        if source.suffix.casefold() == ".rpf"
        else PackageAssetReader(source)
    )
    content = loaded_reader.read(
        path, limit=native_preview_limit(path, entry_size),
    )
    report = NativeAssetInspector(project_root, game_path).inspect_bytes(
        package_member_path(path).name, content.data,
        edition=edition, truncated=content.truncated,
    )
    if report.model_scene is None:
        warning = "; ".join(report.warnings) or "No renderable geometry was found."
        raise ValueError(warning)
    return _DecodedNativeModel(loaded_reader, report.model_scene)


def _crop_model_render(
    image: Image.Image, metadata: dict[str, object],
) -> Image.Image:
    """Crop a renderer's baked chrome when it supplies a validated view box."""
    box = _model_render_view_box(image, metadata)
    return image.crop(box) if box is not None else image


def _prepare_viewport_frame(
    encoded: bytes, metadata: dict[str, object], *,
    quality: str, zoom: float,
) -> _PreparedViewportFrame:
    """Decode/crop and optionally resize one frame away from the Tk thread."""
    if not isinstance(metadata, dict):
        raise TypeError("model renderer returned invalid metadata")
    with Image.open(io.BytesIO(encoded)) as opened:
        # Crop before RGB conversion. Renderer chrome is not shown in the
        # workbench, so converting those pixels just burns CPU and memory.
        box = _model_render_view_box(opened, metadata)
        visible = opened.crop(box) if box is not None else opened
        source = visible.convert("RGB")
    return _prepare_viewport_image(
        source, metadata, quality=quality, zoom=zoom, already_cropped=True,
    )


def _prepare_viewport_image(
    image: Image.Image, metadata: dict[str, object], *,
    quality: str, zoom: float, already_cropped: bool = False,
) -> _PreparedViewportFrame:
    """Prepare a renderer-owned PIL image without an encode/decode round trip."""
    if not isinstance(metadata, dict):
        raise TypeError("model renderer returned invalid metadata")
    visible = image if already_cropped else _crop_model_render(image, metadata)
    source = visible if visible.mode == "RGB" else visible.convert("RGB")
    display: Image.Image | None = None
    display_zoom: float | None = None
    if quality == "interactive":
        target = (
            max(1, round(source.width * zoom)),
            max(1, round(source.height * zoom)),
        )
        display = (
            source if target == source.size
            else source.resize(target, Image.Resampling.BILINEAR)
        )
        display_zoom = zoom
    return _PreparedViewportFrame(source, metadata, display, display_zoom)


def _viewport_frame_weight(frame: _PreparedViewportFrame) -> int:
    """Bound cached decoded frames by their approximate resident pixel size."""
    def weight(image: Image.Image | None) -> int:
        return 0 if image is None else image.width * image.height * len(image.getbands())

    return weight(frame.source_image) + weight(frame.display_image)


def _viewport_render_weight(value: object) -> int:
    if isinstance(value, _PreparedViewportFrame):
        return _viewport_frame_weight(value)
    return encoded_image_weight(value)


class _PageStack(ttk.Frame):
    """Tabless page host used where nested notebook headers consume the editor."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._pages: list[tk.Misc] = []
        self._labels: dict[str, str] = {}
        self._selected = ""
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        # A hidden page can have a much larger requested size than the host.
        # Keep that request from expanding the complete workbench; the active
        # page is laid out in the stack's actual, resizable grid cell instead.
        self.grid_propagate(False)

    def add(self, page: tk.Misc, *, text: str) -> None:
        page.grid(row=0, column=0, sticky="nsew")
        key = str(page)
        self._pages.append(page)
        self._labels[key] = text
        if not self._selected:
            self.select(page)

    def select(self, page: tk.Misc | str | None = None) -> str:
        if page is None:
            return self._selected
        key = str(page)
        if key not in self._labels:
            return self._selected
        page_widget = next(item for item in self._pages if str(item) == key)
        page_widget.tkraise()
        self._selected = key
        return key

    def tab(self, page: tk.Misc | str, option: str) -> str:
        return self._labels[str(page)] if option == "text" else ""


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
        show_context_header: bool = True,
        show_open_control: bool = True,
    ) -> None:
        super().__init__(parent)
        # The unified workbench embeds this frame beside its category rail.
        # Keep large child requests from expanding the page beyond that host;
        # fill/expand still grows the frame on larger windows.
        self.configure(width=720, height=400)
        self.pack_propagate(False)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = tuple(
            Path(root).expanduser().resolve() for root in installation_roots
        )
        self._on_help = on_help
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self._show_context_header = show_context_header
        self._show_open_control = show_open_control
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.project: VehicleProject | None = None
        self.reader: PackageAssetReader | None = None
        self.models: dict[str, VehicleProjectModel] = {}
        self.project_assets: dict[str, str] = {}
        self.selected_model: VehicleProjectModel | None = None
        self.authoring_workspace: VehicleAuthoringWorkspace | None = None
        # Direct RPFs remain immutable, but axle behavior can be authored as a
        # portable sidecar. Keep applied drafts alive while the RPF is open so
        # users can move between vehicles without losing reviewed work.
        self._session_axle_configurations: dict[str, AxleConfiguration] = {}
        self.authoring_values: dict[str, tk.StringVar] = {}
        self.authoring_inputs: dict[str, ttk.Entry] = {}
        self.author_view = tk.StringVar(value="general")
        self.distribution_values: dict[str, tk.Variable] = {}
        self.distribution_inputs: list[tk.Widget] = []
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
        self._scene_revision = 0
        self._active_scene_key: tuple[int, str] = (0, "")
        self._viewport_photo: ImageTk.PhotoImage | None = None
        self._viewport_photo_zoom: float | None = None
        self._viewport_canvas_size: tuple[int, int] = (0, 0)
        self._viewport_canvas_items: dict[str, int] = {}
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_origin: tuple[int, int] | None = None
        self._drag_pan: tuple[float, float] | None = None
        self._orbit_origin: tuple[int, int] | None = None
        self._orbit_camera: tuple[float, float] | None = None
        self._orbit_render_dirty = False
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self._fragment_paths: dict[str, str] = {}
        self._render_job: str | None = None
        self._final_render_job: str | None = None
        self._render_poll_job: str | None = None
        self._render_generation = 0
        self._render_fit_generation: int | None = None
        self._scene_load_poll_job: str | None = None
        self._scene_load_generation = 0
        self._scene_load_key: tuple[int, str] | None = None
        self._scene_load_path: str | None = None
        self._compiled_render_executable = self._load_compiled_render_executable()
        self._compiled_render_installation: BlenderInstallation | None = None
        self._compiled_render_path_error = ""
        self._compiled_render_cancel_event: threading.Event | None = None
        self._compiled_render_thread: threading.Thread | None = None
        self._compiled_render_events: queue.SimpleQueue[tuple[str, object]] = (
            queue.SimpleQueue()
        )
        self._compiled_render_poll_job: str | None = None
        self._viewport_scene_worker = LatestOnlyRenderWorker(
            thread_name="allin1-viewport-scene-loader",
        )
        self._viewport_render_worker = LatestOnlyRenderWorker(
            cache=WeightedLruCache(
                maximum_entries=12, maximum_weight=24 * 1024 * 1024,
                weigh=_viewport_render_weight,
            ),
        )
        self._primary_pane_balance_job: str | None = None
        self._tuning_pane_balance_job: str | None = None
        self._loaded_editor_snapshot: tuple[object, ...] | None = None
        self._restoring_model_selection = False
        self._build()
        self.bind("<Destroy>", self._destroy_viewport_renderer, add="+")

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=(10, 7, 10, 9))
        outer.pack(fill="both", expand=True)

        if self._show_context_header:
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
        self.vehicle_toolbar = toolbar
        toolbar.pack(fill="x", pady=(0, 10))
        if self._show_open_control:
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
        self.vehicle_status_label = ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            anchor="w", justify="left",
        )
        self.vehicle_status_label.pack(fill="x", pady=(0, 6))

        panes = ttk.Panedwindow(outer, orient="horizontal", width=720)
        self.primary_panes = panes
        panes.pack(fill="both", expand=True)
        side_panes = CollapsibleSidePanes(
            panes, left_width=150, center_width=330, right_width=320,
            left_weight=2, center_weight=5, right_weight=3,
            left_label="Vehicles", right_label="Resolved project",
        )
        self.primary_side_panes = side_panes
        model_panel = ttk.LabelFrame(
            side_panes.left_host, text="Vehicles", padding=9,
        )
        viewport_panel = ttk.LabelFrame(
            side_panes.center_host, text="Model viewport", padding=9,
        )
        inspector_panel = ttk.LabelFrame(
            side_panes.right_host, text="Resolved project", padding=9,
        )
        self.catalog_panel = model_panel
        self.work_panel = viewport_panel
        self.integration_panel = inspector_panel
        # Panedwindow otherwise treats the full requested widths of every
        # nested toolbar and editor as hard pane minima. Explicit responsive
        # requests let the panes fit the host while their packed children
        # continue to fill the actual allocated size.
        for panel in (model_panel, viewport_panel, inspector_panel):
            panel.pack_propagate(False)
        side_panes.set_contents(model_panel, viewport_panel, inspector_panel)
        panes.bind("<Configure>", self._balance_primary_panes)

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

        # Treat the preview like a real viewport: one compact dark command
        # strip owns rendering, model isolation, camera, and framing.  The
        # previous four rows of ordinary form controls consumed more vertical
        # space than the model at compact window sizes.
        viewport_surface = tk.Frame(
            viewport_panel, background="#101714", borderwidth=0,
            highlightthickness=0,
        )
        viewport_surface.pack(fill="both", expand=True)
        viewport_toolbar = tk.Frame(
            viewport_surface, background="#141f1a", borderwidth=0,
            highlightthickness=0,
        )
        viewport_toolbar.pack(fill="x")

        menu_options = {
            "tearoff": False,
            "background": "#18231e",
            "foreground": "#dce8e1",
            "activebackground": "#1f7f42",
            "activeforeground": "#ffffff",
            "selectcolor": "#1f7f42",
            "borderwidth": 0,
            "relief": "flat",
            "font": ("Segoe UI", 9),
        }

        def dark_menu(parent: tk.Misc) -> tk.Menu:
            return tk.Menu(parent, **menu_options)

        def strip_menu_button(
            *, text: str | None = None, textvariable: tk.StringVar | None = None,
            menu: tk.Menu, width: int = 0,
        ) -> tk.Menubutton:
            button = tk.Menubutton(
                viewport_toolbar, text=text, textvariable=textvariable,
                menu=menu, width=width, anchor="w", indicatoron=False,
                background="#141f1a", foreground="#dce8e1",
                activebackground="#234b34", activeforeground="#ffffff",
                relief="flat", borderwidth=0, highlightthickness=0,
                padx=7, pady=3, cursor="hand2", takefocus=True,
                font=("Segoe UI Semibold", 9),
            )
            button.pack(side="left", fill="y")
            return button

        self.render_mode = tk.StringVar(value="Shaded")
        self.render_mode_label = tk.StringVar(value="Shaded ▾")
        self.render_mode_menu = dark_menu(viewport_toolbar)
        for mode in ("Shaded", "Materials", "Wireframe"):
            self.render_mode_menu.add_radiobutton(
                label=mode, variable=self.render_mode, value=mode,
                command=self._select_render_mode,
            )
        self.render_mode_menu.add_separator()
        self.render_mode_menu.add_command(
            label="Render full-quality frame",
            command=self._render_full_quality,
        )
        self.render_mode_menu.add_separator()
        self.render_mode_menu.add_command(
            label="Compiled render…",
            command=self._show_compiled_render,
        )
        self.render_mode_button = strip_menu_button(
            textvariable=self.render_mode_label, menu=self.render_mode_menu, width=9,
        )

        self.fragment = tk.StringVar(value="Primary")
        self.lod = tk.StringVar(value="All")
        self.component = tk.StringVar(value="All")
        self.model_filter_menu = dark_menu(viewport_toolbar)
        self.fragment_menu = dark_menu(self.model_filter_menu)
        self.lod_menu = dark_menu(self.model_filter_menu)
        self.component_menu = dark_menu(self.model_filter_menu)
        self.model_filter_menu.add_cascade(label="Fragment", menu=self.fragment_menu)
        self.model_filter_menu.add_cascade(label="LOD", menu=self.lod_menu)
        self.model_filter_menu.add_cascade(label="Component", menu=self.component_menu)
        self.model_filter_button = strip_menu_button(
            text="Model ▾", menu=self.model_filter_menu, width=7,
        )
        self._populate_viewport_menu(
            self.fragment_menu, self.fragment, (), self._select_fragment,
        )
        self._populate_viewport_menu(
            self.lod_menu, self.lod, ("All",), self._select_lod,
        )
        self._populate_viewport_menu(
            self.component_menu, self.component, ("All",), self._select_component,
        )

        camera_menu = dark_menu(viewport_toolbar)
        for label, yaw, pitch in (
            ("Perspective", 34.0, 24.0), ("Front", 0.0, 0.0),
            ("Rear", 180.0, 0.0), ("Left", 270.0, 0.0),
            ("Right", 90.0, 0.0), ("Top", 0.0, 89.0),
        ):
            camera_menu.add_command(
                label=label,
                command=lambda y=yaw, p=pitch: self._set_camera_pose(y, p),
            )
        camera_menu.add_separator()
        for label, yaw, pitch in (
            ("Rotate left", -15.0, 0.0), ("Rotate right", 15.0, 0.0),
            ("Tilt up", 0.0, 10.0), ("Tilt down", 0.0, -10.0),
        ):
            camera_menu.add_command(
                label=label,
                command=lambda y=yaw, p=pitch: self._rotate_camera(y, p),
            )
        camera_menu.add_separator()
        camera_menu.add_command(label="Reset camera", command=self._reset_camera)
        self.camera_menu = camera_menu
        self.camera_menu_button = strip_menu_button(
            text="View ▾", menu=camera_menu, width=6,
        )

        tk.Frame(viewport_toolbar, background="#26362f", width=1).pack(
            side="left", fill="y", pady=5,
        )
        self.fit_button = tk.Button(
            viewport_toolbar, text="Fit", command=self._fit_viewport,
            background="#141f1a", foreground="#dce8e1",
            activebackground="#234b34", activeforeground="#ffffff",
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=7, pady=3, cursor="hand2", takefocus=True,
            font=("Segoe UI Semibold", 9),
        )
        self.fit_button.pack(side="left", fill="y")
        self.zoom_label = tk.Button(
            viewport_toolbar, text="100%", width=5, command=self._reset_zoom,
            background="#141f1a", foreground="#8fb9a2",
            activebackground="#234b34", activeforeground="#ffffff",
            relief="flat", borderwidth=0, highlightthickness=0,
            padx=4, pady=3, cursor="hand2", takefocus=True,
            font=("Segoe UI", 8),
        )
        self.zoom_label.pack(side="right", fill="y")

        self.component_summary = tk.StringVar(
            value="Select a rendered component to inspect its material and texture links."
        )

        self.viewport = tk.Canvas(
            viewport_surface, background="#101714", highlightthickness=0,
            cursor="fleur", takefocus=True,
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
        self.viewport.bind("<Double-Button-1>", self._fit_viewport_event)
        self.viewport.bind("<Double-Button-3>", self._reset_camera_event)
        self.viewport.bind("<KeyPress-f>", self._fit_viewport_event)
        self.viewport.bind("<KeyPress-F>", self._fit_viewport_event)
        self.viewport.bind("<KeyPress-0>", self._reset_zoom_event)
        self.viewport.bind("<KeyPress-plus>", self._zoom_in_event)
        self.viewport.bind("<KeyPress-equal>", self._zoom_in_event)
        self.viewport.bind("<KeyPress-minus>", self._zoom_out_event)
        self.viewport.bind("<KeyPress-r>", self._reset_camera_event)
        self.viewport.bind("<KeyPress-R>", self._reset_camera_event)
        self.viewport_message = tk.StringVar(
            value="Select a vehicle to load its native model preview."
        )
        # The production-render drawer is intentionally lazy. Most authoring
        # sessions never use Blender, so its richer control surface should not
        # add dozens of hidden widgets to every workbench instance.
        self._compiled_render_parent = viewport_surface
        self.compiled_render_panel: CompiledRenderPanel | None = None

        self.model_heading = tk.StringVar(value="No vehicle selected")
        self.model_summary = tk.StringVar(value="No package loaded")
        self.model_heading_label = ttk.Label(
            inspector_panel, textvariable=self.model_heading,
            font=("Segoe UI Semibold", 13), foreground="#1f7f42",
        )
        self.model_heading_label.pack(anchor="w")
        self.model_summary_label = ttk.Label(
            inspector_panel, textvariable=self.model_summary,
            foreground="#52635c", wraplength=330, justify="left",
        )
        self.model_summary_label.pack(fill="x", anchor="w", pady=(3, 9))

        inspector_tabs = ttk.Notebook(inspector_panel)
        self.inspector_tabs = inspector_tabs
        inspector_tabs.pack(fill="both", expand=True)
        overview_tab = ttk.Frame(inspector_tabs, padding=7)
        author_tab = ttk.Frame(inspector_tabs, padding=7)
        self.author_tab = author_tab
        distribution_tab = ttk.Frame(inspector_tabs, padding=7)
        appearance_tab = ttk.Frame(inspector_tabs, padding=7)
        tuning_builder_tab = ttk.Frame(inspector_tabs, padding=7)
        assets_tab = ttk.Frame(inspector_tabs, padding=7)
        inspector_tabs.add(overview_tab, text="Overview")
        inspector_tabs.add(author_tab, text="Author")
        inspector_tabs.add(distribution_tab, text="Distribution")
        inspector_tabs.add(appearance_tab, text="Appearance")
        inspector_tabs.add(tuning_builder_tab, text="Tuning Builder")
        inspector_tabs.add(assets_tab, text="Assets")
        self.tuning_builder_tab = tuning_builder_tab
        inspector_tabs.bind("<<NotebookTabChanged>>", self._inspector_tab_changed)

        self.details = tk.Text(
            overview_tab, wrap="word", relief="flat",
            background="#f4f7f5", foreground="#26332e", padx=8, pady=8,
        )
        self.details.pack(fill="both", expand=True)
        self.details.configure(state="disabled")

        author_switch = ttk.Frame(author_tab)
        author_switch.pack(fill="x", pady=(0, 6))
        for label, value in (("General", "general"), ("Axles", "axles")):
            ttk.Radiobutton(
                author_switch, text=label, value=value, variable=self.author_view,
                command=self._show_author_view, style="Toolbutton",
            ).pack(side="left", padx=(0, 4))
        self.author_general_page = ttk.Frame(author_tab)
        self.author_axles_page = ttk.Frame(author_tab)
        self.author_general_page.pack(fill="both", expand=True)

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
        field_grid = ttk.Frame(self.author_general_page)
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
        author_actions = ttk.Frame(self.author_general_page)
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
            self.author_general_page, textvariable=self.authoring_status,
            foreground="#52635c", wraplength=320, justify="left",
        ).pack(fill="x", anchor="w", pady=(4, 0))

        identity = ttk.LabelFrame(
            self.author_general_page, text="Identity migration", padding=6,
        )
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

        self.axles_panel = VehicleAxlesPanel(
            self.author_axles_page,
            on_apply=self._apply_axle_configuration,
            on_undo=self._undo_authoring_edit,
            on_redo=self._redo_authoring_edit,
            on_export=self._export_axle_configuration,
        )
        self.axles_panel.pack(fill="both", expand=True)

        distribution = ttk.LabelFrame(
            distribution_tab, text="GBAY vehicle listing", padding=8,
        )
        distribution.pack(fill="x")
        self.distribution_values = {
            "listed": tk.BooleanVar(value=True),
            "name": tk.StringVar(),
            "manufacturer": tk.StringVar(),
            "category": tk.StringVar(value="special"),
            "price": tk.StringVar(value="0"),
            "storage": tk.StringVar(value="garage"),
            "size_tier": tk.StringVar(value="0"),
            "preview_dictionary": tk.StringVar(),
            "preview_texture": tk.StringVar(),
            "traffic_enabled": tk.BooleanVar(value=False),
            "traffic_weight": tk.StringVar(value="1.0"),
        }
        listed = ttk.Checkbutton(
            distribution, text="List in GBAY",
            variable=self.distribution_values["listed"], state="disabled",
        )
        listed.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.distribution_inputs.append(listed)
        fields = (
            ("Name", "name"), ("Manufacturer", "manufacturer"),
            ("Price", "price"), ("Preview dictionary", "preview_dictionary"),
            ("Preview texture", "preview_texture"),
            ("Traffic weight", "traffic_weight"),
        )
        for index, (label, key) in enumerate(fields, start=1):
            ttk.Label(distribution, text=label).grid(
                row=index, column=0, sticky="w", pady=2,
            )
            entry = ttk.Entry(
                distribution, textvariable=self.distribution_values[key],
                state="disabled",
            )
            entry.grid(row=index, column=1, sticky="ew", padx=(7, 0), pady=2)
            self.distribution_inputs.append(entry)
        option_row = len(fields) + 1
        for offset, (label, key, values) in enumerate((
            ("Category", "category", tuple(sorted(VEHICLE_CATEGORIES))),
            ("Storage", "storage", tuple(sorted(STORAGE_KINDS))),
            ("Size tier", "size_tier", ("0", "1", "2")),
        )):
            ttk.Label(distribution, text=label).grid(
                row=option_row + offset, column=0, sticky="w", pady=2,
            )
            combo = ttk.Combobox(
                distribution, textvariable=self.distribution_values[key],
                values=values, state="disabled",
            )
            combo.grid(
                row=option_row + offset, column=1, sticky="ew", padx=(7, 0), pady=2,
            )
            self.distribution_inputs.append(combo)
        traffic = ttk.Checkbutton(
            distribution,
            text="Eligible for ambient traffic (package setting stays off by default)",
            variable=self.distribution_values["traffic_enabled"], state="disabled",
        )
        traffic.grid(
            row=option_row + 3, column=0, columnspan=2, sticky="w", pady=(7, 2),
        )
        self.distribution_inputs.append(traffic)
        distribution.columnconfigure(1, weight=1)
        self.save_distribution_button = ttk.Button(
            distribution_tab, text="Apply distribution + validate", state="disabled",
            command=self._save_distribution,
        )
        self.save_distribution_button.pack(anchor="w", pady=(8, 0))
        self.distribution_status = tk.StringVar(
            value="Create an authoring workspace to configure distribution."
        )
        ttk.Label(
            distribution_tab, textvariable=self.distribution_status,
            foreground="#52635c", wraplength=330, justify="left",
        ).pack(fill="x", pady=(5, 0))

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
        self.asset_tree.bind("<Return>", self._open_selected_asset)

        self._render_viewport()

    def _build_tuning_builder(self, parent: ttk.Frame) -> None:
        chooser = ttk.Frame(parent)
        chooser.pack(fill="x", pady=(0, 6))
        ttk.Label(chooser, text="Kit").grid(row=0, column=0, sticky="w")
        self.builder_kit = tk.StringVar()
        self.builder_kit_combo = ttk.Combobox(
            chooser, textvariable=self.builder_kit, state="readonly", width=7,
        )
        self.builder_kit_combo.grid(
            row=1, column=0, sticky="ew", padx=(0, 5), pady=(2, 0),
        )
        self.builder_kit_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_builder_kit,
        )
        ttk.Label(chooser, text="Group").grid(row=0, column=1, sticky="w")
        self.builder_collection = tk.StringVar(value="Visible parts")
        self.builder_collection_combo = ttk.Combobox(
            chooser, textvariable=self.builder_collection, state="readonly",
            values=tuple(TUNING_COLLECTION_LABELS), width=8,
        )
        self.builder_collection_combo.grid(
            row=1, column=1, sticky="ew", padx=(0, 5), pady=(2, 0),
        )
        self.builder_collection_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_collection,
        )
        ttk.Label(chooser, text="View").grid(row=0, column=2, sticky="w")
        self.tuning_view = tk.StringVar(value="Parts and fields")
        self.tuning_view_combo = ttk.Combobox(
            chooser, textvariable=self.tuning_view, state="readonly", width=8,
            values=("Parts and fields", "Assets and checks"),
        )
        self.tuning_view_combo.grid(
            row=1, column=2, sticky="ew", pady=(2, 0),
        )
        self.tuning_view_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_view,
        )
        for column in range(3):
            chooser.columnconfigure(column, weight=1)
        self.tuning_builder_summary = tk.StringVar(
            value="Create an authoring workspace to build tuning parts."
        )
        pages = _PageStack(parent)
        pages.pack(fill="both", expand=True)
        self.tuning_pages = pages
        parts_page = ttk.Frame(pages, padding=5)
        validation_page = ttk.Frame(pages, padding=5)
        self.tuning_parts_page = parts_page
        self.tuning_validation_page = validation_page
        pages.add(parts_page, text="Parts and fields")
        pages.add(validation_page, text="Assets and checks")

        parts_split = ttk.Panedwindow(parts_page, orient="horizontal")
        self.tuning_parts_split = parts_split
        parts_split.pack(fill="both", expand=True)
        list_frame = ttk.LabelFrame(
            parts_split, text="Kit entries", padding=5, width=135,
        )
        editor_host = ttk.Frame(parts_split, width=180)
        list_frame.pack_propagate(False)
        editor_host.pack_propagate(False)
        parts_split.add(list_frame, weight=2)
        parts_split.add(editor_host, weight=3)
        parts_split.bind("<Configure>", self._balance_tuning_parts)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        list_tree_host = ttk.Frame(list_frame)
        list_tree_host.grid(row=0, column=0, sticky="nsew")
        self.tuning_part_tree = ttk.Treeview(
            list_tree_host, columns=("type",), show="tree headings", height=6,
            selectmode="browse",
        )
        self.tuning_part_tree.heading("#0", text="Entry")
        self.tuning_part_tree.heading("type", text="Type")
        self.tuning_part_tree.column("#0", width=155, minwidth=100)
        self.tuning_part_tree.column("type", width=125, minwidth=80)
        part_scroll = ttk.Scrollbar(
            list_tree_host, orient="vertical", command=self.tuning_part_tree.yview,
        )
        self.tuning_part_tree.configure(yscrollcommand=part_scroll.set)
        self.tuning_part_tree.pack(side="left", fill="both", expand=True)
        part_scroll.pack(side="right", fill="y")
        self.tuning_part_tree.bind(
            "<<TreeviewSelect>>", self._select_tuning_builder_entry,
        )

        actions = ttk.Frame(list_frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        actions.columnconfigure(0, weight=1)
        self.tuning_entry_action_menu = tk.Menu(actions, tearoff=False)
        self.tuning_entry_action_menu.add_command(
            label="New entry", command=self._show_tuning_create,
        )
        self.tuning_entry_action_menu.add_separator()
        self.tuning_entry_action_menu.add_command(
            label="Copy selected", command=self._duplicate_tuning_builder_entry,
        )
        self.tuning_entry_action_menu.add_command(
            label="Delete selected", command=self._remove_tuning_builder_entry,
        )
        self.tuning_entry_action_menu.add_separator()
        self.tuning_entry_action_menu.add_command(
            label="Move up", command=lambda: self._move_tuning_builder_entry(-1),
        )
        self.tuning_entry_action_menu.add_command(
            label="Move down", command=lambda: self._move_tuning_builder_entry(1),
        )
        self.tuning_entry_actions_button = ttk.Menubutton(
            actions, text="Entry actions…", state="disabled",
            menu=self.tuning_entry_action_menu,
        )
        self.tuning_entry_actions_button.grid(row=0, column=0, sticky="ew")

        self.tuning_editor_tabs = _PageStack(editor_host)
        self.tuning_editor_tabs.pack(fill="both", expand=True)
        create_page = ttk.Frame(self.tuning_editor_tabs, padding=1)
        fields_page = ttk.Frame(self.tuning_editor_tabs, padding=1)
        self.tuning_create_page = create_page
        self.tuning_fields_page = fields_page
        self.tuning_editor_tabs.add(create_page, text="Add entry")
        self.tuning_editor_tabs.add(fields_page, text="Edit fields")
        create = ttk.Frame(create_page)
        create.pack(fill="both", expand=True)
        self.tuning_primary_label = ttk.Label(create, text="Model asset")
        self.tuning_primary_label.grid(row=0, column=0, sticky="w")
        self.tuning_new_primary = tk.StringVar()
        self.tuning_primary_entry = ttk.Entry(
            create, textvariable=self.tuning_new_primary, state="disabled", width=8,
        )
        self.tuning_primary_entry.grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(5, 0),
        )
        self.tuning_secondary_label = ttk.Label(create, text="Shop label")
        self.tuning_secondary_label.grid(row=1, column=0, sticky="w")
        self.tuning_new_secondary = tk.StringVar()
        self.tuning_secondary_entry = ttk.Entry(
            create, textvariable=self.tuning_new_secondary, state="disabled", width=8,
        )
        self.tuning_secondary_entry.grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(5, 0),
        )
        self.tuning_type_label = ttk.Label(create, text="Type")
        self.tuning_type_label.grid(row=2, column=0, sticky="w")
        self.tuning_new_type = tk.StringVar(value="VMT_SPOILER")
        self.tuning_new_type_combo = ttk.Combobox(
            create, textvariable=self.tuning_new_type, state="disabled",
            values=VMT_TYPES, width=6,
        )
        self.tuning_new_type_combo.grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(5, 0),
        )
        self.tuning_add_button = ttk.Button(
            create, text="Add + validate", state="disabled",
            command=self._add_tuning_builder_entry,
        )
        self.tuning_add_button.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0),
        )
        create.columnconfigure(1, weight=1)

        fields = ttk.Frame(fields_page)
        fields.pack(fill="both", expand=True)
        self.tuning_field_tree = ttk.Treeview(
            fields, columns=("value",), show="tree headings", height=1,
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
            editor, textvariable=self.tuning_field, state="readonly", width=8,
        )
        self.tuning_field_combo.bind(
            "<<ComboboxSelected>>", self._change_tuning_builder_field,
        )
        self.tuning_field_value = tk.StringVar()
        self.tuning_field_value_entry = ttk.Entry(
            editor, textvariable=self.tuning_field_value, width=8,
            state="disabled",
        )
        self.tuning_field_button = ttk.Button(
            editor, text="Apply field", state="disabled",
            command=self._apply_tuning_builder_field,
        )
        # Pack the fixed action first so it remains reachable when the
        # inspector pane is narrowed; the two editors share what remains.
        self.tuning_field_button.pack(side="right", padx=(5, 0))
        self.tuning_field_combo.pack(side="left", fill="x", expand=True)
        self.tuning_field_value_entry.pack(
            side="left", fill="x", expand=True, padx=(5, 0),
        )

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
        self.tuning_asset_tree.bind("<Return>", self._open_tuning_asset)
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
        self.tuning_finding_tree.bind("<Return>", self._open_tuning_finding)
        self._change_tuning_collection()

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("vehicle-workbench")

    def _balance_primary_panes(self, _event: tk.Event | None = None) -> None:
        """Keep all three primary panes inside the resized workbench."""

        if self._primary_pane_balance_job is not None:
            self.after_cancel(self._primary_pane_balance_job)
        self._primary_pane_balance_job = self.after_idle(
            self._apply_primary_pane_balance,
        )

    def _apply_primary_pane_balance(self) -> None:
        self._primary_pane_balance_job = None
        width = self.primary_panes.winfo_width()
        if width <= 480:
            return
        if self.primary_side_panes.has_collapsed_side:
            self.primary_side_panes.enforce_layout()
            return
        # Use the live width rather than the Configure event width. Multiple
        # resize events can be queued while Tk is still resolving requested
        # pane sizes, and applying an older width pushes the inspector outside
        # the application window.
        model_end = max(125, min(round(width * 0.18), width - 560))
        inspector_start = max(
            model_end + 280,
            min(round(width * 0.62), width - 300),
        )
        try:
            self.primary_panes.sashpos(0, model_end)
            self.primary_panes.sashpos(1, inspector_start)
        except tk.TclError:
            return
        self.primary_side_panes.remember_expanded_widths()

    def _balance_tuning_parts(self, _event: tk.Event | None = None) -> None:
        if self._tuning_pane_balance_job is not None:
            self.after_cancel(self._tuning_pane_balance_job)
        self._tuning_pane_balance_job = self.after_idle(
            self._apply_tuning_pane_balance,
        )

    def _apply_tuning_pane_balance(self) -> None:
        self._tuning_pane_balance_job = None
        width = self.tuning_parts_split.winfo_width()
        if width <= 180:
            return
        try:
            self.tuning_parts_split.sashpos(
                0, max(86, min(round(width * 0.40), width - 110)),
            )
        except tk.TclError:
            return

    def _inspector_tab_changed(self, _event: object | None = None) -> None:
        selected = self.inspector_tabs.select()
        axle_compact = (
            selected == str(self.author_tab)
            and self.author_view.get() == "axles"
        )
        compact = selected == str(self.tuning_builder_tab) or axle_compact
        if axle_compact:
            self.vehicle_toolbar.pack_forget()
        elif not self.vehicle_toolbar.winfo_manager():
            self.vehicle_toolbar.pack(
                fill="x", pady=(0, 10), before=self.primary_panes,
            )
        if compact:
            self.model_heading_label.pack_forget()
            self.model_summary_label.pack_forget()
            self.vehicle_status_label.pack_forget()
            return
        if not self.vehicle_status_label.winfo_manager():
            self.vehicle_status_label.pack(
                fill="x", pady=(0, 6), before=self.primary_panes,
            )
        if not self.model_heading_label.winfo_manager():
            self.model_heading_label.pack(
                anchor="w", before=self.inspector_tabs,
            )
        if not self.model_summary_label.winfo_manager():
            self.model_summary_label.pack(
                fill="x", anchor="w", pady=(3, 9), before=self.inspector_tabs,
            )

    def _open_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open DLC RPF…", command=self._choose_rpf)
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

    def _choose_rpf(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select a vehicle DLC RPF",
            filetypes=(("GTA V RPF", "*.rpf"), ("All files", "*.*")),
        )
        if selected:
            self.open_source(selected)

    def open_source(
        self, source: str | Path, scan: PackageScan | None = None,
        *, authoring_workspace: VehicleAuthoringWorkspace | None = None,
    ) -> None:
        self._cancel_scene_load()
        self._cancel_scene_render()
        self.status.set("Resolving vehicle project…")
        self.update_idletasks()
        try:
            loaded_scan = scan or AddonPackageInspector(
                self.project_root, self._inspection_game_path(),
            ).inspect(source)
            project = VehicleProjectResolver.inspect_scan(loaded_scan)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open vehicle package", str(exc), parent=self)
            self.status.set("Vehicle package could not be opened.")
            return
        self.source = Path(source).expanduser().resolve()
        self.scan = loaded_scan
        self.project = project
        # PackageAssetReader may enumerate external archives at construction.
        # Create it with the first scene request on the loader thread instead.
        self.reader = None
        self.authoring_workspace = authoring_workspace
        self._session_axle_configurations.clear()
        self._scene_cache.clear()
        self._scene_revision += 1
        self._active_scene_key = (self._scene_revision, "")
        self._render_generation = self._viewport_render_worker.invalidate(
            clear_cache=True,
        )
        self._model_scene = None
        self._set_compiled_render_scene_available(False)
        self.models.clear()
        self.model_tree.delete(*self.model_tree.get_children())
        for index, model in enumerate(project.models):
            item_id = f"model:{index}"
            self.models[item_id] = model
            state = "Ready" if model.complete else "Review"
            self.model_tree.insert("", "end", iid=item_id, text=model.model, values=(state,))
        self.export_button.configure(state="normal")
        sealed_rpf = loaded_scan.source_kind == "rpf"
        self.author_button.configure(
            state="disabled" if authoring_workspace is not None or sealed_rpf else "normal",
            text=(
                "Authoring workspace active" if authoring_workspace is not None
                else "Extract RPF before authoring" if sealed_rpf
                else "Create authoring workspace…"
            ),
        )
        self.package_button.configure(
            state="disabled" if sealed_rpf else "normal",
            text=(
                "Use Quick Import for RPF" if sealed_rpf
                else "Build installable package…"
            ),
        )
        self.status.set(
            f"{len(project.models)} vehicles · {project.error_count} errors · "
            f"{project.warning_count} warnings"
            + (" · direct RPF inspection" if sealed_rpf else "")
        )
        if project.models:
            self.model_tree.selection_set("model:0")
            self.model_tree.focus("model:0")
            self._select_model()
        else:
            self._clear_model("No vehicles.meta records were found in this package.")

    def select_model(self, model_name: str) -> bool:
        """Select one resolved model when another SDK workspace routes to it."""
        match = next((
            item_id for item_id, model in self.models.items()
            if model.model.casefold() == model_name.casefold()
        ), None)
        if match is None:
            return False
        self.model_tree.selection_set(match)
        self.model_tree.focus(match)
        self.model_tree.see(match)
        return self._select_model()

    def show_axle_configurator(self, model_name: str | None = None) -> bool:
        """Open the Author/Axles work zone for a selected vehicle model."""
        if model_name is not None and not self.select_model(model_name):
            return False
        if self.selected_model is None:
            return False
        self.inspector_tabs.select(self.author_tab)
        self.author_view.set("axles")
        self._show_author_view()
        return True

    def _select_model(self, _event: object | None = None) -> bool:
        if self._restoring_model_selection:
            return False
        selection = self.model_tree.selection()
        model = self.models.get(selection[0]) if selection else None
        if model is None:
            return False
        previous = self.selected_model
        if (
            previous is not None and previous.model != model.model
            and not self.confirm_navigation()
        ):
            previous_id = next((
                item_id for item_id, candidate in self.models.items()
                if candidate.model == previous.model
            ), None)
            if previous_id is not None:
                self._restoring_model_selection = True
                try:
                    self.model_tree.selection_set(previous_id)
                    self.model_tree.focus(previous_id)
                finally:
                    self._restoring_model_selection = False
            return False
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
        self._populate_viewport_menu(
            self.fragment_menu, self.fragment, fragment_values, self._select_fragment,
        )
        if fragment_values:
            self.fragment.set(fragment_values[0])
        self._load_authoring_fields(model)
        self._loaded_editor_snapshot = self._editor_snapshot()
        self._load_model_preview(model)
        return True

    def _clear_model(self, message: str) -> None:
        self.selected_model = None
        self._cancel_scene_load()
        self._cancel_scene_render()
        self.model_heading.set("No vehicle selected")
        self.model_summary.set(message)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", message)
        self.details.configure(state="disabled")
        self.asset_tree.delete(*self.asset_tree.get_children())
        self.project_assets.clear()
        self.open_asset_button.configure(state="disabled")
        self._source_image = None
        self._model_scene = None
        self._set_compiled_render_scene_available(False)
        self._active_scene_key = (self._scene_revision, "")
        self._fragment_paths = {}
        self._populate_viewport_menu(
            self.fragment_menu, self.fragment, (), self._select_fragment,
        )
        self._populate_viewport_menu(
            self.lod_menu, self.lod, ("All",), self._select_lod,
        )
        self.lod.set("All")
        self._populate_viewport_menu(
            self.component_menu, self.component, ("All",), self._select_component,
        )
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
        self.axles_panel.clear()
        self.authoring_status.set("Select a vehicle before editing package metadata.")
        self.save_author_button.configure(state="disabled")
        self.undo_author_button.configure(state="disabled")
        for key, variable in self.distribution_values.items():
            variable.set(False if key in {"listed", "traffic_enabled"} else "")
        for widget in self.distribution_inputs:
            widget.configure(state="disabled")
        self.save_distribution_button.configure(state="disabled")
        self.distribution_status.set("Select a vehicle to inspect distribution settings.")
        self._viewport_photo = None
        self._viewport_photo_zoom = None
        self.viewport_message.set(message)
        self._render_viewport()
        self._loaded_editor_snapshot = None

    def _editor_snapshot(self) -> tuple[object, ...]:
        colors = tuple(
            (
                tuple(self._appearance_colors[item].get("indices", ())),
                tuple(self._appearance_colors[item].get("liveries", ())),
            )
            for item in self.color_tree.get_children()
            if item in self._appearance_colors
        )
        return (
            tuple((key, variable.get()) for key, variable in self.authoring_values.items()),
            self.identity_model.get(), self.identity_handling.get(),
            self.appearance_kits.get(), self.appearance_light.get(),
            self.appearance_siren.get(), colors,
            tuple((key, variable.get()) for key, variable in self.distribution_values.items()),
            self.axles_panel.snapshot(),
        )

    def confirm_navigation(self) -> bool:
        """Prevent model/package navigation from silently discarding form edits."""
        if (
            self._loaded_editor_snapshot is None
            or self._editor_snapshot() == self._loaded_editor_snapshot
        ):
            return True
        direct_rpf_axles = (
            self.authoring_workspace is None
            and self.scan is not None
            and self.scan.source_kind == "rpf"
        )
        if self.authoring_workspace is None and not direct_rpf_axles:
            return True
        return messagebox.askyesno(
            "Discard unsaved vehicle edits?",
            "This vehicle has changes that have not been applied.\n\n"
            "Choose No to return and apply them, or Yes to discard them.",
            parent=self, icon="warning",
        )

    def _load_model_preview(self, model: VehicleProjectModel) -> None:
        self._cancel_scene_load()
        self._cancel_scene_render()
        self._set_compiled_render_scene_available(False)
        path = self._fragment_paths.get(
            self.fragment.get(), model.primary_model or model.high_detail_model or "",
        )
        if not path or self.source is None or self.scan is None:
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
        inventory = getattr(self.scan, "workbench_entries", self.scan.entries)
        entry = next(
            (item for item in inventory if item.path.casefold() == path.casefold()),
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
        try:
            key = (self._scene_revision, path.casefold())
            reader = self.reader
            source = self.source
            project_root = self.project_root
            game_path = self._native_game_path()
            edition = self._native_edition()
            generation = self._viewport_scene_worker.submit(
                key,
                lambda: _decode_native_model_scene(
                    reader, source, path, entry.size, project_root=project_root,
                    game_path=game_path, edition=edition,
                ),
                cache_result=False,
            )
        except (RuntimeError, ValueError) as exc:
            self._source_image = None
            self._model_scene = None
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport_message.set(f"Native preview unavailable: {exc}")
            self._render_viewport()
            return
        self._scene_load_key = key
        self._scene_load_path = path
        self._scene_load_generation = generation
        self._ensure_scene_load_poll()

    def _ensure_scene_load_poll(self) -> None:
        if self._scene_load_poll_job is None:
            self._scene_load_poll_job = self.after(16, self._poll_scene_load)

    def _poll_scene_load(self) -> None:
        self._scene_load_poll_job = None
        outcome = self._viewport_scene_worker.poll()
        if (
            outcome is not None
            and outcome.generation == self._scene_load_generation
            and outcome.key == self._scene_load_key
        ):
            path = self._scene_load_path or outcome.key[1]
            if outcome.error is not None:
                self._source_image = None
                self._model_scene = None
                self._viewport_photo = None
                self._viewport_photo_zoom = None
                self.viewport_message.set(
                    f"Native preview unavailable: {outcome.error}"
                )
                self._render_viewport()
            elif not isinstance(outcome.value, _DecodedNativeModel):
                self._source_image = None
                self._model_scene = None
                self.viewport_message.set(
                    "Native preview unavailable: decoder returned an invalid scene"
                )
                self._render_viewport()
            else:
                self.reader = outcome.value.reader
                self._scene_cache[path.casefold()] = outcome.value.scene
                while len(self._scene_cache) > 2:
                    self._scene_cache.pop(next(iter(self._scene_cache)))
                self._activate_scene(path, outcome.value.scene)
        if self._viewport_scene_worker.busy:
            self._ensure_scene_load_poll()

    def _activate_scene(self, path: str, scene: NativeModelScene) -> None:
        self._model_scene = scene
        self._set_compiled_render_scene_available(True)
        self._active_scene_key = (self._scene_revision, path.casefold())
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self._populate_viewport_menu(
            self.lod_menu, self.lod, ("All", *scene.lods), self._select_lod,
        )
        self.lod.set("All")
        component_names = tuple(dict.fromkeys(
            item.name for item in scene.components
        ))
        self._populate_viewport_menu(
            self.component_menu, self.component, ("All", *component_names),
            self._select_component,
        )
        self.component.set("All")
        self._update_component_summary()
        self.axles_panel.set_scene(scene)
        self.viewport_message.set(path)
        self._render_model_scene(fit=True)

    def _render_model_scene(
        self, *, fit: bool = False, quality: str = "final",
    ) -> None:
        scene = self._model_scene
        if scene is None:
            return
        yaw = self._camera_yaw
        pitch = self._camera_pitch
        lod = self.lod.get()
        component = self.component.get()
        render_mode = self.render_mode.get().casefold()
        zoom = self._zoom

        def render_frame() -> object:
            if quality == "interactive":
                image, metadata = scene.render_image(
                    yaw=yaw, pitch=pitch, lod=lod, component=component,
                    render_mode=render_mode, quality=quality,
                    triangle_budget=INTERACTIVE_ORBIT_TRIANGLE_BUDGET,
                )
                return _prepare_viewport_image(
                    image, metadata, quality=quality, zoom=zoom,
                )
            encoded, metadata = scene.render(
                yaw=yaw, pitch=pitch, lod=lod, component=component,
                render_mode=render_mode, quality=quality,
            )
            # Keep final/full cache entries encoded. They are revisited less
            # often and PNG storage leaves substantially more room in the
            # bounded camera-view cache than resident RGB images would.
            return encoded, metadata

        try:
            key = ViewportRenderKey.create(
                self._active_scene_key, yaw=yaw, pitch=pitch,
                lod=lod, component=component, render_mode=render_mode,
                quality=quality,
            )
            generation = self._viewport_render_worker.submit(
                key,
                render_frame,
                cache_result=(quality in {"final", "full"}),
            )
        except (RuntimeError, ValueError) as exc:
            self._source_image = None
            self.viewport_message.set(f"Model view unavailable: {exc}")
            self._render_viewport()
            return
        self._render_generation = generation
        self._render_fit_generation = generation if fit else None
        self._ensure_render_poll()

    def _ensure_render_poll(self) -> None:
        if self._render_poll_job is None:
            # Four milliseconds bounds worker-to-Tk hand-off to a small part
            # of one display interval. Polling is non-blocking and only stays
            # active while the latest-only worker has useful work.
            self._render_poll_job = self.after(4, self._poll_viewport_render)

    def _poll_viewport_render(self) -> None:
        self._render_poll_job = None
        outcome = self._viewport_render_worker.poll()
        if outcome is not None and outcome.generation == self._render_generation:
            if outcome.error is not None:
                self._source_image = None
                self.viewport_message.set(
                    f"Model view unavailable: {outcome.error}"
                )
                self._render_viewport()
            else:
                try:
                    value = outcome.value
                    if isinstance(value, _PreparedViewportFrame):
                        frame = value
                    elif (
                        isinstance(value, tuple) and len(value) == 2
                        and isinstance(value[0], bytes)
                        and isinstance(value[1], dict)
                    ):
                        frame = _prepare_viewport_frame(
                            value[0], value[1], quality=outcome.key.quality,
                            zoom=self._zoom,
                        )
                    else:
                        raise TypeError("model renderer returned an invalid frame")
                    self._source_image = frame.source_image
                    metadata = frame.metadata
                except (OSError, TypeError, ValueError, UnidentifiedImageError) as exc:
                    self._source_image = None
                    self.viewport_message.set(f"Model view unavailable: {exc}")
                    self._render_viewport()
                else:
                    self._apply_rendered_scene(
                        metadata, fit=(outcome.generation == self._render_fit_generation),
                        display_image=frame.display_image,
                        display_zoom=frame.display_zoom,
                    )
        if self._viewport_render_worker.busy:
            self._ensure_render_poll()
        elif self._orbit_origin is not None and self._orbit_render_dirty:
            # Render at the renderer's actual throughput instead of submitting
            # every pointer event.  Each completed frame becomes visible, then
            # the newest camera pose starts immediately; intermediate poses are
            # coalesced rather than invalidating every frame before Tk sees it.
            self._schedule_scene_render(immediate=True)

    def _apply_rendered_scene(
        self, metadata: dict[str, object], *, fit: bool,
        display_image: Image.Image | None = None,
        display_zoom: float | None = None,
    ) -> None:
        self._viewport_photo = None
        self._viewport_photo_zoom = None
        scene = self._model_scene
        if scene is None:
            return
        self.viewport_message.set(
            f"{scene.name} · {metadata['model_camera_yaw']}°/"
            f"{metadata['model_camera_pitch']}° · "
            f"LOD {metadata['model_camera_lod']} · "
            f"{metadata['model_camera_component']} · "
            f"{self.render_mode.get()}"
        )
        if fit:
            self._fit_viewport()
        else:
            # Interactive frames arrive already resized by the worker. Only
            # ImageTk's main-thread-only handle creation remains on this path.
            if display_image is not None and display_zoom == self._zoom:
                self._viewport_photo = ImageTk.PhotoImage(display_image)
                self._viewport_photo_zoom = display_zoom
            self._render_viewport()

    def _native_edition(self) -> str:
        if self.project and self.project.edition.casefold() == "legacy":
            return "Legacy"
        return "Enhanced"

    def _inspection_game_path(self) -> Path | None:
        """Return a configured GTA root before the opened RPF reveals its edition."""
        return next((root for root in self.installation_roots if root.is_dir()), None)

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

    def _show_author_view(self) -> None:
        """Switch authoring work zones without adding another crowded main tab."""
        self.author_general_page.pack_forget()
        self.author_axles_page.pack_forget()
        page = (
            self.author_axles_page
            if self.author_view.get() == "axles" else self.author_general_page
        )
        page.pack(fill="both", expand=True)
        # The Axles editor owns its own selected-model context and status. Give
        # it the same compact inspector treatment as Tuning Builder instead of
        # spending most of a 680 px window on duplicated heading/summary rows.
        self._inspector_tab_changed()

    def _load_authoring_fields(self, model: VehicleProjectModel) -> None:
        workspace = self.authoring_workspace
        if workspace is None:
            direct_rpf_axles = (
                self.scan is not None and self.scan.source_kind == "rpf"
            )
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
                (
                    "RPF assets stay read-only. Axle behavior can be authored and "
                    "saved as a sidecar configuration."
                    if direct_rpf_axles else
                    "Create an authoring workspace to edit this copied package safely."
                )
            )
            self.identity_model.set(model.model)
            self.identity_handling.set(model.handling_id)
            self.identity_model_entry.configure(state="disabled")
            self.identity_handling_entry.configure(state="disabled")
            self.identity_button.configure(state="disabled")
            self._load_distribution(model, editable=False)
            self._load_appearance(model, editable=False)
            self._load_axle_fields(model, editable=direct_rpf_axles)
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
            self._load_distribution(model, editable=False)
            self._clear_appearance()
            self._load_axle_fields(model, editable=False)
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
        self._load_distribution(model, editable=True)
        self._load_appearance(model, editable=True)
        self._load_axle_fields(model, editable=True)

    def _load_axle_fields(
        self, model: VehicleProjectModel, *, editable: bool,
    ) -> None:
        """Project saved axle state into the draft editor without mutating it."""
        configuration: AxleConfiguration | None = None
        flags: int | None = None
        drive_bias: float | None = None
        workspace = self.authoring_workspace
        try:
            session_configuration = self._session_axle_configurations.get(
                model.model.casefold(),
            )
            if session_configuration is not None:
                configuration = session_configuration
            elif workspace is not None:
                configuration = workspace.axle_configuration(model.model)
                evidence = workspace.axle_handling_evidence(model.model)
                raw_flags = evidence.get("strHandlingFlags", "")
                if raw_flags:
                    flags = parse_handling_flags(raw_flags)
                raw_bias = evidence.get("fDriveBiasFront", "")
                try:
                    drive_bias = float(raw_bias)
                except (TypeError, ValueError):
                    drive_bias = None
            elif self.project is not None:
                payload = next((
                    item for item in self.project.axle_configurations
                    if str(item.get("vehicle_model", "")).casefold()
                    == model.model.casefold()
                ), None)
                if payload is not None:
                    configuration = load_prefab_axle_configuration(payload)
        except (OSError, RuntimeError, TypeError, ValueError):
            configuration = None
        asset_names = tuple(
            entry.path for entry in self.scan.workbench_entries
        ) if self.scan is not None else ()
        self.axles_panel.load(
            model.model, configuration, editable=editable,
            asset_names=asset_names, handling_flags=flags,
            drive_bias_front=drive_bias,
            target=(
                "story-enhanced"
                if self.project is not None
                and self.project.edition.casefold() == "enhanced"
                else "story-legacy"
            ),
        )

    def _load_distribution(
        self, model: VehicleProjectModel, *, editable: bool,
    ) -> None:
        workspace = self.authoring_workspace
        try:
            values = (
                workspace.distribution(model.model).to_dict()
                if workspace is not None
                else {
                    "listed": True,
                    "name": model.display_name or model.model,
                    "manufacturer": model.make_name,
                    "category": "special",
                    "price": 0,
                    "storage": "garage",
                    "size_tier": 0,
                    "preview_dictionary": "",
                    "preview_texture": "",
                    "traffic_enabled": False,
                    "traffic_weight": 1.0,
                }
            )
        except (OSError, ValueError) as exc:
            values = {}
            editable = False
            self.distribution_status.set(f"Distribution unavailable: {exc}")
        for key, variable in self.distribution_values.items():
            value = values.get(key, False if key in {"listed", "traffic_enabled"} else "")
            variable.set(value if value is not None else "")
        for widget in self.distribution_inputs:
            state = "readonly" if editable and isinstance(widget, ttk.Combobox) else (
                "normal" if editable else "disabled"
            )
            widget.configure(state=state)
        self.save_distribution_button.configure(
            state="normal" if editable else "disabled",
        )
        if editable:
            self.distribution_status.set(
                "GBAY listing is independent from traffic. Ambient traffic is an "
                "explicit opt-in and its launcher toggle defaults off."
            )
        elif not values:
            return
        else:
            self.distribution_status.set(
                "Create an authoring workspace to edit distribution settings."
            )

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
            self.tuning_add_button, self.tuning_field_button,
            self.tuning_use_asset_button, self.tuning_open_asset_button,
        ):
            button.configure(state="disabled")
        self._set_tuning_entry_action_states()
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

    def _change_tuning_view(self, _event: object | None = None) -> None:
        page = (
            self.tuning_validation_page
            if self.tuning_view.get() == "Assets and checks"
            else self.tuning_parts_page
        )
        self.tuning_pages.select(page)

    def _show_tuning_create(self) -> None:
        self.tuning_view.set("Parts and fields")
        self.tuning_pages.select(self.tuning_parts_page)
        self.tuning_editor_tabs.select(self.tuning_create_page)

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
        self._set_tuning_entry_action_states()
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

    def _set_tuning_entry_action_states(
        self, *, selected: bool = False,
        can_move_up: bool = False, can_move_down: bool = False,
    ) -> None:
        active = self._tuning_editable and selected
        state = "normal" if active else "disabled"
        editable_state = "normal" if self._tuning_editable else "disabled"
        self.tuning_entry_actions_button.configure(state=editable_state)
        self.tuning_entry_action_menu.entryconfigure(
            "New entry", state=editable_state,
        )
        self.tuning_entry_action_menu.entryconfigure("Copy selected", state=state)
        self.tuning_entry_action_menu.entryconfigure("Delete selected", state=state)
        self.tuning_entry_action_menu.entryconfigure(
            "Move up", state="normal" if active and can_move_up else "disabled",
        )
        self.tuning_entry_action_menu.entryconfigure(
            "Move down", state="normal" if active and can_move_down else "disabled",
        )

    def _select_tuning_builder_entry(self, _event: object | None = None) -> None:
        entry = self._selected_tuning_entry()
        self.tuning_field_tree.delete(*self.tuning_field_tree.get_children())
        if entry is None:
            self._set_tuning_entry_action_states()
            return
        self.tuning_editor_tabs.select(self.tuning_fields_page)
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
        siblings = [
            item for item in self._tuning_entries.values()
            if item.collection == entry.collection
        ]
        self._set_tuning_entry_action_states(
            selected=True,
            can_move_up=entry.index > 0,
            can_move_down=entry.index < len(siblings) - 1,
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
        self.tuning_view.set("Parts and fields")
        self.tuning_pages.select(self.tuning_parts_page)
        self.tuning_editor_tabs.select(self.tuning_create_page)

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
        self.tuning_view.set("Parts and fields")
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

    def _save_distribution(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        try:
            updates = {
                "listed": bool(self.distribution_values["listed"].get()),
                "name": str(self.distribution_values["name"].get()).strip(),
                "manufacturer": str(
                    self.distribution_values["manufacturer"].get()
                ).strip(),
                "category": str(self.distribution_values["category"].get()).strip(),
                "price": int(str(self.distribution_values["price"].get()).strip()),
                "storage": str(self.distribution_values["storage"].get()).strip(),
                "size_tier": int(
                    str(self.distribution_values["size_tier"].get()).strip()
                ),
                "preview_dictionary": str(
                    self.distribution_values["preview_dictionary"].get()
                ).strip() or None,
                "preview_texture": str(
                    self.distribution_values["preview_texture"].get()
                ).strip() or None,
                "traffic_enabled": bool(
                    self.distribution_values["traffic_enabled"].get()
                ),
                "traffic_weight": float(
                    str(self.distribution_values["traffic_weight"].get()).strip()
                ),
            }
            values = workspace.set_distribution(
                model.model, updates, expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            messagebox.showerror("Distribution rejected", str(exc), parent=self)
            self.distribution_status.set(f"Distribution rejected: {exc}")
            return
        self._reload_authoring_model(model.model)
        self.status.set(
            f"Updated {values.model} distribution · revision {workspace.revision}"
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

    def _redo_authoring_edit(self) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if workspace is None or model is None:
            return
        try:
            result = workspace.redo()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Vehicle redo failed", str(exc), parent=self)
            self.authoring_status.set(f"Redo failed: {exc}")
            return
        self._reload_authoring_model(result.model)
        self.status.set(f"Reapplied vehicle edit · revision {result.revision}")

    def _apply_axle_configuration(self, configuration: AxleConfiguration) -> None:
        workspace = self.authoring_workspace
        model = self.selected_model
        if model is None:
            return
        if workspace is None:
            if self.scan is None or self.scan.source_kind != "rpf":
                return
            self._session_axle_configurations[model.model.casefold()] = configuration
            self._loaded_editor_snapshot = self._editor_snapshot()
            self.axles_panel.status.set(
                "Applied to this RPF session. Use Config ▾ > Save workbench "
                "config… for an editable sidecar, or Export native Story "
                "config… for the in-game ASI controller."
            )
            self.status.set(
                f"Applied {len(configuration.axles)} axle pairs to the "
                f"{model.model} sidecar draft; the RPF was not modified."
            )
            return
        try:
            result = workspace.set_axle_configuration(
                configuration,
                bones=self._model_scene.bones if self._model_scene is not None else (),
                expected_revision=workspace.revision,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            messagebox.showerror("Axle configuration rejected", str(exc), parent=self)
            self.axles_panel.status.set(f"Edit rejected and rolled back: {exc}")
            return
        self._reload_authoring_model(result.model)
        self.author_view.set("axles")
        self._show_author_view()
        self.status.set(
            f"Applied {len(configuration.axles)} axle pairs · revision {result.revision} · "
            f"{len(result.changes)} reviewed file/manifest changes"
        )
        if result.warnings:
            self.axles_panel.status.set(
                "Applied with warnings: " + " · ".join(result.warnings[:3])
            )

    def _export_axle_configuration(self, configuration: AxleConfiguration) -> None:
        target = self.axles_panel.target_key()
        if target.startswith("fivem-"):
            parent = filedialog.askdirectory(
                parent=self, title="Select parent folder for the FiveM axle resource",
            )
            if not parent:
                return
            destination = Path(parent) / (
                f"allin1-{configuration.vehicle_model}-axles-{target}"
            )
            try:
                output = write_fivem_resource(configuration, destination)
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror("Axle export failed", str(exc), parent=self)
                return
            self.status.set(f"Exported target-validated FiveM axle resource: {output}")
            return
        if self.authoring_workspace is None or self.selected_model is None:
            messagebox.showinfo(
                "Authoring workspace required",
                "Create an authoring workspace before building a Story package. "
                "That workspace owns the stable OIV identity and reviewed staging output.",
                parent=self,
            )
            return
        default_target = (
            "story-enhanced"
            if self.project is not None and self.project.edition.casefold() == "enhanced"
            else "story-legacy"
        )
        VehicleOivExportDialog(
            self,
            model=configuration.vehicle_model,
            default_name=(
                self.selected_model.display_name or configuration.vehicle_model
            ),
            default_target=default_target,
            on_preview=lambda form: self._prepare_story_export(configuration, form),
        )

    def _prepare_story_export(
        self,
        configuration: AxleConfiguration,
        form: VehicleOivForm,
    ) -> tuple[dict[str, object], _PreparedStoryExport]:
        workspace = self.authoring_workspace
        project = self.project
        if workspace is None or project is None:
            raise ValueError("A reviewed vehicle authoring workspace is required")
        edition = project.edition.casefold()
        requested_edition = form.target_id.removeprefix("story-")
        if edition == "legacy + enhanced":
            raise ValueError("Select one edition-specific vehicle source before Story export")
        if edition in {"legacy", "enhanced"} and edition != requested_edition:
            raise ValueError(
                f"{project.edition} vehicle assets cannot be exported as {form.target_id}"
            )
        if form.mode == MODE_SELF_CONTAINED and not form.confirm_self_contained:
            raise ValueError("Self-contained export requires the explicit overwrite acknowledgement")
        if form.output_path.exists() or form.output_path.is_symlink():
            raise FileExistsError(f"Export output already exists: {form.output_path}")

        temporary = tempfile.TemporaryDirectory(prefix="allin1-story-oiv-")
        stage = Path(temporary.name).resolve()
        try:
            compatibility_manifest: dict[str, object] = {
                "schema_version": 1,
                "target": form.target_id,
                "source_edition": project.edition,
                "game_write_performed": False,
                "vehicle_artifacts": [],
            }
            vehicle_dlcs: tuple[StagedVehicleDlc, ...] = ()
            axle_configs: tuple[StagedAxleConfiguration, ...] = ()
            if form.mode != MODE_RUNTIME_ONLY:
                package = VehicleAddonPackageBuilder(
                    self.project_root, self._native_game_path(),
                ).build(
                    workspace.root,
                    stage / "vehicle-package",
                    pack_name=form.dlc_pack_name,
                    mod_id=f"vehicle.{form.dlc_pack_name}",
                    name=form.package_name,
                    version=form.package_version,
                    editions=(requested_edition,),
                )
                resolved_configs: dict[str, AxleConfiguration] = {
                    configuration.vehicle_model: configuration,
                }
                for project_model in project.models:
                    if project_model.model.casefold() == configuration.vehicle_model:
                        continue
                    saved = workspace.axle_configuration(project_model.model)
                    if saved is not None:
                        resolved_configs[saved.vehicle_model] = saved
                staged_configs = []
                for item in sorted(
                    resolved_configs.values(), key=lambda value: value.vehicle_model,
                ):
                    config_path = stage / "configs" / f"{item.vehicle_model}.json"
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    runtime_payload = compatibility_configuration(
                        VehicleAxleBuildInput(
                            configuration=item,
                            configuration_id=item.configuration_id,
                            model_hash=item.model_hash,
                            minimum_runtime_version=item.minimum_runtime_version,
                            dual_tyre_geometry=tuple(
                                addon.asset
                                for axle in item.axles
                                for addon in axle.addon_geometry
                            ),
                        ),
                        form.target_id,
                    )
                    config_path.write_text(
                        json.dumps(runtime_payload, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    staged_configs.append(StagedAxleConfiguration(
                        model_name=item.vehicle_model,
                        model_hash=item.model_hash,
                        source_path=config_path.relative_to(stage).as_posix(),
                        schema_version=item.schema_version,
                        minimum_runtime_version=item.minimum_runtime_version,
                    ))
                vehicle_dlcs = (StagedVehicleDlc(
                    dlc_pack_name=package.pack_name,
                    archive_path=package.payload.relative_to(stage).as_posix(),
                    vehicle_models=tuple(item.model for item in project.models),
                    asset_edition=requested_edition,
                ),)
                axle_configs = tuple(staged_configs)
                native_index = RpfExplorerService(
                    self.project_root, self._native_game_path(),
                ).index(package.payload)
                entry_names = {
                    item.name.casefold() for item in native_index.entries
                    if item.kind != "directory"
                }
                model_assets = {
                    model.model.casefold(): {
                        "yft": f"{model.model.casefold()}.yft" in entry_names,
                        "ytd": f"{model.model.casefold()}.ytd" in entry_names,
                    }
                    for model in project.models
                }
                required_metadata = {
                    name: name in entry_names
                    for name in (
                        "vehicles.meta", "handling.meta", "carvariations.meta",
                    )
                }
                missing_assets = [
                    model for model, evidence in model_assets.items()
                    if not evidence["yft"] or not evidence["ytd"]
                ]
                missing_metadata = [
                    name for name, present in required_metadata.items() if not present
                ]
                if missing_assets or missing_metadata:
                    details = []
                    if missing_assets:
                        details.append("missing YFT/YTD for " + ", ".join(missing_assets))
                    if missing_metadata:
                        details.append("missing metadata " + ", ".join(missing_metadata))
                    raise ValueError(
                        "Native RPF validation failed: " + "; ".join(details)
                    )
                native_report = stage / "vehicle-package" / "native-rpf-validation.json"
                native_report.write_text(json.dumps({
                    "schema_version": 1,
                    "operation": "validate_story_vehicle_rpf",
                    "status": "validated",
                    "archive_sha256": package.payload_sha256,
                    "edition": native_index.edition.casefold(),
                    "archive_count": len(native_index.archives),
                    "entry_count": len(native_index.entries),
                    "model_assets": model_assets,
                    "required_metadata": required_metadata,
                    "warnings": list(native_index.warnings),
                    "game_write_performed": False,
                }, indent=2) + "\n", encoding="utf-8")
                compatibility_manifest["vehicle_artifacts"] = [{
                    "path": package.payload.relative_to(stage).as_posix(),
                    "sha256": package.payload_sha256,
                    "asset_edition": requested_edition,
                    "asset_format": (
                        "legacy-rpf7-gen8"
                        if form.target_id == "story-legacy" else "gen9-required"
                    ),
                    "validation_status": "validated",
                    "validation_report": package.report.relative_to(stage).as_posix(),
                    "validation_report_sha256": hashlib.sha256(
                        package.report.read_bytes()
                    ).hexdigest(),
                    "native_validation_report": native_report.relative_to(stage).as_posix(),
                    "native_validation_report_sha256": hashlib.sha256(
                        native_report.read_bytes()
                    ).hexdigest(),
                }]

            runtime = self._stage_story_runtime(stage, form)
            (stage / "compatibility-manifest.json").write_text(
                json.dumps(compatibility_manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            project_id, package_id = self._story_oiv_package_ids(form)
            profile = (
                LegacyOivTargetProfile()
                if form.target_id == "story-legacy" else EnhancedOivTargetProfile()
            )
            metadata = OivPackageMetadata(
                project_id=project_id,
                package_id=package_id,
                name=form.package_name,
                version=form.package_version,
                author=form.author,
                description=form.description,
                workbench_version=__version__,
                license_name="User-supplied vehicle assets; see package documentation",
            )
            request = OivExportRequest(
                staging_root=stage,
                target_profile=profile,
                mode=form.mode,
                metadata=metadata,
                vehicle_dlcs=vehicle_dlcs,
                axle_configurations=axle_configs,
                runtime=runtime,
                include_documentation=form.include_documentation,
                icon_path=form.icon_path,
                confirm_self_contained=form.confirm_self_contained,
            )
            identity_store = JsonOivIdentityStore(
                (
                    user_data_root() / "vehicle-workbench-axle-runtime-identities.json"
                    if form.mode == MODE_RUNTIME_ONLY
                    else workspace.root / "oiv-package-identities.json"
                )
            )
            builder = OivPackageBuilder(identity_store)
            enhanced_fallback = form.target_id == "story-enhanced"
            preview_request = request
            if enhanced_fallback:
                preview_request = OivExportRequest(**{
                    **request.__dict__,
                    "target_profile": EnhancedOivTargetProfile(
                        installer_name="OpenRPF manual import",
                        integration_validated=True,
                        supported_game_builds=("manual-validation-required",),
                        archive_paths=("manual-review",),
                        installation_rules=("manual OpenRPF import",),
                        runtime_profile_id="manual-preview-only",
                        acceptance_receipt_sha256="0" * 64,
                    ),
                })
            preview = OivContentPlanner(identity_store).plan(
                preview_request
            ).installation_preview()
            if enhanced_fallback:
                preview["warnings"] = [
                    "Enhanced OIV export is not validated. Export will be an OpenRPF-ready ZIP.",
                    *preview.get("warnings", []),
                ]
            prepared = _PreparedStoryExport(
                temporary, builder, request, form.output_path,
                enhanced_fallback=enhanced_fallback,
            )
            return preview, prepared
        except Exception:
            temporary.cleanup()
            raise

    @staticmethod
    def _story_oiv_package_ids(form: VehicleOivForm) -> tuple[str, str]:
        """Keep shared-runtime identity independent from the open vehicle project."""
        if form.mode == MODE_RUNTIME_ONLY:
            return (
                "vehicle-workbench-axle-runtime",
                "vehicle-workbench-axle-runtime",
            )
        project_id = f"vehicle.{form.dlc_pack_name}"
        if form.mode == MODE_SELF_CONTAINED:
            return project_id, f"{project_id}.self-contained"
        return project_id, project_id

    @staticmethod
    def _stage_story_runtime(
        stage: Path, form: VehicleOivForm,
    ) -> StagedRuntime | None:
        if form.mode not in {MODE_RUNTIME_ONLY, MODE_SELF_CONTAINED}:
            return None
        if form.runtime_path is None:
            raise ValueError(
                "The selected package mode requires a validated runtime profile JSON"
            )
        profile = StoryRuntimeProfile.load(form.runtime_path)
        dependency = profile.runtime_dependency()
        dependency.validate()
        source = Path(dependency.binary_path).resolve()
        receipt = Path(dependency.validation_receipt_path).resolve()
        target = dependency.target_id
        version = dependency.version
        if target != form.target_id:
            raise ValueError("Generic axle runtime edition does not match the export target")
        if version != form.runtime_version:
            raise ValueError("Runtime version field does not match the verified runtime metadata")
        digest = dependency.checksum()
        assert digest is not None
        destination = stage / "runtime" / "VehicleWorkbenchAxles.asi"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        receipt_destination = stage / "runtime" / "validation-receipt.json"
        shutil.copyfile(receipt, receipt_destination)
        return StagedRuntime(
            asi_path=destination.relative_to(stage).as_posix(),
            version=version,
            target_id=target,
            supported_game_builds=dependency.supported_game_builds,
            maximum_schema_version=dependency.maximum_schema_version,
            binary_sha256=digest,
            build_date="validated by pinned acceptance receipt",
            profile_id=dependency.profile_id or "",
            validation_receipt_path=receipt_destination.relative_to(stage).as_posix(),
            validation_receipt_sha256=dependency.validation_receipt_sha256 or "",
            package_eligible=dependency.package_eligible,
            redistribution_allowed=dependency.redistribution_allowed,
            license_name=dependency.license_name,
            architecture="x64",
            required_scripthook_version="current compatible release",
        )

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
                self.authoring_workspace.root
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

    @staticmethod
    def _populate_viewport_menu(
        menu: tk.Menu, variable: tk.StringVar, values: tuple[str, ...], command,
    ) -> None:
        """Replace one compact viewport selector without creating hidden controls."""
        menu.delete(0, "end")
        if not values:
            menu.add_command(label="Unavailable", state="disabled")
            return
        for value in values:
            menu.add_radiobutton(
                label=value, variable=variable, value=value, command=command,
            )

    def _select_render_mode(self) -> None:
        mode = self.render_mode.get()
        self.render_mode_label.set(f"{mode} ▾")
        self._render_model_scene(quality="final")

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

    def _set_camera_pose(self, yaw: float, pitch: float) -> None:
        if self._model_scene is None:
            return
        self._camera_yaw = yaw % 360.0
        self._camera_pitch = min(89.0, max(-89.0, pitch))
        self._render_model_scene(fit=True)

    def _reset_camera(self) -> None:
        if self._model_scene is None:
            return
        self._camera_yaw = 34.0
        self._camera_pitch = 24.0
        self._render_model_scene(fit=True)

    def _render_full_quality(self) -> None:
        if self._model_scene is None:
            return
        self.viewport_message.set("Rendering full-quality frame in background…")
        self._render_viewport()
        self._render_model_scene(quality="full")

    def _show_compiled_render(self) -> None:
        """Open the focused render drawer without adding permanent viewport chrome."""
        if self.compiled_render_panel is None:
            self.compiled_render_panel = CompiledRenderPanel(
                self._compiled_render_parent,
                backend_status=self._compiled_render_backend_status,
                on_render=self._start_compiled_render,
                on_cancel=self._cancel_compiled_render,
                on_locate_backend=self._locate_compiled_render_backend,
            )
        model_name = (
            self.selected_model.model
            if self.selected_model is not None else "vehicle"
        )
        safe_name = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in model_name
        ).strip("-") or "vehicle"
        pictures = Path.home() / "Pictures"
        output_root = pictures if pictures.is_dir() else Path.home()
        suggested = output_root / f"{safe_name}.png"
        self.compiled_render_panel.set_scene_available(self._model_scene is not None)
        self.compiled_render_panel.show(suggested_output=suggested)

    def _set_compiled_render_scene_available(self, available: bool) -> None:
        panel = self.compiled_render_panel
        if panel is not None:
            panel.set_scene_available(available)

    def _compiled_render_backend_status(self) -> dict[str, object]:
        """Detect and validate the optional renderer without assuming it exists."""
        installation = detect_blender(self._compiled_render_executable)
        self._compiled_render_installation = installation
        if installation is not None:
            self._compiled_render_path_error = ""
            return {
                "available": True,
                "name": f"Blender {installation.version}",
                "detail": f"Detected from {installation.source}; headless render ready.",
                "device": "Eevee + Cycles",
            }
        selected = self._compiled_render_executable
        return {
            "available": False,
            "name": (
                "Invalid Blender executable"
                if selected is not None else "Blender not detected"
            ),
            "detail": (
                self._compiled_render_path_error or "Choose another blender.exe."
                if selected is not None
                else "Locate Blender or get the official installer."
            ),
        }

    def _locate_compiled_render_backend(self, executable: Path) -> None:
        """Validate and atomically retain a user-selected Blender executable."""
        try:
            selected = executable.expanduser().resolve(strict=True)
        except OSError as exc:
            self._compiled_render_executable = executable.expanduser()
            self._compiled_render_installation = None
            self._compiled_render_path_error = str(exc)
            return
        self._compiled_render_executable = selected
        installation = detect_blender(selected)
        self._compiled_render_installation = installation
        if installation is None:
            self._compiled_render_path_error = (
                "The selected program did not report a valid Blender version."
            )
            return
        self._compiled_render_path_error = ""
        try:
            self._persist_compiled_render_executable(installation.executable)
        except OSError as exc:
            # The validated executable remains usable for this SDK session.
            self._compiled_render_path_error = f"Could not save renderer setting: {exc}"

    @staticmethod
    def _compiled_render_config_path() -> Path:
        return user_data_root() / "compiled-render.json"

    @classmethod
    def _load_compiled_render_executable(cls) -> Path | None:
        path = cls._compiled_render_config_path()
        try:
            if not path.is_file() or path.stat().st_size > 16_384:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        executable = payload.get("blender_executable") if isinstance(payload, dict) else None
        return Path(executable).expanduser() if isinstance(executable, str) else None

    @classmethod
    def _persist_compiled_render_executable(cls, executable: Path) -> None:
        destination = cls._compiled_render_config_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=destination.parent, prefix=f".{destination.stem}-", suffix=".tmp",
            ) as stream:
                temporary = Path(stream.name)
                json.dump(
                    {"schema": 1, "blender_executable": str(executable)},
                    stream, indent=2, sort_keys=True,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _start_compiled_render(self, settings: RenderSettings) -> bool:
        """Snapshot the active view and compile it away from the Tk thread."""
        panel = self.compiled_render_panel
        if panel is None:
            return False
        if self._compiled_render_thread is not None and self._compiled_render_thread.is_alive():
            panel.set_running(
                True, message="A compiled render is already running.",
            )
            return False
        scene = self._model_scene
        if scene is None:
            panel.set_running(
                False, message="Load a decoded vehicle model before rendering.",
            )
            return False
        installation = self._compiled_render_installation
        if installation is None:
            status = self._compiled_render_backend_status()
            installation = self._compiled_render_installation
            if installation is None:
                panel.set_running(
                    False, message=str(status.get("detail") or status.get("name")),
                )
                return False
        raw_settings = dict(settings)
        output_value = raw_settings.pop("output_path", None)
        try:
            output = Path(output_value).expanduser()  # type: ignore[arg-type]
            configured = CompiledRenderSettings(**raw_settings)
        except (TypeError, ValueError) as exc:
            panel.set_running(False, message=str(exc))
            return False

        protected_roots: list[Path] = list(self.installation_roots)
        source = self.source
        if source is not None:
            protected_roots.append(source if source.is_dir() else source.parent)
        yaw = self._camera_yaw
        pitch = self._camera_pitch
        lod = None if self.lod.get().casefold() == "all" else self.lod.get()
        component = (
            None if self.component.get().casefold() == "all" else self.component.get()
        )
        texture_dictionary: Path | None = None
        selected_model = self.selected_model
        if (
            selected_model is not None and selected_model.texture_asset
            and self.source is not None and self.source.is_dir()
        ):
            candidate = (self.source / selected_model.texture_asset).resolve()
            try:
                candidate.relative_to(self.source)
            except ValueError:
                candidate = Path()
            if candidate.is_file() and not candidate.is_symlink():
                texture_dictionary = candidate
        edition = self.project.edition if self.project is not None else "Enhanced"
        game_path = self._native_game_path()
        cancel_event = threading.Event()
        self._compiled_render_cancel_event = cancel_event
        self._compiled_render_events = queue.SimpleQueue()

        def report(progress: CompiledRenderProgress) -> None:
            self._compiled_render_events.put(("progress", progress))

        def worker() -> None:
            try:
                result = compile_vehicle_render(
                    scene, output, settings=configured,
                    blender_executable=installation.executable,
                    texture_dictionary=texture_dictionary,
                    edition=edition, gta_path=game_path,
                    yaw=yaw, pitch=pitch, lod=lod, component=component,
                    protected_roots=tuple(protected_roots),
                    cancel_event=cancel_event, progress=report,
                )
            except CompiledRenderError as exc:
                self._compiled_render_events.put(("error", exc))
            except (OSError, RuntimeError, ValueError) as exc:
                self._compiled_render_events.put(("error", exc))
            else:
                self._compiled_render_events.put(("complete", result))

        panel.set_progress(0.0, "Preparing compiled render…")
        panel.set_running(True)
        self._compiled_render_thread = threading.Thread(
            target=worker, name="allin1-compiled-render", daemon=True,
        )
        self._compiled_render_thread.start()
        self._ensure_compiled_render_poll()
        return True

    def _cancel_compiled_render(self) -> None:
        """Request cooperative cancellation; the worker owns process cleanup."""
        if self._compiled_render_cancel_event is not None:
            self._compiled_render_cancel_event.set()

    def _ensure_compiled_render_poll(self) -> None:
        if self._compiled_render_poll_job is None:
            self._compiled_render_poll_job = self.after(40, self._poll_compiled_render)

    def _poll_compiled_render(self) -> None:
        self._compiled_render_poll_job = None
        panel = self.compiled_render_panel
        if panel is None:
            self._cancel_compiled_render()
            return
        terminal = False
        while True:
            try:
                kind, value = self._compiled_render_events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress" and isinstance(value, CompiledRenderProgress):
                panel.set_progress(value.fraction, value.message)
            elif kind == "complete" and isinstance(value, CompiledRenderResult):
                panel.set_output(
                    value.output_path,
                    message=f"Render complete in {value.elapsed_seconds:.1f} seconds.",
                )
                terminal = True
            elif kind == "error":
                message = (
                    value.message if isinstance(value, CompiledRenderError) else str(value)
                )
                panel.set_output(None, message=message)
                terminal = True
        thread = self._compiled_render_thread
        if terminal or thread is None or not thread.is_alive():
            self._compiled_render_thread = None
            self._compiled_render_cancel_event = None
        else:
            self._ensure_compiled_render_poll()

    def _schedule_scene_render(self, *, immediate: bool = False) -> None:
        # Pointer motion updates one desired camera pose.  A new interactive
        # frame is only submitted when the preceding frame has been consumed;
        # otherwise continuous 60+ Hz events can invalidate every 10-30 Hz
        # renderer result and make the model appear frozen until drag release.
        if immediate and self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        if self._render_job is None:
            # Submission only places one callable on the render thread; it is
            # cheap enough to run at the next Tk turn.  An arbitrary 8 ms
            # debounce added latency without reducing work because the worker
            # already coalesces to one in-flight frame plus the newest pose.
            self._render_job = self.after(0, self._run_scheduled_scene_render)

    def _run_scheduled_scene_render(self) -> None:
        self._render_job = None
        if self._orbit_origin is None or not self._orbit_render_dirty:
            return
        if self._viewport_render_worker.busy:
            self._ensure_render_poll()
            return
        self._orbit_render_dirty = False
        self._render_model_scene(quality="interactive")

    def _cancel_scene_render(self) -> None:
        if self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        self._cancel_final_scene_render()
        self._render_generation = self._viewport_render_worker.invalidate()
        self._render_fit_generation = None
        self._orbit_render_dirty = False

    def _cancel_scene_load(self) -> None:
        if self._scene_load_poll_job is not None:
            self.after_cancel(self._scene_load_poll_job)
            self._scene_load_poll_job = None
        self._scene_load_generation = self._viewport_scene_worker.invalidate()
        self._scene_load_key = None
        self._scene_load_path = None

    def _destroy_viewport_renderer(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        self._cancel_final_scene_render()
        if self._render_poll_job is not None:
            self.after_cancel(self._render_poll_job)
            self._render_poll_job = None
        if self._scene_load_poll_job is not None:
            self.after_cancel(self._scene_load_poll_job)
            self._scene_load_poll_job = None
        if self._compiled_render_poll_job is not None:
            self.after_cancel(self._compiled_render_poll_job)
            self._compiled_render_poll_job = None
        if self._compiled_render_cancel_event is not None:
            self._compiled_render_cancel_event.set()
        self._viewport_scene_worker.close(wait=False)
        self._viewport_render_worker.close(wait=False)

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
        # The renderer's clean view box still includes a restrained framing
        # margin.  A small overscan makes the vehicle the focus of the panel
        # while retaining enough room for its extremities and the viewport HUD.
        self._zoom = min(width / image.width, height / image.height) * 1.22
        self._zoom = min(4.0, max(0.08, self._zoom))
        self._pan_x = self._pan_y = 0.0
        self._render_viewport()

    def _begin_pan(self, event: tk.Event) -> None:
        self.viewport.focus_set()
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
        self.viewport.focus_set()
        if self._model_scene is None:
            return
        # A rapid re-grab should stay interactive rather than beginning the
        # expensive release frame and blocking behind it on the single worker.
        self._cancel_final_scene_render()
        self._orbit_origin = (event.x, event.y)
        self._orbit_camera = (self._camera_yaw, self._camera_pitch)
        self._orbit_render_dirty = False

    def _continue_orbit(self, event: tk.Event) -> None:
        if self._orbit_origin is None or self._orbit_camera is None:
            return
        delta_x = event.x - self._orbit_origin[0]
        delta_y = event.y - self._orbit_origin[1]
        self._camera_yaw = (self._orbit_camera[0] + delta_x * 0.45) % 360.0
        self._camera_pitch = min(
            89.0, max(-89.0, self._orbit_camera[1] - delta_y * 0.3),
        )
        self._orbit_render_dirty = True
        self._schedule_scene_render()

    def _end_orbit(self, _event: tk.Event) -> None:
        if self._orbit_origin is None:
            return
        self._orbit_origin = None
        self._orbit_camera = None
        self._cancel_scene_render()
        # Let a short pointer-settle window expire before the detailed frame.
        # This prevents click-release-click orbit gestures from repeatedly
        # queueing 45k-triangle renders, while still replacing the interactive
        # frame immediately after an ordinary release.
        self._final_render_job = self.after(
            ORBIT_FINAL_SETTLE_MS, self._render_final_after_orbit,
        )

    def _render_final_after_orbit(self) -> None:
        self._final_render_job = None
        if self._orbit_origin is None and self._model_scene is not None:
            self._render_model_scene(quality="final")

    def _cancel_final_scene_render(self) -> None:
        if self._final_render_job is not None:
            self.after_cancel(self._final_render_job)
            self._final_render_job = None

    def _fit_viewport_event(self, _event: tk.Event | None = None) -> str:
        self._fit_viewport()
        return "break"

    def _reset_zoom_event(self, _event: tk.Event | None = None) -> str:
        self._reset_zoom()
        return "break"

    def _reset_camera_event(self, _event: tk.Event | None = None) -> str:
        self._reset_camera()
        return "break"

    def _zoom_in_event(self, _event: tk.Event | None = None) -> str:
        self._zoom_by(1.25)
        return "break"

    def _zoom_out_event(self, _event: tk.Event | None = None) -> str:
        self._zoom_by(0.8)
        return "break"

    def _render_viewport(self) -> None:
        width = max(1, self.viewport.winfo_width())
        height = max(1, self.viewport.winfo_height())
        self._ensure_viewport_canvas(width, height)
        items = self._viewport_canvas_items
        if self._source_image is None:
            self._viewport_photo = None
            self._viewport_photo_zoom = None
            self.viewport.itemconfigure(items["image"], state="hidden", image="")
            self.viewport.itemconfigure(
                items["empty"], state="normal", text=self.viewport_message.get(),
            )
            for name in ("top", "summary", "help", "bottom", "status"):
                self.viewport.itemconfigure(items[name], state="hidden")
            self.zoom_label.configure(text="100%")
            return
        if self._viewport_photo is None or self._viewport_photo_zoom != self._zoom:
            scaled_width = max(1, round(self._source_image.width * self._zoom))
            scaled_height = max(1, round(self._source_image.height * self._zoom))
            target_size = (scaled_width, scaled_height)
            if target_size == self._source_image.size:
                rendered = self._source_image
            else:
                # Bilinear filtering is materially cheaper while the camera is
                # moving and is immediately replaced by a Lanczos final frame
                # on release.  Static and full-quality views retain Lanczos.
                resampling = (
                    Image.Resampling.BILINEAR
                    if self._orbit_origin is not None
                    else Image.Resampling.LANCZOS
                )
                rendered = self._source_image.resize(target_size, resampling)
            self._viewport_photo = ImageTk.PhotoImage(rendered)
            self._viewport_photo_zoom = self._zoom
        self.viewport.itemconfigure(items["empty"], state="hidden")
        self.viewport.itemconfigure(
            items["image"], state="normal", image=self._viewport_photo,
        )
        self.viewport.coords(
            items["image"], width / 2 + self._pan_x, height / 2 + self._pan_y,
        )
        # Information belongs on the viewport, not in another permanent row.
        # These restrained overlays preserve canvas area and remain readable
        # against both wireframe and shaded output.
        for name in ("top", "summary", "bottom", "status"):
            self.viewport.itemconfigure(items[name], state="normal")
        self.viewport.itemconfigure(
            items["summary"], text=self.component_summary.get(),
        )
        self.viewport.itemconfigure(
            items["status"], text=self.viewport_message.get(),
        )
        if width >= 700:
            self.viewport.itemconfigure(items["help"], state="normal")
        else:
            self.viewport.itemconfigure(items["help"], state="hidden")
        self.zoom_label.configure(text=f"{self._zoom * 100:.0f}%")

    def _ensure_viewport_canvas(self, width: int, height: int) -> None:
        """Create viewport chrome once and only relayout it when size changes."""
        items = self._viewport_canvas_items
        if not items:
            items.update({
                "image": self.viewport.create_image(
                    width / 2, height / 2, anchor="center", state="hidden",
                ),
                "empty": self.viewport.create_text(
                    width / 2, height / 2, fill="#afc5b9",
                    width=max(200, width - 80), justify="center",
                    font=("Segoe UI", 10),
                ),
                "top": self.viewport.create_rectangle(
                    0, 0, width, 25, fill="#0c120f", outline="", state="hidden",
                ),
                "summary": self.viewport.create_text(
                    9, 13, anchor="w", width=max(100, width - 18),
                    fill="#8fb9a2", font=("Segoe UI", 8), state="hidden",
                ),
                "help": self.viewport.create_text(
                    width - 9, 13,
                    text="L-drag pan · R-drag orbit · wheel zoom", anchor="e",
                    fill="#667d71", font=("Segoe UI", 8), state="hidden",
                ),
                "bottom": self.viewport.create_rectangle(
                    0, max(0, height - 25), width, height,
                    fill="#0c120f", outline="", state="hidden",
                ),
                "status": self.viewport.create_text(
                    9, height - 12, anchor="w", width=max(100, width - 18),
                    fill="#afc5b9", font=("Segoe UI", 8), state="hidden",
                ),
            })
        if self._viewport_canvas_size == (width, height):
            return
        self._viewport_canvas_size = (width, height)
        self.viewport.delete("viewport-grid")
        for x in range(0, width, 48):
            self.viewport.create_line(
                x, 0, x, height, fill="#18231e", tags="viewport-grid",
            )
        for y in range(0, height, 48):
            self.viewport.create_line(
                0, y, width, y, fill="#18231e", tags="viewport-grid",
            )
        self.viewport.tag_lower("viewport-grid")
        self.viewport.coords(items["empty"], width / 2, height / 2)
        self.viewport.itemconfigure(items["empty"], width=max(200, width - 80))
        self.viewport.coords(items["top"], 0, 0, width, 25)
        self.viewport.coords(items["summary"], 9, 13)
        self.viewport.itemconfigure(items["summary"], width=max(100, width - 18))
        self.viewport.coords(items["help"], width - 9, 13)
        self.viewport.coords(items["bottom"], 0, max(0, height - 25), width, height)
        self.viewport.coords(items["status"], 9, height - 12)
        self.viewport.itemconfigure(items["status"], width=max(100, width - 18))
