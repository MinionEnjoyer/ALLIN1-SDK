from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from allin1_sdk.map_project import MapProjectResolver
from allin1_sdk.rpf_tools import (
    RpfArchiveRecord,
    RpfEntryRecord,
    RpfIndex,
)


def _map_source(root: Path, *, placement: bool = True) -> Path:
    source = root / "map-source"
    stream = source / "stream"
    stream.mkdir(parents=True)
    if placement:
        (stream / "example.ymap").write_bytes(b"ymap")
    (stream / "example.ytyp").write_bytes(b"ytyp")
    (stream / "example.ydr").write_bytes(b"ydr")
    (stream / "example.ytd").write_bytes(b"ytd")
    (stream / "example.ybn").write_bytes(b"ybn")
    (stream / "example.ynv").write_bytes(b"ynv")
    (stream / "readme.txt").write_text("not a map asset", encoding="utf-8")
    return source


def _rpf_index(
    root: Path,
    placements: tuple[tuple[str, str], ...],
    *,
    source: Path | None = None,
) -> RpfIndex:
    archive_paths = tuple(dict.fromkeys(item[0] for item in placements))
    archives = [RpfArchiveRecord("", "dlc.rpf", 8, "none", 4096, 0)]
    archives.extend(
        RpfArchiveRecord(path, Path(path).name, 8, "none", 2048, 1)
        for path in archive_paths if path
    )
    entries = tuple(
        RpfEntryRecord(
            id=f"{archive_path}::{entry_path}",
            archive_path=archive_path,
            path=entry_path,
            name=PurePosixPath(entry_path).name,
            kind="resource",
            size=128,
            stored_size=96,
            name_hash=index + 100,
            short_name_hash=index + 200,
        )
        for index, (archive_path, entry_path) in enumerate(placements)
    )
    return RpfIndex(
        source=source or root / "dlc.rpf",
        edition="Enhanced",
        archive_size=4096,
        archives=tuple(archives),
        entries=entries,
    )


def test_map_project_resolver_reports_map_roles_and_stable_fingerprint(tmp_path):
    source = _map_source(tmp_path)
    resolver = MapProjectResolver()
    report = resolver.inspect(source)

    assert report.valid
    assert report.role_counts == {
        "archetypes": 1,
        "collision": 1,
        "drawable": 1,
        "navigation_mesh": 1,
        "placement": 1,
        "texture_dictionary": 1,
    }
    assert len(report.inventory_fingerprint) == 64
    assert all(not item.path.endswith("readme.txt") for item in report.assets)
    assert resolver.inspect(source).inventory_fingerprint == report.inventory_fingerprint

    output = tmp_path / "report"
    report_path = report.write(output)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["valid"] is True
    assert payload["summary"]["roles"]["placement"] == 1


def test_map_project_resolver_requires_a_placement_asset(tmp_path):
    report = MapProjectResolver().inspect(_map_source(tmp_path, placement=False))

    assert not report.valid
    assert report.error_count == 1
    assert {item.code for item in report.findings} == {"missing_ymap"}


def test_detect_index_reports_ymap_stems_and_recursive_provenance(tmp_path):
    index = _rpf_index(tmp_path, (
        ("x64/levels/maps.rpf", "maps/davis_garage.ymap"),
        ("x64/levels/maps.rpf", "maps/davis_garage.ytyp"),
        ("x64/levels/props.rpf", "placements/roof_props.ymap"),
    ))

    report = MapProjectResolver.detect_index(
        index,
        pack_name="mptuner",
        source="update/x64/dlcpacks/mptuner/dlc.rpf",
        discovery_source="game_installation",
        expected_ipls=("davis_garage",),
    )

    assert report.valid
    assert [item.name for item in report.placements] == [
        "davis_garage", "roof_props",
    ]
    assert report.matches[0].status == "exact"
    assert report.matches[0].resolved is not None
    assert report.matches[0].resolved.archive_path == "x64/levels/maps.rpf"
    payload = report.to_dict()
    assert payload["source"] == "update/x64/dlcpacks/mptuner/dlc.rpf"
    assert str(tmp_path) not in json.dumps(payload)
    assert len(report.inventory_fingerprint) == 64


def test_detect_index_accepts_only_one_semantic_candidate(tmp_path):
    report = MapProjectResolver.detect_index(
        _rpf_index(tmp_path, (
            ("x64/maps.rpf", "maps/davis_auto_shop_garage_shell.ymap"),
            ("x64/maps.rpf", "maps/paleto_shop.ymap"),
        )),
        pack_name="fixture",
        source="update/x64/dlcpacks/fixture/dlc.rpf",
        discovery_source="game_installation",
        expected_ipls=("davis_auto_shop_garage",),
    )

    assert report.valid
    assert report.matches[0].status == "semantic_unique"
    assert report.matches[0].verified
    assert report.matches[0].resolved.name == "davis_auto_shop_garage_shell"
    assert {item.code for item in report.findings} == {"semantic_ipl_match"}


def test_detect_index_fails_closed_on_ambiguous_semantic_match(tmp_path):
    report = MapProjectResolver.detect_index(
        _rpf_index(tmp_path, (
            ("x64/maps.rpf", "maps/davis_auto_shop_garage_shell.ymap"),
            ("x64/props.rpf", "maps/davis_auto_shop_garage_props.ymap"),
        )),
        pack_name="fixture",
        source="update/x64/dlcpacks/fixture/dlc.rpf",
        discovery_source="game_installation",
        expected_ipls=("davis_auto_shop_garage",),
    )

    assert not report.valid
    assert report.matches[0].status == "ambiguous"
    assert report.matches[0].resolved is None
    assert len(report.matches[0].candidates) == 2
    assert {item.code for item in report.findings} == {
        "ambiguous_semantic_ipl",
    }


def test_detect_index_fails_closed_when_two_requests_resolve_to_one_placement(
    tmp_path,
):
    report = MapProjectResolver.detect_index(
        _rpf_index(tmp_path, ((
            "x64/maps.rpf", "maps/davis_auto_shop_garage_shell.ymap",
        ),)),
        pack_name="fixture",
        source="update/x64/dlcpacks/fixture/dlc.rpf",
        discovery_source="game_installation",
        expected_ipls=(
            "davis_auto_shop_garage_shell", "davis_auto_shop_garage",
        ),
    )

    assert not report.valid
    assert [item.status for item in report.matches] == [
        "duplicate_resolution", "duplicate_resolution",
    ]
    assert all(not item.verified for item in report.matches)
    assert "duplicate_resolved_ipl" in {item.code for item in report.findings}


def test_installed_detection_prefers_mods_overlay_and_keeps_source_portable(
    tmp_path, monkeypatch,
):
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"game")
    stock = game / "update" / "x64" / "dlcpacks" / "fixture" / "dlc.rpf"
    overlay = game / "mods" / "update" / "x64" / "dlcpacks" / "fixture" / "dlc.rpf"
    stock.parent.mkdir(parents=True)
    overlay.parent.mkdir(parents=True)
    stock.write_bytes(b"stock")
    overlay.write_bytes(b"overlay")

    def index(_service, archive):
        selected = Path(archive).resolve()
        return _rpf_index(
            tmp_path,
            (("x64/maps.rpf", "maps/fixture_map.ymap"),),
            source=selected,
        )

    monkeypatch.setattr("allin1_sdk.map_project.RpfExplorerService.index", index)
    report = MapProjectResolver().detect_installed_dlc(
        "fixture",
        project_root=tmp_path,
        gta_path=game,
        expected_ipls=("fixture_map",),
    )

    assert report.valid
    assert report.discovery_source == "mods_overlay"
    assert report.source == "mods/update/x64/dlcpacks/fixture/dlc.rpf"
    assert str(game) not in json.dumps(report.to_dict())


def test_installed_detection_merges_split_roots_with_per_sibling_overlay(
    tmp_path, monkeypatch,
):
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"game")
    stock_root = game / "update" / "x64" / "dlcpacks" / "mpbattle"
    mods_root = game / "mods" / "update" / "x64" / "dlcpacks" / "mpbattle"
    stock_root.mkdir(parents=True)
    mods_root.mkdir(parents=True)
    stock_dlc = stock_root / "dlc.rpf"
    stock_dlc1 = stock_root / "dlc1.rpf"
    overlay_dlc1 = mods_root / "dlc1.rpf"
    stock_dlc.write_bytes(b"stock root")
    stock_dlc1.write_bytes(b"stock split map")
    overlay_dlc1.write_bytes(b"overlay split map")
    calls = []

    def index(_service, archive):
        selected = Path(archive).resolve()
        calls.append(selected)
        placements = () if selected.name.casefold() == "dlc.rpf" else ((
            "x64/levels/gta5/interiors/int_placement_ba.rpf",
            "ba_int_placement_ba_interior_1_dlc_int_02_ba_milo_.ymap",
        ),)
        return _rpf_index(tmp_path, placements, source=selected)

    monkeypatch.setattr("allin1_sdk.map_project.RpfExplorerService.index", index)
    report = MapProjectResolver().detect_installed_dlc(
        "mpbattle",
        project_root=tmp_path,
        gta_path=game,
        expected_ipls=(
            "ba_int_placement_ba_interior_1_dlc_int_02_ba_milo_",
        ),
    )

    assert report.valid
    assert calls == [stock_dlc.resolve(), overlay_dlc1.resolve()]
    assert [item.source_rpf for item in report.root_archives] == [
        "update/x64/dlcpacks/mpbattle/dlc.rpf",
        "mods/update/x64/dlcpacks/mpbattle/dlc1.rpf",
    ]
    assert [item.placement_count for item in report.root_archives] == [0, 1]
    assert report.discovery_source == "mixed"
    assert report.matches[0].status == "exact"
    assert report.matches[0].resolved is not None
    assert report.matches[0].resolved.source_rpf == (
        "mods/update/x64/dlcpacks/mpbattle/dlc1.rpf"
    )
    payload = report.to_dict()
    assert payload["schema_version"] == 2
    assert payload["summary"]["root_archives"] == 2
    assert len(payload["root_archives"]) == 2
