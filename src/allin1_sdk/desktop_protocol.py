"""Versioned JSONL protocol for the Tauri desktop sidecar.

The protocol is intentionally adjacent to, rather than a replacement for, the
public Agent API. Desktop command execution delegates to ``execute_request`` so
risk classifications and all CLI safety checks remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable

from allin1_sdk import __version__
from allin1_sdk.release_identity import embedded_build_identity
from allin1_sdk.agent_api import (
    MAX_OUTPUT_CHARS,
    MAX_REQUEST_BYTES,
    UnclassifiedCommandError,
    _audit,
    command_catalog,
    effective_command_risk,
    execute_request,
)


PROTOCOL_VERSION = "1.0.0"
SUPPORTED_VERSIONS = (PROTOCOL_VERSION,)
OPERATIONS = frozenset({
    "inspect_ped_ymt",
    "inspect_ped_workbench", "review_ped_authoring", "apply_ped_authoring",
    "list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction", "apply_rpf_transaction",
    "inspect_rpf_change_set", "review_rpf_change_set", "apply_rpf_change_set",
    "inspect_authoring_workspace", "review_workspace_action", "apply_workspace_action",
    "inspect_gxt2_workspace", "review_gxt2_action", "apply_gxt2_action",
    "handshake", "catalog", "execute", "inspect_package", "preview_asset",
    "render_vehicle_model", "inspect_model_materials",
    "inspect_model_material_workspace", "review_model_material_workspace",
    "create_model_material_workspace", "review_model_material_edit",
    "apply_model_material_edit", "apply_model_material_history",
    "review_model_material_build", "apply_model_material_build",
    "inspect_texture_workspace", "review_texture_workspace",
    "create_texture_workspace", "preview_texture_workspace",
    "review_texture_edit", "apply_texture_edit", "apply_texture_history",
    "review_texture_build", "apply_texture_build",
    "assistant_status", "assistant_prompt", "configure_assistant",
    "inspect_weapon_workbench", "review_weapon_authoring", "apply_weapon_authoring",
    "inspect_rpf_archive", "review_rpf_utility", "apply_rpf_utility",
    "inspect_vehicle_project",
    "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace",
    "create_vehicle_authoring_workspace", "review_vehicle_authoring_edit",
    "apply_vehicle_authoring_edit", "review_vehicle_authoring_appearance",
    "apply_vehicle_authoring_appearance", "apply_vehicle_authoring_history",
    "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning",
    "apply_vehicle_authoring_tuning", "review_vehicle_authoring_light_profile",
    "apply_vehicle_authoring_light_profile", "review_vehicle_authoring_axles",
    "apply_vehicle_authoring_axles", "inspect_vehicle_authoring_axle_skeleton",
    "review_vehicle_authoring_transmission",
    "apply_vehicle_authoring_transmission",
    "review_vehicle_authoring_distribution",
    "apply_vehicle_authoring_distribution",
    "review_vehicle_package_build", "apply_vehicle_package_build",
    "inspect_recipe", "inspect_package_receipts", "review_package_lifecycle",
    "inspect_vehicle_quick_import",
    "review_vehicle_quick_import", "prepare_vehicle_quick_import",
    "review_vehicle_oiv_export", "apply_vehicle_oiv_export",
    "review_vehicle_package_publish", "apply_vehicle_package_publish",
    "apply_package_lifecycle", "check_update",
    "start_job", "cancel_job", "job_event", "result", "error", "shutdown",
})
CLIENT_OPERATIONS = frozenset({
    "inspect_ped_ymt",
    "inspect_ped_workbench", "review_ped_authoring", "apply_ped_authoring",
    "list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction", "apply_rpf_transaction",
    "inspect_rpf_change_set", "review_rpf_change_set", "apply_rpf_change_set",
    "inspect_authoring_workspace", "review_workspace_action", "apply_workspace_action",
    "inspect_gxt2_workspace", "review_gxt2_action", "apply_gxt2_action",
    "handshake", "catalog", "execute", "inspect_package", "preview_asset",
    "render_vehicle_model", "inspect_model_materials",
    "inspect_model_material_workspace", "review_model_material_workspace",
    "create_model_material_workspace", "review_model_material_edit",
    "apply_model_material_edit", "apply_model_material_history",
    "review_model_material_build", "apply_model_material_build",
    "inspect_texture_workspace", "review_texture_workspace",
    "create_texture_workspace", "preview_texture_workspace",
    "review_texture_edit", "apply_texture_edit", "apply_texture_history",
    "review_texture_build", "apply_texture_build",
    "assistant_status", "assistant_prompt", "configure_assistant",
    "inspect_weapon_workbench", "review_weapon_authoring", "apply_weapon_authoring",
    "inspect_rpf_archive", "review_rpf_utility", "apply_rpf_utility",
    "inspect_vehicle_project",
    "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace",
    "create_vehicle_authoring_workspace", "review_vehicle_authoring_edit",
    "apply_vehicle_authoring_edit", "review_vehicle_authoring_appearance",
    "apply_vehicle_authoring_appearance", "apply_vehicle_authoring_history",
    "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning",
    "apply_vehicle_authoring_tuning", "review_vehicle_authoring_light_profile",
    "apply_vehicle_authoring_light_profile", "review_vehicle_authoring_axles",
    "apply_vehicle_authoring_axles", "inspect_vehicle_authoring_axle_skeleton",
    "review_vehicle_authoring_transmission",
    "apply_vehicle_authoring_transmission",
    "review_vehicle_authoring_distribution",
    "apply_vehicle_authoring_distribution",
    "review_vehicle_package_build", "apply_vehicle_package_build",
    "inspect_recipe", "inspect_package_receipts", "review_package_lifecycle",
    "inspect_vehicle_quick_import",
    "review_vehicle_quick_import", "prepare_vehicle_quick_import",
    "review_vehicle_oiv_export", "apply_vehicle_oiv_export",
    "review_vehicle_package_publish", "apply_vehicle_package_publish",
    "apply_package_lifecycle", "check_update",
    "start_job", "cancel_job", "shutdown",
})
JOB_OPERATIONS = frozenset({
    "inspect_ped_ymt",
    "inspect_ped_workbench", "review_ped_authoring",
    "list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction",
    "inspect_rpf_change_set", "review_rpf_change_set",
    "inspect_authoring_workspace", "review_workspace_action",
    "inspect_gxt2_workspace", "review_gxt2_action",
    "execute", "inspect_package", "preview_asset", "inspect_recipe",
    "inspect_model_materials", "inspect_model_material_workspace",
    "review_model_material_workspace", "review_model_material_edit",
    "review_model_material_build",
    "inspect_texture_workspace", "review_texture_workspace",
    "preview_texture_workspace", "review_texture_edit", "review_texture_build",
    "assistant_status", "assistant_prompt",
    "inspect_weapon_workbench", "review_weapon_authoring",
    "inspect_rpf_archive", "review_rpf_utility", "inspect_vehicle_project",
    "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace",
    "review_vehicle_authoring_edit", "review_vehicle_authoring_appearance",
    "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning",
    "review_vehicle_authoring_light_profile", "review_vehicle_authoring_axles",
    "inspect_vehicle_authoring_axle_skeleton",
    "review_vehicle_authoring_transmission",
    "review_vehicle_authoring_distribution", "review_vehicle_package_build",
    "inspect_package_receipts", "review_package_lifecycle",
    "inspect_vehicle_quick_import", "review_vehicle_quick_import",
    "review_vehicle_oiv_export",
    "review_vehicle_package_publish",
    "check_update",
})
RISKS = frozenset({
    "none", "read_only", "authoring_write", "game_write", "unclassified",
})
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_ENTRIES = 2_000
_MAX_FINDINGS = 500
_MAX_STRING = 32_768

NAVIGATION = (
    {"id": "linker", "label": "Package Linker", "shortcut": "Ctrl+1", "phase": 3},
    {"id": "assets", "label": "Asset Viewer", "shortcut": "Ctrl+2", "phase": 4},
    {"id": "workbench", "label": "Content Workbench", "shortcut": "Ctrl+3", "phase": 3},
    {"id": "receipts", "label": "Package Receipts", "shortcut": "Ctrl+8", "phase": 4},
    {"id": "quick_import", "label": "Quick Import", "shortcut": "Ctrl+I", "phase": 4},
    {"id": "models", "label": "Models & Materials", "shortcut": "Ctrl+4", "phase": 5},
    {"id": "rpf", "label": "RPF Archives", "shortcut": "Ctrl+5", "phase": 3},
    {"id": "recipes", "label": "Package Recipes", "shortcut": "Ctrl+6", "phase": 4},
    {"id": "data_tools", "label": "Data Tools", "shortcut": "Ctrl+9", "phase": 5},
    {"id": "help", "label": "Help Center", "shortcut": "Ctrl+7", "phase": 3},
)


class ProtocolError(ValueError):
    """A client envelope or operation failed validation."""

    def __init__(
        self, message: str, *, risk: str = "none", details: object = None,
    ) -> None:
        super().__init__(message)
        self.risk = risk if risk in RISKS else "unclassified"
        self.details = details


@dataclass
class _Job:
    job_id: str
    request_id: str
    revision: str | None
    risk: str
    process: subprocess.Popen[str]
    worker_request: dict[str, Any]
    cancelled: bool = False


def envelope(
    operation: str, payload: dict[str, Any], *, request_id: str | None,
    job_id: str | None = None, sequence: int = 0, risk: str = "none",
    terminal: bool,
) -> dict[str, Any]:
    """Build one complete protocol envelope."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "job_id": job_id,
        "operation": operation,
        "payload": payload,
        "sequence": sequence,
        "risk": risk,
        "terminal": terminal,
    }


def _bounded(value: object, *, depth: int = 0) -> object:
    """Keep package-controlled report values JSON-safe and bounded."""
    if depth > 8:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_STRING]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key)[:256]: _bounded(item, depth=depth + 1)
            for key, item in list(value.items())[:500]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:2_000]]
    return str(value)[:_MAX_STRING]


def _validate_command_payload(payload: object) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        raise ProtocolError("execute payload must be an object")
    command = payload.get("command")
    arguments = payload.get("args", [])
    if not isinstance(command, str) or not command.strip():
        raise ProtocolError("command must be a non-empty string")
    command = command.strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", command):
        raise ProtocolError("command contains unsupported characters")
    if (
        not isinstance(arguments, list)
        or len(arguments) > 128
        or any(not isinstance(item, str) or "\0" in item for item in arguments)
    ):
        raise ProtocolError(
            "args must be a list of at most 128 strings without NUL bytes"
        )
    return command, list(arguments)


def _operation_risk(operation: str, payload: object) -> str:
    if operation in {
        "list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction",
        "inspect_rpf_change_set", "review_rpf_change_set",
        "inspect_authoring_workspace", "review_workspace_action",
        "inspect_gxt2_workspace", "review_gxt2_action",
        "inspect_package", "preview_asset", "inspect_recipe",
        "inspect_model_materials", "inspect_model_material_workspace",
        "review_model_material_workspace", "review_model_material_edit",
        "review_model_material_build",
        "inspect_texture_workspace", "review_texture_workspace",
        "preview_texture_workspace", "review_texture_edit", "review_texture_build",
        "assistant_status", "assistant_prompt",
        "inspect_weapon_workbench", "review_weapon_authoring",
        "inspect_ped_ymt",
        "inspect_ped_workbench", "review_ped_authoring",
        "inspect_rpf_archive", "review_rpf_utility", "inspect_vehicle_project",
        "inspect_vehicle_authoring_workspace", "review_vehicle_authoring_workspace",
        "review_vehicle_authoring_edit", "review_vehicle_authoring_appearance",
        "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning",
        "review_vehicle_authoring_light_profile", "review_vehicle_authoring_axles",
        "inspect_vehicle_authoring_axle_skeleton",
        "review_vehicle_authoring_transmission",
        "review_vehicle_authoring_distribution", "review_vehicle_package_build",
        "inspect_package_receipts", "review_package_lifecycle",
        "inspect_vehicle_quick_import", "review_vehicle_quick_import",
        "review_vehicle_oiv_export",
        "review_vehicle_package_publish",
        "check_update",
    }:
        return "read_only"
    if operation != "execute":
        raise ProtocolError(f"operation cannot run as a job: {operation}")
    command, arguments = _validate_command_payload(payload)
    try:
        return effective_command_risk(command, arguments)
    except UnclassifiedCommandError as exc:
        raise ProtocolError(str(exc), risk="unclassified") from exc


def _execute_command(
    payload: object, *, allow_game_writes: bool, audit_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    command, arguments = _validate_command_payload(payload)
    try:
        risk = effective_command_risk(command, arguments)
    except UnclassifiedCommandError as exc:
        raise ProtocolError(str(exc), risk="unclassified") from exc
    if risk == "authoring_write" and (
        not isinstance(payload, dict)
        or payload.get("authoring_confirmed") is not True
    ):
        raise ProtocolError(
            "Authoring commands require an explicit action-time confirmation.",
            risk=risk,
        )
    response = execute_request({
        "id": "desktop", "action": "execute", "command": command,
        "args": arguments,
    }, allow_game_writes=allow_game_writes, audit_path=audit_path)
    if not response.get("ok"):
        message = str(response.get("error") or "command failed")
        result = response.get("result")
        if isinstance(result, dict):
            output = str(result.get("output", "")).strip()
            if output:
                message = output
        raise ProtocolError(message, risk=risk, details=_bounded(response))
    result = response.get("result")
    if not isinstance(result, dict):
        raise ProtocolError("Agent API returned an invalid result", risk=risk)
    return risk, dict(_bounded(result))


def _manifest_summary(source: Path) -> dict[str, Any]:
    from allin1_sdk.addon_sdk import AddonLinker, AddonManifest

    manifest = AddonManifest.load(source)
    report = AddonLinker().link(manifest)
    return {
        "kind": "manifest",
        "source": str(manifest.manifest_path),
        "source_root": str(manifest.source_root),
        "id": manifest.addon_id,
        "name": manifest.name,
        "version": manifest.version,
        "summary": manifest.summary,
        "editions": list(manifest.editions),
        "valid": report.valid,
        "error_count": report.error_count,
        "warning_count": report.warning_count,
        "nodes": [{
            "id": item.node_id,
            "kind": item.kind,
            "label": item.label,
            "description": item.description,
            "source": item.source,
            "fields": _bounded(dict(item.fields)),
        } for item in manifest.nodes[:500]],
        "references": [{
            "id": item.reference.reference_id,
            "source": item.reference.source,
            "source_field": item.reference.source_field,
            "target": item.reference.target,
            "target_field": item.reference.target_field,
            "relationship": item.reference.relationship,
            "required": item.reference.required,
            "valid": item.valid,
            "message": item.message,
        } for item in report.references[:1_000]],
        "issues": [asdict(item) for item in report.issues[:_MAX_FINDINGS]],
        "install_steps": [asdict(item) for item in manifest.install_steps[:500]],
        "truncated": (
            len(manifest.nodes) > 500
            or len(report.references) > 1_000
            or len(report.issues) > _MAX_FINDINGS
            or len(manifest.install_steps) > 500
        ),
    }


def _scan_summary(source: Path, gta_path: Path | None) -> dict[str, Any]:
    from allin1_sdk.addon_importer import AddonPackageInspector
    from allin1_sdk.paths import project_root

    scan = AddonPackageInspector(project_root(), gta_path).inspect(source)
    counts = {
        "weapons": len(scan.weapons),
        "weapon_components": len(scan.weapon_components),
        "vehicles": len(scan.vehicles),
        "peds": len(scan.peds),
        "rpf_archives": len(scan.rpf_archives),
        "native_assets": len(scan.rpf_native_assets),
        "plugins": len(scan.plugin_details),
    }
    inventory = scan.workbench_entries
    return {
        "kind": "package_scan",
        "source": str(scan.source),
        "source_kind": scan.source_kind,
        "valid": scan.valid,
        "error_count": scan.error_count,
        "warning_count": scan.warning_count,
        "file_count": len(scan.entries),
        "inventory_count": len(inventory),
        "total_bytes": scan.total_bytes,
        "edition": scan.edition_tag,
        "package_kinds": list(scan.package_kinds),
        "installation_targets": list(scan.installation_targets),
        "dependency_hints": list(scan.dependency_hints),
        "counts": counts,
        "findings": [asdict(item) for item in scan.findings[:_MAX_FINDINGS]],
        "entries": [{
            "path": item.path,
            "size": item.size,
            "category": item.category,
            "preview_kind": item.preview_kind,
        } for item in inventory[:_MAX_ENTRIES]],
        "truncated": (
            len(scan.findings) > _MAX_FINDINGS
            or len(inventory) > _MAX_ENTRIES
        ),
    }


def _inspect_package(payload: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_package payload must be an object")
    raw_source = payload.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
        raise ProtocolError("inspect_package requires a source path")
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"package source was not found: {exc}", risk="read_only") from exc
    raw_game = payload.get("gta_path")
    gta_path: Path | None = None
    if raw_game is not None:
        if not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game:
            raise ProtocolError("gta_path must be a valid path string", risk="read_only")
        try:
            gta_path = Path(raw_game).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"GTA path was not found: {exc}", risk="read_only") from exc
        if not gta_path.is_dir():
            raise ProtocolError("GTA path must be a directory", risk="read_only")
    if source.is_file() and source.name.casefold() in {
        "addon.json", "allin1.workspace.json",
    }:
        result = _manifest_summary(source)
    else:
        result = _scan_summary(source, gta_path)
    return "read_only", dict(_bounded(result))


def _inspect_ped_ymt(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "read_only"
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_ped_ymt payload must be an object", risk=risk)
    raw_source = payload.get("source")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source or len(raw_source) > _MAX_STRING
    ):
        raise ProtocolError("inspect_ped_ymt requires a source path", risk=risk)
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"YMT source was not found: {exc}", risk=risk) from exc
    raw_edition = payload.get("edition")
    if not isinstance(raw_edition, str) or raw_edition.casefold() not in {
        "legacy", "enhanced",
    }:
        raise ProtocolError(
            "inspect_ped_ymt edition must be Legacy or Enhanced", risk=risk,
        )
    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str) or not raw_game.strip()
        or "\0" in raw_game or len(raw_game) > _MAX_STRING
    ):
        raise ProtocolError("gta_path must be a valid path string", risk=risk)
    try:
        gta_path = (
            Path(raw_game).expanduser().resolve(strict=True)
            if isinstance(raw_game, str) else None
        )
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk=risk)
    from allin1_sdk.paths import project_root
    from allin1_sdk.ped_ymt_inspector import PedYmtInspector

    try:
        result = PedYmtInspector(project_root(), gta_path).inspect(
            source, edition=raw_edition,
        ).to_dict()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    bounded = _bounded(result)
    if bounded != result:
        raise ProtocolError(
            "Ped YMT report exceeds desktop evidence limits", risk=risk,
        )
    return risk, dict(bounded)


def _preview_asset(payload: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError("preview_asset payload must be an object")
    raw_source = payload.get("source")
    entry = payload.get("entry")
    if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
        raise ProtocolError("preview_asset requires a source path")
    if (
        not isinstance(entry, str) or not entry.strip() or "\0" in entry
        or len(entry) > _MAX_STRING
    ):
        raise ProtocolError("preview_asset requires a bounded package entry")
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"package source was not found: {exc}", risk="read_only",
        ) from exc
    raw_game = payload.get("gta_path")
    gta_path: Path | None = None
    if raw_game is not None:
        if not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game:
            raise ProtocolError("gta_path must be a valid path string", risk="read_only")
        try:
            gta_path = Path(raw_game).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"GTA path was not found: {exc}", risk="read_only") from exc
        if not gta_path.is_dir():
            raise ProtocolError("GTA path must be a directory", risk="read_only")
    raw_edition = payload.get("edition", "Enhanced")
    if not isinstance(raw_edition, str) or raw_edition.casefold() not in {
        "legacy", "enhanced",
    }:
        raise ProtocolError("edition must be Legacy or Enhanced", risk="read_only")
    from allin1_sdk.asset_preview import AssetPreviewService
    from allin1_sdk.paths import project_root

    try:
        result = AssetPreviewService(
            project_root(), gta_path=gta_path,
        ).preview(source, entry, edition=raw_edition)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    return "read_only", dict(_bounded(result))


def _render_vehicle_model(
    payload: object, *, renderer: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render one validated native model frame for the React viewport."""
    risk = "read_only"
    if not isinstance(payload, dict):
        raise ProtocolError("render_vehicle_model payload must be an object", risk=risk)
    raw_source = payload.get("source")
    entry = payload.get("entry")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError("vehicle viewport requires a source path", risk=risk)
    if (
        not isinstance(entry, str) or not entry.strip() or "\0" in entry
        or len(entry) > _MAX_STRING
    ):
        raise ProtocolError("vehicle viewport requires a bounded model entry", risk=risk)
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle viewport source was not found: {exc}", risk=risk,
        ) from exc
    raw_game = payload.get("gta_path")
    gta_path: Path | None = None
    if raw_game is not None:
        if not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game:
            raise ProtocolError("gta_path must be a valid path string", risk=risk)
        try:
            gta_path = Path(raw_game).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
        if not gta_path.is_dir():
            raise ProtocolError("GTA path must be a directory", risk=risk)
    edition = payload.get("edition", "Enhanced")
    if not isinstance(edition, str) or edition.casefold() not in {
        "legacy", "enhanced",
    }:
        raise ProtocolError("edition must be Legacy or Enhanced", risk=risk)
    from allin1_sdk.paths import project_root
    from allin1_sdk.vehicle_viewport import VehicleViewportRenderer

    viewport = renderer or VehicleViewportRenderer(project_root())
    try:
        result = viewport.render(
            source,
            entry,
            edition=edition,
            gta_path=gta_path,
            yaw=payload.get("yaw", 34.0),
            pitch=payload.get("pitch", 24.0),
            lod=payload.get("lod"),
            component=payload.get("component"),
            material=payload.get("material"),
            texture_entry=payload.get("texture_entry"),
            collision_entry=payload.get("collision_entry"),
            collision_visible=payload.get("collision_visible", False),
            render_mode=payload.get("render_mode", "shaded"),
            quality=payload.get("quality", "final"),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    return risk, dict(_bounded(result))


def _inspect_model_materials(payload: object) -> tuple[str, dict[str, Any]]:
    """Inspect one loose native model and return a bounded material project."""
    risk = "read_only"
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_model_materials payload must be an object", risk=risk)
    raw_source = payload.get("source")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError("model material inspection requires a source path", risk=risk)
    authored = Path(raw_source).expanduser()
    if authored.is_symlink():
        raise ProtocolError("model material source cannot be a symbolic link", risk=risk)
    try:
        source = authored.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"model material source was not found: {exc}", risk=risk) from exc

    from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES, MODEL_PREVIEW_SUFFIXES

    if not source.is_file() or source.suffix.casefold() not in MODEL_PREVIEW_SUFFIXES:
        raise ProtocolError(
            "model material inspection requires a loose YDR, YDD, or YFT asset",
            risk=risk,
        )
    if source.stat().st_size > MAX_NATIVE_PREVIEW_BYTES:
        raise ProtocolError("model material source exceeds the native preview limit", risk=risk)

    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game
    ):
        raise ProtocolError("gta_path must be a valid path string", risk=risk)
    try:
        gta_path = (
            Path(raw_game).expanduser().resolve(strict=True)
            if isinstance(raw_game, str) else None
        )
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk=risk)

    edition = payload.get("edition", "Enhanced")
    if not isinstance(edition, str) or edition.casefold() not in {"legacy", "enhanced"}:
        raise ProtocolError("edition must be Legacy or Enhanced", risk=risk)

    from allin1_sdk.model_materials import inspect_model_file
    from allin1_sdk.paths import project_root

    try:
        project = inspect_model_file(
            project_root(), source, edition=edition, gta_path=gta_path,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc

    siblings = {
        item.suffix.casefold(): item.name
        for item in source.parent.iterdir()
        if (
            item.is_file() and not item.is_symlink()
            and item.stem.casefold() == source.stem.casefold()
            and item.suffix.casefold() in {".ytd", ".ybn"}
        )
    }
    result = project.to_dict()
    result.update({
        "kind": "model_material_project",
        "viewport": {
            "source": str(source.parent),
            "entry": source.name,
            "texture_entry": siblings.get(".ytd"),
            "collision_entry": siblings.get(".ybn"),
        },
        "read_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    return risk, dict(_bounded(result))


def _model_material_workspace(payload: object, *, risk: str) -> Any:
    if not isinstance(payload, dict):
        raise ProtocolError("model material workspace payload must be an object", risk=risk)
    raw_workspace = payload.get("workspace")
    if (
        not isinstance(raw_workspace, str) or not raw_workspace.strip()
        or "\0" in raw_workspace
    ):
        raise ProtocolError("model material workspace requires a path", risk=risk)
    authored = Path(raw_workspace).expanduser()
    if authored.is_symlink():
        raise ProtocolError("model material workspace cannot be a symbolic link", risk=risk)
    try:
        workspace_path = authored.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"model material workspace was not found: {exc}", risk=risk,
        ) from exc
    if not workspace_path.is_dir():
        raise ProtocolError("model material workspace must be a directory", risk=risk)

    from allin1_sdk.model_materials import MaterialAuthoringWorkspace

    try:
        return MaterialAuthoringWorkspace(workspace_path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc


def _model_material_can_undo(workspace: Any) -> bool:
    history = workspace.root / "history"
    return any(
        path.is_dir() and not path.is_symlink()
        and (path / "edit.json").is_file()
        and not path.name.endswith((".undone", ".undo-recovery"))
        for path in history.iterdir()
    )


def _model_material_snapshot(workspace: Any) -> dict[str, Any]:
    project = workspace.inspect()
    source_name = str(workspace.manifest["source_name"])
    source = (workspace.root / "original" / source_name).resolve(strict=True)
    result = project.to_dict()
    result.update({
        "kind": "model_material_authoring_session",
        "operation": "inspect_model_material_workspace",
        "workspace": str(workspace.root),
        "source": str(source),
        "size": source.stat().st_size,
        "native_source_sha256": str(workspace.manifest["source_sha256"]),
        "can_undo": _model_material_can_undo(workspace),
        "viewport": {
            "source": str(source.parent),
            "entry": source.name,
            "texture_entry": None,
            "collision_entry": None,
        },
        "read_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    return result


def _inspect_model_material_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    workspace = _model_material_workspace(payload, risk="read_only")
    try:
        result = _model_material_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    return "read_only", dict(_bounded(result))


def _model_material_create_context(
    payload: object, *, risk: str,
) -> tuple[Path, Path, str, Path | None, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError("model material workspace payload must be an object", risk=risk)
    raw_source = payload.get("source")
    raw_parent = payload.get("parent")
    name = payload.get("name")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError("model material workspace requires a source path", risk=risk)
    if (
        not isinstance(raw_parent, str) or not raw_parent.strip()
        or "\0" in raw_parent
    ):
        raise ProtocolError(
            "model material workspace requires a destination parent", risk=risk,
        )
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", name) is None
    ):
        raise ProtocolError(
            "model material workspace name must contain only letters, numbers, "
            "periods, underscores, and hyphens",
            risk=risk,
        )
    source_authored = Path(raw_source).expanduser()
    parent_authored = Path(raw_parent).expanduser()
    if source_authored.is_symlink() or parent_authored.is_symlink():
        raise ProtocolError("model material source and parent cannot be symbolic links", risk=risk)
    try:
        source = source_authored.resolve(strict=True)
        parent = parent_authored.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"model material source or destination was not found: {exc}", risk=risk,
        ) from exc

    from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES, MODEL_PREVIEW_SUFFIXES

    if not source.is_file() or source.suffix.casefold() not in MODEL_PREVIEW_SUFFIXES:
        raise ProtocolError(
            "model material workspace requires a loose YDR, YDD, or YFT asset",
            risk=risk,
        )
    source_size = source.stat().st_size
    if source_size > MAX_NATIVE_PREVIEW_BYTES:
        raise ProtocolError("model material source exceeds the native preview limit", risk=risk)
    if not parent.is_dir():
        raise ProtocolError("model material destination parent must be a directory", risk=risk)
    destination = (parent / name).resolve(strict=False)
    if destination.parent != parent or destination == source:
        raise ProtocolError(
            "model material destination must be a new child workspace", risk=risk,
        )
    if destination.exists() or destination.is_symlink():
        raise ProtocolError(
            f"model material destination already exists: {destination}", risk=risk,
        )
    edition_value = payload.get("edition", "Enhanced")
    if (
        not isinstance(edition_value, str)
        or edition_value.casefold() not in {"legacy", "enhanced"}
    ):
        raise ProtocolError("edition must be Legacy or Enhanced", risk=risk)
    edition = edition_value.title()
    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game
    ):
        raise ProtocolError("gta_path must be a valid path string", risk=risk)
    try:
        gta_path = (
            Path(raw_game).expanduser().resolve(strict=True)
            if isinstance(raw_game, str) else None
        )
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk=risk)
    with source.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {
        "kind": "model_material_workspace_review",
        "operation": "review_model_material_workspace",
        "source": str(source),
        "source_name": source.name,
        "source_size": source_size,
        "source_sha256": source_sha256,
        "edition": edition,
        "destination": str(destination),
        "ready": True,
        "review_only": True,
        "output_write_performed": False,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return source, destination, edition, gta_path, result


def _review_model_material_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _source, _destination, _edition, _gta_path, result = (
        _model_material_create_context(payload, risk="read_only")
    )
    return "read_only", dict(_bounded(result))


def _create_model_material_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("model material workspace payload must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "model material workspace creation requires a reviewed SHA-256 digest",
            risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Creating a material workspace requires action-time confirmation.",
            risk=risk,
        )
    source, destination, edition, gta_path, current_review = (
        _model_material_create_context(payload, risk=risk)
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The model source or destination changed after review; review it again.",
            risk=risk,
        )
    from allin1_sdk.model_materials import MaterialAuthoringWorkspace
    from allin1_sdk.paths import project_root

    try:
        workspace = MaterialAuthoringWorkspace.create(
            project_root(), source, destination, edition=edition, gta_path=gta_path,
        )
        result = _model_material_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "create_model_material_workspace",
        "review_sha256": expected_review,
        "read_only": False,
        "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _material_review_text(
    value: object, label: str, *, allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    normalized = value.strip()
    if (not normalized and not allow_empty) or len(normalized) > 160:
        requirement = "0–160" if allow_empty else "1–160"
        raise ValueError(f"{label} must contain {requirement} characters")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} contains control characters")
    return normalized


def _model_material_edit_context(
    payload: object, *, risk: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    workspace = _model_material_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("model material edit payload must be an object", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "model material edit requires a non-negative expected revision", risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Model material revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    action = payload.get("action")
    if action not in {"material", "parameter", "geometry"}:
        raise ProtocolError(
            "model material edit action must be material, parameter, or geometry",
            risk=risk,
        )
    project = workspace.inspect()
    normalized: dict[str, Any] = {"action": action}
    changes: list[dict[str, str]] = []
    try:
        if action == "material":
            material_index = payload.get("material_index")
            if (
                not isinstance(material_index, int) or isinstance(material_index, bool)
                or not 0 <= material_index < len(project.materials)
            ):
                raise ValueError("Material index is outside the model shader catalog")
            material = project.materials[material_index]
            normalized["material_index"] = material_index
            if "shader_name" in payload:
                shader_name = _material_review_text(payload.get("shader_name"), "Shader name")
                normalized["shader_name"] = shader_name
                if shader_name != material.shader:
                    changes.append({
                        "field": "shader.name", "before": material.shader,
                        "after": shader_name,
                    })
            raw_textures = payload.get("textures", {})
            if (
                not isinstance(raw_textures, dict) or len(raw_textures) > 64
                or any(not isinstance(key, str) for key in raw_textures)
            ):
                raise ValueError("Material textures must be a bounded slot map")
            slots: dict[str, list[Any]] = {}
            for binding in material.textures:
                slots.setdefault(binding.slot.casefold(), []).append(binding)
            textures: dict[str, str] = {}
            for requested_slot, requested_texture in raw_textures.items():
                slot = _material_review_text(requested_slot, "Texture slot")
                matches = slots.get(slot.casefold(), [])
                if len(matches) != 1:
                    raise ValueError(
                        f"Texture slot must resolve exactly once on this material: {slot}"
                    )
                texture = _material_review_text(
                    requested_texture, f"Texture binding {slot}", allow_empty=True,
                )
                textures[slot] = texture
                if texture != matches[0].texture:
                    changes.append({
                        "field": f"texture.{slot}", "before": matches[0].texture,
                        "after": texture,
                    })
            normalized["textures"] = textures
            subject = f"material:{material_index}"
        elif action == "parameter":
            from allin1_sdk.model_materials import normalize_model_parameter_values

            material_index = payload.get("material_index")
            if (
                not isinstance(material_index, int) or isinstance(material_index, bool)
                or not 0 <= material_index < len(project.materials)
            ):
                raise ValueError("Material index is outside the model shader catalog")
            material = project.materials[material_index]
            parameter_name = _material_review_text(
                payload.get("parameter_name"), "Shader parameter name",
            )
            matches = [
                parameter for parameter in material.parameters
                if parameter.name.casefold() == parameter_name.casefold()
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Numeric shader parameter must resolve exactly once: "
                    f"{parameter_name}"
                )
            parameter = matches[0]
            normalized_rows = normalize_model_parameter_values(
                payload.get("values"), expected_rows=len(parameter.values),
            )
            before_rows = normalize_model_parameter_values(
                parameter.values, expected_rows=len(parameter.values),
            )
            for row_index, (before_row, after_row) in enumerate(
                zip(before_rows, normalized_rows, strict=True)
            ):
                for axis, before_value, after_value in zip(
                    ("x", "y", "z", "w"), before_row, after_row, strict=True,
                ):
                    if before_value != after_value:
                        changes.append({
                            "field": (
                                f"parameter.{parameter.name}[{row_index}].{axis}"
                            ),
                            "before": before_value,
                            "after": after_value,
                        })
            normalized.update({
                "material_index": material_index,
                "parameter_name": parameter.name,
                "values": [list(row) for row in normalized_rows],
            })
            subject = f"parameter:{material_index}:{parameter.name}"
        else:
            geometry_index = payload.get("geometry_index")
            material_index = payload.get("material_index")
            if (
                not isinstance(geometry_index, int) or isinstance(geometry_index, bool)
                or not 0 <= geometry_index < len(project.geometries)
            ):
                raise ValueError("Geometry index is outside the model geometry catalog")
            geometry = project.geometries[geometry_index]
            if (
                not isinstance(material_index, int) or isinstance(material_index, bool)
                or not 0 <= material_index < len(geometry.available_materials)
            ):
                raise ValueError("Material index is outside this geometry's local shader group")
            if geometry.material_index == material_index:
                raise ValueError("Geometry already uses the selected material")
            normalized.update({
                "geometry_index": geometry_index, "material_index": material_index,
            })
            changes.append({
                "field": "geometry.shaderIndex",
                "before": str(geometry.material_index), "after": str(material_index),
            })
            subject = f"geometry:{geometry_index}"
        if not changes:
            raise ValueError("Material edit does not change the selected record")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = {
        "kind": "model_material_edit_review",
        "operation": "review_model_material_edit",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "project_sha256": project.sha256,
        "action": action,
        "subject": subject,
        "changes": changes,
        "ready": True,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, normalized, result


def _review_model_material_edit(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _normalized, result = _model_material_edit_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_model_material_edit(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("model material edit payload must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "model material edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a model material edit requires action-time confirmation.",
            risk=risk,
        )
    workspace, normalized, current_review = _model_material_edit_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The material workspace revision or edit changed after review; review it again.",
            risk=risk,
        )
    try:
        if normalized["action"] == "material":
            changed = workspace.set_material(
                normalized["material_index"], expected_revision=workspace.revision,
                shader_name=normalized.get("shader_name"),
                textures=normalized["textures"],
            )
        elif normalized["action"] == "parameter":
            changed = workspace.set_parameter(
                normalized["material_index"], normalized["parameter_name"],
                normalized["values"], expected_revision=workspace.revision,
            )
        else:
            changed = workspace.set_geometry_material(
                normalized["geometry_index"], normalized["material_index"],
                expected_revision=workspace.revision,
            )
        result = _model_material_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_model_material_edit",
        "review_sha256": expected_review,
        "changes": list(changed.changes),
        "history": str(changed.history),
        "read_only": False,
        "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _apply_model_material_history(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    workspace = _model_material_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("model material history payload must be an object", risk=risk)
    if payload.get("direction") != "undo":
        raise ProtocolError("model material history direction must be undo", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "model material history requires a non-negative expected revision", risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Model material revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Model material undo requires action-time confirmation.", risk=risk,
        )
    try:
        changed = workspace.undo(expected_revision=expected_revision)
        result = _model_material_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_model_material_history",
        "direction": "undo",
        "changes": list(changed.changes),
        "history": str(changed.history),
        "read_only": False,
        "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _model_material_build_context(
    payload: object, *, risk: str,
) -> tuple[Any, Path, Path | None, dict[str, Any]]:
    workspace = _model_material_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("model material build payload must be an object", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "model material build requires a non-negative expected revision", risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Model material revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    raw_destination = payload.get("destination")
    if (
        not isinstance(raw_destination, str) or not raw_destination.strip()
        or "\0" in raw_destination or len(raw_destination) > 4096
    ):
        raise ProtocolError("model material build requires a destination", risk=risk)
    authored_destination = Path(raw_destination).expanduser()
    if not authored_destination.name or len(authored_destination.name) > 160:
        raise ProtocolError("model material output name is invalid", risk=risk)
    try:
        parent = authored_destination.parent.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"model material output parent was not found: {exc}", risk=risk,
        ) from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ProtocolError("model material output parent must be a real directory", risk=risk)
    destination = (parent / authored_destination.name).resolve(strict=False)
    expected_suffix = Path(str(workspace.manifest["source_name"])).suffix.casefold()
    if destination.suffix.casefold() != expected_suffix:
        raise ProtocolError(
            f"model material output must retain the {expected_suffix} extension", risk=risk,
        )
    report = destination.with_name(f"{destination.name}.allin1.json")
    if (
        destination.exists() or destination.is_symlink()
        or report.exists() or report.is_symlink()
    ):
        raise ProtocolError(
            f"model material output already exists: {destination}", risk=risk,
        )
    if destination == workspace.root or destination.is_relative_to(workspace.root):
        raise ProtocolError(
            "model material output must be outside the authoring workspace", risk=risk,
        )
    raw_gta = payload.get("gta_path")
    gta_path: Path | None = None
    if raw_gta not in (None, ""):
        if (
            not isinstance(raw_gta, str) or "\0" in raw_gta
            or len(raw_gta) > 4096
        ):
            raise ProtocolError("model material GTA path is invalid", risk=risk)
        try:
            gta_path = Path(raw_gta).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(
                f"model material GTA path was not found: {exc}", risk=risk,
            ) from exc
        if not gta_path.is_dir():
            raise ProtocolError("model material GTA path must be a directory", risk=risk)
    from allin1_sdk.paths import gta_root_containing, project_root

    explicit_roots = (gta_path,) if gta_path is not None else ()
    detected_game = gta_root_containing(destination, explicit_roots=explicit_roots)
    if detected_game is not None:
        raise ProtocolError(
            f"model material output cannot be written inside GTA V: {detected_game}",
            risk=risk,
        )
    project = workspace.inspect()
    from allin1_sdk.native_assets import NativeAssetInspector

    inspector = NativeAssetInspector(project_root(), gta_path)
    if not inspector.patcher.is_file():
        raise ProtocolError(
            "RpfPatcher is not built; run runtools.ps1 before native material build",
            risk=risk,
        )
    result = {
        "kind": "model_material_build_review",
        "operation": "review_model_material_build",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "project_sha256": project.sha256,
        "edition": str(workspace.manifest["edition"]),
        "source_name": str(workspace.manifest["source_name"]),
        "destination": str(destination),
        "validation_report": str(report),
        "checks": [
            {
                "key": "revision", "label": "Workspace revision",
                "status": "ready", "detail": f"Revision {workspace.revision} is current",
            },
            {
                "key": "toolchain", "label": "Native compiler",
                "status": "ready", "detail": "RpfPatcher asset-from-xml is available",
            },
            {
                "key": "reparse", "label": "Post-build validation",
                "status": "ready", "detail": "Compiled output must decode back to XML",
            },
            {
                "key": "destination", "label": "Output boundary",
                "status": "ready", "detail": "New asset outside the workspace and GTA V",
            },
        ],
        "ready": True,
        "review_only": True,
        "output_write_performed": False,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, destination, gta_path, result


def _review_model_material_build(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _destination, _gta_path, result = _model_material_build_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_model_material_build(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("model material build payload must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "model material build requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Building a native model requires action-time confirmation.", risk=risk,
        )
    workspace, destination, gta_path, current_review = _model_material_build_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The material workspace, toolchain, or destination changed after review; "
            "review the build again.",
            risk=risk,
        )
    from allin1_sdk.model_materials import inspect_model_file
    from allin1_sdk.paths import project_root

    try:
        output, report_path = workspace.build(
            project_root(), destination, gta_path=gta_path,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            not isinstance(report, dict)
            or report.get("operation") != "native_asset_workspace_build"
        ):
            raise ValueError("Native material build report is invalid")
        built_project = inspect_model_file(
            project_root(), output, edition=str(workspace.manifest["edition"]),
            gta_path=gta_path,
        )
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    built = built_project.to_dict()
    built.update({
        "kind": "model_material_project",
        "viewport": {
            "source": str(output.parent), "entry": output.name,
            "texture_entry": None, "collision_entry": None,
        },
        "read_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    output_record = report.get("output")
    validation = report.get("validation")
    if not isinstance(output_record, dict) or not isinstance(validation, dict):
        raise ProtocolError("Native material build report is incomplete", risk=risk)
    result = {
        "kind": "model_material_build_result",
        "operation": "apply_model_material_build",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "review_sha256": expected_review,
        "output": output_record,
        "validation_report": str(report_path),
        "validation_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "validation": validation,
        "comparison": {
            "source_xml_sha256": current_review["project_sha256"],
            "output_sha256": output_record.get("sha256"),
            "source_materials": workspace.inspect().to_dict()["summary"]["materials"],
            "output_materials": built["summary"]["materials"],
            "source_geometries": workspace.inspect().to_dict()["summary"]["geometries"],
            "output_geometries": built["summary"]["geometries"],
        },
        "built_project": built,
        "read_only": False,
        "output_write_performed": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    return risk, dict(_bounded(result))


def _texture_workspace(payload: object, *, risk: str) -> Any:
    if not isinstance(payload, dict):
        raise ProtocolError("texture workspace payload must be an object", risk=risk)
    raw_workspace = payload.get("workspace")
    if (
        not isinstance(raw_workspace, str) or not raw_workspace.strip()
        or "\0" in raw_workspace or len(raw_workspace) > 4096
    ):
        raise ProtocolError("texture workspace requires a path", risk=risk)
    authored = Path(raw_workspace).expanduser()
    if authored.is_symlink():
        raise ProtocolError("texture workspace cannot be a symbolic link", risk=risk)
    try:
        workspace_path = authored.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"texture workspace was not found: {exc}", risk=risk) from exc
    if not workspace_path.is_dir():
        raise ProtocolError("texture workspace must be a directory", risk=risk)
    from allin1_sdk.texture_workspace import TextureDictionaryWorkspace

    try:
        return TextureDictionaryWorkspace(workspace_path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc


def _texture_workspace_snapshot(workspace: Any) -> dict[str, Any]:
    catalog = workspace.catalog()
    source = workspace.manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("YTD workspace source identity is missing")
    source_name = source.get("name")
    snapshot_value = source.get("snapshot")
    if not isinstance(source_name, str) or not isinstance(snapshot_value, str):
        raise ValueError("YTD workspace source identity is malformed")
    snapshot = (workspace.root / Path(snapshot_value)).resolve(strict=True)
    if (
        not snapshot.is_relative_to(workspace.root) or not snapshot.is_file()
        or snapshot.is_symlink() or snapshot.name != source_name
    ):
        raise ValueError("YTD workspace source snapshot is missing or unsafe")
    expected_size = source.get("size")
    expected_sha = source.get("sha256")
    if (
        not isinstance(expected_size, int) or isinstance(expected_size, bool)
        or not isinstance(expected_sha, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) is None
        or snapshot.stat().st_size != expected_size
    ):
        raise ValueError("YTD workspace source identity is malformed")
    with snapshot.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha.casefold():
            raise ValueError("YTD workspace source snapshot was modified")
    result = catalog.to_dict()
    result.update({
        "kind": "texture_workspace_session",
        "operation": "inspect_texture_workspace",
        "source": str(snapshot),
        "source_name": source_name,
        "source_size": snapshot.stat().st_size,
        "source_sha256": str(source.get("sha256", "")),
        "edition": str(workspace.manifest.get("edition", "")),
        "revision": workspace.revision,
        "state_sha256": workspace.state_sha256(),
        "can_undo": workspace.can_undo,
        "read_only": True,
        "workspace_write_performed": False,
        "output_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    return result


def _inspect_texture_workspace(payload: object) -> tuple[str, dict[str, Any]]:
    workspace = _texture_workspace(payload, risk="read_only")
    try:
        result = _texture_workspace_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    return "read_only", dict(_bounded(result))


def _texture_workspace_create_context(
    payload: object, *, risk: str,
) -> tuple[Path, Path, str, Path | None, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError("texture workspace payload must be an object", risk=risk)
    raw_source = payload.get("source")
    raw_parent = payload.get("parent")
    name = payload.get("name")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError("texture workspace requires a loose YTD source", risk=risk)
    if (
        not isinstance(raw_parent, str) or not raw_parent.strip()
        or "\0" in raw_parent
    ):
        raise ProtocolError("texture workspace requires a destination parent", risk=risk)
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", name) is None
    ):
        raise ProtocolError(
            "texture workspace name must contain only letters, numbers, periods, "
            "underscores, and hyphens", risk=risk,
        )
    source_authored = Path(raw_source).expanduser()
    parent_authored = Path(raw_parent).expanduser()
    if source_authored.is_symlink() or parent_authored.is_symlink():
        raise ProtocolError("texture source and parent cannot be symbolic links", risk=risk)
    try:
        source = source_authored.resolve(strict=True)
        parent = parent_authored.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"texture source or destination was not found: {exc}", risk=risk) from exc
    from allin1_sdk.native_assets import MAX_NATIVE_PREVIEW_BYTES

    if not source.is_file() or source.suffix.casefold() != ".ytd":
        raise ProtocolError("texture workspace requires a loose YTD asset", risk=risk)
    if not 0 < source.stat().st_size <= MAX_NATIVE_PREVIEW_BYTES:
        raise ProtocolError("YTD source is empty or exceeds the native limit", risk=risk)
    if not parent.is_dir():
        raise ProtocolError("texture destination parent must be a directory", risk=risk)
    destination = (parent / name).resolve(strict=False)
    if destination.parent != parent or destination.exists() or destination.is_symlink():
        raise ProtocolError(f"texture workspace destination is not new: {destination}", risk=risk)
    edition_value = payload.get("edition", "Enhanced")
    if (
        not isinstance(edition_value, str)
        or edition_value.casefold() not in {"legacy", "enhanced"}
    ):
        raise ProtocolError("edition must be Legacy or Enhanced", risk=risk)
    edition = edition_value.title()
    raw_gta = payload.get("gta_path")
    if raw_gta not in (None, "") and (
        not isinstance(raw_gta, str) or "\0" in raw_gta or len(raw_gta) > 4096
    ):
        raise ProtocolError("texture GTA path is invalid", risk=risk)
    try:
        gta_path = (
            Path(raw_gta).expanduser().resolve(strict=True)
            if isinstance(raw_gta, str) and raw_gta else None
        )
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk=risk)
    from allin1_sdk.paths import gta_root_containing, project_root
    from allin1_sdk.native_assets import NativeAssetInspector

    detected_game = gta_root_containing(
        destination, explicit_roots=(gta_path,) if gta_path is not None else (),
    )
    if detected_game is not None:
        raise ProtocolError(
            f"texture workspace cannot be created inside GTA V: {detected_game}", risk=risk,
        )
    inspector = NativeAssetInspector(project_root(), gta_path)
    if not inspector.patcher.is_file():
        raise ProtocolError(
            "RpfPatcher is not built; run runtools.ps1 before YTD authoring", risk=risk,
        )
    with source.open("rb") as stream:
        source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {
        "kind": "texture_workspace_review",
        "operation": "review_texture_workspace",
        "source": str(source), "source_name": source.name,
        "source_size": source.stat().st_size, "source_sha256": source_sha256,
        "edition": edition, "destination": str(destination),
        "ready": True, "review_only": True,
        "workspace_write_performed": False,
        "output_write_performed": False,
        "package_write_performed": False, "game_write_performed": False,
    }
    result["review_sha256"] = hashlib.sha256(json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return source, destination, edition, gta_path, result


def _review_texture_workspace(payload: object) -> tuple[str, dict[str, Any]]:
    *_context, result = _texture_workspace_create_context(payload, risk="read_only")
    return "read_only", dict(_bounded(result))


def _create_texture_workspace(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("texture workspace payload must be an object", risk=risk)
    review_sha = payload.get("review_sha256")
    if not isinstance(review_sha, str) or re.fullmatch(r"[0-9a-f]{64}", review_sha) is None:
        raise ProtocolError("texture workspace requires a reviewed SHA-256 digest", risk=risk)
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError("Creating a texture workspace requires confirmation.", risk=risk)
    source, destination, edition, gta_path, review = _texture_workspace_create_context(
        payload, risk=risk,
    )
    if review["review_sha256"] != review_sha:
        raise ProtocolError("The YTD source or destination changed after review.", risk=risk)
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.paths import project_root
    from allin1_sdk.texture_workspace import TextureDictionaryWorkspace

    try:
        NativeAssetInspector(project_root(), gta_path).export_workspace(
            source, destination, edition=edition,
        )
        workspace = TextureDictionaryWorkspace(destination)
        result = _texture_workspace_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        if destination.exists():
            import shutil
            shutil.rmtree(destination, ignore_errors=True)
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "create_texture_workspace", "review_sha256": review_sha,
        "read_only": False, "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _preview_texture_workspace(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "read_only"
    workspace = _texture_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("texture preview payload must be an object", risk=risk)
    texture_name = payload.get("texture_name")
    if (
        not isinstance(texture_name, str) or not texture_name.strip()
        or "\0" in texture_name or len(texture_name) > 120
    ):
        raise ProtocolError("texture preview requires a bounded texture name", risk=risk)
    try:
        current_state = workspace.state_sha256()
        expected_state = payload.get("expected_state_sha256")
        if expected_state not in (None, current_state):
            raise ValueError("Texture workspace changed before preview")
        source = workspace.texture_path(texture_name)
        catalog = workspace.catalog()
        record = next(
            item for item in catalog.textures
            if item.name.casefold() == texture_name.strip().casefold()
        )
        import io
        import os
        from PIL import Image, ImageOps, UnidentifiedImageError
        from allin1_sdk.asset_preview import PreviewArtifactStore

        artifact = None
        warning = None
        configured = os.environ.get("ALLIN1_PREVIEW_DIR", "").strip()
        if configured:
            try:
                with Image.open(source) as opened:
                    opened.load()
                    rendered = ImageOps.exif_transpose(opened).convert("RGBA")
                    rendered.thumbnail((1024, 768), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    rendered.save(output, format="PNG", optimize=True)
                artifact = PreviewArtifactStore(configured).write_png(output.getvalue())
                artifact.update({"width": rendered.width, "height": rendered.height})
            except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
                warning = f"Texture image preview unavailable: {exc}"
        else:
            warning = "Preview artifact cache is unavailable; metadata only."
    except (OSError, RuntimeError, StopIteration, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = {
        "kind": "texture_workspace_preview", "workspace": str(workspace.root),
        "state_sha256": current_state, "texture": asdict(record),
        "artifact": artifact, "warning": warning, "read_only": True,
        "workspace_write_performed": False, "package_write_performed": False,
        "game_write_performed": False,
    }
    return risk, dict(_bounded(result))


def _texture_edit_context(
    payload: object, *, risk: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    workspace = _texture_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("texture edit payload must be an object", risk=risk)
    expected_state = payload.get("expected_state_sha256")
    if not isinstance(expected_state, str) or re.fullmatch(r"[0-9a-f]{64}", expected_state) is None:
        raise ProtocolError("texture edit requires the expected workspace digest", risk=risk)
    state = workspace.state_sha256()
    if state != expected_state:
        raise ProtocolError("Texture workspace changed after it was loaded.", risk=risk)
    action = payload.get("action")
    if action not in {"replace", "add", "remove"}:
        raise ProtocolError("texture action must be replace, add, or remove", risk=risk)
    texture_name = payload.get("texture_name")
    if not isinstance(texture_name, str):
        raise ProtocolError("texture edit requires a texture name", risk=risk)
    from allin1_sdk.texture_workspace import inspect_texture_source
    try:
        normalized_name = workspace.validate_texture_name(texture_name)
        catalog = workspace.catalog()
        matches = [
            item for item in catalog.textures
            if item.name.casefold() == normalized_name.casefold()
        ]
        source_inspection = None
        if action in {"replace", "add"}:
            raw_source = payload.get("source_image")
            if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
                raise ValueError("Texture replacement requires an image source")
            source_inspection = inspect_texture_source(raw_source)
        if action == "add" and matches:
            raise ValueError(f"YTD texture already exists: {normalized_name}")
        if action in {"replace", "remove"} and len(matches) != 1:
            raise ValueError(f"YTD texture was not found uniquely: {normalized_name}")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    existing = matches[0] if matches else None
    source_data = source_inspection.to_dict() if source_inspection is not None else None
    changes = [{
        "field": "texture",
        "before": existing.name if existing is not None else "(absent)",
        "after": "(removed)" if action == "remove" else normalized_name,
    }]
    if source_data is not None:
        changes.extend([
            {
                "field": "dimensions",
                "before": f"{existing.width}×{existing.height}" if existing else "(absent)",
                "after": f"{source_data['width']}×{source_data['height']}",
            },
            {
                "field": "format",
                "before": existing.format if existing else "(absent)",
                "after": str(source_data["format"]),
            },
        ])
    normalized = {
        "action": action, "texture_name": normalized_name,
        **({"source_image": str(source_inspection.source)} if source_inspection else {}),
    }
    review = {
        "kind": "texture_edit_review", "operation": "review_texture_edit",
        "workspace": str(workspace.root), "revision": workspace.revision,
        "state_sha256": state, "action": action, "texture_name": normalized_name,
        "source": source_data, "changes": changes,
        "warning": (
            "Removing a texture may leave external model bindings unresolved."
            if action == "remove" else
            "Raster inputs are converted to uncompressed RGBA DDS with one mip level."
            if source_inspection and source_inspection.converted_to_dds else None
        ),
        "ready": True, "review_only": True,
        "workspace_write_performed": False, "package_write_performed": False,
        "game_write_performed": False,
    }
    review["review_sha256"] = hashlib.sha256(json.dumps(
        review, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return workspace, normalized, review


def _review_texture_edit(payload: object) -> tuple[str, dict[str, Any]]:
    _workspace_value, _normalized, review = _texture_edit_context(payload, risk="read_only")
    return "read_only", dict(_bounded(review))


def _apply_texture_edit(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("texture edit payload must be an object", risk=risk)
    review_sha = payload.get("review_sha256")
    if not isinstance(review_sha, str) or re.fullmatch(r"[0-9a-f]{64}", review_sha) is None:
        raise ProtocolError("texture edit requires a reviewed SHA-256 digest", risk=risk)
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError("Texture edits require action-time confirmation.", risk=risk)
    workspace, normalized, review = _texture_edit_context(payload, risk=risk)
    if review["review_sha256"] != review_sha:
        raise ProtocolError("The texture workspace or source changed after review.", risk=risk)
    try:
        action = normalized["action"]
        if action == "replace":
            edit = workspace.replace(normalized["texture_name"], normalized["source_image"])
        elif action == "add":
            edit = workspace.add(normalized["texture_name"], normalized["source_image"])
        else:
            edit = workspace.remove(normalized["texture_name"])
        result = _texture_workspace_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_texture_edit", "action": edit.action,
        "edited_texture": asdict(edit.texture), "review_sha256": review_sha,
        "read_only": False, "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _apply_texture_history(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    workspace = _texture_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("texture history payload must be an object", risk=risk)
    expected = payload.get("expected_state_sha256")
    if not isinstance(expected, str) or expected != workspace.state_sha256():
        raise ProtocolError("Texture workspace changed before undo.", risk=risk)
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError("Texture undo requires action-time confirmation.", risk=risk)
    try:
        restored = workspace.restore_latest()
        result = _texture_workspace_snapshot(workspace)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_texture_history", "restored": restored.restored.name,
        "read_only": False, "workspace_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _texture_build_context(
    payload: object, *, risk: str,
) -> tuple[Any, Path, Path | None, dict[str, Any]]:
    workspace = _texture_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("texture build payload must be an object", risk=risk)
    expected = payload.get("expected_state_sha256")
    state = workspace.state_sha256()
    if not isinstance(expected, str) or expected != state:
        raise ProtocolError("Texture workspace changed before build review.", risk=risk)
    raw_destination = payload.get("destination")
    if (
        not isinstance(raw_destination, str) or not raw_destination.strip()
        or "\0" in raw_destination or len(raw_destination) > 4096
    ):
        raise ProtocolError("texture build requires a destination", risk=risk)
    authored = Path(raw_destination).expanduser()
    if (
        not authored.name or len(authored.name) > 160
        or authored.is_symlink()
    ):
        raise ProtocolError("texture build destination name is unsafe", risk=risk)
    try:
        parent = authored.parent.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"texture build parent was not found: {exc}", risk=risk) from exc
    if not parent.is_dir():
        raise ProtocolError("texture build parent must be a directory", risk=risk)
    destination = (parent / authored.name).resolve(strict=False)
    if destination.suffix.casefold() != ".ytd":
        raise ProtocolError("texture build output must retain the .ytd extension", risk=risk)
    report = destination.with_name(f"{destination.name}.allin1.json")
    if destination.exists() or destination.is_symlink() or report.exists() or report.is_symlink():
        raise ProtocolError(f"texture build output already exists: {destination}", risk=risk)
    if destination == workspace.root or destination.is_relative_to(workspace.root):
        raise ProtocolError("texture build output must be outside the workspace", risk=risk)
    raw_gta = payload.get("gta_path")
    if raw_gta not in (None, "") and (
        not isinstance(raw_gta, str) or "\0" in raw_gta or len(raw_gta) > 4096
    ):
        raise ProtocolError("texture GTA path is invalid", risk=risk)
    try:
        gta_path = (
            Path(raw_gta).expanduser().resolve(strict=True)
            if isinstance(raw_gta, str) and raw_gta else None
        )
    except OSError as exc:
        raise ProtocolError(f"texture GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("texture GTA path must be a directory", risk=risk)
    from allin1_sdk.paths import gta_root_containing, project_root
    from allin1_sdk.native_assets import NativeAssetInspector

    detected = gta_root_containing(
        destination, explicit_roots=(gta_path,) if gta_path is not None else (),
    )
    if detected is not None:
        raise ProtocolError(f"texture output cannot be written inside GTA V: {detected}", risk=risk)
    if not NativeAssetInspector(project_root(), gta_path).patcher.is_file():
        raise ProtocolError("RpfPatcher is not built; run runtools.ps1 before YTD build", risk=risk)
    review = {
        "kind": "texture_build_review", "operation": "review_texture_build",
        "workspace": str(workspace.root), "revision": workspace.revision,
        "state_sha256": state, "destination": str(destination),
        "validation_report": str(report),
        "checks": [
            {"key": "state", "label": "Workspace digest", "status": "ready", "detail": state[:12]},
            {"key": "toolchain", "label": "Native compiler", "status": "ready", "detail": "RpfPatcher asset-from-xml is available"},
            {"key": "reparse", "label": "Post-build validation", "status": "ready", "detail": "Compiled YTD must decode back to XML"},
            {"key": "destination", "label": "Output boundary", "status": "ready", "detail": "New YTD outside the workspace and GTA V"},
        ],
        "ready": True, "review_only": True, "output_write_performed": False,
        "workspace_write_performed": False, "package_write_performed": False,
        "game_write_performed": False,
    }
    review["review_sha256"] = hashlib.sha256(json.dumps(
        review, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return workspace, destination, gta_path, review


def _review_texture_build(payload: object) -> tuple[str, dict[str, Any]]:
    _workspace_value, _destination, _gta_path, review = _texture_build_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(review))


def _apply_texture_build(payload: object) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("texture build payload must be an object", risk=risk)
    review_sha = payload.get("review_sha256")
    if not isinstance(review_sha, str) or re.fullmatch(r"[0-9a-f]{64}", review_sha) is None:
        raise ProtocolError("texture build requires a reviewed SHA-256 digest", risk=risk)
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError("Texture build requires action-time confirmation.", risk=risk)
    workspace, destination, gta_path, review = _texture_build_context(payload, risk=risk)
    if review["review_sha256"] != review_sha:
        raise ProtocolError("The texture workspace or destination changed after review.", risk=risk)
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.paths import project_root

    try:
        output, report_path = NativeAssetInspector(project_root(), gta_path).build_workspace(
            workspace.root, destination,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    output_data = report.get("output") if isinstance(report, dict) else None
    validation = report.get("validation") if isinstance(report, dict) else None
    try:
        valid_receipt = (
            Path(output).resolve(strict=True) == destination
            and Path(report_path).resolve(strict=True) == Path(review["validation_report"])
            and isinstance(output_data, dict)
            and output_data.get("path") == str(destination)
            and isinstance(output_data.get("size"), int)
            and not isinstance(output_data.get("size"), bool)
            and output_data.get("size") == destination.stat().st_size
            and isinstance(output_data.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", output_data["sha256"]) is not None
            and hashlib.sha256(destination.read_bytes()).hexdigest() == output_data["sha256"]
            and isinstance(validation, dict)
            and validation.get("reparsed") is True
            and validation.get("semantic_xml_match") is True
            and isinstance(validation.get("dependency_count"), int)
            and not isinstance(validation.get("dependency_count"), bool)
            and validation["dependency_count"] >= 0
        )
    except (OSError, TypeError, ValueError):
        valid_receipt = False
    if not valid_receipt:
        for created in (destination, destination.with_name(f"{destination.name}.allin1.json")):
            try:
                created.unlink(missing_ok=True)
            except OSError:
                pass
        raise ProtocolError(
            "Rebuilt YTD did not produce a verified semantic validation receipt.",
            risk=risk,
        )
    result = {
        "kind": "texture_build_result", "operation": "apply_texture_build",
        "workspace": str(workspace.root), "revision": workspace.revision,
        "state_sha256": workspace.state_sha256(), "review_sha256": review_sha,
        "output": output_data, "validation": validation,
        "validation_report": str(report_path),
        "validation_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "output_write_performed": True, "workspace_write_performed": False,
        "package_write_performed": False, "game_write_performed": False,
    }
    return risk, dict(_bounded(result))


def _configure_assistant(payload: object) -> tuple[str, dict[str, Any]]:
    from allin1_sdk.assistant_settings import save_standalone_assistant_settings

    risk = "authoring_write"
    if (
        not isinstance(payload, dict)
        or set(payload) != {"settings", "authoring_confirmed"}
        or payload.get("authoring_confirmed") is not True
    ):
        raise ProtocolError("Saving assistant settings requires explicit confirmation", risk=risk)
    try:
        path = save_standalone_assistant_settings(payload["settings"])
    except (OSError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    return risk, {
        "kind": "assistant_configuration", "path": str(path),
        "settings_write_performed": True, "launcher_write_performed": False,
        "game_write_performed": False, "runtime_started": False,
        "message": "SDK assistant settings saved. No runtime was started or downloaded.",
    }


def _assistant_status() -> tuple[str, dict[str, Any]]:
    """Report Qwen/provider readiness without starting an inference runtime."""
    from allin1_sdk.assistant_client import assistant_status

    try:
        result = assistant_status()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result = {
            "configured": False,
            "enabled": False,
            "mode": "unavailable",
            "model": "",
            "local_runtime_running": False,
            "structured_output_ready": False,
            "provider_capabilities": [],
            "message": str(exc),
        }
    else:
        result["configured"] = True
        result.setdefault("message", "Assistant configuration is ready.")
    result.update({
        "kind": "assistant_status",
        "read_only": True,
        "runtime_started": False,
        "command_execution_performed": False,
        "game_write_performed": False,
    })
    return "read_only", dict(_bounded(result))


def _assistant_prompt(payload: object) -> tuple[str, dict[str, Any]]:
    """Run one structured, advisory-only assistant prompt in an isolated job."""
    risk = "read_only"
    if not isinstance(payload, dict):
        raise ProtocolError("assistant_prompt payload must be an object", risk=risk)
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip() or "\0" in question:
        raise ProtocolError("assistant question must be a non-empty string", risk=risk)

    def optional_path(key: str, *, directory: bool = False) -> Path | None:
        raw = payload.get(key)
        if raw is None or raw == "":
            return None
        if not isinstance(raw, str) or "\0" in raw:
            raise ProtocolError(f"{key} must be a valid path string", risk=risk)
        authored = Path(raw).expanduser()
        if authored.is_symlink():
            raise ProtocolError(f"{key} cannot be a symbolic link", risk=risk)
        try:
            resolved = authored.resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"{key} was not found: {exc}", risk=risk) from exc
        if directory and not resolved.is_dir():
            raise ProtocolError(f"{key} must be a directory", risk=risk)
        return resolved

    repository_root = optional_path("repository_root", directory=True)
    gta_path = optional_path("gta_path", directory=True)
    raw_max_tokens = payload.get("max_tokens", 640)
    if (
        isinstance(raw_max_tokens, bool) or not isinstance(raw_max_tokens, int)
        or not 64 <= raw_max_tokens <= 2048
    ):
        raise ProtocolError("assistant max_tokens must be between 64 and 2,048", risk=risk)

    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list) or len(raw_sources) > 24:
        raise ProtocolError("assistant sources must be a list of at most 24 paths", risk=risk)
    sources: list[Path] = []
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
            raise ProtocolError(f"assistant sources[{index}] is invalid", risk=risk)
        authored = Path(raw).expanduser()
        if authored.is_symlink():
            raise ProtocolError("assistant sources cannot be symbolic links", risk=risk)
        try:
            source = authored.resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"assistant source was not found: {exc}", risk=risk) from exc
        sources.append(source)

    from allin1_sdk.assistant_client import prompt_assistant

    try:
        prompt_result = prompt_assistant(
            question,
            repository_root=repository_root,
            gta_path=gta_path,
            sources=sources,
            max_tokens=raw_max_tokens,
            operation_mode="advisory",
            compact_response=True,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        details = getattr(exc, "details", None)
        raise ProtocolError(str(exc), risk=risk, details=details) from exc

    result = prompt_result.to_dict()
    result.pop("context", None)
    result.update({
        "kind": "assistant_prompt_result",
        "question_sha256": hashlib.sha256(question.strip().encode("utf-8")).hexdigest(),
        "read_only": True,
        "advisory_only": True,
        "command_execution_performed": False,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    return risk, dict(_bounded(result))


def _inspect_rpf_archive(payload: object) -> tuple[str, dict[str, Any]]:
    """Return one bounded recursive RPF index without extracting a member."""
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_rpf_archive payload must be an object")
    raw_archive = payload.get("archive")
    raw_game = payload.get("gta_path")
    if (
        not isinstance(raw_archive, str)
        or not raw_archive.strip()
        or "\0" in raw_archive
    ):
        raise ProtocolError(
            "inspect_rpf_archive requires a loose RPF archive", risk="read_only",
        )
    if raw_game is not None and (
        not isinstance(raw_game, str)
        or not raw_game.strip()
        or "\0" in raw_game
    ):
        raise ProtocolError(
            "gta_path must be a valid path string", risk="read_only",
        )
    try:
        archive = Path(raw_archive).expanduser().resolve(strict=True)
        gta_path = (
            Path(raw_game).expanduser().resolve(strict=True)
            if isinstance(raw_game, str) else None
        )
    except OSError as exc:
        raise ProtocolError(
            f"RPF inspection path was not found: {exc}", risk="read_only",
        ) from exc
    if not archive.is_file() or archive.suffix.casefold() != ".rpf":
        raise ProtocolError(
            "RPF inspection requires a loose .rpf archive", risk="read_only",
        )
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk="read_only")

    from allin1_sdk.detector import detect_gta_path
    from allin1_sdk.paths import gta_root_containing, project_root
    from allin1_sdk.rpf_tools import RpfExplorerService

    try:
        gta_path = gta_path or gta_root_containing(archive) or detect_gta_path()
        index = RpfExplorerService(project_root(), gta_path).index(archive)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc

    entries = [asdict(item) for item in index.entries[:_MAX_ENTRIES]]
    archives = [asdict(item) for item in index.archives[:_MAX_ENTRIES]]
    files = [item for item in index.entries if item.kind != "directory"]
    result = {
        "kind": "rpf_archive_index",
        "operation": "inspect_rpf_archive",
        "source": str(index.source),
        "gta_path": str(gta_path),
        "edition": index.edition,
        "archive_size": index.archive_size,
        "archives": archives,
        "entries": entries,
        "warnings": list(index.warnings[:_MAX_FINDINGS]),
        "suffix_counts": dict(list(index.suffix_counts().items())[:250]),
        "archive_count": len(index.archives),
        "entry_count": len(index.entries),
        "returned_entry_count": len(entries),
        "directory_count": sum(
            item.kind == "directory" for item in index.entries
        ),
        "file_count": len(files),
        "logical_bytes": sum(item.size for item in files),
        "stored_bytes": sum(item.stored_size for item in files),
        "truncated": (
            len(index.archives) > _MAX_ENTRIES
            or len(index.entries) > _MAX_ENTRIES
            or len(index.warnings) > _MAX_FINDINGS
        ),
        "read_only": True,
        "game_write_performed": False,
    }
    return "read_only", dict(_bounded(result))


def _vehicle_project_result(
    project: Any, *, gta_path: Path | None,
) -> dict[str, Any]:
    """Serialize one resolved project with shared desktop result bounds."""
    models: list[dict[str, Any]] = []
    returned_assets = 0
    returned_model_findings = 0
    for model in project.models[:500]:
        row = model.to_dict()
        assets = list(row.get("assets", []))
        findings = list(row.get("findings", []))
        asset_limit = max(0, _MAX_ENTRIES - returned_assets)
        finding_limit = max(0, _MAX_FINDINGS - returned_model_findings)
        row["assets"] = assets[:asset_limit]
        row["findings"] = findings[:finding_limit]
        row["asset_count"] = len(assets)
        row["finding_count"] = len(findings)
        row["assets_truncated"] = len(assets) > asset_limit
        row["findings_truncated"] = len(findings) > finding_limit
        returned_assets += len(row["assets"])
        returned_model_findings += len(row["findings"])
        models.append(row)

    all_assets = sum(len(model.assets) for model in project.models)
    all_model_findings = sum(len(model.findings) for model in project.models)
    return {
        "kind": "vehicle_project_inspection",
        "operation": "inspect_vehicle_project",
        "source": str(project.source),
        "source_kind": project.source_kind,
        "gta_path": str(gta_path) if gta_path is not None else None,
        "edition": project.edition,
        "inventory_fingerprint": project.inventory_fingerprint,
        "models": models,
        "findings": [
            asdict(item) for item in project.findings[:_MAX_FINDINGS]
        ],
        "axle_configurations": list(project.axle_configurations[:500]),
        "model_count": len(project.models),
        "returned_model_count": len(models),
        "asset_count": all_assets,
        "returned_asset_count": returned_assets,
        "previewable_count": sum(item.ready_for_preview for item in project.models),
        "complete_count": sum(item.complete for item in project.models),
        "error_count": project.error_count,
        "warning_count": project.warning_count,
        "model_finding_count": all_model_findings,
        "truncated": (
            len(project.models) > len(models)
            or all_assets > returned_assets
            or all_model_findings > returned_model_findings
            or len(project.findings) > _MAX_FINDINGS
            or len(project.axle_configurations) > 500
        ),
        "read_only": True,
        "package_write_performed": False,
        "game_write_performed": False,
    }


def _inspect_vehicle_project(payload: object) -> tuple[str, dict[str, Any]]:
    """Resolve one bounded, read-only vehicle project through Python services."""
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_vehicle_project payload must be an object")
    raw_source = payload.get("source")
    if (
        not isinstance(raw_source, str)
        or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError(
            "inspect_vehicle_project requires a source path", risk="read_only",
        )
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle project source was not found: {exc}", risk="read_only",
        ) from exc
    if not source.is_file() and not source.is_dir():
        raise ProtocolError(
            "vehicle project source is not a file or directory", risk="read_only",
        )

    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str)
        or not raw_game.strip()
        or "\0" in raw_game
    ):
        raise ProtocolError("gta_path must be a valid path string", risk="read_only")
    try:
        gta_path = (
            Path(raw_game).expanduser().resolve(strict=True)
            if isinstance(raw_game, str) else None
        )
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk="read_only") from exc
    if gta_path is not None and not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk="read_only")

    raw_edition = payload.get("edition")
    if raw_edition is not None and (
        not isinstance(raw_edition, str)
        or raw_edition.casefold() not in {"legacy", "enhanced"}
    ):
        raise ProtocolError("edition must be Legacy or Enhanced", risk="read_only")

    from allin1_sdk.detector import detect_gta_path
    from allin1_sdk.paths import gta_root_containing, project_root
    from allin1_sdk.vehicle_project import VehicleProjectResolver

    if gta_path is None and source.is_file() and source.suffix.casefold() == ".rpf":
        gta_path = gta_root_containing(source) or detect_gta_path()
        if gta_path is None:
            raise ProtocolError(
                "GTA V was not detected; select the matching installation for "
                "direct RPF vehicle inspection.",
                risk="read_only",
            )
    try:
        project = VehicleProjectResolver().inspect(
            source,
            edition=raw_edition.casefold() if isinstance(raw_edition, str) else None,
            project_root=project_root(),
            gta_path=gta_path,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc

    return "read_only", dict(_bounded(
        _vehicle_project_result(project, gta_path=gta_path)
    ))


def _vehicle_authoring_workspace(payload: object, *, risk: str) -> Any:
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle authoring payload must be an object", risk=risk)
    raw_workspace = payload.get("workspace")
    if (
        not isinstance(raw_workspace, str)
        or not raw_workspace.strip()
        or "\0" in raw_workspace
    ):
        raise ProtocolError(
            "vehicle authoring requires a workspace path", risk=risk,
        )
    try:
        workspace_path = Path(raw_workspace).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle authoring workspace was not found: {exc}", risk=risk,
        ) from exc
    if not workspace_path.is_dir():
        raise ProtocolError(
            "vehicle authoring workspace must be a directory", risk=risk,
        )
    from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

    try:
        return VehicleAuthoringWorkspace(workspace_path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc


def _vehicle_authoring_model(payload: object, *, risk: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    if model is None:
        return None
    if (
        not isinstance(model, str) or not model.strip() or "\0" in model
        or len(model) > 96
    ):
        raise ProtocolError("vehicle model must be a bounded identifier", risk=risk)
    return model.strip()


def _vehicle_authoring_history_state(workspace: Any) -> tuple[bool, bool]:
    history_root = workspace.root / "history"
    active: set[str] = set()
    undone: set[str] = set()
    redo: set[str] = set()
    for path in history_root.iterdir():
        if not path.is_dir() or path.is_symlink() or not (path / "edit.json").is_file():
            continue
        name = path.name
        if name.endswith(".undo-recovery"):
            continue
        if name.endswith(".undone"):
            undone.add(name.removesuffix(".undone"))
        elif name.endswith(".redo"):
            redo.add(name.removesuffix(".redo"))
        else:
            active.add(name)
    return bool(active), bool((undone & redo) - active)


def _vehicle_authoring_snapshot(
    workspace: Any, *, model: str | None,
) -> dict[str, Any]:
    project = workspace.inspect()
    selected = project.model(model) if model else (
        project.models[0] if project.models else None
    )
    values = workspace.values(selected.model) if selected is not None else None
    appearance = workspace.appearance(selected.model) if selected is not None else None
    transmission = (
        workspace.transmission_configuration(selected.model)
        if selected is not None else None
    )
    distribution = (
        workspace.distribution(selected.model) if selected is not None else None
    )
    can_undo, can_redo = _vehicle_authoring_history_state(workspace)
    from allin1_sdk.vehicle_authoring import EDITABLE_FIELDS

    return {
        "kind": "vehicle_authoring_session",
        "operation": "inspect_vehicle_authoring_workspace",
        "workspace": str(workspace.root),
        "source": str(workspace.source),
        "original_source": str(workspace.manifest.get("original_source", "")),
        "revision": workspace.revision,
        "selected_model": selected.model if selected is not None else None,
        "editable_fields": list(EDITABLE_FIELDS),
        "values": values.values if values is not None else {},
        "sources": values.sources if values is not None else {},
        "appearance": appearance.to_dict() if appearance is not None else None,
        "transmission": transmission.to_dict() if transmission is not None else None,
        "distribution": distribution.to_dict() if distribution is not None else None,
        "can_undo": can_undo,
        "can_redo": can_redo,
        "project": _vehicle_project_result(project, gta_path=None),
        "read_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }


def _inspect_vehicle_authoring_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk="read_only")
    try:
        result = _vehicle_authoring_snapshot(
            workspace, model=_vehicle_authoring_model(payload, risk="read_only"),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    return "read_only", dict(_bounded(result))


def _vehicle_authoring_create_context(
    payload: object, *, risk: str,
) -> tuple[Path, Path, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError(
            "vehicle authoring workspace payload must be an object", risk=risk,
        )
    raw_source = payload.get("source")
    raw_parent = payload.get("parent")
    name = payload.get("name")
    if (
        not isinstance(raw_source, str) or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError(
            "vehicle authoring workspace requires a source path", risk=risk,
        )
    if (
        not isinstance(raw_parent, str) or not raw_parent.strip()
        or "\0" in raw_parent
    ):
        raise ProtocolError(
            "vehicle authoring workspace requires a destination parent", risk=risk,
        )
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", name) is None
    ):
        raise ProtocolError(
            "vehicle authoring workspace name must contain only letters, numbers, "
            "periods, underscores, and hyphens",
            risk=risk,
        )
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
        parent = Path(raw_parent).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle authoring source or destination was not found: {exc}", risk=risk,
        ) from exc
    if not source.is_file() and not source.is_dir():
        raise ProtocolError(
            "vehicle authoring source must be a file or directory", risk=risk,
        )
    if not parent.is_dir():
        raise ProtocolError(
            "vehicle authoring destination parent must be a directory", risk=risk,
        )
    destination = (parent / name).resolve(strict=False)
    if destination.parent != parent or destination == source or destination.is_relative_to(source):
        raise ProtocolError(
            "vehicle authoring destination must be a new sibling workspace", risk=risk,
        )
    if destination.exists() or destination.is_symlink():
        raise ProtocolError(
            f"vehicle authoring destination already exists: {destination}", risk=risk,
        )

    from allin1_sdk.addon_importer import AddonPackageInspector
    from allin1_sdk.vehicle_project import VehicleProjectResolver

    try:
        scan = AddonPackageInspector().inspect(source)
        project = VehicleProjectResolver.inspect_scan(scan)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    if not project.models:
        raise ProtocolError(
            "Vehicle authoring requires visible vehicles.meta records; extract an "
            "opaque dlc.rpf into a reviewed source tree first.",
            risk=risk,
        )
    result = {
        "kind": "vehicle_authoring_workspace_review",
        "operation": "review_vehicle_authoring_workspace",
        "source": str(source),
        "destination": str(destination),
        "source_kind": scan.source_kind,
        "inventory_fingerprint": project.inventory_fingerprint,
        "model_count": len(project.models),
        "models": [item.model for item in project.models[:500]],
        "copy_bytes": scan.total_bytes,
        "ready": True,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return source, destination, result


def _review_vehicle_authoring_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _source, _destination, result = _vehicle_authoring_create_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _create_vehicle_authoring_workspace(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError(
            "vehicle authoring workspace payload must be an object", risk=risk,
        )
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle authoring workspace creation requires a reviewed SHA-256 digest",
            risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Creating an authoring workspace requires action-time confirmation.",
            risk=risk,
        )
    source, destination, current_review = _vehicle_authoring_create_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The source or destination changed after review; review it again.",
            risk=risk,
        )
    from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

    try:
        workspace = VehicleAuthoringWorkspace.create(source, destination)
        result = _vehicle_authoring_snapshot(
            workspace, model=_vehicle_authoring_model(payload, risk=risk),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "create_vehicle_authoring_workspace",
        "review_sha256": expected_review,
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_edit_context(
    payload: object, *, risk: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle authoring edit must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle authoring edit requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 0:
        raise ProtocolError(
            "vehicle authoring edit requires a non-negative expected revision",
            risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Vehicle authoring revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    updates = payload.get("updates")
    if (
        not isinstance(updates, dict) or not updates or len(updates) > 64
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            or "\0" in key or "\0" in value or len(key) > 128 or len(value) > 4096
            for key, value in updates.items()
        )
    ):
        raise ProtocolError(
            "vehicle authoring updates must contain 1–64 bounded string fields",
            risk=risk,
        )
    try:
        review = workspace.review_update(model, updates)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_edit_review",
        "operation": "review_vehicle_authoring_edit",
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, updates, result


def _review_vehicle_authoring_edit(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _updates, result = _vehicle_authoring_edit_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_edit(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle authoring edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle authoring edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a vehicle authoring edit requires action-time confirmation.",
            risk=risk,
        )
    workspace, updates, current_review = _vehicle_authoring_edit_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or edit changed after review; review it again.",
            risk=risk,
        )
    try:
        applied = workspace.update(current_review["model"], updates)
        result = _vehicle_authoring_snapshot(
            workspace, model=applied.model,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_edit",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_appearance_context(
    payload: object, *, risk: str,
) -> tuple[Any, str, dict[str, Any], dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle appearance edit must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle appearance edit requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) \
            or expected_revision < 0:
        raise ProtocolError(
            "vehicle appearance edit requires a non-negative expected revision",
            risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Vehicle authoring revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    raw_appearance = payload.get("appearance")
    if not isinstance(raw_appearance, dict):
        raise ProtocolError("vehicle appearance must be an object", risk=risk)
    colors = raw_appearance.get("colors")
    kits = raw_appearance.get("kits")
    light_settings = raw_appearance.get("light_settings")
    siren_settings = raw_appearance.get("siren_settings")
    if not isinstance(colors, list) or len(colors) > 64:
        raise ProtocolError("vehicle colors must contain at most 64 entries", risk=risk)
    normalized_colors: list[dict[str, Any]] = []
    for index, color in enumerate(colors):
        if not isinstance(color, dict) or set(color) != {"indices", "liveries"}:
            raise ProtocolError(
                f"vehicle color {index + 1} must contain indices and liveries", risk=risk,
            )
        indices = color.get("indices")
        liveries = color.get("liveries")
        if (
            not isinstance(indices, list) or len(indices) > 32
            or any(not isinstance(value, int) or isinstance(value, bool) for value in indices)
            or not isinstance(liveries, list) or len(liveries) > 32
            or any(not isinstance(value, bool) for value in liveries)
        ):
            raise ProtocolError(
                f"vehicle color {index + 1} has invalid bounded arrays", risk=risk,
            )
        normalized_colors.append({"indices": list(indices), "liveries": list(liveries)})
    if (
        not isinstance(kits, list) or len(kits) > 64
        or any(
            not isinstance(value, str) or not value.strip() or "\0" in value
            or len(value) > 128
            for value in kits
        )
    ):
        raise ProtocolError(
            "vehicle appearance kits must contain at most 64 bounded identifiers",
            risk=risk,
        )
    if any(
        not isinstance(value, str) or "\0" in value or len(value) > 32
        for value in (light_settings, siren_settings)
    ):
        raise ProtocolError(
            "vehicle light and siren settings must be bounded strings", risk=risk,
        )
    appearance = {
        "colors": normalized_colors,
        "kits": [value.strip() for value in kits],
        "light_settings": light_settings,
        "siren_settings": siren_settings,
    }
    try:
        review = workspace.review_appearance(model, **appearance)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_appearance_review",
        "operation": "review_vehicle_authoring_appearance",
        "appearance": appearance,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, model, appearance, result


def _review_vehicle_authoring_appearance(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _model, _appearance, result = _vehicle_authoring_appearance_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_appearance(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle appearance edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle appearance edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a vehicle appearance edit requires action-time confirmation.",
            risk=risk,
        )
    workspace, model, appearance, current_review = \
        _vehicle_authoring_appearance_context(payload, risk=risk)
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or appearance changed after review; review it again.",
            risk=risk,
        )
    try:
        applied = workspace.update_appearance(model, **appearance)
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_appearance",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_revision_context(
    payload: object, *, risk: str, label: str,
) -> tuple[Any, str]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError(f"{label} must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError(f"{label} requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            f"{label} requires a non-negative expected revision", risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Vehicle authoring revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    return workspace, model


def _vehicle_authoring_tuning_identifier(
    value: object, *, label: str, risk: str,
) -> str:
    if (
        not isinstance(value, str) or not value.strip() or "\0" in value
        or len(value) > 128
    ):
        raise ProtocolError(f"{label} must be a bounded identifier", risk=risk)
    return value.strip()


def _vehicle_authoring_tuning_payload(
    workspace: Any, builder: Any,
) -> dict[str, Any]:
    result = builder.to_dict()
    result.update({
        "kind": "vehicle_authoring_tuning",
        "operation": "inspect_vehicle_authoring_tuning",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "read_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    return result


def _inspect_vehicle_authoring_tuning(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "read_only"
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle tuning inspection requires a model", risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle tuning inspection must be an object", risk=risk)
    raw_kit = payload.get("kit_name")
    kit_name = None if raw_kit is None else _vehicle_authoring_tuning_identifier(
        raw_kit, label="vehicle tuning kit", risk=risk,
    )
    try:
        builder = workspace.tuning_builder(model, kit_name)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = _vehicle_authoring_tuning_payload(workspace, builder)
    return risk, dict(_bounded(result))


def _vehicle_authoring_tuning_context(
    payload: object, *, risk: str,
) -> tuple[Any, str, dict[str, Any], dict[str, Any]]:
    workspace, model = _vehicle_authoring_revision_context(
        payload, risk=risk, label="vehicle tuning edit",
    )
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle tuning edit must be an object", risk=risk)
    raw_mutation = payload.get("mutation")
    if not isinstance(raw_mutation, dict):
        raise ProtocolError("vehicle tuning mutation must be an object", risk=risk)
    action = raw_mutation.get("action")
    if action not in {
        "update_kit", "add_entry", "duplicate_entry", "update_entry",
        "remove_entry", "move_entry",
    }:
        raise ProtocolError("vehicle tuning mutation action is unsupported", risk=risk)
    kit_name = _vehicle_authoring_tuning_identifier(
        raw_mutation.get("kit_name"), label="vehicle tuning kit", risk=risk,
    )
    mutation: dict[str, Any] = {"action": action, "kit_name": kit_name}
    try:
        if action == "update_kit":
            allowed = {"action", "kit_name", "kit_type", "livery_names"}
            if set(raw_mutation) - allowed:
                raise ProtocolError("vehicle tuning-kit mutation has unknown fields", risk=risk)
            kit_type = _vehicle_authoring_tuning_identifier(
                raw_mutation.get("kit_type"), label="vehicle tuning kit type", risk=risk,
            )
            liveries = raw_mutation.get("livery_names")
            if (
                not isinstance(liveries, list) or len(liveries) > 64
                or any(
                    not isinstance(value, str) or not value.strip() or "\0" in value
                    or len(value) > 128
                    for value in liveries
                )
            ):
                raise ProtocolError(
                    "vehicle tuning liveries must be bounded identifiers", risk=risk,
                )
            mutation.update({
                "kit_type": kit_type,
                "livery_names": [value.strip() for value in liveries],
            })
            review = workspace.review_tuning_kit(
                model, kit_name, kit_type=kit_type,
                livery_names=mutation["livery_names"],
            )
        else:
            allowed = {
                "action", "kit_name", "collection", "index", "new_index", "values",
            }
            if set(raw_mutation) - allowed:
                raise ProtocolError("vehicle tuning-entry mutation has unknown fields", risk=risk)
            collection = raw_mutation.get("collection")
            if collection not in {"visibleMods", "linkMods", "statMods", "slotNames"}:
                raise ProtocolError("vehicle tuning collection is unsupported", risk=risk)
            mutation["collection"] = collection
            index = raw_mutation.get("index")
            if action in {"duplicate_entry", "update_entry", "remove_entry", "move_entry"}:
                if (
                    not isinstance(index, int) or isinstance(index, bool) or index < 0
                    or index > 10_000
                ):
                    raise ProtocolError("vehicle tuning entry index is invalid", risk=risk)
                mutation["index"] = index
            new_index = raw_mutation.get("new_index")
            if action == "move_entry":
                if (
                    not isinstance(new_index, int) or isinstance(new_index, bool)
                    or new_index < 0 or new_index > 10_000
                ):
                    raise ProtocolError("vehicle tuning destination index is invalid", risk=risk)
                mutation["new_index"] = new_index
            values = raw_mutation.get("values", {})
            if (
                not isinstance(values, dict) or len(values) > 64
                or any(
                    not isinstance(key, str) or not key or "\0" in key or len(key) > 128
                    or not isinstance(value, str) or "\0" in value or len(value) > 4096
                    for key, value in values.items()
                )
            ):
                raise ProtocolError(
                    "vehicle tuning values must be bounded string fields", risk=risk,
                )
            if action in {"add_entry", "update_entry", "duplicate_entry"}:
                mutation["values"] = dict(values)
            internal_action = action.removesuffix("_entry")
            review = workspace.review_tuning_action(
                model, kit_name, collection, internal_action,
                index=mutation.get("index"), new_index=mutation.get("new_index"),
                values=mutation.get("values"),
            )
    except ProtocolError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_tuning_review",
        "operation": "review_vehicle_authoring_tuning",
        "action": action,
        "mutation": mutation,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, model, mutation, result


def _review_vehicle_authoring_tuning(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _model, _mutation, result = _vehicle_authoring_tuning_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_tuning(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle tuning edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle tuning edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a vehicle tuning edit requires action-time confirmation.", risk=risk,
        )
    workspace, model, mutation, current_review = _vehicle_authoring_tuning_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or tuning edit changed after review; review it again.",
            risk=risk,
        )
    action = mutation["action"]
    try:
        if action == "update_kit":
            applied = workspace.update_tuning_kit(
                model, mutation["kit_name"], kit_type=mutation["kit_type"],
                livery_names=mutation["livery_names"],
            )
        elif action == "add_entry":
            applied = workspace.add_tuning_entry(
                model, mutation["kit_name"], mutation["collection"], mutation["values"],
            )
        elif action == "duplicate_entry":
            applied = workspace.add_tuning_entry(
                model, mutation["kit_name"], mutation["collection"],
                mutation.get("values", {}), duplicate_index=mutation["index"],
            )
        elif action == "update_entry":
            applied = workspace.update_tuning_entry(
                model, mutation["kit_name"], mutation["collection"],
                mutation["index"], mutation["values"],
            )
        elif action == "remove_entry":
            applied = workspace.remove_tuning_entry(
                model, mutation["kit_name"], mutation["collection"], mutation["index"],
            )
        else:
            applied = workspace.move_tuning_entry(
                model, mutation["kit_name"], mutation["collection"],
                mutation["index"], mutation["new_index"],
            )
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
        result["tuning_builder"] = _vehicle_authoring_tuning_payload(
            workspace,
            workspace.tuning_builder(applied.model, mutation["kit_name"]),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_tuning",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_light_profile_context(
    payload: object, *, risk: str,
) -> tuple[Any, str, str, dict[str, str], dict[str, Any]]:
    workspace, model = _vehicle_authoring_revision_context(
        payload, risk=risk, label="vehicle light-profile edit",
    )
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle light-profile edit must be an object", risk=risk)
    profile_id = _vehicle_authoring_tuning_identifier(
        payload.get("profile_id"), label="vehicle light profile", risk=risk,
    )
    updates = payload.get("updates")
    if (
        not isinstance(updates, dict) or not updates or len(updates) > 256
        or any(
            not isinstance(key, str) or not key or "\0" in key or len(key) > 256
            or not isinstance(value, str) or "\0" in value or len(value) > 4096
            for key, value in updates.items()
        )
    ):
        raise ProtocolError(
            "vehicle light-profile updates must be bounded string fields", risk=risk,
        )
    normalized_updates = dict(updates)
    try:
        review = workspace.review_light_profile(model, profile_id, normalized_updates)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_light_profile_review",
        "operation": "review_vehicle_authoring_light_profile",
        "profile_id": profile_id,
        "updates": normalized_updates,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, model, profile_id, normalized_updates, result


def _review_vehicle_authoring_light_profile(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _model, _profile_id, _updates, result = \
        _vehicle_authoring_light_profile_context(payload, risk="read_only")
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_light_profile(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle light-profile edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle light-profile edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a light-profile edit requires action-time confirmation.", risk=risk,
        )
    workspace, model, profile_id, updates, current_review = \
        _vehicle_authoring_light_profile_context(payload, risk=risk)
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or light-profile edit changed after review; "
            "review it again.", risk=risk,
        )
    try:
        applied = workspace.update_light_profile(model, profile_id, updates)
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_light_profile",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_axle_skeleton(
    payload: object, *, risk: str,
) -> tuple[Path, tuple[Any, ...], str | None]:
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle axle skeleton payload must be an object", risk=risk)
    raw_path = payload.get("skeleton_xml")
    if (
        not isinstance(raw_path, str) or not raw_path.strip()
        or "\0" in raw_path
    ):
        raise ProtocolError(
            "vehicle axle skeleton requires a CodeWalker XML path", risk=risk,
        )
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle axle skeleton was not found: {exc}", risk=risk,
        ) from exc
    if not path.is_file() or path.suffix.casefold() != ".xml":
        raise ProtocolError(
            "vehicle axle skeleton must be a CodeWalker XML file", risk=risk,
        )
    if not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise ProtocolError(
            "vehicle axle skeleton is empty or exceeds 16 MiB", risk=risk,
        )
    try:
        from allin1_sdk.native_assets import load_native_model_scene

        scene, _metadata, warning = load_native_model_scene(path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    if scene is None:
        raise ProtocolError(
            warning or "CodeWalker XML did not contain a supported vehicle skeleton",
            risk=risk,
        )
    bones = tuple(scene.bones)
    if not bones:
        raise ProtocolError(
            "CodeWalker XML did not contain skeleton bones", risk=risk,
        )
    return path, bones, warning


def _inspect_vehicle_authoring_axle_skeleton(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "read_only"
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle axle skeleton payload must be an object", risk=risk)
    model_name = _vehicle_authoring_model(payload, risk=risk)
    if model_name is None:
        raise ProtocolError("vehicle axle skeleton requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
        or workspace.revision != expected_revision
    ):
        raise ProtocolError(
            "vehicle axle skeleton requires the current non-negative revision",
            risk=risk,
        )
    action = payload.get("action", "validate")
    if action not in {"detect", "validate", "steering", "physical_order", "canonical_order"}:
        raise ProtocolError("unsupported vehicle axle skeleton action", risk=risk)
    skeleton_path, bones, parser_warning = _vehicle_authoring_axle_skeleton(
        payload, risk=risk,
    )
    try:
        from allin1_sdk.axle_configurator import (
            AxleConfiguration,
            EXPORT_FIVEM_RUNTIME,
            apply_intentional_layout_override,
            clear_intentional_layout_override,
            detect_axle_configuration,
            validate_axle_configuration,
        )
        from allin1_sdk.axle_steering_geometry import (
            SteeringGeometryRequest,
            apply_steering_geometry_to_configuration,
            canonical_bone_position_sha256,
            solve_automatic_steering_geometry,
        )

        model = workspace.inspect().model(model_name)
        solution = None
        if action == "detect":
            preset = payload.get("preset")
            if preset is not None and not isinstance(preset, str):
                raise ValueError("Axle preset must be a string")
            export_mode = payload.get("export_mode", "stock_metadata")
            target = payload.get("target", "fivem-legacy")
            if not isinstance(export_mode, str) or not isinstance(target, str):
                raise ValueError("Axle export mode and target must be strings")
            configuration = detect_axle_configuration(
                model.model, bones, preset=preset,
                export_mode=export_mode, target=target,
            )
        else:
            raw_configuration = payload.get("configuration")
            if not isinstance(raw_configuration, dict):
                raise ValueError("Axle skeleton action requires a configuration object")
            configuration = AxleConfiguration.from_dict(raw_configuration)
            if configuration.vehicle_model != model.model.casefold():
                raise ValueError(
                    "Axle configuration model does not match the selected vehicle"
                )
            if action == "steering":
                request = SteeringGeometryRequest.from_dict(payload.get("request"))
                solution = solve_automatic_steering_geometry(
                    configuration, bones, request,
                )
                configuration = apply_steering_geometry_to_configuration(
                    configuration, solution,
                )
                if configuration.steering_calculation is not None:
                    configuration = replace(
                        configuration, export_mode=EXPORT_FIVEM_RUNTIME,
                    )
            elif action == "physical_order":
                raw_pairs = payload.get("physical_bone_pairs")
                if (
                    not isinstance(raw_pairs, list)
                    or any(
                        not isinstance(pair, list)
                        or len(pair) != 2
                        or any(not isinstance(name, str) for name in pair)
                        for pair in raw_pairs
                    )
                ):
                    raise ValueError(
                        "Physical axle order must be an array of left/right bone pairs"
                    )
                configuration = apply_intentional_layout_override(
                    configuration,
                    bones,
                    physical_bone_pairs=tuple(
                        (pair[0], pair[1]) for pair in raw_pairs
                    ),
                    reason=(
                        "Author-confirmed physical order from the Tauri vehicle workbench"
                    ),
                )
            elif action == "canonical_order":
                configuration = clear_intentional_layout_override(configuration)
        findings = validate_axle_configuration(
            configuration, bones,
            asset_names=(item.path for item in model.assets),
        )
        configured_names = {
            name for axle in configuration.axles
            for name in (axle.left_bone, axle.right_bone)
        }
        wheel_bones = [
            {
                "name": bone.name,
                "position": [float(value) for value in bone.position],
            }
            for bone in bones
            if str(bone.name).strip().casefold() in configured_names
        ]
        evidence_sha256 = canonical_bone_position_sha256(configuration, bones)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = {
        "kind": "vehicle_authoring_axle_skeleton",
        "operation": "inspect_vehicle_authoring_axle_skeleton",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "model": model.model,
        "action": action,
        "skeleton_xml": str(skeleton_path),
        "bone_count": len(bones),
        "wheel_bones": wheel_bones,
        "bone_position_sha256": evidence_sha256,
        "configuration": configuration.to_dict(),
        "solution": solution.to_dict() if solution is not None else None,
        "findings": [item.to_dict() for item in findings],
        "warnings": [parser_warning] if parser_warning else [],
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    }
    return risk, dict(_bounded(result))


def _vehicle_authoring_axle_context(
    payload: object, *, risk: str,
) -> tuple[Any, Any, dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle axle edit must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle axle edit requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "vehicle axle edit requires a non-negative expected revision",
            risk=risk,
        )
    raw_configuration = payload.get("configuration")
    if not isinstance(raw_configuration, dict):
        raise ProtocolError(
            "vehicle axle edit requires a configuration object", risk=risk,
        )
    try:
        from allin1_sdk.axle_configurator import AxleConfiguration

        configuration = AxleConfiguration.from_dict(raw_configuration)
        if configuration.vehicle_model != model.casefold():
            raise ValueError("Axle configuration model does not match the selected vehicle")
        bones: tuple[Any, ...] = ()
        if payload.get("skeleton_xml") is not None:
            _path, bones, _warning = _vehicle_authoring_axle_skeleton(
                payload, risk=risk,
            )
        review = workspace.review_axle_configuration(
            configuration, bones=bones, expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_axle_review",
        "operation": "review_vehicle_authoring_axles",
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, configuration, result


def _review_vehicle_authoring_axles(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _configuration, result = _vehicle_authoring_axle_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_axles(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle axle edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle axle edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying an axle configuration requires action-time confirmation.",
            risk=risk,
        )
    workspace, configuration, current_review = _vehicle_authoring_axle_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or axle configuration changed after review; "
            "review it again.",
            risk=risk,
        )
    try:
        bones: tuple[Any, ...] = ()
        if payload.get("skeleton_xml") is not None:
            _path, bones, _warning = _vehicle_authoring_axle_skeleton(
                payload, risk=risk,
            )
        applied = workspace.set_axle_configuration(
            configuration, bones=bones,
            expected_revision=current_review["revision"],
        )
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_axles",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "warnings": list(applied.warnings),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_transmission_context(
    payload: object, *, risk: str,
) -> tuple[Any, Any, dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle transmission edit must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle transmission edit requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "vehicle transmission edit requires a non-negative expected revision",
            risk=risk,
        )
    raw_configuration = payload.get("configuration")
    if not isinstance(raw_configuration, dict):
        raise ProtocolError(
            "vehicle transmission edit requires a configuration object", risk=risk,
        )
    try:
        from allin1_sdk.vehicle_authoring import VehicleTransmissionConfiguration

        configuration = VehicleTransmissionConfiguration.from_dict(raw_configuration)
        if configuration.vehicle_model != model.casefold():
            raise ValueError(
                "Transmission configuration model does not match the selected vehicle"
            )
        review = workspace.review_transmission_configuration(
            configuration, expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_transmission_review",
        "operation": "review_vehicle_authoring_transmission",
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, configuration, result


def _review_vehicle_authoring_transmission(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _configuration, result = _vehicle_authoring_transmission_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_transmission(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle transmission edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle transmission edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying a transmission configuration requires action-time confirmation.",
            risk=risk,
        )
    workspace, configuration, current_review = \
        _vehicle_authoring_transmission_context(payload, risk=risk)
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or transmission configuration changed after "
            "review; review it again.", risk=risk,
        )
    try:
        applied = workspace.set_transmission_configuration(
            configuration, expected_revision=current_review["revision"],
        )
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_transmission",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "warnings": list(applied.warnings),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_authoring_distribution_context(
    payload: object, *, risk: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle distribution edit must be an object", risk=risk)
    model = _vehicle_authoring_model(payload, risk=risk)
    if model is None:
        raise ProtocolError("vehicle distribution edit requires a model", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "vehicle distribution edit requires a non-negative expected revision",
            risk=risk,
        )
    updates = payload.get("updates")
    if (
        not isinstance(updates, dict) or not updates or len(updates) > 11
        or any(
            not isinstance(key, str) or len(key) > 64
            or isinstance(value, (dict, list, tuple, set))
            or (isinstance(value, str) and ("\0" in value or len(value) > 4096))
            for key, value in updates.items()
        )
    ):
        raise ProtocolError(
            "vehicle distribution updates must contain bounded scalar fields",
            risk=risk,
        )
    try:
        review = workspace.review_distribution(
            model, updates, expected_revision=expected_revision,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = review.to_dict()
    result.update({
        "kind": "vehicle_authoring_distribution_review",
        "operation": "review_vehicle_authoring_distribution",
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return workspace, updates, result


def _review_vehicle_authoring_distribution(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _workspace, _updates, result = _vehicle_authoring_distribution_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_authoring_distribution(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle distribution edit must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle distribution edit requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Applying vehicle distribution requires action-time confirmation.", risk=risk,
        )
    workspace, updates, current_review = _vehicle_authoring_distribution_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace revision or distribution values changed after review; "
            "review them again.", risk=risk,
        )
    try:
        applied = workspace.apply_distribution(
            current_review["model"], updates,
            expected_revision=current_review["revision"],
        )
        result = _vehicle_authoring_snapshot(workspace, model=applied.model)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_distribution",
        "review_sha256": expected_review,
        "changes": list(applied.changes),
        "history": str(applied.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _vehicle_package_build_context(
    payload: object, *, risk: str,
) -> tuple[Any, Any, dict[str, Any]]:
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle package build must be an object", risk=risk)
    expected_revision = payload.get("expected_revision")
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 0
    ):
        raise ProtocolError(
            "vehicle package build requires a non-negative expected revision",
            risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Vehicle authoring revision changed (expected {expected_revision}, "
            f"found {workspace.revision})", risk=risk,
        )
    raw_destination = payload.get("destination")
    if (
        not isinstance(raw_destination, str) or not raw_destination.strip()
        or "\0" in raw_destination or len(raw_destination) > 4096
    ):
        raise ProtocolError("vehicle package build requires a destination", risk=risk)
    raw_target = Path(raw_destination).expanduser()
    if not raw_target.name or len(raw_target.name) > 128:
        raise ProtocolError("vehicle package destination name is invalid", risk=risk)
    try:
        parent = raw_target.parent.resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"vehicle package destination parent was not found: {exc}", risk=risk,
        ) from exc
    if not parent.is_dir() or parent.is_symlink():
        raise ProtocolError(
            "vehicle package destination parent must be a real directory", risk=risk,
        )
    destination = (parent / raw_target.name).resolve(strict=False)
    if destination.exists() or destination.is_symlink():
        raise ProtocolError(
            f"vehicle package destination already exists: {destination}", risk=risk,
        )
    if destination == workspace.root or destination.is_relative_to(workspace.root):
        raise ProtocolError(
            "vehicle package destination must be outside the authoring workspace",
            risk=risk,
        )

    def optional_text(key: str, maximum: int) -> str | None:
        value = payload.get(key)
        if value is None or value == "":
            return None
        if (
            not isinstance(value, str) or len(value) > maximum
            or "\0" in value or "\r" in value or "\n" in value
        ):
            raise ProtocolError(f"vehicle package {key} is invalid", risk=risk)
        return value.strip()

    pack_name = optional_text("pack_name", 64)
    mod_id = optional_text("mod_id", 64)
    name = optional_text("name", 128)
    version = optional_text("version", 64) or "1.0.0"
    raw_editions = payload.get("editions", ["legacy", "enhanced"])
    if (
        not isinstance(raw_editions, list) or not 1 <= len(raw_editions) <= 2
        or any(not isinstance(item, str) for item in raw_editions)
    ):
        raise ProtocolError(
            "vehicle package editions must select Legacy and/or Enhanced", risk=risk,
        )
    editions = tuple(item.casefold() for item in raw_editions)
    raw_gta = payload.get("gta_path")
    gta_path: Path | None = None
    if raw_gta not in (None, ""):
        if not isinstance(raw_gta, str) or "\0" in raw_gta or len(raw_gta) > 4096:
            raise ProtocolError("vehicle package GTA path is invalid", risk=risk)
        try:
            gta_path = Path(raw_gta).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ProtocolError(f"vehicle package GTA path was not found: {exc}", risk=risk) from exc
        if not gta_path.is_dir():
            raise ProtocolError("vehicle package GTA path must be a directory", risk=risk)
        if destination == gta_path or destination.is_relative_to(gta_path):
            raise ProtocolError(
                "vehicle package output cannot be written inside GTA V", risk=risk,
            )
    try:
        from allin1_sdk.paths import project_root
        from allin1_sdk.vehicle_package import VehicleAddonPackageBuilder

        builder = VehicleAddonPackageBuilder(project_root(), gta_path)
        review = builder.review(
            workspace.root, destination, pack_name=pack_name, mod_id=mod_id,
            name=name, version=version, editions=editions,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    profiles = review.authoring_profiles
    axle_profiles = profiles.get("axle_configurations", {}) if profiles else {}
    transmission_profiles = (
        profiles.get("transmission_configurations", {}) if profiles else {}
    )
    selective_axles = sum(
        isinstance(item, dict) and item.get("export_mode") == "selective_runtime"
        for item in axle_profiles.values()
    ) if isinstance(axle_profiles, dict) else 0
    warnings: list[str] = []
    if transmission_profiles:
        warnings.append(
            "Transmission ratios are preserved in vehicle-profiles.json; runtime "
            "activation remains a separate integration step."
        )
    if selective_axles:
        warnings.append(
            f"{selective_axles} selective-runtime axle profile(s) are preserved, "
            "but require a compatible axle controller at install time."
        )
    catalog_vehicles = review.catalog.get("vehicles", [])
    result = review.to_dict()
    result.update({
        "kind": "vehicle_package_build_review",
        "operation": "review_vehicle_package_build",
        "workspace": str(workspace.root),
        "revision": workspace.revision,
        "ready": True,
        "checks": [
            {"key": "workspace", "label": "Workspace revision", "status": "ready", "detail": f"Revision {workspace.revision} is current"},
            {"key": "source", "label": "DLC source", "status": "ready", "detail": review.source_mode.replace("_", " ")},
            {"key": "distribution", "label": "Distribution catalog", "status": "ready", "detail": f"{len(catalog_vehicles)} listed vehicle(s)"},
            {"key": "profiles", "label": "Authoring profiles", "status": "ready", "detail": f"{len(axle_profiles)} axle · {len(transmission_profiles)} transmission"},
            {"key": "destination", "label": "Output boundary", "status": "ready", "detail": "New folder outside the workspace and GTA V"},
        ],
        "warnings": warnings,
        "review_only": True,
        "workspace_write_performed": False,
        "package_write_performed": False,
        "game_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return builder, review, result


def _review_vehicle_package_build(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _builder, _review, result = _vehicle_package_build_context(
        payload, risk="read_only",
    )
    return "read_only", dict(_bounded(result))


def _apply_vehicle_package_build(
    payload: object, *, allow_package_writes: bool,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not allow_package_writes:
        raise ProtocolError(
            "Vehicle package build authority is disabled for this desktop process.",
            risk=risk,
        )
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle package build must be an object", risk=risk)
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "vehicle package build requires a reviewed SHA-256 digest", risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Building a vehicle package requires action-time confirmation.", risk=risk,
        )
    builder, review, current_review = _vehicle_package_build_context(
        payload, risk=risk,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The workspace, build settings, or destination changed after review; "
            "review the package again.", risk=risk,
        )
    try:
        built = builder.build(
            current_review["workspace"], review.destination,
            pack_name=review.pack_name, mod_id=review.mod_id, name=review.name,
            version=review.version, editions=review.editions,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = {
        "kind": "vehicle_package_build_result",
        "operation": "apply_vehicle_package_build",
        "review_sha256": expected_review,
        "package": built.to_dict(),
        "warnings": current_review["warnings"],
        "read_only": False,
        "workspace_write_performed": False,
        "package_write_performed": True,
        "game_write_performed": False,
    }
    return risk, dict(_bounded(result))


def _apply_vehicle_authoring_history(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    workspace = _vehicle_authoring_workspace(payload, risk=risk)
    if not isinstance(payload, dict):
        raise ProtocolError("vehicle authoring history must be an object", risk=risk)
    direction = payload.get("direction")
    if direction not in {"undo", "redo"}:
        raise ProtocolError(
            "vehicle authoring history direction must be undo or redo", risk=risk,
        )
    expected_revision = payload.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 0:
        raise ProtocolError(
            "vehicle authoring history requires a non-negative expected revision",
            risk=risk,
        )
    if workspace.revision != expected_revision:
        raise ProtocolError(
            f"Vehicle authoring revision changed (expected {expected_revision}, "
            f"found {workspace.revision})",
            risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            f"Vehicle authoring {direction} requires action-time confirmation.",
            risk=risk,
        )
    try:
        changed = workspace.undo() if direction == "undo" else workspace.redo()
        requested_model = _vehicle_authoring_model(payload, risk=risk) or changed.model
        result = _vehicle_authoring_snapshot(
            workspace, model=requested_model,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result.update({
        "operation": "apply_vehicle_authoring_history",
        "direction": direction,
        "changes": list(changed.changes),
        "history": str(changed.history),
        "read_only": False,
        "workspace_write_performed": True,
        "package_write_performed": True,
    })
    return risk, dict(_bounded(result))


def _recipe_readiness(plan: object) -> tuple[str, str]:
    if bool(getattr(plan, "rpf_recipe_compilable", False)):
        return "existing_rpf_compile_ready", "EXISTING RPF COMPILE READY"
    if (
        bool(getattr(plan, "translatable", False))
        and bool(getattr(plan, "created_archive_operations", ()))
    ):
        return "new_archive_build_ready", "NEW ARCHIVE BUILD READY"
    if bool(getattr(plan, "managed_exportable", False)):
        return "managed_package_ready", "MANAGED PACKAGE READY"
    if bool(getattr(plan, "translatable", False)):
        return "atomic_rpf_export_ready", "ATOMIC RPF EXPORT READY"
    return "manual_review_required", "MANUAL REVIEW REQUIRED"


def _inspect_recipe(payload: object) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError("inspect_recipe payload must be an object")
    raw_source = payload.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
        raise ProtocolError("inspect_recipe requires a source path")
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"recipe source was not found: {exc}", risk="read_only",
        ) from exc
    if source.is_file() and source.suffix.casefold() not in {".oiv", ".zip"}:
        raise ProtocolError(
            "recipe source must be an OIV/ZIP package or unpacked directory",
            risk="read_only",
        )
    if not source.is_file() and not source.is_dir():
        raise ProtocolError("recipe source is not a file or directory", risk="read_only")

    from allin1_sdk.oiv_workbench import OivWorkbench

    try:
        plan = OivWorkbench().inspect(source)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    readiness, readiness_label = _recipe_readiness(plan)
    result = plan.to_dict()
    result.update({
        "kind": "recipe_plan",
        "readiness": readiness,
        "readiness_label": readiness_label,
        "operation_count": len(plan.operations),
        "error_count": sum(item.severity == "error" for item in plan.findings),
        "warning_count": sum(item.severity == "warning" for item in plan.findings),
    })
    return "read_only", dict(_bounded(result))


def _inspect_package_receipts(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    """List managed installs and optionally verify one receipt in place."""
    if not isinstance(payload, dict):
        raise ProtocolError(
            "inspect_package_receipts payload must be an object",
            risk="read_only",
        )
    raw_gta_path = payload.get("gta_path")
    if (
        not isinstance(raw_gta_path, str)
        or not raw_gta_path.strip()
        or "\0" in raw_gta_path
    ):
        raise ProtocolError(
            "inspect_package_receipts requires a GTA V folder",
            risk="read_only",
        )
    selected_value = payload.get("selected_id")
    if selected_value is not None and (
        not isinstance(selected_value, str)
        or not selected_value.strip()
        or "\0" in selected_value
        or len(selected_value) > 64
    ):
        raise ProtocolError(
            "selected_id must be a bounded package id",
            risk="read_only",
        )

    from allin1_sdk.mods import ModIntegrationService

    try:
        service = ModIntegrationService(raw_gta_path)
        statuses = service.list_installed()
        packages = [{
            "mod_id": item.mod_id,
            "name": item.name,
            "version": item.version,
            "mod_type": item.mod_type,
            "enabled": item.enabled,
        } for item in statuses]
        selected_id = (
            selected_value.strip().casefold()
            if isinstance(selected_value, str) else None
        )
        receipt = service.inspect_receipt(selected_id) if selected_id else None
        verification = service.verify_ownership(selected_id) if selected_id else None
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc

    checks = verification.get("checks", []) if verification else []
    issues = verification.get("issues", []) if verification else []
    result = {
        "kind": "package_receipt_inventory",
        "operation": "inspect_package_receipts",
        "gta_path": str(service.gta_path),
        "edition": service.edition,
        "receipt_root": str(service.state_root),
        "packages": packages,
        "selected_id": selected_id,
        "receipt": receipt,
        "verification": verification,
        "package_count": len(packages),
        "enabled_count": sum(item["enabled"] for item in packages),
        "check_count": len(checks),
        "issue_count": len(issues),
        "read_only": True,
        "game_write_performed": False,
    }
    return "read_only", dict(_bounded(result))


def _lifecycle_request(
    payload: object, *, risk: str,
) -> tuple[str, "ModIntegrationService"]:
    if not isinstance(payload, dict):
        raise ProtocolError(
            "package lifecycle payload must be an object", risk=risk,
        )
    action = payload.get("action")
    raw_gta_path = payload.get("gta_path")
    if action not in {"install", "uninstall", "enable", "disable"}:
        raise ProtocolError(
            "package lifecycle action must be install, uninstall, enable, or disable",
            risk=risk,
        )
    if (
        not isinstance(raw_gta_path, str)
        or not raw_gta_path.strip()
        or "\0" in raw_gta_path
    ):
        raise ProtocolError(
            "package lifecycle operation requires a GTA V folder", risk=risk,
        )

    from allin1_sdk.mods import ModIntegrationService

    try:
        return action, ModIntegrationService(raw_gta_path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc


def _lifecycle_source(payload: dict[str, Any], *, risk: str) -> Path:
    raw_source = payload.get("source")
    if (
        not isinstance(raw_source, str)
        or not raw_source.strip()
        or "\0" in raw_source
    ):
        raise ProtocolError(
            "install lifecycle operation requires a package source", risk=risk,
        )
    try:
        return Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(f"Package source was not found: {exc}", risk=risk) from exc


def _lifecycle_mod_id(payload: dict[str, Any], *, risk: str) -> str:
    raw_mod_id = payload.get("mod_id")
    if (
        not isinstance(raw_mod_id, str)
        or not raw_mod_id.strip()
        or "\0" in raw_mod_id
        or len(raw_mod_id) > 64
    ):
        raise ProtocolError(
            "package lifecycle operation requires a bounded package id", risk=risk,
        )
    return raw_mod_id.strip().casefold()


def _lifecycle_review_result(
    *, action: str, service: "ModIntegrationService", source: str | None,
    review: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "kind": "package_lifecycle_review",
        "operation": "review_package_lifecycle",
        "source": source,
        "gta_path": str(service.gta_path),
        **review,
        "review_only": True,
        "game_write_required": True,
        "game_write_performed": False,
    }
    digest_source = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return dict(_bounded(result))


def _review_package_lifecycle(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    """Review one package lifecycle action without changing GTA V."""
    action, service = _lifecycle_request(payload, risk="read_only")
    assert isinstance(payload, dict)

    from allin1_sdk.mods import open_mod_package

    try:
        source: str | None = None
        if action == "install":
            selected = _lifecycle_source(payload, risk="read_only")
            source = str(selected)
            with open_mod_package(selected) as manifest:
                review = service.review_install(manifest)
        elif action == "uninstall":
            review = service.review_uninstall(
                _lifecycle_mod_id(payload, risk="read_only")
            )
        else:
            review = service.review_enabled_state(
                _lifecycle_mod_id(payload, risk="read_only"),
                action == "enable",
            )
    except ProtocolError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc

    return "read_only", _lifecycle_review_result(
        action=action, service=service, source=source, review=review,
    )


def _lifecycle_audit(
    payload: object, *, audit_path: Path | None, allowed: bool,
    completed: bool, message: str | None = None,
) -> None:
    values = payload if isinstance(payload, dict) else {}
    _audit({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": "desktop-package-lifecycle",
        "command": "apply-package-lifecycle",
        "action": values.get("action"),
        "gta_path": values.get("gta_path"),
        "package_id": values.get("confirmation_id") or values.get("mod_id"),
        "review_sha256": values.get("review_sha256"),
        "risk": "game_write",
        "allowed": allowed,
        "completed": completed,
        "error": message,
    }, audit_path)


def _apply_package_lifecycle_inner(
    payload: object, *, allow_package_writes: bool,
) -> tuple[str, dict[str, Any]]:
    risk = "game_write"
    if not allow_package_writes:
        raise ProtocolError(
            "Package lifecycle writes are disabled by the desktop process owner.",
            risk=risk,
        )
    if not isinstance(payload, dict):
        raise ProtocolError("package lifecycle payload must be an object", risk=risk)
    if payload.get("game_write_confirmed") is not True:
        raise ProtocolError(
            "Package lifecycle writes require explicit action-time confirmation.",
            risk=risk,
        )
    expected_digest = payload.get("review_sha256")
    if (
        not isinstance(expected_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
    ):
        raise ProtocolError(
            "Package lifecycle writes require a valid review digest.", risk=risk,
        )

    action, service = _lifecycle_request(payload, risk=risk)
    from allin1_sdk.mods import open_mod_package
    from allin1_sdk.rpf_tools import _running_gta_processes

    selected: Path | None = None
    manifest_context = None
    if action == "install":
        selected = _lifecycle_source(payload, risk=risk)
        manifest_context = open_mod_package(selected)

    try:
        if manifest_context is not None:
            with manifest_context as manifest:
                review = service.review_install(manifest)
                current = _lifecycle_review_result(
                    action=action, service=service, source=str(selected), review=review,
                )
                package_id = str(review["package"]["id"])
                if current["review_sha256"] != expected_digest:
                    raise ProtocolError(
                        "Package or installation state changed after review. Review it again.",
                        risk=risk,
                        details={
                            "expected_review_sha256": expected_digest,
                            "current_review_sha256": current["review_sha256"],
                        },
                    )
                if not review.get("ready"):
                    raise ProtocolError(
                        "Package lifecycle preflight is blocked.", risk=risk,
                        details={"findings": review.get("findings", [])},
                    )
                if review.get("replacing") and payload.get("replace_confirmed") is not True:
                    raise ProtocolError(
                        "Replacing an installed package requires explicit confirmation.",
                        risk=risk,
                    )
                if str(payload.get("confirmation_id", "")).casefold() != package_id:
                    raise ProtocolError(
                        "Action-time package confirmation does not match the reviewed package.",
                        risk=risk,
                    )
                running = _running_gta_processes()
                if running:
                    raise ProtocolError(
                        "Close GTA V before changing a package: " + ", ".join(running),
                        risk=risk,
                    )
                status = service.install(manifest)
                receipt = service._read_receipt(status.mod_id)
                verification = service.verify_ownership(status.mod_id)
                files = receipt.get("files", [])
                rollback = {
                    "receipt_written": service._receipt_path(status.mod_id).is_file(),
                    "ownership_verified": verification.get("ownership_verified", False),
                    "backup_count": sum(bool(item.get("backup")) for item in files),
                    "rpf_entry_count": len(receipt.get("rpf_entries", [])),
                }
                package = {
                    "id": status.mod_id, "name": status.name,
                    "version": status.version, "type": status.mod_type,
                }
                postcondition = {
                    "installed": status.installed,
                    "enabled": status.enabled,
                    "ownership": verification,
                }
        elif action == "uninstall":
            package_id = _lifecycle_mod_id(payload, risk=risk)
            review = service.review_uninstall(package_id)
            current = _lifecycle_review_result(
                action=action, service=service, source=None, review=review,
            )
            if current["review_sha256"] != expected_digest:
                raise ProtocolError(
                    "Package or installation state changed after review. Review it again.",
                    risk=risk,
                    details={
                        "expected_review_sha256": expected_digest,
                        "current_review_sha256": current["review_sha256"],
                    },
                )
            if not review.get("ready"):
                raise ProtocolError(
                    "Package lifecycle preflight is blocked.", risk=risk,
                    details={"findings": review.get("findings", [])},
                )
            if str(payload.get("confirmation_id", "")).casefold() != package_id:
                raise ProtocolError(
                    "Action-time package confirmation does not match the reviewed package.",
                    risk=risk,
                )
            running = _running_gta_processes()
            if running:
                raise ProtocolError(
                    "Close GTA V before changing a package: " + ", ".join(running),
                    risk=risk,
                )
            package = dict(review["package"])
            service.uninstall(package_id)
            operations = review.get("operations", [])
            rollback = {
                "receipt_removed": not service._receipt_path(package_id).exists(),
                "restored_backup_count": sum(
                    item.get("disposition") == "restore_backup" for item in operations
                ),
                "removed_payload_count": sum(
                    item.get("disposition") == "remove" for item in operations
                ),
                "extension_registry_rebuilt": True,
            }
            postcondition = {
                "installed": False,
                "receipt_present": service._receipt_path(package_id).exists(),
            }
        else:
            package_id = _lifecycle_mod_id(payload, risk=risk)
            target_enabled = action == "enable"
            review = service.review_enabled_state(package_id, target_enabled)
            current = _lifecycle_review_result(
                action=action, service=service, source=None, review=review,
            )
            if current["review_sha256"] != expected_digest:
                raise ProtocolError(
                    "Package or installation state changed after review. Review it again.",
                    risk=risk,
                    details={
                        "expected_review_sha256": expected_digest,
                        "current_review_sha256": current["review_sha256"],
                    },
                )
            if not review.get("ready"):
                raise ProtocolError(
                    "Package lifecycle preflight is blocked.", risk=risk,
                    details={"findings": review.get("findings", [])},
                )
            if str(payload.get("confirmation_id", "")).casefold() != package_id:
                raise ProtocolError(
                    "Action-time package confirmation does not match the reviewed package.",
                    risk=risk,
                )
            running = _running_gta_processes()
            if running:
                raise ProtocolError(
                    "Close GTA V before changing a package: " + ", ".join(running),
                    risk=risk,
                )
            status = service.set_enabled(package_id, target_enabled)
            receipt = service.inspect_receipt(package_id)
            verification = service.verify_ownership(package_id)
            operations = review.get("operations", [])
            package = {
                "id": status.mod_id, "name": status.name,
                "version": status.version, "type": status.mod_type,
            }
            rollback = {
                "receipt_state_updated": receipt.get("enabled") is target_enabled,
                "ownership_verified": verification.get("ownership_verified", False),
                "loose_move_count": sum(
                    item.get("kind") == "file" for item in operations
                ),
                "rpf_entry_count": sum(
                    item.get("kind") == "rpf_entry" for item in operations
                ),
                "dlc_registration_count": sum(
                    item.get("kind") == "dlc_registration" for item in operations
                ),
                "extension_registry_rebuilt": True,
            }
            postcondition = {
                "installed": status.installed,
                "enabled": status.enabled,
                "ownership": verification,
            }
    except ProtocolError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc

    result = {
        "kind": "package_lifecycle_execution",
        "operation": "apply_package_lifecycle",
        "action": action,
        "status": {
            "install": "installed", "uninstall": "uninstalled",
            "enable": "enabled", "disable": "disabled",
        }[action],
        "source": str(selected) if selected is not None else None,
        "gta_path": str(service.gta_path),
        "package": package,
        "review_sha256": expected_digest,
        "process_check": {"gta_closed": True, "running_processes": []},
        "postcondition": postcondition,
        "rollback": rollback,
        "game_write_confirmed": True,
        "game_write_performed": True,
    }
    return risk, dict(_bounded(result))


def _apply_package_lifecycle(
    payload: object, *, allow_package_writes: bool, audit_path: Path | None,
) -> tuple[str, dict[str, Any]]:
    try:
        result = _apply_package_lifecycle_inner(
            payload, allow_package_writes=allow_package_writes,
        )
    except ProtocolError as exc:
        _lifecycle_audit(
            payload, audit_path=audit_path, allowed=False, completed=False,
            message=str(exc),
        )
        raise
    _lifecycle_audit(
        payload, audit_path=audit_path, allowed=True, completed=True,
    )
    return result


def _inspect_vehicle_quick_import(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError(
            "inspect_vehicle_quick_import payload must be an object"
        )
    raw_source = payload.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
        raise ProtocolError(
            "inspect_vehicle_quick_import requires a source path"
        )
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"quick-import source was not found: {exc}", risk="read_only",
        ) from exc
    if not source.is_file() and not source.is_dir():
        raise ProtocolError(
            "quick-import source is not a file or directory", risk="read_only",
        )

    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game
    ):
        raise ProtocolError("gta_path must be a valid path string", risk="read_only")
    try:
        if raw_game is not None:
            gta_path = Path(raw_game).expanduser().resolve(strict=True)
        else:
            from allin1_sdk.detector import detect_gta_path

            gta_path = detect_gta_path()
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk="read_only") from exc
    if gta_path is None:
        raise ProtocolError(
            "GTA V was not detected; select the matching installation.",
            risk="read_only",
        )
    if not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk="read_only")

    preferred = payload.get("preferred_edition")
    if preferred is not None and (
        not isinstance(preferred, str)
        or preferred.casefold() not in {"legacy", "enhanced"}
    ):
        raise ProtocolError(
            "preferred_edition must be Legacy or Enhanced", risk="read_only",
        )

    from allin1_sdk.paths import project_root
    from allin1_sdk.vehicle_quick_import import VehicleQuickImportService

    try:
        inspection = VehicleQuickImportService(project_root(), gta_path).inspect(
            source,
            preferred_edition=(preferred.casefold() if preferred else None),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    result = inspection.to_dict()
    result.update({
        "kind": "vehicle_quick_import_inspection",
        "branch_count": len(inspection.available_editions),
        "vehicle_count": len(result.get("vehicles", [])),
        "game_write_performed": False,
        "package_write_performed": False,
    })
    return "read_only", dict(_bounded(result))


def _vehicle_quick_import_review_context(
    payload: object, *, risk: str, include_destination: bool = True,
) -> tuple[Any, Any, Path | None, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProtocolError(
            "vehicle quick-import payload must be an object", risk=risk,
        )
    raw_source = payload.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip() or "\0" in raw_source:
        raise ProtocolError(
            "vehicle quick-import requires a source path", risk=risk,
        )
    try:
        source = Path(raw_source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ProtocolError(
            f"quick-import source was not found: {exc}", risk=risk,
        ) from exc
    if not source.is_file() and not source.is_dir():
        raise ProtocolError(
            "quick-import source is not a file or directory", risk=risk,
        )

    raw_game = payload.get("gta_path")
    if raw_game is not None and (
        not isinstance(raw_game, str) or not raw_game.strip() or "\0" in raw_game
    ):
        raise ProtocolError("gta_path must be a valid path string", risk=risk)
    try:
        if raw_game is not None:
            gta_path = Path(raw_game).expanduser().resolve(strict=True)
        else:
            from allin1_sdk.detector import detect_gta_path

            gta_path = detect_gta_path()
    except OSError as exc:
        raise ProtocolError(f"GTA path was not found: {exc}", risk=risk) from exc
    if gta_path is None:
        raise ProtocolError(
            "GTA V was not detected; select the matching installation.",
            risk=risk,
        )
    if not gta_path.is_dir():
        raise ProtocolError("GTA path must be a directory", risk=risk)

    edition = payload.get("edition")
    if not isinstance(edition, str) or edition.casefold() not in {"legacy", "enhanced"}:
        raise ProtocolError(
            "edition must be Legacy or Enhanced", risk=risk,
        )
    edition = edition.casefold()
    identity: dict[str, str] = {}
    for key in ("package_id", "name", "version"):
        value = payload.get(key)
        if value is None:
            continue
        if (
            not isinstance(value, str) or not value.strip() or "\0" in value
            or len(value) > 128
        ):
            raise ProtocolError(
                f"{key} must be a non-empty string of at most 128 characters",
                risk=risk,
            )
        identity[key] = value.strip()
    updates = payload.get("updates", {})
    if (
        not isinstance(updates, dict) or len(updates) > _MAX_ENTRIES
        or any(not isinstance(key, str) or not isinstance(value, dict)
               for key, value in updates.items())
    ):
        raise ProtocolError(
            "updates must be an object containing bounded per-model objects",
            risk=risk,
        )

    from allin1_sdk.paths import project_root
    from allin1_sdk.vehicle_quick_import import VehicleQuickImportService

    service = VehicleQuickImportService(project_root(), gta_path)
    try:
        inspection = service.inspect(source, preferred_edition=edition)
        review = service.plan(
            inspection,
            edition=edition,
            package_id=identity.get("package_id"),
            name=identity.get("name"),
            version=identity.get("version", "1.0.0"),
        )
        if updates:
            review = service.customize(review.plan, updates)
        destination = service.library_destination(review.plan) if include_destination else None
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc

    if destination is None:
        return service, review, None, {}
    exists = destination.exists() or destination.is_symlink()
    destination_review: dict[str, Any] = {
        "state": "new",
        "exists": exists,
        "replaceable": True,
        "message": "A new Launcher package will be created.",
    }
    if exists:
        try:
            service.validate_replaceable_destination(
                destination, review.plan.package_id,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            destination_review.update({
                "state": "blocked",
                "replaceable": False,
                "message": str(exc),
            })
        else:
            destination_review.update({
                "state": "managed_replacement",
                "message": "The existing SDK-managed package can be replaced atomically.",
            })
    return service, review, destination, destination_review


def _vehicle_quick_import_review_result(
    review: Any, destination: Path, destination_review: dict[str, Any],
) -> dict[str, Any]:
    result = review.to_dict()
    result.update({
        "kind": "vehicle_quick_import_review",
        "destination_preview": str(destination),
        "destination_review": destination_review,
        "vehicle_count": len(review.plan.catalog.vehicles),
        "warning_count": len(review.warnings),
        "review_only": True,
        "game_write_performed": False,
        "package_write_performed": False,
    })
    digest_source = json.dumps(
        result, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    result["review_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return result


def _review_vehicle_quick_import(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    _service, review, destination, destination_review = (
        _vehicle_quick_import_review_context(payload, risk="read_only")
    )
    result = _vehicle_quick_import_review_result(
        review, destination, destination_review,
    )
    return "read_only", dict(_bounded(result))


def _prepare_vehicle_quick_import(
    payload: object,
) -> tuple[str, dict[str, Any]]:
    risk = "authoring_write"
    if not isinstance(payload, dict):
        raise ProtocolError(
            "prepare_vehicle_quick_import payload must be an object", risk=risk,
        )
    expected_review = payload.get("review_sha256")
    if (
        not isinstance(expected_review, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_review) is None
    ):
        raise ProtocolError(
            "prepare_vehicle_quick_import requires a reviewed SHA-256 digest",
            risk=risk,
        )
    if payload.get("authoring_confirmed") is not True:
        raise ProtocolError(
            "Package preparation requires an explicit action-time confirmation.",
            risk=risk,
        )

    service, review, destination, destination_review = (
        _vehicle_quick_import_review_context(payload, risk=risk)
    )
    current_review = _vehicle_quick_import_review_result(
        review, destination, destination_review,
    )
    if current_review["review_sha256"] != expected_review:
        raise ProtocolError(
            "The source, draft, or destination changed after review; validate it again.",
            risk=risk,
        )
    if not destination_review["replaceable"]:
        raise ProtocolError(str(destination_review["message"]), risk=risk)
    if destination_review["exists"] and payload.get("replace_confirmed") is not True:
        raise ProtocolError(
            "Replacing an existing SDK-managed package requires explicit confirmation.",
            risk=risk,
        )
    try:
        prepared = service.prepare(review, destination)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
    result = prepared.to_dict()
    result.update({
        "kind": "vehicle_quick_import_prepared",
        "review_sha256": expected_review,
        "destination_review": destination_review,
        "package_write_performed": True,
        "game_write_performed": False,
    })
    return risk, dict(_bounded(result))


def _check_update() -> tuple[str, dict[str, Any]]:
    from allin1_sdk.self_update import fetch_latest_release, update_available

    try:
        release = fetch_latest_release()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk="read_only") from exc
    return "read_only", {
        "current_version": __version__,
        "latest_version": release.version,
        "update_available": update_available(__version__, release.version),
        "name": release.name,
        "page_url": release.page_url,
        "archive_name": release.archive_name,
        "archive_size": release.archive_size,
    }


def dispatch_operation(
    operation: str, payload: object, *, allow_game_writes: bool = False,
    allow_package_writes: bool = False, allow_rpf_writes: bool = False,
    audit_path: Path | None = None, vehicle_viewport_renderer: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run one non-lifecycle desktop operation."""
    if operation in {"list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction", "apply_rpf_transaction"}:
        from allin1_sdk import rpf_transaction_desktop
        writing = operation == "apply_rpf_transaction"
        # Conservative classification until the document, not caller-supplied scope, is validated.
        risk = "game_write" if writing else "read_only"
        try:
            handler = {"list_rpf_transactions": rpf_transaction_desktop.history,
                       "inspect_rpf_transaction": rpf_transaction_desktop.inspect,
                       "review_rpf_transaction": rpf_transaction_desktop.review,
                       "apply_rpf_transaction": rpf_transaction_desktop.apply}[operation]
            result = handler(payload, allow_rpf_writes=allow_rpf_writes) if writing else handler(payload)
            if writing:
                risk = "game_write" if result["game_write_performed"] else "authoring_write"
            if _bounded(result) != result:
                raise ValueError("RPF transaction evidence exceeds desktop limits")
            if writing:
                _audit({"timestamp": datetime.now(timezone.utc).isoformat(), "command": operation,
                        "action": result["action"], "review_sha256": result["review_sha256"],
                        "archive": result["session"]["archive"], "receipt": result["session"]["source"],
                        "archive_write_performed": result["archive_write_performed"],
                        "receipt_write_performed": result["receipt_write_performed"],
                        "lock_write_performed": result["lock_write_performed"], "lock_evidence": result["lock_evidence"],
                        "risk": risk, "completed": True}, audit_path)
            return risk, result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            if writing:
                _audit({"timestamp": datetime.now(timezone.utc).isoformat(), "command": operation,
                        "risk": risk, "completed": False, "error": str(exc)}, audit_path)
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation in {"inspect_rpf_change_set", "review_rpf_change_set", "apply_rpf_change_set"}:
        from allin1_sdk import rpf_change_set_desktop
        risk = "authoring_write" if operation == "apply_rpf_change_set" else "read_only"
        try:
            handler = {"inspect_rpf_change_set": rpf_change_set_desktop.inspect,
                       "review_rpf_change_set": rpf_change_set_desktop.review,
                       "apply_rpf_change_set": rpf_change_set_desktop.apply}[operation]
            result = handler(payload)
            if _bounded(result) != result:
                raise ValueError("RPF change-set evidence exceeds desktop limits")
            return risk, result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation in {"inspect_authoring_workspace", "review_workspace_action", "apply_workspace_action"}:
        from allin1_sdk import workspace_desktop
        risk = "authoring_write" if operation == "apply_workspace_action" else "read_only"
        try:
            handler = {"inspect_authoring_workspace": workspace_desktop.inspect,
                       "review_workspace_action": workspace_desktop.review,
                       "apply_workspace_action": workspace_desktop.apply}[operation]
            result = handler(payload)
            if _bounded(result) != result:
                raise ValueError("Workspace response exceeds desktop evidence limits")
            return risk, result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation in {"inspect_gxt2_workspace", "review_gxt2_action", "apply_gxt2_action"}:
        from allin1_sdk import gxt2_desktop
        risk = "authoring_write" if operation == "apply_gxt2_action" else "read_only"
        try:
            handler = {"inspect_gxt2_workspace": gxt2_desktop.inspect, "review_gxt2_action": gxt2_desktop.review,
                       "apply_gxt2_action": gxt2_desktop.apply}[operation]
            result = handler(payload)
            if _bounded(result) != result:
                raise ValueError("GXT2 response exceeds desktop evidence limits")
            return risk, result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation == "execute":
        return _execute_command(
            payload, allow_game_writes=allow_game_writes, audit_path=audit_path,
        )
    if operation == "inspect_package":
        return _inspect_package(payload)
    if operation == "inspect_ped_ymt":
        return _inspect_ped_ymt(payload)
    if operation == "preview_asset":
        return _preview_asset(payload)
    if operation == "render_vehicle_model":
        return _render_vehicle_model(payload, renderer=vehicle_viewport_renderer)
    if operation == "inspect_model_materials":
        return _inspect_model_materials(payload)
    if operation == "inspect_model_material_workspace":
        return _inspect_model_material_workspace(payload)
    if operation == "review_model_material_workspace":
        return _review_model_material_workspace(payload)
    if operation == "create_model_material_workspace":
        return _create_model_material_workspace(payload)
    if operation == "review_model_material_edit":
        return _review_model_material_edit(payload)
    if operation == "apply_model_material_edit":
        return _apply_model_material_edit(payload)
    if operation == "apply_model_material_history":
        return _apply_model_material_history(payload)
    if operation == "review_model_material_build":
        return _review_model_material_build(payload)
    if operation == "apply_model_material_build":
        return _apply_model_material_build(payload)
    if operation == "inspect_texture_workspace":
        return _inspect_texture_workspace(payload)
    if operation == "review_texture_workspace":
        return _review_texture_workspace(payload)
    if operation == "create_texture_workspace":
        return _create_texture_workspace(payload)
    if operation == "preview_texture_workspace":
        return _preview_texture_workspace(payload)
    if operation == "review_texture_edit":
        return _review_texture_edit(payload)
    if operation == "apply_texture_edit":
        return _apply_texture_edit(payload)
    if operation == "apply_texture_history":
        return _apply_texture_history(payload)
    if operation == "review_texture_build":
        return _review_texture_build(payload)
    if operation == "apply_texture_build":
        return _apply_texture_build(payload)
    if operation == "assistant_status":
        return _assistant_status()
    if operation == "configure_assistant":
        return _configure_assistant(payload)
    if operation in {"inspect_ped_workbench", "review_ped_authoring", "apply_ped_authoring"}:
        from allin1_sdk import ped_desktop

        risk = "authoring_write" if operation == "apply_ped_authoring" else "read_only"
        try:
            if not isinstance(payload, dict):
                raise ValueError("Ped payload must be an object")
            handler = {"inspect_ped_workbench": ped_desktop.inspect,
                       "review_ped_authoring": ped_desktop.review,
                       "apply_ped_authoring": ped_desktop.apply}[operation]
            return risk, dict(_bounded(handler(payload)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation in {"inspect_weapon_workbench", "review_weapon_authoring", "apply_weapon_authoring"}:
        from allin1_sdk import weapon_desktop

        risk = "authoring_write" if operation == "apply_weapon_authoring" else "read_only"
        try:
            if not isinstance(payload, dict):
                raise ValueError("Weapon payload must be an object")
            handler = {
                "inspect_weapon_workbench": weapon_desktop.inspect,
                "review_weapon_authoring": weapon_desktop.review,
                "apply_weapon_authoring": weapon_desktop.apply,
            }[operation]
            return risk, dict(_bounded(handler(payload)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation == "assistant_prompt":
        return _assistant_prompt(payload)
    if operation == "inspect_rpf_archive":
        return _inspect_rpf_archive(payload)
    if operation in {"review_rpf_utility", "apply_rpf_utility"}:
        from allin1_sdk import rpf_utility_desktop

        risk = "authoring_write" if operation == "apply_rpf_utility" else "read_only"
        try:
            if not isinstance(payload, dict):
                raise ValueError("RPF utility payload must be an object")
            handler = (
                rpf_utility_desktop.apply
                if operation == "apply_rpf_utility"
                else rpf_utility_desktop.review
            )
            return risk, dict(_bounded(handler(payload)))
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation == "inspect_vehicle_project":
        return _inspect_vehicle_project(payload)
    if operation == "inspect_vehicle_authoring_workspace":
        return _inspect_vehicle_authoring_workspace(payload)
    if operation == "review_vehicle_authoring_workspace":
        return _review_vehicle_authoring_workspace(payload)
    if operation == "create_vehicle_authoring_workspace":
        return _create_vehicle_authoring_workspace(payload)
    if operation == "review_vehicle_authoring_edit":
        return _review_vehicle_authoring_edit(payload)
    if operation == "apply_vehicle_authoring_edit":
        return _apply_vehicle_authoring_edit(payload)
    if operation == "review_vehicle_authoring_appearance":
        return _review_vehicle_authoring_appearance(payload)
    if operation == "apply_vehicle_authoring_appearance":
        return _apply_vehicle_authoring_appearance(payload)
    if operation == "inspect_vehicle_authoring_tuning":
        return _inspect_vehicle_authoring_tuning(payload)
    if operation == "review_vehicle_authoring_tuning":
        return _review_vehicle_authoring_tuning(payload)
    if operation == "apply_vehicle_authoring_tuning":
        return _apply_vehicle_authoring_tuning(payload)
    if operation == "review_vehicle_authoring_light_profile":
        return _review_vehicle_authoring_light_profile(payload)
    if operation == "apply_vehicle_authoring_light_profile":
        return _apply_vehicle_authoring_light_profile(payload)
    if operation == "review_vehicle_authoring_axles":
        return _review_vehicle_authoring_axles(payload)
    if operation == "apply_vehicle_authoring_axles":
        return _apply_vehicle_authoring_axles(payload)
    if operation == "inspect_vehicle_authoring_axle_skeleton":
        return _inspect_vehicle_authoring_axle_skeleton(payload)
    if operation == "review_vehicle_authoring_transmission":
        return _review_vehicle_authoring_transmission(payload)
    if operation == "apply_vehicle_authoring_transmission":
        return _apply_vehicle_authoring_transmission(payload)
    if operation == "review_vehicle_authoring_distribution":
        return _review_vehicle_authoring_distribution(payload)
    if operation == "apply_vehicle_authoring_distribution":
        return _apply_vehicle_authoring_distribution(payload)
    if operation == "review_vehicle_package_build":
        return _review_vehicle_package_build(payload)
    if operation == "apply_vehicle_package_build":
        return _apply_vehicle_package_build(
            payload, allow_package_writes=allow_package_writes,
        )
    if operation == "apply_vehicle_authoring_history":
        return _apply_vehicle_authoring_history(payload)
    if operation == "inspect_recipe":
        return _inspect_recipe(payload)
    if operation == "inspect_package_receipts":
        return _inspect_package_receipts(payload)
    if operation == "review_package_lifecycle":
        return _review_package_lifecycle(payload)
    if operation in {"review_vehicle_package_publish", "apply_vehicle_package_publish"}:
        from allin1_sdk import vehicle_publish_desktop
        risk = "authoring_write" if operation == "apply_vehicle_package_publish" else "read_only"
        try:
            handler = vehicle_publish_desktop.apply if risk == "authoring_write" else vehicle_publish_desktop.review
            return risk, dict(_bounded(handler(payload)))
        except (OSError, RuntimeError, TypeError, ValueError, StopIteration, ProtocolError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation in {"review_vehicle_oiv_export", "apply_vehicle_oiv_export"}:
        from allin1_sdk import vehicle_oiv_desktop
        risk = "authoring_write" if operation == "apply_vehicle_oiv_export" else "read_only"
        try:
            handler = vehicle_oiv_desktop.apply if risk == "authoring_write" else vehicle_oiv_desktop.review
            return risk, dict(_bounded(handler(payload)))
        except (OSError, RuntimeError, TypeError, ValueError, ProtocolError) as exc:
            raise ProtocolError(str(exc), risk=risk) from exc
    if operation == "inspect_vehicle_quick_import":
        return _inspect_vehicle_quick_import(payload)
    if operation == "review_vehicle_quick_import":
        return _review_vehicle_quick_import(payload)
    if operation == "prepare_vehicle_quick_import":
        return _prepare_vehicle_quick_import(payload)
    if operation == "apply_package_lifecycle":
        return _apply_package_lifecycle(
            payload, allow_package_writes=allow_package_writes,
            audit_path=audit_path,
        )
    if operation == "check_update":
        return _check_update()
    raise ProtocolError(f"unsupported desktop operation: {operation}")


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--job-worker"]
    return [sys.executable, "-m", "allin1_sdk.desktop_sidecar_host", "--job-worker"]


class DesktopProtocolService:
    """Stateful protocol router for one persistent desktop sidecar."""

    def __init__(
        self, *, allow_game_writes: bool = False, audit_path: Path | None = None,
        allow_package_writes: bool = False, allow_rpf_writes: bool = False,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.allow_game_writes = allow_game_writes
        self.allow_package_writes = allow_package_writes
        self.allow_rpf_writes = allow_rpf_writes
        self.audit_path = audit_path
        self.emit = emit or (lambda _message: None)
        self.negotiated = False
        self.stopping = False
        self._job: _Job | None = None
        self._lock = threading.RLock()
        self._catalog: dict[str, Any] | None = None
        self._vehicle_viewport_renderer: Any | None = None

    def _viewport_renderer(self) -> Any:
        if self._vehicle_viewport_renderer is None:
            from allin1_sdk.paths import project_root
            from allin1_sdk.vehicle_viewport import VehicleViewportRenderer

            self._vehicle_viewport_renderer = VehicleViewportRenderer(project_root())
        return self._vehicle_viewport_renderer

    def _error(
        self, request_id: str | None, message: str, *, job_id: str | None = None,
        sequence: int = 0, risk: str = "none", details: object = None,
        terminal: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": str(message)[:_MAX_STRING]}
        if details is not None:
            payload["details"] = _bounded(details)
        return envelope(
            "error", payload, request_id=request_id, job_id=job_id,
            sequence=sequence, risk=risk, terminal=terminal,
        )

    @staticmethod
    def _validate_request(request: object) -> tuple[str, str, dict[str, Any]]:
        if not isinstance(request, dict):
            raise ProtocolError("request must be a JSON object")
        required = {
            "protocol_version", "request_id", "job_id", "operation", "payload",
            "sequence", "risk", "terminal",
        }
        unknown = set(request) - required
        missing = required - set(request)
        if missing:
            raise ProtocolError("request is missing: " + ", ".join(sorted(missing)))
        if unknown:
            raise ProtocolError("request has unknown fields: " + ", ".join(sorted(unknown)))
        request_id = request.get("request_id")
        operation = request.get("operation")
        payload = request.get("payload")
        if not isinstance(request_id, str) or not _ID_PATTERN.fullmatch(request_id):
            raise ProtocolError("request_id must be a conservative 1-128 character id")
        if operation not in CLIENT_OPERATIONS:
            raise ProtocolError(f"client operation is not allowed: {operation}")
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object")
        if request.get("job_id") is not None:
            raise ProtocolError("client job_id belongs in the operation payload")
        if request.get("sequence") != 0:
            raise ProtocolError("client sequence must be zero")
        if request.get("risk") != "none":
            raise ProtocolError("clients cannot assign risk")
        if request.get("terminal") is not False:
            raise ProtocolError("client request terminal must be false")
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(
                f"unsupported protocol_version: {request.get('protocol_version')!r}"
            )
        return request_id, str(operation), payload

    def _catalog_payload(self) -> dict[str, Any]:
        if self._catalog is not None:
            return self._catalog
        from allin1_sdk.help_topics import HELP_TOPICS

        help_topics = [asdict(item) for item in HELP_TOPICS]
        self._catalog = {
            "commands": command_catalog(),
            "navigation": list(NAVIGATION),
            "help_topics": help_topics,
            "operations": sorted(CLIENT_OPERATIONS),
            "job_operations": sorted(JOB_OPERATIONS),
        }
        return self._catalog

    def _start_job(
        self, request_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        operation = payload.get("operation")
        inner_payload = payload.get("payload", {})
        revision = payload.get("revision")
        requested_job_id = payload.get("job_id")
        if operation not in JOB_OPERATIONS:
            raise ProtocolError(f"unsupported job operation: {operation}")
        if not isinstance(inner_payload, dict):
            raise ProtocolError("job payload must be an object")
        if revision is not None and (not isinstance(revision, str) or len(revision) > 256):
            raise ProtocolError("job revision must be a string of at most 256 characters")
        if requested_job_id is None:
            job_id = f"job-{uuid.uuid4().hex}"
        elif isinstance(requested_job_id, str) and _ID_PATTERN.fullmatch(requested_job_id):
            job_id = requested_job_id
        else:
            raise ProtocolError("job_id must be a conservative 1-128 character id")
        risk = _operation_risk(str(operation), inner_payload)
        if risk != "read_only":
            raise ProtocolError(
                "desktop protocol v1 accepts only read-only cancellable jobs; "
                "run mutations synchronously after their normal acknowledgement",
                risk=risk,
            )
        with self._lock:
            if self._job is not None:
                raise ProtocolError(
                    f"resource-heavy job is already running: {self._job.job_id}",
                    risk="read_only",
                )
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            process = subprocess.Popen(
                _worker_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                creationflags=creationflags,
                start_new_session=(os.name != "nt"),
            )
            worker_request = {
                "operation": operation,
                "payload": inner_payload,
                "allow_game_writes": False,
            }
            job = _Job(
                job_id, request_id, revision, risk, process, worker_request,
            )
            self._job = job
        thread = threading.Thread(
            target=self._monitor_job, args=(job,), daemon=True,
            name=f"allin1-desktop-{job_id}",
        )
        thread.start()
        return envelope(
            "job_event", {
                "state": "accepted", "status": "Job accepted", "progress": None,
                "revision": revision,
            }, request_id=request_id, job_id=job_id, sequence=0,
            risk=risk, terminal=False,
        )

    def _monitor_job(self, job: _Job) -> None:
        try:
            stdout, stderr = job.process.communicate(
                json.dumps(job.worker_request, ensure_ascii=False) + "\n"
            )
        except (OSError, ValueError) as exc:
            stdout, stderr = "", str(exc)
        with self._lock:
            if job.cancelled or self._job is not job:
                return
            self._job = None
        diagnostics = stderr[-32_768:].strip()
        lines = [line for line in stdout.splitlines() if line.strip()]
        if job.process.returncode != 0 or len(lines) != 1:
            message = diagnostics or "desktop job worker exited without one result"
            self.emit(self._error(
                job.request_id, message, job_id=job.job_id, sequence=1,
                risk=job.risk,
            ))
            return
        try:
            response = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            self.emit(self._error(
                job.request_id, f"desktop job worker returned invalid JSON: {exc.msg}",
                job_id=job.job_id, sequence=1, risk=job.risk,
                details=diagnostics or None,
            ))
            return
        if not isinstance(response, dict) or response.get("ok") is not True:
            message = (
                str(response.get("error", "desktop job failed"))
                if isinstance(response, dict) else "desktop job returned an invalid result"
            )
            self.emit(self._error(
                job.request_id, message, job_id=job.job_id, sequence=1,
                risk=job.risk,
                details=(response.get("details") if isinstance(response, dict) else None),
            ))
            return
        self.emit(envelope(
            "result", {
                "state": "completed", "revision": job.revision,
                "result": _bounded(response.get("result", {})),
                "diagnostics": diagnostics,
            }, request_id=job.request_id, job_id=job.job_id, sequence=1,
            risk=job.risk, terminal=True,
        ))

    def _cancel_active_job(
        self, *, request_id: str, requested_job_id: str, status: str,
    ) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or job.job_id != requested_job_id:
                raise ProtocolError(f"job is not active: {requested_job_id}")
            job.cancelled = True
            self._job = None
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(job.process.pid), "/T", "/F"],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                    timeout=5,
                )
            else:
                os.killpg(job.process.pid, signal.SIGTERM)
            job.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name != "nt":
                    os.killpg(job.process.pid, signal.SIGKILL)
                else:
                    job.process.kill()
                job.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return envelope(
            "job_event", {
                "state": "cancelled", "status": status, "progress": None,
                "revision": job.revision,
            }, request_id=request_id, job_id=job.job_id, sequence=1,
            risk=job.risk, terminal=True,
        )

    def handle(self, request: object) -> list[dict[str, Any]]:
        """Validate and route one decoded request."""
        request_id = request.get("request_id") if isinstance(request, dict) else None
        try:
            request_id, operation, payload = self._validate_request(request)
            if self.stopping:
                raise ProtocolError("desktop sidecar is shutting down")
            if operation != "handshake" and not self.negotiated:
                raise ProtocolError("handshake is required before other operations")
            if operation == "handshake":
                supported = payload.get("supported_versions")
                client = payload.get("client")
                if (
                    not isinstance(supported, list)
                    or PROTOCOL_VERSION not in supported
                    or not isinstance(client, dict)
                    or not isinstance(client.get("name"), str)
                    or not isinstance(client.get("version"), str)
                ):
                    raise ProtocolError(
                        f"no compatible protocol; sidecar supports {PROTOCOL_VERSION}"
                    )
                self.negotiated = True
                return [envelope(
                    "result", {
                        "negotiated_version": PROTOCOL_VERSION,
                        "service": "ALLIN1 SDK Desktop Sidecar",
                        "sdk_version": __version__,
                        "build_identity": embedded_build_identity(),
                        "transport": "jsonl-stdio",
                        "game_writes_enabled": self.allow_game_writes,
                        "package_writes_enabled": self.allow_package_writes,
                        "rpf_writes_enabled": self.allow_rpf_writes,
                        "max_request_bytes": MAX_REQUEST_BYTES,
                        "max_output_chars": MAX_OUTPUT_CHARS,
                        "capabilities": {
                            "persistent": True,
                            "streaming_jobs": True,
                            "cancellable_read_only_jobs": True,
                            "mutation_job_cancellation": False,
                        },
                    }, request_id=request_id, terminal=True,
                )]
            if operation == "catalog":
                return [envelope(
                    "result", self._catalog_payload(), request_id=request_id,
                    risk="read_only", terminal=True,
                )]
            if operation in {
                "execute", "inspect_package", "preview_asset",
                "render_vehicle_model", "inspect_recipe",
                "inspect_model_materials", "inspect_model_material_workspace",
                "review_model_material_workspace", "create_model_material_workspace",
                "review_model_material_edit", "apply_model_material_edit",
                "apply_model_material_history", "review_model_material_build",
                "apply_model_material_build", "assistant_status", "assistant_prompt",
                "configure_assistant",
                "inspect_weapon_workbench", "review_weapon_authoring", "apply_weapon_authoring",
                "inspect_ped_ymt",
                "inspect_ped_workbench", "review_ped_authoring", "apply_ped_authoring",
                "inspect_authoring_workspace", "review_workspace_action", "apply_workspace_action",
                "inspect_gxt2_workspace", "review_gxt2_action", "apply_gxt2_action",
                "list_rpf_transactions", "inspect_rpf_transaction", "review_rpf_transaction", "apply_rpf_transaction",
                "inspect_rpf_change_set", "review_rpf_change_set", "apply_rpf_change_set",
                "inspect_texture_workspace", "review_texture_workspace",
                "create_texture_workspace", "preview_texture_workspace",
                "review_texture_edit", "apply_texture_edit", "apply_texture_history",
                "review_texture_build", "apply_texture_build",
                "inspect_rpf_archive", "review_rpf_utility", "apply_rpf_utility",
                "inspect_vehicle_project",
                "inspect_vehicle_authoring_workspace",
                "review_vehicle_authoring_workspace",
                "create_vehicle_authoring_workspace",
                "review_vehicle_authoring_edit",
                "apply_vehicle_authoring_edit",
                "review_vehicle_authoring_appearance",
                "apply_vehicle_authoring_appearance",
                "inspect_vehicle_authoring_tuning",
                "review_vehicle_authoring_tuning",
                "apply_vehicle_authoring_tuning",
                "review_vehicle_authoring_light_profile",
                "apply_vehicle_authoring_light_profile",
                "review_vehicle_authoring_axles",
                "apply_vehicle_authoring_axles",
                "inspect_vehicle_authoring_axle_skeleton",
                "review_vehicle_authoring_transmission",
                "apply_vehicle_authoring_transmission",
                "review_vehicle_authoring_distribution",
                "apply_vehicle_authoring_distribution",
                "review_vehicle_package_build", "apply_vehicle_package_build",
                "apply_vehicle_authoring_history",
                "inspect_package_receipts",
                "review_package_lifecycle",
                "inspect_vehicle_quick_import", "review_vehicle_quick_import",
                "review_vehicle_oiv_export",
                "review_vehicle_package_publish",
                "prepare_vehicle_quick_import", "apply_package_lifecycle",
                "apply_vehicle_oiv_export",
                "apply_vehicle_package_publish",
                "check_update",
            }:
                risk, result = dispatch_operation(
                    operation, payload, allow_game_writes=self.allow_game_writes,
                    allow_package_writes=self.allow_package_writes,
                    allow_rpf_writes=self.allow_rpf_writes,
                    audit_path=self.audit_path,
                    vehicle_viewport_renderer=(
                        self._viewport_renderer()
                        if operation == "render_vehicle_model" else None
                    ),
                )
                return [envelope(
                    "result", {"result": result}, request_id=request_id,
                    risk=risk, terminal=True,
                )]
            if operation == "start_job":
                return [self._start_job(request_id, payload)]
            if operation == "cancel_job":
                job_id = payload.get("job_id")
                if not isinstance(job_id, str) or not _ID_PATTERN.fullmatch(job_id):
                    raise ProtocolError("cancel_job requires a valid job_id")
                return [self._cancel_active_job(
                    request_id=request_id, requested_job_id=job_id,
                    status="Cancelled by user",
                )]
            if operation == "shutdown":
                with self._lock:
                    active = self._job.job_id if self._job else None
                messages: list[dict[str, Any]] = []
                if active is not None:
                    messages.append(self._cancel_active_job(
                        request_id=request_id, requested_job_id=active,
                        status="Cancelled during application shutdown",
                    ))
                self.stopping = True
                messages.append(envelope(
                    "result", {"state": "stopped"}, request_id=request_id,
                    terminal=True,
                ))
                return messages
            raise ProtocolError(f"unsupported operation: {operation}")
        except ProtocolError as exc:
            return [self._error(
                request_id if isinstance(request_id, str) else None, str(exc),
                risk=exc.risk, details=exc.details,
            )]
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return [self._error(
                request_id if isinstance(request_id, str) else None, str(exc),
                risk="unclassified",
            )]


def serve_stdio(
    input_stream: IO[str], output_stream: IO[str], *,
    allow_game_writes: bool = False, audit_path: Path | None = None,
    allow_package_writes: bool = False, allow_rpf_writes: bool = False,
) -> None:
    """Serve the persistent desktop protocol until shutdown or stdin closes."""
    output_lock = threading.Lock()

    def write(message: dict[str, Any]) -> None:
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with output_lock:
            output_stream.write(encoded + "\n")
            output_stream.flush()

    service = DesktopProtocolService(
        allow_game_writes=allow_game_writes,
        allow_package_writes=allow_package_writes,
        allow_rpf_writes=allow_rpf_writes,
        audit_path=audit_path, emit=write,
    )
    for raw_line in input_stream:
        if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
            messages = [service._error(None, "request exceeds the size limit")]
        else:
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                messages = [service._error(None, f"invalid JSON: {exc.msg}")]
            else:
                messages = service.handle(request)
        for message in messages:
            write(message)
        if service.stopping:
            break


def run_job_worker(input_stream: IO[str], output_stream: IO[str]) -> int:
    """Run one isolated read-only job for a persistent sidecar."""
    try:
        request = json.loads(input_stream.readline())
        if not isinstance(request, dict):
            raise ProtocolError("job worker request must be an object")
        operation = request.get("operation")
        payload = request.get("payload", {})
        if operation not in JOB_OPERATIONS:
            raise ProtocolError(f"unsupported job worker operation: {operation}")
        risk = _operation_risk(str(operation), payload)
        if risk != "read_only" or request.get("allow_game_writes") is not False:
            raise ProtocolError("job worker accepts read-only work only", risk=risk)
        _risk, result = dispatch_operation(str(operation), payload)
        response = {"ok": True, "risk": risk, "result": result}
    except ProtocolError as exc:
        response = {
            "ok": False, "risk": exc.risk, "error": str(exc),
            "details": _bounded(exc.details),
        }
    except (json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        response = {"ok": False, "risk": "unclassified", "error": str(exc)}
    output_stream.write(
        json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    output_stream.flush()
    return 0 if response["ok"] else 1
