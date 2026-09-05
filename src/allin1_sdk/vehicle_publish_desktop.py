"""Reviewed desktop publication of an already prepared vehicle package."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from allin1_sdk.managed_package_conversion import ManagedVehiclePackageConverter, _safe_publication_path
from allin1_sdk.paths import gta_root_containing, project_root


def _context(payload: object):
    allowed = {"source_package", "destination", "gta_path", "review_sha256", "authoring_confirmed"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("ZIP publication requires a prepared package and destination, not unsaved draft edits")
    for field in ("source_package", "destination"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\0" in value:
            raise ValueError(f"A bounded {field} path is required")
        _safe_publication_path(Path(value).expanduser())
    source = Path(payload["source_package"]).expanduser().resolve(strict=True)
    raw_output = Path(payload["destination"]).expanduser()
    name = raw_output.name
    if (not raw_output.is_absolute() or raw_output.suffix.casefold() != ".zip"
            or len(name) > 160 or name != name.strip() or not raw_output.stem
            or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", raw_output.stem)):
        raise ValueError("Choose an absolute destination with a safe .zip filename")
    parent = raw_output.parent.resolve(strict=True)
    if not parent.is_dir(): raise ValueError("ZIP destination parent must be an existing directory")
    destination = parent / name
    if destination.exists() or destination.is_symlink(): raise ValueError("ZIP destination already exists; choose a new filename")
    if destination.is_relative_to(source) or gta_root_containing(destination):
        raise ValueError("Export ZIP files outside the source package and GTA V")
    raw_game = payload.get("gta_path")
    if raw_game is None:
        from allin1_sdk.detector import detect_gta_path
        game = detect_gta_path()
    else:
        if not isinstance(raw_game, str) or not raw_game.strip() or len(raw_game) > 4096 or "\0" in raw_game:
            raise ValueError("gta_path must be a bounded directory path")
        game = Path(raw_game).expanduser().resolve(strict=True)
    if game is None or not game.is_dir(): raise ValueError("Select the GTA installation used for Quick Import")
    if destination.is_relative_to(game): raise ValueError("ZIP destination must be outside the selected GTA installation")
    converter = ManagedVehiclePackageConverter(project_root(), game)
    publication = converter.review_publication(source)
    value = {**publication, "kind": "vehicle_package_publish_review", "destination": str(destination),
             "gta_path": str(game), "review_only": True, "file_write_performed": False,
             "game_write_performed": False, "upload_performed": False}
    from allin1_sdk.desktop_protocol import _bounded
    if _bounded(value) != value: raise ValueError("Publication review exceeds desktop evidence limits")
    value["review_sha256"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return converter, publication, value


def review(payload: object) -> dict:
    return _context(payload)[2]


def apply(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("authoring_confirmed") is not True:
        raise ValueError("ZIP publication requires explicit action-time confirmation")
    expected = payload.get("review_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("ZIP publication requires a reviewed digest")
    converter, publication, current = _context(payload)
    if current["review_sha256"] != expected:
        raise ValueError("Prepared package or output changed after review; review ZIP publication again")
    result = converter.publish(current["source_package"], current["destination"], expected_review=publication).to_dict()
    return {**result, "kind": "vehicle_package_published", "review_sha256": expected,
            "review_only": False, "file_write_performed": True, "game_write_performed": False, "upload_performed": False}
