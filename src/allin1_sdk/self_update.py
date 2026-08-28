"""Checksum-verified, transactional updates for the standalone SDK."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.request import Request, urlopen

from allin1_sdk import __version__

SDK_RELEASES_API = "https://api.github.com/repos/MinionEnjoyer/ALLIN1-SDK/releases/latest"
SDK_REPOSITORY_URL = "https://github.com/MinionEnjoyer/ALLIN1-SDK"
SDK_EXECUTABLE = "ALLIN1-SDK-Desktop.exe"
SDK_CLI_EXECUTABLE = "allin1-sdk.exe"
SDK_AGENT_EXECUTABLE = "ALLIN1-SDK-Agent.exe"
SDK_UPDATER_EXECUTABLE = "ALLIN1-SDK-Updater.exe"
SDK_RELEASE_METADATA = "release.json"
SDK_CHECKSUMS = "checksums.json"
MAX_ARCHIVE_BYTES = 768 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
_ARCHIVE_PATTERN = re.compile(
    r"^ALLIN1-SDK-(?P<version>\d+(?:\.\d+){1,3})-win-x64\.zip$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SdkRelease:
    version: str
    name: str
    page_url: str
    archive_url: str
    archive_name: str
    archive_size: int
    checksum_url: str


@dataclass(frozen=True)
class StagedUpdate:
    version: str
    install_root: Path
    staged_root: Path
    helper_source: Path


ProgressCallback = Callable[[str, int, int], None]


def _version_key(value: str) -> tuple[int, ...]:
    normalized = value.strip().lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", normalized):
        raise ValueError(f"invalid SDK version: {value}")
    return tuple(int(part) for part in normalized.split("."))


def update_available(current: str, latest: str) -> bool:
    current_key = _version_key(current)
    latest_key = _version_key(latest)
    width = max(len(current_key), len(latest_key))
    return latest_key + (0,) * (width - len(latest_key)) > (
        current_key + (0,) * (width - len(current_key))
    )


def current_install_root() -> Path | None:
    """Return a packaged SDK root; source checkouts cannot self-replace."""
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    metadata = root / SDK_RELEASE_METADATA
    return root if metadata.is_file() else None


def _github_request(url: str) -> Request:
    return Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"ALLIN1-SDK/{__version__}",
    })


def fetch_latest_release(*, timeout: float = 8.0, opener=urlopen) -> SdkRelease:
    with opener(_github_request(SDK_RELEASES_API), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = str(payload["tag_name"]).lstrip("vV")
    _version_key(version)
    expected_name = f"ALLIN1-SDK-{version}-win-x64.zip"
    assets = payload.get("assets", [])
    archive = next(
        (item for item in assets if str(item.get("name", "")).casefold()
         == expected_name.casefold()),
        None,
    )
    if archive is None:
        matching = [
            item for item in assets
            if _ARCHIVE_PATTERN.fullmatch(str(item.get("name", "")))
        ]
        archive = matching[0] if len(matching) == 1 else None
    if archive is None:
        raise ValueError("latest SDK release has no unambiguous win-x64 archive")
    archive_name = str(archive["name"])
    match = _ARCHIVE_PATTERN.fullmatch(archive_name)
    if match is None or _version_key(match.group("version")) != _version_key(version):
        raise ValueError("SDK archive version does not match its release tag")
    checksum_name = archive_name + ".sha256"
    checksum = next(
        (item for item in assets if str(item.get("name", "")).casefold()
         == checksum_name.casefold()),
        None,
    )
    if checksum is None:
        raise ValueError(f"latest SDK release is missing {checksum_name}")
    size = int(archive.get("size", 0))
    if size < 1 or size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"SDK archive size is outside the allowed range: {size} bytes")
    return SdkRelease(
        version=version,
        name=str(payload.get("name") or f"ALLIN1 SDK {version}"),
        page_url=str(payload["html_url"]),
        archive_url=str(archive["browser_download_url"]),
        archive_name=archive_name,
        archive_size=size,
        checksum_url=str(checksum["browser_download_url"]),
    )


def _read_limited_response(response, limit: int) -> bytes:
    content = bytearray()
    while True:
        chunk = response.read(min(1024 * 1024, limit + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > limit:
            raise ValueError("download exceeds the allowed size")


def _parse_checksum(content: bytes, archive_name: str) -> str:
    try:
        line = next(value.strip() for value in content.decode("ascii").splitlines()
                    if value.strip())
    except (UnicodeDecodeError, StopIteration) as exc:
        raise ValueError("SDK checksum asset is empty or invalid") from exc
    parts = line.split()
    digest = parts[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("SDK checksum asset does not contain a SHA-256 digest")
    if len(parts) > 1 and Path(parts[-1].lstrip("*")).name.casefold() != archive_name.casefold():
        raise ValueError("SDK checksum names a different archive")
    return digest


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if "\\" in info.filename or "\x00" in info.filename:
        raise ValueError(f"unsafe SDK archive member: {info.filename}")
    path = PurePosixPath(info.filename)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe SDK archive member: {info.filename}")
    if any(":" in part for part in path.parts):
        raise ValueError(f"unsafe SDK archive member: {info.filename}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError(f"SDK archive contains a symbolic link: {info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError(f"SDK archive contains an encrypted member: {info.filename}")
    return path


def _filesystem_path(path: Path) -> Path:
    """Use Win32 extended paths for deeply nested packaged dependencies."""
    if os.name != "nt":
        return path
    absolute = str(path.resolve())
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def inspect_release_archive(archive_path: Path, expected_version: str) -> str:
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("SDK archive exceeds the allowed size")
    with zipfile.ZipFile(archive_path) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        if len(files) > MAX_ARCHIVE_FILES:
            raise ValueError("SDK archive contains too many files")
        if sum(item.file_size for item in files) > MAX_EXTRACTED_BYTES:
            raise ValueError("SDK archive expands beyond the allowed size")
        names: dict[str, zipfile.ZipInfo] = {}
        windows_names: set[str] = set()
        for item in files:
            name = _safe_member(item).as_posix()
            folded = name.casefold()
            if name in names or folded in windows_names:
                raise ValueError(f"SDK archive contains a duplicate path: {name}")
            names[name] = item
            windows_names.add(folded)
        required = {
            SDK_EXECUTABLE, SDK_CLI_EXECUTABLE, SDK_AGENT_EXECUTABLE,
            SDK_UPDATER_EXECUTABLE, SDK_RELEASE_METADATA, SDK_CHECKSUMS,
        }
        missing = required - names.keys()
        if missing:
            raise ValueError("SDK archive is missing: " + ", ".join(sorted(missing)))
        try:
            metadata = json.loads(archive.read(names[SDK_RELEASE_METADATA]).decode("utf-8"))
            checksums = json.loads(archive.read(names[SDK_CHECKSUMS]).decode("utf-8"))
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("SDK release metadata is invalid") from exc
        version = str(metadata.get("version", "")).lstrip("vV")
        if _version_key(version) != _version_key(expected_version):
            raise ValueError("SDK package version does not match the selected release")
        expected_metadata = {
            "product": "ALLIN1-SDK",
            "entrypoint": SDK_EXECUTABLE,
            "cli_entrypoint": SDK_CLI_EXECUTABLE,
            "agent_entrypoint": SDK_AGENT_EXECUTABLE,
            "updater_entrypoint": SDK_UPDATER_EXECUTABLE,
        }
        for key, value in expected_metadata.items():
            if metadata.get(key) != value:
                raise ValueError(f"SDK release metadata has an invalid {key}")
        if not isinstance(checksums, dict) or set(checksums) != set(names) - {SDK_CHECKSUMS}:
            raise ValueError("SDK checksum manifest does not exactly match the payload")
        for name, expected in checksums.items():
            digest = str(expected).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"invalid SDK checksum for {name}")
            if hashlib.sha256(archive.read(names[name])).hexdigest() != digest:
                raise ValueError(f"SDK checksum mismatch: {name}")
        for name in (
            SDK_EXECUTABLE, SDK_CLI_EXECUTABLE, SDK_AGENT_EXECUTABLE,
            SDK_UPDATER_EXECUTABLE,
        ):
            if archive.read(names[name])[:2] != b"MZ":
                raise ValueError(f"SDK executable is not a Windows PE file: {name}")
    return version


def _extract_archive(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        extracted: set[str] = set()
        for info in archive.infolist():
            relative = _safe_member(info)
            folded = relative.as_posix().casefold()
            if folded in extracted:
                raise ValueError(
                    f"SDK archive contains a duplicate path: {relative.as_posix()}"
                )
            extracted.add(folded)
            destination = _filesystem_path(target.joinpath(*relative.parts))
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def stage_release(
    release: SdkRelease,
    install_root: Path,
    *,
    timeout: float = 60.0,
    opener=urlopen,
    progress: ProgressCallback | None = None,
) -> StagedUpdate:
    """Download and verify a release without touching the running installation."""
    install_root = install_root.resolve(strict=True)
    if not (install_root / SDK_EXECUTABLE).is_file():
        raise ValueError("current SDK installation root is invalid")
    with opener(_github_request(release.checksum_url), timeout=timeout) as response:
        expected = _parse_checksum(
            _read_limited_response(response, 16 * 1024), release.archive_name,
        )
    staged = install_root.with_name(
        f"{install_root.name}.updating-{uuid.uuid4().hex}"
    )
    if staged.exists():
        raise FileExistsError(f"update staging path already exists: {staged}")
    try:
        with tempfile.TemporaryDirectory(prefix="allin1-sdk-download-") as temporary:
            archive_path = Path(temporary) / release.archive_name
            digest = hashlib.sha256()
            downloaded = 0
            with opener(_github_request(release.archive_url), timeout=timeout) as response:
                with archive_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > MAX_ARCHIVE_BYTES:
                            raise ValueError("SDK download exceeds the allowed size")
                        output.write(chunk)
                        digest.update(chunk)
                        if progress:
                            progress("Downloading SDK", downloaded, release.archive_size)
            if downloaded != release.archive_size:
                raise ValueError(
                    f"SDK download size mismatch: expected {release.archive_size}, "
                    f"received {downloaded}"
                )
            if digest.hexdigest() != expected:
                raise ValueError("downloaded SDK archive failed SHA-256 verification")
            if progress:
                progress("Verifying SDK", downloaded, downloaded)
            inspect_release_archive(archive_path, release.version)
            staged.mkdir(parents=False)
            _extract_archive(archive_path, staged)
        return StagedUpdate(
            version=release.version,
            install_root=install_root,
            staged_root=staged,
            helper_source=staged / SDK_UPDATER_EXECUTABLE,
        )
    except Exception:
        if staged.exists():
            shutil.rmtree(_filesystem_path(staged))
        raise


def schedule_staged_update(staged: StagedUpdate, *, process_id: int | None = None) -> Path:
    """Launch the detached swap helper; the caller must then exit promptly."""
    if os.name != "nt":
        raise OSError("standalone SDK updates are currently supported only on Windows")
    root = staged.install_root.resolve(strict=True)
    pending = staged.staged_root.resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("update staging directory is outside the SDK installation boundary")
    helper = staged.helper_source.resolve(strict=True)
    if helper.parent != pending or helper.name != SDK_UPDATER_EXECUTABLE:
        raise ValueError("staged updater executable is invalid")
    helper_copy = Path(tempfile.gettempdir()) / (
        f"ALLIN1-SDK-Updater-{uuid.uuid4().hex}.exe"
    )
    shutil.copy2(helper, helper_copy)
    command = [
        str(helper_copy), "--wait-pid", str(process_id or os.getpid()),
        "--install-root", str(root), "--staged-root", str(pending),
        "--entrypoint", SDK_EXECUTABLE, "--delete-self", str(helper_copy),
    ]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    subprocess.Popen(
        command, cwd=str(root.parent), close_fds=True, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return helper_copy


def discard_staged_update(staged: StagedUpdate) -> bool:
    """Remove only the exact sibling staging directory created for this update."""
    root = staged.install_root.resolve(strict=True)
    pending = staged.staged_root.resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("update staging directory is outside the SDK installation boundary")
    shutil.rmtree(_filesystem_path(pending))
    return True
