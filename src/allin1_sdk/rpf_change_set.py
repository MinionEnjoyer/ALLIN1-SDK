"""Persistent, reviewable change sets for atomic RPF archive authoring."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex, _safe_virtual_path


RPF_CHANGE_SET_SCHEMA = 1
RPF_CHANGE_SET_OPERATION = "rpf_change_set"
MAX_RPF_CHANGE_SET_ACTIONS = 1_000
CHANGE_ACTIONS = frozenset({
    "replace", "add", "delete", "mkdir", "rmdir", "rename",
})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _write_json_new(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        temporary.rename(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _detected_gta_root(path: Path) -> Path | None:
    folder = path if path.is_dir() else path.parent
    for candidate in (folder, *folder.parents):
        if any((candidate / marker).is_file() for marker in (
            "GTA5.exe", "GTA5_Enhanced.exe", "PlayGTAV.exe",
        )):
            return candidate
    return None


def _action_id(value: object) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in value)
    ):
        raise ValueError(f"Invalid RPF change-set action id: {value!r}")
    return value


def _virtual_path(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"RPF change-set {label} must be text")
    return _safe_virtual_path(value, allow_empty=allow_empty)


class RpfChangeSet:
    """Create and edit an inert list that compiles to a guarded atomic plan."""

    @classmethod
    def create(cls, index: RpfIndex, destination: str | Path) -> Path:
        source = index.source.resolve()
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"RPF source archive not found: {source}")
        if source.stat().st_size != index.archive_size:
            raise ValueError("RPF source changed after indexing; index it again")
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != ".json":
            raise ValueError("RPF change set must use a .json extension")
        detected = _detected_gta_root(output)
        if detected is not None:
            raise ValueError(f"RPF change sets must be stored outside GTA V: {detected}")
        source_sha256 = _sha256_file(source)
        if source.stat().st_size != index.archive_size:
            raise ValueError("RPF source changed while creating the change set")
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema_version": RPF_CHANGE_SET_SCHEMA,
            "operation": RPF_CHANGE_SET_OPERATION,
            "created_utc": now,
            "updated_utc": now,
            "archive": {
                "path": str(source), "edition": index.edition,
                "size": index.archive_size, "sha256": source_sha256,
            },
            "actions": [],
        }
        cls._normalize(payload, verify_files=False)
        _write_json_new(output, payload)
        return output

    @staticmethod
    def _read(path: str | Path) -> tuple[Path, dict[str, Any]]:
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"RPF change set not found: {source}")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid RPF change-set JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("RPF change set must be a JSON object")
        return source, payload

    @classmethod
    def _normalize(
        cls, payload: dict[str, Any], *, verify_files: bool,
    ) -> dict[str, Any]:
        if (
            payload.get("schema_version") != RPF_CHANGE_SET_SCHEMA
            or payload.get("operation") != RPF_CHANGE_SET_OPERATION
        ):
            raise ValueError("Unsupported RPF change-set schema")
        archive = payload.get("archive")
        actions = payload.get("actions")
        if not isinstance(archive, dict) or not isinstance(actions, list):
            raise ValueError("RPF change set requires archive metadata and an actions array")
        if len(actions) > MAX_RPF_CHANGE_SET_ACTIONS:
            raise ValueError("RPF change set exceeds its guarded action limit")
        archive_path_value = archive.get("path")
        if not isinstance(archive_path_value, str) or not archive_path_value:
            raise ValueError("RPF change set has an invalid archive path")
        archive_path = Path(archive_path_value).expanduser()
        if not archive_path.is_absolute():
            raise ValueError("RPF change-set archive path must be absolute")
        archive_path = archive_path.resolve()
        if not isinstance(archive.get("edition"), str) or not archive["edition"]:
            raise ValueError("RPF change set has an invalid archive edition")
        if not isinstance(archive.get("size"), int) or archive["size"] < 0:
            raise ValueError("RPF change set has an invalid archive size")
        if not _is_sha256(archive.get("sha256")):
            raise ValueError("RPF change set has an invalid archive SHA-256")

        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for number, item in enumerate(actions, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"RPF change-set action {number} must be an object")
            action_id = _action_id(item.get("id"))
            if action_id.casefold() in seen_ids:
                raise ValueError(f"Duplicate RPF change-set action id: {action_id}")
            seen_ids.add(action_id.casefold())
            action = item.get("action")
            if action not in CHANGE_ACTIONS:
                raise ValueError(f"Unsupported RPF change-set action: {action!r}")
            archive_path_virtual = _virtual_path(
                item.get("archive_path", ""), f"action {action_id} archive_path",
                allow_empty=True,
            )
            entry = _virtual_path(item.get("entry"), f"action {action_id} entry")
            prepared = {
                "id": action_id, "action": action,
                "archive_path": archive_path_virtual, "entry": entry,
            }
            if action in {"replace", "add"}:
                payload_record = item.get("payload")
                if not isinstance(payload_record, dict):
                    raise ValueError(f"RPF {action} action {action_id} requires a payload")
                payload_value = payload_record.get("path")
                if not isinstance(payload_value, str) or not payload_value:
                    raise ValueError(f"RPF action {action_id} has an invalid payload path")
                payload_path = Path(payload_value).expanduser()
                if not payload_path.is_absolute():
                    raise ValueError(f"RPF action {action_id} payload path must be absolute")
                payload_path = payload_path.resolve()
                if not isinstance(payload_record.get("size"), int) or payload_record["size"] < 0:
                    raise ValueError(f"RPF action {action_id} has an invalid payload size")
                if not _is_sha256(payload_record.get("sha256")):
                    raise ValueError(f"RPF action {action_id} has an invalid payload SHA-256")
                prepared["payload"] = {
                    "path": str(payload_path), "size": payload_record["size"],
                    "sha256": payload_record["sha256"],
                }
                if verify_files:
                    if not payload_path.is_file() or payload_path.is_symlink():
                        raise FileNotFoundError(f"RPF action payload not found: {payload_path}")
                    if payload_path.stat().st_size != payload_record["size"] or (
                        _sha256_file(payload_path) != payload_record["sha256"]
                    ):
                        raise ValueError(f"RPF action payload changed: {payload_path}")
            elif item.get("payload") not in (None, ""):
                raise ValueError(f"RPF {action} action {action_id} cannot have a payload")
            if action == "rename":
                prepared["new_entry"] = _virtual_path(
                    item.get("new_entry"), f"action {action_id} new_entry",
                )
            elif item.get("new_entry") not in (None, ""):
                raise ValueError(f"RPF {action} action {action_id} cannot have new_entry")
            normalized.append(prepared)

        if verify_files:
            if not archive_path.is_file() or archive_path.is_symlink():
                raise FileNotFoundError(f"RPF source archive not found: {archive_path}")
            if archive_path.stat().st_size != archive["size"] or (
                _sha256_file(archive_path) != archive["sha256"]
            ):
                raise ValueError("RPF source archive changed after the change set was created")
        return {
            "archive": archive_path, "archive_record": dict(archive),
            "actions": tuple(normalized),
        }

    @classmethod
    def validate(
        cls, path: str | Path, *, verify_files: bool = False,
    ) -> dict[str, Any]:
        authored = Path(path).expanduser().resolve()
        if not authored.is_file() or authored.is_symlink():
            raise FileNotFoundError(f"RPF change set not found: {authored}")
        before_sha256 = _sha256_file(authored)
        source, payload = cls._read(path)
        state = cls._normalize(payload, verify_files=verify_files)
        if _sha256_file(source) != before_sha256:
            raise ValueError("RPF change set changed while opening it")
        state.update({
            "change_set": source, "change_set_sha256": before_sha256,
            "payload": payload,
        })
        return state

    @classmethod
    def describe(cls, path: str | Path, *, verify_files: bool = False) -> dict[str, Any]:
        state = cls.validate(path, verify_files=verify_files)
        counts = {action: 0 for action in sorted(CHANGE_ACTIONS)}
        for item in state["actions"]:
            counts[item["action"]] += 1
        return {
            "schema_version": 1,
            "operation": "rpf_change_set_inspection",
            "status": "ready" if state["actions"] else "empty",
            "change_set": str(state["change_set"]),
            "change_set_sha256": state["change_set_sha256"],
            "archive": state["archive_record"],
            "summary": {"actions": len(state["actions"]), "by_action": counts},
            "actions": list(state["actions"]),
            "files_verified": verify_files,
        }

    @classmethod
    def _mutate(
        cls, path: str | Path, callback: Callable[[dict[str, Any]], Any],
    ) -> Any:
        source, payload = cls._read(path)
        before_sha256 = _sha256_file(source)
        detected = _detected_gta_root(source)
        if detected is not None:
            raise ValueError(f"RPF change sets cannot be edited inside GTA V: {detected}")
        cls._normalize(payload, verify_files=False)
        result = callback(payload)
        payload["updated_utc"] = datetime.now(timezone.utc).isoformat()
        cls._normalize(payload, verify_files=False)
        if _sha256_file(source) != before_sha256:
            raise ValueError("RPF change set changed during edit")
        _write_json_atomic(source, payload)
        return result

    @classmethod
    def stage(
        cls, path: str | Path, action: str, entry: str, *,
        archive_path: str = "", payload: str | Path | None = None,
        new_entry: str | None = None,
    ) -> str:
        if action not in CHANGE_ACTIONS:
            raise ValueError(f"Unsupported RPF change-set action: {action}")
        safe_archive = _safe_virtual_path(archive_path, allow_empty=True)
        safe_entry = _safe_virtual_path(entry)
        prepared_payload = None
        if action in {"replace", "add"}:
            if payload is None:
                raise ValueError(f"RPF {action} requires a payload")
            authored = Path(payload).expanduser()
            if authored.is_symlink():
                raise ValueError("RPF change-set payload cannot be a symbolic link")
            selected = authored.resolve()
            if not selected.is_file():
                raise FileNotFoundError(f"RPF change-set payload not found: {selected}")
            prepared_payload = {
                "path": str(selected), "size": selected.stat().st_size,
                "sha256": _sha256_file(selected),
            }
        elif payload is not None:
            raise ValueError(f"RPF {action} cannot include a payload")
        safe_new_entry = None
        if action == "rename":
            if new_entry is None:
                raise ValueError("RPF rename requires new_entry")
            safe_new_entry = _safe_virtual_path(new_entry)
        elif new_entry is not None:
            raise ValueError(f"RPF {action} cannot include new_entry")

        def update(document: dict[str, Any]) -> str:
            action_id = f"change-{uuid.uuid4().hex[:12]}"
            item = {
                "id": action_id, "action": action,
                "archive_path": safe_archive, "entry": safe_entry,
            }
            if prepared_payload is not None:
                item["payload"] = prepared_payload
            if safe_new_entry is not None:
                item["new_entry"] = safe_new_entry
            document["actions"].append(item)
            return action_id

        return cls._mutate(path, update)

    @classmethod
    def remove(cls, path: str | Path, action_id: str) -> None:
        wanted = _action_id(action_id)

        def update(document: dict[str, Any]) -> None:
            before = len(document["actions"])
            document["actions"] = [
                item for item in document["actions"] if item["id"] != wanted
            ]
            if len(document["actions"]) == before:
                raise ValueError(f"RPF change-set action not found: {wanted}")

        cls._mutate(path, update)

    @classmethod
    def move(cls, path: str | Path, action_id: str, position: int) -> None:
        wanted = _action_id(action_id)
        if position < 1:
            raise ValueError("RPF change-set position starts at 1")

        def update(document: dict[str, Any]) -> None:
            actions = document["actions"]
            current = next((i for i, item in enumerate(actions) if item["id"] == wanted), None)
            if current is None:
                raise ValueError(f"RPF change-set action not found: {wanted}")
            item = actions.pop(current)
            actions.insert(min(position - 1, len(actions)), item)

        cls._mutate(path, update)

    @classmethod
    def compile_plan(
        cls, path: str | Path, service: RpfExplorerService,
        destination: str | Path,
    ) -> tuple[Path, dict[str, Any]]:
        state = cls.validate(path, verify_files=True)
        if not state["actions"]:
            raise ValueError("RPF change set contains no staged actions")
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() != ".json":
            raise ValueError("RPF change-set plan must use a .json extension")
        detected = _detected_gta_root(output)
        if detected is not None:
            raise ValueError(f"RPF change-set plans must be stored outside GTA V: {detected}")
        indexed = service.index(state["archive"])
        archive = state["archive_record"]
        if (
            indexed.edition.casefold() != archive["edition"].casefold()
            or indexed.archive_size != archive["size"]
            or _sha256_file(indexed.source) != archive["sha256"]
        ):
            raise ValueError("RPF source index no longer matches the change set")
        authored = []
        for item in state["actions"]:
            change = {
                key: item[key]
                for key in ("action", "archive_path", "entry", "new_entry")
                if key in item
            }
            if "payload" in item:
                change["payload"] = item["payload"]["path"]
            authored.append(change)
        plan = service.multi_change_plan(indexed, authored)
        plan["change_set"] = {
            "path": str(state["change_set"]),
            "sha256": state["change_set_sha256"],
            "action_ids": [item["id"] for item in state["actions"]],
        }
        after = cls.validate(path, verify_files=True)
        if after["change_set_sha256"] != state["change_set_sha256"]:
            raise ValueError("RPF change set changed while compiling its plan")
        _write_json_new(output, plan)
        return output, plan
