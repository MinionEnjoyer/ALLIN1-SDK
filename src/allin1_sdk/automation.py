"""Public, headless access to the same reviewed workflows as the desktop.

These are domain adapters, not a UI-control or arbitrary Python execution API.
An apply always rechecks the caller's exact request, review digest and inputs.
"""
from __future__ import annotations

import json
from pathlib import Path

from allin1_sdk import workspace_desktop as workspace
from allin1_sdk.release_paths import strict_json, no_links

MAX_PAYLOAD_BYTES = 1024 * 1024

# Discovery hints, not a second validator. The shared domain owns requirements.
WORKFLOWS = {
    "graph": ("Package layout", ["source", "workspace"], ["create", "save", "import_package", "import_archive", "expand", "analyze", "refresh", "materialize", "build", "plan_origin", "preview_bundle"]),
    "program": ("Build flow", ["graph", "template", "workspace"], ["create", "save", "plan", "run"]),
    "binary": ("Binary editor", ["source", "workspace", "archive", "entry_id", "gta_path"], ["create", "patch", "undo", "build"]),
    "maps": ("Map workbench", ["descriptor", "source"], ["create", "save", "build"]),
    "code": ("XML, JSON and Lua editors", ["source", "document"], ["save", "save_copy"]),
    "native": ("Native resources and audio", ["source", "workspace", "archive", "entry_id", "gta_path", "edition"], ["export", "save_xml", "export_dependency", "export_validation", "build", "plan_replacement"]),
    "data_tools": ("Data tools and asset/build diagnostics", ["source", "comparison", "document", "task", "settings", "edition", "gta_path", "crash_event"], ["export"]),
    "optimization": ("Reversible package optimization", ["source", "workspace", "comparison", "settings", "edition", "gta_path", "preview_region"], ["export", "recover"]),
    "recipe": ("Package recipes", ["source", "edition"], ["managed", "batches", "created", "compile"]),
    "vehicle_identity": ("Vehicle identity", ["workspace", "model"], ["migrate"]),
    "vehicle_hitches": ("Vehicle hitches", ["workspace", "model"], ["configure"]),
    "runtime": ("Story controller", ["toolchain"], ["build"]),
    "render": ("Render studio", ["source", "gta_path", "blender_executable", "settings", "camera", "render"], ["export"]),
}

WORKBENCH_ROUTES = {
    "vehicle-hitches": {"module": "vehicle_hitches"},
    "native-resources": {"module": "native"},
    "archive-browser": {"desktop_operations": ["browse_game_files", "search_game_files"], "note": "Read-only typed browser jobs preserve exact archive member identities; reviewed outputs use archive utilities."},
    "xml-editor": {"module": "code"}, "lua-editor": {"module": "code"},
    "data-tools": {"module": "data_tools", "additional_module": "optimization"}, "map-workbench": {"module": "maps"},
    "story-runtime": {"module": "runtime"}, "render-studio": {"module": "render"},
    "rpf-binary": {"module": "binary"}, "rpf-package-layout": {"module": "graph"},
    "rpf-build-flow": {"module": "program"}, "package-recipes": {"module": "recipe"},
    "application-shell": {"commands": ["authoring-catalog"], "note": "Headless discovery replaces window navigation; CLI --version identifies this build."},
    "help-center": {"commands": ["authoring-catalog"], "note": "Agent catalog includes command help and parameter schemas; CLI commands expose --help."},
    "package-linker": {"commands": ["validate", "link"]},
    "asset-viewer": {"commands": ["inspect-native-asset", "export-native-workspace"]},
    "package-receipts": {"commands": ["list-installed-packages", "inspect-package-receipt", "verify-package-ownership"]},
    "quick-import": {"commands": ["inspect-vehicle-quick-import", "prepare-vehicle-quick-import", "plan-managed-vehicle-package", "publish-managed-vehicle-package", "export-legacy-vehicle-oiv"]},
    "vehicle-workbench": {"commands": ["inspect-vehicle-project", "create-vehicle-authoring", "inspect-vehicle-authoring", "set-vehicle-fields", "set-vehicle-axles", "build-vehicle-package", "undo-vehicle-edit", "redo-vehicle-edit"], "additional_module": "vehicle_identity"},
    "weapon-workbench": {"commands": ["create-weapon-authoring", "inspect-weapon-authoring", "set-weapon-fields", "set-weapon-component", "set-weapon-attachment", "inspect-weapon-animation", "clone-weapon-animation", "plan-weapon-clone", "clone-weapon-bundle", "undo-weapon-edit"]},
    "ped-workbench": {"commands": ["create-ped-authoring", "inspect-ped-authoring", "set-ped-fields", "inspect-ped-ymt", "plan-ped-clone", "clone-ped-bundle", "undo-ped-edit"]},
    "models-materials": {"commands": ["inspect-model-materials", "create-material-workspace", "inspect-material-workspace", "set-material-binding", "set-geometry-material", "build-material-workspace", "undo-material-edit"]},
    "texture-dictionaries": {"commands": ["export-native-workspace", "list-ytd-textures", "add-ytd-texture", "replace-ytd-texture", "remove-ytd-texture", "undo-ytd-texture-edit", "build-native-workspace"]},
    "rpf-archive-inspection": {"commands": ["index-rpf", "inspect-rpf", "inspect-rpf-native-entry", "extract-rpf-entry"]},
    "rpf-archive-utilities": {"commands": ["verify-rpf-archive", "defragment-rpf"]},
    "rpf-game-text": {"commands": ["export-rpf-gxt2-workspace", "list-gxt2-entries", "set-gxt2-text", "add-gxt2-entry", "remove-gxt2-entry", "undo-gxt2-edit", "build-gxt2-workspace"]},
    "rpf-change-sets": {"commands": ["create-rpf-change-set", "stage-rpf-change", "move-rpf-change", "unstage-rpf-change", "inspect-rpf-change-set", "plan-rpf-change-set"]},
    "rpf-transactions": {"commands": ["list-rpf-transactions", "apply-rpf-plan", "verify-rpf-transaction", "rollback-rpf-transaction", "recover-rpf-transaction"], "note": "Live writes require process-level --allow-game-writes plus command acknowledgement."},
    "sdk-console": {"commands": ["authoring-catalog"], "note": "Agent catalog/execute is the headless console; use named parameters or argv, never shell text."},
    "qwen-assistant": {"commands": ["assistant"], "note": "See catalog.subcommands for status/context/prompt/review/stop. Advice is not execution authority."},
    "update-check": {"commands": ["check-sdk-update"]},
}


def authoring_catalog():
    return {
        "schema_version": 1,
        "operations": {
            "inspect": {"cli": "inspect-authoring-workspace", "python": "allin1_sdk.automation.inspect_authoring", "risk": "authoring_write", "note": "Read-only sources; native preflight/render inspection can create external caches and run tools."},
            "review": {"cli": "review-authoring-action", "python": "allin1_sdk.automation.review_authoring", "risk": "read_only"},
            "apply": {"cli": "apply-authoring-action", "python": "allin1_sdk.automation.apply_authoring", "risk": "authoring_write", "acknowledgement": "--acknowledge-authoring"},
        },
        "modules": [{"module": key, "title": value[0], "input_choices": value[1], "actions": value[2]} for key, value in WORKFLOWS.items()],
        "workbench_routes": [{"workbench": key, **value} for key, value in WORKBENCH_ROUTES.items()],
        "inspect_fields": sorted(workspace._INSPECT_FIELDS),
        "review_fields": sorted(workspace._REVIEW_FIELDS),
        "sequence": [
            "Inspect the input; retain its state_sha256 and document. New documents can start with create.",
            "Review the exact action with expected_state_sha256, draft fields and a new external destination where required.",
            "Read the returned review. No final output has been applied by review.",
            "After user approval, apply the identical request plus review_sha256 and explicit acknowledgement.",
            "Use the returned session, output hashes and receipts; re-inspect before the next edit. Never reuse stale approvals.",
        ],
        "path_policy": "Absolute local input paths; no traversal, links, game-owned authoring outputs or silent overwrite.",
        "payload_limit_bytes": MAX_PAYLOAD_BYTES,
        "native_requirements": {"runtime": "Successful compiler/CMake/CTest/x64 probe", "render": "Matching GTA decoder and Blender", "graph": "Matching GTA decoder for native RPF import/build; loose graphs do not require GTA"},
        "game_writes_supported": False,
        "other_workflows": "Use the Agent command catalog for vehicle, weapon, ped, material, texture, package lifecycle and archive transaction commands. Their own acknowledgements still apply.",
    }


def _payload(value):
    if not isinstance(value, dict):
        raise ValueError("Authoring request must be a JSON object")
    rendered = json.dumps(value, allow_nan=False, ensure_ascii=False)
    if len(rendered.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValueError("Authoring request exceeds 1 MiB; split the workflow into bounded actions")
    return value


def read_payload(request_file: Path | None, request_json: str | None):
    if (request_file is None) == (request_json is None):
        raise ValueError("Choose exactly one of --request-file or --request-json")
    if request_file is not None:
        selected = no_links(request_file.expanduser().absolute())
        with selected.open("rb") as stream:
            content = stream.read(MAX_PAYLOAD_BYTES + 1)
        if len(content) > MAX_PAYLOAD_BYTES:
            raise ValueError("Authoring request exceeds 1 MiB")
        value = strict_json(content.decode("utf-8-sig"))
    else:
        if len(request_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("Authoring request exceeds 1 MiB")
        value = strict_json(request_json)
    return _payload(value)


def inspect_authoring(payload):
    return workspace.inspect(_payload(payload))


def review_authoring(payload):
    return workspace.review(_payload(payload))


def apply_authoring(payload):
    return workspace.apply(_payload(payload))


def check_sdk_update():
    """Read official release metadata only; do not download or install binaries."""
    from allin1_sdk import __version__
    from allin1_sdk.self_update import fetch_latest_release, update_available
    release = fetch_latest_release()
    return {"current_version": __version__, "latest_version": release.version,
            "update_available": update_available(__version__, release.version), "name": release.name,
            "page_url": release.page_url, "archive_name": release.archive_name, "archive_size": release.archive_size}


def register_commands(group):
    import click

    @group.command("authoring-catalog")
    def catalog_command():
        """Discover headless inspect/review/apply workflows, fields and safety gates."""
        click.echo(json.dumps(authoring_catalog(), ensure_ascii=False))

    commands = [catalog_command]
    @group.command("check-sdk-update")
    def update_command():
        """Check the official SDK release metadata as JSON; never install an update."""
        try:
            click.echo(json.dumps(check_sdk_update(), ensure_ascii=False))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
    commands.append(update_command)
    for operation, name, handler in (
        ("inspect", "inspect-authoring-workspace", inspect_authoring),
        ("review", "review-authoring-action", review_authoring),
        ("apply", "apply-authoring-action", apply_authoring),
    ):
        def make_callback(selected_operation, selected_handler):
            def callback(request_file, request_json, acknowledge_authoring=False):
                try:
                    payload = read_payload(request_file, request_json)
                    if selected_operation == "apply":
                        if not acknowledge_authoring:
                            raise ValueError("Apply requires --acknowledge-authoring after reviewing this exact request")
                        payload = {**payload, "authoring_confirmed": True}
                    result = selected_handler(payload)
                    click.echo(json.dumps(result, ensure_ascii=False, allow_nan=False))
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    raise click.ClickException(str(exc)) from exc
            return callback
        callback = make_callback(operation, handler)
        callback = click.option("--request-json", help="One JSON request object; use a file for large or shell-sensitive input.")(callback)
        callback = click.option("--request-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="UTF-8 JSON request, at most 1 MiB.")(callback)
        if operation == "apply":
            callback = click.option("--acknowledge-authoring", is_flag=True, help="Approve the exact reviewed external-authoring action.")(callback)
        commands.append(group.command(name, help=f"{operation.title()} a typed authoring request using the desktop's shared validation. Returns JSON; no GUI required.")(callback))
    return tuple(commands)
