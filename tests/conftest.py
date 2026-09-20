"""Shared test isolation for repository-local state."""

import os
from pathlib import Path
import pytest

import allin1_sdk.detector as detector


def launcher_source() -> Path:
    """Return the provisioned Launcher source, rejecting a bad CI override."""
    configured = os.environ.get("ALLIN1_LAUNCHER_SRC")
    if configured:
        source = Path(configured).resolve()
        if not source.is_dir():
            raise RuntimeError(
                "ALLIN1_LAUNCHER_SRC must name an existing Launcher src directory: "
                f"{source}"
            )
        return source
    return Path(__file__).resolve().parents[2] / "ALLIN1/src"


@pytest.fixture(autouse=True)
def isolate_detector_cache(tmp_path, monkeypatch):
    """Never let path detection tests overwrite the real .gta_path marker."""
    monkeypatch.setattr(detector, "_project_root", lambda: tmp_path)


@pytest.fixture(autouse=True)
def isolate_user_state_and_game_discovery(tmp_path, monkeypatch):
    """Release gates must never discover real games or write real user settings."""
    for name in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / "isolated-user" / name))
    monkeypatch.delenv("ALLIN1_GTA_PATH", raising=False)
    monkeypatch.setattr(detector, "_detect_windows", lambda: None)
    monkeypatch.setattr(detector, "_detect_linux", lambda: None)
