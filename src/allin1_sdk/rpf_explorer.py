"""Interactive read-only RPF explorer for the desktop launcher."""

from __future__ import annotations

import io
import json
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from allin1_sdk.detector import detect_gta_path
from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES, NativeAssetInspector
from allin1_sdk.rpf_tools import RpfEntryRecord, RpfExplorerService, RpfIndex
from allin1_sdk.help_center import HelpCenterDialog


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class RpfExplorerDialog(tk.Toplevel):
    """Search, inspect, extract, and plan changes without editing an RPF."""

    def __init__(
        self, parent: tk.Misc, project_root: str | Path,
        installation_roots: tuple[Path, ...] = (), archive: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        roots = [str(path.resolve()) for path in installation_roots if path.is_dir()]
        detected = detect_gta_path()
        if detected and str(detected.resolve()) not in roots:
            roots.append(str(detected.resolve()))
        self.game_paths = roots
        self.index: RpfIndex | None = None
        self.service: RpfExplorerService | None = None
        self.entry_items: dict[str, RpfEntryRecord] = {}
        self.entry_action_menus: list[tk.Menu] = []
        self.file_menus: list[tk.Menu] = []
        self._photo: ImageTk.PhotoImage | None = None
        self._preview_temp = tempfile.TemporaryDirectory(prefix="allin1-rpf-preview-")
        self.title("ALLIN1 — RPF Explorer")
        self.geometry("1320x840")
        self.minsize(980, 650)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
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
            label="RPF Explorer Help", accelerator="F1",
            command=lambda: HelpCenterDialog(self, initial_topic="rpf-explorer"),
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self.bind(
            "<F1>", lambda _event: HelpCenterDialog(self, initial_topic="rpf-explorer"),
        )

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer, text="RPF explorer", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Browse real RPF and nested-RPF entries with edition-aware keys. "
                "Extraction and native preview are read-only; replacement produces a "
                "reviewable safety plan and never modifies the archive."
            ),
            wraplength=1180, justify="left", foreground="#52635c",
        ).pack(anchor="w", pady=(3, 10))

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
            toolbar, text="Entry actions", menu=self._entry_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        ttk.Menubutton(
            toolbar, text="Archive actions", menu=self._file_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(value="Select a GTA V installation and open an RPF.")
        ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=1180, justify="left",
        ).pack(fill="x", pady=(0, 8))

        filter_row = ttk.Frame(outer)
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

        panes = ttk.Panedwindow(outer, orient="horizontal")
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

    def _file_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open RPF…", command=self._choose_archive)
        menu.add_command(
            label="Export index…", command=self._export_index, state="disabled",
        )
        self.file_menus.append(menu)
        return menu

    def _entry_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(
            label="Native preview", command=self._preview_selected, state="disabled",
        )
        menu.add_command(
            label="Extract selected…", command=self._extract_selected, state="disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="Plan replacement…", command=self._plan_replacement, state="disabled",
        )
        self.entry_action_menus.append(menu)
        return menu

    def _set_archive_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.file_menus:
            menu.entryconfigure("Export index…", state=state)

    def _set_entry_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.entry_action_menus:
            menu.entryconfigure("Native preview", state=state)
            menu.entryconfigure("Extract selected…", state=state)
            menu.entryconfigure("Plan replacement…", state=state)

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

    def _select_entry(self, _event: object | None = None) -> None:
        entry = self._selected()
        self._set_entry_actions(bool(entry and entry.kind != "directory"))
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
                "are capped at {_human_size(MAX_NATIVE_PREVIEW_BYTES)}.", parent=self,
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
            report = NativeAssetInspector(self.project_root).inspect_bytes(
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
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not create plan", str(exc), parent=self)
            return
        self.status.set(f"Wrote plan only; no RPF changes were made: {output}")

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

    def _close(self) -> None:
        self._preview_temp.cleanup()
        self.destroy()


def PurePathParts(value: str) -> tuple[str, ...]:
    """Tk-facing path splitter kept tiny for predictable virtual paths."""
    return tuple(part for part in value.replace("\\", "/").split("/") if part)
