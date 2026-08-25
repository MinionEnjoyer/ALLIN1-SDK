"""OIV export form for the Vehicle Workbench.

The form is deliberately transport-only: the host supplies a preview/build
closure around an already staged Story build.  Changing any field invalidates
that preview so an artifact can never be created from stale UI evidence.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Mapping

from allin1_sdk.axle_oiv_export import (
    MODE_RUNTIME_ONLY,
    MODE_SELF_CONTAINED,
    MODE_VEHICLE_ONLY,
)
from allin1_sdk.ui_foundation import place_window


MODE_LABELS = {
    "Vehicle Only — Recommended": MODE_VEHICLE_ONLY,
    "Runtime Only": MODE_RUNTIME_ONLY,
    "Self-Contained — Advanced": MODE_SELF_CONTAINED,
}
TARGET_LABELS = {
    "Story Legacy — OIV 2.2": "story-legacy",
    "Story Enhanced — OpenRPF fallback": "story-enhanced",
}


@dataclass(frozen=True)
class VehicleOivForm:
    target_id: str
    mode: str
    package_name: str
    package_version: str
    author: str
    description: str
    dlc_pack_name: str
    include_documentation: bool
    icon_path: Path | None
    runtime_path: Path | None
    runtime_version: str
    output_path: Path
    confirm_self_contained: bool


class VehicleOivExportDialog(tk.Toplevel):
    """Preview-first export window with no direct game-write capability."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        model: str,
        default_name: str,
        default_target: str,
        on_preview: Callable[[VehicleOivForm], tuple[Mapping[str, object], Callable[[], Path]]],
    ) -> None:
        super().__init__(parent)
        self.title("Export Story package")
        place_window(self, preferred=(760, 720), minimum=(650, 580))
        self.transient(parent.winfo_toplevel())
        self._on_preview = on_preview
        self._build_action: Callable[[], Path] | None = None
        self._variables: list[tk.Variable] = []
        self._build(model, default_name, default_target)

    def _build(self, model: str, default_name: str, default_target: str) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer, text="Story package export", style="DialogTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "OIV is the installer around the same reviewed Story staging output. "
                "It never regenerates vehicle assets or modifies the game during export."
            ),
            foreground="#52635c", wraplength=700, justify="left",
        ).pack(fill="x", pady=(3, 9))

        form = ttk.LabelFrame(outer, text="Package", padding=9)
        form.pack(fill="x")
        target_label = next(
            (label for label, value in TARGET_LABELS.items() if value == default_target),
            next(iter(TARGET_LABELS)),
        )
        self.target = tk.StringVar(value=target_label)
        self.mode = tk.StringVar(value=next(iter(MODE_LABELS)))
        self.package_name = tk.StringVar(value=default_name or model)
        self.package_version = tk.StringVar(value="1.0.0")
        self.author = tk.StringVar(value="Vehicle Workbench author")
        self.description = tk.StringVar(
            value=f"Story Mode add-on vehicle package for {default_name or model}."
        )
        self.pack = tk.StringVar(value=self._safe_pack(model))
        self.include_docs = tk.BooleanVar(value=True)
        self.icon = tk.StringVar()
        self.runtime = tk.StringVar()
        self.runtime_version = tk.StringVar(value="1.0.0")
        self.output = tk.StringVar()
        self.confirm_self_contained = tk.BooleanVar(value=False)
        self._variables = [
            self.target, self.mode, self.package_name, self.package_version,
            self.author, self.description, self.pack, self.include_docs,
            self.icon, self.runtime, self.runtime_version, self.output,
            self.confirm_self_contained,
        ]

        rows = (
            ("Target edition", self.target, tuple(TARGET_LABELS), "combo"),
            ("Package mode", self.mode, tuple(MODE_LABELS), "combo"),
            ("Package name", self.package_name, (), "entry"),
            ("Version", self.package_version, (), "entry"),
            ("Author", self.author, (), "entry"),
            ("Description", self.description, (), "entry"),
            ("DLC pack name", self.pack, (), "entry"),
            ("Runtime version", self.runtime_version, (), "entry"),
        )
        for row, (label, variable, values, kind) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            if kind == "combo":
                widget = ttk.Combobox(
                    form, textvariable=variable, values=values, state="readonly",
                )
            else:
                widget = ttk.Entry(form, textvariable=variable)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=2)
        self.mode.trace_add("write", self._mode_changed)

        ttk.Label(form, text="Icon (128×128 PNG)").grid(row=8, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.icon).grid(
            row=8, column=1, sticky="ew", padx=(8, 5), pady=2,
        )
        ttk.Button(form, text="Browse…", command=self._browse_icon).grid(row=8, column=2)
        ttk.Label(form, text="Validated runtime profile JSON").grid(
            row=9, column=0, sticky="w", pady=2,
        )
        self.runtime_entry = ttk.Entry(form, textvariable=self.runtime)
        self.runtime_entry.grid(row=9, column=1, sticky="ew", padx=(8, 5), pady=2)
        self.runtime_button = ttk.Button(form, text="Browse…", command=self._browse_runtime)
        self.runtime_button.grid(row=9, column=2)
        ttk.Label(form, text="Output").grid(row=10, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.output).grid(
            row=10, column=1, sticky="ew", padx=(8, 5), pady=2,
        )
        ttk.Button(form, text="Browse…", command=self._browse_output).grid(row=10, column=2)
        ttk.Checkbutton(
            form, text="Include installation and dependency documentation",
            variable=self.include_docs,
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self.confirm_check = ttk.Checkbutton(
            form,
            text=(
                "I understand self-contained export may replace a newer shared axle runtime"
            ),
            variable=self.confirm_self_contained,
        )
        self.confirm_check.grid(row=12, column=0, columnspan=3, sticky="w")
        form.columnconfigure(1, weight=1)

        preview_frame = ttk.LabelFrame(
            outer, text="Installation preview", padding=8,
        )
        preview_frame.pack(fill="both", expand=True, pady=(9, 0))
        self.preview = tk.Text(
            preview_frame, wrap="word", relief="flat", background="#f4f7f5",
            foreground="#26332e", padx=8, pady=8,
        )
        scroll = ttk.Scrollbar(preview_frame, command=self.preview.yview)
        self.preview.configure(yscrollcommand=scroll.set)
        self.preview.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._show_preview_text(
            "Choose an output and preview the complete install plan. Blocking "
            "validation findings prevent export."
        )

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(9, 0))
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")
        self.export_button = ttk.Button(
            actions, text="Export package", state="disabled", command=self._export,
        )
        self.export_button.pack(side="right", padx=(0, 6))
        ttk.Button(
            actions, text="Preview installation", style="Accent.TButton",
            command=self._preview,
        ).pack(side="right", padx=(0, 6))
        for variable in self._variables:
            variable.trace_add("write", self._invalidate)
        self._mode_changed()

    @staticmethod
    def _safe_pack(model: str) -> str:
        value = "".join(char if char.isalnum() or char == "_" else "_" for char in model.casefold())
        return f"vwb_{value.strip('_') or 'vehicle'}"[:64]

    def _mode_changed(self, *_args) -> None:
        needs_runtime = MODE_LABELS.get(self.mode.get()) in {
            MODE_RUNTIME_ONLY, MODE_SELF_CONTAINED,
        }
        state = "normal" if needs_runtime else "disabled"
        self.runtime_entry.configure(state=state)
        self.runtime_button.configure(state=state)
        self.confirm_check.configure(
            state="normal" if MODE_LABELS.get(self.mode.get()) == MODE_SELF_CONTAINED
            else "disabled"
        )

    def _invalidate(self, *_args) -> None:
        self._build_action = None
        if hasattr(self, "export_button"):
            self.export_button.configure(state="disabled")

    def _browse_icon(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Select OIV icon", filetypes=(("PNG image", "*.png"),),
        )
        if path:
            self.icon.set(path)

    def _browse_runtime(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Select validated axle runtime profile",
            filetypes=(("Runtime profile", "*.json"),),
        )
        if path:
            self.runtime.set(path)

    def _browse_output(self) -> None:
        enhanced = TARGET_LABELS.get(self.target.get()) == "story-enhanced"
        extension = ".zip" if enhanced else ".oiv"
        path = filedialog.asksaveasfilename(
            parent=self, title="Export Story package", defaultextension=extension,
            filetypes=(("OpenRPF-ready ZIP", "*.zip"),) if enhanced else (("OIV package", "*.oiv"),),
        )
        if path:
            self.output.set(path)

    def _values(self) -> VehicleOivForm:
        icon = Path(self.icon.get()).expanduser() if self.icon.get().strip() else None
        runtime = Path(self.runtime.get()).expanduser() if self.runtime.get().strip() else None
        output = self.output.get().strip()
        if not output:
            raise ValueError("Select an output package path")
        return VehicleOivForm(
            target_id=TARGET_LABELS[self.target.get()],
            mode=MODE_LABELS[self.mode.get()],
            package_name=self.package_name.get().strip(),
            package_version=self.package_version.get().strip(),
            author=self.author.get().strip(),
            description=self.description.get().strip(),
            dlc_pack_name=self.pack.get().strip(),
            include_documentation=self.include_docs.get(),
            icon_path=icon,
            runtime_path=runtime,
            runtime_version=self.runtime_version.get().strip(),
            output_path=Path(output).expanduser(),
            confirm_self_contained=self.confirm_self_contained.get(),
        )

    def _preview(self) -> None:
        try:
            preview, action = self._on_preview(self._values())
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._show_preview_text(f"BLOCKED\n\n{exc}")
            self.export_button.configure(state="disabled")
            return
        lines = [
            f"Target: {preview.get('target', '')}",
            f"Edition: {preview.get('edition', '')}",
            f"Mode: {preview.get('mode', '')}",
            f"Asset format: {preview.get('asset_format', '')}",
            "",
            "Files added:",
            *(f"  + {item}" for item in preview.get("files_added", [])),
            "Files replaced:",
            *(f"  ! {item}" for item in preview.get("files_replaced", [])),
            "Archives modified:",
            *(f"  * {item}" for item in preview.get("archives_modified", [])),
            "XML entries added:",
            *(f"  + {item}" for item in preview.get("xml_entries_added", [])),
            "Dependencies:",
            *(f"  - {item.get('id')} (bundled: {item.get('bundled', False)})" for item in preview.get("dependencies", [])),
            "Warnings:",
            *(f"  - {item}" for item in preview.get("warnings", [])),
        ]
        self._show_preview_text("\n".join(lines))
        self._build_action = action
        self.export_button.configure(state="normal")

    def _export(self) -> None:
        if self._build_action is None:
            return
        try:
            output = self._build_action()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            messagebox.showerror("Story package export failed", str(exc), parent=self)
            return
        messagebox.showinfo(
            "Story package exported",
            f"Verified package written to:\n{output}", parent=self,
        )
        self.destroy()

    def _show_preview_text(self, value: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", value)
        self.preview.configure(state="disabled")
