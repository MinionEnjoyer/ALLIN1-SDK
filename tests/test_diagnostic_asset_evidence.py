import json
from pathlib import Path

import pytest

from allin1_sdk import asset_validation, diagnostic_trail, diagnostic_asset_evidence, optimization_package, data_tools_desktop
from allin1_sdk.artifact_contract import digest, seal
from test_diagnostic_trail import fixture, codes
from test_artifact_identity import build
from test_optimization_package import request


def report_fixture():
    return asset_validation.analyze((Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes())


def reseal(value, key):
    value.pop(key, None)
    return seal(value, key)


@pytest.mark.parametrize("relation", ["inputs", "outputs", "neither"])
def test_recorded_report_scope_never_becomes_crash_cause(tmp_path, relation):
    payload, artifact, _ = fixture(tmp_path)
    report = report_fixture()
    if relation != "neither":
        report["source_sha256"] = digest(artifact[relation])
    report = reseal(report, "report_sha256")
    artifact["validation_reports"] = [report["report_sha256"]]
    artifact = reseal(artifact, "artifact_id")
    Path(payload["source"]).write_text(json.dumps(artifact))
    source = tmp_path / "report.json"; source.write_text(json.dumps(report))
    result = diagnostic_trail.inspect({**payload, "settings":{"asset_report":str(source)}})
    evidence = result["asset_validation"]
    assert "asset_report_link" in codes(result)
    assert evidence["source_relation"] == (relation + "_inventory" if relation != "neither" else "not_established")
    assert result["crash_cause"] == "not_established"
    assert str(tmp_path) not in json.dumps(evidence)
    assert "message" not in json.dumps(evidence)


def test_unlinked_report_and_selected_file_changes_are_explicit(tmp_path):
    payload, _, _ = fixture(tmp_path)
    source = tmp_path / "report.json"; source.write_text(json.dumps(report_fixture()))
    payload["settings"] = {"asset_report":str(source)}
    result = diagnostic_trail.inspect(payload)
    assert "asset_report_unlinked" in codes(result) and "asset_report_link" not in codes(result)
    source.write_text(source.read_text() + " ")
    assert diagnostic_trail.inspect(payload)["state_sha256"] != result["state_sha256"]


@pytest.mark.parametrize("mutation", ["seal", "status", "count", "category", "code", "runtime", "type"])
def test_malformed_or_contradictory_report_is_rejected(tmp_path, mutation):
    _, artifact, _ = fixture(tmp_path)
    report = report_fixture()
    if mutation == "seal": report["source_sha256"] = "a" * 64
    if mutation == "status": report["static_status"] = "pass"
    if mutation == "count": report["checks"][0]["finding_count"] = -1
    if mutation == "category": report["checks"][0]["category"] = report["checks"][1]["category"]
    if mutation == "code": report["checks"][1]["findings"][0]["code"] = "C:/private/file"
    if mutation == "runtime": report["runtime_status"] = "passed"
    if mutation == "type": report["checks"][0]["status"] = []
    if mutation != "seal": report = reseal(report, "report_sha256")
    with pytest.raises(ValueError):
        diagnostic_asset_evidence.summarize(report, artifact, "a" * 64)


def test_real_optimization_report_links_candidate_inventory_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(optimization_package.artifact_identity, "current", build)
    payload = request(tmp_path)
    session = optimization_package.inspect(payload)
    artifact = session["artifact_manifest"]
    after = session["after_report"]
    evidence = diagnostic_asset_evidence.summarize(after, artifact, digest(after))
    assert evidence["status"] == "recorded" and evidence["source_relation"] == "outputs_inventory"
    before = diagnostic_asset_evidence.summarize(session["before_report"], artifact, digest(session["before_report"]))
    assert before["source_relation"] == "inputs_inventory"
    assert evidence["runtime_status"] == before["runtime_status"] == "not_tested"
    output = tmp_path / "optimized"
    optimization_package.apply({**payload,"action":"export","destination":str(output),"expected_state_sha256":session["state_sha256"]})
    diagnostics = tmp_path / "diagnostics"; diagnostics.mkdir()
    diagnostic, _, _ = fixture(diagnostics)
    diagnostic["source"] = str(output / "package/sdk-artifact.json")
    diagnostic["settings"] = {"asset_report":str(output / "optimization.json")}
    result = diagnostic_trail.inspect(diagnostic)
    assert result["asset_validation"]["source_relation"] == "outputs_inventory"
    assert result["asset_validation"]["report_sha256"] == after["report_sha256"]
    assert result["crash_cause"] == "not_established"


@pytest.mark.parametrize("wrapper", [{"schema_version":True,"kind":"optimization_package"},
    {"schema_version":1,"kind":"optimization_package"},
    {"schema_version":1,"kind":"optimization_package","after_report":{"report_sha256":"a"*64}}])
def test_invalid_optimization_wrapper_or_candidate_report_fails(tmp_path, wrapper):
    _, artifact, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        diagnostic_asset_evidence.summarize(diagnostic_asset_evidence.selected_report(wrapper), artifact, "a"*64)


@pytest.mark.parametrize("truncated", [False, True])
def test_static_failure_and_truncated_evidence_stay_noncausal(tmp_path, truncated):
    payload, artifact, _ = fixture(tmp_path)
    report = report_fixture()
    report["checks"][0].update(status="fail", finding_count=45 if truncated else 1, truncated=truncated,
        findings=[{"code":"invalid_skeleton", "status":"fail", "message":"private information", "location":str(tmp_path)}])
    report["static_status"] = "fail"
    report = reseal(report, "report_sha256")
    artifact["validation_reports"] = [report["report_sha256"]]
    Path(payload["source"]).write_text(json.dumps(reseal(artifact, "artifact_id")))
    source = tmp_path / "report.json"; source.write_text(json.dumps(report))
    result = diagnostic_trail.inspect({**payload, "settings":{"asset_report":str(source)}})
    assert "static_asset_findings" in codes(result)
    assert result["crash_cause"] == "not_established"
    assert result["asset_validation"]["checks"][0]["truncated"] is truncated
    assert "private information" not in json.dumps(result)


def test_export_binds_selected_report_bytes_and_coexists_with_redacted_logs(tmp_path):
    payload, _, _ = fixture(tmp_path)
    source = tmp_path / "report.json"; source.write_text(json.dumps(report_fixture()))
    log = tmp_path / "runtime.log"; log.write_text("version=1.4.0\npassword=do-not-export\n")
    payload.update(task="diagnostic_trail", settings={"asset_report":str(source),
        "logs":[{"source":str(log), "start_line":1,"line_count":2}], "redact_terms":[]})
    result = data_tools_desktop.inspect(payload)
    destination = tmp_path / "export"
    request = {**payload, "action":"export", "destination":str(destination),
        "expected_state_sha256":result["state_sha256"],
        "privacy_review_sha256":result["document"]["log_bundle"]["preview_sha256"]}
    data_tools_desktop.review(request)
    data_tools_desktop.apply(request)
    exported = (destination / "diagnostic-trail.json").read_text()
    assert json.loads(exported)["asset_validation"] == result["document"]["asset_validation"]
    assert "do-not-export" not in exported and str(source) not in exported
    assert "asset_report" in payload["settings"]  # Inspection must not mutate the input.
    source.write_text(source.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        data_tools_desktop.review({**request, "destination":str(tmp_path / "stale")})
    assert not (tmp_path / "stale").exists()
