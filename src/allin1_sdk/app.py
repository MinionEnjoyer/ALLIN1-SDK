"""Standalone desktop entry point for the ALLIN1 SDK."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from allin1_sdk import __version__
from allin1_sdk.addon_sdk_ui import AddonSdkDialog
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.paths import project_root


def _configure_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*tearOff", False)
    style.configure(".", font=("Segoe UI", 10), foreground="#173d32")
    style.configure("TFrame", background="#f4f7f5")
    style.configure("TLabel", background="#f4f7f5", foreground="#24332d")
    style.configure("TButton", padding=(11, 7))
    style.configure("TEntry", padding=(7, 6))
    style.configure("TCombobox", padding=(6, 5))
    style.configure("TLabelframe", background="#ffffff", bordercolor="#d4ddd9")
    style.configure(
        "TLabelframe.Label", background="#f4f7f5", foreground="#1f7f42",
        font=("Segoe UI Semibold", 10),
    )
    style.configure(
        "Accent.TButton", background="#2d9c50", foreground="white",
        font=("Segoe UI Semibold", 10), padding=(13, 8),
    )
    style.map("Accent.TButton", background=[("active", "#1f7f42")])
    style.configure(
        "Treeview", rowheight=28, font=("Segoe UI", 10),
        background="#ffffff", fieldbackground="#ffffff",
    )
    style.configure(
        "Treeview.Heading", font=("Segoe UI Semibold", 9), padding=(7, 7),
    )
    style.configure(
        "FieldLabel.TLabel", font=("Segoe UI Semibold", 9), foreground="#52635c",
    )


def main() -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MinionEnjoyer.ALLIN1SDK"
        )
    root = tk.Tk()
    root.withdraw()
    favicon = project_root() / "assets" / "favicon.ico"
    if favicon.is_file() and os.name == "nt":
        try:
            root.iconbitmap(default=str(favicon))
        except tk.TclError:
            pass
    _configure_style(root)
    detected = detect_gta_path()
    roots = (detected,) if detected else ()
    dialog = AddonSdkDialog(
        root, project_root(), installation_roots=roots, standalone=True,
    )
    dialog.title(f"ALLIN1 SDK {__version__} — Developer Workspace")
    dialog.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
