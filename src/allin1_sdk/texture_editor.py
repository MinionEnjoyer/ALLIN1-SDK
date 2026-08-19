"""Embedded native YTD texture workspace editor."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.texture_workspace import (
    TextureDictionaryWorkspace,
    TextureRecord,
)


def _human_size(value: int | None) -> str:
    if value is None:
        return "missing"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return str(value)


class TextureDictionaryEditorFrame(ttk.Frame):
    """Edit a native YTD workspace without opening another application window."""

    def __init__(
        self, parent: tk.Misc, workspace: str | Path, project_root: str | Path,
        *, on_close,
    ) -> None:
        super().__init__(parent, padding=16)
        self.workspace = Path(workspace).resolve()
        self.project_root = Path(project_root).resolve()
        self.on_close = on_close
        self.editor = TextureDictionaryWorkspace(self.workspace)
        self.catalog = self.editor.catalog()
        self.records: dict[str, TextureRecord] = {}
        self._photo: ImageTk.PhotoImage | None = None
        self._build()
        self._refresh()

    def _build(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header, text="YTD texture editor", font=("Segoe UI Semibold", 17),
            foreground="#1f7f42",
        ).pack(side="left")
        ttk.Button(header, text="Back to assets", command=self.on_close).pack(side="right")
        ttk.Label(
            self,
            text=(
                "Replace, add, or remove texture dependencies in an editable native "
                "workspace. Raster imports become uncompressed DDS; existing compressed "
                "DDS files retain their format. Every edit keeps local undo artifacts, "
                "and the YTD must still pass a separate CodeWalker rebuild/reparse."
            ),
            wraplength=1080, justify="left", foreground="#52635c",
        ).pack(fill="x", pady=(0, 10))
        tools = ttk.Frame(self)
        tools.pack(fill="x", pady=(0, 9))
        ttk.Button(tools, text="Replace…", command=self._replace).pack(side="left")
        ttk.Button(tools, text="Add…", command=self._add).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="Remove…", command=self._remove).pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="Undo last edit", command=self._undo).pack(
            side="left", padx=(6, 0),
        )
        ttk.Button(tools, text="Reload", command=self._refresh).pack(side="left", padx=(6, 0))
        ttk.Button(
            tools, text="Build + validate YTD…", command=self._build_ytd,
            style="Accent.TButton",
        ).pack(side="right")
        self.status = tk.StringVar(value=str(self.workspace))
        ttk.Label(
            self, textvariable=self.status, foreground="#52635c",
            wraplength=1080, justify="left",
        ).pack(fill="x", pady=(0, 8))

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        inventory = ttk.LabelFrame(panes, text="Textures", padding=9)
        preview = ttk.LabelFrame(panes, text="Texture preview", padding=10)
        panes.add(inventory, weight=3)
        panes.add(preview, weight=4)

        filter_row = ttk.Frame(inventory)
        filter_row.pack(fill="x", pady=(0, 7))
        ttk.Label(filter_row, text="Filter").pack(side="left")
        self.query = tk.StringVar()
        self.query.trace_add("write", lambda *_args: self._populate())
        ttk.Entry(filter_row, textvariable=self.query).pack(
            side="left", fill="x", expand=True, padx=(7, 0),
        )
        tree_frame = ttk.Frame(inventory)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame, columns=("dimensions", "format", "mips", "size", "state"),
            show="tree headings", selectmode="browse",
        )
        self.tree.heading("#0", text="Name")
        for name, label, width in (
            ("dimensions", "Dimensions", 90), ("format", "Format", 145),
            ("mips", "Mips", 44), ("size", "Size", 72), ("state", "State", 75),
        ):
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, minwidth=40, stretch=name == "format")
        self.tree.column("#0", width=180, minwidth=110)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select)

        self.preview_title = tk.StringVar(value="Select a texture")
        self.preview_meta = tk.StringVar(value="")
        ttk.Label(
            preview, textvariable=self.preview_title,
            font=("Segoe UI Semibold", 13), foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            preview, textvariable=self.preview_meta, foreground="#52635c",
            wraplength=620, justify="left",
        ).pack(anchor="w", pady=(3, 8))
        ttk.Separator(preview).pack(fill="x", pady=(0, 8))
        self.preview = tk.Label(
            preview, background="#202722", foreground="#dfe9e2", anchor="center",
            text="Texture image preview",
        )
        self.preview.pack(fill="both", expand=True)

    def _refresh(self, select_name: str | None = None) -> None:
        try:
            self.catalog = self.editor.catalog()
        except (OSError, ValueError) as exc:
            messagebox.showerror("YTD workspace error", str(exc), parent=self)
            return
        self._populate(select_name)
        self.status.set(
            f"{len(self.catalog.textures)} textures · {len(self.catalog.warnings)} finding(s) · "
            f"{self.workspace}"
        )

    def _populate(self, select_name: str | None = None) -> None:
        selected_name = select_name
        if selected_name is None:
            record = self._selected()
            selected_name = record.name if record else None
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        query = self.query.get().strip().casefold()
        selected_item = None
        for number, record in enumerate(self.catalog.textures):
            if query and query not in record.name.casefold() and query not in record.file_name.casefold():
                continue
            item = f"texture:{number}"
            self.records[item] = record
            self.tree.insert("", "end", iid=item, text=record.name, values=(
                f"{record.width}×{record.height}", record.format,
                record.mip_levels, _human_size(record.size),
                "Review" if record.warnings else "Ready",
            ))
            if selected_name and record.name.casefold() == selected_name.casefold():
                selected_item = item
        if selected_item:
            self.tree.selection_set(selected_item)
            self.tree.see(selected_item)
            self._select()

    def _selected(self) -> TextureRecord | None:
        selected = self.tree.selection()
        return self.records.get(selected[0]) if selected else None

    def _select(self, _event: object | None = None) -> None:
        record = self._selected()
        if record is None:
            return
        self.preview_title.set(record.name)
        warnings = " · ".join(record.warnings) if record.warnings else "Validated DDS metadata"
        self.preview_meta.set(
            f"{record.file_name} · {record.width}×{record.height} · {record.mip_levels} mip(s) · "
            f"{record.format} · {_human_size(record.size)}\n{warnings}"
        )
        path = self.catalog.assets.joinpath(*Path(record.file_name).parts)
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                image.thumbnail((620, 500), Image.Resampling.LANCZOS)
                rendered = image.copy()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            self._photo = None
            self.preview.configure(image="", text=f"DDS preview unavailable\n\n{exc}")
            return
        self._photo = ImageTk.PhotoImage(rendered)
        self.preview.configure(image=self._photo, text="", compound="center")

    @staticmethod
    def _image_types():
        return (
            ("Texture images", "*.dds *.png *.jpg *.jpeg *.bmp *.tga *.webp"),
            ("All files", "*.*"),
        )

    def _replace(self) -> None:
        record = self._selected()
        if record is None:
            messagebox.showinfo("Select a texture", "Select a texture first.", parent=self)
            return
        source = filedialog.askopenfilename(
            parent=self, title=f"Replace {record.name}", filetypes=self._image_types(),
        )
        if not source:
            return
        try:
            result = self.editor.replace(record.name, source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Texture replacement failed", str(exc), parent=self)
            return
        self._refresh(result.texture.name)
        self.status.set(f"Replaced {result.texture.name}; undo history: {result.history}")

    def _add(self) -> None:
        name = simpledialog.askstring(
            "Add YTD texture", "Texture name (also used for its DDS filename):", parent=self,
        )
        if not name:
            return
        source = filedialog.askopenfilename(
            parent=self, title=f"Select image for {name}", filetypes=self._image_types(),
        )
        if not source:
            return
        try:
            result = self.editor.add(name, source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Texture addition failed", str(exc), parent=self)
            return
        self._refresh(result.texture.name)
        self.status.set(f"Added {result.texture.name}; undo history: {result.history}")

    def _remove(self) -> None:
        record = self._selected()
        if record is None:
            messagebox.showinfo("Select a texture", "Select a texture first.", parent=self)
            return
        if not messagebox.askyesno(
            "Remove texture?",
            f"Remove {record.name} from this YTD workspace? External models may still "
            "reference this name. Local undo artifacts will be retained.",
            parent=self, icon="warning",
        ):
            return
        try:
            result = self.editor.remove(record.name)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Texture removal failed", str(exc), parent=self)
            return
        self._refresh()
        self.status.set(f"Removed {record.name}; undo history: {result.history}")

    def _undo(self) -> None:
        if not messagebox.askyesno(
            "Undo last texture edit?",
            "Restore the most recent YTD edit snapshot? The current pre-restore state "
            "will also be retained in history.", parent=self,
        ):
            return
        try:
            result = self.editor.restore_latest()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Texture undo failed", str(exc), parent=self)
            return
        self._refresh()
        self.status.set(
            f"Restored {result.restored.name}; recovery snapshot: {result.recovery_history}"
        )

    def _build_ytd(self) -> None:
        manifest = self.editor.manifest
        name = str(manifest["source"]["name"])
        output = filedialog.asksaveasfilename(
            parent=self, title="Build and validate edited YTD", initialfile=name,
            defaultextension=".ytd", filetypes=(("YTD texture dictionary", "*.ytd"),),
        )
        if not output:
            return
        self.status.set("Building and reparsing edited YTD through CodeWalker…")
        self.update_idletasks()
        try:
            asset, report = NativeAssetInspector(self.project_root).build_workspace(
                self.workspace, output,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self.status.set("YTD build failed; the workspace remains editable.")
            messagebox.showerror("YTD build failed", str(exc), parent=self)
            return
        self.status.set(f"Built and reparsed YTD: {asset}")
        messagebox.showinfo(
            "YTD validated",
            f"CodeWalker rebuilt and reparsed the texture dictionary.\n\n"
            f"Asset: {asset}\nReport: {report}", parent=self,
        )
