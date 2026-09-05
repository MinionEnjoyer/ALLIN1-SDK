"""Release evidence contracts. Integrity, automation and live acceptance are distinct.

A caller must supply an independently reviewed session anchor. This module never
creates that anchor, infers live success from PE headers, or parses display text.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from allin1_sdk.release_paths import contained, strict_json, unique_paths

SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_AGE = timedelta(days=7)
CHECKS = {
    "sdk-desktop": frozenset({"clean_install", "upgrade", "repair", "uninstall", "rollback",
        "missing_dependencies", "space_paths", "long_paths", "user_data_preservation",
        "ped_authoring", "native_preview", "texture_preview", "cancel_close"}),
    # Reactor is an in-game integration, not the SDK's React/native preview.
    "reactor-story": frozenset({"reactor_loaded", "renderer_initialized", "frame_presented",
        "resize_recovery", "device_recovery", "shutdown", "online_guard"}),
}


def _shape(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"Incomplete or unsupported {label} schema")


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Evidence timestamp must be an ISO-8601 string")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid evidence timestamp") from exc
    if stamp.tzinfo is None:
        raise ValueError("Evidence timestamp must include timezone")
    return stamp


def _hashes(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Missing {label} identities")
    unique_paths(list(value))
    if any(not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest) for digest in value.values()):
        raise ValueError(f"Invalid {label} hash")


def validate_identity(identity):
    _shape(identity, {"sdk_commit", "build_id", "source_tree_sha256", "sdk_version", "artifacts",
                      "dependencies", "schema_versions"}, "build identity")
    if not isinstance(identity["sdk_commit"], str) or not re.fullmatch("[a-f0-9]{40}", identity["sdk_commit"]):
        raise ValueError("Exact SDK commit required")
    if not isinstance(identity["build_id"], str) or not identity["build_id"] or not identity["sdk_version"]:
        raise ValueError("Missing SDK build/version")
    _hashes({"source": identity["source_tree_sha256"]}, "source")
    _hashes(identity["artifacts"], "artifact")
    _hashes(identity["dependencies"], "dependency")
    schemas = identity["schema_versions"]
    if (not isinstance(schemas, dict) or not schemas or
            any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in schemas.items())):
        raise ValueError("Exact schema version identities required")


def verify_identity_files(identity, artifact_root: Path, dependency_root: Path):
    validate_identity(identity)
    for key, root in (("artifacts", artifact_root), ("dependencies", dependency_root)):
        for name, expected in identity[key].items():
            path = contained(root, name)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Actual {key} bytes do not match the tested build: {name}")


def validate_live_acceptance(report: dict, *, expected_identity: dict, trusted_session: dict,
                             evidence_root: Path, artifact_root: Path, dependency_root: Path,
                             target_edition: str, suite: str, now: datetime | None = None) -> dict:
    """Validate a complete report against bytes AND an independently pinned session.

    ``trusted_session`` must come from the release authority, not the report or
    its logs. Trusting an anchor supplied by the report defeats this boundary.
    """
    _shape(report, {"schema_version", "kind", "suite", "session_id", "target_edition", "identity",
                    "started_at", "ended_at", "checks", "events_path", "events_sha256", "synthetic"}, "acceptance report")
    _shape(trusted_session, {"schema_version", "session_id", "suite", "target_edition", "identity",
                            "started_at", "ended_at", "events_sha256", "authority"}, "session anchor")
    if type(report["schema_version"]) is not int or report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported acceptance schema")
    if type(trusted_session["schema_version"]) is not int or trusted_session["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported session schema")
    if report["kind"] != "live_acceptance" or report["synthetic"] is not False:
        raise ValueError("Synthetic/automated evidence cannot qualify live acceptance")
    if suite not in CHECKS or target_edition not in {"Legacy", "Enhanced"}:
        raise ValueError("Unsupported acceptance suite or edition")
    if not isinstance(trusted_session["authority"], str) or not trusted_session["authority"].strip():
        raise ValueError("Independent session authority required")
    if not isinstance(report["session_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,96}", report["session_id"]):
        raise ValueError("Bounded unique session identity required")
    for key in ("session_id", "suite", "target_edition", "identity", "started_at", "ended_at", "events_sha256"):
        if report[key] != trusted_session[key]:
            raise ValueError(f"Report does not match independent session: {key}")
    if report["identity"] != expected_identity or report["target_edition"] != target_edition or report["suite"] != suite:
        raise ValueError("Acceptance evidence is from another build, edition or suite")
    verify_identity_files(expected_identity, artifact_root, dependency_root)
    now = now or datetime.now(timezone.utc)
    start, end = _time(report["started_at"]), _time(report["ended_at"])
    if now.tzinfo is None or not start <= end <= now or now - start > MAX_AGE:
        raise ValueError("Stale, future-dated or reversed acceptance session")
    checks = report["checks"]
    _shape(checks, CHECKS[suite], "acceptance-check")
    if any(value != "PASS" for value in checks.values()):
        raise ValueError("Missing, failed or skipped live acceptance checks")
    event_path = contained(evidence_root, report["events_path"])
    if event_path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ValueError("Acceptance evidence exceeds size limit")
    raw = event_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != report["events_sha256"]:
        raise ValueError("Acceptance event evidence hash mismatch")
    events = [strict_json(line) for line in raw.splitlines() if line.strip()]
    if len(events) != len(CHECKS[suite]) + 2:
        raise ValueError("Incomplete acceptance session events")
    observed = set()
    previous = start
    for sequence, event in enumerate(events):
        _shape(event, {"schema_version", "sequence", "session_id", "timestamp", "type", "identity",
                       "target_edition", "check", "status", "evidence"}, "acceptance event")
        if (type(event["schema_version"]) is not int or event["schema_version"] != EVENT_SCHEMA_VERSION
                or type(event["sequence"]) is not int or event["sequence"] != sequence
                or event["session_id"] != report["session_id"] or event["identity"] != expected_identity
                or event["target_edition"] != target_edition):
            raise ValueError("Unrelated, duplicated or unordered acceptance event")
        stamp = _time(event["timestamp"])
        if not previous <= stamp <= end:
            raise ValueError("Acceptance event is outside its session")
        previous = stamp
        if sequence in (0, len(events) - 1):
            expected_type = "session_start" if sequence == 0 else "session_end"
            if (event["type"] != expected_type or event["check"] is not None or event["status"] != "PASS"
                    or event["evidence"] != {} or stamp != (start if sequence == 0 else end)):
                raise ValueError("Missing/invalid session boundary event")
            continue
        check = event["check"]
        if event["type"] != "acceptance_check" or check not in CHECKS[suite] or check in observed or event["status"] != "PASS":
            raise ValueError("Missing/failed/duplicate acceptance event")
        observed.add(check)
        _hashes(event["evidence"], "per-check evidence")
        for name, digest in event["evidence"].items():
            proof = contained(evidence_root, name)
            if not proof.is_file() or hashlib.sha256(proof.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Missing or changed check evidence: {check}")
    return {"schema_version": 1, "live_acceptance": "PASS", "session_id": report["session_id"],
            "target_edition": target_edition, "identity": expected_identity,
            "package_integrity": "NOT TESTED", "automated_tests": "NOT TESTED"}
