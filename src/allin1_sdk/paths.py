"""Stable application, resource, and user-state locations."""

from __future__ import annotations

import os
import sys
from pathlib import Path


GTA_EXECUTABLE_MARKERS = (
    "GTA5.exe",
    "GTA5_Enhanced.exe",
    "PlayGTAV.exe",
)


def gta_root_containing(
    path: str | Path, *, explicit_roots: tuple[str | Path, ...] = (),
) -> Path | None:
    """Return the protected GTA root containing ``path``, if any.

    Both the authored path and its resolved target are checked.  This prevents
    a symlink from bypassing the boundary in either direction: a link outside
    GTA cannot target the game, and a link authored inside GTA cannot redirect
    an otherwise-staged operation elsewhere.  Explicit roots let callers
    protect a selected installation before every game file is present.
    """
    authored = Path(path).expanduser()
    lexical = Path(os.path.abspath(authored))
    resolved = authored.resolve(strict=False)
    candidates = tuple(dict.fromkeys((lexical, resolved)))

    roots: list[tuple[Path, Path]] = []
    for value in explicit_roots:
        explicit = Path(value).expanduser()
        roots.append((
            Path(os.path.abspath(explicit)),
            explicit.resolve(strict=False),
        ))
    for candidate in candidates:
        for lexical_root, resolved_root in roots:
            if (
                candidate == lexical_root
                or candidate.is_relative_to(lexical_root)
                or candidate == resolved_root
                or candidate.is_relative_to(resolved_root)
            ):
                return resolved_root

        folder = candidate if candidate.is_dir() else candidate.parent
        for ancestor in (folder, *folder.parents):
            if any((ancestor / marker).is_file() for marker in GTA_EXECUTABLE_MARKERS):
                return ancestor.resolve(strict=False)
    return None


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
