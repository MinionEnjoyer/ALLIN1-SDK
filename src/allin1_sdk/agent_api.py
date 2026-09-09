"""Structured stdio automation API for trusted local AI and developer tools.

The API deliberately transports SDK commands as JSON values instead of shell
text.  It never invokes a shell, does not expose Python evaluation, and keeps
game/archive writes behind both a process-level opt-in and the CLI's existing
acknowledgement checks.
"""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

import click
from click.testing import CliRunner

from allin1_sdk.paths import gta_root_containing, user_data_root


PROTOCOL_VERSION = "1.0"
MAX_REQUEST_BYTES = 256 * 1024
MAX_OUTPUT_CHARS = 1024 * 1024
_EXECUTION_LOCK = threading.RLock()  # Click's capture streams are process-global.
_OPTIONAL_REPORT_COMMANDS = frozenset({
    "inspect-package-graph-relations", "inspect-ped-ymt", "inspect-rpf",
    "inspect-rpf-change-set", "inspect-rpf-graph", "inspect-rpf-program",
    "list-gxt2-entries", "list-rpf-transactions", "search-rpf-catalog",
    "validate-rpf-graph", "verify-rpf-transaction",
})
GAME_WRITE_COMMANDS = frozenset({
    "apply-rpf-plan",
    "install-package",
    "rollback-rpf-transaction",
    # The compatibility group contains the complete legacy command surface,
    # including game-write operations.  Classify the group conservatively;
    # typed API callers should use the explicitly cataloged top-level command.
    "sdk",
    "uninstall-package",
})
AUTHORING_COMMANDS = frozenset({
    "build-edition-bundle",
    "apply-authoring-action",
    "inspect-authoring-workspace",
    "add-vehicle-tuning-entry",
    "analyze-package-graph",
    "add-ytd-texture",
    "add-rpf-program-node",
    "audit-folder",
    "build-native-workspace",
    "build-material-workspace",
    "build-map-package",
    "build-binary-workspace",
    "build-gxt2-workspace",
    "build-rpf-tree",
    "build-rpf-graph",
    "build-vehicle-package",
    "build-axle-oiv",
    "build-axle-runtime-bundle",
    "build-story-axle-runtime",
    "create-weapon-authoring",
    "apply-weapon-calibration",
    "create-vehicle-authoring",
    "create-ped-authoring",
    "create-material-workspace",
    "catalog-rpfs",
    "canary-rpf-transaction",
    "compile-vehicle-data",
    "compile-oiv-xml",
    "compile-oiv-recipe",
    "clone-weapon-animation",
    "clone-weapon-bundle",
    "clone-ped-bundle",
    "configure-rpf-program-node",
    "connect-rpf-program-nodes",
    "create-rpf-change-set",
    "create-rpf-program",
    "create-rpf-graph",
    "diff-meta",
    "diff-rpf",
    "derive-rpf-plan",
    "defragment-rpf",
    "dlc-inventory",
    "extract-rpf-entry",
    "extract-rpf-subtree",
    "expand-rpf-graph-sealed",
    "add-rpf-graph-container",
    "add-rpf-graph-file",
    "export-native-workspace",
    "export-managed-vehicle-package",
    "export-legacy-vehicle-oiv",
    "export-vehicle-project",
    "export-vehicle-axles",
    "export-story-axle-runtime-config",
    "export-rpf-binary-workspace",
    "export-rpf-gxt2-workspace",
    "export-rpf-native-workspace",
    "import-package",
    "import-package-graph",
    "open-package-graph",
    "index-rpf",
    "import-rpf-graph",
    "inspect-native-asset",
    "inspect-package-rpfs",
    "inspect-rpf-native-entry",
    "link",
    "layout-rpf-graph",
    "layout-rpf-program",
    "list-ytd-textures",
    "oiv-plan",
    "plan-axle-oiv",
    "plan-rpf-add",
    "plan-rpf-batch",
    "plan-rpf-native-workspace",
    "plan-rpf-binary-workspace",
    "plan-rpf-gxt2-workspace",
    "plan-rpf-graph-origin",
    "plan-rpf-change-set",
    "plan-rpf-program",
    "plan-rpf-sync",
    "plan-rpf-delete",
    "plan-rpf-replacement",
    "materialize-rpf-graph",
    "move-rpf-change",
    "move-vehicle-tuning-entry",
    "position-rpf-graph-node",
    "position-rpf-program-node",
    "prepare-vehicle-quick-import",
    "publish-managed-vehicle-package",
    "refresh-rpf-graph-sources",
    "recover-rpf-transaction",
    "render-native-model",
    "render-rpf-graph-previews",
    "remove-rpf-graph-node",
    "remove-rpf-program-node",
    "remove-vehicle-tuning-entry",
    "redo-vehicle-edit",
    "rename-rpf-graph-node",
    "reparent-rpf-graph-node",
    "run-rpf-program",
    "migrate-vehicle-identity",
    "migrate-ped-identity",
    "set-vehicle-appearance",
    "set-vehicle-axles",
    "stage-rpf-change",
    "set-vehicle-light-profile",
    "set-vehicle-fields",
    "set-vehicle-distribution",
    "set-vehicle-tuning-kit",
    "set-vehicle-tuning-entry",
    "set-ped-fields",
    "set-material-binding",
    "set-geometry-material",
    "set-weapon-attachment",
    "set-weapon-component",
    "set-weapon-fields",
    "set-weapon-shop-fields",
    "disconnect-rpf-program-node",
    "remove-ytd-texture",
    "replace-ytd-texture",
    "undo-vehicle-edit",
    "undo-weapon-edit",
    "undo-ped-edit",
    "undo-material-edit",
    "patch-binary-workspace",
    "add-gxt2-entry",
    "remove-gxt2-entry",
    "set-gxt2-text",
    "undo-ytd-texture-edit",
    "undo-binary-workspace",
    "undo-gxt2-edit",
    "unstage-rpf-change",
    "validate-meta-roundtrip",
    "verify-rpf-archive",
})
READ_ONLY_COMMANDS = frozenset({
    "authoring-catalog",
    "check-sdk-update",
    "query-node-graph",
    "open-rpf-program",
    "review-authoring-action",
    "assistant",
    "compare-telemetry",
    "detect-map-placements",
    "inspect-binary-workspace",
    "inspect-log",
    "inspect-model-materials",
    "inspect-material-workspace",
    "inspect-map-project",
    "inspect-package-graph-relations",
    "inspect-package-receipt",
    "inspect-ped-ymt",
    "inspect-product-workspace",
    "inspect-rpf",
    "inspect-rpf-change-set",
    "inspect-rpf-graph",
    "inspect-rpf-program",
    "inspect-source",
    "inspect-story-axle-runtimes",
    "inspect-story-axle-toolchain",
    "inspect-vehicle-authoring",
    "inspect-vehicle-axles",
    "inspect-vehicle-distribution",
    "inspect-vehicle-quick-import",
    "inspect-ped-authoring",
    "inspect-vehicle-project",
    "inspect-vehicle-tuning",
    "inspect-weapon-authoring",
    "inspect-weapon-calibration",
    "inspect-weapon-sights",
    "review-weapon-calibration",
    "inspect-weapon-animation",
    "inspect-weapon-shop",
    "inspect-workbench",
    "list-axle-prefabs",
    "list",
    "list-gxt2-entries",
    "list-installed-packages",
    "list-rpf-program-templates",
    "list-rpf-transactions",
    "plan-weapon-clone",
    "plan-ped-clone",
    "plan-managed-vehicle-package",
    "plan-axle-runtime-bundle",
    "preview-axle-prefab",
    "preview-axle-steering",
    "preview-axle-tyres",
    "open-rpf-graph",
    "open-model-material-workbench",
    "open-launcher-package",
    "open-product-workspace",
    "open-vehicle-workbench",
    "open-axle-configurator",
    "open-workbench",
    "propose-package-settings",
    "search-rpf-catalog",
    "validate",
    "validate-package",
    "validate-map-project",
    "validate-package-settings-proposal",
    "validate-rpf-graph",
    "verify-package-ownership",
    "verify-rpf-transaction",
})


class UnclassifiedCommandError(ValueError):
    """Raised when an API command has not received an explicit risk review."""


_RISK_GROUPS = {
    "read_only": READ_ONLY_COMMANDS,
    "authoring_write": AUTHORING_COMMANDS,
    "game_write": GAME_WRITE_COMMANDS,
}
_risk_members = [
    command
    for commands in _RISK_GROUPS.values()
    for command in commands
]
if len(_risk_members) != len(set(_risk_members)):
    raise RuntimeError("Agent API command risk groups overlap")
COMMAND_RISKS = {
    command: risk
    for risk, commands in _RISK_GROUPS.items()
    for command in commands
}

_PATH_SENSITIVE_AXLE_OUTPUTS = {
    "build-axle-runtime-bundle": ("--output-dir", "-o"),
    "build-story-axle-runtime": ("--output-dir", "-o"),
    "export-story-axle-runtime-config": ("--output", "-o"),
}
_PATH_SENSITIVE_POSITIONAL_OUTPUTS = {
    # build-map-package SOURCE DESCRIPTOR OUTPUT
    "build-map-package": 2,
}


def _option_values(arguments: list[str], flags: tuple[str, ...]) -> tuple[str, ...]:
    """Extract every Click option value relevant to a safety preflight."""
    values: list[str] = []
    index = 0
    long_flags = tuple(flag for flag in flags if flag.startswith("--"))
    short_flags = tuple(
        flag for flag in flags if flag.startswith("-") and not flag.startswith("--")
    )
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            break
        if token in flags:
            if index + 1 < len(arguments):
                values.append(arguments[index + 1])
            index += 2
            continue
        matched = False
        for flag in long_flags:
            prefix = f"{flag}="
            if token.startswith(prefix):
                values.append(token[len(prefix):])
                matched = True
                break
        if not matched:
            for flag in short_flags:
                if token.startswith(flag) and len(token) > len(flag):
                    value = token[len(flag):]
                    values.append(value[1:] if value.startswith("=") else value)
                    matched = True
                    break
        index += 1
    return tuple(value for value in values if value)


def _positional_values(
    arguments: list[str], value_flags: tuple[str, ...],
) -> tuple[str, ...]:
    """Return positionals while skipping reviewed options and their values."""

    values: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            values.extend(arguments[index + 1:])
            break
        if token in value_flags:
            index += 2
            continue
        if any(token.startswith(f"{flag}=") for flag in value_flags):
            index += 1
            continue
        if not token.startswith("-"):
            values.append(token)
        index += 1
    return tuple(values)


def _effective_command_risk(
    command: str, arguments: list[str], base_risk: str,
) -> str:
    """Elevate authoring output aimed at a live GTA installation."""
    output_flags = _PATH_SENSITIVE_AXLE_OUTPUTS.get(command)
    if command in _OPTIONAL_REPORT_COMMANDS and _option_values(arguments, ("--output", "-o")):
        base_risk = "authoring_write"
        output_flags = ("--output", "-o")
    positional_index = _PATH_SENSITIVE_POSITIONAL_OUTPUTS.get(command)
    if base_risk != "authoring_write":
        return base_risk
    explicit_roots = _option_values(arguments, ("--gta-path",))
    outputs = _option_values(arguments, output_flags) if output_flags else ()
    if positional_index is not None:
        positional = _positional_values(
            arguments, ("--project-root", "--gta-path", "--edition"),
        )
        if len(positional) > positional_index:
            outputs += (positional[positional_index],)
    if any(
        gta_root_containing(value, explicit_roots=explicit_roots) is not None
        for value in outputs
    ):
        return "game_write"
    return base_risk


def _cli_group() -> click.Group:
    from allin1_sdk.cli import main

    return main


def command_risk(command: str) -> str:
    """Return an explicitly reviewed command risk or fail closed."""
    try:
        return COMMAND_RISKS[command]
    except KeyError as exc:
        raise UnclassifiedCommandError(
            f"command has no explicit Agent API risk classification: {command}"
        ) from exc


def effective_command_risk(command: str, arguments: Iterable[str] = ()) -> str:
    """Return the reviewed risk after path-sensitive elevation.

    Desktop and other typed transports use this public helper so every caller
    shares the Agent API's fail-closed classification instead of copying it.
    """
    normalized = [str(value) for value in arguments]
    return _effective_command_risk(command, normalized, command_risk(command))


def _parameter_schema(parameter: click.Parameter) -> dict[str, Any]:
    raw_default = parameter.default
    default_provided = not (
        type(raw_default).__name__ == "Sentinel"
        and getattr(raw_default, "name", "") == "UNSET"
    )
    if not default_provided:
        catalog_default: Any = None
    elif isinstance(raw_default, (str, int, float, bool)) or raw_default is None:
        catalog_default = raw_default
    elif isinstance(raw_default, (list, tuple)):
        catalog_default = list(raw_default)
    elif isinstance(raw_default, Path):
        catalog_default = str(raw_default)
    else:
        # Keep the catalog JSON-safe without silently dropping a future Click
        # default type. No current top-level command reaches this fallback.
        catalog_default = str(raw_default)
    item: dict[str, Any] = {
        "name": parameter.name,
        "required": bool(parameter.required),
        "type": parameter.type.name,
        "nargs": parameter.nargs,
        "default": catalog_default,
        "default_provided": default_provided,
    }
    if isinstance(parameter, click.Option):
        item.update({
            "kind": "option",
            "flags": list(parameter.opts) + list(parameter.secondary_opts),
            "multiple": bool(parameter.multiple),
            "is_flag": bool(parameter.is_flag),
            "help": parameter.help or "",
        })
    else:
        item["kind"] = "argument"
    if isinstance(parameter.type, click.Choice):
        item["choices"] = [str(value) for value in parameter.type.choices]
        item["case_sensitive"] = parameter.type.case_sensitive
    if isinstance(parameter.type, (click.IntRange, click.FloatRange)):
        item["minimum"] = parameter.type.min
        item["maximum"] = parameter.type.max
        item["minimum_open"] = parameter.type.min_open
        item["maximum_open"] = parameter.type.max_open
        item["clamp"] = parameter.type.clamp
    if isinstance(parameter.type, click.Path):
        item["path"] = {key: getattr(parameter.type, key) for key in ("exists", "file_okay", "dir_okay", "readable", "writable", "resolve_path")}
    if isinstance(parameter, click.Option):
        item["primary_flags"] = list(parameter.opts)
        item["secondary_flags"] = list(parameter.secondary_opts)
    return item


def command_catalog() -> list[dict[str, Any]]:
    """Return a machine-readable catalog of the supported automation surface."""
    group = _cli_group()
    context = click.Context(group, info_name="allin1-sdk")
    catalog: list[dict[str, Any]] = []
    for name in group.list_commands(context):
        if name == "agent-api":
            continue
        command = group.get_command(context, name)
        if command is None:
            continue
        entry = {
            "name": name,
            "description": command.get_short_help_str(),
            "help": command.help or "",
            "risk": command_risk(name),
            "parameters": [_parameter_schema(item) for item in command.params],
        }
        if name in _OPTIONAL_REPORT_COMMANDS:
            entry["risk_overrides"] = {"--output": "authoring_write", "output_inside_gta": "game_write"}
        if isinstance(command, click.Group):
            child_context = click.Context(command, parent=context, info_name=name)
            entry["subcommands"] = [{"name": child_name, "help": child.help or "", "parameters": [_parameter_schema(item) for item in child.params]}
                                    for child_name in command.list_commands(child_context)
                                    if (child := command.get_command(child_context, child_name)) is not None]
            entry["parameter_input"] = "Use args with the subcommand name first"
        else:
            entry["parameter_input"] = "args array or parameters object, never both"
        catalog.append(entry)
    return catalog


def _parameter_arguments(command: click.Command, values: object) -> list[str]:
    """Encode named JSON values without a shell or ambiguous option injection."""
    if isinstance(command, click.Group):
        raise ValueError("Grouped commands require args beginning with the subcommand; see catalog.subcommands")
    if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
        raise ValueError("parameters must be an object keyed by catalog parameter names")
    known = {param.name for param in command.params}
    if set(values) - known:
        raise ValueError("Unknown parameters: " + ", ".join(sorted(set(values) - known)))
    options, arguments = [], []

    def scalar(value):
        if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Parameter values must be finite scalar values (or arrays for multiple/nargs parameters)")
        rendered = str(value) if not isinstance(value, bool) else str(value).lower()
        if "\0" in rendered:
            raise ValueError("Parameter values must not contain NUL bytes")
        return rendered

    omitted_argument = False
    for param in command.params:
        value = values.get(param.name)
        if value is None:
            if isinstance(param, click.Argument):
                omitted_argument = True
            continue
        if isinstance(param, click.Option) and param.is_flag:
            if type(value) is not bool:
                raise ValueError(f"{param.name} requires a JSON boolean")
            if value:
                options.append(param.opts[0])
            elif param.secondary_opts:
                options.append(param.secondary_opts[0])
            elif param.default is True:
                raise ValueError(f"{param.name} has no false flag; use the documented command form")
            continue
        if isinstance(param, click.Option) and param.count:
            if type(value) is not int or not 0 <= value <= 16:
                raise ValueError(f"{param.name} requires a count between 0 and 16")
            options.extend([param.opts[0]] * value)
            continue
        multiple = isinstance(param, click.Option) and param.multiple
        if multiple and not isinstance(value, list):
            raise ValueError(f"{param.name} requires an array")
        rows = value if multiple else [value]
        for row in rows:
            if param.nargs != 1:
                if not isinstance(row, list) or (param.nargs != -1 and len(row) != param.nargs):
                    raise ValueError(f"{param.name} requires an array of {param.nargs if param.nargs != -1 else 'variable'} values")
                encoded = [scalar(item) for item in row]
            else:
                encoded = [scalar(row)]
            if isinstance(param, click.Option):
                options.extend([f"{param.opts[0]}={encoded[0]}", *encoded[1:]])
            else:
                if omitted_argument:
                    raise ValueError("Cannot provide a positional parameter after omitting an earlier one")
                arguments.extend(encoded)
    return options + (["--", *arguments] if arguments else [])


def _response(request_id: object, *, ok: bool, **values: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "id": request_id,
        "ok": ok,
        **values,
    }


def _audit(record: dict[str, Any], audit_path: Path | None = None) -> None:
    destination = audit_path or user_data_root() / "agent-api-audit.jsonl"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # An unwritable audit location must not corrupt the protocol stream.
        pass


def execute_request(
    request: object, *, allow_game_writes: bool = False,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and execute one JSON-compatible API request."""
    if not isinstance(request, dict):
        return _response(None, ok=False, error="request must be a JSON object")
    request_id = request.get("id")
    action = request.get("action")
    if action == "ping":
        return _response(
            request_id, ok=True, result={
                "service": "ALLIN1 SDK Agent API",
                "version": PROTOCOL_VERSION,
                "transport": "jsonl-stdio",
                "game_writes_enabled": allow_game_writes,
            },
        )
    if action == "catalog":
        try:
            catalog = command_catalog()
        except UnclassifiedCommandError as exc:
            return _response(
                request_id, ok=False, risk="unclassified", error=str(exc),
            )
        return _response(request_id, ok=True, result=catalog)
    if action != "execute":
        return _response(
            request_id, ok=False,
            error="unsupported action; use ping, catalog, or execute",
        )

    command_name = request.get("command")
    arguments = request.get("args", [])
    if not isinstance(command_name, str) or not command_name.strip():
        return _response(request_id, ok=False, error="command must be a non-empty string")
    command_name = command_name.strip().casefold()
    if command_name == "agent-api":
        return _response(request_id, ok=False, error="agent-api cannot invoke itself")
    if (
        not isinstance(arguments, list)
        or len(arguments) > 128
        or any(not isinstance(value, str) or "\0" in value for value in arguments)
    ):
        return _response(
            request_id, ok=False,
            error="args must be a list of at most 128 strings without NUL bytes",
        )

    group = _cli_group()
    context = click.Context(group, info_name="allin1-sdk")
    command = group.get_command(context, command_name)
    if command is None:
        return _response(request_id, ok=False, error=f"unknown command: {command_name}")
    if "parameters" in request:
        if "args" in request:
            return _response(request_id, ok=False, error="Choose args or parameters, not both")
        try:
            arguments = _parameter_arguments(command, request["parameters"])
        except ValueError as exc:
            return _response(request_id, ok=False, error=str(exc))
        if len(arguments) > 128:
            return _response(request_id, ok=False, error="Encoded parameters exceed 128 arguments; use a bounded request file")
    try:
        risk = effective_command_risk(command_name, arguments)
    except UnclassifiedCommandError as exc:
        response = _response(
            request_id, ok=False, risk="unclassified", error=str(exc),
        )
        _audit({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id, "command": command_name,
            "args": arguments, "risk": "unclassified", "allowed": False,
            "exit_code": None,
        }, audit_path)
        return response
    if risk == "game_write" and not allow_game_writes:
        response = _response(
            request_id, ok=False, risk=risk,
            error=(
                "game/archive writes are disabled for this API process; the user must "
                "restart it with --allow-game-writes and the command must still include "
                "its acknowledgement option"
            ),
        )
        _audit({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id, "command": command_name,
            "args": arguments, "risk": risk, "allowed": False,
            "exit_code": None,
        }, audit_path)
        return response

    with _EXECUTION_LOCK:
        result = CliRunner().invoke(
            group, [command_name, *arguments], color=False, prog_name="allin1-sdk",
        )
    output = result.output
    if result.exception and not isinstance(result.exception, SystemExit):
        detail = str(result.exception).strip()
        if detail and detail not in output:
            output += f"ERROR: {detail}\n"
    truncated = len(output) > MAX_OUTPUT_CHARS
    if truncated:
        output = output[:MAX_OUTPUT_CHARS]
    data = None
    data_available = False
    if not truncated:
        try:
            data = json.loads(output)
            data_available = True
        except (ValueError, TypeError):
            pass
    response = _response(
        request_id, ok=result.exit_code == 0, risk=risk,
        result={
            "command": command_name,
            "exit_code": result.exit_code,
            "output": output,
            "output_truncated": truncated,
            "data": data,
            "data_available": data_available,
        },
    )
    _audit({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id, "command": command_name,
        "args": arguments, "risk": risk, "allowed": True,
        "exit_code": result.exit_code,
    }, audit_path)
    return response


def serve_stdio(
    input_stream: IO[str], output_stream: IO[str], *,
    allow_game_writes: bool = False, audit_path: Path | None = None,
) -> None:
    """Serve newline-delimited JSON requests until stdin closes."""
    for raw_line in input_stream:
        if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
            response = _response(None, ok=False, error="request exceeds the size limit")
        else:
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                response = _response(None, ok=False, error=f"invalid JSON: {exc.msg}")
            else:
                response = execute_request(
                    request, allow_game_writes=allow_game_writes,
                    audit_path=audit_path,
                )
        output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
        output_stream.flush()
