"""Regression coverage for the standalone SDK update boundary."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from allin1_sdk.self_update import (
    SDK_AGENT_EXECUTABLE,
    SDK_CHECKSUMS,
    SDK_CLI_EXECUTABLE,
    SDK_EXECUTABLE,
    SDK_RELEASE_METADATA,
    SDK_UPDATER_EXECUTABLE,
    SdkRelease,
    StagedUpdate,
    _filesystem_path,
    _extract_archive,
    discard_staged_update,
    inspect_release_archive,
    stage_release,
    update_available,
)
from allin1_sdk.updater_host import apply_staged_update
from allin1_sdk.release_paths import tree_files


def _stage_fixture(staged):
    archive = staged.parent / "fixture.zip"
    _release_archive(archive)
    _extract_archive(archive, staged)


def _refresh_fixture_manifest(staged):
    files = tree_files(staged)
    (staged / SDK_CHECKSUMS).write_text(json.dumps({
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in files.items() if name != SDK_CHECKSUMS
    }))


def _release_archive(path: Path, version: str = "0.6.0") -> bytes:
    payload = {
        SDK_EXECUTABLE: b"MZdesktop",
        SDK_CLI_EXECUTABLE: b"MZconsole",
        SDK_AGENT_EXECUTABLE: b"MZagent",
        SDK_UPDATER_EXECUTABLE: b"MZupdater",
        SDK_RELEASE_METADATA: json.dumps({
            "product": "ALLIN1-SDK",
            "version": version,
            "entrypoint": SDK_EXECUTABLE,
            "cli_entrypoint": SDK_CLI_EXECUTABLE,
            "agent_entrypoint": SDK_AGENT_EXECUTABLE,
            "updater_entrypoint": SDK_UPDATER_EXECUTABLE,
        }).encode(),
        "_internal/runtime.bin": b"runtime",
    }
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in payload.items()
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
        archive.writestr(SDK_CHECKSUMS, json.dumps(checksums))
    return path.read_bytes()


def _release(
    archive_bytes: bytes, archive_name: str = "ALLIN1-SDK-0.6.0-win-x64.zip",
) -> tuple[SdkRelease, bytes]:
    checksum = hashlib.sha256(archive_bytes).hexdigest().encode() + (
        b"  " + archive_name.encode() + b"\n"
    )
    return SdkRelease(
        "0.6.0", "ALLIN1 SDK 0.6.0", "https://example/release",
        "https://example/archive", archive_name, len(archive_bytes),
        "https://example/checksum",
    ), checksum


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_version_comparison_is_numeric_and_padding_safe():
    assert update_available("0.5.9", "0.5.10")
    assert update_available("0.5", "0.5.1")
    assert not update_available("0.5.9", "0.5.9")
    assert not update_available("0.6.0", "0.5.10")


def test_release_archive_requires_updater_and_verifies_every_payload(tmp_path):
    archive = tmp_path / "sdk.zip"
    _release_archive(archive)
    assert inspect_release_archive(archive, "0.6.0") == "0.6.0"

    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, "w") as target:
        for item in source.infolist():
            if item.filename != SDK_UPDATER_EXECUTABLE:
                target.writestr(item, source.read(item))
    with pytest.raises(ValueError, match="ALLIN1-SDK-Updater.exe"):
        inspect_release_archive(broken, "0.6.0")


def test_release_archive_rejects_case_colliding_windows_paths(tmp_path):
    archive = tmp_path / "duplicate.zip"
    _release_archive(archive)
    with zipfile.ZipFile(archive, "a") as package:
        package.writestr("_INTERNAL/runtime.bin", b"collision")
    with pytest.raises(ValueError, match="duplicate path"):
        inspect_release_archive(archive, "0.6.0")


def test_stage_release_keeps_running_installation_untouched(tmp_path):
    install = tmp_path / "SDK"
    install.mkdir()
    (install / SDK_EXECUTABLE).write_bytes(b"MZold")
    archive_path = tmp_path / "release.zip"
    archive_bytes = _release_archive(archive_path)
    release, checksum = _release(archive_bytes)

    def opener(request, timeout=0):
        url = request.full_url
        return _Response(checksum if url.endswith("checksum") else archive_bytes)

    staged = stage_release(release, install, opener=opener)
    assert (install / SDK_EXECUTABLE).read_bytes() == b"MZold"
    assert staged.staged_root.parent == install.parent
    assert staged.staged_root.name.startswith("SDK.updating-")
    assert staged.helper_source.read_bytes() == b"MZupdater"
    assert (staged.staged_root / SDK_EXECUTABLE).read_bytes() == b"MZdesktop"


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_stage_release_extracts_deep_packaged_dependencies(tmp_path):
    # Keep the staging root itself below MAX_PATH while ensuring a legitimate
    # nested PyInstaller dependency crosses it, matching a deep user install.
    install = tmp_path / ("deep-install-" + "x" * 36) / "SDK"
    install.mkdir(parents=True)
    (install / SDK_EXECUTABLE).write_bytes(b"MZold")
    base_archive = tmp_path / "base-release.zip"
    _release_archive(base_archive)
    archive_path = tmp_path / "release.zip"
    deep_member = (
        "_internal/lxml/isoschematron/resources/xsl/iso-schematron-xslt1/"
        "iso_abstract_expand.xsl"
    )
    with zipfile.ZipFile(base_archive) as source:
        payload = {
            item.filename: source.read(item)
            for item in source.infolist()
            if not item.is_dir() and item.filename != SDK_CHECKSUMS
        }
    payload[deep_member] = b"deep dependency"
    checksums = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in payload.items()
    }
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
        archive.writestr(SDK_CHECKSUMS, json.dumps(checksums))
    archive_bytes = archive_path.read_bytes()
    release, checksum = _release(archive_bytes)

    def opener(request, timeout=0):
        return _Response(
            checksum if request.full_url.endswith("checksum") else archive_bytes
        )

    staged = stage_release(release, install, opener=opener)
    assert _filesystem_path(
        staged.staged_root / deep_member
    ).read_bytes() == b"deep dependency"
    assert discard_staged_update(staged)
    assert not staged.staged_root.exists()


def test_external_helper_swaps_and_relaunches_with_rollback_boundary(tmp_path, monkeypatch):
    install = tmp_path / "SDK"
    staged = tmp_path / "SDK.updating-fixture"
    install.mkdir()
    staged.mkdir()
    (install / SDK_EXECUTABLE).write_bytes(b"MZold")
    _stage_fixture(staged)
    launched = []
    monkeypatch.setattr(
        "allin1_sdk.updater_host.subprocess.Popen",
        lambda command, **kwargs: launched.append((command, kwargs)),
    )

    result = apply_staged_update(install, staged, SDK_EXECUTABLE)

    assert result.read_bytes() == b"MZdesktop"
    assert not staged.exists()
    assert not (tmp_path / "SDK.previous").exists()
    backup = next(tmp_path.glob("SDK.previous-*"))
    assert (backup / SDK_EXECUTABLE).read_bytes() == b"MZold"
    assert launched[0][0] == [str(result)]


def test_external_helper_restores_previous_install_when_launch_fails(
    tmp_path, monkeypatch,
):
    install = tmp_path / "SDK"
    staged = tmp_path / "SDK.updating-fixture"
    install.mkdir()
    staged.mkdir()
    (install / SDK_EXECUTABLE).write_bytes(b"MZold")
    _stage_fixture(staged)

    def fail_launch(*_args, **_kwargs):
        raise OSError("launch refused")

    monkeypatch.setattr("allin1_sdk.updater_host.subprocess.Popen", fail_launch)
    with pytest.raises(OSError, match="launch refused"):
        apply_staged_update(install, staged, SDK_EXECUTABLE)

    assert (install / SDK_EXECUTABLE).read_bytes() == b"MZold"
    assert staged.exists()  # failed candidate retained, not destructively removed
    assert not (tmp_path / "SDK.previous").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path regression")
def test_external_helper_rolls_back_deep_packaged_tree(tmp_path, monkeypatch):
    install = tmp_path / ("deep-helper-" + "x" * 36) / "SDK"
    staged = install.with_name("SDK.updating-fixture")
    install.mkdir(parents=True)
    staged.mkdir()
    (install / SDK_EXECUTABLE).write_bytes(b"MZold")
    _stage_fixture(staged)
    deep_member = (
        "_internal/lxml/isoschematron/resources/xsl/iso-schematron-xslt1/"
        "iso_abstract_expand.xsl"
    )
    deep_file = _filesystem_path(staged / deep_member)
    deep_file.parent.mkdir(parents=True)
    deep_file.write_bytes(b"deep dependency")
    _refresh_fixture_manifest(staged)

    def fail_launch(*_args, **_kwargs):
        raise OSError("launch refused")

    monkeypatch.setattr("allin1_sdk.updater_host.subprocess.Popen", fail_launch)
    with pytest.raises(OSError, match="launch refused"):
        apply_staged_update(install, staged, SDK_EXECUTABLE)

    assert (install / SDK_EXECUTABLE).read_bytes() == b"MZold"
    assert staged.exists()
    assert not (install.parent / "SDK.previous").exists()


def test_discard_removes_only_valid_sibling_stage(tmp_path):
    install = tmp_path / "SDK"
    staged = tmp_path / "SDK.updating-fixture"
    install.mkdir()
    staged.mkdir()
    helper = staged / SDK_UPDATER_EXECUTABLE
    helper.write_bytes(b"MZupdater")
    update = StagedUpdate("0.6.0", install, staged, helper)
    assert discard_staged_update(update)
    assert not staged.exists()

    outside = tmp_path / "other"
    outside.mkdir()
    invalid = StagedUpdate("0.6.0", install, outside, outside / SDK_UPDATER_EXECUTABLE)
    with pytest.raises(ValueError, match="outside"):
        discard_staged_update(invalid)
    assert outside.exists()
