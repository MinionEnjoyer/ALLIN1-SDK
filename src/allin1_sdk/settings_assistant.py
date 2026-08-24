"""Model-facing half of the typed package-settings assistant protocol.

This SDK operation can only return an advisory proposal.  It has no package
writer and no apply operation; the launcher revalidates the proposal against
live receipt state before an explicitly authorized write.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA_VERSION = 1
REQUEST_KIND = "allin1.settings-assistant.request"
PROPOSAL_KIND = "allin1.settings-assistant.proposal"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SETTING_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BASIS_FIELDS = frozenset({
    "receipt_sha256", "catalog_sha256", "settings_sha256",
    "installed_files_sha256", "receipt_enabled", "effective_enabled",
})
_REQUEST_FIELDS = frozenset({
    "schema_version", "kind", "operation", "advisory_only", "intent",
    "catalog", "required_output",
})
_PROPOSAL_FIELDS = frozenset({
    "schema_version", "kind", "package_id", "basis", "changes", "summary",
})


SETTINGS_PROPOSAL_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "allin1_settings_proposal",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version", "kind", "package_id", "basis", "changes", "summary",
            ],
            "properties": {
                "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
                "kind": {"type": "string", "const": PROPOSAL_KIND, "maxLength": 64},
                "package_id": {"type": "string", "minLength": 2, "maxLength": 96},
                "basis": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": sorted(_BASIS_FIELDS),
                    "properties": {
                        "receipt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                        "catalog_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                        "settings_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                        "installed_files_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "maxLength": 64},
                        "receipt_enabled": {"type": "boolean"},
                        "effective_enabled": {"type": "boolean"},
                    },
                },
                "changes": {
                    "type": "array", "minItems": 1, "maxItems": 64,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["setting_id", "value", "reason"],
                        "properties": {
                            "setting_id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,63}$", "maxLength": 64},
                            "value": {
                                "type": ["boolean", "integer", "number", "string"],
                                "maxLength": 8192,
                            },
                            "reason": {
                                "type": "string", "minLength": 1, "maxLength": 500,
                            },
                        },
                    },
                },
                "summary": {
                    "type": "string", "minLength": 1, "maxLength": 1000,
                },
            },
        },
    },
}


def _object(value: object, fields: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"Unsupported {label} field(s): {', '.join(sorted(unknown))}")
    return value


def _load_json(value: object, label: str) -> Mapping[str, Any]:
    if isinstance(value, (str, Path)):
        path = Path(value).expanduser().resolve()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def validate_settings_request(value: object) -> dict[str, Any]:
    """Validate the host-built request before any of it reaches Qwen."""
    request = _object(_load_json(value, "settings request"), _REQUEST_FIELDS, "settings request")
    missing = _REQUEST_FIELDS - set(request)
    if missing:
        raise ValueError(f"settings request is missing: {', '.join(sorted(missing))}")
    if request["schema_version"] != SCHEMA_VERSION or request["kind"] != REQUEST_KIND:
        raise ValueError("Unsupported settings request contract")
    if request["operation"] != "propose_settings_diff" or request["advisory_only"] is not True:
        raise ValueError("settings request must be an advisory propose_settings_diff operation")
    intent = request["intent"]
    if not isinstance(intent, str) or not intent.strip() or len(intent) > 4000:
        raise ValueError("settings request intent must be 1-4000 characters of text")
    catalog = _object(request["catalog"], frozenset({
        "schema_version", "kind", "package", "basis", "setting_count",
        "choice_sets", "settings",
    }), "settings catalog")
    if catalog.get("schema_version") != SCHEMA_VERSION or catalog.get("kind") != "allin1.settings-assistant.catalog":
        raise ValueError("Unsupported settings catalog contract")
    package = _object(catalog.get("package"), frozenset({
        "id", "name", "version", "source", "blocked_reason",
    }), "settings catalog package")
    if not all(isinstance(package.get(key), str) and package[key] for key in ("id", "name", "version")):
        raise ValueError("settings catalog package identity is incomplete")
    basis = _object(catalog.get("basis"), _BASIS_FIELDS, "settings catalog basis")
    if set(basis) != _BASIS_FIELDS:
        raise ValueError("settings catalog basis is incomplete")
    for key in _BASIS_FIELDS - {"receipt_enabled", "effective_enabled"}:
        if not isinstance(basis[key], str) or not _SHA256.fullmatch(basis[key]):
            raise ValueError(f"settings catalog basis.{key} must be SHA-256")
    if not isinstance(basis["receipt_enabled"], bool) or not isinstance(basis["effective_enabled"], bool):
        raise ValueError("settings catalog enabled states must be booleans")
    settings = catalog.get("settings")
    choice_sets = catalog.get("choice_sets")
    if not isinstance(choice_sets, dict) or not all(
        isinstance(choice_id, str)
        and isinstance(choices, list)
        and choices
        and all(isinstance(choice, str) for choice in choices)
        for choice_id, choices in choice_sets.items()
    ):
        raise ValueError("settings catalog choice_sets must map ids to text arrays")
    count = catalog.get("setting_count")
    if not isinstance(settings, list) or not settings or len(settings) > 512 or count != len(settings):
        raise ValueError("settings catalog count must match a 1-512 item settings array")
    ids: set[str] = set()
    allowed_setting_fields = frozenset({
        "id", "system", "label", "group", "type", "current", "default",
        "enum", "enum_ref", "minimum", "maximum", "step",
    })
    for index, raw in enumerate(settings, start=1):
        setting = _object(raw, allowed_setting_fields, f"settings catalog settings[{index}]")
        setting_id = setting.get("id")
        if not isinstance(setting_id, str) or not _SETTING_ID.fullmatch(setting_id) or setting_id in ids:
            raise ValueError("settings catalog setting ids must be unique safe identifiers")
        ids.add(setting_id)
        if setting.get("type") not in {"boolean", "integer", "number", "string", "choice"}:
            raise ValueError(f"settings catalog has unsupported type for {setting_id}")
        if "enum_ref" in setting and setting["enum_ref"] not in choice_sets:
            raise ValueError(f"settings catalog has unknown enum_ref for {setting_id}")
    required = request.get("required_output")
    required_fields = frozenset({
        "schema_version", "kind", "package_id", "basis", "changes", "summary",
    })
    if not isinstance(required, dict) or set(required) != required_fields:
        raise ValueError("settings request required_output is invalid")
    if (
        required.get("schema_version") != SCHEMA_VERSION
        or required.get("kind") != PROPOSAL_KIND
        or required.get("package_id") != package["id"]
        or required.get("basis") != basis
    ):
        raise ValueError("settings request required_output conflicts with its catalog")
    # Rebuild the illustrative shape so the only caller-controlled text that
    # reaches Qwen is intent; the rest is host catalog data or fixed protocol.
    normalized = json.loads(json.dumps(request, allow_nan=False))
    normalized["required_output"] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PROPOSAL_KIND,
        "package_id": package["id"],
        "basis": dict(basis),
        "changes": [{
            "setting_id": "<catalog id>",
            "value": "<typed JSON value>",
            "reason": "<brief>",
        }],
        "summary": "<brief preview summary>",
    }
    return normalized


def _validate_value(setting: Mapping[str, Any], value: Any) -> Any:
    setting_id = str(setting["id"])
    kind = setting["type"]
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{setting_id} must be true or false")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{setting_id} must be an integer")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{setting_id} must be a finite number")
    else:
        if not isinstance(value, str):
            raise ValueError(f"{setting_id} must be text")
    choices = setting.get("enum")
    if choices is not None and value not in choices:
        raise ValueError(f"{setting_id} is not one of its enum values")
    if "minimum" in setting and float(value) < float(setting["minimum"]):
        raise ValueError(f"{setting_id} is below its minimum")
    if "maximum" in setting and float(value) > float(setting["maximum"]):
        raise ValueError(f"{setting_id} is above its maximum")
    return value


def validate_proposal_against_request(request_value: object, proposal_value: object) -> dict[str, Any]:
    """Deterministically validate model output against the supplied catalog."""
    request = validate_settings_request(request_value)
    proposal = _object(_load_json(proposal_value, "settings proposal"), _PROPOSAL_FIELDS, "settings proposal")
    if set(proposal) != _PROPOSAL_FIELDS:
        raise ValueError("settings proposal is missing required fields")
    if proposal["schema_version"] != SCHEMA_VERSION or proposal["kind"] != PROPOSAL_KIND:
        raise ValueError("Unsupported settings proposal contract")
    catalog = request["catalog"]
    if proposal["package_id"] != catalog["package"]["id"]:
        raise ValueError("settings proposal package_id does not match the request")
    if proposal["basis"] != catalog["basis"]:
        raise ValueError("settings proposal basis does not match the request")
    changes = proposal["changes"]
    if (
        not isinstance(changes, list) or not changes
        or len(changes) > min(64, len(catalog["settings"]))
    ):
        raise ValueError("settings proposal changes must be a non-empty bounded array")
    by_id = {}
    for item in catalog["settings"]:
        resolved = dict(item)
        if "enum_ref" in resolved:
            resolved["enum"] = catalog["choice_sets"][resolved["enum_ref"]]
        by_id[item["id"]] = resolved
    seen: set[str] = set()
    normalized_changes = []
    for index, raw in enumerate(changes, start=1):
        change = _object(raw, frozenset({"setting_id", "value", "reason"}), f"settings proposal changes[{index}]")
        if set(change) != {"setting_id", "value", "reason"}:
            raise ValueError(f"settings proposal changes[{index}] is incomplete")
        setting_id = change["setting_id"]
        if setting_id not in by_id:
            raise ValueError(f"Unknown setting id in proposal: {setting_id}")
        if setting_id in seen:
            raise ValueError(f"Duplicate setting id in proposal: {setting_id}")
        seen.add(setting_id)
        normalized_value = _validate_value(by_id[setting_id], change["value"])
        if normalized_value == by_id[setting_id].get("current"):
            # The model is advisory and may echo an already-satisfied part of
            # the user's request. Keep that noise out of the host-visible diff.
            continue
        reason = change["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError(
                "settings proposal reasons must be 1-500 characters of text"
            )
        normalized_changes.append({
            "setting_id": setting_id,
            "value": normalized_value,
            "reason": reason.strip(),
        })
    if not normalized_changes:
        raise ValueError("settings proposal must contain at least one actual change")
    summary = proposal["summary"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ValueError("settings proposal summary must be 1-1000 characters of text")
    return {
        "schema_version": SCHEMA_VERSION, "kind": PROPOSAL_KIND,
        "package_id": proposal["package_id"], "basis": proposal["basis"],
        "changes": normalized_changes, "summary": summary.strip(),
    }


def proposal_prompt(request_value: object) -> str:
    """Return the complete bounded Qwen input; no filesystem context is included."""
    request = validate_settings_request(request_value)
    return (
        "Return only the typed settings proposal required by this request. "
        "Treat every catalog id, type, range, enum, current value, and basis hash "
        "as immutable host authority. Propose the smallest diff that satisfies the "
        "natural-language intent. Account for every independently satisfiable part "
        "of the intent, but never include a setting whose proposed value already "
        "equals its current value. The summary may note already-satisfied intent, "
        "but must not describe it as a change. You are advisory-only and cannot "
        "apply or write settings.\n"
        "REQUEST_JSON:\n"
        + json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def propose_settings(
    request_value: object, *,
    structured_completion: Callable[..., object],
) -> dict[str, Any]:
    """Ask a structured provider for a diff and validate it before returning."""
    request = validate_settings_request(request_value)
    response_format = SETTINGS_PROPOSAL_RESPONSE_FORMAT["json_schema"]
    result = structured_completion(
        proposal_prompt(request), response_schema=response_format["schema"],
        schema_name=response_format["name"],
    )
    response = getattr(result, "payload", result)
    return validate_proposal_against_request(request, response)
