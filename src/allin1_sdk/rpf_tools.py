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
_GTA_PROCESS_NAMES = {"gta5.exe", "gta5_enhanced.exe"}
_COPY_MARGIN_BYTES = 64 * 1024 * 1024
_MAX_CANARY_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_NESTED_WRITE_DEPTH = 8
_RPF_ACTIONS = {"replace", "add", "delete"}
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
            expected_entry = self._applied_entry_state(receipt)
        elif current_hash == receipt["backup"]["sha256"]:
            archive_state = "original"
            expected_entry = receipt["original"]
        else:
            archive_state = "modified_externally"
            expected_entry = None
        entry_valid = False
        entry_error: str | None = None
        if expected_entry is not None:
            try:
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
                history.append({
                    "receipt": str(path.resolve()),
                    "transaction_id": receipt["transaction_id"],
                    "created_at": receipt.get("created_at", ""),
                    "status": receipt["status"], "action": receipt["action"],
                    "archive": receipt["archive"],
                    "entry": (
                        f"{receipt['archive_path']}::{receipt['entry']}"
                        if receipt["archive_path"] else receipt["entry"]
                    ),
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
