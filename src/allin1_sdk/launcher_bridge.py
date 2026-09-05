"""Narrow argv-only bridge from SDK Quick Import to ALLIN1 Launcher."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Mapping


_PACKAGE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def launcher_process_command(
    project_root: str | Path,
    package_id: str,
    *,
    traffic_requested: bool | None,
    executable: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> tuple[list[str], Path | None]:
    """Resolve the trusted Launcher entry point without a shell command."""

    if not isinstance(package_id, str):
        raise ValueError("Launcher handoff requires a valid package ID")
    normalized = package_id.strip().casefold()
    if not _PACKAGE_ID.fullmatch(normalized):
        raise ValueError("Launcher handoff requires a valid package ID")
    if traffic_requested is not None and not isinstance(traffic_requested, bool):
        raise ValueError("Launcher traffic intent must be true, false, or omitted")
    values = os.environ if environment is None else environment
    configured = executable or values.get("ALLIN1_LAUNCHER_EXECUTABLE")
    cwd: Path | None = None
    if configured:
        candidate = Path(configured).expanduser().resolve(strict=False)
        if not candidate.is_file():
            raise ValueError(f"Configured ALLIN1 Launcher was not found: {candidate}")
        command = [str(candidate)]
    else:
        discovered = next((
            resolved for name in ("allin1-launcher-desktop",)
            if (resolved := shutil.which(name))
        ), None)
        if discovered:
            command = [discovered]
        else:
            raise ValueError(
                "React ALLIN1 Launcher was not found. Add allin1-launcher-desktop to "
                "PATH, or set ALLIN1_LAUNCHER_EXECUTABLE to the installed React "
                "Launcher's executable. The SDK works without the Launcher."
            )
    command.extend(("--workspace", "packages", "--package-id", normalized))
    if traffic_requested is not None:
        command.extend(("--traffic", "on" if traffic_requested else "off"))
    return command, cwd


def open_launcher_packages(
    project_root: str | Path,
    package_id: str,
    *,
    traffic_requested: bool | None,
    executable: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    """Open/focus Launcher Packages; never install or mutate game content."""

    command, cwd = launcher_process_command(
        project_root,
        package_id,
        traffic_requested=traffic_requested,
        executable=executable,
        environment=environment,
    )
    return subprocess.Popen(command, cwd=cwd, close_fds=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def open_launcher_package(
    package_id: str,
    *,
    traffic: bool | None = None,
    executable: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
) -> subprocess.Popen[bytes]:
    """CLI-compatible singular package handoff."""

    root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None else Path(__file__).resolve().parents[2]
    )
    return open_launcher_packages(
        root,
        package_id,
        traffic_requested=traffic,
        executable=executable,
        environment=environment,
    )


__all__ = [
    "launcher_process_command", "open_launcher_package", "open_launcher_packages",
]
