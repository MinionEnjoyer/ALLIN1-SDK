"""Structured, read-only RPF indexing and extraction services."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from allin1_sdk.processes import run_hidden


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
    """Invoke the pinned helper through explicit read-only operations."""

    def __init__(self, project_root: str | Path, gta_path: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.gta_path = Path(gta_path).resolve()
        self.patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"

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

    def replacement_plan(
        self, index: RpfIndex, entry: RpfEntryRecord, payload: str | Path,
    ) -> dict[str, Any]:
        source = Path(payload).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Replacement payload not found: {source}")
        if entry.kind == "directory":
            raise ValueError("A directory cannot be a replacement target")
        data = source.read_bytes()
        archive_parts = {part.casefold() for part in index.source.parts}
        warnings = []
        if "mods" not in archive_parts:
            warnings.append(
                "Target is not inside a mods directory; create and select a mods copy before applying."
            )
        if entry.archive_path:
            warnings.append(
                "Nested archive replacement requires a transactional parent-archive rewrite."
            )
        return {
            "schema_version": 1, "operation": "replace_rpf_entry",
            "status": "plan_only", "archive": str(index.source),
            "archive_path": entry.archive_path, "entry": entry.path,
            "current_size": entry.size, "payload": str(source),
            "payload_size": len(data),
            "payload_sha256": hashlib.sha256(data).hexdigest(),
            "edition": index.edition, "warnings": warnings,
            "safety": {
                "writes_performed": False, "backup_required": True,
                "post_write_hash_verification_required": True,
                "rollback_required": True,
            },
        }
