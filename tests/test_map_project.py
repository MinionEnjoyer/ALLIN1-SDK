from __future__ import annotations

import json
from pathlib import Path

from allin1_sdk.map_project import MapProjectResolver


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
