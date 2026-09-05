"""External process that swaps a staged SDK after the desktop process exits."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from allin1_sdk.release_paths import no_links, tree_files
from allin1_sdk.self_update import SDK_CHECKSUMS, SDK_EXECUTABLE, verify_release_tree


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
    root = no_links(install_root).resolve(strict=True)
    pending = no_links(staged_root).resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("staged update is outside the SDK installation boundary")
    if entrypoint != SDK_EXECUTABLE or not (pending / entrypoint).is_file():
        raise ValueError("staged SDK entrypoint is invalid")
    tree_files(root)
    verify_release_tree(pending)
    return root, pending


def apply_staged_update(
    install_root: Path, staged_root: Path, entrypoint: str,
    *, expected_manifest_sha256: str | None = None,
) -> Path:
    """Swap one already-verified sibling directory, rolling back on failure."""
    root, pending = _validated_paths(install_root, staged_root, entrypoint)
    if expected_manifest_sha256 is not None and hashlib.sha256(
        (pending / SDK_CHECKSUMS).read_bytes()
    ).hexdigest() != expected_manifest_sha256:
        raise ValueError("Staged SDK manifest changed after scheduling")
    # A unique retained backup preserves unowned/user data and never destroys an
    # older backup (or follows an attacker-controlled .previous junction).
    backup = no_links(root.with_name(root.name + ".previous-" + uuid.uuid4().hex))
    if backup.exists():
        raise FileExistsError("SDK backup destination already exists")
    root.replace(backup)
    try:
        pending.replace(root)
        executable = root / entrypoint
        subprocess.Popen([str(executable)], cwd=str(root), close_fds=True)
    except Exception:
        if root.exists():
            no_links(root)
            root.replace(pending)
        if backup.exists():
            tree_files(backup)
            backup.replace(root)
        raise
    return root / entrypoint


def _delete_self_later(path: Path) -> None:
    # Do not interpolate an externally supplied path into a destructive shell.
    # The uniquely named temporary helper is intentionally retained for normal
    # OS temporary-file cleanup; it contains no user data.
    return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--delete-self", type=Path)
    args = parser.parse_args(argv)
    try:
        _wait_for_process(args.wait_pid)
        apply_staged_update(args.install_root, args.staged_root, args.entrypoint,
                            expected_manifest_sha256=args.expected_manifest_sha256)
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
