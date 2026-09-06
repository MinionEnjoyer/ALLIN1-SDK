from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk import archive_browser as browser
from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope, PROTOCOL_VERSION
from allin1_sdk.rpf_tools import RpfEntryRecord, RpfArchiveRecord, RpfIndex


def setup_archive(tmp_path, monkeypatch):
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"original")
    entries = (
        RpfEntryRecord("::common", "", "common", "common", "directory", 0, 0),
        RpfEntryRecord("::common/text.gxt2", "", "common/text.gxt2", "text.gxt2", "binary", 16, 16),
        RpfEntryRecord("::child.rpf", "", "child.rpf", "child.rpf", "archive", 20, 20),
        RpfEntryRecord("child.rpf::text.gxt2", "child.rpf", "text.gxt2", "text.gxt2", "binary", 18, 18),
    )
    index = RpfIndex(archive, "Legacy", 8, (
        RpfArchiveRecord("", "update.rpf", 7, "none", 8, 3),
        RpfArchiveRecord("child.rpf", "child.rpf", 7, "none", 20, 1),
    ), entries)
    monkeypatch.setattr(browser, "RpfExplorerService", lambda *_: SimpleNamespace(index=lambda _: index))
    return {"root": str(tmp_path), "gta_path": str(tmp_path), "path": archive.name}


def test_folder_navigation_and_paging(tmp_path):
    folder = tmp_path / "version.1"
    folder.mkdir()
    for i in range(105):
        (folder / f"file-{i:03}.txt").write_text(str(i))
    request = {"root": str(tmp_path), "path": "version.1"}
    page = browser.browse(request)
    assert page["parent"] == {"path": "", "layer": "", "directory": ""}
    assert len(page["entries"]) == 100 and page["has_more"]
    assert page["entries"][0]["name"] == "file-000.txt"
    next_page = browser.browse({**request, "offset": 100})
    assert len(next_page["entries"]) == 5 and not next_page["has_more"]
    nested = folder / "nested"
    nested.mkdir()
    assert browser.browse({**request, "path": "version.1/nested"})["parent"]["path"] == "version.1"


def test_exact_nested_archive_navigation(tmp_path, monkeypatch):
    request = setup_archive(tmp_path, monkeypatch)
    root = browser.browse(request)
    assert {e["name"] for e in root["entries"]} == {"common", "child.rpf"}
    child = next(e for e in root["entries"] if e["name"] == "child.rpf")
    nested = browser.browse({**request, **child["location"]})
    assert nested["entries"][0]["entry_id"] == "child.rpf::text.gxt2"
    assert nested["entries"][0]["edition"] == "Legacy"
    assert nested["parent"] == {"path": "update.rpf", "layer": "", "directory": ""}
    common = browser.browse({**request, "directory": "common"})
    assert common["entries"][0]["entry_id"] == "::common/text.gxt2"
    assert (tmp_path / "update.rpf").read_bytes() == b"original"


@pytest.mark.parametrize("patch", [{"path": "../escape"}, {"path": "C:/escape"}, {"layer": "../bad"}, {"directory": "missing"}, {"layer": "absent.rpf"}, {"offset": -1}, {"offset": True}])
def test_invalid_browser_locations_fail_closed(tmp_path, monkeypatch, patch):
    request = setup_archive(tmp_path, monkeypatch)
    with pytest.raises((ValueError, FileNotFoundError)):
        browser.browse({**request, **patch})


def test_recursive_search_keeps_duplicate_names_and_exact_origins(tmp_path, monkeypatch):
    request = setup_archive(tmp_path, monkeypatch)
    result = browser.search({**request, "query": "text.gxt2"})
    assert result["matched_count"] == 2
    assert len({e["id"] for e in result["entries"]}) == 2
    assert result["scan_complete"]
    assert result["scanned_archives"] == 1
    assert result["game_write_performed"] is False


def test_search_scope_and_missing_decoder_are_explicit(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (tmp_path / "car.txt").write_text("stock")
    (mods / "car.txt").write_text("mods")
    (tmp_path / "dlc.rpf").write_bytes(b"RPF7")
    request = {"root": str(tmp_path), "query": "car"}
    stock = browser.search({**request, "scope": "source"})
    assert [e["path"] for e in stock["entries"]] == ["car.txt"]
    assert not stock["scan_complete"]
    assert any("choose the matching GTA" in s for s in stock["warnings"])
    modded = browser.search({**request, "scope": "mods"})
    assert [e["path"] for e in modded["entries"]] == ["mods/car.txt"]
    assert modded["scan_complete"]


def test_scan_limit_reports_incomplete_coverage(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"item-{i}").write_text("")
    monkeypatch.setattr(browser, "MAX_FILES", 2)
    result = browser.search({"root": str(tmp_path), "query": "item"})
    assert result["matched_count"] == 2
    assert not result["scan_complete"]
    assert result["warnings"]


def test_directory_link_is_never_traversed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("not a browser member")
    try:
        (root / "redirect").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    result = browser.search({"root": str(root), "query": "private"})
    assert result["matched_count"] == 0
    assert not result["scan_complete"]
    with pytest.raises(ValueError):
        browser.browse({"root": str(root), "path": "redirect"})


def test_browser_is_wired_through_real_desktop_protocol(tmp_path):
    (tmp_path / "test.meta").write_text("<root/>")
    service = DesktopProtocolService()
    handshake = service.handle(envelope("handshake", {"client": {"name": "browser-test", "version": "1"}, "supported_versions": [PROTOCOL_VERSION]}, request_id="handshake", terminal=False))[0]
    assert handshake["operation"] == "result", handshake
    result = service.handle(envelope("browse_game_files", {"root": str(tmp_path)}, request_id="browse", terminal=False))[0]
    assert result["operation"] == "result", result
    assert result["risk"] == "read_only"
    assert result["payload"]["result"]["entries"][0]["name"] == "test.meta"
