from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk import native_assets, rpf_tools
from allin1_sdk.cli import main
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    NativeAssetInspector,
    native_preview_limit,
)
from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex


def _index_payload(source: Path, *, nested: bool = True) -> dict:
    archives = [{
        "path": "", "name": source.name, "version": 7,
        "encryption": "OPEN", "size": 900, "entry_count": 4,
    }]
    entries = [
        {
            "id": "::common", "archive_path": "", "path": "common",
            "name": "common", "kind": "directory", "size": 0,
            "stored_size": 0, "name_hash": 1, "short_name_hash": 1,
            "child_count": 2,
        },
        {
            "id": "::common/data/test.ymap", "archive_path": "",
            "path": "common/data/test.ymap", "name": "test.ymap",
            "kind": "resource", "size": 4096, "stored_size": 1024,
            "name_hash": 2, "short_name_hash": 3, "offset": 512,
            "encrypted": False, "resource_version": 2,
            "system_size": 3072, "graphics_size": 1024,
            "system_flags": "0x00000001", "graphics_flags": "0x00000002",
        },
        {
            "id": "::x64/textures.rpf", "archive_path": "",
            "path": "x64/textures.rpf", "name": "textures.rpf",
            "kind": "archive", "size": 500, "stored_size": 400,
            "name_hash": 4, "short_name_hash": 5, "compressed": True,
        },
    ]
    if nested:
        archives.append({
            "path": "x64/textures.rpf", "name": "textures.rpf", "version": 7,
            "encryption": "OPEN", "size": 500, "entry_count": 1,
        })
        entries.append({
            "id": "x64/textures.rpf::vehicle.ytd",
            "archive_path": "x64/textures.rpf", "path": "vehicle.ytd",
            "name": "vehicle.ytd", "kind": "resource", "size": 8192,
            "stored_size": 2048, "resource_version": 13,
            "name_hash": 6, "short_name_hash": 7,
        })
    return {
        "schema_version": 1, "source": str(source.resolve()),
        "edition": "Enhanced", "archive_size": 900,
        "archives": archives, "entries": entries,
        "warnings": ["test warning"],
    }


def _write_index(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "index.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_rpf_index_load_search_summary_and_export(tmp_path):
    source = tmp_path / "dlc.rpf"
    source.write_bytes(b"RPF7")
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(source)))

    assert index.source == source
    assert index.edition == "Enhanced"
    assert index.entry("X64/TEXTURES.RPF::VEHICLE.YTD").resource_version == 13
    assert index.search("vehicle")[0].virtual_name == "x64/textures.rpf::vehicle.ytd"
    assert [item.path for item in index.search(kinds=("RESOURCE",), suffix="ymap")] == [
        "common/data/test.ymap"
    ]
    assert index.suffix_counts() == {".ymap": 1, ".rpf": 1, ".ytd": 1}
    json_path, csv_path = index.export(tmp_path / "reports" / "archive")
    assert json_path.name == "archive.json" and csv_path.name == "archive.csv"
    assert '"resource_version": 13' in json_path.read_text(encoding="utf-8")
    assert "vehicle.ytd" in csv_path.read_text(encoding="utf-8-sig")
    with pytest.raises(KeyError, match="Unknown RPF entry"):
        index.entry("missing")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"schema_version": 2}, "schema"),
        ({"entries": []}, "Duplicate"),
        ({"entries": [{
            "id": "bad", "archive_path": "", "path": "../escape.bin",
            "name": "escape.bin", "kind": "binary", "size": 1, "stored_size": 1,
        }]}, "Unsafe"),
    ],
)
def test_rpf_index_rejects_unsupported_duplicate_and_unsafe_data(tmp_path, change, message):
    source = tmp_path / "test.rpf"
    payload = _index_payload(source, nested=False)
    if change.get("entries") == []:
        payload["entries"].append(dict(payload["entries"][0]))
    else:
        payload.update(change)
    with pytest.raises(ValueError, match=message):
        RpfIndex.load(_write_index(tmp_path, payload))


def test_rpf_index_rejects_invalid_json_and_missing_fields(tmp_path):
    invalid = tmp_path / "bad.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid RPF index"):
        RpfIndex.load(invalid)
    invalid.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed RPF index"):
        RpfIndex.load(invalid)


@pytest.mark.parametrize("field,value,message", [
    ("id", "forged", "does not match"),
    ("archive_path", "missing.rpf", "unknown archive"),
    ("kind", "executable", "Unknown RPF entry kind"),
    ("size", -1, "Negative RPF entry size"),
])
def test_rpf_index_rejects_forged_entry_contracts(tmp_path, field, value, message):
    source = tmp_path / "test.rpf"
    payload = _index_payload(source, nested=False)
    payload["entries"][1][field] = value
    if field == "archive_path":
        payload["entries"][1]["id"] = f"{value}::{payload['entries'][1]['path']}"
    with pytest.raises(ValueError, match=message):
        RpfIndex.load(_write_index(tmp_path, payload))


def _service(tmp_path: Path) -> tuple[RpfExplorerService, Path, Path]:
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    return RpfExplorerService(project, game), archive, patcher


def test_rpf_service_indexes_extracts_and_builds_plan(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)

    def fake_run(args, **_kwargs):
        if args[1] == "index-json":
            Path(args[4]).write_text(json.dumps(_index_payload(archive)), encoding="utf-8")
        elif args[1] == "extract-virtual-entry":
            Path(args[6]).write_bytes(b"native payload")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", fake_run)
    index = service.index(archive)
    entry = index.entry("x64/textures.rpf::vehicle.ytd")
    extracted = service.extract(index, entry, tmp_path / "out" / "vehicle.ytd")
    assert extracted.read_bytes() == b"native payload"

    replacement = tmp_path / "new.ytd"
    replacement.write_bytes(b"replacement")
    plan = service.replacement_plan(index, entry, replacement)
    assert plan["status"] == "plan_only"
    assert plan["payload_sha256"] == hashlib.sha256(b"replacement").hexdigest()
    assert len(plan["warnings"]) == 2
    assert plan["safety"]["writes_performed"] is False


def test_rpf_service_failure_paths_and_membership_checks(tmp_path, monkeypatch):
    service, archive, patcher = _service(tmp_path)
    patcher.unlink()
    with pytest.raises(FileNotFoundError, match="RpfPatcher"):
        service.index(archive)
    patcher.write_bytes(b"exe")
    with pytest.raises(ValueError, match="loose .rpf"):
        service.index(tmp_path / "missing.zip")

    def failed(*_args, **_kwargs):
        return SimpleNamespace(returncode=5, stdout="", stderr="bad archive")

    monkeypatch.setattr(rpf_tools, "run_hidden", failed)
    with pytest.raises(ValueError, match="bad archive"):
        service.index(archive)

    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    directory = index.entry("::common")
    with pytest.raises(ValueError, match="Directories"):
        service.extract(index, directory, tmp_path / "directory")
    with pytest.raises(ValueError, match="directory"):
        service.replacement_plan(index, directory, archive)
    with pytest.raises(FileNotFoundError, match="Replacement"):
        service.replacement_plan(index, index.entries[1], tmp_path / "missing")


def test_rpf_service_rejects_wrong_helper_source_and_extraction_failure(tmp_path, monkeypatch):
    service, archive, _ = _service(tmp_path)
    wrong = tmp_path / "wrong.rpf"

    def wrong_index(args, **_kwargs):
        Path(args[4]).write_text(json.dumps(_index_payload(wrong)), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rpf_tools, "run_hidden", wrong_index)
    with pytest.raises(ValueError, match="different archive"):
        service.index(archive)

    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))
    monkeypatch.setattr(
        rpf_tools, "run_hidden",
        lambda *_a, **_k: SimpleNamespace(returncode=5, stdout="", stderr="extract bad"),
    )
    with pytest.raises(ValueError, match="extract bad"):
        service.extract(index, index.entries[1], tmp_path / "out.bin")


def _gxt2() -> bytes:
    text = b"Hello Los Santos\0"
    text_offset = 24
    end = text_offset + len(text)
    return b"".join((
        b"GXT2", struct.pack("<I", 1), struct.pack("<II", 0x12345678, text_offset),
        b"GXT2", struct.pack("<I", end), text,
    ))


def test_native_asset_lightweight_gxt_dds_and_signatures(tmp_path):
    inspector = NativeAssetInspector(tmp_path)
    gxt = inspector.inspect_bytes("global.gxt2", _gxt2())
    assert gxt.format_name == "Rockstar GXT2 text table"
    assert gxt.metadata["label_count"] == 1
    assert "0x12345678  Hello Los Santos" in gxt.structured_text
    assert "RpfPatcher is not built" in gxt.warnings[0]

    dds = bytearray(128)
    dds[:4] = b"DDS "
    struct.pack_into("<II", dds, 12, 64, 128)
    struct.pack_into("<I", dds, 28, 5)
    dds[84:88] = b"DXT5"
    report = inspector.inspect_bytes("paint.dds", bytes(dds))
    assert report.metadata == {
        "dimensions": "128 × 64", "mip_levels": 5, "pixel_format": "DXT5",
    }

    rsc = inspector.inspect_bytes(
        "model.ydr", b"RSC8" + struct.pack("<III", 159, 1, 2), truncated=True,
    )
    assert rsc.metadata["resource_container"] == "RSC8"
    assert rsc.metadata["resource_version"] == 159
    assert "safety limit" in rsc.warnings[0]

    awc = inspector.inspect_bytes("sound.awc", b"ADAT" + b"\x01\0\0\0" + struct.pack("<I", 4))
    assert awc.metadata["endianness"] == "little"
    assert awc.metadata["stream_count"] == 4
    gfx = inspector.inspect_bytes("hud.gfx", b"CWS\x0A" + b"\0" * 10)
    assert gfx.metadata["scaleform_version"] == 10


def test_native_asset_helper_xml_and_texture_contact_sheet(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")

    def convert(args, **_kwargs):
        Path(args[3]).write_text("<TextureDictionary><Item /></TextureDictionary>", encoding="utf-8")
        assets = Path(args[4])
        assets.mkdir(parents=True)
        Image = pytest.importorskip("PIL.Image")
        Image.new("RGB", (32, 16), "red").save(assets / "diffuse.png")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    report = NativeAssetInspector(project).inspect_bytes("vehicle.ytd", b"RSC8" + b"\0" * 32)
    assert report.structured_text.startswith("<TextureDictionary>")
    assert report.metadata["exported_textures"] == 1
    assert report.image_png.startswith(b"\x89PNG")


def test_native_asset_conversion_failure_retries_other_edition(tmp_path, monkeypatch):
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"exe")
    calls = []

    def convert(args, **_kwargs):
        calls.append(args[-1])
        if args[-1] == "gen9":
            return SimpleNamespace(returncode=5, stdout="", stderr="wrong version")
        Path(args[3]).write_text("<Drawable />", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)
    report = NativeAssetInspector(project).inspect_bytes("prop.ydr", b"RSC7" + b"\0" * 20)
    assert calls == ["gen9", "legacy"]
    assert report.metadata["interpreted_edition"] == "Legacy"
    assert "parsed as Legacy" in report.warnings[0]


def test_native_preview_limits():
    assert native_preview_limit("model.yft", 20) == 21
    assert native_preview_limit("huge.yft", MAX_NATIVE_PREVIEW_BYTES + 10) == MAX_NATIVE_PREVIEW_BYTES
    assert native_preview_limit("huge.bin", 20 * 1024 * 1024) == 8 * 1024 * 1024


def test_new_rpf_cli_index_extract_and_plan(tmp_path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))

    class FakeService:
        def __init__(self, project_root, gta_path):
            assert Path(project_root).name == "ALLIN1-SDK"
            assert Path(gta_path) == game

        def index(self, source):
            assert Path(source) == archive
            return index

        def extract(self, loaded, entry, output):
            assert loaded is index and entry.path == "common/data/test.ymap"
            target = Path(output)
            target.write_bytes(b"extracted")
            return target

        def replacement_plan(self, loaded, entry, payload):
            assert loaded is index and entry.path == "common/data/test.ymap"
            return {"operation": "replace_rpf_entry", "status": "plan_only"}

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    runner = CliRunner()
    exported = runner.invoke(main, [
        "sdk", "index-rpf", str(archive), "--gta-path", str(game),
        "-o", str(tmp_path / "cli-index.json"),
    ])
    assert exported.exit_code == 0, exported.output
    assert "4 entries across 2 archive(s)" in exported.output
    assert (tmp_path / "cli-index.csv").is_file()

    extracted = runner.invoke(main, [
        "sdk", "extract-rpf-entry", str(archive), "common/data/test.ymap",
        "--gta-path", str(game), "-o", str(tmp_path / "test.ymap"),
    ])
    assert extracted.exit_code == 0, extracted.output
    assert (tmp_path / "test.ymap").read_bytes() == b"extracted"

    payload = tmp_path / "new.ymap"
    payload.write_bytes(b"new")
    planned = runner.invoke(main, [
        "sdk", "plan-rpf-replacement", str(archive), "common/data/test.ymap",
        str(payload), "--gta-path", str(game), "-o", str(tmp_path / "plan.json"),
    ])
    assert planned.exit_code == 0, planned.output
    assert json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))["status"] == "plan_only"


def test_new_rpf_cli_reports_missing_detection_and_unknown_entries(tmp_path, monkeypatch):
    from allin1_sdk import cli

    archive = tmp_path / "dlc.rpf"
    archive.write_bytes(b"RPF7")
    monkeypatch.setattr(cli, "detect_gta_path", lambda: None)
    runner = CliRunner()
    missing = runner.invoke(main, [
        "sdk", "index-rpf", str(archive), "-o", str(tmp_path / "index.json"),
    ])
    assert missing.exit_code != 0
    assert "GTA V was not detected" in missing.output

    game = tmp_path / "game"
    game.mkdir()
    index = RpfIndex.load(_write_index(tmp_path, _index_payload(archive)))

    class FakeService:
        def __init__(self, *_args):
            pass

        def index(self, _source):
            return index

    monkeypatch.setattr("allin1_sdk.cli.RpfExplorerService", FakeService)
    unknown = runner.invoke(main, [
        "sdk", "extract-rpf-entry", str(archive), "missing.bin",
        "--gta-path", str(game), "-o", str(tmp_path / "missing.bin"),
    ])
    assert unknown.exit_code != 0
    assert "not found uniquely" in unknown.output

    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"x")
    unknown_plan = runner.invoke(main, [
        "sdk", "plan-rpf-replacement", str(archive), "missing.bin", str(payload),
        "--gta-path", str(game), "-o", str(tmp_path / "plan.json"),
    ])
    assert unknown_plan.exit_code != 0
    assert "not found uniquely" in unknown_plan.output
