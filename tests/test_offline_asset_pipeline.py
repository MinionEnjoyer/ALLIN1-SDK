"""Source workflow acceptance against a fixture-only Launcher installation.

No executable is launched, native helper built, retail asset read or real game
installation touched. The deterministic build fixture is not release provenance.
"""
import json
from pathlib import Path

import pytest

from allin1_sdk import optimization_package, workspace_desktop as desktop
from allin1_sdk.release_identity import sha256
from test_artifact_identity import build
from test_optimization_package import request


@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_offline_validate_optimize_install_diagnose_and_recover(tmp_path,monkeypatch,edition):
    launcher = Path(__file__).resolve().parents[2] / "ALLIN1/src"
    if not (launcher / "allin1/sdk_provenance.py").is_file():
        pytest.skip("Matching Launcher source required for fixture integration")
    monkeypatch.syspath_prepend(str(launcher))
    from allin1.mods import ModManifest, ModIntegrationService
    monkeypatch.setattr(optimization_package.artifact_identity,"current",build)
    # Bound any incidental per-user paths to this test's sandbox as well.
    monkeypatch.setenv("LOCALAPPDATA",str(tmp_path / "user"))
    monkeypatch.setenv("APPDATA",str(tmp_path / "user"))
    payload = {"module":"optimization",**request(tmp_path)}
    source = Path(payload["source"])
    (source / "clip.ydr.xml").write_bytes((source / "car.ydr.xml").read_bytes())
    (source / "clip.meta").write_text('<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>clip</modelName><txdName>paint</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>')
    assemblies = [{"parent":"package:car.ydr.xml","parent_drawable":0,"parent_sha256":sha256(source / "car.ydr.xml"),"parent_bone":"tip",
        "child":"package:clip.ydr.xml","child_drawable":0,"child_sha256":sha256(source / "clip.ydr.xml"),"child_bone":"","offset":[2,0,0],"rotation":[0,0,1,0]}]
    payload["settings"]["assembly_bindings"] = assemblies
    (source / "mod.toml").write_text('schema_version = 1\nid = "offline-pipeline"\nname = "Offline pipeline"\nversion = "1.0.0"\ntype = "config"\neditions = ["legacy", "enhanced"]\n[[files]]\nsource = "assets/diffuse.dds"\ndestination = "scripts/fixture.dds"\n')
    original = desktop._inventory(source)
    validation = desktop.inspect({"module":"data_tools","task":"asset_validation","source":str(source),"settings":{"assembly_bindings":assemblies}})
    assert validation["document"]["runtime_status"] == "not_tested"
    assert validation["document"]["assembly_evidence"][0]["status"] == "checked"
    session = desktop.inspect(payload)
    assert session["before_report"]["assembly_evidence"] == session["after_report"]["assembly_evidence"]
    assert session["storage_delta_bytes"] < 0

    def apply(fields):
        reviewed = desktop.review(fields)
        return desktop.apply({**fields,"review_sha256":reviewed["review_sha256"],"authoring_confirmed":True})

    output = tmp_path / "optimized"
    apply({**payload,"action":"export","destination":str(output),"expected_state_sha256":session["state_sha256"]})
    assert desktop._inventory(source) == original
    game = tmp_path / "mock-install"; game.mkdir()
    (game / ("GTA5.exe" if edition == "Legacy" else "GTA5_Enhanced.exe")).write_bytes(b"non-executable test marker")
    (game / "scripts").mkdir()
    installed = game / "scripts/fixture.dds"; installed.write_bytes(b"test-owned pre-install bytes")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(output / "package/mod.toml"))
    candidate_bytes = installed.read_bytes()
    assert candidate_bytes == (output / "package/assets/diffuse.dds").read_bytes()
    report_file = output / "optimization.json"
    log = tmp_path / "fixture.log"; log.write_text("version=1.4.0\npassword=never-share-this\n")
    diagnostic = {"module":"data_tools","task":"diagnostic_trail","source":str(output / "package/sdk-artifact.json"),
        "comparison":str(service._receipt_path("offline-pipeline")),"gta_path":str(game),
        "settings":{"asset_report":str(report_file),"logs":[{"source":str(log),"start_line":1,"line_count":2}],"redact_terms":[]}}
    matched = desktop.inspect(diagnostic)
    evidence = matched["document"]
    assert all(row["status"] == "match" for row in evidence["files"]) and evidence["files"]
    assert evidence["asset_validation"]["status"] == "recorded"
    assert evidence["asset_validation"]["source_relation"] == "outputs_inventory"
    assert evidence["crash_cause"] == "not_established"
    export_report = {**diagnostic,"action":"export","destination":str(tmp_path / "stale"),
        "expected_state_sha256":matched["state_sha256"],"privacy_review_sha256":evidence["log_bundle"]["preview_sha256"]}
    review = desktop.review(export_report)
    report_bytes = report_file.read_bytes(); report_file.write_bytes(report_bytes + b" ")
    with pytest.raises(ValueError,match="changed"):
        desktop.apply({**export_report,"review_sha256":review["review_sha256"],"authoring_confirmed":True})
    assert not (tmp_path / "stale").exists()
    report_file.write_bytes(report_bytes)
    broken = json.loads(report_bytes); broken["after_report"]["static_status"] = "invalid"
    report_file.write_text(json.dumps(broken))
    with pytest.raises(ValueError): desktop.inspect(diagnostic)
    report_file.write_bytes(report_bytes)
    installed.write_bytes(b"injected installed-file drift")
    drift = desktop.inspect(diagnostic)
    assert "installed_bytes_changed" in {f["code"] for f in drift["document"]["findings"]}
    assert drift["document"]["crash_cause"] == "not_established"
    apply({**diagnostic,"action":"export","destination":str(tmp_path / "diagnostics"),"expected_state_sha256":drift["state_sha256"],
        "privacy_review_sha256":drift["document"]["log_bundle"]["preview_sha256"]})
    text = (tmp_path / "diagnostics/diagnostic-trail.json").read_text()
    assert "never-share-this" not in text and str(tmp_path) not in text
    assert json.loads(text)["asset_validation"]["report_sha256"] == session["after_report"]["report_sha256"]
    installed.write_bytes(candidate_bytes)
    service.uninstall("offline-pipeline")
    assert installed.read_bytes() == b"test-owned pre-install bytes"
    recovery = desktop.inspect({"module":"optimization","workspace":str(output)})
    apply({"module":"optimization","workspace":str(output),"action":"recover","destination":str(tmp_path / "recovered"),"expected_state_sha256":recovery["state_sha256"]})
    assert desktop._inventory(tmp_path / "recovered") == original == desktop._inventory(source)
