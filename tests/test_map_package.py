from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from allin1_sdk.addon_importer import PackageEntry, PackageScan
from allin1_sdk.map_contract import MapProject
from allin1_sdk.map_package import MapAddonPackageBuilder
from allin1_sdk.mods import ModManifest

from test_map_contract import map_payload


def _prebuilt_map(root: Path, *, second: bool = False) -> Path:
    source = root / "downloaded-map"
    pack = source / "examplemap"
    stream = pack / "stream"
    stream.mkdir(parents=True)
    (pack / "dlc.rpf").write_bytes(b"RPF7-map-payload")
    (stream / "example.ymap").write_bytes(b"ymap-placement")
    (stream / "example.ytyp").write_bytes(b"ytyp-archetypes")
    (stream / "example.ybn").write_bytes(b"ybn-collision")
    if second:
        other = source / "other"
        other.mkdir()
        (other / "dlc.rpf").write_bytes(b"RPF7-second-map")
    return source


def _prebuilt_scan(source: Path) -> PackageScan:
    if source.is_dir():
        entries = tuple(
            PackageEntry(path.relative_to(source).as_posix(), path.stat().st_size)
            for path in source.rglob("*") if path.is_file()
        )
    else:
        with zipfile.ZipFile(source) as package:
            entries = tuple(
                PackageEntry(info.filename, info.file_size)
                for info in package.infolist() if not info.is_dir()
            )
    indexed = tuple(
        PackageEntry(f"{entry.path}::stream/{name}.ymap", 4)
        for entry in entries if Path(entry.path).name.casefold() == "dlc.rpf"
        for name in ("example_map_placement", "example_map_interior")
    )
    return PackageScan(
        source=source,
        source_kind="folder" if source.is_dir() else "zip",
        entries=entries,
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        rpf_indexed_entries=indexed,
    )


def _use_prebuilt_scan(monkeypatch) -> None:
    monkeypatch.setattr(
        "allin1_sdk.map_package.AddonPackageInspector.inspect",
        lambda _inspector, source: _prebuilt_scan(Path(source)),
    )


def test_map_package_builder_publishes_managed_single_edition_package(
    tmp_path, monkeypatch,
):
    _use_prebuilt_scan(monkeypatch)
    source = _prebuilt_map(tmp_path)
    source_snapshot = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }
    descriptor = MapProject.from_dict(map_payload())
    output = tmp_path / "published-map"

    result = MapAddonPackageBuilder(tmp_path).build(
        source, descriptor, output, edition="enhanced",
    )

    assert result.package_id == "example.custom-map"
    assert result.edition == "enhanced"
    assert result.source_mode == "prebuilt_dlc_rpf"
    assert result.payload.read_bytes() == b"RPF7-map-payload"
    assert json.loads(result.descriptor.read_text(encoding="utf-8")) == (
        descriptor.to_dict()
    )
    manifest = ModManifest.load(result.manifest)
    assert manifest.schema_version == 2
    assert manifest.editions == ("enhanced",)
    assert manifest.dlc_packs == ("examplemap",)
    assert manifest.dependencies == ("openrpf",)
    assert manifest.files[1].destination.as_posix() == (
        "scripts/ALLIN1/Maps/example.custom-map/maps.json"
    )
    assert manifest.extension is not None
    assert manifest.extension.capabilities == ("world.maps",)
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["status"] == "validated"
    assert report["safety"]["source_unchanged"] is True
    assert report["descriptor"]["sha256"] == result.descriptor_sha256
    serialized_report = result.report.read_text(encoding="utf-8")
    assert str(source.resolve()) not in serialized_report
    assert str(tmp_path.resolve()) not in serialized_report
    assert report["source"] == source.name
    assert report["map_project"]["source"] == source.name
    assert source_snapshot == {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*") if path.is_file()
    }

    with pytest.raises(FileExistsError, match="already exists"):
        MapAddonPackageBuilder(tmp_path).build(
            source, descriptor, output, edition="enhanced",
        )


def test_map_package_builder_rejects_ambiguous_and_undeclared_targets(
    tmp_path, monkeypatch,
):
    _use_prebuilt_scan(monkeypatch)
    descriptor = MapProject.from_dict(map_payload())
    ambiguous = _prebuilt_map(tmp_path / "ambiguous", second=True)
    with pytest.raises(ValueError, match="multiple dlc.rpf"):
        MapAddonPackageBuilder(tmp_path).build(
            ambiguous, descriptor, tmp_path / "ambiguous-output", edition="legacy",
        )

    payload = map_payload()
    payload["editions"] = ["legacy"]
    with pytest.raises(ValueError, match="does not declare support"):
        MapAddonPackageBuilder(tmp_path).build(
            _prebuilt_map(tmp_path / "edition"), payload,
            tmp_path / "edition-output", edition="enhanced",
        )


def test_map_package_builder_requires_visible_ymap_evidence(tmp_path):
    source = tmp_path / "no-placement"
    source.mkdir()
    (source / "dlc.rpf").write_bytes(b"RPF7-map-payload")
    with pytest.raises(ValueError, match="YMAP"):
        MapAddonPackageBuilder(tmp_path).build(
            source, map_payload(), tmp_path / "output", edition="legacy",
        )


def test_map_package_builder_reads_a_reviewed_zip_without_extracting_it(
    tmp_path, monkeypatch,
):
    _use_prebuilt_scan(monkeypatch)
    archive = tmp_path / "map-download.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("examplemap/dlc.rpf", b"RPF7-map-payload")
        package.writestr("examplemap/stream/example.ymap", b"ymap-placement")
        package.writestr("examplemap/stream/example.ybn", b"ybn-collision")

    output = tmp_path / "zip-output"
    result = MapAddonPackageBuilder(tmp_path).build(
        archive, map_payload(), output, edition="legacy",
    )

    assert result.source_mode == "prebuilt_dlc_rpf"
    assert result.payload.read_bytes() == b"RPF7-map-payload"
    assert result.edition == "legacy"


def test_map_package_builder_accepts_a_direct_inspected_rpf(tmp_path, monkeypatch):
    source = tmp_path / "custom-map.rpf"
    source.write_bytes(b"RPF7-direct-map")
    scan = PackageScan(
        source=source,
        source_kind="rpf",
        entries=(),
        findings=(),
        weapons=(),
        ammo=(),
        animation_weapons=(),
        shop_weapons=(),
        rpf_indexed_entries=(
            PackageEntry(
                "custom-map.rpf::stream/example_map_placement.ymap", 4,
            ),
            PackageEntry(
                "custom-map.rpf::stream/example_map_interior.ymap", 4,
            ),
        ),
    )
    monkeypatch.setattr(
        "allin1_sdk.map_package.AddonPackageInspector.inspect",
        lambda _inspector, _source: scan,
    )

    result = MapAddonPackageBuilder(tmp_path).build(
        source, map_payload(), tmp_path / "direct-output", edition="enhanced",
    )

    assert result.source_mode == "direct_rpf"
    assert result.payload.read_bytes() == b"RPF7-direct-map"
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["source_evidence"]["source"] == source.name
    assert str(source.resolve()) not in result.report.read_text(encoding="utf-8")


def test_map_package_builder_rejects_declared_ipls_missing_from_selected_rpf(
    tmp_path, monkeypatch,
):
    source = _prebuilt_map(tmp_path)
    scan = _prebuilt_scan(source)
    scan = PackageScan(
        **{
            **scan.__dict__,
            "rpf_indexed_entries": (
                PackageEntry(
                    "examplemap/dlc.rpf::stream/example_map_placement.ymap", 4,
                ),
            ),
        }
    )
    monkeypatch.setattr(
        "allin1_sdk.map_package.AddonPackageInspector.inspect",
        lambda _inspector, _source: scan,
    )

    with pytest.raises(ValueError, match="example_map_interior"):
        MapAddonPackageBuilder(tmp_path).build(
            source, map_payload(), tmp_path / "missing-ipl-output", edition="legacy",
        )


def test_map_package_builder_rejects_loose_ymap_evidence_for_an_unindexed_rpf(
    tmp_path, monkeypatch,
):
    source = _prebuilt_map(tmp_path)
    scan = _prebuilt_scan(source)
    scan = PackageScan(
        **{
            **scan.__dict__,
            "rpf_indexed_entries": (),
        }
    )
    monkeypatch.setattr(
        "allin1_sdk.map_package.AddonPackageInspector.inspect",
        lambda _inspector, _source: scan,
    )

    with pytest.raises(ValueError, match="does not expose an indexed YMAP"):
        MapAddonPackageBuilder(tmp_path).build(
            source, map_payload(), tmp_path / "rejected-output", edition="legacy",
        )
