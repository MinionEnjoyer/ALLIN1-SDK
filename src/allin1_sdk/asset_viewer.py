"""Read-only package asset browser for the ALLIN1 desktop tool."""

from __future__ import annotations

import io
import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageEntry,
    PackageScan,
    decode_text_preview,
    hex_preview,
)
from allin1_sdk.native_assets import (
    NATIVE_ASSET_SUFFIXES,
    NativeAssetInspector,
    native_preview_limit,
)
from allin1_sdk.help_center import HelpCenterDialog


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


_BINARY_HELP = {
    ".rpf": "Rockstar archive. Inventory is shown, but nested entries require the Enhanced-aware RPF toolchain.",
    ".ytd": "Rockstar texture dictionary. Use RpfPatcher or CodeWalker to inspect contained textures.",
    ".ydr": "Rockstar drawable model. Use CodeWalker or Sollumz for geometry and materials.",
    ".ydd": "Rockstar drawable dictionary. Use CodeWalker or Sollumz for contained models.",
    ".yft": "Rockstar fragment model, commonly used by vehicles and breakable objects.",
    ".ybn": "Rockstar collision bounds asset.",
    ".ymap": "Rockstar map placement asset.",
    ".ytyp": "Rockstar archetype definition asset.",
    ".ymt": "Rockstar compiled metadata resource. Structured XML preview is attempted read-only.",
    ".ymf": "Rockstar metadata resource.",
    ".ynd": "Rockstar path-node graph.",
    ".ynv": "Rockstar navigation mesh.",
    ".ypt": "Rockstar particle-effect dictionary.",
    ".ycd": "Rockstar animation clip dictionary.",
    ".gfx": "Scaleform UI movie. Use a SWF/GFX-aware inspector before editing frames or labels.",
    ".gxt2": "Rockstar text-label table. Preserve hashes and merge against the current game build.",
    ".awc": "Rockstar audio wave container.",
    ".rel": "Rockstar audio relationship data.",
    ".dll": "Compiled .NET/native library. The viewer does not execute package code.",
    ".asi": "Compiled ScriptHook plug-in. The viewer does not execute package code.",
}


class AssetViewerDialog(tk.Toplevel):
    """Browse loose or archived package assets without installing them."""

    def __init__(
        self, parent: tk.Misc, source: str | Path | None = None,
        scan: PackageScan | None = None,
    ) -> None:
        super().__init__(parent)
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.reader: PackageAssetReader | None = None
        self.entries: dict[str, PackageEntry] = {}
        self.action_menus: list[tk.Menu] = []
        self._photo: ImageTk.PhotoImage | None = None
        self.title("ALLIN1 Package Asset Viewer")
        self.geometry("1180x780")
        self.minsize(900, 620)
        self.transient(parent)
        self._build()
        if source is not None:
            self._load_source(Path(source), scan)

    def _build(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        file_menu = self._open_menu(menu)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self.destroy)
        menu.add_cascade(label="File", menu=file_menu)
        action_menu = self._action_menu(menu)
        menu.add_cascade(label="Actions", menu=action_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="Asset Viewer Help", accelerator="F1",
            command=lambda: HelpCenterDialog(self, initial_topic="asset-viewer"),
        )
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)
        self.bind(
            "<F1>", lambda _event: HelpCenterDialog(self, initial_topic="asset-viewer"),
        )

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="Package asset viewer", style="DialogTitle.TLabel",
            font=("Segoe UI Semibold", 17),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Inspect package files before installation. Images and authored text "
                "are previewed directly; GTA resources receive header analysis, structured "
                "CodeWalker XML, and texture contact sheets when supported. Nothing is executed."
            ),
            wraplength=1080, justify="left", foreground="#52635c",
        ).pack(anchor="w", pady=(3, 12))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Menubutton(
            toolbar, text="Open package", menu=self._open_menu(toolbar),
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Menubutton(
            toolbar, text="Package actions", menu=self._action_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(
            value="Open a package folder or OIV/ZIP/RAR/7z to begin."
        )
        ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=1080, justify="left",
        ).pack(fill="x", anchor="w", pady=(0, 10))

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)
        inventory = ttk.LabelFrame(panes, text="Package files", padding=10)
        preview = ttk.LabelFrame(panes, text="Asset preview", padding=12)
        panes.add(inventory, weight=2)
        panes.add(preview, weight=5)

        search_row = ttk.Frame(inventory)
        search_row.pack(fill="x", pady=(0, 8))
        ttk.Label(search_row, text="Filter").pack(side="left")
        self.search = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.search)
        search_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.search.trace_add("write", lambda *_args: self._populate_tree())

        tree_row = ttk.Frame(inventory)
        tree_row.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_row, columns=("size",), show="tree headings", selectmode="browse",
        )
        self.tree.heading("#0", text="Asset")
        self.tree.heading("size", text="Size")
        self.tree.column("#0", width=300, minwidth=180)
        self.tree.column("size", width=82, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(tree_row, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select_asset)

        self.asset_title = tk.StringVar(value="Select an asset")
        self.asset_meta = tk.StringVar(value="No package loaded")
        ttk.Label(
            preview, textvariable=self.asset_title,
            font=("Segoe UI Semibold", 13), foreground="#1f7f42",
        ).pack(anchor="w")
        ttk.Label(
            preview, textvariable=self.asset_meta, foreground="#52635c",
            wraplength=660, justify="left",
        ).pack(anchor="w", pady=(3, 10))
        ttk.Separator(preview).pack(fill="x", pady=(0, 10))

        self.preview_surface = tk.Frame(preview, background="#ffffff")
        self.preview_surface.pack(fill="both", expand=True)
        self.image_preview = tk.Label(
            self.preview_surface, background="#ffffff", anchor="center",
            text="Open a package to browse its assets.", foreground="#52635c",
        )
        self.image_preview.pack(fill="both", expand=True)
        self.text_preview = tk.Text(
            self.preview_surface, wrap="word", relief="flat", borderwidth=0,
            background="#ffffff", foreground="#1e2925",
            font=("Cascadia Mono", 9), padx=10, pady=10, state="disabled",
        )

    def _open_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open folder…", command=self._choose_folder)
        menu.add_command(label="Open archive…", command=self._choose_archive)
        return menu

    def _action_menu(self, parent: tk.Misc) -> tk.Menu:
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(
            label="Export inventory…", command=self._export_inventory, state="disabled",
        )
        menu.add_command(
            label="Open package location", command=self._open_location, state="disabled",
        )
        self.action_menus.append(menu)
        return menu

    def _set_package_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.action_menus:
            menu.entryconfigure("Export inventory…", state=state)
            menu.entryconfigure("Open package location", state=state)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select a package or loose DLC folder",
        )
        if selected:
            self._load_source(Path(selected))

    def _choose_archive(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Select an OIV, ZIP, RAR, or 7z package",
            filetypes=(("GTA package", "*.oiv *.zip *.rar *.7z"),
                       ("All files", "*.*")),
        )
        if selected:
            self._load_source(Path(selected))

    def _load_source(self, source: Path, scan: PackageScan | None = None) -> None:
        self.status.set("Scanning package…")
        self.update_idletasks()
        try:
            loaded = scan or AddonPackageInspector().inspect(source)
            reader = PackageAssetReader(source)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open package", str(exc), parent=self)
            self.status.set("Package could not be opened.")
            return
        self.source = source.resolve()
        self.scan = loaded
        self.reader = reader
        self._set_package_actions(True)
        self._populate_tree()
        self.status.set(
            f"{len(loaded.entries):,} files · {_human_size(loaded.total_bytes)} · "
            f"{loaded.warning_count} warnings"
        )
        self.asset_title.set(self.source.name)
        self.asset_meta.set(
            f"{loaded.source_kind.upper()} package · {loaded.error_count} errors · "
            f"{loaded.warning_count} warnings"
        )
        self._show_text(
            "Package inventory loaded. Select an asset on the left to inspect it.\n\n" +
            "\n".join(
                f"{item.severity.upper()} {item.code}: {item.message}"
                for item in loaded.findings
            )
        )

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        if self.scan is None:
            return
        query = self.search.get().strip().casefold()
        grouped: dict[str, list[PackageEntry]] = {}
        for entry in self.scan.entries:
            if query and query not in entry.path.casefold():
                continue
            grouped.setdefault(entry.category, []).append(entry)
        counter = 0
        for category in sorted(grouped):
            parent = self.tree.insert(
                "", "end", text=category,
                values=(f"{len(grouped[category])} files",), open=True,
            )
            for entry in sorted(grouped[category], key=lambda item: item.path.casefold()):
                item_id = f"asset:{counter}"
                counter += 1
                self.entries[item_id] = entry
                self.tree.insert(
                    parent, "end", iid=item_id, text=entry.path,
                    values=(_human_size(entry.size),),
                )

    def _select_asset(self, _event: object | None = None) -> None:
        selection = self.tree.selection()
        entry = self.entries.get(selection[0]) if selection else None
        if entry is None or self.reader is None:
            return
        try:
            content = self.reader.read(
                entry.path, limit=native_preview_limit(entry.path, entry.size),
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not read asset", str(exc), parent=self)
            return
        digest = content.sha256 or "not calculated for a truncated preview"
        truncation = " · preview truncated to 8 MiB" if content.truncated else ""
        self.asset_title.set(entry.path)
        self.asset_meta.set(
            f"{entry.category} · {_human_size(content.size)} · "
            f"SHA-256 {digest}{truncation}"
        )
        if content.preview_kind == "image" and not content.truncated:
            self._show_image(content.data, entry.path)
        elif content.preview_kind == "text":
            self._show_text(decode_text_preview(content.data))
        else:
            suffix = Path(entry.path).suffix.lower()
            if suffix in NATIVE_ASSET_SUFFIXES:
                self.status.set(f"Inspecting native GTA asset: {entry.path}…")
                self.update_idletasks()
                project_root = Path(__file__).resolve().parents[2]
                edition = (
                    "Legacy" if self.scan and self.scan.edition_hints == ("legacy",)
                    else "Enhanced"
                )
                report = NativeAssetInspector(project_root).inspect_bytes(
                    entry.path, content.data, edition=edition,
                    truncated=content.truncated,
                )
                self.asset_meta.set(report.summary().replace("\n", " · "))
                if report.image_png:
                    self._show_image(report.image_png, entry.path)
                else:
                    body = report.summary()
                    if report.structured_text:
                        body += (
                            "\n\nStructured CodeWalker preview\n\n"
                            + report.structured_text[:2_000_000]
                        )
                    else:
                        body += (
                            "\n\nFirst bytes\n\n" + hex_preview(content.data)
                        )
                    self._show_text(body)
                self.status.set(f"Native asset inspected read-only: {entry.path}")
                return
            explanation = _BINARY_HELP.get(
                suffix,
                "Binary asset. The viewer displays its header but never executes or rewrites it.",
            )
            self._show_text(
                f"{explanation}\n\nFirst {min(len(content.data), 256)} bytes:\n\n" +
                hex_preview(content.data)
            )

    def _show_image(self, data: bytes, path: str) -> None:
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGBA")
                original = image.size
                image.thumbnail((690, 520), Image.Resampling.LANCZOS)
                rendered = image.copy()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            self._show_text(f"Image preview failed: {exc}\n\n{hex_preview(data)}")
            return
        self.text_preview.pack_forget()
        self._photo = ImageTk.PhotoImage(rendered)
        self.image_preview.configure(
            image=self._photo, text=f"{path}\n{original[0]} × {original[1]}",
            compound="top",
        )
        self.image_preview.pack(fill="both", expand=True)

    def _show_text(self, value: str) -> None:
        self.image_preview.pack_forget()
        self._photo = None
        self.text_preview.configure(state="normal")
        self.text_preview.delete("1.0", "end")
        self.text_preview.insert("1.0", value or "(empty file)")
        self.text_preview.configure(state="disabled")
        self.text_preview.pack(fill="both", expand=True)

    def _open_location(self) -> None:
        if self.source is None:
            return
        target = self.source if self.source.is_dir() else self.source.parent
        try:
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            else:  # pragma: no cover - desktop target is Windows
                import webbrowser
                webbrowser.open(target.as_uri())
        except OSError as exc:
            messagebox.showerror("Could not open location", str(exc), parent=self)

    def _export_inventory(self) -> None:
        if self.scan is None or self.source is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self, title="Export package asset inventory",
            defaultextension=".json", initialfile=f"{self.source.stem}-assets.json",
            filetypes=(("JSON", "*.json"),),
        )
        if not destination:
            return
        payload = {
            "source": str(self.source),
            "kind": self.scan.source_kind,
            "total_bytes": self.scan.total_bytes,
            "assets": [
                {
                    "path": entry.path, "size": entry.size,
                    "category": entry.category,
                    "preview": entry.preview_kind,
                }
                for entry in self.scan.entries
            ],
            "findings": [
                {
                    "severity": finding.severity, "code": finding.code,
                    "message": finding.message, "path": finding.path,
                }
                for finding in self.scan.findings
            ],
        }
        try:
            Path(destination).write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self.status.set(f"Exported inventory: {destination}")
