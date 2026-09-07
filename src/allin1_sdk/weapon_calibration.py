"""Local, immutable sight observations. Measurements are not ballistic fixes.

All writes go through weapon_desktop review/apply; game files are never written.
Camera conditions and checklist outcomes are explicitly operator observations.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from PIL import Image
from lxml import etree

from allin1_sdk.artifact_contract import digest, verify_seal
from allin1_sdk.authoring_core import safe_xml_parser
from allin1_sdk.release_paths import strict_json
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from allin1_sdk.weapon_camera import CAMERA_FIELDS, validate_advanced_value

CHECKS = ("aim", "fire", "reload", "camera_switch", "attachment_stability")
STORE = ".weapon-calibration"
MAX_IMAGE = 16 * 1024**2


def number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be finite and between {low} and {high}")
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError("Use a session ID of 1–80 letters, digits, underscores or hyphens")
    return value


def workspace(payload):
    from allin1_sdk.weapon_desktop import _path
    return WeaponAuthoringWorkspace(_path(payload, "workspace", writable=True))


def store(ws):
    root = ws.root / STORE
    if root.is_symlink() or root.resolve().parent != ws.root.resolve():
        raise ValueError("Calibration store must remain directly inside the editable workspace")
    return root


def image_evidence(raw):
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        raise ValueError("Choose a screenshot path")
    file = Path(raw)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_IMAGE:
        raise ValueError("Choose a regular PNG/JPEG screenshot of at most 16 MiB")
    with file.open("rb") as stream:
        data = stream.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ValueError("Screenshot grew beyond 16 MiB")
    from io import BytesIO
    with Image.open(BytesIO(data)) as picture:
        if picture.format not in {"PNG", "JPEG"} or picture.width * picture.height > 32_000_000:
            raise ValueError("Use PNG/JPEG with at most 32 million pixels")
        if picture.getexif().get(274, 1) != 1:
            raise ValueError("Normalize screenshot EXIF rotation before marking; stored pixel coordinates must match display orientation")
        dimensions = [picture.width, picture.height]
    with Image.open(BytesIO(data)) as picture:
        picture.verify()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "width": dimensions[0], "height": dimensions[1]}, data


def measurements(draft, picture):
    width, height = picture["width"], picture["height"]
    def point(raw):
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError("Each mark needs [x, y] screenshot pixel coordinates")
        return [number(raw[0], 0, width - 1, "X"), number(raw[1], 0, height - 1, "Y")]
    aim, sight = point(draft.get("aim")), point(draft.get("sight"))
    impacts = draft.get("impacts", [])
    if not isinstance(impacts, list) or len(impacts) > 64:
        raise ValueError("Use at most 64 impact marks from the same fixed-camera frame")
    impacts = [point(p) for p in impacts]
    visual = [sight[i] - aim[i] for i in range(2)]
    impact = [sum(p[i] - aim[i] for p in impacts) / len(impacts) for i in range(2)] if impacts else None
    return {"aim": aim, "sight": sight, "impacts": impacts,
            "visual_error_px": visual, "impact_centroid_error_px": impact,
            "impact_count": len(impacts), "axes": "X right, Y down; screenshot pixels",
            "ballistic_correction": "not_inferred"}


def package_invariant(ws, weapon, values):
    """Byte identity except this weapon's existing camera attributes.

The complete values dictionary is compared separately to prove one-field trials.
XML serialization differences alone do not count as another asset change.
"""
    files = {}; budget = 2 * 1024**3
    for file in sorted(ws.source.rglob("*")):
        if file.is_symlink():
            raise ValueError("Calibration source cannot contain symlinks")
        if not file.is_file():
            continue
        if len(files) >= 4096 or file.stat().st_size > budget:
            raise ValueError("Calibration source exceeds 4096 files / 2 GiB")
        before_stat = file.stat()
        budget -= before_stat.st_size
        relative = file.relative_to(ws.source).as_posix()
        if relative == values["sources"]["weapon"]:
            if file.stat().st_size > 16 * 1024**2:
                raise ValueError("Weapon metadata exceeds 16 MiB")
            with file.open("rb") as stream:
                data = stream.read(16 * 1024**2 + 1)
            if len(data) > 16 * 1024**2:
                raise ValueError("Weapon metadata grew beyond 16 MiB")
            tree = etree.ElementTree(etree.fromstring(data, safe_xml_parser()))
            for item in tree.iter():
                if any(etree.QName(n).localname == "Name" and (n.text or "").strip() == weapon for n in item if isinstance(n.tag, str)):
                    for spec in CAMERA_FIELDS.values():
                        for child in item:
                            if isinstance(child.tag, str) and etree.QName(child).localname == spec["tag"] and spec["attribute"] in child.attrib:
                                child.set(spec["attribute"], "CALIBRATION")
            files[relative] = hashlib.sha256(etree.tostring(tree, method="c14n")).hexdigest()
        else:
            hashed = hashlib.sha256(); size = 0
            with file.open("rb") as stream:
                while chunk := stream.read(1024**2):
                    size += len(chunk)
                    if size > before_stat.st_size:
                        raise ValueError("Package changed during calibration review")
                    hashed.update(chunk)
            files[relative] = hashed.hexdigest()
        after_stat = file.stat()
        if (before_stat.st_size, before_stat.st_mtime_ns) != (after_stat.st_size, after_stat.st_mtime_ns):
            raise ValueError("Package changed during calibration review")
    return digest(files)


def prepare(payload):
    ws = workspace(payload)
    if type(payload.get("expected_revision")) is not int or payload["expected_revision"] < 0:
        raise ValueError("A non-negative expected revision is required")
    ws._check_revision(payload.get("expected_revision"))
    source_state = ws.state_sha256()
    draft = payload.get("session")
    if not isinstance(draft, dict):
        raise ValueError("A session object is required")
    sid = identifier(draft.get("id"))
    if (store(ws) / sid).exists():
        raise ValueError("Session already exists; accepted and recorded sessions are never overwritten")
    weapon = payload.get("weapon")
    values = ws.values(weapon).to_dict()
    profile = draft.get("profile")
    if profile not in {"iron", "scope"}:
        raise ValueError("Choose independent iron or scope profile")
    edition = draft.get("edition")
    project_edition = ws.inspect().edition
    if edition not in {"Legacy", "Enhanced"} or (project_edition in {"Legacy", "Enhanced"} and edition != project_edition):
        raise ValueError("Choose the weapon package's game edition")
    created = draft.get("captured_at")
    if not isinstance(created, str) or len(created) > 40 or datetime.fromisoformat(created.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Capture time must include a timezone")
    conditions = draft.get("conditions")
    if not isinstance(conditions, dict) or set(conditions) != {"camera", "fov_degrees", "fov_axis", "distance_m", "pose", "attachments"}:
        raise ValueError("Record camera, FOV and its axis, distance, pose and equipped attachments")
    conditions = dict(conditions)
    for key in ("camera", "pose"):
        if not isinstance(conditions[key], str) or not 1 <= len(conditions[key].strip()) <= 160:
            raise ValueError(f"Record a bounded {key}")
    number(conditions["fov_degrees"], 1, 179, "FOV")
    number(conditions["distance_m"], .1, 2000, "Target distance")
    if conditions["fov_axis"] not in {"horizontal", "vertical", "unknown"}:
        raise ValueError("FOV axis must be horizontal, vertical or unknown")
    attachments = conditions["attachments"]
    if not isinstance(attachments, list) or len(attachments) > 32 or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", x) for x in attachments) or len(set(attachments)) != len(attachments):
        raise ValueError("Record at most 32 unique attachment identifiers")
    conditions["attachments"] = sorted(attachments)
    checks = draft.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS) or any(v not in {"pass", "fail", "not_tested"} for v in checks.values()):
        raise ValueError("Record every repeatable check as pass, fail or not_tested")
    accepted = draft.get("accepted", False)
    if type(accepted) is not bool or (accepted and any(v != "pass" for v in checks.values())):
        raise ValueError("Accepted references require all checks to pass")
    notes = draft.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError("Notes are limited to 2000 characters")
    picture, _ = image_evidence(draft.get("screenshot"))
    if draft.get("screenshot_sha256") != picture["sha256"]:
        raise ValueError("Screenshot differs from the marked preview; reopen and mark it again")
    measured = measurements(draft, picture)
    telemetry = draft.get("frame_times_ms", [])
    if not isinstance(telemetry, list) or len(telemetry) > 1000:
        raise ValueError("Use at most 1000 selected frame-time samples")
    for value in telemetry:
        number(value, .001, 60_000, "Frame time")
    provenance = None
    if payload.get("diagnostic"):
        from allin1_sdk.diagnostic_trail import inspect
        provenance = inspect(payload["diagnostic"])
        from allin1_sdk.diagnostic_trail import _read
        artifact, _ = _read(payload["diagnostic"]["source"])
        if artifact["edition"] and artifact["edition"] != edition:
            raise ValueError("Selected diagnostic artifact belongs to another edition")
    record = {"schema_version": 1, "kind": "weapon_calibration_session", "id": sid,
              "captured_at": created, "weapon": weapon, "edition": edition, "profile": profile,
              "conditions": conditions, "checks": checks, "accepted": accepted, "notes": notes,
              "screenshot": picture, "measurements": measured, "frame_times_ms": telemetry,
              "workspace_revision": ws.revision, "workspace_state_sha256": source_state,
              "values": values["values"], "package_invariant_sha256": package_invariant(ws, weapon, values),
              "diagnostic": provenance, "capture_source": "operator_imported",
              "runtime_asset_use": "not_proven", "workspace_to_installed_asset": "not_proven"}
    record["record_sha256"] = digest(record)
    if len(json.dumps(record).encode("utf-8")) > 1024**2:
        raise ValueError("Session evidence exceeds 1 MiB; narrow the selected diagnostic evidence")
    if ws.state_sha256() != source_state:
        raise ValueError("Workspace changed during calibration review")
    return record


def read(ws, sid):
    root = store(ws) / identifier(sid)
    if root.is_symlink() or root.resolve().parent != store(ws).resolve():
        raise ValueError("Invalid session directory")
    file = root / "session.json"
    if file.is_symlink() or file.stat().st_size > 2 * 1024**2:
        raise ValueError("Invalid session record")
    record = strict_json(file.read_bytes())
    verify_seal(record, "record_sha256")
    if record.get("schema_version") != 1 or record.get("kind") != "weapon_calibration_session" or record.get("id") != sid:
        raise ValueError("Unsupported session record")
    required = {"weapon", "edition", "profile", "conditions", "checks", "accepted", "screenshot", "measurements", "frame_times_ms", "values", "package_invariant_sha256", "workspace_state_sha256"}
    if not required <= record.keys() or record["profile"] not in {"iron", "scope"} or type(record["accepted"]) is not bool:
        raise ValueError("Malformed calibration record")
    picture, _ = image_evidence(str(root / "screenshot.png"))
    if picture != record["screenshot"]:
        raise ValueError("Session screenshot changed after recording")
    if measurements(record["measurements"], picture) != record["measurements"]:
        raise ValueError("Session measurements are inconsistent with their marks")
    return record


def save(payload, record):
    ws = workspace(payload)
    root = store(ws)
    root.mkdir(exist_ok=True)
    picture, data = image_evidence(payload["session"]["screenshot"])
    if picture != record["screenshot"] or ws.state_sha256() != record["workspace_state_sha256"]:
        raise ValueError("Screenshot or workspace changed during recording; review again")
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=root))
    try:
        (stage / "screenshot.png").write_bytes(data)
        (stage / "session.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        destination = root / identifier(record["id"])
        if destination.exists():
            raise ValueError("Session already exists; never overwrite a reference")
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {"kind": "weapon_calibration_saved", "session": record,
            "path": str(destination / "session.json"), "workspace_write_performed": True,
            "game_write_performed": False}


def compare(first, second):
    keys = ("weapon", "edition", "profile", "conditions")
    incompatible = [k for k in keys if first[k] != second[k]]
    if first["screenshot"]["width"] != second["screenshot"]["width"] or first["screenshot"]["height"] != second["screenshot"]["height"]:
        incompatible.append("resolution")
    if incompatible:
        raise ValueError("Sessions are not comparable: " + ", ".join(incompatible))
    a, b = first["measurements"], second["measurements"]
    return {"visual_error_change_px": [b["visual_error_px"][i] - a["visual_error_px"][i] for i in range(2)],
            "impact_centroid_change_px": [b["impact_centroid_error_px"][i] - a["impact_centroid_error_px"][i] for i in range(2)] if a["impact_count"] and b["impact_count"] else None,
            "frame_time_max_ms": [max(s["frame_times_ms"], default=None) for s in (first, second)],
            "checks": {key: [first["checks"][key], second["checks"][key]] for key in CHECKS},
            "scope": "Operator observations; correlated differences, not a causal verdict"}


def proposal(payload):
    ws = workspace(payload)
    first, second = (read(ws, payload.get(k)) for k in ("baseline", "trial"))
    compare(first, second)
    if first["id"] == second["id"] or payload.get("controlled_trial_confirmed") is not True:
        raise ValueError("Confirm two distinct controlled tests with only one field changed")
    if first["package_invariant_sha256"] != second["package_invariant_sha256"]:
        raise ValueError("Other package content changed; this is not a single-field trial")
    field = payload.get("field")
    tags = {"FirstPersonLTOffset", "FirstPersonLTRotationOffset"} if first["profile"] == "iron" else {
        "FirstPersonScopeOffset", "FirstPersonScopeRotationOffset", "FirstPersonScopeAttachmentOffset", "FirstPersonScopeAttachmentRotationOffset"}
    if field not in CAMERA_FIELDS or CAMERA_FIELDS[field]["tag"] not in tags:
        raise ValueError("Field does not belong to this independent sight profile")
    changed = {k for k in first["values"].keys() | second["values"].keys() if first["values"].get(k) != second["values"].get(k)}
    if changed != {field}:
        raise ValueError("Trial must differ in exactly the selected existing field")
    if second["accepted"]:
        raise ValueError("The current session is accepted; use a separate unaccepted trial before tuning")
    for record in (first, second):
        diagnostic = record.get("diagnostic")
        if diagnostic and (diagnostic.get("package_enabled") is not True or any(
            f.get("code") in {"different_artifact_receipted", "installed_file_missing", "installed_bytes_changed", "selected_build_mismatch", "installed_rpf_member_mismatch", "runtime_edition_mismatch", "session_installation_mismatch", "session_receipt_unlinked"}
            for f in diagnostic.get("findings", [])
        )):
            raise ValueError("Recorded installation evidence conflicts with the selected build; resolve it before proposing")
    if ws.state_sha256() != second["workspace_state_sha256"] or ws.values(second["weapon"]).to_dict()["values"] != second["values"]:
        raise ValueError("Workspace no longer matches the trial; record a new test")
    axis = payload.get("axis")
    if axis not in {"x", "y"}:
        raise ValueError("Choose the measured screen axis x or y")
    i = 0 if axis == "x" else 1
    error_a, error_b = (s["measurements"]["visual_error_px"][i] for s in (first, second))
    movement = error_b - error_a
    if abs(movement) < 2:
        raise ValueError("Response is under two pixels; too small for a bounded correction")
    delta = float(second["values"][field]) - float(first["values"][field])
    limit = .01 if CAMERA_FIELDS[field]["unit"] == "metres" else 1.0
    correction = -error_b * delta / movement
    if not math.isfinite(correction) or abs(correction) > min(abs(delta), limit) or abs(correction) < .000005:
        raise ValueError("Correction is zero or outside the measured step / 1 cm / 1 degree bound; take a smaller controlled trial")
    value = f"{float(second['values'][field]) + correction:.5f}"
    validate_advanced_value(field, value)
    rounded_delta = float(value) - float(second["values"][field])
    if rounded_delta == 0 or abs(rounded_delta) > min(abs(delta), limit) + 1e-12:
        raise ValueError("Rounded correction is zero or exceeds the bounded step")
    return {"kind": "weapon_calibration_proposal", "weapon": second["weapon"], "profile": second["profile"],
            "baseline_sha256": first["record_sha256"], "trial_sha256": second["record_sha256"],
            "updates": {field: value}, "before": second["values"][field], "after": value,
            "correction": correction, "unit": CAMERA_FIELDS[field]["unit"],
            "status": "empirical_visual_hypothesis_requires_retest", "impact_correction": "not_inferred",
            "conditions_source": "operator_reported", "installed_workspace_link": "not_proven"}


def inspect(payload):
    ws = workspace(payload)
    action = payload.get("calibration_action", "list")
    if action == "propose":
        return proposal(payload)
    if action == "compare":
        first, second = (read(ws, payload.get(k)) for k in ("baseline", "trial"))
        return {"kind": "weapon_calibration_comparison", "baseline": first, "trial": second,
                "comparison": compare(first, second)}
    if action != "list":
        raise ValueError("Calibration inspection supports list, compare or propose")
    root = store(ws)
    records = []
    if root.exists():
        entries = sorted(root.iterdir())
        if len(entries) > 256:
            raise ValueError("Calibration store exceeds 256 entries; archive older sessions outside this workspace")
        for entry in entries:
            if entry.name.startswith(".pending-"):
                continue
            record = read(ws, entry.name)
            if record["weapon"] == payload.get("weapon"):
                records.append({k: record[k] for k in ("id", "profile", "edition", "accepted", "captured_at", "record_sha256")})
    return {"kind": "weapon_calibration_sessions", "sessions": records, "read_only": True}
