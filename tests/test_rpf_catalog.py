from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk import rpf_catalog
from allin1_sdk.cli import main
from allin1_sdk.rpf_catalog import RpfCatalogService
from allin1_sdk.rpf_tools import (
    RpfArchiveRecord, RpfEntryRecord, RpfIndex,
)


def _index(source: Path) -> RpfIndex:
    return RpfIndex(
        source=source.resolve(), edition="Enhanced",
        archive_size=source.stat().st_size,
        archives=(
            RpfArchiveRecord("", source.name, 7, "OPEN", source.stat().st_size, 3),
            RpfArchiveRecord("x64/models.rpf", "models.rpf", 7, "OPEN", 12, 1),
        ),
        entries=(
            RpfEntryRecord(
                "::common", "", "common", "common", "directory", 0, 0,
                child_count=1,
            ),
            RpfEntryRecord(
                "::common/data/setup.xml", "", "common/data/setup.xml", "setup.xml",
                "binary", 8, 8,
            ),
            RpfEntryRecord(
                "::x64/models.rpf", "", "x64/models.rpf", "models.rpf",
                "archive", 12, 12,
            ),
            RpfEntryRecord(
                "x64/models.rpf::vehicles/example.ydr", "x64/models.rpf",
                "vehicles/example.ydr", "example.ydr", "resource", 10, 9,
                resource_version=165,
            ),
        ),
    )


def _service(tmp_path: Path, monkeypatch):
    calls: list[Path] = []

    class FakeExplorer:
        def __init__(self, _project, _game):
            pass

        def index(self, archive):
            source = Path(archive).resolve()
            calls.append(source)
            if source.name == "broken.rpf":
                raise ValueError("bad archive")
            return _index(source)

    monkeypatch.setattr(rpf_catalog, "RpfExplorerService", FakeExplorer)
    game = tmp_path / "game"
    game.mkdir()
    return RpfCatalogService(tmp_path / "project", game), calls


def test_catalog_build_search_incremental_cache_and_refresh(tmp_path, monkeypatch):
    service, calls = _service(tmp_path, monkeypatch)
    source = tmp_path / "archives"
    (source / "packs").mkdir(parents=True)
    first = source / "a.rpf"
    second = source / "packs" / "b.rpf"
    first.write_bytes(b"RPF7-a")
    second.write_bytes(b"RPF7-b")
    database = tmp_path / "catalog.sqlite"
    progress: list[tuple[str, int]] = []

    written, summary = service.build(
        source, database, progress=lambda message, percent: progress.append(
            (message, percent)
        ),
    )
    assert written == database.resolve()
    assert summary["indexed"] == 2
    assert summary["cached"] == 0
    assert len(calls) == 2
    assert progress[-1] == ("RPF catalog ready", 100)

    results = service.search(database, "example", suffix="ydr")
    assert len(results) == 2
    assert {Path(result.outer_archive).name for result in results} == {"a.rpf", "b.rpf"}
    assert all(result.resource_version == 165 for result in results)
    assert service.search(database, "setup", kind="binary")[0].entry_path.endswith("setup.xml")
    report = service.export_results(results, tmp_path / "search.json", query="example")
    assert json.loads(report.read_text(encoding="utf-8"))["result_count"] == 2

    _, cached = service.build(source, database)
    assert cached["cached"] == 2
    assert cached["indexed"] == 0
    assert len(calls) == 2

    first.write_bytes(b"RPF7-a-changed")
    os.utime(first, None)
    _, updated = service.build(source, database)
    assert updated["cached"] == 1
    assert updated["indexed"] == 1
    assert len(calls) == 3

    _, refreshed = service.build(source, database, refresh=True)
    assert refreshed["cached"] == 0
    assert refreshed["indexed"] == 2
    assert len(calls) == 5


def test_catalog_records_unreadable_archive_without_losing_good_results(
    tmp_path, monkeypatch,
):
    service, _calls = _service(tmp_path, monkeypatch)
    source = tmp_path / "archives"
    source.mkdir()
    (source / "good.rpf").write_bytes(b"good")
    (source / "broken.rpf").write_bytes(b"broken")
    database, summary = service.build(source, tmp_path / "catalog.db")
    assert summary["failed"] == 1
    assert summary["indexed"] == 1
    assert len(service.search(database, "example")) == 1


def test_catalog_guards_database_location_schema_limits_and_empty_sources(
    tmp_path, monkeypatch,
):
    service, _calls = _service(tmp_path, monkeypatch)
    source = tmp_path / "archives"
    source.mkdir()
    with pytest.raises(ValueError, match="no loose"):
        service.build(source, tmp_path / "empty.sqlite")
    (source / "test.rpf").write_bytes(b"rpf")
    with pytest.raises(ValueError, match="outside the GTA V"):
        service.build(source, service.gta_path / "catalog.sqlite")
    invalid = tmp_path / "invalid.sqlite"
    invalid.write_bytes(b"not sqlite")
    with pytest.raises(ValueError, match="Invalid RPF catalog"):
        service.search(invalid, "test")
    database, _ = service.build(source, tmp_path / "valid.sqlite")
    with pytest.raises(ValueError, match="limit"):
        service.search(database, "test", limit=0)


def test_catalog_cli_build_and_search_routes(tmp_path, monkeypatch):
    source = tmp_path / "archives"
    source.mkdir()
    (source / "test.rpf").write_bytes(b"rpf")
    game = tmp_path / "game"
    game.mkdir()
    database = tmp_path / "catalog.sqlite"
    report = tmp_path / "results.json"

    class FakeCatalog:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game

        def build(self, selected_source, output, *, refresh, progress):
            assert Path(selected_source) == source
            assert refresh is True
            Path(output).write_bytes(b"sqlite")
            progress("done", 100)
            return Path(output), {
                "archives": 1, "indexed": 1, "cached": 0, "failed": 0,
            }

        @classmethod
        def search(cls, selected_catalog, query, *, kind, suffix, limit):
            assert Path(selected_catalog) == database
            assert (query, kind, suffix, limit) == ("model", "resource", "ydr", 12)
            return (rpf_catalog.RpfCatalogResult(
                str(source / "test.rpf"), "x64/models.rpf", "example.ydr",
                "resource", 10, ".ydr", "Enhanced", 165,
            ),)

        @classmethod
        def export_results(cls, results, output, *, query):
            assert len(results) == 1 and query == "model"
            Path(output).write_text("{}", encoding="utf-8")
            return Path(output)

    monkeypatch.setattr("allin1_sdk.cli.RpfCatalogService", FakeCatalog)
    runner = CliRunner()
    built = runner.invoke(main, [
        "sdk", "catalog-rpfs", str(source), "--gta-path", str(game),
        "--refresh", "-o", str(database),
    ])
    assert built.exit_code == 0, built.output
    assert "1 indexed" in built.output
    searched = runner.invoke(main, [
        "sdk", "search-rpf-catalog", str(database), "model",
        "--kind", "resource", "--suffix", "ydr", "--limit", "12",
        "-o", str(report),
    ])
    assert searched.exit_code == 0, searched.output
    assert "Found 1" in searched.output
    assert report.is_file()
