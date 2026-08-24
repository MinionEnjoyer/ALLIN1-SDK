"""Visual node editor for RPF package graphs."""

from __future__ import annotations

import queue
import hashlib
import io
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk, UnidentifiedImageError

from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.package_relations import PackageRelationshipAnalyzer
from allin1_sdk.rpf_graph_previews import (
    ASSET_PREVIEW_HEIGHT,
    ASSET_PREVIEW_WIDTH,
    AssetPreviewRequest,
    render_asset_preview,
    render_graph_preview_bundle,
)
from allin1_sdk.rpf_program_ui import RpfProgramFrame
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.ui_foundation import place_window


NODE_WIDTH = 270
NODE_HEIGHT = 82
CANVAS_LIMIT = 2500
MAX_QUEUED_ASSET_PREVIEWS = 96
MIN_ZOOM = 0.35
MAX_ZOOM = 2.0
ZOOM_FACTOR = 1.2


class _GraphWorkDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, message: str, work, completed) -> None:
        super().__init__(parent)
        self.title(title)
        place_window(self, preferred=(520, 145), minimum=(440, 130))
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=message, wraplength=470, justify="left").pack(
            fill="x", pady=(0, 12),
        )
        ttk.Progressbar(frame, mode="indeterminate").pack(fill="x")
        frame.winfo_children()[-1].start(10)
        ttk.Label(
            frame, text="Referenced source files remain unchanged.",
            foreground="#52635c",
        ).pack(anchor="w", pady=(10, 0))
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.completed = completed

        def runner() -> None:
            try:
                self.events.put(("result", work()))
            except Exception as exc:
                self.events.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()
        self.after(80, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload = self.events.get_nowait()
        except queue.Empty:
            self.after(80, self._poll)
            return
        self.grab_release()
        self.destroy()
        if kind == "error":
            messagebox.showerror("RPF graph operation failed", str(payload), parent=self.master)
        else:
            self.completed(payload)


class RpfPackageGraphFrame(ttk.Frame):
    """Embeddable visual graph backed by the same CLI/API graph document."""

    COLORS = {
        "archive": ("#6D4AA0", "#251D32"),
        "sealed_archive": ("#A56B20", "#302416"),
        "directory": ("#23815A", "#182A23"),
        "file": ("#2E6D98", "#172731"),
        "vehicle_entity": ("#C14775", "#321B25"),
    }
    RELATION_COLORS = {
        "assets": "#48A9E6",
        "metadata": "#54C987",
        "tuning": "#C88AF4",
        "registration": "#E6B84A",
    }
    RELATION_FILTERS = {
        "All relationships": None,
        "Assets": "assets",
        "Metadata": "metadata",
        "Tuning": "tuning",
        "Registration": "registration",
        "Containment only": "containment",
    }

    def __init__(
        self, parent: tk.Misc, graph: str | Path, project_root: str | Path,
        game_path: str | Path | None = None,
        *, on_close=None, on_open_asset=None, on_open_vehicle=None,
        initial_select: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.graph = Path(graph).resolve()
        self.project_root = Path(project_root).resolve()
        self.game_path = Path(game_path).resolve() if game_path else None
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self._on_open_vehicle = on_open_vehicle
        self.graph_state: dict = {}
        self.semantic_entities: dict[str, dict] = {}
        self.visible: set[str] = set()
        self.collapsed: set[str] = set()
        self.edge_items: dict[tuple[str, str], int] = {}
        self.relation_edge_items: dict[tuple[str, str, str], int] = {}
        self.selected: str | None = None
        self.dragging: str | None = None
        self.drag_last = (0.0, 0.0)
        self.connecting_parent: str | None = None
        self.connection_line: int | None = None
        self.query = tk.StringVar()
        self.relation_filter = tk.StringVar(value="All relationships")
        self.zoom = 1.0
        self.zoom_text = tk.StringVar(value="100%")
        self.status = tk.StringVar(value="Loading validated package graph…")
        self.detail_name = tk.StringVar(value="Nothing selected")
        self.detail_type = tk.StringVar(value="")
        self.detail_id = tk.StringVar(value="")
        self.detail_parent = tk.StringVar(value="")
        self.detail_source = tk.StringVar(value="")
        self.detail_metadata = tk.StringVar(value="")
        self.detail_findings = tk.StringVar(value="")
        self._preview_requests: queue.Queue[AssetPreviewRequest | None] = queue.Queue()
        self._preview_results: queue.Queue[
            tuple[str, str, bytes | None, str | None]
        ] = queue.Queue()
        self._preview_pending: set[str] = set()
        self._preview_keys: dict[str, str] = {}
        self._preview_images: dict[str, Image.Image] = {}
        self._preview_photos: dict[str, ImageTk.PhotoImage] = {}
        self._preview_messages: dict[str, str] = {}
        self._preview_stop = threading.Event()
        self._preview_worker_thread = threading.Thread(
            target=self._asset_preview_worker, daemon=True,
            name="allin1-rpf-asset-previews",
        )
        self._preview_worker_thread.start()
        self._build_ui()
        self._reload(initial_select)
        # Tk can preserve the canvas' far-edge view while a toplevel is first
        # mapped. Always present the package root on initial open.
        self.after_idle(self._focus_initial_view)
        self.after(150, self._focus_initial_view)
        if initial_select is not None:
            self.after(220, self._focus_selected)
        self.after(90, self._poll_asset_previews)
        for widget in (self, self.canvas):
            widget.bind("<Control-plus>", lambda _event: self._zoom_by(ZOOM_FACTOR))
            widget.bind("<Control-equal>", lambda _event: self._zoom_by(ZOOM_FACTOR))
            widget.bind(
                "<Control-minus>", lambda _event: self._zoom_by(1 / ZOOM_FACTOR),
            )
            widget.bind("<Control-0>", lambda _event: self._reset_zoom())

    def _focus_initial_view(self) -> None:
        if not self.winfo_exists():
            return
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header, text="Package node graph", font=("Segoe UI Semibold", 18),
            foreground="#1f7f42",
        ).pack(side="left")
        ttk.Label(
            header, text=str(self.graph), foreground="#52635c",
        ).pack(side="left", padx=(14, 0))
        ttk.Button(header, text="Validate", command=self._validate_sources).pack(side="right")
        self.analyze_links_button = ttk.Button(
            header, text="Analyze links", command=self._analyze_links,
        )
        self.analyze_links_button.pack(
            side="right", padx=(0, 6),
        )
        ttk.Button(header, text="Refresh sources", command=self._refresh_sources).pack(
            side="right", padx=(0, 6),
        )
        ttk.Button(header, text="Auto layout", command=self._auto_layout).pack(
            side="right", padx=(0, 6),
        )

        authoring_bar = ttk.Frame(outer)
        authoring_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(
            authoring_bar, text="AUTHORING", font=("Segoe UI Semibold", 9),
            foreground="#52635c",
        ).pack(side="left", padx=(0, 7))
        add_button = ttk.Menubutton(authoring_bar, text="Add to package")
        add_menu = tk.Menu(add_button, tearoff=False)
        add_menu.add_command(label="Directory…", command=self._add_directory)
        add_menu.add_command(label="Nested RPF…", command=self._add_archive)
        add_menu.add_command(label="Source file…", command=self._add_file)
        add_button.configure(menu=add_menu)
        add_button.pack(side="left")
        ttk.Button(
            authoring_bar, text="Rename selected…", command=self._rename,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            authoring_bar, text="Remove selected…", command=self._remove,
        ).pack(side="left", padx=(6, 0))
        output_button = ttk.Menubutton(authoring_bar, text="Create output")
        output_menu = tk.Menu(output_button, tearoff=False)
        output_menu.add_command(
            label="Materialize loose source…", command=self._materialize,
        )
        output_menu.add_command(
            label="Build + exactly verify RPF…", command=self._build_archive,
        )
        output_menu.add_command(
            label="Plan changes to imported origin…",
            command=self._plan_origin_changes,
        )
        output_menu.add_separator()
        output_menu.add_command(
            label="Export preview bundle…",
            command=self._export_preview_bundle,
        )
        output_button.configure(menu=output_menu)
        output_button.pack(side="left", padx=(12, 0))
        self.close_button = ttk.Button(
            authoring_bar, text="Close graph", command=self._close_panel,
        )
        self.close_button.pack(side="right")

        ttk.Label(outer, textvariable=self.status, foreground="#52635c").pack(
            side="bottom", fill="x", pady=(7, 0),
        )

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        layout_tab = ttk.Frame(notebook, padding=4)
        program_tab = ttk.Frame(notebook, padding=4)
        notebook.add(layout_tab, text="Package Layout")
        notebook.add(program_tab, text="Build Flow")

        work = ttk.Panedwindow(layout_tab, orient="horizontal")
        work.pack(fill="both", expand=True)
        canvas_frame = ttk.Frame(work)
        inspector = ttk.Frame(work, padding=(14, 4, 4, 4), width=315)
        work.add(canvas_frame, weight=5)
        work.add(inspector, weight=1)

        search = ttk.Frame(canvas_frame)
        search.pack(fill="x", pady=(0, 6))
        ttk.Label(search, text="Find node").pack(side="left")
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda _event: self._focus_search())
        ttk.Button(search, text="Focus", command=self._focus_search).pack(side="left")
        ttk.Separator(search, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(
            search, text="−", width=3, command=lambda: self._zoom_by(1 / ZOOM_FACTOR),
        ).pack(side="left")
        ttk.Label(search, textvariable=self.zoom_text, width=6, anchor="center").pack(
            side="left", padx=3,
        )
        ttk.Button(
            search, text="+", width=3, command=lambda: self._zoom_by(ZOOM_FACTOR),
        ).pack(side="left")
        ttk.Button(search, text="Fit", command=self._fit_graph).pack(side="left", padx=(6, 0))
        ttk.Button(search, text="100%", command=self._reset_zoom).pack(
            side="left", padx=(4, 0),
        )
        ttk.Button(
            search, text="Collapse", command=self._toggle_selected_collapse,
        ).pack(side="left", padx=(10, 0))
        ttk.Button(search, text="Expand all", command=self._expand_all).pack(
            side="left", padx=(4, 0),
        )
        ttk.Label(search, text="Links").pack(side="left", padx=(12, 4))
        relationship_filter = ttk.Combobox(
            search, textvariable=self.relation_filter,
            values=tuple(self.RELATION_FILTERS), state="readonly", width=18,
        )
        relationship_filter.pack(side="left")
        relationship_filter.bind("<<ComboboxSelected>>", lambda _event: self._render())
        legend = ttk.Frame(canvas_frame)
        legend.pack(fill="x", pady=(0, 5))
        ttk.Label(legend, text="Legend:", foreground="#52635c").pack(side="left")
        for label, color in (
            ("contains", "#50655C"), ("asset", self.RELATION_COLORS["assets"]),
            ("metadata", self.RELATION_COLORS["metadata"]),
            ("tuning", self.RELATION_COLORS["tuning"]),
            ("registration / target", self.RELATION_COLORS["registration"]),
        ):
            tk.Label(
                legend, text=f" ● {label}", foreground=color,
                background="#f4f7f5", font=("Segoe UI Semibold", 8),
            ).pack(side="left", padx=(4, 0))
        canvas_host = tk.Frame(canvas_frame, background="#111714")
        canvas_host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            canvas_host, background="#111714", highlightthickness=0,
            scrollregion=(0, 0, 5000, 5000), takefocus=True,
        )
        x_scroll = ttk.Scrollbar(canvas_host, orient="horizontal", command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(canvas_host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        canvas_host.grid_rowconfigure(0, weight=1)
        canvas_host.grid_columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._zoom_mousewheel)
        self.canvas.bind("<Double-1>", self._activate_selected)
        self.canvas.bind("<Return>", self._activate_selected)
        self.canvas.bind("<Button-3>", self._show_context_menu)

        ttk.Label(
            inspector, text="Node inspector", font=("Segoe UI Semibold", 14),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Separator(inspector).pack(fill="x", pady=(7, 10))
        ttk.Label(
            inspector, textvariable=self.detail_name, font=("Segoe UI Semibold", 11),
            wraplength=285, justify="left",
        ).pack(anchor="w")
        for variable in (
            self.detail_type, self.detail_id, self.detail_parent, self.detail_source,
            self.detail_metadata, self.detail_findings,
        ):
            ttk.Label(
                inspector, textvariable=variable, foreground="#52635c",
                wraplength=285, justify="left",
            ).pack(anchor="w", pady=(3, 0))

        self.expand_sealed_button = ttk.Button(
            inspector, text="Expand sealed RPF into nodes…",
            command=self._expand_selected_sealed, state="disabled",
        )
        self.expand_sealed_button.pack(fill="x", pady=(14, 0))
        self.open_asset_button = ttk.Button(
            inspector, text="Open in Asset Viewer",
            command=self._open_selected_asset, state="disabled",
        )
        self.open_asset_button.pack(fill="x", pady=(6, 0))
        self.open_vehicle_button = ttk.Button(
            inspector, text="Open in Vehicle Workbench",
            command=self._open_selected_vehicle, state="disabled",
        )
        self.open_vehicle_button.pack(fill="x", pady=(6, 0))

        ttk.Label(
            inspector,
            text=(
                "Use the Authoring bar to add, rename, or remove nodes and to create "
                "verified outputs."
            ),
            foreground="#52635c", wraplength=285, justify="left",
        ).pack(anchor="w", pady=(18, 0))
        self.program_frame = RpfProgramFrame(
            program_tab, self.graph, self.project_root, self.game_path,
            on_busy_change=self._set_program_busy,
        )
        self.program_frame.pack(fill="both", expand=True)

    def _set_program_busy(self, busy: bool) -> None:
        button = getattr(self, "close_button", None)
        if button is not None and button.winfo_exists():
            button.configure(state="disabled" if busy else "normal")

    def has_active_work(self) -> bool:
        program = getattr(self, "program_frame", None)
        return bool(program is not None and program.winfo_exists() and program.busy)

    def _close_panel(self) -> bool:
        if self.has_active_work():
            messagebox.showinfo(
                "Build flow still running",
                "Wait for the current dry run or build to finish before closing this "
                "package graph.", parent=self,
            )
            return False
        if self._on_close is not None:
            self._on_close()
        else:
            self.destroy()
        return True

    def destroy(self) -> None:
        stop = getattr(self, "_preview_stop", None)
        if stop is not None:
            stop.set()
        worker = getattr(self, "_preview_worker_thread", None)
        if worker is not None and worker.is_alive():
            while True:
                try:
                    self._preview_requests.get_nowait()
                except queue.Empty:
                    break
            self._preview_requests.put(None)
        super().destroy()

    def _reload(self, select: str | None = None) -> None:
        try:
            self.graph_state = RpfPackageGraph.validate(self.graph, verify_sources=False)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF graph validation failed", str(exc), parent=self)
            self.status.set("Graph validation failed; the document was not changed.")
            return
        semantic = self.graph_state.get("semantic") or {}
        self.semantic_entities = {
            item["id"]: {**item, "type": "vehicle_entity"}
            for item in semantic.get("entities", [])
        }
        origin = self.graph_state.get("payload", {}).get("origin")
        self.analyze_links_button.configure(
            state=(
                "normal" if isinstance(origin, dict)
                and origin.get("type") == "mod_package_import" else "disabled"
            ),
        )
        valid_nodes = set(self.graph_state["nodes"]) | set(self.semantic_entities)
        self.collapsed.intersection_update(valid_nodes)
        self._preview_pending.intersection_update(valid_nodes)
        for cache in (
            self._preview_keys, self._preview_images, self._preview_photos,
            self._preview_messages,
        ):
            for node_id in tuple(cache):
                if node_id not in valid_nodes:
                    cache.pop(node_id, None)
        self.selected = select if select in valid_nodes else self.selected
        if self.selected not in valid_nodes:
            self.selected = self.graph_state["root_id"]
        self._render()
        self._show_selected()

    def _render(self) -> None:
        self.canvas.delete("all")
        nodes = list(self.graph_state["nodes"])
        ordered: list[str] = []

        def include(node_id: str) -> None:
            ordered.append(node_id)
            if node_id in self.collapsed:
                return
            for child in self.graph_state["children"][node_id]:
                include(child)

        include(self.graph_state["root_id"])
        selected_filter = self.RELATION_FILTERS.get(self.relation_filter.get())
        semantic_order = (
            [] if selected_filter == "containment"
            else sorted(
                self.semantic_entities,
                key=lambda item: (
                    self.semantic_entities[item]["name"].casefold(),
                    self.semantic_entities[item]["edition"].casefold(),
                ),
            )
        )
        ordered.extend(semantic_order)
        self.visible = set(ordered[:CANVAS_LIMIT])
        max_x = max((self._node(node)["x"] for node in self.visible), default=0)
        max_y = max((self._node(node)["y"] for node in self.visible), default=0)
        width = max(
            self.canvas.winfo_width(), int((max_x + 500) * self.zoom),
        )
        height = max(
            self.canvas.winfo_height(), int((max_y + 300) * self.zoom),
        )
        grid = max(35, round(100 * self.zoom))
        for x in range(0, width + 1, grid):
            self.canvas.create_line(x, 0, x, height, fill="#18211D", tags=("grid",))
        for y in range(0, height + 1, grid):
            self.canvas.create_line(0, y, width, y, fill="#18211D", tags=("grid",))
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self.edge_items.clear()
        self.relation_edge_items.clear()
        for child, parent in self.graph_state["parents"].items():
            if child not in self.visible or parent not in self.visible:
                continue
            item = self.canvas.create_line(
                0, 0, 0, 0, 0, 0, 0, 0, smooth=True, splinesteps=20,
                width=3, fill="#50655C", tags=("edge",),
            )
            self.edge_items[(parent, child)] = item
        if selected_filter != "containment":
            semantic = self.graph_state.get("semantic") or {}
            for relation in semantic.get("relations", []):
                if (
                    relation["source"] not in self.visible
                    or relation["target"] not in self.visible
                    or (
                        selected_filter is not None
                        and relation["group"] != selected_filter
                    )
                ):
                    continue
                key = (relation["source"], relation["target"], relation["type"])
                self.relation_edge_items[key] = self.canvas.create_line(
                    0, 0, 0, 0, 0, 0, 0, 0, smooth=True, splinesteps=20,
                    width=3 if relation.get("required") else 2,
                    dash=() if relation.get("required") else (7, 4),
                    fill=self.RELATION_COLORS[relation["group"]],
                    tags=("semantic-edge", f"relation:{relation['type']}"),
                )
        for node_id in ordered:
            if node_id not in self.visible:
                continue
            self._draw_node(node_id)
        self._update_edges()
        hidden = len(nodes) + len(self.semantic_entities) - len(self.visible)
        note = f" · {hidden:,} nodes collapsed/hidden" if hidden else ""
        semantic = self.graph_state.get("semantic") or {}
        relation_count = len(semantic.get("relations", []))
        finding_count = len(semantic.get("findings", []))
        self.status.set(
            f"{len(nodes):,} nodes · {len(self.graph_state['parents']):,} links · "
            f"{relation_count:,} semantic links · {finding_count:,} findings · "
            f"{self.graph_state['file_count']:,} sources · "
            f"{self.graph_state['sealed_archive_count']:,} sealed RPFs · "
            f"{self.graph_state['byte_count']:,} bytes{note}"
        )

    def _draw_node(self, node_id: str) -> None:
        node = self._node(node_id)
        x, y = node["x"] * self.zoom, node["y"] * self.zoom
        node_width, node_height = NODE_WIDTH * self.zoom, NODE_HEIGHT * self.zoom
        shadow = max(2, 5 * self.zoom)
        header_height = 27 * self.zoom
        font_header = max(6, round(9 * self.zoom))
        font_name = max(6, round(10 * self.zoom))
        font_detail = max(5, round(8 * self.zoom))
        header, body = self.COLORS[node["type"]]
        tags = ("node", f"node:{node_id}")
        self.canvas.create_rectangle(
            x + shadow, y + shadow, x + node_width + shadow, y + node_height + shadow,
            fill="#080C0A", outline="", tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + node_width, y + node_height,
            fill=body, outline="#E7B94B" if node_id == self.selected else "#53635C",
            width=3 if node_id == self.selected else 1, tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + node_width, y + header_height,
            fill=header, outline="", tags=tags,
        )
        self.canvas.create_text(
            x + 10 * self.zoom, y + 13 * self.zoom,
            text=(
                "SEALED RPF" if node["type"] == "sealed_archive"
                else "VEHICLE SYSTEM" if node["type"] == "vehicle_entity"
                else node["type"].upper()
            ), anchor="w",
            fill="#FFFFFF", font=("Segoe UI Semibold", font_header), tags=tags,
        )
        if node["type"] in {"archive", "directory"}:
            self.canvas.create_text(
                x + node_width - 10 * self.zoom, y + 13 * self.zoom,
                text="+" if node_id in self.collapsed else "−", anchor="e",
                fill="#FFFFFF", font=("Segoe UI Semibold", font_header), tags=tags,
            )
        is_file = node["type"] == "file"
        is_source = node["type"] in {"file", "sealed_archive"}
        text_width = (NODE_WIDTH - 116 if is_file else NODE_WIDTH - 20) * self.zoom
        self.canvas.create_text(
            x + 10 * self.zoom, y + 49 * self.zoom,
            text=node["name"], anchor="w", width=text_width,
            fill="#F0F5F2", font=("Segoe UI Semibold", font_name), tags=tags,
        )
        subtitle = (
            f"{node['edition']} · vehicle system"
            if node["type"] == "vehicle_entity"
            else node_id if not is_source else f"{node['size']:,} bytes"
        )
        self.canvas.create_text(
            x + 10 * self.zoom, y + 68 * self.zoom,
            text=subtitle, anchor="w", width=text_width,
            fill="#9FB0A8", font=("Consolas", font_detail), tags=tags,
        )
        if is_file:
            self._draw_asset_preview(node_id, node, x, y, tags)
        if node_id in self.graph_state["nodes"] and node_id != self.graph_state["root_id"]:
            port_radius = max(4, 7 * self.zoom)
            port_y = y + 41 * self.zoom
            self.canvas.create_oval(
                x - port_radius, port_y - port_radius,
                x + port_radius, port_y + port_radius,
                fill="#D9E4DF", outline="#111714",
                width=2, tags=(*tags, f"in:{node_id}"),
            )
        if node["type"] in {"archive", "directory"}:
            port_radius = max(4, 7 * self.zoom)
            port_x, port_y = x + node_width, y + 41 * self.zoom
            self.canvas.create_oval(
                port_x - port_radius, port_y - port_radius,
                port_x + port_radius, port_y + port_radius,
                fill="#E7B94B", outline="#111714", width=2,
                tags=(*tags, f"out:{node_id}"),
            )

    def _node(self, node_id: str) -> dict:
        node = self.graph_state.get("nodes", {}).get(node_id)
        if node is not None:
            return node
        return self.semantic_entities[node_id]

    def _draw_asset_preview(
        self, node_id: str, node: dict, x: float, y: float,
        tags: tuple[str, ...],
    ) -> None:
        preview_width = max(1, round(ASSET_PREVIEW_WIDTH * self.zoom))
        preview_height = max(1, round(ASSET_PREVIEW_HEIGHT * self.zoom))
        left = x + (NODE_WIDTH - ASSET_PREVIEW_WIDTH - 8) * self.zoom
        top = y + 31 * self.zoom
        border = max(1, 2 * self.zoom)
        self.canvas.create_rectangle(
            left - border, top - border,
            left + preview_width + border, top + preview_height + border,
            fill="#09100D", outline="#466258", width=1, tags=tags,
        )
        self._queue_asset_preview(node_id, node)
        preview_image = self._preview_images.get(node_id)
        if preview_image is not None:
            photo = ImageTk.PhotoImage(
                preview_image.resize(
                    (preview_width, preview_height), Image.Resampling.LANCZOS,
                ),
                master=self,
            )
            self._preview_photos[node_id] = photo
            self.canvas.create_image(
                left, top, image=photo, anchor="nw", tags=tags,
            )
        else:
            suffix = Path(str(node.get("name", ""))).suffix.upper().lstrip(".")
            label = "…" if node_id in self._preview_pending else (suffix or "FILE")
            self.canvas.create_text(
                left + preview_width / 2,
                top + preview_height / 2,
                text=label[:9], fill="#B8CDC2",
                font=("Segoe UI Semibold", max(5, round(8 * self.zoom))), tags=tags,
            )

    def _graph_edition(self) -> str:
        origin = self.graph_state.get("payload", {}).get("origin", {})
        authored = origin.get("edition") if isinstance(origin, dict) else None
        if isinstance(authored, str) and authored.casefold() in {"legacy", "enhanced"}:
            return authored.title()
        if self.game_path is not None and (self.game_path / "GTA5.exe").is_file():
            return "Legacy"
        return "Enhanced"

    def _queue_asset_preview(self, node_id: str, node: dict) -> None:
        if self._preview_stop.is_set():
            return
        source_value = node.get("source")
        expected_hash = str(node.get("sha256", "")).casefold()
        expected_size = node.get("size")
        if (
            not isinstance(source_value, str) or not source_value
            or not isinstance(expected_size, int) or isinstance(expected_size, bool)
            or len(expected_hash) != 64
        ):
            self._preview_messages[node_id] = "No hash-bound source preview is available"
            return
        edition = self._graph_edition()
        source = Path(source_value).expanduser()
        key = hashlib.sha256(
            f"{source}|{expected_size}|{expected_hash}|{edition}".encode("utf-8")
        ).hexdigest()
        if self._preview_keys.get(node_id) == key and (
            node_id in self._preview_images or node_id in self._preview_messages
        ):
            return
        if node_id in self._preview_pending and self._preview_keys.get(node_id) == key:
            return
        if len(self._preview_pending) >= MAX_QUEUED_ASSET_PREVIEWS:
            self._preview_messages[node_id] = "Preview queue limit reached"
            return
        self._preview_keys[node_id] = key
        self._preview_images.pop(node_id, None)
        self._preview_photos.pop(node_id, None)
        self._preview_messages.pop(node_id, None)
        self._preview_pending.add(node_id)
        self._preview_requests.put(AssetPreviewRequest(
            node_id, source, expected_size, expected_hash, edition, key,
        ))

    def _asset_preview_worker(self) -> None:
        while not self._preview_stop.is_set():
            request = self._preview_requests.get()
            if request is None or self._preview_stop.is_set():
                return
            try:
                preview = render_asset_preview(
                    request, self.project_root, self.game_path,
                )
                if self._preview_stop.is_set():
                    return
                self._preview_results.put(
                    (request.node_id, request.cache_key, preview, None)
                )
            except (
                OSError, RuntimeError, ValueError, UnidentifiedImageError,
                Image.DecompressionBombError,
            ) as exc:
                if self._preview_stop.is_set():
                    return
                self._preview_results.put(
                    (request.node_id, request.cache_key, None, str(exc))
                )

    def _poll_asset_previews(self) -> None:
        if self._preview_stop.is_set():
            return
        changed = False
        while True:
            try:
                node_id, key, preview, error = self._preview_results.get_nowait()
            except queue.Empty:
                break
            if self._preview_keys.get(node_id) != key:
                continue
            self._preview_pending.discard(node_id)
            if preview is None:
                self._preview_messages[node_id] = error or "Preview unavailable"
                changed = True
                continue
            try:
                with Image.open(io.BytesIO(preview)) as image:
                    rendered = image.convert("RGB").copy()
                self._preview_images[node_id] = rendered
                self._preview_photos.pop(node_id, None)
                self._preview_messages.pop(node_id, None)
                changed = True
            except (
                OSError, UnidentifiedImageError, Image.DecompressionBombError,
            ) as exc:
                self._preview_messages[node_id] = str(exc)
        if changed and self.winfo_exists():
            self._render()
        if self.winfo_exists():
            self.after(90, self._poll_asset_previews)

    def _update_edges(self) -> None:
        for (parent, child), item in self.edge_items.items():
            source, target = self.graph_state["nodes"][parent], self.graph_state["nodes"][child]
            x1 = (source["x"] + NODE_WIDTH) * self.zoom
            y1 = (source["y"] + 41) * self.zoom
            x2 = target["x"] * self.zoom
            y2 = (target["y"] + 41) * self.zoom
            curve = max(60, abs(x2 - x1) * 0.45)
            self.canvas.coords(item, x1, y1, x1 + curve, y1, x2 - curve, y2, x2, y2)
        for (source_id, target_id, _kind), item in self.relation_edge_items.items():
            source, target = self._node(source_id), self._node(target_id)
            x1 = source["x"] * self.zoom
            y1 = (source["y"] + 41) * self.zoom
            x2 = (target["x"] + NODE_WIDTH) * self.zoom
            y2 = (target["y"] + 41) * self.zoom
            curve = max(60, abs(x2 - x1) * 0.4)
            self.canvas.coords(item, x1, y1, x1 - curve, y1, x2 + curve, y2, x2, y2)

    def _tags_at_current(self) -> tuple[str, ...]:
        current = self.canvas.find_withtag("current")
        return self.canvas.gettags(current[-1]) if current else ()

    def _node_at(self, event: tk.Event) -> str | None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(x - 3, y - 3, x + 3, y + 3)):
            node_id = self._tag_value(self.canvas.gettags(item), "node:")
            if node_id is not None:
                return node_id
        return None

    @staticmethod
    def _tag_value(tags: tuple[str, ...], prefix: str) -> str | None:
        return next((tag[len(prefix):] for tag in tags if tag.startswith(prefix)), None)

    def _press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        tags = self._tags_at_current()
        node_id = self._tag_value(tags, "node:")
        if node_id is None:
            self._select(None)
            return
        self._select(node_id)
        output = self._tag_value(tags, "out:")
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if output:
            self.connecting_parent = output
            source = self._node(output)
            x1 = (source["x"] + NODE_WIDTH) * self.zoom
            y1 = (source["y"] + 41) * self.zoom
            self.connection_line = self.canvas.create_line(
                x1, y1, x, y, fill="#E7B94B", width=3, dash=(7, 4),
                tags=("connection-preview",),
            )
            return
        self.dragging = node_id
        self.drag_last = (x, y)

    def _motion(self, event: tk.Event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.connecting_parent and self.connection_line:
            source = self._node(self.connecting_parent)
            self.canvas.coords(
                self.connection_line,
                (source["x"] + NODE_WIDTH) * self.zoom,
                (source["y"] + 41) * self.zoom, x, y,
            )
            return
        if not self.dragging:
            return
        dx, dy = x - self.drag_last[0], y - self.drag_last[1]
        node = self._node(self.dragging)
        node["x"], node["y"] = (
            node["x"] + dx / self.zoom, node["y"] + dy / self.zoom,
        )
        self.canvas.move(f"node:{self.dragging}", dx, dy)
        self.drag_last = (x, y)
        self._update_edges()

    def _release(self, event: tk.Event) -> None:
        if self.connecting_parent:
            parent = self.connecting_parent
            child = self._node_at(event)
            self.connecting_parent = None
            if self.connection_line:
                self.canvas.delete(self.connection_line)
                self.connection_line = None
            if child in self.graph_state["nodes"] and child != parent:
                try:
                    RpfPackageGraph.reparent_node(self.graph, child, parent)
                except (OSError, ValueError) as exc:
                    messagebox.showerror("Could not connect graph nodes", str(exc), parent=self)
                self._reload(child)
            return
        if self.dragging:
            node_id = self.dragging
            node = self._node(node_id)
            self.dragging = None
            try:
                if node_id in self.semantic_entities:
                    RpfPackageGraph.set_semantic_position(
                        self.graph, node_id, node["x"], node["y"],
                    )
                else:
                    RpfPackageGraph.set_position(
                        self.graph, node_id, node["x"], node["y"],
                    )
            except (OSError, ValueError) as exc:
                messagebox.showerror("Could not save node position", str(exc), parent=self)
                self._reload(node_id)

    def _mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _shift_mousewheel(self, event: tk.Event) -> None:
        self.canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")

    def _zoom_mousewheel(self, event: tk.Event) -> str:
        self._zoom_by(
            ZOOM_FACTOR if event.delta > 0 else 1 / ZOOM_FACTOR,
            event.x, event.y,
        )
        return "break"

    def _zoom_by(
        self, factor: float, focus_x: float | None = None,
        focus_y: float | None = None,
    ) -> None:
        self._set_zoom(self.zoom * factor, focus_x, focus_y)

    def _set_zoom(
        self, value: float, focus_x: float | None = None,
        focus_y: float | None = None,
    ) -> None:
        target = max(MIN_ZOOM, min(MAX_ZOOM, value))
        if abs(target - self.zoom) < 0.001:
            return
        view_x = self.canvas.winfo_width() / 2 if focus_x is None else focus_x
        view_y = self.canvas.winfo_height() / 2 if focus_y is None else focus_y
        logical_x = self.canvas.canvasx(view_x) / self.zoom
        logical_y = self.canvas.canvasy(view_y) / self.zoom
        self.zoom = target
        self.zoom_text.set(f"{round(self.zoom * 100):d}%")
        self._preview_photos.clear()
        self._render()
        region = [float(value) for value in self.canvas.cget("scrollregion").split()]
        width, height = max(1.0, region[2]), max(1.0, region[3])
        left = logical_x * self.zoom - view_x
        top = logical_y * self.zoom - view_y
        self.canvas.xview_moveto(max(0.0, min(1.0, left / width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, top / height)))

    def _reset_zoom(self) -> None:
        self._set_zoom(1.0)

    def _fit_graph(self) -> None:
        if not self.visible:
            return
        max_x = max(self._node(node)["x"] for node in self.visible)
        max_y = max(self._node(node)["y"] for node in self.visible)
        available_width = max(1, self.canvas.winfo_width() - 40)
        available_height = max(1, self.canvas.winfo_height() - 40)
        target = min(
            available_width / max(1, max_x + NODE_WIDTH + 20),
            available_height / max(1, max_y + NODE_HEIGHT + 20),
        )
        self._set_zoom(target, 0, 0)
        self.canvas.xview_moveto(0.0)
        self.canvas.yview_moveto(0.0)

    def _select(self, node_id: str | None) -> None:
        self.selected = node_id
        self._render()
        self._show_selected()

    def _show_selected(self) -> None:
        node = (
            self._node(self.selected) if self.selected in (
                set(self.graph_state.get("nodes", {})) | set(self.semantic_entities)
            ) else None
        )
        if node is None:
            self.detail_name.set("Nothing selected")
            for variable in (
                self.detail_type, self.detail_id, self.detail_parent, self.detail_source,
                self.detail_metadata, self.detail_findings,
            ):
                variable.set("")
            self.expand_sealed_button.configure(state="disabled")
            self.open_asset_button.configure(state="disabled")
            self.open_vehicle_button.configure(state="disabled")
            return
        self.detail_name.set(node["name"])
        authored_type = (
            "vehicle system" if node["type"] == "vehicle_entity" else node["type"]
        )
        self.detail_type.set(f"Type: {authored_type}")
        self.detail_id.set(f"ID: {node['id']}")
        parent = self.graph_state["parents"].get(node["id"])
        self.detail_parent.set(
            f"Parent: {parent or ('(semantic overlay)' if node['type'] == 'vehicle_entity' else '(package root)')}"
        )
        expanded = node.get("expanded_from")
        source = node.get("source") or (
            expanded.get("path") if isinstance(expanded, dict) else None
        )
        source = source or node.get("source_root")
        self.detail_source.set(f"Source: {source or '(generated container)'}")
        semantic = self.graph_state.get("semantic") or {}
        if node["type"] == "vehicle_entity":
            metadata = node.get("metadata", {})
            tuning = ", ".join(metadata.get("tuning_kits", [])) or "none"
            relation_groups = {}
            for relation in semantic.get("relations", []):
                if relation.get("source") == node["id"]:
                    relation_groups[relation["group"]] = (
                        relation_groups.get(relation["group"], 0) + 1
                    )
            linked = " · ".join(
                f"{name}: {count}" for name, count in relation_groups.items()
            ) or "none"
            self.detail_metadata.set(
                f"Edition: {node['edition']}\nHandling: "
                f"{metadata.get('handling_id') or '(none)'}\nTexture: "
                f"{metadata.get('texture_dictionary') or '(none)'}\n"
                f"Tuning kits: {tuning}\nLinks: {linked}"
            )
        else:
            linked_from = []
            for relation in semantic.get("relations", []):
                if relation.get("target") != node["id"]:
                    continue
                entity = self.semantic_entities.get(relation.get("source"))
                if entity is not None:
                    linked_from.append(
                        f"{relation['label']}: {entity['name']} ({entity['edition']})"
                    )
            self.detail_metadata.set(
                "Relationships:\n" + "\n".join(linked_from[:8])
                if linked_from else ""
            )
        related_findings = [
            item for item in semantic.get("findings", [])
            if item.get("entity_id") == node["id"] or item.get("node_id") == node["id"]
        ]
        self.detail_findings.set(
            "Findings: none" if not related_findings else
            "Findings:\n" + "\n".join(
                f"{item['severity'].upper()} · {item['message']}"
                for item in related_findings[:5]
            )
        )
        self.expand_sealed_button.configure(
            state="normal" if node["type"] == "sealed_archive" else "disabled",
        )
        self.open_asset_button.configure(
            state=(
                "normal" if node["type"] == "file"
                and self._on_open_asset is not None else "disabled"
            ),
        )
        self.open_vehicle_button.configure(
            state=(
                "normal" if node["type"] == "vehicle_entity"
                and self._on_open_vehicle is not None else "disabled"
            ),
        )

    def _activate_selected(self, _event: tk.Event | None = None) -> None:
        node = self.graph_state.get("nodes", {}).get(self.selected)
        if node is not None and node["type"] == "sealed_archive":
            self._expand_selected_sealed()
        elif node is not None and node["type"] in {"archive", "directory"}:
            self._toggle_selected_collapse()

    def _toggle_selected_collapse(self) -> None:
        node = self.graph_state.get("nodes", {}).get(self.selected)
        if node is None or node["type"] not in {"archive", "directory"}:
            return
        if node["id"] in self.collapsed:
            self.collapsed.remove(node["id"])
        else:
            self.collapsed.add(node["id"])
        self._render()
        self._show_selected()

    def _expand_all(self) -> None:
        self.collapsed.clear()
        self._render()
        self._show_selected()

    def _show_context_menu(self, event: tk.Event) -> None:
        node_id = self._node_at(event)
        if node_id is None:
            return
        self._select(node_id)
        node = self._node(node_id)
        menu = tk.Menu(self, tearoff=False)
        if node["type"] in {"archive", "directory"}:
            menu.add_command(
                label="Expand children" if node_id in self.collapsed else "Collapse children",
                command=self._toggle_selected_collapse,
            )
        if node["type"] == "sealed_archive":
            menu.add_command(
                label="Expand sealed RPF into nodes…",
                command=self._expand_selected_sealed,
            )
        if node["type"] == "file" and self._on_open_asset is not None:
            menu.add_command(
                label="Open in Asset Viewer", command=self._open_selected_asset,
            )
        if node["type"] == "vehicle_entity" and self._on_open_vehicle is not None:
            menu.add_command(
                label="Open in Vehicle Workbench", command=self._open_selected_vehicle,
            )
        menu.add_command(label="Focus node", command=self._focus_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def _focus_selected(self) -> None:
        node = self._node(self.selected) if self.selected in self.visible else None
        if node is None or node["id"] not in self.visible:
            return
        region = self.canvas.cget("scrollregion").split()
        width, height = max(1.0, float(region[2])), max(1.0, float(region[3]))
        self.canvas.xview_moveto(max(0, (node["x"] * self.zoom - 100) / width))
        self.canvas.yview_moveto(max(0, (node["y"] * self.zoom - 100) / height))

    def _expand_selected_sealed(self) -> None:
        node = self.graph_state.get("nodes", {}).get(self.selected)
        if node is None or node["type"] != "sealed_archive":
            return
        if self.game_path is None or not self.game_path.is_dir():
            messagebox.showerror(
                "GTA V installation required",
                "Select or detect the matching GTA V installation before expanding "
                "a sealed RPF.", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Expand sealed RPF",
            "Index this immutable RPF and create a retained editable node subtree?\n\n"
            "The source package and game files will not be changed.", parent=self,
        ):
            return
        node_id = node["id"]

        def work() -> dict:
            service = RpfExplorerService(
                self.project_root, self.game_path,
                workspace_roots=(self.graph.parent,),
            )
            return RpfPackageGraph.expand_sealed_archive(
                self.graph, node_id, service,
            )

        def completed(result: dict) -> None:
            self.collapsed.discard(node_id)
            try:
                semantic = PackageRelationshipAnalyzer.analyze(self.graph)
                result["semantic_links"] = semantic["summary"]["relations"]
            except (OSError, ValueError):
                result["semantic_links"] = None
            self._reload(node_id)
            self.status.set(
                f"Expanded {result['files']:,} files across "
                f"{result['archives']:,} RPF archive(s) · "
                f"{result['semantic_links'] or 0:,} semantic links."
            )

        _GraphWorkDialog(
            self, "Expanding sealed RPF",
            "Indexing nested archives and creating hash-bound source nodes…",
            work, completed,
        )

    def _analyze_links(self) -> None:
        def completed(report: dict) -> None:
            selected = self.selected
            self._reload(selected)
            summary = report["summary"]
            self.status.set(
                f"Relationship analysis complete · {summary['entities']:,} vehicle "
                f"systems · {summary['relations']:,} links · "
                f"{summary['errors']:,} errors · {summary['warnings']:,} warnings"
            )

        _GraphWorkDialog(
            self, "Analyzing package relationships",
            "Resolving vehicle assets, metadata, tuning, registrations, and targets…",
            lambda: PackageRelationshipAnalyzer.analyze(self.graph), completed,
        )

    def _open_selected_asset(self) -> None:
        node = self.graph_state.get("nodes", {}).get(self.selected)
        if (
            node is None or node.get("type") != "file"
            or self._on_open_asset is None
        ):
            return
        source = Path(node["source"])
        root = source.parent
        semantic = self.graph_state.get("semantic") or {}
        for relation in semantic.get("relations", []):
            if relation.get("target") != node["id"]:
                continue
            entity = self.semantic_entities.get(relation.get("source"))
            if entity is not None:
                root = Path(entity["source_root"])
                break
        self._on_open_asset(str(source), str(root))

    def _open_selected_vehicle(self) -> None:
        entity = self.semantic_entities.get(self.selected)
        if entity is None or self._on_open_vehicle is None:
            return
        self._on_open_vehicle(
            entity["source_root"], entity["name"],
        )

    def _focus_search(self) -> None:
        wanted = self.query.get().strip().casefold()
        if not wanted:
            return
        all_nodes = {**self.graph_state["nodes"], **self.semantic_entities}
        found = next((
            node_id for node_id, node in all_nodes.items()
            if wanted in node_id.casefold() or wanted in node["name"].casefold()
            or wanted in str(node.get("source", node.get("source_root", ""))).casefold()
            or wanted in str(node.get("metadata", {})).casefold()
        ), None)
        if found is None:
            self.status.set(f"No node matches {self.query.get()!r}")
            return
        parent = self.graph_state["parents"].get(found)
        while parent is not None:
            self.collapsed.discard(parent)
            parent = self.graph_state["parents"].get(parent)
        self._render()
        if found not in self.visible:
            self.status.set("The matching node is beyond the canvas display limit.")
            return
        self._select(found)
        node = self._node(found)
        region = self.canvas.cget("scrollregion").split()
        width, height = max(1.0, float(region[2])), max(1.0, float(region[3]))
        self.canvas.xview_moveto(max(0, (node["x"] * self.zoom - 100) / width))
        self.canvas.yview_moveto(max(0, (node["y"] * self.zoom - 100) / height))

    def _container_parent(self) -> str:
        selected = self.selected or self.graph_state["root_id"]
        if selected in self.semantic_entities:
            return self.graph_state["root_id"]
        if self.graph_state["nodes"][selected]["type"] in {
            "file", "sealed_archive",
        }:
            return self.graph_state["parents"][selected]
        return selected

    def _new_position(self, parent: str) -> tuple[float, float]:
        node = self.graph_state["nodes"][parent]
        return node["x"] + 300, node["y"] + 112 * (
            len(self.graph_state["children"][parent]) + 1
        )

    def _add_directory(self) -> None:
        name = simpledialog.askstring("Add directory node", "Directory name:", parent=self)
        if name is None:
            return
        parent = self._container_parent()
        x, y = self._new_position(parent)
        try:
            node = RpfPackageGraph.add_container(
                self.graph, parent, name, x=x, y=y,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add directory", str(exc), parent=self)
            return
        self._reload(node)

    def _add_archive(self) -> None:
        name = simpledialog.askstring(
            "Add nested RPF node", "Archive name ending in .rpf:", parent=self,
        )
        if name is None:
            return
        if not name.casefold().endswith(".rpf"):
            name += ".rpf"
        parent = self._container_parent()
        x, y = self._new_position(parent)
        try:
            node = RpfPackageGraph.add_container(
                self.graph, parent, name, archive=True, x=x, y=y,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add nested RPF", str(exc), parent=self)
            return
        self._reload(node)

    def _add_file(self) -> None:
        source = filedialog.askopenfilename(parent=self, title="Select source file")
        if not source:
            return
        name = simpledialog.askstring(
            "Authored RPF name", "File name inside the archive:",
            initialvalue=Path(source).name, parent=self,
        )
        if name is None:
            return
        parent = self._container_parent()
        x, y = self._new_position(parent)
        try:
            node = RpfPackageGraph.add_file(
                self.graph, parent, source, name=name, x=x, y=y,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add source file", str(exc), parent=self)
            return
        self._reload(node)

    def _rename(self) -> None:
        if not self.selected or self.selected in self.semantic_entities:
            if self.selected in self.semantic_entities:
                messagebox.showinfo(
                    "Derived relationship node",
                    "Vehicle-system nodes are derived from package metadata and cannot "
                    "be renamed here.", parent=self,
                )
            return
        node = self.graph_state["nodes"][self.selected]
        name = simpledialog.askstring(
            "Rename graph node", "Authored name:", initialvalue=node["name"], parent=self,
        )
        if name is None:
            return
        try:
            RpfPackageGraph.rename_node(self.graph, self.selected, name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not rename node", str(exc), parent=self)
            return
        self._reload(self.selected)

    def _remove(self) -> None:
        if self.selected in self.semantic_entities:
            messagebox.showinfo(
                "Derived relationship node",
                "Vehicle-system nodes are derived from package metadata. Edit the "
                "source records or change the relationship filter instead.", parent=self,
            )
            return
        if not self.selected or self.selected == self.graph_state["root_id"]:
            messagebox.showinfo("Root retained", "The package root cannot be removed.", parent=self)
            return
        if not messagebox.askyesno(
            "Remove graph subtree",
            "Remove the selected node and every graph descendant? Referenced source "
            "files will not be deleted.", parent=self,
        ):
            return
        parent = self.graph_state["parents"].get(self.selected)
        try:
            removed = RpfPackageGraph.remove_node(self.graph, self.selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remove graph subtree", str(exc), parent=self)
            return
        self._reload(parent)
        self.status.set(f"Removed {len(removed)} graph node(s); source files unchanged.")

    def _auto_layout(self) -> None:
        try:
            count = RpfPackageGraph.auto_layout(self.graph)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not lay out graph", str(exc), parent=self)
            return
        self._reload(self.selected)
        self.status.set(f"Applied deterministic readable layout to {count:,} nodes.")

    def _refresh_sources(self) -> None:
        if not messagebox.askyesno(
            "Refresh source hashes",
            "Accept the current bytes for every changed source file?",
            parent=self,
        ):
            return
        try:
            changed = RpfPackageGraph.refresh_sources(self.graph)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Source refresh failed", str(exc), parent=self)
            return
        self._reload(self.selected)
        self.status.set(f"Refreshed {changed} changed source record(s).")

    def _validate_sources(self) -> None:
        try:
            report = RpfPackageGraph.describe(self.graph)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF graph validation failed", str(exc), parent=self)
            return
        summary = report["summary"]
        messagebox.showinfo(
            "RPF graph is valid",
            f"Nodes: {summary['nodes']:,}\nArchives: {summary['archives']:,}\n"
            f"Sealed RPFs: {summary['sealed_archives']:,}\n"
            f"Directories: {summary['directories']:,}\nFiles: {summary['files']:,}\n"
            f"Source bytes: {summary['source_bytes']:,}\n\nAll referenced hashes match.",
            parent=self,
        )

    def _export_preview_bundle(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self, title="Create portable graph preview bundle",
            initialfile=f"{self.graph.stem}-preview-bundle",
            filetypes=(("Preview bundle folder", "*"),),
        )
        if not destination:
            return

        def completed(result) -> None:
            bundle, report = result
            self.status.set(f"Exported hash-verified preview bundle: {bundle}")
            messagebox.showinfo(
                "Graph previews exported",
                f"Bundle: {bundle}\nReport: {report}", parent=self,
            )

        _GraphWorkDialog(
            self, "Exporting graph previews",
            "Rendering bounded, hash-verified previews into a portable bundle…",
            lambda: render_graph_preview_bundle(
                self.graph, destination, self.project_root,
                game_path=self.game_path,
            ),
            completed,
        )

    def _materialize(self) -> None:
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for loose graph source",
        )
        if not parent:
            return
        name = simpledialog.askstring(
            "Loose source folder", "New folder name:", initialvalue="rpf-graph-source",
            parent=self,
        )
        if not name:
            return
        destination = Path(parent) / name

        def completed(result) -> None:
            self.status.set(f"Materialized verified loose source: {result}")
            messagebox.showinfo(
                "RPF graph materialized",
                f"Created provenance-safe loose source:\n\n{result}", parent=self,
            )

        _GraphWorkDialog(
            self, "Materializing RPF graph", "Hashing and copying every source node…",
            lambda: RpfPackageGraph.materialize(self.graph, destination), completed,
        )

    def _build_archive(self) -> None:
        if self.game_path is None or not self.game_path.is_dir():
            selected = filedialog.askdirectory(
                parent=self, title="Select matching GTA V installation for RPF keys",
            )
            if not selected:
                return
            self.game_path = Path(selected).resolve()
        output = filedialog.asksaveasfilename(
            parent=self, title="Build graph-authored RPF",
            initialfile=self.graph_state["nodes"][self.graph_state["root_id"]]["name"],
            defaultextension=".rpf", filetypes=(("Rockstar RPF", "*.rpf"),),
        )
        if not output:
            return
        builder = RpfArchiveBuilder(self.project_root, self.game_path)

        def completed(result) -> None:
            archive, report = result
            self.status.set(f"Built and exactly verified graph archive: {archive}")
            messagebox.showinfo(
                "Graph-authored RPF verified",
                f"Archive: {archive}\nValidation: {report}\n\nNo stock game file was changed.",
                parent=self,
            )

        _GraphWorkDialog(
            self, "Building RPF package graph",
            "Materializing source, building nested archives, and exactly extracting every payload…",
            lambda: RpfPackageGraph.build(self.graph, builder, output), completed,
        )

    def _plan_origin_changes(self) -> None:
        if not self.graph_state.get("payload", {}).get("origin"):
            messagebox.showinfo(
                "No imported origin",
                "This graph was not imported from an existing RPF. Build a new archive "
                "instead, or import an opened RPF from RPF Archives.",
                parent=self,
            )
            return
        if self.game_path is None or not self.game_path.is_dir():
            selected = filedialog.askdirectory(
                parent=self, title="Select matching GTA V installation for RPF keys",
            )
            if not selected:
                return
            self.game_path = Path(selected).resolve()
        output = filedialog.asksaveasfilename(
            parent=self, title="Save reviewed graph-to-origin plan",
            initialfile=f"{self.graph.stem}-origin-plan.json",
            defaultextension=".json", filetypes=(("RPF change plan", "*.json"),),
        )
        if not output:
            return
        builder = RpfArchiveBuilder(self.project_root, self.game_path)

        def completed(result) -> None:
            plan, payloads = result
            self.status.set(f"Created inert graph-to-origin plan: {plan}")
            messagebox.showinfo(
                "Origin change plan ready",
                f"Plan: {plan}\nEvidence and payloads: {payloads}\n\n"
                "The origin archive was not changed. Review/apply remains separate.",
                parent=self,
            )

        _GraphWorkDialog(
            self, "Planning graph changes to origin",
            "Building the graph, comparing canonical content, and retaining reviewed payloads…",
            lambda: RpfPackageGraph.plan_origin_changes(
                self.graph, builder, builder.service, output,
            ),
            completed,
        )


class RpfPackageGraphDialog(tk.Toplevel):
    """Compatibility host for opening the package graph as a standalone window."""

    def __init__(
        self, parent: tk.Misc, graph: str | Path, project_root: str | Path,
        game_path: str | Path | None = None,
        *, on_close=None, initial_select: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_close = on_close
        self.title("ALLIN1 — RPF Package Node Graph")
        place_window(
            self, preferred=(1460, 900), minimum=(1050, 680),
        )
        self.transient(parent.winfo_toplevel())
        self.editor = RpfPackageGraphFrame(
            self, graph, project_root, game_path, on_close=self._finish_close,
            initial_select=initial_select,
        )

    def _finish_close(self) -> None:
        self.destroy()
        if self._on_close is not None:
            self._on_close()

    def request_close(self) -> bool:
        """Close only after any active Build Flow operation has completed."""
        return self.editor._close_panel()

    def destroy(self) -> None:
        editor = getattr(self, "editor", None)
        if editor is not None and editor.winfo_exists():
            editor.destroy()
        super().destroy()
