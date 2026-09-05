import hashlib
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
