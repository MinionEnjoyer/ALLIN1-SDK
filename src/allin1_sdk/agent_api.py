"""Structured stdio automation API for trusted local AI and developer tools.

The API deliberately transports SDK commands as JSON values instead of shell
text.  It never invokes a shell, does not expose Python evaluation, and keeps
game/archive writes behind both a process-level opt-in and the CLI's existing
acknowledgement checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Iterable

import click
from click.testing import CliRunner

from allin1_sdk.paths import user_data_root


PROTOCOL_VERSION = "1.0"
MAX_REQUEST_BYTES = 256 * 1024
MAX_OUTPUT_CHARS = 1024 * 1024
GAME_WRITE_COMMANDS = frozenset({
    "apply-rpf-plan",
    "install-package",
    "rollback-rpf-transaction",
    "uninstall-package",
})
AUTHORING_COMMANDS = frozenset({
    "add-ytd-texture",
    "add-rpf-program-node",
    "audit-folder",
    "build-native-workspace",
    "build-binary-workspace",
    "build-gxt2-workspace",
    "build-rpf-tree",
    "build-rpf-graph",
    "catalog-rpfs",
    "canary-rpf-transaction",
    "compile-vehicle-data",
    "compile-oiv-xml",
    "configure-rpf-program-node",
    "connect-rpf-program-nodes",
    "create-rpf-change-set",
    "create-rpf-program",
    "create-rpf-graph",
    "diff-meta",
    "diff-rpf",
    "defragment-rpf",
    "dlc-inventory",
    "extract-rpf-entry",
    "extract-rpf-subtree",
    "add-rpf-graph-container",
    "add-rpf-graph-file",
    "export-native-workspace",
    "export-rpf-binary-workspace",
    "export-rpf-gxt2-workspace",
    "export-rpf-native-workspace",
    "import-package",
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
    "position-rpf-graph-node",
    "position-rpf-program-node",
    "refresh-rpf-graph-sources",
    "remove-rpf-graph-node",
    "remove-rpf-program-node",
    "rename-rpf-graph-node",
    "reparent-rpf-graph-node",
    "run-rpf-program",
    "stage-rpf-change",
    "disconnect-rpf-program-node",
    "remove-ytd-texture",
    "replace-ytd-texture",
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


def _cli_group() -> click.Group:
    from allin1_sdk.cli import main

    return main


def command_risk(command: str) -> str:
    """Classify a command for agents and the audit trail."""
    if command in GAME_WRITE_COMMANDS:
        return "game_write"
    if command in AUTHORING_COMMANDS:
        return "authoring_write"
    return "read_only"


def _parameter_schema(parameter: click.Parameter) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": parameter.name,
        "required": bool(parameter.required),
        "type": parameter.type.name,
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
        catalog.append({
            "name": name,
            "description": command.get_short_help_str(),
            "risk": command_risk(name),
            "parameters": [_parameter_schema(item) for item in command.params],
        })
    return catalog


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
        return _response(request_id, ok=True, result=command_catalog())
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
    risk = command_risk(command_name)
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
    response = _response(
        request_id, ok=result.exit_code == 0, risk=risk,
        result={
            "command": command_name,
            "exit_code": result.exit_code,
            "output": output,
            "output_truncated": truncated,
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
