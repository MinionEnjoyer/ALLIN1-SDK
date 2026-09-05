"""A diagnostic build must never produce release-qualified evidence."""
import json
from pathlib import Path

import pytest

from scripts import desktop_smoke_candidate as candidate


@pytest.mark.parametrize("failure", [None, "publish.log", "freeze.log", "smoke_desktop_sidecar.py.log"])
def test_diagnostic_reports_limits_and_keeps_failed_run_evidence(tmp_path, monkeypatch, failure):
    folder = tmp_path / "candidate"
    folder.mkdir()
    identity = {"build_id": "fixture", "release_qualified": False}
    monkeypatch.setattr(candidate, "prepare", lambda *_: folder / "identity.json")
    monkeypatch.setattr(candidate, "check_source", lambda *_: identity)
    def stage(*args, **kwargs):
        path = kwargs["destination"]
        path.mkdir()
        (path / "resource-checksums.json").write_text("{}")
        return path
    monkeypatch.setattr(candidate, "stage_resources", stage)
    commands = []
    def run(command, root, log):
        commands.append(command)
        log.write_text("diagnostic fixture")
        if log.name == failure:
            raise RuntimeError("injected failure")
        if log.name == "freeze.log":
            binary = folder / "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"
            binary.parent.mkdir()
            binary.write_bytes(b"fixture, not executed")
    monkeypatch.setattr(candidate, "run", run)
    monkeypatch.setattr(candidate, "inspect_frozen", lambda *_: {"status": "PASS"})
    monkeypatch.setattr(candidate, "verify_inventory", lambda *_: {})
    if failure:
        with pytest.raises(RuntimeError, match="injected failure"):
            candidate.build(tmp_path, "pnpm")
    else:
        assert candidate.build(tmp_path, "pnpm") == folder
    report = json.loads((folder / "diagnostic-validation.json").read_text())
    assert report["status"] == ("FAIL" if failure else "PASS")
    assert report["release_readiness"] == "FAIL"
    assert report["installer_lifecycle"] == report["full_test_qualification"] == "NOT TESTED"
    assert all("makensis" not in " ".join(command) for command in commands)


def test_optional_diagnostic_shell_does_not_bypass_qualification_or_touch_staging():
    source = Path(candidate.__file__).read_text(encoding="utf-8")
    root = Path(candidate.__file__).resolve().parents[1]
    guard = (root / "desktop/src-tauri/build.rs").read_text(encoding="utf-8")
    assert '"--features", "tauri/custom-protocol"' in source
    assert '"--verify-embedded-frontend"' in source
    assert '"bundle": {"active": False, "resources": []}' in source
    assert '!tauri_build::is_dev()' in guard
    assert '"ALLIN1_BUILD_IDENTITY_FILE"' in guard
