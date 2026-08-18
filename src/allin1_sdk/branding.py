"""Shared native-window branding for source and packaged SDK runs."""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageTk


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
    return applied
