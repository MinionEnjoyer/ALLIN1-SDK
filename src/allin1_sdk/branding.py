"""Shared native-window branding for source and packaged SDK runs."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk


def _apply_windows_native_icon(window: tk.Misc, favicon: Path) -> bool:
    """Set both Tk's client window and its native Windows wrapper icons."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
        user32.GetDpiForWindow.restype = wintypes.UINT
        user32.GetSystemMetricsForDpi.argtypes = (ctypes.c_int, wintypes.UINT)
        user32.GetSystemMetricsForDpi.restype = ctypes.c_int
        user32.LoadImageW.argtypes = (
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.SendMessageW.restype = wintypes.LPARAM

        window.update_idletasks()
        client = int(window.winfo_id())
        wrapper = int(user32.GetParent(client) or 0)
        targets = tuple(dict.fromkeys(handle for handle in (wrapper, client) if handle))
        if not targets:
            return False

        handles = getattr(window, "_allin1_sdk_native_icons", None)
        if not handles:
            image_icon = 1
            load_from_file = 0x0010
            dpi = int(user32.GetDpiForWindow(targets[0]) or 96)
            small_width = max(16, int(user32.GetSystemMetricsForDpi(49, dpi)))
            small_height = max(16, int(user32.GetSystemMetricsForDpi(50, dpi)))
            large_width = max(32, int(user32.GetSystemMetricsForDpi(11, dpi)))
            large_height = max(32, int(user32.GetSystemMetricsForDpi(12, dpi)))
            small = int(user32.LoadImageW(
                None, str(favicon), image_icon,
                small_width, small_height, load_from_file,
            ) or 0)
            large = int(user32.LoadImageW(
                None, str(favicon), image_icon,
                large_width, large_height, load_from_file,
            ) or 0)
            if not small and not large:
                return False
            handles = (small or large, large or small)
            setattr(window, "_allin1_sdk_native_icons", handles)

        wm_seticon = 0x0080
        icon_small, icon_big = handles
        for target in targets:
            user32.SendMessageW(target, wm_seticon, 0, icon_small)
            user32.SendMessageW(target, wm_seticon, 1, icon_big)
            user32.SendMessageW(target, wm_seticon, 2, icon_small)
        return True
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return False


def _reapply_mapped_icon(window: tk.Misc, favicon: Path) -> None:
    """Reapply the native icon after Tk creates/maps its wrapper window."""
    try:
        if not window.winfo_exists():
            return
        if os.name == "nt":
            window.iconbitmap(default=str(favicon))
        _apply_windows_native_icon(window, favicon)
    except tk.TclError:
        return


def apply_sdk_window_icon(window: tk.Misc, sdk_root: Path | str) -> bool:
    """Apply the SDK favicon to the title bar and Windows taskbar.

    ``iconbitmap`` supplies the native Windows icon handle, while
    ``iconphoto`` is also required when the source build is hosted by
    Python/Pythonw instead of the branded PyInstaller executable. A reference
    is retained on the window because Tk releases unreferenced photo images.
    """
    favicon = Path(sdk_root) / "assets" / "favicon.ico"
    if not favicon.is_file():
        return False

    applied = False
    try:
        with Image.open(favicon) as source:
            if hasattr(source, "ico"):
                sizes = source.ico.sizes()
                largest = max(sizes, key=lambda size: size[0] * size[1])
                image = source.ico.getimage(largest).convert("RGBA")
            else:  # pragma: no cover - Pillow identifies .ico as ICO
                image = source.convert("RGBA")
            photo = ImageTk.PhotoImage(image, master=window)
        window.iconphoto(True, photo)
        setattr(window, "_allin1_sdk_icon_photo", photo)
        applied = True
    except (OSError, ValueError, tk.TclError):
        pass

    if os.name == "nt":
        try:
            window.iconbitmap(default=str(favicon))
            applied = True
        except tk.TclError:
            pass
        applied = _apply_windows_native_icon(window, favicon) or applied

    try:
        window.after_idle(lambda: _reapply_mapped_icon(window, favicon))
    except tk.TclError:
        pass
    return applied
