from __future__ import annotations

import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk import rpf_builder, rpf_tools
from allin1_sdk.cli import main
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.rpf_tools import _content_fingerprint


def _encode(entries: dict[str, bytes]) -> bytes:
    return b"RPF7" + json.dumps({
        name: base64.b64encode(data).decode("ascii")
        for name, data in entries.items()
    }, sort_keys=True).encode("utf-8")


def _decode_bytes(data: bytes) -> dict[str, bytes]:
    assert data.startswith(b"RPF7")
    return {
        name: base64.b64decode(value)
        for name, value in json.loads(data[4:].decode("utf-8")).items()
    }


def _decode(path: Path) -> dict[str, bytes]:
    return _decode_bytes(path.read_bytes())


def _pack_folder(folder: Path) -> bytes:
    entries: dict[str, bytes] = {}
    for item in sorted(folder.rglob("*"), key=lambda child: child.as_posix().casefold()):
        relative = item.relative_to(folder).as_posix()
        if item.is_dir():
            entries[f"{relative}/"] = b""
        else:
            entries[relative] = item.read_bytes()
    return _encode(entries)


def _index(source: Path) -> dict:
    archives: list[dict] = []
    entries: list[dict] = []

    def walk(data: bytes, archive_path: str, name: str) -> None:
        state = _decode_bytes(data)
        archives.append({
            "path": archive_path, "name": name, "version": 7,
            "encryption": "OPEN", "size": len(data), "entry_count": len(state),
        })
        for path, payload in state.items():
            clean = path.rstrip("/")
            if path.endswith("/"):
                entries.append({
                    "id": f"{archive_path}::{clean}", "archive_path": archive_path,
                    "path": clean, "name": Path(clean).name, "kind": "directory",
                    "size": 0, "stored_size": 0, "child_count": 0,
                })
                continue
            kind = "archive" if path.casefold().endswith(".rpf") else "binary"
            entries.append({
                "id": f"{archive_path}::{path}", "archive_path": archive_path,
                "path": path, "name": Path(path).name, "kind": kind,
                "size": len(payload), "stored_size": len(payload),
            })
            if kind == "archive":
                nested_path = path if not archive_path else f"{archive_path}!{path}"
                walk(payload, nested_path, Path(path).name)

    walk(source.read_bytes(), "", source.name)
    return {
        "schema_version": 1, "source": str(source.resolve()),
        "edition": "Enhanced", "archive_size": source.stat().st_size,
        "archives": archives, "entries": entries, "warnings": [],
    }


def _payload(source: Path, archive_path: str, entry_path: str) -> bytes:
    state = _decode(source)
    if archive_path:
        for nested in archive_path.split("!"):
            state = _decode_bytes(state[nested])
    return state[entry_path]


def _builder_runner(args, **_kwargs):
    assert args[1] == "build-dlc"
    Path(args[3]).write_bytes(_pack_folder(Path(args[2])))
    return SimpleNamespace(returncode=0, stdout="built", stderr="")


def _rsc7(payload: bytes, level: int) -> bytes:
    compressor = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
    compressed = compressor.compress(payload) + compressor.flush()
    return b"RSC7" + struct.pack("<III", 162, 0xA0000541, 0x20000000) + compressed


def _reader_runner(args, **_kwargs):
    command = args[1]
    source = Path(args[3])
    if command == "index-json":
        Path(args[4]).write_text(json.dumps(_index(source)), encoding="utf-8")
    elif command == "extract-virtual-entries":
        output = Path(args[5])
        for line in Path(args[4]).read_text(encoding="utf-8").splitlines():
            archive_path, entry_path, relative = line.split("\t", 2)
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_payload(source, archive_path, entry_path))
    else:
        raise AssertionError(command)
    return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _service(tmp_path: Path, monkeypatch) -> RpfArchiveBuilder:
    project = tmp_path / "project"
    patcher = project / "tools" / "RpfPatcher" / "RpfPatcher.exe"
    patcher.parent.mkdir(parents=True)
    patcher.write_bytes(b"helper")
    game = tmp_path / "game"
    game.mkdir()
    monkeypatch.setattr(rpf_builder, "run_hidden", _builder_runner)
    monkeypatch.setattr(rpf_tools, "run_hidden", _reader_runner)
    return RpfArchiveBuilder(project, game)


def test_builds_and_exactly_verifies_recursive_rpf_tree(tmp_path, monkeypatch):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    (source / "common" / "data").mkdir(parents=True)
    (source / "common" / "empty").mkdir()
    (source / "common" / "data" / "setup.xml").write_text("setup", encoding="utf-8")
    nested = source / "x64" / "vehicles.rpf.source"
    (nested / "models" / "deep.rpf.source").mkdir(parents=True)
    (nested / "vehicle.ytd").write_bytes(b"texture")
    (nested / "models" / "deep.rpf.source" / "vehicle.ydr").write_bytes(b"model")

    archive, report_path = builder.build(source, tmp_path / "release" / "dlc.rpf")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert report["summary"]["archives"] == 3
    assert report["summary"]["payloads_exactly_verified"] == 5
    assert report["safety"]["stock_game_files_modified"] is False
    assert report["archive"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    outer = _decode(archive)
    assert "x64/vehicles.rpf" in outer
    vehicles = _decode_bytes(outer["x64/vehicles.rpf"])
    assert vehicles["vehicle.ytd"] == b"texture"
    assert _decode_bytes(vehicles["models/deep.rpf"])["vehicle.ydr"] == b"model"


def test_refuses_existing_outputs_prebuilt_archives_and_authored_collisions(
    tmp_path, monkeypatch,
):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested.rpf").write_bytes(b"untrusted")
    with pytest.raises(ValueError, match="Prebuilt nested RPF"):
        builder.build(source, tmp_path / "out.rpf")
    (source / "nested.rpf").unlink()
    (source / "nested.rpf.source").mkdir()
    (source / "NESTED.RPF").mkdir()
    with pytest.raises(ValueError, match="collision"):
        builder.build(source, tmp_path / "out.rpf")
    (source / "NESTED.RPF").rmdir()
    output = tmp_path / "out.rpf"
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError, match="already exists"):
        builder.build(source, output)
    assert output.read_bytes() == b"keep"


def test_refuses_new_archive_output_inside_game_installation(tmp_path, monkeypatch):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "content.bin").write_bytes(b"content")
    output = builder.service.gta_path / "mods" / "update" / "new.rpf"
    with pytest.raises(ValueError, match="outside the GTA V installation"):
        builder.build(source, output)
    assert not output.exists()


def test_discards_build_when_exact_readback_is_wrong(tmp_path, monkeypatch):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    (source / "content.bin").write_bytes(b"expected")

    def corrupt_reader(args, **kwargs):
        result = _reader_runner(args, **kwargs)
        if args[1] == "extract-virtual-entries":
            first = next(path for path in Path(args[5]).iterdir() if path.is_file())
            first.write_bytes(b"corrupt")
        return result

    monkeypatch.setattr(rpf_tools, "run_hidden", corrupt_reader)
    output = tmp_path / "out.rpf"
    with pytest.raises(ValueError, match="payload hashes"):
        builder.build(source, output)
    assert not output.exists()
    assert not builder.validation_path(output).exists()


def test_accepts_recompressed_resource_only_after_canonical_payload_match(
    tmp_path, monkeypatch,
):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    logical = (b"resource-payload-" * 100_000) + bytes(range(256))
    authored = _rsc7(logical, 1)
    recompressed = _rsc7(logical, 9)
    assert authored != recompressed
    (source / "model.yft").write_bytes(authored)

    def recompressing_builder(args, **_kwargs):
        assert args[1] == "build-dlc"
        Path(args[3]).write_bytes(_encode({"model.yft": recompressed}))
        return SimpleNamespace(returncode=0, stdout="built", stderr="")

    monkeypatch.setattr(rpf_builder, "run_hidden", recompressing_builder)
    archive, report_path = builder.build(source, tmp_path / "resource.rpf")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert _decode(archive)["model.yft"] == recompressed
    recompressed_file = tmp_path / "recompressed.yft"
    recompressed_file.write_bytes(recompressed)
    assert _content_fingerprint(source / "model.yft")["canonical_sha256"] == (
        _content_fingerprint(recompressed_file)["canonical_sha256"]
    )
    assert _content_fingerprint(source / "model.yft")["raw_sha256"] != (
        _content_fingerprint(recompressed_file)["raw_sha256"]
    )
    assert report["summary"]["canonical_resource_payloads"] == 1
    assert report["summary"]["byte_exact_payloads"] == 0


def test_resource_fingerprint_accepts_only_a_valid_adler32_trailer(tmp_path):
    logical = b"real-resource-content" * 10_000
    authored = _rsc7(logical, 6)
    trailer = struct.pack(">I", zlib.adler32(logical) & 0xFFFFFFFF)
    with_trailer = tmp_path / "valid.yft"
    with_trailer.write_bytes(authored + trailer)
    fingerprint = _content_fingerprint(with_trailer)
    assert fingerprint["mode"] == "rsc7_canonical"
    assert fingerprint["resource_adler32_trailer"] is True

    invalid = tmp_path / "invalid.yft"
    invalid.write_bytes(authored + b"nope")
    with pytest.raises(ValueError, match="Invalid or trailing RSC7"):
        _content_fingerprint(invalid)


def test_build_rpf_tree_cli_and_compatibility_alias(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    game = tmp_path / "game"
    game.mkdir()
    output = tmp_path / "new.rpf"

    class FakeBuilder:
        def __init__(self, _project, selected_game):
            assert Path(selected_game) == game

        def build(self, selected_source, selected_output):
            assert Path(selected_source) == source
            archive = Path(selected_output)
            archive.write_bytes(b"RPF7")
            report = archive.with_name(f"{archive.name}.validation.json")
            report.write_text("{}", encoding="utf-8")
            return archive, report

    monkeypatch.setattr("allin1_sdk.cli.RpfArchiveBuilder", FakeBuilder)
    runner = CliRunner()
    result = runner.invoke(main, [
        "sdk", "build-rpf-tree", str(source), "--gta-path", str(game),
        "-o", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert "exactly verified" in result.output
    assert output.is_file()
