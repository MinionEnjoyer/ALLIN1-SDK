"""Nested member identity and leaf-only restore in both package readers."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_rpf_member_contract import reader, manifest_data, write_manifest, sha


def nested_manifest(target="x64/american.rpf!text/global.gxt2"):
    value = manifest_data()
    value["schema_version"] = 4
    value["rpf_entries"][0]["entry"] = target
    return value


@pytest.mark.parametrize("target", ["x64/american.rpf!text/global.gxt2", "outer.rpf!inner.rpf!global.gxt2", "child.rpf!" * 8 + "global.gxt2"])
def test_explicit_layers(reader, target, tmp_path):
    mods, contract = reader
    assert contract.validate_mod_schema_envelope(nested_manifest(target)) == (4, None)
    write_manifest(tmp_path, nested_manifest(target))
    assert mods.ModManifest.load(tmp_path, validate_payload=False).rpf_entries[0].entry.as_posix() == target


@pytest.mark.parametrize("target", ["global.gxt2", "child.rpf/global.gxt2", "child.rpf!../global.gxt2",
    "child.rpf!!global.gxt2", "child.rpf!inner.rpf!global.gxt2!", "child.bin!global.gxt2",
    "child.rpf!inner.rpf", "child.rpf!inner.rpf/global.gxt2", "child.rpf!C:/global.gxt2",
    "child.rpf!/global.gxt2", "child.rpf!CON.gxt2", "child.rpf!global.gxt2.", "child.rpf!a//global.gxt2",
    "child.rpf!" * 9 + "global.gxt2", "a" * 2050, True, None])
def test_invalid_target(reader, target):
    with pytest.raises(ValueError): reader[1].validate_mod_schema_envelope(nested_manifest(target))


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_no_downgrade(reader, tmp_path, schema):
    data = nested_manifest(); data["schema_version"] = schema
    write_manifest(tmp_path, data)
    with pytest.raises(ValueError): reader[0].ModManifest.load(tmp_path, validate_payload=False)


@pytest.fixture(params=["x64/american.rpf!text/global.gxt2", "outer.rpf!inner.rpf!text/global.gxt2"])
def nested_install(reader, tmp_path, monkeypatch, request):
    mods, _ = reader
    target = request.param
    root = tmp_path / "package"; root.mkdir()
    (root / "payload.gxt2").write_bytes(b"new text")
    write_manifest(root, nested_manifest(target))
    game = tmp_path / "game"; game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"test marker")
    (game / "test.rpf").write_bytes(b"temporary archive")
    service = mods.ModIntegrationService(game)
    monkeypatch.setattr(service, "_check_dependencies", lambda manifest: None)
    neighbour = target.rsplit("!", 1)[0] + "!other.gxt2"
    entries = {target: b"original text", neighbour: b"keep", "text/global.gxt2": b"root decoy"}
    calls = []
    def native(command, archive, entry, *args):
        assert Path(archive).is_relative_to(game)
        entry = str(entry); calls.append((command, entry))
        if command == "extract-exact-nested-entry":
            if entry not in entries: return SimpleNamespace(returncode=5, stderr="Exact member not found", stdout="")
            Path(args[0]).write_bytes(entries[entry])
        elif command == "replace-exact-nested-entry":
            payload, expected, replacement = args
            assert sha(Path(payload).read_bytes()) == replacement
            if entry not in entries or sha(entries[entry]) not in (expected, replacement):
                return SimpleNamespace(returncode=99, stderr="Current nested member checksum mismatch", stdout="")
            entries[entry] = Path(payload).read_bytes()
        else: raise AssertionError("Nested operation used legacy command: " + command)
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(service, "_run_rpf_command", native)
    return mods, service, entries, calls, root, game, target, neighbour


def test_restore_preserves_later_unrelated_edits(nested_install):
    mods, service, entries, _, root, _, target, neighbour = nested_install
    manifest = mods.ModManifest.load(root)
    service.install(manifest)
    receipt = service._read_receipt(manifest.mod_id)
    assert receipt["schema_version"] == 4 and receipt["rpf_entries"][0]["entry"] == target
    assert entries[target] == b"new text"
    entries[neighbour] = b"another mod changed this"
    service.set_enabled(manifest.mod_id, False)
    assert entries[target] == b"original text"
    service.set_enabled(manifest.mod_id, True)
    assert entries[target] == b"new text"
    service.uninstall(manifest.mod_id)
    assert entries == {target: b"original text", neighbour: b"another mod changed this", "text/global.gxt2": b"root decoy"}


@pytest.mark.parametrize("failure", ["changed", "missing", "older_helper", "second_member"])
def test_preflight_never_prepares_game_writes_on_failure(nested_install, monkeypatch, failure):
    mods, service, entries, calls, root, game, target, _ = nested_install
    if failure == "changed": entries[target] = b"changed"
    elif failure == "missing": del entries[target]
    elif failure == "older_helper":
        monkeypatch.setattr(service, "_run_rpf_command", lambda *args: SimpleNamespace(returncode=1, stderr="Unknown command", stdout=""))
    else:
        data = nested_manifest(target)
        data["rpf_entries"].append({**data["rpf_entries"][0], "entry": "missing.rpf!global.gxt2"})
        write_manifest(root, data)
    before = {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()}
    with pytest.raises((RuntimeError, ValueError)): service.install(mods.ModManifest.load(root))
    assert {p.relative_to(game): p.read_bytes() for p in game.rglob("*") if p.is_file()} == before
    assert not (game / "mods").exists()
    assert not any(c.startswith("replace") for c, _ in calls)


@pytest.mark.parametrize("cache", ["backup", "applied"])
def test_corrupt_cache_cannot_be_used(nested_install, cache):
    mods, service, entries, _, root, game, _, _ = nested_install
    manifest = mods.ModManifest.load(root)
    service.install(manifest)
    if cache == "applied": service.set_enabled(manifest.mod_id, False)
    receipt = service._read_receipt(manifest.mod_id)
    (game / receipt["rpf_entries"][0][cache]).write_bytes(b"corrupt")
    before = dict(entries)
    with pytest.raises(ValueError, match="checksum mismatch"): service.set_enabled(manifest.mod_id, cache == "applied")
    assert entries == before


@pytest.mark.parametrize("parent", ["same", "parent", "outer"])
def test_parent_and_member_ownership_conflicts_both_directions(nested_install, parent):
    mods, service, _, _, root, _, target, _ = nested_install
    service.install(mods.ModManifest.load(root))
    data = nested_manifest(target); data["id"] = "test.conflicting"
    if parent == "parent":
        data["schema_version"] = 1
        data["rpf_entries"][0] = {k: v for k, v in data["rpf_entries"][0].items() if k != "original_sha256"}
        data["rpf_entries"][0]["entry"] = target.split("!")[0]
    elif parent == "outer":
        data = {k: v for k, v in data.items() if k != "rpf_entries"}
        data.update(schema_version=1, files=[{"source": "payload.gxt2", "destination": "mods/test.rpf"}])
    write_manifest(root, data)
    with pytest.raises(ValueError, match="owned by"): service._check_conflicts(mods.ModManifest.load(root))
    original = service._read_receipt("test.member"); original["id"] = "test.conflicting"
    original["rpf_entries"] = [] if parent == "outer" else [
        {"archive": "mods/test.rpf", "entry": target.split("!")[0] if parent == "parent" else target}]
    original["files"] = [{"destination": "mods/test.rpf"}] if parent == "outer" else []
    service._receipt_path("test.member").unlink()
    service._write_receipt(original)
    write_manifest(root, nested_manifest(target))
    with pytest.raises(ValueError, match="owned by"): service._check_conflicts(mods.ModManifest.load(root))


def test_native_source_is_shared():
    sdk = Path(__file__).resolve().parents[1]
    launcher = sdk.parent / "ALLIN1/tools/RpfPatcher/ExactNestedMember.cs"
    if not launcher.is_file(): pytest.skip("Sibling Launcher unavailable")
    assert (sdk / "tools/RpfPatcher/ExactNestedMember.cs").read_bytes() == launcher.read_bytes()
