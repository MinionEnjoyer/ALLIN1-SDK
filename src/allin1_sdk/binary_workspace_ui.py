"""Embedded, recovery-backed binary patch workspace editor."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from allin1_sdk.binary_workspace import BinaryPatchWorkspace


VIEW_LENGTHS = (256, 512, 1024, 4096, 16384, 65536)


def _parse_offset(value: str) -> int:
    text = value.strip()
    if not text:
        raise ValueError("Enter a decimal or 0x-prefixed byte offset")
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError("Offset must be decimal or begin with 0x") from exc


def _parse_hex(value: str, label: str) -> bytes:
    normalized = "".join(value.split()).replace("0x", "")
    if not normalized or len(normalized) % 2:
        raise ValueError(f"{label} must contain complete hexadecimal bytes")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} contains non-hexadecimal characters") from exc


class BinaryWorkspaceFrame(ttk.Frame):
    """Inspect and safely patch one binary workspace without leaving RPF Archives."""

    def __init__(
        self, parent: tk.Misc, workspace: str | Path, *,
        on_close=None, on_plan=None,
    ) -> None:
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.workspace = Path(workspace).expanduser().resolve()
        self._on_close = on_close
        self._on_plan = on_plan
        self._busy = False
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._controls: list[tk.Widget] = []
        self.state: dict | None = None
        self.offset = tk.StringVar(value="0x00000000")
        self.length = tk.StringVar(value="512")
        self.expected = tk.StringVar()
        self.replacement = tk.StringVar()
        self.summary = tk.StringVar(value="Validating workspace…")
        self.status = tk.StringVar(value="Checking source snapshot and patch history…")
        self._build_ui()
        self.after_idle(self._reload_async)

    @property
    def busy(self) -> bool:
        return self._busy

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(
            heading, text="Binary workspace", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(side="left")
        self.close_button = ttk.Button(
            heading, text="Close editor", command=self._close_panel,
        )
        self.close_button.pack(side="right")
        self._controls.append(self.close_button)
        ttk.Button(
            heading, text="Open folder", command=self._open_folder,
        ).pack(side="right", padx=(0, 7))
        ttk.Label(
            outer,
            text=(
                "Inspect an immutable source beside its same-size editable copy. "
                "Every patch requires the bytes you expect to replace and is retained "
                "in a hash-chained history. Orange bytes differ from the source."
            ),
            foreground="#52635c", wraplength=960, justify="left",
        ).pack(anchor="w", pady=(3, 7))
        ttk.Label(
            outer, textvariable=self.summary, foreground="#37584d",
            font=("Segoe UI Semibold", 9), wraplength=980, justify="left",
        ).pack(anchor="w", pady=(0, 9))

        navigation = ttk.Frame(outer)
        navigation.pack(fill="x", pady=(0, 8))
        ttk.Label(navigation, text="Offset").pack(side="left")
        self.offset_entry = ttk.Entry(
            navigation, textvariable=self.offset, width=16,
            font=("Cascadia Mono", 9),
        )
        self.offset_entry.pack(side="left", padx=(7, 12))
        self.offset_entry.bind("<Return>", lambda _event: self._render_view())
        ttk.Label(navigation, text="Bytes per page").pack(side="left")
        self.length_combo = ttk.Combobox(
            navigation, textvariable=self.length, width=9,
            values=tuple(str(value) for value in VIEW_LENGTHS),
        )
        self.length_combo.pack(side="left", padx=(7, 7))
        for text, command in (
            ("Go", self._render_view), ("Previous", self._previous_page),
            ("Next", self._next_page), ("Refresh", self._reload_async),
        ):
            button = ttk.Button(navigation, text=text, command=command)
            button.pack(side="left", padx=(0, 6))
            self._controls.append(button)
        self._controls.extend((self.offset_entry, self.length_combo))

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 7))
        self.progress.pack_forget()
        ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=980, justify="left",
        ).pack(side="bottom", fill="x", pady=(8, 0))

        body = ttk.Panedwindow(outer, orient="horizontal")
        self.body = body
        body.pack(fill="both", expand=True)
        viewer = ttk.LabelFrame(body, text="Editable bytes", padding=8)
        inspector = ttk.Frame(body, padding=(12, 0, 0, 0), width=360)
        body.add(viewer, weight=5)
        body.add(inspector, weight=2)

        legend = ttk.Frame(viewer)
        legend.pack(fill="x", pady=(0, 6))
        tk.Label(
            legend, text=" CHANGED FROM SOURCE ", background="#f4c978",
            foreground="#5b3900", font=("Segoe UI Semibold", 8), padx=4, pady=2,
        ).pack(side="left")
        self.view_summary = tk.StringVar(value="No page loaded")
        ttk.Label(
            legend, textvariable=self.view_summary, foreground="#52635c",
        ).pack(side="right")
        text_host = ttk.Frame(viewer)
        text_host.pack(fill="both", expand=True)
        self.hex_view = tk.Text(
            text_host, wrap="none", state="disabled", font=("Cascadia Mono", 9),
            background="#101713", foreground="#dce8e1", insertbackground="#ffffff",
            relief="flat", padx=9, pady=9, takefocus=True,
        )
        self.hex_view.tag_configure(
            "changed", background="#f4c978", foreground="#4a2f00",
        )
        yscroll = ttk.Scrollbar(text_host, orient="vertical", command=self.hex_view.yview)
        xscroll = ttk.Scrollbar(text_host, orient="horizontal", command=self.hex_view.xview)
        self.hex_view.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.hex_view.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        text_host.rowconfigure(0, weight=1)
        text_host.columnconfigure(0, weight=1)

        side = ttk.Notebook(inspector)
        self.inspector_tabs = side
        side.pack(fill="both", expand=True)
        patch = ttk.Frame(side, padding=10)
        history = ttk.Frame(side, padding=8)
        self.history_panel = history
        side.add(patch, text="Guarded Patch")
        side.add(history, text="History")

        ttk.Label(patch, text="Patch offset", style="FieldLabel.TLabel").pack(
            anchor="w",
        )
        self.patch_offset = tk.StringVar(value="0x00000000")
        patch_offset_entry = ttk.Entry(
            patch, textvariable=self.patch_offset, font=("Cascadia Mono", 9),
        )
        patch_offset_entry.pack(fill="x", pady=(3, 8))
        ttk.Label(
            patch, text="Expected current bytes", style="FieldLabel.TLabel",
        ).pack(anchor="w")
        expected_entry = ttk.Entry(
            patch, textvariable=self.expected, font=("Cascadia Mono", 9),
        )
        expected_entry.pack(fill="x", pady=(3, 8))
        ttk.Label(
            patch, text="Replacement bytes", style="FieldLabel.TLabel",
        ).pack(anchor="w")
        replacement_entry = ttk.Entry(
            patch, textvariable=self.replacement, font=("Cascadia Mono", 9),
        )
        replacement_entry.pack(fill="x", pady=(3, 8))
        patch_actions = ttk.Frame(patch)
        patch_actions.pack(fill="x")
        patch_actions.columnconfigure(0, weight=1)
        patch_actions.columnconfigure(1, weight=1)
        read_button = ttk.Button(
            patch_actions, text="Read current bytes", command=self._read_expected,
        )
        read_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        apply_button = ttk.Button(
            patch_actions, text="Apply patch…", command=self._apply_patch,
            style="Accent.TButton",
        )
        apply_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        secondary = ttk.Frame(patch)
        secondary.pack(fill="x", pady=(7, 0))
        for text, command in (
            ("Undo latest", self._undo), ("Build verified…", self._build),
            ("Create RPF plan…", self._plan),
        ):
            button = ttk.Button(secondary, text=text, command=command)
            button.pack(side="left", padx=(0, 5))
            self._controls.append(button)
        self._controls.extend((
            patch_offset_entry, expected_entry, replacement_entry,
            read_button, apply_button,
        ))

        self.history_tree = ttk.Treeview(
            history, columns=("action", "offset", "length"),
            show="tree headings", selectmode="browse", height=8,
        )
        self.history_tree.heading("#0", text="#")
        self.history_tree.heading("action", text="Action")
        self.history_tree.heading("offset", text="Offset")
        self.history_tree.heading("length", text="Bytes")
        self.history_tree.column("#0", width=44, stretch=False, anchor="e")
        self.history_tree.column("action", width=62, stretch=False)
        self.history_tree.column("offset", width=95, stretch=True)
        self.history_tree.column("length", width=52, stretch=False, anchor="e")
        history_scroll = ttk.Scrollbar(
            history, orient="vertical", command=self.history_tree.yview,
        )
        self.history_xscroll = ttk.Scrollbar(
            history, orient="horizontal", command=self.history_tree.xview,
        )
        self.history_tree.configure(
            yscrollcommand=history_scroll.set,
            xscrollcommand=self.history_xscroll.set,
        )
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        history_scroll.grid(row=0, column=1, sticky="ns")
        self.history_xscroll.grid(row=1, column=0, sticky="ew")
        history.rowconfigure(0, weight=1)
        history.columnconfigure(0, weight=1)
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selected)

    def has_active_work(self) -> bool:
        return self._busy

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
            self.status.set("Operation failed safely; the immutable source is unchanged.")
            messagebox.showerror("Binary workspace operation failed", str(payload), parent=self)
            return
        completed, result = payload
        completed(result)

    def _reload_async(self) -> None:
        self._run(
            "Validating source snapshot, editable copy, and patch history…",
            lambda: BinaryPatchWorkspace.validate(self.workspace),
            self._loaded,
        )

    def _loaded(self, state: dict) -> None:
        self.state = state
        manifest = state["manifest"]
        binding = manifest.get("source_binding", {})
        bound = "archive-bound" if isinstance(binding, dict) and binding.get("entry_id") else "standalone"
        self.summary.set(
            f"{manifest['name']} · {int(manifest['size']):,} bytes · "
            f"{len(state['records']):,} history records · {bound} · source snapshot verified"
        )
        self._populate_history()
        self._render_view()
        self.status.set("Workspace validation passed. No archive has been modified.")

    def _populate_history(self) -> None:
        self.history_tree.delete(*self.history_tree.get_children())
        if self.state is None:
            return
        self.inspector_tabs.tab(
            self.history_panel, text=f"History ({len(self.state['records']):,})",
        )
        for record in self.state["records"]:
            sequence = int(record["sequence"])
            self.history_tree.insert(
                "", "end", iid=str(sequence), text=str(sequence),
                values=(
                    str(record.get("action", "patch")).title(),
                    f"0x{int(record['offset']):08X}", int(record["length"]),
                ),
            )

    def _page_values(self) -> tuple[int, int, int]:
        if self.state is None:
            raise ValueError("Binary workspace is still loading")
        size = int(self.state["manifest"]["size"])
        offset = _parse_offset(self.offset.get())
        try:
            length = int(self.length.get().strip())
        except ValueError as exc:
            raise ValueError("Bytes per page must be a number") from exc
        if not 1 <= length <= 65536:
            raise ValueError("Bytes per page must be between 1 and 65,536")
        if not 0 <= offset < size:
            raise ValueError(f"Offset must be between 0 and 0x{size - 1:X}")
        return offset, min(length, size - offset), size

    def _render_view(self) -> None:
        try:
            offset, length, size = self._page_values()
            assert self.state is not None
            with self.state["original"].open("rb") as original_stream:
                original_stream.seek(offset)
                original = original_stream.read(length)
            with self.state["editable"].open("rb") as editable_stream:
                editable_stream.seek(offset)
                editable = editable_stream.read(length)
            if len(original) != length or len(editable) != length:
                raise RuntimeError("Binary workspace changed while this page was read")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not display binary page", str(exc), parent=self)
            return
        self.offset.set(f"0x{offset:08X}")
        self.patch_offset.set(f"0x{offset:08X}")
        self.hex_view.configure(state="normal")
        self.hex_view.delete("1.0", "end")
        changed = 0
        for index in range(0, length, 16):
            before = original[index:index + 16]
            after = editable[index:index + 16]
            self.hex_view.insert("end", f"{offset + index:08X}  ")
            for position, value in enumerate(after):
                different = value != before[position]
                if different:
                    changed += 1
                self.hex_view.insert(
                    "end", f"{value:02X} ", ("changed",) if different else (),
                )
            self.hex_view.insert("end", " " * ((16 - len(after)) * 3))
            self.hex_view.insert("end", " |")
            for position, value in enumerate(after):
                character = chr(value) if 32 <= value < 127 else "."
                self.hex_view.insert(
                    "end", character,
                    ("changed",) if value != before[position] else (),
                )
            self.hex_view.insert("end", " " * (16 - len(after)) + "|\n")
        self.hex_view.configure(state="disabled")
        self.hex_view.yview_moveto(0.0)
        self.view_summary.set(
            f"0x{offset:08X}–0x{offset + length - 1:08X} of 0x{size - 1:08X} · "
            f"{changed} changed on this page"
        )

    def _previous_page(self) -> None:
        try:
            offset, length, _size = self._page_values()
        except ValueError as exc:
            messagebox.showerror("Invalid page", str(exc), parent=self)
            return
        self.offset.set(f"0x{max(0, offset - length):08X}")
        self._render_view()

    def _next_page(self) -> None:
        try:
            offset, length, size = self._page_values()
        except ValueError as exc:
            messagebox.showerror("Invalid page", str(exc), parent=self)
            return
        self.offset.set(f"0x{min(size - 1, offset + length):08X}")
        self._render_view()

    def _history_selected(self, _event: object | None = None) -> None:
        if self.state is None:
            return
        selected = self.history_tree.selection()
        if not selected:
            return
        sequence = int(selected[0])
        record = self.state["records"][sequence - 1]
        offset = int(record["offset"])
        self.offset.set(f"0x{offset:08X}")
        self.patch_offset.set(f"0x{offset:08X}")
        self.expected.set("")
        self.replacement.set("")
        self._render_view()

    def _read_expected(self) -> None:
        if self.state is None:
            return
        try:
            replacement = _parse_hex(self.replacement.get(), "Replacement")
            offset = _parse_offset(self.patch_offset.get())
            size = int(self.state["manifest"]["size"])
            if offset < 0 or offset + len(replacement) > size:
                raise ValueError("Patch falls outside the editable asset")
            with self.state["editable"].open("rb") as stream:
                stream.seek(offset)
                current = stream.read(len(replacement))
            if len(current) != len(replacement):
                raise RuntimeError("Editable asset changed while bytes were read")
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Could not read expected bytes", str(exc), parent=self)
            return
        self.expected.set(" ".join(f"{value:02X}" for value in current))

    def _apply_patch(self) -> None:
        try:
            offset = _parse_offset(self.patch_offset.get())
            expected = _parse_hex(self.expected.get(), "Expected bytes")
            replacement = _parse_hex(self.replacement.get(), "Replacement")
            if len(expected) != len(replacement):
                raise ValueError("Expected and replacement byte counts must match")
        except ValueError as exc:
            messagebox.showerror("Invalid binary patch", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Apply guarded binary patch?",
            f"Offset: 0x{offset:X}\nBytes: {len(replacement):,}\n\n"
            f"Expected: {expected.hex(' ').upper()}\n"
            f"Replace:  {replacement.hex(' ').upper()}\n\n"
            "The immutable source remains unchanged and this edit is appended to history.",
            parent=self, icon="warning",
        ):
            return

        def completed(_record: Path) -> None:
            self.replacement.set("")
            self._reload_async()

        self._run(
            "Validating expected bytes and applying the same-size patch…",
            lambda: BinaryPatchWorkspace.patch(
                self.workspace, offset, replacement.hex(), expected_hex=expected.hex(),
            ),
            completed,
        )

    def _undo(self) -> None:
        if self.state is None or not self.state["records"]:
            messagebox.showinfo("Nothing to undo", "This workspace has no patch history.", parent=self)
            return
        latest = self.state["records"][-1]
        if not messagebox.askyesno(
            "Undo latest binary operation?",
            f"Append a recovery operation at 0x{int(latest['offset']):X} for "
            f"{int(latest['length']):,} bytes?\n\nExisting history is retained.",
            parent=self,
        ):
            return
        self._run(
            "Validating history and appending the undo operation…",
            lambda: BinaryPatchWorkspace.undo(self.workspace),
            lambda _record: self._reload_async(),
        )

    def _build(self) -> None:
        if self.state is None:
            return
        name = str(self.state["manifest"]["name"])
        output = filedialog.asksaveasfilename(
            parent=self, title="Build verified binary asset",
            initialfile=name, filetypes=(("Binary asset", "*.*"), ("All files", "*.*")),
        )
        if not output:
            return

        def completed(result) -> None:
            asset, report = result
            self.status.set(f"Built verified same-size asset: {asset}")
            messagebox.showinfo(
                "Verified binary asset built",
                f"Asset: {asset}\nChanged-range report: {report}", parent=self,
            )

        self._run(
            "Comparing every byte and building the changed-range report…",
            lambda: BinaryPatchWorkspace.build(self.workspace, output),
            completed,
        )

    def _plan(self) -> None:
        if self._on_plan is None:
            messagebox.showinfo(
                "RPF planning unavailable",
                "Open this workspace from RPF Archives to create an archive-bound plan.",
                parent=self,
            )
            return
        self._on_plan(self.workspace)

    def _open_folder(self) -> None:
        if os.name == "nt":
            os.startfile(self.workspace)  # type: ignore[attr-defined]

    def _close_panel(self) -> bool:
        if self._busy:
            messagebox.showinfo(
                "Binary operation still running",
                "Wait for the current validation or build to finish before closing.",
                parent=self,
            )
            return False
        if self._on_close is not None:
            self._on_close()
        else:
            self.destroy()
        return True
