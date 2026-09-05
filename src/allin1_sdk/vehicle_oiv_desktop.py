"""Digest-bound desktop review for the existing Legacy vehicle OIV exporter."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path

from allin1_sdk.paths import gta_root_containing
from allin1_sdk.vehicle_oiv_export import LegacyVehicleOivExporter, _validated_text


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _not_redirected(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("OIV source and destination paths must not use symbolic links or reparse points")


def _context(payload: dict):
    from allin1_sdk.desktop_protocol import _vehicle_quick_import_review_context

    allowed = {"source", "gta_path", "edition", "package_id", "name", "version",
               "author", "destination", "review_sha256", "authoring_confirmed"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("OIV export requires a bounded identity, author and destination; GBAY/traffic edits are not included")
    if payload.get("edition") != "legacy":
        raise ValueError("Select a verified Legacy branch; Enhanced OIV export is not supported")
    author = _validated_text(payload.get("author"), "Author")
    for key in ("source", "destination"):
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096 or "\0" in raw:
            raise ValueError(f"A bounded {key} path is required")
        _not_redirected(Path(raw).expanduser())
    source = Path(payload["source"]).expanduser().resolve(strict=True)
    raw_destination = Path(payload["destination"]).expanduser()
    if not raw_destination.is_absolute() or raw_destination.suffix.casefold() != ".oiv":
        raise ValueError("Choose an absolute .oiv destination")
    name = raw_destination.name
    if (len(name) > 160 or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
            or name != name.strip() or name.endswith('.') or not raw_destination.stem
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", raw_destination.stem)):
        raise ValueError("Choose a safe OIV filename")
    parent = raw_destination.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("OIV destination parent must be an existing directory")
    destination = parent / raw_destination.name
    if destination.exists() or destination.is_symlink():
        raise ValueError("OIV destination already exists; choose a new filename")
    if gta_root_containing(destination) or (source.is_dir() and destination.is_relative_to(source)):
        raise ValueError("Export outside GTA V and outside the source package")
    service, review, _unused, _preview = _vehicle_quick_import_review_context(
        {key: value for key, value in payload.items() if key in {"source", "gta_path", "edition", "package_id", "name", "version"}},
        risk="read_only", include_destination=False,
    )
    if destination.is_relative_to(service.gta_path):
        raise ValueError("OIV export destination must be outside the selected GTA directory")
    plan = review.plan
    if plan.edition.casefold() != "legacy":
        raise ValueError("The reviewed payload is not a Legacy vehicle branch")
    result = {
        "kind": "vehicle_oiv_export_review", "edition": "legacy", "source": str(source),
        "destination": str(destination), "author": author,
        "package_id": plan.package_id, "name": plan.name, "version": plan.version,
        "dlc_pack": plan.dlc_pack, "vehicles": list(plan.vehicles),
        "payload_member": plan.source_member, "payload_size": plan.source_member_size,
        "payload_sha256": plan.source_member_sha256,
        "plan_sha256": _digest(plan.to_dict()),
        "members": ["assembly.xml", f"content/dlcpacks/{plan.dlc_pack}/dlc.rpf"],
        "excluded": ["GBAY catalog", "traffic preferences", "ALLIN1 receipt", "managed backups and rollback"],
        "review_only": True, "game_write_performed": False, "file_write_performed": False,
    }
    result["review_sha256"] = _digest(result)
    return result, plan, LegacyVehicleOivExporter(service.gta_path)


def review(payload: dict) -> dict:
    result, _plan, _exporter = _context(payload)
    from allin1_sdk.desktop_protocol import _bounded
    if _bounded(result) != result:
        raise ValueError("OIV review exceeds desktop evidence limits")
    return result


def apply(payload: dict) -> dict:
    expected = payload.get("review_sha256") if isinstance(payload, dict) else None
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or payload.get("authoring_confirmed") is not True:
        raise ValueError("OIV export requires its reviewed digest and explicit action-time confirmation")
    current, plan, exporter = _context(payload)
    if current["review_sha256"] != expected:
        raise ValueError("OIV source, identity, author or destination changed after review; review again")
    # The domain rechecks the source archive/member hash again while reading
    # its exact payload and claims the final filename exclusively.
    result = exporter.export_plan(plan, current["destination"], author=current["author"]).to_dict()
    result.update({"kind": "vehicle_oiv_exported", "review_sha256": expected})
    return result
