from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from scripts import smoke_desktop_sidecar as smoke


def test_disposable_profile_exists_and_overrides_every_user_state_path(
    tmp_path: Path,
) -> None:
    preview = tmp_path / "preview cache"
    environment = smoke.disposable_profile_environment(preview, {
        "USERPROFILE": r"C:\\real-user",
        "APPDATA": r"C:\\real-user\\AppData\\Roaming",
        "LOCALAPPDATA": r"C:\\real-user\\AppData\\Local",
        "HOME": r"C:\\real-user",
    })
    profile = preview / "user"

    assert Path(environment["USERPROFILE"]) == profile
    assert Path(environment["HOME"]) == profile
    assert Path(environment["APPDATA"]) == profile / "AppData" / "Roaming"
    assert Path(environment["LOCALAPPDATA"]) == profile / "AppData" / "Local"
    assert Path(environment["XDG_DATA_HOME"]) == profile / ".local" / "share"
    assert Path(environment["XDG_CACHE_HOME"]) == profile / ".cache"
    assert all(path.is_dir() for path in (
        profile,
        Path(environment["APPDATA"]),
        Path(environment["LOCALAPPDATA"]),
        profile / "AppData" / "LocalLow",
        Path(environment["XDG_DATA_HOME"]),
        Path(environment["XDG_CACHE_HOME"]),
    ))
    assert all("real-user" not in value for value in environment.values())
    if os.name == "nt":
        assert environment["HOMEDRIVE"] + environment["HOMEPATH"] == str(profile)


def test_windows_special_folders_receive_an_existing_disposable_profile(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows special-folder behavior is platform-specific")
    profile = tmp_path / "profile"
    environment = smoke.disposable_profile_environment(profile, dict(os.environ))
    command = (
        "$folders = [Environment+SpecialFolder]::UserProfile, "
        "[Environment+SpecialFolder]::ApplicationData, "
        "[Environment+SpecialFolder]::LocalApplicationData; "
        "$folders | ForEach-Object { [Environment]::GetFolderPath($_) }"
    )
    powershell = (
        Path(environment.get("SystemRoot", os.environ["SystemRoot"])) / "System32"
        / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    completed = subprocess.run(
        [str(powershell), "-NoProfile", "-Command", command],
        env=environment, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    locations = [Path(line) for line in completed.stdout.splitlines() if line]
    assert len(locations) == 3 and all(location.is_dir() for location in locations)
