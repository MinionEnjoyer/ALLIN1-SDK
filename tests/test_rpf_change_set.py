from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main
from allin1_sdk.rpf_change_set import RpfChangeSet
from allin1_sdk.rpf_tools import RpfArchiveRecord, RpfIndex


def _index(tmp_path: Path) -> RpfIndex:
    archive = tmp_path / "source.rpf"
    archive.write_bytes(b"RPF7 source archive")
    return RpfIndex(
        source=archive.resolve(), edition="legacy", archive_size=archive.stat().st_size,
        archives=(RpfArchiveRecord(
            path="", name=archive.name, version=7, encryption="OPEN",
            size=archive.stat().st_size, entry_count=0,
        ),),
        entries=(),
    )


class FakeService:
    def __init__(self, index: RpfIndex):
        self.bound = index
        self.authored = None

    def index(self, archive):
        assert Path(archive).resolve() == self.bound.source
        return self.bound

    def multi_change_plan(self, index, authored):
        assert index is self.bound
        self.authored = authored
        return {
            "schema_version": 2,
            "operation": "rpf_multi_entry_change",
            "status": "ready",
            "changes": list(authored),
        }


def test_rpf_change_set_stages_reorders_verifies_and_compiles(tmp_path):
    index = _index(tmp_path)
    change_set = RpfChangeSet.create(index, tmp_path / "changes.json")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"new content")
    replace = RpfChangeSet.stage(
        change_set, "replace", "common/data/item.bin", payload=payload,
    )
    rename = RpfChangeSet.stage(
        change_set, "rename", "common/old", new_entry="common/new",
    )
    mkdir = RpfChangeSet.stage(change_set, "mkdir", "common/created")
    report = RpfChangeSet.describe(change_set, verify_files=True)
    assert report["status"] == "ready"
    assert report["summary"]["actions"] == 3
    assert report["summary"]["by_action"]["replace"] == 1
    assert report["actions"][0]["payload"]["sha256"] == hashlib.sha256(
        payload.read_bytes()
    ).hexdigest()

    RpfChangeSet.move(change_set, mkdir, 1)
    assert RpfChangeSet.describe(change_set)["actions"][0]["id"] == mkdir
    RpfChangeSet.remove(change_set, rename)
    assert [
        item["id"] for item in RpfChangeSet.describe(change_set)["actions"]
    ] == [mkdir, replace]

    service = FakeService(index)
    plan_path, plan = RpfChangeSet.compile_plan(
        change_set, service, tmp_path / "atomic-plan.json",
    )
    assert plan_path.is_file() and plan["status"] == "ready"
    assert [item["action"] for item in service.authored] == ["mkdir", "replace"]
    assert service.authored[1]["payload"] == str(payload.resolve())
    assert plan["change_set"]["action_ids"] == [mkdir, replace]
    assert index.source.read_bytes() == b"RPF7 source archive"


def test_rpf_change_set_rejects_drift_bad_actions_and_game_outputs(tmp_path):
    index = _index(tmp_path)
    change_set = RpfChangeSet.create(index, tmp_path / "changes.json")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    with pytest.raises(ValueError, match="requires a payload"):
        RpfChangeSet.stage(change_set, "add", "new.bin")
    with pytest.raises(ValueError, match="cannot include a payload"):
        RpfChangeSet.stage(change_set, "delete", "old.bin", payload=payload)
    with pytest.raises(ValueError, match="requires new_entry"):
        RpfChangeSet.stage(change_set, "rename", "old.bin")
    with pytest.raises(ValueError, match="position starts at 1"):
        RpfChangeSet.move(change_set, "change-missing", 0)
    RpfChangeSet.stage(change_set, "add", "new.bin", payload=payload)
    payload.write_bytes(b"drift")
    with pytest.raises(ValueError, match="payload changed"):
        RpfChangeSet.describe(change_set, verify_files=True)

    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="stored outside GTA V"):
        RpfChangeSet.create(index, game / "changes.json")


def test_rpf_change_set_rejects_archive_and_document_drift(tmp_path):
    index = _index(tmp_path)
    change_set = RpfChangeSet.create(index, tmp_path / "changes.json")
    RpfChangeSet.stage(change_set, "mkdir", "new")
    index.source.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="source archive changed"):
        RpfChangeSet.describe(change_set, verify_files=True)

    index.source.write_bytes(b"RPF7 source archive")

    class MutatingService(FakeService):
        def multi_change_plan(self, loaded, authored):
            plan = super().multi_change_plan(loaded, authored)
            change_set.write_text(
                change_set.read_text(encoding="utf-8") + " ", encoding="utf-8",
            )
            return plan

    service = MutatingService(index)
    with pytest.raises(ValueError, match="changed while compiling"):
        RpfChangeSet.compile_plan(change_set, service, tmp_path / "plan.json")
    assert not (tmp_path / "plan.json").exists()


def test_rpf_change_set_cli_sdk_alias_and_agent_risk(tmp_path, monkeypatch):
    index = _index(tmp_path)
    service = FakeService(index)
    monkeypatch.setattr("allin1_sdk.cli._rpf_service", lambda *_args: service)
    change_set = tmp_path / "changes.json"
    runner = CliRunner()
    created = runner.invoke(main, [
        "sdk", "create-rpf-change-set", str(index.source),
        "--output", str(change_set),
    ])
    assert created.exit_code == 0, created.output
    staged = runner.invoke(main, [
        "stage-rpf-change", str(change_set), "mkdir", "common/new",
        "--acknowledge-edit",
    ])
    assert staged.exit_code == 0, staged.output
    action_id = staged.output.split(": ", 1)[1].split(" ", 1)[0]
    moved = runner.invoke(main, [
        "move-rpf-change", str(change_set), action_id, "1", "--acknowledge-edit",
    ])
    assert moved.exit_code == 0, moved.output
    inspected = runner.invoke(main, [
        "inspect-rpf-change-set", str(change_set), "--verify-files",
    ])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["summary"]["actions"] == 1
    planned = runner.invoke(main, [
        "plan-rpf-change-set", str(change_set),
        "--output", str(tmp_path / "plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    removed = runner.invoke(main, [
        "unstage-rpf-change", str(change_set), action_id, "--acknowledge-edit",
    ])
    assert removed.exit_code == 0, removed.output

    catalog = {item["name"]: item for item in command_catalog()}
    for command in (
        "create-rpf-change-set", "stage-rpf-change", "move-rpf-change",
        "unstage-rpf-change", "plan-rpf-change-set",
    ):
        assert catalog[command]["risk"] == "authoring_write"
    assert catalog["inspect-rpf-change-set"]["risk"] == "read_only"


def test_rpf_change_set_desktop_is_embedded_in_explorer_workspace():
    root = Path(__file__).parents[1] / "src" / "allin1_sdk"
    ui = (root / "rpf_change_set_ui.py").read_text(encoding="utf-8")
    explorer = (root / "rpf_explorer.py").read_text(encoding="utf-8")
    assert "class RpfChangeSetFrame" in ui
    assert "RpfChangeSet.stage" in ui
    assert "RpfChangeSet.compile_plan" in ui
    assert 'self.workspace_tabs.add(changes_tab, text="Visual Change Set")' in explorer
    assert "self.change_set_frame = RpfChangeSetFrame(" in explorer


def test_rpf_change_set_rejects_malformed_documents(tmp_path):
    index = _index(tmp_path)
    change_set = RpfChangeSet.create(index, tmp_path / "base.json")
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(b"payload")
    RpfChangeSet.stage(change_set, "add", "new.bin", payload=payload_file)
    base = json.loads(change_set.read_text(encoding="utf-8"))

    def changed(mutator, name):
        document = json.loads(json.dumps(base))
        mutator(document)
        path = tmp_path / f"bad-{name}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    cases = [
        (lambda item: item.update(schema_version=99), "schema", "Unsupported"),
        (lambda item: item.update(archive=[]), "archive", "archive metadata"),
        (lambda item: item["archive"].update(path=""), "archive-path", "archive path"),
        (lambda item: item["archive"].update(path="relative.rpf"), "relative-archive", "must be absolute"),
        (lambda item: item["archive"].update(edition=""), "edition", "archive edition"),
        (lambda item: item["archive"].update(size=-1), "size", "archive size"),
        (lambda item: item["archive"].update(sha256="z" * 64), "archive-sha", "archive SHA"),
        (lambda item: item.update(actions=[None]), "action-object", "must be an object"),
        (lambda item: item["actions"][0].update(id="bad id"), "action-id", "action id"),
        (lambda item: item["actions"].append(dict(item["actions"][0])), "duplicate", "Duplicate"),
        (lambda item: item["actions"][0].update(action="execute"), "action", "Unsupported"),
        (lambda item: item["actions"][0].update(entry=None), "entry", "must be text"),
        (lambda item: item["actions"][0].update(payload=None), "payload", "requires a payload"),
        (lambda item: item["actions"][0]["payload"].update(path=""), "payload-path", "payload path"),
        (lambda item: item["actions"][0]["payload"].update(path="relative.bin"), "relative-payload", "must be absolute"),
        (lambda item: item["actions"][0]["payload"].update(size=-1), "payload-size", "payload size"),
        (lambda item: item["actions"][0]["payload"].update(sha256="no"), "payload-sha", "payload SHA"),
        (lambda item: item["actions"][0].update(action="rename", payload=None), "rename-target", "must be text"),
        (lambda item: item["actions"][0].update(action="delete", payload=None, new_entry="other"), "delete-target", "cannot have new_entry"),
    ]
    for mutate, name, message in cases:
        with pytest.raises(ValueError, match=message):
            RpfChangeSet.validate(changed(mutate, name))

    overflow = json.loads(json.dumps(base))
    overflow["actions"] = [dict(base["actions"][0]) for _ in range(1001)]
    overflow_path = tmp_path / "overflow.json"
    overflow_path.write_text(json.dumps(overflow), encoding="utf-8")
    with pytest.raises(ValueError, match="action limit"):
        RpfChangeSet.validate(overflow_path)
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid RPF change-set JSON"):
        RpfChangeSet.validate(invalid_json)
    array_json = tmp_path / "array.json"
    array_json.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        RpfChangeSet.validate(array_json)


def test_rpf_change_set_guarded_create_edit_and_plan_failures(tmp_path):
    index = _index(tmp_path)
    with pytest.raises(FileNotFoundError, match="source archive not found"):
        RpfChangeSet.create(
            replace(index, source=tmp_path / "missing.rpf"), tmp_path / "missing.json",
        )
    with pytest.raises(ValueError, match="changed after indexing"):
        RpfChangeSet.create(
            replace(index, archive_size=index.archive_size + 1), tmp_path / "stale.json",
        )
    with pytest.raises(ValueError, match=".json extension"):
        RpfChangeSet.create(index, tmp_path / "changes.txt")
    change_set = RpfChangeSet.create(index, tmp_path / "changes.json")
    with pytest.raises(FileExistsError, match="already exists"):
        RpfChangeSet.create(index, change_set)
    with pytest.raises(FileNotFoundError, match="change set not found"):
        RpfChangeSet.describe(tmp_path / "absent.json")
    with pytest.raises(ValueError, match="Unsupported"):
        RpfChangeSet.stage(change_set, "execute", "entry.bin")
    with pytest.raises(FileNotFoundError, match="payload not found"):
        RpfChangeSet.stage(change_set, "add", "entry.bin", payload=tmp_path / "none")
    with pytest.raises(ValueError, match="cannot include new_entry"):
        RpfChangeSet.stage(change_set, "mkdir", "folder", new_entry="other")
    with pytest.raises(ValueError, match="action not found"):
        RpfChangeSet.remove(change_set, "change-missing")
    with pytest.raises(ValueError, match="action not found"):
        RpfChangeSet.move(change_set, "change-missing", 1)
    service = FakeService(index)
    with pytest.raises(ValueError, match="no staged actions"):
        RpfChangeSet.compile_plan(change_set, service, tmp_path / "empty-plan.json")
    RpfChangeSet.stage(change_set, "mkdir", "folder")
    with pytest.raises(ValueError, match=".json extension"):
        RpfChangeSet.compile_plan(change_set, service, tmp_path / "plan.txt")
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="plans must be stored outside GTA V"):
        RpfChangeSet.compile_plan(change_set, service, game / "plan.json")
    mismatched = FakeService(replace(index, edition="enhanced"))
    with pytest.raises(ValueError, match="index no longer matches"):
        RpfChangeSet.compile_plan(change_set, mismatched, tmp_path / "mismatch.json")
