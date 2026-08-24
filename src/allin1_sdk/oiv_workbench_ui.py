"""Embedded desktop workspace for inspecting and compiling OIV package recipes."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk

from allin1_sdk.oiv_workbench import OivPlan, OivWorkbench


class OivWorkbenchFrame(ttk.Frame):
    """Expose recipe inspection and safe outputs without a prompt cascade."""

    def __init__(
        self, parent: tk.Misc, project_root: str | Path, *,
        installation_roots: tuple[Path, ...] = (), on_close=None, on_help=None,
    ) -> None:
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.project_root = Path(project_root).resolve()
        self.installation_roots = tuple(Path(item).resolve() for item in installation_roots)
        self._on_close = on_close
        self._on_help = on_help
        self._busy = False
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._controls: list[tk.Widget] = []
        self._action_buttons: dict[str, ttk.Button] = {}
        self.plan: OivPlan | None = None
        self.source: Path | None = None
        self.summary = tk.StringVar(value="Open an OIV/ZIP package or unpacked recipe folder.")
        self.status = tk.StringVar(value="No package recipe is open.")
        self.readiness = tk.StringVar(value="NO RECIPE LOADED")
        self.game_path = tk.StringVar(
            value=str(self.installation_roots[0]) if len(self.installation_roots) == 1 else ""
        )
        self._build_ui()

    @property
    def busy(self) -> bool:
        return self._busy

    def has_active_work(self) -> bool:
        return self._busy

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(
            heading, text="Package recipes", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(side="left")
        if self._on_help is not None:
            ttk.Button(
                heading, text="Help", command=lambda: self._on_help("package-recipes"),
            ).pack(side="right", padx=(0, 7))
        ttk.Label(
            outer,
            text=(
                "Inspect ordered package instructions without executing them. The SDK enables "
                "only outputs that can be represented by its guarded package and RPF tools."
            ),
            foreground="#52635c", wraplength=980, justify="left",
        ).pack(anchor="w", pady=(3, 7))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        open_menu = tk.Menu(toolbar, tearoff=False)
        open_menu.add_command(label="Open OIV or ZIP package…", command=self._choose_package)
        open_menu.add_command(label="Open unpacked recipe folder…", command=self._choose_folder)
        self.open_button = ttk.Menubutton(
            toolbar, text="Open recipe…", menu=open_menu, style="Accent.TButton",
        )
        self.open_button.pack(side="left")
        self._controls.append(self.open_button)
        self.open_folder_button = ttk.Button(
            toolbar, text="Open source folder", command=self._open_source_folder,
            state="disabled",
        )
        self.open_folder_button.pack(side="left", padx=(7, 0))
        self._controls.append(self.open_folder_button)
        ttk.Label(
            toolbar, textvariable=self.summary, foreground="#37584d",
            font=("Segoe UI Semibold", 9),
        ).pack(side="left", padx=(14, 0))

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 7))
        self.progress.pack_forget()

        body = ttk.Panedwindow(outer, orient="horizontal")
        self.body = body
        body.pack(fill="both", expand=True)
        review = ttk.Frame(body)
        actions = ttk.LabelFrame(body, text="Available outputs", padding=10, width=330)
        body.add(review, weight=5)
        body.add(actions, weight=2)

        self.review_tabs = ttk.Notebook(review)
        self.review_tabs.grid(row=0, column=0, sticky="nsew")
        review.rowconfigure(0, weight=1)
        review.rowconfigure(1, weight=0)
        review.columnconfigure(0, weight=1)
        operation_page = ttk.Frame(self.review_tabs, padding=7)
        finding_page = ttk.Frame(self.review_tabs, padding=7)
        self.review_tabs.add(operation_page, text="Ordered operations")
        self.review_tabs.add(finding_page, text="Findings")

        self.operations = ttk.Treeview(
            operation_page,
            columns=("action", "archive", "source", "target", "status"),
            show="tree headings", selectmode="browse", height=12,
        )
        for column, label in (
            ("#0", "#"), ("action", "Action"), ("archive", "Archive"),
            ("source", "Source"), ("target", "Target"), ("status", "Translation"),
        ):
            self.operations.heading(column, text=label)
        self.operations.column("#0", width=36, stretch=False, anchor="e")
        self.operations.column("action", width=64, stretch=False)
        self.operations.column("archive", width=130, stretch=True)
        self.operations.column("source", width=125, stretch=True)
        self.operations.column("target", width=145, stretch=True)
        self.operations.column("status", width=90, stretch=False)
        operation_scroll = ttk.Scrollbar(
            operation_page, orient="vertical", command=self.operations.yview,
        )
        self.operation_xscroll = ttk.Scrollbar(
            operation_page, orient="horizontal", command=self.operations.xview,
        )
        self.operations.configure(
            yscrollcommand=operation_scroll.set,
            xscrollcommand=self.operation_xscroll.set,
        )
        self.operations.grid(row=0, column=0, sticky="nsew")
        operation_scroll.grid(row=0, column=1, sticky="ns")
        self.operation_xscroll.grid(row=1, column=0, sticky="ew")
        operation_page.rowconfigure(0, weight=1)
        operation_page.columnconfigure(0, weight=1)
        self.operations.bind("<<TreeviewSelect>>", self._operation_selected)

        self.findings = ttk.Treeview(
            finding_page, columns=("code", "operation", "message"),
            show="tree headings", selectmode="browse", height=12,
        )
        for column, label in (
            ("#0", "Level"), ("code", "Code"),
            ("operation", "Operation"), ("message", "Finding"),
        ):
            self.findings.heading(column, text=label)
        self.findings.column("#0", width=75, stretch=False)
        self.findings.column("code", width=180, stretch=False)
        self.findings.column("operation", width=76, stretch=False, anchor="center")
        self.findings.column("message", width=440, stretch=True)
        self.findings.tag_configure("error", foreground="#9f1d20")
        self.findings.tag_configure("warning", foreground="#9a6500")
        finding_scroll = ttk.Scrollbar(
            finding_page, orient="vertical", command=self.findings.yview,
        )
        self.finding_xscroll = ttk.Scrollbar(
            finding_page, orient="horizontal", command=self.findings.xview,
        )
        self.findings.configure(
            yscrollcommand=finding_scroll.set,
            xscrollcommand=self.finding_xscroll.set,
        )
        self.findings.grid(row=0, column=0, sticky="nsew")
        finding_scroll.grid(row=0, column=1, sticky="ns")
        self.finding_xscroll.grid(row=1, column=0, sticky="ew")
        finding_page.rowconfigure(0, weight=1)
        finding_page.columnconfigure(0, weight=1)
        self.findings.bind("<<TreeviewSelect>>", self._finding_selected)

        detail = ttk.LabelFrame(review, text="Selection details", padding=7)
        detail.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.detail = tk.Text(
            detail, height=5, wrap="word", state="disabled", relief="flat",
            background="#f4f7f5", foreground="#26332e", padx=7, pady=7,
        )
        self.detail.pack(fill="x")

        tk.Label(
            actions, textvariable=self.readiness, background="#e3eee7",
            foreground="#176b36", font=("Segoe UI Semibold", 10), padx=8, pady=6,
        ).pack(fill="x", pady=(0, 9))
        ttk.Label(
            actions,
            text="Outputs are external reports, payloads, or inert plans; GTA V is not changed.",
            foreground="#52635c", wraplength=500, justify="left",
        ).pack(fill="x", pady=(0, 8))
        ttk.Label(
            actions, text="Game path (only when required)", style="FieldLabel.TLabel",
        ).pack(anchor="w")
        game = ttk.Frame(actions)
        game.pack(fill="x", pady=(4, 8))
        self.game_entry = ttk.Entry(game, textvariable=self.game_path)
        self.game_entry.pack(side="left", fill="x", expand=True)
        self.game_button = ttk.Button(game, text="Browse…", command=self._choose_game)
        self.game_button.pack(side="left", padx=(6, 0))
        self._controls.extend((self.game_entry, self.game_button))
        action_grid = ttk.Frame(actions)
        action_grid.pack(fill="x")
        action_grid.columnconfigure(0, weight=1)
        action_grid.columnconfigure(1, weight=1)
        for index, (key, label, command) in enumerate((
            ("report", "Export inspection report…", self._export_report),
            ("compile", "Compile against existing RPF…", self._compile_existing),
            ("batches", "Export atomic RPF batches…", self._export_batches),
            ("created", "Build declared new archives…", self._build_created),
            ("managed", "Export managed package…", self._export_managed),
        )):
            button = ttk.Button(
                action_grid, text=label, command=command, state="disabled",
            )
            button.grid(
                row=index // 2, column=index % 2,
                sticky="ew", padx=(0, 3) if index % 2 == 0 else (3, 0), pady=2,
            )
            self._action_buttons[key] = button
        ttk.Separator(actions).pack(fill="x", pady=8)
        ttk.Label(actions, text="Recent output", style="FieldLabel.TLabel").pack(anchor="w")
        self.activity = tk.Text(
            actions, height=3, wrap="word", relief="flat",
            background="#f4f7f5", foreground="#37584d", padx=7, pady=7,
        )
        self.activity.pack(fill="both", expand=True, pady=(5, 0))
        self.activity.insert("1.0", "No output has been created.")
        self.activity.configure(state="disabled")

        ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=1020, justify="left",
        ).pack(side="bottom", fill="x", pady=(8, 0))

    def _choose_package(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open an OIV package recipe",
            filetypes=(("OIV package", "*.oiv *.zip"), ("All files", "*.*")),
        )
        if selected:
            self.open_source(selected)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Open an unpacked OIV package recipe",
        )
        if selected:
            self.open_source(selected)

    def open_source(self, source: str | Path) -> None:
        if self._busy:
            messagebox.showinfo(
                "Recipe operation still running",
                "Wait for the current operation to finish before opening another recipe.",
                parent=self,
            )
            return
        path = Path(source).expanduser().resolve()
        self.source = path
        self.plan = None
        self._clear_review()
        self.summary.set(f"Inspecting {path.name}…")
        self._run(
            "Reading assembly.xml and validating ordered package operations…",
            lambda: OivWorkbench().inspect(path), self._loaded,
        )

    def _loaded(self, plan: OivPlan) -> None:
        self.plan = plan
        self.source = plan.source
        errors = sum(item.severity == "error" for item in plan.findings)
        warnings = sum(item.severity == "warning" for item in plan.findings)
        self.summary.set(
            f"{plan.name} · v{plan.version or 'unknown'} · {len(plan.operations):,} operations · "
            f"{errors} errors · {warnings} warnings"
        )
        self._populate_review()
        self._set_action_states()
        self.open_folder_button.configure(state="normal")
        self.status.set(
            "Inspection complete. No package instructions were executed and GTA V was not changed."
        )

    def _clear_review(self) -> None:
        self.operations.delete(*self.operations.get_children())
        self.findings.delete(*self.findings.get_children())
        self.readiness.set("INSPECTING RECIPE")
        self._set_detail("Select an ordered operation or finding to see its details.")
        self._set_action_states()
        self.open_folder_button.configure(state="disabled")

    def _populate_review(self) -> None:
        assert self.plan is not None
        self.operations.delete(*self.operations.get_children())
        for item in self.plan.operations:
            self.operations.insert(
                "", "end", iid=str(item.number), text=str(item.number),
                values=(
                    item.kind.title(), " → ".join(item.archives) or "Filesystem",
                    item.source or "—", item.target or "—",
                    "Supported" if item.supported else "Review",
                ),
            )
        self.findings.delete(*self.findings.get_children())
        for index, item in enumerate(self.plan.findings):
            self.findings.insert(
                "", "end", iid=str(index), text=item.severity.title(),
                values=(item.code, item.operation or "—", item.message),
                tags=(item.severity.casefold(),),
            )
        if not self.plan.findings:
            self.findings.insert(
                "", "end", iid="clear", text="Clear",
                values=("no_blockers", "—", "No recipe blockers were found."),
            )
        if self.plan.rpf_recipe_compilable:
            result = "EXISTING RPF COMPILE READY"
        elif self.plan.translatable and self.plan.created_archive_operations:
            result = "NEW ARCHIVE BUILD READY"
        elif self.plan.managed_exportable:
            result = "MANAGED PACKAGE READY"
        elif self.plan.translatable:
            result = "ATOMIC RPF EXPORT READY"
        else:
            result = "MANUAL REVIEW REQUIRED"
        self.readiness.set(result)
        if self.plan.operations:
            self.operations.selection_set(str(self.plan.operations[0].number))
            self._operation_selected()

    def _operation_selected(self, _event: object | None = None) -> None:
        if self.plan is None:
            return
        selected = self.operations.selection()
        if not selected:
            return
        number = int(selected[0])
        item = next(operation for operation in self.plan.operations if operation.number == number)
        edit_summary = f"\nStructured edits: {len(item.edits):,}" if item.edits else ""
        self._set_detail(
            f"Operation {item.number}: {item.kind.title()}\n"
            f"Archive chain: {' → '.join(item.archives) or 'Filesystem'}\n"
            f"Source: {item.source or 'None'}\nTarget: {item.target or 'None'}\n"
            f"Translation: {'Supported' if item.supported else 'Manual review required'}\n"
            f"{item.detail}{edit_summary}"
        )

    def _finding_selected(self, _event: object | None = None) -> None:
        if self.plan is None:
            return
        selected = self.findings.selection()
        if not selected:
            return
        if selected[0] == "clear":
            self._set_detail("No recipe blockers were found.")
            return
        item = self.plan.findings[int(selected[0])]
        self._set_detail(
            f"{item.severity.upper()}: {item.code}\n"
            f"Operation: {item.operation or 'Package-level'}\n\n{item.message}"
        )

    def _set_detail(self, value: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", value)
        self.detail.configure(state="disabled")

    def _set_action_states(self) -> None:
        plan = self.plan
        available = {
            "report": plan is not None,
            "compile": bool(plan and plan.rpf_recipe_compilable),
            "batches": bool(plan and plan.translatable and plan.rpf_batch_operations),
            "created": bool(plan and plan.translatable and plan.created_archive_operations),
            "managed": bool(plan and plan.managed_exportable),
        }
        for key, button in self._action_buttons.items():
            button.configure(state="normal" if available[key] and not self._busy else "disabled")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for control in self._controls:
            if control.winfo_exists():
                control.configure(state=state)
        if busy:
            self.status.set(message)
            if not self.progress.winfo_manager():
                self.progress.pack(fill="x", pady=(0, 7), before=self.body)
            self.progress.start(12)
        else:
            self.progress.stop()
            if self.progress.winfo_manager():
                self.progress.pack_forget()
        self._set_action_states()

    def _run(self, message: str, work, completed) -> None:
        if self._busy:
            return
        self._set_busy(True, message)

        def runner() -> None:
            try:
                self._events.put(("result", (completed, work())))
            except Exception as exc:
                self._events.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()
        self.after(70, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            kind, payload = self._events.get_nowait()
        except queue.Empty:
            self.after(70, self._poll)
            return
        self._set_busy(False)
        if kind == "error":
            self.status.set("Recipe operation failed safely. No game files were changed.")
            if self.plan is None:
                self.summary.set("Recipe inspection failed. Review the error and choose another package.")
                self.readiness.set("INSPECTION FAILED")
            messagebox.showerror("Package recipe operation failed", str(payload), parent=self)
            return
        completed, result = payload
        completed(result)

    def _validated_game(self) -> Path | None:
        value = self.game_path.get().strip()
        game = Path(value).expanduser().resolve() if value else None
        if game is None or not game.is_dir():
            messagebox.showerror(
                "GTA V installation required",
                "Select the matching Legacy or Enhanced GTA V installation first.",
                parent=self,
            )
            return None
        return game

    def _choose_game(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select GTA V Legacy or Enhanced installation",
        )
        if selected:
            self.game_path.set(selected)

    def _export_report(self) -> None:
        if self.plan is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Export package-recipe inspection report",
            initialdir=str(self.plan.source.parent),
            initialfile=f"{self.plan.source.stem}-recipe-plan.md",
            defaultextension=".md", filetypes=(("Markdown", "*.md"),),
        )
        if output:
            self._run(
                "Writing Markdown and JSON inspection reports…",
                lambda: self.plan.write_report(output),
                lambda path: self._completed_output("Inspection report", (path, path.with_suffix('.json'))),
            )

    def _compile_existing(self) -> None:
        if self.plan is None or not self.plan.rpf_recipe_compilable:
            return
        game = self._validated_game()
        if game is None:
            return
        expected = next(
            operation.archives[0] for operation in self.plan.operations
            if operation.kind != "archive" and operation.archives
        )
        archive = filedialog.askopenfilename(
            parent=self, title=f"Select matching {PurePosixPath(expected).name}",
            filetypes=(("RPF archive", "*.rpf"), ("All files", "*.*")),
        )
        if not archive:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a new recipe-compile output folder", mustexist=False,
        )
        if not destination or not messagebox.askyesno(
            "Compile guarded RPF recipe?",
            "The selected archive will be read and hash-bound, not modified. The output "
            "will contain verified payloads, an audit, and an inert RPF plan.",
            parent=self,
        ):
            return
        plan = self.plan
        archive_path = Path(archive).resolve()

        def work():
            from allin1_sdk.rpf_tools import RpfExplorerService
            service = RpfExplorerService(
                self.project_root, game, workspace_roots=(archive_path.parent,),
            )
            return OivWorkbench().compile_rpf_recipe_bundle(
                plan, archive_path, destination, service=service,
            )

        self._run(
            "Compiling ordered XML, text, native, and file operations into an inert plan…",
            work, lambda paths: self._completed_output("Verified RPF recipe", paths),
        )

    def _export_batches(self) -> None:
        if self.plan is None:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a new atomic RPF batch folder", mustexist=False,
        )
        if destination:
            plan = self.plan
            self._run(
                "Exporting payload-backed atomic RPF batch manifests…",
                lambda: OivWorkbench().export_rpf_batch_manifests(plan, destination),
                lambda paths: self._completed_output("Atomic RPF batches", paths),
            )

    def _build_created(self) -> None:
        if self.plan is None:
            return
        game = self._validated_game()
        if game is None:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a new verified package folder", mustexist=False,
        )
        if destination:
            plan = self.plan
            self._run(
                "Replaying bounded instructions and recursively verifying new archives…",
                lambda: OivWorkbench().export_created_rpf_package(
                    plan, destination, project_root=self.project_root, gta_path=game,
                ),
                lambda path: self._completed_output("Verified new-archive package", (path,)),
            )

    def _export_managed(self) -> None:
        if self.plan is None:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Select a new managed-package folder", mustexist=False,
        )
        if destination:
            plan = self.plan
            self._run(
                "Exporting receipt-owned files and validating mod.toml…",
                lambda: OivWorkbench().export_managed_package(plan, destination),
                lambda path: self._completed_output("Managed package", (path,)),
            )

    def _completed_output(self, label: str, paths) -> None:
        resolved = tuple(Path(path).resolve() for path in paths)
        self.status.set(f"{label} completed. GTA V was not changed.")
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.insert("1.0", f"{label}\n" + "\n".join(str(path) for path in resolved))
        self.activity.configure(state="disabled")

    def _open_source_folder(self) -> None:
        if self.source is None or os.name != "nt":
            return
        target = self.source if self.source.is_dir() else self.source.parent
        os.startfile(target)  # type: ignore[attr-defined]
