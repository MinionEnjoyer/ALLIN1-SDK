import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk import desktop_protocol as protocol, gxt2_desktop as desktop
from allin1_sdk.gxt2_workspace import Gxt2Workspace


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "global.gxt2"
    path.write_bytes(Gxt2Workspace.encode(({"hash": 256, "text": "Vector"}, {"hash": 512, "text": "Français 日本語"})))
    return path


def action(context, action, **extra):
    state = desktop.inspect(context)
    payload = {**context, "action": action, "expected_state_sha256": state["state_sha256"], **extra}
    review = desktop.review(payload)
    return {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


@pytest.fixture
def workspace(source, tmp_path):
    destination = tmp_path / "text-copy"
    desktop.apply(action({"source": str(source)}, "create", destination=str(destination)))
    return {"workspace": str(destination)}


def test_complete_review_create_edit_add_remove_undo_build_roundtrip(source, workspace, tmp_path):
    before = source.read_bytes()
    state = desktop.inspect(workspace)
    assert state["revision"] == 0 and state["selected"]["text"] == "Vector"
    payload = action(workspace, "edit", label_hash="0x100", text="KRISS — 日本語")
    assert desktop.inspect(workspace)["revision"] == 0
    result = desktop.apply(payload)
    assert result["session"]["selected"]["text"] == "KRISS — 日本語"
    desktop.apply(action(workspace, "add", label_hash=768, text=""))
    desktop.apply(action(workspace, "remove", label_hash=512))
    desktop.apply(action(workspace, "undo"))
    assert desktop.inspect(workspace)["entry_count"] == 3
    output = tmp_path / "rebuilt.gxt2"
    built = desktop.apply(action(workspace, "build", destination=str(output)))
    assert built["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert not built["game_write_performed"] and built["file_write_performed"]
    assert [e["text"] for e in Gxt2Workspace.parse(output.read_bytes())] == ["KRISS — 日本語", "Français 日本語", ""]
    assert json.loads(Path(built["report"]).read_text())["status"] == "verified"
    assert source.read_bytes() == before
    assert (Path(workspace["workspace"]) / "original.gxt2").read_bytes() == before


def test_search_pagination_and_large_text_are_explicitly_bounded(tmp_path):
    source = tmp_path / "large.gxt2"
    source.write_bytes(Gxt2Workspace.encode(tuple({"hash": n, "text": f"Label {n}"} for n in range(301))))
    first = desktop.inspect({"source": str(source)})
    assert len(first["entries"]) == 100 and first["match_count"] == 301
    assert desktop.inspect({"source": str(source), "offset": 300})["entries"][0]["hash"] == 300
    assert desktop.inspect({"source": str(source), "query": "Label 300"})["match_count"] == 1
    source.write_bytes(Gxt2Workspace.encode(({"hash": 1, "text": "x" * (desktop.TEXT_LIMIT + 1)},)))
    selected = desktop.inspect({"source": str(source)})["selected"]
    assert selected["text"] is None and not selected["editable"]


@pytest.mark.parametrize("kind", ["edit", "remove", "undo", "build"])
def test_stale_workspace_does_not_authorize_changes(workspace, tmp_path, kind):
    desktop.apply(action(workspace, "edit", label_hash=256, text="First edit"))
    pending = action(workspace, kind, **({"label_hash": 256, "text": "Desired"} if kind == "edit" else {"label_hash": 256} if kind == "remove" else {"destination": str(tmp_path / "built.gxt2")} if kind == "build" else {}))
    Gxt2Workspace.set_text(workspace["workspace"], 256, "Concurrent edit")
    with pytest.raises(ValueError, match="state changed"):
        desktop.apply(pending)
    assert desktop.inspect(workspace)["selected"]["text"] == "Concurrent edit"


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_confirmation_is_explicit(workspace, value):
    payload = action(workspace, "edit", label_hash=256, text="Updated")
    with pytest.raises(ValueError, match="confirmation"):
        desktop.apply({**payload, "authoring_confirmed": value})
    assert desktop.inspect(workspace)["revision"] == 0


@pytest.mark.parametrize("field,value", [("text", "Changed"), ("label_hash", 512), ("review_sha256", "0" * 64)])
def test_changed_action_cannot_reuse_review(workspace, field, value):
    payload = action(workspace, "edit", label_hash=256, text="Reviewed")
    with pytest.raises(ValueError, match="review changed"):
        desktop.apply({**payload, field: value})


@pytest.mark.parametrize("label", [True, -1, 4294967296, "not-a-hash", 2.2])
def test_invalid_hashes_rejected(workspace, label):
    with pytest.raises(ValueError):
        action(workspace, "add", label_hash=label, text="Invalid")


@pytest.mark.parametrize("case", ["duplicate", "missing", "nul", "oversized", "unchanged"])
def test_invalid_edits_rejected(workspace, case):
    with pytest.raises(ValueError):
        action(workspace, "add" if case == "duplicate" else "edit", label_hash=999 if case == "missing" else 256,
               text={"nul": "x\0y", "oversized": "x" * 16385, "unchanged": "Vector"}.get(case, "New"))


def test_source_change_rejects_create_review_and_review_never_writes(source, tmp_path):
    output = tmp_path / "new-copy"
    pending = action({"source": str(source)}, "create", destination=str(output))
    assert not output.exists()
    source.write_bytes(Gxt2Workspace.encode(({"hash": 1, "text": "Changed"},)))
    with pytest.raises(ValueError, match="state changed"):
        desktop.apply(pending)
    assert not output.exists()


def test_destinations_protect_game_workspace_existing_files_and_report(workspace, tmp_path):
    game = tmp_path / "game"; game.mkdir(); (game / "GTA5_Enhanced.exe").write_bytes(b"fixture")
    for destination in [game / "output.gxt2", Path(workspace["workspace"]) / "output.gxt2", tmp_path / "CON.gxt2", tmp_path / "wrong.txt"]:
        with pytest.raises(ValueError): action(workspace, "build", destination=str(destination))
    output = tmp_path / "new.gxt2"
    pending = action(workspace, "build", destination=str(output))
    report = Path(str(output) + ".gxt2-validation.json")
    report.write_text("someone else's report")
    with pytest.raises(ValueError): desktop.apply(pending)
    assert not output.exists() and report.read_text() == "someone else's report"


def test_redirected_workspace_parent_rejected(workspace, tmp_path):
    link = tmp_path / "redirected"
    try: link.symlink_to(Path(workspace["workspace"]), target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Symlink privilege unavailable")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), workspace["workspace"]], capture_output=True)
        assert made.returncode == 0, made.stderr
    try:
        with pytest.raises(ValueError, match="reparse|symbolic|symlink"):
            desktop.inspect({"workspace": str(link)})
    finally:
        # Only unlink this test-created redirect, never recurse into its target.
        if link.is_symlink(): link.unlink()
        else: link.rmdir()


def test_core_handles_unsorted_original_and_preserves_history(source, tmp_path):
    data = bytearray(source.read_bytes())
    data[8:16], data[16:24] = data[16:24], data[8:16]
    root = Gxt2Workspace().export_bytes("unsorted.gxt2", bytes(data), tmp_path / "unsorted")
    Gxt2Workspace.set_text(root, 256, "Changed")
    Gxt2Workspace.undo(root)
    assert Gxt2Workspace.validate(root)["entries"][0]["text"] == "Vector"


def test_core_rolls_back_when_history_write_fails(workspace, monkeypatch):
    from allin1_sdk import gxt2_workspace as core
    original = core._write_json_atomic
    def fail_record(path, value):
        if path.name == "000001.json": raise OSError("Injected record failure")
        return original(path, value)
    monkeypatch.setattr(core, "_write_json_atomic", fail_record)
    with pytest.raises(OSError): desktop.apply(action(workspace, "edit", label_hash=256, text="Fail"))
    assert desktop.inspect(workspace)["revision"] == 0


def test_core_exclusive_build_preserves_concurrent_output(workspace, tmp_path, monkeypatch):
    original = Path.open
    output = tmp_path / "race.gxt2"
    pending = action(workspace, "build", destination=str(output))
    def race(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            with original(path, "wb") as stream: stream.write(b"concurrent")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", race)
    with pytest.raises(FileExistsError): desktop.apply(pending)
    assert output.read_bytes() == b"concurrent"


def test_protocol_routes_and_keeps_writes_out_of_jobs(workspace):
    assert {"inspect_gxt2_workspace", "review_gxt2_action"} <= protocol.JOB_OPERATIONS
    assert "apply_gxt2_action" not in protocol.JOB_OPERATIONS
    assert protocol.dispatch_operation("inspect_gxt2_workspace", workspace)[0] == "read_only"
    payload = action(workspace, "edit", label_hash=256, text="Updated")
    assert protocol.dispatch_operation("apply_gxt2_action", payload)[0] == "authoring_write"
    with pytest.raises(protocol.ProtocolError): protocol._operation_risk("apply_gxt2_action", payload)


@pytest.fixture
def packed_source(source, tmp_path, monkeypatch):
    from allin1_sdk import rpf_tools
    archive = tmp_path / "texts.rpf"
    archive.write_bytes(b"RPF7-fixture")
    nested = "x64/american.rpf::text/global.gxt2"
    entry = SimpleNamespace(id=nested, name="global.gxt2", path="text/global.gxt2", suffix=".gxt2",
                            archive_path="x64/american.rpf", size=source.stat().st_size, kind="binary")
    other = SimpleNamespace(**{**vars(entry), "id": "x64/french.rpf::text/global.gxt2", "archive_path": "x64/french.rpf"})
    rows = {entry.id: entry, other.id: other}
    index = SimpleNamespace(source=archive, edition="Enhanced", entry=lambda key: rows[key])
    monkeypatch.setattr(rpf_tools.RpfExplorerService, "index", lambda self, path: index)
    def extract(self, loaded, selected, destination):
        assert loaded is index and selected.id in rows
        Path(destination).write_bytes(source.read_bytes())
        return Path(destination)
    monkeypatch.setattr(rpf_tools.RpfExplorerService, "extract", extract)
    return {"archive": str(archive), "entry_id": nested, "gta_path": str(tmp_path)}, source, entry


def test_packed_dictionary_copy_edit_build_retains_exact_nested_provenance(packed_source, tmp_path):
    context, source, _ = packed_source
    archive = Path(context["archive"])
    original = archive.read_bytes()
    state = desktop.inspect(context)
    assert state["workspace"] is None and state["selected"]["text"] == "Vector"
    binding = state["source_binding"]
    assert binding["entry_id"] == context["entry_id"]
    assert binding["outer_archive_sha256"] == hashlib.sha256(original).hexdigest()
    output = tmp_path / "archive-text-copy"
    pending = action(context, "create", destination=str(output))
    assert not output.exists()
    copied = desktop.apply(pending)["session"]
    assert copied["source_binding"] == binding
    assert (output / "original.gxt2").read_bytes() == source.read_bytes()
    # Authoring is independent of the source after copying, including on reopen.
    archive.unlink()
    workspace = {"workspace": str(output)}
    desktop.apply(action(workspace, "edit", label_hash=256, text="Copied — 日本語"))
    built = desktop.apply(action(workspace, "build", destination=str(tmp_path / "copied.gxt2")))
    report = json.loads(Path(built["report"]).read_text(encoding="utf-8"))
    assert report["source_binding"] == binding
    assert Gxt2Workspace.parse(Path(built["archive"]).read_bytes())[0]["text"] == "Copied — 日本語"
    assert not archive.exists() and source.read_bytes() == (output / "original.gxt2").read_bytes()


@pytest.mark.parametrize("change", ["archive", "member", "same_named_member"])
def test_packed_copy_rejects_changed_archive_payload_or_selection(packed_source, tmp_path, change):
    context, source, _ = packed_source
    destination = tmp_path / "stale-copy"
    pending = action(context, "create", destination=str(destination))
    if change == "archive":
        Path(context["archive"]).write_bytes(b"RPF7-other")
    elif change == "member":
        source.write_bytes(source.read_bytes().replace(b"Vector", b"KRISS!"))
    else:
        pending["entry_id"] = "x64/french.rpf::text/global.gxt2"
    with pytest.raises(ValueError, match="state changed"):
        desktop.apply(pending)
    assert not destination.exists()


def test_archive_mutation_during_extraction_fails_closed(packed_source, monkeypatch):
    from allin1_sdk.rpf_tools import RpfExplorerService
    context, _, _ = packed_source
    original = RpfExplorerService.extract
    def changed(self, index, entry, destination):
        path = original(self, index, entry, destination)
        index.source.write_bytes(b"RPF7-concurrent")
        return path
    monkeypatch.setattr(RpfExplorerService, "extract", changed)
    with pytest.raises(RuntimeError, match="RPF changed"):
        desktop.inspect(context)


@pytest.mark.parametrize("kind", ["missing", "directory", "wrong_type", "too_large", "size_mismatch", "malformed"])
def test_invalid_packed_dictionary_is_never_opened(packed_source, kind):
    context, source, entry = packed_source
    if kind == "missing": context = {**context, "entry_id": "::missing.gxt2"}
    elif kind == "directory": entry.kind = "directory"
    elif kind == "wrong_type": entry.suffix = ".ytd"
    elif kind == "too_large": entry.size = desktop.MAX_GXT2_BYTES + 1
    elif kind == "size_mismatch": entry.size += 1
    else: source.write_bytes(b"bad!" + source.read_bytes()[4:])
    with pytest.raises(ValueError): desktop.inspect(context)


@pytest.mark.parametrize("extra", [{"source": "C:/other.gxt2"}, {"workspace": "C:/copy"}, {"entry_id": "../global.gxt2"}, {"entry_id": "::bad\0.gxt2"}, {"gta_path": "relative"}])
def test_archive_intake_rejects_ambiguous_or_invalid_context(packed_source, extra):
    context, _, _ = packed_source
    with pytest.raises(ValueError): desktop.inspect({**context, **extra})


def test_packed_context_uses_existing_protocol_risk_and_confirmation(packed_source, tmp_path):
    context, _, _ = packed_source
    risk, state = protocol.dispatch_operation("inspect_gxt2_workspace", context)
    assert risk == "read_only" and state["source_binding"]["entry_id"] == context["entry_id"]
    pending = action(context, "create", destination=str(tmp_path / "confirmed-copy"))
    with pytest.raises(protocol.ProtocolError, match="confirmation"):
        protocol.dispatch_operation("apply_gxt2_action", {**pending, "authoring_confirmed": False})
    assert not Path(pending["destination"]).exists()
