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
from allin1_sdk.rpf_graph_previews import (
    ASSET_PREVIEW_HEIGHT,
    ASSET_PREVIEW_WIDTH,
    AssetPreviewRequest,
    render_asset_preview,
)
from allin1_sdk.rpf_program_ui import RpfProgramFrame


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
        self.geometry("520x145")
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


class RpfPackageGraphDialog(tk.Toplevel):
    """Visual containment graph backed by the same CLI/API graph document."""

    COLORS = {
        "archive": ("#6D4AA0", "#251D32"),
        "directory": ("#23815A", "#182A23"),
        "file": ("#2E6D98", "#172731"),
    }

    def __init__(
        self, parent: tk.Misc, graph: str | Path, project_root: str | Path,
        game_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.graph = Path(graph).resolve()
        self.project_root = Path(project_root).resolve()
        self.game_path = Path(game_path).resolve() if game_path else None
        self.title("ALLIN1 — RPF Package Node Graph")
        self.geometry("1460x900")
        self.minsize(1050, 680)
        self.transient(parent.winfo_toplevel())
        self.graph_state: dict = {}
        self.visible: set[str] = set()
        self.edge_items: dict[tuple[str, str], int] = {}
        self.selected: str | None = None
        self.dragging: str | None = None
        self.drag_last = (0.0, 0.0)
        self.connecting_parent: str | None = None
        self.connection_line: int | None = None
        self.query = tk.StringVar()
        self.zoom = 1.0
        self.zoom_text = tk.StringVar(value="100%")
        self.status = tk.StringVar(value="Loading validated package graph…")
        self.detail_name = tk.StringVar(value="Nothing selected")
        self.detail_type = tk.StringVar(value="")
        self.detail_id = tk.StringVar(value="")
        self.detail_parent = tk.StringVar(value="")
        self.detail_source = tk.StringVar(value="")
        self._preview_requests: queue.Queue[AssetPreviewRequest | None] = queue.Queue()
        self._preview_results: queue.Queue[
            tuple[str, str, bytes | None, str | None]
        ] = queue.Queue()
        self._preview_pending: set[str] = set()
        self._preview_keys: dict[str, str] = {}
        self._preview_images: dict[str, Image.Image] = {}
        self._preview_photos: dict[str, ImageTk.PhotoImage] = {}
        self._preview_messages: dict[str, str] = {}
        self._preview_worker_thread = threading.Thread(
            target=self._asset_preview_worker, daemon=True,
            name="allin1-rpf-asset-previews",
        )
        self._preview_worker_thread.start()
        self._build_ui()
        self._reload()
        # Tk can preserve the canvas' far-edge view while a toplevel is first
        # mapped. Always present the package root on initial open.
        self.after_idle(self._focus_initial_view)
        self.after(150, self._focus_initial_view)
        self.after(90, self._poll_asset_previews)
        self.bind("<Control-plus>", lambda _event: self._zoom_by(ZOOM_FACTOR))
        self.bind("<Control-equal>", lambda _event: self._zoom_by(ZOOM_FACTOR))
        self.bind("<Control-minus>", lambda _event: self._zoom_by(1 / ZOOM_FACTOR))
        self.bind("<Control-0>", lambda _event: self._reset_zoom())

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
            header, text="RPF package node graph", font=("Segoe UI Semibold", 18),
            foreground="#1f7f42",
        ).pack(side="left")
        ttk.Label(
            header, text=str(self.graph), foreground="#52635c",
        ).pack(side="left", padx=(14, 0))
        ttk.Button(header, text="Validate", command=self._validate_sources).pack(side="right")
        ttk.Button(header, text="Refresh sources", command=self._refresh_sources).pack(
            side="right", padx=(0, 6),
        )
        ttk.Button(header, text="Auto layout", command=self._auto_layout).pack(
            side="right", padx=(0, 6),
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
        ttk.Label(
            search, text="Drag cards · drag an output port onto another card to reparent",
            foreground="#52635c",
        ).pack(side="right", padx=(12, 0))

        canvas_host = tk.Frame(canvas_frame, background="#111714")
        canvas_host.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            canvas_host, background="#111714", highlightthickness=0,
            scrollregion=(0, 0, 5000, 5000),
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

        ttk.Label(
            inspector, text="Node inspector", font=("Segoe UI Semibold", 14),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Separator(inspector).pack(fill="x", pady=(7, 10))
        ttk.Label(
            inspector, textvariable=self.detail_name, font=("Segoe UI Semibold", 11),
            wraplength=285, justify="left",
        ).pack(anchor="w")
        for variable in (self.detail_type, self.detail_id, self.detail_parent, self.detail_source):
            ttk.Label(
                inspector, textvariable=variable, foreground="#52635c",
                wraplength=285, justify="left",
            ).pack(anchor="w", pady=(3, 0))

        ttk.Label(
            inspector, text="Authoring", font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", pady=(18, 6))
        for text, command in (
            ("Add directory", self._add_directory),
            ("Add nested RPF", self._add_archive),
            ("Add source file", self._add_file),
            ("Rename selected", self._rename),
            ("Remove selected subtree", self._remove),
        ):
            ttk.Button(inspector, text=text, command=command).pack(fill="x", pady=(0, 5))

        ttk.Label(
            inspector, text="Output", font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", pady=(15, 6))
        ttk.Button(
            inspector, text="Materialize loose source…", command=self._materialize,
        ).pack(fill="x", pady=(0, 5))
        ttk.Button(
            inspector, text="Build + exactly verify RPF…", command=self._build_archive,
        ).pack(fill="x", pady=(0, 5))
        ttk.Button(
            inspector, text="Plan changes to imported origin…",
            command=self._plan_origin_changes,
        ).pack(fill="x", pady=(0, 5))
        ttk.Button(inspector, text="Close", command=self.destroy).pack(
            fill="x", pady=(20, 0),
        )
        RpfProgramFrame(
            program_tab, self.graph, self.project_root, self.game_path,
        ).pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self.status, foreground="#52635c").pack(
            fill="x", pady=(7, 0),
        )

    def _reload(self, select: str | None = None) -> None:
        try:
            self.graph_state = RpfPackageGraph.validate(self.graph, verify_sources=False)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF graph validation failed", str(exc), parent=self)
            self.status.set("Graph validation failed; the document was not changed.")
            return
        valid_nodes = set(self.graph_state["nodes"])
        self._preview_pending.intersection_update(valid_nodes)
        for cache in (
            self._preview_keys, self._preview_images, self._preview_photos,
            self._preview_messages,
        ):
            for node_id in tuple(cache):
                if node_id not in valid_nodes:
                    cache.pop(node_id, None)
        self.selected = select if select in self.graph_state["nodes"] else self.selected
        if self.selected not in self.graph_state["nodes"]:
            self.selected = self.graph_state["root_id"]
        self._render()
        self._show_selected()

    def _render(self) -> None:
        self.canvas.delete("all")
        nodes = list(self.graph_state["nodes"])
        ordered = [self.graph_state["root_id"], *(
            node for node in nodes if node != self.graph_state["root_id"]
        )]
        self.visible = set(ordered[:CANVAS_LIMIT])
        max_x = max((self.graph_state["nodes"][node]["x"] for node in self.visible), default=0)
        max_y = max((self.graph_state["nodes"][node]["y"] for node in self.visible), default=0)
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
        for child, parent in self.graph_state["parents"].items():
            if child not in self.visible or parent not in self.visible:
                continue
            item = self.canvas.create_line(
                0, 0, 0, 0, 0, 0, 0, 0, smooth=True, splinesteps=20,
                width=3, fill="#50655C", tags=("edge",),
            )
            self.edge_items[(parent, child)] = item
        for node_id in ordered:
            if node_id not in self.visible:
                continue
            self._draw_node(node_id)
        self._update_edges()
        hidden = len(nodes) - len(self.visible)
        note = f" · {hidden:,} nodes hidden by canvas limit" if hidden else ""
        self.status.set(
            f"{len(nodes):,} nodes · {len(self.graph_state['parents']):,} links · "
            f"{self.graph_state['file_count']:,} files · {self.graph_state['byte_count']:,} bytes{note}"
        )

    def _draw_node(self, node_id: str) -> None:
        node = self.graph_state["nodes"][node_id]
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
            text=node["type"].upper(), anchor="w",
            fill="#FFFFFF", font=("Segoe UI Semibold", font_header), tags=tags,
        )
        is_file = node["type"] == "file"
        text_width = (NODE_WIDTH - 116 if is_file else NODE_WIDTH - 20) * self.zoom
        self.canvas.create_text(
            x + 10 * self.zoom, y + 49 * self.zoom,
            text=node["name"], anchor="w", width=text_width,
            fill="#F0F5F2", font=("Segoe UI Semibold", font_name), tags=tags,
        )
        subtitle = node_id if not is_file else f"{node['size']:,} bytes"
        self.canvas.create_text(
            x + 10 * self.zoom, y + 68 * self.zoom,
            text=subtitle, anchor="w", width=text_width,
            fill="#9FB0A8", font=("Consolas", font_detail), tags=tags,
        )
        if is_file:
            self._draw_asset_preview(node_id, node, x, y, tags)
        if node_id != self.graph_state["root_id"]:
            port_radius = max(4, 7 * self.zoom)
            port_y = y + 41 * self.zoom
            self.canvas.create_oval(
                x - port_radius, port_y - port_radius,
                x + port_radius, port_y + port_radius,
                fill="#D9E4DF", outline="#111714",
                width=2, tags=(*tags, f"in:{node_id}"),
            )
        if node["type"] != "file":
            port_radius = max(4, 7 * self.zoom)
            port_x, port_y = x + node_width, y + 41 * self.zoom
            self.canvas.create_oval(
                port_x - port_radius, port_y - port_radius,
                port_x + port_radius, port_y + port_radius,
                fill="#E7B94B", outline="#111714", width=2,
                tags=(*tags, f"out:{node_id}"),
            )

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
        while True:
            request = self._preview_requests.get()
            if request is None:
                return
            try:
                preview = render_asset_preview(
                    request, self.project_root, self.game_path,
                )
                self._preview_results.put(
                    (request.node_id, request.cache_key, preview, None)
                )
            except (
                OSError, RuntimeError, ValueError, UnidentifiedImageError,
                Image.DecompressionBombError,
            ) as exc:
                self._preview_results.put(
                    (request.node_id, request.cache_key, None, str(exc))
                )

    def _poll_asset_previews(self) -> None:
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
            source = self.graph_state["nodes"][output]
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
            source = self.graph_state["nodes"][self.connecting_parent]
            self.canvas.coords(
                self.connection_line,
                (source["x"] + NODE_WIDTH) * self.zoom,
                (source["y"] + 41) * self.zoom, x, y,
            )
            return
        if not self.dragging:
            return
        dx, dy = x - self.drag_last[0], y - self.drag_last[1]
        node = self.graph_state["nodes"][self.dragging]
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
            if child and child != parent:
                try:
                    RpfPackageGraph.reparent_node(self.graph, child, parent)
                except (OSError, ValueError) as exc:
                    messagebox.showerror("Could not connect graph nodes", str(exc), parent=self)
                self._reload(child)
            return
        if self.dragging:
            node_id = self.dragging
            node = self.graph_state["nodes"][node_id]
            self.dragging = None
            try:
                RpfPackageGraph.set_position(self.graph, node_id, node["x"], node["y"])
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
        max_x = max(self.graph_state["nodes"][node]["x"] for node in self.visible)
        max_y = max(self.graph_state["nodes"][node]["y"] for node in self.visible)
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
        node = self.graph_state.get("nodes", {}).get(self.selected)
        if node is None:
            self.detail_name.set("Nothing selected")
            for variable in (
                self.detail_type, self.detail_id, self.detail_parent, self.detail_source,
            ):
                variable.set("")
            return
        self.detail_name.set(node["name"])
        self.detail_type.set(f"Type: {node['type']}")
        self.detail_id.set(f"ID: {node['id']}")
        parent = self.graph_state["parents"].get(node["id"])
        self.detail_parent.set(f"Parent: {parent or '(package root)'}")
        self.detail_source.set(
            f"Source: {node.get('source', '(generated container)')}"
        )

    def _focus_search(self) -> None:
        wanted = self.query.get().strip().casefold()
        if not wanted:
            return
        found = next((
            node_id for node_id, node in self.graph_state["nodes"].items()
            if wanted in node_id.casefold() or wanted in node["name"].casefold()
            or wanted in str(node.get("source", "")).casefold()
        ), None)
        if found is None:
            self.status.set(f"No node matches {self.query.get()!r}")
            return
        if found not in self.visible:
            self.status.set("The matching node is beyond the canvas display limit.")
            return
        self._select(found)
        node = self.graph_state["nodes"][found]
        region = self.canvas.cget("scrollregion").split()
        width, height = max(1.0, float(region[2])), max(1.0, float(region[3]))
        self.canvas.xview_moveto(max(0, (node["x"] * self.zoom - 100) / width))
        self.canvas.yview_moveto(max(0, (node["y"] * self.zoom - 100) / height))

    def _container_parent(self) -> str:
        selected = self.selected or self.graph_state["root_id"]
        if self.graph_state["nodes"][selected]["type"] == "file":
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
        if not self.selected:
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
            f"Directories: {summary['directories']:,}\nFiles: {summary['files']:,}\n"
            f"Source bytes: {summary['source_bytes']:,}\n\nAll referenced hashes match.",
            parent=self,
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
                "instead, or import an opened RPF from RPF Explorer.",
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
