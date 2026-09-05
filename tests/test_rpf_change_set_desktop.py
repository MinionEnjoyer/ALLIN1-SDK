"""Desktop staging uses real change-set and planning domains with controlled native reads."""
import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk import rpf_change_set_desktop as desktop
from allin1_sdk.rpf_change_set import RpfChangeSet
from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex, RpfArchiveRecord, RpfEntryRecord


@pytest.fixture
def source(tmp_path, monkeypatch):
    archive = tmp_path / "fixture.rpf"; archive.write_bytes(b"original RPF fixture")
    game = tmp_path / "game"; game.mkdir(); (game / "GTA5_Enhanced.exe").write_bytes(b"marker")
    data = tmp_path / "new.bin"; data.write_bytes(b"replacement")
    index = RpfIndex(source=archive, edition="enhanced", archive_size=archive.stat().st_size,
        archives=(RpfArchiveRecord("", archive.name, 7, "OPEN", archive.stat().st_size, 2),),
        entries=(RpfEntryRecord("::old.bin", "", "old.bin", "old.bin", "binary", 3, 3),
                 RpfEntryRecord("::empty", "", "empty", "empty", "directory", 0, 0, child_count=0)))
    monkeypatch.setattr(RpfExplorerService, "index", lambda self, path: index)
    monkeypatch.setattr(RpfExplorerService, "_require_tool", lambda self: None)
    monkeypatch.setattr(RpfExplorerService, "_batch_content_hashes", lambda self, index, entries, **kw:
        {entry.id: hashlib.sha256(b"old").hexdigest() for entry in entries})
    return {"action": "create", "archive": str(archive), "gta_path": str(game), "destination": str(tmp_path / "changes.json")}, data


def confirm(request):
    value = desktop.review(request)
    return {**request, "review_sha256": value["review_sha256"], "authoring_confirmed": True}, value


@pytest.fixture
def change_set(source):
    request, data = source
    result = desktop.apply(confirm(request)[0])
    return result["session"], data, request


def edit(session, action, **fields):
    return {"change_set": session["change_set"], "expected_sha256": session["state_sha256"], "action": action, **fields}


def test_create_review_is_read_only_and_confirmed_result_is_bound(source):
    request, _ = source
    before = Path(request["archive"]).read_bytes()
    payload, review = confirm(request)
    assert not Path(request["destination"]).exists()
    assert review["archive"]["sha256"] == hashlib.sha256(before).hexdigest()
    result = desktop.apply(payload)
    assert result["session"]["actions"] == [] and result["action"] == "create"
    assert not result["archive_write_performed"] and not result["game_write_performed"]
    assert Path(request["archive"]).read_bytes() == before
    assert result["output_sha256"] == result["session"]["state_sha256"]


def test_stage_reorder_remove_compile_round_trip(change_set):
    session, data, request = change_set
    for change in ({"action": "replace", "entry": "old.bin", "payload": str(data)},
                   {"action": "mkdir", "entry": "new"}, {"action": "rmdir", "entry": "empty"}):
        payload, review = confirm(edit(session, "stage", change=change))
        assert len(desktop.inspect({"change_set": session["change_set"]})["actions"]) == len(session["actions"])
        result = desktop.apply(payload); session = result["session"]
        assert session["actions"] == review["after"]
    row_id = session["actions"][2]["id"]
    session = desktop.apply(confirm(edit(session, "move", action_id=row_id, position=1))[0])["session"]
    assert session["actions"][0]["id"] == row_id
    session = desktop.apply(confirm(edit(session, "remove", action_id=row_id))[0])["session"]
    output = str(data.parent / "plan.json")
    payload, review = confirm(edit(session, "compile", gta_path=request["gta_path"], destination=output, authorized_root=str(data.parent)))
    assert review["plan"]["status"] == "ready" and not Path(output).exists()
    assert review["plan"]["changes"][0]["original"]["sha256"] == hashlib.sha256(b"old").hexdigest()
    result = desktop.apply(payload)
    plan = json.loads(Path(output).read_text())
    assert {k: v for k, v in plan.items() if k != "created_at"} == review["plan"]
    assert result["plan_status"] == "ready" and result["session"] == session
    assert Path(request["archive"]).read_bytes() == b"original RPF fixture"


@pytest.mark.parametrize("change", [
    {"action": "rename", "entry": "old.bin", "new_entry": "renamed.bin"},
    {"action": "delete", "entry": "old.bin"},
    {"action": "mkdir", "entry": "text/日本語"},
    {"action": "rmdir", "entry": "empty"},
])
def test_all_non_payload_actions_can_be_reviewed_and_staged(change_set, change):
    session, _, _ = change_set
    payload, review = confirm(edit(session, "stage", change=change))
    assert desktop.apply(payload)["session"]["actions"] == review["after"]


@pytest.mark.parametrize("mutation", ["confirmation", "review", "document", "archive", "payload", "action", "target", "output"])
def test_stale_or_unconfirmed_review_never_saves(change_set, mutation):
    session, data, request = change_set
    payload, _ = confirm(edit(session, "stage", change={"action": "replace", "entry": "old.bin", "payload": str(data)}))
    document = Path(session["change_set"])
    if mutation == "confirmation": payload["authoring_confirmed"] = "true"
    elif mutation == "review": payload["review_sha256"] = "f" * 64
    elif mutation == "document": document.write_text(document.read_text() + " ")
    elif mutation == "archive": Path(request["archive"]).write_bytes(b"changed archive")
    elif mutation == "payload": data.write_bytes(b"changed payload")
    elif mutation == "action": payload["change"]["action"] = "add"
    elif mutation == "target": payload["change"]["entry"] = "different.bin"
    else: payload["destination"] = str(data.parent / "forbidden.json")
    before = document.read_bytes()
    with pytest.raises(ValueError): desktop.apply(payload)
    assert document.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"action": "execute", "entry": "old.bin"}, {"action": "add", "entry": "new.bin"},
    {"action": "rename", "entry": "old.bin"}, {"action": "delete", "entry": "../escape"},
    {"action": "delete", "entry": "/absolute"}, {"action": "delete", "entry": "nested.rpf!item"},
    {"action": "delete", "entry": "x", "script": "anything"}, {},
])
def test_rejects_untyped_or_unsafe_changes(change_set, change):
    session, _, _ = change_set
    with pytest.raises((TypeError, ValueError)): desktop.review(edit(session, "stage", change=change))


def test_self_payload_and_game_outputs_refused(change_set):
    session, data, request = change_set
    with pytest.raises(ValueError, match="own payload"):
        desktop.review(edit(session, "stage", change={"action": "add", "entry": "self.json", "payload": session["change_set"]}))
    with pytest.raises(ValueError, match="outside GTA"):
        desktop.review({**request, "destination": str(Path(request["gta_path"]) / "changes.json")})
    with pytest.raises(ValueError, match="new output"):
        desktop.review(request)


def test_missing_payload_can_be_removed_without_discarding_other_actions(change_set):
    session, data, _ = change_set
    session = desktop.apply(confirm(edit(session, "stage", change={"action": "add", "entry": "new.bin", "payload": str(data)}))[0])["session"]
    data.unlink()
    assert desktop.inspect({"change_set": session["change_set"]})["actions"]
    payload, _ = confirm(edit(session, "remove", action_id=session["actions"][0]["id"]))
    assert desktop.apply(payload)["session"]["actions"] == []


def test_compile_surfaces_blocked_scope_and_refuses_duplicate_targets(change_set):
    session, data, request = change_set
    change = {"action": "mkdir", "entry": "new"}
    session = desktop.apply(confirm(edit(session, "stage", change=change))[0])["session"]
    payload, value = confirm(edit(session, "compile", gta_path=request["gta_path"], destination=str(data.parent / "blocked.json")))
    assert value["plan"]["status"] == "blocked" and value["plan"]["blocking_reasons"]
    assert desktop.apply(payload)["plan_status"] == "blocked"
    session = desktop.apply(confirm(edit(session, "stage", change=change))[0])["session"]
    with pytest.raises(ValueError, match="more than once"):
        desktop.review(edit(session, "compile", gta_path=request["gta_path"], destination=str(data.parent / "bad.json")))


def test_commit_rechecks_domain_state(change_set, monkeypatch):
    session, data, request = change_set
    payload, _ = confirm(edit(session, "stage", change={"action": "mkdir", "entry": "new"}))
    original = RpfChangeSet.commit_actions.__func__
    def concurrent(cls, path, actions, **kw):
        Path(path).write_text(Path(path).read_text() + " ")
        original(cls, path, actions, **kw)
    monkeypatch.setattr(RpfChangeSet, "commit_actions", classmethod(concurrent))
    with pytest.raises(ValueError, match="after review"): desktop.apply(payload)
    assert RpfChangeSet.describe(session["change_set"])["actions"] == []


@pytest.mark.parametrize("drift", ["document", "plan"])
def test_compile_rechecks_state_and_plan_at_final_domain_boundary(change_set, monkeypatch, drift):
    session, data, request = change_set
    session = desktop.apply(confirm(edit(session, "stage", change={"action": "mkdir", "entry": "new"}))[0])["session"]
    output = data.parent / "plan.json"
    payload, _ = confirm(edit(session, "compile", gta_path=request["gta_path"], destination=str(output), authorized_root=str(data.parent)))
    original = RpfChangeSet.compile_plan.__func__
    def concurrent(cls, path, service, destination, **kw):
        if drift == "document":
            Path(path).write_text(Path(path).read_text() + " ")
        else:
            original_plan = service.multi_change_plan
            def changed(*args, **kwargs):
                result = original_plan(*args, **kwargs)
                result["warnings"].append("Scope evidence changed")
                return result
            service.multi_change_plan = changed
        return original(cls, path, service, destination, **kw)
    monkeypatch.setattr(RpfChangeSet, "compile_plan", classmethod(concurrent))
    with pytest.raises(ValueError, match="after review"): desktop.apply(payload)
    assert not output.exists()
    assert Path(request["archive"]).read_bytes() == b"original RPF fixture"


def test_create_rechecks_same_size_archive_change_at_final_domain_boundary(source, monkeypatch):
    request, _ = source
    payload, _ = confirm(request)
    original = RpfChangeSet.create.__func__
    def concurrent(cls, index, destination, **kw):
        index.source.write_bytes(b"x" * index.archive_size)
        return original(cls, index, destination, **kw)
    monkeypatch.setattr(RpfChangeSet, "create", classmethod(concurrent))
    with pytest.raises(ValueError, match="after review"): desktop.apply(payload)
    assert not Path(request["destination"]).exists()


@pytest.mark.parametrize("limit", ["MAX_DOCUMENT", "MAX_ARCHIVE", "MAX_PAYLOAD", "MAX_ACTIONS"])
def test_desktop_limits_fail_before_saving(change_set, monkeypatch, limit):
    session, data, _ = change_set
    document = Path(session["change_set"])
    before = document.read_bytes()
    monkeypatch.setattr(desktop, limit, 0)
    with pytest.raises(ValueError, match="limit"):
        desktop.review(edit(session, "stage", change={"action": "add", "entry": "new.bin", "payload": str(data)}))
    assert document.read_bytes() == before


def test_external_plan_scope_must_be_the_exact_archive_parent(change_set):
    session, data, request = change_set
    session = desktop.apply(confirm(edit(session, "stage", change={"action": "mkdir", "entry": "new"}))[0])["session"]
    with pytest.raises(ValueError, match="directly containing"):
        desktop.review(edit(session, "compile", gta_path=request["gta_path"], destination=str(data.parent / "plan.json"), authorized_root=str(data.parent.parent)))


def test_protocol_risk_and_mutation_job_refusal(source):
    from allin1_sdk.desktop_protocol import dispatch_operation, JOB_OPERATIONS, _operation_risk
    request, _ = source
    risk, value = dispatch_operation("review_rpf_change_set", request)
    assert risk == "read_only" and value["review_only"]
    assert "apply_rpf_change_set" not in JOB_OPERATIONS
    assert _operation_risk("review_rpf_change_set", {}) == "read_only"
    risk, result = dispatch_operation("apply_rpf_change_set", confirm(request)[0])
    assert risk == "authoring_write" and not result["archive_write_performed"]
