"""Windowless compatibility entrypoint for the React/Tauri desktop.

Python supplies domain services, not an alternate graphical interface.
Source users must install a complete desktop or explicitly select its executable.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


EXECUTABLE_NAME = "allin1-sdk-desktop" + (".exe" if os.name == "nt" else "")
EXECUTABLE_ENV = "ALLIN1_SDK_EXECUTABLE"


def desktop_executable(executable: Path | None = None) -> Path:
    """Resolve only the native shell; never fall back to Python or a sidecar."""
    configured = os.environ.get(EXECUTABLE_ENV)
    if configured:
        selected = Path(configured).expanduser().resolve(strict=True)
        if not selected.is_file():
            raise FileNotFoundError(f"Desktop executable is not a file: {selected}")
        if os.name == "nt" and selected.suffix.casefold() != ".exe":
            raise ValueError("The desktop executable must be a Windows .exe")
        return selected
    if getattr(sys, "frozen", False):
        current = (executable or Path(sys.executable)).resolve()
        for selected in (current.parent / EXECUTABLE_NAME, current.parent.parent / EXECUTABLE_NAME):
            if selected.is_file():
                return selected
    found = shutil.which(EXECUTABLE_NAME)
    if found:
        return Path(found).resolve(strict=True)
    raise FileNotFoundError(
        "ALLIN1 SDK React/Tauri desktop was not found. Install the complete "
        f"desktop distribution or set {EXECUTABLE_ENV} to its executable. "
        "A Python-only installation does not include the desktop."
    )


def main(argv: list[str] | None = None) -> int:
    try:
        selected = desktop_executable()
        # Keep the caller's working directory so relative deep links retain
        # their meaning. Shell parsing and automatic fallback are forbidden.
        subprocess.Popen([str(selected), *(sys.argv[1:] if argv is None else argv)],
                         close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, ValueError) as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "ALLIN1 SDK", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
