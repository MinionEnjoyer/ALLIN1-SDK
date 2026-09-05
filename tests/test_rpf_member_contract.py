"""Schema-3 compatibility and install preconditions in both package readers."""
import hashlib
import importlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(params=["allin1_sdk", "allin1"])
def reader(request, monkeypatch):
    if request.param == "allin1":
        source = Path(__file__).resolve().parents[2] / "ALLIN1" / "src"
        if not source.is_dir(): pytest.skip("Sibling Launcher is unavailable")
        monkeypatch.syspath_prepend(str(source))
    return importlib.import_module(request.param + ".mods"), importlib.import_module(request.param + ".mod_package_contract")


def manifest_data():
    return {"schema_version": 3, "id": "test.member", "name": "Text patch", "version": "1.0.0", "type": "rpf",
            "editions": ["enhanced"], "dependencies": ["openrpf"], "rpf_entries": [
                {"source": "payload.gxt2", "archive": "mods/test.rpf", "entry": "text/global.gxt2",
                 "sha256": sha(b"new text"), "original_sha256": sha(b"original text")}]}


def write_manifest(root, data):
    lines = [f"{key} = {json.dumps(value)}" for key, value in data.items() if key not in ("rpf_entries", "files")]
    for table in ("files", "rpf_entries"):
        for entry in data.get(table, []):
            lines += [f"[[{table}]]", *[f"{key} = {json.dumps(value)}" for key, value in entry.items()]]
    (root / "mod.toml").write_text("\n".join(lines), encoding="utf-8")


def test_schema3_envelope_requires_new_reader(reader):
    mods, contract = reader
    assert contract.validate_mod_schema_envelope(manifest_data()) == (3, None)
    legacy = runpy.run_path(str(Path(__file__).parent / "contract_fixtures" / "legacy_schema_v1_v2.py"))["validate_mod_schema_envelope"]
    assert legacy({"schema_version": 1}) == (1, None)
    with pytest.raises(ValueError, match="schema_version must be 1 or 2"):
        legacy(manifest_data())
    assert mods.RpfEntryPatch.__dataclass_fields__["original_sha256"].default is None


@pytest.mark.parametrize("change", ["files", "allin1", "dlc", "edition", "dependency", "kind", "unknown", "missing_hash", "original_hash", "nested", "nested_marker", "unsafe", "empty", "too_many", "entry_field", "bool_schema"])
def test_schema3_is_a_strict_bounded_member_only_contract(reader, change):
    _, contract = reader
    data = manifest_data()
    if change == "files": data["files"] = [{"source": "x", "destination": "y"}]
    elif change == "allin1": data["allin1"] = {"api_version": 1}
    elif change == "dlc": data["dlc_packs"] = ["example"]
    elif change == "edition": data["editions"] = ["legacy", "enhanced"]
    elif change == "dependency": data["dependencies"] = []
    elif change == "kind": data["type"] = "mixed"
    elif change == "unknown": data["installer_fallback"] = True
    elif change == "missing_hash": del data["rpf_entries"][0]["sha256"]
    elif change == "original_hash": data["rpf_entries"][0]["original_sha256"] = "x" * 64
    elif change == "nested": data["rpf_entries"][0]["entry"] = "american.rpf/global.gxt2"
    elif change == "nested_marker": data["rpf_entries"][0]["entry"] = "american.rpf!global.gxt2"
    elif change == "unsafe": data["rpf_entries"][0]["archive"] = "mods/../test.rpf"
    elif change == "empty": data["rpf_entries"] = []
    elif change == "too_many": data["rpf_entries"] *= 129
    elif change == "entry_field": data["rpf_entries"][0]["archive_path"] = "child.rpf"
    else: data["schema_version"] = True
    with pytest.raises(ValueError): contract.validate_mod_schema_envelope(data)


@pytest.fixture
def installation(reader, tmp_path, monkeypatch):
    mods, _ = reader
    root = tmp_path / "package"; root.mkdir()
    (root / "payload.gxt2").write_bytes(b"new text")
    write_manifest(root, manifest_data())
    game = tmp_path / "game"; game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"test marker")
    (game / "test.rpf").write_bytes(b"temporary archive")
    service = mods.ModIntegrationService(game)
    monkeypatch.setattr(service, "_check_dependencies", lambda manifest: None)
    entries = {"text/global.gxt2": b"original text", "shadow/text/global.gxt2": b"keep decoy"}
    calls = []

    def native(command, archive, entry, *args):
        assert Path(archive).is_relative_to(game)
        calls.append((command, str(entry)))
        if command == "extract-exact-entry":
            if str(entry) not in entries: return SimpleNamespace(returncode=5, stderr="Entry not found", stdout="")
            Path(args[0]).write_bytes(entries[str(entry)])
        elif command == "replace-entry": entries[str(entry)] = Path(args[0]).read_bytes()
        elif command == "delete-entry": del entries[str(entry)]
        else: raise AssertionError(command)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(service, "_run_rpf_command", native)
    return mods, service, entries, calls, root, game


def test_exact_package_install_toggle_restore_and_receipt(installation):
    mods, service, entries, calls, root, _ = installation
    manifest = mods.ModManifest.load(root)
    service.install(manifest)
    assert entries["text/global.gxt2"] == b"new text"
    receipt = service._read_receipt(manifest.mod_id)
    assert receipt["schema_version"] == 3 and receipt["rpf_entries"][0]["original_sha256"] == sha(b"original text")
    service.set_enabled(manifest.mod_id, False)
    assert entries["text/global.gxt2"] == b"original text"
    service.set_enabled(manifest.mod_id, True)
    assert entries["text/global.gxt2"] == b"new text"
    service.uninstall(manifest.mod_id)
    assert entries == {"text/global.gxt2": b"original text", "shadow/text/global.gxt2": b"keep decoy"}
    assert all(command != "extract-entry" for command, _ in calls)


@pytest.mark.parametrize("failure", ["changed", "missing", "old_helper", "second_member"])
def test_preflight_failure_has_no_game_writes(installation, monkeypatch, failure):
    mods, service, entries, calls, root, game = installation
    if failure == "changed": entries["text/global.gxt2"] = b"externally edited"
    elif failure == "missing": del entries["text/global.gxt2"]
    elif failure == "old_helper": monkeypatch.setattr(service, "_run_rpf_command", lambda *args: SimpleNamespace(returncode=1, stderr="Unknown command", stdout=""))
    else:
        data = manifest_data()
        data["rpf_entries"].append({**data["rpf_entries"][0], "entry": "missing.gxt2"})
        write_manifest(root, data)
    before = {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    with pytest.raises((ValueError, RuntimeError)): service.install(mods.ModManifest.load(root))
    assert {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert not (game / "mods").exists() and not service.state_root.exists() and not service.backup_root.exists()
    assert not any(command == "replace-entry" for command, _ in calls)


def test_manifest_cannot_drop_preconditions_by_downgrading(installation):
    mods, _, _, _, root, _ = installation
    data = manifest_data(); data["schema_version"] = 1; write_manifest(root, data)
    with pytest.raises(ValueError, match="schema_version = 3"): mods.ModManifest.load(root)


def test_original_is_rechecked_after_preflight(installation, monkeypatch):
    mods, service, entries, calls, root, _ = installation
    original_extract = service._extract_rpf_entry
    count = 0
    def extract(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2: entries["text/global.gxt2"] = b"changed during install"
        return original_extract(*args, **kwargs)
    monkeypatch.setattr(service, "_extract_rpf_entry", extract)
    with pytest.raises(ValueError, match="changed before replacement"): service.install(mods.ModManifest.load(root))
    assert not any(command == "replace-entry" for command, _ in calls)


@pytest.mark.parametrize("cache", ["backup", "applied"])
def test_corrupt_cached_member_cannot_be_restored_or_reenabled(installation, cache):
    mods, service, entries, _, root, game = installation
    manifest = mods.ModManifest.load(root)
    service.install(manifest)
    if cache == "applied": service.set_enabled(manifest.mod_id, False)
    receipt = service._read_receipt(manifest.mod_id)
    (game / receipt["rpf_entries"][0][cache]).write_bytes(b"tampered cache")
    before = dict(entries)
    with pytest.raises(ValueError, match="checksum mismatch"):
        service.set_enabled(manifest.mod_id, cache == "applied")
    assert entries == before
