import hashlib
import json
import os
from pathlib import Path

import pytest

from allin1_sdk import rpf_transaction_desktop as desktop, rpf_change_set_desktop, rpf_tools
from allin1_sdk.rpf_tools import RpfExplorerService
from test_rpf_tools import _transaction_service


@pytest.fixture
def planned(tmp_path, monkeypatch):
    original_service, game_archive, _, _ = _transaction_service(tmp_path, monkeypatch, nested=True)
    (original_service.gta_path / "GTA5_Enhanced.exe").write_bytes(b"marker")
    root = tmp_path / "authoring"; root.mkdir()
    archive = root / game_archive.name; archive.write_bytes(game_archive.read_bytes())
    data = tmp_path / "new.ymap"; data.write_bytes(b"new root payload")
    service = RpfExplorerService(original_service.project_root, original_service.gta_path, workspace_roots=(root,))
    monkeypatch.setattr(rpf_change_set_desktop, "project_root", lambda: service.project_root)
    monkeypatch.setattr(desktop, "user_data_root", lambda: tmp_path / "user")
    plan = service.multi_change_plan(service.index(archive), [
        {"action": "replace", "entry": "common/data/test.ymap", "payload": str(data)},
        {"action": "replace", "archive_path": "x64/textures.rpf", "entry": "vehicle.ytd", "payload": str(data)},
        {"action": "mkdir", "entry": "new-folder"},
    ])
    path = tmp_path / "plan.json"; path.write_text(json.dumps(plan))
    request = {"source": str(path), "gta_path": str(service.gta_path), "authorized_root": str(root),
               "action": "execute", "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return request, archive, data, service


def confirmed(request):
    value = desktop.review(request)
    return {**request, "review_sha256": value["review_sha256"], "archive_write_confirmed": True}


@pytest.fixture
def stale_lock(planned, monkeypatch):
    if os.name != "nt":
        pytest.skip("Reviewed file-handle cleanup is Windows-only")
    request, archive, _, _ = planned
    applied = desktop.apply(confirmed(request))
    session = applied["session"]
    lock = archive.with_name(f".{archive.name}.allin1.lock")
    lock.write_text(json.dumps({"pid": 99999999, "plan_id": session["plan_id"], "created_at": "2026-09-04T00:00:00Z"}))
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: False))
    request = {**rollback_request(applied, request), "action": "clear_lock"}
    return request, archive, lock, session


def confirm_lock(request):
    reviewed = desktop.review(request)
    return {**request, "review_sha256": reviewed["review_sha256"], "lock_clear_confirmed": True}


def test_reviewed_lock_cleanup_retains_bytes_and_leaves_transaction_unchanged(stale_lock):
    request, archive, lock, session = stale_lock
    receipt, backup = Path(session["source"]), Path(session["backup"]["path"])
    before = [path.read_bytes() for path in (archive, receipt, backup)]
    raw = lock.read_bytes()
    reviewed = desktop.review(request)
    assert lock.exists() and not Path(reviewed["lock_evidence"]["path"]).exists()
    assert reviewed["lock_write_required"] and not reviewed["archive_write_required"]
    result = desktop.apply(confirm_lock(request))
    assert not lock.exists() and result["session"]["archive_lock"] is None
    assert Path(result["lock_evidence"]["path"]).read_bytes() == raw
    assert result["lock_write_performed"] and not result["archive_write_performed"]
    assert not result["receipt_write_performed"] and not result["game_write_performed"]
    assert before == [path.read_bytes() for path in (archive, receipt, backup)]
    assert desktop.review({**request, "action": "rollback"})["archive_write_required"]


@pytest.mark.parametrize("mutation", ["confirmation", "digest", "lock", "owner", "plan", "receipt", "backup", "archive", "game", "scope", "evidence"])
def test_lock_cleanup_refuses_stale_or_unconfirmed_inputs(stale_lock, monkeypatch, mutation):
    request, archive, lock, session = stale_lock
    payload = confirm_lock(request)
    if mutation == "confirmation": payload["lock_clear_confirmed"] = "true"
    elif mutation == "digest": payload["review_sha256"] = "f" * 64
    elif mutation == "lock": lock.write_text(lock.read_text() + " ")
    elif mutation == "owner": monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: True))
    elif mutation == "plan":
        data = json.loads(lock.read_bytes()); data["plan_id"] = "f" * 64; lock.write_text(json.dumps(data))
    elif mutation == "receipt": Path(session["source"]).write_text(Path(session["source"]).read_text() + " ")
    elif mutation == "backup": Path(session["backup"]["path"]).write_bytes(b"changed")
    elif mutation == "archive": archive.write_bytes(b"changed")
    elif mutation == "game": monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
    elif mutation == "scope": payload["authorized_root"] = str(archive.parent.parent)
    else: Path(desktop.review(request)["lock_evidence"]["path"]).write_bytes(b"unrelated evidence")
    before = archive.read_bytes(), lock.read_bytes()
    with pytest.raises((ValueError, RuntimeError, OSError)):
        desktop.apply(payload)
    assert before == (archive.read_bytes(), lock.read_bytes())


def test_cleanup_holds_the_exact_lock_and_retains_evidence_if_delete_fails(stale_lock, monkeypatch):
    request, _, lock, _ = stale_lock
    raw = lock.read_bytes()
    recovery = desktop.rpf_lock_recovery
    original = recovery._delete_open_file
    def fail(stream):
        # Both replacement and mutation fail while the reviewed handle is held.
        with pytest.raises(OSError): lock.unlink()
        with pytest.raises(OSError): lock.write_bytes(b"racing writer")
        raise OSError("injected delete failure")
    monkeypatch.setattr(recovery, "_delete_open_file", fail)
    with pytest.raises(OSError, match="injected"):
        desktop.apply(confirm_lock(request))
    retained = Path(desktop.review(request)["lock_evidence"]["path"])
    assert lock.read_bytes() == raw == retained.read_bytes()
    monkeypatch.setattr(recovery, "_delete_open_file", original)
    # A fresh review can reuse the exact retained bytes; it never overwrites them.
    desktop.apply(confirm_lock(request))
    assert not lock.exists() and retained.read_bytes() == raw


def test_cleanup_requires_reconciled_receipt(stale_lock):
    request, _, lock, session = stale_lock
    path = Path(session["source"])
    data = json.loads(path.read_bytes()); data["status"] = "verified_staging"; path.write_text(json.dumps(data))
    request["expected_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="settled receipt"):
        desktop.review(request)
    assert lock.exists()


@pytest.mark.parametrize("mutation", ["archive", "backup", "receipt", "game", "owner"])
def test_cleanup_rechecks_after_retaining_evidence(stale_lock, monkeypatch, mutation):
    request, archive, lock, session = stale_lock
    original_sync = os.fsync
    owner_running = [False]
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: owner_running[0]))
    def drift(descriptor):
        original_sync(descriptor)
        if mutation == "archive": archive.write_bytes(b"external edit")
        elif mutation == "backup": Path(session["backup"]["path"]).write_bytes(b"changed backup")
        elif mutation == "receipt": Path(session["source"]).write_text(Path(session["source"]).read_text() + " ")
        elif mutation == "game": monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
        else: owner_running[0] = True
    monkeypatch.setattr(os, "fsync", drift)
    raw = lock.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        desktop.apply(confirm_lock(request))
    assert lock.read_bytes() == raw
    if mutation == "archive": assert archive.read_bytes() == b"external edit"


def test_cleanup_rejects_identical_replacement_after_final_review(stale_lock, monkeypatch):
    request, _, lock, _ = stale_lock
    raw = lock.read_bytes()
    original = desktop.rpf_lock_recovery.clear_reviewed_lock
    def replace(*args, **kwargs):
        lock.rename(lock.with_suffix(".old"))
        lock.write_bytes(raw)
        return original(*args, **kwargs)
    monkeypatch.setattr(desktop.rpf_lock_recovery, "clear_reviewed_lock", replace)
    with pytest.raises(ValueError, match="Lock changed"):
        desktop.apply(confirm_lock(request))
    assert lock.read_bytes() == raw


def test_cleanup_refuses_hardlinked_lock(stale_lock):
    request, _, lock, _ = stale_lock
    os.link(lock, lock.with_suffix(".alias"))
    with pytest.raises(ValueError, match="supported transaction lock"):
        desktop.review(request)
    assert lock.exists()


def test_cleanup_fails_closed_when_process_check_is_unavailable(stale_lock, monkeypatch):
    request, _, lock, _ = stale_lock
    def unavailable(pid):
        raise RuntimeError("Could not verify the RPF lock owner process")
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(unavailable))
    with pytest.raises(RuntimeError, match="Could not verify"):
        desktop.review(request)
    assert lock.exists()


def rollback_request(applied, request):
    session = applied["session"]
    return {**request, "source": session["source"], "action": "rollback", "expected_sha256": session["state_sha256"]}


def test_execute_verify_and_rollback_round_trip(planned):
    request, archive, _, service = planned
    original = archive.read_bytes()
    session = desktop.inspect({"source": request["source"]})
    assert session["source_kind"] == "plan" and not session["archive_write_performed"]
    payload = confirmed(request)
    assert archive.read_bytes() == original and not desktop.transaction_root().exists()
    applied = desktop.apply(payload)
    receipt = applied["session"]
    assert receipt["status"] == "applied" and receipt["verification"]["healthy"]
    assert receipt["verification"]["archive_state"] == "applied" and archive.read_bytes() != original
    assert applied["archive_write_performed"] and not applied["game_write_performed"]
    restored = desktop.apply(confirmed(rollback_request(applied, request)))
    assert restored["session"]["status"] == "rolled_back"
    assert restored["session"]["verification"]["archive_state"] == "original"
    assert archive.read_bytes() == original
    assert Path(receipt["backup"]["path"]).read_bytes() == original
    assert service.verify_transaction(receipt["source"])["healthy"]
    with pytest.raises(ValueError, match="Rollback requires"):
        desktop.review(rollback_request(restored, request))


@pytest.mark.parametrize("mutation", ["confirmation", "digest", "document", "archive", "payload", "scope", "action", "extra"])
def test_stale_or_unconfirmed_execution_does_not_write(planned, mutation):
    request, archive, data, _ = planned
    payload = confirmed(request)
    if mutation == "confirmation": payload["archive_write_confirmed"] = "true"
    elif mutation == "digest": payload["review_sha256"] = "f" * 64
    elif mutation == "document": Path(request["source"]).write_text(Path(request["source"]).read_text() + " ")
    elif mutation == "archive": archive.write_bytes(b"external edit")
    elif mutation == "payload": data.write_bytes(b"new external payload")
    elif mutation == "scope": payload["authorized_root"] = str(archive.parent.parent)
    elif mutation == "action": payload["action"] = "rollback"
    else: payload["force"] = True
    before = archive.read_bytes()
    with pytest.raises((ValueError, RuntimeError)): desktop.apply(payload)
    assert archive.read_bytes() == before and not desktop.transaction_root().exists()


@pytest.mark.parametrize("mutation", ["archive", "backup", "receipt", "game"])
def test_rollback_rejects_drift_and_open_game(planned, monkeypatch, mutation):
    request, archive, _, _ = planned
    applied = desktop.apply(confirmed(request))
    payload = confirmed(rollback_request(applied, request))
    if mutation == "archive": archive.write_bytes(b"external edit")
    elif mutation == "backup": Path(applied["session"]["backup"]["path"]).write_bytes(b"corrupt backup")
    elif mutation == "receipt": Path(payload["source"]).write_text(Path(payload["source"]).read_text() + " ")
    else: monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
    before = archive.read_bytes()
    with pytest.raises((ValueError, RuntimeError)): desktop.apply(payload)
    assert archive.read_bytes() == before


def test_execution_refuses_open_game_before_receipts_or_archive_writes(planned, monkeypatch):
    request, archive, _, _ = planned
    payload = confirmed(request); before = archive.read_bytes()
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
    with pytest.raises(RuntimeError, match="Close GTA V"): desktop.apply(payload)
    assert archive.read_bytes() == before and not desktop.transaction_root().exists()


@pytest.mark.parametrize("action", ["execute", "rollback"])
@pytest.mark.parametrize("mutation", ["archive", "document"])
def test_drift_during_staging_is_not_overwritten(planned, monkeypatch, action, mutation):
    request, archive, _, _ = planned
    if action == "rollback": request = rollback_request(desktop.apply(confirmed(request)), request)
    payload = confirmed(request)
    original_copy = RpfExplorerService._copy_verified
    previous = archive.read_bytes()
    def changing_copy(source, destination, digest):
        original_copy(source, destination, digest)
        target_stage = destination.name.endswith(".rollback-stage") if action == "rollback" else destination.parent.name.startswith(".allin1-stage-")
        if target_stage:
            if mutation == "archive": archive.write_bytes(b"concurrent external edit")
            else: Path(request["source"]).write_text(Path(request["source"]).read_text() + " ")
    monkeypatch.setattr(RpfExplorerService, "_copy_verified", staticmethod(changing_copy))
    with pytest.raises(RuntimeError, match="changed"): desktop.apply(payload)
    assert archive.read_bytes() == (b"concurrent external edit" if mutation == "archive" else previous)


def test_refuses_stock_game_targets_and_unselected_scope(planned):
    request, archive, _, service = planned
    with pytest.raises(ValueError, match="Explicit external"):
        desktop.review({k: v for k, v in request.items() if k != "authorized_root"})
    data = json.loads(Path(request["source"]).read_text())
    data["archive"] = str(service.gta_path / "update" / "test.rpf")
    Path(request["source"]).write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Stock GTA"):
        desktop.inspect({"source": request["source"]})


def test_protocol_authoring_risk_does_not_enable_game_writes(planned):
    from allin1_sdk.desktop_protocol import dispatch_operation, JOB_OPERATIONS, _operation_risk
    request, _, _, _ = planned
    assert "apply_rpf_transaction" not in JOB_OPERATIONS
    assert _operation_risk("review_rpf_transaction", {}) == "read_only"
    risk, review = dispatch_operation("review_rpf_transaction", request)
    assert risk == "read_only" and review["archive_write_required"]
    risk, result = dispatch_operation("apply_rpf_transaction", confirmed(request))
    assert risk == "authoring_write" and result["archive_write_performed"] and not result["game_write_performed"]


def test_missing_backup_remains_inspectable_but_cannot_restore(planned):
    request, _, _, _ = planned
    result = desktop.apply(confirmed(request))
    Path(result["session"]["backup"]["path"]).unlink()
    session = desktop.inspect({"source": result["session"]["source"], "gta_path": request["gta_path"]})
    assert not session["verification"]["healthy"] and not session["verification"]["backup_valid"]
    with pytest.raises(ValueError, match="verified original backup"):
        desktop.review(rollback_request(result, request))


def test_receipt_cannot_redirect_backup_outside_its_transaction(planned):
    request, _, _, _ = planned
    result = desktop.apply(confirmed(request))
    path = Path(result["session"]["source"])
    data = json.loads(path.read_text())
    data["backup"]["path"] = str(path.parent.parent / "elsewhere.rpf")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="selected transaction folder"):
        desktop.inspect({"source": str(path), "gta_path": request["gta_path"]})


def test_archive_lock_refuses_execution_without_overwriting(planned):
    request, archive, _, _ = planned
    payload = confirmed(request); before = archive.read_bytes()
    lock = archive.with_name(f".{archive.name}.allin1.lock")
    lock.write_text('{"pid":1}')
    with pytest.raises(RuntimeError, match="Another ALLIN1"):
        desktop.apply(payload)
    assert archive.read_bytes() == before and lock.read_text() == '{"pid":1}'


def test_insufficient_disk_space_refuses_review_without_writes(planned, monkeypatch):
    request, archive, _, _ = planned
    original = archive.read_bytes()
    def refuse(*args, **kwargs):
        raise RuntimeError("Not enough free space")
    monkeypatch.setattr(RpfExplorerService, "_require_transaction_space", staticmethod(refuse))
    with pytest.raises(RuntimeError, match="free space"): desktop.review(request)
    assert archive.read_bytes() == original and not desktop.transaction_root().exists()


@pytest.mark.parametrize("limit", ["MAX_DOCUMENT", "MAX_ARCHIVE", "MAX_PAYLOAD", "MAX_ACTIONS"])
def test_transaction_limits_refuse_before_writes(planned, monkeypatch, limit):
    request, archive, _, _ = planned
    original = archive.read_bytes()
    monkeypatch.setattr(desktop, limit, 0)
    with pytest.raises(ValueError): desktop.review(request)
    assert archive.read_bytes() == original and not desktop.transaction_root().exists()


@pytest.fixture
def live_plan(planned):
    request, _, data, service = planned
    archive = service.gta_path / "mods" / "update" / "test.rpf"
    plan = service.multi_change_plan(service.index(archive), [
        {"action": "replace", "entry": "common/data/test.ymap", "payload": str(data)},
        {"action": "replace", "archive_path": "x64/textures.rpf", "entry": "vehicle.ytd", "payload": str(data)},
    ])
    path = Path(request["source"]); path.write_text(json.dumps(plan))
    request = {k: v for k, v in request.items() if k != "authorized_root"}
    request["expected_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return request, archive, service


def test_mods_live_write_and_rollback_need_separate_native_authority(live_plan, tmp_path):
    from allin1_sdk.desktop_protocol import dispatch_operation, ProtocolError
    request, archive, _ = live_plan
    before = archive.read_bytes(); payload = {**confirmed(request), "game_write_confirmed": True}
    assert desktop.review(request)["game_write_required"] is True
    assert archive.read_bytes() == before
    for flags in ({}, {"allow_game_writes": True}, {"allow_package_writes": True}):
        with pytest.raises(ProtocolError, match="native-owner RPF authority"):
            dispatch_operation("apply_rpf_transaction", payload, **flags, audit_path=tmp_path / "audit.jsonl")
    risk, applied = dispatch_operation("apply_rpf_transaction", payload, allow_rpf_writes=True, audit_path=tmp_path / "audit.jsonl")
    assert risk == "game_write" and applied["game_write_performed"]
    assert archive.read_bytes() != before and applied["session"]["verification"]["healthy"]
    restore = {**confirmed(rollback_request(applied, request)), "game_write_confirmed": True}
    restored = desktop.apply(restore, allow_rpf_writes=True)
    assert restored["game_write_performed"] and archive.read_bytes() == before
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert not records[0]["completed"] and records[-1]["completed"] and records[-1]["archive"] == str(archive)


def test_mods_lock_cleanup_needs_native_authority_and_distinct_confirmation(live_plan, monkeypatch, tmp_path):
    if os.name != "nt": pytest.skip("Windows file-handle cleanup")
    from allin1_sdk.desktop_protocol import dispatch_operation, ProtocolError
    request, archive, _ = live_plan
    applied = desktop.apply({**confirmed(request), "game_write_confirmed": True}, allow_rpf_writes=True)
    session = applied["session"]
    lock = archive.with_name(f".{archive.name}.allin1.lock")
    lock.write_text(json.dumps({"pid": 99999999, "plan_id": session["plan_id"]}))
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: False))
    request = {**rollback_request(applied, request), "action": "clear_lock"}
    payload = confirm_lock(request)
    with pytest.raises(ValueError, match="game-write confirmation"):
        desktop.apply(payload, allow_rpf_writes=True)
    payload["game_write_confirmed"] = True
    audit = tmp_path / "lock-audit.jsonl"
    for flags in ({}, {"allow_game_writes": True}, {"allow_package_writes": True}):
        with pytest.raises(ProtocolError, match="native-owner RPF authority"):
            dispatch_operation("apply_rpf_transaction", payload, **flags, audit_path=audit)
    before = archive.read_bytes()
    risk, result = dispatch_operation("apply_rpf_transaction", payload, allow_rpf_writes=True, audit_path=audit)
    assert risk == "game_write" and result["game_write_performed"] and result["lock_write_performed"]
    assert not result["archive_write_performed"] and not result["receipt_write_performed"]
    assert archive.read_bytes() == before and not lock.exists()
    record = json.loads(audit.read_text().splitlines()[-1])
    assert record["lock_write_performed"] and record["lock_evidence"]["sha256"]
    assert not record["archive_write_performed"]


@pytest.mark.parametrize("mutation", ["confirmation", "owner", "missing_game", "other_game", "marker", "workspace", "injected_authority", "running"])
def test_mods_authority_refusals_never_write(live_plan, monkeypatch, mutation, tmp_path):
    request, archive, service = live_plan
    payload = {**confirmed(request), "game_write_confirmed": True}; owner = True
    if mutation == "confirmation": payload["game_write_confirmed"] = "true"
    elif mutation == "owner": owner = False
    elif mutation == "missing_game": payload.pop("gta_path")
    elif mutation == "other_game":
        game = tmp_path / "other-game"; game.mkdir(); (game / "GTA5_Enhanced.exe").write_bytes(b"marker")
        payload["gta_path"] = str(game)
    elif mutation == "marker": (service.gta_path / "GTA5_Enhanced.exe").unlink()
    elif mutation == "workspace": payload["authorized_root"] = str(archive.parent)
    elif mutation == "injected_authority": payload["allow_rpf_writes"] = True
    else: monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
    before = archive.read_bytes()
    with pytest.raises((RuntimeError, ValueError)): desktop.apply(payload, allow_rpf_writes=owner)
    assert archive.read_bytes() == before and not desktop.transaction_root().exists()


def test_mods_scope_is_rechecked_at_final_commit(live_plan, monkeypatch):
    request, archive, service = live_plan
    payload = {**confirmed(request), "game_write_confirmed": True}; before = archive.read_bytes()
    original_copy = RpfExplorerService._copy_verified
    def remove_marker(source, destination, digest):
        original_copy(source, destination, digest)
        if destination.parent.name.startswith(".allin1-stage-"):
            (service.gta_path / "GTA5_Enhanced.exe").unlink()
    monkeypatch.setattr(RpfExplorerService, "_copy_verified", staticmethod(remove_marker))
    with pytest.raises(RuntimeError, match="explicitly selected"):
        desktop.apply(payload, allow_rpf_writes=True)
    assert archive.read_bytes() == before


def interrupted(planned, state="applied"):
    request, archive, _, _ = planned
    applied = desktop.apply(confirmed(request)); path = Path(applied["session"]["source"])
    receipt = json.loads(path.read_text()); receipt["status"] = "verified_staging"
    path.write_text(json.dumps(receipt))
    if state == "original": archive.write_bytes(Path(receipt["backup"]["path"]).read_bytes())
    return {**request, "source": str(path), "action": "recover", "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.mark.parametrize("state", ["applied", "original"])
def test_interrupted_receipt_reconciliation_never_changes_archive_or_backup(planned, state):
    request = interrupted(planned, state); archive = planned[1]
    before = archive.read_bytes(); review = desktop.review(request)
    backup = Path(review["session"]["backup"]["path"]); backup_before = backup.read_bytes()
    assert not review["archive_write_required"] and not review["game_write_required"]
    result = desktop.apply({**request, "review_sha256": review["review_sha256"], "receipt_write_confirmed": True})
    assert not result["archive_write_performed"] and not result["game_write_performed"]
    assert result["session"]["status"] == ("applied" if state == "applied" else "interrupted_before_commit")
    assert archive.read_bytes() == before and backup.read_bytes() == backup_before
    with pytest.raises(ValueError, match="already settled"):
        desktop.review({**request, "expected_sha256": result["session"]["state_sha256"]})


@pytest.mark.parametrize("mutation", ["archive", "backup", "receipt", "game", "lock", "confirmation", "digest"])
def test_recovery_rejects_stale_or_unconfirmed_evidence(planned, monkeypatch, mutation):
    request = interrupted(planned); review = desktop.review(request)
    payload = {**request, "review_sha256": review["review_sha256"], "receipt_write_confirmed": True}
    archive = planned[1]; receipt = Path(request["source"])
    if mutation == "archive": archive.write_bytes(b"external edit")
    elif mutation == "backup": Path(review["session"]["backup"]["path"]).write_bytes(b"bad backup")
    elif mutation == "receipt": receipt.write_text(receipt.read_text() + " ")
    elif mutation == "game": monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("GTA5_Enhanced.exe",))
    elif mutation == "lock": archive.with_name(f".{archive.name}.allin1.lock").write_text(json.dumps({"pid": 1}))
    elif mutation == "confirmation": payload["receipt_write_confirmed"] = "true"
    else: payload["review_sha256"] = "f" * 64
    before, receipt_before = archive.read_bytes(), receipt.read_bytes()
    with pytest.raises((ValueError, RuntimeError)): desktop.apply(payload)
    assert archive.read_bytes() == before and receipt.read_bytes() == receipt_before


def test_stale_lock_is_reported_and_retained_during_receipt_recovery(planned, monkeypatch):
    request = interrupted(planned); archive = planned[1]
    lock = archive.with_name(f".{archive.name}.allin1.lock"); lock.write_text('{"pid":99999999}')
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: False))
    reviewed = desktop.review(request)
    assert reviewed["session"]["archive_lock"]["process_running"] is False
    result = desktop.apply({**request, "review_sha256": reviewed["review_sha256"], "receipt_write_confirmed": True})
    assert result["session"]["status"] == "applied" and lock.read_text() == '{"pid":99999999}'


def test_active_lock_blocks_receipt_recovery(planned, monkeypatch):
    request = interrupted(planned); archive = planned[1]
    archive.with_name(f".{archive.name}.allin1.lock").write_text('{"pid":1}')
    monkeypatch.setattr(RpfExplorerService, "_pid_is_running", staticmethod(lambda pid: True))
    with pytest.raises(ValueError, match="still running"): desktop.review(request)


def test_history_is_read_only_bounded_and_retains_malformed_receipts(planned):
    request, archive, _, _ = planned
    assert desktop.history({})["receipts"] == [] and not desktop.transaction_root().exists()
    applied = desktop.apply(confirmed(request)); before = archive.read_bytes()
    bad = desktop.transaction_root() / "bad"; bad.mkdir(); (bad / "receipt.json").write_text("not JSON")
    result = desktop.history({})
    assert len(result["receipts"]) == 2 and result["receipts"][0]["source"] == applied["session"]["source"]
    assert result["receipts"][1]["valid"] is False and result["read_only"] is True
    for number in range(260): (desktop.transaction_root() / str(number)).mkdir()
    assert desktop.history({})["truncated"] is True
    assert archive.read_bytes() == before
    with pytest.raises(ValueError): desktop.history({"root": str(archive.parent)})


def test_native_owner_negotiates_only_dedicated_rpf_authority():
    from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope, JOB_OPERATIONS
    service = DesktopProtocolService(allow_rpf_writes=True)
    handshake = service.handle(envelope("handshake", {"client": {"name": "test", "version": "1"}, "supported_versions": ["1.0.0"]}, request_id="rpf-owner", terminal=False))[0]
    assert handshake["payload"]["rpf_writes_enabled"] is True
    assert handshake["payload"]["game_writes_enabled"] is False
    assert "list_rpf_transactions" in JOB_OPERATIONS and "apply_rpf_transaction" not in JOB_OPERATIONS
