"""Windows handle-bound removal of a reviewed stale lock, retaining its bytes.

Never fall back to check-then-unlink: a replacement lock must not be removed.
The archive, receipt and backup are verified by the caller, never written here.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import stat

from allin1_sdk.managed_package_conversion import _safe_publication_path

MAX_LOCK = 16384


def identity(info):
    return f"{info.st_dev}:{info.st_ino}"


def require_supported(path):
    if os.name != "nt" or not Path(path).drive or Path(path).drive.startswith("\\"):
        raise ValueError("Reviewed lock cleanup requires a local Windows volume")


@contextmanager
def _exclusive_file(path, *, create=False, delete=False):
    """Deny concurrent read/write/delete opens, including our own path reopens."""
    require_supported(path)
    import msvcrt

    _safe_publication_path(path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    name = str(path)
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\" + name
    # GENERIC_READ, optional GENERIC_WRITE / DELETE; OPEN_REPARSE_POINT.
    handle = kernel.CreateFileW(name, 0x80000000 | (0x40000000 if create else 0)
                               | (0x10000 if delete else 0), 0, None,
                               1 if create else 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if create else os.O_RDONLY))
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "w+b" if create else "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Lock evidence must be a regular file without links")
        _safe_publication_path(path)
        if identity(path.stat()) != identity(info):
            raise ValueError("Lock evidence path changed while opening")
        yield stream


def _delete_open_file(stream):
    """Mark the exact held file for deletion on close (FileDispositionInfo = 4)."""
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    disposition = wintypes.BOOL(True)
    if not kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()), 4,
                                            ctypes.byref(disposition), ctypes.sizeof(disposition)):
        raise ctypes.WinError(ctypes.get_last_error())


def clear_reviewed_lock(lock, evidence, retained, retained_sha256, *, process_running, before_delete):
    """Retain exact bytes before deleting the same exclusively-held stale file."""
    with _exclusive_file(lock, delete=True) as stream:
        raw = stream.read(MAX_LOCK + 1)
        if (len(raw) > MAX_LOCK or hashlib.sha256(raw).hexdigest() != evidence["sha256"]
                or identity(os.fstat(stream.fileno())) != evidence["identity"]):
            raise ValueError("Lock changed after review; nothing was cleared")
        data = json.loads(raw)
        if (not isinstance(data, dict) or type(data.get("pid")) is not int
                or data["pid"] != evidence["pid"] or data.get("plan_id") != evidence["plan_id"]):
            raise ValueError("Lock ownership no longer matches the reviewed receipt")
        if process_running(data["pid"]):
            raise RuntimeError("Lock owner is still running; nothing was cleared")
        before_delete()
        with _exclusive_file(retained, create=retained_sha256 is None) as copy:
            if retained_sha256 is None:
                copy.write(raw)
                copy.flush()
                os.fsync(copy.fileno())
                copy.seek(0)
            if copy.read(MAX_LOCK + 1) != raw:
                raise ValueError("Retained lock evidence differs; original lock was kept")
            before_delete()
            if process_running(data["pid"]):
                raise RuntimeError("Lock owner is still running; original lock was kept")
            _delete_open_file(stream)
    return {"path": str(retained), "sha256": evidence["sha256"]}
