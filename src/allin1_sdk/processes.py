"""Subprocess helpers shared by desktop repair and packaging workflows."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def hidden_process_options() -> dict[str, Any]:
    """Return platform-safe options that prevent helper console windows."""
    options: dict[str, Any] = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startup
    return options


def run_hidden(
    command: Sequence[str | Path], **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a helper process without flashing a console on Windows."""
    for key, value in hidden_process_options().items():
        kwargs.setdefault(key, value)
    return subprocess.run([str(part) for part in command], **kwargs)
