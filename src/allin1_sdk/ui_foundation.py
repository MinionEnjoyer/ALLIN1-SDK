"""Shared desktop-window behavior for the ALLIN1 SDK.

The SDK is still a Tk application, so a small amount of explicit Windows/DPI
handling is needed before any widgets are created.  Keeping it here prevents
each compatibility window from inventing its own sizing rules.
"""

from __future__ import annotations

import os
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


# Keep the desktop shell on the same restrained palette as the Launcher. The
# specialist workspaces inherit these named styles, so they can stay dense
# without each inventing a separate visual language.
BRAND_GREEN = "#2d9c50"
BRAND_DARK_GREEN = "#1f7f42"
BRAND_DEEP_GREEN = "#176b36"
BODY_BACKGROUND = "#f4f7f5"
SURFACE_BACKGROUND = "#ffffff"
PRIMARY_TEXT = "#173d32"
MUTED_TEXT = "#52635c"
SUCCESS_TEXT = "#18753a"
WARNING_TEXT = "#9a6700"
ERROR_TEXT = "#b42318"


@dataclass(frozen=True)
class ShellStatusPresentation:
    """One compact, non-modal activity-bar presentation."""

    tone: str
    glyph: str
    label_style: str
    indicator_style: str


def shell_status_presentation(message: str) -> ShellStatusPresentation:
    """Classify shell activity without coupling operations to Tk widgets."""

    normalized = str(message).strip().casefold()
    diagnostic = re.sub(r"\b0\s+errors?\b", "", normalized)
    diagnostic = re.sub(r"\b0\s+warnings?\b", "", diagnostic)
    if any(token in diagnostic for token in (
        "failed", "error", "could not", "invalid", "blocked", "refused",
    )):
        tone = "error"
    elif any(token in diagnostic for token in (
        "warning", "not found", "required", "no packages", "select a ",
    )):
        tone = "warning"
    elif normalized.endswith("…") or any(token in normalized for token in (
        "loading", "auditing", "inspecting", "compiling", "working",
    )):
        tone = "busy"
    elif any(token in normalized for token in (
        "written", "wrote ", "exported", "copied", "refreshed", "equivalent",
        "ready", "loaded", "compiled",
    )):
        tone = "success"
    else:
        tone = "info"
    glyph = "◌" if tone == "busy" else "●"
    return ShellStatusPresentation(
        tone=tone,
        glyph=glyph,
        label_style=f"Activity.{tone.title()}.TLabel",
        indicator_style=f"ActivityDot.{tone.title()}.TLabel",
    )


def configure_sdk_style(root: tk.Misc) -> None:
    """Configure the shared Launcher-aligned SDK desktop style catalog."""

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*tearOff", False)
    root.option_add("*Menu.font", ("Segoe UI", 10))
    root.option_add("*Menu.background", SURFACE_BACKGROUND)
    root.option_add("*Menu.foreground", "#26332e")
    root.option_add("*Menu.activeBackground", "#dcefe3")
    root.option_add("*Menu.activeForeground", BRAND_DEEP_GREEN)
    try:
        root.configure(background=BODY_BACKGROUND)
    except tk.TclError:
        pass

    style.configure(".", font=("Segoe UI", 10), foreground=PRIMARY_TEXT)
    style.configure("TFrame", background=BODY_BACKGROUND)
    style.configure("TLabel", background=BODY_BACKGROUND, foreground="#24332d")
    style.configure("Surface.TFrame", background=SURFACE_BACKGROUND)
    style.configure("Workspace.TFrame", background=BODY_BACKGROUND)
    style.configure("StatusBar.TFrame", background=SURFACE_BACKGROUND)
    style.configure("TButton", padding=(11, 7))
    style.configure("Quiet.TButton", padding=(10, 7))
    style.configure("Danger.TButton", foreground="#9a3412", padding=(10, 7))
    style.configure("TEntry", padding=(7, 6))
    style.configure("TCombobox", padding=(6, 5))
    style.configure("TCheckbutton", padding=(0, 2))
    style.configure("TLabelframe", background=SURFACE_BACKGROUND, bordercolor="#d4ddd9")
    style.configure(
        "TLabelframe.Label", background=BODY_BACKGROUND, foreground=BRAND_DARK_GREEN,
        font=("Segoe UI Semibold", 10),
    )
    style.configure(
        "Accent.TButton", background=BRAND_GREEN, foreground="white",
        font=("Segoe UI Semibold", 10), padding=(13, 8),
    )
    style.map(
        "Accent.TButton",
        background=[("active", BRAND_DARK_GREEN), ("disabled", "#c8d4cc")],
        foreground=[("disabled", "#66756e")],
    )
    style.configure(
        "Accent.TMenubutton", background=BRAND_GREEN, foreground="white",
        font=("Segoe UI Semibold", 10), padding=(13, 8),
    )
    style.map(
        "Accent.TMenubutton",
        background=[("active", BRAND_DARK_GREEN), ("disabled", "#c8d4cc")],
        foreground=[("disabled", "#66756e")],
    )
    style.configure("Quiet.TMenubutton", padding=(11, 7))
    style.configure(
        "Nav.TButton", anchor="w", padding=(16, 11), relief="flat",
        background="#eef3f0", foreground="#3c5048",
    )
    style.map(
        "Nav.TButton",
        background=[("active", "#e2ebe6"), ("focus", "#e2ebe6")],
        foreground=[("disabled", "#84928c")],
    )
    style.configure(
        "NavSelected.TButton", anchor="w", padding=(16, 11), relief="flat",
        background="#dcefe3", foreground=BRAND_DEEP_GREEN,
        font=("Segoe UI Semibold", 10),
    )
    style.map(
        "NavSelected.TButton",
        background=[("active", "#d2e8da"), ("focus", "#c9e4d3")],
    )
    style.configure(
        "DialogTitle.TLabel", font=("Segoe UI Semibold", 17),
        foreground=PRIMARY_TEXT,
    )
    style.configure(
        "PageTitle.TLabel", font=("Segoe UI Semibold", 15),
        foreground=PRIMARY_TEXT,
    )
    style.configure("PageIntro.TLabel", foreground=MUTED_TEXT)
    style.configure(
        "Section.TLabel", font=("Segoe UI Semibold", 11), foreground=PRIMARY_TEXT,
    )
    style.configure(
        "Treeview", rowheight=28, font=("Segoe UI", 10),
        background=SURFACE_BACKGROUND, fieldbackground=SURFACE_BACKGROUND,
    )
    style.configure(
        "Treeview.Heading", font=("Segoe UI Semibold", 9), padding=(7, 7),
        foreground="#26332e",
    )
    style.map(
        "Treeview",
        background=[("selected", BRAND_DEEP_GREEN)],
        foreground=[("selected", "#ffffff")],
    )
    style.configure("TNotebook", background=BODY_BACKGROUND, borderwidth=0)
    style.configure(
        "TNotebook.Tab", font=("Segoe UI Semibold", 10), padding=(14, 8),
        foreground="#646e69",
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", SURFACE_BACKGROUND), ("active", "#e6efe9")],
        foreground=[("selected", BRAND_DEEP_GREEN)],
    )
    style.configure(
        "FieldLabel.TLabel", font=("Segoe UI Semibold", 9), foreground=MUTED_TEXT,
    )
    style.configure("Muted.TLabel", foreground=MUTED_TEXT)
    style.configure("Success.TLabel", foreground=BRAND_DEEP_GREEN)
    style.configure("Warning.TLabel", foreground="#8a5a00")
    style.configure("Error.TLabel", foreground="#a52a2a")
    style.configure(
        "Link.TButton", relief="flat", borderwidth=0, padding=(4, 3),
        background=BODY_BACKGROUND, foreground=BRAND_DEEP_GREEN,
        font=("Segoe UI Semibold", 9, "underline"),
    )
    style.map(
        "Link.TButton",
        foreground=[("active", "#0e5228"), ("focus", "#0e5228")],
        background=[("active", "#e2ebe6"), ("focus", "#e2ebe6")],
    )
    style.configure(
        "HeaderLink.TButton", relief="flat", borderwidth=0, padding=(4, 2),
        background=SURFACE_BACKGROUND, foreground=BRAND_DEEP_GREEN,
        font=("Segoe UI Semibold", 9, "underline"),
    )
    style.map(
        "HeaderLink.TButton",
        foreground=[("active", "#0e5228"), ("focus", "#0e5228")],
        background=[("active", "#e2ebe6"), ("focus", "#e2ebe6")],
    )
    for tone, color in (
        ("Info", MUTED_TEXT), ("Busy", "#2563a3"),
        ("Success", SUCCESS_TEXT), ("Warning", WARNING_TEXT),
        ("Error", ERROR_TEXT),
    ):
        style.configure(
            f"Activity.{tone}.TLabel", background=SURFACE_BACKGROUND,
            foreground=color,
        )
        style.configure(
            f"ActivityDot.{tone}.TLabel", background=SURFACE_BACKGROUND,
            foreground=color, font=("Segoe UI Semibold", 10),
        )
    style.configure(
        "StatusHint.TLabel", background=SURFACE_BACKGROUND,
        foreground="#6d7a74", font=("Segoe UI", 9),
    )


def enable_windows_dpi_awareness() -> None:
    """Opt into the best DPI-awareness mode available on this Windows build."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) in (0, -2147024891):
                return
        except (AttributeError, OSError):
            pass
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except (ImportError, OSError):
        pass


def configure_tk_scaling(root: tk.Misc) -> float:
    """Use the monitor's reported DPI while keeping pathological values bounded."""
    try:
        scale = float(root.winfo_fpixels("1i")) / 72.0
        scale = min(max(scale, 1.0), 2.5)
        root.tk.call("tk", "scaling", scale)
        return scale
    except (tk.TclError, TypeError, ValueError):
        return 1.0


def fitted_geometry(
    preferred_width: int,
    preferred_height: int,
    screen_width: int,
    screen_height: int,
    *,
    margin: int = 24,
    taskbar_allowance: int = 56,
) -> tuple[int, int, int, int]:
    """Return a centered geometry that stays within the visible desktop."""
    screen_width = max(int(screen_width), 1)
    screen_height = max(int(screen_height), 1)
    usable_width = max(screen_width - margin * 2, 1)
    usable_height = max(screen_height - margin - taskbar_allowance, 1)
    width = max(min(int(preferred_width), usable_width), 1)
    height = max(min(int(preferred_height), usable_height), 1)
    x = max((screen_width - width) // 2, 0)
    y = max((screen_height - taskbar_allowance - height) // 2, 0)
    return width, height, x, y


def place_window(
    window: tk.Misc,
    *,
    preferred: tuple[int, int],
    minimum: tuple[int, int],
) -> tuple[int, int]:
    """Apply a screen-safe initial geometry and a reachable minimum size."""
    try:
        tk_scale = float(window.tk.call("tk", "scaling"))
        monitor_ratio = min(max(tk_scale / (96.0 / 72.0), 1.0), 2.5)
    except (tk.TclError, TypeError, ValueError):
        monitor_ratio = 1.0
    preferred_scaled = (
        round(preferred[0] * monitor_ratio),
        round(preferred[1] * monitor_ratio),
    )
    minimum_scaled = (
        round(minimum[0] * monitor_ratio),
        round(minimum[1] * monitor_ratio),
    )
    width, height, x, y = fitted_geometry(
        preferred_scaled[0], preferred_scaled[1],
        int(window.winfo_screenwidth()), int(window.winfo_screenheight()),
    )
    min_width = min(minimum_scaled[0], width)
    min_height = min(minimum_scaled[1], height)
    window.minsize(min_width, min_height)
    window.geometry(f"{width}x{height}+{x}+{y}")
    return width, height
