"""Interactive RPF inspection and guarded transaction workspace."""

from __future__ import annotations

import io
import json
import os
import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from allin1_sdk.binary_workspace_ui import BinaryWorkspaceFrame
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    NATIVE_XML_IMPORT_SUFFIXES,
    NativeAssetInspector,
)
from allin1_sdk.paths import user_data_root
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_catalog import RpfCatalogResult, RpfCatalogService
from allin1_sdk.rpf_change_set_ui import RpfChangeSetFrame
from allin1_sdk.rpf_delta import RpfDeltaPlanResult, derive_rpf_change_plan
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_graph_ui import RpfPackageGraphFrame
from allin1_sdk.package_graph import PackageGraphWorkspace
from allin1_sdk.rpf_tools import RpfEntryRecord, RpfExplorerService, RpfIndex
from allin1_sdk.help_center import HelpCenterDialog
from allin1_sdk.ui_foundation import place_window


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class RpfProgressDialog(tk.Toplevel):
    """Run one archive transaction off the Tk thread and show guarded phases."""

    def __init__(
        self, parent: tk.Misc, title: str, work, on_success, on_failure,
    ) -> None:
        super().__init__(parent)
        self.title(title)
        place_window(self, preferred=(520, 150), minimum=(440, 135))
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        self.message = tk.StringVar(value="Preparing guarded transaction…")
        ttk.Label(
            frame, textvariable=self.message, wraplength=470, justify="left",
        ).pack(fill="x", pady=(0, 12))
        self.progress = ttk.Progressbar(frame, maximum=100, value=0)
        self.progress.pack(fill="x")
        ttk.Label(
            frame, text="Do not start GTA V while this operation is running.",
            foreground="#52635c",
        ).pack(anchor="w", pady=(10, 0))
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._on_success = on_success
        self._on_failure = on_failure

        def progress(message: str, percent: int) -> None:
            self._events.put(("progress", (message, percent)))

        def runner() -> None:
            try:
                self._events.put(("result", work(progress)))
            except Exception as exc:  # marshalled to the Tk thread
                self._events.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()
        self.after(80, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "progress":
                    message, percent = payload
                    self.message.set(str(message))
                    self.progress.configure(value=int(percent))
                elif kind == "result":
                    self.grab_release()
                    self.destroy()
                    self._on_success(payload)
                    return
                else:
                    self.grab_release()
                    self.destroy()
                    self._on_failure(payload)
                    return
        except queue.Empty:
            self.after(80, self._poll)


class Gxt2WorkspaceFrame(ttk.Frame):
    """Search and edit one validated GXT2 hash/text workspace in place."""

    DISPLAY_LIMIT = 2500

    def __init__(
        self, parent: tk.Misc, workspace: str | Path, *, on_close=None,
    ) -> None:
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.workspace = Path(workspace).resolve()
        self._on_close = on_close
        self.entries: dict[int, dict[str, object]] = {}
        self.loaded_hash: int | None = None
        self.dirty = False
        self._restoring_selection = False
        self.query = tk.StringVar()
        self.status = tk.StringVar(value="Loading validated GXT2 table…")

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer, text="GXT2 game-text editor", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Edit label hashes and UTF-8 text in a recovery-backed workspace. "
                "The immutable source and exact RPF binding are never modified here."
            ),
            foreground="#52635c", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(3, 10))

        search = ttk.Frame(outer)
        search.pack(fill="x", pady=(0, 8))
        ttk.Label(search, text="Find hash or text").pack(side="left")
        query = ttk.Entry(search, textvariable=self.query)
        query.pack(side="left", fill="x", expand=True, padx=8)
        query.bind("<Return>", lambda _event: self._reload())
        ttk.Button(search, text="Search", command=self._reload).pack(side="left")
        ttk.Button(search, text="Clear", command=self._clear_search).pack(
            side="left", padx=(6, 0),
        )

        ttk.Label(outer, textvariable=self.status, foreground="#52635c").pack(
            side="bottom", fill="x", pady=(8, 0),
        )

        split = ttk.Panedwindow(outer, orient="vertical")
        self._gxt2_split = split
        split.pack(fill="both", expand=True)
        table_frame = ttk.Frame(split)
        editor_frame = ttk.Frame(split, padding=(0, 10, 0, 0))
        split.add(table_frame, weight=3)
        split.add(editor_frame, weight=2)

        self.tree = ttk.Treeview(
            table_frame, columns=("hash", "text"), show="headings", selectmode="browse",
        )
        self.tree.heading("hash", text="Label hash")
        self.tree.heading("text", text="Game text")
        self.tree.column("hash", width=130, minwidth=110, stretch=False)
        self.tree.column("text", width=850, minwidth=320)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._select)

        editor_header = ttk.Frame(editor_frame)
        editor_header.pack(fill="x")
        ttk.Label(
            editor_header, text="Selected text", font=("Segoe UI Semibold", 10),
        ).pack(side="left")
        for label, command in (
            ("Save", self._save), ("Add entry…", self._add),
            ("Remove", self._remove), ("Undo", self._undo),
            ("Build verified…", self._build),
        ):
            ttk.Button(editor_header, text=label, command=command).pack(
                side="left", padx=(7, 0),
            )
        ttk.Button(
            editor_header, text="Close editor", command=self._close_panel,
        ).pack(side="right")
        self.text = tk.Text(
            editor_frame, height=7, wrap="word", font=("Consolas", 10),
            undo=True, borderwidth=1, relief="solid",
        )
        self.text.pack(fill="both", expand=True, pady=(4, 0))
        self.text.bind("<<Modified>>", self._text_modified)
        self.text.bind("<Control-s>", self._save_shortcut)
        self._reload()
        self.after_idle(self._balance_split)
        self.after(120, self._balance_split)

    def _balance_split(self) -> None:
        split = getattr(self, "_gxt2_split", None)
        if split is None or not split.winfo_exists():
            return
        height = split.winfo_height()
        if height > 80:
            split.sashpos(0, round(height * 0.58))

    def _close_panel(self) -> None:
        if not self._confirm_unsaved():
            return
        if self._on_close is not None:
            self._on_close()
        else:
            self.destroy()

    def _clear_search(self) -> None:
        self.query.set("")
        self._reload()

    def _reload(self, select_hash: int | None = None) -> None:
        if self.dirty and not self._confirm_unsaved():
            return
        try:
            state = Gxt2Workspace.validate(self.workspace)
        except (OSError, ValueError) as exc:
            messagebox.showerror("GXT2 workspace validation failed", str(exc), parent=self)
            self.status.set("Workspace validation failed; no edits were made.")
            return
        self.entries = {int(item["hash"]): item for item in state["entries"]}
        wanted = self.query.get().strip().casefold()
        matches = [
            item for item in state["entries"]
            if not wanted or wanted in str(item["hash_hex"]).casefold()
            or wanted in str(item["text"]).casefold()
        ]
        self.tree.delete(*self.tree.get_children())
        for item in matches[:self.DISPLAY_LIMIT]:
            label_hash = int(item["hash"])
            self.tree.insert(
                "", "end", iid=str(label_hash),
                values=(item["hash_hex"], str(item["text"]).replace("\n", " ↵ ")),
            )
        shown = min(len(matches), self.DISPLAY_LIMIT)
        suffix = " · refine the search to see more" if len(matches) > shown else ""
        self.status.set(
            f"{len(state['entries']):,} total entries · {shown:,} of {len(matches):,} "
            f"matches shown{suffix}"
        )
        target_hash = select_hash if select_hash is not None else self.loaded_hash
        if target_hash is not None and self.tree.exists(str(target_hash)):
            self.tree.selection_set(str(target_hash))
            self.tree.see(str(target_hash))
            self._select()
        elif self.loaded_hash is not None:
            self.loaded_hash = None
            self.dirty = False
            self.text.delete("1.0", "end")
            self.text.edit_modified(False)

    def _selected_hash(self) -> int | None:
        selected = self.tree.selection()
        return int(selected[0]) if selected else None

    def _select(self, _event: object | None = None) -> None:
        if self._restoring_selection:
            return
        label_hash = self._selected_hash()
        if label_hash is None or label_hash not in self.entries:
            return
        if (
            self.dirty and self.loaded_hash is not None
            and label_hash != self.loaded_hash and not self._confirm_unsaved()
        ):
            self._restoring_selection = True
            try:
                if self.tree.exists(str(self.loaded_hash)):
                    self.tree.selection_set(str(self.loaded_hash))
                    self.tree.focus(str(self.loaded_hash))
            finally:
                self._restoring_selection = False
            return
        self.loaded_hash = label_hash
        self.text.delete("1.0", "end")
        self.text.insert("1.0", str(self.entries[label_hash]["text"]))
        self.text.edit_modified(False)
        self.dirty = False

    def _text_modified(self, _event: object | None = None) -> None:
        if self.text.edit_modified():
            self.dirty = True
            if self.loaded_hash is not None:
                self.status.set(
                    f"Unsaved text for 0x{self.loaded_hash:08X} · Ctrl+S to apply",
                )
        self.text.edit_modified(False)

    def _confirm_unsaved(self) -> bool:
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel(
            "Unsaved GXT2 text",
            "Save the selected text before continuing?",
            parent=self,
        )
        if choice is None:
            return False
        if choice:
            return self._save()
        self.dirty = False
        return True

    def _save(self) -> bool:
        label_hash = self.loaded_hash
        if label_hash is None:
            messagebox.showinfo("Select an entry", "Select a text record first.", parent=self)
            return False
        try:
            Gxt2Workspace.set_text(
                self.workspace, label_hash, self.text.get("1.0", "end-1c"),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("GXT2 edit failed", str(exc), parent=self)
            return False
        self.dirty = False
        self._reload(label_hash)
        return True

    def _save_shortcut(self, _event: object | None = None) -> str:
        self._save()
        return "break"

    def _add(self) -> None:
        label = simpledialog.askstring(
            "Add GXT2 entry", "Unique decimal or 0x-prefixed label hash:", parent=self,
        )
        if label is None:
            return
        text = simpledialog.askstring("Add GXT2 entry", "UTF-8 game text:", parent=self)
        if text is None:
            return
        try:
            Gxt2Workspace.add(self.workspace, label, text)
            self.query.set("")
            self._reload()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not add GXT2 entry", str(exc), parent=self)

    def _remove(self) -> None:
        if not self._confirm_unsaved():
            return
        label_hash = self._selected_hash()
        if label_hash is None:
            return
        if not messagebox.askyesno(
            "Remove GXT2 entry",
            f"Remove 0x{label_hash:08X}? This remains undoable in the workspace.",
            parent=self,
        ):
            return
        try:
            Gxt2Workspace.remove(self.workspace, label_hash)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not remove GXT2 entry", str(exc), parent=self)
            return
        self.text.delete("1.0", "end")
        self.loaded_hash = None
        self.dirty = False
        self._reload()

    def _undo(self) -> None:
        if not self._confirm_unsaved():
            return
        try:
            Gxt2Workspace.undo(self.workspace)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not undo GXT2 edit", str(exc), parent=self)
            return
        self._reload()

    def _build(self) -> None:
        if not self._confirm_unsaved():
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Build verified GXT2 dictionary",
            initialfile="rebuilt.gxt2", defaultextension=".gxt2",
            filetypes=(("Rockstar GXT2", "*.gxt2"),),
        )
        if not output:
            return
        try:
            asset, report = Gxt2Workspace.build(self.workspace, output)
        except (OSError, ValueError) as exc:
            messagebox.showerror("GXT2 build failed", str(exc), parent=self)
            return
        self.status.set(f"Built and reparsed {asset}; validation: {report}")
        messagebox.showinfo(
            "Verified GXT2 built",
            f"The output was semantically reparsed.\n\nAsset: {asset}\nReport: {report}",
            parent=self,
        )


class Gxt2WorkspaceDialog(tk.Toplevel):
    """Compatibility host for opening a GXT2 workspace independently."""

    def __init__(self, parent: tk.Misc, workspace: str | Path) -> None:
        super().__init__(parent)
        self.title("ALLIN1 — GXT2 Text Workspace")
        place_window(
            self, preferred=(1180, 760), minimum=(860, 600),
        )
        self.transient(parent.winfo_toplevel())
        self.editor = Gxt2WorkspaceFrame(
            self, workspace, on_close=self.destroy,
        )


class RpfTransactionHistoryDialog(ttk.Frame):
    """Receipt history embedded inside the RPF workspace."""

    def __init__(
        self, parent: tk.Misc, service: RpfExplorerService,
        *, embedded: bool = False, on_close=None,
    ) -> None:
        self._window: tk.Toplevel | None = None
        self._on_close = on_close
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 — RPF Transaction History")
            place_window(
                self._window, preferred=(1180, 560), minimum=(880, 420),
            )
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.service = service
        self.records: dict[str, dict] = {}
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer, text="RPF transaction history",
            font=("Segoe UI Semibold", 16), foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Receipts preserve the complete rollback snapshot. Recovery only reconciles "
                "receipt state; it never completes an interrupted archive commit."
            ),
            foreground="#52635c", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(3, 10))
        tools = ttk.Frame(outer)
        tools.pack(fill="x", pady=(0, 8))
        for label, command in (
            ("Refresh", self._refresh), ("Verify", self._verify),
            ("Recover receipt", self._recover), ("Rollback", self._rollback),
            ("Clear stale lock", self._clear_lock), ("Open folder", self._open_folder),
        ):
            ttk.Button(tools, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(
            tools, text="Back to archive", command=self._close_panel,
        ).pack(side="right")
        frame = ttk.Frame(outer)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            frame, columns=("created", "action", "status", "archive", "entry"),
            show="headings", selectmode="browse",
        )
        widths = {"created": 165, "action": 75, "status": 165, "archive": 320, "entry": 310}
        for name in ("created", "action", "status", "archive", "entry"):
            self.tree.heading(name, text=name.title())
            self.tree.column(name, width=widths[name], minwidth=70)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.status = tk.StringVar()
        ttk.Label(outer, textvariable=self.status, foreground="#52635c").pack(
            fill="x", pady=(8, 0),
        )
        self._refresh()

    def _close_panel(self) -> None:
        if self._on_close is not None:
            self._on_close()
        elif self._window is not None:
            self._window.destroy()
        else:
            self.destroy()

    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        for number, record in enumerate(self.service.list_transactions()):
            item = f"receipt-{number}"
            self.records[item] = record
            self.tree.insert("", "end", iid=item, values=(
                str(record.get("created_at", ""))[:19].replace("T", " "),
                record.get("action", ""), record.get("status", ""),
                record.get("archive", ""), record.get("entry", ""),
            ))
        self.status.set(f"{len(self.records)} receipt(s) · {self._receipt_root()}")

    @staticmethod
    def _receipt_root() -> Path:
        return user_data_root() / "rpf-transactions"

    def _selected(self) -> dict | None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select a receipt", "Select a transaction first.", parent=self)
            return None
        return self.records[selected[0]]

    def _valid_selected(self) -> dict | None:
        record = self._selected()
        if record is not None and not record.get("valid"):
            messagebox.showerror(
                "Invalid receipt", record.get("error", "Malformed receipt"), parent=self,
            )
            return None
        return record

    def _verify(self) -> None:
        record = self._valid_selected()
        if record is None:
            return
        try:
            result = self.service.verify_transaction(record["receipt"])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Verification failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Transaction verification", json.dumps(result, indent=2), parent=self,
        )

    def _recover(self) -> None:
        record = self._valid_selected()
        if record is None:
            return
        try:
            result = self.service.recover_transaction(record["receipt"])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Recovery refused", str(exc), parent=self)
            return
        self._refresh()
        messagebox.showinfo(
            "Receipt recovered", json.dumps(result, indent=2), parent=self,
        )

    def _rollback(self) -> None:
        record = self._valid_selected()
        if record is None or not messagebox.askyesno(
            "Rollback transaction?",
            "The archive must still exactly match this receipt. The complete pre-change "
            "snapshot will be restored and verified.", parent=self, icon="warning",
        ):
            return
        RpfProgressDialog(
            self, "Rolling back RPF transaction",
            lambda progress: self.service.rollback_transaction(
                record["receipt"], progress=progress,
            ),
            lambda _result: (self._refresh(), messagebox.showinfo(
                "Rollback complete", "The original archive was restored and verified.",
                parent=self,
            )),
            lambda error: messagebox.showerror("Rollback failed", str(error), parent=self),
        )

    def _clear_lock(self) -> None:
        record = self._valid_selected()
        if record is None:
            return
        try:
            lock = self.service.inspect_archive_lock(record["archive"])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Lock inspection failed", str(exc), parent=self)
            return
        if lock is None:
            messagebox.showinfo("No lock", "This archive has no ALLIN1 lock.", parent=self)
            return
        if lock["process_running"]:
            messagebox.showwarning(
                "Lock is active", f"PID {lock['pid']} still owns this archive.", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Clear stale lock?",
            f"The recorded owner process is gone. Remove this lock?\n\n{lock['path']}",
            parent=self, icon="warning",
        ):
            return
        try:
            self.service.clear_stale_lock(record["archive"])
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Lock removal refused", str(exc), parent=self)
            return
        messagebox.showinfo("Lock cleared", "The stale archive lock was removed.", parent=self)

    def _open_folder(self) -> None:
        record = self._selected()
        if record is not None:
            os.startfile(Path(record["receipt"]).parent)


class RpfExplorerDialog(ttk.Frame):
    """Search and transact RPF changes inside the unified SDK shell."""

    def __init__(
        self, parent: tk.Misc, project_root: str | Path,
        installation_roots: tuple[Path, ...] = (), archive: str | Path | None = None,
        *, embedded: bool = False, on_help=None, on_close=None,
        on_open_asset=None, on_open_vehicle=None,
    ) -> None:
        self._window: tk.Toplevel | None = None
        self._on_help = on_help
        self._on_close = on_close
        self._on_open_asset = on_open_asset
        self._on_open_vehicle = on_open_vehicle
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 — RPF Archives")
            place_window(
                self._window, preferred=(1320, 840), minimum=(980, 650),
            )
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.project_root = Path(project_root).resolve()
        roots = [str(path.resolve()) for path in installation_roots if path.is_dir()]
        detected = detect_gta_path()
        if detected and str(detected.resolve()) not in roots:
            roots.append(str(detected.resolve()))
        self.game_paths = roots
        self.index: RpfIndex | None = None
        self.service: RpfExplorerService | None = None
        self.entry_items: dict[str, RpfEntryRecord] = {}
        self.catalog_items: dict[str, RpfCatalogResult] = {}
        self.entry_action_menus: list[tk.Menu] = []
        self.file_menus: list[tk.Menu] = []
        self._archive_bound_actions: list[tuple[tk.Menu, str]] = []
        self._graph_import_actions: list[tuple[tk.Menu, str]] = []
        self._entry_bound_actions: list[tuple[tk.Menu, str]] = []
        self._subtree_actions: list[tuple[tk.Menu, str]] = []
        self._native_authoring_actions: list[tuple[tk.Menu, str]] = []
        self._gxt2_authoring_actions: list[tuple[tk.Menu, str]] = []
        self._photo: ImageTk.PhotoImage | None = None
        self._preview_temp = tempfile.TemporaryDirectory(prefix="allin1-rpf-preview-")
        if self._window is not None:
            self._window.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        if archive:
            self._load_archive(Path(archive))

    def _build(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        file_menu = self._file_menu(menu)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self._close)
        menu.add_cascade(label="File", menu=file_menu)
        menu.add_cascade(label="Entry", menu=self._entry_menu(menu))
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="RPF Archives Help", accelerator="F1",
            command=self._show_help,
        )
        menu.add_cascade(label="Help", menu=help_menu)
        if self._window is not None:
            self._window.configure(menu=menu)
            self._window.bind("<F1>", lambda _event: self._show_help())

        outer = ttk.Frame(self, padding=14)
        self.main_surface = outer
        outer.pack(fill="both", expand=True)
        archive_title = ttk.Label(
            outer, text="RPF archives", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        )
        archive_title.pack(anchor="w")
        archive_description = ttk.Label(
            outer,
            text=(
                "Browse and safely author deep nested-RPF entries with edition-aware keys. "
                "Writes require a reviewed plan, a mods or isolated workspace copy, "
                "full-archive staging, exact-entry verification, and a rollback receipt."
            ),
            wraplength=900, justify="left", foreground="#52635c",
        )
        archive_description.pack(anchor="w", pady=(3, 10))

        target = ttk.Frame(outer)
        target.pack(fill="x", pady=(0, 8))
        ttk.Label(target, text="GTA V installation").pack(side="left")
        self.game_path = tk.StringVar(value=self.game_paths[0] if self.game_paths else "")
        self.game_combo = ttk.Combobox(
            target, textvariable=self.game_path, values=self.game_paths, width=62,
        )
        self.game_combo.pack(side="left", padx=(8, 6), fill="x", expand=True)
        ttk.Button(target, text="Browse…", command=self._choose_game).pack(side="left")

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(
            toolbar, text="Open RPF…", command=self._choose_archive,
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Menubutton(
            toolbar, text="Selected entry", menu=self._entry_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        ttk.Menubutton(
            toolbar, text="Archive tools", menu=self._file_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        ttk.Button(
            toolbar, text="Package graph",
            command=lambda: self.workspace_tabs.select(self.graph_tab),
        ).pack(side="left", padx=(7, 0))
        ttk.Button(
            toolbar, text="Transactions", command=self._transaction_history,
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(value="Select a GTA V installation and open an RPF.")
        status_label = ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=900, justify="left",
        )
        status_label.pack(fill="x", pady=(0, 8))
        self._browser_chrome = (
            (archive_title, {"anchor": "w"}),
            (archive_description, {"anchor": "w", "pady": (3, 10)}),
            (target, {"fill": "x", "pady": (0, 8)}),
            (toolbar, {"fill": "x", "pady": (0, 8)}),
            (status_label, {"fill": "x", "pady": (0, 8)}),
        )

        filter_row = ttk.Frame(outer)
        self.browser_filter_row = filter_row
        filter_row.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_row, text="Search").pack(side="left")
        self.query = tk.StringVar()
        ttk.Entry(filter_row, textvariable=self.query).pack(
            side="left", fill="x", expand=True, padx=(8, 12),
        )
        ttk.Label(filter_row, text="Type").pack(side="left")
        self.kind = tk.StringVar(value="All")
        ttk.Combobox(
            filter_row, textvariable=self.kind, state="readonly", width=13,
            values=("All", "resource", "binary", "archive", "directory"),
        ).pack(side="left", padx=(8, 12))
        ttk.Label(filter_row, text="Extension").pack(side="left")
        self.suffix = tk.StringVar(value="All")
        self.suffix_combo = ttk.Combobox(
            filter_row, textvariable=self.suffix, state="readonly", width=12,
            values=("All",),
        )
        self.suffix_combo.pack(side="left", padx=(8, 0))
        self.query.trace_add("write", lambda *_: self._populate())
        self.kind.trace_add("write", lambda *_: self._populate())
        self.suffix.trace_add("write", lambda *_: self._populate())

        self.workspace_tabs = ttk.Notebook(outer)
        self.workspace_tabs.pack(fill="both", expand=True)
        browser_tab = ttk.Frame(self.workspace_tabs)
        changes_tab = ttk.Frame(self.workspace_tabs)
        self.graph_tab = ttk.Frame(self.workspace_tabs)
        self.binary_tab = ttk.Frame(self.workspace_tabs)
        self.gxt2_tab = ttk.Frame(self.workspace_tabs)
        self.workspace_tabs.add(browser_tab, text="Archive Browser")
        self.workspace_tabs.add(changes_tab, text="Visual Change Set")
        self.workspace_tabs.add(self.graph_tab, text="Package Graph")
        self.workspace_tabs.add(self.binary_tab, text="Binary Workspace")
        self.workspace_tabs.add(self.gxt2_tab, text="GXT2 Text")
        self.browser_tab = browser_tab
        self.changes_tab = changes_tab

        panes = ttk.Panedwindow(browser_tab, orient="horizontal")
        panes.pack(fill="both", expand=True)
        browser = ttk.LabelFrame(panes, text="Archive tree", padding=8)
        preview = ttk.LabelFrame(panes, text="Entry inspector", padding=10)
        panes.add(browser, weight=5)
        panes.add(preview, weight=6)

        tree_frame = ttk.Frame(browser)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=("kind", "size", "version"),
            show="tree headings", selectmode="browse",
        )
        self.tree.heading("#0", text="Path")
        self.tree.heading("kind", text="Type")
        self.tree.heading("size", text="Logical size")
        self.tree.heading("version", text="Resource")
        self.tree.column("#0", width=440, minwidth=240)
        self.tree.column("kind", width=82, stretch=False)
        self.tree.column("size", width=92, anchor="e", stretch=False)
        self.tree.column("version", width=75, anchor="center", stretch=False)
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._select_entry)
        self.tree.bind("<Double-1>", self._activate_tree_item)
        self.tree.bind("<Return>", self._activate_tree_item)

        self.asset_title = tk.StringVar(value="Select an entry")
        self.asset_meta = tk.StringVar(value="No archive loaded")
        ttk.Label(
            preview, textvariable=self.asset_title,
            font=("Segoe UI Semibold", 13), foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            preview, textvariable=self.asset_meta, foreground="#52635c",
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(3, 10))
        ttk.Separator(preview).pack(fill="x", pady=(0, 8))
        context_actions = ttk.Frame(preview)
        context_actions.pack(fill="x", pady=(0, 8))
        for column in range(5):
            context_actions.columnconfigure(column, weight=1)
        self.preview_entry_button = ttk.Button(
            context_actions, text="Preview", command=self._preview_selected,
            state="disabled", padding=(5, 4),
        )
        self.preview_entry_button.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        self.extract_entry_button = ttk.Button(
            context_actions, text="Extract", command=self._extract_selected,
            state="disabled", padding=(5, 4),
        )
        self.extract_entry_button.grid(row=0, column=1, sticky="ew", padx=2)
        self.plan_entry_button = ttk.Button(
            context_actions, text="Plan", command=self._plan_replacement,
            state="disabled", padding=(5, 4),
        )
        self.plan_entry_button.grid(row=0, column=2, sticky="ew", padx=2)
        self.edit_gxt2_button = ttk.Button(
            context_actions, text="GXT2", command=self._export_gxt2_workspace,
            state="disabled", padding=(5, 4),
        )
        self.binary_workspace_button = ttk.Button(
            context_actions, text="Edit bytes", command=self._export_binary_workspace,
            state="disabled", padding=(5, 4),
        )
        self.binary_workspace_button.grid(row=0, column=3, sticky="ew", padx=2)
        self.edit_gxt2_button.grid(row=0, column=4, sticky="ew", padx=(2, 0))
        self.preview_surface = tk.Frame(preview, background="#ffffff")
        self.preview_surface.pack(fill="both", expand=True)
        self.image_preview = tk.Label(
            self.preview_surface, background="#ffffff", foreground="#52635c",
            anchor="center", text="Open an archive to inspect its contents.",
        )
        self.image_preview.pack(fill="both", expand=True)
        self.text_preview = tk.Text(
            self.preview_surface, wrap="none", relief="flat", borderwidth=0,
            background="#ffffff", foreground="#1e2925",
            font=("Cascadia Mono", 9), padx=10, pady=10, state="disabled",
        )
        self.change_set_frame = RpfChangeSetFrame(
            changes_tab,
            get_index=lambda: self.index,
            get_service=lambda: self.service,
            get_selected=self._selected,
            on_choose_target=self._choose_change_set_target,
        )
        self.change_set_frame.pack(fill="both", expand=True)
        self.graph_host = ttk.Frame(self.graph_tab)
        self.graph_host.pack(fill="both", expand=True)
        self.graph_host.pack_propagate(False)
        self.binary_host = ttk.Frame(self.binary_tab)
        self.binary_host.pack(fill="both", expand=True)
        self.binary_host.pack_propagate(False)
        self.gxt2_host = ttk.Frame(self.gxt2_tab)
        self.gxt2_host.pack(fill="both", expand=True)
        self.gxt2_host.pack_propagate(False)
        self._graph_editor: RpfPackageGraphFrame | None = None
        self._binary_editor: BinaryWorkspaceFrame | None = None
        self._gxt2_editor: Gxt2WorkspaceFrame | None = None
        self._show_graph_home()
        self._show_binary_home()
        self._show_gxt2_home()
        self.workspace_tabs.bind("<<NotebookTabChanged>>", self._workspace_tab_changed)

    def _workspace_tab_changed(self, _event: object | None = None) -> None:
        browser_selected = self.workspace_tabs.select() == str(self.browser_tab)
        for widget, options in self._browser_chrome:
            if browser_selected and not widget.winfo_manager():
                widget.pack(before=self.workspace_tabs, **options)
            elif not browser_selected and widget.winfo_manager():
                widget.pack_forget()
        if browser_selected:
            if not self.browser_filter_row.winfo_manager():
                self.browser_filter_row.pack(
                    fill="x", pady=(0, 8), before=self.workspace_tabs,
                )
        elif self.browser_filter_row.winfo_manager():
            self.browser_filter_row.pack_forget()
        if self.workspace_tabs.select() == str(self.changes_tab):
            self.change_set_frame.capture_target()

    def _choose_change_set_target(self) -> None:
        self.workspace_tabs.select(self.browser_tab)
        self.tree.focus_set()
        self.status.set(
            "Select an archive entry, then return to Visual Change Set to capture it.",
        )

    @staticmethod
    def _clear_workspace_host(host: ttk.Frame) -> None:
        for child in host.winfo_children():
            child.destroy()

    def _show_graph_home(self) -> None:
        self._clear_workspace_host(self.graph_host)
        self._graph_editor = None
        surface = ttk.Frame(self.graph_host, padding=20)
        surface.pack(fill="both", expand=True)
        ttk.Label(
            surface, text="Package graph", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            surface,
            text=(
                "Visually arrange files and nested archives, then validate sources, "
                "build a verified RPF, or create a reviewed change plan. The editor "
                "stays inside this workspace."
            ),
            foreground="#52635c", wraplength=980, justify="left",
        ).pack(anchor="w", pady=(4, 18))
        choices = ttk.LabelFrame(surface, text="Start or continue", padding=14)
        choices.pack(fill="x")
        for column in range(5):
            choices.columnconfigure(column, weight=1)
        for index, (label, command) in enumerate((
            ("Import mod package…", self._import_mod_package_graph),
            ("Create from loose folder…", self._create_rpf_graph_from_folder),
            ("Create empty graph…", self._create_empty_rpf_graph),
            ("Open existing graph…", self._open_rpf_graph),
        )):
            ttk.Button(choices, text=label, command=command).grid(
                row=0, column=index, sticky="ew",
                padx=(0 if index == 0 else 4, 4 if index < 3 else 0),
            )
        self.graph_import_button = ttk.Button(
            choices, text="Import currently open RPF…",
            command=self._import_open_rpf_graph,
            state="normal" if self.index is not None else "disabled",
        )
        self.graph_import_button.grid(
            row=0, column=4, sticky="ew", padx=(4, 0),
        )
        projects = PackageGraphWorkspace().list_projects()
        recent = ttk.LabelFrame(surface, text="Recent package projects", padding=10)
        recent.pack(fill="both", expand=True, pady=(14, 0))
        self.package_graph_projects: dict[str, Path] = {}
        tree = ttk.Treeview(
            recent, columns=("source", "nodes", "rpfs"), show="headings", height=6,
        )
        tree.heading("source", text="Source package")
        tree.heading("nodes", text="Nodes")
        tree.heading("rpfs", text="RPF state")
        tree.column("source", width=620, anchor="w")
        tree.column("nodes", width=80, anchor="center")
        tree.column("rpfs", width=190, anchor="w")
        tree.pack(fill="both", expand=True)
        for index, project in enumerate(projects):
            item = f"package-project:{index}"
            self.package_graph_projects[item] = Path(project["graph"])
            tree.insert("", "end", iid=item, values=(
                Path(project["source"]).name, project["nodes"],
                f"{project['expanded_rpfs']} expanded · "
                f"{project['sealed_rpfs']} sealed",
            ))
        if not projects:
            tree.insert("", "end", values=(
                "No retained package projects yet", "—", "Import a package above",
            ))
        tree.bind("<Double-1>", lambda _event: self._open_recent_package_graph(tree))
        tree.bind("<Return>", lambda _event: self._open_recent_package_graph(tree))

    def _show_binary_home(self) -> None:
        current = getattr(self, "_binary_editor", None)
        if current is not None and current.winfo_exists() and current.has_active_work():
            messagebox.showinfo(
                "Binary operation still running",
                "Wait for the current validation or build to finish before closing the editor.",
                parent=self,
            )
            return
        self._clear_workspace_host(self.binary_host)
        self._binary_editor = None
        surface = ttk.Frame(self.binary_host, padding=20)
        surface.pack(fill="both", expand=True)
        ttk.Label(
            surface, text="Binary workspace", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            surface,
            text=(
                "Inspect raw bytes, apply expected-byte patches, review the retained "
                "history, undo operations, build a changed-range report, and create an "
                "archive-bound replacement plan without leaving RPF Archives."
            ),
            foreground="#52635c", wraplength=920, justify="left",
        ).pack(anchor="w", pady=(4, 16))
        actions = ttk.Frame(surface)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Open binary workspace…",
            command=self._open_binary_workspace, style="Accent.TButton",
        ).pack(side="left")
        ttk.Label(
            actions,
            text=(
                "To start from an archive entry, return to Archive Browser and choose "
                "Edit bytes."
            ),
            foreground="#52635c", wraplength=650, justify="left",
        ).pack(side="left", padx=(12, 0))

    def _open_binary_workspace(self) -> None:
        current = getattr(self, "_binary_editor", None)
        if current is not None and current.winfo_exists() and current.has_active_work():
            messagebox.showinfo(
                "Binary operation still running",
                "Wait for the current validation or build to finish before opening "
                "another workspace.", parent=self,
            )
            return
        selected = filedialog.askdirectory(
            parent=self, title="Open a binary patch workspace",
        )
        if selected:
            self._open_binary_editor(selected)

    def _open_binary_editor(self, workspace: str | Path) -> None:
        current = getattr(self, "_binary_editor", None)
        if current is not None and current.winfo_exists() and current.has_active_work():
            messagebox.showinfo(
                "Binary operation still running",
                "Wait for the current validation or build to finish before replacing "
                "this editor.", parent=self,
            )
            return
        self._clear_workspace_host(self.binary_host)
        self._binary_editor = BinaryWorkspaceFrame(
            self.binary_host, workspace,
            on_close=self._show_binary_home,
            on_plan=self._plan_binary_workspace_from_editor,
        )
        self.workspace_tabs.select(self.binary_tab)

    def _show_gxt2_home(self) -> None:
        self._clear_workspace_host(self.gxt2_host)
        self._gxt2_editor = None
        surface = ttk.Frame(self.gxt2_host, padding=20)
        surface.pack(fill="both", expand=True)
        ttk.Label(
            surface, text="GXT2 text", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            surface,
            text=(
                "Open a recovery-backed GXT2 workspace, or select a .gxt2 entry in "
                "Archive Browser and choose Edit GXT2. Editing and validation stay in "
                "this tab."
            ),
            foreground="#52635c", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(4, 16))
        ttk.Button(
            surface, text="Open GXT2 workspace…", command=self._open_gxt2_workspace,
            style="Accent.TButton",
        ).pack(anchor="w")

    def _open_gxt2_editor(self, workspace: str | Path) -> None:
        self._clear_workspace_host(self.gxt2_host)
        self._gxt2_editor = Gxt2WorkspaceFrame(
            self.gxt2_host, workspace, on_close=self._show_gxt2_home,
        )
        self.workspace_tabs.select(self.gxt2_tab)

    def _file_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open RPF…", command=self._choose_archive)
        author_menu = tk.Menu(menu, tearoff=False)
        author_menu.add_command(
            label="Build new RPF from folder…", command=self._build_new_archive,
        )
        graph_menu = tk.Menu(author_menu, tearoff=False)
        graph_menu.add_command(
            label="Create from folder…", command=self._create_rpf_graph_from_folder,
        )
        graph_menu.add_command(
            label="Import mod package…", command=self._import_mod_package_graph,
        )
        graph_menu.add_command(
            label="Create empty graph…", command=self._create_empty_rpf_graph,
        )
        graph_menu.add_command(
            label="Import opened RPF…", command=self._import_open_rpf_graph,
            state="disabled",
        )
        self._graph_import_actions.append((graph_menu, "Import opened RPF…"))
        graph_menu.add_command(label="Open graph…", command=self._open_rpf_graph)
        author_menu.add_cascade(label="Package graph", menu=graph_menu)
        author_menu.add_command(
            label="Open binary patch workspace…", command=self._open_binary_workspace,
        )
        author_menu.add_command(
            label="Open GXT2 text workspace…", command=self._open_gxt2_workspace,
        )
        menu.add_cascade(label="Build & Author", menu=author_menu)

        inspect_menu = tk.Menu(menu, tearoff=False)
        for label, command in (
            ("Export index…", self._export_index),
            ("Extract current archive tree…", self._extract_current_archive),
            ("Compare with another archive…", self._compare_archive),
            ("Verify full archive integrity…", self._verify_archive_integrity),
            ("Build verified defragmented copy…", self._defragment_archive_copy),
        ):
            inspect_menu.add_command(label=label, command=command, state="disabled")
            self._archive_bound_actions.append((inspect_menu, label))
        menu.add_cascade(label="Inspect & Verify", menu=inspect_menu)

        catalog_menu = tk.Menu(menu, tearoff=False)
        catalog_menu.add_command(
            label="Build/update global RPF catalog…", command=self._build_catalog,
        )
        catalog_menu.add_command(
            label="Search global RPF catalog…", command=self._search_catalog,
        )
        menu.add_cascade(label="Catalog", menu=catalog_menu)

        plan_menu = tk.Menu(menu, tearoff=False)
        for label, command in (
            ("Derive plan from desired archive…", self._derive_change_plan),
            ("Create multi-entry plan…", self._plan_batch),
            ("Plan new directory…", self._plan_directory),
            ("Plan subtree workspace sync…", self._plan_subtree_sync),
        ):
            plan_menu.add_command(label=label, command=command, state="disabled")
            self._archive_bound_actions.append((plan_menu, label))
        menu.add_cascade(label="Plan Changes", menu=plan_menu)

        transaction_menu = tk.Menu(menu, tearoff=False)
        transaction_menu.add_command(
            label="Apply entry-change plan…", command=self._apply_replacement_plan,
        )
        transaction_menu.add_command(
            label="Verify transaction receipt…", command=self._verify_transaction,
        )
        transaction_menu.add_command(
            label="Rollback transaction…", command=self._rollback_transaction,
        )
        transaction_menu.add_command(
            label="Transaction history…", command=self._transaction_history,
        )
        transaction_menu.add_separator()
        transaction_menu.add_command(
            label="Run disposable archive canary…", command=self._run_canary,
            state="disabled",
        )
        self._archive_bound_actions.append(
            (transaction_menu, "Run disposable archive canary…")
        )
        menu.add_cascade(label="Transactions & Recovery", menu=transaction_menu)
        self.file_menus.append(menu)
        return menu

    def _entry_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        preview_menu = tk.Menu(menu, tearoff=False)
        for label, command in (
            ("Native preview", self._preview_selected),
            ("Raw hex preview", self._preview_selected_hex),
        ):
            preview_menu.add_command(label=label, command=command, state="disabled")
            self._entry_bound_actions.append((preview_menu, label))
        menu.add_cascade(label="Preview", menu=preview_menu)

        export_menu = tk.Menu(menu, tearoff=False)
        for label, command in (
            ("Extract selected…", self._extract_selected),
            ("Export binary patch workspace…", self._export_binary_workspace),
        ):
            export_menu.add_command(label=label, command=command, state="disabled")
            self._entry_bound_actions.append((export_menu, label))
        export_menu.add_command(
            label="Export editable native workspace…",
            command=self._export_native_workspace, state="disabled",
        )
        self._native_authoring_actions.append(
            (export_menu, "Export editable native workspace…")
        )
        export_menu.add_command(
            label="Export GXT2 text workspace…",
            command=self._export_gxt2_workspace, state="disabled",
        )
        self._gxt2_authoring_actions.append(
            (export_menu, "Export GXT2 text workspace…")
        )
        export_menu.add_command(
            label="Extract selected subtree…",
            command=self._extract_selected_subtree, state="disabled",
        )
        self._subtree_actions.append((export_menu, "Extract selected subtree…"))
        menu.add_cascade(label="Export Workspace", menu=export_menu)

        plan_menu = tk.Menu(menu, tearoff=False)
        for label, command in (
            ("Plan replacement…", self._plan_replacement),
            ("Plan replacement from binary workspace…", self._plan_binary_workspace_replacement),
            ("Plan new entry…", self._plan_addition),
            ("Plan deletion…", self._plan_deletion),
            ("Plan rename…", self._plan_rename),
        ):
            plan_menu.add_command(label=label, command=command, state="disabled")
            self._entry_bound_actions.append((plan_menu, label))
        plan_menu.add_command(
            label="Plan replacement from native workspace…",
            command=self._plan_native_workspace_replacement, state="disabled",
        )
        self._native_authoring_actions.append(
            (plan_menu, "Plan replacement from native workspace…")
        )
        plan_menu.add_command(
            label="Plan replacement from GXT2 workspace…",
            command=self._plan_gxt2_workspace_replacement, state="disabled",
        )
        self._gxt2_authoring_actions.append(
            (plan_menu, "Plan replacement from GXT2 workspace…")
        )
        menu.add_cascade(label="Plan Change", menu=plan_menu)
        self.entry_action_menus.append(menu)
        return menu

    def _set_archive_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu, label in self._archive_bound_actions:
            menu.entryconfigure(label, state=state)
        for menu, label in self._graph_import_actions:
            menu.entryconfigure(label, state=state)
        button = getattr(self, "graph_import_button", None)
        if button is not None and button.winfo_exists():
            button.configure(state=state)

    def _set_entry_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu, label in self._entry_bound_actions:
            menu.entryconfigure(label, state=state)
        for button_name in (
            "preview_entry_button", "extract_entry_button", "plan_entry_button",
            "binary_workspace_button",
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.configure(state=state)

    def _set_subtree_action(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu, label in self._subtree_actions:
            menu.entryconfigure(label, state=state)

    def _set_native_authoring_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu, label in self._native_authoring_actions:
            menu.entryconfigure(label, state=state)

    def _set_gxt2_authoring_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu, label in self._gxt2_authoring_actions:
            menu.entryconfigure(label, state=state)
        button = getattr(self, "edit_gxt2_button", None)
        if button is not None:
            button.configure(state=state)

    def _choose_game(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Select GTA V installation")
        if selected:
            self.game_path.set(selected)

    def _choose_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open Rockstar RPF archive",
            filetypes=(("Rockstar archive", "*.rpf"), ("All files", "*.*")),
        )
        if selected:
            self._load_archive(Path(selected))

    def _graph_game_path(self) -> Path | None:
        authored = self.game_path.get().strip()
        selected = Path(authored).resolve() if authored else None
        return selected if selected and selected.is_dir() else None

    def _open_graph_dialog(self, graph: str | Path) -> None:
        self._clear_workspace_host(self.graph_host)
        self._graph_editor = RpfPackageGraphFrame(
            self.graph_host, graph, self.project_root, self._graph_game_path(),
            on_close=self._show_graph_home,
            on_open_asset=self._on_open_asset,
            on_open_vehicle=self._on_open_vehicle,
        )
        self.workspace_tabs.select(self.graph_tab)

    def _open_recent_package_graph(self, tree: ttk.Treeview) -> None:
        selection = tree.selection()
        graph = self.package_graph_projects.get(selection[0]) if selection else None
        if graph is not None:
            self._open_graph_dialog(graph)

    def _import_mod_package_graph(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Import a mod package into the node graph",
            filetypes=(
                ("GTA mod package", "*.oiv *.zip *.rar *.7z"),
                ("All files", "*.*"),
            ),
        )
        if not selected:
            return

        def completed(project) -> None:
            self.status.set(
                f"Package graph {'reused' if project.reused else 'created'}: "
                f"{project.graph}"
            )
            self._open_graph_dialog(project.graph)

        RpfProgressDialog(
            self, "Importing mod package graph",
            lambda _progress: PackageGraphWorkspace().import_package(selected),
            completed,
            lambda exc: messagebox.showerror(
                "Package graph import failed", str(exc), parent=self,
            ),
        )

    def _create_rpf_graph_from_folder(self) -> None:
        source = filedialog.askdirectory(
            parent=self, title="Select loose RPF source folder",
        )
        if not source:
            return
        folder = Path(source)
        inferred = (
            folder.name[:-len(".source")]
            if folder.name.casefold().endswith(".rpf.source")
            else f"{folder.name}.rpf"
        )
        root_name = simpledialog.askstring(
            "Root archive node", "Root archive name:",
            initialvalue=inferred, parent=self,
        )
        if not root_name:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF package graph",
            initialfile=f"{Path(root_name).stem}-rpf-graph.json",
            defaultextension=".json", filetypes=(("RPF package graph", "*.json"),),
        )
        if not output:
            return
        try:
            graph = RpfPackageGraph.create_from_folder(
                source, output, root_name=root_name,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create RPF graph", str(exc), parent=self)
            return
        self.status.set(f"Created validated RPF package graph: {graph}")
        self._open_graph_dialog(graph)

    def _create_empty_rpf_graph(self) -> None:
        root_name = simpledialog.askstring(
            "New RPF package graph", "Root archive name ending in .rpf:",
            initialvalue="dlc.rpf", parent=self,
        )
        if not root_name:
            return
        if not root_name.casefold().endswith(".rpf"):
            root_name += ".rpf"
        output = filedialog.asksaveasfilename(
            parent=self, title="Save empty RPF package graph",
            initialfile=f"{Path(root_name).stem}-rpf-graph.json",
            defaultextension=".json", filetypes=(("RPF package graph", "*.json"),),
        )
        if not output:
            return
        try:
            graph = RpfPackageGraph.create_empty(root_name, output)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create RPF graph", str(exc), parent=self)
            return
        self.status.set(f"Created empty RPF package graph: {graph}")
        self._open_graph_dialog(graph)

    def _import_open_rpf_graph(self) -> None:
        if self.index is None or self.service is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for imported RPF graph workspace",
        )
        if not parent:
            return
        name = simpledialog.askstring(
            "Import existing RPF",
            "New workspace folder name:",
            initialvalue=f"{self.index.source.stem}-rpf-graph-workspace",
            parent=self,
        )
        if not name:
            return
        destination = Path(parent) / name
        index, service = self.index, self.service

        def completed(graph: Path) -> None:
            self.status.set(f"Imported existing RPF into external graph workspace: {graph}")
            self._open_graph_dialog(graph)

        RpfProgressDialog(
            self, "Importing RPF package graph",
            lambda _progress: RpfPackageGraph.import_archive(
                index, service, destination,
            ),
            completed,
            lambda exc: messagebox.showerror(
                "RPF graph import failed", str(exc), parent=self,
            ),
        )

    def _open_rpf_graph(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open RPF package node graph",
            filetypes=(("RPF package graph", "*.json"),),
        )
        if not selected:
            return
        try:
            RpfPackageGraph.validate(selected, verify_sources=False)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid RPF package graph", str(exc), parent=self)
            return
        self._open_graph_dialog(selected)

    def _open_gxt2_workspace(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Open a GXT2 text workspace",
        )
        if not selected:
            return
        try:
            Gxt2Workspace.validate(selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Invalid GXT2 workspace", str(exc), parent=self)
            return
        self._open_gxt2_editor(selected)

    def _build_new_archive(self) -> None:
        game = Path(self.game_path.get().strip())
        if not game.is_dir():
            messagebox.showerror(
                "GTA V path required",
                "Select the matching Legacy or Enhanced installation before building.",
                parent=self,
            )
            return
        selected = filedialog.askdirectory(
            parent=self, title="Select loose RPF source folder",
        )
        if not selected:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Create new verified RPF",
            defaultextension=".rpf",
            filetypes=(("Rockstar archive", "*.rpf"),),
        )
        if not output:
            return
        source = Path(selected)
        destination = Path(output)
        self.status.set("Building staged RPF and verifying every recursive payload…")

        def completed(result) -> None:
            archive, report = result
            self.status.set(f"New RPF built and exactly verified: {archive}")
            messagebox.showinfo(
                "RPF creation complete",
                "The recursive archive tree and every extracted payload passed exact "
                f"verification.\n\nArchive: {archive}\nReport: {report}",
                parent=self,
            )
            self._load_archive(archive)

        RpfProgressDialog(
            self, "Building new RPF archive",
            lambda _progress: RpfArchiveBuilder(
                self.project_root, game,
            ).build(source, destination),
            completed,
            lambda exc: (
                self.status.set("New RPF creation was refused or failed safely."),
                messagebox.showerror("RPF creation failed", str(exc), parent=self),
            ),
        )

    def _load_archive(self, archive: Path) -> None:
        game = Path(self.game_path.get().strip())
        if not game.is_dir():
            messagebox.showerror(
                "GTA V path required",
                "Select the matching Legacy or Enhanced installation so archive keys "
                "and resource versions are interpreted correctly.", parent=self,
            )
            return
        self.status.set("Indexing RPF and nested archives…")
        self.update_idletasks()
        try:
            service = RpfExplorerService(self.project_root, game)
            index = service.index(archive)
        except (OSError, ValueError) as exc:
            self.status.set("RPF could not be indexed.")
            messagebox.showerror("RPF indexing failed", str(exc), parent=self)
            return
        self.service = service
        self.index = index
        self.change_set_frame.archive_changed()
        suffixes = tuple(index.suffix_counts())
        self.suffix_combo.configure(values=("All",) + suffixes)
        self.suffix.set("All")
        self._set_archive_actions(True)
        self._populate()
        files = sum(entry.kind != "directory" for entry in index.entries)
        self.status.set(
            f"{index.source.name} · {index.edition} · {len(index.archives)} archive(s) · "
            f"{files:,} files · {len(index.warnings)} warnings"
        )
        self.asset_title.set(index.source.name)
        self.asset_meta.set(
            f"{_human_size(index.archive_size)} · "
            f"{', '.join(f'{ext} {count}' for ext, count in list(index.suffix_counts().items())[:8])}"
        )
        self._show_text("\n".join(index.warnings) or "Archive index loaded successfully.")

    def _filtered(self) -> tuple[RpfEntryRecord, ...]:
        if self.index is None:
            return ()
        kind = () if self.kind.get() == "All" else (self.kind.get(),)
        suffix = "" if self.suffix.get() == "All" else self.suffix.get()
        return self.index.search(self.query.get(), kinds=kind, suffix=suffix)

    def _populate(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.entry_items.clear()
        self.catalog_items.clear()
        self._set_entry_actions(False)
        self._set_subtree_action(False)
        self._set_native_authoring_actions(False)
        self._set_gxt2_authoring_actions(False)
        if self.index is None:
            return
        filtered = self._filtered()
        grouped: dict[str, list[RpfEntryRecord]] = {}
        for entry in filtered:
            grouped.setdefault(entry.archive_path, []).append(entry)
        counter = 0
        filtered_mode = bool(
            self.query.get().strip() or self.kind.get() != "All" or self.suffix.get() != "All"
        )
        archive_names = {record.path: record.name for record in self.index.archives}
        for archive_path in sorted(grouped, key=str.casefold):
            label = archive_names.get(archive_path, Path(archive_path).name)
            if archive_path:
                label = f"{label}  [{archive_path}]"
            root = self.tree.insert(
                "", "end", text=label, values=("RPF", f"{len(grouped[archive_path])} entries", ""),
                open=True,
            )
            parents: dict[str, str] = {"": root}
            for entry in sorted(grouped[archive_path], key=lambda item: item.path.casefold()):
                parts = PurePathParts(entry.path)
                parent_path = ""
                if not filtered_mode:
                    for part in parts[:-1]:
                        current = f"{parent_path}/{part}".strip("/")
                        if current not in parents:
                            parents[current] = self.tree.insert(
                                parents[parent_path], "end", text=part,
                                values=("folder", "", ""), open=False,
                            )
                        parent_path = current
                item_id = f"entry:{counter}"
                counter += 1
                self.entry_items[item_id] = entry
                display = entry.path if filtered_mode else parts[-1]
                self.tree.insert(
                    parents.get(parent_path, root), "end", iid=item_id, text=display,
                    values=(
                        entry.kind, _human_size(entry.size),
                        entry.resource_version if entry.resource_version is not None else "",
                    ),
                )

    def _selected(self) -> RpfEntryRecord | None:
        selected = self.tree.selection()
        return self.entry_items.get(selected[0]) if selected else None

    def _activate_tree_item(self, _event: object | None = None) -> None:
        selected = self.tree.selection()
        result = self.catalog_items.get(selected[0]) if selected else None
        if result is None:
            return
        archive = Path(result.outer_archive)
        self._load_archive(archive)
        for item_id, entry in self.entry_items.items():
            if (
                entry.archive_path.casefold() == result.archive_path.casefold()
                and entry.path.casefold() == result.entry_path.casefold()
            ):
                self.tree.selection_set(item_id)
                self.tree.focus(item_id)
                self.tree.see(item_id)
                self._select_entry()
                break

    def _catalog_game(self) -> Path | None:
        game = Path(self.game_path.get().strip())
        if game.is_dir():
            return game
        messagebox.showerror(
            "GTA V path required",
            "Select the matching Legacy or Enhanced installation for catalog indexing.",
            parent=self,
        )
        return None

    def _build_catalog(self) -> None:
        game = self._catalog_game()
        if game is None:
            return
        source = filedialog.askdirectory(
            parent=self, title="Select folder containing loose RPF archives",
        )
        if not source:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Create or update global RPF catalog",
            defaultextension=".sqlite",
            filetypes=(("RPF search catalog", "*.sqlite *.db"),),
        )
        if not destination:
            return
        self.status.set("Building incremental global RPF catalog…")

        def completed(result) -> None:
            database, summary = result
            self.status.set(
                f"RPF catalog ready: {summary['archives']} archives · "
                f"{summary['indexed']} indexed · {summary['cached']} cached · "
                f"{summary['failed']} failed"
            )
            messagebox.showinfo(
                "RPF catalog ready",
                f"Database: {database}\n\nIndexed: {summary['indexed']}\n"
                f"Reused cache: {summary['cached']}\nFailed: {summary['failed']}",
                parent=self,
            )

        RpfProgressDialog(
            self, "Cataloging loose RPF archives",
            lambda progress: RpfCatalogService(
                self.project_root, game,
            ).build(source, destination, progress=progress),
            completed,
            lambda exc: messagebox.showerror(
                "RPF catalog failed", str(exc), parent=self,
            ),
        )

    def _search_catalog(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Open global RPF catalog",
            filetypes=(("RPF search catalog", "*.sqlite *.db"),),
        )
        if not selected:
            return
        query = simpledialog.askstring(
            "Search all cataloged RPFs",
            "File, archive, or virtual-path text (blank lists the first entries):",
            parent=self,
        )
        if query is None:
            return
        try:
            results = RpfCatalogService.search(selected, query, limit=1000)
        except (OSError, ValueError) as exc:
            messagebox.showerror("RPF catalog search failed", str(exc), parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        self.entry_items.clear()
        self.catalog_items.clear()
        self._set_entry_actions(False)
        self._set_subtree_action(False)
        self._set_native_authoring_actions(False)
        self._set_gxt2_authoring_actions(False)
        grouped: dict[str, list[RpfCatalogResult]] = {}
        for result in results:
            grouped.setdefault(result.outer_archive, []).append(result)
        counter = 0
        for archive, items in grouped.items():
            root = self.tree.insert(
                "", "end", text=Path(archive).name,
                values=("RPF", f"{len(items)} matches", ""), open=True,
            )
            for result in items:
                item_id = f"catalog:{counter}"
                counter += 1
                self.catalog_items[item_id] = result
                label = (
                    f"{result.archive_path} :: {result.entry_path}"
                    if result.archive_path else result.entry_path
                )
                self.tree.insert(
                    root, "end", iid=item_id, text=label,
                    values=(
                        result.kind, _human_size(result.size),
                        result.resource_version if result.resource_version is not None else "",
                    ),
                )
        self.asset_title.set("Global RPF search")
        self.asset_meta.set(f"{len(results):,} result(s) for {query!r}")
        self._show_text(
            "Double-click a result to open its outer archive and select the exact "
            "root or nested entry. Catalog searching does not modify any archive."
        )
        self.status.set(f"Global RPF catalog search returned {len(results):,} result(s)")

    def _select_entry(self, _event: object | None = None) -> None:
        entry = self._selected()
        self._set_entry_actions(bool(entry and entry.kind != "directory"))
        self._set_subtree_action(bool(entry and entry.kind == "directory"))
        self._set_native_authoring_actions(bool(
            entry and entry.kind != "directory"
            and Path(entry.name).suffix.casefold() in NATIVE_XML_IMPORT_SUFFIXES
        ))
        self._set_gxt2_authoring_actions(bool(
            entry and entry.kind != "directory" and entry.suffix == ".gxt2"
        ))
        if entry is None:
            return
        self.asset_title.set(entry.name)
        flags = []
        if entry.encrypted is not None:
            flags.append("encrypted" if entry.encrypted else "not entry-encrypted")
        if entry.compressed is not None:
            flags.append("compressed" if entry.compressed else "stored")
        self.asset_meta.set(
            f"{entry.virtual_name} · {entry.kind} · {_human_size(entry.size)} logical · "
            f"{_human_size(entry.stored_size)} stored"
        )
        details = {
            "Archive": entry.archive_path or "root",
            "Path": entry.path, "Type": entry.kind,
            "Logical size": f"{entry.size:,}", "Stored size": f"{entry.stored_size:,}",
            "Offset": entry.offset, "Resource version": entry.resource_version,
            "System size": entry.system_size, "Graphics size": entry.graphics_size,
            "System flags": entry.system_flags, "Graphics flags": entry.graphics_flags,
            "Name hash": f"0x{entry.name_hash:08X}",
            "Short-name hash": f"0x{entry.short_name_hash:08X}",
            "Storage": ", ".join(flags) or "directory metadata",
        }
        self._show_text("\n".join(
            f"{key}: {value}" for key, value in details.items() if value is not None
        ))

    def _preview_selected(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        if entry.size > MAX_NATIVE_PREVIEW_BYTES:
            messagebox.showwarning(
                "Asset too large for interactive preview",
                f"This asset is {_human_size(entry.size)}. Extract it instead; deep previews "
                f"are capped at {_human_size(MAX_NATIVE_PREVIEW_BYTES)}.", parent=self,
            )
            return
        destination = Path(self._preview_temp.name) / (
            f"preview-{len(list(Path(self._preview_temp.name).iterdir()))}-{entry.name}"
        )
        self.status.set(f"Extracting read-only preview: {entry.name}…")
        self.update_idletasks()
        try:
            extracted = self.service.extract(self.index, entry, destination)
            data = extracted.read_bytes()
            report = NativeAssetInspector(
                self.project_root, self.service.gta_path,
            ).inspect_bytes(
                entry.name, data, edition=self.index.edition,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Native preview failed", str(exc), parent=self)
            return
        self.asset_meta.set(report.summary().replace("\n", " · "))
        if report.image_png:
            self._show_image(report.image_png, report.format_name)
        else:
            body = report.summary()
            if report.structured_text:
                body += "\n\nStructured CodeWalker preview\n\n" + report.structured_text[:2_000_000]
            self._show_text(body)
        self.status.set(f"Previewed {entry.virtual_name} without modifying the archive")

    def _preview_selected_hex(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        length = min(entry.size, 16 * 1024)
        if length <= 0:
            messagebox.showwarning("Empty entry", "This entry has no bytes to preview.", parent=self)
            return
        destination = Path(self._preview_temp.name) / (
            f"hex-{len(list(Path(self._preview_temp.name).iterdir()))}-{entry.name}"
        )
        try:
            data = self.service.extract(self.index, entry, destination).read_bytes()[:length]
        except (OSError, ValueError) as exc:
            messagebox.showerror("Raw hex preview failed", str(exc), parent=self)
            return
        lines = []
        for offset in range(0, len(data), 16):
            block = data[offset:offset + 16]
            hexadecimal = " ".join(f"{value:02X}" for value in block)
            printable = "".join(
                chr(value) if 32 <= value < 127 else "." for value in block
            )
            lines.append(f"{offset:08X}  {hexadecimal:<47}  |{printable:<16}|")
        if entry.size > length:
            lines.extend([
                "", f"Preview capped at {_human_size(length)} of {_human_size(entry.size)}.",
                "Export a bound binary patch workspace for offset inspection and editing.",
            ])
        self.asset_meta.set(
            f"Raw bytes · {entry.virtual_name} · {_human_size(entry.size)} · read-only"
        )
        self._show_text("\n".join(lines))
        self.status.set(f"Previewed raw bytes from {entry.virtual_name}")

    def _export_binary_workspace(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        selected = filedialog.askdirectory(
            parent=self, title="Select a new binary patch workspace folder",
            mustexist=False,
        )
        if not selected:
            return
        try:
            workspace = self.service.export_binary_workspace(
                self.index, entry, selected,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Binary workspace export failed", str(exc), parent=self)
            return
        self.status.set(f"Bound binary patch workspace exported: {workspace}")
        self._open_binary_editor(workspace)

    def _export_gxt2_workspace(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        selected = filedialog.askdirectory(
            parent=self, title="Select a new GXT2 text workspace folder",
            mustexist=False,
        )
        if not selected:
            return
        try:
            workspace = self.service.export_gxt2_workspace(
                self.index, entry, selected,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("GXT2 workspace export failed", str(exc), parent=self)
            return
        self.status.set(f"Bound GXT2 text workspace exported: {workspace}")
        self._open_gxt2_editor(workspace)

    def _extract_selected(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        selected = filedialog.asksaveasfilename(
            parent=self, title="Extract RPF entry", initialfile=entry.name,
        )
        if not selected:
            return
        try:
            output = self.service.extract(self.index, entry, selected)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Extraction failed", str(exc), parent=self)
            return
        self.status.set(f"Extracted read-only copy: {output}")

    def _export_native_workspace(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for editable native workspace",
        )
        if not parent:
            return
        destination = Path(parent) / f"{entry.name}-workspace"

        def work(progress):
            progress(f"Extracting {entry.virtual_name}…", 20)
            result = self.service.export_native_workspace(
                self.index, entry, destination,
            )
            progress("CodeWalker XML workspace verified", 100)
            return result

        def completed(workspace):
            self.status.set(f"Exported editable native workspace: {workspace}")
            messagebox.showinfo(
                "Native workspace exported",
                "The source snapshot is immutable. Edit only the XML/dependencies under "
                f"the edit folder.\n\n{workspace}", parent=self,
            )

        RpfProgressDialog(
            self, "Exporting native RPF workspace", work, completed,
            lambda error: messagebox.showerror(
                "Native workspace export failed", str(error), parent=self,
            ),
        )

    def _extract_current_archive(self) -> None:
        if self.index is None:
            return
        self._extract_subtree(
            archive_path="", directory_path="",
            suggested_name=f"{self.index.source.stem}-rpf-export",
        )

    def _compare_archive(self) -> None:
        if self.index is None or self.service is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Select RPF archive to compare",
            filetypes=(("Rockstar archive", "*.rpf"), ("All files", "*.*")),
        )
        if not selected:
            return
        mode = simpledialog.askstring(
            "RPF comparison mode",
            "Choose metadata, logical, or exact.\n\n"
            "Logical compares canonical RSC7 headers and decompressed content, so "
            "harmless resource recompression is not reported as a change. Exact "
            "compares every extracted byte.",
            initialvalue="logical", parent=self,
        )
        if mode is None:
            return
        mode = mode.strip().casefold()
        if mode not in {"metadata", "logical", "exact"}:
            messagebox.showerror(
                "Invalid comparison mode",
                "Enter metadata, logical, or exact.", parent=self,
            )
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF comparison reports",
            initialfile=(
                f"{self.index.source.stem}-vs-{Path(selected).stem}-rpf-diff.json"
            ),
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        self.status.set("Indexing and comparing recursive RPF trees…")
        self.update_idletasks()
        try:
            other = self.service.index(selected)
            report = self.service.compare_indexes(
                self.index, other, exact_content=mode == "exact",
                logical_content=mode == "logical",
            )
            json_path, markdown_path = self.service.export_diff(report, output)
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set("RPF comparison failed.")
            messagebox.showerror("RPF comparison failed", str(exc), parent=self)
            return
        summary = report["summary"]
        self._show_text(markdown_path.read_text(encoding="utf-8"))
        self.status.set(
            f"RPF diff: {summary['added']} added · {summary['removed']} removed · "
            f"{summary['modified']} modified · {json_path}"
        )

    def _derive_change_plan(self) -> None:
        if self.index is None or self.service is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Select desired finished RPF archive",
            filetypes=(("Rockstar archive", "*.rpf"), ("All files", "*.*")),
        )
        if not selected:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save portable RPF change plan",
            initialfile=(
                f"{self.index.source.stem}-to-{Path(selected).stem}-change-plan.json"
            ),
            defaultextension=".json", filetypes=(("RPF change plan", "*.json"),),
        )
        if not output:
            return
        base, service = self.index, self.service
        self.status.set("Deriving reviewed deep-entry changes from desired RPF…")

        def work(progress) -> RpfDeltaPlanResult:
            progress("Indexing desired recursive RPF tree", 3)
            desired = service.index(selected)
            return derive_rpf_change_plan(
                service, base, desired, output, progress=progress,
            )

        def completed(result: RpfDeltaPlanResult) -> None:
            plan = result.plan
            self.status.set(
                f"Derived {plan['status']} plan · {len(plan['changes']):,} action(s) · "
                f"{result.plan_path}"
            )
            detail = (
                "The base and desired archives were left untouched. Only changed "
                "payloads were copied beside the hash-bound plan."
            )
            if plan["blocking_reasons"]:
                detail += "\n\nApply remains blocked:\n• " + "\n• ".join(
                    plan["blocking_reasons"]
                )
            messagebox.showinfo(
                "RPF change plan ready",
                f"{detail}\n\nPlan: {result.plan_path}\n"
                f"Payloads: {result.payload_directory or 'none'}",
                parent=self,
            )

        RpfProgressDialog(
            self, "Deriving RPF change plan", work, completed,
            lambda error: (
                self.status.set("RPF delta planning was refused or failed safely."),
                messagebox.showerror(
                    "RPF change plan failed", str(error), parent=self,
                ),
            ),
        )

    def _verify_archive_integrity(self) -> None:
        if self.index is None or self.service is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save full RPF integrity report",
            initialfile=f"{self.index.source.stem}-integrity.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        self.status.set("Extracting and hashing every recursive RPF payload…")

        def completed(result) -> None:
            report_path, report = result
            summary = report["summary"]
            self.status.set(
                f"RPF integrity {report['status']} · "
                f"{summary['payloads_exactly_extracted']} exact payloads · "
                f"{summary['structural_issues']} structural issues"
            )
            self._show_text(json.dumps(report, indent=2))
            messagebox.showinfo(
                "RPF integrity verification complete",
                f"Status: {report['status']}\n"
                f"Exact payloads: {summary['payloads_exactly_extracted']}\n"
                f"Structural issues: {summary['structural_issues']}\n\n"
                f"Report: {report_path}",
                parent=self,
            )

        RpfProgressDialog(
            self, "Verifying complete RPF integrity",
            lambda _progress: self.service.verify_archive_integrity(
                self.index, output,
            ),
            completed,
            lambda exc: messagebox.showerror(
                "RPF integrity verification failed", str(exc), parent=self,
            ),
        )

    def _defragment_archive_copy(self) -> None:
        if self.index is None or self.service is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Build external verified defragmented RPF copy",
            initialfile=f"{self.index.source.stem}-defragmented.rpf",
            defaultextension=".rpf",
            filetypes=(("Rockstar archive", "*.rpf"),),
        )
        if not output:
            return
        destination = Path(output).resolve()
        report = destination.with_name(f"{destination.name}.defragment.json")
        self.status.set("Compacting an external copy and verifying every recursive leaf…")

        def completed(result) -> None:
            archive, report_path, evidence = result
            summary = evidence["summary"]
            self.status.set(
                f"Verified defragmented copy · {summary['bytes_saved']:,} bytes saved · "
                f"{summary['leaf_payloads_verified']:,} exact leaves"
            )
            self._show_text(json.dumps(evidence, indent=2))
            messagebox.showinfo(
                "Verified RPF copy complete",
                f"Archive: {archive}\nReport: {report_path}\n\n"
                f"Saved: {summary['bytes_saved']:,} bytes\n"
                f"Exact leaf payloads: {summary['leaf_payloads_verified']:,}\n\n"
                "The opened source archive was not changed.",
                parent=self,
            )

        RpfProgressDialog(
            self, "Building verified defragmented RPF copy",
            lambda _progress: self.service.defragment_verified_copy(
                self.index, destination, report,
            ),
            completed,
            lambda exc: (
                self.status.set("RPF defragmentation was refused or failed safely."),
                messagebox.showerror(
                    "RPF defragmentation failed", str(exc), parent=self,
                ),
            ),
        )

    def _extract_selected_subtree(self) -> None:
        entry = self._selected()
        if entry is None or entry.kind != "directory":
            return
        self._extract_subtree(
            archive_path=entry.archive_path, directory_path=entry.path,
            suggested_name=f"{Path(entry.path).name}-rpf-export",
        )

    def _extract_subtree(
        self, *, archive_path: str, directory_path: str, suggested_name: str,
    ) -> None:
        if self.index is None or self.service is None:
            return
        selected = filedialog.askdirectory(
            parent=self, title="Select parent folder for the RPF subtree export",
        )
        if not selected:
            return
        destination = Path(selected) / suggested_name
        self.status.set("Extracting and hashing RPF subtree…")
        self.update_idletasks()
        try:
            output = self.service.extract_subtree(
                self.index, destination, archive_path=archive_path,
                directory_path=directory_path,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set("RPF subtree extraction failed.")
            messagebox.showerror("Subtree extraction failed", str(exc), parent=self)
            return
        self.status.set(
            f"Extracted verified read-only subtree: {output}"
        )

    def _plan_replacement(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        payload = filedialog.askopenfilename(
            parent=self, title=f"Select replacement payload for {entry.name}",
        )
        if not payload:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF replacement safety plan",
            initialfile=f"{Path(entry.name).stem}-replacement-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = self.service.replacement_plan(self.index, entry, payload)
            Path(output).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create plan", str(exc), parent=self)
            return
        self.status.set(
            f"Wrote {plan['status']} plan; no RPF changes were made: {output}"
        )
        if plan["status"] == "blocked":
            messagebox.showwarning(
                "Replacement plan is blocked",
                "No archive was changed. Resolve these items and create a new plan:\n\n"
                + "\n".join(f"• {item}" for item in plan["blocking_reasons"]),
                parent=self,
            )

    def _plan_native_workspace_replacement(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        workspace = filedialog.askdirectory(
            parent=self, title=f"Select edited native workspace for {entry.name}",
        )
        if not workspace:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save rebuilt-native RPF replacement plan",
            initialfile=f"{Path(entry.name).stem}-native-replacement-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return

        def work(progress):
            progress("Rebuilding edited CodeWalker XML…", 20)
            result = self.service.plan_native_workspace_replacement(
                self.index, entry, workspace, output,
            )
            progress("Reparsed payload and bound reviewed RPF plan", 100)
            return result

        def completed(result):
            plan, asset, report = result
            self.status.set(f"Wrote rebuilt-native replacement plan: {plan}")
            messagebox.showinfo(
                "Native replacement plan ready",
                "The rebuilt asset parsed successfully and is bound to the plan. The RPF "
                "has not changed; review and apply the plan separately.\n\n"
                f"Payload: {asset}\nValidation: {report}\nPlan: {plan}", parent=self,
            )

        RpfProgressDialog(
            self, "Building native RPF replacement", work, completed,
            lambda error: messagebox.showerror(
                "Native replacement plan failed", str(error), parent=self,
            ),
        )

    def _plan_binary_workspace_replacement(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        workspace = filedialog.askdirectory(
            parent=self, title=f"Select patched binary workspace for {entry.name}",
        )
        if not workspace:
            return
        self._create_binary_workspace_plan(entry, workspace)

    def _plan_binary_workspace_from_editor(self, workspace: str | Path) -> None:
        root = Path(workspace).expanduser().resolve()
        try:
            manifest = json.loads(
                (root / "binary-workspace.json").read_text(encoding="utf-8")
            )
            binding = manifest.get("source_binding", {})
            if not isinstance(binding, dict):
                raise ValueError("Binary workspace has no valid RPF source binding")
            archive_text = str(binding.get("outer_archive", "")).strip()
            entry_id = str(binding.get("entry_id", "")).strip()
            if not archive_text or not entry_id:
                raise ValueError(
                    "This binary workspace is not bound to an RPF archive entry"
                )
            archive = Path(archive_text).expanduser().resolve()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Binary workspace binding failed", str(exc), parent=self)
            return
        if self.index is None or self.index.source != archive:
            if not archive.is_file():
                messagebox.showerror(
                    "Bound RPF unavailable",
                    f"The archive used to create this workspace was not found:\n{archive}",
                    parent=self,
                )
                return
            self._load_archive(archive)
        if self.index is None or self.service is None or self.index.source != archive:
            return
        entry = self.index.entry(entry_id)
        if entry is None or entry.kind == "directory":
            messagebox.showerror(
                "Bound entry unavailable",
                "The exact archive entry recorded by this workspace is no longer present.",
                parent=self,
            )
            return
        self._create_binary_workspace_plan(entry, root)

    def _create_binary_workspace_plan(
        self, entry: RpfEntryRecord, workspace: str | Path,
    ) -> None:
        if self.index is None or self.service is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save binary RPF replacement plan",
            initialfile=f"{Path(entry.name).stem}-binary-replacement-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return

        def work(progress):
            progress("Validating binary workspace history and source binding…", 20)
            result = self.service.plan_binary_workspace_replacement(
                self.index, entry, str(workspace), output,
            )
            progress("Built same-size diff and bound reviewed RPF plan", 100)
            return result

        def completed(result):
            plan, asset, report = result
            self.status.set(f"Wrote binary replacement plan: {plan}")
            messagebox.showinfo(
                "Binary replacement plan ready",
                "The same-size output, changed byte ranges, history chain, archive hash, "
                "and exact entry identity are bound to this plan. The RPF has not "
                f"changed.\n\nPayload: {asset}\nDiff: {report}\nPlan: {plan}",
                parent=self,
            )

        RpfProgressDialog(
            self, "Building binary RPF replacement", work, completed,
            lambda error: messagebox.showerror(
                "Binary replacement plan failed", str(error), parent=self,
            ),
        )

    def _plan_gxt2_workspace_replacement(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        workspace = filedialog.askdirectory(
            parent=self, title=f"Select edited GXT2 workspace for {entry.name}",
        )
        if not workspace:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save GXT2 RPF replacement plan",
            initialfile=f"{Path(entry.name).stem}-gxt2-replacement-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return

        def work(progress):
            progress("Validating GXT2 table and exact source binding…", 20)
            result = self.service.plan_gxt2_workspace_replacement(
                self.index, entry, workspace, output,
            )
            progress("Rebuilt, reparsed, and bound reviewed RPF plan", 100)
            return result

        def completed(result):
            plan, asset, report = result
            self.status.set(f"Wrote GXT2 replacement plan: {plan}")
            messagebox.showinfo(
                "GXT2 replacement plan ready",
                "The rebuilt dictionary parsed successfully and remains bound to the "
                "original archive hash and exact virtual entry. The RPF has not changed; "
                "review and apply the plan separately.\n\n"
                f"Payload: {asset}\nValidation: {report}\nPlan: {plan}",
                parent=self,
            )

        RpfProgressDialog(
            self, "Building GXT2 RPF replacement", work, completed,
            lambda error: messagebox.showerror(
                "GXT2 replacement plan failed", str(error), parent=self,
            ),
        )

    def _plan_addition(self) -> None:
        if self.index is None or self.service is None:
            return
        selected_entry = self._selected()
        default_archive = selected_entry.archive_path if selected_entry else ""
        archive_path = simpledialog.askstring(
            "Nested archive",
            "Nested RPF virtual path (use ! between levels; blank means root):",
            initialvalue=default_archive, parent=self,
        )
        if archive_path is None:
            return
        entry_path = simpledialog.askstring(
            "New RPF entry",
            "Exact new virtual path, including its filename and extension:", parent=self,
        )
        if not entry_path:
            return
        payload = filedialog.askopenfilename(
            parent=self, title=f"Select payload for {entry_path}",
        )
        if not payload:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF add safety plan",
            initialfile=f"{Path(entry_path).stem}-add-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = self.service.addition_plan(
                self.index, entry_path, payload, archive_path=archive_path,
            )
            Path(output).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create add plan", str(exc), parent=self)
            return
        self._report_plan(plan, Path(output))

    def _plan_deletion(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF delete safety plan",
            initialfile=f"{Path(entry.name).stem}-delete-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = (
                self.service.multi_change_plan(self.index, [{
                    "action": "rmdir" if entry.kind == "directory" else "delete",
                    "archive_path": entry.archive_path, "entry": entry.path,
                }])
                if entry.kind in {"directory", "archive"}
                else self.service.deletion_plan(self.index, entry)
            )
            Path(output).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create delete plan", str(exc), parent=self)
            return
        if plan.get("operation") == "rpf_multi_entry_change":
            self.status.set(
                f"Wrote {plan['status']} atomic delete plan; no RPF changes were made: "
                f"{output}"
            )
        else:
            self._report_plan(plan, Path(output))

    def _plan_directory(self) -> None:
        if self.index is None or self.service is None:
            return
        selected = self._selected()
        archive_path = simpledialog.askstring(
            "Nested archive",
            "Nested RPF virtual path (use ! between levels; blank means root):",
            initialvalue=selected.archive_path if selected else "", parent=self,
        )
        if archive_path is None:
            return
        entry_path = simpledialog.askstring(
            "New RPF directory", "Exact new virtual directory path:", parent=self,
        )
        if not entry_path:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF directory safety plan",
            initialfile=f"{Path(entry_path).name}-mkdir-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = self.service.multi_change_plan(self.index, [{
                "action": "mkdir", "archive_path": archive_path,
                "entry": entry_path,
            }])
            Path(output).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create directory plan", str(exc), parent=self)
            return
        self.status.set(
            f"Wrote {plan['status']} directory plan; no RPF changes were made: {output}"
        )

    def _plan_rename(self) -> None:
        entry = self._selected()
        if entry is None or self.index is None or self.service is None:
            return
        if entry.kind == "archive":
            messagebox.showinfo(
                "Archive rename unavailable",
                "Nested RPF containers cannot be renamed because their indexed archive "
                "identity would also change. Extract and review that migration separately.",
                parent=self,
            )
            return
        parent = str(Path(entry.path).parent).replace("\\", "/")
        if parent == ".":
            parent = ""
        authored = simpledialog.askstring(
            "Rename RPF entry",
            "New exact path in the same parent directory:",
            initialvalue=f"{parent + '/' if parent else ''}{entry.name}", parent=self,
        )
        if not authored:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save RPF rename safety plan",
            initialfile=f"{Path(entry.name).stem}-rename-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = self.service.multi_change_plan(self.index, [{
                "action": "rename", "archive_path": entry.archive_path,
                "entry": entry.path, "new_entry": authored,
            }])
            Path(output).write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create rename plan", str(exc), parent=self)
            return
        self.status.set(
            f"Wrote {plan['status']} rename plan; no RPF changes were made: {output}"
        )

    def _report_plan(self, plan: dict, output: Path) -> None:
        self.status.set(
            f"Wrote {plan['status']} {plan['action']} plan; no RPF changes were made: {output}"
        )
        if plan["status"] == "blocked":
            messagebox.showwarning(
                "Entry-change plan is blocked",
                "No archive was changed. Resolve these items and create a new plan:\n\n"
                + "\n".join(f"• {item}" for item in plan["blocking_reasons"]),
                parent=self,
            )

    def _plan_batch(self) -> None:
        if self.index is None or self.service is None:
            return
        manifest = filedialog.askopenfilename(
            parent=self, title="Open RPF multi-entry change manifest",
            filetypes=(("JSON", "*.json"), ("All files", "*.*")),
        )
        if not manifest:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save atomic RPF multi-entry plan",
            initialfile=f"{self.index.source.stem}-multi-entry-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            authored = json.loads(Path(manifest).read_text(encoding="utf-8"))
            changes = authored.get("changes") if isinstance(authored, dict) else authored
            if not isinstance(changes, list):
                raise ValueError(
                    "Batch manifest must be a list or contain a changes list"
                )
            resolved = []
            for item in changes:
                if not isinstance(item, dict):
                    raise ValueError("Every batch change must be a JSON object")
                normalized = dict(item)
                if normalized.get("payload"):
                    payload = Path(str(normalized["payload"])).expanduser()
                    if not payload.is_absolute():
                        payload = Path(manifest).resolve().parent / payload
                    normalized["payload"] = str(payload.resolve())
                resolved.append(normalized)
            plan = self.service.multi_change_plan(self.index, resolved)
            Path(output).write_text(
                json.dumps(plan, indent=2) + "\n", encoding="utf-8",
            )
        except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not create batch plan", str(exc), parent=self)
            return
        self.status.set(
            f"Wrote {plan['status']} atomic plan for {len(plan['changes'])} changes: "
            f"{output}"
        )
        if plan["status"] == "blocked":
            messagebox.showwarning(
                "Multi-entry plan is blocked",
                "No archive was changed. Resolve these items and create a new plan:\n\n"
                + "\n".join(f"• {item}" for item in plan["blocking_reasons"]),
                parent=self,
            )

    def _plan_subtree_sync(self) -> None:
        if self.index is None or self.service is None:
            return
        export_directory = filedialog.askdirectory(
            parent=self, title="Select verified RPF subtree workspace",
        )
        if not export_directory:
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save atomic subtree sync plan",
            initialfile=f"{Path(export_directory).name}-sync-plan.json",
            defaultextension=".json", filetypes=(("JSON", "*.json"),),
        )
        if not output:
            return
        try:
            plan = self.service.subtree_sync_plan(
                self.index, export_directory,
            )
            Path(output).write_text(
                json.dumps(plan, indent=2) + "\n", encoding="utf-8",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Could not create sync plan", str(exc), parent=self,
            )
            return
        self.status.set(
            f"Wrote {plan['status']} atomic subtree sync plan for "
            f"{len(plan['changes'])} changes: {output}"
        )
        if plan["status"] == "blocked":
            messagebox.showwarning(
                "Subtree sync plan is blocked",
                "No archive was changed. Resolve these items and create a new plan:\n\n"
                + "\n".join(f"• {item}" for item in plan["blocking_reasons"]),
                parent=self,
            )

    def _transaction_service(self) -> RpfExplorerService | None:
        game = Path(self.game_path.get().strip())
        if not game.is_dir():
            messagebox.showerror(
                "GTA V path required",
                "Select the installation that owns the mods copy and its encryption keys.",
                parent=self,
            )
            return None
        return RpfExplorerService(self.project_root, game)

    def _apply_replacement_plan(self) -> None:
        service = self._transaction_service()
        if service is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Open reviewed RPF entry-change plan",
            filetypes=(("RPF entry-change plan", "*.json"),),
        )
        if not selected:
            return
        try:
            summary = json.loads(Path(selected).read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise ValueError("Entry-change plan must contain a JSON object")
            archive = str(summary.get("archive", "unknown archive"))
            entry = str(summary.get("entry", "unknown entry"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Invalid replacement plan", str(exc), parent=self)
            return
        is_batch = summary.get("operation") == "rpf_multi_entry_change"
        change_count = len(summary.get("changes", ())) if is_batch else 1
        target_summary = (
            f"Entries: {change_count} reviewed changes"
            if is_batch else (
                f"Entry: {summary.get('archive_path') or 'root'}::{entry}"
            )
        )
        if not messagebox.askyesno(
            "Apply guarded RPF transaction?",
            "GTA V must be closed. ALLIN1 will copy the complete archive, modify a "
            "staged copy, verify every reviewed entry, and retain one rollback "
            "receipt.\n\n"
            f"Action: {'atomic batch' if is_batch else summary.get('action', 'unknown')}\n"
            f"Archive: {archive}\n"
            f"{target_summary}\n\nContinue?",
            parent=self, icon="warning",
        ):
            return
        self.status.set("Guarded RPF transaction is running…")

        def failed(exc) -> None:
            self.status.set("RPF transaction was refused or rolled back.")
            messagebox.showerror("RPF transaction failed", str(exc), parent=self)

        def completed(receipt) -> None:
            self.status.set(f"RPF transaction applied and verified: {receipt}")
            messagebox.showinfo(
                "RPF transaction complete",
                f"The staged archive and all reviewed entries passed verification."
                f"\n\nReceipt: {receipt}",
                parent=self,
            )
            if self.index and Path(archive).resolve() == self.index.source:
                self._load_archive(self.index.source)

        RpfProgressDialog(
            self, "Applying RPF transaction",
            lambda progress: service.apply_change_plan(selected, progress=progress),
            completed, failed,
        )

    def _verify_transaction(self) -> None:
        service = self._transaction_service()
        if service is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Open RPF transaction receipt",
            initialdir=str(self._transaction_receipt_directory()),
            filetypes=(("Transaction receipt", "*.json"),),
        )
        if not selected:
            return
        try:
            result = service.verify_transaction(selected)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Transaction verification failed", str(exc), parent=self)
            return
        rendered = json.dumps(result, indent=2)
        self._show_text(rendered)
        self.status.set(
            f"Transaction {'is healthy' if result['healthy'] else 'needs attention'} · "
            f"archive state: {result['archive_state']}"
        )
        messagebox.showinfo(
            "RPF transaction verification",
            f"Archive state: {result['archive_state']}\n"
            f"Rollback snapshot: {'valid' if result['backup_valid'] else 'invalid'}\n"
            f"Exact entry: {'valid' if result['entry_valid'] else 'invalid'}",
            parent=self,
        )

    def _rollback_transaction(self) -> None:
        service = self._transaction_service()
        if service is None:
            return
        selected = filedialog.askopenfilename(
            parent=self, title="Open applied RPF transaction receipt",
            initialdir=str(self._transaction_receipt_directory()),
            filetypes=(("Transaction receipt", "*.json"),),
        )
        if not selected:
            return
        try:
            summary = json.loads(Path(selected).read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise ValueError("Transaction receipt must contain a JSON object")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("Invalid transaction receipt", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Rollback RPF transaction?",
            "ALLIN1 will first prove that the archive still matches this receipt, then "
            "restore and verify its complete pre-transaction snapshot.\n\n"
            f"Archive: {summary.get('archive', 'unknown')}\n"
            f"Entry: {summary.get('entry', 'unknown')}\n\nContinue?",
            parent=self, icon="warning",
        ):
            return
        self.status.set("Verifying ownership and restoring rollback snapshot…")

        def failed(exc) -> None:
            self.status.set("RPF rollback was refused or failed safely.")
            messagebox.showerror("RPF rollback failed", str(exc), parent=self)

        def completed(receipt) -> None:
            self.status.set(f"RPF rollback completed and verified: {receipt}")
            messagebox.showinfo(
                "RPF rollback complete",
                f"The original archive and exact entry passed verification.\n\nReceipt: {receipt}",
                parent=self,
            )
            if (self.index
                    and Path(str(summary.get("archive", ""))).resolve() == self.index.source):
                self._load_archive(self.index.source)

        RpfProgressDialog(
            self, "Rolling back RPF transaction",
            lambda progress: service.rollback_transaction(selected, progress=progress),
            completed, failed,
        )

    def _transaction_history(self) -> None:
        service = self._transaction_service()
        if service is not None:
            existing = getattr(self, "_history_panel", None)
            if existing is not None and existing.winfo_exists():
                existing.destroy()
            self.main_surface.pack_forget()
            self._history_panel = RpfTransactionHistoryDialog(
                self, service, embedded=True,
                on_close=self._hide_transaction_history,
            )

    def _hide_transaction_history(self) -> None:
        existing = getattr(self, "_history_panel", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        self._history_panel = None
        self.main_surface.pack(fill="both", expand=True)

    def _run_canary(self) -> None:
        service = self._transaction_service()
        if service is None or self.index is None:
            return
        if not messagebox.askyesno(
            "Run disposable real-archive canary?",
            "ALLIN1 will copy this archive into its isolated canary workspace, change one "
            "byte in the smallest eligible entry, verify the write, and prove an exact "
            "full-archive rollback. The selected source archive is never written.\n\n"
            f"Source: {self.index.source}\n\nContinue?",
            parent=self, icon="warning",
        ):
            return

        def completed(report) -> None:
            self.status.set(f"Disposable real-archive canary passed: {report}")
            messagebox.showinfo(
                "RPF canary passed",
                f"Apply, exact-entry verification, and byte-for-byte rollback passed.\n\n"
                f"Report: {report}", parent=self,
            )

        RpfProgressDialog(
            self, "Running disposable RPF canary",
            lambda progress: service.run_canary(self.index.source, progress=progress),
            completed,
            lambda exc: messagebox.showerror("RPF canary failed", str(exc), parent=self),
        )

    @staticmethod
    def _transaction_receipt_directory() -> Path:
        transactions = user_data_root() / "rpf-transactions"
        return transactions if transactions.is_dir() else user_data_root()

    def _export_index(self) -> None:
        if self.index is None:
            return
        selected = filedialog.asksaveasfilename(
            parent=self, title="Export structured RPF index",
            initialfile=f"{self.index.source.stem}-index.json",
            defaultextension=".json", filetypes=(("JSON and CSV", "*.json"),),
        )
        if not selected:
            return
        try:
            json_path, csv_path = self.index.export(selected)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status.set(f"Exported {json_path.name} and {csv_path.name}")

    def _show_image(self, data: bytes, label: str) -> None:
        try:
            with Image.open(io.BytesIO(data)) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                image.thumbnail((720, 560), Image.Resampling.LANCZOS)
                rendered = image.copy()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            self._show_text(f"Image preview failed: {exc}")
            return
        self.text_preview.pack_forget()
        self._photo = ImageTk.PhotoImage(rendered)
        self.image_preview.configure(image=self._photo, text=label, compound="top")
        self.image_preview.pack(fill="both", expand=True)

    def _show_text(self, value: str) -> None:
        self.image_preview.pack_forget()
        self._photo = None
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.insert("1.0", value or "(empty)")
        self.text_preview.configure(state="disabled")
        self.text_preview.pack(fill="both", expand=True)

    def has_active_work(self) -> bool:
        for name in ("_graph_editor", "_binary_editor"):
            editor = getattr(self, name, None)
            if editor is not None and editor.winfo_exists() and editor.has_active_work():
                return True
        return False

    def focus_active_work(self) -> bool:
        binary = getattr(self, "_binary_editor", None)
        if binary is not None and binary.winfo_exists() and binary.has_active_work():
            self.workspace_tabs.select(self.binary_tab)
            return True
        graph = getattr(self, "_graph_editor", None)
        if graph is not None and graph.winfo_exists() and graph.has_active_work():
            self.workspace_tabs.select(self.graph_tab)
            return True
        return False

    def _close(self) -> None:
        if self.has_active_work():
            self.focus_active_work()
            messagebox.showinfo(
                "Authoring operation still running",
                "Wait for the current validation, build, or dry run to finish before "
                "closing RPF Archives.", parent=self,
            )
            return
        if self._on_close is not None:
            self._on_close()
        elif self._window is not None:
            self._preview_temp.cleanup()
            self._window.destroy()
        else:
            self.destroy()

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("rpf-explorer")
        else:
            HelpCenterDialog(self, initial_topic="rpf-explorer")


def PurePathParts(value: str) -> tuple[str, ...]:
    """Tk-facing path splitter kept tiny for predictable virtual paths."""
    return tuple(part for part in value.replace("\\", "/").split("/") if part)
