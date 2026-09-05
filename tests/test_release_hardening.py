"""Adversarial tests use disposable roots and outside-destination canaries only."""
import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from allin1_sdk import self_update as update
from allin1_sdk.mods import ModIntegrationService, _contained_path
from allin1_sdk.release_paths import contained, relative_path, strict_json, tree_files
from allin1_sdk.updater_host import apply_staged_update
from test_self_update import _release_archive, _stage_fixture
from test_self_update import _Response, _release


UNSAFE = ["../canary", "/canary", "C:/canary", "C:canary", "//host/share",
          "a\\..\\canary", "a\\b", "a//b", "a/./b", "a/../b", "a/CON.txt",
          "a/NUL", "a/com¹.txt", "a/b.", "a/b ", "a/ b", "a:x", "a\x00x", "a/*"]


@pytest.mark.parametrize("name", UNSAFE)
def test_manifest_paths_are_canonical_relative(name, tmp_path):
    canary = tmp_path / "canary"; canary.write_bytes(b"outside")
    with pytest.raises(ValueError):
        contained(tmp_path / "destination", name)
    assert canary.read_bytes() == b"outside"
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("name", [n for n in UNSAFE if "\x00" not in n])
def test_archive_preflight_refuses_before_first_write(name, tmp_path):
    archive = tmp_path / "bad.zip"
    target = tmp_path / "destination"
    canary = tmp_path / "canary"; canary.write_bytes(b"outside")
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("first-good.txt", b"must not be written")
        raw = zipfile.ZipInfo(name)
        raw.filename = raw.orig_filename = name  # retain hostile Windows spelling on disk
        package.writestr(raw, b"attack")
    with pytest.raises(ValueError):
        update._extract_archive(archive, target)
    assert not target.exists()
    assert canary.read_bytes() == b"outside"


def test_checksum_key_escape_is_already_rejected_by_exact_agreement(tmp_path):
    original = tmp_path / "original.zip"; _release_archive(original)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(bad, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == update.SDK_CHECKSUMS:
                values = json.loads(content)
                values["../canary"] = hashlib.sha256(b"attack").hexdigest()
                content = json.dumps(values).encode()
            target.writestr(item, content)
    canary = tmp_path / "canary"; canary.write_bytes(b"outside")
    with pytest.raises(ValueError, match="exactly match"):
        update.inspect_release_archive(bad, "0.6.0")
    assert canary.read_bytes() == b"outside"


@pytest.mark.parametrize("names", [("a", "a/b"), ("A", "a"), ("a/", "a")])
def test_archive_aliases_and_file_parent_collisions(names, tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for name in names:
            package.writestr(name, b"x")
    with pytest.raises(ValueError):
        update._extract_archive(archive, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_duplicate_json_keys_not_silently_overwritten():
    with pytest.raises(ValueError, match="Duplicate JSON"):
        strict_json('{"a":"first", "a":"second"}')


@pytest.mark.parametrize("tamper", ["bytes", "extra", "manifest"])
def test_swap_reverifies_candidate_before_renaming_install(tmp_path, monkeypatch, tamper):
    root = tmp_path / "SDK with spaces"; root.mkdir()
    (root / "user-data.txt").write_bytes(b"preserve")
    stage = root.with_name(root.name + ".updating-fixture"); stage.mkdir()
    _stage_fixture(stage)
    expected = hashlib.sha256((stage / update.SDK_CHECKSUMS).read_bytes()).hexdigest()
    if tamper == "bytes":
        (stage / update.SDK_EXECUTABLE).write_bytes(b"MZunrelated")
    elif tamper == "extra":
        (stage / "stale.dll").write_bytes(b"MZstale")
    else:
        (stage / update.SDK_CHECKSUMS).write_text('{"../canary":"' + '0' * 64 + '"}')
    monkeypatch.setattr("allin1_sdk.updater_host.subprocess.Popen", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError):
        apply_staged_update(root, stage, update.SDK_EXECUTABLE, expected_manifest_sha256=expected)
    assert (root / "user-data.txt").read_bytes() == b"preserve"
    assert not list(tmp_path.glob("*.previous-*"))


def test_swapped_manifest_even_with_consistent_rehashed_payload_is_rejected(tmp_path):
    root = tmp_path / "SDK"; root.mkdir()
    stage = tmp_path / "SDK.updating-fixture"; stage.mkdir(); _stage_fixture(stage)
    manifest = stage / update.SDK_CHECKSUMS
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(json.loads(manifest.read_bytes()), indent=4))
    with pytest.raises(ValueError, match="after scheduling"):
        apply_staged_update(root, stage, update.SDK_EXECUTABLE, expected_manifest_sha256=expected)
    assert root.is_dir() and stage.is_dir()


def junction(link, target):
    if os.name == "nt":
        quote = lambda p: "'" + str(p).replace("'", "''") + "'"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} | Out-Null"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def test_root_and_nested_junctions_rejected_with_canary(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir()
    canary = outside / "canary"; canary.write_bytes(b"outside")
    link = tmp_path / "linked"; junction(link, outside)
    with pytest.raises(ValueError, match="junction"):
        contained(link, "canary")
    with pytest.raises(ValueError, match="junction"):
        _contained_path(link, "canary")
    with pytest.raises(ValueError, match="junction"):
        tree_files(tmp_path)
    assert canary.read_bytes() == b"outside"


def test_hardlink_destination_refused(tmp_path):
    canary = tmp_path / "outside"; canary.write_bytes(b"outside")
    root = tmp_path / "target"; root.mkdir(); os.link(canary, root / "payload")
    with pytest.raises(ValueError, match="Hard-linked"):
        contained(root, "payload")
    assert canary.read_bytes() == b"outside"


@pytest.mark.parametrize("bad_record", [
    {"destination": "../canary"},
    {"destination": "scripts/good", "backup": "scripts/not-a-backup"},
    {"destination": "scripts/good", "backup": "ALLIN1_Backups/Mods/other/old"},
    {"destination": "scripts/GOOD"},
])
def test_rollback_receipt_preflight_preserves_first_file_and_canary(tmp_path, bad_record):
    game = tmp_path / "synthetic game"; game.mkdir()
    (game / "GTA5.exe").write_bytes(b"synthetic marker, never executed")
    service = ModIntegrationService(game)
    target = game / "scripts/good"; target.parent.mkdir(); target.write_bytes(b"owned")
    canary = tmp_path / "canary"; canary.write_bytes(b"outside")
    service.state_root.mkdir(parents=True)
    receipt = {"id": "fixture", "files": [{"destination": "scripts/good"}, bad_record]}
    path = service.state_root / "fixture.json"; path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        service.uninstall("fixture", check_dependents=False)
    assert target.read_bytes() == b"owned"
    assert canary.read_bytes() == b"outside"
    assert path.is_file()


def test_update_catalog_and_download_failure_paths_without_network(tmp_path, monkeypatch):
    archive = tmp_path / "fixture.zip"; content = _release_archive(archive)
    release, checksum = _release(content)
    catalog = {"tag_name": "v0.6.0", "html_url": "https://example.invalid/release", "assets": [
        {"name": release.archive_name, "size": len(content), "browser_download_url": release.archive_url},
        {"name": release.archive_name + ".sha256", "browser_download_url": release.checksum_url}]}
    def fetch(value):
        return update.fetch_latest_release(opener=lambda *a, **k: _Response(json.dumps(value).encode()))
    assert fetch(catalog).version == "0.6.0"
    for mutate, message in [(lambda c: c["assets"].pop(), "missing"),
        (lambda c: c["assets"][0].update(size=0), "allowed range"),
        (lambda c: c.update(tag_name="0.7.0"), "version does not match"),
        (lambda c: c["assets"].append(dict(c["assets"][0])), "duplicate identities"),
        (lambda c: c.update(assets=[]), "unambiguous")]:
        from copy import deepcopy
        bad = deepcopy(catalog); mutate(bad)
        with pytest.raises(ValueError, match=message): fetch(bad)
    install = tmp_path / "SDK"; install.mkdir(); (install / update.SDK_EXECUTABLE).write_bytes(b"MZold")
    def opener(*args, **kwargs):
        return _Response(checksum if args[0].full_url.endswith("checksum") else content[:-1])
    with pytest.raises(ValueError, match="size mismatch"):
        update.stage_release(release, install, opener=opener)
    assert (install / update.SDK_EXECUTABLE).read_bytes() == b"MZold"
    assert not list(tmp_path.glob("SDK.updating-*"))


@pytest.mark.parametrize("text", [b"", b"g" * 64, b"a" * 64 + b"  ../sdk.zip", b"a" * 64 + b"\n" + b"b" * 64])
def test_checksum_assets_are_unambiguous(text):
    with pytest.raises(ValueError): update._parse_checksum(text, "sdk.zip")


def test_limited_download_and_invalid_versions():
    assert update._read_limited_response(_Response(b"123"), 3) == b"123"
    with pytest.raises(ValueError, match="exceeds"):
        update._read_limited_response(_Response(b"1234"), 3)
    with pytest.raises(ValueError, match="invalid SDK version"):
        update.update_available("1.0", "../2")


@pytest.mark.skipif(os.name != "nt", reason="Windows detached helper scheduling")
def test_scheduler_binds_manifest_and_copy_without_launching(tmp_path, monkeypatch):
    root = tmp_path / "SDK"; root.mkdir()
    stage = tmp_path / "SDK.updating-fixture"; stage.mkdir(); _stage_fixture(stage)
    recorded = []
    monkeypatch.setattr(update.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: recorded.append((a, k)))
    staged = update.StagedUpdate("0.6.0", root, stage, stage / update.SDK_UPDATER_EXECUTABLE)
    helper = update.schedule_staged_update(staged, process_id=1234)
    assert helper.parent == tmp_path and helper.read_bytes() == b"MZupdater"
    args = recorded[0][0][0]
    assert args[args.index("--expected-manifest-sha256") + 1] == hashlib.sha256((stage / update.SDK_CHECKSUMS).read_bytes()).hexdigest()
    assert args[args.index("--wait-pid") + 1] == "1234"
    assert recorded[0][1]["creationflags"]


@pytest.mark.parametrize("mutation", ["symlink", "special", "reparse", "encrypted"])
def test_special_archive_members_fail(mutation):
    import stat
    info = zipfile.ZipInfo("test")
    if mutation == "symlink": info.external_attr = (stat.S_IFLNK | 0o777) << 16
    elif mutation == "special": info.external_attr = (stat.S_IFIFO | 0o600) << 16
    elif mutation == "reparse": info.external_attr = 0x400
    else: info.flag_bits = 1
    with pytest.raises(ValueError): update._safe_member(info)


@pytest.mark.parametrize("name", ["safe///", "COM¹/", "CONOUT$/", "a\\b/"])
def test_managed_archive_directory_names_are_not_silently_normalized(name):
    from allin1_sdk.mods import _archive_member_path
    info = zipfile.ZipInfo(name); info.filename = info.orig_filename = name
    with pytest.raises(ValueError): _archive_member_path(info)


def test_new_swap_preserves_outside_junction_canary_and_user_data(tmp_path, monkeypatch):
    outside = tmp_path / "outside"; outside.mkdir()
    canary = outside / "canary"; canary.write_bytes(b"outside")
    junction(tmp_path / "SDK.previous", outside)
    root = tmp_path / "SDK"; root.mkdir()
    (root / "user-data.txt").write_bytes(b"preserve")
    stage = tmp_path / "SDK.updating-fixture"; stage.mkdir(); _stage_fixture(stage)
    monkeypatch.setattr("allin1_sdk.updater_host.subprocess.Popen", lambda *a, **k: None)
    apply_staged_update(root, stage, update.SDK_EXECUTABLE)
    assert canary.read_bytes() == b"outside"
    backups = list(tmp_path.glob("SDK.previous-*"))
    assert len(backups) == 1 and (backups[0] / "user-data.txt").read_bytes() == b"preserve"


@pytest.mark.parametrize("offset", [-8, 1])
def test_runtime_acceptance_receipt_rejects_stale_or_future_time(tmp_path, offset):
    from datetime import datetime, timedelta, timezone
    from dataclasses import replace
    from test_axle_runtime_bundler import _story_profile
    from allin1_sdk.axle_runtime_bundler import TARGET_STORY_LEGACY
    profile = _story_profile(tmp_path, TARGET_STORY_LEGACY)
    path = profile.validation_receipt_path
    payload = json.loads(path.read_text())
    payload["accepted_at"] = (datetime.now(timezone.utc) + timedelta(days=offset)).isoformat()
    path.write_text(json.dumps(payload))
    profile = replace(profile, expected_receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="stale or future"):
        profile.runtime_dependency()
