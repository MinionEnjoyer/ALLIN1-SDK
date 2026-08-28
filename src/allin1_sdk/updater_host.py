"""External process that swaps a staged SDK after the desktop process exits."""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _filesystem_path(path: Path) -> Path:
    """Use Win32 extended paths when recursively cleaning packaged trees."""
    if os.name != "nt":
        return path
    absolute = str(path.resolve())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _wait_for_process(process_id: int, timeout_seconds: int = 120) -> None:
    if os.name != "nt":
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except OSError:
                return
            time.sleep(0.2)
        raise TimeoutError("SDK did not close before the update timeout")
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
    if not handle:
        return
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(
            handle, timeout_seconds * 1000,
        )
        if result == 0x00000102:
            raise TimeoutError("SDK did not close before the update timeout")
        if result != 0:
            raise OSError(f"failed waiting for SDK process: 0x{result:08X}")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _validated_paths(install_root: Path, staged_root: Path, entrypoint: str) -> tuple[Path, Path]:
    root = install_root.resolve(strict=True)
    pending = staged_root.resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("staged update is outside the SDK installation boundary")
    if Path(entrypoint).name != entrypoint or not (pending / entrypoint).is_file():
        raise ValueError("staged SDK entrypoint is invalid")
    return root, pending


def apply_staged_update(
    install_root: Path, staged_root: Path, entrypoint: str,
) -> Path:
    """Swap one already-verified sibling directory, rolling back on failure."""
    root, pending = _validated_paths(install_root, staged_root, entrypoint)
    backup = root.with_name(root.name + ".previous")
    if backup.exists():
        shutil.rmtree(_filesystem_path(backup))
    root.replace(backup)
    try:
        pending.replace(root)
        executable = root / entrypoint
        subprocess.Popen([str(executable)], cwd=str(root), close_fds=True)
    except Exception:
        if root.exists():
            shutil.rmtree(_filesystem_path(root))
        if backup.exists():
            backup.replace(root)
        raise
    if backup.exists():
        shutil.rmtree(_filesystem_path(backup))
    return root / entrypoint


def _delete_self_later(path: Path) -> None:
    if os.name != "nt":
        return
    command = f'ping 127.0.0.1 -n 3 > nul & del /f /q "{path}"'
    subprocess.Popen(
        ["cmd.exe", "/d", "/s", "/c", command],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        close_fds=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--delete-self", type=Path)
    args = parser.parse_args(argv)
    try:
        _wait_for_process(args.wait_pid)
        apply_staged_update(args.install_root, args.staged_root, args.entrypoint)
    except Exception as exc:
        log_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ALLIN1" / "SDK"
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "update-error.log").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8",
        )
        return 1
    finally:
        if args.delete_self:
            _delete_self_later(args.delete_self.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
