"""Validated Rockstar GXT2 text-table workspaces."""

from __future__ import annotations

import hashlib
import os
import json
import shutil
import struct
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GXT2_WORKSPACE_SCHEMA = 1
MAX_GXT2_ENTRIES = 1_000_000
MAX_GXT2_BYTES = 128 * 1024 * 1024
MAX_GXT2_TEXT_BYTES = 1024 * 1024
MAX_GXT2_HISTORY_RECORDS = 10_000
# Rockstar writes the uint32 value 0x47585432 in little-endian byte order.
_MAGIC = b"2TXG"
_LOCKS: dict[str, threading.RLock] = {}
_HELD = threading.local()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    _write_bytes_atomic(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _label_hash(value: int | str) -> int:
    if isinstance(value, str):
        authored = value.strip()
        try:
            result = int(authored, 0)
        except ValueError as exc:
            raise ValueError("GXT2 label hash must be decimal or 0x-prefixed") from exc
    else:
        result = int(value)
    if not 0 <= result <= 0xFFFFFFFF:
        raise ValueError("GXT2 label hash must fit an unsigned 32-bit value")
    return result


class Gxt2Workspace:
    """Parse, edit, undo, rebuild, and reparse one GXT2 dictionary."""

    @staticmethod
    @contextmanager
    def operation_lock(root: str | Path):
        from allin1_sdk.managed_package_conversion import _safe_publication_path
        authored = Path(root).expanduser()
        _safe_publication_path(authored)
        workspace = authored.resolve(strict=True)
        key = str(workspace)
        with _LOCKS.setdefault(key, threading.RLock()):
            held = getattr(_HELD, "paths", set())
            if key in held:
                yield
                return
            path = workspace / ".gxt2-operation.lock"
            _safe_publication_path(path)
            with path.open("a+b") as stream:
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                _HELD.paths = held | {key}
                try:
                    yield
                finally:
                    _HELD.paths = held
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def parse(data: bytes) -> tuple[dict[str, object], ...]:
        if len(data) < 16 or len(data) > MAX_GXT2_BYTES or data[:4] != _MAGIC:
            raise ValueError("Invalid or oversized GXT2 header")
        count = struct.unpack_from("<I", data, 4)[0]
        if count > MAX_GXT2_ENTRIES:
            raise ValueError("GXT2 entry count exceeds the guarded limit")
        table_end = 8 + count * 8
        text_start = table_end + 8
        if text_start > len(data) or data[table_end:table_end + 4] != _MAGIC:
            raise ValueError("GXT2 index or text marker is truncated")
        end_offset = struct.unpack_from("<I", data, table_end + 4)[0]
        if end_offset != len(data) or end_offset < text_start:
            raise ValueError("GXT2 declared end offset does not match the file")
        entries: list[dict[str, object]] = []
        seen: set[int] = set()
        for number in range(count):
            label_hash, offset = struct.unpack_from("<II", data, 8 + number * 8)
            if label_hash in seen:
                raise ValueError(f"GXT2 contains duplicate label hash 0x{label_hash:08X}")
            seen.add(label_hash)
            if not text_start <= offset < end_offset:
                raise ValueError(f"GXT2 text offset is invalid for 0x{label_hash:08X}")
            terminator = data.find(b"\0", offset, end_offset)
            if terminator < 0:
                raise ValueError(f"GXT2 text is not null terminated for 0x{label_hash:08X}")
            try:
                text = data[offset:terminator].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"GXT2 text is not UTF-8 for 0x{label_hash:08X}") from exc
            entries.append({
                "hash": label_hash,
                "hash_hex": f"0x{label_hash:08X}",
                "text": text,
            })
        return tuple(entries)

    @staticmethod
    def encode(entries: tuple[dict[str, object], ...]) -> bytes:
        normalized = Gxt2Workspace._validate_entries(entries)
        count = len(normalized)
        offset = 16 + count * 8
        encoded: list[bytes] = []
        table: list[tuple[int, int]] = []
        for item in normalized:
            payload = str(item["text"]).encode("utf-8") + b"\0"
            table.append((int(item["hash"]), offset))
            encoded.append(payload)
            offset += len(payload)
        if offset > MAX_GXT2_BYTES:
            raise ValueError("Rebuilt GXT2 exceeds the guarded size limit")
        output = bytearray(_MAGIC)
        output.extend(struct.pack("<I", count))
        for label_hash, text_offset in table:
            output.extend(struct.pack("<II", label_hash, text_offset))
        output.extend(_MAGIC)
        output.extend(struct.pack("<I", offset))
        for payload in encoded:
            output.extend(payload)
        result = bytes(output)
        if Gxt2Workspace.parse(result) != normalized:
            raise ValueError("Rebuilt GXT2 failed semantic reparse validation")
        return result

    @staticmethod
    def _validate_entries(entries: object) -> tuple[dict[str, object], ...]:
        if not isinstance(entries, (list, tuple)) or len(entries) > MAX_GXT2_ENTRIES:
            raise ValueError("GXT2 entries must be a bounded array")
        normalized: list[dict[str, object]] = []
        seen: set[int] = set()
        total = 0
        for number, item in enumerate(entries, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"GXT2 entry {number} must be an object")
            label_hash = _label_hash(item.get("hash", -1))
            if label_hash in seen:
                raise ValueError(f"Duplicate GXT2 label hash 0x{label_hash:08X}")
            seen.add(label_hash)
            text = item.get("text")
            if not isinstance(text, str) or "\0" in text:
                raise ValueError(f"GXT2 text for 0x{label_hash:08X} is invalid")
            size = len(text.encode("utf-8"))
            if size > MAX_GXT2_TEXT_BYTES:
                raise ValueError(f"GXT2 text for 0x{label_hash:08X} is too large")
            total += size + 1
            if total > MAX_GXT2_BYTES:
                raise ValueError("GXT2 text payload exceeds the guarded size limit")
            normalized.append({
                "hash": label_hash,
                "hash_hex": f"0x{label_hash:08X}",
                "text": text,
            })
        normalized.sort(key=lambda item: int(item["hash"]))
        return tuple(normalized)

    def export_bytes(
        self, name: str, data: bytes, destination: str | Path, *,
        source_binding: dict[str, object] | None = None,
    ) -> Path:
        entries = self.parse(data)
        root = Path(destination).expanduser().resolve()
        if root.exists() or root.is_symlink():
            raise ValueError(f"GXT2 workspace destination already exists: {root}")
        safe_name = Path(name).name
        if Path(safe_name).suffix.casefold() != ".gxt2":
            raise ValueError("GXT2 workspace source must use a .gxt2 extension")
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{root.name}.gxt2-workspace-", dir=root.parent,
        )).resolve()
        try:
            original = stage / "original.gxt2"
            original.write_bytes(data)
            entries_path = stage / "entries.json"
            _write_json_atomic(entries_path, list(entries))
            (stage / "history").mkdir()
            _write_json_atomic(stage / "gxt2-workspace.json", {
                "schema_version": GXT2_WORKSPACE_SCHEMA,
                "operation": "gxt2_text_workspace",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "name": safe_name,
                "entry_count": len(entries),
                "original_sha256": hashlib.sha256(data).hexdigest(),
                "initial_entries_sha256": _sha256_file(entries_path),
                "source_binding": source_binding or {},
            })
            stage.rename(root)
            return root
        except Exception:
            if stage.is_dir() and stage.parent == root.parent:
                shutil.rmtree(stage)
            raise

    @classmethod
    def validate(cls, root: str | Path) -> dict[str, Any]:
        from allin1_sdk.managed_package_conversion import _safe_publication_path
        _safe_publication_path(Path(root).expanduser())
        workspace = Path(root).expanduser().resolve()
        manifest_path = workspace / "gxt2-workspace.json"
        original = workspace / "original.gxt2"
        entries_path = workspace / "entries.json"
        history = workspace / "history"
        for path in (workspace, manifest_path, original, entries_path, history):
            _safe_publication_path(path)
            if path.is_symlink():
                raise ValueError(f"GXT2 workspace may not contain links: {path}")
        if not workspace.is_dir() or not original.is_file() or not history.is_dir():
            raise FileNotFoundError(f"GXT2 workspace not found: {workspace}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            authored = json.loads(entries_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid GXT2 workspace JSON: {exc}") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != GXT2_WORKSPACE_SCHEMA
            or manifest.get("operation") != "gxt2_text_workspace"
        ):
            raise ValueError("Unsupported GXT2 workspace manifest")
        if _sha256_file(original) != str(manifest.get("original_sha256", "")).casefold():
            raise ValueError("GXT2 immutable source snapshot was modified")
        original_entries = cls.parse(original.read_bytes())
        if len(original_entries) != int(manifest.get("entry_count", -1)):
            raise ValueError("GXT2 source snapshot no longer matches its manifest")
        entries = cls._validate_entries(authored)
        current_hash = _sha256_file(entries_path)
        previous_hash = manifest.get("initial_entries_sha256")
        if not _is_sha256(previous_hash):
            raise ValueError("GXT2 workspace is missing its initial entries hash")
        records = sorted(
            path for path in history.glob("*.json")
            if not path.name.endswith(".before.json")
        )
        if len(records) > MAX_GXT2_HISTORY_RECORDS:
            raise ValueError("GXT2 workspace history exceeds the guarded limit")
        history_items = list(history.iterdir())
        for path in history_items:
            _safe_publication_path(path)
        expected_history_names = {
            name
            for sequence in range(1, len(records) + 1)
            for name in (f"{sequence:06d}.json", f"{sequence:06d}.before.json")
        }
        if (
            len(history_items) > MAX_GXT2_HISTORY_RECORDS * 2
            or any(path.is_symlink() or not path.is_file() for path in history_items)
            or {path.name for path in history_items} != expected_history_names
        ):
            raise ValueError("GXT2 workspace history contains unexpected records")
        for sequence, record_path in enumerate(records, start=1):
            if record_path.is_symlink() or record_path.name != f"{sequence:06d}.json":
                raise ValueError("GXT2 workspace history is not contiguous")
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid GXT2 history record: {exc}") from exc
            snapshot = history / f"{sequence:06d}.before.json"
            if snapshot.is_symlink() or not snapshot.is_file():
                raise ValueError("GXT2 workspace history snapshot is missing")
            if (
                not isinstance(record, dict)
                or record.get("sequence") != sequence
                or record.get("snapshot") != snapshot.name
                or not _is_sha256(record.get("before_sha256"))
                or not _is_sha256(record.get("after_sha256"))
                or not _is_sha256(record.get("snapshot_sha256"))
                or str(record["before_sha256"]).casefold() != str(previous_hash).casefold()
                or _sha256_file(snapshot) != str(record["snapshot_sha256"]).casefold()
                or str(record["snapshot_sha256"]).casefold()
                != str(record["before_sha256"]).casefold()
            ):
                raise ValueError("GXT2 workspace history hash chain is invalid")
            try:
                cls._validate_entries(json.loads(snapshot.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid GXT2 history snapshot: {exc}") from exc
            previous_hash = str(record["after_sha256"]).casefold()
        if current_hash != str(previous_hash).casefold():
            raise ValueError("GXT2 workspace entries do not match the edit history")
        return {
            "workspace": workspace, "manifest": manifest, "manifest_path": manifest_path,
            "original": original, "entries": entries, "entries_path": entries_path,
            "entries_sha256": current_hash, "history": history,
        }

    @classmethod
    def _mutate(
        cls, root: str | Path, action: str, update,
    ) -> Path:
        with cls.operation_lock(root):
            return cls._mutate_locked(root, action, update)

    @classmethod
    def _mutate_locked(cls, root: str | Path, action: str, update) -> Path:
        state = cls.validate(root)
        before = [dict(item) for item in state["entries"]]
        after = [dict(item) for item in before]
        update(after)
        normalized = cls._validate_entries(after)
        if tuple(before) == normalized:
            raise ValueError("GXT2 edit would not change the workspace")
        records = sorted(
            path for path in state["history"].glob("*.json")
            if not path.name.endswith(".before.json")
        )
        sequence = len(records) + 1
        snapshot = state["history"] / f"{sequence:06d}.before.json"
        record = state["history"] / f"{sequence:06d}.json"
        before_bytes = state["entries_path"].read_bytes()
        before_hash = state["entries_sha256"]
        try:
            _write_bytes_atomic(snapshot, before_bytes)
            _write_json_atomic(state["entries_path"], list(normalized))
            after_hash = _sha256_file(state["entries_path"])
            _write_json_atomic(record, {
                "sequence": sequence, "action": action,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "before_sha256": before_hash, "after_sha256": after_hash,
                "snapshot": snapshot.name,
                "snapshot_sha256": _sha256_file(snapshot),
            })
            cls.validate(root)
        except Exception:
            _write_bytes_atomic(state["entries_path"], before_bytes)
            snapshot.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            raise
        return record

    @classmethod
    def set_text(cls, root: str | Path, label_hash: int | str, text: str) -> Path:
        wanted = _label_hash(label_hash)

        def update(entries):
            for item in entries:
                if int(item["hash"]) == wanted:
                    item["text"] = text
                    return
            raise ValueError(f"GXT2 label was not found: 0x{wanted:08X}")

        return cls._mutate(root, "set_text", update)

    @classmethod
    def add(cls, root: str | Path, label_hash: int | str, text: str) -> Path:
        wanted = _label_hash(label_hash)

        def update(entries):
            if any(int(item["hash"]) == wanted for item in entries):
                raise ValueError(f"GXT2 label already exists: 0x{wanted:08X}")
            entries.append({"hash": wanted, "text": text})

        return cls._mutate(root, "add", update)

    @classmethod
    def remove(cls, root: str | Path, label_hash: int | str) -> Path:
        wanted = _label_hash(label_hash)

        def update(entries):
            for index, item in enumerate(entries):
                if int(item["hash"]) == wanted:
                    entries.pop(index)
                    return
            raise ValueError(f"GXT2 label was not found: 0x{wanted:08X}")

        return cls._mutate(root, "remove", update)

    @classmethod
    def undo(cls, root: str | Path) -> Path:
        with cls.operation_lock(root):
            return cls._undo_locked(root)

    @classmethod
    def _undo_locked(cls, root: str | Path) -> Path:
        state = cls.validate(root)
        records = sorted(
            path for path in state["history"].glob("*.json")
            if not path.name.endswith(".before.json")
        )
        if not records:
            raise ValueError("GXT2 workspace has no edit to undo")
        record = json.loads(records[-1].read_text(encoding="utf-8"))
        snapshot = state["history"] / str(record.get("snapshot", ""))
        if snapshot.is_symlink() or not snapshot.is_file():
            raise ValueError("GXT2 undo snapshot is missing")
        try:
            previous = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid GXT2 undo snapshot: {exc}") from exc
        normalized = cls._validate_entries(previous)
        return cls._mutate(
            root, f"undo_{record.get('sequence')}",
            lambda entries: entries.__setitem__(slice(None), list(normalized)),
        )

    @classmethod
    def build(cls, root: str | Path, destination: str | Path) -> tuple[Path, Path]:
        with cls.operation_lock(root):
            return cls._build_locked(root, destination)

    @classmethod
    def _build_locked(cls, root: str | Path, destination: str | Path) -> tuple[Path, Path]:
        from allin1_sdk.managed_package_conversion import _safe_publication_path
        _safe_publication_path(Path(destination).expanduser())
        state = cls.validate(root)
        output = Path(destination).expanduser().resolve()
        report = output.with_name(f"{output.name}.gxt2-validation.json")
        if output.suffix.casefold() != ".gxt2":
            raise ValueError("GXT2 build output must use .gxt2")
        if output.exists() or output.is_symlink() or report.exists() or report.is_symlink():
            raise ValueError("GXT2 build output or validation report already exists")
        data = cls.encode(state["entries"])
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects against a file appearing after review.
        stream = output.open("xb")
        report_created = False
        try:
            with stream:
                stream.write(data)
            reparsed = cls.parse(output.read_bytes())
            if reparsed != cls._validate_entries(state["entries"]):
                raise ValueError("GXT2 output differs from the validated workspace")
            evidence = {
                "schema_version": 1, "operation": "gxt2_text_build",
                "status": "verified", "workspace": str(state["workspace"]),
                "entry_count": len(reparsed), "size": len(data),
                "sha256": _sha256_file(output),
                "original_sha256": state["manifest"]["original_sha256"],
                "source_binding": state["manifest"].get("source_binding", {}),
            }
            with report.open("x", encoding="utf-8") as receipt:
                report_created = True
                json.dump(evidence, receipt, indent=2)
        except Exception:
            output.unlink(missing_ok=True)
            if report_created:
                report.unlink(missing_ok=True)
            raise
        return output, report
