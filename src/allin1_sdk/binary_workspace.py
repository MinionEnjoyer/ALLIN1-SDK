"""Snapshot-backed same-size binary patch workspaces with auditable history."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BINARY_WORKSPACE_SCHEMA = 1
MAX_BINARY_WORKSPACE_BYTES = 512 * 1024 * 1024
MAX_BINARY_PATCH_BYTES = 64 * 1024
MAX_BINARY_DIFF_BYTES = 4 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _decode_hex(value: str, label: str) -> bytes:
    normalized = "".join(value.split()).replace("0x", "")
    if not normalized or len(normalized) % 2:
        raise ValueError(f"{label} must contain complete hexadecimal bytes")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} contains non-hexadecimal characters") from exc


class BinaryPatchWorkspace:
    """Create, mutate, validate, inspect, and build an exact binary workspace."""

    def export_bytes(
        self, name: str, data: bytes, destination: str | Path, *,
        source_binding: dict[str, object] | None = None,
    ) -> Path:
        if not data:
            raise ValueError("Binary workspace source is empty")
        if len(data) > MAX_BINARY_WORKSPACE_BYTES:
            raise ValueError(
                f"Binary workspace exceeds {MAX_BINARY_WORKSPACE_BYTES:,} bytes"
            )
        safe_name = Path(name).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Binary workspace requires a safe asset name")
        root = Path(destination).expanduser().resolve()
        if root.exists() or root.is_symlink():
            raise ValueError(f"Binary workspace destination already exists: {root}")
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{root.name}.binary-workspace-", dir=root.parent,
        )).resolve()
        try:
            original = stage / "original.bin"
            editable = stage / "editable.bin"
            original.write_bytes(data)
            editable.write_bytes(data)
            (stage / "history").mkdir()
            digest = hashlib.sha256(data).hexdigest()
            _write_json_atomic(stage / "binary-workspace.json", {
                "schema_version": BINARY_WORKSPACE_SCHEMA,
                "operation": "binary_patch_workspace",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "name": safe_name,
                "size": len(data),
                "original_sha256": digest,
                "source_binding": source_binding or {},
                "rules": {
                    "same_size_only": True,
                    "maximum_patch_bytes": MAX_BINARY_PATCH_BYTES,
                },
            })
            stage.rename(root)
            return root
        except Exception:
            if stage.is_dir() and stage.parent == root.parent:
                shutil.rmtree(stage)
            raise

    @staticmethod
    def _load(root: str | Path) -> tuple[Path, dict[str, Any], Path, Path, Path]:
        workspace = Path(root).expanduser().resolve()
        manifest_path = workspace / "binary-workspace.json"
        original = workspace / "original.bin"
        editable = workspace / "editable.bin"
        history = workspace / "history"
        for path in (workspace, manifest_path, original, editable, history):
            if path.is_symlink():
                raise ValueError(f"Binary workspace may not contain symbolic links: {path}")
        if not workspace.is_dir() or not history.is_dir():
            raise FileNotFoundError(f"Binary workspace not found: {workspace}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid binary workspace manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ValueError("Unsupported binary workspace schema")
        if manifest.get("operation") != "binary_patch_workspace":
            raise ValueError("Invalid binary workspace operation")
        size = int(manifest.get("size", -1))
        if (
            size <= 0 or size > MAX_BINARY_WORKSPACE_BYTES
            or not original.is_file() or not editable.is_file()
            or original.stat().st_size != size or editable.stat().st_size != size
        ):
            raise ValueError("Binary workspace size contract is invalid")
        original_hash = str(manifest.get("original_sha256", "")).casefold()
        if _sha256_file(original) != original_hash:
            raise ValueError("Binary workspace immutable source snapshot was modified")
        return workspace, manifest, original, editable, history

    @classmethod
    def validate(cls, root: str | Path) -> dict[str, Any]:
        workspace, manifest, original, editable, history = cls._load(root)
        current = str(manifest["original_sha256"]).casefold()
        records: list[dict[str, Any]] = []
        files = sorted(history.glob("*.json"), key=lambda item: item.name)
        if any(path.is_symlink() for path in files):
            raise ValueError("Binary workspace history may not contain symbolic links")
        for expected_number, path in enumerate(files, start=1):
            if path.name != f"{expected_number:06d}.json":
                raise ValueError("Binary workspace history sequence is incomplete")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid binary workspace history: {exc}") from exc
            if not isinstance(record, dict) or record.get("sequence") != expected_number:
                raise ValueError("Binary workspace history record is malformed")
            if str(record.get("before_sha256", "")).casefold() != current:
                raise ValueError("Binary workspace history hash chain is broken")
            offset = int(record.get("offset", -1))
            before = _decode_hex(str(record.get("old_hex", "")), "History old_hex")
            after = _decode_hex(str(record.get("new_hex", "")), "History new_hex")
            if len(before) != len(after) or not 0 <= offset <= int(manifest["size"]) - len(after):
                raise ValueError("Binary workspace history patch is outside the asset")
            current = str(record.get("after_sha256", "")).casefold()
            if len(current) != 64 or any(char not in "0123456789abcdef" for char in current):
                raise ValueError("Binary workspace history has an invalid result hash")
            records.append(record)
        editable_hash = _sha256_file(editable)
        if editable_hash != current:
            raise ValueError("Binary workspace editable file does not match its history")
        return {
            "workspace": workspace,
            "manifest": manifest,
            "original": original,
            "editable": editable,
            "history": history,
            "records": tuple(records),
            "editable_sha256": editable_hash,
        }

    @classmethod
    def patch(
        cls, root: str | Path, offset: int, replacement_hex: str, *,
        expected_hex: str = "",
    ) -> Path:
        state = cls.validate(root)
        replacement = _decode_hex(replacement_hex, "Replacement")
        if len(replacement) > MAX_BINARY_PATCH_BYTES:
            raise ValueError(
                f"Binary patch exceeds {MAX_BINARY_PATCH_BYTES:,} bytes"
            )
        size = int(state["manifest"]["size"])
        if offset < 0 or offset + len(replacement) > size:
            raise ValueError("Binary patch falls outside the editable asset")
        editable: Path = state["editable"]
        with editable.open("rb") as stream:
            stream.seek(offset)
            old = stream.read(len(replacement))
        if expected_hex:
            expected = _decode_hex(expected_hex, "Expected bytes")
            if len(expected) != len(replacement):
                raise ValueError("Expected and replacement byte counts must match")
            if old != expected:
                raise ValueError(
                    f"Expected bytes do not match offset 0x{offset:X}: {old.hex(' ')}"
                )
        if old == replacement:
            raise ValueError("Binary patch would not change the editable asset")
        before_hash = str(state["editable_sha256"])
        temporary = editable.with_name(".editable.bin.tmp")
        shutil.copyfile(editable, temporary)
        with temporary.open("r+b") as stream:
            stream.seek(offset)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        after_hash = _sha256_file(temporary)
        sequence = len(state["records"]) + 1
        record = state["history"] / f"{sequence:06d}.json"
        _write_json_atomic(record, {
            "sequence": sequence,
            "action": "patch",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "offset": offset,
            "length": len(replacement),
            "old_hex": old.hex(),
            "new_hex": replacement.hex(),
            "before_sha256": before_hash,
            "after_sha256": after_hash,
        })
        try:
            temporary.replace(editable)
        except Exception:
            record.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
            raise
        cls.validate(root)
        return record

    @classmethod
    def undo(cls, root: str | Path) -> Path:
        state = cls.validate(root)
        records = state["records"]
        if not records:
            raise ValueError("Binary workspace has no patch to undo")
        latest = records[-1]
        return cls.patch(
            root, int(latest["offset"]), str(latest["old_hex"]),
            expected_hex=str(latest["new_hex"]),
        )

    @classmethod
    def hexdump(
        cls, root: str | Path, *, offset: int = 0, length: int = 256,
    ) -> str:
        state = cls.validate(root)
        if length < 1 or length > 64 * 1024:
            raise ValueError("Hexdump length must be 1-65,536 bytes")
        size = int(state["manifest"]["size"])
        if offset < 0 or offset >= size:
            raise ValueError("Hexdump offset falls outside the asset")
        with state["editable"].open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        lines = []
        for index in range(0, len(data), 16):
            block = data[index:index + 16]
            hex_bytes = " ".join(f"{value:02X}" for value in block)
            ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in block)
            lines.append(f"{offset + index:08X}  {hex_bytes:<47}  |{ascii_text:<16}|")
        return "\n".join(lines)

    @classmethod
    def build(
        cls, root: str | Path, destination: str | Path,
    ) -> tuple[Path, Path]:
        state = cls.validate(root)
        output = Path(destination).expanduser().resolve()
        report = output.with_name(f"{output.name}.binary-diff.json")
        if output.exists() or output.is_symlink() or report.exists() or report.is_symlink():
            raise ValueError("Binary build output or diff report already exists")
        output.parent.mkdir(parents=True, exist_ok=True)
        ranges: list[dict[str, object]] = []
        changed_bytes = 0
        open_range: dict[str, object] | None = None
        with state["original"].open("rb") as left, state["editable"].open("rb") as right:
            offset = 0
            while True:
                original = left.read(1024 * 1024)
                edited = right.read(1024 * 1024)
                if not original:
                    break
                for index, (before, after) in enumerate(zip(original, edited)):
                    absolute = offset + index
                    if before != after:
                        changed_bytes += 1
                        if changed_bytes > MAX_BINARY_DIFF_BYTES:
                            raise ValueError(
                                f"Binary build exceeds the {MAX_BINARY_DIFF_BYTES:,}-byte "
                                "auditable diff limit"
                            )
                        if open_range is None:
                            open_range = {
                                "offset": absolute, "length": 1,
                                "original_hex": f"{before:02x}",
                                "edited_hex": f"{after:02x}",
                            }
                        elif absolute == int(open_range["offset"]) + int(open_range["length"]):
                            open_range["length"] = int(open_range["length"]) + 1
                            open_range["original_hex"] = str(open_range["original_hex"]) + f"{before:02x}"
                            open_range["edited_hex"] = str(open_range["edited_hex"]) + f"{after:02x}"
                        else:
                            ranges.append(open_range)
                            open_range = {
                                "offset": absolute, "length": 1,
                                "original_hex": f"{before:02x}",
                                "edited_hex": f"{after:02x}",
                            }
                    elif open_range is not None:
                        ranges.append(open_range)
                        open_range = None
                offset += len(original)
        if open_range is not None:
            ranges.append(open_range)
        if not changed_bytes:
            raise ValueError("Binary workspace has no changes to build")
        temporary = output.with_name(f".{output.name}.tmp")
        shutil.copyfile(state["editable"], temporary)
        temporary.replace(output)
        try:
            _write_json_atomic(report, {
                "schema_version": 1,
                "operation": "binary_patch_build",
                "status": "verified",
                "workspace": str(state["workspace"]),
                "name": state["manifest"]["name"],
                "size": state["manifest"]["size"],
                "original_sha256": state["manifest"]["original_sha256"],
                "output_sha256": _sha256_file(output),
                "changed_bytes": changed_bytes,
                "changed_ranges": ranges,
                "history_records": len(state["records"]),
                "source_binding": state["manifest"].get("source_binding", {}),
            })
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return output, report
