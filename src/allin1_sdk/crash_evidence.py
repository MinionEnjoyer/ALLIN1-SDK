"""Bounded, selected Windows Application Error XML; never infer a root cause.

Event 1000 semantics and FILETIME epoch are documented by Microsoft:
https://learn.microsoft.com/troubleshoot/windows-server/performance/troubleshoot-application-service-crashing-behavior
https://learn.microsoft.com/windows/win32/api/minwinbase/ns-minwinbase-filetime
"""
from datetime import datetime, timezone
import hashlib
import ntpath
import re

from lxml import etree

from allin1_sdk.workspace_desktop import path

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"


def ticks(value):
    """ISO time to integer FILETIME ticks, preserving the seventh fractional digit."""
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,7}))?(Z|[+-]\d\d:\d\d)", value)
    if not match:
        raise ValueError("Process/event timestamp needs an explicit timezone and at most seven fractional digits")
    base = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
    elapsed = base - datetime(1601, 1, 1, tzinfo=timezone.utc)
    return (elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + int((match[2] or "").ljust(7, "0"))


def windows_path(value):
    if not isinstance(value, str) or not value or len(value) > 32768 or "\x00" in value:
        raise ValueError("Invalid event path")
    return ntpath.normcase(ntpath.normpath(value))


def _number(value, bits):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0[xX][a-fA-F0-9]+|[0-9]+)", value):
        raise ValueError("Invalid process identity in crash event")
    result = int(value, 16 if value.lower().startswith("0x") else 10)
    if not 0 < result < 2**bits:
        raise ValueError("Crash event process identity is out of range")
    return result


def read(source):
    file = path(source)
    if not file.is_file() or file.stat().st_size > 1024**2:
        raise ValueError("Choose exported event XML of at most 1 MiB, not an EVTX or crash dump")
    with file.open("rb") as stream:
        data = stream.read(1024**2 + 1)
    if len(data) > 1024**2:
        raise ValueError("Event XML grew beyond 1 MiB")
    return parse(data),hashlib.sha256(data).hexdigest()


def parse(data):
    if not isinstance(data,bytes) or len(data)>1024**2:
        raise ValueError("Event XML exceeds 1 MiB")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError("Malformed exported event XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("Event XML must not contain a DTD")
    events = [root] if root.tag == NS + "Event" else list(root) if root.tag in {"Events", NS + "Events"} else []
    if not 1 <= len(events) <= 32 or any(event.tag != NS + "Event" for event in events):
        raise ValueError("Choose one Event or an Events collection of at most 32 Windows events")
    rows = []
    for event in events:
        def one(parent, name):
            children = parent.findall(NS + name)
            if len(children) != 1:
                raise ValueError("Missing or duplicate Windows event field: " + name)
            return children[0]
        system = one(event, "System")
        if one(system, "Provider").get("Name") != "Application Error" or one(system, "EventID").text != "1000" or one(system, "Level").text != "2" or one(system, "Channel").text != "Application":
            rows.append({"status": "unsupported_event"})
            continue
        when = one(system, "TimeCreated").get("SystemTime", "")
        ticks(when)
        fields = {}
        for item in one(event, "EventData"):
            name = item.get("Name")
            if item.tag != NS + "Data" or not name or name in fields or len(item) or len(item.text or "") > 32768:
                raise ValueError("Malformed or duplicate named Application Error field")
            fields[name] = item.text or ""
        if not all(fields.get(key) for key in ("ProcessId", "ProcessCreationTime", "AppPath")):
            rows.append({"status": "process_identity_incomplete"})
            continue
        row = {"status": "application_error", "timestamp": when, "pid": _number(fields["ProcessId"], 32),
               "creation_ticks": _number(fields["ProcessCreationTime"], 64), "app_path": windows_path(fields["AppPath"]),
               "module_path": windows_path(fields["ModulePath"]) if fields.get("ModulePath") else None}
        for key, target, size in (("ExceptionCode", "exception_code", 8), ("FaultingOffset", "fault_offset", 16)):
            value = fields.get(key, "").removeprefix("0x")
            if value and not re.fullmatch(r"[a-fA-F0-9]{1," + str(size) + "}", value):
                raise ValueError("Invalid exception code or fault offset")
            row[target] = "0x" + value.lower().zfill(size) if value else None
        rows.append(row)
    return rows


def correlate(rows, session, game, files):
    """Return only whitelisted derived evidence; raw host/user/path fields never leave here."""
    result = {"status": "unlinked", "events_considered": len(rows), "matches": [], "root_cause": "not_established"}
    if not session or windows_path(session["game_path"]) != windows_path(str(game)):
        result["reason"] = "A session for this installation is required."
        return result
    identities = [event for event in session["events"] if event["type"] == "process_identity"]
    if len(identities) != 1:
        result["reason"] = "No unique observed process identity is available."
        return result
    process = identities[0]["data"]
    for row in rows:
        if row["status"] != "application_error" or row["pid"] != process["pid"] or row["app_path"] != windows_path(process["executable_path"]) or row["creation_ticks"] != ticks(process["started_at"]) or ticks(row["timestamp"]) < ticks(identities[0]["timestamp"]):
            continue
        module = row["module_path"]
        observed = [item for event in session["events"] if event["type"] == "module_paths" and ticks(event["timestamp"]) <= ticks(row["timestamp"])
                    for item in event["data"].get("modules", []) if windows_path(item["path"]) == module]
        hashes = {item["file_sha256"] for item in observed if item.get("hash_status") == "on_disk_at_observation" and isinstance(item.get("file_sha256"), str) and re.fullmatch("[a-f0-9]{64}", item["file_sha256"])}
        selected = [item for item in files if windows_path(ntpath.join(str(game), item["destination"])) == module and item.get("expected_sha256") in hashes]
        result["matches"].append({"timestamp": row["timestamp"], "pid": row["pid"], "process_creation_ticks": str(row["creation_ticks"]),
            "exception_code": row["exception_code"], "fault_offset": row["fault_offset"],
            "faulting_module": ntpath.basename(module) if module else None,
            "module_observed": bool(observed), "module_matches_selected_artifact": bool(selected),
            "observed_file_sha256": sorted(hashes),
            "module_scope": "selected_artifact" if selected else "not_linked_to_selected_artifact"})
    if result["matches"]:
        result["status"] = "application_crash_recorded"
        result["reason"] = "Supplied Application Error event matches executable path, PID, exact process creation time and observation chronology. A faulting module identifies the fault site, not necessarily its cause."
    else:
        result["reason"] = "No supported event matched all process identity fields and chronology; timestamps or executable names alone are insufficient."
    return result
