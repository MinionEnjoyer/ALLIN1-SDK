"""Stable application, resource, and user-state locations."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    """Return the source checkout root or an explicit SDK home."""
    configured = os.environ.get("ALLIN1_SDK_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def user_data_root() -> Path:
    """Return a writable per-user directory, separate from the source tree."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser().resolve() / "ALLIN1-SDK"
    return Path.home() / ".allin1-sdk"
