"""Offline desktop lifecycle and hostile-input regressions; no installed files."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from allin1_sdk import workspace_desktop as desktop
from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.map_contract import MapProject
from allin1_sdk import desktop_protocol as protocol
from test_map_contract import map_payload


def reviewed(context, action, **extra):
    payload = {**context, "action": action, **extra}
    if action != "create" or context.get("module") == "binary":
        state = desktop.inspect(context)
        payload["expected_state_sha256"] = state["state_sha256"]
    result = desktop.review(payload)
    return {**payload, "review_sha256": result["review_sha256"], "authoring_confirmed": True}


@pytest.fixture
def binary(tmp_path):
    folder = tmp_path / "Paths containing spaces"
    folder.mkdir()
    source = folder / "original.bin"
    source.write_bytes(bytes(range(256)) * 3)
    context = {"module": "binary", "source": str(source)}
    result = desktop.apply(reviewed(context, "create", destination=str(folder / "Editable copy")))
    assert result["session"]["revision"] == 0
    return {"module": "binary", "workspace": result["session"]["workspace"]}, source


def test_binary_complete_create_patch_undo_reopen_build(binary, tmp_path):
    context, source = binary
    original = source.read_bytes()
    before = desktop.inspect(context)
    assert before["bytes"] == list(range(256))
    request = reviewed(context, "patch", offset=3, expected_hex="03 04", replacement_hex="AA BB")
    assert desktop.inspect(context) == before  # Review never mutates.
    changed = desktop.apply(request)["session"]
    assert changed["bytes"][3:5] == [170, 187]
    assert changed["original_bytes"][3:5] == [3, 4]
    assert changed["revision"] == 1
    desktop.apply(reviewed(context, "undo"))
    assert desktop.inspect(context)["bytes"] == list(range(256))
    desktop.apply(reviewed(context, "patch", offset=3, expected_hex="03 04", replacement_hex="CC DD"))
    out = tmp_path / "patched asset.bin"
    built = desktop.apply(reviewed(context, "build", destination=str(out)))
    assert out.read_bytes()[3:5] == b"\xcc\xdd"
    assert built["output_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert json.loads(Path(built["report"]).read_text())["changed_bytes"] == 2
    assert source.read_bytes() == original
    assert (Path(context["workspace"]) / "original.bin").read_bytes() == original
    assert not built["game_write_performed"]
    assert desktop.inspect({**context, "offset": 700, "length": 256})["bytes"] == list(original[700:])


@pytest.mark.parametrize("change", ["bytes", "history", "request"])
def test_stale_binary_reviews_never_write(binary, change):
    context, source = binary
    pending = reviewed(context, "patch", offset=0, expected_hex="00", replacement_hex="FF")
    root = Path(context["workspace"])
    if change == "bytes":
        BinaryPatchWorkspace.patch(root, 2, "FE", expected_hex="02")
    elif change == "history":
        (root / "unrelated.txt").write_text("new input")
    else:
        pending["replacement_hex"] = "EE"
    before = (root / "editable.bin").read_bytes()
    with pytest.raises(ValueError, match="changed|Review"):
        desktop.apply(pending)
    assert (root / "editable.bin").read_bytes() == before
    assert source.read_bytes()[0] == 0


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_mutation_confirmation_is_real_boolean(binary, value):
    context, _ = binary
    request = reviewed(context, "patch", offset=0, expected_hex="00", replacement_hex="FF")
    with pytest.raises(ValueError, match="confirmation"):
        desktop.apply({**request, "authoring_confirmed": value})
    assert desktop.inspect(context)["revision"] == 0


@pytest.mark.parametrize("extra", [
    {"offset": -1, "expected_hex": "00", "replacement_hex": "FF"},
    {"offset": True, "expected_hex": "00", "replacement_hex": "FF"},
    {"offset": 0, "expected_hex": "FF", "replacement_hex": "00"},
    {"offset": 0, "expected_hex": "00", "replacement_hex": "FF FF"},
    {"offset": 0, "expected_hex": "00", "replacement_hex": "00"},
    {"offset": 768, "expected_hex": "00", "replacement_hex": "FF"},
])
def test_invalid_patch_preflight_is_nonmutating(binary, extra):
    context, _ = binary
    before = desktop.inspect(context)
    with pytest.raises(ValueError):
        reviewed(context, "patch", **extra)
    assert desktop.inspect(context) == before


def test_destination_traversal_and_outside_hardlink_canaries(binary, tmp_path):
    context, _ = binary
    desktop.apply(reviewed(context, "patch", offset=0, expected_hex="00", replacement_hex="FF"))
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-canary")
    with pytest.raises(ValueError, match="traversal"):
        reviewed(context, "build", destination=str(tmp_path / "folder" / ".." / "outside.bin"))
    target = tmp_path / "new.bin"
    os.link(outside, tmp_path / ".new.bin.tmp")
    with pytest.raises(ValueError, match="Hard-linked"):
        reviewed(context, "build", destination=str(target))
    assert outside.read_bytes() == b"outside-canary" and not target.exists()


def test_workspace_junction_is_rejected_before_outside_writes(binary, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    import subprocess
    context, _ = binary
    target = Path(context["workspace"])
    link = tmp_path / "junction workspace"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    before = (target / "editable.bin").read_bytes()
    try:
        with pytest.raises(ValueError, match="junction"):
            desktop.inspect({"module": "binary", "workspace": str(link)})
    finally:
        os.rmdir(link)  # Removes only the explicitly-created junction, never its target.
    assert (target / "editable.bin").read_bytes() == before


def test_map_create_edit_reopen_and_invalid_topology_are_real_contract(tmp_path):
    descriptor = tmp_path / "Maps with spaces.json"
    document = map_payload()
    request = reviewed({"module": "maps"}, "create", destination=str(descriptor), document=document)
    assert not descriptor.exists()
    created = desktop.apply(request)["session"]
    assert created["document"] == MapProject.from_dict(document).to_dict()
    context = {"module": "maps", "descriptor": str(descriptor)}
    edited = created["document"]
    edited["name"] = "Custom garage — 日本語"
    edited["garages"][0]["slots"].append({"id": "second-slot", "position": {"x": 3, "y": 4, "z": 5, "heading": 90}})
    saved = desktop.apply(reviewed(context, "save", document=edited))
    assert saved["session"]["document"]["name"] == edited["name"]
    assert len(MapProject.load(descriptor).garages[0].slots) == 2
    before = descriptor.read_bytes()
    edited["portals"][0]["to"]["level"] = "missing-level"
    with pytest.raises(ValueError):
        reviewed(context, "save", document=edited)
    assert descriptor.read_bytes() == before


def test_map_stale_review_and_destination_collision_preserve_user_edits(tmp_path):
    descriptor = tmp_path / "maps.json"
    desktop.apply(reviewed({"module": "maps"}, "create", destination=str(descriptor), document=map_payload()))
    context = {"module": "maps", "descriptor": str(descriptor)}
    document = desktop.inspect(context)["document"]
    document["name"] = "Planned edit"
    request = reviewed(context, "save", document=document)
    descriptor.write_text(descriptor.read_text() + "\n")
    before = descriptor.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        desktop.apply(request)
    with pytest.raises(ValueError, match="new destination"):
        reviewed({"module": "maps"}, "create", destination=str(descriptor), document=document)
    assert descriptor.read_bytes() == before


def test_desktop_protocol_routes_inspect_review_apply_with_separate_risks(binary):
    context, _ = binary
    risk, state = protocol.dispatch_operation("inspect_authoring_workspace", context, allow_game_writes=False, audit_path=None)
    assert risk == "read_only" and state["kind"] == "workspace_session"
    payload = {**context, "action": "patch", "offset": 0, "expected_hex": "00", "replacement_hex": "FE", "expected_state_sha256": state["state_sha256"]}
    risk, review = protocol.dispatch_operation("review_workspace_action", payload, allow_game_writes=False, audit_path=None)
    assert risk == "read_only"
    risk, result = protocol.dispatch_operation("apply_workspace_action", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}, allow_game_writes=False, audit_path=None)
    assert risk == "authoring_write" and result["session"]["bytes"][0] == 254
    assert "apply_workspace_action" not in protocol.JOB_OPERATIONS


@pytest.mark.parametrize("module", ["unknown", "execute", "install", None])
def test_no_arbitrary_module_or_game_write_dispatch(module):
    with pytest.raises(ValueError, match="Unknown"):
        desktop.inspect({"module": module})


@pytest.mark.parametrize("entry_id", [None, "filename", "::a::b", 5, "::" + "a" * 2048])
def test_binary_archive_intake_rejects_nonexact_id_before_native_execution(tmp_path, entry_id):
    archive = tmp_path / "owned.rpf"
    archive.write_bytes(b"owned fixture")
    game = tmp_path / "fixture game"
    game.mkdir()
    with pytest.raises(ValueError, match="exact indexed"):
        desktop.inspect({"module": "binary", "archive": str(archive), "gta_path": str(game), "entry_id": entry_id})


def test_binary_archive_rechecks_changed_archive_and_empty_extraction(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from allin1_sdk import rpf_tools
    archive = tmp_path / "owned.rpf"
    archive.write_bytes(b"owned fixture")
    game = tmp_path / "fixture game"
    game.mkdir()
    entry = SimpleNamespace(kind="file", size=3, name="data.bin", id="::data.bin")
    index = SimpleNamespace(entry=lambda entry_id: entry, edition="Enhanced")
    def extract(index, entry, destination):
        destination.write_bytes(b"")
        return destination
    monkeypatch.setattr(rpf_tools, "RpfExplorerService", lambda *args: SimpleNamespace(index=lambda path: index, extract=extract))
    context = {"module": "binary", "archive": str(archive), "gta_path": str(game), "entry_id": "::data.bin"}
    with pytest.raises(ValueError, match="changed or exceeds"):
        desktop.inspect(context)
    def changed(index, entry, destination):
        destination.write_bytes(b"abc")
        archive.write_bytes(b"concurrent archive change")
        return destination
    monkeypatch.setattr(rpf_tools, "RpfExplorerService", lambda *args: SimpleNamespace(index=lambda path: index, extract=changed))
    with pytest.raises(ValueError, match="changed or exceeds"):
        desktop.inspect(context)


def test_map_non_ipl_source_validation_does_not_invent_required_ymaps(tmp_path):
    document = map_payload()
    document["streaming"]["mode"] = "none"
    document["streaming"]["content_group"] = None
    document["streaming"]["ipls"] = []
    for level in document["levels"]:
        level["ipls"] = []
    descriptor = tmp_path / "maps.json"
    desktop.apply(reviewed({"module": "maps"}, "create", destination=str(descriptor), document=document))
    source = tmp_path / "source"
    source.mkdir()
    (source / "notes.txt").write_text("owned map source fixture")
    result = desktop.inspect({"module": "maps", "descriptor": str(descriptor), "source": str(source)})
    assert not any("ymap" in str(issue).lower() for issue in result["inventory"].get("findings", []))
