import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk import rpf_utility_desktop as desktop


class FakeIndex:
    edition = "Enhanced"
    archives = (object(),)
    entries = ()

    def entry(self, value):
        if value == "file-id":
            return SimpleNamespace(id=value, archive_path="", path="x/data.ydr", name="data.ydr", kind="resource", size=4)
        if value == "dir-id":
            return SimpleNamespace(id=value, archive_path="nested.rpf", path="x", name="x", kind="directory", size=0)
        if value == "text-id":
            return SimpleNamespace(id=value, archive_path="", path="x/readme.txt", name="readme.txt", kind="binary", size=4)
        if value == "nested-id":
            return SimpleNamespace(id=value, archive_path="child.rpf", path="x/data.ydr", name="data.ydr", kind="resource", size=4)
        raise ValueError("unknown entry")


class FakeService:
    def __init__(self, *_args, **_kwargs):
        self.index_value = FakeIndex()

    def index(self, _archive):
        return self.index_value

    def extract(self, _index, _entry, destination):
        destination.write_bytes(b"data")
        return destination

    def extract_subtree(self, _index, destination, **_scope):
        destination.mkdir()
        (destination / ".allin1-rpf-export.json").write_text("{}")
        return destination

    def export_native_workspace(self, _index, _entry, destination):
        destination.mkdir()
        (destination / "native-workspace.json").write_text("{}")
        return destination

    def compare_indexes(self, *_args, **_kwargs):
        return {"summary": {"added": 0, "removed": 0, "modified": 0}}

    def export_diff(self, report, destination):
        destination.write_text(str(report))
        markdown = destination.with_suffix(".md")
        markdown.write_text("no changes")
        return destination, markdown

    def verify_archive_integrity(self, _index, destination):
        report = {"status": "PASS", "summary": {"payloads_exactly_extracted": 1}}
        destination.write_text(str(report))
        return destination, report

    def defragment_verified_copy(self, _index, destination, report_path):
        destination.write_bytes(b"RPF7")
        report_path.write_text("{}")
        return destination, report_path, {"summary": {"bytes_saved": 0}}


@pytest.fixture
def roots(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    source = tmp_path / "source.rpf"
    source.write_bytes(b"RPF7 source")
    other = tmp_path / "other.rpf"
    other.write_bytes(b"RPF7 other")
    monkeypatch.setattr(desktop, "RpfExplorerService", FakeService)
    monkeypatch.setattr(desktop, "project_root", lambda: tmp_path)
    return game, source, other


def test_selection_export_preserves_duplicate_names_in_distinct_layers(roots):
    import json
    game, source, _ = roots
    destination = source.parent / "selection"
    payload = {"action": "extract_selection", "archive": str(source), "gta_path": str(game), "destination": str(destination), "entry_ids": ["file-id", "nested-id"]}
    review = desktop.review(payload)
    assert [row["id"] for row in review["selection"]] == payload["entry_ids"]
    assert len({row["output"] for row in review["selection"]}) == 2
    result = desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    manifest = json.loads(Path(result["evidence"]["manifest"]).read_text())
    assert len(manifest["entries"]) == 2
    for row in manifest["entries"]:
        assert (destination / row["output"]).read_bytes() == b"data"
    assert source.read_bytes() == b"RPF7 source"


@pytest.mark.parametrize("selection", [[], ["file-id", "file-id"], ["dir-id"], ["missing"], ["x"] * 129])
def test_selection_export_rejects_invalid_or_ambiguous_intake(roots, selection):
    game, source, _ = roots
    with pytest.raises((ValueError, KeyError)):
        desktop.review({"action": "extract_selection", "archive": str(source), "gta_path": str(game), "destination": str(source.parent / "output"), "entry_ids": selection})


def test_selection_export_failure_preserves_raced_user_destination(roots, monkeypatch):
    game, source, _ = roots
    destination = source.parent / "selection"
    payload = {"action": "extract_selection", "archive": str(source), "gta_path": str(game), "destination": str(destination), "entry_ids": ["file-id"]}
    review = desktop.review(payload)
    def race(self, index, entry, output):
        destination.mkdir()
        (destination / "user.txt").write_text("do not delete")
        output.write_bytes(b"data")
        return output
    monkeypatch.setattr(FakeService, "extract", race)
    with pytest.raises(ValueError, match="already exists"):
        desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert (destination / "user.txt").read_text() == "do not delete"


@pytest.mark.parametrize("action,extension", [
    ("extract_entry", ".bin"), ("export_native_workspace", ""), ("extract_subtree", ""),
    ("extract_archive", ""), ("compare", ".json"),
    ("verify_integrity", ".json"), ("defragment_copy", ".rpf"),
])
def test_review_and_apply_every_rpf_utility_happy_path(roots, action, extension):
    game, source, other = roots
    destination = source.parent / f"output-{action}{extension}"
    payload = {"action": action, "archive": str(source), "gta_path": str(game), "destination": str(destination)}
    if action in {"extract_entry", "export_native_workspace"}:
        payload["entry_id"] = "file-id"
    elif action == "extract_subtree":
        payload["entry_id"] = "dir-id"
    elif action == "compare":
        payload.update(compare_archive=str(other), comparison_mode="logical")
    review = desktop.review(payload)
    assert review["ready"] is True
    assert review["source_write_performed"] is False
    result = desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["output_write_performed"] is True
    assert result["game_write_performed"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == review["archive_sha256"]
    assert result["destination"] == str(destination)
    assert "allin1-rpf-utility-" not in str(result["evidence"])
    assert destination.exists()
    assert not list(source.parent.glob("allin1-rpf-utility-*"))


@pytest.mark.parametrize("action,method,extension", [
    ("extract_entry", "extract", ".bin"),
    ("export_native_workspace", "export_native_workspace", ""),
    ("extract_subtree", "extract_subtree", ""),
    ("extract_archive", "extract_subtree", ""),
    ("compare", "export_diff", ".json"),
    ("verify_integrity", "verify_archive_integrity", ".json"),
    ("defragment_copy", "defragment_verified_copy", ".rpf"),
])
@pytest.mark.parametrize("fail", [False, True])
def test_every_utility_preserves_raced_user_destinations(roots, monkeypatch, action, method, extension, fail):
    game, source, other = roots
    destination = source.parent / f"raced{extension}"
    payload = {"action": action, "archive": str(source), "gta_path": str(game),
               "destination": str(destination), "entry_id": "dir-id" if action == "extract_subtree" else "file-id",
               "compare_archive": str(other)}
    review = desktop.review(payload)
    original = getattr(FakeService, method)
    def race(*args, **kwargs):
        destination.mkdir()
        (destination / "user.txt").write_text("keep my data")
        if fail:
            raise RuntimeError("converter failed")
        return original(*args, **kwargs)
    monkeypatch.setattr(FakeService, method, race)
    with pytest.raises((ValueError, RuntimeError), match="already exists|converter failed"):
        desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert (destination / "user.txt").read_text() == "keep my data"
    assert source.read_bytes() == b"RPF7 source"
    assert not list(source.parent.glob("allin1-rpf-utility-*"))


def test_late_companion_race_keeps_first_output_and_user_file(roots, monkeypatch):
    game, source, other = roots
    destination = source.parent / "diff.json"
    companion = destination.with_suffix(".md")
    payload = {"action": "compare", "archive": str(source), "gta_path": str(game),
               "destination": str(destination), "compare_archive": str(other)}
    review = desktop.review(payload)
    original_rename, original_link = Path.rename, desktop.os.link
    def race_rename(self, target):
        output = original_rename(self, target)
        if Path(target) == destination:
            companion.write_text("user report")
        return output
    def race_link(self, target):
        output = original_link(self, target)
        if Path(target) == destination:
            companion.write_text("user report")
        return output
    monkeypatch.setattr(Path, "rename", race_rename)
    monkeypatch.setattr(desktop.os, "link", race_link)
    with pytest.raises(RuntimeError, match="Retained outputs") as error:
        desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert str(destination) in str(error.value)
    assert destination.is_file()
    assert companion.read_text() == "user report"
    assert not list(source.parent.glob("allin1-rpf-utility-*"))


def test_rpf_utility_rejects_game_outputs_stale_reviews_and_existing_destinations(roots):
    game, source, _other = roots
    payload = {
        "action": "extract_entry", "archive": str(source), "gta_path": str(game),
        "entry_id": "file-id", "destination": str(source.parent / "copy.bin"),
    }
    review = desktop.review(payload)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after review"):
        desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    outside = source.parent / "canary.txt"
    outside.write_text("keep")
    with pytest.raises(ValueError, match="inside GTA"):
        desktop.review({**payload, "destination": str(game / "escape.bin")})
    existing = source.parent / "exists.bin"
    existing.write_text("keep")
    with pytest.raises(ValueError, match="already exists"):
        desktop.review({**payload, "destination": str(existing)})
    assert outside.read_text() == "keep"
    assert existing.read_text() == "keep"


def test_rpf_utility_removes_only_its_new_output_if_source_changes_during_work(roots, monkeypatch):
    game, source, _other = roots
    destination = source.parent / "new-output.bin"
    payload = {"action": "extract_entry", "archive": str(source), "gta_path": str(game), "entry_id": "file-id", "destination": str(destination)}
    review = desktop.review(payload)
    original_extract = FakeService.extract
    def changing_extract(self, index, entry, output):
        result = original_extract(self, index, entry, output)
        source.write_bytes(b"changed during extraction")
        return result
    monkeypatch.setattr(FakeService, "extract", changing_extract)
    canary = source.parent / "outside-canary.txt"
    canary.write_text("keep")
    with pytest.raises(RuntimeError, match="changed while producing"):
        desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert not destination.exists()
    assert canary.read_text() == "keep"


def test_rpf_utility_rejects_unreviewed_and_wrong_entry_operations(roots):
    game, source, other = roots
    base = {"archive": str(source), "gta_path": str(game), "destination": str(source.parent / "output.bin")}
    with pytest.raises(ValueError, match="review payload"):
        desktop.review(None)
    with pytest.raises(ValueError, match="Unsupported"):
        desktop.review({**base, "action": "unknown"})
    with pytest.raises(ValueError, match="apply payload"):
        desktop.apply(None)
    with pytest.raises(ValueError, match="reviewed evidence"):
        desktop.apply({**base, "action": "extract_entry", "entry_id": "file-id"})
    review = desktop.review({**base, "action": "extract_entry", "entry_id": "file-id"})
    with pytest.raises(ValueError, match="action-time confirmation"):
        desktop.apply({**base, "action": "extract_entry", "entry_id": "file-id", "review_sha256": review["review_sha256"]})
    with pytest.raises(ValueError, match="directory must be exported"):
        desktop.review({**base, "action": "extract_entry", "entry_id": "dir-id"})
    with pytest.raises(ValueError, match="Subtree export requires"):
        desktop.review({**base, "action": "extract_subtree", "entry_id": "file-id"})
    with pytest.raises(ValueError, match="Editable native export"):
        desktop.review({**base, "action": "export_native_workspace", "entry_id": "text-id"})
    with pytest.raises(ValueError, match="different RPF"):
        desktop.review({**base, "action": "compare", "destination": str(source.parent / "compare.json"), "compare_archive": str(source)})
    with pytest.raises(ValueError, match="metadata, logical, or exact"):
        desktop.review({**base, "action": "compare", "destination": str(source.parent / "compare.json"), "compare_archive": str(other), "comparison_mode": "bytes"})


@pytest.mark.parametrize("action,name,message", [
    ("compare", "report.txt", "must use a .json"),
    ("verify_integrity", "report.txt", "must use a .json"),
    ("defragment_copy", "copy.bin", "must use a .rpf"),
])
def test_rpf_utility_requires_action_specific_output_extensions(roots, action, name, message):
    game, source, other = roots
    payload = {"action": action, "archive": str(source), "gta_path": str(game), "destination": str(source.parent / name)}
    if action == "compare":
        payload["compare_archive"] = str(other)
    with pytest.raises(ValueError, match=message):
        desktop.review(payload)


def test_rpf_utility_rejects_existing_companion_output(roots):
    game, source, other = roots
    destination = source.parent / "comparison.json"
    destination.with_suffix(".md").write_text("keep")
    with pytest.raises(ValueError, match="companion output already exists"):
        desktop.review({
            "action": "compare", "archive": str(source), "gta_path": str(game),
            "destination": str(destination), "compare_archive": str(other),
        })


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires actual built RpfPatcher/CodeWalker")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_real_staged_utility_outputs_remain_usable_after_publication(tmp_path, edition):
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    from allin1_sdk.texture_workspace import TextureDictionaryWorkspace
    from test_texture_workspace import _workspace
    fixture = _workspace(tmp_path)
    game = tmp_path / "decoder"
    game.mkdir()
    (game / ("GTA5.exe" if edition == "Legacy" else "GTA5_Enhanced.exe")).write_bytes(b"MZ-test-decoder-only")
    payload_dir = tmp_path / "payload"
    (payload_dir / "textures").mkdir(parents=True)
    patcher = project_root() / "tools/RpfPatcher/RpfPatcher.exe"
    def command(*args):
        result = run_hidden([str(patcher), *map(str, args)], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr or result.stdout
    native = payload_dir / "textures/fixture.ytd"
    command("asset-from-xml", fixture / "edit/vehicle.ytd.xml", native, fixture / "edit/assets", "legacy" if edition == "Legacy" else "gen9")
    archive = tmp_path / "input.rpf"
    command("build-dlc", payload_dir, archive)
    other = tmp_path / "other.rpf"
    other.write_bytes(archive.read_bytes())
    original = hashlib.sha256(archive.read_bytes()).hexdigest()
    for action, name in [
        ("extract_entry", "copied.ytd"), ("extract_selection", "selected"),
        ("export_native_workspace", "editable"), ("extract_subtree", "subtree"),
        ("extract_archive", "tree"), ("compare", "comparison.v1.json"),
        ("verify_integrity", "integrity.json"), ("defragment_copy", "compact.rpf"),
    ]:
        destination = tmp_path / name
        payload = {"action": action, "archive": str(archive), "gta_path": str(game),
                   "destination": str(destination), "compare_archive": str(other),
                   "entry_id": "::textures" if action == "extract_subtree" else "::textures/fixture.ytd",
                   "entry_ids": ["::textures/fixture.ytd"]}
        reviewed = desktop.review(payload)
        result = desktop.apply({**payload, "review_sha256": reviewed["review_sha256"], "authoring_confirmed": True})
        assert destination.exists()
        assert "allin1-rpf-utility-" not in str(result["evidence"])
        if action == "export_native_workspace":
            workspace = TextureDictionaryWorkspace(destination)
            assert len(workspace.catalog().textures) == 2
        if action == "extract_entry":
            assert destination.read_bytes() == native.read_bytes()
        if action == "compare":
            assert destination.with_suffix(".md").is_file()
        if action == "defragment_copy":
            report_path = Path(result["evidence"]["report"])
            report = json.loads(report_path.read_text())
            assert report["output"]["path"] == str(destination)
            assert result["evidence"]["report_sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == original
    assert not list(tmp_path.glob("allin1-rpf-utility-*"))
