"""Shared desktop-window behavior for the ALLIN1 SDK.

The SDK is still a Tk application, so a small amount of explicit Windows/DPI
handling is needed before any widgets are created.  Keeping it here prevents
each compatibility window from inventing its own sizing rules.
"""

from __future__ import annotations

import os
import tkinter as tk


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
