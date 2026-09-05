"""Reviewed transactions on authoring copies and explicitly selected GTA mods copies."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from allin1_sdk.gxt2_desktop import _digest, _file_hash
from allin1_sdk.managed_package_conversion import _safe_publication_path
from allin1_sdk.paths import gta_root_containing, user_data_root
from allin1_sdk.rpf_change_set_desktop import MAX_ACTIONS, MAX_ARCHIVE, MAX_DOCUMENT, MAX_PAYLOAD, _file, _path, _service
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk import rpf_lock_recovery


def _absolute_path(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or "\0" in value or not Path(value).is_absolute():
        raise ValueError("Choose a bounded absolute authoring path")
    path = Path(value)
    _safe_publication_path(path)
    return path.resolve()


def _local_path(value):
    path = _absolute_path(value)
    if gta_root_containing(path):
        raise ValueError("Desktop RPF transactions must stay outside GTA V, including mods")
    return path


def _archive_path(value):
    path = _absolute_path(value)
    game = gta_root_containing(path)
    if game and not path.is_relative_to(game / "mods"):
        raise ValueError("Stock GTA archives are blocked; select an existing mods archive")
    return path


def _scope(archive, data, payload):
    """Authority comes from a selected physical folder, never from the document alone."""
    archive = _archive_path(str(archive))
    if data.get("target_scope") == "mods_copy":
        if not payload.get("gta_path"):
            raise ValueError("Explicitly select the matching GTA installation for a mods transaction")
        game = _absolute_path(payload["gta_path"])
        if (not game.is_dir() or gta_root_containing(game) != game
                or not archive.is_relative_to(game / "mods")
                or gta_root_containing(archive) != game):
            raise ValueError("Mods archive must belong to the explicitly selected GTA installation")
        if payload.get("authorized_root") or data.get("authorized_root") is not None:
            raise ValueError("A mods transaction cannot use an external workspace authorization")
        return _service(archive, str(game))
    if data.get("target_scope") != "workspace_copy" or not payload.get("authorized_root"):
        raise ValueError("Explicit external workspace scope or matching GTA mods scope is required")
    _local_path(str(archive))
    service, game, authorized = _service(archive, payload.get("gta_path"), payload["authorized_root"])
    if archive.is_relative_to(Path(game)) or data.get("authorized_root") != authorized:
        raise ValueError("Selected workspace does not match the plan or receipt scope")
    return service, game, authorized


def transaction_root():
    return _local_path(str(user_data_root() / "rpf-transactions"))


def _document(value):
    path = _path(value, write=True)
    _file(str(path), MAX_DOCUMENT)
    raw = path.read_bytes()
    if len(raw) > MAX_DOCUMENT:
        raise ValueError("RPF transaction document exceeds desktop limits")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("operation") != "rpf_multi_entry_change":
        raise ValueError("Choose a compiled multi-entry plan or its transaction receipt")
    changes = data.get("changes")
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_ACTIONS:
        raise ValueError("Transaction must contain 1–128 changes")
    for row in changes:
        if not isinstance(row, dict):
            raise ValueError("Invalid RPF transaction change")
        for key in ("archive_path", "entry", "new_entry"):
            if key in row and (not isinstance(row[key], str) or len(row[key]) > 2048):
                raise ValueError("RPF member path exceeds desktop limits")
    archive = _archive_path(data.get("archive"))
    if archive.suffix.casefold() != ".rpf" or (archive.exists() and (not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE)):
        raise ValueError("Choose a bounded RPF authoring or mods copy")
    source_kind = "receipt" if "transaction_id" in data else "plan"
    if source_kind == "receipt":
        RpfExplorerService._validate_multi_receipt(data)
        if path.name != "receipt.json" or path.parent.name != data["transaction_id"]:
            raise ValueError("Keep the transaction receipt in its original transaction folder")
        backup = _local_path(data["backup"].get("path"))
        if backup != path.parent / "archive.rpf.backup" or data["backup"]["size"] > MAX_ARCHIVE:
            raise ValueError("Rollback backup must belong to the selected transaction folder")
        if backup.exists() and (not backup.is_file() or backup.stat().st_size > MAX_ARCHIVE):
            raise ValueError("Rollback backup exceeds desktop limits")
    else:
        if data.get("schema_version") != 2 or "derived_delta" in data:
            raise ValueError("This screen accepts current compiled change-set plans, not derived delta plans")
        total = 0
        for row in changes:
            if row.get("payload") is not None:
                if not isinstance(row["payload"], dict):
                    raise ValueError("Invalid payload evidence")
                payload = _file(row["payload"].get("path"), MAX_PAYLOAD)
                if payload == archive or payload == path:
                    raise ValueError("The plan and target archive cannot be their own payload")
                total += payload.stat().st_size
        if total > 1024**3:
            raise ValueError("RPF transaction payloads exceed the 1-GiB desktop limit")
    return path, data, hashlib.sha256(raw).hexdigest(), archive, source_kind


def _bounded(value):
    if len(json.dumps(value, ensure_ascii=True).encode()) > 1024**2:
        raise ValueError("Transaction evidence exceeds desktop limits")
    return value


def history(payload):
    """Bounded summaries, not integrity verification; never walks arbitrary folders."""
    if payload != {}:
        raise ValueError("Transaction history uses only the SDK's retained receipt folder")
    root = transaction_root()
    rows, truncated = [], False
    if root.is_dir():
        with os.scandir(root) as entries:
            for count, entry in enumerate(entries):
                if count >= 256:
                    truncated = True
                    break
                source = Path(entry.path) / "receipt.json"
                if not source.exists():
                    continue
                row = {"source": str(source), "transaction_id": entry.name, "valid": False}
                try:
                    # Only bounded structural validation here, never payload reads/native decoding.
                    _local_path(str(source))
                    _file(str(source), MAX_DOCUMENT)
                    raw = source.read_bytes()
                    if len(raw) > MAX_DOCUMENT:
                        raise ValueError("Receipt exceeds desktop limits")
                    data = RpfExplorerService._validate_multi_receipt(json.loads(raw.decode("utf-8")))
                    if data["transaction_id"] != entry.name:
                        raise ValueError("Receipt folder identity mismatch")
                    row.update(valid=True, status=data["status"], archive=data["archive"],
                               created_at=data.get("created_at", ""), change_count=len(data["changes"]))
                    if len(data["changes"]) > MAX_ACTIONS or len(json.dumps(row)) > 8192:
                        raise ValueError("Receipt summary exceeds desktop limits")
                except (OSError, RuntimeError, TypeError, KeyError, ValueError) as exc:
                    row = {"source": str(source), "transaction_id": entry.name, "valid": False, "error": str(exc)[:512]}
                rows.append(row)
    rows.sort(key=lambda item: (str(item.get("created_at", "")), item["transaction_id"]), reverse=True)
    return _bounded({"kind": "rpf_transaction_history", "root": str(root), "receipts": rows,
                     "truncated": truncated, "scan_limit": 256, "read_only": True,
                     "archive_write_performed": False, "game_write_performed": False})


def _lock(service, archive):
    path = archive.with_name(f".{archive.name}.allin1.lock")
    _absolute_path(str(path))
    if not path.exists():
        return None
    _file(str(path), 16384)
    info = path.stat()
    digest = _file_hash(path)
    value = service.inspect_archive_lock(archive)
    if (_file_hash(path) != digest or value is None
            or rpf_lock_recovery.identity(path.stat()) != rpf_lock_recovery.identity(info)):
        raise ValueError("Archive lock changed during inspection")
    return {"path": str(path), "pid": value["pid"], "process_running": value["process_running"], "sha256": digest,
            "plan_id": value.get("plan_id"), "created_at": value.get("created_at"),
            "identity": rpf_lock_recovery.identity(info),
            "cleanup_supported": os.name == "nt" and not path.drive.startswith("\\") and info.st_nlink == 1}


def inspect(payload):
    if not isinstance(payload, dict) or set(payload) - {"source", "gta_path"}:
        raise ValueError("Inspection requires a source and optional GTA decoding context")
    path, data, digest, archive, kind = _document(payload.get("source"))
    verification, lock, game = None, None, payload.get("gta_path")
    if kind == "receipt":
        service, game, _ = _service(archive, game)
        verification = service.verify_transaction(path)
        lock = _lock(service, archive)
    archive_hash = _file_hash(archive) if archive.is_file() else None
    if _file_hash(path) != digest or (verification and verification.get("archive_sha256") != archive_hash):
        raise ValueError("Transaction evidence changed during inspection; inspect again")
    return _bounded({"kind": "rpf_transaction_session", "source": str(path), "source_kind": kind,
        "state_sha256": digest, "archive": str(archive), "archive_sha256": archive_hash,
        "edition": data.get("edition"), "plan_id": data.get("plan_id"), "status": data.get("status"),
        "target_scope": data.get("target_scope"), "authorized_root": data.get("authorized_root"),
        "changes": data["changes"], "gta_path": game, "verification": verification,
        "backup": data.get("backup"), "transaction_id": data.get("transaction_id"),
        "archive_lock": lock,
        "read_only": True, "archive_write_performed": False, "game_write_performed": False})


def review(payload):
    confirmations = {"review_sha256", "archive_write_confirmed", "game_write_confirmed", "receipt_write_confirmed", "lock_clear_confirmed"}
    allowed = {"source", "gta_path", "action", "expected_sha256", "authorized_root"} | confirmations
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("action") not in {"execute", "rollback", "recover", "clear_lock"}:
        raise ValueError("Choose an explicit execute, rollback, receipt recovery or lock cleanup action")
    path, data, digest, archive, kind = _document(payload.get("source"))
    if digest != payload.get("expected_sha256"):
        raise ValueError("Plan or receipt changed after inspection; reopen it before reviewing")
    if (payload["action"] == "execute") != (kind == "plan"):
        raise ValueError("Execution requires a plan; rollback requires its receipt")
    service, game, authorized = _scope(archive, data, payload)
    if not archive.is_file():
        raise ValueError("The transaction archive is missing; automatic recovery is not authorized")
    session = inspect({"source": str(path), "gta_path": game})
    recovery_status, lock_evidence = None, None
    if payload["action"] == "clear_lock":
        rpf_lock_recovery.require_supported(archive)
        verification, lock = session["verification"], session["archive_lock"]
        expected_state = "applied" if data["status"] == "applied" else "original"
        if (data["status"] not in {"applied", "rolled_back", "rolled_back_after_failure", "interrupted_before_commit"}
                or not verification["healthy"] or verification["archive_state"] != expected_state):
            raise ValueError("Lock cleanup requires a settled receipt and verified archive and backup; recover the receipt first")
        if (not lock or not lock["cleanup_supported"] or lock["plan_id"] != data["plan_id"]
                or type(lock["pid"]) is not int or lock["pid"] <= 0):
            raise ValueError("Lock cleanup requires a matching, supported transaction lock")
        if lock["process_running"]:
            raise ValueError("Lock owner is still running; cleanup is blocked")
        retained = _local_path(str(path.parent / f"cleared-lock-{lock['sha256']}.json"))
        existing = None
        if retained.exists():
            _file(str(retained), rpf_lock_recovery.MAX_LOCK)
            existing = _file_hash(retained)
            if existing != lock["sha256"] or retained.stat().st_nlink != 1:
                raise ValueError("Retained lock evidence is different or linked; nothing may be overwritten")
        lock_evidence = {"path": str(retained), "sha256": lock["sha256"], "existing_sha256": existing}
        backup_root = path.parent
    elif payload["action"] == "recover":
        verification = session["verification"]
        if not verification["healthy"] or verification["archive_state"] not in {"applied", "original"}:
            raise ValueError("Receipt recovery requires verified archive entries and an intact original backup")
        if session["archive_lock"] and session["archive_lock"]["process_running"]:
            raise ValueError("Archive lock owner is still running; receipt recovery is blocked")
        recovery_status = "applied" if verification["archive_state"] == "applied" else "interrupted_before_commit"
        if data["status"] in {"applied", "rolled_back", "rolled_back_after_failure", "interrupted_before_commit"}:
            raise ValueError("Receipt is already settled; no recovery is needed")
        backup_root = path.parent
    elif kind == "plan":
        if session["archive_sha256"] != data.get("archive_sha256") or archive.stat().st_size != data.get("archive_size"):
            raise ValueError("Source archive changed after planning; compile and review again")
        validated_archive, changes = service._validate_multi_plan(data)
        if validated_archive != archive:
            raise ValueError("Plan archive identity mismatch")
        index = service.index(archive)
        if len(index.entries) > 25000:
            raise ValueError("Transaction index exceeds the 25,000-entry desktop limit")
        authored = [{**{k: row[k] for k in ("action", "archive_path", "entry", "new_entry") if k in row},
                     **({"payload": str(file)} if file is not None else {})} for row, file in changes]
        fresh = service.multi_change_plan(index, authored)
        for key in ("plan_id", "archive", "archive_size", "archive_sha256", "edition", "target_scope", "authorized_root", "changes", "status"):
            if fresh.get(key) != data.get(key):
                raise ValueError(f"Compiled plan no longer matches current {key}; compile and review again")
        backup_root = transaction_root()
        ancestor = backup_root
        while not ancestor.exists():
            ancestor = ancestor.parent
        service._require_transaction_space(archive, ancestor, archive.stat().st_size,
            sum(file.stat().st_size for _, file in changes if file is not None))
    else:
        verification = session["verification"]
        if data["status"] not in {"applied", "verified_staging", "rollback_failed"} or not verification["healthy"] or verification["archive_state"] != "applied":
            raise ValueError("Rollback requires an unchanged applied archive and a verified original backup")
        backup_root = path.parent
        service._require_transaction_space(archive, backup_root, archive.stat().st_size * 2, 0, backup_copy=False)
    if _file_hash(path) != digest or _file_hash(archive) != session["archive_sha256"]:
        raise ValueError("Transaction inputs changed while reviewing")
    request = {k: v for k, v in payload.items() if k not in confirmations}
    writes_archive = payload["action"] in {"execute", "rollback"}
    clears_lock = payload["action"] == "clear_lock"
    live = (writes_archive or clears_lock) and data["target_scope"] == "mods_copy"
    result = {"kind": "rpf_transaction_review", "action": payload["action"], "request": request,
        "session": session, "receipt_root": str(backup_root), "authorized_root": authorized,
        "restore_sha256": data["backup"]["sha256"] if kind == "receipt" else None,
        "review_only": True, "archive_write_required": writes_archive, "game_write_required": live,
        "lock_write_required": clears_lock, "lock_evidence": lock_evidence,
        "recovery_status": recovery_status, "game_write_performed": False,
        "warning": ("Retain the reviewed lock evidence, then remove only this stale transaction lock. The archive, receipt and backup stay unchanged. GTA must be closed."
                    if clears_lock else "Update only this receipt to match the verified archive. No archive is rewritten. Stale locks are retained."
                    if not writes_archive else f"This replaces the selected {'GTA mods' if live else 'authoring'} RPF in place. Keep the receipt and full-archive backup for rollback. GTA must be closed.")}
    _bounded(result)
    result["review_sha256"] = _digest(result)
    return result


def apply(payload, *, allow_rpf_writes=False):
    recovery = isinstance(payload, dict) and payload.get("action") == "recover"
    clearing = isinstance(payload, dict) and payload.get("action") == "clear_lock"
    if recovery and payload.get("receipt_write_confirmed") is not True:
        raise ValueError("Explicit receipt-write confirmation is required")
    if clearing and payload.get("lock_clear_confirmed") is not True:
        raise ValueError("Explicit lock-clear confirmation is required")
    if not isinstance(payload, dict) or (not recovery and not clearing and payload.get("archive_write_confirmed") is not True):
        raise ValueError("Explicit archive-write confirmation is required")
    value = review(payload)
    if payload.get("review_sha256") != value["review_sha256"]:
        raise ValueError("Transaction review is stale; review and confirm again")
    if value["game_write_required"] and (allow_rpf_writes is not True or payload.get("game_write_confirmed") is not True):
        raise ValueError("GTA mods writes require native-owner RPF authority and explicit game-write confirmation")
    session = value["session"]
    source, archive = Path(session["source"]), Path(session["archive"])
    service, game, _ = _scope(archive, session, value["request"])
    service._require_game_closed()

    def final_guard():
        _scope(archive, session, value["request"])
        _local_path(str(source))
        _local_path(value["receipt_root"])
        if _file_hash(source) != session["state_sha256"]:
            raise ValueError("Plan or receipt changed after review; refusing commit")
        if value["action"] in {"rollback", "recover", "clear_lock"}:
            _local_path(session["backup"]["path"])
        if recovery or clearing:
            service._require_game_closed()
            if (_file_hash(archive) != session["archive_sha256"]
                    or _file_hash(Path(session["backup"]["path"])) != session["backup"]["sha256"]
                    or (recovery and _lock(service, archive) != session["archive_lock"])):
                raise ValueError("Archive, backup or lock changed after recovery review")
        if clearing:
            _absolute_path(session["archive_lock"]["path"])
            _local_path(value["lock_evidence"]["path"])

    final_guard()
    if value["action"] == "execute":
        receipt = service.apply_change_plan(source, receipt_root=transaction_root(),
            expected_sha256=session["state_sha256"], before_commit=final_guard)
    elif value["action"] == "rollback":
        receipt = service.rollback_transaction(source, expected_sha256=session["state_sha256"], before_commit=final_guard)
    elif clearing:
        evidence = value["lock_evidence"]
        rpf_lock_recovery.clear_reviewed_lock(Path(session["archive_lock"]["path"]), session["archive_lock"],
            Path(evidence["path"]), evidence["existing_sha256"],
            process_running=service._pid_is_running, before_delete=final_guard)
        receipt = source
    else:
        service.recover_transaction(source, expected_sha256=session["state_sha256"], before_commit=final_guard)
        receipt = source
    result = inspect({"source": str(receipt), "gta_path": game})
    expected = session["verification"]["archive_state"] if recovery or clearing else "applied" if value["action"] == "execute" else "original"
    if not result["verification"]["healthy"] or result["verification"]["archive_state"] != expected:
        raise RuntimeError(f"Transaction returned unverifiable evidence. Inspect receipt before retrying: {receipt}")
    if clearing and (result["state_sha256"] != session["state_sha256"]
            or result["archive_sha256"] != session["archive_sha256"] or result["archive_lock"] is not None
            or _file_hash(Path(value["lock_evidence"]["path"])) != value["lock_evidence"]["sha256"]):
        raise RuntimeError("Lock cleanup completed but evidence changed; recheck the receipt before another operation")
    return {"kind": "rpf_transaction_applied", "action": value["action"], "review_sha256": value["review_sha256"],
        "session": result, "archive_write_performed": value["archive_write_required"],
        "receipt_write_performed": not clearing, "lock_write_performed": clearing,
        "lock_evidence": value["lock_evidence"], "game_write_performed": value["game_write_required"]}
