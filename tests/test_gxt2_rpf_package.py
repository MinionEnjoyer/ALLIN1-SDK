"""Desktop RPF packaging guards; native helper coverage lives in packaged smoke."""
import base64
import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk import desktop_protocol, gxt2_desktop as desktop, gxt2_rpf_package as package
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.rpf_tools import RpfArchiveRecord, RpfEntryRecord, RpfExplorerService, RpfIndex


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unpack(path):
    return {key: base64.b64decode(value) for key, value in json.loads(Path(path).read_bytes()[4:]).items()}


def pack(path, rows):
    Path(path).write_bytes(b"RPF7" + json.dumps({k: base64.b64encode(v).decode() for k, v in rows.items()}, sort_keys=True).encode())


@pytest.fixture(params=["::global.gxt2", "american.rpf::global.gxt2"])
def workspace(tmp_path, monkeypatch, request):
    target = request.param
    source = tmp_path / "source.rpf"
    original = Gxt2Workspace.encode(({"hash": 256, "text": "Original — 日本語"},))
    pack(source, {target: original, "::untouched.bin": b"keep-original", "::other.gxt2": original})
    game = tmp_path / "game"; game.mkdir()
    def index(self, path):
        path = Path(path).resolve()
        rows = unpack(path)
        records = []
        for key, data in rows.items():
            layer, member = key.split("::")
            records.append(RpfEntryRecord(key, layer, member, Path(member).name, "binary", len(data), len(data)))
        archives = [RpfArchiveRecord("", path.name, 7, "OPEN", path.stat().st_size, len(records))]
        if target.startswith("american"):
            archives.append(RpfArchiveRecord("american.rpf", "american.rpf", 7, "OPEN", 100, 1))
            records.append(RpfEntryRecord("::american.rpf", "", "american.rpf", "american.rpf", "archive", 100, 100))
        return RpfIndex(path, "Enhanced", path.stat().st_size, tuple(archives), tuple(records))
    def extract(self, index, entry, destination):
        Path(destination).write_bytes(unpack(index.source)[entry.id])
        return Path(destination)
    def fingerprints(self, index, entries=None):
        rows = unpack(index.source)
        return {entry.id: {"mode": "byte_exact", "logical_size": len(rows[entry.id]), "raw_sha256": digest(rows[entry.id]),
                           "canonical_sha256": digest(rows[entry.id])} for entry in entries}
    def transaction(self, plan_path, *, receipt_root=None, **kwargs):
        plan = json.loads(Path(plan_path).read_text())
        archive = Path(plan["archive"])
        assert archive != source and archive.is_relative_to(self.workspace_roots[0])
        assert plan["target_scope"] == "workspace_copy"
        rows = unpack(archive)
        rows[f"{plan['archive_path']}::{plan['entry']}"] = Path(plan["payload"]["path"]).read_bytes()
        pack(archive, rows)
        receipt = Path(receipt_root) / "receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"status": "applied", "plan_id": plan["plan_id"], "backup": {"sha256": plan["archive_sha256"]}, "applied_archive_sha256": package._hash(archive)}))
        return receipt
    monkeypatch.setattr(RpfExplorerService, "index", index)
    monkeypatch.setattr(RpfExplorerService, "extract", extract)
    monkeypatch.setattr(RpfExplorerService, "entry_content_fingerprints", fingerprints)
    monkeypatch.setattr(RpfExplorerService, "apply_change_plan", transaction)
    monkeypatch.setattr(RpfExplorerService, "_require_tool", lambda self: None)
    monkeypatch.setattr(RpfExplorerService, "_require_game_closed", staticmethod(lambda: None))
    binding = {"outer_archive": str(source), "outer_archive_sha256": package._hash(source), "entry_id": target, "edition": "Enhanced", "gta_path": str(game)}
    root = Gxt2Workspace().export_bytes("global.gxt2", original, tmp_path / "workspace", source_binding=binding)
    Gxt2Workspace.set_text(root, 256, "Edited — Français")
    return {"workspace": str(root)}, source, game


def pending(context, destination):
    state = desktop.inspect(context)
    payload = {**context, "action": "package_rpf", "destination": str(destination), "expected_state_sha256": state["state_sha256"]}
    reviewed = desktop.review(payload)
    return {**payload, "review_sha256": reviewed["review_sha256"], "authoring_confirmed": True}, reviewed


def test_package_replaces_only_bound_dictionary_and_publishes_verified_artifacts(workspace, tmp_path):
    context, archive, _ = workspace
    before = archive.read_bytes()
    output = tmp_path / "package"
    payload, reviewed = pending(context, output)
    assert not output.exists()
    assert reviewed["rpf_package"]["game_must_be_closed"]
    result = desktop.apply(payload)
    built = Path(result["archive"])
    assert built.name == archive.name and built.parent == output / "archive"
    rows, original = unpack(built), unpack(archive)
    target = reviewed["rpf_package"]["entry_id"]
    assert Gxt2Workspace.parse(rows[target])[0]["text"] == "Edited — Français"
    assert all(rows[key] == data for key, data in original.items() if key != target)
    assert archive.read_bytes() == before
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["source_unchanged"] and report["status"] == "verified" and not report["installable_allin1_package"]
    assert result["sha256"] == package._hash(built)
    assert result["report_sha256"] == package._hash(result["report"])
    assert sum(row["changed"] for row in report["verification"]) == 1
    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()) == sorted(reviewed["rpf_package"]["outputs"])
    assert not list(tmp_path.glob(".allin1-rpf-package-*"))


@pytest.mark.parametrize("changed", ["archive", "workspace", "destination", "confirmation", "review"])
def test_stale_or_unconfirmed_packaging_never_publishes(workspace, tmp_path, changed):
    context, archive, _ = workspace
    destination = tmp_path / "package"
    payload, _ = pending(context, destination)
    if changed == "archive": archive.write_bytes(archive.read_bytes() + b" ")
    elif changed == "workspace": Gxt2Workspace.set_text(context["workspace"], 256, "Concurrent")
    elif changed == "destination": payload["destination"] = str(tmp_path / "other")
    elif changed == "confirmation": payload["authoring_confirmed"] = False
    else: payload["review_sha256"] = "0" * 64
    with pytest.raises((ValueError, RuntimeError)): desktop.apply(payload)
    assert not destination.exists() and not (tmp_path / "other").exists()


@pytest.mark.parametrize("failure", ["unrelated", "structure", "transaction", "source_during_build", "destination_race", "receipt"])
def test_failed_verification_discards_staging_and_preserves_existing_outputs(workspace, tmp_path, monkeypatch, failure):
    context, source, _ = workspace
    destination = tmp_path / "package"
    payload, _ = pending(context, destination)
    before = source.read_bytes()
    transaction = RpfExplorerService.apply_change_plan
    def mutate(self, plan_path, **kwargs):
        if failure == "transaction": raise RuntimeError("Injected transaction failure")
        receipt = transaction(self, plan_path, **kwargs)
        plan = json.loads(Path(plan_path).read_text())
        archive = Path(plan["archive"])
        rows = unpack(archive)
        if failure == "unrelated": rows["::untouched.bin"] = b"corrupted"
        elif failure == "structure": rows["::extra.bin"] = b"unexpected"
        elif failure == "source_during_build": source.write_bytes(before + b" ")
        elif failure == "destination_race":
            destination.mkdir(); (destination / "user-file.txt").write_text("keep")
        elif failure == "receipt": Path(receipt).write_text(json.dumps({"status": "failed"}))
        pack(archive, rows)
        return receipt
    monkeypatch.setattr(RpfExplorerService, "apply_change_plan", mutate)
    with pytest.raises((ValueError, RuntimeError)): desktop.apply(payload)
    assert source.read_bytes() == (before + b" " if failure == "source_during_build" else before)
    if failure == "destination_race": assert (destination / "user-file.txt").read_text() == "keep"
    else: assert not destination.exists()
    assert not list(tmp_path.glob(".allin1-rpf-package-*"))


def test_packaging_refuses_unbound_unchanged_wrong_edition_and_game_destinations(workspace, tmp_path):
    context, _, game = workspace
    for output in [game / "package", Path(context["workspace"]) / "package"]:
        with pytest.raises(ValueError): pending(context, output)
    Gxt2Workspace.undo(context["workspace"])
    with pytest.raises(ValueError, match="unchanged"): pending(context, tmp_path / "unchanged")
    manifest = Path(context["workspace"]) / "gxt2-workspace.json"
    data = json.loads(manifest.read_text())
    data["source_binding"]["edition"] = "Legacy"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="edition"): pending(context, tmp_path / "wrong-edition")
    data["source_binding"] = {}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="copied archive dictionary"): pending(context, tmp_path / "unbound")


def test_packaging_preflights_closed_game_and_disk_space(workspace, tmp_path, monkeypatch):
    context, _, _ = workspace
    def running(): raise RuntimeError("Close GTA V")
    monkeypatch.setattr(RpfExplorerService, "_require_game_closed", staticmethod(running))
    with pytest.raises(RuntimeError, match="Close GTA"): pending(context, tmp_path / "running")
    monkeypatch.setattr(RpfExplorerService, "_require_game_closed", staticmethod(lambda: None))
    usage = package.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(package.shutil, "disk_usage", lambda path: usage._replace(free=0))
    with pytest.raises(ValueError, match="disk space"): pending(context, tmp_path / "full")


def test_protocol_preserves_authoring_only_risk(workspace, tmp_path):
    context, _, _ = workspace
    payload, _ = pending(context, tmp_path / "package")
    assert desktop_protocol.dispatch_operation("review_gxt2_action", payload)[0] == "read_only"
    assert desktop_protocol.dispatch_operation("apply_gxt2_action", payload)[0] == "authoring_write"
    assert "apply_gxt2_action" not in desktop_protocol.JOB_OPERATIONS
