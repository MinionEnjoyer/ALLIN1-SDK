import json
from pathlib import Path

import pytest

from allin1_sdk import diagnostic_trail as diagnostic
from allin1_sdk.artifact_contract import digest
from test_artifact_identity import build
from allin1_sdk.artifact_identity import manifest


def fixture(tmp_path):
    game=tmp_path/"game";game.mkdir();(game/"scripts").mkdir();(game/"scripts/owned.asi").write_bytes(b"owned runtime")
    checksum=diagnostic.file_hash(game/"scripts/owned.asi")
    artifact=manifest(build(),{"before.asi":"a"*64},{"owned.asi":checksum},edition="Legacy")
    source=tmp_path/"artifact.json";source.write_text(json.dumps(artifact))
    receipt={"id":"owned","enabled":True,"files":[{"destination":"scripts/owned.asi","sha256":checksum}],
        "sdk_provenance":{"artifact":artifact,"artifact_id":artifact["artifact_id"],"build_fingerprint":artifact["build"]["build_fingerprint"],
            "files":[{"source":"owned.asi","destination":"scripts/owned.asi","sha256":checksum}],"rpf_members":[]}}
    comparison=tmp_path/"receipt.json";comparison.write_text(json.dumps(receipt))
    return {"source":str(source),"comparison":str(comparison),"gta_path":str(game)},artifact,receipt


def codes(report):return {row["code"] for row in report["findings"]}


def test_current_file_hashes_and_build_lineage_without_runtime_claim(tmp_path):
    payload,_,_=fixture(tmp_path)
    report=diagnostic.inspect(payload)
    assert report["files"][0]["status"]=="match"
    assert "artifact_receipt_link" in codes(report) and "session_not_supplied" in codes(report)
    assert report["crash_cause"]=="not_established"
    (Path(payload["gta_path"])/"scripts/owned.asi").write_bytes(b"old or unrelated")
    report=diagnostic.inspect(payload)
    assert {"installed_bytes_changed","selected_build_mismatch"} <= codes(report)
    assert report["crash_cause"]=="not_established"


def test_receipted_different_build_and_untraced_version_are_explicit(tmp_path):
    payload,artifact,receipt=fixture(tmp_path)
    newer=manifest(build(),{"before.asi":"b"*64},{"owned.asi":"c"*64},edition="Legacy")
    Path(payload["source"]).write_text(json.dumps(newer))
    report=diagnostic.inspect(payload)
    assert {"different_artifact_receipted","selected_build_mismatch"} <= codes(report)
    receipt.pop("sdk_provenance");receipt["version"]="1.4.0"
    Path(payload["comparison"]).write_text(json.dumps(receipt))
    report=diagnostic.inspect(payload)
    assert "untraced_installation" in codes(report) and not report["files"]


def session_file(tmp_path,payload,artifact):
    session={"schema_version":1,"kind":"launcher_runtime_session","session_id":"f"*32,"created_at":"2026-01-01T00:00:00Z",
        "game_path":payload["gta_path"],"installed":[{"receipt_sha256":diagnostic.file_hash(Path(payload["comparison"])),"artifact_id":artifact["artifact_id"],"build_fingerprint":artifact["build"]["build_fingerprint"]}],"events":[],"status":"process_disappeared"}
    previous=None
    for index,(kind,data) in enumerate([("session_start",{}),("process_identity",{"pid":123,"started_at":"2026-01-01T00:00:00Z","executable_path":str(Path(payload["gta_path"])/"GTA5.exe"),"executable_sha256":"e"*64}),("session_end",{"reason":"Process no longer observed"})]):
        event={"sequence":index,"timestamp":f"2026-01-01T00:00:0{index}Z","type":kind,"data":data,"previous_sha256":previous}
        event["event_sha256"]=digest(event);previous=event["event_sha256"];session["events"].append(event)
    session["record_sha256"]=digest(session)
    file=tmp_path/"session.json";file.write_text(json.dumps(session))
    return file,session


def test_session_correlates_exact_receipt_but_disappearance_is_not_crash_proof(tmp_path):
    payload,artifact,_=fixture(tmp_path)
    file,_=session_file(tmp_path,payload,artifact)
    report=diagnostic.inspect({**payload,"document":str(file)})
    assert {"session_receipt_link","process_identity_observed","process_disappeared","crash_cause_unestablished"} <= codes(report)
    assert report["crash_cause"]=="not_established"


@pytest.mark.parametrize("mutation",["hash","chain","clock","pid","process_path"])
def test_altered_or_contradictory_session_evidence_is_rejected(tmp_path,mutation):
    payload,artifact,_=fixture(tmp_path)
    file,value=session_file(tmp_path,payload,artifact)
    if mutation=="hash":value["status"]="passed"
    else:
        event=value["events"][1]
        if mutation=="chain":event["previous_sha256"]="a"*64
        elif mutation=="clock":event["timestamp"]="2025-01-01T00:00:00Z"
        elif mutation=="pid":event["data"]["pid"]=-1
        else:event["data"]["executable_path"]="elsewhere.exe"
        event["event_sha256"]=digest({k:v for k,v in event.items() if k!="event_sha256"})
        value["events"][2]["previous_sha256"]=event["event_sha256"]
        value["events"][2]["event_sha256"]=digest({k:v for k,v in value["events"][2].items() if k!="event_sha256"})
        value["record_sha256"]=digest({k:v for k,v in value.items() if k!="record_sha256"})
    file.write_text(json.dumps(value))
    with pytest.raises(ValueError):diagnostic.inspect({**payload,"document":str(file)})
