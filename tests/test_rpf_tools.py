from __future__ import annotations

import hashlib
import json
import base64
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk import native_assets, rpf_tools
from allin1_sdk.cli import main
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    NativeAssetInspector,
    native_preview_limit,
)
from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex


def _index_payload(source: Path, *, nested: bool = True) -> dict:
    archives = [{
        "path": "", "name": source.name, "version": 7,
        "encryption": "OPEN", "size": 900, "entry_count": 4,
    }]
    entries = [
        {
            "id": "::common", "archive_path": "", "path": "common",
            "name": "common", "kind": "directory", "size": 0,
            "stored_size": 0, "name_hash": 1, "short_name_hash": 1,
            "child_count": 2,
        },
        {
            "id": "::common/data/test.ymap", "archive_path": "",
            "path": "common/data/test.ymap", "name": "test.ymap",
            "kind": "resource", "size": 4096, "stored_size": 1024,
            "name_hash": 2, "short_name_hash": 3, "offset": 512,
            "encrypted": False, "resource_version": 2,
            "system_size": 3072, "graphics_size": 1024,
            "system_flags": "0x00000001", "graphics_flags": "0x00000002",
        },
        {
            "id": "::x64/textures.rpf", "archive_path": "",
            "path": "x64/textures.rpf", "name": "textures.rpf",
            "kind": "archive", "size": 500, "stored_size": 400,
            "name_hash": 4, "short_name_hash": 5, "compressed": True,
        },
    ]
    if nested:
        archives.append({
            "path": "x64/textures.rpf", "name": "textures.rpf", "version": 7,
            "encryption": "OPEN", "size": 500, "entry_count": 1,
        })
        entries.append({
            "id": "x64/textures.rpf::vehicle.ytd",
            "archive_path": "x64/textures.rpf", "path": "vehicle.ytd",
            "name": "vehicle.ytd", "kind": "resource", "size": 8192,
            "stored_size": 2048, "resource_version": 13,
            "name_hash": 6, "short_name_hash": 7,
        })
    return {
        "schema_version": 1, "source": str(source.resolve()),
        "edition": "Enhanced", "archive_size": 900,
        "archives": archives, "entries": entries,
        "warnings": ["test warning"],
    }


def _write_index(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "index.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_rpf_index_load_search_summary_and_export(tmp_path):
    source = tmp_path / "dlc.rpf"
    source.write_bytes(b"RPF7")
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(source)))

    assert index.source == source
    assert index.edition == "Enhanced"
    assert index.entry("X64/TEXTURES.RPF::VEHICLE.YTD").resource_version == 13
    assert index.search("vehicle")[0].virtual_name == "x64/textures.rpf::vehicle.ytd"
    assert [item.path for item in index.search(kinds=("RESOURCE",), suffix="ymap")] == [
        "common/data/test.ymap"
    ]
    assert index.suffix_counts() == {".ymap": 1, ".rpf": 1, ".ytd": 1}
    json_path, csv_path = index.export(tmp_path / "reports" / "archive")
    assert json_path.name == "archive.json" and csv_path.name == "archive.csv"
    assert '"resource_version": 13' in json_path.read_text(encoding="utf-8")
    assert "vehicle.ytd" in csv_path.read_text(encoding="utf-8-sig")
    with pytest.raises(KeyError, match="Unknown RPF entry"):
        index.entry("missing")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"schema_version": 2}, "schema"),
        ({"entries": []}, "Duplicate"),
        ({"entries": [{
            "id": "bad", "archive_path": "", "path": "../escape.bin",
            "name": "escape.bin", "kind": "binary", "size": 1, "stored_size": 1,
        }]}, "Unsafe"),
    ],
)
def test_rpf_index_rejects_unsupported_duplicate_and_unsafe_data(tmp_path, change, message):
    source = tmp_path / "test.rpf"
    payload = _index_payload(source, nested=False)
    if change.get("entries") == []:
        payload["entries"].append(dict(payload["entries"][0]))
    else:
        payload.update(change)
    with pytest.raises(ValueError, match=message):
        RpfIndex.load(_write_index(tmp_path, payload))


def test_rpf_index_rejects_invalid_json_and_missing_fields(tmp_path):
    invalid = tmp_path / "bad.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid RPF index"):
        RpfIndex.load(invalid)
    invalid.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed RPF index"):
        RpfIndex.load(invalid)


@pytest.mark.parametrize("field,value,message", [
    ("id", "forged", "does not match"),
    ("archive_path", "missing.rpf", "unknown archive"),
    ("kind", "executable", "Unknown RPF entry kind"),
    ("size", -1, "Negative RPF entry size"),
])
def test_rpf_index_rejects_forged_entry_contracts(tmp_path, field, value, message):
    source = tmp_path / "test.rpf"
    payload = _index_payload(source, nested=False)
    payload["entries"][1][field] = value
    if field == "archive_path":
        payload["entries"][1]["id"] = f"{value}::{payload['entries'][1]['path']}"
    with pytest.raises(ValueError, match=message):
        RpfIndex.load(_write_index(tmp_path, payload))


def _service(tmp_path: Path) -> tuple[RpfExplorerService, Path, Path]:
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    return RpfExplorerService(project, game), archive, patcher


def test_running_gta_process_detection_and_query_failure(monkeypatch):
    if rpf_tools.os.name != "nt":
        pytest.skip("tasklist process guard is Windows-specific")
    monkeypatch.setattr(
        rpf_tools, "run_hidden",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='"GTA5_Enhanced.exe","123","Console","1","2,000 K"\n'
                   '"explorer.exe","456","Console","1","4,000 K"\n',
            stderr="",
        ),
    )
    assert rpf_tools._running_gta_processes() == ("gta5_enhanced.exe",)

    monkeypatch.setattr(
        rpf_tools, "run_hidden",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="tasklist unavailable",
        ),
    )
    with pytest.raises(RuntimeError, match="Could not verify.*tasklist unavailable"):
        rpf_tools._running_gta_processes()


def test_rpf_service_indexes_extracts_and_builds_plan(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)

    def fake_run(args, **_kwargs):
        if args[1] == "index-json":
            Path(args[4]).write_text(json.dumps(_index_payload(archive)), encoding="utf-8")
        elif args[1] == "extract-virtual-entry":
            Path(args[6]).write_bytes(b"native payload")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    index = service.index(archive)
    entry = index.entry("x64/textures.rpf::vehicle.ytd")
    extracted = service.extract(index, entry, tmp_path / "out" / "vehicle.ytd")
    assert extracted.read_bytes() == b"native payload"

    replacement = tmp_path / "new.ytd"
    replacement.write_bytes(b"replacement")
    plan = service.replacement_plan(index, entry, replacement)
    assert plan["schema_version"] == 3
    assert plan["operation"] == "rpf_entry_change"
    assert plan["action"] == "replace"
    assert plan["status"] == "blocked"
    assert plan["payload"]["sha256"] == hashlib.sha256(b"replacement").hexdigest()
    assert plan["original"]["sha256"] == hashlib.sha256(b"native payload").hexdigest()
    assert len(plan["blocking_reasons"]) == 1
    assert plan["safety"]["writes_performed"] is False


def _encode_archive(entries: dict[str, bytes]) -> bytes:
    return b"RPF7" + json.dumps({
        path: base64.b64encode(data).decode("ascii") for path, data in entries.items()
    }, sort_keys=True).encode("utf-8")


def _decode_archive(path: Path) -> dict[str, bytes]:
    raw = path.read_bytes()
    assert raw.startswith(b"RPF7")
    return _decode_archive_bytes(raw)


def _decode_archive_bytes(raw: bytes) -> dict[str, bytes]:
    assert raw.startswith(b"RPF7")
    return {
        name: base64.b64decode(value)
        for name, value in json.loads(raw[4:].decode("utf-8")).items()
    }


def _dynamic_index(source: Path) -> dict:
    root_entries = _decode_archive(source)
    archives = [{
        "path": "", "name": source.name, "version": 7,
        "encryption": "OPEN", "size": source.stat().st_size,
        "entry_count": len(root_entries),
    }]
    entries: list[dict] = []

    def add_directory(archive_path: str, path: str) -> None:
        entry_id = f"{archive_path}::{path}"
        if any(item["id"] == entry_id for item in entries):
            return
        entries.append({
            "id": entry_id, "archive_path": archive_path, "path": path,
            "name": Path(path).name, "kind": "directory", "size": 0,
            "stored_size": 0, "child_count": 1,
        })

    def add_file(archive_path: str, path: str, data: bytes) -> None:
        parent = Path(path).parent.as_posix()
        parts = []
        if parent != ".":
            for part in parent.split("/"):
                parts.append(part)
                add_directory(archive_path, "/".join(parts))
        kind = "archive" if path.casefold().endswith(".rpf") else "resource"
        entries.append({
            "id": f"{archive_path}::{path}", "archive_path": archive_path,
            "path": path, "name": Path(path).name, "kind": kind,
            "size": len(data), "stored_size": len(data),
        })

    def index_archive(state: dict[str, bytes], archive_path: str) -> None:
        for path, data in state.items():
            add_file(archive_path, path, data)
            if not path.casefold().endswith(".rpf"):
                continue
            nested_entries = _decode_archive_bytes(data)
            nested_path = path if not archive_path else f"{archive_path}!{path}"
            archives.append({
                "path": nested_path, "name": Path(path).name, "version": 7,
                "encryption": "OPEN", "size": len(data),
                "entry_count": len(nested_entries),
            })
            index_archive(nested_entries, nested_path)

    index_archive(root_entries, "")
    return {
        "schema_version": 1, "source": str(source.resolve()),
        "edition": "Enhanced", "archive_size": source.stat().st_size,
        "archives": archives, "entries": entries, "warnings": [],
    }


def _fake_archive_runner(args, **_kwargs):
    command = args[1]
    if command == "index-json":
        source = Path(args[3])
        Path(args[4]).write_text(json.dumps(_dynamic_index(source)), encoding="utf-8")
    elif command == "extract-virtual-entry":
        source = Path(args[3])
        archive_path = str(args[4])
        entry_path = str(args[5])
        state = _decode_archive(source)
        if archive_path:
            for nested_entry_path in archive_path.split("!"):
                state = _decode_archive_bytes(state[nested_entry_path])
        Path(args[6]).write_bytes(state[entry_path])
    elif command == "replace-entry":
        source = Path(args[3])
        state = _decode_archive(source)
        state[str(args[4]).replace("\\", "/")] = Path(args[5]).read_bytes()
        source.write_bytes(_encode_archive(state))
    elif command == "delete-entry":
        source = Path(args[3])
        state = _decode_archive(source)
        state.pop(str(args[4]).replace("\\", "/"), None)
        source.write_bytes(_encode_archive(state))
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _transaction_service(
    tmp_path, monkeypatch, *, nested: bool = False, deep_nested: bool = False,
):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    game = tmp_path / "game"
    archive = game / "mods" / "update" / "test.rpf"
    archive.parent.mkdir(parents=True)
    entries = {"common/data/test.ymap": b"original entry"}
    if deep_nested:
        entries["x64/textures.rpf"] = _encode_archive({
            "archives/level2.rpf": _encode_archive({
                "deep/level3.rpf": _encode_archive({
                    "assets/target.ytd": b"original deeply nested texture",
                }),
            }),
        })
    elif nested:
        entries["x64/textures.rpf"] = _encode_archive({
            "vehicle.ytd": b"original nested texture",
        })
    archive.write_bytes(_encode_archive(entries))

    monkeypatch.setattr(rpf_tools, "run_hidden", _fake_archive_runner)
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ())
    service = RpfExplorerService(project, game)
    index = service.index(archive)
    entry = index.entry("::common/data/test.ymap")
    return service, archive, entry, _fake_archive_runner


def _ready_plan(tmp_path, monkeypatch):
    service, archive, entry, fake_run = _transaction_service(tmp_path, monkeypatch)
    payload = tmp_path / "replacement.ymap"
    payload.write_bytes(b"replacement entry")
    plan = service.replacement_plan(service.index(archive), entry, payload)
    assert plan["status"] == "ready"
    plan_path = tmp_path / "replacement-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return service, archive, payload, plan_path, fake_run


def test_rpf_transaction_applies_verifies_and_rolls_back(tmp_path, monkeypatch):
    service, archive, _payload, plan_path, _fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()
    receipt = service.apply_replacement_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )

    applied = json.loads(receipt.read_text(encoding="utf-8"))
    assert applied["status"] == "applied"
    assert _decode_archive(archive)["common/data/test.ymap"] == b"replacement entry"
    assert Path(applied["backup"]["path"]).read_bytes() == original
    assert Path(applied["payload"]["snapshot"]).read_bytes() == b"replacement entry"
    assert service.verify_transaction(receipt) == {
        "healthy": True,
        "archive_state": "applied",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "backup_valid": True,
        "entry_valid": True,
    }

    assert service.rollback_transaction(receipt) == receipt
    assert archive.read_bytes() == original
    rolled_back = json.loads(receipt.read_text(encoding="utf-8"))
    assert rolled_back["status"] == "rolled_back"
    assert service.verify_transaction(receipt)["archive_state"] == "original"


def test_rpf_transaction_recovers_interrupted_post_staging_receipt(
    tmp_path, monkeypatch,
):
    service, archive, _payload, plan_path, _fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()
    receipt = service.apply_replacement_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    saved["status"] = "verified_staging"
    receipt.write_text(json.dumps(saved), encoding="utf-8")

    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "rolled_back"


def test_rpf_transaction_refuses_stale_payload_running_game_and_external_change(
    tmp_path, monkeypatch,
):
    service, archive, payload, plan_path, _fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()
    payload.write_bytes(b"changed after review")
    with pytest.raises(RuntimeError, match="payload size changed|payload changed"):
        service.apply_replacement_plan(plan_path, receipt_root=tmp_path / "stale")
    assert archive.read_bytes() == original

    payload.write_bytes(b"replacement entry")
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("gta5.exe",))
    with pytest.raises(RuntimeError, match="Close GTA V"):
        service.apply_replacement_plan(plan_path, receipt_root=tmp_path / "running")
    assert archive.read_bytes() == original

    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ())
    receipt = service.apply_replacement_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    archive.write_bytes(archive.read_bytes() + b"external")
    with pytest.raises(RuntimeError, match="changed after this transaction"):
        service.rollback_transaction(receipt)
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "applied"


def test_rpf_transaction_staged_failure_never_changes_live_archive(tmp_path, monkeypatch):
    service, archive, _payload, plan_path, fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()

    def fail_write(args, **kwargs):
        if args[1] == "replace-entry":
            return SimpleNamespace(returncode=9, stdout="", stderr="simulated write failure")
        return fake_run(args, **kwargs)

    monkeypatch.setattr(rpf_tools, "run_hidden", fail_write)
    root = tmp_path / "transactions"
    with pytest.raises(RuntimeError, match="failed_before_commit"):
        service.apply_replacement_plan(plan_path, receipt_root=root)
    assert archive.read_bytes() == original
    receipts = list(root.glob("*/receipt.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["status"] == (
        "failed_before_commit"
    )
    assert not list(archive.parent.glob(".allin1-stage-*"))


def test_rpf_transaction_refuses_concurrent_archive_owner(tmp_path, monkeypatch):
    service, archive, _payload, plan_path, _fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()
    lock = archive.with_name(f".{archive.name}.allin1.lock")
    lock.write_text("existing transaction", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Another ALLIN1 RPF transaction"):
        service.apply_replacement_plan(plan_path, receipt_root=tmp_path / "transactions")
    assert archive.read_bytes() == original
    assert lock.read_text(encoding="utf-8") == "existing transaction"


def test_rpf_transaction_post_commit_failure_restores_full_snapshot(
    tmp_path, monkeypatch,
):
    service, archive, _payload, plan_path, fake_run = _ready_plan(tmp_path, monkeypatch)
    original = archive.read_bytes()

    def fail_live_verification(args, **kwargs):
        if (args[1] == "extract-virtual-entry" and Path(args[3]) == archive
                and _decode_archive(archive).get("common/data/test.ymap")
                == b"replacement entry"):
            Path(args[6]).write_bytes(b"corrupt post-commit extraction")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return fake_run(args, **kwargs)

    monkeypatch.setattr(rpf_tools, "run_hidden", fail_live_verification)
    root = tmp_path / "transactions"
    with pytest.raises(RuntimeError, match="rolled_back_after_failure"):
        service.apply_replacement_plan(plan_path, receipt_root=root)
    assert archive.read_bytes() == original
    receipt = next(root.glob("*/receipt.json"))
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved["status"] == "rolled_back_after_failure"
    assert Path(saved["backup"]["path"]).read_bytes() == original


def test_rpf_transaction_rejects_blocked_or_tampered_plan(tmp_path, monkeypatch):
    service, archive, payload, plan_path, _fake_run = _ready_plan(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["status"] = "blocked"
    plan["blocking_reasons"] = ["test blocker"]
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="test blocker"):
        service.apply_replacement_plan(plan_path, receipt_root=tmp_path / "blocked")

    plan = service.replacement_plan(service.index(archive), service.index(archive).entry(
        "::common/data/test.ymap"
    ), payload)
    plan["entry"] = "common/data/other.ymap"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        service.apply_replacement_plan(plan_path, receipt_root=tmp_path / "tampered")


def test_rpf_transaction_add_and_delete_are_verified_and_reversible(tmp_path, monkeypatch):
    service, archive, entry, _fake = _transaction_service(tmp_path, monkeypatch)
    payload = tmp_path / "new.ymap"
    payload.write_bytes(b"new map payload")
    index = service.index(archive)
    add = service.addition_plan(index, "common/data/new.ymap", payload)
    assert add["status"] == "ready" and add["action"] == "add"
    add_path = tmp_path / "add.json"
    add_path.write_text(json.dumps(add), encoding="utf-8")
    receipt = service.apply_change_plan(add_path, receipt_root=tmp_path / "transactions")
    assert _decode_archive(archive)["common/data/new.ymap"] == b"new map payload"
    assert service.verify_transaction(receipt)["healthy"] is True
    service.rollback_transaction(receipt)
    assert "common/data/new.ymap" not in _decode_archive(archive)

    index = service.index(archive)
    delete = service.deletion_plan(index, index.entry(entry.id))
    delete_path = tmp_path / "delete.json"
    delete_path.write_text(json.dumps(delete), encoding="utf-8")
    receipt = service.apply_change_plan(delete_path, receipt_root=tmp_path / "transactions")
    assert "common/data/test.ymap" not in _decode_archive(archive)
    assert service.verify_transaction(receipt)["archive_state"] == "applied"
    service.rollback_transaction(receipt)
    assert _decode_archive(archive)["common/data/test.ymap"] == b"original entry"


def test_nested_rpf_replace_add_delete_use_outer_transaction(tmp_path, monkeypatch):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, nested=True,
    )
    original_outer = archive.read_bytes()
    index = service.index(archive)
    nested = index.entry("x64/textures.rpf::vehicle.ytd")
    payload = tmp_path / "vehicle.ytd"
    payload.write_bytes(b"replacement nested texture")
    replace = service.replacement_plan(index, nested, payload)
    assert replace["status"] == "ready"
    assert replace["archive_path"] == "x64/textures.rpf"
    path = tmp_path / "nested-replace.json"
    path.write_text(json.dumps(replace), encoding="utf-8")
    receipt = service.apply_change_plan(path, receipt_root=tmp_path / "transactions")
    nested_state = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    assert nested_state["vehicle.ytd"] == b"replacement nested texture"
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original_outer

    index = service.index(archive)
    addition = service.addition_plan(
        index, "extra.ytd", payload, archive_path="x64/textures.rpf",
    )
    path.write_text(json.dumps(addition), encoding="utf-8")
    receipt = service.apply_change_plan(path, receipt_root=tmp_path / "transactions")
    nested_state = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    assert nested_state["extra.ytd"] == b"replacement nested texture"
    service.rollback_transaction(receipt)

    index = service.index(archive)
    deletion = service.deletion_plan(
        index, index.entry("x64/textures.rpf::vehicle.ytd"),
    )
    path.write_text(json.dumps(deletion), encoding="utf-8")
    receipt = service.apply_change_plan(path, receipt_root=tmp_path / "transactions")
    nested_state = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    assert "vehicle.ytd" not in nested_state
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original_outer


def test_deep_nested_rpf_replace_add_delete_reassemble_every_parent(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, deep_nested=True,
    )
    original_outer = archive.read_bytes()
    archive_path = (
        "x64/textures.rpf!archives/level2.rpf!deep/level3.rpf"
    )
    target_id = f"{archive_path}::assets/target.ytd"
    payload = tmp_path / "target.ytd"
    payload.write_bytes(b"replacement deeply nested texture")
    plan_path = tmp_path / "deep-change.json"

    replacement = service.replacement_plan(
        service.index(archive), service.index(archive).entry(target_id), payload,
    )
    assert replacement["status"] == "ready"
    assert replacement["archive_path"] == archive_path
    plan_path.write_text(json.dumps(replacement), encoding="utf-8")
    receipt = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    level1 = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    level2 = _decode_archive_bytes(level1["archives/level2.rpf"])
    level3 = _decode_archive_bytes(level2["deep/level3.rpf"])
    assert level3["assets/target.ytd"] == payload.read_bytes()
    assert service.verify_transaction(receipt)["healthy"] is True
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original_outer

    addition = service.addition_plan(
        service.index(archive), "assets/extra.ytd", payload,
        archive_path=archive_path,
    )
    plan_path.write_text(json.dumps(addition), encoding="utf-8")
    receipt = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    level1 = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    level2 = _decode_archive_bytes(level1["archives/level2.rpf"])
    level3 = _decode_archive_bytes(level2["deep/level3.rpf"])
    assert level3["assets/extra.ytd"] == payload.read_bytes()
    service.rollback_transaction(receipt)

    deletion = service.deletion_plan(
        service.index(archive), service.index(archive).entry(target_id),
    )
    plan_path.write_text(json.dumps(deletion), encoding="utf-8")
    receipt = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    level1 = _decode_archive_bytes(_decode_archive(archive)["x64/textures.rpf"])
    level2 = _decode_archive_bytes(level1["archives/level2.rpf"])
    level3 = _decode_archive_bytes(level2["deep/level3.rpf"])
    assert "assets/target.ytd" not in level3
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original_outer


def test_workspace_authorization_history_recovery_and_stale_lock(tmp_path, monkeypatch):
    service, archive, entry, _fake = _transaction_service(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = workspace / "copy.rpf"
    external.write_bytes(archive.read_bytes())
    default = RpfExplorerService(service.project_root, service.gta_path)
    payload = tmp_path / "payload.ymap"
    payload.write_bytes(b"workspace replacement")
    blocked = default.replacement_plan(
        default.index(external), default.index(external).entry(entry.id), payload,
    )
    assert blocked["status"] == "blocked"
    authorized = RpfExplorerService(
        service.project_root, service.gta_path, workspace_roots=(workspace,),
    )
    plan = authorized.replacement_plan(
        authorized.index(external), authorized.index(external).entry(entry.id), payload,
    )
    assert plan["target_scope"] == "workspace_copy" and plan["status"] == "ready"
    plan_path = tmp_path / "workspace-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    transactions = tmp_path / "history"
    receipt = authorized.apply_change_plan(plan_path, receipt_root=transactions)
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    saved["status"] = "verified_staging"
    receipt.write_text(json.dumps(saved), encoding="utf-8")
    assert authorized.recover_transaction(receipt)["archive_state"] == "applied"
    history = authorized.list_transactions(transactions)
    assert history[0]["valid"] is True and history[0]["action"] == "replace"

    lock = external.with_name(f".{external.name}.allin1.lock")
    lock.write_text(json.dumps({
        "pid": 987654, "plan_id": plan["plan_id"], "created_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(authorized, "_pid_is_running", lambda _pid: False)
    assert authorized.inspect_archive_lock(external)["process_running"] is False
    authorized.clear_stale_lock(external)
    assert not lock.exists()
    authorized.rollback_transaction(receipt)

    with pytest.raises(ValueError, match="stock GTA V files"):
        RpfExplorerService(
            service.project_root, service.gta_path,
            workspace_roots=(service.gta_path / "update",),
        )


def test_real_archive_canary_changes_only_disposable_copy(tmp_path, monkeypatch):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    source = tmp_path / "source.rpf"
    source.write_bytes(archive.read_bytes())
    original = source.read_bytes()
    report_path = service.run_canary(source, output_root=tmp_path / "canaries")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["writes_to_source"] is False
    assert report["rollback_verification"]["archive_state"] == "original"
    assert source.read_bytes() == original


def test_entry_plan_constraints_and_internal_action_guards(tmp_path, monkeypatch):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, nested=True,
    )
    index = service.index(archive)
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    with pytest.raises(ValueError, match="already exists"):
        service.addition_plan(index, "common/data/test.ymap", payload)
    with pytest.raises(ValueError, match="target directory"):
        service.addition_plan(index, "missing/path/new.bin", payload)
    with pytest.raises(ValueError, match="Nested RPF chain"):
        service.addition_plan(index, "new.bin", payload, archive_path="missing.rpf")
    with pytest.raises(ValueError, match="limited to 8"):
        service.addition_plan(
            index, "new.bin", payload,
            archive_path="!".join(f"level-{number}.rpf" for number in range(9)),
        )
    with pytest.raises(ValueError, match="Directories"):
        service.deletion_plan(index, index.entry("::common"))
    with pytest.raises(ValueError, match="Directories"):
        service.deletion_plan(index, index.entry("::x64/textures.rpf"))
    with pytest.raises(ValueError, match="Unsupported RPF action"):
        service._entry_change_plan(index, "move", "", "new.bin", None, None)
    with pytest.raises(ValueError, match="requires a payload"):
        service._entry_change_plan(index, "add", "", "new.bin", None, None)
    with pytest.raises(ValueError, match="do not accept"):
        service._entry_change_plan(
            index, "delete", "", "common/data/test.ymap", payload,
            index.entry("::common/data/test.ymap"),
        )


def test_plan_and_receipt_contract_validation_branches(tmp_path, monkeypatch):
    service, archive, payload, plan_path, _fake = _ready_plan(tmp_path, monkeypatch)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutations = [
        ({"schema_version": 99}, "Unsupported"),
        ({"operation": "other"}, "operation"),
        ({"action": "move"}, "invalid action"),
        ({"status": "blocked", "blocking_reasons": ["reason"]}, "reason"),
        ({"original": None}, "original metadata"),
        ({"payload": None}, "payload metadata"),
        ({"edition": ""}, "edition"),
        ({"archive_sha256": "bad"}, "SHA-256"),
        ({"archive_size": -1}, "archive_size"),
    ]
    for authored, message in mutations:
        changed = json.loads(json.dumps(plan))
        changed.update(authored)
        with pytest.raises((ValueError, FileNotFoundError), match=message):
            service._validate_plan(changed)

    receipt_path = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "receipts",
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_mutations = [
        ({"schema_version": 99}, "Unsupported"),
        ({"operation": "other"}, "operation"),
        ({"action": "move"}, "invalid action"),
        ({"original": None}, "original"),
        ({"payload": None}, "payload"),
        ({"entry": ""}, "missing entry"),
        ({"plan_id": "bad"}, "SHA-256"),
        ({"applied_archive_sha256": "bad"}, "applied hash"),
        ({"transaction_id": "../escape"}, "unsafe transaction"),
    ]
    for authored, message in receipt_mutations:
        changed = json.loads(json.dumps(receipt))
        changed.update(authored)
        with pytest.raises(ValueError, match=message):
            service._validate_receipt(changed)

    delete_receipt = json.loads(json.dumps(receipt))
    delete_receipt["action"] = "delete"
    with pytest.raises(ValueError, match="unexpectedly contains"):
        service._validate_receipt(delete_receipt)
    negative = json.loads(json.dumps(receipt))
    negative["backup"]["size"] = -1
    with pytest.raises(ValueError, match="invalid size"):
        service._validate_receipt(negative)
    bad_state = json.loads(json.dumps(receipt))
    bad_state["original"]["exists"] = "yes"
    with pytest.raises(ValueError, match="original state"):
        service._validate_receipt(bad_state)


def test_history_recovery_lock_and_verification_attention_paths(tmp_path, monkeypatch):
    service, archive, _payload, plan_path, _fake = _ready_plan(tmp_path, monkeypatch)
    transactions = tmp_path / "transactions"
    receipt = service.apply_change_plan(plan_path, receipt_root=transactions)
    saved = json.loads(receipt.read_text(encoding="utf-8"))
    backup = Path(saved["backup"]["path"])
    archive.write_bytes(backup.read_bytes())
    saved["status"] = "verified_staging"
    receipt.write_text(json.dumps(saved), encoding="utf-8")
    recovered = service.recover_transaction(receipt)
    assert recovered["archive_state"] == "original"
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == (
        "interrupted_before_commit"
    )

    malformed_dir = transactions / "malformed"
    malformed_dir.mkdir()
    (malformed_dir / "receipt.json").write_text("{", encoding="utf-8")
    history = service.list_transactions(transactions)
    assert any(item["valid"] is False for item in history)
    assert service.list_transactions(tmp_path / "missing-history") == ()

    archive.write_bytes(b"externally modified")
    attention = service.verify_transaction(receipt)
    assert attention["healthy"] is False
    assert attention["archive_state"] == "modified_externally"
    archive.unlink()
    assert service.verify_transaction(receipt)["archive_state"] == "missing"

    archive.write_bytes(backup.read_bytes())
    assert service.inspect_archive_lock(archive) is None
    with pytest.raises(FileNotFoundError, match="No ALLIN1"):
        service.clear_stale_lock(archive)
    lock = archive.with_name(f".{archive.name}.allin1.lock")
    lock.write_text(json.dumps({"pid": 0}), encoding="utf-8")
    with pytest.raises(ValueError, match="process id"):
        service.inspect_archive_lock(archive)
    lock.write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    monkeypatch.setattr(service, "_pid_is_running", lambda _pid: True)
    with pytest.raises(RuntimeError, match="still running"):
        service.clear_stale_lock(archive)


def test_canary_and_runtime_guard_failure_paths(tmp_path, monkeypatch):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="existing loose"):
        service.run_canary(tmp_path / "missing.rpf")
    assert service._pid_is_running(rpf_tools.os.getpid()) is True
    monkeypatch.setattr(rpf_tools, "_running_gta_processes", lambda: ("gta5.exe",))
    with pytest.raises(RuntimeError, match="Close GTA V"):
        service._require_game_closed()

    usage = SimpleNamespace(free=1)
    monkeypatch.setattr(rpf_tools.shutil, "disk_usage", lambda _path: usage)
    with pytest.raises(RuntimeError, match="Not enough free space"):
        service._require_transaction_space(archive, tmp_path, 100, 100)


def test_rpf_service_failure_paths_and_membership_checks(tmp_path, monkeypatch):
    service, archive, patcher = _service(tmp_path)
    patcher.unlink()
    with pytest.raises(FileNotFoundError, match="RpfPatcher"):
        service.index(archive)
    patcher.write_bytes(b"exe")
    with pytest.raises(ValueError, match="loose .rpf"):
        service.index(tmp_path / "missing.zip")

    def failed(*_args, **_kwargs):
        return SimpleNamespace(returncode=5, stdout="", stderr="bad archive")

    monkeypatch.setattr(rpf_tools, "run_hidden", failed)
    with pytest.raises(ValueError, match="bad archive"):
        service.index(archive)

    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    directory = index.entry("::common")
    with pytest.raises(ValueError, match="Directories"):
        service.extract(index, directory, tmp_path / "directory")
    with pytest.raises(ValueError, match="directory"):
        service.replacement_plan(index, directory, archive)
    with pytest.raises(FileNotFoundError, match="payload"):
        service.replacement_plan(index, index.entries[1], tmp_path / "missing")


def test_rpf_service_rejects_wrong_helper_source_and_extraction_failure(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)
    wrong = tmp_path / "wrong.rpf"

    def wrong_index(args, **_kwargs):
        Path(args[4]).write_text(json.dumps(_index_payload(wrong)), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", wrong_index)
    with pytest.raises(ValueError, match="different archive"):
        service.index(archive)

    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    monkeypatch.setattr(
        rpf_tools, "run_hidden",
        lambda *_a, **_k: SimpleNamespace(returncode=5, stdout="", stderr="extract bad"),
    )
    with pytest.raises(ValueError, match="extract bad"):
        service.extract(index, index.entries[1], tmp_path / "out.bin")


def _gxt2() -> bytes:
    text = b"Hello Los Santos\0"
    text_offset = 24
    end = text_offset + len(text)
    return b"".join((
        b"GXT2", struct.pack("<I", 1), struct.pack("<II", 0x12345678, text_offset),
        b"GXT2", struct.pack("<I", end), text,
    ))


def test_native_asset_lightweight_gxt_dds_and_signatures(tmp_path):
    inspector = NativeAssetInspector(tmp_path)
    gxt = inspector.inspect_bytes("global.gxt2", _gxt2())
    assert gxt.format_name == "Rockstar GXT2 text table"
    assert gxt.metadata["label_count"] == 1
    assert "0x12345678  Hello Los Santos" in gxt.structured_text
    assert "RpfPatcher is not built" in gxt.warnings[0]

    dds = bytearray(128)
    dds[:4] = b"DDS "
    struct.pack_into("<II", dds, 12, 64, 128)
    struct.pack_into("<I", dds, 28, 5)
    dds[84:88] = b"DXT5"
    report = inspector.inspect_bytes("paint.dds", bytes(dds))
    assert report.metadata == {
        "dimensions": "128 × 64", "mip_levels": 5, "pixel_format": "DXT5",
    }

    rsc = inspector.inspect_bytes(
        "model.ydr", b"RSC8" + struct.pack("<III", 159, 1, 2), truncated=True,
    )
    assert rsc.metadata["resource_container"] == "RSC8"
    assert rsc.metadata["resource_version"] == 159
    assert "safety limit" in rsc.warnings[0]

    awc = inspector.inspect_bytes("sound.awc", b"ADAT" + b"\x01\0\0\0" + struct.pack("<I", 4))
    assert awc.metadata["endianness"] == "little"
    assert awc.metadata["stream_count"] == 4
    gfx = inspector.inspect_bytes("hud.gfx", b"CWS\x0A" + b"\0" * 10)
    assert gfx.metadata["scaleform_version"] == 10


def test_native_asset_helper_xml_and_texture_contact_sheet(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")

    def convert(args, **_kwargs):
        Path(args[3]).write_text("<TextureDictionary><Item /></TextureDictionary>", encoding="utf-8")
        assets = Path(args[4])
        assets.mkdir(parents=True)
        Image = pytest.importorskip("PIL.Image")
        Image.new("RGB", (32, 16), "red").save(assets / "diffuse.png")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    report = NativeAssetInspector(project).inspect_bytes("vehicle.ytd", b"RSC8" + b"\0" * 32)
    assert report.structured_text.startswith("<TextureDictionary>")
    assert report.metadata["exported_textures"] == 1
    assert report.image_png.startswith(b"\x89PNG")


def test_native_asset_conversion_failure_retries_other_edition(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    calls = []

    def convert(args, **_kwargs):
        calls.append(args[-1])
        if args[-1] == "gen9":
            return SimpleNamespace(returncode=5, stdout="", stderr="wrong version")
        Path(args[3]).write_text("<Drawable />", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    report = NativeAssetInspector(project).inspect_bytes("prop.ydr", b"RSC7" + b"\0" * 20)
    assert calls == ["gen9", "legacy"]
    assert report.metadata["interpreted_edition"] == "Legacy"
    assert "parsed as Legacy" in report.warnings[0]


def test_native_preview_limits():
    assert native_preview_limit("model.yft", 20) == 21
    assert native_preview_limit("huge.yft", MAX_NATIVE_PREVIEW_BYTES + 10) == MAX_NATIVE_PREVIEW_BYTES
    assert native_preview_limit("huge.bin", 20 * 1024 * 1024) == 8 * 1024 * 1024


def test_new_rpf_cli_index_extract_and_plan(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))

    class FakeService:
        def __init__(self, project_root, gta_path, **_kwargs):
            assert Path(project_root).name == "ALLIN1-SDK"
            assert Path(gta_path) == game

        def index(self, source):
            assert Path(source) == archive
            return index

        def extract(self, loaded, entry, output):
            assert loaded is index and entry.path == "common/data/test.ymap"
            target = Path(output)
            target.write_bytes(b"extracted")
            return target

        def replacement_plan(self, loaded, entry, payload):
            assert loaded is index and entry.path == "common/data/test.ymap"
            return {"operation": "replace_rpf_entry", "status": "plan_only"}

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    runner = CliRunner()
    exported = runner.invoke(main, [
        "sdk", "index-rpf", str(archive), "--gta-path", str(game),
        "-o", str(tmp_path / "cli-index.json"),
    ])
    assert exported.exit_code == 0, exported.output
    assert "4 entries across 2 archive(s)" in exported.output
    assert (tmp_path / "cli-index.csv").is_file()

    extracted = runner.invoke(main, [
        "sdk", "extract-rpf-entry", str(archive), "common/data/test.ymap",
        "--gta-path", str(game), "-o", str(tmp_path / "test.ymap"),
    ])
    assert extracted.exit_code == 0, extracted.output
    assert (tmp_path / "test.ymap").read_bytes() == b"extracted"

    payload = tmp_path / "new.ymap"
    payload.write_bytes(b"new")
    planned = runner.invoke(main, [
        "sdk", "plan-rpf-replacement", str(archive), "common/data/test.ymap",
        str(payload), "--gta-path", str(game), "-o", str(tmp_path / "plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    assert json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))["status"] == "plan_only"


def test_new_rpf_cli_reports_missing_detection_and_unknown_entries(tmp_path, monkeypatch):
    from allin1_sdk import cli

    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    monkeypatch.setattr(cli, "detect_gta_path", lambda: None)
    runner = CliRunner()
    missing = runner.invoke(main, [
        "sdk", "index-rpf", str(archive), "-o", str(tmp_path / "index.json"),
    ])
    assert missing.exit_code != 0
    assert "GTA V was not detected" in missing.output

    game = tmp_path / "game"
    game.mkdir()
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def index(self, _source):
            return index

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    unknown = runner.invoke(main, [
        "sdk", "extract-rpf-entry", str(archive), "missing.bin",
        "--gta-path", str(game), "-o", str(tmp_path / "missing.bin"),
    ])
    assert unknown.exit_code != 0
    assert "not found uniquely" in unknown.output

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"x")
    unknown_plan = runner.invoke(main, [
        "sdk", "plan-rpf-replacement", str(archive), "missing.bin", str(payload),
        "--gta-path", str(game), "-o", str(tmp_path / "plan.json"),
    ])
    assert unknown_plan.exit_code != 0
    assert "not found uniquely" in unknown_plan.output


def test_rpf_transaction_cli_requires_acknowledgement_and_routes_actions(
    tmp_path, monkeypatch,
):
    game = tmp_path / "game"
    game.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")

    class FakeService:
        def __init__(self, project_root, gta_path, **_kwargs):
            assert Path(project_root).name == "ALLIN1-SDK"
            assert Path(gta_path) == game

        def apply_change_plan(self, selected, *, receipt_root=None, progress=None):
            assert Path(selected) == plan
            assert Path(receipt_root) == tmp_path / "receipts"
            assert progress is not None
            return receipt

        def verify_transaction(self, selected):
            assert Path(selected) == receipt
            return {
                "healthy": True, "archive_state": "applied",
                "backup_valid": True, "entry_valid": True,
            }

        def rollback_transaction(self, selected, *, progress=None):
            assert Path(selected) == receipt
            assert progress is not None
            return receipt

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    runner = CliRunner()
    refused = runner.invoke(main, [
        "apply-rpf-plan", str(plan), "--gta-path", str(game),
    ])
    assert refused.exit_code != 0
    assert "--acknowledge-write" in refused.output

    applied = runner.invoke(main, [
        "apply-rpf-plan", str(plan), "--gta-path", str(game),
        "--receipt-dir", str(tmp_path / "receipts"), "--acknowledge-write",
    ])
    assert applied.exit_code == 0, applied.output
    assert "Applied and verified" in applied.output

    verified = runner.invoke(main, [
        "verify-rpf-transaction", str(receipt), "--gta-path", str(game),
    ])
    assert verified.exit_code == 0, verified.output
    assert '"archive_state": "applied"' in verified.output

    rolled_back = runner.invoke(main, [
        "sdk", "rollback-rpf-transaction", str(receipt),
        "--gta-path", str(game), "--acknowledge-write",
    ])
    assert rolled_back.exit_code == 0, rolled_back.output
    assert "Rolled back and verified" in rolled_back.output


def test_rpf_cli_add_delete_history_recovery_canary_and_output(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    archive = workspace / "test.rpf"
    archive.write_bytes(b"RPF7")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    report = tmp_path / "canary.json"
    report.write_text("{}", encoding="utf-8")
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive, nested=False)))

    class FakeService:
        def __init__(self, project_root, gta_path, **kwargs):
            assert Path(project_root).name == "ALLIN1-SDK"
            assert Path(gta_path) == game
            if kwargs.get("workspace_roots"):
                assert kwargs["workspace_roots"] == (workspace.resolve(),)

        def index(self, selected):
            assert Path(selected) == archive
            return index

        def addition_plan(self, loaded, entry_path, selected_payload, *, archive_path=""):
            assert loaded is index and entry_path == "new.bin"
            assert Path(selected_payload) == payload and archive_path == ""
            return {"status": "ready", "action": "add"}

        def deletion_plan(self, loaded, entry):
            assert loaded is index and entry.path == "common/data/test.ymap"
            return {"status": "ready", "action": "delete"}

        def recover_transaction(self, selected):
            assert Path(selected) == receipt
            return {"healthy": True, "archive_state": "applied"}

        def list_transactions(self, selected_root):
            assert Path(selected_root) == workspace
            return ({"transaction_id": "one", "valid": True},)

        def run_canary(self, selected, *, output_root=None, progress=None):
            assert Path(selected) == archive and Path(output_root) == workspace
            progress("done", 100)
            return report

        def verify_transaction(self, selected):
            assert Path(selected) == receipt
            return {
                "healthy": True, "archive_state": "applied",
                "backup_valid": True, "entry_valid": True,
            }

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    runner = CliRunner()
    common = ["--gta-path", str(game), "--workspace-root", str(workspace)]
    added = runner.invoke(main, [
        "plan-rpf-add", str(archive), "new.bin", str(payload), *common,
        "-o", str(tmp_path / "add.json"),
    ])
    assert added.exit_code == 0, added.output
    assert "ready add plan" in added.output
    deleted = runner.invoke(main, [
        "sdk", "plan-rpf-delete", str(archive), "common/data/test.ymap",
        *common, "-o", str(tmp_path / "delete.json"),
    ])
    assert deleted.exit_code == 0, deleted.output
    assert "ready delete plan" in deleted.output
    recovered = runner.invoke(main, [
        "recover-rpf-transaction", str(receipt), *common,
    ])
    assert recovered.exit_code == 0 and '"healthy": true' in recovered.output
    listed = runner.invoke(main, [
        "list-rpf-transactions", "--gta-path", str(game),
        "--receipt-dir", str(workspace), "-o", str(tmp_path / "history.json"),
    ])
    assert listed.exit_code == 0 and "1 transaction record" in listed.output
    refused = runner.invoke(main, [
        "canary-rpf-transaction", str(archive), "--gta-path", str(game),
    ])
    assert refused.exit_code != 0 and "--acknowledge-write" in refused.output
    canary = runner.invoke(main, [
        "canary-rpf-transaction", str(archive), "--gta-path", str(game),
        "--output-dir", str(workspace), "--acknowledge-write",
    ])
    assert canary.exit_code == 0 and "Real-archive canary passed" in canary.output
    verified = runner.invoke(main, [
        "verify-rpf-transaction", str(receipt), "--gta-path", str(game),
        "-o", str(tmp_path / "verified.json"),
    ])
    assert verified.exit_code == 0 and "Transaction is healthy" in verified.output
