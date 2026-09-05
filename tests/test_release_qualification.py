"""Synthetic contract tests are not live acceptance evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk.release_identity import verify_inventory, require_reviewed_source
from allin1_sdk.release_qualification import CHECKS, validate_live_acceptance
from scripts.qualify_release import pinned_json


def fixture(tmp_path, suite="sdk-desktop"):
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=2)).isoformat()
    end = (now - timedelta(minutes=1)).isoformat()
    (tmp_path / "sdk.exe").write_bytes(b"fixture SDK, not executable")
    (tmp_path / "renderer.dll").write_bytes(b"fixture renderer, not executable")
    (tmp_path / "proof.json").write_text('{"synthetic_test_only":true}')
    digest = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    identity = {"sdk_commit": "a" * 40, "build_id": "reviewed-build-fixture", "sdk_version": "1.2.3",
        "source_tree_sha256": "b" * 64, "artifacts": {"sdk.exe": digest("sdk.exe")},
        "dependencies": {"renderer.dll": digest("renderer.dll")}, "schema_versions": {"desktop": "1.0.0", "acceptance": "1"}}
    # Deliberately model a trusted authority only inside this unit fixture.
    report = {"schema_version": 1, "kind": "live_acceptance", "synthetic": False, "suite": suite,
        "session_id": "unit-fixture-session-1234", "target_edition": "Enhanced", "identity": identity,
        "started_at": start, "ended_at": end, "checks": {name: "PASS" for name in CHECKS[suite]},
        "events_path": "events.jsonl", "events_sha256": ""}
    events = []
    for sequence, check in enumerate([None, *sorted(CHECKS[suite]), None]):
        events.append({"schema_version": 1, "sequence": sequence, "session_id": report["session_id"],
            "timestamp": start if sequence == 0 else end, "type": "acceptance_check" if check else "session_start" if sequence == 0 else "session_end",
            "identity": identity, "target_edition": "Enhanced", "check": check, "status": "PASS",
            "evidence": {"proof.json": digest("proof.json")} if check else {}})
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events))
    report["events_sha256"] = digest("events.jsonl")
    anchor = {key: deepcopy(report[key]) for key in ("schema_version", "session_id", "suite", "target_edition", "identity", "started_at", "ended_at", "events_sha256")}
    anchor["authority"] = "unit-test authority only"
    kwargs = dict(expected_identity=deepcopy(identity), trusted_session=anchor, evidence_root=tmp_path,
        artifact_root=tmp_path, dependency_root=tmp_path, target_edition="Enhanced", suite=suite, now=now)
    return report, kwargs


@pytest.mark.parametrize("suite", CHECKS)
def test_complete_versioned_event_contract_keeps_results_separate(tmp_path, suite):
    report, kwargs = fixture(tmp_path, suite)
    result = validate_live_acceptance(report, **kwargs)
    assert result["live_acceptance"] == "PASS"
    assert result["automated_tests"] == result["package_integrity"] == "NOT TESTED"


@pytest.mark.parametrize("field", ["schema_version", "kind", "suite", "session_id", "target_edition", "identity",
    "started_at", "ended_at", "checks", "events_path", "events_sha256", "synthetic"])
def test_every_acceptance_field_required(tmp_path, field):
    report, kwargs = fixture(tmp_path); del report[field]
    with pytest.raises(ValueError):
        validate_live_acceptance(report, **kwargs)


@pytest.mark.parametrize("mutation", ["missing-check", "skip", "failed", "old", "future", "synthetic", "schema", "session", "edition", "commit", "build", "schema-identity", "binary", "dependency", "no-proof", "log-only"])
def test_untrusted_incomplete_stale_or_unrelated_claims_fail(tmp_path, mutation):
    report, kwargs = fixture(tmp_path)
    if mutation == "missing-check": report["checks"].pop("upgrade")
    elif mutation == "skip": report["checks"]["upgrade"] = "SKIP"
    elif mutation == "failed": report["checks"]["upgrade"] = "FAIL"
    elif mutation == "old": kwargs["now"] += timedelta(days=8)
    elif mutation == "future": kwargs["now"] -= timedelta(days=1)
    elif mutation == "synthetic": report["synthetic"] = True
    elif mutation == "schema": report["schema_version"] = True
    elif mutation == "session": report["session_id"] = "different-session-123456"
    elif mutation == "edition": report["target_edition"] = "Legacy"
    elif mutation == "commit": report["identity"]["sdk_commit"] = "c" * 40
    elif mutation == "build": report["identity"]["build_id"] = "another build"
    elif mutation == "schema-identity": report["identity"]["schema_versions"]["desktop"] = "2.0.0"
    elif mutation == "binary": (tmp_path / "sdk.exe").write_bytes(b"MZ unrelated")
    elif mutation == "dependency": (tmp_path / "renderer.dll").write_bytes(b"MZ unrelated")
    elif mutation == "no-proof": (tmp_path / "proof.json").unlink()
    else:
        report = {"schema_version": 1, "source_log_sha256": report["events_sha256"], "status": "PASS"}
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_live_acceptance(report, **kwargs)


@pytest.mark.parametrize("mutation", ["old-event-version", "missing-boundary", "duplicate-check", "wrong-session", "wrong-edition", "wrong-artifact", "reordered", "outside-proof"])
def test_semantic_events_checked_even_when_log_hash_matches(tmp_path, mutation):
    report, kwargs = fixture(tmp_path)
    path = tmp_path / "events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "old-event-version": events[1]["schema_version"] = 0
    elif mutation == "missing-boundary": events.pop()
    elif mutation == "duplicate-check": events[2]["check"] = events[1]["check"]
    elif mutation == "wrong-session": events[1]["session_id"] = "different-session-1234"
    elif mutation == "wrong-edition": events[1]["target_edition"] = "Legacy"
    elif mutation == "wrong-artifact": events[1]["identity"]["artifacts"]["sdk.exe"] = "0" * 64
    elif mutation == "reordered": events[1], events[2] = events[2], events[1]
    else: events[1]["evidence"] = {"../canary": "0" * 64}
    path.write_text("\n".join(json.dumps(event) for event in events))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report["events_sha256"] = kwargs["trusted_session"]["events_sha256"] = digest
    with pytest.raises(ValueError):
        validate_live_acceptance(report, **kwargs)


def test_resource_manifest_rejects_unlisted_stale_companion(tmp_path):
    (tmp_path / "expected.dll").write_bytes(b"expected")
    (tmp_path / "resource-checksums.json").write_text(json.dumps({"expected.dll": hashlib.sha256(b"expected").hexdigest()}))
    verify_inventory(tmp_path)
    (tmp_path / "stale.dll").write_bytes(b"stale")
    with pytest.raises(ValueError, match="exactly match"):
        verify_inventory(tmp_path)


def test_pins_are_not_taken_from_the_report(tmp_path):
    path = tmp_path / "identity.json"; path.write_text("{}")
    with pytest.raises(ValueError, match="trust pin"):
        pinned_json(path, "0" * 64)


@pytest.mark.parametrize("dirty,agree,commit", [(True, True, "a" * 40), (False, False, "a" * 40), (False, True, "b" * 40)])
def test_release_source_must_be_reviewed_clean_and_version_consistent(monkeypatch, tmp_path, dirty, agree, commit):
    monkeypatch.setattr("allin1_sdk.release_identity.source_identity", lambda root: {
        "sdk_commit": commit, "dirty": dirty, "versions_agree": agree, "versions": {"python": "1.2.3"}})
    with pytest.raises(ValueError):
        require_reviewed_source(tmp_path, "a" * 40, "1.2.3")


def test_source_versions_and_browser_preview_are_synchronized():
    from allin1_sdk import __version__
    import re
    root = Path(__file__).resolve().parents[1]
    for name in ("desktop/package.json", "desktop/src-tauri/tauri.conf.json"):
        assert json.loads((root / name).read_text())["version"] == __version__
    for name in ("pyproject.toml", "desktop/src-tauri/Cargo.toml"):
        assert re.search(r'^version\s*=\s*"([^"]+)"', (root / name).read_text(), re.M)[1] == __version__
    preview = (root / "desktop/src/previewClient.ts").read_text()
    assert 'sdk_version: packageInfo.version' in preview
    assert 'current_version: packageInfo.version' in preview
    resources = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())["bundle"]["resources"]
    assert "sidecar/*.exe" not in resources
    assert resources["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"] == "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"


def test_real_source_identity_rejects_dirty_and_wrong_version_in_disposable_git(tmp_path):
    import subprocess
    from allin1_sdk.release_identity import source_identity
    files = {"pyproject.toml": 'version = "1.2.3"', "desktop/src-tauri/Cargo.toml": 'version = "1.2.3"',
        "src/allin1_sdk/__init__.py": '__version__ = "1.2.3"',
        "desktop/package.json": '{"version":"1.2.3"}', "desktop/src-tauri/tauri.conf.json": '{"version":"1.2.3"}'}
    for name, value in files.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q", "-b", "codex/release-test")
    git("add", ".")
    git("-c", "user.name=Disposable Test", "-c", "user.email=test@example.invalid", "commit", "--no-gpg-sign", "-qm", "Synthetic fixture")
    head = git("rev-parse", "HEAD")
    clean = require_reviewed_source(tmp_path, head, "1.2.3")
    assert clean["versions_agree"] and not clean["dirty"] and clean["input_count"] == 5
    with pytest.raises(ValueError, match="versions disagree"):
        require_reviewed_source(tmp_path, head, "1.2.4")
    (tmp_path / "extra.txt").write_text("unreviewed")
    dirty = source_identity(tmp_path)
    assert dirty["dirty"] and clean["source_tree_sha256"] != dirty["source_tree_sha256"]
    with pytest.raises(ValueError, match="uncommitted"):
        require_reviewed_source(tmp_path, head, "1.2.3")


@pytest.mark.parametrize("value", [None, {}, {"sdk.exe": "not-a-hash"}, {"../sdk.exe": "a" * 64}])
def test_malformed_hash_manifests_fail(tmp_path, value):
    from allin1_sdk.release_qualification import validate_identity
    report, kwargs = fixture(tmp_path)
    report["identity"]["artifacts"] = value
    with pytest.raises(ValueError):
        validate_identity(report["identity"])


@pytest.mark.parametrize("value", ["not-a-date", "2026-09-04T00:00:00", None])
def test_malformed_session_timestamps_fail(tmp_path, value):
    report, kwargs = fixture(tmp_path)
    report["started_at"] = kwargs["trusted_session"]["started_at"] = value
    with pytest.raises(ValueError):
        validate_live_acceptance(report, **kwargs)
