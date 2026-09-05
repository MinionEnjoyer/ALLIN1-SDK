"""Structured RPF inspection and guarded replacement transactions."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    NATIVE_XML_IMPORT_SUFFIXES,
    NativeAssetInspector, NativeAssetReport,
)
from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.gxt2_workspace import Gxt2Workspace, MAX_GXT2_BYTES
from allin1_sdk.paths import user_data_root
from allin1_sdk.processes import run_hidden


RPF_REPLACEMENT_PLAN_SCHEMA = 3
RPF_TRANSACTION_RECEIPT_SCHEMA = 2
RPF_MULTI_CHANGE_PLAN_SCHEMA = 2
RPF_MULTI_TRANSACTION_RECEIPT_SCHEMA = 2
_GTA_PROCESS_NAMES = {"gta5.exe", "gta5_enhanced.exe"}
_COPY_MARGIN_BYTES = 64 * 1024 * 1024
_MAX_CANARY_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_DEFRAGMENT_ARCHIVE_BYTES = 32 * 1024 * 1024 * 1024
_MAX_NESTED_WRITE_DEPTH = 8
_MAX_SUBTREE_FILES = 25_000
_MAX_SUBTREE_LOGICAL_BYTES = 16 * 1024 * 1024 * 1024
_MAX_MULTI_ENTRY_CHANGES = 1_000
_RPF_ACTIONS = {"replace", "add", "delete"}
_RPF_TREE_ACTIONS = {"mkdir", "rmdir", "rename"}
_RPF_MULTI_ACTIONS = _RPF_ACTIONS | _RPF_TREE_ACTIONS
_WINDOWS_RESERVED_COMPONENTS = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})
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


def _content_fingerprint(path: str | Path) -> dict[str, Any]:
    """Hash raw bytes and canonical logical bytes for recompressible RSC7 resources."""
    source = Path(path).resolve()
    raw = hashlib.sha256()
    canonical = hashlib.sha256()
    size = source.stat().st_size
    with source.open("rb") as stream:
        header = stream.read(16)
        raw.update(header)
        if len(header) == 16 and header[:4] == b"RSC7":
            canonical.update(header)
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            logical_size = 0
            adler32 = 1
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                raw.update(block)
                expanded = inflater.decompress(block)
                logical_size += len(expanded)
                canonical.update(expanded)
                adler32 = zlib.adler32(expanded, adler32)
            expanded = inflater.flush()
            logical_size += len(expanded)
            canonical.update(expanded)
            adler32 = zlib.adler32(expanded, adler32)
            trailer = inflater.unused_data
            if not inflater.eof or (
                trailer and trailer != struct.pack(">I", adler32 & 0xFFFFFFFF)
            ):
                raise ValueError(f"Invalid or trailing RSC7 deflate stream: {source}")
            return {
                "mode": "rsc7_canonical", "size": size,
                "logical_size": logical_size,
                "raw_sha256": raw.hexdigest(),
                "canonical_sha256": canonical.hexdigest(),
                "resource_header_sha256": hashlib.sha256(header).hexdigest(),
                "resource_adler32_trailer": bool(trailer),
            }
        canonical.update(header)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            raw.update(block)
            canonical.update(block)
    return {
        "mode": "byte_exact", "size": size, "logical_size": size,
        "raw_sha256": raw.hexdigest(),
        "canonical_sha256": canonical.hexdigest(),
        "resource_header_sha256": None,
        "resource_adler32_trailer": False,
    }


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


def _read_json_object(path: str | Path, label: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_bytes()
        if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError(f"{label} changed after review")
        payload = json.loads(raw.decode("utf-8"))
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


def _safe_materialized_path(value: str) -> str:
    """Validate a virtual path that will become a real Windows loose-source path."""
    normalized = _safe_virtual_path(value)
    for component in PurePosixPath(normalized).parts:
        if (
            any(character in '<>:"\\|?*' for character in component)
            or component.rstrip(" .") != component
            or len(component.encode("utf-16-le")) // 2 > 255
            or component.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_COMPONENTS
        ):
            raise ValueError(
                f"RPF path cannot be materialized safely on Windows: {normalized}"
            )
    return normalized


def _nonnegative_integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _required_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{label} must be text")
    return value


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
        if not isinstance(self.edition, str) or not self.edition.strip():
            raise ValueError("RPF index has an invalid edition")
        if (
            not isinstance(self.archive_size, int)
            or isinstance(self.archive_size, bool)
            or self.archive_size < 0
        ):
            raise ValueError("RPF index has an invalid archive size")
        lookup: dict[str, RpfEntryRecord] = {}
        archive_paths = [archive.path.casefold() for archive in self.archives]
        if not self.archives or "" not in archive_paths:
            raise ValueError("RPF index does not contain a root archive")
        if len(archive_paths) != len(set(archive_paths)):
            raise ValueError("RPF index contains duplicate archive paths")
        for archive in self.archives:
            if _safe_virtual_path(archive.path, allow_empty=True) != archive.path:
                raise ValueError(f"RPF archive path is not normalized: {archive.path}")
            if not isinstance(archive.name, str) or not archive.name:
                raise ValueError(f"RPF archive has an invalid name: {archive.path}")
            if (
                not isinstance(archive.version, int)
                or isinstance(archive.version, bool)
                or archive.version < 0
                or not isinstance(archive.encryption, str)
                or not archive.encryption.strip()
                or not isinstance(archive.size, int)
                or isinstance(archive.size, bool)
                or archive.size < 0
                or not isinstance(archive.entry_count, int)
                or isinstance(archive.entry_count, bool)
                or archive.entry_count < 0
            ):
                raise ValueError(f"RPF archive metadata is invalid: {archive.path}")
        for entry in self.entries:
            expected_id = f"{entry.archive_path}::{entry.path}"
            if entry.id != expected_id:
                raise ValueError(f"RPF entry id does not match its path: {entry.id}")
            if (
                _safe_virtual_path(entry.archive_path, allow_empty=True)
                != entry.archive_path
                or _safe_virtual_path(entry.path) != entry.path
            ):
                raise ValueError(f"RPF entry path is not normalized: {entry.id}")
            if entry.archive_path.casefold() not in archive_paths:
                raise ValueError(f"RPF entry references an unknown archive: {entry.archive_path}")
            if entry.kind not in {"directory", "resource", "binary", "archive"}:
                raise ValueError(f"Unknown RPF entry kind: {entry.kind}")
            if (
                not isinstance(entry.size, int) or isinstance(entry.size, bool)
                or not isinstance(entry.stored_size, int)
                or isinstance(entry.stored_size, bool)
                or entry.size < 0 or entry.stored_size < 0
            ):
                raise ValueError(f"Negative RPF entry size: {entry.id}")
            if entry.encrypted is not None and not isinstance(entry.encrypted, bool):
                raise ValueError(f"Invalid RPF entry encrypted flag: {entry.id}")
            if entry.compressed is not None and not isinstance(entry.compressed, bool):
                raise ValueError(f"Invalid RPF entry compressed flag: {entry.id}")
            for label, value in (
                ("offset", entry.offset),
                ("resource version", entry.resource_version),
                ("system size", entry.system_size),
                ("graphics size", entry.graphics_size),
                ("child count", entry.child_count),
            ):
                if value is not None and (
                    not isinstance(value, int) or isinstance(value, bool) or value < 0
                ):
                    raise ValueError(f"Invalid RPF entry {label}: {entry.id}")
            for label, value in (
                ("system flags", entry.system_flags),
                ("graphics flags", entry.graphics_flags),
            ):
                if value is not None and (
                    not isinstance(value, str) or not value.strip()
                ):
                    raise ValueError(f"Invalid RPF entry {label}: {entry.id}")
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
        if not isinstance(payload, dict):
            raise ValueError("Malformed RPF index: expected a JSON object")
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported RPF index schema")
        try:
            if not isinstance(payload["archives"], list) or not isinstance(
                payload["entries"], list
            ):
                raise TypeError("archives and entries must be arrays")
            if any(not isinstance(item, dict) for item in payload["archives"]):
                raise TypeError("archives must contain objects")
            archives = tuple(
                RpfArchiveRecord(
                    path=_safe_virtual_path(
                        _required_string(
                            item.get("path", ""), "RPF archive path", allow_empty=True,
                        ),
                        allow_empty=True,
                    ),
                    name=_required_string(item["name"], "RPF archive name"),
                    version=_nonnegative_integer(
                        item["version"], "RPF archive version",
                    ),
                    encryption=_required_string(
                        item["encryption"], "RPF archive encryption",
                    ),
                    size=_nonnegative_integer(item["size"], "RPF archive size"),
                    entry_count=_nonnegative_integer(
                        item["entry_count"], "RPF archive entry count",
                    ),
                )
                for item in payload["archives"]
            )
            allowed = set(RpfEntryRecord.__dataclass_fields__)
            entries = []
            for authored in payload["entries"]:
                if not isinstance(authored, dict):
                    raise TypeError("entries must contain objects")
                item = {key: value for key, value in authored.items() if key in allowed}
                item["archive_path"] = _safe_virtual_path(
                    _required_string(
                        item.get("archive_path", ""), "RPF entry archive path",
                        allow_empty=True,
                    ),
                    allow_empty=True,
                )
                item["path"] = _safe_virtual_path(
                    _required_string(item["path"], "RPF entry path"),
                )
                item["id"] = _required_string(item["id"], "RPF entry id")
                item["name"] = _required_string(item["name"], "RPF entry name")
                item["kind"] = _required_string(item["kind"], "RPF entry kind")
                item["size"] = _nonnegative_integer(item["size"], "RPF entry size")
                item["stored_size"] = _nonnegative_integer(
                    item["stored_size"], "RPF entry stored size",
                )
                entries.append(RpfEntryRecord(**item))
            source = Path(_required_string(payload["source"], "RPF index source")).resolve()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed RPF index: {exc}") from exc
        return cls(
            source=source, edition=_required_string(payload["edition"], "RPF index edition"),
            archive_size=_nonnegative_integer(
                payload["archive_size"], "RPF index archive size",
            ),
            archives=archives,
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
        self.workspace_roots = tuple(sorted(
            dict.fromkeys(roots),
            key=lambda item: (-len(item.parts), str(item).casefold(), str(item)),
        ))

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

    def extract_many(
        self, index: RpfIndex, entries: Iterable[RpfEntryRecord],
        destination: str | Path,
    ) -> tuple[Path, ...]:
        """Extract exact entries in one archive scan into a new guarded folder."""
        self._require_tool()
        selected = tuple(entries)
        if not selected:
            raise ValueError("Select at least one RPF entry to extract")
        if len(selected) > 512:
            raise ValueError("A single guarded extraction is limited to 512 entries")
        if len({item.id for item in selected}) != len(selected):
            raise ValueError("RPF batch extraction contains duplicate entries")
        for entry in selected:
            if entry.kind == "directory" or index.entry(entry.id) != entry:
                raise ValueError("An entry does not belong to this RPF index")
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"RPF batch destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256_file(index.source)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.allin1-stage-", dir=target.parent,
        )).resolve()
        relative = tuple(
            f"{number:04d}{entry.suffix or '.bin'}"
            for number, entry in enumerate(selected, start=1)
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix="allin1-rpf-batch-manifest-",
            ) as temporary:
                manifest = Path(temporary) / "entries.tsv"
                manifest.write_text("".join(
                    f"{entry.archive_path}\t{entry.path}\t{name}\n"
                    for entry, name in zip(selected, relative)
                ), encoding="utf-8")
                completed = run_hidden(
                    [
                        self.patcher, "extract-virtual-entries", self.gta_path,
                        index.source, manifest, staging,
                    ],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace",
                )
            if completed.returncode:
                detail = (
                    completed.stderr or completed.stdout
                    or "unknown helper error"
                ).strip()
                raise ValueError(f"RPF batch extraction failed: {detail}")
            produced = tuple(staging / name for name in relative)
            if any(not path.is_file() for path in produced):
                raise ValueError("RPF helper omitted a requested batch entry")
            if _sha256_file(index.source) != source_hash:
                raise RuntimeError(
                    "RPF changed during read-only batch extraction; output was discarded"
                )
            staging.rename(target)
            return tuple(target / name for name in relative)
        except Exception:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise

    def export_native_workspace(
        self, index: RpfIndex, entry: RpfEntryRecord, destination: str | Path,
    ) -> Path:
        """Extract an exact RPF resource into a snapshot-backed XML workspace."""
        suffix = Path(entry.name).suffix.casefold()
        if suffix not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError(f"Native XML round-trip is not supported for {entry.name}")
        if entry.size <= 0 or entry.size > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Selected RPF native asset is empty or exceeds the guarded limit")
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-native-export-") as temporary:
            source = self.extract(index, entry, Path(temporary) / entry.name)
            data = source.read_bytes()
        return NativeAssetInspector(
            self.project_root, self.gta_path,
        ).export_workspace_bytes(
            entry.name, data, destination, edition=index.edition,
        )

    def inspect_native_entry(
        self, index: RpfIndex, entry: RpfEntryRecord,
    ) -> tuple[NativeAssetReport, dict[str, object]]:
        """Inspect one exact RPF member while proving the archive stayed unchanged."""
        if entry.kind == "directory" or index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        if entry.size <= 0 or entry.size > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Selected RPF asset is empty or exceeds the guarded limit")
        archive_hash = _sha256_file(index.source)
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-native-inspect-") as temporary:
            source = self.extract(index, entry, Path(temporary) / entry.name)
            data = source.read_bytes()
        if not data or len(data) > MAX_NATIVE_PREVIEW_BYTES:
            raise RuntimeError("Extracted RPF asset is empty or exceeds the guarded limit")
        if _sha256_file(index.source) != archive_hash:
            raise RuntimeError("RPF changed during native asset inspection")
        report = NativeAssetInspector(
            self.project_root, self.gta_path,
        ).inspect_bytes(
            entry.name, data, edition=index.edition,
        )
        return report, {
            "outer_archive": str(index.source),
            "outer_archive_sha256": archive_hash,
            "archive_path": entry.archive_path,
            "entry_path": entry.path,
            "entry_id": entry.id,
            "extracted_size": len(data),
            "extracted_sha256": report.sha256,
        }

    def export_binary_workspace(
        self, index: RpfIndex, entry: RpfEntryRecord, destination: str | Path,
    ) -> Path:
        """Extract any bounded exact entry into a snapshot-backed hex workspace."""
        if entry.kind == "directory" or index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        if entry.size <= 0 or entry.size > 512 * 1024 * 1024:
            raise ValueError("Selected binary entry is empty or exceeds the guarded limit")
        archive_hash = _sha256_file(index.source)
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-binary-export-") as temporary:
            source = self.extract(index, entry, Path(temporary) / entry.name)
            data = source.read_bytes()
        if _sha256_file(index.source) != archive_hash:
            raise RuntimeError("RPF changed during binary workspace export")
        return BinaryPatchWorkspace().export_bytes(
            entry.name, data, destination,
            source_binding={
                "outer_archive": str(index.source),
                "outer_archive_sha256": archive_hash,
                "entry_id": entry.id,
                "edition": index.edition,
            },
        )

    def read_gxt2_entry(
        self, index: RpfIndex, entry: RpfEntryRecord,
    ) -> tuple[bytes, dict[str, object]]:
        """Read a complete bounded dictionary and bind it to an unchanged archive."""
        if entry.kind == "directory" or index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        if entry.suffix != ".gxt2":
            raise ValueError(f"GXT2 text editing is not supported for {entry.name}")
        if entry.size < 16 or entry.size > MAX_GXT2_BYTES:
            raise ValueError("Selected GXT2 entry is invalid or exceeds the guarded limit")
        archive_hash = _sha256_file(index.source)
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-gxt2-export-") as temporary:
            source = self.extract(index, entry, Path(temporary) / "dictionary.gxt2")
            if source.stat().st_size > MAX_GXT2_BYTES:
                raise ValueError("Extracted GXT2 exceeds the guarded limit")
            with source.open("rb") as stream:
                data = stream.read(MAX_GXT2_BYTES + 1)
        if len(data) != entry.size or len(data) > MAX_GXT2_BYTES:
            raise ValueError("Extracted GXT2 size does not match the indexed dictionary")
        if _sha256_file(index.source) != archive_hash:
            raise RuntimeError("RPF changed during GXT2 dictionary read")
        Gxt2Workspace.parse(data)
        return data, {
            "outer_archive": str(index.source),
            "outer_archive_sha256": archive_hash,
            "entry_id": entry.id,
            "edition": index.edition,
        }

    def export_gxt2_workspace(
        self, index: RpfIndex, entry: RpfEntryRecord, destination: str | Path,
    ) -> Path:
        """Extract one exact GXT2 dictionary into a bound text workspace."""
        data, binding = self.read_gxt2_entry(index, entry)
        return Gxt2Workspace().export_bytes(
            entry.name, data, destination, source_binding=binding,
        )

    def plan_gxt2_workspace_replacement(
        self, index: RpfIndex, entry: RpfEntryRecord, workspace: str | Path,
        plan_destination: str | Path,
    ) -> tuple[Path, Path, Path]:
        """Rebuild/reparse a bound GXT2 dictionary and create its RPF plan."""
        if entry.kind == "directory" or index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        if entry.suffix != ".gxt2":
            raise ValueError(f"GXT2 text editing is not supported for {entry.name}")
        state = Gxt2Workspace.validate(workspace)
        binding = state["manifest"].get("source_binding", {})
        expected = {
            "outer_archive": str(index.source),
            "outer_archive_sha256": _sha256_file(index.source),
            "entry_id": entry.id,
            "edition": index.edition,
        }
        if not isinstance(binding, dict) or any(
            str(binding.get(key, "")).casefold() != str(value).casefold()
            for key, value in expected.items()
        ):
            raise ValueError(
                "GXT2 workspace is not bound to this exact RPF archive and entry"
            )
        plan_path = Path(plan_destination).expanduser().resolve()
        if plan_path.suffix.casefold() != ".json":
            raise ValueError("GXT2 RPF replacement plan must use a .json extension")
        if plan_path.exists() or plan_path.is_symlink():
            raise ValueError(f"GXT2 RPF replacement plan already exists: {plan_path}")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        payload_dir = plan_path.with_name(f"{plan_path.stem}.payload")
        if payload_dir.exists() or payload_dir.is_symlink():
            raise ValueError(f"GXT2 RPF plan payload folder already exists: {payload_dir}")
        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{plan_path.stem}.gxt2-stage-", dir=plan_path.parent,
        )).resolve()
        published = False
        try:
            staged_asset, staged_report = Gxt2Workspace.build(
                workspace, stage_root / entry.name,
            )
            stage_root.rename(payload_dir)
            published = True
            asset = payload_dir / staged_asset.name
            report = payload_dir / staged_report.name
            plan = self.replacement_plan(index, entry, asset)
            plan["gxt2_workspace"] = {
                "path": str(Path(workspace).resolve()),
                "manifest_sha256": _sha256_file(
                    Path(workspace).resolve() / "gxt2-workspace.json"
                ),
                "rebuilt_asset": str(asset),
                "validation_report": str(report),
                "validation_report_sha256": _sha256_file(report),
            }
            _write_json_atomic(plan_path, plan)
            return plan_path, asset, report
        except Exception:
            cleanup = payload_dir if published else stage_root
            if cleanup.is_dir() and cleanup.parent == plan_path.parent:
                shutil.rmtree(cleanup)
            plan_path.unlink(missing_ok=True)
            raise

    def plan_binary_workspace_replacement(
        self, index: RpfIndex, entry: RpfEntryRecord, workspace: str | Path,
        plan_destination: str | Path,
    ) -> tuple[Path, Path, Path]:
        """Build an auditable same-size binary diff and bind it to an RPF plan."""
        if entry.kind == "directory" or index.entry(entry.id) != entry:
            raise ValueError("Entry does not belong to this RPF index")
        state = BinaryPatchWorkspace.validate(workspace)
        binding = state["manifest"].get("source_binding", {})
        expected = {
            "outer_archive": str(index.source),
            "outer_archive_sha256": _sha256_file(index.source),
            "entry_id": entry.id,
            "edition": index.edition,
        }
        if not isinstance(binding, dict) or any(
            str(binding.get(key, "")).casefold() != str(value).casefold()
            for key, value in expected.items()
        ):
            raise ValueError(
                "Binary workspace is not bound to this exact RPF archive and entry"
            )
        plan_path = Path(plan_destination).expanduser().resolve()
        if plan_path.suffix.casefold() != ".json":
            raise ValueError("Binary RPF replacement plan must use a .json extension")
        if plan_path.exists() or plan_path.is_symlink():
            raise ValueError(f"Binary RPF replacement plan already exists: {plan_path}")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        payload_dir = plan_path.with_name(f"{plan_path.stem}.payload")
        if payload_dir.exists() or payload_dir.is_symlink():
            raise ValueError(f"Binary RPF plan payload folder already exists: {payload_dir}")
        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{plan_path.stem}.binary-stage-", dir=plan_path.parent,
        )).resolve()
        published = False
        try:
            staged_asset, staged_report = BinaryPatchWorkspace.build(
                workspace, stage_root / entry.name,
            )
            stage_root.rename(payload_dir)
            published = True
            asset = payload_dir / staged_asset.name
            report = payload_dir / staged_report.name
            plan = self.replacement_plan(index, entry, asset)
            plan["binary_workspace"] = {
                "path": str(Path(workspace).resolve()),
                "manifest_sha256": _sha256_file(
                    Path(workspace).resolve() / "binary-workspace.json"
                ),
                "rebuilt_asset": str(asset),
                "diff_report": str(report),
                "diff_report_sha256": _sha256_file(report),
            }
            _write_json_atomic(plan_path, plan)
            return plan_path, asset, report
        except Exception:
            cleanup = payload_dir if published else stage_root
            if cleanup.is_dir() and cleanup.parent == plan_path.parent:
                shutil.rmtree(cleanup)
            plan_path.unlink(missing_ok=True)
            raise

    def plan_native_workspace_replacement(
        self, index: RpfIndex, entry: RpfEntryRecord, workspace: str | Path,
        plan_destination: str | Path,
    ) -> tuple[Path, Path, Path]:
        """Build, reparse, and bind a native workspace to a guarded RPF plan."""
        if Path(entry.name).suffix.casefold() not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError(f"Native XML round-trip is not supported for {entry.name}")
        if index.entry(entry.id) != entry or entry.kind == "directory":
            raise ValueError("Entry does not belong to this RPF index")
        plan_path = Path(plan_destination).expanduser().resolve()
        if plan_path.suffix.casefold() != ".json":
            raise ValueError("Native RPF replacement plan must use a .json extension")
        if plan_path.exists() or plan_path.is_symlink():
            raise ValueError(f"Native RPF replacement plan already exists: {plan_path}")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        payload_dir = plan_path.with_name(f"{plan_path.stem}.payload")
        if payload_dir.exists() or payload_dir.is_symlink():
            raise ValueError(f"Native RPF plan payload folder already exists: {payload_dir}")
        stage_root = Path(tempfile.mkdtemp(
            prefix=f".{plan_path.stem}.native-stage-", dir=plan_path.parent,
        )).resolve()
        published = False
        try:
            staged_asset, staged_report = NativeAssetInspector(
                self.project_root, self.gta_path,
            ).build_workspace(workspace, stage_root / entry.name)
            stage_root.rename(payload_dir)
            published = True
            asset = payload_dir / staged_asset.name
            report = payload_dir / staged_report.name
            plan = self.replacement_plan(index, entry, asset)
            workspace_manifest = Path(workspace).resolve() / "native-workspace.json"
            plan["native_workspace"] = {
                "path": str(Path(workspace).resolve()),
                "manifest_sha256": _sha256_file(workspace_manifest),
                "rebuilt_asset": str(asset),
                "validation_report": str(report),
                "validation_report_sha256": _sha256_file(report),
            }
            _write_json_atomic(plan_path, plan)
            return plan_path, asset, report
        except Exception:
            cleanup = payload_dir if published else stage_root
            if cleanup.is_dir() and cleanup.parent == plan_path.parent:
                shutil.rmtree(cleanup)
            try:
                plan_path.unlink()
            except FileNotFoundError:
                pass
            raise

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
        selected_directories = tuple(
            entry for entry in index.entries
            if entry.archive_path.casefold() == selected_archive.casefold()
            and entry.kind == "directory"
            and entry.path.casefold() != selected_directory.casefold()
            and (not prefix or entry.path.casefold().startswith(prefix.casefold()))
        )
        if not selected and not selected_directories:
            label = f"{selected_archive}::{selected_directory}".strip(":") or "root"
            raise ValueError(f"RPF subtree contains no exportable entries: {label}")
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

        directory_exports: list[tuple[RpfEntryRecord, str]] = []
        for entry in sorted(selected_directories, key=lambda item: item.path.casefold()):
            relative = entry.path[len(prefix):] if prefix else entry.path
            relative = _safe_virtual_path(relative)
            folded = relative.casefold()
            if folded in destinations:
                raise ValueError(
                    f"RPF subtree contains a file/directory output collision: {relative}"
                )
            destinations.add(folded)
            directory_exports.append((entry, relative))

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
            for _entry, relative in directory_exports:
                directory_output = (staging / Path(relative)).resolve()
                if not directory_output.is_relative_to(staging):
                    raise ValueError(f"RPF subtree directory escapes staging: {relative}")
                directory_output.mkdir(parents=True, exist_ok=True)
            if exports:
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
            directories = [{
                "archive_path": entry.archive_path,
                "entry_path": entry.path,
                "relative_path": relative,
                "child_count": entry.child_count,
            } for entry, relative in directory_exports]
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
                "directory_count": len(directories),
                "logical_bytes": logical_bytes,
                "directories": directories,
                "files": files,
            }
            _write_json_atomic(staging / ".allin1-rpf-export.json", export_manifest)
            staging.replace(target)
            return target
        except Exception:
            if staging.is_dir() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise

    def extract_authoring_tree(
        self, index: RpfIndex, destination: str | Path,
    ) -> tuple[Path, dict[str, Any]]:
        """Expand a complete recursive RPF index into a loose nested authoring tree."""
        self._require_tool()
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"RPF authoring-tree destination already exists: {target}")
        if not index.source.is_file() or index.source.stat().st_size != index.archive_size:
            raise ValueError("RPF source changed after indexing; index it again")
        source_sha256 = _sha256_file(index.source)
        archive_paths = {archive.path.casefold() for archive in index.archives}

        def archive_prefix(archive_path: str) -> str:
            if not archive_path:
                return ""
            output_parts: list[str] = []
            for nested in archive_path.split("!"):
                parts = list(PurePosixPath(_safe_virtual_path(nested)).parts)
                if not parts or not parts[-1].casefold().endswith(".rpf"):
                    raise ValueError(f"Invalid nested RPF authoring path: {archive_path}")
                output_parts.extend(parts[:-1])
                output_parts.append(f"{parts[-1]}.source")
            return "/".join(output_parts)

        paths: dict[str, str] = {}
        directories: set[str] = set()

        def register(relative: str, kind: str) -> str:
            safe = _safe_materialized_path(relative)
            folded = safe.casefold()
            previous = paths.get(folded)
            if previous is not None:
                raise ValueError(
                    f"RPF authoring tree has a case-insensitive output collision: {safe}"
                )
            paths[folded] = kind
            if kind == "directory":
                directories.add(safe)
            return safe

        exports: list[tuple[RpfEntryRecord, str]] = []
        for entry in sorted(index.entries, key=lambda item: item.id.casefold()):
            prefix = archive_prefix(entry.archive_path)
            relative = f"{prefix}/{entry.path}" if prefix else entry.path
            if entry.kind == "directory":
                register(relative, "directory")
                continue
            if entry.kind == "archive":
                nested_archive_path = (
                    entry.path if not entry.archive_path
                    else f"{entry.archive_path}!{entry.path}"
                )
                if nested_archive_path.casefold() not in archive_paths:
                    raise ValueError(
                        "RPF authoring import requires every nested archive to be "
                        f"recursively indexed: {nested_archive_path}"
                    )
                register(f"{relative}.source", "directory")
                continue
            exports.append((entry, register(relative, "file")))

        ordered_paths = sorted(paths)
        for position, folded in enumerate(ordered_paths):
            kind = paths[folded]
            parts = PurePosixPath(folded).parts
            for depth in range(1, len(parts)):
                ancestor = "/".join(parts[:depth])
                if paths.get(ancestor) == "file":
                    raise ValueError(
                        "RPF authoring tree has a file used as a directory: "
                        f"{ancestor}"
                    )
            if (
                kind == "file" and position + 1 < len(ordered_paths)
                and ordered_paths[position + 1].startswith(f"{folded}/")
            ):
                raise ValueError(
                    f"RPF authoring tree has descendants beneath a file: {folded}"
                )

        if len(exports) > _MAX_SUBTREE_FILES:
            raise ValueError(
                f"RPF authoring import exceeds the {_MAX_SUBTREE_FILES:,}-file limit"
            )
        logical_bytes = sum(entry.size for entry, _relative in exports)
        if logical_bytes > _MAX_SUBTREE_LOGICAL_BYTES:
            raise ValueError(
                "RPF authoring import exceeds the guarded logical-byte limit"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.rpf-authoring-", dir=target.parent,
        )).resolve()
        try:
            if shutil.disk_usage(staging).free < logical_bytes + _COPY_MARGIN_BYTES:
                raise ValueError("Not enough temporary disk space for RPF authoring import")
            for relative in sorted(
                directories, key=lambda value: (len(PurePosixPath(value).parts), value.casefold()),
            ):
                (staging / Path(relative)).mkdir(parents=True, exist_ok=True)
            if exports:
                with tempfile.TemporaryDirectory(prefix="allin1-rpf-authoring-manifest-") as temporary:
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
                    raise ValueError(f"RPF authoring-tree extraction failed: {detail}")

            files: list[dict[str, Any]] = []
            for entry, relative in exports:
                output = (staging / Path(relative)).resolve()
                if not output.is_relative_to(staging) or not output.is_file():
                    raise ValueError(
                        f"RPF helper omitted an authoring-tree payload: {entry.id}"
                    )
                files.append({
                    "archive_path": entry.archive_path,
                    "entry_path": entry.path,
                    "relative_path": relative,
                    "kind": entry.kind,
                    "actual_size": output.stat().st_size,
                    "sha256": _sha256_file(output),
                })
            if _sha256_file(index.source) != source_sha256:
                raise RuntimeError(
                    "RPF source changed during authoring import; output was discarded"
                )
            report = {
                "schema_version": 1,
                "operation": "rpf_authoring_tree_export",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "path": str(index.source), "edition": index.edition,
                    "size": index.archive_size, "sha256": source_sha256,
                },
                "summary": {
                    "archives": len(index.archives), "directories": len(directories),
                    "files": len(files), "logical_bytes": logical_bytes,
                },
                "directories": sorted(directories, key=str.casefold),
                "files": files,
            }
            staging.replace(target)
            return target, report
        except Exception:
            if staging.is_dir() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise

    def compare_indexes(
        self, left: RpfIndex, right: RpfIndex, *, exact_content: bool = False,
        logical_content: bool = False,
    ) -> dict[str, Any]:
        """Compare two recursively indexed archives without modifying either source."""
        self._require_tool()
        if exact_content and logical_content:
            raise ValueError("Choose exact-content or logical-content RPF diff, not both")
        for label, index in (("left", left), ("right", right)):
            if not index.source.is_file():
                raise ValueError(f"RPF diff {label} source no longer exists: {index.source}")
            if index.source.stat().st_size != index.archive_size:
                raise ValueError(
                    f"RPF diff {label} source changed after indexing; index it again"
                )
        left_source_hash = _sha256_file(left.source)
        right_source_hash = _sha256_file(right.source)
        left_hashes: dict[str, Any] = {}
        right_hashes: dict[str, Any] = {}
        if exact_content:
            left_hashes = self._batch_content_hashes(
                left, (entry for entry in left.entries if entry.kind != "directory"),
                expected_source_sha256=left_source_hash,
            )
            right_hashes = self._batch_content_hashes(
                right, (entry for entry in right.entries if entry.kind != "directory"),
                expected_source_sha256=right_source_hash,
            )
        elif logical_content:
            left_hashes = self._batch_content_fingerprints(
                left, (entry for entry in left.entries if entry.kind != "directory"),
                expected_source_sha256=left_source_hash,
            )
            right_hashes = self._batch_content_fingerprints(
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
            entry: RpfEntryRecord, hashes: dict[str, Any],
        ) -> dict[str, Any]:
            item = {
                "archive_path": entry.archive_path,
                "path": entry.path,
                "kind": entry.kind,
                "size": entry.size,
                "stored_size": entry.stored_size,
            }
            if entry.id in hashes:
                fingerprint = hashes[entry.id]
                if isinstance(fingerprint, str):
                    item["sha256"] = fingerprint
                else:
                    item["content"] = {
                        key: fingerprint[key]
                        for key in (
                            "mode", "size", "logical_size", "raw_sha256",
                            "canonical_sha256", "resource_header_sha256",
                        )
                        if key in fingerprint
                    }
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
            compared_fields = _RPF_DIFF_ENTRY_FIELDS
            if logical_content:
                resource_pair = (
                    before.kind != "directory" and after.kind != "directory"
                    and left_hashes[before.id]["mode"] == "rsc7_canonical"
                    and right_hashes[after.id]["mode"] == "rsc7_canonical"
                )
                compared_fields = tuple(
                    field for field in compared_fields
                    if field not in (
                        {"size", "stored_size", "compressed"} if resource_pair
                        else {"stored_size", "compressed"}
                    )
                )
            for field_name in compared_fields:
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
            elif logical_content and before.kind != "directory" and after.kind != "directory":
                content_compared += 1
                old_fingerprint = left_hashes[before.id]
                new_fingerprint = right_hashes[after.id]
                old_identity = (
                    old_fingerprint["canonical_sha256"],
                    old_fingerprint["logical_size"],
                )
                new_identity = (
                    new_fingerprint["canonical_sha256"],
                    new_fingerprint["logical_size"],
                )
                if old_identity != new_identity:
                    changes["logical_content"] = {
                        "left": {
                            "canonical_sha256": old_identity[0],
                            "logical_size": old_identity[1],
                        },
                        "right": {
                            "canonical_sha256": new_identity[0],
                            "logical_size": new_identity[1],
                        },
                    }
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
            "comparison_mode": (
                "exact_content" if exact_content else
                "logical_content" if logical_content else "metadata"
            ),
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

    def _batch_content_fingerprints(
        self, index: RpfIndex, entries: Iterable[RpfEntryRecord], *,
        expected_source_sha256: str,
    ) -> dict[str, dict[str, Any]]:
        selected = tuple(entries)
        if len(selected) > _MAX_SUBTREE_FILES:
            raise ValueError(
                f"Exact RPF verification contains {len(selected):,} files; "
                f"the guarded limit is {_MAX_SUBTREE_FILES:,}"
            )
        logical_bytes = sum(entry.size for entry in selected)
        if logical_bytes > _MAX_SUBTREE_LOGICAL_BYTES:
            raise ValueError(
                f"Exact RPF verification requires {logical_bytes:,} logical bytes; "
                f"the guarded limit is {_MAX_SUBTREE_LOGICAL_BYTES:,}"
            )
        if not selected:
            return {}
        with tempfile.TemporaryDirectory(prefix="allin1-rpf-fingerprint-") as temporary:
            root = Path(temporary)
            if shutil.disk_usage(root).free < logical_bytes + _COPY_MARGIN_BYTES:
                raise ValueError("Not enough temporary disk space for RPF verification")
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
                raise ValueError(f"RPF fingerprint extraction failed: {detail}")
            fingerprints: dict[str, dict[str, Any]] = {}
            for number, entry in enumerate(selected):
                output = output_root / f"{number:08d}.bin"
                if not output.is_file():
                    raise ValueError(
                        f"RPF verification did not extract the expected entry: {entry.id}"
                    )
                fingerprint = _content_fingerprint(output)
                fingerprint["entry_kind"] = entry.kind
                fingerprints[entry.id] = fingerprint
        if _sha256_file(index.source) != expected_source_sha256:
            raise RuntimeError("RPF changed during content fingerprinting")
        return fingerprints

    def entry_content_hashes(
        self, index: RpfIndex, entries: Iterable[RpfEntryRecord] | None = None,
    ) -> dict[str, str]:
        """Extract and hash exact indexed payloads through one bounded helper call."""
        selected = tuple(
            entry for entry in (entries if entries is not None else index.entries)
            if entry.kind != "directory"
        )
        indexed = {entry.id for entry in index.entries}
        if any(entry.id not in indexed for entry in selected):
            raise ValueError("Content-hash entry does not belong to this RPF index")
        source_sha256 = _sha256_file(index.source)
        return self._batch_content_hashes(
            index, selected, expected_source_sha256=source_sha256,
        )

    def entry_content_fingerprints(
        self, index: RpfIndex, entries: Iterable[RpfEntryRecord] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Hash raw and canonical logical content for exact resource-safe verification."""
        selected = tuple(
            entry for entry in (entries if entries is not None else index.entries)
            if entry.kind != "directory"
        )
        indexed = {entry.id for entry in index.entries}
        if any(entry.id not in indexed for entry in selected):
            raise ValueError("Fingerprint entry does not belong to this RPF index")
        return self._batch_content_fingerprints(
            index, selected, expected_source_sha256=_sha256_file(index.source),
        )

    def verify_archive_integrity(
        self, index: RpfIndex, destination: str | Path,
    ) -> tuple[Path, dict[str, Any]]:
        """Prove recursive structure and exact extraction for an existing RPF."""
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != ".json":
            raise ValueError("RPF integrity report must use a .json extension")
        if output.exists() or output.is_symlink():
            raise ValueError(f"RPF integrity report already exists: {output}")
        source_hash = _sha256_file(index.source)
        issues: list[dict[str, str]] = []
        entries = {entry.id.casefold(): entry for entry in index.entries}
        archives = {archive.path.casefold(): archive for archive in index.archives}

        for entry in index.entries:
            parent = PurePosixPath(entry.path).parent.as_posix()
            if parent == ".":
                continue
            parent_id = f"{entry.archive_path}::{parent}".casefold()
            parent_entry = entries.get(parent_id)
            if parent_entry is None:
                issues.append({
                    "code": "missing_parent_directory",
                    "entry": entry.id,
                    "message": f"Indexed parent directory is absent: {parent}",
                })
            elif parent_entry.kind != "directory":
                issues.append({
                    "code": "parent_is_not_directory",
                    "entry": entry.id,
                    "message": f"Indexed parent is not a directory: {parent_entry.id}",
                })

        for archive in index.archives:
            if not archive.path:
                continue
            levels = archive.path.split("!")
            parent_archive = "!".join(levels[:-1])
            archive_entry_id = f"{parent_archive}::{levels[-1]}".casefold()
            archive_entry = entries.get(archive_entry_id)
            if archive_entry is None or archive_entry.kind != "archive":
                issues.append({
                    "code": "orphan_nested_archive",
                    "entry": archive.path,
                    "message": "Nested archive has no matching archive entry in its parent.",
                })
        for entry in (item for item in index.entries if item.kind == "archive"):
            nested = entry.path if not entry.archive_path else f"{entry.archive_path}!{entry.path}"
            if nested.casefold() not in archives:
                issues.append({
                    "code": "unindexed_archive_entry",
                    "entry": entry.id,
                    "message": "Archive entry was not recursively indexed.",
                })

        content_hashes = self.entry_content_hashes(index)
        if set(content_hashes) != {
            entry.id for entry in index.entries if entry.kind != "directory"
        }:
            raise ValueError("Exact integrity extraction omitted one or more RPF payloads")
        if _sha256_file(index.source) != source_hash:
            raise RuntimeError("RPF changed during integrity verification")
        duplicate_groups: dict[str, list[str]] = {}
        for entry_id, digest in content_hashes.items():
            duplicate_groups.setdefault(digest, []).append(entry_id)
        duplicates = [
            {"sha256": digest, "entries": sorted(entry_ids, key=str.casefold)}
            for digest, entry_ids in duplicate_groups.items() if len(entry_ids) > 1
        ]
        files = [entry for entry in index.entries if entry.kind != "directory"]
        logical = sum(entry.size for entry in files)
        stored = sum(entry.stored_size for entry in files)
        report: dict[str, Any] = {
            "schema_version": 1,
            "operation": "rpf_archive_integrity_verification",
            "status": "verified" if not issues else "structural_issues",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": {
                "path": str(index.source), "edition": index.edition,
                "size": index.archive_size, "sha256": source_hash,
            },
            "summary": {
                "archives": len(index.archives),
                "directories": sum(
                    entry.kind == "directory" for entry in index.entries
                ),
                "payloads": len(files),
                "payloads_exactly_extracted": len(content_hashes),
                "logical_bytes": logical,
                "stored_bytes": stored,
                "duplicate_payload_groups": len(duplicates),
                "structural_issues": len(issues),
            },
            "index_warnings": list(index.warnings),
            "structural_issues": issues,
            "duplicate_payloads": duplicates,
            "payloads": [
                {
                    "id": entry.id, "kind": entry.kind,
                    "logical_size": entry.size, "stored_size": entry.stored_size,
                    "sha256": content_hashes[entry.id],
                }
                for entry in files
            ],
            "safety": {
                "archive_unchanged": True,
                "exact_payload_extraction": True,
                "writes_to_source": False,
            },
        }
        _write_json_atomic(output, report)
        return output, report

    def defragment_verified_copy(
        self, index: RpfIndex, destination: str | Path, report_path: str | Path,
    ) -> tuple[Path, Path, dict[str, Any]]:
        """Recursively compact a new external copy and prove leaf fidelity."""
        self._require_tool()
        source = index.source.resolve()
        if not source.is_file() or source.suffix.casefold() != ".rpf":
            raise ValueError("RPF defragmentation requires a loose source archive")
        if source.stat().st_size != index.archive_size:
            raise ValueError("RPF source changed after indexing; index it again")
        if index.archive_size > _MAX_DEFRAGMENT_ARCHIVE_BYTES:
            raise ValueError(
                f"RPF defragmentation is limited to "
                f"{_MAX_DEFRAGMENT_ARCHIVE_BYTES:,} bytes"
            )
        authored_output = Path(destination).expanduser()
        authored_report = Path(report_path).expanduser()
        if authored_output.is_symlink() or authored_report.is_symlink():
            raise ValueError("RPF defragmentation outputs cannot be symbolic links")
        output = authored_output.resolve()
        report_output = authored_report.resolve()
        if output.suffix.casefold() != ".rpf":
            raise ValueError("Defragmented archive output must use the .rpf extension")
        if report_output.suffix.casefold() != ".json":
            raise ValueError("Defragmentation report must use the .json extension")
        if output == source or report_output in {source, output}:
            raise ValueError("Defragmentation output and report must use new paths")
        if output.exists() or output.is_symlink():
            raise ValueError(f"Defragmented archive output already exists: {output}")
        if report_output.exists() or report_output.is_symlink():
            raise ValueError(f"Defragmentation report already exists: {report_output}")
        if output.is_relative_to(self.gta_path) or report_output.is_relative_to(
            self.gta_path
        ):
            raise ValueError(
                "Verified defragmentation writes only outside the GTA V installation; "
                "install or replace the reviewed copy through a separate guarded workflow"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        report_output.parent.mkdir(parents=True, exist_ok=True)
        required_space = index.archive_size + _COPY_MARGIN_BYTES
        if shutil.disk_usage(output.parent).free < required_space:
            raise ValueError("Not enough free space for a verified RPF defragmented copy")

        current_index = self.index(source)
        if (
            current_index.source != source
            or current_index.edition.casefold() != index.edition.casefold()
            or current_index.archive_size != index.archive_size
            or current_index.archives != index.archives
            or current_index.entries != index.entries
        ):
            raise ValueError(
                "RPF source index changed before defragmentation; index it again"
            )
        index = current_index
        source_sha256 = _sha256_file(source)
        source_leaves = tuple(
            entry for entry in index.entries
            if entry.kind not in {"directory", "archive"}
        )
        source_fingerprints = self.entry_content_fingerprints(index, source_leaves)
        temporary = Path(tempfile.mkdtemp(
            prefix=f".{output.stem}.allin1-defrag-", dir=output.parent,
        )).resolve()
        staged_output = temporary / output.name
        helper_report_path = temporary / "helper-report.json"
        published = False
        try:
            completed = run_hidden(
                [
                    self.patcher, "defragment-copy", self.gta_path, source,
                    staged_output, helper_report_path,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not staged_output.is_file() or not (
                helper_report_path.is_file()
            ):
                detail = (
                    completed.stderr or completed.stdout or "unknown helper error"
                ).strip()
                raise ValueError(f"RPF defragmentation failed: {detail}")
            helper = _read_json_object(
                helper_report_path, "RPF defragmentation helper report",
            )
            staged_sha256 = _sha256_file(staged_output)
            if (
                helper.get("schema_version") != 1
                or helper.get("operation") != "rpf_defragment_copy"
                or Path(str(helper.get("source", ""))).resolve() != source
                or Path(str(helper.get("output", ""))).resolve() != staged_output
                or helper.get("source_sha256") != source_sha256
                or helper.get("output_sha256") != staged_sha256
                or helper.get("source_size") != index.archive_size
                or helper.get("output_size") != staged_output.stat().st_size
                or helper.get("predicted_output_size") != staged_output.stat().st_size
                or helper.get("source_unchanged") is not True
                or helper.get("recursive") is not True
            ):
                raise ValueError("RPF defragmentation helper report failed binding checks")
            if staged_output.stat().st_size > index.archive_size:
                raise ValueError("Defragmented RPF is larger than its source")

            compacted = self.index(staged_output)
            before_entries = {
                (entry.archive_path.casefold(), entry.path.casefold()): entry
                for entry in index.entries
            }
            after_entries = {
                (entry.archive_path.casefold(), entry.path.casefold()): entry
                for entry in compacted.entries
            }
            if before_entries.keys() != after_entries.keys():
                raise ValueError("Defragmented RPF changed the recursive entry tree")
            preserved_fields = (
                "archive_path", "path", "name", "kind", "name_hash",
                "short_name_hash", "encrypted", "compressed", "resource_version",
                "system_size", "graphics_size", "system_flags", "graphics_flags",
                "child_count",
            )
            for identity, before in before_entries.items():
                after = after_entries[identity]
                changed = [
                    field_name for field_name in preserved_fields
                    if getattr(before, field_name) != getattr(after, field_name)
                ]
                if before.kind != "archive" and before.size != after.size:
                    changed.append("size")
                if changed:
                    raise ValueError(
                        "Defragmented RPF changed entry metadata "
                        f"{before.virtual_name}: {', '.join(changed)}"
                    )

            before_archives = {
                item.path.casefold(): item for item in index.archives
            }
            after_archives = {
                item.path.casefold(): item for item in compacted.archives
            }
            if before_archives.keys() != after_archives.keys():
                raise ValueError("Defragmented RPF changed the nested archive tree")
            for identity, before in before_archives.items():
                after = after_archives[identity]
                if (
                    before.path != after.path or before.version != after.version
                    or before.encryption != after.encryption
                    or before.entry_count != after.entry_count
                    or (identity and before.name != after.name)
                ):
                    raise ValueError(
                        f"Defragmented RPF changed archive metadata: {before.path or 'root'}"
                    )
                if after.size > before.size:
                    raise ValueError(
                        f"Defragmented nested archive grew: {before.path or 'root'}"
                    )

            compacted_leaves = tuple(
                entry for entry in compacted.entries
                if entry.kind not in {"directory", "archive"}
            )
            compacted_fingerprints = self.entry_content_fingerprints(
                compacted, compacted_leaves,
            )
            if source_fingerprints.keys() != compacted_fingerprints.keys():
                raise ValueError("Defragmented RPF omitted one or more leaf payloads")
            for entry_id, before in source_fingerprints.items():
                after = compacted_fingerprints[entry_id]
                if (
                    before["raw_sha256"] != after["raw_sha256"]
                    or before["canonical_sha256"] != after["canonical_sha256"]
                    or before["logical_size"] != after["logical_size"]
                ):
                    raise ValueError(
                        f"Defragmented RPF changed leaf payload bytes: {entry_id}"
                    )
            if _sha256_file(source) != source_sha256:
                raise RuntimeError("RPF source changed during verified defragmentation")
            if _sha256_file(staged_output) != staged_sha256:
                raise RuntimeError("Defragmented RPF changed during verification")

            if output.exists() or report_output.exists():
                raise FileExistsError(
                    "Defragmentation destination appeared during verification"
                )
            staged_output.rename(output)
            published = True
            bytes_saved = index.archive_size - output.stat().st_size
            report: dict[str, Any] = {
                "schema_version": 1,
                "operation": "rpf_verified_defragment_copy",
                "status": "verified",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "path": str(source), "edition": index.edition,
                    "size": index.archive_size, "sha256": source_sha256,
                },
                "output": {
                    "path": str(output), "edition": compacted.edition,
                    "size": output.stat().st_size, "sha256": staged_sha256,
                },
                "summary": {
                    "archives": len(index.archives),
                    "directories": sum(
                        entry.kind == "directory" for entry in index.entries
                    ),
                    "entries": len(index.entries),
                    "leaf_payloads_verified": len(source_fingerprints),
                    "bytes_saved": bytes_saved,
                    "space_reduction_percent": round(
                        (bytes_saved / index.archive_size * 100.0)
                        if index.archive_size else 0.0, 4,
                    ),
                },
                "helper": helper,
                "verification": {
                    "source_unchanged": True,
                    "recursive_tree_exact": True,
                    "archive_metadata_preserved": True,
                    "entry_metadata_preserved": True,
                    "leaf_payloads_raw_exact": True,
                    "leaf_payloads_canonical_exact": True,
                    "output_rescanned": True,
                    "writes_to_source": False,
                    "writes_inside_gta_installation": False,
                },
            }
            try:
                _write_json_atomic(report_output, report)
            except Exception:
                if output.is_file():
                    output.unlink()
                published = False
                raise
            return output, report_output, report
        except Exception:
            if published and output.is_file() and not report_output.is_file():
                output.unlink()
            raise
        finally:
            if temporary.is_dir() and temporary.parent == output.parent:
                shutil.rmtree(temporary)

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
            "| Added | Removed | Modified | Unchanged | Payload contents compared |",
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
        """Create one guarded plan for file and directory tree changes."""
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
        indexed = {
            (entry.archive_path.casefold(), entry.path.casefold()): entry
            for entry in index.entries
        }
        for number, authored in enumerate(requested, start=1):
            if not isinstance(authored, dict):
                raise ValueError(f"RPF multi-change item {number} is not an object")
            authored_action = str(authored.get("action", "")).casefold()
            if authored_action not in _RPF_MULTI_ACTIONS | {"upsert"}:
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
            if existing is not None:
                entry_path = existing.path
            action = (
                ("replace" if existing is not None else "add")
                if authored_action == "upsert" else authored_action
            )
            if action in {"add", "mkdir"}:
                if existing is not None:
                    raise ValueError(
                        f"RPF {action} target already exists: {archive_path}::{entry_path}"
                    )
            elif existing is None:
                raise ValueError(
                    f"RPF {action} target does not exist: {archive_path}::{entry_path}"
                )
            elif action in {"replace", "delete"} and existing.kind == "directory":
                raise ValueError(
                    f"RPF {action} cannot target {existing.kind}: {existing.virtual_name}"
                )
            elif action == "rmdir" and existing.kind != "directory":
                raise ValueError(f"RPF rmdir requires a directory: {existing.virtual_name}")

            new_entry: str | None = None
            if action == "rename":
                if existing is None or existing.kind == "archive":
                    raise ValueError("RPF rename does not support archive entries")
                new_entry = _safe_virtual_path(str(authored.get("new_entry", "")))
                if new_entry.casefold() == entry_path.casefold():
                    raise ValueError("RPF rename destination must differ from its source")
                old_parent = str(PurePosixPath(entry_path).parent)
                new_parent = str(PurePosixPath(new_entry).parent)
                if old_parent == ".":
                    old_parent = ""
                if new_parent == ".":
                    new_parent = ""
                if old_parent.casefold() != new_parent.casefold():
                    raise ValueError("RPF rename is limited to the same parent directory")
                destination_existing = indexed.get(
                    (archive_path.casefold(), new_entry.casefold())
                )
                if destination_existing is not None:
                    raise ValueError(
                        f"RPF rename destination already exists: "
                        f"{archive_path}::{new_entry}"
                    )
            elif authored.get("new_entry") not in (None, ""):
                raise ValueError(f"RPF {action} item {number} cannot include new_entry")

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
                raise ValueError(f"RPF {action} item {number} cannot include a payload")
            if existing is not None and existing.kind != "directory":
                existing_entries.append(existing)
            prepared.append({
                "action": action, "archive_path": archive_path,
                "entry": entry_path, "existing": existing,
                "payload": payload_meta,
                **({"new_entry": new_entry} if new_entry is not None else {}),
            })

        source_actions = {
            (item["archive_path"].casefold(), item["entry"].casefold()): item
            for item in prepared
        }
        available_directories = {
            (entry.archive_path.casefold(), entry.path.casefold())
            for entry in index.entries if entry.kind == "directory"
        }
        for item in prepared:
            existing = item["existing"]
            identity = (item["archive_path"].casefold(), item["entry"].casefold())
            if item["action"] == "rmdir" or (
                item["action"] == "rename" and existing is not None
                and existing.kind == "directory"
            ):
                available_directories.discard(identity)
            if item["action"] == "mkdir":
                available_directories.add(identity)
            elif item["action"] == "rename" and existing is not None and (
                existing.kind == "directory"
            ):
                available_directories.add(
                    (item["archive_path"].casefold(), item["new_entry"].casefold())
                )

        for item in prepared:
            if item["action"] not in {"add", "mkdir"}:
                continue
            parent = str(PurePosixPath(item["entry"]).parent)
            if parent in {"", "."}:
                continue
            parent_id = (item["archive_path"].casefold(), parent.casefold())
            if parent_id not in available_directories:
                raise ValueError(f"RPF target directory does not exist: {parent}")

        # Removing a directory is explicit and non-recursive: every indexed child
        # must be independently reviewed for deletion before the parent can vanish.
        for item in prepared:
            if item["action"] != "rmdir":
                continue
            prefix = item["entry"].casefold() + "/"
            for child in index.entries:
                if child.archive_path.casefold() != item["archive_path"].casefold() or not (
                    child.path.casefold().startswith(prefix)
                ):
                    continue
                child_change = source_actions.get(
                    (child.archive_path.casefold(), child.path.casefold())
                )
                required = "rmdir" if child.kind == "directory" else "delete"
                if child_change is None or child_change["action"] != required:
                    raise ValueError(
                        f"RPF directory is not empty after reviewed changes: "
                        f"{item['archive_path']}::{item['entry']}"
                    )

        # A directory rename updates every descendant path. Mixing that implicit
        # path rewrite with other edits in the same subtree would be order-dependent.
        for item in prepared:
            existing = item["existing"]
            if item["action"] != "rename" or existing is None or (
                existing.kind != "directory"
            ):
                continue
            old_prefix = item["entry"].casefold() + "/"
            new_prefix = item["new_entry"].casefold() + "/"
            if any(
                other is not item
                and other["archive_path"].casefold() == item["archive_path"].casefold()
                and (
                    other["entry"].casefold().startswith(old_prefix)
                    or other["entry"].casefold().startswith(new_prefix)
                )
                for other in prepared
            ):
                raise ValueError(
                    "RPF directory rename cannot be mixed with edits inside its subtree"
                )

        final_targets: set[tuple[str, str]] = set()
        for item in prepared:
            if item["action"] in {"delete", "rmdir"}:
                continue
            result_entry = item.get("new_entry", item["entry"])
            target = (item["archive_path"].casefold(), result_entry.casefold())
            if target in final_targets:
                raise ValueError("RPF multi-change plan has a duplicate result target")
            final_targets.add(target)

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
        for item in prepared:
            existing = item["existing"]
            if (
                item["action"] == "replace" and existing is not None
                and item["payload"]["sha256"] == original_hashes[existing.id]
            ):
                raise ValueError(
                    f"RPF replacement payload is unchanged: {existing.virtual_name}"
                )
        changes: list[dict[str, Any]] = []
        for item in prepared:
            existing = item.pop("existing")
            original = (
                {
                    "exists": False, "kind": None, "size": 0,
                    "sha256": None, "child_count": None,
                }
                if existing is None else {
                    "exists": True, "kind": existing.kind, "size": existing.size,
                    "sha256": (
                        None if existing.kind == "directory"
                        else original_hashes[existing.id]
                    ),
                    "child_count": (
                        int(existing.child_count or 0)
                        if existing.kind == "directory" else None
                    ),
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
        directory_records = manifest.get("directories", [])
        if not isinstance(source, dict) or not isinstance(selection, dict):
            raise ValueError("RPF subtree export is missing source or selection metadata")
        if not isinstance(records, list) or not isinstance(directory_records, list):
            raise ValueError("RPF subtree export has invalid file or directory records")
        if not records and not directory_records:
            raise ValueError("RPF subtree export contains no entry records")
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
        if manifest.get("directory_count", len(directory_records)) != len(
            directory_records
        ):
            raise ValueError("RPF subtree export directory count does not match its manifest")

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

        prefix = f"{directory_path}/" if directory_path else ""
        base_directories: dict[str, tuple[str, str]] = {}
        for entry in index.entries:
            if entry.archive_path.casefold() != archive_path.casefold() or (
                entry.kind != "directory"
            ):
                continue
            if entry.path.casefold() == directory_path.casefold():
                continue
            if prefix and not entry.path.casefold().startswith(prefix.casefold()):
                continue
            relative = entry.path[len(prefix):] if prefix else entry.path
            relative = _safe_virtual_path(relative)
            base_directories[relative.casefold()] = (relative, entry.path)
        if directory_records:
            manifested_directories: dict[str, tuple[str, str]] = {}
            for number, item in enumerate(directory_records, start=1):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"RPF subtree export directory record {number} is invalid"
                    )
                relative = _safe_virtual_path(str(item.get("relative_path", "")))
                entry_path = _safe_virtual_path(str(item.get("entry_path", "")))
                item_archive = _safe_virtual_path(
                    str(item.get("archive_path", "")), allow_empty=True,
                )
                expected_entry = f"{directory_path}/{relative}" if directory_path else relative
                if item_archive.casefold() != archive_path.casefold() or (
                    entry_path.casefold() != expected_entry.casefold()
                ):
                    raise ValueError(
                        f"RPF subtree export directory escapes its selection: {relative}"
                    )
                if relative.casefold() in manifested_directories:
                    raise ValueError(
                        f"RPF subtree export contains a duplicate directory: {relative}"
                    )
                manifested_directories[relative.casefold()] = (relative, entry_path)
            if set(manifested_directories) != set(base_directories):
                raise ValueError(
                    "RPF subtree export directory records do not match its source index"
                )

        workspace_files: dict[str, tuple[str, Path]] = {}
        workspace_directories: dict[str, str] = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"RPF subtree workspace contains a symbolic link: {path}")
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                relative = _safe_virtual_path(relative)
                if relative.casefold() in workspace_directories:
                    raise ValueError(
                        f"RPF subtree workspace contains a directory collision: {relative}"
                    )
                workspace_directories[relative.casefold()] = relative
                continue
            if not path.is_file():
                continue
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
        collision = set(workspace_files).intersection(workspace_directories)
        if collision:
            raise ValueError(
                "RPF subtree workspace contains a file/directory path collision: "
                f"{next(iter(collision))}"
            )

        changes: list[dict[str, Any]] = []
        for key, relative in sorted(
            workspace_directories.items(), key=lambda item: item[1].count("/"),
        ):
            if key in base_directories:
                continue
            entry_path = f"{directory_path}/{relative}" if directory_path else relative
            changes.append({
                "action": "mkdir", "archive_path": archive_path,
                "entry": entry_path,
            })
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
        for key, (_relative, entry_path) in sorted(
            base_directories.items(),
            key=lambda item: item[1][1].count("/"), reverse=True,
        ):
            if key in workspace_directories:
                continue
            changes.append({
                "action": "rmdir", "archive_path": archive_path,
                "entry": entry_path,
            })
        if not changes:
            raise ValueError("RPF subtree workspace has no changes to plan")
        plan = self.multi_change_plan(index, changes)
        plan["workspace_sync"] = {
            "manifest": str(manifest_path),
            "archive_path": archive_path, "directory_path": directory_path,
            "changed_entries": len(changes),
            "changed_files": sum(
                item["action"] in {"add", "replace", "delete"} for item in changes
            ),
            "changed_directories": sum(
                item["action"] in {"mkdir", "rmdir"} for item in changes
            ),
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
        if (
            action == "replace" and payload_meta is not None
            and payload_meta["sha256"] == original.get("sha256")
        ):
            raise ValueError(f"RPF replacement payload is unchanged: {existing.virtual_name}")
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
        expected_sha256: str | None = None, before_commit: Callable[[], None] | None = None,
    ) -> Path:
        """Apply a guarded plan through backup, staging, verification, and commit."""
        self._require_tool()
        plan_source = Path(plan_path).expanduser().resolve()
        plan = _read_json_object(plan_source, "RPF entry-change plan", expected_sha256=expected_sha256)
        if plan.get("operation") == "rpf_multi_entry_change":
            derived = plan.get("derived_delta")
            if derived is not None:
                if not isinstance(derived, dict) or derived.get("schema_version") != 1:
                    raise ValueError("Unsupported derived RPF delta metadata")
                authored_root = derived.get("payload_directory")
                if authored_root is not None:
                    relative_root = Path(str(authored_root))
                    if relative_root.is_absolute():
                        raise ValueError(
                            "Derived RPF delta payload directory must be relative to its plan"
                        )
                    if len(relative_root.parts) != 1 or relative_root.name in {
                        "", ".", "..",
                    }:
                        raise ValueError(
                            "Derived RPF delta payload directory must be one sibling folder"
                        )
                    payload_root = (plan_source.parent / relative_root).resolve()
                    if not payload_root.is_relative_to(plan_source.parent):
                        raise ValueError(
                            "Derived RPF delta payload directory escapes its plan folder"
                        )
                    for change in plan.get("changes", ()):
                        if not isinstance(change, dict) or change.get("payload") is None:
                            continue
                        payload = change["payload"]
                        if not isinstance(payload, dict):
                            raise ValueError(
                                "Derived RPF delta contains invalid payload metadata"
                            )
                        authored_payload = Path(str(payload.get("path", "")))
                        if authored_payload.is_absolute():
                            raise ValueError(
                                "Derived RPF delta payload paths must be relative to its plan"
                            )
                        resolved_payload = (plan_source.parent / authored_payload).resolve()
                        if not resolved_payload.is_relative_to(payload_root):
                            raise ValueError(
                                "Derived RPF delta payload escapes its declared sidecar"
                            )
                        payload["path"] = str(resolved_payload)
            archive, changes = self._validate_multi_plan(plan)
            self._require_game_closed()
            lock = self._acquire_archive_lock(archive, plan["plan_id"])
            try:
                return self._apply_multi_plan_locked(
                    plan, archive, changes, receipt_root, progress, before_commit,
                )
            finally:
                lock.unlink(missing_ok=True)
        if before_commit is not None:
            raise ValueError("Reviewed desktop execution requires a multi-entry plan")
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
        before_commit: Callable[[], None] | None = None,
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
            [state for change, _payload in changes
             for state in self._change_entry_states(change, applied=False)],
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
                **({"new_entry": change["new_entry"]} if change.get(
                    "new_entry"
                ) else {}),
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
                state for change in receipt_changes
                for state in self._change_entry_states(change, applied=True)
            ]
            self._emit(progress, "Verifying every staged entry", 65)
            self._verify_entry_states(stage, expected_applied, plan["edition"])
            staged_hash = _sha256_file(stage)
            receipt["applied_archive_sha256"] = staged_hash
            receipt["status"] = "verified_staging"
            _write_json_atomic(receipt_path, receipt)

            self._emit(progress, "Committing one verified outer archive", 80)
            self._require_game_closed()
            if before_commit is not None:
                before_commit()
            if _sha256_file(archive) != plan["archive_sha256"]:
                raise RuntimeError("RPF changed while the transaction was staging; refusing commit")
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
                [state for change in receipt["changes"]
                 for state in self._change_entry_states(change, applied=True)]
                if receipt["operation"] == "rpf_multi_entry_change"
                else self._applied_entry_state(receipt)
            )
        elif current_hash == receipt["backup"]["sha256"]:
            archive_state = "original"
            expected_entry = (
                [state for change in receipt["changes"]
                 for state in self._change_entry_states(change, applied=False)]
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
        expected_sha256: str | None = None, before_commit: Callable[[], None] | None = None,
    ) -> Path:
        """Restore an applied transaction if its archive is still receipt-owned."""
        self._require_tool()
        source = Path(receipt_path).expanduser().resolve()
        receipt = self._validate_receipt(
            _read_json_object(source, "RPF transaction receipt", expected_sha256=expected_sha256)
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
                source, receipt, archive, progress, before_commit,
            )
        finally:
            lock.unlink(missing_ok=True)

    def _rollback_transaction_locked(
        self, source: Path, receipt: dict[str, Any], archive: Path,
        progress: ProgressCallback | None,
        before_commit: Callable[[], None] | None = None,
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
                [state for change in receipt["changes"]
                 for state in self._change_entry_states(change, applied=True)],
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
        committed = False
        try:
            self._emit(progress, "Creating rollback recovery copy", 30)
            self._copy_verified(archive, recovery, expected_applied)
            self._copy_verified(backup, rollback_stage, receipt["backup"]["sha256"])
            self._emit(progress, "Restoring pre-transaction archive", 65)
            self._require_game_closed()
            if before_commit is not None:
                before_commit()
            if _sha256_file(archive) != expected_applied:
                raise RuntimeError("RPF changed while rollback was staging; refusing restore")
            rollback_stage.replace(archive)
            committed = True
            if _sha256_file(archive) != receipt["backup"]["sha256"]:
                raise RuntimeError("Restored archive does not match its rollback snapshot")
            if receipt["operation"] == "rpf_multi_entry_change":
                self._verify_entry_states(
                    archive,
                    [state for change in receipt["changes"]
                     for state in self._change_entry_states(change, applied=False)],
                    receipt["edition"],
                )
            else:
                self._verify_entry_state(
                    archive, receipt["archive_path"], receipt["entry"],
                    receipt["original"], receipt["edition"],
                )
        except Exception as exc:
            if committed and recovery.is_file():
                recovery.replace(archive)
            outcome = "applied archive was restored" if committed else "archive was not overwritten"
            raise RuntimeError(f"Rollback failed; {outcome}: {exc}") from exc
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
        return next((
            root for root in self.workspace_roots if resolved.is_relative_to(root)
        ), None)

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
            "new_entry": item.get("new_entry"),
            "original_exists": item["original"]["exists"],
            "original_kind": item["original"].get("kind"),
            "original_child_count": item["original"].get("child_count"),
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
        result_targets: set[tuple[str, str]] = set()
        for number, authored in enumerate(authored_changes, start=1):
            if not isinstance(authored, dict):
                raise ValueError(f"RPF multi-change item {number} is invalid")
            action = str(authored.get("action", ""))
            if action not in _RPF_MULTI_ACTIONS:
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
            new_entry: str | None = None
            if action == "rename":
                new_entry = _safe_virtual_path(str(authored.get("new_entry", "")))
                if new_entry.casefold() == entry_path.casefold():
                    raise ValueError("RPF rename destination must differ from its source")
                old_parent = str(PurePosixPath(entry_path).parent)
                new_parent = str(PurePosixPath(new_entry).parent)
                if old_parent == ".":
                    old_parent = ""
                if new_parent == ".":
                    new_parent = ""
                if old_parent.casefold() != new_parent.casefold():
                    raise ValueError("RPF rename is limited to the same parent directory")
            elif authored.get("new_entry") not in (None, ""):
                raise ValueError(f"RPF multi-change {action} item has new_entry")
            original = authored.get("original")
            if not isinstance(original, dict) or not isinstance(
                original.get("exists"), bool,
            ):
                raise ValueError(f"RPF multi-change item {number} has invalid original state")
            if not isinstance(original.get("size"), int) or original["size"] < 0:
                raise ValueError(f"RPF multi-change item {number} has invalid original size")
            original_kind = original.get("kind")
            if original["exists"]:
                if original_kind not in {"directory", "resource", "binary", "archive"}:
                    raise ValueError(
                        f"RPF multi-change item {number} has invalid original kind"
                    )
                if original_kind == "directory":
                    if original.get("sha256") is not None or not isinstance(
                        original.get("child_count"), int,
                    ) or original["child_count"] < 0:
                        raise ValueError(
                            f"RPF multi-change item {number} has invalid directory state"
                        )
                elif not _is_sha256(original.get("sha256")):
                    raise ValueError(
                        f"RPF multi-change item {number} has invalid original hash"
                    )
            elif original_kind is not None or original.get("sha256") is not None:
                raise ValueError(
                    f"RPF multi-change item {number} has invalid absent state"
                )
            if action in {"add", "mkdir"} and original["exists"]:
                raise ValueError(f"RPF multi-change {action} item claims its target exists")
            if action in {"replace", "delete", "rmdir", "rename"} and not original[
                "exists"
            ]:
                raise ValueError(
                    f"RPF multi-change {action} item claims its target is absent"
                )
            if action in {"replace", "delete"} and original_kind == "directory":
                raise ValueError(f"RPF multi-change {action} cannot target a directory")
            if action == "rmdir" and original_kind != "directory":
                raise ValueError("RPF multi-change rmdir must target a directory")
            if action == "rename" and original_kind == "archive":
                raise ValueError("RPF multi-change rename cannot target an archive")
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
                raise ValueError(
                    f"RPF multi-change {action} item unexpectedly has a payload"
                )
            normalized_change = {
                **authored, "action": action, "archive_path": archive_path,
                "entry": entry_path,
                **({"new_entry": new_entry} if new_entry is not None else {}),
            }
            normalized.append((normalized_change, payload))
            if action not in {"delete", "rmdir"}:
                result = new_entry if new_entry is not None else entry_path
                result_id = (archive_path.casefold(), result.casefold())
                if result_id in result_targets:
                    raise ValueError("RPF multi-change plan has a duplicate result target")
                result_targets.add(result_id)

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
        expected_kind = expected.get("kind")
        if expected_kind is not None and entry.kind != expected_kind:
            raise RuntimeError(
                f"RPF entry kind changed for {entry_path}: expected {expected_kind}, "
                f"found {entry.kind}"
            )
        if expected_kind == "directory":
            expected_children = expected.get("child_count")
            if expected_children is not None and (
                not isinstance(expected_children, int)
                or int(entry.child_count or 0) != expected_children
            ):
                raise RuntimeError(
                    f"RPF directory child count changed for {entry_path}: expected "
                    f"{expected_children}, found {int(entry.child_count or 0)}"
                )
            return
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
            expected_kind = expected.get("kind")
            if expected_kind is not None and entry.kind != expected_kind:
                raise RuntimeError(
                    f"RPF batch entry kind changed for {entry.virtual_name}: expected "
                    f"{expected_kind}, found {entry.kind}"
                )
            if expected_kind == "directory":
                expected_children = expected.get("child_count")
                if expected_children is not None and (
                    not isinstance(expected_children, int)
                    or int(entry.child_count or 0) != expected_children
                ):
                    raise RuntimeError(
                        f"RPF batch directory child count changed for {entry.virtual_name}: "
                        f"expected {expected_children}, found {int(entry.child_count or 0)}"
                    )
                continue
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
                elif action == "rename":
                    relative = _safe_virtual_path(str(change.get("new_entry", "")))
                elif action not in {"delete", "mkdir", "rmdir"}:
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
        action = container.get("action")
        if action in {"delete", "rmdir"}:
            return {
                "exists": False, "kind": None, "size": 0,
                "sha256": None, "child_count": None,
            }
        if action == "mkdir":
            return {
                "exists": True, "kind": "directory", "size": 0,
                "sha256": None, "child_count": None,
            }
        if action == "rename":
            raise ValueError("Rename produces two guarded RPF entry states")
        payload = container.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Applied RPF entry state is missing payload metadata")
        return {
            "exists": True, "kind": None, "size": int(payload["size"]),
            "sha256": payload["sha256"], "child_count": None,
        }

    @classmethod
    def _change_entry_states(
        cls, change: dict[str, Any], *, applied: bool,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        archive_path = str(change["archive_path"])
        entry_path = str(change["entry"])
        absent = {
            "exists": False, "kind": None, "size": 0,
            "sha256": None, "child_count": None,
        }
        if change.get("action") != "rename":
            state = cls._applied_entry_state(change) if applied else change["original"]
            return [(archive_path, entry_path, state)]
        new_entry = _safe_virtual_path(str(change.get("new_entry", "")))
        if applied:
            return [
                (archive_path, entry_path, absent),
                (archive_path, new_entry, change["original"]),
            ]
        return [
            (archive_path, entry_path, change["original"]),
            (archive_path, new_entry, absent),
        ]

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
        schema = receipt.get("schema_version")
        if schema not in {1, RPF_MULTI_TRANSACTION_RECEIPT_SCHEMA}:
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
        results: set[tuple[str, str]] = set()
        for number, change in enumerate(changes, start=1):
            allowed_actions = _RPF_ACTIONS if schema == 1 else _RPF_MULTI_ACTIONS
            if not isinstance(change, dict) or change.get("action") not in allowed_actions:
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
            new_entry: str | None = None
            if change["action"] == "rename":
                new_entry = _safe_virtual_path(str(change.get("new_entry", "")))
                old_parent = str(PurePosixPath(entry_path).parent)
                new_parent = str(PurePosixPath(new_entry).parent)
                if old_parent == ".":
                    old_parent = ""
                if new_parent == ".":
                    new_parent = ""
                if new_entry.casefold() == entry_path.casefold() or (
                    old_parent.casefold() != new_parent.casefold()
                ):
                    raise ValueError("RPF multi-change receipt has an invalid rename")
            elif change.get("new_entry") not in (None, ""):
                raise ValueError("RPF multi-change receipt unexpectedly contains new_entry")
            original = change.get("original")
            if not isinstance(original, dict) or not isinstance(
                original.get("exists"), bool,
            ):
                raise ValueError("RPF multi-change receipt has invalid original state")
            if not isinstance(original.get("size"), int) or original["size"] < 0:
                raise ValueError("RPF multi-change receipt has invalid original size")
            original_kind = original.get("kind")
            if schema == 1:
                if original["exists"] and not _is_sha256(original.get("sha256")):
                    raise ValueError("RPF multi-change receipt has invalid original hash")
            elif original["exists"]:
                if original_kind not in {"directory", "resource", "binary", "archive"}:
                    raise ValueError("RPF multi-change receipt has invalid original kind")
                if original_kind == "directory":
                    if original.get("sha256") is not None or not isinstance(
                        original.get("child_count"), int,
                    ) or original["child_count"] < 0:
                        raise ValueError(
                            "RPF multi-change receipt has invalid directory state"
                        )
                elif not _is_sha256(original.get("sha256")):
                    raise ValueError("RPF multi-change receipt has invalid original hash")
            elif original_kind is not None or original.get("sha256") is not None:
                raise ValueError("RPF multi-change receipt has invalid absent state")
            if change["action"] in {"add", "mkdir"} and original["exists"]:
                raise ValueError(
                    f"RPF multi-change {change['action']} receipt claims its target exists"
                )
            if change["action"] in {"replace", "delete", "rmdir", "rename"} and not (
                original["exists"]
            ):
                raise ValueError(
                    "RPF multi-change receipt claims an existing target is absent"
                )
            if change["action"] == "rmdir" and original_kind != "directory":
                raise ValueError("RPF multi-change rmdir receipt is not a directory")
            if change["action"] == "rename" and original_kind == "archive":
                raise ValueError("RPF multi-change receipt renames an archive")
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
                raise ValueError("RPF multi-change non-file receipt has a payload")
            if change["action"] not in {"delete", "rmdir"}:
                result_entry = new_entry if new_entry is not None else entry_path
                result = (archive_path.casefold(), result_entry.casefold())
                if result in results:
                    raise ValueError(
                        "RPF multi-change receipt contains a duplicate result target"
                    )
                results.add(result)
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

    def recover_transaction(
        self, receipt_path: str | Path, *, expected_sha256: str | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Reconcile an interrupted receipt with the archive without committing a write."""
        source = Path(receipt_path).expanduser().resolve()
        receipt = self._validate_receipt(
            _read_json_object(source, "RPF transaction receipt", expected_sha256=expected_sha256)
        )
        verification = self.verify_transaction(source)
        state = verification["archive_state"]
        if not verification["healthy"]:
            raise RuntimeError(
                "Interrupted transaction cannot be reconciled safely: "
                + json.dumps(verification, sort_keys=True)
            )
        if before_commit is not None:
            before_commit()
        if expected_sha256 is not None and _sha256_file(source) != expected_sha256:
            raise ValueError("Receipt changed before recovery; review again")
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
        if type(pid) is not int or pid <= 0:
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

            # Exercise directory creation, file add, rename, explicit file delete,
            # and empty-directory removal as chained atomic transactions. Rolling
            # back in reverse order must recover the source-identical archive.
            add_payload = canary_root / "allin1-sdk-canary.bin"
            add_payload.write_bytes(b"ALLIN1 SDK RPF canary\n")
            add_name = "allin1_sdk_canary.bin"
            directory_name = "allin1_sdk_canary"
            restored_index = canary_service.index(canary)
            root_paths = {
                item.path.casefold() for item in restored_index.entries
                if not item.archive_path
            }
            if directory_name.casefold() in root_paths:
                directory_name = f"allin1_sdk_canary_{timestamp}"
            add_entry = f"{directory_name}/{add_name}"
            renamed_entry = f"{directory_name}/verified_{add_name}"
            add_plan = canary_service.multi_change_plan(restored_index, [
                {"action": "mkdir", "entry": directory_name},
                {"action": "add", "entry": add_entry, "payload": add_payload},
            ])
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
            rename_plan = canary_service.multi_change_plan(with_entry, [{
                "action": "rename", "entry": add_entry,
                "new_entry": renamed_entry,
            }])
            rename_plan_path = canary_root / "canary-rename-plan.json"
            _write_json_atomic(rename_plan_path, rename_plan)
            rename_receipt = canary_service.apply_change_plan(
                rename_plan_path, receipt_root=canary_root / "transactions",
                progress=progress,
            )
            rename_verification = canary_service.verify_transaction(rename_receipt)
            if not rename_verification["healthy"]:
                raise RuntimeError(
                    f"Canary rename verification failed: {rename_verification}"
                )

            renamed_index = canary_service.index(canary)
            delete_plan = canary_service.multi_change_plan(renamed_index, [
                {"action": "delete", "entry": renamed_entry},
                {"action": "rmdir", "entry": directory_name},
            ])
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
            canary_service.rollback_transaction(rename_receipt, progress=progress)
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
                    "replace": str(receipt), "tree_create": str(add_receipt),
                    "tree_rename": str(rename_receipt),
                    "tree_remove": str(delete_receipt),
                },
                "replace_apply_verification": applied,
                "replace_rollback_verification": replace_restored,
                "add_verification": add_verification,
                "rename_verification": rename_verification,
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
