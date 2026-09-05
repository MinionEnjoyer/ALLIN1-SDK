"""Standalone resources and assistant setup must not require Launcher state."""

import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk.assistant_client import default_assistant_root, load_assistant_settings
from allin1_sdk.assistant_settings import save_standalone_assistant_settings
from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope
from scripts.package_release import _REQUIRED_AUTHORING_RESOURCES
from scripts.stage_desktop_resources import stage_resources


@pytest.fixture
def clean_user(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    return tmp_path / "user"


def test_sdk_settings_work_without_launcher_and_take_precedence(clean_user):
    legacy = clean_user / "ALLIN1" / "Assistant" / "config.json"
    sdk = clean_user / "ALLIN1-SDK" / "Assistant" / "config.json"
    assert default_assistant_root() == sdk.parent
    with pytest.raises(ValueError, match="Standalone setup"):
        load_assistant_settings()
    legacy.parent.mkdir(parents=True)
    original = '{"schema":1,"mode":"disabled"}'
    legacy.write_text(original)
    assert default_assistant_root() == legacy.parent
    assert save_standalone_assistant_settings({
        "mode": "compatible_api", "endpoint": "http://127.0.0.1:8080/v1",
        "model_name": "qwen-test", "structured_output": True,
    }) == sdk
    settings = load_assistant_settings()
    assert settings.root == sdk.parent
    assert settings.enabled and settings.model_name == "qwen-test"
    assert legacy.read_text() == original
    save_standalone_assistant_settings({"mode": "disabled"})
    assert not load_assistant_settings().enabled
    sdk.write_text("invalid")
    with pytest.raises(ValueError, match="invalid"):
        load_assistant_settings()  # corrupt SDK settings never silently enable legacy config
    assert load_assistant_settings(legacy.parent).mode == "disabled"


@pytest.mark.parametrize("extra", [
    {"endpoint": "http://remote.example/v1"},
    {"endpoint": "https://user:password@example.com"},
    {"endpoint": "https://example.com/v1?api_key=secret"},
    {"endpoint": "file:///tmp/provider"},
    {"api_key_env": "Bearer secret"}, {"api_key": "raw-secret"},
    {"structured_output": "yes"}, {"mode": "managed_local"},
    {"model_name": ""}, {"model_name": 123},
])
def test_invalid_settings_do_not_replace_existing_configuration(clean_user, extra):
    saved = save_standalone_assistant_settings({"mode": "disabled"})
    original = saved.read_bytes()
    payload = {"mode": "compatible_api", "endpoint": "https://example.com/v1", "model_name": "qwen"}
    with pytest.raises(ValueError):
        save_standalone_assistant_settings(payload | extra)
    assert saved.read_bytes() == original
    assert not (clean_user / "ALLIN1").exists()


def test_local_setup_validates_files_without_launching_anything(clean_user, tmp_path, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("Saving settings must not execute a runtime")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    runtime = tmp_path / "llama-server.exe"
    model = tmp_path / "qwen.gguf"
    runtime.write_bytes(b"MZfixture")
    model.write_bytes(b"GGUFfixture")
    payload = {"mode": "custom_local", "runtime_path": str(runtime), "model_path": str(model)}
    save_standalone_assistant_settings(payload)
    assert load_assistant_settings().model_path == str(model.resolve())
    model.write_bytes(b"not GGUF")
    with pytest.raises(ValueError, match="GGUF"):
        save_standalone_assistant_settings(payload)


def test_protocol_requires_confirmation_and_disallows_settings_jobs(clean_user):
    service = DesktopProtocolService()
    service.handle(envelope("handshake", {
        "client": {"name": "test", "version": "1"}, "supported_versions": ["1.0.0"],
    }, request_id="hello", terminal=False))
    status = service.handle(envelope("assistant_status", {}, request_id="status", terminal=False))[0]
    assert status["operation"] == "result"
    assert not status["payload"]["result"]["configured"]
    for confirmed in (False, "true", None):
        response = service.handle(envelope("configure_assistant", {
            "settings": {"mode": "disabled"}, "authoring_confirmed": confirmed,
        }, request_id="denied", terminal=False))[0]
        assert response["operation"] == "error"
        assert response["risk"] == "authoring_write"
    assert not clean_user.exists()
    saved = service.handle(envelope("configure_assistant", {
        "settings": {"mode": "disabled"}, "authoring_confirmed": True,
    }, request_id="saved", terminal=False))[0]
    assert saved["operation"] == "result"
    assert saved["risk"] == "authoring_write"
    assert saved["payload"]["result"]["runtime_started"] is False
    job = service.handle(envelope("start_job", {
        "job_id": "settings-job", "operation": "configure_assistant", "payload": {},
    }, request_id="job", terminal=False))[0]
    assert job["operation"] == "error"
    assert not (clean_user / "ALLIN1").exists()


def resource_fixture(tmp_path):
    root = tmp_path / "source"
    for relative in (*_REQUIRED_AUTHORING_RESOURCES, Path("sdk/addon.schema.json"), Path("README.md"), Path("LICENSE"), Path("desktop/src-tauri/windows/TAURI-LICENSE-MIT")):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    generated = root / "runtime/VehicleWorkbenchAxles/out/leftover.asi"
    generated.parent.mkdir()
    generated.write_bytes(b"MZgenerated")
    rpf = tmp_path / "rpf"
    rpf.mkdir()
    for name in ("RpfPatcher.exe", "RpfPatcher.dll", "coreclr.dll", "hostfxr.dll"):
        (rpf / name).write_bytes(b"MZfixture")
    return root, rpf


def test_resource_home_contains_schemas_examples_sources_and_self_contained_helper(tmp_path):
    root, rpf = resource_fixture(tmp_path)
    staged = stage_resources(root, rpf)
    manifest = json.loads((staged / "resource-checksums.json").read_text())
    assert "sdk/addon.schema.json" in manifest
    assert "README.md" in manifest and "LICENSE" in manifest
    assert "tools/RpfPatcher/coreclr.dll" in manifest
    assert "licenses/tauri-installer-MIT.txt" in manifest
    assert "runtime/VehicleWorkbenchAxles/out/leftover.asi" not in manifest
    for relative, digest in manifest.items():
        assert hashlib.sha256((staged / relative).read_bytes()).hexdigest() == digest
    (staged / "stale.txt").write_text("stale generated payload")
    stage_resources(root, rpf)
    assert not (staged / "stale.txt").exists()


def test_resource_staging_rejects_framework_dependent_helper(tmp_path):
    root, rpf = resource_fixture(tmp_path)
    (rpf / "coreclr.dll").unlink()
    with pytest.raises(ValueError, match="Self-contained"):
        stage_resources(root, rpf)


def test_diagnostic_staging_is_scoped_and_never_replaces_release_staging(tmp_path):
    root, rpf = resource_fixture(tmp_path)
    release = stage_resources(root, rpf)
    (release / "preserve-canary").write_bytes(b"existing candidate")
    destination = root / "build/diagnostic/resources"
    staged = stage_resources(root, rpf, destination=destination)
    assert staged == destination
    assert (release / "preserve-canary").read_bytes() == b"existing candidate"
    with pytest.raises(FileExistsError):
        stage_resources(root, rpf, destination=destination)
    for invalid in (root / "build", tmp_path / "outside", root / "src"):
        with pytest.raises(ValueError, match="inside this checkout"):
            stage_resources(root, rpf, destination=invalid)
    assert not (tmp_path / "outside").exists()
