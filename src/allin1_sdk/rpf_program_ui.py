"""Embedded visual editor for typed RPF package programs."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from allin1_sdk.rpf_program import NODE_SPECS, PROGRAM_TEMPLATES, RpfPackageProgram


NODE_WIDTH = 248
NODE_HEIGHT = 92
MIN_ZOOM = 0.35
MAX_ZOOM = 2.0
ZOOM_FACTOR = 1.2
CANVAS_PADDING = 180


class RpfProgramFrame(ttk.Frame):
    """A typed pin-and-wire build-flow editor embedded in the package graph."""

    COLORS = {
        "package_source": ("#7A4F9D", "#2A2032"),
        "validate_graph": ("#247A55", "#192B23"),
        "materialize_tree": ("#2F718E", "#172A32"),
        "build_rpf": ("#B56D20", "#33261A"),
        "defragment_rpf": ("#9A6130", "#30251B"),
        "plan_origin": ("#376A9E", "#1A2835"),
        "artifact_output": ("#3E7C4B", "#1A2D20"),
    }

    def __init__(
        self, parent: tk.Misc, graph: str | Path, project_root: str | Path,
        game_path: str | Path | None = None,
        *, on_busy_change=None,
    ) -> None:
        super().__init__(parent, padding=8)
        self.graph = Path(graph).resolve()
        self.project_root = Path(project_root).resolve()
        self.game_path = Path(game_path).resolve() if game_path else None
        self._on_busy_change = on_busy_change
        self._busy = False
        self._authoring_controls: list[tk.Widget] = []
        self.program: Path | None = None
        self.state: dict = {}
        self.selected: str | None = None
        self.dragging: str | None = None
        self.drag_last = (0.0, 0.0)
        self.connecting: str | None = None
        self.connection_line: int | None = None
        self.edge_items: dict[tuple[str, str], int] = {}
        self.zoom = 1.0
        self.zoom_text = tk.StringVar(value="100%")
        self.status = tk.StringVar(value="Create or open a build flow for this package graph.")
        self.detail = tk.StringVar(value="No operation node selected")
        self.config_text = tk.StringVar(value="")
        self.issue_text = tk.StringVar(value="")
        self._build_ui()
        suggested = self.graph.with_name(f"{self.graph.stem}.program.json")
        if suggested.is_file():
            self._open_program(suggested)
        else:
            self._show_empty()

    def _build_ui(self) -> None:
        tools = ttk.Frame(self)
        tools.pack(fill="x", pady=(0, 7))
        ttk.Label(
            tools, text="Build flow", font=("Segoe UI Semibold", 14),
            foreground="#1f7f42",
        ).pack(side="left")
        create = ttk.Menubutton(tools, text="Create ▾")
        create_menu = tk.Menu(create, tearoff=False)
        for template_id, spec in PROGRAM_TEMPLATES.items():
            create_menu.add_command(
                label=spec["title"],
                command=lambda value=template_id: self._create(value),
            )
        create.configure(menu=create_menu)
        create.pack(side="right", padx=(5, 0))
        self._authoring_controls.append(create)
        open_button = ttk.Button(tools, text="Open", command=self._choose)
        open_button.pack(
            side="right", padx=(5, 0),
        )
        self._authoring_controls.append(open_button)
        flow_tools = ttk.Menubutton(tools, text="Flow tools")
        flow_menu = tk.Menu(flow_tools, tearoff=False)
        flow_menu.add_command(label="Auto layout", command=self._auto_layout)
        flow_menu.add_command(label="Validate", command=self._validate)
        flow_tools.configure(menu=flow_menu)
        flow_tools.pack(side="right", padx=(5, 0))
        self._authoring_controls.append(flow_tools)
        dry_run_button = ttk.Button(tools, text="Dry run", command=self._plan)
        dry_run_button.pack(
            side="right", padx=(5, 0),
        )
        self._authoring_controls.append(dry_run_button)
        run_button = ttk.Button(tools, text="Run", command=self._run)
        run_button.pack(
            side="right", padx=(5, 0),
        )
        self._authoring_controls.append(run_button)

        ttk.Label(self, textvariable=self.status, foreground="#52635c").pack(
            side="bottom", fill="x", pady=(7, 0),
        )

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        palette = ttk.Frame(body, padding=(4, 4, 10, 4), width=205)
        canvas_host = ttk.Frame(body)
        inspector = ttk.Frame(body, padding=(12, 4, 4, 4), width=310)
        body.add(palette, weight=0)
        body.add(canvas_host, weight=5)
        body.add(inspector, weight=1)

        ttk.Label(palette, text="NODE PALETTE", font=("Segoe UI Semibold", 9)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 7),
        )
        palette_labels = {
            "validate_graph": "＋ Validate",
            "materialize_tree": "＋ Materialize",
            "build_rpf": "＋ Build RPF",
            "defragment_rpf": "＋ Defragment",
            "plan_origin": "＋ Plan origin",
            "artifact_output": "＋ Output",
        }
        for index, node_type in enumerate(palette_labels):
            button = ttk.Button(
                palette, text=palette_labels[node_type],
                command=lambda value=node_type: self._add(value),
                padding=(6, 2),
            )
            button.grid(
                row=1 + index // 2, column=index % 2, sticky="ew",
                padx=(0 if index % 2 == 0 else 3, 3 if index % 2 == 0 else 0),
                pady=(0, 5),
            )
            self._authoring_controls.append(button)
        palette.columnconfigure(0, weight=1)
        palette.columnconfigure(1, weight=1)

        view_tools = ttk.Frame(canvas_host)
        view_tools.pack(fill="x", pady=(0, 6))
        ttk.Label(
            view_tools, text="Canvas view", font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        ttk.Button(
            view_tools, text="−", width=3,
            command=lambda: self._zoom_by(1 / ZOOM_FACTOR),
        ).pack(side="left", padx=(8, 0))
        ttk.Label(
            view_tools, textvariable=self.zoom_text, width=6, anchor="center",
        ).pack(side="left", padx=3)
        ttk.Button(
            view_tools, text="+", width=3,
            command=lambda: self._zoom_by(ZOOM_FACTOR),
        ).pack(side="left")
        ttk.Button(view_tools, text="Fit", command=self._fit_graph).pack(
            side="left", padx=(6, 0),
        )
        ttk.Button(view_tools, text="100%", command=self._reset_zoom).pack(
            side="left", padx=(4, 0),
        )
        surface = tk.Frame(canvas_host, background="#0F1512")
        surface.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            surface, background="#0F1512", highlightthickness=0,
            scrollregion=(0, 0, 5000, 3200), takefocus=True,
        )
        x_scroll = ttk.Scrollbar(surface, orient="horizontal", command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(surface, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        surface.grid_rowconfigure(0, weight=1)
        surface.grid_columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Shift-MouseWheel>", self._shift_mousewheel)
        self.canvas.bind("<Control-MouseWheel>", self._zoom_mousewheel)
        self.canvas.bind(
            "<Control-plus>", lambda _event: self._keyboard_zoom(ZOOM_FACTOR),
        )
        self.canvas.bind(
            "<Control-equal>", lambda _event: self._keyboard_zoom(ZOOM_FACTOR),
        )
        self.canvas.bind(
            "<Control-minus>", lambda _event: self._keyboard_zoom(1 / ZOOM_FACTOR),
        )
        self.canvas.bind("<Control-0>", self._keyboard_reset_zoom)

        ttk.Label(
            inspector, text="Operation inspector", font=("Segoe UI Semibold", 13),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Separator(inspector).pack(fill="x", pady=(7, 10))
        ttk.Label(
            inspector, textvariable=self.detail, font=("Segoe UI Semibold", 10),
            wraplength=285, justify="left",
        ).pack(anchor="w")
        ttk.Label(
            inspector, textvariable=self.config_text, foreground="#52635c",
            wraplength=285, justify="left",
        ).pack(anchor="w", pady=(7, 0))
        ttk.Label(
            inspector, textvariable=self.issue_text, foreground="#A35A28",
            wraplength=285, justify="left",
        ).pack(anchor="w", pady=(7, 0))
        inspector_actions = ttk.Frame(inspector)
        inspector_actions.pack(fill="x", pady=(12, 0))
        inspector_actions.columnconfigure(0, weight=1)
        inspector_actions.columnconfigure(1, weight=1)
        configure_button = ttk.Button(
            inspector_actions, text="Configure…", command=self._configure,
        )
        configure_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._authoring_controls.append(configure_button)
        node_actions = ttk.Menubutton(inspector_actions, text="Node actions")
        node_menu = tk.Menu(node_actions, tearoff=False)
        node_menu.add_command(
            label="Disconnect input", command=self._disconnect,
        )
        node_menu.add_command(label="Remove node", command=self._remove)
        node_actions.configure(menu=node_menu)
        node_actions.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self._authoring_controls.append(node_actions)

    @property
    def busy(self) -> bool:
        """Whether an authoring job must finish before this frame can close."""
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for control in self._authoring_controls:
            if control.winfo_exists():
                control.configure(state=state)
        if self._on_busy_change is not None:
            self._on_busy_change(busy)

    def _show_empty(self) -> None:
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 1240, 660))
        self.canvas.create_text(
            620, 330,
            text="No build flow is open\n\nCreate a typed flow or open an existing program JSON.",
            fill="#AFC0B8", font=("Segoe UI Semibold", 16), justify="center",
        )
        self.state = {}

    def _create(self, template: str = "validate") -> None:
        spec = PROGRAM_TEMPLATES[template]
        output = filedialog.asksaveasfilename(
            parent=self, title="Create RPF package build flow",
            initialfile=f"{self.graph.stem}.{template}.program.json",
            defaultextension=".json", filetypes=(("RPF program", "*.json"),),
        )
        if not output:
            return
        try:
            program = RpfPackageProgram.create(
                self.graph, output, template=template,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create build flow", str(exc), parent=self)
            return
        self._open_program(program)
        self.status.set(
            f"Created {spec['title']} template · configure highlighted operation nodes"
        )

    def _choose(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open RPF package build flow",
            filetypes=(("RPF program", "*.json"), ("All files", "*.*")),
        )
        if selected:
            self._open_program(Path(selected))

    def _open_program(self, selected: Path) -> None:
        try:
            state = RpfPackageProgram.validate(selected)
            if state["package_graph"] != self.graph:
                raise ValueError(
                    "This build flow is bound to a different RPF package graph"
                )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid RPF build flow", str(exc), parent=self)
            return
        self.program = selected.resolve()
        self._reload()

    def _reload(self, select: str | None = None) -> None:
        if self.program is None:
            self._show_empty()
            return
        try:
            self.state = RpfPackageProgram.validate(self.program)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF build flow failed validation", str(exc), parent=self)
            return
        if select in self.state["nodes"]:
            self.selected = select
        if self.selected not in self.state["nodes"]:
            self.selected = self.state["source_id"]
        self._render()
        self._show_selected()

    def _render(self) -> None:
        self.canvas.delete("all")
        nodes = tuple(self.state.get("nodes", {}).values())
        min_x = min((float(node["x"]) for node in nodes), default=0.0)
        min_y = min((float(node["y"]) for node in nodes), default=0.0)
        max_x = max((float(node["x"]) + NODE_WIDTH for node in nodes), default=1060.0)
        max_y = max((float(node["y"]) + NODE_HEIGHT for node in nodes), default=540.0)
        left = min(0.0, min_x * self.zoom - CANVAS_PADDING)
        top = min(0.0, min_y * self.zoom - CANVAS_PADDING)
        right = max(
            left + max(1, self.canvas.winfo_width()),
            max_x * self.zoom + CANVAS_PADDING,
        )
        bottom = max(
            top + max(1, self.canvas.winfo_height()),
            max_y * self.zoom + CANVAS_PADDING,
        )
        self.canvas.configure(scrollregion=(left, top, right, bottom))
        grid = max(35, round(100 * self.zoom))
        grid_left = int(left // grid) * grid
        grid_top = int(top // grid) * grid
        for x in range(grid_left, int(right) + grid, grid):
            self.canvas.create_line(x, top, x, bottom, fill="#18211D", tags=("grid",))
        for y in range(grid_top, int(bottom) + grid, grid):
            self.canvas.create_line(left, y, right, y, fill="#18211D", tags=("grid",))
        self.edge_items.clear()
        for link in self.state["links"]:
            key = (link["from"], link["to"])
            self.edge_items[key] = self.canvas.create_line(
                0, 0, 0, 0, smooth=True, splinesteps=22,
                width=max(2, round(4 * self.zoom)),
                fill="#C99B3A", tags=("program-edge",),
            )
        for node_id in self.state["nodes"]:
            self._draw_node(node_id)
        self._update_edges()
        readiness = "READY" if not self.state["issues"] else "INCOMPLETE"
        self.status.set(
            f"{readiness} · {len(self.state['nodes'])} operation nodes · "
            f"{len(self.state['links'])} typed links · {self.program}"
        )

    def _draw_node(self, node_id: str) -> None:
        node = self.state["nodes"][node_id]
        spec = NODE_SPECS[node["type"]]
        x, y = node["x"] * self.zoom, node["y"] * self.zoom
        node_width, node_height = NODE_WIDTH * self.zoom, NODE_HEIGHT * self.zoom
        shadow = max(2, 5 * self.zoom)
        header_height = 29 * self.zoom
        header_font = max(6, round(9 * self.zoom))
        detail_font = max(5, round(8 * self.zoom))
        header, body = self.COLORS[node["type"]]
        tags = ("program-node", f"pnode:{node_id}")
        self.canvas.create_rectangle(
            x + shadow, y + shadow,
            x + node_width + shadow, y + node_height + shadow,
            fill="#080C0A", outline="", tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + node_width, y + node_height, fill=body,
            outline="#E7B94B" if node_id == self.selected else "#53635C",
            width=max(1, round((3 if node_id == self.selected else 1) * self.zoom)),
            tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + node_width, y + header_height,
            fill=header, outline="", tags=tags,
        )
        self.canvas.create_text(
            x + 10 * self.zoom, y + 15 * self.zoom,
            text=spec.title, anchor="w", width=(NODE_WIDTH - 20) * self.zoom,
            fill="#FFFFFF", font=("Segoe UI Semibold", header_font), tags=tags,
        )
        in_text = " / ".join(spec.input_types) if spec.input_types else "START"
        out_text = spec.output_type or "END"
        self.canvas.create_text(
            x + 12 * self.zoom, y + 51 * self.zoom,
            text=f"IN  {in_text}", anchor="w", width=(NODE_WIDTH - 24) * self.zoom,
            fill="#B9C8C1", font=("Consolas", detail_font), tags=tags,
        )
        self.canvas.create_text(
            x + 12 * self.zoom, y + 70 * self.zoom,
            text=f"OUT {out_text}", anchor="w", width=(NODE_WIDTH - 24) * self.zoom,
            fill="#D8B660", font=("Consolas", detail_font), tags=tags,
        )
        pin_radius = max(4, 7 * self.zoom)
        pin_y = y + 46 * self.zoom
        if spec.input_types:
            self.canvas.create_oval(
                x - pin_radius, pin_y - pin_radius,
                x + pin_radius, pin_y + pin_radius,
                fill="#E6EEEA", outline="#111714",
                width=max(1, round(2 * self.zoom)), tags=(*tags, f"pin:{node_id}"),
            )
        if spec.output_type:
            pin_x = x + node_width
            self.canvas.create_oval(
                pin_x - pin_radius, pin_y - pin_radius,
                pin_x + pin_radius, pin_y + pin_radius,
                fill="#E7B94B", outline="#111714",
                width=max(1, round(2 * self.zoom)),
                tags=(*tags, f"pout:{node_id}"),
            )

    def _update_edges(self) -> None:
        for (parent, child), item in self.edge_items.items():
            source, target = self.state["nodes"][parent], self.state["nodes"][child]
            x1 = (source["x"] + NODE_WIDTH) * self.zoom
            y1 = (source["y"] + 46) * self.zoom
            x2 = target["x"] * self.zoom
            y2 = (target["y"] + 46) * self.zoom
            curve = max(35, 70 * self.zoom, abs(x2 - x1) * 0.45)
            self.canvas.coords(item, x1, y1, x1 + curve, y1, x2 - curve, y2, x2, y2)

    @staticmethod
    def _tag(tags: tuple[str, ...], prefix: str) -> str | None:
        return next((item[len(prefix):] for item in tags if item.startswith(prefix)), None)

    def _current_tags(self) -> tuple[str, ...]:
        current = self.canvas.find_withtag("current")
        return self.canvas.gettags(current[-1]) if current else ()

    def _node_at(self, event: tk.Event) -> str | None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(x - 4, y - 4, x + 4, y + 4)):
            node_id = self._tag(self.canvas.gettags(item), "pnode:")
            if node_id:
                return node_id
        return None

    def _press(self, event: tk.Event) -> None:
        if self._busy:
            return
        self.canvas.focus_set()
        tags = self._current_tags()
        node_id = self._tag(tags, "pnode:")
        if node_id is None:
            self.selected = None
            self._render()
            self._show_selected()
            return
        self.selected = node_id
        self._render()
        self._show_selected()
        output = self._tag(tags, "pout:")
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if output:
            self.connecting = output
            source = self.state["nodes"][output]
            self.connection_line = self.canvas.create_line(
                (source["x"] + NODE_WIDTH) * self.zoom,
                (source["y"] + 46) * self.zoom, x, y,
                fill="#E7B94B", width=max(2, round(3 * self.zoom)), dash=(7, 4),
            )
        else:
            self.dragging, self.drag_last = node_id, (x, y)

    def _motion(self, event: tk.Event) -> None:
        if self._busy:
            return
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.connecting and self.connection_line:
            source = self.state["nodes"][self.connecting]
            self.canvas.coords(
                self.connection_line,
                (source["x"] + NODE_WIDTH) * self.zoom,
                (source["y"] + 46) * self.zoom, x, y,
            )
            return
        if not self.dragging:
            return
        dx, dy = x - self.drag_last[0], y - self.drag_last[1]
        self.state["nodes"][self.dragging]["x"] += dx / self.zoom
        self.state["nodes"][self.dragging]["y"] += dy / self.zoom
        self.canvas.move(f"pnode:{self.dragging}", dx, dy)
        self.drag_last = (x, y)
        self._update_edges()

    def _release(self, event: tk.Event) -> None:
        if self._busy:
            return
        if self.program is None:
            return
        if self.connecting:
            parent, child = self.connecting, self._node_at(event)
            self.connecting = None
            if self.connection_line:
                self.canvas.delete(self.connection_line)
                self.connection_line = None
            if child and child != parent:
                try:
                    RpfPackageProgram.connect(self.program, parent, child)
                except (OSError, ValueError) as exc:
                    messagebox.showerror("Typed connection refused", str(exc), parent=self)
                self._reload(child)
            return
        if self.dragging:
            node_id, self.dragging = self.dragging, None
            node = self.state["nodes"][node_id]
            try:
                RpfPackageProgram.set_position(
                    self.program, node_id, node["x"], node["y"],
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

    def _keyboard_zoom(self, factor: float) -> str:
        self._zoom_by(factor)
        return "break"

    def _keyboard_reset_zoom(self, _event: tk.Event) -> str:
        self._reset_zoom()
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
        if self.dragging or self.connecting:
            return
        target = max(MIN_ZOOM, min(MAX_ZOOM, value))
        if abs(target - self.zoom) < 0.001:
            return
        view_x = self.canvas.winfo_width() / 2 if focus_x is None else focus_x
        view_y = self.canvas.winfo_height() / 2 if focus_y is None else focus_y
        logical_x = self.canvas.canvasx(view_x) / self.zoom
        logical_y = self.canvas.canvasy(view_y) / self.zoom
        self.zoom = target
        self.zoom_text.set(f"{round(self.zoom * 100):d}%")
        if self.state.get("nodes"):
            self._render()
        else:
            self._show_empty()
        region = tuple(float(item) for item in self.canvas.cget("scrollregion").split())
        if len(region) != 4:
            return
        left, top, right, bottom = region
        width, height = max(1.0, right - left), max(1.0, bottom - top)
        wanted_left = logical_x * self.zoom - view_x
        wanted_top = logical_y * self.zoom - view_y
        self.canvas.xview_moveto(max(0.0, min(1.0, (wanted_left - left) / width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (wanted_top - top) / height)))

    def _reset_zoom(self) -> None:
        self._set_zoom(1.0)

    def _fit_graph(self) -> None:
        nodes = tuple(self.state.get("nodes", {}).values())
        if not nodes:
            return
        min_x = min(float(node["x"]) for node in nodes)
        min_y = min(float(node["y"]) for node in nodes)
        max_x = max(float(node["x"]) + NODE_WIDTH for node in nodes)
        max_y = max(float(node["y"]) + NODE_HEIGHT for node in nodes)
        available_width = max(1, self.canvas.winfo_width() - 40)
        available_height = max(1, self.canvas.winfo_height() - 40)
        target = min(
            available_width / max(1.0, max_x - min_x + 40),
            available_height / max(1.0, max_y - min_y + 40),
        )
        self._set_zoom(target, 0, 0)
        region = tuple(float(item) for item in self.canvas.cget("scrollregion").split())
        if len(region) != 4:
            return
        left, top, right, bottom = region
        width, height = max(1.0, right - left), max(1.0, bottom - top)
        wanted_left = min_x * self.zoom - 20
        wanted_top = min_y * self.zoom - 20
        self.canvas.xview_moveto(max(0.0, min(1.0, (wanted_left - left) / width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (wanted_top - top) / height)))

    def _show_selected(self) -> None:
        node = self.state.get("nodes", {}).get(self.selected)
        if node is None:
            self.detail.set("No operation node selected")
            self.config_text.set("")
            self.issue_text.set("")
            return
        spec = NODE_SPECS[node["type"]]
        self.detail.set(f"{spec.title}\n{node['id']} · {node['type']}")
        config = node["config"]
        self.config_text.set(
            "Configuration\n" + (
                "\n".join(f"{key}: {value}" for key, value in config.items())
                if config else "No configured fields"
            )
        )
        relevant = [
            issue for issue in self.state.get("issues", ())
            if issue.startswith(f"{node['id']}:")
        ]
        self.issue_text.set("\n".join(relevant))

    def _add(self, node_type: str) -> None:
        if self.program is None:
            messagebox.showinfo("Create a flow", "Create or open a build flow first.", parent=self)
            return
        try:
            node_id = RpfPackageProgram.add_node(
                self.program, node_type,
                x=710 + (len(self.state["nodes"]) % 4) * 60,
                y=120 + len(self.state["nodes"]) * 75,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add operation node", str(exc), parent=self)
            return
        self._reload(node_id)
        self._configure()

    def _configure(self) -> None:
        if self.program is None or self.selected not in self.state.get("nodes", {}):
            return
        node = self.state["nodes"][self.selected]
        spec = NODE_SPECS[node["type"]]
        if not spec.required_config and not spec.optional_config:
            messagebox.showinfo("No configuration", "This node is configured by its input.", parent=self)
            return
        config = dict(node["config"])
        for key in (*spec.required_config, *spec.optional_config):
            current = config.get(key, "")
            if key == "gta_path":
                selected = filedialog.askdirectory(
                    parent=self, title=f"Select GTA V path for {spec.title}",
                    initialdir=str(current or self.game_path or self.project_root),
                )
            elif key == "label":
                selected = simpledialog.askstring(
                    "Artifact label", "Display label:", initialvalue=str(current), parent=self,
                )
                if selected is None:
                    return
            elif key == "output" and node["type"] == "materialize_tree":
                parent = filedialog.askdirectory(
                    parent=self, title="Select parent folder for new loose tree",
                )
                if not parent:
                    return
                name = simpledialog.askstring(
                    "Loose tree name", "New folder name:", initialvalue="rpf-package",
                    parent=self,
                )
                if not name:
                    return
                selected = str(Path(parent) / name)
            else:
                extension = ".rpf" if key == "output" and node["type"] in {
                    "build_rpf", "defragment_rpf",
                } else ".json"
                selected = filedialog.asksaveasfilename(
                    parent=self, title=f"Select {key} for {spec.title}",
                    initialfile=Path(current).name if current else f"artifact{extension}",
                    defaultextension=extension,
                    filetypes=(("Rockstar RPF", "*.rpf"),) if extension == ".rpf" else (("JSON", "*.json"),),
                )
            if not selected:
                return
            config[key] = selected
        try:
            RpfPackageProgram.configure_node(self.program, self.selected, config)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Node configuration refused", str(exc), parent=self)
            return
        self._reload(self.selected)

    def _disconnect(self) -> None:
        if self.program is None or not self.selected:
            return
        try:
            RpfPackageProgram.disconnect(self.program, self.selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not disconnect input", str(exc), parent=self)
            return
        self._reload(self.selected)

    def _remove(self) -> None:
        if self.program is None or not self.selected:
            return
        if self.selected == self.state.get("source_id"):
            messagebox.showinfo("Source retained", "The package source cannot be removed.", parent=self)
            return
        if not messagebox.askyesno(
            "Remove operation node",
            "Remove this node and its links? Existing external artifacts are untouched.",
            parent=self,
        ):
            return
        try:
            RpfPackageProgram.remove_node(self.program, self.selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remove operation node", str(exc), parent=self)
            return
        self._reload()

    def _auto_layout(self) -> None:
        if self.program is None:
            return
        try:
            RpfPackageProgram.auto_layout(self.program)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Build flow layout failed", str(exc), parent=self)
            return
        self._reload(self.selected)

    def _validate(self) -> None:
        if self.program is None:
            return
        try:
            report = RpfPackageProgram.describe(self.program, verify_graph=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Build flow validation failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "RPF build flow validation",
            f"Status: {report['status']}\nNodes: {report['summary']['nodes']}\n"
            f"Links: {report['summary']['links']}\nIssues: {report['summary']['issues']}"
            + ("\n\n" + "\n".join(report["issues"][:12]) if report["issues"] else ""),
            parent=self,
        )

    def _background(self, title: str, work, completed) -> None:
        if self._busy:
            messagebox.showinfo(
                "Build flow already running",
                "Wait for the current dry run or build to finish before starting "
                "another operation.", parent=self,
            )
            return
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._set_busy(True)
        self.status.set(f"{title}…")

        def runner() -> None:
            try:
                events.put(("result", work()))
            except Exception as exc:
                events.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()

        def poll() -> None:
            if not self.winfo_exists():
                return
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            self._set_busy(False)
            if kind == "error":
                self.status.set(f"{title} failed safely.")
                messagebox.showerror(title, str(value), parent=self)
            else:
                completed(value)

        self.after(80, poll)

    def _plan(self) -> None:
        if self.program is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save compiled RPF build-flow plan",
            initialfile=f"{self.program.stem}.plan.json", defaultextension=".json",
            filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return

        def completed(result) -> None:
            plan, evidence = result
            self.status.set(f"Ready dry-run plan: {plan}")
            messagebox.showinfo(
                "RPF build flow compiled",
                f"Nodes: {len(evidence['nodes'])}\nOutputs: {len(evidence['outputs'])}\n\n"
                f"Plan: {plan}\n\nNo operation was executed.", parent=self,
            )

        self._background(
            "Compiling RPF build flow",
            lambda: RpfPackageProgram.plan(self.program, output), completed,
        )

    def _run(self) -> None:
        if self.program is None:
            return
        report = filedialog.asksaveasfilename(
            parent=self, title="Save RPF build-flow execution report",
            initialfile=f"{self.program.stem}.execution.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not report:
            return
        if not messagebox.askyesno(
            "Run external authoring flow",
            "Create every configured external artifact now?\n\n"
            "The flow cannot install into GTA V or mutate a game archive. If any node "
            "fails, outputs created by this run are removed.", parent=self,
        ):
            return

        def completed(result) -> None:
            report_path, evidence = result
            self.status.set(f"Verified RPF build flow complete: {report_path}")
            messagebox.showinfo(
                "RPF build flow complete",
                f"Nodes: {len(evidence['nodes'])}\nArtifacts: {len(evidence['artifacts'])}\n\n"
                f"Report: {report_path}\n\nStock/game archives unchanged.", parent=self,
            )

        self._background(
            "Running RPF build flow",
            lambda: RpfPackageProgram.execute(
                self.program, self.project_root, report,
            ),
            completed,
        )
