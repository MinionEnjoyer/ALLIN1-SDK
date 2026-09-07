import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk import optimization_package as optimization
from allin1_sdk.artifact_contract import seal
from allin1_sdk.workspace_desktop import _inventory
from test_artifact_identity import build
from test_optimization_package import request


@pytest.fixture
def exported(tmp_path, monkeypatch):
    monkeypatch.setattr(optimization.artifact_identity, "current", build)
    payload = request(tmp_path)
    session = optimization.inspect(payload)
    destination = tmp_path / "export"
    export = {**payload,"action":"export","destination":str(destination),"expected_state_sha256":session["state_sha256"]}
    optimization.apply(export)
    return payload, export, destination


def restate(receipt):
    receipt["state_sha256"] = optimization._state({k:v for k,v in receipt.items() if k not in {"schema_version","kind"}})


@pytest.mark.parametrize("fault", ["state","schema","runtime","artifact","inputs","outputs","edition","changes","before_report","after_report","severity"])
def test_recovery_rejects_contradictory_receipt_even_after_reinspection(exported, fault):
    _, _, root = exported
    file = root / "optimization.json"; receipt = json.loads(file.read_bytes())
    if fault == "state": receipt["preservation"] = "altered"
    elif fault == "schema": receipt["schema_version"] = True
    elif fault == "runtime": receipt["runtime_status"] = "passed"
    elif fault == "artifact": receipt["artifact_manifest"]["artifact_id"] = "0" * 64
    elif fault in {"inputs","outputs"}: receipt["before_inventory" if fault == "inputs" else "after_inventory"]["unexpected.xml"] = "a"*64
    elif fault == "edition": receipt["edition"] = "Enhanced"
    elif fault == "changes": receipt["changes"][0]["role"] = "unexpected"
    elif fault in {"before_report","after_report"}: receipt[fault]["source_sha256"] = "a" * 64
    else:
        report = receipt["after_report"]
        report["static_status"] = "pass" if report["static_status"] != "pass" else "fail"
        report.pop("report_sha256")
        receipt["after_report"] = seal(report,"report_sha256")
    if fault != "state": restate(receipt)
    file.write_text(json.dumps(receipt))
    with pytest.raises(ValueError): optimization.inspect({"workspace":str(root)})


@pytest.mark.parametrize("changes", [None,{},[None],["wrong"]])
def test_malformed_changes_fail_cleanly(exported,changes):
    _, _, root = exported
    file = root / "optimization.json"; receipt = json.loads(file.read_bytes())
    receipt["changes"] = changes; file.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match="change report"):
        optimization.inspect({"workspace":str(root)})


def test_recovery_does_not_depend_on_candidate_or_original_source(exported, tmp_path):
    payload, _, root = exported
    expected = _inventory(root / "originals")
    # Rename only fixture-owned directories; no destruction needed.
    (root / "package").rename(root / "candidate-unavailable")
    Path(payload["source"]).rename(tmp_path / "source-unavailable")
    result = optimization.inspect({"workspace":str(root)})
    optimization.apply({"action":"recover","workspace":str(root),"destination":str(tmp_path / "recovered"),"expected_state_sha256":result["state_sha256"]})
    assert _inventory(tmp_path / "recovered") == expected


@pytest.mark.parametrize("fault", ["originals","candidate","artifact","receipt","unexpected"])
def test_final_export_readback_rejects_staged_drift(exported, tmp_path, monkeypatch, fault):
    _, export, _ = exported
    original_copy = optimization._copy
    stage = {}
    def copy(source,destination,inventory):
        original_copy(source,destination,inventory)
        if destination.name == "package": stage["root"] = destination.parent
    monkeypatch.setattr(optimization,"_copy",copy)
    original_recheck = optimization._recheck_inputs
    def recheck(result):
        original_recheck(result)
        root = stage["root"]
        selected = {"originals":root / "originals/car.ydr.xml","candidate":root / "package/car.ydr.xml",
                    "artifact":root / "package/sdk-artifact.json","receipt":root / "optimization.json","unexpected":root / "unexpected.txt"}[fault]
        selected.write_bytes(b"injected staged drift")
    monkeypatch.setattr(optimization,"_recheck_inputs",recheck)
    destination = tmp_path / "blocked"
    with pytest.raises(ValueError): optimization.apply({**export,"destination":str(destination)})
    assert not destination.exists()
    assert not list(tmp_path.glob(".allin1-optimized-*"))


@pytest.mark.parametrize("operation", ["export","recover"])
@pytest.mark.parametrize("fault", ["low_space","interrupted_copy"])
def test_failed_copy_never_publishes_partial_output(exported,tmp_path,monkeypatch,operation,fault):
    payload, export, root = exported
    before = _inventory(Path(payload["source"]))
    recovered = optimization.inspect({"workspace":str(root)})
    destination = tmp_path / "failed"
    call = {**export,"destination":str(destination)} if operation == "export" else {
        "action":"recover","workspace":str(root),"destination":str(destination),"expected_state_sha256":recovered["state_sha256"]}
    def final_stage(target):
        return any(part.startswith(".allin1-optimized-" if operation == "export" else ".allin1-recovery-") for part in Path(target).parts)
    if fault == "low_space":
        usage = optimization.shutil.disk_usage
        monkeypatch.setattr(optimization.shutil,"disk_usage",lambda target:SimpleNamespace(free=0) if final_stage(target) else usage(target))
    else:
        copy = optimization.shutil.copyfile
        def interrupted(source,target,*args,**kwargs):
            copy(source,target,*args,**kwargs)
            if final_stage(target): raise OSError("injected interrupted copy")
        monkeypatch.setattr(optimization.shutil,"copyfile",interrupted)
    with pytest.raises((ValueError,OSError)): optimization.apply(call)
    assert not destination.exists()
    assert not list(tmp_path.glob(".allin1-optimized-*")) and not list(tmp_path.glob(".allin1-recovery-*"))
    assert _inventory(Path(payload["source"])) == before
    assert _inventory(root / "originals") == before


@pytest.mark.parametrize("fault", ["receipt","originals","staged"])
def test_recovery_rechecks_after_copy(exported,tmp_path,monkeypatch,fault):
    _, _, root = exported
    result = optimization.inspect({"workspace":str(root)})
    copy = optimization._copy
    def drift(source,destination,inventory):
        copy(source,destination,inventory)
        if fault == "receipt":
            file = root / "optimization.json"; receipt = json.loads(file.read_bytes())
            receipt["preservation"] = "changed during recovery"; restate(receipt)
            file.write_text(json.dumps(receipt))
        else: (source if fault == "originals" else destination).joinpath("car.ydr.xml").write_bytes(b"changed during recovery")
    monkeypatch.setattr(optimization,"_copy",drift)
    with pytest.raises(ValueError): optimization.apply({"action":"recover","workspace":str(root),"destination":str(tmp_path / "blocked"),"expected_state_sha256":result["state_sha256"]})
    assert not (tmp_path / "blocked").exists()
