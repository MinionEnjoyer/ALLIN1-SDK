"""Guarded creation of new RPF archives from provenance-preserving folders."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from allin1_sdk.processes import run_hidden
from allin1_sdk.rpf_tools import RpfExplorerService, _content_fingerprint


RPF_BUILD_REPORT_SCHEMA = 2
MAX_RPF_BUILD_FILES = 25_000
MAX_RPF_BUILD_BYTES = 16 * 1024 * 1024 * 1024
MAX_RPF_BUILD_FILE_BYTES = 512 * 1024 * 1024
MAX_RPF_BUILD_DEPTH = 8
_COPY_MARGIN_BYTES = 64 * 1024 * 1024
_NESTED_SOURCE_SUFFIX = ".rpf.source"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _safe_name(name: str) -> None:
    if (
        not name or name in {".", ".."} or ":" in name
        or "/" in name or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError(f"Unsafe RPF source name: {name!r}")


def _entry_id(archive_path: str, path: str) -> str:
    return f"{archive_path}::{path}"


@dataclass(frozen=True)
class _SourceSnapshot:
    rows: tuple[tuple[str, str, int, str], ...]
    file_count: int
    byte_count: int
    archive_count: int


class RpfArchiveBuilder:
    """Build a new OPEN-encrypted RPF and prove its recursive payload fidelity.

    A directory named ``example.rpf.source`` is authored as the nested archive
    ``example.rpf``. Prebuilt RPF files are refused so the validation report can
    account for every nested payload from its loose source.
    """

    def __init__(self, project_root: str | Path, gta_path: str | Path) -> None:
        self.service = RpfExplorerService(project_root, gta_path)

    @staticmethod
    def validation_path(output: str | Path) -> Path:
        archive = Path(output).expanduser().resolve()
        return archive.with_name(f"{archive.name}.validation.json")

    def _snapshot(self, root: Path) -> _SourceSnapshot:
        rows: list[tuple[str, str, int, str]] = []
        file_count = 0
        byte_count = 0
        archive_count = 1

        def scan(folder: Path, relative: Path, archive_depth: int) -> None:
            nonlocal file_count, byte_count, archive_count
            if _is_reparse_point(folder):
                raise ValueError(f"RPF source cannot contain links or reparse points: {folder}")
            children = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
            emitted: dict[str, str] = {}
            for child in children:
                _safe_name(child.name)
                if _is_reparse_point(child):
                    raise ValueError(
                        f"RPF source cannot contain links or reparse points: {child}"
                    )
                authored_name = child.name
                nested = child.is_dir() and child.name.casefold().endswith(
                    _NESTED_SOURCE_SUFFIX
                )
                if nested:
                    authored_name = child.name[:-len(".source")]
                    if authored_name.casefold() == ".rpf":
                        raise ValueError(f"Nested RPF source requires a file name: {child}")
                folded = authored_name.casefold()
                if folded in emitted:
                    raise ValueError(
                        "RPF source has a case-insensitive authored-name collision: "
                        f"{emitted[folded]!r} and {child.name!r}"
                    )
                emitted[folded] = child.name
                child_relative = relative / child.name
                if child.is_dir():
                    rows.append((child_relative.as_posix(), "archive" if nested else "directory", 0, ""))
                    if nested:
                        if archive_depth >= MAX_RPF_BUILD_DEPTH:
                            raise ValueError(
                                f"Nested RPF depth exceeds {MAX_RPF_BUILD_DEPTH}: {child}"
                            )
                        archive_count += 1
                        scan(child, child_relative, archive_depth + 1)
                    else:
                        scan(child, child_relative, archive_depth)
                    continue
                if not child.is_file():
                    raise ValueError(f"Unsupported RPF source object: {child}")
                if child.suffix.casefold() == ".rpf":
                    raise ValueError(
                        f"Prebuilt nested RPF is not provenance-safe: {child}; "
                        "use a matching .rpf.source directory"
                    )
                size = child.stat().st_size
                if size > MAX_RPF_BUILD_FILE_BYTES:
                    raise ValueError(
                        f"RPF source file exceeds the {MAX_RPF_BUILD_FILE_BYTES:,}-byte "
                        f"per-file limit: {child}"
                    )
                file_count += 1
                byte_count += size
                if file_count > MAX_RPF_BUILD_FILES:
                    raise ValueError(
                        f"RPF source exceeds the {MAX_RPF_BUILD_FILES:,}-file limit"
                    )
                if byte_count > MAX_RPF_BUILD_BYTES:
                    raise ValueError(
                        f"RPF source exceeds the {MAX_RPF_BUILD_BYTES:,}-byte limit"
                    )
                rows.append((child_relative.as_posix(), "file", size, _sha256_file(child)))

        scan(root, Path(), 0)
        return _SourceSnapshot(tuple(rows), file_count, byte_count, archive_count)

    def _run_builder(self, loose: Path, output: Path) -> None:
        completed = run_hidden(
            [
                self.service.patcher, "build-dlc", loose, output,
                "--gta-path", self.service.gta_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode or not output.is_file():
            detail = (completed.stderr or completed.stdout or "unknown helper error").strip()
            raise ValueError(f"RPF creation failed: {detail}")

    def _materialize_and_build(
        self, source: Path, loose: Path, output: Path, *, archive_path: str,
        expected_files: dict[str, dict[str, Any]], expected_directories: set[str],
        expected_archives: set[str],
    ) -> None:
        loose.mkdir(parents=True)
        for child in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir() and child.name.casefold().endswith(_NESTED_SOURCE_SUFFIX):
                archive_name = child.name[:-len(".source")]
                archive_file = loose / archive_name
                virtual_path = archive_name
                if archive_path:
                    nested_archive_path = f"{archive_path}!{virtual_path}"
                else:
                    nested_archive_path = virtual_path
                nested_loose = loose / f".{archive_name}.allin1-source"
                self._materialize_and_build(
                    child, nested_loose, archive_file,
                    archive_path=nested_archive_path,
                    expected_files=expected_files,
                    expected_directories=expected_directories,
                    expected_archives=expected_archives,
                )
                shutil.rmtree(nested_loose)
                expected_archives.add(nested_archive_path)
                expected_files[_entry_id(archive_path, virtual_path)] = (
                    _content_fingerprint(archive_file)
                )
                continue
            if child.is_dir():
                destination = loose / child.name
                destination.mkdir()
                virtual_directory = child.name
                expected_directories.add(_entry_id(archive_path, virtual_directory))
                self._copy_directory(
                    child, destination, archive_path=archive_path,
                    prefix=virtual_directory, expected_files=expected_files,
                    expected_directories=expected_directories,
                    expected_archives=expected_archives,
                )
                continue
            destination = loose / child.name
            shutil.copyfile(child, destination)
            if _sha256_file(child) != _sha256_file(destination):
                raise RuntimeError(f"RPF source copy did not preserve bytes: {child}")
            expected_files[_entry_id(archive_path, child.name)] = (
                _content_fingerprint(destination)
            )
        self._run_builder(loose, output)

    def _copy_directory(
        self, source: Path, destination: Path, *, archive_path: str, prefix: str,
        expected_files: dict[str, dict[str, Any]], expected_directories: set[str],
        expected_archives: set[str],
    ) -> None:
        for child in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
            virtual_path = f"{prefix}/{child.name}"
            if child.is_dir() and child.name.casefold().endswith(_NESTED_SOURCE_SUFFIX):
                archive_name = child.name[:-len(".source")]
                virtual_path = f"{prefix}/{archive_name}"
                archive_file = destination / archive_name
                nested_archive_path = (
                    f"{archive_path}!{virtual_path}" if archive_path else virtual_path
                )
                nested_loose = destination / f".{archive_name}.allin1-source"
                self._materialize_and_build(
                    child, nested_loose, archive_file,
                    archive_path=nested_archive_path,
                    expected_files=expected_files,
                    expected_directories=expected_directories,
                    expected_archives=expected_archives,
                )
                shutil.rmtree(nested_loose)
                expected_archives.add(nested_archive_path)
                expected_files[_entry_id(archive_path, virtual_path)] = (
                    _content_fingerprint(archive_file)
                )
            elif child.is_dir():
                target = destination / child.name
                target.mkdir()
                expected_directories.add(_entry_id(archive_path, virtual_path))
                self._copy_directory(
                    child, target, archive_path=archive_path, prefix=virtual_path,
                    expected_files=expected_files,
                    expected_directories=expected_directories,
                    expected_archives=expected_archives,
                )
            else:
                target = destination / child.name
                shutil.copyfile(child, target)
                if _sha256_file(child) != _sha256_file(target):
                    raise RuntimeError(f"RPF source copy did not preserve bytes: {child}")
                expected_files[_entry_id(archive_path, virtual_path)] = (
                    _content_fingerprint(target)
                )

    def build(self, source_folder: str | Path, output_rpf: str | Path) -> tuple[Path, Path]:
        """Create, recursively re-read, and atomically publish a brand-new RPF."""
        self.service._require_tool()
        source = Path(source_folder).expanduser().resolve()
        if not source.is_dir() or _is_reparse_point(source):
            raise ValueError(f"RPF build source must be a real directory: {source}")
        output = Path(output_rpf).expanduser().resolve()
        report_path = self.validation_path(output)
        if output.suffix.casefold() != ".rpf":
            raise ValueError("RPF build output must use a .rpf extension")
        if output == source or output.is_relative_to(source):
            raise ValueError("RPF build output must be outside its source tree")
        if output.is_relative_to(self.service.gta_path):
            raise ValueError(
                "New RPF authoring output must be outside the GTA V installation; "
                "install a validated package through the guarded package workflow"
            )
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"RPF build output already exists: {output}")
        if report_path.exists() or report_path.is_symlink():
            raise FileExistsError(f"RPF validation report already exists: {report_path}")
        output.parent.mkdir(parents=True, exist_ok=True)
        before = self._snapshot(source)
        required_bytes = before.byte_count * 2 + _COPY_MARGIN_BYTES
        if shutil.disk_usage(output.parent).free < required_bytes:
            raise ValueError("Not enough free disk space for staged RPF creation and validation")

        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{output.stem}.rpf-build-", dir=output.parent,
        )).resolve()
        stage_archive = stage_root / output.name
        stage_report = stage_root / report_path.name
        try:
            expected_files: dict[str, dict[str, Any]] = {}
            expected_directories: set[str] = set()
            expected_archives = {""}
            self._materialize_and_build(
                source, stage_root / "loose", stage_archive, archive_path="",
                expected_files=expected_files,
                expected_directories=expected_directories,
                expected_archives=expected_archives,
            )
            after = self._snapshot(source)
            if before != after:
                raise RuntimeError("RPF source changed during creation; output was discarded")

            index = self.service.index(stage_archive)
            actual_archives = {archive.path.casefold() for archive in index.archives}
            if actual_archives != {path.casefold() for path in expected_archives}:
                raise ValueError("Built RPF recursive archive tree does not match its source")
            actual_files = {
                entry.id.casefold(): entry for entry in index.entries
                if entry.kind != "directory"
            }
            expected_file_ids = {entry_id.casefold() for entry_id in expected_files}
            if set(actual_files) != expected_file_ids:
                raise ValueError("Built RPF file tree does not match its source")
            actual_directories = {
                entry.id.casefold() for entry in index.entries if entry.kind == "directory"
            }
            if actual_directories != {item.casefold() for item in expected_directories}:
                raise ValueError("Built RPF directory tree does not match its source")
            actual_fingerprints = {
                entry_id.casefold(): fingerprint
                for entry_id, fingerprint in self.service.entry_content_fingerprints(
                    index
                ).items()
            }
            expected_fingerprints = {
                entry_id.casefold(): fingerprint
                for entry_id, fingerprint in expected_files.items()
            }
            verification_fields = ("mode", "logical_size", "canonical_sha256")
            if any(
                any(actual_fingerprints[entry_id][field] != expected[field]
                    for field in verification_fields)
                for entry_id, expected in expected_fingerprints.items()
            ):
                raise ValueError("Built RPF payload hashes do not match their source")
            canonical_resources = sum(
                fingerprint["mode"] == "rsc7_canonical"
                for fingerprint in actual_fingerprints.values()
            )

            report: dict[str, Any] = {
                "schema_version": RPF_BUILD_REPORT_SCHEMA,
                "operation": "rpf_archive_build",
                "status": "verified",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": str(source),
                "output": str(output),
                "edition": index.edition,
                "archive": {
                    "size": stage_archive.stat().st_size,
                    "sha256": _sha256_file(stage_archive),
                    "encryption": index.archives[0].encryption,
                },
                "summary": {
                    "source_files": before.file_count,
                    "source_bytes": before.byte_count,
                    "archives": len(index.archives),
                    "directories": len(actual_directories),
                    "entries": len(actual_files),
                    "payloads_exactly_verified": len(actual_fingerprints),
                    "byte_exact_payloads": len(actual_fingerprints) - canonical_resources,
                    "canonical_resource_payloads": canonical_resources,
                },
                "payload_verification": [
                    {
                        "entry": entry_id,
                        "mode": fingerprint["mode"],
                        "raw_size": fingerprint["size"],
                        "logical_size": fingerprint["logical_size"],
                        "raw_sha256": fingerprint["raw_sha256"],
                        "canonical_sha256": fingerprint["canonical_sha256"],
                    }
                    for entry_id, fingerprint in sorted(actual_fingerprints.items())
                ],
                "source_snapshot": [
                    {"path": row[0], "kind": row[1], "size": row[2], "sha256": row[3]}
                    for row in before.rows
                ],
                "safety": {
                    "output_was_new": True,
                    "source_unchanged": True,
                    "recursive_index_verified": True,
                    "exact_logical_payload_hashes_verified": True,
                    "resource_recompression_normalized": canonical_resources,
                    "stock_game_files_modified": False,
                },
            }
            stage_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            stage_archive.replace(output)
            try:
                stage_report.replace(report_path)
            except Exception:
                output.unlink(missing_ok=True)
                raise
            return output, report_path
        finally:
            if stage_root.is_dir() and stage_root.parent == output.parent:
                shutil.rmtree(stage_root)
