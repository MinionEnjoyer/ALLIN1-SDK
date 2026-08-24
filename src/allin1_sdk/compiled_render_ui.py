"""Compact compiled-render controls shared by model workbenches.

The panel deliberately owns presentation and validation only.  A workbench
supplies the backend/status callbacks so Blender discovery and rendering stay
outside Tk and can be exercised by the console and Agent API as well.
"""

from __future__ import annotations

import os
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Mapping


RenderSettings = dict[str, object]
BackendStatus = Mapping[str, object]


_RESOLUTIONS = {
    "Full HD · 1920 × 1080": (1920, 1080),
    "QHD · 2560 × 1440": (2560, 1440),
    "4K UHD · 3840 × 2160": (3840, 2160),
}
_ENGINES = ("Eevee · fast", "Cycles · path-traced")
_QUALITIES = ("Preview", "Production", "Maximum")
_LIGHT_RIGS = ("Studio", "Outdoor", "Dramatic", "Neutral")
_BACKGROUNDS = ("Studio dark", "Studio light", "Custom color")


class CompiledRenderPanel(tk.Frame):
    """An embedded render drawer that never creates another application window."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        backend_status: Callable[[], BackendStatus],
        on_render: Callable[[RenderSettings], bool | None],
        on_cancel: Callable[[], None],
        on_open_output: Callable[[Path], None] | None = None,
        on_locate_backend: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(
            parent, background="#101714", highlightbackground="#2a513a",
            highlightcolor="#2a513a", highlightthickness=1, borderwidth=0,
            width=352,
        )
        self.pack_propagate(False)
        self._backend_status = backend_status
        self._on_render = on_render
        self._on_cancel = on_cancel
        self._on_open_output = on_open_output
        self._on_locate_backend = on_locate_backend
        self._scene_available = False
        self._backend_available = False
        self._running = False
        self._advanced_visible = False
        self._last_output: Path | None = None

        self.engine = tk.StringVar(value=_ENGINES[0])
        self.quality = tk.StringVar(value="Production")
        self.resolution = tk.StringVar(value=next(iter(_RESOLUTIONS)))
        self.width = tk.StringVar(value="1920")
        self.height = tk.StringVar(value="1080")
        self.samples = tk.StringVar(value="128")
        self.device = tk.StringVar(value="Auto")
        self.light_rig = tk.StringVar(value="Studio")
        self.light_rotation = tk.DoubleVar(value=25.0)
        self.light_strength = tk.DoubleVar(value=1.0)
        self.background = tk.StringVar(value="Studio dark")
        self.background_color = tk.StringVar(value="#151b18")
        self.transparent = tk.BooleanVar(value=False)
        self.ground_plane = tk.BooleanVar(value=True)
        self.contact_shadows = tk.BooleanVar(value=True)
        self.output_path = tk.StringVar(value="")
        self.backend_name = tk.StringVar(value="Detecting render engine…")
        self.backend_detail = tk.StringVar(value="")
        self.progress_message = tk.StringVar(value="Ready to compile a still image.")
        self.advanced_label = tk.StringVar(value="Advanced  ▾")

        self._build()

    @staticmethod
    def _label(parent: tk.Misc, text: str, **options) -> tk.Label:
        return tk.Label(
            parent, text=text, background="#101714", foreground="#a9bbb1",
            font=("Segoe UI Semibold", 8), anchor="w", **options,
        )

    @staticmethod
    def _entry(parent: tk.Misc, variable: tk.Variable, *, width: int = 8) -> tk.Entry:
        return tk.Entry(
            parent, textvariable=variable, width=width, relief="flat",
            borderwidth=0, highlightthickness=1, highlightbackground="#31443a",
            highlightcolor="#2e9b55", background="#19241f", foreground="#edf5f0",
            insertbackground="#edf5f0", font=("Segoe UI", 9),
        )

    @staticmethod
    def _combo(
        parent: tk.Misc, variable: tk.StringVar, values: tuple[str, ...], *, width: int,
    ) -> ttk.Combobox:
        return ttk.Combobox(
            parent, textvariable=variable, values=values, state="readonly",
            width=width, takefocus=True,
        )

    @staticmethod
    def _flat_button(
        parent: tk.Misc, text: str, command, *, accent: bool = False, width: int = 0,
    ) -> tk.Button:
        background = "#238746" if accent else "#1a2921"
        active = "#2b9d54" if accent else "#294535"
        return tk.Button(
            parent, text=text, command=command, width=width,
            background=background, foreground="#ffffff" if accent else "#d9e7df",
            activebackground=active, activeforeground="#ffffff", relief="flat",
            borderwidth=0, highlightthickness=0, padx=9, pady=5,
            cursor="hand2", takefocus=True, font=("Segoe UI Semibold", 9),
        )

    def _build(self) -> None:
        header = tk.Frame(self, background="#14221a")
        header.pack(fill="x")
        heading = tk.Frame(header, background="#14221a")
        heading.pack(side="left", fill="x", expand=True, padx=(13, 4), pady=(8, 7))
        tk.Label(
            heading, text="COMPILED RENDER", background="#14221a",
            foreground="#f2f8f4", font=("Segoe UI Semibold", 10), anchor="w",
        ).pack(anchor="w")
        tk.Label(
            heading, text="Lighting, sampling, and a production image",
            background="#14221a", foreground="#87a393",
            font=("Segoe UI", 8), anchor="w",
        ).pack(anchor="w")
        close = tk.Button(
            header, text="×", command=self.hide, background="#14221a",
            foreground="#b6c8bd", activebackground="#294535",
            activeforeground="#ffffff", relief="flat", borderwidth=0,
            highlightthickness=0, padx=10, pady=6, cursor="hand2", takefocus=True,
            font=("Segoe UI", 13),
        )
        close.pack(side="right", anchor="n")
        self.close_button = close

        # Reserve the entire job footer before the settings body expands. This
        # keeps every action needed to start, cancel, or retrieve a render
        # inside the panel even when the embedded viewport is short.
        job_footer = tk.Frame(self, background="#101714")
        self.job_footer = job_footer
        job_footer.pack(side="bottom", fill="x", padx=13, pady=(0, 8))

        body = tk.Frame(self, background="#101714")
        self.settings_body = body
        body.pack(fill="both", expand=True, padx=13, pady=(10, 8))

        engine_grid = tk.Frame(body, background="#101714")
        self.engine_grid = engine_grid
        engine_grid.pack(fill="x", pady=(0, 7))
        engine_grid.columnconfigure(0, weight=2)
        engine_grid.columnconfigure(1, weight=1)
        self._label(engine_grid, "ENGINE").grid(
            row=0, column=0, sticky="ew", padx=(0, 4),
        )
        self._label(engine_grid, "QUALITY").grid(
            row=0, column=1, sticky="ew", padx=(4, 0),
        )
        self.engine_combo = self._combo(engine_grid, self.engine, _ENGINES, width=19)
        self.engine_combo.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 0))
        self.quality_combo = self._combo(
            engine_grid, self.quality, _QUALITIES, width=11,
        )
        self.quality_combo.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 0))
        self.quality_combo.bind("<<ComboboxSelected>>", self._quality_changed)

        grid = tk.Frame(body, background="#101714")
        self.basic_grid = grid
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        self._label(grid, "RESOLUTION").grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._label(grid, "LIGHTING").grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.resolution_combo = self._combo(
            grid, self.resolution, tuple(_RESOLUTIONS) + ("Custom",), width=19,
        )
        self.resolution_combo.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(2, 7))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._resolution_changed)
        self.light_combo = self._combo(grid, self.light_rig, _LIGHT_RIGS, width=13)
        self.light_combo.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 7))

        self.background_label = self._label(body, "BACKGROUND")
        self.background_label.pack(fill="x")
        background_row = tk.Frame(body, background="#101714")
        self.background_row = background_row
        background_row.pack(fill="x", pady=(2, 7))
        self.background_combo = self._combo(
            background_row, self.background, _BACKGROUNDS, width=18,
        )
        self.background_combo.pack(side="left", fill="x", expand=True)
        self.transparent_check = tk.Checkbutton(
            background_row, text="Transparent", variable=self.transparent,
            command=self._background_changed, background="#101714",
            foreground="#c4d3ca", activebackground="#101714",
            activeforeground="#ffffff", selectcolor="#1a2921", borderwidth=0,
            highlightthickness=0, takefocus=True, font=("Segoe UI", 8),
        )
        self.transparent_check.pack(side="right", padx=(8, 0))

        self.advanced_button = tk.Button(
            body, textvariable=self.advanced_label, command=self.toggle_advanced,
            anchor="w", background="#101714", foreground="#56b873",
            activebackground="#18271f", activeforeground="#76cf8e", relief="flat",
            borderwidth=0, highlightthickness=0, padx=0, pady=4, cursor="hand2",
            takefocus=True, font=("Segoe UI Semibold", 9),
        )
        self.advanced_button.pack(fill="x", pady=(0, 2))

        self.advanced = tk.Frame(body, background="#131d18")
        self.advanced.columnconfigure(0, weight=1)
        self.advanced.columnconfigure(1, weight=1)
        self.advanced.columnconfigure(2, weight=1)
        self._label(self.advanced, "WIDTH × HEIGHT").grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(3, 1),
        )
        dimensions = tk.Frame(self.advanced, background="#131d18")
        dimensions.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8)
        self.width_entry = self._entry(dimensions, self.width)
        self.width_entry.pack(side="left", fill="x", expand=True)
        tk.Label(
            dimensions, text="×", background="#131d18", foreground="#91a69a",
        ).pack(side="left", padx=5)
        self.height_entry = self._entry(dimensions, self.height)
        self.height_entry.pack(side="left", fill="x", expand=True)
        for entry in (self.width_entry, self.height_entry):
            entry.bind("<KeyRelease>", self._custom_dimensions_entered)

        self._label(self.advanced, "SAMPLES").grid(
            row=2, column=0, sticky="w", padx=8, pady=(3, 1),
        )
        self._label(self.advanced, "DEVICE").grid(
            row=2, column=1, sticky="w", padx=4, pady=(3, 1),
        )
        self._label(self.advanced, "BACKGROUND").grid(
            row=2, column=2, sticky="w", padx=(4, 8), pady=(3, 1),
        )
        self.samples_combo = self._combo(
            self.advanced, self.samples, ("32", "64", "128", "256", "512"), width=8,
        )
        self.samples_combo.grid(row=3, column=0, sticky="ew", padx=(8, 4))
        self.device_combo = self._combo(
            self.advanced, self.device, ("Auto", "GPU", "CPU"), width=8,
        )
        self.device_combo.grid(row=3, column=1, sticky="ew", padx=4)
        self.background_entry = self._entry(
            self.advanced, self.background_color, width=8,
        )
        self.background_entry.grid(row=3, column=2, sticky="ew", padx=(4, 8))

        self._label(self.advanced, "LIGHT ROTATION").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(3, 1),
        )
        self._label(self.advanced, "STRENGTH").grid(
            row=4, column=2, sticky="w", padx=(4, 8), pady=(3, 1),
        )
        self.rotation_entry = self._entry(
            self.advanced, self.light_rotation, width=12,
        )
        self.rotation_entry.grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=(8, 4), pady=(0, 3),
        )
        self.strength_entry = self._entry(
            self.advanced, self.light_strength, width=8,
        )
        self.strength_entry.grid(
            row=5, column=2, sticky="ew", padx=(4, 8), pady=(0, 3),
        )
        effects = tk.Frame(self.advanced, background="#131d18")
        effects.grid(row=6, column=0, columnspan=3, sticky="ew", padx=4)
        self.ground_check = tk.Checkbutton(
            effects, text="Ground plane", variable=self.ground_plane,
            background="#131d18", foreground="#c4d3ca",
            activebackground="#131d18", activeforeground="#ffffff",
            selectcolor="#1a2921", borderwidth=0, highlightthickness=0,
            takefocus=True, font=("Segoe UI", 8),
        )
        self.ground_check.pack(side="left")
        self.contact_shadow_check = tk.Checkbutton(
            effects, text="Contact shadows", variable=self.contact_shadows,
            background="#131d18", foreground="#c4d3ca",
            activebackground="#131d18", activeforeground="#ffffff",
            selectcolor="#1a2921", borderwidth=0, highlightthickness=0,
            takefocus=True, font=("Segoe UI", 8),
        )
        self.contact_shadow_check.pack(side="left", padx=(6, 0))

        self.output_label = self._label(job_footer, "OUTPUT")
        self.output_label.pack(fill="x")
        output_row = tk.Frame(job_footer, background="#101714")
        self.output_row = output_row
        output_row.pack(fill="x", pady=(2, 6))
        self.output_entry = self._entry(output_row, self.output_path, width=24)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.browse_button = self._flat_button(
            output_row, "…", self._browse_output, width=2,
        )
        self.browse_button.pack(side="left", padx=(5, 0))

        backend = tk.Frame(job_footer, background="#14221a")
        self.backend_card = backend
        backend.pack(fill="x", pady=(0, 8))
        self.backend_badge = tk.Label(
            backend, text="●", background="#14221a", foreground="#b88b36",
            font=("Segoe UI", 10),
        )
        self.backend_badge.pack(side="left", padx=(7, 5), pady=5)
        backend_text = tk.Frame(backend, background="#14221a")
        self.backend_text = backend_text
        backend_text.pack(side="left", fill="x", expand=True, pady=4)
        tk.Label(
            backend_text, textvariable=self.backend_name, background="#14221a",
            foreground="#e0ebe4", anchor="w", font=("Segoe UI Semibold", 8),
        ).pack(fill="x")
        tk.Label(
            backend_text, textvariable=self.backend_detail, background="#14221a",
            foreground="#8da397", anchor="w", justify="left", wraplength=285,
            font=("Segoe UI", 7),
        ).pack(fill="x")
        self.backend_refresh_button = tk.Button(
            backend, text="↻", command=self.refresh_backend_status,
            background="#14221a", foreground="#8fc9a1",
            activebackground="#294535", activeforeground="#ffffff", relief="flat",
            borderwidth=0, highlightthickness=0, padx=7, pady=4, cursor="hand2",
            takefocus=True, font=("Segoe UI", 10),
        )
        self.backend_refresh_button.pack(side="right")
        self.backend_refresh_button.pack_forget()
        self.backend_refresh_button.pack(side="right", before=self.backend_text)

        self.backend_actions = tk.Frame(backend, background="#14221a")
        self.locate_backend_button = self._flat_button(
            self.backend_actions, "Locate…", self._locate_backend,
        )
        self.locate_backend_button.configure(
            background="#14221a", activebackground="#294535", padx=4, pady=3,
            font=("Segoe UI Semibold", 8),
        )
        self.locate_backend_button.pack(side="left")
        self.get_backend_button = tk.Button(
            self.backend_actions, text="Get ↗", command=self._get_backend,
            background="#14221a", foreground="#55ba73",
            activebackground="#18271f", activeforeground="#78d292", relief="flat",
            borderwidth=0, highlightthickness=0, padx=4, pady=3, cursor="hand2",
            takefocus=True, font=("Segoe UI Semibold", 8),
        )
        self.get_backend_button.pack(side="left", padx=(5, 0))

        self.progress = ttk.Progressbar(job_footer, maximum=100, mode="determinate")
        self.progress.pack(fill="x")
        self.progress_label = tk.Label(
            job_footer, textvariable=self.progress_message, background="#101714",
            foreground="#8fa49a", anchor="w", justify="left", wraplength=320,
            font=("Segoe UI", 8),
        )
        self.progress_label.pack(fill="x", pady=(3, 7))

        actions = tk.Frame(job_footer, background="#101714")
        self.actions = actions
        actions.pack(side="bottom", fill="x")
        self.render_button = self._flat_button(
            actions, "Render image", self._request_render, accent=True,
        )
        self.render_button.pack(side="right")
        self.cancel_button = self._flat_button(actions, "Cancel", self._request_cancel)
        self.cancel_button.pack(side="right", padx=(0, 5))
        self.open_output_button = self._flat_button(
            actions, "Open output", self._open_output,
        )
        self.open_output_button.pack(side="left")
        self._update_action_states()

    def show(self, *, suggested_output: Path | None = None) -> None:
        if suggested_output is not None and not self.output_path.get().strip():
            self.output_path.set(str(suggested_output))
        self.refresh_backend_status()
        self.place(
            relx=1.0, rely=0.0, anchor="ne", relheight=1.0, width=352,
        )
        self.lift()
        self.engine_combo.focus_set()

    def hide(self) -> None:
        self.place_forget()

    def toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            for control in (
                self.engine_grid, self.basic_grid,
                self.background_label, self.background_row,
            ):
                control.pack_forget()
            self.advanced.pack(fill="x", after=self.advanced_button, pady=(0, 1))
            self.advanced_label.set("‹  Basic settings")
        else:
            self.advanced.pack_forget()
            self.engine_grid.pack(fill="x", before=self.advanced_button, pady=(0, 7))
            self.basic_grid.pack(fill="x", before=self.advanced_button)
            self.background_label.pack(fill="x", before=self.advanced_button)
            self.background_row.pack(
                fill="x", before=self.advanced_button, pady=(2, 7),
            )
            self.advanced_label.set("Advanced  ▾")

    def set_scene_available(self, available: bool) -> None:
        self._scene_available = bool(available)
        self._update_action_states()

    def refresh_backend_status(self) -> None:
        try:
            status = dict(self._backend_status())
        except (OSError, RuntimeError, ValueError) as exc:
            status = {
                "available": False,
                "name": "Render engine unavailable",
                "detail": str(exc),
            }
        self._backend_available = bool(status.get("available"))
        self.backend_name.set(str(status.get("name") or "Render engine unavailable"))
        detail = str(status.get("detail") or "")
        device = str(status.get("device") or "")
        self.backend_detail.set(" · ".join(value for value in (detail, device) if value))
        self.backend_badge.configure(
            foreground="#50bd70" if self._backend_available else "#d39a3d",
        )
        if self._backend_available:
            self.backend_actions.pack_forget()
            if not self.backend_refresh_button.winfo_manager():
                self.backend_refresh_button.pack(
                    side="right", before=self.backend_text,
                )
        else:
            self.backend_refresh_button.pack_forget()
            if not self.backend_actions.winfo_manager():
                self.backend_actions.pack(
                    side="right", before=self.backend_text, padx=(2, 0),
                )
        self._update_action_states()

    def set_progress(self, fraction: float, message: str) -> None:
        self.progress.configure(value=max(0.0, min(1.0, fraction)) * 100.0)
        self.progress_message.set(message)

    def set_running(self, running: bool, *, message: str | None = None) -> None:
        self._running = bool(running)
        if message is not None:
            self.progress_message.set(message)
        self._update_action_states()

    def set_output(self, output: Path | None, *, message: str | None = None) -> None:
        self._last_output = output
        if output is not None:
            self.output_path.set(str(output))
            self.progress.configure(value=100.0)
        if message is not None:
            self.progress_message.set(message)
        self._running = False
        self._update_action_states()

    def collect_settings(self) -> RenderSettings:
        try:
            width = int(self.width.get())
            height = int(self.height.get())
            samples = int(self.samples.get())
            rotation = float(self.light_rotation.get())
            strength = float(self.light_strength.get())
        except ValueError as exc:
            raise ValueError("Resolution, samples, and lighting must be numeric.") from exc
        if not (256 <= width <= 8_192 and 256 <= height <= 8_192):
            raise ValueError("Resolution must be between 256 and 8,192 pixels per side.")
        if samples not in {32, 64, 128, 256, 512}:
            raise ValueError("Choose a supported sample count.")
        raw_output = self.output_path.get().strip()
        if not raw_output:
            raise ValueError("Choose an output file.")
        output = Path(raw_output).expanduser()
        if output.suffix.casefold() != ".png":
            raise ValueError("Compiled renders use PNG output.")
        color = self.background_color.get().strip()
        if len(color) != 7 or not color.startswith("#"):
            raise ValueError("Custom background must be a #RRGGBB color.")
        try:
            int(color[1:], 16)
        except ValueError as exc:
            raise ValueError("Custom background must be a #RRGGBB color.") from exc
        return {
            "width": width,
            "height": height,
            "engine": "cycles" if self.engine.get().startswith("Cycles") else "eevee",
            "quality": self.quality.get().casefold(),
            "samples": samples,
            "device": self.device.get().casefold(),
            "light_rig": self.light_rig.get().casefold(),
            "light_rotation_deg": rotation,
            "light_strength": strength,
            "background": self.background.get().casefold().replace(" ", "_"),
            "background_color": color.upper(),
            "transparent": bool(self.transparent.get()),
            "ground_plane": bool(self.ground_plane.get()),
            "contact_shadows": bool(self.contact_shadows.get()),
            "output_path": output,
        }

    def _request_render(self) -> None:
        try:
            settings = self.collect_settings()
        except ValueError as exc:
            self.progress_message.set(str(exc))
            return
        accepted = self._on_render(settings)
        if accepted is not False:
            self.progress.configure(value=0.0)
            self.set_running(True, message="Preparing compiled render…")

    def _request_cancel(self) -> None:
        self._on_cancel()
        self.progress_message.set("Cancelling after the current render stage…")
        self.cancel_button.configure(state="disabled")

    def _open_output(self) -> None:
        path = self._last_output
        if path is None or not path.exists():
            return
        if self._on_open_output is not None:
            self._on_open_output(path)
        elif hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]

    def _browse_output(self) -> None:
        current = Path(self.output_path.get()).expanduser() if self.output_path.get() else None
        selected = filedialog.asksaveasfilename(
            parent=self, title="Save compiled render",
            initialdir=str(current.parent) if current else None,
            initialfile=current.name if current else "vehicle-render.png",
            defaultextension=".png",
            filetypes=(("PNG image", "*.png"),),
        )
        if selected:
            self.output_path.set(selected)

    def _locate_backend(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self, title="Locate Blender",
            filetypes=(("Blender executable", "blender.exe"), ("Executable", "*.exe")),
        )
        if not selected:
            return
        path = Path(selected)
        if self._on_locate_backend is not None:
            self._on_locate_backend(path)
        self.refresh_backend_status()

    @staticmethod
    def _get_backend() -> None:
        webbrowser.open("https://www.blender.org/download/", new=2)

    def _resolution_changed(self, _event: object | None = None) -> None:
        dimensions = _RESOLUTIONS.get(self.resolution.get())
        if dimensions is not None:
            self.width.set(str(dimensions[0]))
            self.height.set(str(dimensions[1]))
        elif not self._advanced_visible:
            self.toggle_advanced()

    def _quality_changed(self, _event: object | None = None) -> None:
        defaults = {"Preview": "32", "Production": "128", "Maximum": "512"}
        self.samples.set(defaults[self.quality.get()])

    def _custom_dimensions_entered(self, _event: object | None = None) -> None:
        expected = _RESOLUTIONS.get(self.resolution.get())
        if expected != (self._safe_int(self.width.get()), self._safe_int(self.height.get())):
            self.resolution.set("Custom")

    def _background_changed(self) -> None:
        state = "disabled" if self.transparent.get() else "normal"
        self.background_combo.configure(state=state if state == "disabled" else "readonly")

    def _update_action_states(self) -> None:
        ready = self._backend_available and self._scene_available and not self._running
        self.render_button.configure(state="normal" if ready else "disabled")
        self.cancel_button.configure(state="normal" if self._running else "disabled")
        self.open_output_button.configure(
            state="normal"
            if self._last_output is not None and self._last_output.exists() else "disabled",
        )
        state = "disabled" if self._running else "normal"
        for control in (
            self.engine_combo, self.quality_combo, self.resolution_combo, self.light_combo,
            self.background_combo, self.transparent_check, self.advanced_button,
            self.samples_combo, self.device_combo, self.width_entry, self.height_entry,
            self.background_entry, self.rotation_entry, self.strength_entry,
            self.ground_check, self.contact_shadow_check,
            self.output_entry, self.browse_button, self.backend_refresh_button,
        ):
            if isinstance(control, ttk.Combobox):
                disabled = self._running or (
                    control is self.background_combo and self.transparent.get()
                )
                control.configure(state="disabled" if disabled else "readonly")
            else:
                control.configure(state=state)

    @staticmethod
    def _safe_int(value: str) -> int | None:
        try:
            return int(value)
        except ValueError:
            return None


__all__ = ["BackendStatus", "CompiledRenderPanel", "RenderSettings"]
