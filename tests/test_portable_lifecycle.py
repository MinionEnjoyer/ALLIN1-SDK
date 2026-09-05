"""Real ZIP/filesystem checks; synthetic executables are never launched."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from allin1_sdk.release_identity import sha256
from scripts import portable_lifecycle as lifecycle


def make_package(path, *, change=None, extra=None):
    identity = {"kind": "sdk_build_identity", "schema_version": 1, "build_id": "a" * 32,
                "sdk_version": "0.6.4", "release_qualified": False}
    files = {lifecycle.SHELL: b"MZ fake shell, not executable",
             lifecycle.SIDECAR: b"MZ fake sidecar, not executable",
             "tools/RpfPatcher/RpfPatcher.exe": b"MZ fake helper, not executable",
             "tools/RpfPatcher/RpfPatcher.dll": b"fake managed helper",
             "build-identity.json": json.dumps(identity).encode(),
             "LICENSE": b"owned fixture"}
    encode = lambda value: json.dumps(value).encode()
    hash_bytes = lambda value: hashlib.sha256(value).hexdigest()
    files["resource-checksums.json"] = encode({name: hash_bytes(value) for name, value in files.items()
                                              if name not in {lifecycle.SHELL, lifecycle.SIDECAR}})
    metadata = {"schema_version": 1, "product": "ALLIN1-SDK", "format": "tauri-v2", "version": "0.6.4",
                "build_id": "a" * 32, "entrypoint": lifecycle.SHELL, "sidecar_entrypoint": lifecycle.SIDECAR,
                "build_identity_sha256": hash_bytes(files["build-identity.json"])}
    files["release.json"] = encode(metadata)
    if change:
        change(files)
    files["checksums.json"] = encode({name: hash_bytes(value) for name, value in files.items()})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        if extra:
            archive.writestr(*extra)
    return sha256(path)


def change_json(files, name, **fields):
    doc = json.loads(files[name]); doc.update(fields)
    files[name] = json.dumps(doc).encode()


def test_portable_directory_lifecycle_preserves_original_and_user_data(tmp_path, monkeypatch):
    archive = tmp_path / "SDK.zip"
    digest = make_package(archive)
    monkeypatch.setattr(lifecycle, "probe", lambda *_: pytest.fail("Verification-only must not execute binaries"))
    output = tmp_path / "new isolated rehearsal"
    report = lifecycle.rehearse(archive, digest, output)
    assert report["status"] == "PASS" and len(report["cases"]) == 6
    assert report["release_qualified"] is False
    assert report["nsis_install_upgrade_uninstall"] == report["automatic_updater"] == "NOT TESTED"
    assert all(case.get("process_probes") == "NOT TESTED" for case in report["cases"][:-1])
    assert report["cases"][-2]["path_length"] >= 275
    assert sha256(archive) == digest
    assert (output / "isolated user state/retained-settings.json").read_text() == '{"keep":true}'
    assert (output / "outside-install-canary.bin").is_file()
    assert not list(output.rglob("*.exe"))


def test_process_probes_are_opt_in_and_a_failed_run_keeps_evidence_and_payload(tmp_path, monkeypatch):
    archive = tmp_path / "SDK.zip"; digest = make_package(archive)
    def fail(*_, **__):
        raise RuntimeError("injected packaged startup failure")
    monkeypatch.setattr(lifecycle, "probe", fail)
    output = tmp_path / "failed rehearsal"
    with pytest.raises(RuntimeError, match="startup failure"):
        lifecycle.rehearse(archive, digest, output, execute_probes=True)
    report = json.loads((output / "portable-lifecycle.json").read_text())
    assert report["status"] == "FAIL" and report["release_qualified"] is False
    assert report["cases"] == [] and "startup failure" in report["error"]
    assert (output / "Portable SDK with spaces" / lifecycle.SHELL).is_file()
    assert (output / "outside-install-canary.bin").is_file()


@pytest.mark.parametrize("field,value", [
    ("version", "0.6.3"), ("build_id", "b" * 32), ("product", "other"), ("format", "legacy"),
    ("entrypoint", "other.exe"), ("sidecar_entrypoint", "../outside.exe"),
    ("schema_version", True), ("build_identity_sha256", "0" * 64),
])
def test_wrong_distribution_identity_is_refused_before_output(tmp_path, field, value):
    archive = tmp_path / "wrong.zip"
    digest = make_package(archive, change=lambda files: change_json(files, "release.json", **{field: value}))
    output = tmp_path / "not-created"
    with pytest.raises(ValueError):
        lifecycle.rehearse(archive, digest, output)
    assert not output.exists()


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "a\\b", "a//b", "CON.txt", "a:stream", "release.json/child", "RELEASE.JSON"])
def test_hostile_archive_members_never_create_outputs(tmp_path, name):
    archive = tmp_path / "bad.zip"
    raw = zipfile.ZipInfo(name); raw.filename = raw.orig_filename = name
    digest = make_package(archive, extra=(raw, b"hostile"))
    with pytest.raises(ValueError):
        lifecycle.rehearse(archive, digest, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("case", ["missing-sidecar", "bad-resources", "bad-pe", "extra-file", "linked-member"])
def test_incomplete_or_unlisted_companions_are_refused(tmp_path, case):
    archive = tmp_path / "bad.zip"
    def change(files):
        if case == "missing-sidecar":
            del files[lifecycle.SIDECAR]
        elif case == "bad-resources":
            files["resource-checksums.json"] = b"{}"
        elif case == "bad-pe":
            files[lifecycle.SHELL] = b"not an executable"
    link = zipfile.ZipInfo("linked"); link.create_system = 3; link.external_attr = 0o120777 << 16
    extra = (link, b"outside") if case == "linked-member" else ("unlisted", b"extra") if case == "extra-file" else None
    digest = make_package(archive, change=change, extra=extra)
    with pytest.raises(ValueError):
        lifecycle.inspect_archive(archive, digest)


def test_stale_zip_digest_or_replaced_archive_is_refused_before_extraction(tmp_path):
    archive = tmp_path / "SDK.zip"; digest = make_package(archive)
    package = lifecycle.inspect_archive(archive, digest)
    archive.write_bytes(archive.read_bytes() + b"changed")
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="SHA-256"):
        lifecycle.rehearse(archive, digest, target)
    with pytest.raises(ValueError, match="changed"):
        lifecycle.extract_new(archive, target, package)
    assert not target.exists()


def test_existing_paths_are_preserved_and_tree_tampering_is_detected(tmp_path):
    archive = tmp_path / "SDK.zip"; digest = make_package(archive)
    package = lifecycle.inspect_archive(archive, digest)
    target = tmp_path / "existing"; target.mkdir()
    canary = target / "user.txt"; canary.write_text("keep")
    with pytest.raises(ValueError, match="new evidence"):
        lifecycle.rehearse(archive, digest, target)
    with pytest.raises(FileExistsError):
        lifecycle.extract_new(archive, target, package)
    assert canary.read_text() == "keep"
    fresh = tmp_path / "fresh"
    lifecycle.extract_new(archive, fresh, package)
    (fresh / "LICENSE").write_bytes(b"changed after extraction")
    with pytest.raises(ValueError, match="checksum"):
        lifecycle.verify_tree(fresh, package)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction test")
def test_junction_destination_is_refused_without_touching_canary(tmp_path):
    archive = tmp_path / "SDK.zip"; digest = make_package(archive)
    outside = tmp_path / "outside"; outside.mkdir()
    canary = outside / "canary"; canary.write_text("unchanged")
    link = tmp_path / "redirect"
    # Both exact paths are under this test's disposable root; no deletion follows
    # the junction. Pytest owns cleanup of this temporary fixture.
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "New-Item -ItemType Junction -Path $env:SDK_TEST_LINK -Target $env:SDK_TEST_TARGET -ErrorAction Stop | Out-Null"],
                   env={**os.environ, "SDK_TEST_LINK": str(link), "SDK_TEST_TARGET": str(outside)},
                   check=True, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    with pytest.raises(ValueError, match="reparse|junction"):
        lifecycle.rehearse(archive, digest, link / "new")
    assert canary.read_text() == "unchanged" and not (outside / "new").exists()


def probe_fixture(tmp_path, monkeypatch, *, fault=None, blocked=False):
    archive = tmp_path / "SDK.zip"; digest = make_package(archive)
    package = lifecycle.inspect_archive(archive, digest)
    root = tmp_path / "extracted"
    lifecycle.extract_new(archive, root, package)
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        if "--build-identity" in command:
            return json.dumps({} if fault == "shell-identity" else package["identity"])
        if "--verify-embedded-frontend" in command:
            return json.dumps({"status": "PASS", "build_id": package["build_id"],
                               "runtime_location_probe_version": None if fault == "old-shell" else 1})
        if "--check-runtime-location" in command:
            report = {"schema_version": 1, "kind": "sdk_runtime_location_probe", "status": "BLOCKED" if blocked else "READY",
                      "build_identity": package["identity"], "sidecar_process_started": False, "release_ready": False,
                      "long_path_runtime_supported": False,
                      "error": "SDK installation path is too long. Move the entire SDK folder to a shorter local path." if blocked else None}
            if fault == "location-identity": report["build_identity"] = {}
            if fault == "location-spawned": report["sidecar_process_started"] = True
            if fault == "location-blocked": report["status"] = "BLOCKED"
            if fault == "unhelpful-location": report["error"] = "Missing file"
            return json.dumps(report)
        assert command == [str(lifecycle.filesystem_path(root / lifecycle.SIDECAR))]
        env = kwargs["env"]
        assert not any(key.upper().startswith(("PYTHON", "ALLIN1_DESKTOP", "TAURI", "VITE_")) for key in env)
        assert env["APPDATA"] == env["TEMP"] == str(tmp_path / "state")
        assert "--allow-package-writes" not in command and "--allow-rpf-writes" not in command
        requests = [json.loads(line) for line in kwargs["input"].splitlines()]
        assert [r["operation"] for r in requests] == ["handshake", "shutdown"]
        payload = {"build_identity": package["identity"], "sdk_version": package["version"],
                   "game_writes_enabled": False, "package_writes_enabled": False, "rpf_writes_enabled": False}
        if fault == "sidecar-identity": payload["build_identity"] = {}
        if fault == "write-authority": payload["package_writes_enabled"] = True
        if fault == "missing-authority": del payload["rpf_writes_enabled"]
        responses = [{"protocol_version": "1.0.0", "request_id": request["operation"], "operation": "result", "terminal": True,
                      "payload": payload if index == 0 else {"state": "stopped"}} for index, request in enumerate(requests)]
        if fault == "extra-response": responses.append(responses[-1])
        if fault == "wrong-request": responses[0]["request_id"] = "unrelated"
        if fault == "not-stopped": responses[1]["payload"]["state"] = "running"
        return "\n".join(json.dumps(response) for response in responses)
    monkeypatch.setattr(lifecycle, "run_process", process)
    return root, package, calls


def test_read_only_packaged_protocol_preserves_isolation_and_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLIN1_DESKTOP_SIDECAR", "untrusted-development-override")
    monkeypatch.setenv("PYTHONPATH", "untrusted-development-path")
    root, package, calls = probe_fixture(tmp_path, monkeypatch)
    report = lifecycle.probe(root, package, tmp_path / "state")
    assert report["status"] == "PASS" and report["game_write_authority"] is False
    assert len(calls) == 4


@pytest.mark.parametrize("fault", ["shell-identity", "old-shell", "location-identity", "location-spawned", "location-blocked",
                                  "sidecar-identity", "write-authority", "missing-authority", "extra-response", "wrong-request", "not-stopped"])
def test_runtime_probe_rejects_invalid_evidence(tmp_path, monkeypatch, fault):
    root, package, calls = probe_fixture(tmp_path, monkeypatch, fault=fault)
    with pytest.raises(ValueError):
        lifecycle.probe(root, package, tmp_path / "state")
    if fault == "old-shell":
        assert len(calls) == 2  # Never pass an unknown switch to an older shell.


def test_long_path_policy_is_tested_without_executing_the_unsupported_sidecar(tmp_path, monkeypatch):
    root, package, calls = probe_fixture(tmp_path, monkeypatch, blocked=True)
    report = lifecycle.probe(root, package, tmp_path / "state", expected_location="BLOCKED")
    assert report["status"] == "PASS" and report["runtime_startup"].startswith("BLOCKED")
    assert report["sidecar_handshake_shutdown"] == "NOT TESTED"
    assert report["long_path_runtime_supported"] is False and report["sidecar_process_started"] is False
    assert len(calls) == 3


def test_long_path_refusal_requires_actionable_location_guidance(tmp_path, monkeypatch):
    root, package, _ = probe_fixture(tmp_path, monkeypatch, fault="unhelpful-location", blocked=True)
    with pytest.raises(ValueError, match="actionable"):
        lifecycle.probe(root, package, tmp_path / "state", expected_location="BLOCKED")


def test_process_launcher_pins_the_application_and_cleans_up_timeouts(tmp_path, monkeypatch):
    actual_popen = subprocess.Popen
    children = []
    def capture(command, **kwargs):
        if command[0] == sys.executable:
            assert kwargs["executable"] == command[0]
            assert kwargs.get("shell", False) is False
        child = actual_popen(command, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, "Popen", capture)
    assert lifecycle.run_process([sys.executable, "-c", "print('owned probe')"], cwd=tmp_path, env=dict(os.environ)).strip() == "owned probe"
    with pytest.raises(RuntimeError, match="exited 2"):
        lifecycle.run_process([sys.executable, "-c", "raise SystemExit(2)"], cwd=tmp_path, env=dict(os.environ))
    with pytest.raises(subprocess.TimeoutExpired):
        lifecycle.run_process([sys.executable, "-c", "import time; time.sleep(10)"], cwd=tmp_path, env=dict(os.environ), timeout=0.1)
    assert all(child.poll() is not None for child in children)
