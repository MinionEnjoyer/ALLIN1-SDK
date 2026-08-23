"""Standalone desktop entry point for the ALLIN1 SDK."""

from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from allin1_sdk import __version__
from allin1_sdk.addon_sdk_ui import AddonSdkDialog
from allin1_sdk.branding import apply_sdk_window_icon
from allin1_sdk.detector import detect_gta_path
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_graph import RpfPackageGraph
from allin1_sdk.rpf_graph_ui import RpfPackageGraphDialog

_INSTANCE_MUTEX: int | None = None


def _focus_existing_sdk() -> bool:
    if os.name != "nt":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.startswith("ALLIN1 SDK"):
                matches.append(int(hwnd))
                return False
        return True

    user32.EnumWindows(visit, 0)
    if not matches:
        return False
    user32.ShowWindow(matches[0], 9)
    user32.SetForegroundWindow(matches[0])
    return True


def _claim_single_instance() -> bool:
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\MinionEnjoyer.ALLIN1SDK")
    if not handle:
        return True
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        _focus_existing_sdk()
        return False
    _INSTANCE_MUTEX = int(handle)
    return True


def _configure_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.option_add("*tearOff", False)
    style.configure(".", font=("Segoe UI", 10), foreground="#173d32")
    style.configure("TFrame", background="#f4f7f5")
    style.configure("TLabel", background="#f4f7f5", foreground="#24332d")
    style.configure("Surface.TFrame", background="#ffffff")
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
        "Nav.TButton", anchor="w", padding=(16, 11), relief="flat",
        background="#eef3f0", foreground="#3c5048",
    )
    style.map("Nav.TButton", background=[("active", "#e2ebe6")])
    style.configure(
        "NavSelected.TButton", anchor="w", padding=(16, 11), relief="flat",
        background="#dcefe3", foreground="#176b36",
        font=("Segoe UI Semibold", 10),
    )
    style.map("NavSelected.TButton", background=[("active", "#d2e8da")])
    style.configure(
        "DialogTitle.TLabel", font=("Segoe UI Semibold", 17),
        foreground="#173d32",
    )
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


def _launch_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    direct_open = parser.add_mutually_exclusive_group()
    direct_open.add_argument(
        "--rpf-graph", type=Path,
        help="Open a validated RPF package graph directly in the visual node editor.",
    )
    direct_open.add_argument(
        "--vehicle-package", type=Path,
        help="Open a vehicle add-on package directly in the Vehicle Workbench.",
    )
    parser.add_argument(
        "--gta-path", type=Path,
        help="Matching GTA installation for encrypted/native asset previews.",
    )
    parser.add_argument(
        "--graph-node",
        help="Node id selected when opening a package graph directly.",
    )
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def main(argv: list[str] | None = None) -> None:
    arguments = _launch_arguments(argv)
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "MinionEnjoyer.ALLIN1SDK"
        )
    if (
        arguments.rpf_graph is None
        and arguments.vehicle_package is None
        and not _claim_single_instance()
    ):
        return
    root = tk.Tk()
    root.withdraw()
    apply_sdk_window_icon(root, project_root())
    _configure_style(root)
    detected = (
        arguments.gta_path.expanduser().resolve()
        if arguments.gta_path is not None else detect_gta_path()
    )
    if arguments.rpf_graph is not None:
        try:
            graph = arguments.rpf_graph.expanduser().resolve(strict=True)
            RpfPackageGraph.validate(graph, verify_sources=False)
            if arguments.gta_path is not None and not detected.is_dir():
                raise ValueError(f"GTA installation was not found: {detected}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not open RPF package graph", str(exc), parent=root)
            root.destroy()
            return
        dialog = RpfPackageGraphDialog(
            root, graph, project_root(), detected, on_close=root.destroy,
            initial_select=arguments.graph_node,
        )
        dialog.tk.call("wm", "transient", dialog._w, "")
        dialog.protocol("WM_DELETE_WINDOW", dialog.request_close)
        dialog.deiconify()
        dialog.state("normal")
        dialog.lift()
        dialog.focus_force()
        dialog.attributes("-topmost", True)
        dialog.after(1000, lambda: dialog.attributes("-topmost", False))
        root.mainloop()
        return
    roots = (detected,) if detected else ()
    dialog = AddonSdkDialog(
        root, project_root(), installation_roots=roots, standalone=True,
    )
    dialog.title(f"ALLIN1 SDK {__version__} — Developer Workspace")
    def close_sdk() -> None:
        if dialog.request_close():
            root.destroy()

    dialog.protocol("WM_DELETE_WINDOW", close_sdk)
    if arguments.vehicle_package is not None:
        package = arguments.vehicle_package
        dialog.after_idle(lambda: dialog.open_vehicle_package(package))
    root.mainloop()


if __name__ == "__main__":
    main()
