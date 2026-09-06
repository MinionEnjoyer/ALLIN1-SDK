import json
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from allin1_sdk import crash_evidence as crash
from allin1_sdk import diagnostic_trail as diagnostic
from allin1_sdk.artifact_contract import digest
from test_diagnostic_trail import fixture, session_file


def event_file(root, payload, **overrides):
    values = {"ProcessId": "0x7b", "ProcessCreationTime": hex(crash.ticks("2026-01-01T00:00:00Z")),
              "AppPath": str(Path(payload["gta_path"])/"GTA5.exe"), "ModulePath": r"C:\Windows\System32\ntdll.dll",
              "ExceptionCode": "c0000005", "FaultingOffset": "12"}
    values.update(overrides)
    fields = "".join(f'<Data Name="{key}">{escape(value)}</Data>' for key, value in values.items())
    xml = f'''<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System>
    <Provider Name="Application Error"/><EventID>1000</EventID><Level>2</Level><Channel>Application</Channel>
    <TimeCreated SystemTime="2026-01-01T00:00:02Z"/><Computer>PRIVATE-HOST</Computer><Security UserID="PRIVATE-SID"/>
    </System><EventData>{fields}</EventData></Event>'''
    file = root/"crash.xml";file.write_text(xml)
    return file


def test_correlated_crash_does_not_claim_module_or_asset_causality_and_redacts(tmp_path):
    payload, artifact, _ = fixture(tmp_path)
    session, _ = session_file(tmp_path, payload, artifact)
    event = event_file(tmp_path, payload)
    report = diagnostic.inspect({**payload, "document": str(session), "crash_event": str(event)})
    evidence = report["crash_evidence"]
    assert evidence["status"] == "application_crash_recorded"
    assert evidence["matches"][0]["faulting_module"] == "ntdll.dll"
    assert evidence["matches"][0]["module_scope"] == "not_linked_to_selected_artifact"
    assert evidence["root_cause"] == report["crash_cause"] == "not_established"
    assert report["source_identities"]["crash_event_sha256"] == diagnostic.file_hash(event)
    text = json.dumps(report)
    assert str(tmp_path) not in text and "PRIVATE-" not in text and "System32" not in text


@pytest.mark.parametrize("changes", [{"ProcessId": "0x7c"}, {"ProcessCreationTime": "0x1"},
    {"AppPath": r"D:\OtherGame\GTA5.exe"}, {"ProcessCreationTime": ""}])
def test_stale_pid_wrong_installation_or_missing_identity_never_correlate(tmp_path, changes):
    payload, artifact, _ = fixture(tmp_path)
    _, session = session_file(tmp_path, payload, artifact)
    rows, _ = crash.read(str(event_file(tmp_path, payload, **changes)))
    assert crash.correlate(rows, session, payload["gta_path"], [])["status"] == "unlinked"


def test_exact_seventh_fraction_digit_is_not_rounded_into_pid_reuse_match():
    assert crash.ticks("2026-01-01T00:00:00.0000001Z") - crash.ticks("2026-01-01T00:00:00Z") == 1
    assert crash.ticks("2025-12-31T16:00:00-08:00") == crash.ticks("2026-01-01T00:00:00Z")
    with pytest.raises(ValueError): crash.ticks("2026-01-01T00:00:00")


@pytest.mark.parametrize("mutation", ["duplicate", "dtd", "bound", "bad_pid"])
def test_malformed_or_unbounded_event_input_rejected(tmp_path, mutation):
    payload, _, _ = fixture(tmp_path)
    file = event_file(tmp_path, payload)
    text = file.read_text()
    if mutation == "duplicate": text = text.replace("</EventData>", '<Data Name="ProcessId">123</Data></EventData>')
    elif mutation == "dtd": text = '<!DOCTYPE Event [<!ENTITY sample SYSTEM "file:///private">]>' + text
    elif mutation == "bound": text = "<Events>" + text*33 + "</Events>"
    else: text = text.replace("0x7b", "NaN")
    file.write_text(text)
    with pytest.raises(ValueError): crash.read(str(file))


def test_module_binding_needs_observed_path_and_artifact_hash_not_just_basename(tmp_path):
    payload, artifact, _ = fixture(tmp_path)
    file, session = session_file(tmp_path, payload, artifact)
    module = str(Path(payload["gta_path"])/"scripts/owned.asi")
    event = event_file(tmp_path, payload, ModulePath=module)
    session["events"].insert(2, {"timestamp": "2026-01-01T00:00:01Z", "type": "module_paths", "data": {"modules": [
        {"path": module, "hash_status": "on_disk_at_observation", "file_sha256": artifact["outputs"]["owned.asi"]}]}})
    previous = None
    for index, item in enumerate(session["events"]):
        item.pop("event_sha256", None);item.update(sequence=index, previous_sha256=previous)
        item["event_sha256"] = previous = digest(item)
    session.pop("record_sha256");session["record_sha256"] = digest(session)
    file.write_text(json.dumps(session))
    report = diagnostic.inspect({**payload, "document": str(file), "crash_event": str(event)})
    assert report["crash_evidence"]["matches"][0]["module_matches_selected_artifact"] is True
    assert report["crash_cause"] == "not_established"
    rows, _ = crash.read(str(event_file(tmp_path, payload, ModulePath=r"D:\other\owned.asi")))
    evidence = crash.correlate(rows, session, payload["gta_path"], report["files"])
    assert evidence["matches"][0]["module_matches_selected_artifact"] is False


def test_no_session_and_unsupported_provider_are_not_crash_proof(tmp_path):
    payload, _, _ = fixture(tmp_path)
    file = event_file(tmp_path, payload)
    rows, _ = crash.read(str(file))
    assert crash.correlate(rows, None, payload["gta_path"], [])["status"] == "unlinked"
    file.write_text(file.read_text().replace("Application Error", "Other Provider"))
    rows, _ = crash.read(str(file))
    assert rows == [{"status": "unsupported_event"}]


def collected_session(tmp_path, payload, artifact, observations):
    file,session=session_file(tmp_path,payload,artifact)
    for data in observations:
        event={"sequence":len(session["events"]),"timestamp":"2026-01-01T00:00:03Z","type":"crash_observation","data":data,"previous_sha256":session["events"][-1]["event_sha256"]}
        event["event_sha256"]=digest(event);session["events"].append(event)
    session.pop("record_sha256");session["record_sha256"]=digest(session)
    file.write_text(json.dumps(session))
    return file


def test_launcher_collected_event_correlates_without_selecting_another_file(tmp_path):
    payload,artifact,_=fixture(tmp_path)
    xml=event_file(tmp_path,payload).read_text()
    file=collected_session(tmp_path,payload,artifact,[{"attempt":1,"status":"no_matching_event","events":[]},{"attempt":2,"status":"observed","events":[xml],"query_truncated":False}])
    report=diagnostic.inspect({**payload,"document":str(file)})
    assert report["crash_evidence"]["status"]=="application_crash_recorded"
    assert report["crash_evidence"]["source"]=="launcher_session_event_query"
    assert report["source_identities"]["session_crash_events_sha256"]
    assert report["source_identities"]["crash_event_sha256"] is None
    assert "PRIVATE-HOST" not in json.dumps(report)
    assert report["crash_cause"]=="not_established"


def test_no_automatic_event_is_not_normal_exit_proof(tmp_path):
    payload,artifact,_=fixture(tmp_path)
    file=collected_session(tmp_path,payload,artifact,[{"attempt":1,"status":"no_matching_event","events":[],"query_truncated":True}])
    report=diagnostic.inspect({**payload,"document":str(file)})
    assert {"automatic_crash_evidence_unavailable","crash_query_truncated"}<={f["code"] for f in report["findings"]}
    assert report["crash_evidence"] is None and report["crash_cause"]=="not_established"


def test_event_cannot_precede_its_own_collection_timestamp(tmp_path):
    payload,artifact,_=fixture(tmp_path)
    xml=event_file(tmp_path,payload).read_text().replace("00:00:02Z","00:00:04Z")
    file=collected_session(tmp_path,payload,artifact,[{"attempt":1,"status":"observed","events":[xml]}])
    with pytest.raises(ValueError,match="after its recorded collection"):
        diagnostic.inspect({**payload,"document":str(file)})
