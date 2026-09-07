"""Opt-in three-goal smoke against an actual frozen sidecar, never a game."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from allin1_sdk.release_identity import sha256, verify_inventory
from test_optimization_package import request as optimization_request


@pytest.mark.skipif(not os.environ.get("ALLIN1_FROZEN_SIDECAR"), reason="explicit frozen sidecar and resource paths required")
def test_frozen_validation_optimization_install_diagnostics_and_recovery(tmp_path, monkeypatch):
    sidecar = Path(os.environ["ALLIN1_FROZEN_SIDECAR"]).resolve(strict=True)
    resources = Path(os.environ["ALLIN1_FROZEN_RESOURCES"]).resolve(strict=True)
    verify_inventory(resources)
    binary_hash = sha256(sidecar)
    resource_hash = sha256(resources / "resource-checksums.json")
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "ALLIN1_DESKTOP_PYTHON", "ALLIN1_GTA_PATH"):
        environment.pop(key, None)
    environment.update(ALLIN1_SDK_HOME=str(resources), LOCALAPPDATA=str(tmp_path / "user"),
                       APPDATA=str(tmp_path / "user"), ALLIN1_PREVIEW_DIR=str(tmp_path / "preview"),
                       PATH=str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"))

    def invoke(operation, payload):
        envelope = {"protocol_version":"1.0.0", "request_id":"frozen-pipeline", "job_id":None,
                    "operation":operation, "payload":payload, "sequence":0, "risk":"none", "terminal":False}
        handshake = {**envelope,"request_id":"frozen-handshake","operation":"handshake",
                     "payload":{"client":{"name":"frozen-pipeline","version":"1.0.0"},"supported_versions":["1.0.0"]}}
        result = subprocess.run([str(sidecar), "--allow-package-writes", "--allow-rpf-writes"],
            input=json.dumps(handshake)+"\n"+json.dumps(envelope)+"\n", text=True, encoding="utf-8", capture_output=True,
            env=environment, cwd=tmp_path, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0, result.stderr
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert len(responses) == 2, result.stdout
        assert responses[0]["payload"]["build_identity"] == json.loads((resources / "build-identity.json").read_bytes())
        response = responses[1]
        assert response["operation"] == "result", json.dumps(response)
        return response["payload"]["result"]

    def apply(payload):
        reviewed = invoke("review_workspace_action", payload)
        return invoke("apply_workspace_action", {**payload,"review_sha256":reviewed["review_sha256"],"authoring_confirmed":True})

    payload = {"module":"optimization", **optimization_request(tmp_path)}
    source = Path(payload["source"])
    (source / "clip.ydr.xml").write_bytes((source / "car.ydr.xml").read_bytes())
    (source / "clip.meta").write_text('<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>clip</modelName><txdName>paint</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>')
    assemblies = [{"parent":"package:car.ydr.xml","parent_drawable":0,"parent_sha256":sha256(source / "car.ydr.xml"),"parent_bone":"tip",
                   "child":"package:clip.ydr.xml","child_drawable":0,"child_sha256":sha256(source / "clip.ydr.xml"),"child_bone":"", "offset":[2,0,0]}]
    payload["settings"]["assembly_bindings"] = assemblies
    (source / "mod.toml").write_text('schema_version = 1\nid = "frozen-pipeline"\nname = "Frozen pipeline"\nversion = "1.0.0"\ntype = "config"\neditions = ["legacy", "enhanced"]\n[[files]]\nsource = "assets/diffuse.dds"\ndestination = "scripts/fixture.dds"\n')
    original = (source / "assets/diffuse.dds").read_bytes()
    validation = invoke("inspect_authoring_workspace", {"module":"data_tools","task":"asset_validation","source":str(source),"settings":{"assembly_bindings":assemblies}})
    assert validation["document"]["runtime_status"] == "not_tested"
    assert validation["document"]["assembly_evidence"][0]["status"] == "checked"
    session = invoke("inspect_authoring_workspace", payload)
    assert session["before_report"]["assembly_evidence"] == session["after_report"]["assembly_evidence"]
    assert session["validation_context"]["assembly_pair_count"] == 1
    build = session["artifact_manifest"]["build"]
    assert build["mode"] == "frozen_verified_resources"
    assert build["executable_sha256"] == binary_hash
    assert build["source"] == json.loads((resources / "build-identity.json").read_bytes())
    output = tmp_path / "optimized"
    apply({**payload,"action":"export","destination":str(output),"expected_state_sha256":session["state_sha256"]})
    artifact = output / "package/sdk-artifact.json"
    assert json.loads(artifact.read_bytes())["artifact_id"] == session["artifact_manifest"]["artifact_id"]
    assert (source / "assets/diffuse.dds").read_bytes() == original

    launcher = Path(__file__).resolve().parents[2] / "ALLIN1/src"
    assert (launcher / "allin1/sdk_provenance.py").is_file(), "Matching Launcher is required for this integration"
    monkeypatch.syspath_prepend(str(launcher))
    from allin1.mods import ModManifest, ModIntegrationService
    game = tmp_path / "install-fixture"; game.mkdir()
    (game / "GTA5.exe").write_bytes(b"non-executable test marker")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(output / "package/mod.toml"))
    receipt = service._receipt_path("frozen-pipeline")
    report_request = {"module":"data_tools","task":"diagnostic_trail","source":str(artifact),
                      "comparison":str(receipt),"gta_path":str(game)}
    matched = invoke("inspect_authoring_workspace", report_request)
    assert matched["document"]["build_fingerprint"] == build["build_fingerprint"]
    assert matched["document"]["crash_cause"] == "not_established"
    installed_file = game / "scripts/fixture.dds"
    installed_bytes = installed_file.read_bytes()
    installed_file.write_bytes(b"test-owned installation drift")
    drift = invoke("inspect_authoring_workspace", report_request)
    assert "installed_bytes_changed" in {row["code"] for row in drift["document"]["findings"]}
    apply({**report_request,"action":"export","destination":str(tmp_path / "diagnostics"),"expected_state_sha256":drift["state_sha256"]})
    assert (tmp_path / "diagnostics/diagnostic-trail.json").is_file()
    installed_file.write_bytes(installed_bytes)
    service.uninstall("frozen-pipeline")
    recovery = invoke("inspect_authoring_workspace", {"module":"optimization","workspace":str(output)})
    apply({"module":"optimization","workspace":str(output),"action":"recover","destination":str(tmp_path / "recovered"),"expected_state_sha256":recovery["state_sha256"]})
    assert (tmp_path / "recovered/assets/diffuse.dds").read_bytes() == original
    assert sha256(sidecar) == binary_hash and sha256(resources / "resource-checksums.json") == resource_hash
