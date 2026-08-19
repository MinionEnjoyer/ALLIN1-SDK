from __future__ import annotations

import hashlib
import json
import base64
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk import native_assets, rpf_tools
from allin1_sdk.cli import main
from allin1_sdk.gxt2_workspace import Gxt2Workspace
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


def test_rpf_service_inspects_bound_native_entry_without_archive_write(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    entry = index.entry("::common/data/test.ymap")
    original = archive.read_bytes()

    def fake_run(args, **_kwargs):
        if args[1] == "extract-virtual-entry":
            Path(args[6]).write_bytes(b"RSC7" + b"\0" * 28)
        elif args[1] == "asset-xml":
            Path(args[3]).write_text("<CMapData><entities /></CMapData>", encoding="utf-8")
            Path(args[4]).mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    monkeypatch.setattr(native_assets, "run_hidden", fake_run)
    report, binding = service.inspect_native_entry(index, entry)
    assert report.format_name == "Rockstar map placement"
    assert report.structured_text.startswith("<CMapData>")
    assert binding["outer_archive_sha256"] == hashlib.sha256(original).hexdigest()
    assert binding["entry_path"] == "common/data/test.ymap"
    assert binding["extracted_sha256"] == report.sha256
    assert archive.read_bytes() == original


def test_extract_authoring_tree_expands_recursively_indexed_archives(tmp_path, monkeypatch):
    service, archive, _patcher = _service(tmp_path)
    payload = _index_payload(archive)
    payload["archive_size"] = archive.stat().st_size
    payload["archives"][0]["size"] = archive.stat().st_size
    index = RpfIndex.load(_write_index(tmp_path, payload))
    content = {
        ("", "common/data/test.ymap"): b"root resource",
        ("x64/textures.rpf", "vehicle.ytd"): b"nested texture",
    }

    def fake_run(args, **_kwargs):
        assert args[1] == "extract-virtual-entries"
        output = Path(args[5])
        for line in Path(args[4]).read_text(encoding="utf-8").splitlines():
            archive_path, entry_path, relative = line.split("\t")
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content[(archive_path, entry_path)])
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    source, report = service.extract_authoring_tree(index, tmp_path / "authoring")
    assert (source / "common" / "data" / "test.ymap").read_bytes() == b"root resource"
    assert (
        source / "x64" / "textures.rpf.source" / "vehicle.ytd"
    ).read_bytes() == b"nested texture"
    assert not (source / "x64" / "textures.rpf").exists()
    assert report["summary"] == {
        "archives": 2, "directories": 2, "files": 2,
        "logical_bytes": 12_288,
    }
    assert report["source"]["sha256"] == hashlib.sha256(b"RPF7").hexdigest()


def test_extract_authoring_tree_refuses_unindexed_nested_archive(tmp_path):
    service, archive, _patcher = _service(tmp_path)
    payload = _index_payload(archive, nested=False)
    payload["archive_size"] = archive.stat().st_size
    payload["archives"][0]["size"] = archive.stat().st_size
    index = RpfIndex.load(_write_index(tmp_path, payload))
    with pytest.raises(ValueError, match="recursively indexed"):
        service.extract_authoring_tree(index, tmp_path / "authoring")


def test_extract_authoring_tree_refuses_loose_nested_source_collision(tmp_path):
    service, archive, _patcher = _service(tmp_path)
    payload = _index_payload(archive)
    payload["archive_size"] = archive.stat().st_size
    payload["archives"][0]["size"] = archive.stat().st_size
    payload["entries"].append({
        "id": "::x64/textures.rpf.source", "archive_path": "",
        "path": "x64/textures.rpf.source", "name": "textures.rpf.source",
        "kind": "directory", "size": 0, "stored_size": 0, "child_count": 0,
    })
    index = RpfIndex.load(_write_index(tmp_path, payload))
    with pytest.raises(ValueError, match="output collision"):
        service.extract_authoring_tree(index, tmp_path / "authoring")


def test_rpf_binary_workspace_exports_patches_and_builds_bound_plan(
    tmp_path, monkeypatch,
):
    service, archive, _ = _service(tmp_path)
    payload = b"native payload"

    def fake_run(args, **_kwargs):
        if args[1] == "index-json":
            Path(args[4]).write_text(json.dumps(_index_payload(archive)), encoding="utf-8")
        elif args[1] == "extract-virtual-entry":
            Path(args[6]).write_bytes(payload)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    index = service.index(archive)
    entry = index.entry("::common/data/test.ymap")
    workspace = service.export_binary_workspace(index, entry, tmp_path / "binary-workspace")
    from allin1_sdk.binary_workspace import BinaryPatchWorkspace
    BinaryPatchWorkspace.patch(workspace, 0, "FF", expected_hex=payload[:1].hex())
    plan_path, asset, report = service.plan_binary_workspace_replacement(
        index, entry, workspace, tmp_path / "binary-plan.json",
    )
    assert plan_path.is_file() and asset.is_file() and report.is_file()
    assert asset.read_bytes()[0] == 0xFF
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["binary_workspace"]["diff_report"] == str(report)
    assert plan["payload"]["sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()

    manifest = workspace / "binary-workspace.json"
    authored = json.loads(manifest.read_text(encoding="utf-8"))
    authored["source_binding"]["entry_id"] = "::wrong.bin"
    manifest.write_text(json.dumps(authored), encoding="utf-8")
    with pytest.raises(ValueError, match="not bound"):
        service.plan_binary_workspace_replacement(
            index, entry, workspace, tmp_path / "wrong-plan.json",
        )


def test_rpf_gxt2_workspace_exports_edits_and_builds_bound_plan(
    tmp_path, monkeypatch,
):
    service, archive, _ = _service(tmp_path)
    source = Gxt2Workspace.encode((
        {"hash": 0x100, "text": "Original text"},
        {"hash": 0x200, "text": "Second text"},
    ))
    payload = _index_payload(archive, nested=False)
    payload["entries"].append({
        "id": "::text/global.gxt2", "archive_path": "",
        "path": "text/global.gxt2", "name": "global.gxt2",
        "kind": "binary", "size": len(source), "stored_size": len(source),
    })
    index = RpfIndex.load(_write_index(tmp_path, payload))
    entry = index.entry("::text/global.gxt2")

    def fake_run(args, **_kwargs):
        assert args[1] == "extract-virtual-entry"
        Path(args[6]).write_bytes(source)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    workspace = service.export_gxt2_workspace(
        index, entry, tmp_path / "gxt2-workspace",
    )
    Gxt2Workspace.set_text(workspace, 0x100, "Edited text")
    plan_path, asset, report = service.plan_gxt2_workspace_replacement(
        index, entry, workspace, tmp_path / "gxt2-plan.json",
    )
    assert plan_path.is_file() and asset.is_file() and report.is_file()
    assert Gxt2Workspace.parse(asset.read_bytes())[0]["text"] == "Edited text"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["gxt2_workspace"]["validation_report"] == str(report)
    assert plan["payload"]["sha256"] == hashlib.sha256(asset.read_bytes()).hexdigest()
    assert archive.read_bytes() == b"RPF7"

    archive.write_bytes(b"RPF7-changed")
    with pytest.raises(ValueError, match="not bound"):
        service.plan_gxt2_workspace_replacement(
            index, entry, workspace, tmp_path / "stale-plan.json",
        )


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
            if path.endswith("/"):
                add_directory(archive_path, path.rstrip("/"))
                continue
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
    for directory in (item for item in entries if item["kind"] == "directory"):
        directory["child_count"] = sum(
            1 for item in entries
            if item["archive_path"] == directory["archive_path"]
            and Path(item["path"]).parent.as_posix() == directory["path"]
        )
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
    elif command == "extract-virtual-entries":
        source = Path(args[3])
        output_root = Path(args[5])
        for line in Path(args[4]).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            archive_path, entry_path, relative = line.split("\t", 2)
            state = _decode_archive(source)
            if archive_path:
                for nested_entry_path in archive_path.split("!"):
                    state = _decode_archive_bytes(state[nested_entry_path])
            destination = output_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(state[entry_path])
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
    elif command == "apply-entry-changes":
        source = Path(args[3])
        state = _decode_archive(source)
        payload_root = Path(args[5])
        for line in Path(args[4]).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            action, entry_path, relative = line.split("\t", 2)
            if action == "delete":
                state.pop(entry_path)
            elif action == "mkdir":
                state[entry_path.rstrip("/") + "/"] = b""
            elif action == "rmdir":
                state.pop(entry_path.rstrip("/") + "/", None)
            elif action == "rename":
                destination = relative
                marker = entry_path.rstrip("/") + "/"
                if marker in state or any(key.startswith(marker) for key in state):
                    rewritten = {}
                    for key, value in state.items():
                        if key == marker:
                            rewritten[destination.rstrip("/") + "/"] = value
                        elif key.startswith(marker):
                            rewritten[destination.rstrip("/") + "/" + key[len(marker):]] = value
                        else:
                            rewritten[key] = value
                    state = rewritten
                else:
                    state[destination] = state.pop(entry_path)
            else:
                state[entry_path] = (payload_root / relative).read_bytes()
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


def test_rpf_integrity_report_verifies_recursive_structure_and_exact_payloads(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _runner = _transaction_service(
        tmp_path, monkeypatch, nested=True,
    )
    index = service.index(archive)
    output, report = service.verify_archive_integrity(
        index, tmp_path / "integrity.json",
    )
    assert output.is_file()
    assert report["status"] == "verified"
    assert report["summary"]["archives"] == 2
    assert report["summary"]["payloads"] == 3
    assert report["summary"]["payloads_exactly_extracted"] == 3
    assert report["summary"]["structural_issues"] == 0
    assert all(item["sha256"] for item in report["payloads"])
    assert report["safety"]["writes_to_source"] is False
    with pytest.raises(ValueError, match="already exists"):
        service.verify_archive_integrity(index, output)


def test_rpf_integrity_report_records_orphaned_structure(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)

    def fake_run(args, **_kwargs):
        if args[1] == "index-json":
            Path(args[4]).write_text(
                json.dumps(_index_payload(archive)), encoding="utf-8",
            )
        elif args[1] == "extract-virtual-entries":
            output = Path(args[5])
            for line in Path(args[4]).read_text(encoding="utf-8").splitlines():
                _archive_path, _entry_path, relative = line.split("\t", 2)
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(relative.encode("utf-8"))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    _output, report = service.verify_archive_integrity(
        service.index(archive), tmp_path / "issues.json",
    )
    assert report["status"] == "structural_issues"
    codes = {item["code"] for item in report["structural_issues"]}
    assert "missing_parent_directory" in codes


def test_rpf_integrity_cli_routes_report(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "archive.rpf"
    archive.write_bytes(b"RPF7")
    output = tmp_path / "integrity.json"

    class FakeService:
        def __init__(self, _project, selected_game, **_kwargs):
            assert Path(selected_game) == game

        def index(self, selected):
            assert Path(selected) == archive
            return object()

        def verify_archive_integrity(self, _index, selected_output):
            report = {
                "status": "verified",
                "summary": {
                    "archives": 2, "payloads_exactly_extracted": 7,
                    "structural_issues": 0,
                },
            }
            Path(selected_output).write_text(json.dumps(report), encoding="utf-8")
            return Path(selected_output), report

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    result = CliRunner().invoke(main, [
        "sdk", "verify-rpf-archive", str(archive),
        "--gta-path", str(game), "-o", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert "7 exact payload(s)" in result.output
    assert output.is_file()


def _defragment_fixture(tmp_path, monkeypatch, *, corrupt_leaf=False):
    service, archive, _patcher = _service(tmp_path)
    archive.write_bytes(b"RPF7" + (b"\0" * 896))
    payload = _index_payload(archive)
    index = RpfIndex.load(_write_index(tmp_path, payload))
    source_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

    def fake_run(args, **_kwargs):
        assert args[1] == "defragment-copy"
        assert Path(args[3]) == archive
        staged = Path(args[4])
        helper_report = Path(args[5])
        staged.write_bytes(b"RPF7")
        helper_report.write_text(json.dumps({
            "schema_version": 1,
            "operation": "rpf_defragment_copy",
            "source": str(archive.resolve()),
            "output": str(staged.resolve()),
            "source_size": 900,
            "output_size": 4,
            "predicted_output_size": 4,
            "source_sha256": source_sha256,
            "output_sha256": hashlib.sha256(b"RPF7").hexdigest(),
            "source_unchanged": True,
            "recursive": True,
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def compacted_index(selected):
        if Path(selected).resolve() == archive.resolve():
            return index
        archives = tuple(
            type(item)(
                path=item.path, name=(Path(selected).name if not item.path else item.name),
                version=item.version, encryption=item.encryption,
                size=4 if not item.path else min(item.size, 400),
                entry_count=item.entry_count,
            )
            for item in index.archives
        )
        return RpfIndex(
            source=Path(selected).resolve(), edition=index.edition,
            archive_size=4, archives=archives, entries=index.entries,
            warnings=index.warnings,
        )

    calls = 0

    def fingerprints(loaded, entries):
        nonlocal calls
        calls += 1
        digest = "b" * 64 if corrupt_leaf and calls == 2 else "a" * 64
        return {
            entry.id: {
                "raw_sha256": digest,
                "canonical_sha256": digest,
                "logical_size": entry.size,
            }
            for entry in entries
        }

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    monkeypatch.setattr(service, "index", compacted_index)
    monkeypatch.setattr(service, "entry_content_fingerprints", fingerprints)
    return service, archive, index, source_sha256


def test_rpf_defragment_verified_copy_binds_tree_payloads_and_source(
    tmp_path, monkeypatch,
):
    service, archive, index, source_sha256 = _defragment_fixture(
        tmp_path, monkeypatch,
    )
    output = tmp_path / "authored" / "compact.rpf"
    report_path = tmp_path / "reports" / "compact.json"

    written, report_written, report = service.defragment_verified_copy(
        index, output, report_path,
    )

    assert written == output.resolve() and written.read_bytes() == b"RPF7"
    assert report_written == report_path.resolve() and report_written.is_file()
    assert report["status"] == "verified"
    assert report["source"]["sha256"] == source_sha256
    assert report["summary"]["bytes_saved"] == 896
    assert report["summary"]["leaf_payloads_verified"] == 2
    assert report["verification"]["leaf_payloads_raw_exact"] is True
    assert report["verification"]["writes_inside_gta_installation"] is False
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == source_sha256
    assert not any(output.parent.glob(".*.allin1-defrag-*"))


def test_rpf_defragment_verified_copy_rejects_payload_drift_and_cleans_output(
    tmp_path, monkeypatch,
):
    service, archive, index, source_sha256 = _defragment_fixture(
        tmp_path, monkeypatch, corrupt_leaf=True,
    )
    output = tmp_path / "compact.rpf"
    report = tmp_path / "compact.json"

    with pytest.raises(ValueError, match="changed leaf payload bytes"):
        service.defragment_verified_copy(index, output, report)

    assert not output.exists() and not report.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == source_sha256
    assert not any(tmp_path.glob(".*.allin1-defrag-*"))


def test_rpf_defragment_verified_copy_blocks_game_and_existing_outputs(
    tmp_path, monkeypatch,
):
    service, _archive, index, _source_sha256 = _defragment_fixture(
        tmp_path, monkeypatch,
    )
    outside = tmp_path / "outside.rpf"
    outside.write_bytes(b"occupied")
    with pytest.raises(ValueError, match="already exists"):
        service.defragment_verified_copy(index, outside, tmp_path / "new.json")
    with pytest.raises(ValueError, match="outside the GTA V installation"):
        service.defragment_verified_copy(
            index, service.gta_path / "mods" / "compact.rpf",
            tmp_path / "game-report.json",
        )


def test_rpf_defragment_verified_copy_reindexes_source_before_authoring(
    tmp_path, monkeypatch,
):
    service, _archive, index, _source_sha256 = _defragment_fixture(
        tmp_path, monkeypatch,
    )
    changed = RpfIndex(
        source=index.source, edition=index.edition, archive_size=index.archive_size,
        archives=index.archives, entries=index.entries[:-1], warnings=index.warnings,
    )
    monkeypatch.setattr(service, "index", lambda _selected: changed)
    with pytest.raises(ValueError, match="source index changed"):
        service.defragment_verified_copy(
            index, tmp_path / "compact.rpf", tmp_path / "compact.json",
        )


def test_rpf_defragment_cli_routes_verified_external_copy(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "source.rpf"
    archive.write_bytes(b"RPF7")
    output = tmp_path / "compact.rpf"
    report = tmp_path / "compact.json"

    class FakeService:
        def __init__(self, _project, selected_game, **_kwargs):
            assert Path(selected_game) == game

        def index(self, selected):
            assert Path(selected) == archive
            return object()

        def defragment_verified_copy(self, _index, selected_output, selected_report):
            assert Path(selected_output) == output
            assert Path(selected_report) == report
            output.write_bytes(b"RPF7")
            report.write_text('{"status":"verified"}', encoding="utf-8")
            return output, report, {
                "summary": {"bytes_saved": 4096, "leaf_payloads_verified": 7},
            }

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    result = CliRunner().invoke(main, [
        "sdk", "defragment-rpf", str(archive), "--gta-path", str(game),
        "--output", str(output), "--report", str(report),
    ])
    assert result.exit_code == 0, result.output
    assert "4,096 bytes saved" in result.output
    assert "Source archive unchanged" in result.output
    assert output.is_file() and report.is_file()


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


def test_atomic_multi_entry_plan_batches_root_and_deep_changes_with_one_receipt(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, deep_nested=True,
    )
    state = _decode_archive(archive)
    state["common/data/delete.bin"] = b"delete me"
    archive.write_bytes(_encode_archive(state))
    original = archive.read_bytes()
    deep = "x64/textures.rpf!archives/level2.rpf!deep/level3.rpf"
    root_replacement = tmp_path / "root.ymap"
    root_replacement.write_bytes(b"new root")
    root_addition = tmp_path / "new.bin"
    root_addition.write_bytes(b"new root file")
    deep_replacement = tmp_path / "target.ytd"
    deep_replacement.write_bytes(b"new deep texture")
    deep_addition = tmp_path / "extra.ytd"
    deep_addition.write_bytes(b"extra deep texture")
    plan = service.multi_change_plan(service.index(archive), [
        {
            "action": "upsert", "archive_path": "",
            "entry": "common/data/test.ymap", "payload": root_replacement,
        },
        {
            "action": "upsert", "archive_path": "",
            "entry": "common/data/new.bin", "payload": root_addition,
        },
        {
            "action": "delete", "archive_path": "",
            "entry": "common/data/delete.bin",
        },
        {
            "action": "replace", "archive_path": deep,
            "entry": "assets/target.ytd", "payload": deep_replacement,
        },
        {
            "action": "add", "archive_path": deep,
            "entry": "assets/extra.ytd", "payload": deep_addition,
        },
    ])
    assert plan["status"] == "ready"
    assert plan["safety"]["single_outer_archive_commit"] is True
    plan_path = tmp_path / "batch-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    calls = []

    def track_batch(args, **kwargs):
        if args[1] == "apply-entry-changes":
            calls.append(Path(args[3]))
        return _fake_archive_runner(args, **kwargs)

    monkeypatch.setattr(rpf_tools, "run_hidden", track_batch)
    receipt_path = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["operation"] == "rpf_multi_entry_change"
    assert receipt["action"] == "batch"
    assert len(receipt["changes"]) == 5
    legacy_receipt = json.loads(json.dumps(receipt))
    legacy_receipt["schema_version"] = 1
    for change in legacy_receipt["changes"]:
        change["original"].pop("kind", None)
        change["original"].pop("child_count", None)
    assert service._validate_receipt(legacy_receipt)["schema_version"] == 1
    # One helper session for the outer archive and each of the three nested
    # containers, not one full outer reconstruction per authored change.
    assert len(calls) == 4

    applied = _decode_archive(archive)
    assert applied["common/data/test.ymap"] == b"new root"
    assert applied["common/data/new.bin"] == b"new root file"
    assert "common/data/delete.bin" not in applied
    level1 = _decode_archive_bytes(applied["x64/textures.rpf"])
    level2 = _decode_archive_bytes(level1["archives/level2.rpf"])
    level3 = _decode_archive_bytes(level2["deep/level3.rpf"])
    assert level3["assets/target.ytd"] == b"new deep texture"
    assert level3["assets/extra.ytd"] == b"extra deep texture"
    assert service.verify_transaction(receipt_path)["healthy"] is True
    service.rollback_transaction(receipt_path)
    assert archive.read_bytes() == original


def test_multi_entry_plan_rejects_conflicts_tampering_and_stale_payloads(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, deep_nested=True,
    )
    original = archive.read_bytes()
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    index = service.index(archive)
    duplicate = [
        {
            "action": "replace", "entry": "common/data/test.ymap",
            "payload": payload,
        },
        {
            "action": "delete", "entry": "COMMON/DATA/TEST.YMAP",
        },
    ]
    with pytest.raises(ValueError, match="more than once"):
        service.multi_change_plan(index, duplicate)

    deep = "x64/textures.rpf!archives/level2.rpf!deep/level3.rpf"
    with pytest.raises(ValueError, match="replace an archive.*internal tree"):
        service.multi_change_plan(index, [
            {
                "action": "replace", "entry": "x64/textures.rpf",
                "payload": payload,
            },
            {
                "action": "replace", "archive_path": deep,
                "entry": "assets/target.ytd", "payload": payload,
            },
        ])

    plan = service.multi_change_plan(index, [{
        "action": "replace", "entry": "common/data/test.ymap",
        "payload": payload,
    }])
    path = tmp_path / "batch.json"
    plan["changes"][0]["entry"] = "common/data/forged.ymap"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="identity does not match"):
        service.apply_change_plan(path, receipt_root=tmp_path / "tampered")

    plan = service.multi_change_plan(index, [{
        "action": "replace", "entry": "common/data/test.ymap",
        "payload": payload,
    }])
    path.write_text(json.dumps(plan), encoding="utf-8")
    payload.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="payload.*changed after planning"):
        service.apply_change_plan(path, receipt_root=tmp_path / "stale")
    assert archive.read_bytes() == original


def test_atomic_tree_plan_creates_renames_removes_and_rolls_back_directories(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    original = archive.read_bytes()
    payload = tmp_path / "tree.bin"
    payload.write_bytes(b"tree payload")
    create_plan = service.multi_change_plan(service.index(archive), [
        {"action": "mkdir", "entry": "common/new"},
        {"action": "mkdir", "entry": "common/new/deep"},
        {
            "action": "add", "entry": "common/new/deep/item.bin",
            "payload": payload,
        },
        {
            "action": "rename", "entry": "common/data/test.ymap",
            "new_entry": "common/data/renamed.ymap",
        },
    ])
    assert [item["action"] for item in create_plan["changes"]] == [
        "mkdir", "mkdir", "add", "rename",
    ]
    create_path = tmp_path / "create-tree.json"
    create_path.write_text(json.dumps(create_plan), encoding="utf-8")
    receipt = service.apply_change_plan(
        create_path, receipt_root=tmp_path / "create-transactions",
    )
    applied = _decode_archive(archive)
    assert applied["common/new/"] == b""
    assert applied["common/new/deep/"] == b""
    assert applied["common/new/deep/item.bin"] == b"tree payload"
    assert applied["common/data/renamed.ymap"] == b"original entry"
    assert "common/data/test.ymap" not in applied
    assert service.verify_transaction(receipt)["healthy"] is True

    with pytest.raises(ValueError, match="not empty after reviewed changes"):
        service.multi_change_plan(service.index(archive), [
            {"action": "rmdir", "entry": "common/new"},
        ])
    remove_plan = service.multi_change_plan(service.index(archive), [
        {"action": "delete", "entry": "common/new/deep/item.bin"},
        {"action": "rmdir", "entry": "common/new/deep"},
        {"action": "rmdir", "entry": "common/new"},
    ])
    remove_path = tmp_path / "remove-tree.json"
    remove_path.write_text(json.dumps(remove_plan), encoding="utf-8")
    remove_receipt = service.apply_change_plan(
        remove_path, receipt_root=tmp_path / "remove-transactions",
    )
    removed = _decode_archive(archive)
    assert not any(path.startswith("common/new") for path in removed)
    assert service.verify_transaction(remove_receipt)["healthy"] is True
    service.rollback_transaction(remove_receipt)
    assert _decode_archive(archive) == applied
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original


def test_atomic_tree_plan_renames_directory_and_can_delete_nested_archive(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, nested=True,
    )
    original = archive.read_bytes()
    rename = service.multi_change_plan(service.index(archive), [{
        "action": "rename", "entry": "common", "new_entry": "renamed",
    }])
    rename_path = tmp_path / "rename-dir.json"
    rename_path.write_text(json.dumps(rename), encoding="utf-8")
    receipt = service.apply_change_plan(
        rename_path, receipt_root=tmp_path / "rename-transactions",
    )
    state = _decode_archive(archive)
    assert state["renamed/data/test.ymap"] == b"original entry"
    assert not any(path.startswith("common/") for path in state)
    assert service.verify_transaction(receipt)["healthy"] is True
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original

    delete_archive = service.multi_change_plan(service.index(archive), [{
        "action": "delete", "entry": "x64/textures.rpf",
    }])
    delete_path = tmp_path / "delete-nested-container.json"
    delete_path.write_text(json.dumps(delete_archive), encoding="utf-8")
    delete_receipt = service.apply_change_plan(
        delete_path, receipt_root=tmp_path / "archive-delete-transactions",
    )
    assert "x64/textures.rpf" not in _decode_archive(archive)
    assert service.verify_transaction(delete_receipt)["healthy"] is True
    service.rollback_transaction(delete_receipt)
    assert archive.read_bytes() == original


def test_rpf_subtree_export_handles_deep_archives_and_writes_hash_manifest(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, deep_nested=True,
    )
    archive_path = "x64/textures.rpf!archives/level2.rpf!deep/level3.rpf"
    original = archive.read_bytes()
    index = service.index(archive)
    target = service.extract_subtree(
        index, tmp_path / "deep-export", archive_path=archive_path,
        directory_path="assets",
    )

    exported = target / "target.ytd"
    assert exported.read_bytes() == b"original deeply nested texture"
    manifest = json.loads(
        (target / ".allin1-rpf-export.json").read_text(encoding="utf-8")
    )
    assert manifest["operation"] == "rpf_subtree_export"
    assert manifest["selection"] == {
        "archive_path": archive_path, "directory_path": "assets",
    }
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["relative_path"] == "target.ytd"
    assert manifest["files"][0]["sha256"] == hashlib.sha256(
        exported.read_bytes()
    ).hexdigest()
    assert manifest["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert archive.read_bytes() == original
    assert not list(tmp_path.glob(".deep-export.allin1-stage-*"))

    with pytest.raises(ValueError, match="already exists"):
        service.extract_subtree(index, target, archive_path=archive_path)
    with pytest.raises(ValueError, match="not a directory"):
        service.extract_subtree(
            index, tmp_path / "file-export", archive_path=archive_path,
            directory_path="assets/target.ytd",
        )
    with pytest.raises(ValueError, match="was not indexed"):
        service.extract_subtree(
            index, tmp_path / "missing-export", archive_path="missing.rpf",
        )


def test_rpf_subtree_export_enforces_limits_and_cleans_failed_staging(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(
        tmp_path, monkeypatch, deep_nested=True,
    )
    index = service.index(archive)
    monkeypatch.setattr(rpf_tools, "_MAX_SUBTREE_FILES", 1)
    with pytest.raises(ValueError, match="guarded export limit"):
        service.extract_subtree(index, tmp_path / "too-many")

    monkeypatch.setattr(rpf_tools, "_MAX_SUBTREE_FILES", 25_000)
    monkeypatch.setattr(rpf_tools, "_MAX_SUBTREE_LOGICAL_BYTES", 1)
    with pytest.raises(ValueError, match="logical bytes.*guarded export"):
        service.extract_subtree(index, tmp_path / "too-large")
    assert not (tmp_path / "too-large").exists()

    monkeypatch.setattr(rpf_tools, "_MAX_SUBTREE_LOGICAL_BYTES", 16 * 1024**3)

    def fail_batch(args, **kwargs):
        if args[1] == "extract-virtual-entries":
            return SimpleNamespace(returncode=7, stdout="", stderr="batch failed")
        return _fake_archive_runner(args, **kwargs)

    monkeypatch.setattr(rpf_tools, "run_hidden", fail_batch)
    with pytest.raises(ValueError, match="batch failed"):
        service.extract_subtree(index, tmp_path / "failed-export")
    assert not (tmp_path / "failed-export").exists()
    assert not list(tmp_path.glob(".failed-export.allin1-stage-*"))


def test_rpf_subtree_workspace_sync_plans_applies_and_rolls_back_file_edits(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    original = archive.read_bytes()
    index = service.index(archive)
    workspace = service.extract_subtree(
        index, tmp_path / "common-workspace", directory_path="common",
    )
    edited = workspace / "data" / "test.ymap"
    edited.write_bytes(b"workspace replacement")
    added = workspace / "data" / "new.bin"
    added.write_bytes(b"workspace addition")
    plan = service.subtree_sync_plan(index, workspace)
    assert plan["operation"] == "rpf_multi_entry_change"
    assert plan["workspace_sync"]["changed_files"] == 2
    assert {(item["action"], item["entry"]) for item in plan["changes"]} == {
        ("replace", "common/data/test.ymap"),
        ("add", "common/data/new.bin"),
    }
    plan_path = tmp_path / "sync-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    receipt = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "transactions",
    )
    applied = _decode_archive(archive)
    assert applied["common/data/test.ymap"] == b"workspace replacement"
    assert applied["common/data/new.bin"] == b"workspace addition"
    assert service.verify_transaction(receipt)["healthy"] is True
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original

    clean = service.extract_subtree(
        service.index(archive), tmp_path / "clean-workspace", directory_path="common",
    )
    with pytest.raises(ValueError, match="no changes"):
        service.subtree_sync_plan(service.index(archive), clean)


def test_rpf_subtree_workspace_sync_rejects_wrong_base_and_manifest_escape(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    index = service.index(archive)
    workspace = service.extract_subtree(
        index, tmp_path / "workspace", directory_path="common",
    )
    manifest_path = workspace / ".allin1-rpf-export.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match.*base"):
        service.subtree_sync_plan(index, workspace)

    manifest["source"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest["files"][0]["entry_path"] = "outside/escape.bin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="escapes its selection"):
        service.subtree_sync_plan(index, workspace)


def test_rpf_subtree_workspace_sync_preserves_and_reconciles_directory_tree(
    tmp_path, monkeypatch,
):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    state = _decode_archive(archive)
    state["common/empty/"] = b""
    archive.write_bytes(_encode_archive(state))
    original = archive.read_bytes()
    index = service.index(archive)
    workspace = service.extract_subtree(
        index, tmp_path / "tree-workspace", directory_path="common",
    )
    export_manifest = json.loads(
        (workspace / ".allin1-rpf-export.json").read_text(encoding="utf-8")
    )
    assert export_manifest["directory_count"] == 2
    assert (workspace / "empty").is_dir()
    (workspace / "empty").rmdir()
    nested = workspace / "new" / "deep"
    nested.mkdir(parents=True)
    (nested / "payload.bin").write_bytes(b"workspace tree")

    plan = service.subtree_sync_plan(index, workspace)
    assert {(item["action"], item["entry"]) for item in plan["changes"]} == {
        ("mkdir", "common/new"),
        ("mkdir", "common/new/deep"),
        ("add", "common/new/deep/payload.bin"),
        ("rmdir", "common/empty"),
    }
    assert plan["workspace_sync"]["changed_directories"] == 3
    plan_path = tmp_path / "tree-sync.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    receipt = service.apply_change_plan(
        plan_path, receipt_root=tmp_path / "tree-sync-transactions",
    )
    applied = _decode_archive(archive)
    assert "common/empty/" not in applied
    assert applied["common/new/deep/payload.bin"] == b"workspace tree"
    assert service.verify_transaction(receipt)["healthy"] is True
    service.rollback_transaction(receipt)
    assert archive.read_bytes() == original


def test_rpf_diff_detects_tree_and_exact_content_changes_and_exports_reports(
    tmp_path, monkeypatch,
):
    service, _archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    left = tmp_path / "left.rpf"
    right = tmp_path / "right.rpf"
    left.write_bytes(_encode_archive({
        "same.bin": b"same",
        "changed.bin": b"one",
        "removed.bin": b"gone",
        "nested.rpf": _encode_archive({"inner.bin": b"A"}),
    }))
    right.write_bytes(_encode_archive({
        "same.bin": b"same",
        "changed.bin": b"two",
        "added.bin": b"new!",
        "nested.rpf": _encode_archive({"inner.bin": b"B"}),
    }))
    left_original = left.read_bytes()
    right_original = right.read_bytes()
    left_index = service.index(left)
    right_index = service.index(right)

    metadata = service.compare_indexes(left_index, right_index)
    assert metadata["comparison_mode"] == "metadata"
    assert metadata["summary"]["added"] == 1
    assert metadata["summary"]["removed"] == 1
    assert metadata["summary"]["modified"] == 0

    exact = service.compare_indexes(left_index, right_index, exact_content=True)
    assert exact["comparison_mode"] == "exact_content"
    assert exact["summary"]["content_compared"] == 4
    assert exact["summary"]["modified"] == 3
    assert {
        item["identity"]["path"] for item in exact["entries"]["modified"]
    } == {"changed.bin", "nested.rpf", "inner.bin"}
    assert all(
        "sha256" in item["changes"] for item in exact["entries"]["modified"]
    )
    assert left.read_bytes() == left_original
    assert right.read_bytes() == right_original

    json_path, markdown_path = service.export_diff(
        exact, tmp_path / "reports" / "archive-comparison.json",
    )
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"][
        "modified"
    ] == 3
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# RPF archive diff" in markdown
    assert "changed.bin" in markdown


def test_rpf_logical_diff_ignores_rsc7_recompression_but_detects_payload_change(
    tmp_path, monkeypatch,
):
    service, _archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    header = b"RSC7" + bytes(range(12))
    logical = (b"rockstar resource payload" * 4096) + b"end"

    def resource(level, payload=logical):
        compressor = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
        return header + compressor.compress(payload) + compressor.flush()

    left = tmp_path / "left-resource.rpf"
    recompressed = tmp_path / "recompressed-resource.rpf"
    changed = tmp_path / "changed-resource.rpf"
    left.write_bytes(_encode_archive({"asset.ytd": resource(1)}))
    recompressed.write_bytes(_encode_archive({"asset.ytd": resource(9)}))
    changed.write_bytes(_encode_archive({"asset.ytd": resource(9, logical + b"changed")}))

    left_index = service.index(left)
    recompressed_index = service.index(recompressed)
    changed_index = service.index(changed)
    raw = service.compare_indexes(
        left_index, recompressed_index, exact_content=True,
    )
    assert raw["summary"]["modified"] == 1
    logical_same = service.compare_indexes(
        left_index, recompressed_index, logical_content=True,
    )
    assert logical_same["comparison_mode"] == "logical_content"
    assert logical_same["summary"]["modified"] == 0
    assert logical_same["summary"]["content_compared"] == 1
    logical_changed = service.compare_indexes(
        left_index, changed_index, logical_content=True,
    )
    assert logical_changed["summary"]["modified"] == 1
    assert "logical_content" in logical_changed["entries"]["modified"][0]["changes"]
    assert "content" in logical_changed["entries"]["modified"][0]["left"]
    with pytest.raises(ValueError, match="not both"):
        service.compare_indexes(
            left_index, changed_index, exact_content=True, logical_content=True,
        )


def test_rpf_diff_refuses_stale_indexes_and_invalid_reports(tmp_path, monkeypatch):
    service, archive, _entry, _fake = _transaction_service(tmp_path, monkeypatch)
    index = service.index(archive)
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed after indexing"):
        service.compare_indexes(index, index)
    with pytest.raises(ValueError, match="Expected an RPF archive diff"):
        service.export_diff({}, tmp_path / "invalid")


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
        b"2TXG", struct.pack("<I", 1), struct.pack("<II", 0x12345678, text_offset),
        b"2TXG", struct.pack("<I", end), text,
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


def test_native_asset_helper_renders_bounded_model_geometry(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    model_xml = """<?xml version="1.0"?>
<Drawable>
 <DrawableModelsHigh><Item><Geometries><Item>
  <VertexBuffer><Layout type="GTAV1"><Position/><Normal/></Layout><Data>
0 0 0  0 0 1
1 0 0  0 0 1
1 1 0  0 0 1
0 1 0  0 0 1
0.5 0.5 1  0 0 1
  </Data></VertexBuffer>
  <IndexBuffer><Data>0 1 4  1 2 4  2 3 4  3 0 4  0 3 2  2 1 0</Data></IndexBuffer>
 </Item></Geometries></Item></DrawableModelsHigh>
</Drawable>"""

    def convert(args, **_kwargs):
        Path(args[3]).write_text(model_xml, encoding="utf-8")
        Path(args[4]).mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    report = NativeAssetInspector(project).inspect_bytes("pyramid.ydr", b"RSC8" + b"\0" * 32)
    assert report.image_png.startswith(b"\x89PNG")
    assert report.metadata["model_drawable_count"] == 1
    assert report.metadata["model_geometry_count"] == 1
    assert report.metadata["model_vertex_count"] == 5
    assert report.metadata["model_triangle_count"] == 6
    assert report.metadata["model_lods"] == "High: 1"
    assert report.metadata["model_preview"] == "isometric geometry diagnostic"


def test_native_model_preview_rejects_dtd_and_bad_indices(tmp_path):
    dtd = tmp_path / "unsafe.ydr.xml"
    dtd.write_text(
        '<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///windows/win.ini">]><Drawable>&xxe;</Drawable>',
        encoding="utf-8",
    )
    image, metadata, warning = native_assets._model_preview_from_xml(dtd, "unsafe.ydr")
    assert image is None and metadata == {}
    assert "prohibited" in warning

    invalid = tmp_path / "invalid.ydr.xml"
    invalid.write_text(
        "<Drawable><VertexBuffer><Layout><Position/></Layout><Data>0 0 0</Data>"
        "</VertexBuffer><IndexBuffer><Data>0 1 2</Data></IndexBuffer></Drawable>",
        encoding="utf-8",
    )
    image, metadata, warning = native_assets._model_preview_from_xml(invalid, "invalid.ydr")
    assert image is None and metadata == {}
    assert "missing vertex" in warning


def test_inspect_native_asset_cli_publishes_portable_report(tmp_path):
    source = tmp_path / "paint.dds"
    data = bytearray(128)
    data[:4] = b"DDS "
    struct.pack_into("<II", data, 12, 32, 64)
    data[84:88] = b"DXT1"
    source.write_bytes(data)
    destination = tmp_path / "native-report"
    result = CliRunner().invoke(main, [
        "inspect-native-asset", str(source), "--edition", "Legacy",
        "--output-dir", str(destination),
    ])
    assert result.exit_code == 0, result.output
    response = json.loads(result.output)
    saved = json.loads((destination / "report.json").read_text(encoding="utf-8"))
    assert response == saved
    assert response["edition"] == "Legacy"
    assert response["metadata"]["dimensions"] == "64 × 32"
    assert response["has_image_preview"] is False
    assert response["outputs"] == []
    assert not (destination / "preview.png").exists()


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


def _native_workspace_inspector(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")

    def convert(args, **_kwargs):
        command = str(args[1])
        source = Path(args[2])
        output = Path(args[3])
        assets = Path(args[4])
        assets.mkdir(parents=True, exist_ok=True)
        if command == "asset-xml":
            output.write_text(
                f"<Drawable><Source>{source.name}</Source></Drawable>",
                encoding="utf-8",
            )
            (assets / "texture.png").write_bytes(b"png-dependency")
        elif command == "asset-from-xml":
            output.write_bytes(b"RSC8" + source.read_bytes())
        else:  # pragma: no cover - catches a future command-contract regression
            raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    return NativeAssetInspector(project)


def test_native_workspace_exports_builds_and_reparses(tmp_path, monkeypatch):
    inspector = _native_workspace_inspector(tmp_path, monkeypatch)
    source = tmp_path / "vehicle.ydd"
    source.write_bytes(b"RSC8-original")
    workspace = inspector.export_workspace(
        source, tmp_path / "vehicle-workspace", edition="Enhanced",
    )
    manifest = json.loads(
        (workspace / "native-workspace.json").read_text(encoding="utf-8")
    )
    assert manifest["operation"] == "native_asset_workspace"
    assert manifest["edition"] == "Enhanced"
    assert manifest["source"]["sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    assert manifest["dependencies"][0]["path"] == "texture.png"
    assert (workspace / "original" / "vehicle.ydd").read_bytes() == source.read_bytes()

    xml = workspace / "edit" / "vehicle.ydd.xml"
    xml.write_text("<Drawable><Edited>true</Edited></Drawable>", encoding="utf-8")
    (workspace / "edit" / "assets" / "extra.bin").write_bytes(b"dependency")
    output, report = inspector.build_workspace(workspace, tmp_path / "rebuilt.ydd")
    assert output.read_bytes().startswith(b"RSC8<Drawable>")
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["operation"] == "native_asset_workspace_build"
    assert result["validation"]["reparsed"] is True
    assert result["validation"]["dependency_count"] == 1
    assert result["edited_xml_sha256"] == hashlib.sha256(xml.read_bytes()).hexdigest()


def test_native_workspace_rejects_tampering_escapes_and_collisions(
    tmp_path, monkeypatch,
):
    inspector = _native_workspace_inspector(tmp_path, monkeypatch)
    workspace = inspector.export_workspace_bytes(
        "asset.ydr", b"RSC8-original", tmp_path / "workspace", edition="gen9",
    )
    snapshot = workspace / "original" / "asset.ydr"
    snapshot.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source snapshot was modified"):
        inspector.build_workspace(workspace, tmp_path / "rebuilt.ydr")

    snapshot.write_bytes(b"RSC8-original")
    manifest_path = workspace / "native-workspace.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["xml"]["path"] = "../outside.xml"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="XML path is unsafe"):
        inspector.build_workspace(workspace, tmp_path / "rebuilt.ydr")

    manifest["xml"]["path"] = "edit/asset.ydr.xml"
    manifest["source"]["name"] = "renamed.ydr"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="source identity was modified"):
        inspector.build_workspace(workspace, tmp_path / "rebuilt.ydr")

    manifest["source"]["name"] = "asset.ydr"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="retain the .ydr extension"):
        inspector.build_workspace(workspace, tmp_path / "rebuilt.ydd")
    existing = tmp_path / "rebuilt.ydr"
    existing.write_bytes(b"owned")
    with pytest.raises(ValueError, match="output already exists"):
        inspector.build_workspace(workspace, existing)
    assert existing.read_bytes() == b"owned"


def test_native_workspace_rejects_symlinked_dependencies(tmp_path, monkeypatch):
    inspector = _native_workspace_inspector(tmp_path, monkeypatch)
    workspace = inspector.export_workspace_bytes(
        "asset.ytd", b"RSC8-original", tmp_path / "workspace", edition="Legacy",
    )
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = workspace / "edit" / "assets" / "linked.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are not available to this Windows test account")
    with pytest.raises(ValueError, match="symbolic link"):
        inspector.build_workspace(workspace, tmp_path / "rebuilt.ytd")


def test_rpf_native_workspace_export_and_planned_replacement(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    entry = index.entry("x64/textures.rpf::vehicle.ytd")

    def rpf_helper(args, **_kwargs):
        assert args[1] == "extract-virtual-entry"
        Path(args[6]).write_bytes(b"RSC8-original")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def native_helper(args, **_kwargs):
        command = str(args[1])
        output = Path(args[3])
        assets = Path(args[4])
        assets.mkdir(parents=True, exist_ok=True)
        if command == "asset-xml":
            output.write_text("<TextureDictionary />", encoding="utf-8")
        elif command == "asset-from-xml":
            output.write_bytes(b"RSC8-rebuilt")
        else:  # pragma: no cover
            raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", rpf_helper)
    monkeypatch.setattr(native_assets, "run_hidden", native_helper)
    workspace = service.export_native_workspace(
        index, entry, tmp_path / "vehicle-workspace",
    )
    plan_path, asset, report = service.plan_native_workspace_replacement(
        index, entry, workspace, tmp_path / "vehicle-native-plan.json",
    )
    assert plan_path.is_file() and asset.read_bytes() == b"RSC8-rebuilt"
    assert report.is_file() and asset.parent.name == "vehicle-native-plan.payload"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["payload"]["path"] == str(asset)
    assert plan["payload"]["sha256"] == hashlib.sha256(b"RSC8-rebuilt").hexdigest()
    assert plan["native_workspace"]["validation_report"] == str(report)
    assert plan["status"] == "blocked"
    assert archive.read_bytes() == b"RPF7"


def test_rpf_native_workspace_plan_cleans_failed_build(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    entry = index.entry("x64/textures.rpf::vehicle.ytd")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "native-workspace.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        NativeAssetInspector, "build_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad XML")),
    )
    plan = tmp_path / "failed.json"
    with pytest.raises(RuntimeError, match="bad XML"):
        service.plan_native_workspace_replacement(index, entry, workspace, plan)
    assert not plan.exists()
    assert not (tmp_path / "failed.payload").exists()
    assert not list(tmp_path.glob(".failed.native-stage-*"))


def test_native_preview_limits():
    assert native_preview_limit("model.yft", 20) == 21
    assert native_preview_limit("huge.yft", MAX_NATIVE_PREVIEW_BYTES + 10) == MAX_NATIVE_PREVIEW_BYTES
    assert native_preview_limit("huge.bin", 20 * 1024 * 1024) == 8 * 1024 * 1024


def test_gxt2_rpf_cli_export_and_plan_commands(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    source = Gxt2Workspace.encode(({"hash": 0x100, "text": "Text"},))
    payload = _index_payload(archive, nested=False)
    payload["entries"].append({
        "id": "::text/global.gxt2", "archive_path": "",
        "path": "text/global.gxt2", "name": "global.gxt2", "kind": "binary",
        "size": len(source), "stored_size": len(source),
    })
    index = RpfIndex.load(_write_index(tmp_path, payload))

    class FakeService:
        def __init__(self, _project_root, gta_path, **_kwargs):
            assert Path(gta_path) == game

        def index(self, source_archive):
            assert Path(source_archive) == archive
            return index

        def export_gxt2_workspace(self, loaded, entry, output):
            assert loaded is index and entry.path == "text/global.gxt2"
            return Gxt2Workspace().export_bytes(entry.name, source, output)

        def plan_gxt2_workspace_replacement(self, loaded, entry, workspace, output):
            assert loaded is index and entry.path == "text/global.gxt2"
            plan = Path(output)
            payload_dir = plan.with_name(f"{plan.stem}.payload")
            payload_dir.mkdir()
            asset, report = Gxt2Workspace.build(workspace, payload_dir / entry.name)
            plan.write_text("{}", encoding="utf-8")
            return plan, asset, report

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    runner = CliRunner()
    workspace = tmp_path / "gxt2-workspace"
    exported = runner.invoke(main, [
        "export-rpf-gxt2-workspace", str(archive), "text/global.gxt2",
        "--gta-path", str(game), "--output", str(workspace),
    ])
    assert exported.exit_code == 0, exported.output
    Gxt2Workspace.set_text(workspace, 0x100, "Edited")
    planned = runner.invoke(main, [
        "sdk", "plan-rpf-gxt2-workspace", str(archive), "text/global.gxt2",
        str(workspace), "--gta-path", str(game),
        "--output", str(tmp_path / "gxt2-plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    assert "archive unchanged" in planned.output


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

        def inspect_native_entry(self, loaded, entry):
            assert loaded is index and entry.path == "common/data/test.ymap"
            report = NativeAssetInspector(tmp_path).inspect_bytes(
                entry.name, b"RSC7" + b"\0" * 28, edition=loaded.edition,
            )
            return report, {
                "outer_archive": str(archive),
                "outer_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "archive_path": entry.archive_path,
                "entry_path": entry.path,
                "entry_id": entry.id,
                "extracted_size": report.size,
                "extracted_sha256": report.sha256,
            }

        def extract_subtree(
            self, loaded, output, *, archive_path="", directory_path="",
        ):
            assert loaded is index
            assert archive_path == "x64/textures.rpf"
            assert directory_path == ""
            target = Path(output)
            target.mkdir()
            (target / ".allin1-rpf-export.json").write_text(
                "{}", encoding="utf-8",
            )
            return target

        def export_native_workspace(self, loaded, entry, output):
            assert loaded is index and entry.path == "common/data/test.ymap"
            target = Path(output)
            target.mkdir()
            (target / "native-workspace.json").write_text("{}", encoding="utf-8")
            return target

        def plan_native_workspace_replacement(self, loaded, entry, workspace, output):
            assert loaded is index and entry.path == "common/data/test.ymap"
            assert Path(workspace).name == "native-workspace"
            plan = Path(output)
            payload_dir = plan.with_name(f"{plan.stem}.payload")
            payload_dir.mkdir()
            asset = payload_dir / entry.name
            report = payload_dir / f"{entry.name}.allin1.json"
            asset.write_bytes(b"rebuilt")
            report.write_text("{}", encoding="utf-8")
            plan.write_text("{}", encoding="utf-8")
            return plan, asset, report

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

    native_inspection = runner.invoke(main, [
        "sdk", "inspect-rpf-native-entry", str(archive),
        "common/data/test.ymap", "--gta-path", str(game),
        "--output-dir", str(tmp_path / "native-inspection"),
    ])
    assert native_inspection.exit_code == 0, native_inspection.output
    inspected = json.loads(native_inspection.output)
    assert inspected["operation"] == "inspect_rpf_native_entry"
    assert inspected["binding"]["entry_path"] == "common/data/test.ymap"
    assert (tmp_path / "native-inspection" / "report.json").is_file()

    native_export = runner.invoke(main, [
        "sdk", "export-rpf-native-workspace", str(archive),
        "common/data/test.ymap", "--gta-path", str(game),
        "-o", str(tmp_path / "native-export"),
    ])
    assert native_export.exit_code == 0, native_export.output
    assert (tmp_path / "native-export" / "native-workspace.json").is_file()

    native_workspace = tmp_path / "native-workspace"
    native_workspace.mkdir()
    native_plan = runner.invoke(main, [
        "sdk", "plan-rpf-native-workspace", str(archive),
        "common/data/test.ymap", str(native_workspace),
        "--gta-path", str(game), "-o", str(tmp_path / "native-plan.json"),
    ])
    assert native_plan.exit_code == 0, native_plan.output
    assert (tmp_path / "native-plan.json").is_file()

    subtree = runner.invoke(main, [
        "sdk", "extract-rpf-subtree", str(archive),
        "--archive-path", "x64/textures.rpf", "--gta-path", str(game),
        "-o", str(tmp_path / "subtree"),
    ])
    assert subtree.exit_code == 0, subtree.output
    assert (tmp_path / "subtree" / ".allin1-rpf-export.json").is_file()

    payload = tmp_path / "new.ymap"
    payload.write_bytes(b"new")
    planned = runner.invoke(main, [
        "sdk", "plan-rpf-replacement", str(archive), "common/data/test.ymap",
        str(payload), "--gta-path", str(game), "-o", str(tmp_path / "plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    assert json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))["status"] == "plan_only"


def test_rpf_diff_cli_routes_exact_comparison_and_reports(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    left = tmp_path / "left.rpf"
    right = tmp_path / "right.rpf"
    left.write_bytes(b"RPF7-left")
    right.write_bytes(b"RPF7-right")
    left_index = object()
    right_index = object()
    report = {
        "operation": "rpf_archive_diff",
        "summary": {"added": 1, "removed": 2, "modified": 3},
    }
    modes = []

    class FakeService:
        def __init__(self, _project_root, gta_path, **_kwargs):
            assert Path(gta_path) == game

        def index(self, source):
            return left_index if Path(source) == left else right_index

        def compare_indexes(
            self, first, second, *, exact_content=False, logical_content=False,
        ):
            assert first is left_index and second is right_index
            modes.append((exact_content, logical_content))
            return report

        def export_diff(self, authored, output):
            assert authored is report
            json_path = Path(output).with_suffix(".json")
            markdown_path = Path(output).with_suffix(".md")
            json_path.write_text("{}", encoding="utf-8")
            markdown_path.write_text("# diff", encoding="utf-8")
            return json_path, markdown_path

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    result = CliRunner().invoke(main, [
        "sdk", "diff-rpf", str(left), str(right), "--exact-content",
        "--gta-path", str(game), "-o", str(tmp_path / "comparison.json"),
    ])
    assert result.exit_code == 0, result.output
    assert "1 added, 2 removed, 3 modified" in result.output
    assert (tmp_path / "comparison.json").is_file()
    assert (tmp_path / "comparison.md").is_file()
    logical = CliRunner().invoke(main, [
        "diff-rpf", str(left), str(right), "--logical-content",
        "--gta-path", str(game), "-o", str(tmp_path / "logical.json"),
    ])
    assert logical.exit_code == 0, logical.output
    assert modes == [(True, False), (False, True)]


def test_rpf_diff_cli_rejects_multiple_content_modes(tmp_path):
    left = tmp_path / "left.rpf"
    right = tmp_path / "right.rpf"
    left.write_bytes(b"RPF7-left")
    right.write_bytes(b"RPF7-right")
    result = CliRunner().invoke(main, [
        "diff-rpf", str(left), str(right), "--exact-content",
        "--logical-content", "-o", str(tmp_path / "report.json"),
    ])
    assert result.exit_code != 0
    assert "not both" in result.output


def test_rpf_batch_cli_resolves_manifest_payloads_and_writes_plan(
    tmp_path, monkeypatch,
):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "archive.rpf"
    archive.write_bytes(b"RPF7")
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    manifest = tmp_path / "changes.json"
    manifest.write_text(json.dumps({"changes": [{
        "action": "add", "archive_path": "", "entry": "new.bin",
        "payload": "payload.bin",
    }]}), encoding="utf-8")
    sentinel = object()

    class FakeService:
        def __init__(self, _project_root, gta_path, **_kwargs):
            assert Path(gta_path) == game

        def index(self, source):
            assert Path(source) == archive
            return sentinel

        def multi_change_plan(self, index, changes):
            assert index is sentinel
            assert Path(changes[0]["payload"]) == payload
            return {
                "operation": "rpf_multi_entry_change", "status": "ready",
                "changes": changes,
            }

        def subtree_sync_plan(self, index, export_directory):
            assert index is sentinel
            assert Path(export_directory) == tmp_path / "workspace"
            return {
                "operation": "rpf_multi_entry_change", "status": "ready",
                "changes": [{"action": "replace"}],
            }

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    output = tmp_path / "batch-plan.json"
    result = CliRunner().invoke(main, [
        "sdk", "plan-rpf-batch", str(archive), str(manifest),
        "--gta-path", str(game), "-o", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert "atomic plan for 1 changes" in result.output
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ready"

    (tmp_path / "workspace").mkdir()
    sync_output = tmp_path / "sync-plan.json"
    sync = CliRunner().invoke(main, [
        "sdk", "plan-rpf-sync", str(archive), str(tmp_path / "workspace"),
        "--gta-path", str(game), "-o", str(sync_output),
    ])
    assert sync.exit_code == 0, sync.output
    assert "atomic sync plan for 1 changes" in sync.output
    assert sync_output.is_file()


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
