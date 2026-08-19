"""Structured RPF inspection and guarded replacement transactions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from allin1_sdk.paths import user_data_root
from allin1_sdk.processes import run_hidden


RPF_REPLACEMENT_PLAN_SCHEMA = 3
RPF_TRANSACTION_RECEIPT_SCHEMA = 2
RPF_MULTI_CHANGE_PLAN_SCHEMA = 1
RPF_MULTI_TRANSACTION_RECEIPT_SCHEMA = 1
_GTA_PROCESS_NAMES = {"gta5.exe", "gta5_enhanced.exe"}
_COPY_MARGIN_BYTES = 64 * 1024 * 1024
_MAX_CANARY_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_NESTED_WRITE_DEPTH = 8
_MAX_SUBTREE_FILES = 25_000
_MAX_SUBTREE_LOGICAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MULTI_ENTRY_CHANGES = 1_000
_RPF_ACTIONS = {"replace", "add", "delete"}
_RPF_DIFF_ENTRY_FIELDS = (
    "archive_path", "path", "kind", "size", "stored_size", "name_hash",
    "short_name_hash", "encrypted", "compressed", "resource_version",
    "system_size", "graphics_size", "system_flags", "graphics_flags",
    "child_count",
)
_RPF_DIFF_ARCHIVE_FIELDS = (
    "path", "name", "version", "encryption", "size", "entry_count",
)
ProgressCallback = Callable[[str, int], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _read_json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {label}: expected a JSON object")
    return payload


def _running_gta_processes() -> tuple[str, ...]:
    """Return running GTA executables, refusing writes if Windows cannot be queried."""
    if os.name != "nt":
        return ()
    completed = run_hidden(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "unknown tasklist error").strip()
        raise RuntimeError(f"Could not verify that GTA V is closed: {detail}")
    found = {
        line.split('","', 1)[0].strip().strip('"').casefold()
        for line in completed.stdout.splitlines() if line.strip()
    }
    return tuple(sorted(found.intersection(_GTA_PROCESS_NAMES)))


def _safe_virtual_path(value: str, *, allow_empty: bool = False) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized and allow_empty:
        return ""
    if (not normalized or ":" in normalized
            or any(ord(character) < 32 for character in normalized) or any(
        part in {"", ".", ".."} for part in normalized.replace("!", "/").split("/")
    )):
        raise ValueError(f"Unsafe RPF virtual path: {value!r}")
    return normalized


@dataclass(frozen=True)
class RpfArchiveRecord:
    path: str
    name: str
    version: int
    encryption: str
    size: int
    entry_count: int


@dataclass(frozen=True)
class RpfEntryRecord:
    id: str
    archive_path: str
    path: str
    name: str
    kind: str
    size: int
    stored_size: int
    name_hash: int = 0
    short_name_hash: int = 0
    offset: int | None = None
    encrypted: bool | None = None
    compressed: bool | None = None
    resource_version: int | None = None
    system_size: int | None = None
    graphics_size: int | None = None
    system_flags: str | None = None
    graphics_flags: str | None = None
    child_count: int | None = None

    @property
    def suffix(self) -> str:
        return PurePosixPath(self.path).suffix.casefold()

    @property
    def virtual_name(self) -> str:
        return f"{self.archive_path}::{self.path}" if self.archive_path else self.path


@dataclass(frozen=True)
class RpfIndex:
    source: Path
    edition: str
    archive_size: int
    archives: tuple[RpfArchiveRecord, ...]
    entries: tuple[RpfEntryRecord, ...]
    warnings: tuple[str, ...] = ()
    schema_version: int = 1
    _by_id: dict[str, RpfEntryRecord] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        lookup: dict[str, RpfEntryRecord] = {}
        archive_paths = [archive.path.casefold() for archive in self.archives]
        if not self.archives or "" not in archive_paths:
            raise ValueError("RPF index does not contain a root archive")
        if len(archive_paths) != len(set(archive_paths)):
            raise ValueError("RPF index contains duplicate archive paths")
        for entry in self.entries:
            expected_id = f"{entry.archive_path}::{entry.path}"
            if entry.id != expected_id:
                raise ValueError(f"RPF entry id does not match its path: {entry.id}")
            if entry.archive_path.casefold() not in archive_paths:
                raise ValueError(f"RPF entry references an unknown archive: {entry.archive_path}")
            if entry.kind not in {"directory", "resource", "binary", "archive"}:
                raise ValueError(f"Unknown RPF entry kind: {entry.kind}")
            if entry.size < 0 or entry.stored_size < 0:
                raise ValueError(f"Negative RPF entry size: {entry.id}")
            if entry.id.casefold() in lookup:
                raise ValueError(f"Duplicate RPF entry id: {entry.id}")
            lookup[entry.id.casefold()] = entry
        object.__setattr__(self, "_by_id", lookup)

    @classmethod
    def load(cls, path: str | Path) -> "RpfIndex":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid RPF index: {exc}") from exc
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported RPF index schema")
        try:
            archives = tuple(
                RpfArchiveRecord(
                    path=_safe_virtual_path(item.get("path", ""), allow_empty=True),
                    name=str(item["name"]), version=int(item["version"]),
                    encryption=str(item["encryption"]), size=int(item["size"]),
                    entry_count=int(item["entry_count"]),
                )
                for item in payload["archives"]
            )
            allowed = set(RpfEntryRecord.__dataclass_fields__)
            entries = []
            for authored in payload["entries"]:
                item = {key: value for key, value in authored.items() if key in allowed}
                item["archive_path"] = _safe_virtual_path(
                    str(item.get("archive_path", "")), allow_empty=True,
                )
                item["path"] = _safe_virtual_path(str(item["path"]))
                item["id"] = str(item["id"])
                item["name"] = str(item["name"])
                item["kind"] = str(item["kind"])
                item["size"] = int(item["size"])
                item["stored_size"] = int(item["stored_size"])
                entries.append(RpfEntryRecord(**item))
            source = Path(payload["source"]).resolve()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed RPF index: {exc}") from exc
        return cls(
            source=source, edition=str(payload["edition"]),
            archive_size=int(payload["archive_size"]), archives=archives,
            entries=tuple(entries),
            warnings=tuple(str(item) for item in payload.get("warnings", ())),
        )

    def entry(self, entry_id: str) -> RpfEntryRecord:
        try:
            return self._by_id[entry_id.casefold()]
        except KeyError as exc:
            raise KeyError(f"Unknown RPF entry: {entry_id}") from exc

    def search(
        self, query: str = "", *, kinds: Iterable[str] = (), suffix: str = "",
    ) -> tuple[RpfEntryRecord, ...]:
        text = query.strip().casefold()
        allowed_kinds = {value.casefold() for value in kinds}
        wanted_suffix = suffix.casefold()
        if wanted_suffix and not wanted_suffix.startswith("."):
            wanted_suffix = "." + wanted_suffix
        return tuple(
            entry for entry in self.entries
            if (not text or text in entry.virtual_name.casefold())
            and (not allowed_kinds or entry.kind.casefold() in allowed_kinds)
            and (not wanted_suffix or entry.suffix == wanted_suffix)
        )

    def suffix_counts(self) -> dict[str, int]:
        return dict(Counter(
            entry.suffix or "(none)" for entry in self.entries
            if entry.kind != "directory"
        ).most_common())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": str(self.source), "edition": self.edition,
            "archive_size": self.archive_size,
            "archives": [asdict(item) for item in self.archives],
            "entries": [asdict(item) for item in self.entries],
            "warnings": list(self.warnings),
        }

    def export(self, destination: str | Path) -> tuple[Path, Path]:
        target = Path(destination).resolve()
        if target.suffix.casefold() != ".json":
            target = target.with_suffix(".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        csv_path = target.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "archive_path", "path", "kind", "size", "stored_size",
                "resource_version", "encrypted", "compressed", "offset",
            ])
            writer.writeheader()
            for entry in self.entries:
                writer.writerow({key: getattr(entry, key) for key in writer.fieldnames})
        return target, csv_path


class RpfExplorerService:
    """Invoke the pinned helper for inspection and explicit safe transactions."""

    def __init__(
        self, project_root: str | Path, gta_path: str | Path, *,
        workspace_roots: Iterable[str | Path] = (),
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.gta_path = Path(gta_path).resolve()
        self.patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        mods_root = (self.gta_path / "mods").resolve()
        roots: list[Path] = []
        for authored in workspace_roots:
            root = Path(authored).expanduser().resolve()
            if root == self.gta_path or (
                root.is_relative_to(self.gta_path) and not root.is_relative_to(mods_root)
            ):
                raise ValueError(
                    "An RPF workspace cannot authorize stock GTA V files; use a directory "
                    "outside the game installation or its mods directory."
                )
            roots.append(root)
        self.workspace_roots = tuple(dict.fromkeys(roots))

    def _require_tool(self) -> None:
        if not self.patcher.is_file():
            raise FileNotFoundError(
                "RpfPatcher.exe is missing; run runtools.ps1 to build the SDK helper."
            )
        if not self.gta_path.is_dir():
            raise FileNotFoundError(f"GTA V directory not found: {self.gta_path}")

    def index(self, archive: str | Path) -> RpfIndex:
        self._require_tool()
        source = Path(archive).resolve()
        if not source.is_file() or source.suffix.casefold() != ".rpf":
            raise ValueError("RPF explorer requires a loose .rpf archive")
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-index-") as temporary:
            output = Path(temporary) / "index.json"
            completed = run_hidden(
                [self.patcher, "index-json", self.gta_path, source, output],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not output.is_file():
                detail = (completed.stderr or completed.stdout or "unknown helper error").strip()
                raise ValueError(f"RPF indexing failed: {detail}")
            result = RpfIndex.load(output)
        if result.source != source:
            raise ValueError("RPF helper returned an index for a different archive")
        return result

    def extract(
        self, index: RpfIndex, entry: RpfEntryRecord, destination: str | Path,
    ) -> Path:
        self._require_tool()
        if entry.kind == "directory":
            raise ValueError("Directories cannot be extracted as a single asset")
        if index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        target = Path(destination).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = run_hidden(
            [
                self.patcher, "extract-virtual-entry", self.gta_path,
                index.source, entry.archive_path, entry.path, target,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if completed.returncode or not target.is_file():
            detail = (completed.stderr or completed.stdout or "unknown helper error").strip()
            raise ValueError(f"RPF extraction failed: {detail}")
        return target

    def extract_subtree(
        self, index: RpfIndex, destination: str | Path, *,
        archive_path: str = "", directory_path: str = "",
    ) -> Path:
        """Export one virtual archive directory through a single guarded scan.

        Nested RPF files contained by the selected directory are exported as
        archive files. Their internal entries retain a separate virtual archive
        identity and are only exported when that archive is selected explicitly.
        """
        self._require_tool()
        selected_archive = _safe_virtual_path(archive_path, allow_empty=True)
        selected_directory = _safe_virtual_path(directory_path, allow_empty=True)
        archive_paths = {item.path.casefold() for item in index.archives}
        if selected_archive.casefold() not in archive_paths:
            raise ValueError(f"RPF archive path was not indexed: {selected_archive}")
        if selected_directory:
            directory_id = f"{selected_archive}::{selected_directory}"
            try:
                directory = index.entry(directory_id)
            except KeyError as exc:
                raise ValueError(
                    f"RPF directory was not indexed: {directory_id}"
                ) from exc
            if directory.kind != "directory":
                raise ValueError(f"RPF subtree selection is not a directory: {directory_id}")

        prefix = f"{selected_directory}/" if selected_directory else ""
        selected = tuple(
            entry for entry in index.entries
            if entry.archive_path.casefold() == selected_archive.casefold()
            and entry.kind != "directory"
            and (not prefix or entry.path.casefold().startswith(prefix.casefold()))
        )
        if not selected:
            label = f"{selected_archive}::{selected_directory}".strip(":") or "root"
            raise ValueError(f"RPF subtree contains no extractable files: {label}")
        if len(selected) > _MAX_SUBTREE_FILES:
            raise ValueError(
                f"RPF subtree contains {len(selected):,} files; the guarded export limit "
                f"is {_MAX_SUBTREE_FILES:,}"
            )
        logical_bytes = sum(entry.size for entry in selected)
        if logical_bytes > _MAX_SUBTREE_LOGICAL_BYTES:
            raise ValueError(
                f"RPF subtree is {logical_bytes:,} logical bytes; the guarded export "
                f"limit is {_MAX_SUBTREE_LOGICAL_BYTES:,}"
            )

        exports: list[tuple[RpfEntryRecord, str]] = []
        destinations: set[str] = set()
        for entry in sorted(selected, key=lambda item: item.path.casefold()):
            relative = entry.path[len(prefix):] if prefix else entry.path
            relative = _safe_virtual_path(relative)
            folded = relative.casefold()
            if folded == ".allin1-rpf-export.json":
                raise ValueError(
                    "RPF subtree contains the reserved export-manifest path: "
                    f"{relative}"
                )
            if folded in destinations:
                raise ValueError(
                    f"RPF subtree contains a case-insensitive output collision: {relative}"
                )
            destinations.add(folded)
            exports.append((entry, relative))

        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"RPF subtree destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_sha256 = _sha256_file(index.source)
        if index.source.stat().st_size != index.archive_size:
            raise ValueError("RPF source size changed after it was indexed; index it again")
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.allin1-stage-", dir=target.parent,
        )).resolve()
        try:
            with tempfile.TemporaryDirectory(prefix="allin1-rpf-export-") as temporary:
                manifest = Path(temporary) / "entries.tsv"
                manifest.write_text("".join(
                    f"{entry.archive_path}\t{entry.path}\t{relative}\n"
                    for entry, relative in exports
                ), encoding="utf-8")
                completed = run_hidden(
                    [
                        self.patcher, "extract-virtual-entries", self.gta_path,
                        index.source, manifest, staging,
                    ],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
            if completed.returncode:
                detail = (
                    completed.stderr or completed.stdout or "unknown helper error"
                ).strip()
                raise ValueError(f"RPF subtree extraction failed: {detail}")

            files: list[dict[str, Any]] = []
            for entry, relative in exports:
                output = (staging / Path(relative)).resolve()
                if not output.is_relative_to(staging) or not output.is_file():
                    raise ValueError(
                        f"RPF helper did not produce the expected subtree file: {relative}"
                    )
                files.append({
                    "archive_path": entry.archive_path,
                    "entry_path": entry.path,
                    "relative_path": relative,
                    "kind": entry.kind,
                    "indexed_size": entry.size,
                    "actual_size": output.stat().st_size,
                    "sha256": _sha256_file(output),
                })
            if _sha256_file(index.source) != source_sha256:
                raise RuntimeError(
                    "RPF source changed during read-only subtree extraction; output was discarded"
                )
            export_manifest = {
                "schema_version": 1,
                "operation": "rpf_subtree_export",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "path": str(index.source),
                    "edition": index.edition,
                    "size": index.archive_size,
                    "sha256": source_sha256,
                },
                "selection": {
                    "archive_path": selected_archive,
                    "directory_path": selected_directory,
                },
                "file_count": len(files),
                "logical_bytes": logical_bytes,
                "files": files,
            }
            _write_json_atomic(staging / ".allin1-rpf-export.json", export_manifest)
            staging.replace(target)
            return target
        except Exception:
            if staging.is_dir() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise

    def compare_indexes(
        self, left: RpfIndex, right: RpfIndex, *, exact_content: bool = False,
    ) -> dict[str, Any]:
        """Compare two recursively indexed archives without modifying either source."""
        self._require_tool()
        for label, index in (("left", left), ("right", right)):
            if not index.source.is_file():
                raise ValueError(f"RPF diff {label} source no longer exists: {index.source}")
            if index.source.stat().st_size != index.archive_size:
                raise ValueError(
                    f"RPF diff {label} source changed after indexing; index it again"
                )
        left_source_hash = _sha256_file(left.source)
        right_source_hash = _sha256_file(right.source)
        left_hashes: dict[str, str] = {}
        right_hashes: dict[str, str] = {}
        if exact_content:
            left_hashes = self._batch_content_hashes(
                left, (entry for entry in left.entries if entry.kind != "directory"),
                expected_source_sha256=left_source_hash,
            )
            right_hashes = self._batch_content_hashes(
                right, (entry for entry in right.entries if entry.kind != "directory"),
                expected_source_sha256=right_source_hash,
            )

        left_entries = {
            (entry.archive_path.casefold(), entry.path.casefold()): entry
            for entry in left.entries
        }
        right_entries = {
            (entry.archive_path.casefold(), entry.path.casefold()): entry
            for entry in right.entries
        }

        def describe_entry(
            entry: RpfEntryRecord, hashes: dict[str, str],
        ) -> dict[str, Any]:
            item = {
                "archive_path": entry.archive_path,
                "path": entry.path,
                "kind": entry.kind,
                "size": entry.size,
                "stored_size": entry.stored_size,
            }
            if entry.id in hashes:
                item["sha256"] = hashes[entry.id]
            return item

        added = [
            describe_entry(right_entries[key], right_hashes)
            for key in sorted(right_entries.keys() - left_entries.keys())
        ]
        removed = [
            describe_entry(left_entries[key], left_hashes)
            for key in sorted(left_entries.keys() - right_entries.keys())
        ]
        modified: list[dict[str, Any]] = []
        unchanged = 0
        content_compared = 0
        for key in sorted(left_entries.keys() & right_entries.keys()):
            before = left_entries[key]
            after = right_entries[key]
            changes: dict[str, dict[str, Any]] = {}
            for field_name in _RPF_DIFF_ENTRY_FIELDS:
                old = getattr(before, field_name)
                new = getattr(after, field_name)
                if old != new:
                    changes[field_name] = {"left": old, "right": new}
            if exact_content and before.kind != "directory" and after.kind != "directory":
                content_compared += 1
                old_hash = left_hashes[before.id]
                new_hash = right_hashes[after.id]
                if old_hash != new_hash:
                    changes["sha256"] = {"left": old_hash, "right": new_hash}
            if changes:
                modified.append({
                    "identity": {
                        "archive_path": after.archive_path, "path": after.path,
                    },
                    "left": describe_entry(before, left_hashes),
                    "right": describe_entry(after, right_hashes),
                    "changes": changes,
                })
            else:
                unchanged += 1

        left_archives = {item.path.casefold(): item for item in left.archives}
        right_archives = {item.path.casefold(): item for item in right.archives}

        def describe_archive(record: RpfArchiveRecord) -> dict[str, Any]:
            return asdict(record)

        archives_added = [
            describe_archive(right_archives[key])
            for key in sorted(right_archives.keys() - left_archives.keys())
        ]
        archives_removed = [
            describe_archive(left_archives[key])
            for key in sorted(left_archives.keys() - right_archives.keys())
        ]
        archives_modified: list[dict[str, Any]] = []
        archives_unchanged = 0
        for key in sorted(left_archives.keys() & right_archives.keys()):
            before = left_archives[key]
            after = right_archives[key]
            changes = {}
            for field_name in _RPF_DIFF_ARCHIVE_FIELDS:
                old = getattr(before, field_name)
                new = getattr(after, field_name)
                if old != new:
                    changes[field_name] = {"left": old, "right": new}
            if changes:
                archives_modified.append({
                    "identity": after.path,
                    "left": describe_archive(before),
                    "right": describe_archive(after),
                    "changes": changes,
                })
            else:
                archives_unchanged += 1

        if _sha256_file(left.source) != left_source_hash:
            raise RuntimeError("Left RPF changed during read-only diff; report was discarded")
        if _sha256_file(right.source) != right_source_hash:
            raise RuntimeError("Right RPF changed during read-only diff; report was discarded")
        return {
            "schema_version": 1,
            "operation": "rpf_archive_diff",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "comparison_mode": "exact_content" if exact_content else "metadata",
            "left": {
                "path": str(left.source), "edition": left.edition,
                "size": left.archive_size, "sha256": left_source_hash,
            },
            "right": {
                "path": str(right.source), "edition": right.edition,
                "size": right.archive_size, "sha256": right_source_hash,
            },
            "summary": {
                "added": len(added), "removed": len(removed),
                "modified": len(modified), "unchanged": unchanged,
                "content_compared": content_compared,
                "archives_added": len(archives_added),
                "archives_removed": len(archives_removed),
                "archives_modified": len(archives_modified),
                "archives_unchanged": archives_unchanged,
            },
            "entries": {
                "added": added, "removed": removed, "modified": modified,
            },
            "archives": {
                "added": archives_added, "removed": archives_removed,
                "modified": archives_modified,
            },
        }

    def _batch_content_hashes(
        self, index: RpfIndex, entries: Iterable[RpfEntryRecord], *,
        expected_source_sha256: str,
    ) -> dict[str, str]:
        selected = tuple(entries)
        if len(selected) > _MAX_SUBTREE_FILES:
            raise ValueError(
                f"Exact RPF diff contains {len(selected):,} files on one side; "
                f"the guarded limit is {_MAX_SUBTREE_FILES:,}"
            )
        logical_bytes = sum(entry.size for entry in selected)
        if logical_bytes > _MAX_SUBTREE_LOGICAL_BYTES:
            raise ValueError(
                f"Exact RPF diff requires {logical_bytes:,} logical bytes on one side; "
                f"the guarded limit is {_MAX_SUBTREE_LOGICAL_BYTES:,}"
            )
        if not selected:
            return {}
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-diff-") as temporary:
            root = Path(temporary)
            if shutil.disk_usage(root).free < logical_bytes + _COPY_MARGIN_BYTES:
                raise ValueError("Not enough temporary disk space for exact RPF diff")
            manifest = root / "entries.tsv"
            output_root = root / "content"
            manifest.write_text("".join(
                f"{entry.archive_path}\t{entry.path}\t{number:08d}.bin\n"
                for number, entry in enumerate(selected)
            ), encoding="utf-8")
            completed = run_hidden(
                [
                    self.patcher, "extract-virtual-entries", self.gta_path,
                    index.source, manifest, output_root,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode:
                detail = (
                    completed.stderr or completed.stdout or "unknown helper error"
                ).strip()
                raise ValueError(f"Exact RPF diff extraction failed: {detail}")
            hashes: dict[str, str] = {}
            for number, entry in enumerate(selected):
                output = output_root / f"{number:08d}.bin"
                if not output.is_file():
                    raise ValueError(
                        f"Exact RPF diff did not extract the expected entry: {entry.id}"
                    )
                hashes[entry.id] = _sha256_file(output)
        if _sha256_file(index.source) != expected_source_sha256:
            raise RuntimeError("RPF changed during exact content extraction")
        return hashes

    @staticmethod
    def export_diff(
        report: dict[str, Any], destination: str | Path,
    ) -> tuple[Path, Path]:
        if report.get("operation") != "rpf_archive_diff":
            raise ValueError("Expected an RPF archive diff report")
        authored = Path(destination).expanduser().resolve()
        base = authored.with_suffix("") if authored.suffix.casefold() in {".json", ".md"} else authored
        json_path = base.with_suffix(".json")
        markdown_path = base.with_suffix(".md")
        _write_json_atomic(json_path, report)

        def cell(value: object) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")

        summary = report["summary"]
        lines = [
            "# RPF archive diff", "",
            f"- Mode: `{report['comparison_mode']}`",
            f"- Left: `{report['left']['path']}`",
            f"- Right: `{report['right']['path']}`", "",
            "## Summary", "",
            "| Added | Removed | Modified | Unchanged | Exact contents compared |",
            "|---:|---:|---:|---:|---:|",
            f"| {summary['added']} | {summary['removed']} | {summary['modified']} | "
            f"{summary['unchanged']} | {summary['content_compared']} |", "",
            "## Archive records", "",
            "| Added | Removed | Modified | Unchanged |",
            "|---:|---:|---:|---:|",
            f"| {summary['archives_added']} | {summary['archives_removed']} | "
            f"{summary['archives_modified']} | {summary['archives_unchanged']} |", "",
        ]
        for title, key in (("Added entries", "added"), ("Removed entries", "removed")):
            lines.extend([f"## {title}", ""])
            items = report["entries"][key]
            if not items:
                lines.extend(["None.", ""])
                continue
            lines.extend(["| Archive | Path | Kind | Size | SHA-256 |", "|---|---|---|---:|---|"])
            for item in items:
                lines.append(
                    f"| {cell(item['archive_path'] or 'root')} | {cell(item['path'])} | "
                    f"{cell(item['kind'])} | {item['size']} | {item.get('sha256', '')} |"
                )
            lines.append("")
        lines.extend(["## Modified entries", ""])
        if not report["entries"]["modified"]:
            lines.extend(["None.", ""])
        else:
            lines.extend(["| Archive | Path | Changed fields |", "|---|---|---|"])
            for item in report["entries"]["modified"]:
                identity = item["identity"]
                lines.append(
                    f"| {cell(identity['archive_path'] or 'root')} | "
                    f"{cell(identity['path'])} | "
                    f"{cell(', '.join(item['changes']))} |"
                )
            lines.append("")
        _write_text_atomic(markdown_path, "\n".join(lines).rstrip() + "\n")
        return json_path, markdown_path

    def multi_change_plan(
        self, index: RpfIndex, authored_changes: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create one guarded plan for many root and nested entry changes."""
        self._require_tool()
        if index.source.suffix.casefold() != ".rpf" or not index.source.is_file():
            raise ValueError("RPF multi-change plans require a loose .rpf archive")
        requested = tuple(authored_changes)
        if not requested:
            raise ValueError("RPF multi-change plan contains no changes")
        if len(requested) > _MAX_MULTI_ENTRY_CHANGES:
            raise ValueError(
                f"RPF multi-change plans are limited to {_MAX_MULTI_ENTRY_CHANGES:,} changes"
            )
        archive_hash = _sha256_file(index.source)
        prepared: list[dict[str, Any]] = []
        existing_entries: list[RpfEntryRecord] = []
        seen: set[tuple[str, str]] = set()
        warnings: list[str] = []
        archive_records = {item.path.casefold(): item.path for item in index.archives}
        for number, authored in enumerate(requested, start=1):
            if not isinstance(authored, dict):
                raise ValueError(f"RPF multi-change item {number} is not an object")
            authored_action = str(authored.get("action", "")).casefold()
            if authored_action not in _RPF_ACTIONS | {"upsert"}:
                raise ValueError(f"RPF multi-change item {number} has an invalid action")
            archive_path = _safe_virtual_path(
                str(authored.get("archive_path", "")), allow_empty=True,
            )
            entry_path = _safe_virtual_path(str(authored.get("entry", "")))
            self._require_supported_nested_archive(index, archive_path)
            archive_path = archive_records[archive_path.casefold()]
            identity = (archive_path.casefold(), entry_path.casefold())
            if identity in seen:
                raise ValueError(
                    f"RPF multi-change plan targets an entry more than once: "
                    f"{archive_path}::{entry_path}"
                )
            seen.add(identity)
            try:
                existing = index.entry(f"{archive_path}::{entry_path}")
            except KeyError:
                existing = None
            action = (
                ("replace" if existing is not None else "add")
                if authored_action == "upsert" else authored_action
            )
            if action == "add":
                if existing is not None:
                    raise ValueError(
                        f"RPF add target already exists: {archive_path}::{entry_path}"
                    )
                self._require_existing_parent(index, archive_path, entry_path)
            elif existing is None:
                raise ValueError(
                    f"RPF {action} target does not exist: {archive_path}::{entry_path}"
                )
            elif existing.kind == "directory" or (
                action == "delete" and existing.kind == "archive"
            ):
                raise ValueError(
                    f"RPF {action} cannot target {existing.kind}: {existing.virtual_name}"
                )

            payload_meta: dict[str, Any] | None = None
            if action in {"replace", "add"}:
                payload_value = authored.get("payload")
                if not isinstance(payload_value, (str, Path)):
                    raise ValueError(f"RPF {action} item {number} requires a payload")
                payload_authored = Path(payload_value).expanduser()
                if payload_authored.is_symlink():
                    raise ValueError("RPF payload cannot be a symbolic link")
                payload = payload_authored.resolve()
                if not payload.is_file():
                    raise FileNotFoundError(f"RPF payload not found: {payload}")
                payload_meta = {
                    "path": str(payload), "size": payload.stat().st_size,
                    "sha256": _sha256_file(payload),
                }
                if existing is not None and (
                    payload.suffix.casefold() != Path(existing.name).suffix.casefold()
                ):
                    warnings.append(
                        f"Payload extension differs for {existing.virtual_name}; verify "
                        "the native resource type before applying."
                    )
            elif authored.get("payload") not in (None, ""):
                raise ValueError(f"RPF delete item {number} cannot include a payload")
            if existing is not None:
                existing_entries.append(existing)
            prepared.append({
                "action": action, "archive_path": archive_path,
                "entry": entry_path, "existing": existing,
                "payload": payload_meta,
            })

        # Replacing a child RPF while also editing its internals would produce
        # order-dependent results, so the reviewed plan must choose one operation.
        containers = {
            (
                item["entry"] if not item["archive_path"]
                else f"{item['archive_path']}!{item['entry']}"
            ).casefold()
            for item in prepared
            if item["existing"] is not None and item["existing"].kind == "archive"
        }
        for container in containers:
            if any(
                other["archive_path"].casefold() == container
                or other["archive_path"].casefold().startswith(container + "!")
                for other in prepared
            ):
                raise ValueError(
                    "RPF multi-change plan cannot replace an archive and also edit "
                    f"its internal tree: {container}"
                )

        original_hashes = self._batch_content_hashes(
            index, existing_entries, expected_source_sha256=archive_hash,
        )
        changes: list[dict[str, Any]] = []
        for item in prepared:
            existing = item.pop("existing")
            original = (
                {"exists": False, "size": 0, "sha256": None}
                if existing is None else {
                    "exists": True, "size": existing.size,
                    "sha256": original_hashes[existing.id],
                }
            )
            changes.append({**item, "original": original})
        if _sha256_file(index.source) != archive_hash:
            raise RuntimeError("RPF changed while the multi-change plan was being created")

        target_scope = self._target_scope(index.source)
        authorized_root = self._authorized_workspace_root(index.source)
        blocking_reasons = []
        if target_scope == "unsafe":
            blocking_reasons.append(
                "The archive is neither inside the selected GTA V mods directory nor an "
                "explicitly authorized external workspace."
            )
        plan_id = self._multi_plan_identifier(
            index.source, archive_hash, changes, index.edition, target_scope,
            str(authorized_root or ""),
        )
        return {
            "schema_version": RPF_MULTI_CHANGE_PLAN_SCHEMA,
            "operation": "rpf_multi_entry_change", "plan_id": plan_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked" if blocking_reasons else "ready",
            "archive": str(index.source),
            "archive_size": index.source.stat().st_size,
            "archive_sha256": archive_hash, "edition": index.edition,
            "target_scope": target_scope,
            "authorized_root": str(authorized_root) if authorized_root else None,
            "changes": changes, "blocking_reasons": blocking_reasons,
            "warnings": warnings,
            "safety": {
                "writes_performed": False, "backup_required": True,
                "single_outer_archive_commit": True,
                "post_write_hash_verification_required": True,
                "rollback_required": True, "game_must_be_closed": True,
                "stock_archive_write_allowed": False,
            },
        }

    def subtree_sync_plan(
        self, index: RpfIndex, export_directory: str | Path,
    ) -> dict[str, Any]:
        """Convert edits in a verified subtree export into one atomic plan."""
        authored_root = Path(export_directory).expanduser()
        if authored_root.is_symlink():
            raise ValueError("RPF subtree workspace cannot be a symbolic link")
        root = authored_root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"RPF subtree export not found: {root}")
        manifest_path = root / ".allin1-rpf-export.json"
        manifest = _read_json_object(manifest_path, "RPF subtree export manifest")
        if manifest.get("schema_version") != 1 or manifest.get(
            "operation"
        ) != "rpf_subtree_export":
            raise ValueError("Unsupported RPF subtree export manifest")
        source = manifest.get("source")
        selection = manifest.get("selection")
        records = manifest.get("files")
        if not isinstance(source, dict) or not isinstance(selection, dict):
            raise ValueError("RPF subtree export is missing source or selection metadata")
        if not isinstance(records, list) or not records:
            raise ValueError("RPF subtree export contains no file records")
        if len(records) > _MAX_SUBTREE_FILES:
            raise ValueError("RPF subtree export exceeds the guarded file limit")
        source_hash = source.get("sha256")
        if not _is_sha256(source_hash):
            raise ValueError("RPF subtree export has an invalid source hash")
        if str(source.get("edition", "")).casefold() != index.edition.casefold():
            raise ValueError("RPF subtree export edition does not match the target archive")
        if _sha256_file(index.source) != source_hash:
            raise ValueError(
                "RPF target does not match the subtree export base; export it again "
                "or use an unchanged byte-identical mods copy"
            )
        archive_path = _safe_virtual_path(
            str(selection.get("archive_path", "")), allow_empty=True,
        )
        directory_path = _safe_virtual_path(
            str(selection.get("directory_path", "")), allow_empty=True,
        )
        self._require_supported_nested_archive(index, archive_path)
        if manifest.get("file_count") != len(records):
            raise ValueError("RPF subtree export file count does not match its manifest")

        recorded: dict[str, dict[str, Any]] = {}
        for number, item in enumerate(records, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"RPF subtree export file record {number} is invalid")
            relative = _safe_virtual_path(str(item.get("relative_path", "")))
            entry_path = _safe_virtual_path(str(item.get("entry_path", "")))
            item_archive = _safe_virtual_path(
                str(item.get("archive_path", "")), allow_empty=True,
            )
            expected_entry = (
                f"{directory_path}/{relative}" if directory_path else relative
            )
            if item_archive.casefold() != archive_path.casefold() or (
                entry_path.casefold() != expected_entry.casefold()
            ):
                raise ValueError(
                    f"RPF subtree export record escapes its selection: {relative}"
                )
            if not _is_sha256(item.get("sha256")):
                raise ValueError(f"RPF subtree export record has an invalid hash: {relative}")
            if relative.casefold() in recorded:
                raise ValueError(
                    f"RPF subtree export contains a duplicate path: {relative}"
                )
            try:
                current_entry = index.entry(f"{archive_path}::{entry_path}")
            except KeyError as exc:
                raise ValueError(
                    f"RPF subtree export base entry is absent: {entry_path}"
                ) from exc
            if current_entry.kind == "directory":
                raise ValueError(f"RPF subtree export file record is a directory: {entry_path}")
            recorded[relative.casefold()] = {
                **item, "relative_path": relative, "entry_path": entry_path,
            }

        workspace_files: dict[str, tuple[str, Path]] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"RPF subtree workspace contains a symbolic link: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.casefold() == ".allin1-rpf-export.json":
                continue
            relative = _safe_virtual_path(relative)
            if relative.casefold() in workspace_files:
                raise ValueError(
                    f"RPF subtree workspace contains a case-insensitive collision: {relative}"
                )
            workspace_files[relative.casefold()] = (relative, path)
        if len(workspace_files) > _MAX_SUBTREE_FILES:
            raise ValueError("RPF subtree workspace exceeds the guarded file limit")

        changes: list[dict[str, Any]] = []
        for key, item in recorded.items():
            workspace = workspace_files.get(key)
            if workspace is None:
                changes.append({
                    "action": "delete", "archive_path": archive_path,
                    "entry": item["entry_path"],
                })
                continue
            _relative, payload = workspace
            if _sha256_file(payload) != item["sha256"]:
                changes.append({
                    "action": "replace", "archive_path": archive_path,
                    "entry": item["entry_path"], "payload": payload,
                })
        for key, (relative, payload) in workspace_files.items():
            if key in recorded:
                continue
            entry_path = f"{directory_path}/{relative}" if directory_path else relative
            changes.append({
                "action": "add", "archive_path": archive_path,
                "entry": entry_path, "payload": payload,
            })
        if not changes:
            raise ValueError("RPF subtree workspace has no changes to plan")
        plan = self.multi_change_plan(index, changes)
        plan["workspace_sync"] = {
            "manifest": str(manifest_path),
            "archive_path": archive_path, "directory_path": directory_path,
            "changed_files": len(changes),
        }
        return plan

    def replacement_plan(
        self, index: RpfIndex, entry: RpfEntryRecord, payload: str | Path,
    ) -> dict[str, Any]:
        if entry.kind == "directory":
            raise ValueError("A directory cannot be a replacement target")
        if index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        return self._entry_change_plan(
            index, "replace", entry.archive_path, entry.path, payload, entry,
        )

    def addition_plan(
        self, index: RpfIndex, entry_path: str, payload: str | Path, *,
        archive_path: str = "",
    ) -> dict[str, Any]:
        """Create a guarded plan to add a new root or nested entry."""
        normalized_archive = _safe_virtual_path(archive_path, allow_empty=True)
        normalized_entry = _safe_virtual_path(entry_path)
        entry_id = f"{normalized_archive}::{normalized_entry}"
        try:
            index.entry(entry_id)
        except KeyError:
            pass
        else:
            raise ValueError(f"RPF entry already exists: {entry_id}")
        self._require_existing_parent(index, normalized_archive, normalized_entry)
        self._require_supported_nested_archive(index, normalized_archive)
        return self._entry_change_plan(
            index, "add", normalized_archive, normalized_entry, payload, None,
        )

    def deletion_plan(
        self, index: RpfIndex, entry: RpfEntryRecord,
    ) -> dict[str, Any]:
        """Create a guarded plan to delete an existing root or nested entry."""
        if entry.kind in {"directory", "archive"}:
            raise ValueError("Directories and nested archives cannot be deleted as entries")
        if index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        return self._entry_change_plan(
            index, "delete", entry.archive_path, entry.path, None, entry,
        )

    def _entry_change_plan(
        self, index: RpfIndex, action: str, archive_path: str, entry_path: str,
        payload: str | Path | None, existing: RpfEntryRecord | None,
    ) -> dict[str, Any]:
        self._require_tool()
        if action not in _RPF_ACTIONS:
            raise ValueError(f"Unsupported RPF action: {action}")
        if index.source.suffix.casefold() != ".rpf" or not index.source.is_file():
            raise ValueError("RPF change plans require a loose .rpf archive")
        archive_path = _safe_virtual_path(archive_path, allow_empty=True)
        entry_path = _safe_virtual_path(entry_path)
        self._require_supported_nested_archive(index, archive_path)

        source: Path | None = None
        payload_meta: dict[str, Any] | None = None
        if action in {"replace", "add"}:
            if payload is None:
                raise ValueError(f"RPF {action} requires a payload")
            authored_source = Path(payload).expanduser()
            if authored_source.is_symlink():
                raise ValueError("RPF payload cannot be a symbolic link")
            source = authored_source.resolve()
            if not source.is_file():
                raise FileNotFoundError(f"RPF payload not found: {source}")
            payload_meta = {
                "path": str(source), "size": source.stat().st_size,
                "sha256": _sha256_file(source),
            }
        elif payload is not None:
            raise ValueError("RPF delete plans do not accept a payload")

        archive_hash = _sha256_file(index.source)
        if existing is None:
            original = {"exists": False, "size": 0, "sha256": None}
        else:
            with tempfile.TemporaryDirectory(prefix="allin1-rpf-plan-") as temporary:
                extracted = self.extract(index, existing, Path(temporary) / existing.name)
                original = {
                    "exists": True, "size": extracted.stat().st_size,
                    "sha256": _sha256_file(extracted),
                }
        if _sha256_file(index.source) != archive_hash:
            raise RuntimeError("RPF changed while the entry-change plan was being created")

        target_scope = self._target_scope(index.source)
        authorized_root = self._authorized_workspace_root(index.source)
        blocking_reasons: list[str] = []
        if target_scope == "unsafe":
            blocking_reasons.append(
                "The archive is neither inside the selected GTA V mods directory nor an "
                "explicitly authorized external workspace."
            )
        warnings: list[str] = []
        if (source is not None and existing is not None
                and source.suffix.casefold() != Path(existing.name).suffix.casefold()):
            warnings.append(
                "The payload extension differs from the selected entry; verify the native "
                "resource type before applying."
            )
        plan_id = self._plan_identifier(
            index.source, archive_hash, action, archive_path, entry_path,
            bool(original["exists"]), original.get("sha256"),
            payload_meta.get("sha256") if payload_meta else None,
            index.edition, target_scope, str(authorized_root or ""),
        )
        return {
            "schema_version": RPF_REPLACEMENT_PLAN_SCHEMA,
            "operation": "rpf_entry_change", "action": action,
            "plan_id": plan_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "blocked" if blocking_reasons else "ready",
            "archive": str(index.source),
            "archive_size": index.source.stat().st_size,
            "archive_sha256": archive_hash,
            "archive_path": archive_path, "entry": entry_path,
            "original": original, "payload": payload_meta,
            "edition": index.edition, "target_scope": target_scope,
            "authorized_root": str(authorized_root) if authorized_root else None,
            "blocking_reasons": blocking_reasons, "warnings": warnings,
            "safety": {
                "writes_performed": False, "backup_required": True,
                "full_archive_staging_required": True,
                "post_write_hash_verification_required": True,
                "rollback_required": True, "game_must_be_closed": True,
                "stock_archive_write_allowed": False,
            },
        }

    def apply_replacement_plan(
        self, plan_path: str | Path, *, receipt_root: str | Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Compatibility name for applying any schema-v3 RPF entry-change plan."""
        return self.apply_change_plan(
            plan_path, receipt_root=receipt_root, progress=progress,
        )

    def apply_change_plan(
        self, plan_path: str | Path, *, receipt_root: str | Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Apply a guarded plan through backup, staging, verification, and commit."""
        self._require_tool()
        plan_source = Path(plan_path).expanduser().resolve()
        plan = _read_json_object(plan_source, "RPF entry-change plan")
        if plan.get("operation") == "rpf_multi_entry_change":
            archive, changes = self._validate_multi_plan(plan)
            self._require_game_closed()
            lock = self._acquire_archive_lock(archive, plan["plan_id"])
            try:
                return self._apply_multi_plan_locked(
                    plan, archive, changes, receipt_root, progress,
                )
            finally:
                lock.unlink(missing_ok=True)
        archive, payload, archive_path, entry_path, action = self._validate_plan(plan)
        self._require_game_closed()
        lock = self._acquire_archive_lock(archive, plan["plan_id"])
        try:
            return self._apply_change_plan_locked(
                plan, archive, payload, archive_path, entry_path, action,
                receipt_root, progress,
            )
        finally:
            lock.unlink(missing_ok=True)

    def _apply_change_plan_locked(
        self, plan: dict[str, Any], archive: Path, payload: Path | None,
        archive_path: str, entry_path: str, action: str,
        receipt_root: str | Path | None, progress: ProgressCallback | None,
    ) -> Path:
        self._emit(progress, "Checking guarded inputs", 5)
        self._preflight_current_state(
            plan, archive, payload, archive_path, entry_path, action,
        )

        transactions = (
            Path(receipt_root).expanduser().resolve()
            if receipt_root is not None
            else user_data_root() / "rpf-transactions"
        )
        transactions.mkdir(parents=True, exist_ok=True)
        self._require_transaction_space(
            archive, transactions, archive.stat().st_size,
            payload.stat().st_size if payload else 0,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        transaction_id = f"{timestamp}-{plan['plan_id'][:12]}"
        transaction_dir = transactions / transaction_id
        transaction_dir.mkdir(parents=False, exist_ok=False)
        receipt_path = transaction_dir / "receipt.json"
        backup = transaction_dir / "archive.rpf.backup"
        payload_snapshot = (
            transaction_dir / f"payload{payload.suffix or '.bin'}" if payload else None
        )
        plan_snapshot = transaction_dir / "plan.json"
        # NG-encrypted RPF keys are derived from the archive filename. Stage in
        # a sibling directory while preserving that filename; the final rename
        # remains on the archive volume and the encrypted TOC stays readable.
        stage_dir = archive.parent / f".allin1-stage-{transaction_id}"
        stage_dir.mkdir(parents=False, exist_ok=False)
        stage = stage_dir / archive.name

        receipt: dict[str, Any] = {
            "schema_version": RPF_TRANSACTION_RECEIPT_SCHEMA,
            "operation": "rpf_entry_change", "action": action,
            "transaction_id": transaction_id,
            "plan_id": plan["plan_id"], "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "plan": str(plan_snapshot), "archive": str(archive),
            "archive_path": archive_path, "entry": entry_path,
            "edition": plan["edition"], "target_scope": plan["target_scope"],
            "authorized_root": plan.get("authorized_root"),
            "original": dict(plan["original"]),
            "payload": ({
                "source": str(payload), "snapshot": str(payload_snapshot),
                "size": plan["payload"]["size"],
                "sha256": plan["payload"]["sha256"],
            } if payload is not None and payload_snapshot is not None else None),
            "backup": {
                "path": str(backup), "size": plan["archive_size"],
                "sha256": plan["archive_sha256"],
            },
            "applied_archive_sha256": None,
        }
        _write_json_atomic(plan_snapshot, plan)
        _write_json_atomic(receipt_path, receipt)
        committed = False
        try:
            self._emit(progress, "Creating verified rollback snapshot", 20)
            self._copy_verified(archive, backup, plan["archive_sha256"])
            if payload is not None and payload_snapshot is not None:
                self._copy_verified(payload, payload_snapshot, plan["payload"]["sha256"])
            self._copy_verified(archive, stage, plan["archive_sha256"])
            receipt["status"] = "staged"
            receipt["staged_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(receipt_path, receipt)

            self._emit(progress, "Applying change to staged archive", 45)
            self._apply_entry_change(
                stage, archive_path, entry_path, action, payload_snapshot,
                plan["edition"],
            )
            expected_applied = self._applied_entry_state(plan)
            self._emit(progress, "Verifying staged archive and entry", 65)
            self._verify_entry_state(
                stage, archive_path, entry_path, expected_applied, plan["edition"],
            )
            staged_hash = _sha256_file(stage)
            receipt["applied_archive_sha256"] = staged_hash
            receipt["status"] = "verified_staging"
            _write_json_atomic(receipt_path, receipt)

            self._emit(progress, "Committing verified archive", 80)
            self._require_game_closed()
            stage.replace(archive)
            committed = True
            if _sha256_file(archive) != staged_hash:
                raise RuntimeError("Committed RPF does not match the verified staged archive")
            self._verify_entry_state(
                archive, archive_path, entry_path, expected_applied, plan["edition"],
            )
            receipt["status"] = "applied"
            receipt["applied_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(receipt_path, receipt)
            self._emit(progress, "Transaction applied and verified", 100)
            return receipt_path
        except Exception as exc:
            receipt["error"] = str(exc)
            if committed:
                try:
                    self._restore_snapshot(backup, archive, plan["archive_sha256"])
                    self._verify_entry_state(
                        archive, archive_path, entry_path, plan["original"],
                        plan["edition"],
                    )
                    receipt["status"] = "rolled_back_after_failure"
                    receipt["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
                except Exception as rollback_error:
                    receipt["status"] = "rollback_failed"
                    receipt["rollback_error"] = str(rollback_error)
            else:
                receipt["status"] = "failed_before_commit"
            _write_json_atomic(receipt_path, receipt)
            raise RuntimeError(
                f"RPF transaction failed ({receipt['status']}): {exc}. "
                f"Receipt: {receipt_path}"
            ) from exc
        finally:
            stage.unlink(missing_ok=True)
            try:
                stage_dir.rmdir()
            except FileNotFoundError:
                pass

    def _apply_multi_plan_locked(
        self, plan: dict[str, Any], archive: Path,
        changes: list[tuple[dict[str, Any], Path | None]],
        receipt_root: str | Path | None, progress: ProgressCallback | None,
    ) -> Path:
        self._emit(progress, "Checking guarded batch inputs", 5)
        if archive.stat().st_size != plan["archive_size"]:
            raise RuntimeError("RPF size changed after the multi-change plan was created")
        if _sha256_file(archive) != plan["archive_sha256"]:
            raise RuntimeError("RPF changed after the multi-change plan was created")
        for change, payload in changes:
            if payload is None:
                continue
            if payload.stat().st_size != change["payload"]["size"]:
                raise RuntimeError(
                    f"RPF payload size changed after planning: {payload}"
                )
            if _sha256_file(payload) != change["payload"]["sha256"]:
                raise RuntimeError(f"RPF payload changed after planning: {payload}")
        self._verify_entry_states(
            archive,
            [
                (change["archive_path"], change["entry"], change["original"])
                for change, _payload in changes
            ],
            plan["edition"],
        )

        transactions = (
            Path(receipt_root).expanduser().resolve()
            if receipt_root is not None
            else user_data_root() / "rpf-transactions"
        )
        transactions.mkdir(parents=True, exist_ok=True)
        payload_bytes = sum(
            payload.stat().st_size for _change, payload in changes
            if payload is not None
        )
        self._require_transaction_space(
            archive, transactions, archive.stat().st_size, payload_bytes,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        transaction_id = f"{timestamp}-{plan['plan_id'][:12]}"
        transaction_dir = transactions / transaction_id
        transaction_dir.mkdir(parents=False, exist_ok=False)
        receipt_path = transaction_dir / "receipt.json"
        backup = transaction_dir / "archive.rpf.backup"
        payload_root = transaction_dir / "payloads"
        payload_root.mkdir()
        plan_snapshot = transaction_dir / "plan.json"
        stage_dir = archive.parent / f".allin1-stage-{transaction_id}"
        stage_dir.mkdir(parents=False, exist_ok=False)
        stage = stage_dir / archive.name

        receipt_changes: list[dict[str, Any]] = []
        for number, (change, payload) in enumerate(changes):
            payload_receipt = None
            if payload is not None:
                snapshot = payload_root / f"{number:04d}{payload.suffix or '.bin'}"
                payload_receipt = {
                    "source": str(payload), "snapshot": str(snapshot),
                    "size": change["payload"]["size"],
                    "sha256": change["payload"]["sha256"],
                }
            receipt_changes.append({
                "action": change["action"],
                "archive_path": change["archive_path"],
                "entry": change["entry"],
                "original": dict(change["original"]),
                "payload": payload_receipt,
            })
        receipt: dict[str, Any] = {
            "schema_version": RPF_MULTI_TRANSACTION_RECEIPT_SCHEMA,
            "operation": "rpf_multi_entry_change", "action": "batch",
            "transaction_id": transaction_id, "plan_id": plan["plan_id"],
            "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "plan": str(plan_snapshot), "archive": str(archive),
            "edition": plan["edition"], "target_scope": plan["target_scope"],
            "authorized_root": plan.get("authorized_root"),
            "changes": receipt_changes,
            "backup": {
                "path": str(backup), "size": plan["archive_size"],
                "sha256": plan["archive_sha256"],
            },
            "applied_archive_sha256": None,
        }
        _write_json_atomic(plan_snapshot, plan)
        _write_json_atomic(receipt_path, receipt)
        committed = False
        try:
            self._emit(progress, "Creating verified batch rollback snapshot", 20)
            self._copy_verified(archive, backup, plan["archive_sha256"])
            for change in receipt_changes:
                payload = change.get("payload")
                if payload:
                    self._copy_verified(
                        Path(payload["source"]), Path(payload["snapshot"]),
                        payload["sha256"],
                    )
            self._copy_verified(archive, stage, plan["archive_sha256"])
            receipt["status"] = "staged"
            receipt["staged_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(receipt_path, receipt)

            staged_changes = [{
                **change,
                "payload_path": (
                    Path(change["payload"]["snapshot"])
                    if change.get("payload") else None
                ),
            } for change in receipt_changes]
            self._emit(progress, "Applying batch to staged archive", 45)
            self._apply_entry_changes(stage, staged_changes, plan["edition"])
            expected_applied = [
                (
                    change["archive_path"], change["entry"],
                    self._applied_entry_state(change),
                )
                for change in receipt_changes
            ]
            self._emit(progress, "Verifying every staged entry", 65)
            self._verify_entry_states(stage, expected_applied, plan["edition"])
            staged_hash = _sha256_file(stage)
            receipt["applied_archive_sha256"] = staged_hash
            receipt["status"] = "verified_staging"
            _write_json_atomic(receipt_path, receipt)

            self._emit(progress, "Committing one verified outer archive", 80)
            self._require_game_closed()
            stage.replace(archive)
            committed = True
            if _sha256_file(archive) != staged_hash:
                raise RuntimeError("Committed RPF does not match verified batch staging")
            self._verify_entry_states(archive, expected_applied, plan["edition"])
            receipt["status"] = "applied"
            receipt["applied_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(receipt_path, receipt)
            self._emit(progress, "Batch transaction applied and verified", 100)
            return receipt_path
        except Exception as exc:
            receipt["error"] = str(exc)
            if committed:
                try:
                    self._restore_snapshot(backup, archive, plan["archive_sha256"])
                    self._verify_entry_states(
                        archive,
                        [
                            (
                                change["archive_path"], change["entry"],
                                change["original"],
                            )
                            for change in receipt_changes
                        ],
                        plan["edition"],
                    )
                    receipt["status"] = "rolled_back_after_failure"
                    receipt["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
                except Exception as rollback_error:
                    receipt["status"] = "rollback_failed"
                    receipt["rollback_error"] = str(rollback_error)
            else:
                receipt["status"] = "failed_before_commit"
            _write_json_atomic(receipt_path, receipt)
            raise RuntimeError(
                f"RPF batch transaction failed ({receipt['status']}): {exc}. "
                f"Receipt: {receipt_path}"
            ) from exc
        finally:
            stage.unlink(missing_ok=True)
            try:
                stage_dir.rmdir()
            except FileNotFoundError:
                pass

    def verify_transaction(self, receipt_path: str | Path) -> dict[str, Any]:
        """Verify archive state, entry content, and rollback snapshot integrity."""
        self._require_tool()
        receipt = self._validate_receipt(
            _read_json_object(receipt_path, "RPF transaction receipt")
        )
        archive = Path(receipt["archive"]).resolve()
        backup = Path(receipt["backup"]["path"]).resolve()
        backup_valid = (
            backup.is_file()
            and backup.stat().st_size == int(receipt["backup"]["size"])
            and _sha256_file(backup) == receipt["backup"]["sha256"]
        )
        if not archive.is_file():
            return {
                "healthy": False, "archive_state": "missing",
                "backup_valid": backup_valid, "entry_valid": False,
            }
        current_hash = _sha256_file(archive)
        if current_hash == receipt.get("applied_archive_sha256"):
            archive_state = "applied"
            expected_entry = (
                [
                    (
                        change["archive_path"], change["entry"],
                        self._applied_entry_state(change),
                    )
                    for change in receipt["changes"]
                ]
                if receipt["operation"] == "rpf_multi_entry_change"
                else self._applied_entry_state(receipt)
            )
        elif current_hash == receipt["backup"]["sha256"]:
            archive_state = "original"
            expected_entry = (
                [
                    (
                        change["archive_path"], change["entry"],
                        change["original"],
                    )
                    for change in receipt["changes"]
                ]
                if receipt["operation"] == "rpf_multi_entry_change"
                else receipt["original"]
            )
        else:
            archive_state = "modified_externally"
            expected_entry = None
        entry_valid = False
        entry_error: str | None = None
        if expected_entry is not None:
            try:
                if receipt["operation"] == "rpf_multi_entry_change":
                    self._verify_entry_states(
                        archive, expected_entry, receipt["edition"],
                    )
                else:
                    self._verify_entry_state(
                        archive, receipt["archive_path"], receipt["entry"],
                        expected_entry, receipt["edition"],
                    )
                entry_valid = True
            except (OSError, ValueError, RuntimeError) as exc:
                entry_error = str(exc)
        result = {
            "healthy": backup_valid and entry_valid,
            "archive_state": archive_state, "archive_sha256": current_hash,
            "backup_valid": backup_valid, "entry_valid": entry_valid,
        }
        if entry_error:
            result["entry_error"] = entry_error
        return result

    def rollback_transaction(
        self, receipt_path: str | Path, *, progress: ProgressCallback | None = None,
    ) -> Path:
        """Restore an applied transaction if its archive is still receipt-owned."""
        self._require_tool()
        source = Path(receipt_path).expanduser().resolve()
        receipt = self._validate_receipt(
            _read_json_object(source, "RPF transaction receipt")
        )
        recoverable_statuses = {"applied", "verified_staging", "rollback_failed"}
        if receipt["status"] not in recoverable_statuses:
            raise ValueError(
                "Only an applied or interrupted post-staging transaction can be "
                f"rolled back (status: {receipt['status']})"
            )
        self._require_game_closed()
        archive = Path(receipt["archive"]).resolve()
        if not self._receipt_scope_is_authorized(receipt, archive):
            raise ValueError("Receipt archive is no longer inside its authorized write scope")
        lock = self._acquire_archive_lock(archive, receipt["plan_id"])
        try:
            return self._rollback_transaction_locked(
                source, receipt, archive, progress,
            )
        finally:
            lock.unlink(missing_ok=True)

    def _rollback_transaction_locked(
        self, source: Path, receipt: dict[str, Any], archive: Path,
        progress: ProgressCallback | None,
    ) -> Path:
        self._emit(progress, "Verifying applied transaction", 10)
        expected_applied = receipt.get("applied_archive_sha256")
        if not archive.is_file() or _sha256_file(archive) != expected_applied:
            raise RuntimeError(
                "Refusing rollback because the archive changed after this transaction"
            )
        if receipt["operation"] == "rpf_multi_entry_change":
            self._verify_entry_states(
                archive,
                [
                    (
                        change["archive_path"], change["entry"],
                        self._applied_entry_state(change),
                    )
                    for change in receipt["changes"]
                ],
                receipt["edition"],
            )
        else:
            self._verify_entry_state(
                archive, receipt["archive_path"], receipt["entry"],
                self._applied_entry_state(receipt), receipt["edition"],
            )
        backup = Path(receipt["backup"]["path"]).resolve()
        if (not backup.is_file()
                or backup.stat().st_size != int(receipt["backup"]["size"])
                or _sha256_file(backup) != receipt["backup"]["sha256"]):
            raise RuntimeError("Rollback snapshot is missing or does not match its receipt")

        self._require_transaction_space(
            archive, source.parent, archive.stat().st_size * 2, 0,
            backup_copy=False,
        )
        recovery = archive.with_name(
            f".{archive.name}.{receipt['transaction_id']}.rollback-recovery"
        )
        rollback_stage = archive.with_name(
            f".{archive.name}.{receipt['transaction_id']}.rollback-stage"
        )
        try:
            self._emit(progress, "Creating rollback recovery copy", 30)
            self._copy_verified(archive, recovery, expected_applied)
            self._copy_verified(backup, rollback_stage, receipt["backup"]["sha256"])
            self._emit(progress, "Restoring pre-transaction archive", 65)
            self._require_game_closed()
            rollback_stage.replace(archive)
            if _sha256_file(archive) != receipt["backup"]["sha256"]:
                raise RuntimeError("Restored archive does not match its rollback snapshot")
            if receipt["operation"] == "rpf_multi_entry_change":
                self._verify_entry_states(
                    archive,
                    [
                        (
                            change["archive_path"], change["entry"],
                            change["original"],
                        )
                        for change in receipt["changes"]
                    ],
                    receipt["edition"],
                )
            else:
                self._verify_entry_state(
                    archive, receipt["archive_path"], receipt["entry"],
                    receipt["original"], receipt["edition"],
                )
        except Exception as exc:
            if recovery.is_file():
                recovery.replace(archive)
            raise RuntimeError(f"Rollback failed; applied archive was restored: {exc}") from exc
        finally:
            rollback_stage.unlink(missing_ok=True)
            recovery.unlink(missing_ok=True)
        receipt["status"] = "rolled_back"
        receipt["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(source, receipt)
        self._emit(progress, "Rollback restored and verified", 100)
        return source

    def _target_scope(self, archive: Path) -> str:
        resolved = archive.resolve()
        mods_root = (self.gta_path / "mods").resolve()
        if resolved.is_relative_to(mods_root):
            return "mods_copy"
        if self._authorized_workspace_root(resolved) is not None:
            return "workspace_copy"
        return "unsafe"

    def _authorized_workspace_root(self, archive: Path) -> Path | None:
        resolved = archive.resolve()
        return next(
            (root for root in self.workspace_roots if resolved.is_relative_to(root)),
            None,
        )

    def _receipt_scope_is_authorized(
        self, receipt: dict[str, Any], archive: Path,
    ) -> bool:
        current = self._target_scope(archive)
        if current != receipt.get("target_scope"):
            return False
        if current == "workspace_copy":
            root = self._authorized_workspace_root(archive)
            return root is not None and str(root) == receipt.get("authorized_root")
        return current == "mods_copy"

    @staticmethod
    def _acquire_archive_lock(archive: Path, plan_id: str) -> Path:
        lock = archive.with_name(f".{archive.name}.allin1.lock")
        try:
            descriptor = os.open(
                lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
            )
        except FileExistsError as exc:
            raise RuntimeError(
                "Another ALLIN1 RPF transaction owns this archive. If a prior process "
                f"crashed, verify its receipt before removing the stale lock: {lock}"
            ) from exc
        try:
            content = json.dumps({
                "pid": os.getpid(), "plan_id": plan_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8")
            os.write(descriptor, content)
        except Exception:
            os.close(descriptor)
            lock.unlink(missing_ok=True)
            raise
        else:
            os.close(descriptor)
        return lock

    @staticmethod
    def _plan_identifier(
        archive: Path, archive_hash: str, action: str, archive_path: str,
        entry_path: str, original_exists: bool, original_hash: str | None,
        payload_hash: str | None, edition: str, target_scope: str,
        authorized_root: str,
    ) -> str:
        seed = "\0".join((
            str(archive.resolve()), archive_hash, action, archive_path, entry_path,
            str(original_exists), original_hash or "-", payload_hash or "-", edition,
            target_scope, authorized_root,
        ))
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def _multi_plan_identifier(
        archive: Path, archive_hash: str, changes: Iterable[dict[str, Any]],
        edition: str, target_scope: str, authorized_root: str,
    ) -> str:
        guarded_changes = [{
            "action": item["action"],
            "archive_path": item["archive_path"],
            "entry": item["entry"],
            "original_exists": item["original"]["exists"],
            "original_sha256": item["original"].get("sha256"),
            "payload_sha256": (
                item["payload"].get("sha256") if item.get("payload") else None
            ),
        } for item in changes]
        seed = json.dumps({
            "archive": str(archive.resolve()), "archive_sha256": archive_hash,
            "changes": guarded_changes, "edition": edition,
            "target_scope": target_scope, "authorized_root": authorized_root,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _validate_multi_plan(
        self, plan: dict[str, Any],
    ) -> tuple[Path, list[tuple[dict[str, Any], Path | None]]]:
        if plan.get("schema_version") != RPF_MULTI_CHANGE_PLAN_SCHEMA:
            raise ValueError(
                "Unsupported RPF multi-change plan; recreate it with this SDK version"
            )
        if plan.get("operation") != "rpf_multi_entry_change":
            raise ValueError("Unsupported RPF multi-change operation")
        if plan.get("status") != "ready" or plan.get("blocking_reasons"):
            reasons = "; ".join(str(item) for item in plan.get("blocking_reasons", ()))
            raise ValueError(f"RPF multi-change plan is blocked: {reasons or 'unknown reason'}")
        if not isinstance(plan.get("edition"), str) or not plan["edition"].strip():
            raise ValueError("RPF multi-change plan is missing its GTA V edition")
        archive = Path(str(plan.get("archive", ""))).expanduser().resolve()
        if not archive.is_file() or archive.suffix.casefold() != ".rpf":
            raise FileNotFoundError(f"Planned RPF archive not found: {archive}")
        if not self._plan_scope_is_authorized(plan, archive):
            raise ValueError("RPF multi-change plan is outside its authorized write scope")
        if not _is_sha256(plan.get("archive_sha256")) or not _is_sha256(
            plan.get("plan_id")
        ):
            raise ValueError("RPF multi-change plan contains an invalid SHA-256 value")
        if not isinstance(plan.get("archive_size"), int) or plan["archive_size"] < 0:
            raise ValueError("RPF multi-change plan contains an invalid archive size")
        authored_changes = plan.get("changes")
        if not isinstance(authored_changes, list) or not authored_changes:
            raise ValueError("RPF multi-change plan contains no changes")
        if len(authored_changes) > _MAX_MULTI_ENTRY_CHANGES:
            raise ValueError("RPF multi-change plan exceeds the guarded change limit")

        normalized: list[tuple[dict[str, Any], Path | None]] = []
        seen: set[tuple[str, str]] = set()
        for number, authored in enumerate(authored_changes, start=1):
            if not isinstance(authored, dict):
                raise ValueError(f"RPF multi-change item {number} is invalid")
            action = str(authored.get("action", ""))
            if action not in _RPF_ACTIONS:
                raise ValueError(f"RPF multi-change item {number} has an invalid action")
            archive_path = _safe_virtual_path(
                str(authored.get("archive_path", "")), allow_empty=True,
            )
            self._nested_archive_chain(archive_path)
            entry_path = _safe_virtual_path(str(authored.get("entry", "")))
            identity = (archive_path.casefold(), entry_path.casefold())
            if identity in seen:
                raise ValueError("RPF multi-change plan contains a duplicate target")
            seen.add(identity)
            original = authored.get("original")
            if not isinstance(original, dict) or not isinstance(
                original.get("exists"), bool,
            ):
                raise ValueError(f"RPF multi-change item {number} has invalid original state")
            if not isinstance(original.get("size"), int) or original["size"] < 0:
                raise ValueError(f"RPF multi-change item {number} has invalid original size")
            if original["exists"] and not _is_sha256(original.get("sha256")):
                raise ValueError(f"RPF multi-change item {number} has invalid original hash")
            if action == "add" and original["exists"]:
                raise ValueError("RPF multi-change add item claims its target exists")
            if action in {"replace", "delete"} and not original["exists"]:
                raise ValueError(
                    f"RPF multi-change {action} item claims its target is absent"
                )
            payload_meta = authored.get("payload")
            payload: Path | None = None
            if action in {"replace", "add"}:
                if not isinstance(payload_meta, dict):
                    raise ValueError(f"RPF multi-change {action} item has no payload")
                payload_authored = Path(str(payload_meta.get("path", ""))).expanduser()
                if payload_authored.is_symlink():
                    raise ValueError("RPF payload cannot be a symbolic link")
                payload = payload_authored.resolve()
                if not payload.is_file():
                    raise FileNotFoundError(f"Planned RPF payload not found: {payload}")
                if not isinstance(payload_meta.get("size"), int) or payload_meta["size"] < 0:
                    raise ValueError("RPF multi-change payload has an invalid size")
                if not _is_sha256(payload_meta.get("sha256")):
                    raise ValueError("RPF multi-change payload has an invalid hash")
            elif payload_meta is not None:
                raise ValueError("RPF multi-change delete item unexpectedly has a payload")
            normalized_change = {
                **authored, "action": action, "archive_path": archive_path,
                "entry": entry_path,
            }
            normalized.append((normalized_change, payload))

        for change, _payload in normalized:
            container = (
                change["entry"] if not change["archive_path"]
                else f"{change['archive_path']}!{change['entry']}"
            ).casefold()
            if any(
                other is not change and (
                    other["archive_path"].casefold() == container
                    or other["archive_path"].casefold().startswith(container + "!")
                )
                for other, _other_payload in normalized
            ):
                raise ValueError(
                    "RPF multi-change plan cannot replace an archive and also edit "
                    f"its internal tree: {container}"
                )

        expected_id = self._multi_plan_identifier(
            archive, plan["archive_sha256"],
            (change for change, _payload in normalized),
            plan["edition"], str(plan.get("target_scope", "")),
            str(plan.get("authorized_root") or ""),
        )
        if plan["plan_id"] != expected_id:
            raise ValueError("RPF multi-change plan identity does not match its inputs")
        return archive, normalized

    def _validate_plan(
        self, plan: dict[str, Any],
    ) -> tuple[Path, Path | None, str, str, str]:
        if plan.get("schema_version") != RPF_REPLACEMENT_PLAN_SCHEMA:
            raise ValueError(
                "Unsupported RPF entry-change plan; recreate it with this SDK version"
            )
        if plan.get("operation") != "rpf_entry_change":
            raise ValueError("Unsupported RPF entry-change operation")
        action = str(plan.get("action", ""))
        if action not in _RPF_ACTIONS:
            raise ValueError("RPF entry-change plan contains an invalid action")
        if plan.get("status") != "ready" or plan.get("blocking_reasons"):
            reasons = "; ".join(str(item) for item in plan.get("blocking_reasons", ()))
            raise ValueError(f"RPF entry-change plan is blocked: {reasons or 'unknown reason'}")
        if not isinstance(plan.get("original"), dict):
            raise ValueError("Entry-change plan is missing original metadata")
        if action in {"replace", "add"} and not isinstance(plan.get("payload"), dict):
            raise ValueError("Entry-change plan is missing payload metadata")
        if action == "delete" and plan.get("payload") is not None:
            raise ValueError("Delete plan unexpectedly contains a payload")
        if not isinstance(plan.get("edition"), str) or not plan["edition"].strip():
            raise ValueError("Replacement plan is missing its GTA V edition")
        entry_path = _safe_virtual_path(str(plan.get("entry", "")))
        archive_path = _safe_virtual_path(
            str(plan.get("archive_path", "")), allow_empty=True,
        )
        self._nested_archive_chain(archive_path)
        archive = Path(str(plan.get("archive", ""))).expanduser().resolve()
        payload: Path | None = None
        if action in {"replace", "add"}:
            payload_authored = Path(str(plan["payload"].get("path", ""))).expanduser()
            if payload_authored.is_symlink():
                raise ValueError("RPF payload cannot be a symbolic link")
            payload = payload_authored.resolve()
        if not archive.is_file() or archive.suffix.casefold() != ".rpf":
            raise FileNotFoundError(f"Planned RPF archive not found: {archive}")
        if payload is not None and not payload.is_file():
            raise FileNotFoundError(f"Planned RPF payload not found: {payload}")
        if not self._plan_scope_is_authorized(plan, archive):
            raise ValueError("RPF plan is outside its currently authorized write scope")
        required_hashes: list[object] = [plan.get("archive_sha256"), plan.get("plan_id")]
        if plan["original"].get("exists"):
            required_hashes.append(plan["original"].get("sha256"))
        if plan.get("payload"):
            required_hashes.append(plan["payload"].get("sha256"))
        if not all(_is_sha256(value) for value in required_hashes):
            raise ValueError("Entry-change plan contains an invalid SHA-256 value")
        size_containers = [(plan, "archive_size"), (plan["original"], "size")]
        if plan.get("payload"):
            size_containers.append((plan["payload"], "size"))
        for container, label in size_containers:
            if not isinstance(container.get(label), int) or container[label] < 0:
                raise ValueError(f"Entry-change plan contains an invalid {label}")
        if not isinstance(plan["original"].get("exists"), bool):
            raise ValueError("Entry-change plan has invalid original-exists metadata")
        if action == "add" and plan["original"]["exists"]:
            raise ValueError("Add plan claims the target already exists")
        if action in {"replace", "delete"} and not plan["original"]["exists"]:
            raise ValueError(f"{action.title()} plan claims the target is absent")
        expected_id = self._plan_identifier(
            archive, plan["archive_sha256"], action, archive_path, entry_path,
            plan["original"]["exists"], plan["original"].get("sha256"),
            plan["payload"].get("sha256") if plan.get("payload") else None,
            str(plan.get("edition", "")), str(plan.get("target_scope", "")),
            str(plan.get("authorized_root") or ""),
        )
        if plan["plan_id"] != expected_id:
            raise ValueError("Replacement plan identity does not match its guarded inputs")
        return archive, payload, archive_path, entry_path, action

    def _preflight_current_state(
        self, plan: dict[str, Any], archive: Path, payload: Path | None,
        archive_path: str, entry_path: str, action: str,
    ) -> None:
        if archive.stat().st_size != plan["archive_size"]:
            raise RuntimeError("RPF size changed after the replacement plan was created")
        if _sha256_file(archive) != plan["archive_sha256"]:
            raise RuntimeError("RPF changed after the replacement plan was created")
        if payload is not None:
            if payload.stat().st_size != plan["payload"]["size"]:
                raise RuntimeError("RPF payload size changed after planning")
            if _sha256_file(payload) != plan["payload"]["sha256"]:
                raise RuntimeError("RPF payload changed after planning")
        self._verify_entry_state(
            archive, archive_path, entry_path, plan["original"], plan["edition"],
        )

    def _verify_entry_state(
        self, archive: Path, archive_path: str, entry_path: str,
        expected: dict[str, Any], edition: str,
    ) -> None:
        index = self.index(archive)
        if index.edition.casefold() != str(edition).casefold():
            raise RuntimeError(
                f"Archive edition changed from {edition} to {index.edition}"
            )
        try:
            entry = index.entry(f"{archive_path}::{entry_path}")
        except KeyError as exc:
            if not expected.get("exists"):
                return
            raise RuntimeError(
                f"RPF entry is missing after write: {archive_path}::{entry_path}"
            ) from exc
        if not expected.get("exists"):
            raise RuntimeError(
                f"RPF entry should be absent after write: {archive_path}::{entry_path}"
            )
        expected_hash = expected.get("sha256")
        if not _is_sha256(expected_hash):
            raise ValueError("Expected RPF entry state has no valid SHA-256 hash")
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-verify-") as temporary:
            extracted = self.extract(index, entry, Path(temporary) / entry.name)
            actual_hash = _sha256_file(extracted)
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"RPF entry verification failed for {entry_path}: "
                f"expected {expected_hash}, found {actual_hash}"
            )

    def _verify_entry_states(
        self, archive: Path,
        states: Iterable[tuple[str, str, dict[str, Any]]], edition: str,
    ) -> None:
        """Verify many exact entry states with one index and one extraction scan."""
        expected_states = tuple(states)
        index = self.index(archive)
        if index.edition.casefold() != str(edition).casefold():
            raise RuntimeError(f"Archive edition changed from {edition} to {index.edition}")
        present: list[tuple[RpfEntryRecord, dict[str, Any]]] = []
        for archive_path, entry_path, expected in expected_states:
            try:
                entry = index.entry(f"{archive_path}::{entry_path}")
            except KeyError as exc:
                if not expected.get("exists"):
                    continue
                raise RuntimeError(
                    f"RPF entry is missing after batch write: "
                    f"{archive_path}::{entry_path}"
                ) from exc
            if not expected.get("exists"):
                raise RuntimeError(
                    f"RPF entry should be absent after batch write: "
                    f"{archive_path}::{entry_path}"
                )
            if not _is_sha256(expected.get("sha256")):
                raise ValueError("Expected RPF batch entry state has no valid SHA-256 hash")
            present.append((entry, expected))
        archive_hash = _sha256_file(archive)
        hashes = self._batch_content_hashes(
            index, (entry for entry, _expected in present),
            expected_source_sha256=archive_hash,
        )
        for entry, expected in present:
            if hashes[entry.id] != expected["sha256"]:
                raise RuntimeError(
                    f"RPF batch entry verification failed for {entry.virtual_name}: "
                    f"expected {expected['sha256']}, found {hashes[entry.id]}"
                )

    def _apply_entry_changes(
        self, archive: Path, changes: list[dict[str, Any]], edition: str,
    ) -> None:
        """Apply a tree of changes, rebuilding every nested container only once."""
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-batch-tree-") as temporary:
            tree_root = Path(temporary)

            def apply_level(
                current_archive: Path, current_virtual: str,
                relevant: list[dict[str, Any]], depth: int,
            ) -> None:
                direct = [
                    change for change in relevant
                    if change["archive_path"].casefold() == current_virtual.casefold()
                ]
                current_chain = self._nested_archive_chain(current_virtual)
                child_groups: dict[str, list[dict[str, Any]]] = {}
                for change in relevant:
                    if change in direct:
                        continue
                    chain = self._nested_archive_chain(change["archive_path"])
                    if chain[:len(current_chain)] != current_chain or len(chain) <= len(
                        current_chain
                    ):
                        raise ValueError(
                            "RPF batch change does not descend from its transaction tree"
                        )
                    child_groups.setdefault(chain[len(current_chain)], []).append(change)

                if child_groups:
                    current_index = self.index(current_archive)
                    for child_number, (child_entry_path, child_changes) in enumerate(
                        sorted(child_groups.items(), key=lambda item: item[0].casefold())
                    ):
                        try:
                            child_entry = current_index.entry(f"::{child_entry_path}")
                        except KeyError as exc:
                            raise RuntimeError(
                                f"Nested RPF disappeared during batch staging: "
                                f"{child_entry_path}"
                            ) from exc
                        if child_entry.kind != "archive":
                            raise RuntimeError(
                                f"Nested batch target is not an RPF: {child_entry_path}"
                            )
                        child_file = (
                            tree_root / f"level-{depth:02d}-{child_number:04d}"
                            / child_entry.name
                        )
                        self.extract(current_index, child_entry, child_file)
                        child_virtual = (
                            child_entry_path if not current_virtual
                            else f"{current_virtual}!{child_entry_path}"
                        )
                        apply_level(
                            child_file, child_virtual, child_changes, depth + 1,
                        )
                        direct.append({
                            "action": "replace", "archive_path": current_virtual,
                            "entry": child_entry_path, "payload_path": child_file,
                        })
                if direct:
                    self._run_entry_changes_helper(current_archive, direct)

            apply_level(archive, "", changes, 0)

    def _run_entry_changes_helper(
        self, archive: Path, changes: Iterable[dict[str, Any]],
    ) -> None:
        selected = tuple(changes)
        if not selected:
            return
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-batch-helper-") as temporary:
            root = Path(temporary)
            payload_root = root / "payloads"
            payload_root.mkdir()
            manifest = root / "changes.tsv"
            lines: list[str] = []
            for number, change in enumerate(selected):
                action = str(change["action"])
                entry_path = _safe_virtual_path(str(change["entry"]))
                relative = ""
                if action in {"replace", "add"}:
                    payload = Path(change["payload_path"]).resolve()
                    if not payload.is_file():
                        raise FileNotFoundError(f"RPF batch payload not found: {payload}")
                    snapshot = payload_root / f"{number:04d}{payload.suffix or '.bin'}"
                    shutil.copy2(payload, snapshot)
                    relative = snapshot.name
                elif action != "delete":
                    raise ValueError(f"Unsupported RPF batch action: {action}")
                lines.append(f"{action}\t{entry_path}\t{relative}\n")
            manifest.write_text("".join(lines), encoding="utf-8")
            completed = run_hidden(
                [
                    self.patcher, "apply-entry-changes", self.gta_path,
                    archive, manifest, payload_root,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode:
                detail = (
                    completed.stderr or completed.stdout or "unknown helper error"
                ).strip()
                raise RuntimeError(f"Staged RPF batch change failed: {detail}")

    def _apply_entry_change(
        self, archive: Path, archive_path: str, entry_path: str, action: str,
        payload: Path | None, edition: str,
    ) -> None:
        if not archive_path:
            self._run_entry_helper(archive, entry_path, action, payload)
            return
        archive_chain = self._nested_archive_chain(archive_path)
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-nested-") as temporary:
            extracted_chain: list[tuple[Path, str, Path]] = []
            current_archive = archive
            for depth, nested_entry_path in enumerate(archive_chain):
                current_index = self.index(current_archive)
                try:
                    nested_entry = current_index.entry(f"::{nested_entry_path}")
                except KeyError as exc:
                    raise RuntimeError(
                        "Nested RPF disappeared from the staged archive chain: "
                        f"{nested_entry_path} ({archive_path})"
                    ) from exc
                if nested_entry.kind != "archive":
                    raise RuntimeError(
                        f"Nested target is no longer an RPF archive: {nested_entry_path}"
                    )
                nested = (
                    Path(temporary) / f"level-{depth:02d}" / nested_entry.name
                )
                self.extract(current_index, nested_entry, nested)
                extracted_chain.append(
                    (current_archive, nested_entry_path, nested)
                )
                current_archive = nested

            self._run_entry_helper(current_archive, entry_path, action, payload)
            expected = (
                {"exists": False, "size": 0, "sha256": None}
                if action == "delete" else {
                    "exists": True, "size": payload.stat().st_size,
                    "sha256": _sha256_file(payload),
                }
            )
            self._verify_entry_state(
                current_archive, "", entry_path, expected, edition,
            )

            # Reinsert each verified child into its immediate parent. The live
            # archive is still untouched: this entire chain belongs to the
            # transaction's staged outer copy.
            for parent_archive, nested_entry_path, nested in reversed(
                extracted_chain
            ):
                nested_state = {
                    "exists": True,
                    "size": nested.stat().st_size,
                    "sha256": _sha256_file(nested),
                }
                self._run_entry_helper(
                    parent_archive, nested_entry_path, "replace", nested,
                )
                self._verify_entry_state(
                    parent_archive, "", nested_entry_path, nested_state, edition,
                )

    def _run_entry_helper(
        self, archive: Path, entry_path: str, action: str, payload: Path | None,
    ) -> None:
        if action in {"replace", "add"}:
            if payload is None:
                raise ValueError(f"RPF {action} helper requires a payload")
            command = [
                self.patcher, "replace-entry", self.gta_path, archive,
                entry_path, payload,
            ]
        elif action == "delete":
            command = [self.patcher, "delete-entry", self.gta_path, archive, entry_path]
        else:
            raise ValueError(f"Unsupported RPF entry helper action: {action}")
        completed = run_hidden(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "unknown helper error").strip()
            raise RuntimeError(f"Staged RPF {action} failed: {detail}")

    @staticmethod
    def _applied_entry_state(container: dict[str, Any]) -> dict[str, Any]:
        if container.get("action") == "delete":
            return {"exists": False, "size": 0, "sha256": None}
        payload = container.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Applied RPF entry state is missing payload metadata")
        return {
            "exists": True, "size": int(payload["size"]),
            "sha256": payload["sha256"],
        }

    @staticmethod
    def _require_existing_parent(
        index: RpfIndex, archive_path: str, entry_path: str,
    ) -> None:
        parent = str(PurePosixPath(entry_path).parent)
        if parent in {"", "."}:
            return
        try:
            candidate = index.entry(f"{archive_path}::{parent}")
        except KeyError as exc:
            raise ValueError(f"RPF target directory does not exist: {parent}") from exc
        if candidate.kind != "directory":
            raise ValueError(f"RPF target parent is not a directory: {parent}")

    @staticmethod
    def _nested_archive_chain(archive_path: str) -> tuple[str, ...]:
        normalized = _safe_virtual_path(archive_path, allow_empty=True)
        if not normalized:
            return ()
        chain = tuple(normalized.split("!"))
        if len(chain) > _MAX_NESTED_WRITE_DEPTH:
            raise ValueError(
                "Nested RPF writes are limited to "
                f"{_MAX_NESTED_WRITE_DEPTH} archive levels; found {len(chain)}"
            )
        return chain

    @classmethod
    def _require_supported_nested_archive(
        cls, index: RpfIndex, archive_path: str,
    ) -> None:
        if not archive_path:
            return
        chain = cls._nested_archive_chain(archive_path)
        indexed_archives = {
            archive.path.casefold() for archive in index.archives
        }
        container = ""
        for nested_entry_path in chain:
            try:
                entry = index.entry(f"{container}::{nested_entry_path}")
            except KeyError as exc:
                raise ValueError(
                    "Nested RPF chain is not present in the index: "
                    f"{container or 'root'}::{nested_entry_path}"
                ) from exc
            if entry.kind != "archive":
                raise ValueError(
                    f"Nested target is not an RPF archive: {entry.virtual_name}"
                )
            container = (
                nested_entry_path
                if not container else f"{container}!{nested_entry_path}"
            )
            if container.casefold() not in indexed_archives:
                raise ValueError(
                    "Nested RPF contents were not indexed and cannot be written: "
                    f"{container}"
                )

    def _plan_scope_is_authorized(
        self, plan: dict[str, Any], archive: Path,
    ) -> bool:
        current = self._target_scope(archive)
        if current != plan.get("target_scope"):
            return False
        if current == "workspace_copy":
            root = self._authorized_workspace_root(archive)
            return root is not None and str(root) == plan.get("authorized_root")
        return current == "mods_copy"

    @staticmethod
    def _copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.partial")
        temporary.unlink(missing_ok=True)
        try:
            shutil.copy2(source, temporary)
            if _sha256_file(temporary) != expected_hash:
                raise RuntimeError(f"Copy verification failed: {destination}")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _restore_snapshot(
        self, backup: Path, archive: Path, expected_hash: str,
    ) -> None:
        stage = archive.with_name(f".{archive.name}.allin1-restore")
        self._copy_verified(backup, stage, expected_hash)
        stage.replace(archive)
        if _sha256_file(archive) != expected_hash:
            raise RuntimeError("Rollback archive verification failed")

    @classmethod
    def _validate_receipt(
        cls, receipt: dict[str, Any],
    ) -> dict[str, Any]:
        if receipt.get("operation") == "rpf_multi_entry_change":
            return cls._validate_multi_receipt(receipt)
        if receipt.get("schema_version") != RPF_TRANSACTION_RECEIPT_SCHEMA:
            raise ValueError("Unsupported RPF transaction receipt")
        if receipt.get("operation") != "rpf_entry_change":
            raise ValueError("Unsupported RPF transaction operation")
        action = receipt.get("action")
        if action not in _RPF_ACTIONS:
            raise ValueError("RPF transaction receipt contains an invalid action")
        for key in ("original", "backup"):
            if not isinstance(receipt.get(key), dict):
                raise ValueError(f"RPF transaction receipt is missing {key} metadata")
        if action in {"replace", "add"} and not isinstance(receipt.get("payload"), dict):
            raise ValueError("RPF transaction receipt is missing payload metadata")
        if action == "delete" and receipt.get("payload") is not None:
            raise ValueError("Delete transaction receipt unexpectedly contains a payload")
        for key in ("transaction_id", "archive", "entry", "edition", "status"):
            if not isinstance(receipt.get(key), str) or not receipt[key].strip():
                raise ValueError(f"RPF transaction receipt is missing {key}")
        _safe_virtual_path(str(receipt.get("entry", "")))
        cls._nested_archive_chain(str(receipt.get("archive_path", "")))
        hashes: list[object] = [receipt.get("plan_id"), receipt["backup"].get("sha256")]
        if receipt["original"].get("exists"):
            hashes.append(receipt["original"].get("sha256"))
        if receipt.get("payload"):
            hashes.append(receipt["payload"].get("sha256"))
        for value in hashes:
            if not _is_sha256(value):
                raise ValueError("RPF transaction receipt contains an invalid SHA-256 value")
        applied = receipt.get("applied_archive_sha256")
        if applied is not None and not _is_sha256(applied):
            raise ValueError("RPF transaction receipt contains an invalid applied hash")
        if receipt["status"] in {"applied", "verified_staging", "rollback_failed"}:
            if not _is_sha256(applied):
                raise ValueError("Applied RPF transaction receipt is missing its archive hash")
        transaction_id = receipt["transaction_id"]
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
               for character in transaction_id) or ".." in transaction_id:
            raise ValueError("RPF transaction receipt contains an unsafe transaction id")
        size_containers = [(receipt["original"], "size"), (receipt["backup"], "size")]
        if receipt.get("payload"):
            size_containers.append((receipt["payload"], "size"))
        for container, label in size_containers:
            if not isinstance(container.get(label), int) or container[label] < 0:
                raise ValueError(f"RPF transaction receipt contains an invalid {label}")
        if not isinstance(receipt["original"].get("exists"), bool):
            raise ValueError("RPF transaction receipt has invalid original state")
        return receipt

    @classmethod
    def _validate_multi_receipt(
        cls, receipt: dict[str, Any],
    ) -> dict[str, Any]:
        if receipt.get("schema_version") != RPF_MULTI_TRANSACTION_RECEIPT_SCHEMA:
            raise ValueError("Unsupported RPF multi-change transaction receipt")
        if receipt.get("operation") != "rpf_multi_entry_change" or receipt.get(
            "action"
        ) != "batch":
            raise ValueError("Unsupported RPF multi-change transaction operation")
        if not isinstance(receipt.get("backup"), dict):
            raise ValueError("RPF multi-change receipt is missing backup metadata")
        for key in ("transaction_id", "archive", "edition", "status"):
            if not isinstance(receipt.get(key), str) or not receipt[key].strip():
                raise ValueError(f"RPF multi-change receipt is missing {key}")
        changes = receipt.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("RPF multi-change receipt contains no changes")
        if len(changes) > _MAX_MULTI_ENTRY_CHANGES:
            raise ValueError("RPF multi-change receipt exceeds the guarded change limit")
        seen: set[tuple[str, str]] = set()
        for number, change in enumerate(changes, start=1):
            if not isinstance(change, dict) or change.get("action") not in _RPF_ACTIONS:
                raise ValueError(f"RPF multi-change receipt item {number} is invalid")
            archive_path = _safe_virtual_path(
                str(change.get("archive_path", "")), allow_empty=True,
            )
            cls._nested_archive_chain(archive_path)
            entry_path = _safe_virtual_path(str(change.get("entry", "")))
            identity = (archive_path.casefold(), entry_path.casefold())
            if identity in seen:
                raise ValueError("RPF multi-change receipt contains a duplicate target")
            seen.add(identity)
            original = change.get("original")
            if not isinstance(original, dict) or not isinstance(
                original.get("exists"), bool,
            ):
                raise ValueError("RPF multi-change receipt has invalid original state")
            if not isinstance(original.get("size"), int) or original["size"] < 0:
                raise ValueError("RPF multi-change receipt has invalid original size")
            if original["exists"] and not _is_sha256(original.get("sha256")):
                raise ValueError("RPF multi-change receipt has invalid original hash")
            if change["action"] == "add" and original["exists"]:
                raise ValueError("RPF multi-change add receipt claims its target exists")
            if change["action"] in {"replace", "delete"} and not original["exists"]:
                raise ValueError(
                    "RPF multi-change receipt claims an existing target is absent"
                )
            payload = change.get("payload")
            if change["action"] in {"replace", "add"}:
                if not isinstance(payload, dict):
                    raise ValueError("RPF multi-change receipt is missing a payload")
                for key in ("source", "snapshot"):
                    if not isinstance(payload.get(key), str) or not payload[key].strip():
                        raise ValueError(
                            f"RPF multi-change receipt payload is missing {key}"
                        )
                if not isinstance(payload.get("size"), int) or payload["size"] < 0:
                    raise ValueError("RPF multi-change receipt payload has invalid size")
                if not _is_sha256(payload.get("sha256")):
                    raise ValueError("RPF multi-change receipt payload has invalid hash")
            elif payload is not None:
                raise ValueError("RPF multi-change delete receipt has a payload")
        for change in changes:
            container = (
                change["entry"] if not change["archive_path"]
                else f"{change['archive_path']}!{change['entry']}"
            ).casefold()
            if any(
                other is not change and (
                    other["archive_path"].casefold() == container
                    or other["archive_path"].casefold().startswith(container + "!")
                )
                for other in changes
            ):
                raise ValueError(
                    "RPF multi-change receipt mixes an archive replacement with "
                    "internal changes"
                )
        hashes: list[object] = [
            receipt.get("plan_id"), receipt["backup"].get("sha256"),
        ]
        if not all(_is_sha256(value) for value in hashes):
            raise ValueError("RPF multi-change receipt contains an invalid SHA-256 value")
        if not isinstance(receipt["backup"].get("size"), int) or receipt[
            "backup"
        ]["size"] < 0:
            raise ValueError("RPF multi-change receipt has invalid backup size")
        applied = receipt.get("applied_archive_sha256")
        if applied is not None and not _is_sha256(applied):
            raise ValueError("RPF multi-change receipt has invalid applied hash")
        if receipt["status"] in {"applied", "verified_staging", "rollback_failed"}:
            if not _is_sha256(applied):
                raise ValueError("RPF multi-change receipt is missing its applied hash")
        transaction_id = receipt["transaction_id"]
        if any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for character in transaction_id
        ) or ".." in transaction_id:
            raise ValueError("RPF multi-change receipt has an unsafe transaction id")
        return receipt

    def list_transactions(
        self, receipt_root: str | Path | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return newest-first receipt summaries, retaining malformed entries for review."""
        root = (
            Path(receipt_root).expanduser().resolve() if receipt_root is not None
            else user_data_root() / "rpf-transactions"
        )
        if not root.is_dir():
            return ()
        history: list[dict[str, Any]] = []
        for path in root.glob("*/receipt.json"):
            try:
                receipt = self._validate_receipt(
                    _read_json_object(path, "RPF transaction receipt")
                )
                entry_summary = (
                    f"{len(receipt['changes'])} entry changes"
                    if receipt["operation"] == "rpf_multi_entry_change"
                    else (
                        f"{receipt['archive_path']}::{receipt['entry']}"
                        if receipt["archive_path"] else receipt["entry"]
                    )
                )
                history.append({
                    "receipt": str(path.resolve()),
                    "transaction_id": receipt["transaction_id"],
                    "created_at": receipt.get("created_at", ""),
                    "status": receipt["status"], "action": receipt["action"],
                    "archive": receipt["archive"],
                    "entry": entry_summary,
                    "valid": True,
                })
            except (OSError, ValueError) as exc:
                history.append({
                    "receipt": str(path.resolve()), "transaction_id": path.parent.name,
                    "created_at": "", "status": "invalid", "action": "unknown",
                    "archive": "", "entry": "", "valid": False, "error": str(exc),
                })
        return tuple(sorted(
            history, key=lambda item: (item["created_at"], item["transaction_id"]),
            reverse=True,
        ))

    def recover_transaction(self, receipt_path: str | Path) -> dict[str, Any]:
        """Reconcile an interrupted receipt with the archive without committing a write."""
        source = Path(receipt_path).expanduser().resolve()
        receipt = self._validate_receipt(
            _read_json_object(source, "RPF transaction receipt")
        )
        verification = self.verify_transaction(source)
        state = verification["archive_state"]
        if not verification["healthy"]:
            raise RuntimeError(
                "Interrupted transaction cannot be reconciled safely: "
                + json.dumps(verification, sort_keys=True)
            )
        if state == "applied":
            receipt["status"] = "applied"
            receipt["recovered_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(source, receipt)
        elif state == "original" and receipt["status"] not in {
            "rolled_back", "rolled_back_after_failure",
        }:
            receipt["status"] = "interrupted_before_commit"
            receipt["recovered_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(source, receipt)
        return self.verify_transaction(source)

    def inspect_archive_lock(self, archive: str | Path) -> dict[str, Any] | None:
        target = Path(archive).expanduser().resolve()
        lock = target.with_name(f".{target.name}.allin1.lock")
        if not lock.is_file():
            return None
        payload = _read_json_object(lock, "RPF archive lock")
        pid = payload.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("RPF archive lock has an invalid process id")
        return {
            **payload, "path": str(lock), "process_running": self._pid_is_running(pid),
        }

    def clear_stale_lock(self, archive: str | Path) -> Path:
        """Remove a lock only when its owner is gone and the target remains authorized."""
        target = Path(archive).expanduser().resolve()
        if self._target_scope(target) == "unsafe":
            raise ValueError("Cannot clear a lock outside an authorized RPF write scope")
        self._require_game_closed()
        inspected = self.inspect_archive_lock(target)
        if inspected is None:
            raise FileNotFoundError("No ALLIN1 transaction lock exists for this archive")
        if inspected["process_running"]:
            raise RuntimeError(
                f"RPF lock owner is still running (PID {inspected['pid']})"
            )
        lock = Path(inspected["path"])
        lock.unlink()
        return lock

    def run_canary(
        self, source_archive: str | Path, *, output_root: str | Path | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Exercise a real RPF writer on a disposable copy and prove full rollback."""
        self._require_tool()
        source = Path(source_archive).expanduser().resolve()
        if not source.is_file() or source.suffix.casefold() != ".rpf":
            raise ValueError("RPF canary requires an existing loose .rpf archive")
        if source.stat().st_size > _MAX_CANARY_ARCHIVE_BYTES:
            raise ValueError("RPF canary source exceeds the 512 MiB safety limit")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        canary_root = (
            Path(output_root).expanduser().resolve() / timestamp
            if output_root is not None else user_data_root() / "rpf-canaries" / timestamp
        )
        canary_root.mkdir(parents=True, exist_ok=False)
        report_path = canary_root / "canary-report.json"
        source_hash = _sha256_file(source)
        canary = canary_root / source.name
        report: dict[str, Any] = {
            "schema_version": 1, "status": "preparing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source), "source_sha256": source_hash,
            "canary_archive": str(canary), "writes_to_source": False,
        }
        _write_json_atomic(report_path, report)
        try:
            self._emit(progress, "Copying real archive into isolated canary", 5)
            self._copy_verified(source, canary, source_hash)
            canary_service = RpfExplorerService(
                self.project_root, self.gta_path, workspace_roots=(canary_root,),
            )
            index = canary_service.index(canary)
            candidates = sorted(
                (entry for entry in index.entries
                 if not entry.archive_path and entry.kind in {"binary", "resource"}
                 and entry.size > 0),
                key=lambda entry: entry.size,
            )
            if not candidates:
                raise RuntimeError("Canary archive has no writable root file entries")
            entry = candidates[0]
            original_payload = canary_root / f"original-{entry.name}"
            canary_service.extract(index, entry, original_payload)
            data = bytearray(original_payload.read_bytes())
            if not data:
                raise RuntimeError("Selected canary entry extracted as an empty payload")
            data[len(data) // 2] ^= 0x01
            changed_payload = canary_root / f"changed-{entry.name}"
            changed_payload.write_bytes(data)
            plan = canary_service.replacement_plan(index, entry, changed_payload)
            if plan["status"] != "ready" or plan["target_scope"] != "workspace_copy":
                raise RuntimeError("Disposable canary copy was not authorized for writing")
            plan_path = canary_root / "canary-plan.json"
            _write_json_atomic(plan_path, plan)
            receipt = canary_service.apply_change_plan(
                plan_path, receipt_root=canary_root / "transactions", progress=progress,
            )
            applied = canary_service.verify_transaction(receipt)
            if not applied["healthy"] or applied["archive_state"] != "applied":
                raise RuntimeError(f"Canary apply verification failed: {applied}")
            canary_service.rollback_transaction(receipt, progress=progress)
            replace_restored = canary_service.verify_transaction(receipt)

            # Exercise root add and delete as a chained transaction. Roll back
            # delete first (restoring the added entry), then roll back add to
            # recover the source-identical archive.
            add_payload = canary_root / "allin1-sdk-canary.bin"
            add_payload.write_bytes(b"ALLIN1 SDK RPF canary\n")
            add_name = "allin1_sdk_canary.bin"
            restored_index = canary_service.index(canary)
            if any(item.id.casefold() == f"::{add_name}" for item in restored_index.entries):
                add_name = f"allin1_sdk_canary_{timestamp}.bin"
            add_plan = canary_service.addition_plan(
                restored_index, add_name, add_payload,
            )
            add_plan_path = canary_root / "canary-add-plan.json"
            _write_json_atomic(add_plan_path, add_plan)
            add_receipt = canary_service.apply_change_plan(
                add_plan_path, receipt_root=canary_root / "transactions",
                progress=progress,
            )
            add_verification = canary_service.verify_transaction(add_receipt)
            if not add_verification["healthy"]:
                raise RuntimeError(f"Canary add verification failed: {add_verification}")

            with_entry = canary_service.index(canary)
            delete_plan = canary_service.deletion_plan(
                with_entry, with_entry.entry(f"::{add_name}"),
            )
            delete_plan_path = canary_root / "canary-delete-plan.json"
            _write_json_atomic(delete_plan_path, delete_plan)
            delete_receipt = canary_service.apply_change_plan(
                delete_plan_path, receipt_root=canary_root / "transactions",
                progress=progress,
            )
            delete_verification = canary_service.verify_transaction(delete_receipt)
            if not delete_verification["healthy"]:
                raise RuntimeError(
                    f"Canary delete verification failed: {delete_verification}"
                )
            canary_service.rollback_transaction(delete_receipt, progress=progress)
            canary_service.rollback_transaction(add_receipt, progress=progress)
            restored = canary_service.verify_transaction(add_receipt)
            restored_hash = _sha256_file(canary)
            if (not restored["healthy"] or restored["archive_state"] != "original"
                    or restored_hash != source_hash):
                raise RuntimeError(f"Canary rollback verification failed: {restored}")
            report.update({
                "status": "passed", "edition": index.edition,
                "entry": entry.path, "entry_kind": entry.kind,
                "receipts": {
                    "replace": str(receipt), "add": str(add_receipt),
                    "delete": str(delete_receipt),
                },
                "replace_apply_verification": applied,
                "replace_rollback_verification": replace_restored,
                "add_verification": add_verification,
                "delete_verification": delete_verification,
                "rollback_verification": restored,
                "restored_sha256": restored_hash,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            _write_json_atomic(report_path, report)
            self._emit(progress, "Real-archive canary passed with exact rollback", 100)
            return report_path
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = str(exc)
            report["failed_at"] = datetime.now(timezone.utc).isoformat()
            _write_json_atomic(report_path, report)
            raise RuntimeError(f"RPF canary failed; report: {report_path}: {exc}") from exc

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        if pid == os.getpid():
            return True
        if os.name == "nt":
            completed = run_hidden(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode:
                raise RuntimeError("Could not verify the RPF lock owner process")
            return f'"{pid}"' in completed.stdout
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str, percent: int) -> None:
        if progress is not None:
            progress(message, percent)

    @staticmethod
    def _require_transaction_space(
        archive: Path, transaction_root: Path, archive_size: int,
        payload_size: int, *, backup_copy: bool = True,
    ) -> None:
        archive_available = shutil.disk_usage(archive.parent).free
        transaction_available = shutil.disk_usage(transaction_root).free
        same_volume = archive.anchor.casefold() == transaction_root.anchor.casefold()
        archive_required = archive_size + _COPY_MARGIN_BYTES
        transaction_required = (
            archive_size + payload_size + _COPY_MARGIN_BYTES if backup_copy
            else _COPY_MARGIN_BYTES
        )
        if same_volume:
            combined = archive_required + (transaction_required if backup_copy else 0)
            if archive_available < combined:
                raise RuntimeError(
                    f"Not enough free space for the RPF transaction; "
                    f"{combined:,} bytes are required"
                )
        elif archive_available < archive_required or transaction_available < transaction_required:
            raise RuntimeError("Not enough free space for RPF staging and rollback snapshots")

    @staticmethod
    def _require_game_closed() -> None:
        running = _running_gta_processes()
        if running:
            raise RuntimeError(
                "Close GTA V before changing an RPF archive: " + ", ".join(running)
            )
