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
from allin1_sdk.release_paths import contained, no_links, relative_path, strict_json, tree_files, unique_paths

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
        payload = strict_json(_read_limited_response(response, 2 * 1024 * 1024))
    version = str(payload["tag_name"]).lstrip("vV")
    _version_key(version)
    expected_name = f"ALLIN1-SDK-{version}-win-x64.zip"
    assets = payload.get("assets", [])
    if not isinstance(assets, list) or any(not isinstance(item, dict) for item in assets):
        raise ValueError("Invalid SDK release asset catalog")
    asset_names = [str(item.get("name", "")).casefold() for item in assets]
    if len(asset_names) != len(set(asset_names)):
        raise ValueError("SDK release asset catalog has duplicate identities")
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
        lines = [value.strip() for value in content.decode("ascii").splitlines() if value.strip()]
        if len(lines) != 1:
            raise ValueError("SDK checksum asset must contain exactly one digest")
        line = lines[0]
    except (UnicodeDecodeError, StopIteration) as exc:
        raise ValueError("SDK checksum asset is empty or invalid") from exc
    parts = line.split()
    digest = parts[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("SDK checksum asset does not contain a SHA-256 digest")
    if len(parts) > 2 or (len(parts) == 2 and parts[-1].lstrip("*") != archive_name):
        raise ValueError("SDK checksum names a different archive")
    return digest


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if info.orig_filename != info.filename:
        raise ValueError("SDK archive member contains a NUL")
    path = relative_path(info.filename[:-1] if info.is_dir() else info.filename)
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG) or info.external_attr & 0x400:
        raise ValueError(f"SDK archive contains a link or special file: {info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError(f"SDK archive contains an encrypted member: {info.filename}")
    return path


def _archive_inventory(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    items = archive.infolist()
    if len(items) > MAX_ARCHIVE_FILES or sum(i.file_size for i in items) > MAX_EXTRACTED_BYTES:
        raise ValueError("SDK archive exceeds payload limits")
    all_names = [_safe_member(item).as_posix() for item in items]
    if len({name.casefold() for name in all_names}) != len(all_names):
        raise ValueError("SDK archive contains a duplicate path")
    files = {name: item for name, item in zip(all_names, items) if not item.is_dir()}
    unique_paths(list(files))
    folded_files = {n.casefold() for n in files}
    for name in all_names:
        if any(parent.as_posix().casefold() in folded_files
               for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise ValueError("SDK archive contains a file/directory collision")
    return files


def verify_release_tree(root: Path, expected_version: str | None = None) -> dict:
    """Reverify actual staged bytes at consumption time, not a past smoke result."""
    files = tree_files(root)
    required = {SDK_EXECUTABLE, SDK_CLI_EXECUTABLE, SDK_AGENT_EXECUTABLE,
                SDK_UPDATER_EXECUTABLE, SDK_RELEASE_METADATA, SDK_CHECKSUMS}
    if not required <= files.keys():
        raise ValueError("Staged SDK is missing release metadata or required payloads")
    checksums = strict_json(files[SDK_CHECKSUMS].read_bytes())
    metadata = strict_json(files[SDK_RELEASE_METADATA].read_bytes())
    if not isinstance(checksums, dict):
        raise ValueError("Invalid staged checksum manifest")
    unique_paths(list(checksums))
    if set(checksums) != files.keys() - {SDK_CHECKSUMS}:
        raise ValueError("Staged checksum manifest does not exactly match payload")
    for name, digest in checksums.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid staged checksum: {name}")
        if hashlib.sha256(_filesystem_path(contained(root, name)).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Staged checksum mismatch: {name}")
    if not isinstance(metadata, dict) or metadata.get("product") != "ALLIN1-SDK":
        raise ValueError("Invalid staged SDK identity")
    version = str(metadata.get("version", ""))
    _version_key(version)
    if expected_version and _version_key(version) != _version_key(expected_version):
        raise ValueError("Staged SDK version changed")
    for field, name in (("entrypoint", SDK_EXECUTABLE), ("cli_entrypoint", SDK_CLI_EXECUTABLE),
                        ("agent_entrypoint", SDK_AGENT_EXECUTABLE), ("updater_entrypoint", SDK_UPDATER_EXECUTABLE)):
        if metadata.get(field) != name or files[name].read_bytes()[:2] != b"MZ":
            raise ValueError(f"Invalid staged SDK entrypoint: {field}")
    return metadata


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
        inventory = _archive_inventory(archive)
        files = list(inventory.values())
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
            metadata = strict_json(archive.read(names[SDK_RELEASE_METADATA]))
            checksums = strict_json(archive.read(names[SDK_CHECKSUMS]))
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("SDK release metadata is invalid") from exc
        if not isinstance(metadata, dict):
            raise ValueError("SDK release metadata must be an object")
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
        unique_paths(list(checksums))
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
        _archive_inventory(archive)
        # Validate every destination before creating even the first directory.
        for info in archive.infolist():
            destination = contained(target, _safe_member(info).as_posix())
            if destination.exists() and not (info.is_dir() and destination.is_dir()):
                raise FileExistsError(f"Staging destination already exists: {destination}")
        extracted: set[str] = set()
        for info in archive.infolist():
            relative = _safe_member(info)
            folded = relative.as_posix().casefold()
            if folded in extracted:
                raise ValueError(
                    f"SDK archive contains a duplicate path: {relative.as_posix()}"
                )
            extracted.add(folded)
            destination = _filesystem_path(contained(target, relative.as_posix()))
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("xb") as output:
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
    install_root = no_links(install_root).resolve(strict=True)
    if relative_path(release.archive_name).name != release.archive_name:
        raise ValueError("SDK archive download name must be a filename")
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
            verify_release_tree(staged, release.version)
        return StagedUpdate(
            version=release.version,
            install_root=install_root,
            staged_root=staged,
            helper_source=staged / SDK_UPDATER_EXECUTABLE,
        )
    except Exception:
        if staged.exists():
            tree_files(staged)
            shutil.rmtree(_filesystem_path(staged))
        raise


def schedule_staged_update(staged: StagedUpdate, *, process_id: int | None = None) -> Path:
    """Launch the detached swap helper; the caller must then exit promptly."""
    if os.name != "nt":
        raise OSError("standalone SDK updates are currently supported only on Windows")
    root = no_links(staged.install_root).resolve(strict=True)
    pending = no_links(staged.staged_root).resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("update staging directory is outside the SDK installation boundary")
    verify_release_tree(pending, staged.version)
    helper = no_links(staged.helper_source).resolve(strict=True)
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
        "--expected-manifest-sha256", hashlib.sha256((pending / SDK_CHECKSUMS).read_bytes()).hexdigest(),
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
    root = no_links(staged.install_root).resolve(strict=True)
    pending = no_links(staged.staged_root).resolve(strict=True)
    if pending.parent != root.parent or not pending.name.startswith(root.name + ".updating-"):
        raise ValueError("update staging directory is outside the SDK installation boundary")
    tree_files(pending)
    shutil.rmtree(_filesystem_path(pending))
    return True
