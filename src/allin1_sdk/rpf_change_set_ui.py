"""Embedded visual staging workspace for atomic RPF change sets."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

from allin1_sdk.rpf_change_set import RpfChangeSet
from allin1_sdk.rpf_tools import RpfEntryRecord, RpfExplorerService, RpfIndex


class RpfChangeSetFrame(ttk.Frame):
    """Stage and review multi-entry changes without writing an archive."""

    def __init__(
        self, parent: tk.Misc, *,
        get_index: Callable[[], RpfIndex | None],
        get_service: Callable[[], RpfExplorerService | None],
        get_selected: Callable[[], RpfEntryRecord | None],
    ) -> None:
        super().__init__(parent, padding=12)
        self.get_index = get_index
        self.get_service = get_service
        self.get_selected = get_selected
        self.change_set: Path | None = None
        self.authorized_root: Path | None = None
        self.status = tk.StringVar(
            value="Open an RPF, then create or open a source-bound change set.",
        )
        self._build()

    def _build(self) -> None:
        heading = ttk.Frame(self)
        heading.pack(fill="x", pady=(0, 10))
        ttk.Label(
            heading, text="Visual change set", font=("Segoe UI Semibold", 15),
            foreground="#1f7f42",
        ).pack(side="left")
        ttk.Label(
            heading,
            text="Stage many actions, then compile one reviewed atomic plan",
            foreground="#52635c",
        ).pack(side="left", padx=(12, 0))
        for text, command in (
            ("Create…", self._create), ("Open…", self._open),
            ("Authorize root…", self._authorize_root),
            ("Verify", self._verify), ("Compile plan…", self._compile),
        ):
            ttk.Button(heading, text=text, command=command).pack(
                side="right", padx=(5, 0),
            )

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 8))
        for text, command in (
            ("Replace selected…", self._stage_replace),
            ("Add file…", self._stage_add),
            ("Delete selected", self._stage_delete),
            ("Rename selected…", self._stage_rename),
            ("New directory…", self._stage_directory),
        ):
            ttk.Button(actions, text=text, command=command).pack(
                side="left", padx=(0, 5),
            )
        ttk.Button(actions, text="Remove staged", command=self._remove).pack(
            side="right",
        )
        ttk.Button(actions, text="Move down", command=lambda: self._move(1)).pack(
            side="right", padx=(0, 5),
        )
        ttk.Button(actions, text="Move up", command=lambda: self._move(-1)).pack(
            side="right", padx=(0, 5),
        )

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table, columns=("order", "action", "archive", "entry", "detail"),
            show="headings", selectmode="browse",
        )
        for column, title, width in (
            ("order", "#", 45), ("action", "Action", 90),
            ("archive", "Virtual archive", 230), ("entry", "Entry", 330),
            ("detail", "Payload / destination", 360),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, stretch=column in {"entry", "detail"})
        yscroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        ttk.Label(
            self, textvariable=self.status, foreground="#52635c",
            wraplength=1120, justify="left",
        ).pack(fill="x", pady=(8, 0))
        ttk.Label(
            self,
            text=(
                "This workspace never writes an RPF. Compile produces the existing "
                "hash-bound multi-entry plan; Apply remains a separate staged, "
                "receipt-owned transaction with rollback."
            ),
            foreground="#52635c", wraplength=1120, justify="left",
        ).pack(fill="x", pady=(4, 0))

    def archive_changed(self) -> None:
        self.authorized_root = None
        index = self.get_index()
        if self.change_set is None or index is None:
            return
        try:
            state = RpfChangeSet.validate(self.change_set)
        except (OSError, ValueError):
            self.change_set = None
            self.tree.delete(*self.tree.get_children())
            return
        if state["archive"] != index.source.resolve():
            self.change_set = None
            self.tree.delete(*self.tree.get_children())
            self.status.set(
                "The opened archive changed; create or open its matching change set.",
            )

    def _authorize_root(self) -> None:
        service = self.get_service()
        if service is None:
            messagebox.showerror("RPF required", "Open an RPF archive first.", parent=self)
            return
        selected = filedialog.askdirectory(
            parent=self, title="Authorize one external RPF authoring root",
        )
        if not selected:
            return
        try:
            probe = RpfExplorerService(
                service.project_root, service.gta_path, workspace_roots=(selected,),
            )
        except ValueError as exc:
            messagebox.showerror("Unsafe workspace root", str(exc), parent=self)
            return
        self.authorized_root = probe.workspace_roots[0]
        self.status.set(
            f"Authorized external workspace root for this Explorer session: "
            f"{self.authorized_root}"
        )

    def _require_index(self) -> RpfIndex | None:
        index = self.get_index()
        if index is None:
            messagebox.showerror(
                "RPF required", "Open an RPF archive first.", parent=self,
            )
        return index

    def _require_workspace(self) -> Path | None:
        if self.change_set is None:
            messagebox.showerror(
                "Change set required", "Create or open an RPF change set first.",
                parent=self,
            )
        return self.change_set

    def _create(self) -> None:
        index = self._require_index()
        if index is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Create visual RPF change set",
            initialfile=f"{index.source.stem}-changes.json",
            defaultextension=".json", filetypes=(("RPF change set", "*.json"),),
        )
        if not output:
            return
        try:
            self.change_set = RpfChangeSet.create(index, output)
            self._reload()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create change set", str(exc), parent=self)

    def _open(self) -> None:
        index = self._require_index()
        if index is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Open visual RPF change set",
            filetypes=(("RPF change set", "*.json"), ("All files", "*.*")),
        )
        if not selected:
            return
        try:
            state = RpfChangeSet.validate(selected)
            if state["archive"] != index.source.resolve():
                raise ValueError("This change set is bound to a different RPF archive")
            self.change_set = Path(selected).resolve()
            self._reload()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid RPF change set", str(exc), parent=self)

    def _reload(self, select: str | None = None) -> None:
        workspace = self._require_workspace()
        if workspace is None:
            return
        try:
            report = RpfChangeSet.describe(workspace)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not read change set", str(exc), parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        for number, item in enumerate(report["actions"], start=1):
            detail = item.get("new_entry", "")
            if "payload" in item:
                detail = item["payload"]["path"]
            self.tree.insert(
                "", "end", iid=item["id"], values=(
                    number, item["action"], item["archive_path"] or "root",
                    item["entry"], detail,
                ),
            )
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.see(select)
        self.status.set(
            f"{report['summary']['actions']} staged action(s) · {workspace}"
        )

    def _stage(self, action: str, entry: str, **options) -> None:
        workspace = self._require_workspace()
        if workspace is None:
            return
        try:
            action_id = RpfChangeSet.stage(workspace, action, entry, **options)
            self._reload(action_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not stage RPF action", str(exc), parent=self)

    def _stage_replace(self) -> None:
        entry = self.get_selected()
        if entry is None or entry.kind == "directory":
            messagebox.showerror(
                "File entry required", "Select a file or resource entry to replace.",
                parent=self,
            )
            return
        payload = filedialog.askopenfilename(
            parent=self, title=f"Replacement payload for {entry.name}",
        )
        if payload:
            self._stage(
                "replace", entry.path, archive_path=entry.archive_path,
                payload=payload,
            )

    def _stage_add(self) -> None:
        payload = filedialog.askopenfilename(parent=self, title="Payload for new RPF entry")
        if not payload:
            return
        selected = self.get_selected()
        archive_path = selected.archive_path if selected else ""
        parent = ""
        if selected:
            parent = selected.path if selected.kind == "directory" else str(
                PurePosixPath(selected.path).parent
            )
            if parent == ".":
                parent = ""
        initial = f"{parent}/{Path(payload).name}".strip("/")
        entry = simpledialog.askstring(
            "New RPF entry", "Virtual entry path:", initialvalue=initial, parent=self,
        )
        if entry:
            self._stage("add", entry, archive_path=archive_path, payload=payload)

    def _stage_delete(self) -> None:
        entry = self.get_selected()
        if entry is None:
            messagebox.showerror("Entry required", "Select an entry to delete.", parent=self)
            return
        action = "rmdir" if entry.kind == "directory" else "delete"
        self._stage(action, entry.path, archive_path=entry.archive_path)

    def _stage_rename(self) -> None:
        entry = self.get_selected()
        if entry is None:
            messagebox.showerror("Entry required", "Select an entry to rename.", parent=self)
            return
        if entry.kind == "archive":
            messagebox.showerror(
                "Nested RPF rename unavailable",
                "Nested archive identities cannot be renamed in place.", parent=self,
            )
            return
        parent_path = str(PurePosixPath(entry.path).parent)
        parent_path = "" if parent_path == "." else parent_path
        name = simpledialog.askstring(
            "Rename RPF entry", "New name in the same directory:",
            initialvalue=entry.name, parent=self,
        )
        if not name:
            return
        destination = f"{parent_path}/{name}".strip("/")
        self._stage(
            "rename", entry.path, archive_path=entry.archive_path,
            new_entry=destination,
        )

    def _stage_directory(self) -> None:
        selected = self.get_selected()
        archive_path = selected.archive_path if selected else ""
        parent = selected.path if selected and selected.kind == "directory" else ""
        initial = f"{parent}/new_folder".strip("/")
        entry = simpledialog.askstring(
            "New RPF directory", "Virtual directory path:",
            initialvalue=initial, parent=self,
        )
        if entry:
            self._stage("mkdir", entry, archive_path=archive_path)

    def _selected_action(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showerror(
                "Staged action required", "Select a staged action first.", parent=self,
            )
            return None
        return selected[0]

    def _remove(self) -> None:
        workspace = self._require_workspace()
        action_id = self._selected_action()
        if workspace is None or action_id is None:
            return
        try:
            RpfChangeSet.remove(workspace, action_id)
            self._reload()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remove action", str(exc), parent=self)

    def _move(self, direction: int) -> None:
        workspace = self._require_workspace()
        action_id = self._selected_action()
        if workspace is None or action_id is None:
            return
        items = self.tree.get_children()
        position = items.index(action_id) + 1 + direction
        if position < 1 or position > len(items):
            return
        try:
            RpfChangeSet.move(workspace, action_id, position)
            self._reload(action_id)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not reorder action", str(exc), parent=self)

    def _verify(self) -> None:
        workspace = self._require_workspace()
        if workspace is None:
            return
        try:
            report = RpfChangeSet.describe(workspace, verify_files=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Change-set verification failed", str(exc), parent=self)
            return
        self.status.set(
            f"Verified archive and payload bindings for {report['summary']['actions']} action(s)."
        )

    def _compile(self) -> None:
        workspace = self._require_workspace()
        service = self.get_service()
        if workspace is None or service is None:
            return
        if self.authorized_root is not None:
            try:
                service = RpfExplorerService(
                    service.project_root, service.gta_path,
                    workspace_roots=(self.authorized_root,),
                )
            except ValueError as exc:
                messagebox.showerror("Unsafe workspace root", str(exc), parent=self)
                return
        output = filedialog.asksaveasfilename(
            parent=self, title="Compile guarded atomic RPF plan",
            initialfile=f"{workspace.stem}-plan.json", defaultextension=".json",
            filetypes=(("RPF atomic plan", "*.json"),),
        )
        if not output:
            return
        try:
            plan_path, plan = RpfChangeSet.compile_plan(workspace, service, output)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not compile change set", str(exc), parent=self)
            return
        self.status.set(
            f"Compiled {len(plan['changes'])} action(s) into {plan['status']} plan: {plan_path}"
        )
        messagebox.showinfo(
            "Atomic RPF plan ready",
            "The archive is unchanged. Review the plan, then use Apply entry-change "
            f"plan as a separate transaction.\n\n{plan_path}", parent=self,
        )
