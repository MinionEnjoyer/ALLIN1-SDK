"""Subprocess helpers shared by desktop repair and packaging workflows."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any


_dll_directory_lock = threading.RLock()


def _frozen_bundle_root() -> str | None:
    """Return PyInstaller's bundle directory when its DLL search path is active."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    bundle_root = getattr(sys, "_MEIPASS", None)
    return str(bundle_root) if bundle_root else None


def _external_tool_environment(
    environment: dict[str, str] | None,
    bundle_root: str,
) -> dict[str, str]:
    """Keep only non-bundled PATH entries for a system-tool child process."""
    result = dict(os.environ if environment is None else environment)
    raw_path = result.get("PATH")
    if not raw_path:
        return result

    normalized_root = os.path.normcase(os.path.normpath(bundle_root))

    def is_bundled_path(entry: str) -> bool:
        if not entry:
            return False
        try:
            normalized_entry = os.path.normcase(os.path.normpath(entry))
            return os.path.commonpath((normalized_entry, normalized_root)) == normalized_root
        except ValueError:
            return False

    result["PATH"] = os.pathsep.join(
        entry for entry in raw_path.split(os.pathsep) if not is_bundled_path(entry)
    )
    return result


def _set_dll_directory(directory: str | None) -> None:
    """Set Windows' process DLL directory, surfacing an unexpected API failure."""
    import ctypes

    if not ctypes.windll.kernel32.SetDllDirectoryW(directory):
        raise ctypes.WinError()


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
    command: Sequence[str | Path], *, sanitize_frozen_dlls: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a helper process without flashing a console on Windows.

    ``sanitize_frozen_dlls`` is for operating-system tools (such as CMake and
    MSBuild), not helpers bundled with this application.  PyInstaller sets a
    process-wide DLL directory; reset it only while the external child is
    created, then immediately restore it so concurrent bundled helpers retain
    their expected resolution behavior.
    """
    for key, value in hidden_process_options().items():
        kwargs.setdefault(key, value)
    rendered = [str(part) for part in command]
    frozen_bundle_root = _frozen_bundle_root()
    if frozen_bundle_root is None:
        return subprocess.run(rendered, **kwargs)

    if kwargs.get("input") is not None and kwargs.get("stdin") is not None:
        raise ValueError("stdin and input arguments may not both be used.")
    if kwargs.get("capture_output") and (
        kwargs.get("stdout") is not None or kwargs.get("stderr") is not None
    ):
        raise ValueError("stdout and stderr arguments may not be used with capture_output.")

    popen_kwargs = dict(kwargs)
    input_value = popen_kwargs.pop("input", None)
    timeout = popen_kwargs.pop("timeout", None)
    check = popen_kwargs.pop("check", False)
    if popen_kwargs.pop("capture_output", False):
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE
    if input_value is not None:
        popen_kwargs["stdin"] = subprocess.PIPE
    if sanitize_frozen_dlls:
        popen_kwargs["env"] = _external_tool_environment(
            popen_kwargs.get("env"), frozen_bundle_root,
        )

    # SetDllDirectoryW is process-wide.  Every frozen helper spawn coordinates
    # with this lock. The child inherits its DLL directory at creation, so the
    # lock need not cover communicate()/the entire tool invocation.
    with _dll_directory_lock:
        if sanitize_frozen_dlls:
            _set_dll_directory(None)
        try:
            process = subprocess.Popen(rendered, **popen_kwargs)
        finally:
            if sanitize_frozen_dlls:
                _set_dll_directory(frozen_bundle_root)
    with process:
        try:
            stdout, stderr = process.communicate(input_value, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise
    completed = subprocess.CompletedProcess(
        rendered, process.returncode, stdout, stderr,
    )
    if check:
        completed.check_returncode()
    return completed
