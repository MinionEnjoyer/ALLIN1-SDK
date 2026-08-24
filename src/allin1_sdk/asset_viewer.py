"""Package asset browser and guarded native-resource authoring workspace."""

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
    NATIVE_XML_IMPORT_SUFFIXES,
    NativeAssetInspector,
    native_preview_limit,
)
from allin1_sdk.help_center import HelpCenterDialog
from allin1_sdk.texture_editor import TextureDictionaryEditorFrame
from allin1_sdk.ui_foundation import place_window


def _human_size(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


_BINARY_HELP = {
    ".rpf": "Rockstar archive. Inventory is shown, but nested entries require the Enhanced-aware RPF toolchain.",
    ".ytd": "Rockstar texture dictionary. Export a native workspace, then use the embedded YTD texture editor for catalog, preview, import, and rebuild validation.",
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
    ".awc": (
        "Rockstar audio wave container. Select the matching GTA installation to "
        "decrypt its stream table and export editable WAV dependencies."
    ),
    ".rel": "Rockstar audio relationship data.",
    ".dll": "Compiled .NET/native library. The viewer does not execute package code.",
    ".asi": "Compiled ScriptHook plug-in. The viewer does not execute package code.",
}


class AssetViewerDialog(ttk.Frame):
    """Browse package assets in the SDK shell or a compatibility window."""

    def __init__(
        self, parent: tk.Misc, source: str | Path | None = None,
        scan: PackageScan | None = None,
        *, installation_roots: tuple[Path, ...] = (), embedded: bool = False,
        on_help=None, on_close=None,
    ) -> None:
        self._window: tk.Toplevel | None = None
        self._on_help = on_help
        self._on_close = on_close
        self.installation_roots = tuple(
            Path(root).expanduser().resolve() for root in installation_roots
        )
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 Package Asset Viewer")
            place_window(
                self._window, preferred=(1180, 780), minimum=(900, 620),
            )
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.source: Path | None = None
        self.scan: PackageScan | None = None
        self.reader: PackageAssetReader | None = None
        self.entries: dict[str, PackageEntry] = {}
        self.selected_entry: PackageEntry | None = None
        self.action_menus: list[tk.Menu] = []
        self.native_action_menus: list[tk.Menu] = []
        self.package_action_buttons: list[ttk.Button] = []
        self.native_action_buttons: list[ttk.Button] = []
        self._texture_editor: TextureDictionaryEditorFrame | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._build()
        if source is not None:
            self._load_source(Path(source), scan)

    def _build(self) -> None:
        menu = tk.Menu(self, tearoff=False)
        file_menu = self._open_menu(menu)
        file_menu.add_separator()
        file_menu.add_command(label="Close", command=self._close_panel)
        menu.add_cascade(label="File", menu=file_menu)
        menu.add_cascade(label="Package", menu=self._action_menu(menu))
        menu.add_cascade(label="Native authoring", menu=self._native_menu(menu))
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="Asset Viewer Help", accelerator="F1",
            command=self._show_help,
        )
        menu.add_cascade(label="Help", menu=help_menu)
        if self._window is not None:
            self._window.configure(menu=menu)
            self._window.bind("<F1>", lambda _event: self._show_help())

        outer = ttk.Frame(self, padding=16)
        self.viewer_surface = outer
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
                "CodeWalker XML, texture contact sheets, and manifest-backed editable native "
                "workspaces when supported. Package code is never executed."
            ),
            wraplength=900, justify="left", foreground="#52635c",
        ).pack(anchor="w", pady=(3, 12))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(
            toolbar, text="PACKAGE", style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 7))
        ttk.Menubutton(
            toolbar, text="Open package", menu=self._open_menu(toolbar),
            style="Accent.TButton",
        ).pack(side="left")
        ttk.Menubutton(
            toolbar, text="Package tools", menu=self._action_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        ttk.Separator(toolbar, orient="vertical").pack(
            side="left", fill="y", padx=12, pady=3,
        )
        ttk.Label(
            toolbar, text="NATIVE AUTHORING", style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 7))
        self.export_native_button = ttk.Button(
            toolbar, text="Export selected for editing…",
            command=self._export_native_workspace, state="disabled",
        )
        self.export_native_button.pack(side="left")
        self.native_action_buttons.append(self.export_native_button)
        ttk.Menubutton(
            toolbar, text="Workspace tools", menu=self._native_menu(toolbar),
        ).pack(side="left", padx=(7, 0))
        self.status = tk.StringVar(
            value=(
                "Open a package to inspect its files, or use Workspace tools to "
                "continue an existing native-asset workspace."
            )
        )
        ttk.Label(
            outer, textvariable=self.status, foreground="#52635c",
            wraplength=900, justify="left",
        ).pack(fill="x", anchor="w", pady=(0, 10))

        context_actions = ttk.Frame(outer)
        context_actions.pack(fill="x", pady=(0, 10))
        ttk.Label(
            context_actions, text="AVAILABLE FOR THIS PACKAGE",
            style="FieldLabel.TLabel",
        ).pack(side="left", padx=(0, 8))
        self.export_inventory_button = ttk.Button(
            context_actions, text="Export inventory…",
            command=self._export_inventory, state="disabled",
        )
        self.export_inventory_button.pack(side="left")
        self.open_location_button = ttk.Button(
            context_actions, text="Open package folder",
            command=self._open_location, state="disabled",
        )
        self.open_location_button.pack(side="left", padx=(7, 0))
        self.package_action_buttons.extend((
            self.export_inventory_button, self.open_location_button,
        ))

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
        self.search_entry = ttk.Entry(search_row, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
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
        self.asset_xscroll = ttk.Scrollbar(
            tree_row, orient="horizontal", command=self.tree.xview,
        )
        self.tree.configure(
            yscrollcommand=scroll.set, xscrollcommand=self.asset_xscroll.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.asset_xscroll.grid(row=1, column=0, sticky="ew")
        tree_row.rowconfigure(0, weight=1)
        tree_row.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._select_asset)
        self.tree.bind("<Return>", self._activate_selected_asset)

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
        self._install_filter_shortcuts()

    def _install_filter_shortcuts(self) -> None:
        """Keep filter shortcuts local when the viewer is embedded in the SDK shell."""
        tag = f"AssetViewerFilter:{id(self)}"
        self.bind_class(tag, "<Control-f>", self._focus_search)
        self.bind_class(tag, "<Escape>", self._clear_search)
        pending = [self]
        while pending:
            widget = pending.pop()
            tags = widget.bindtags()
            if tag not in tags:
                widget.bindtags((tags[0], tag, *tags[1:]))
            pending.extend(widget.winfo_children())

    def _focus_search(self, _event: object | None = None) -> str:
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _clear_search(self, _event: object | None = None) -> str:
        self.search.set("")
        return "break"

    def _open_menu(self, parent: tk.Misc) -> tk.Menu:
        """Return package-source choices only.

        Native workspace operations intentionally live in their own menu so
        opening content and authoring a rebuilt asset are never conflated.
        """
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(label="Open package folder…", command=self._choose_folder)
        menu.add_command(label="Open package archive…", command=self._choose_archive)
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

    def _native_menu(self, parent: tk.Misc) -> tk.Menu:
        """Return native-workspace authoring commands."""
        menu = tk.Menu(parent, tearoff=False)
        menu.add_command(
            label="Export selected asset as editable workspace…",
            command=self._export_native_workspace, state="disabled",
        )
        menu.add_separator()
        menu.add_command(
            label="Build verified asset from workspace…",
            command=self._build_native_workspace,
        )
        menu.add_command(
            label="Open YTD texture workspace…", command=self._open_texture_workspace,
        )
        self.native_action_menus.append(menu)
        return menu

    def _set_package_actions(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.action_menus:
            menu.entryconfigure("Export inventory…", state=state)
            menu.entryconfigure("Open package location", state=state)
        for button in self.package_action_buttons:
            button.configure(state=state)

    def _set_native_action(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for menu in self.native_action_menus:
            menu.entryconfigure(
                "Export selected asset as editable workspace…", state=state,
            )
        for button in self.native_action_buttons:
            button.configure(state=state)

    def _show_help(self) -> None:
        if self._on_help is not None:
            self._on_help("asset-viewer")
        else:
            HelpCenterDialog(self, initial_topic="asset-viewer")

    def _close_panel(self) -> None:
        if self._on_close is not None:
            self._on_close()
        elif self._window is not None:
            self._window.destroy()
        else:
            self.destroy()

    def open_source(
        self, source: str | Path, scan: PackageScan | None = None,
    ) -> None:
        """Load a package into an existing embedded asset workspace."""
        self._load_source(Path(source), scan)

    def select_asset(self, path: str) -> bool:
        """Select one exact package member after another workspace routes to it."""
        self.search.set("")
        match = next((
            item_id for item_id, entry in self.entries.items()
            if entry.path.casefold() == path.casefold()
        ), None)
        if match is None:
            return False
        self.tree.selection_set(match)
        self.tree.focus(match)
        self.tree.see(match)
        self._select_asset()
        return True

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
        self.selected_entry = None
        self._set_native_action(False)
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
        selected_path = self.selected_entry.path if self.selected_entry else None
        self.tree.delete(*self.tree.get_children())
        self.entries.clear()
        if self.scan is None:
            self._clear_asset_selection("Open a package to browse its assets.")
            return
        query = self.search.get().strip().casefold()
        grouped: dict[str, list[PackageEntry]] = {}
        for entry in self.scan.entries:
            if query and query not in entry.path.casefold():
                continue
            grouped.setdefault(entry.category, []).append(entry)
        counter = 0
        restored: str | None = None
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
                if selected_path and entry.path.casefold() == selected_path.casefold():
                    restored = item_id
        if restored is not None:
            self.tree.selection_set(restored)
            self.tree.focus(restored)
            self.tree.see(restored)
            return
        if query:
            message = (
                f"No package assets match {self.search.get().strip()!r}."
                if counter == 0 else
                f"{counter:,} package asset(s) match. Select one to inspect it."
            )
        else:
            message = "Select an asset on the left to inspect it."
        self._clear_asset_selection(message)

    def _clear_asset_selection(self, message: str) -> None:
        self.selected_entry = None
        self._set_native_action(False)
        self.asset_title.set("No asset selected")
        self.asset_meta.set(message)
        self._show_text(message)

    def _activate_selected_asset(self, _event: object | None = None) -> str:
        self._select_asset()
        return "break"

    def _select_asset(self, _event: object | None = None) -> None:
        selection = self.tree.selection()
        entry = self.entries.get(selection[0]) if selection else None
        if entry is None or self.reader is None:
            self.selected_entry = None
            self._set_native_action(False)
            return
        self.selected_entry = entry
        self._set_native_action(
            Path(entry.path).suffix.casefold() in NATIVE_XML_IMPORT_SUFFIXES
        )
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
                report = NativeAssetInspector(
                    project_root, self._native_game_path(),
                ).inspect_bytes(
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

    def _export_native_workspace(self) -> None:
        entry = self.selected_entry
        if entry is None or self.reader is None:
            return
        parent = filedialog.askdirectory(
            parent=self, title="Select parent folder for editable native workspace",
        )
        if not parent:
            return
        destination = Path(parent) / f"{Path(entry.path).name}-workspace"
        try:
            content = self.reader.read(
                entry.path, limit=native_preview_limit(entry.path, entry.size),
            )
            if content.truncated:
                raise ValueError("Native asset exceeds the guarded editable-workspace limit")
            workspace = NativeAssetInspector(
                Path(__file__).resolve().parents[2], self._native_game_path(),
            ).export_workspace_bytes(
                entry.path, content.data, destination, edition=self._native_edition(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Native workspace export failed", str(exc), parent=self,
            )
            return
        self.status.set(f"Exported editable native workspace: {workspace}")

    def _build_native_workspace(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select native editing workspace",
        )
        if not selected:
            return
        try:
            manifest = json.loads(
                (Path(selected) / "native-workspace.json").read_text(encoding="utf-8")
            )
            name = str(manifest["source"]["name"])
        except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showerror(
                "Invalid native workspace", str(exc), parent=self,
            )
            return
        output = filedialog.asksaveasfilename(
            parent=self, title="Save rebuilt native asset", initialfile=name,
            defaultextension=Path(name).suffix,
            filetypes=((f"{Path(name).suffix.upper()} asset", f"*{Path(name).suffix}"),),
        )
        if not output:
            return
        try:
            asset, report = NativeAssetInspector(
                Path(__file__).resolve().parents[2], self._native_game_path(),
            ).build_workspace(selected, output)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Native workspace build failed", str(exc), parent=self)
            return
        self.status.set(f"Built and reparsed native asset: {asset} · {report.name}")

    def _open_texture_workspace(self) -> None:
        selected = filedialog.askdirectory(
            parent=self, title="Select editable native YTD workspace",
        )
        if not selected:
            return
        try:
            editor = TextureDictionaryEditorFrame(
                self, selected, Path(__file__).resolve().parents[2],
                on_close=self._close_texture_workspace,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open YTD workspace", str(exc), parent=self)
            return
        if self._texture_editor is not None:
            self._texture_editor.destroy()
        self.viewer_surface.pack_forget()
        self._texture_editor = editor
        editor.pack(fill="both", expand=True)

    def _close_texture_workspace(self) -> None:
        if self._texture_editor is not None:
            self._texture_editor.destroy()
            self._texture_editor = None
        self.viewer_surface.pack(fill="both", expand=True)

    def _native_edition(self) -> str:
        return (
            "Legacy" if self.scan and self.scan.edition_hints == ("legacy",)
            else "Enhanced"
        )

    def _native_game_path(self) -> Path | None:
        edition = self._native_edition()
        executable = "GTA5.exe" if edition == "Legacy" else "GTA5_Enhanced.exe"
        matching = tuple(
            root for root in self.installation_roots
            if (root / executable).is_file()
        )
        if len(matching) == 1:
            return matching[0]
        if len(self.installation_roots) == 1:
            return self.installation_roots[0]
        return None

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
