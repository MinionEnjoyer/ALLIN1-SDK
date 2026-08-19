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
    ) -> None:
        super().__init__(parent, padding=8)
        self.graph = Path(graph).resolve()
        self.project_root = Path(project_root).resolve()
        self.game_path = Path(game_path).resolve() if game_path else None
        self.program: Path | None = None
        self.state: dict = {}
        self.selected: str | None = None
        self.dragging: str | None = None
        self.drag_last = (0.0, 0.0)
        self.connecting: str | None = None
        self.connection_line: int | None = None
        self.edge_items: dict[tuple[str, str], int] = {}
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
        ttk.Label(
            tools,
            text="Typed pins prevent invalid package operations",
            foreground="#52635c",
        ).pack(side="left", padx=(12, 0))
        create = ttk.Menubutton(tools, text="Create flow ▾")
        create_menu = tk.Menu(create, tearoff=False)
        for template_id, spec in PROGRAM_TEMPLATES.items():
            create_menu.add_command(
                label=spec["title"],
                command=lambda value=template_id: self._create(value),
            )
        create.configure(menu=create_menu)
        create.pack(side="right", padx=(5, 0))
        for text, command in (
            ("Open flow", self._choose),
            ("Auto layout", self._auto_layout), ("Validate", self._validate),
            ("Dry-run plan", self._plan), ("Run flow", self._run),
        ):
            ttk.Button(tools, text=text, command=command).pack(side="right", padx=(5, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        palette = ttk.Frame(body, padding=(4, 4, 10, 4), width=205)
        canvas_host = ttk.Frame(body)
        inspector = ttk.Frame(body, padding=(12, 4, 4, 4), width=310)
        body.add(palette, weight=0)
        body.add(canvas_host, weight=5)
        body.add(inspector, weight=1)

        ttk.Label(palette, text="NODE PALETTE", font=("Segoe UI Semibold", 9)).pack(
            anchor="w", pady=(0, 7),
        )
        for node_type in (
            "validate_graph", "materialize_tree", "build_rpf",
            "defragment_rpf", "plan_origin", "artifact_output",
        ):
            spec = NODE_SPECS[node_type]
            ttk.Button(
                palette, text=f"＋  {spec.title}",
                command=lambda value=node_type: self._add(value),
            ).pack(fill="x", pady=(0, 5))
        ttk.Separator(palette).pack(fill="x", pady=10)
        ttk.Label(
            palette,
            text=(
                "Wire the gold artifact pin into a compatible white input pin. "
                "A target accepts one input; reconnecting replaces it."
            ),
            foreground="#52635c", wraplength=185, justify="left",
        ).pack(anchor="w")

        surface = tk.Frame(canvas_host, background="#0F1512")
        surface.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            surface, background="#0F1512", highlightthickness=0,
            scrollregion=(0, 0, 5000, 3200),
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
        self.canvas.bind(
            "<MouseWheel>",
            lambda event: self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"),
        )

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
        for text, command in (
            ("Configure selected…", self._configure),
            ("Disconnect selected input", self._disconnect),
            ("Remove selected node", self._remove),
        ):
            ttk.Button(inspector, text=text, command=command).pack(
                fill="x", pady=(12 if text.startswith("Configure") else 5, 0),
            )
        ttk.Separator(inspector).pack(fill="x", pady=14)
        ttk.Label(
            inspector,
            text=(
                "Dry-run compiles program + graph hashes and all expected outputs. "
                "Run only creates new external artifacts; installation remains a "
                "separate reviewed action."
            ),
            foreground="#52635c", wraplength=285, justify="left",
        ).pack(anchor="w")
        ttk.Label(self, textvariable=self.status, foreground="#52635c").pack(
            fill="x", pady=(7, 0),
        )

    def _show_empty(self) -> None:
        self.canvas.delete("all")
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
        width, height = 5000, 3200
        for x in range(0, width + 1, 100):
            self.canvas.create_line(x, 0, x, height, fill="#18211D", tags=("grid",))
        for y in range(0, height + 1, 100):
            self.canvas.create_line(0, y, width, y, fill="#18211D", tags=("grid",))
        self.edge_items.clear()
        for link in self.state["links"]:
            key = (link["from"], link["to"])
            self.edge_items[key] = self.canvas.create_line(
                0, 0, 0, 0, smooth=True, splinesteps=22,
                width=4, fill="#C99B3A", tags=("program-edge",),
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
        x, y = node["x"], node["y"]
        header, body = self.COLORS[node["type"]]
        tags = ("program-node", f"pnode:{node_id}")
        self.canvas.create_rectangle(
            x + 5, y + 6, x + NODE_WIDTH + 5, y + NODE_HEIGHT + 6,
            fill="#080C0A", outline="", tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + NODE_WIDTH, y + NODE_HEIGHT, fill=body,
            outline="#E7B94B" if node_id == self.selected else "#53635C",
            width=3 if node_id == self.selected else 1, tags=tags,
        )
        self.canvas.create_rectangle(
            x, y, x + NODE_WIDTH, y + 29, fill=header, outline="", tags=tags,
        )
        self.canvas.create_text(
            x + 10, y + 15, text=spec.title, anchor="w", fill="#FFFFFF",
            font=("Segoe UI Semibold", 9), tags=tags,
        )
        in_text = " / ".join(spec.input_types) if spec.input_types else "START"
        out_text = spec.output_type or "END"
        self.canvas.create_text(
            x + 12, y + 51, text=f"IN  {in_text}", anchor="w", fill="#B9C8C1",
            font=("Consolas", 8), tags=tags,
        )
        self.canvas.create_text(
            x + 12, y + 70, text=f"OUT {out_text}", anchor="w", fill="#D8B660",
            font=("Consolas", 8), tags=tags,
        )
        if spec.input_types:
            self.canvas.create_oval(
                x - 7, y + 39, x + 7, y + 53, fill="#E6EEEA", outline="#111714",
                width=2, tags=(*tags, f"pin:{node_id}"),
            )
        if spec.output_type:
            self.canvas.create_oval(
                x + NODE_WIDTH - 7, y + 39, x + NODE_WIDTH + 7, y + 53,
                fill="#E7B94B", outline="#111714", width=2,
                tags=(*tags, f"pout:{node_id}"),
            )

    def _update_edges(self) -> None:
        for (parent, child), item in self.edge_items.items():
            source, target = self.state["nodes"][parent], self.state["nodes"][child]
            x1, y1 = source["x"] + NODE_WIDTH, source["y"] + 46
            x2, y2 = target["x"], target["y"] + 46
            curve = max(70, abs(x2 - x1) * 0.45)
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
                source["x"] + NODE_WIDTH, source["y"] + 46, x, y,
                fill="#E7B94B", width=3, dash=(7, 4),
            )
        else:
            self.dragging, self.drag_last = node_id, (x, y)

    def _motion(self, event: tk.Event) -> None:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.connecting and self.connection_line:
            source = self.state["nodes"][self.connecting]
            self.canvas.coords(
                self.connection_line, source["x"] + NODE_WIDTH,
                source["y"] + 46, x, y,
            )
            return
        if not self.dragging:
            return
        dx, dy = x - self.drag_last[0], y - self.drag_last[1]
        self.state["nodes"][self.dragging]["x"] += dx
        self.state["nodes"][self.dragging]["y"] += dy
        self.canvas.move(f"pnode:{self.dragging}", dx, dy)
        self.drag_last = (x, y)
        self._update_edges()

    def _release(self, event: tk.Event) -> None:
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
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.status.set(f"{title}…")

        def runner() -> None:
            try:
                events.put(("result", work()))
            except Exception as exc:
                events.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()

        def poll() -> None:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
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
