"""Per-user native toolchain choices for the Vehicle Workbench.

These preferences are deliberately stored below the SDK user-data root.  They
are workstation configuration, not vehicle-package metadata, and must never be
copied into a controller build or distributable archive.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from allin1_sdk.paths import user_data_root
from allin1_sdk.story_axle_runtime_builder import NativeAxleToolchainSettings


TOOLCHAIN_SETTINGS_SCHEMA_VERSION = 1
TOOLCHAIN_SETTINGS_FILENAME = "story-axle-toolchain.json"
TOOLCHAIN_MODES = ("auto", "manual")


def native_toolchain_settings_path() -> Path:
    """Return the private workstation preference path used by the SDK."""

    return user_data_root() / TOOLCHAIN_SETTINGS_FILENAME


def _optional_path(value: object) -> Path | None:
    """Preserve an explicit path so preflight can diagnose it precisely."""

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "\0" in text:
        return None
    return Path(text).expanduser()


def _normalized_mode(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in TOOLCHAIN_MODES else "auto"


def load_native_toolchain_settings(
    path: str | Path | None = None,
) -> NativeAxleToolchainSettings:
    """Load preferences defensively; corrupt files fall back to Auto mode."""

    source = Path(path) if path is not None else native_toolchain_settings_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return NativeAxleToolchainSettings()
    if not isinstance(raw, Mapping):
        return NativeAxleToolchainSettings()
    return NativeAxleToolchainSettings(
        mode=_normalized_mode(raw.get("mode")),
        cmake_path=_optional_path(raw.get("cmake_path")),
        ctest_path=_optional_path(raw.get("ctest_path")),
        visual_studio_path=_optional_path(raw.get("visual_studio_path")),
    )


def save_native_toolchain_settings(
    settings: NativeAxleToolchainSettings,
    path: str | Path | None = None,
) -> Path:
    """Atomically save only the user's discovery choices and local paths."""

    if not isinstance(settings, NativeAxleToolchainSettings):
        raise TypeError("Toolchain preferences must use NativeAxleToolchainSettings")
    destination = (
        Path(path) if path is not None else native_toolchain_settings_path()
    )
    payload = {
        "schema_version": TOOLCHAIN_SETTINGS_SCHEMA_VERSION,
        "mode": _normalized_mode(settings.mode),
        "cmake_path": str(settings.cmake_path) if settings.cmake_path else None,
        "ctest_path": str(settings.ctest_path) if settings.ctest_path else None,
        "visual_studio_path": (
            str(settings.visual_studio_path)
            if settings.visual_studio_path else None
        ),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def reset_native_toolchain_settings(
    path: str | Path | None = None,
) -> NativeAxleToolchainSettings:
    """Clear every local override and restore automatic discovery."""

    settings = NativeAxleToolchainSettings()
    save_native_toolchain_settings(settings, path)
    return settings


__all__ = [
    "TOOLCHAIN_MODES",
    "TOOLCHAIN_SETTINGS_FILENAME",
    "TOOLCHAIN_SETTINGS_SCHEMA_VERSION",
    "load_native_toolchain_settings",
    "native_toolchain_settings_path",
    "reset_native_toolchain_settings",
    "save_native_toolchain_settings",
]
