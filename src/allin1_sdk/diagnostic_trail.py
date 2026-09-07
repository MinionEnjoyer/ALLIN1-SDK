"""Evidence-scoped artifact/install/session analysis. No crash cause is guessed."""
from datetime import datetime
import hashlib
import re
from pathlib import Path

from allin1_sdk.artifact_contract import digest, sha, validate_manifest, verify_seal
from allin1_sdk.release_paths import contained, strict_json
from allin1_sdk.workspace_desktop import path, file_hash


def _read(value):
    file=path(value)
    if not file.is_file() or file.stat().st_size>4*1024**2:
        raise ValueError("Choose a diagnostic JSON file of at most 4 MiB")
    with file.open("rb") as stream:
        data=stream.read(4*1024**2+1)
    if len(data)>4*1024**2:
        raise ValueError("Diagnostic input grew beyond 4 MiB")
    return strict_json(data), hashlib.sha256(data).hexdigest()


def validate_session(value):
    verify_seal(value,"record_sha256")
    if value.get("schema_version")!=1 or type(value.get("schema_version")) is not int or value.get("kind")!="launcher_runtime_session":
        raise ValueError("Unsupported runtime session")
    if not isinstance(value.get("session_id"),str) or not re.fullmatch("[a-f0-9]{32}",value["session_id"]):
        raise ValueError("Missing runtime session identity")
    previous=None
    module_count=0
    crash_observations=0
    events=value.get("events")
    if not isinstance(events,list) or not 1<=len(events)<=64 or events[0].get("type")!="session_start":
        raise ValueError("Missing or unbounded session events")
    last_time=datetime.fromisoformat(value["created_at"].replace("Z","+00:00"))
    if last_time.tzinfo is None:
        raise ValueError("Session clock has no timezone")
    for index,event in enumerate(events):
        verify_seal(event,"event_sha256")
        when=datetime.fromisoformat(event["timestamp"].replace("Z","+00:00"))
        if when.tzinfo is None or when<last_time or event.get("sequence")!=index or event.get("previous_sha256")!=previous:
            raise ValueError("Session event sequence, clock or chain disagrees")
        if not isinstance(event.get("data"),dict):
            raise ValueError("Malformed session event data")
        if event.get("type")=="process_identity":
            process=event["data"]
            if type(process.get("pid")) is not int or not 0<process["pid"]<2**32:
                raise ValueError("Invalid observed PID")
            started=datetime.fromisoformat(process["started_at"].replace("Z","+00:00"))
            if started.tzinfo is None or started>when:
                raise ValueError("Invalid observed process creation time")
            if Path(process["executable_path"]) not in {Path(value["game_path"])/"GTA5.exe",Path(value["game_path"])/"GTA5_Enhanced.exe"}:
                raise ValueError("Process identity belongs to another installation")
            sha(process.get("executable_sha256"))
        if event.get("type")=="module_paths":
            from allin1_sdk.crash_evidence import windows_path
            modules=event["data"].get("modules")
            if not isinstance(modules,list):
                raise ValueError("Malformed module observations")
            module_count+=len(modules)
            if module_count>512:
                raise ValueError("Session module observations exceed 512")
            for module in modules:
                if not isinstance(module,dict):
                    raise ValueError("Malformed module observation")
                windows_path(module.get("path"))
                if module.get("hash_status")=="on_disk_at_observation":
                    sha(module.get("file_sha256"))
        if event.get("type")=="crash_observation":
            crash_observations+=1
            data=event["data"];xmls=data.get("events")
            if crash_observations>3 or type(data.get("attempt")) is not int or not 1<=data["attempt"]<=3 or data.get("status") not in {"observed","no_matching_event","unavailable"} or not isinstance(xmls,list) or len(xmls)>4 or any(not isinstance(xml,str) or len(xml)>32768 for xml in xmls):
                raise ValueError("Malformed or unbounded session crash evidence")
            if bool(xmls)!=(data["status"]=="observed"):
                raise ValueError("Crash observation status disagrees with its events")
            from allin1_sdk.crash_evidence import parse,ticks
            for xml in xmls:
                for row in parse(xml.encode("utf-8")):
                    if row.get("timestamp") and ticks(row["timestamp"])>ticks(event["timestamp"]):
                        raise ValueError("Crash event occurs after its recorded collection time")
        last_time=when;previous=event["event_sha256"]
    if not isinstance(value.get("installed"),list) or len(value["installed"])>128:
        raise ValueError("Malformed installation snapshot")
    return value


def inspect(payload):
    artifact,artifact_sha=_read(payload.get("source"))
    validate_manifest(artifact)
    receipt,receipt_sha=_read(payload.get("comparison"))
    game=path(payload.get("gta_path"))
    if not game.is_dir():
        raise ValueError("Choose the corresponding GTA installation folder")
    findings=[]
    def add(level,code,message,**evidence):
        findings.append({"level":level,"code":code,"message":message,"evidence":evidence})
    lineage=receipt.get("sdk_provenance") if isinstance(receipt,dict) else None
    if not isinstance(lineage,dict):
        add("unresolved","untraced_installation","This receipt has no SDK artifact lineage; a matching version label is not build proof.")
        installed_artifact=None
    else:
        installed_artifact=validate_manifest(lineage["artifact"])
        if lineage.get("artifact_id")!=installed_artifact["artifact_id"] or lineage.get("build_fingerprint")!=installed_artifact["build"]["build_fingerprint"]:
            raise ValueError("Installation receipt has contradictory build identities")
        if installed_artifact["artifact_id"]!=artifact["artifact_id"]:
            add("verified","different_artifact_receipted","The receipt names a different artifact from the selected SDK output. Current bytes are checked separately below.",expected=artifact["artifact_id"],receipted=installed_artifact["artifact_id"])
        else:
            add("verified","artifact_receipt_link","The installation receipt identifies the selected SDK artifact.",artifact_id=artifact["artifact_id"])
    files=[];rpf_members=[];budget=2*1024**3
    if installed_artifact:
        if type(receipt.get("enabled")) is not bool or not isinstance(lineage.get("files"),list) or len(lineage["files"])>512:
            raise ValueError("Unsupported installation file evidence")
        if not receipt["enabled"]:
            add("verified","package_disabled","This package is disabled. Recovery payload hashes do not establish active game loading.")
        seen=set()
        for item in lineage["files"]:
            source=item["source"];destination=item["destination"]
            if destination.casefold() in seen:
                raise ValueError("Duplicate receipt destination")
            seen.add(destination.casefold())
            recorded=installed_artifact["outputs"][source]
            if recorded!=item["sha256"] or not any(row.get("destination")==destination and row.get("sha256")==recorded for row in receipt.get("files",[])):
                raise ValueError("Receipt source/destination hash mapping is inconsistent")
            expected=artifact["outputs"].get(source)
            relative=destination+("" if receipt["enabled"] else ".disabled")
            target=contained(game,relative)
            row={"source":source,"destination":relative,"receipted_sha256":recorded,"expected_sha256":expected,"actual_sha256":None}
            if not target.is_file():
                row["status"]="missing"
                add("verified","installed_file_missing","A receipted installed file is missing at the selected installation.",destination=relative)
            elif target.stat().st_size>budget:
                row["status"]="not_checked"
                add("unresolved","file_hash_budget","This file exceeds the remaining diagnostic hash budget.",destination=relative)
            else:
                budget-=target.stat().st_size
                actual=row["actual_sha256"]=file_hash(target)
                row["status"]="match" if actual==expected else "mismatch" if expected else "not_in_selected_artifact"
                if actual!=recorded:
                    add("verified","installed_bytes_changed","Current bytes differ from the installation receipt. This proves installation drift, not crash causality.",destination=relative,actual=actual,receipted=recorded)
                if expected and actual!=expected:
                    add("verified","selected_build_mismatch","Installed bytes do not match the selected SDK output.",destination=relative,actual=actual,expected=expected)
            files.append(row)
        if lineage.get("rpf_members"):
            from allin1_sdk.diagnostic_rpf import inspect as inspect_members
            rpf_members=inspect_members(game,receipt,installed_artifact,artifact)
            for member in rpf_members:
                evidence={key:member[key] for key in ("archive","entry","status","actual_sha256","expected_sha256")}
                if member["status"]=="match":
                    add("verified","installed_rpf_member_match","An exact installed RPF member matches the selected SDK output's extracted bytes. This does not prove runtime use.",**evidence)
                elif member["status"] in {"missing_archive","missing_member","mismatch","not_in_selected_artifact"}:
                    add("verified","installed_rpf_member_mismatch","The installed RPF member is absent or does not match the selected SDK artifact. Byte drift does not establish a crash cause or canonical resource difference.",**evidence)
                else:
                    add("unresolved","archive_members_unverified",member.get("reason","RPF member was not verified."),**evidence)
    session=None;session_sha=None
    if payload.get("document"):
        session,session_sha=_read(payload["document"])
        validate_session(session)
        if Path(session["game_path"])!=game:
            add("verified","session_installation_mismatch","The session observed a different installation path. Its runtime observations cannot certify this installation.")
        else:
            snapshots=[row for row in session["installed"] if row.get("receipt_sha256")==receipt_sha]
            if len(snapshots)!=1:
                add("unresolved","session_receipt_unlinked","No unique snapshot in this session matches the exact selected receipt; it may belong to another installation revision.")
            else:
                if not installed_artifact or snapshots[0].get("artifact_id")!=installed_artifact["artifact_id"] or snapshots[0].get("build_fingerprint")!=installed_artifact["build"]["build_fingerprint"]:
                    raise ValueError("Session installation snapshot contradicts the selected receipt")
                add("verified","session_receipt_link","The session captured the exact selected installation receipt.",session_id=session["session_id"],artifact_id=snapshots[0].get("artifact_id"))
            processes=[event["data"] for event in session["events"] if event["type"]=="process_identity"]
            if len(processes)==1:
                process=processes[0]
                observed_edition="Enhanced" if Path(process["executable_path"]).name.casefold()=="gta5_enhanced.exe" else "Legacy"
                if artifact["edition"] and artifact["edition"]!=observed_edition:
                    add("verified","runtime_edition_mismatch","The observed game edition differs from the selected artifact edition.",expected=artifact["edition"],observed=observed_edition)
                add("verified","process_identity_observed","The observer recorded an executable path, PID and creation time. The private installation path is redacted here; this is not proof that an asset rendered correctly.",pid=process["pid"],started_at=process["started_at"],executable="<game>/"+Path(process["executable_path"]).name,executable_sha256=process["executable_sha256"])
            else:
                add("unresolved","process_identity_missing","No unique complete process identity was recorded.")
            if session.get("status")=="process_disappeared":
                add("indicated","process_disappeared","The process stopped being observed. Normal exit and crash cannot be distinguished from this observation alone.")
            if session.get("status") in {"observer_unavailable","event_limit","process_identity_changed"}:
                add("unresolved","session_observation_incomplete","Runtime observation ended without a verified game-exit cause.",status=session["status"])
        add("unresolved","crash_cause_unestablished","This session does not independently establish an asset defect, runtime failure or unrelated-mod root cause. Correlated crash evidence and controlled reproduction are still needed.")
    else:
        add("unresolved","session_not_supplied","No runtime session was selected; installation hashes cannot prove what the game loaded.")
    crash=None;crash_sha=None;session_crash_sha=None
    observations=[event["data"] for event in session["events"] if event["type"]=="crash_observation"] if session else []
    if payload.get("crash_event"):
        from allin1_sdk.crash_evidence import read as read_crash, correlate
        events,crash_sha=read_crash(payload["crash_event"])
        crash=correlate(events,session,game,files)
        crash["source"]="selected_event_xml"
    elif observations:
        from allin1_sdk.crash_evidence import parse,correlate
        xmls=[xml for row in observations for xml in row["events"]]
        session_crash_sha=digest([hashlib.sha256(xml.encode("utf-8")).hexdigest() for xml in xmls])
        if xmls:
            events=[event for xml in xmls for event in parse(xml.encode("utf-8"))]
            crash=correlate(events,session,game,files)
            crash["source"]="launcher_session_event_query"
        else:
            add("unresolved","automatic_crash_evidence_unavailable","The bounded Launcher event queries did not capture a matching application-crash event. Logging may be delayed, disabled, inaccessible or outside the query window; this is not proof of normal exit.",attempts=len(observations))
        if any(row.get("query_truncated") for row in observations):
            add("unresolved","crash_query_truncated","At least one Windows event query reached its bound; event coverage is incomplete.")
    if crash is not None:
        add("verified" if crash["status"]=="application_crash_recorded" else "unresolved",crash["status"],crash["reason"],matches=len(crash["matches"]))
        if crash["status"]=="application_crash_recorded":
            add("unresolved","fault_site_not_root_cause","A correlated application crash is recorded, but its faulting module does not establish an asset defect or prove that another mod caused it. Controlled reproduction or dump analysis is still required.")
    result={"schema_version":1,"kind":"sdk_diagnostic_trail","read_only":True,"artifact_id":artifact["artifact_id"],
        "build_fingerprint":artifact["build"]["build_fingerprint"],"sdk_version":artifact["build"]["sdk_version"],"build_mode":artifact["build"]["mode"],"package_enabled":receipt.get("enabled"),
        "source_identities":{"artifact_sha256":artifact_sha,"receipt_sha256":receipt_sha,"session_sha256":session_sha,"crash_event_sha256":crash_sha,"session_crash_events_sha256":session_crash_sha},
        "files":files,"rpf_members":rpf_members,"findings":findings,"session_id":session["session_id"] if session else None,
        "crash_evidence":crash,"crash_cause":"not_established","scope":"Locally supplied content identities and observations, not publisher signatures or a causal crash verdict."}
    settings = payload.get("settings")
    if settings:
        if not isinstance(settings, dict):
            raise ValueError("Diagnostic settings must be an object")
        settings = dict(settings)
        selected_report = settings.pop("asset_report", None)
        if selected_report is not None:
            from allin1_sdk.diagnostic_asset_evidence import summarize, selected_report as extract_report
            selected, selected_sha = _read(selected_report)
            evidence = result["asset_validation"] = summarize(extract_report(selected), artifact, selected_sha)
            result["source_identities"]["asset_report_file_sha256"] = selected_sha
            if evidence["status"] == "recorded":
                add("verified", "asset_report_link", "The selected artifact records this exact static report; source/input versus candidate/output scope is separate.", report_sha256=evidence["report_sha256"], source_relation=evidence["source_relation"])
                if evidence["static_status"] == "fail":
                    add("indicated", "static_asset_findings", "The recorded report contains static failures. This is not proof the installed candidate has those failures or that they caused a crash.", source_relation=evidence["source_relation"])
            else:
                add("unresolved", "asset_report_unlinked", "The selected static report is not recorded by this artifact. Its findings cannot be attributed to this build.")
    if settings:
        from allin1_sdk.diagnostic_bundle import inspect as inspect_bundle
        result["log_bundle"]=inspect_bundle(settings,game)
    result["state_sha256"]=digest(result)
    return result
