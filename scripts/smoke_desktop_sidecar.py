"""Packaged smoke test for the persistent ALLIN1 desktop sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import struct
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

from PIL import Image
from allin1_sdk.release_identity import sha256, verify_inventory


PROTOCOL = "1.0.0"


def request(request_id: str, operation: str, payload: dict) -> dict:
    return {
        "protocol_version": PROTOCOL,
        "request_id": request_id,
        "job_id": None,
        "operation": operation,
        "payload": payload,
        "sequence": 0,
        "risk": "none",
        "terminal": False,
    }


def exchange(process: subprocess.Popen[str], message: dict) -> dict:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        diagnostics = process.stderr.read() if process.stderr else ""
        raise RuntimeError(f"sidecar closed before responding: {diagnostics[-4000:]}")
    response = json.loads(line)
    required = {
        "protocol_version", "request_id", "job_id", "operation", "payload",
        "sequence", "risk", "terminal",
    }
    if set(response) != required:
        raise RuntimeError("sidecar returned an invalid envelope shape")
    if response["protocol_version"] != PROTOCOL:
        raise RuntimeError("sidecar negotiated an unexpected protocol")
    if response["request_id"] != message["request_id"]:
        raise RuntimeError("sidecar response belongs to another request")
    return response


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--resource-home", type=Path, required=True)
    parser.add_argument("--build-identity", type=Path, help="Require this exact frozen candidate identity")
    parser.add_argument(
        "--blender-executable", type=Path,
        default=os.environ.get("ALLIN1_BLENDER_EXECUTABLE") or None,
        help="Optional Blender executable for the packaged Render Studio happy path",
    )
    parser.add_argument("--rpf-game-path", type=Path, help="Optional matching GTA installation for real root/nested GXT2 archive intake checks; game files are read only")
    parser.add_argument("--rpf-launcher-source", type=Path, help="Optional Launcher src directory to install the exported member ZIP only in a temporary game")
    options = parser.parse_args()
    if options.rpf_launcher_source and not options.rpf_game_path:
        parser.error("--rpf-launcher-source requires --rpf-game-path")
    executable = options.sidecar.resolve(strict=True)
    resource_home = options.resource_home.resolve(strict=True)
    verify_inventory(resource_home)
    tested_binary = sha256(executable)
    tested_resources = sha256(resource_home / "resource-checksums.json")
    preview_cache_context = tempfile.TemporaryDirectory(
        prefix="allin1-sidecar-preview-cache-",
    )
    preview_cache = Path(preview_cache_context.name).resolve()
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "ALLIN1_DESKTOP_PYTHON", "ALLIN1_DESKTOP_SIDECAR", "ALLIN1_GTA_PATH"):
        environment.pop(key, None)
    environment["ALLIN1_SDK_HOME"] = str(resource_home)
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        environment[key] = str(preview_cache / "user")
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
    environment["DOTNET_ROOT"] = str(preview_cache / "no-dotnet")
    environment["ALLIN1_PREVIEW_DIR"] = str(preview_cache)
    helper = subprocess.run(
        [str(resource_home / "tools" / "RpfPatcher" / "RpfPatcher.exe")],
        cwd=preview_cache, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if helper.returncode != 1 or "Usage:" not in helper.stderr:
        raise RuntimeError(f"Bundled RPF helper did not start independently: {helper.stderr[-2000:]}")
    process = subprocess.Popen(
        [str(executable), "--allow-package-writes", "--allow-rpf-writes"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        env=environment, cwd=preview_cache,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        handshake = exchange(process, request("smoke-handshake", "handshake", {
            "client": {"name": "packaged-smoke", "version": "1.0.0"},
            "supported_versions": [PROTOCOL],
        }))
        if handshake["operation"] != "result":
            raise RuntimeError(f"sidecar handshake failed: {handshake}")
        if options.build_identity:
            expected_identity = json.loads(options.build_identity.read_bytes())
            if (handshake["payload"].get("build_identity") != expected_identity
                    or json.loads((resource_home / "build-identity.json").read_bytes()) != expected_identity
                    or handshake["payload"].get("sdk_version") != expected_identity["sdk_version"]):
                raise RuntimeError("Frozen sidecar, resources, and candidate identities do not match")
        if (
            handshake["payload"].get("package_writes_enabled") is not True
            or handshake["payload"].get("rpf_writes_enabled") is not True
            or handshake["payload"].get("game_writes_enabled") is not False
        ):
            raise RuntimeError("packaged sidecar package authority was not least-privilege")
        catalog = exchange(process, request("smoke-catalog", "catalog", {}))
        assistant = exchange(process, request("smoke-assistant", "assistant_status", {}))
        if assistant["operation"] != "result" or assistant["payload"]["result"]["configured"]:
            raise RuntimeError("Clean-user assistant status must be an optional unconfigured state")
        saved_assistant = exchange(process, request("smoke-assistant-save", "configure_assistant", {
            "settings": {"mode": "disabled"}, "authoring_confirmed": True,
        }))
        expected_config = preview_cache / "user" / "ALLIN1-SDK" / "Assistant" / "config.json"
        if (
            saved_assistant["operation"] != "result"
            or saved_assistant["risk"] != "authoring_write"
            or not expected_config.is_file()
            or Path(saved_assistant["payload"]["result"]["path"]) != expected_config
        ):
            raise RuntimeError(f"Packaged SDK-owned settings save failed: {saved_assistant}")
        disabled_assistant = exchange(process, request("smoke-assistant-disabled", "assistant_status", {}))
        if (
            disabled_assistant["operation"] != "result"
            or not disabled_assistant["payload"]["result"]["configured"]
            or disabled_assistant["payload"]["result"]["enabled"]
        ):
            raise RuntimeError("Packaged assistant settings did not survive a fresh status read")
        if (preview_cache / "user" / "ALLIN1").exists():
            raise RuntimeError("Standalone startup must not initialize Launcher state")
        command_names = {item["name"] for item in catalog["payload"]["commands"]}
        if not {"inspect-rpf", "validate-package", "list-axle-prefabs", "link"} <= command_names:
            raise RuntimeError("packaged sidecar command catalog is incomplete")
        required_jobs = {
            "inspect_gxt2_workspace", "review_gxt2_action",
            "inspect_weapon_workbench", "review_weapon_authoring",
            "preview_asset", "inspect_rpf_archive", "inspect_vehicle_project",
            "inspect_vehicle_authoring_workspace",
            "review_vehicle_authoring_workspace", "review_vehicle_authoring_edit",
            "review_vehicle_authoring_appearance",
            "inspect_vehicle_authoring_tuning", "review_vehicle_authoring_tuning",
            "review_vehicle_authoring_light_profile", "review_vehicle_authoring_axles",
            "inspect_vehicle_authoring_axle_skeleton",
            "review_vehicle_authoring_transmission",
            "review_vehicle_authoring_distribution",
            "review_vehicle_package_build",
            "inspect_recipe",
            "inspect_package_receipts",
            "review_package_lifecycle",
            "inspect_vehicle_quick_import", "review_vehicle_quick_import",
            "review_vehicle_oiv_export",
            "review_vehicle_package_publish",
            "inspect_model_materials", "inspect_model_material_workspace",
            "review_model_material_workspace", "review_model_material_edit",
            "review_model_material_build",
            "inspect_texture_workspace", "review_texture_workspace",
            "preview_texture_workspace", "review_texture_edit",
            "review_texture_build",
        }
        if not required_jobs <= set(catalog["payload"]["job_operations"]):
            raise RuntimeError("packaged sidecar workspace contract is incomplete")
        for operation, payload, risk, message in (
            ("review_vehicle_oiv_export", {"edition": "enhanced"}, "read_only", "Legacy branch"),
            ("apply_vehicle_oiv_export", {}, "authoring_write", "explicit action-time"),
            ("review_vehicle_package_publish", {}, "read_only", "source_package"),
            ("apply_vehicle_package_publish", {}, "authoring_write", "explicit action-time"),
            ("apply_gxt2_action", {}, "authoring_write", "explicit action-time"),
        ):
            rejected = exchange(process, request(f"smoke-{operation}", operation, payload))
            if rejected["operation"] != "error" or rejected["risk"] != risk or message not in str(rejected["payload"]):
                raise RuntimeError(f"Packaged export safety contract failed: {rejected}")
        if "apply_vehicle_package_publish" in catalog["payload"]["job_operations"]:
            raise RuntimeError("Package publication must not be exposed as a cancellable job")
        if (
            "prepare_vehicle_quick_import" not in catalog["payload"]["operations"]
            or "prepare_vehicle_quick_import" in catalog["payload"]["job_operations"]
        ):
            raise RuntimeError("packaged guarded authoring contract is incomplete")
        if (
            "apply_package_lifecycle" not in catalog["payload"]["operations"]
            or "apply_package_lifecycle" in catalog["payload"]["job_operations"]
        ):
            raise RuntimeError("packaged guarded lifecycle contract is incomplete")
        if (
            "render_vehicle_model" not in catalog["payload"]["operations"]
            or "render_vehicle_model" in catalog["payload"]["job_operations"]
        ):
            raise RuntimeError("packaged vehicle viewport contract is incomplete")
        required_authoring_actions = {
            "apply_gxt2_action",
            "apply_weapon_authoring",
            "create_vehicle_authoring_workspace", "apply_vehicle_authoring_edit",
            "apply_vehicle_authoring_appearance",
            "apply_vehicle_authoring_tuning", "apply_vehicle_authoring_light_profile",
            "apply_vehicle_authoring_axles",
            "apply_vehicle_authoring_transmission",
            "apply_vehicle_authoring_distribution",
            "apply_vehicle_package_build",
            "apply_vehicle_authoring_history",
            "create_model_material_workspace", "apply_model_material_edit",
            "apply_model_material_history", "apply_model_material_build",
            "create_texture_workspace", "apply_texture_edit",
            "apply_texture_history", "apply_texture_build",
        }
        if not required_authoring_actions <= set(catalog["payload"]["operations"]):
            raise RuntimeError("packaged vehicle authoring contract is incomplete")
        if required_authoring_actions & set(catalog["payload"]["job_operations"]):
            raise RuntimeError("vehicle authoring mutations must not run as jobs")
        executed = exchange(process, request("smoke-execute", "execute", {
            "command": "list-axle-prefabs", "args": [],
        }))
        if executed["operation"] != "result" or executed["risk"] != "read_only":
            raise RuntimeError(f"packaged read-only command failed: {executed}")
        with tempfile.TemporaryDirectory(prefix="allin1-sidecar-smoke-") as directory:
            root = Path(directory)

            def inspect_workspace(label: str, payload: dict) -> dict:
                response = exchange(process, request(
                    f"smoke-workspace-{label}-inspect",
                    "inspect_authoring_workspace", payload,
                ))
                result = response["payload"].get("result", {})
                if (
                    response["operation"] != "result"
                    or response["risk"] != "read_only"
                    or result.get("kind") != "workspace_session"
                    or result.get("module") != payload.get("module")
                    or result.get("game_write_performed") is not False
                ):
                    raise RuntimeError(
                        f"Packaged {label} workspace inspection failed: {response}"
                    )
                return result

            def apply_workspace(label: str, payload: dict) -> dict:
                reviewed = exchange(process, request(
                    f"smoke-workspace-{label}-review",
                    "review_workspace_action", payload,
                ))
                review = reviewed["payload"].get("result", {})
                if (
                    reviewed["operation"] != "result"
                    or reviewed["risk"] != "read_only"
                    or review.get("kind") != "workspace_review"
                    or review.get("review_only") is not True
                    or review.get("game_write_performed") is not False
                ):
                    raise RuntimeError(
                        f"Packaged {label} workspace review failed: {reviewed}"
                    )
                applied = exchange(process, request(
                    f"smoke-workspace-{label}-apply",
                    "apply_workspace_action", {
                        **payload,
                        "review_sha256": review["review_sha256"],
                        "authoring_confirmed": True,
                    },
                ))
                result = applied["payload"].get("result", {})
                if (
                    applied["operation"] != "result"
                    or applied["risk"] != "authoring_write"
                    or result.get("kind") != "workspace_applied"
                    or result.get("module") != payload.get("module")
                    or result.get("game_write_performed") is not False
                ):
                    raise RuntimeError(
                        f"Packaged {label} workspace action failed: {applied}"
                    )
                return result

            def gxt_bytes(rows: dict[int, str]) -> bytes:
                strings, table = bytearray(), bytearray()
                start = 16 + 8 * len(rows)
                for key, value in sorted(rows.items()):
                    table.extend(struct.pack("<II", key, start + len(strings)))
                    strings.extend(value.encode("utf-8") + b"\0")
                return b"2TXG" + struct.pack("<I", len(rows)) + table + b"2TXG" + struct.pack("<I", start + len(strings)) + strings

            text_source = root / "original.gxt2"
            text_original = gxt_bytes({256: "Original label", 512: "Keep this label"})
            text_source.write_bytes(text_original)
            # Inert change-set staging also works without a game/toolchain installed.
            change_archive = root / "change-fixture.rpf"
            change_archive.write_bytes(b"temporary inert archive fixture")
            change_document = root / "changes.json"
            change_document.write_text(json.dumps({"schema_version": 1, "operation": "rpf_change_set",
                "archive": {"path": str(change_archive), "size": change_archive.stat().st_size, "edition": "enhanced",
                            "sha256": hashlib.sha256(change_archive.read_bytes()).hexdigest()}, "actions": []}), encoding="utf-8")
            change_opened = exchange(process, request("smoke-change-open", "inspect_rpf_change_set", {"change_set": str(change_document)}))
            if change_opened["operation"] != "result":
                raise RuntimeError(f"Packaged change-set inspection failed: {change_opened}")
            change_session = change_opened["payload"]["result"]

            def change_action(action, **fields):
                nonlocal change_session
                payload = {"action": action, **({"change_set": change_session["change_set"],
                    "expected_sha256": change_session["state_sha256"]} if action != "create" else {}), **fields}
                reviewed = exchange(process, request("smoke-change-review", "review_rpf_change_set", payload))
                if reviewed["operation"] != "result" or reviewed["risk"] != "read_only":
                    raise RuntimeError(f"Packaged change-set review failed: {reviewed}")
                review = reviewed["payload"]["result"]
                result = exchange(process, request("smoke-change-apply", "apply_rpf_change_set",
                    {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}))
                if result["operation"] != "result" or result["risk"] != "authoring_write":
                    raise RuntimeError(f"Packaged change-set save failed: {result}")
                value = result["payload"]["result"]
                if (value["archive_write_performed"] or value["game_write_performed"]
                        or value["session"]["actions"] != review["after"]
                        or hashlib.sha256(Path(value["output"]).read_bytes()).hexdigest() != value["output_sha256"]):
                    raise RuntimeError("Change-set save exceeded authority or differed from review")
                change_session = value["session"]
                return value

            change_action("stage", change={"action": "add", "entry": "global.gxt2", "payload": str(text_source)})
            change_action("stage", change={"action": "mkdir", "entry": "text"})
            change_id = change_session["actions"][1]["id"]
            change_action("move", action_id=change_id, position=1)
            change_action("remove", action_id=change_id)
            if change_archive.read_bytes() != b"temporary inert archive fixture":
                raise RuntimeError("Change-set staging modified its archive")
            print("Packaged change-set inspect / stage / reorder / remove passed; archive unchanged.", flush=True)
            inspected = exchange(process, request("smoke-gxt-inspect", "inspect_gxt2_workspace", {"source": str(text_source)}))
            if inspected["operation"] != "result" or inspected["risk"] != "read_only":
                raise RuntimeError(f"Packaged GXT2 inspection failed: {inspected}")
            text_session = inspected["payload"]["result"]

            def text_action(action: str, **fields: object) -> dict:
                nonlocal text_session
                binding = text_session.get("source_binding")
                context = ({"workspace": text_session["workspace"]} if text_session["workspace"] else
                    {"archive": binding["outer_archive"], "entry_id": binding["entry_id"], "gta_path": binding["gta_path"]} if binding else
                    {"source": text_session["source"]})
                payload = {"action": action, "expected_state_sha256": text_session["state_sha256"],
                    **context, **fields}
                reviewed = exchange(process, request(f"smoke-gxt-{action}-review", "review_gxt2_action", payload))
                if reviewed["operation"] != "result" or reviewed["risk"] != "read_only":
                    raise RuntimeError(f"Packaged GXT2 review failed: {reviewed}")
                review = reviewed["payload"]["result"]
                applied = exchange(process, request(f"smoke-gxt-{action}-apply", "apply_gxt2_action",
                    {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}))
                if applied["operation"] != "result" or applied["risk"] != "authoring_write":
                    raise RuntimeError(f"Packaged GXT2 action failed: {applied}")
                result = applied["payload"]["result"]
                if result["game_write_performed"] or not result["file_write_performed"]:
                    raise RuntimeError("GXT2 action exceeded copied-file authority")
                if action not in {"build", "package_rpf", "publish_rpf"}:
                    text_session = result["session"]
                elif action == "build" and result["sha256"] != review["output_sha256"]:
                    raise RuntimeError("Packaged GXT2 build did not match reviewed hash")
                return result

            text_action("create", destination=str(root / "game-text"))
            text_action("edit", label_hash=256, text="Vector — 日本語")
            text_action("add", label_hash=768, text="New label")
            text_action("remove", label_hash=512)
            text_action("undo")
            text_built = text_action("build", destination=str(root / "compiled.gxt2"))
            expected_text = gxt_bytes({256: "Vector — 日本語", 512: "Keep this label", 768: "New label"})
            if (Path(text_built["archive"]).read_bytes() != expected_text
                    or text_source.read_bytes() != text_original
                    or not Path(text_built["report"]).is_file()):
                raise RuntimeError("Packaged GXT2 round-trip or original preservation failed")

            if not options.rpf_game_path:
                # Generated unencrypted fixture archives need only an owned
                # directory context. Never discover or touch a user's GTA tree.
                sandbox_game = root / "owned-rpf-game-context"
                sandbox_game.mkdir()
                (sandbox_game / "GTA5_Enhanced.exe").write_bytes(b"MZ owned smoke marker")
                options.rpf_game_path = sandbox_game
            if options.rpf_game_path:
                game_context = options.rpf_game_path.resolve(strict=True)
                if not game_context.is_dir():
                    raise RuntimeError("RPF game context must be a directory")
                loose, nested = root / "rpf-root", root / "rpf-nested"
                loose.mkdir(); nested.mkdir()
                (loose / "global.gxt2").write_bytes(gxt_bytes({256: "Root dictionary"}))
                (nested / "global.gxt2").write_bytes(gxt_bytes({256: "Nested — 日本語"}))
                archive = root / "text-fixture.rpf"
                built = subprocess.run([str(resource_home / "tools" / "RpfPatcher" / "RpfPatcher.exe"),
                    "build-dlc", str(loose), str(archive), "--embed-rpf", str(nested), "x64/american.rpf"],
                    cwd=root, env=environment, capture_output=True, text=True, timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if built.returncode or not archive.is_file():
                    raise RuntimeError(f"GXT2 RPF fixture construction failed: {built.stderr}")
                archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
                utility_indexed = exchange(process, request(
                    "smoke-rpf-utility-index", "inspect_rpf_archive",
                    {"archive": str(archive), "gta_path": str(game_context)},
                ))
                utility_entries = utility_indexed.get("payload", {}).get("result", {}).get("entries", [])
                root_member = next((item for item in utility_entries if item.get("id") == "::global.gxt2"), None)
                directory = next((item for item in utility_entries if item.get("kind") == "directory"), None)
                if utility_indexed["operation"] != "result" or root_member is None or directory is None:
                    raise RuntimeError(f"Packaged RPF utility fixture index is incomplete: {utility_indexed}")

                def utility_action(action, destination, **fields):
                    payload = {"action": action, "archive": str(archive), "gta_path": str(game_context),
                               "destination": str(destination), **fields}
                    reviewed = exchange(process, request(f"smoke-rpf-utility-{action}-review", "review_rpf_utility", payload))
                    value = reviewed.get("payload", {}).get("result", {})
                    if (reviewed["operation"] != "result" or reviewed["risk"] != "read_only"
                            or not value.get("ready") or value.get("game_write_performed")):
                        raise RuntimeError(f"Packaged RPF utility review failed: {reviewed}")
                    unconfirmed = exchange(process, request(f"smoke-rpf-utility-{action}-unconfirmed", "apply_rpf_utility",
                        {**payload, "review_sha256": value["review_sha256"]}))
                    if unconfirmed["operation"] != "error" or Path(destination).exists():
                        raise RuntimeError("Packaged RPF utility accepted a missing confirmation")
                    applied = exchange(process, request(f"smoke-rpf-utility-{action}-apply", "apply_rpf_utility",
                        {**payload, "review_sha256": value["review_sha256"], "authoring_confirmed": True}))
                    result = applied.get("payload", {}).get("result", {})
                    if (applied["operation"] != "result" or applied["risk"] != "authoring_write"
                            or result.get("game_write_performed") or result.get("source_write_performed")
                            or not result.get("output_write_performed")
                            or hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash):
                        raise RuntimeError(f"Packaged RPF utility output failed verification: {applied}")
                    return result

                utility_action("extract_entry", root / "rpf-member-copy.gxt2", entry_id=root_member["id"])
                utility_action("extract_subtree", root / "rpf-subtree-copy", entry_id=directory["id"])
                utility_action("extract_archive", root / "rpf-archive-tree")
                comparison = root / "rpf-comparison-source.rpf"
                shutil.copy2(archive, comparison)
                utility_action("compare", root / "rpf-comparison.json",
                               compare_archive=str(comparison), comparison_mode="logical")
                utility_action("verify_integrity", root / "rpf-integrity.json")
                utility_action("defragment_copy", root / "rpf-defragmented.rpf")
                print("Packaged RPF extraction, subtree/archive export, comparison, integrity and defragmented-copy happy paths passed; source archive unchanged.", flush=True)
                for number, (member, expected) in enumerate((("::global.gxt2", "Root dictionary"), ("x64/american.rpf::global.gxt2", "Nested — 日本語"))):
                    intake = {"archive": str(archive), "entry_id": member, "gta_path": str(game_context)}
                    opened = exchange(process, request(f"smoke-packed-gxt-{number}", "inspect_gxt2_workspace", intake))
                    if opened["operation"] != "result" or opened["risk"] != "read_only":
                        raise RuntimeError(f"Packaged archive GXT2 intake failed: {opened}")
                    text_session = opened["payload"]["result"]
                    binding = text_session["source_binding"]
                    if (text_session["selected"]["text"] != expected or binding["entry_id"] != member
                            or binding["outer_archive_sha256"] != archive_hash or text_session["workspace"] is not None):
                        raise RuntimeError("Archive intake selected the wrong dictionary or lost provenance")
                    text_action("create", destination=str(root / f"packed-copy-{number}"))
                    text_action("edit", label_hash=256, text=expected + " — edited")
                    result = text_action("build", destination=str(root / f"packed-output-{number}.gxt2"))
                    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
                    if (Path(result["archive"]).read_bytes() != gxt_bytes({256: expected + " — edited"})
                            or report["source_binding"] != binding or hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash):
                        raise RuntimeError("Packed text round-trip lost provenance or modified its source archive")
                    packaged = text_action("package_rpf", destination=str(root / f"rpf-package-{number}"))
                    rpf_output = Path(packaged["archive"])
                    rpf_report = json.loads(Path(packaged["report"]).read_text(encoding="utf-8"))
                    if (packaged["kind"] != "gxt2_rpf_packaged" or rpf_output.name != archive.name
                            or hashlib.sha256(rpf_output.read_bytes()).hexdigest() != packaged["sha256"]
                            or hashlib.sha256(Path(packaged["report"]).read_bytes()).hexdigest() != packaged["report_sha256"]
                            or rpf_report["status"] != "verified" or not rpf_report["source_unchanged"]
                            or sum(row["changed"] for row in rpf_report["verification"]) != 1):
                        raise RuntimeError("Packaged RPF output or verification report did not match")
                    for check_number, (check_id, check_text) in enumerate((("::global.gxt2", "Root dictionary"), ("x64/american.rpf::global.gxt2", "Nested — 日本語"))):
                        checked = exchange(process, request(f"smoke-repacked-{number}-{check_number}", "inspect_gxt2_workspace",
                            {"archive": str(rpf_output), "entry_id": check_id, "gta_path": str(game_context)}))
                        expected_value = check_text + (" — edited" if check_id == member else "")
                        if checked.get("payload", {}).get("result", {}).get("selected", {}).get("text") != expected_value:
                            raise RuntimeError(f"Repacked RPF changed the wrong dictionary: {checked}")
                    if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
                        raise RuntimeError("RPF packaging modified its original archive")
                    published = text_action("publish_rpf", source_package=packaged["destination"],
                        destination=str(root / f"text-package-{number}.zip"),
                        package_metadata={"id": f"smoke.text-{number}", "name": "Smoke text package", "version": "1.0.0",
                                          "author": "Packaged smoke", "target": f"mods/update/{archive.name}"})
                    if (published["kind"] != "gxt2_rpf_published" or published["payload_sha256"] != packaged["sha256"]
                            or published["install_performed"] or published["upload_performed"]
                            or hashlib.sha256(Path(published["archive"]).read_bytes()).hexdigest() != published["sha256"]):
                        raise RuntimeError("Published ALLIN1 RPF ZIP did not match the verified build")
                    with zipfile.ZipFile(published["archive"]) as exported:
                        manifest = tomllib.loads(exported.read("mod.toml").decode("utf-8"))
                        if (manifest["editions"] != ["enhanced"] or manifest["dependencies"] != ["openrpf"]
                                or manifest["dlc_packs"] or len(manifest["files"]) != 1
                                or manifest["files"][0]["destination"] != f"mods/update/{archive.name}"
                                or hashlib.sha256(exported.read(manifest["files"][0]["source"])).hexdigest() != packaged["sha256"]):
                            raise RuntimeError("Exported ALLIN1 manifest/payload did not match the RPF build")
                        for item in published["members"]:
                            if hashlib.sha256(exported.read(item["path"])).hexdigest() != item["sha256"]:
                                raise RuntimeError("Published ALLIN1 ZIP member hash mismatch")
                    print(f"Packaged RPF {number}: exact member edit, archive verification and ALLIN1 ZIP publication passed.", flush=True)
                    member_destination = root / f"text-member-{number}.zip"
                    metadata = {"id": f"smoke.member-{number}", "name": "Smoke member patch", "version": "1.0.0",
                                "author": "Packaged smoke", "target": f"mods/update/{archive.name}"}
                    member_schema = 3 if number == 0 else 4
                    member_entry = "global.gxt2" if number == 0 else "x64/american.rpf!global.gxt2"
                    member_zip = text_action("publish_rpf", source_package=packaged["destination"],
                        destination=str(member_destination), package_metadata=metadata, publication_mode="member")
                    with zipfile.ZipFile(member_destination) as exported:
                        manifest = tomllib.loads(exported.read("mod.toml").decode("utf-8"))
                        entry = manifest["rpf_entries"][0]
                        if (manifest["schema_version"] != member_schema or manifest.get("files") or len(manifest["rpf_entries"]) != 1
                                or entry["entry"] != member_entry or entry["archive"] != metadata["target"]
                                or entry["original_sha256"] != hashlib.sha256(gxt_bytes({256: expected})).hexdigest()
                                or exported.read(entry["source"]) != gxt_bytes({256: expected + " — edited"})
                                or member_zip["publication_mode"] != "member" or member_zip["manifest_schema_version"] != member_schema
                                or any(name.endswith(".rpf") for name in exported.namelist())):
                            raise RuntimeError("Member-only ZIP did not match its exact dictionary and original checksum")
                    if options.rpf_launcher_source:
                        from smoke_rpf_member_install import verify_export
                        verify_export(member_destination, archive, resource_home / "tools/RpfPatcher/RpfPatcher.exe",
                                      game_context, options.rpf_launcher_source, gxt_bytes({256: "Nested — 日本語" if number == 0 else "Root dictionary"}))
                    print(f"Packaged member-only export passed (schema {member_schema}, no archive payload).", flush=True)
                print("Packaged root/nested GXT2 intake, edit, RPF build and ALLIN1 publication passed; original RPF SHA-256 unchanged.", flush=True)
                change_action("create", archive=str(archive), gta_path=str(game_context), destination=str(root / "native-changes.json"))
                for layer in ("", "x64/american.rpf"):
                    change_action("stage", change={"action": "replace", "archive_path": layer, "entry": "global.gxt2", "payload": str(text_source)})
                change_action("stage", change={"action": "mkdir", "entry": "authored"})
                compiled = change_action("compile", gta_path=str(game_context), authorized_root=str(root), destination=str(root / "native-plan.json"))
                plan = json.loads(Path(compiled["output"]).read_text(encoding="utf-8"))
                if (plan["status"] != "ready" or len(plan["changes"]) != 3
                        or [row["archive_path"] for row in plan["changes"][:2]] != ["", "x64/american.rpf"]
                        or any(row["original"]["sha256"] is None for row in plan["changes"][:2])
                        or hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash):
                    raise RuntimeError("Native change-set plan lost exact targets or modified the source")
                print("Packaged source-bound change-set creation and exact root/nested multi-entry plan export passed; archive unchanged.", flush=True)

                def transaction_action(action, source, live=False):
                    clearing = action == "clear_lock"
                    inspected = exchange(process, request("smoke-transaction-inspect", "inspect_rpf_transaction", {
                        "source": str(source), "gta_path": str(game_context)}))
                    session = inspected["payload"].get("result", {})
                    if inspected["operation"] != "result" or session.get("source") != str(source):
                        raise RuntimeError(f"Packaged transaction inspection failed: {inspected}")
                    payload = {"source": str(source), "gta_path": str(game_context), **({} if live else {"authorized_root": str(root)}),
                               "action": action, "expected_sha256": session["state_sha256"]}
                    reviewed = exchange(process, request("smoke-transaction-review", "review_rpf_transaction", payload))
                    value = reviewed["payload"].get("result", {})
                    if reviewed["operation"] != "result" or not value.get("review_only"):
                        raise RuntimeError(f"Packaged transaction review failed: {reviewed}")
                    unconfirmed = exchange(process, request("smoke-transaction-unconfirmed", "apply_rpf_transaction", {
                        **payload, "review_sha256": value["review_sha256"]}))
                    if unconfirmed["operation"] != "error":
                        raise RuntimeError("Packaged archive transaction accepted missing confirmation")
                    if live and action != "recover":
                        missing_game = exchange(process, request("smoke-live-unconfirmed", "apply_rpf_transaction", {
                            **payload, "review_sha256": value["review_sha256"],
                            **({"lock_clear_confirmed": True} if clearing else {"archive_write_confirmed": True})}))
                        if missing_game["operation"] != "error":
                            raise RuntimeError("Live mods transaction accepted missing game confirmation")
                    applied = exchange(process, request("smoke-transaction-apply", "apply_rpf_transaction", {
                        **payload, "review_sha256": value["review_sha256"],
                        **({"lock_clear_confirmed": True} if clearing else {"receipt_write_confirmed": True} if action == "recover" else {"archive_write_confirmed": True}),
                        **({"game_write_confirmed": True} if live and action != "recover" else {})}))
                    result = applied["payload"].get("result", {})
                    state = result.get("session", {})
                    if (applied["operation"] != "result" or applied["risk"] != ("game_write" if live and action != "recover" else "authoring_write")
                            or result.get("archive_write_performed") is not (action not in {"recover", "clear_lock"})
                            or result.get("receipt_write_performed") is not (not clearing)
                            or result.get("lock_write_performed") is not clearing
                            or result.get("game_write_performed") is not (live and action != "recover")
                            or not state.get("verification", {}).get("healthy")
                            or state.get("archive_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest()):
                        raise RuntimeError(f"Packaged archive transaction did not verify: {applied}")
                    return state

                def cleanup_stale_lock(state, live=False):
                    source = Path(state["source"])
                    lock = archive.with_name(f".{archive.name}.allin1.lock")
                    before = {path: path.read_bytes() for path in (archive, source, Path(state["backup"]["path"]))}
                    owner = {"pid": os.getpid(), "plan_id": state["plan_id"], "created_at": "2026-09-04T00:00:00Z"}
                    lock.write_text(json.dumps(owner))
                    blocked = exchange(process, request("smoke-active-lock", "review_rpf_transaction", {
                        "source": str(source), "gta_path": str(game_context), **({} if live else {"authorized_root": str(root)}),
                        "action": "clear_lock", "expected_sha256": state["state_sha256"]}))
                    if blocked["operation"] != "error" or "still running" not in str(blocked["payload"]):
                        raise RuntimeError("Packaged lock cleanup did not refuse the active smoke process")
                    with subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as probe:
                        probe.wait(timeout=15)
                        owner["pid"] = probe.pid
                    raw = json.dumps(owner).encode("utf-8")
                    lock.write_bytes(raw)
                    cleaned = transaction_action("clear_lock", source, live=live)
                    retained = source.parent / f"cleared-lock-{hashlib.sha256(raw).hexdigest()}.json"
                    if (lock.exists() or cleaned["archive_lock"] is not None or retained.read_bytes() != raw
                            or any(path.read_bytes() != content for path, content in before.items())):
                        raise RuntimeError("Packaged stale-lock cleanup lost evidence or changed the transaction")
                    print(f"Packaged {'mods-copy' if live else 'external'} lock cleanup retained exact evidence; archive, receipt and backup unchanged.", flush=True)

                executed = transaction_action("execute", Path(compiled["output"]))
                receipt = Path(executed["source"])
                if (executed["status"] != "applied" or len(executed["changes"]) != 3
                        or not receipt.is_relative_to(preview_cache)
                        or executed["backup"]["sha256"] != archive_hash
                        or hashlib.sha256(Path(executed["backup"]["path"]).read_bytes()).hexdigest() != archive_hash):
                    raise RuntimeError("Packaged transaction lost its isolated receipt or original backup")
                applied_bytes = archive.read_bytes()
                cleanup_stale_lock(executed)
                archive.write_bytes(applied_bytes + b"external edit fixture")
                refused = exchange(process, request("smoke-rollback-external-refusal", "review_rpf_transaction", {
                    "source": str(receipt), "gta_path": str(game_context), "authorized_root": str(root),
                    "action": "rollback", "expected_sha256": executed["state_sha256"]}))
                if refused["operation"] != "error" or archive.read_bytes() != applied_bytes + b"external edit fixture":
                    raise RuntimeError("Rollback did not refuse an externally changed fixture")
                archive.write_bytes(applied_bytes)
                restored = transaction_action("rollback", receipt)
                if restored["status"] != "rolled_back" or hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
                    raise RuntimeError("Packaged rollback did not restore the exact original archive")
                print("Packaged root/nested execution, receipt verification, external-change refusal and exact rollback passed; real GTA untouched.", flush=True)

                # Exercise the exact frozen live-write path in an isolated game tree.
                # Copy only the executable used for read-only decoding keys; never launch it.
                fake_game = root / "transaction-game"
                fake_game.mkdir()
                game_exe = next((game_context / name for name in ("GTA5_Enhanced.exe", "GTA5.exe") if (game_context / name).is_file()), None)
                if game_exe is None or game_exe.stat().st_size > 256 * 1024**2:
                    raise RuntimeError("Native live smoke requires a bounded local GTA decoding executable")
                shutil.copy2(game_exe, fake_game / game_exe.name)
                live_archive = fake_game / "mods" / "update" / "live-fixture.rpf"
                live_archive.parent.mkdir(parents=True)
                shutil.copy2(archive, live_archive)
                assert fake_game.is_relative_to(root) and live_archive.is_relative_to(fake_game / "mods")
                game_context, archive = fake_game, live_archive
                change_action("create", archive=str(archive), gta_path=str(game_context), destination=str(root / "live-changes.json"))
                for layer in ("", "x64/american.rpf"):
                    change_action("stage", change={"action": "replace", "archive_path": layer, "entry": "global.gxt2", "payload": str(text_source)})
                live_compiled = change_action("compile", gta_path=str(game_context), destination=str(root / "live-plan.json"))
                live_plan = json.loads(Path(live_compiled["output"]).read_text())
                if live_plan["target_scope"] != "mods_copy" or live_plan["status"] != "ready":
                    raise RuntimeError("Packaged live fixture did not compile to a ready mods scope")
                executed = transaction_action("execute", Path(live_compiled["output"]), live=True)
                receipt = Path(executed["source"])
                history = exchange(process, request("smoke-history", "list_rpf_transactions", {}))
                if history["operation"] != "result" or not any(row["source"] == str(receipt) for row in history["payload"]["result"]["receipts"]):
                    raise RuntimeError("Packaged retained history omitted the live transaction")
                applied_bytes = archive.read_bytes()
                interrupted_receipt = json.loads(receipt.read_text()); interrupted_receipt["status"] = "verified_staging"
                receipt.write_text(json.dumps(interrupted_receipt))
                recovered = transaction_action("recover", receipt, live=True)
                if recovered["status"] != "applied" or archive.read_bytes() != applied_bytes:
                    raise RuntimeError("Packaged receipt reconciliation changed the archive")
                cleanup_stale_lock(recovered, live=True)
                restored = transaction_action("rollback", receipt, live=True)
                if restored["status"] != "rolled_back" or hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
                    raise RuntimeError("Packaged mods rollback did not restore exact original bytes")
                stock_plan = {**live_plan, "archive": str(fake_game / "update" / "stock.rpf")}
                stock_source = root / "stock-refusal.json"; stock_source.write_text(json.dumps(stock_plan))
                stock = exchange(process, request("smoke-stock-refusal", "inspect_rpf_transaction", {"source": str(stock_source)}))
                if stock["operation"] != "error" or "Stock GTA" not in str(stock["payload"]):
                    raise RuntimeError("Packaged transaction accepted a stock-game archive")
                print("Packaged mods-copy execute/rollback, dual confirmation, history, interrupted-receipt recovery and stock refusal passed in a temporary game; real GTA untouched.", flush=True)

            weapon_source = root / "weapon-source"
            weapon_source.mkdir()
            weapon_xml = b'<CWeaponInfoBlob><Infos><Item type="CWeaponInfo"><Name>WEAPON_SMOKE</Name><Model>w_pi_smoke</Model><Slot ref="SLOT_PISTOL"/><AmmoInfo ref="AMMO_SMOKE"/><HumanNameHash>WT_SMOKE</HumanNameHash><StatName>ST_SMOKE</StatName><!-- preserve --><AttachPoints><Item><AttachBone>WAPClip</AttachBone><Components><Item><Name>COMPONENT_SMOKE_CLIP</Name><Default value="true"/></Item></Components></Item></AttachPoints></Item></Infos></CWeaponInfoBlob>'
            weapon_xml = weapon_xml.replace(b"</StatName>", b'</StatName><FirstPersonScopeOffset x="0.00000" y="0.0000" z="-0.014"/><FirstPersonScopeFov value="30"/><WeaponFlags>Gun Automatic FutureFlag</WeaponFlags>')
            (weapon_source / "weapons.meta").write_bytes(weapon_xml)
            weapon_xml = weapon_xml.replace(b"</StatName>", b'</StatName><TimeBetweenShots value="0.118000"/>')
            (weapon_source / "weapons.meta").write_bytes(weapon_xml)
            component_xml = b'<CWeaponComponentInfoBlob><Infos><Item type="CWeaponComponentClipInfo"><Name>COMPONENT_SMOKE_CLIP</Name><Model>w_at_smoke_clip</Model><LocName>WCT_SMOKE</LocName><LocDesc>WCD_SMOKE</LocDesc><AttachBone>WAPClip</AttachBone><!-- preserve component --></Item></Infos></CWeaponComponentInfoBlob>'
            (weapon_source / "weaponcomponents.meta").write_bytes(component_xml)
            (weapon_source / "ammo.meta").write_bytes(b'<CWeaponInfoBlob><AmmoInfos><Item type="CAmmoInfo"><Name>AMMO_SMOKE</Name><Model>w_ammo_smoke</Model><AmmoMax value="240"/><Explosion>NONE</Explosion><TrailFx>NULL</TrailFx><PrimedFx>NULL</PrimedFx></Item></AmmoInfos></CWeaponInfoBlob>')
            (weapon_source / "weaponanimations.meta").write_bytes(b'<CWeaponAnimationsSets><Sets><Item key="DEFAULT"><WeaponAnimations><Item key="WEAPON_SMOKE"><Clip ref="clip_smoke"/></Item></WeaponAnimations></Item></Sets></CWeaponAnimationsSets>')
            (weapon_source / "weapon_shop.meta").write_bytes(b'<WeaponShopItemArray><weaponShopItems><Item><nameHash>WEAPON_SMOKE</nameHash><cost value="900"/><textLabel>WT_SMOKE</textLabel></Item></weaponShopItems></WeaponShopItemArray>')
            (weapon_source / "stream").mkdir()
            for asset in ("w_pi_smoke", "w_pi_clone", "w_at_smoke_clip", "w_ammo_smoke"):
                (weapon_source / "stream" / f"{asset}.ydr").write_bytes(b"asset:" + asset.encode("ascii"))
            weapon_originals = {path.relative_to(weapon_source): path.read_bytes() for path in weapon_source.rglob("*") if path.is_file()}
            weapon_inspection = exchange(process, request("smoke-weapons", "inspect_weapon_workbench", {"source": str(weapon_source)}))
            if weapon_inspection["payload"].get("result", {}).get("selected_weapon") != "WEAPON_SMOKE":
                raise RuntimeError(f"Packaged weapon inspection failed: {weapon_inspection}")
            if len(weapon_inspection["payload"]["result"].get("camera_fields", [])) != 4:
                raise RuntimeError("Packaged scope inspection omitted the existing camera fields")
            if weapon_inspection["payload"]["result"]["values"]["values"].get("weapon.roundsPerMinute") != "508.474576":
                raise RuntimeError("Packaged RPM inspection did not resolve the native shot interval")

            def weapon_action(label: str, payload: dict) -> dict:
                reviewed = exchange(process, request(f"smoke-weapon-{label}-review", "review_weapon_authoring", payload))
                if reviewed["operation"] != "result" or reviewed["risk"] != "read_only":
                    raise RuntimeError(f"Packaged weapon review failed: {reviewed}")
                if payload["action"] == "clone" and not reviewed["payload"]["result"]["clone_plan"]["ready"]:
                    raise RuntimeError(f"Packaged clone fixture is not ready: {reviewed['payload']['result']['clone_plan']}")
                applied = exchange(process, request(f"smoke-weapon-{label}-apply", "apply_weapon_authoring", {
                    **payload, "authoring_confirmed": True,
                    "review_sha256": reviewed["payload"]["result"]["review_sha256"],
                }))
                if applied["operation"] != "result" or applied["risk"] != "authoring_write":
                    raise RuntimeError(f"Packaged weapon action failed: {applied}")
                result = applied["payload"]["result"]
                if result["game_write_performed"] or not result["workspace_write_performed"]:
                    raise RuntimeError("Weapon action crossed its workspace-only write boundary")
                return result

            weapon_copy = weapon_action("copy", {"action": "create", "source": str(weapon_source), "parent": str(root), "name": "weapon-copy"})
            weapon_edit = weapon_action("edit", {"action": "edit", "workspace": weapon_copy["workspace"], "expected_revision": 0,
                                                "weapon": "WEAPON_SMOKE", "updates": {"weapon.slot": "SLOT_TEST",
                                                    "weapon.roundsPerMinute": "1200",
                                                    "weapon.firstPersonScopeOffset.z": "0.0180", "weapon.weaponFlags": "Gun FutureFlag"}})
            if weapon_edit["values"]["values"]["weapon.timeBetweenShots"] != "0.05" or weapon_edit["values"]["values"]["weapon.roundsPerMinute"] != "1200":
                raise RuntimeError("Packaged RPM edit did not round-trip")
            if weapon_edit["revision"] != 1 or weapon_edit["values"]["values"]["weapon.slot"] != "SLOT_TEST":
                raise RuntimeError("Packaged weapon edit did not save the reviewed field")
            if weapon_edit["values"]["values"]["weapon.firstPersonScopeOffset.z"] != "0.0180" or weapon_edit["values"]["values"]["weapon.weaponFlags"] != "Gun FutureFlag":
                raise RuntimeError("Packaged scope/flag edit did not round-trip")
            weapon_undo = weapon_action("undo", {"action": "undo", "workspace": weapon_copy["workspace"], "expected_revision": 1})
            if weapon_undo["revision"] != 2 or (Path(weapon_undo["source"]) / "weapons.meta").read_bytes() != weapon_xml:
                raise RuntimeError("Packaged weapon undo did not restore exact source bytes")
            if (weapon_source / "weapons.meta").read_bytes() != weapon_xml:
                raise RuntimeError("Weapon authoring modified the original source")
            component_edit = weapon_action("component", {"action": "edit_component", "workspace": weapon_copy["workspace"],
                "expected_revision": 2, "component": "COMPONENT_SMOKE_CLIP", "updates": {"component.locName": "WCT_EDITED"}})
            if component_edit["editor_kind"] != "component" or component_edit["component_values"]["values"]["component.locName"] != "WCT_EDITED":
                raise RuntimeError("Packaged component edit did not preserve its selection and value")
            component_undo = weapon_action("component-undo", {"action": "undo", "workspace": weapon_copy["workspace"], "expected_revision": 3})
            if component_undo["editor_kind"] != "component" or (Path(component_undo["source"]) / "weaponcomponents.meta").read_bytes() != component_xml:
                raise RuntimeError("Packaged component undo did not restore exact bytes and selection")
            attachment_edit = weapon_action("attachment", {"action": "edit_attachment", "workspace": weapon_copy["workspace"],
                "expected_revision": 4, "weapon": "WEAPON_SMOKE", "component": "COMPONENT_SMOKE_CLIP", "updates": {"attachment.default": "false"}})
            if attachment_edit["editor_kind"] != "attachment" or attachment_edit["attachment_values"]["values"]["attachment.default"] != "false":
                raise RuntimeError("Packaged attachment edit did not save the exact link")
            attachment_undo = weapon_action("attachment-undo", {"action": "undo", "workspace": weapon_copy["workspace"], "expected_revision": 5})
            if attachment_undo["revision"] != 6 or attachment_undo["editor_kind"] != "attachment" or (Path(attachment_undo["source"]) / "weapons.meta").read_bytes() != weapon_xml:
                raise RuntimeError("Packaged attachment undo did not restore exact bytes and selection")
            if (weapon_source / "weaponcomponents.meta").read_bytes() != component_xml or (weapon_source / "weapons.meta").read_bytes() != weapon_xml:
                raise RuntimeError("Relationship authoring modified the original source")
            clone_payload = {"action": "clone", "workspace": weapon_copy["workspace"], "expected_revision": 6,
                "spec": {"donor_weapon": "WEAPON_SMOKE", "weapon_name": "WEAPON_CLONE", "slot": "SLOT_CLONE",
                         "model": "w_pi_clone", "human_name_hash": "WT_CLONE", "stat_name": "ST_CLONE",
                         "clone_ammo": True, "ammo_info": "AMMO_CLONE", "ammo_name": "AMMO_CLONE"}}
            cloned = weapon_action("clone", clone_payload)
            if cloned["revision"] != 7 or cloned["selected_weapon"] != "WEAPON_CLONE" or len(cloned["project"]["weapons"]) != 2:
                raise RuntimeError("Packaged clone did not create and select its reviewed weapon bundle")
            clone_undo = weapon_action("clone-undo", {"action": "undo", "workspace": weapon_copy["workspace"], "expected_revision": 7})
            if clone_undo["revision"] != 8 or clone_undo["selected_weapon"] != "WEAPON_SMOKE" or len(clone_undo["project"]["weapons"]) != 1:
                raise RuntimeError("Packaged clone undo did not restore its donor selection")
            for package in (weapon_source, Path(clone_undo["source"])):
                restored_files = {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
                if restored_files != weapon_originals:
                    raise RuntimeError("Packaged clone/undo changed original bytes or left added files")
            shop_edit = weapon_action("shop", {"action": "edit_shop", "workspace": weapon_copy["workspace"],
                "expected_revision": 8, "weapon": "WEAPON_SMOKE", "metadata_source": "weapon_shop.meta",
                "updates": {"shop.cost": "950", "shop.textLabel": "WT_SHOP_SMOKE"}})
            if shop_edit["revision"] != 9 or shop_edit["editor_kind"] != "shop" or shop_edit["shop_values"]["values"]["shop.cost"] != "950":
                raise RuntimeError("Packaged shop authoring did not save the reviewed record")
            shop_undo = weapon_action("shop-undo", {"action": "undo", "workspace": weapon_copy["workspace"], "expected_revision": 9})
            if shop_undo["revision"] != 10 or shop_undo["shop_values"]["source"] != "weapon_shop.meta":
                raise RuntimeError("Packaged shop undo lost its source selection")
            if (Path(shop_undo["source"]) / "weapon_shop.meta").read_bytes() != weapon_originals[Path("weapon_shop.meta")]:
                raise RuntimeError("Packaged shop undo did not restore exact bytes")
            animation_source = root / "animation-source"
            animation_source.mkdir()
            for relative, content in weapon_originals.items():
                member = animation_source / relative
                member.parent.mkdir(parents=True, exist_ok=True)
                member.write_bytes(content.replace(b'key="WEAPON_SMOKE"', b'key="WEAPON_TEMPLATE"')
                                   if relative.name == "weaponanimations.meta" else content)
            animation_originals = {path.relative_to(animation_source): path.read_bytes() for path in animation_source.rglob("*") if path.is_file()}
            animation_copy = weapon_action("animation-copy", {"action": "create", "source": str(animation_source), "parent": str(root), "name": "animation-copy"})
            animation_edit = weapon_action("animation", {"action": "clone_animation", "workspace": animation_copy["workspace"],
                "expected_revision": 0, "weapon": "WEAPON_SMOKE", "template_weapon": "WEAPON_TEMPLATE", "metadata_source": "weaponanimations.meta"})
            if animation_edit["revision"] != 1 or animation_edit["editor_kind"] != "animation" or animation_edit["selected_weapon"] != "WEAPON_SMOKE":
                raise RuntimeError("Packaged animation authoring lost its target selection")
            animation_undo = weapon_action("animation-undo", {"action": "undo", "workspace": animation_copy["workspace"], "expected_revision": 1})
            for package in (animation_source, Path(animation_undo["source"])):
                if {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()} != animation_originals:
                    raise RuntimeError("Packaged animation clone/undo changed original bytes")
            game = root / "Grand Theft Auto V Enhanced"
            managed_file = game / "scripts" / "PackagedSmoke.asi"
            receipt_root = game / "scripts" / ".allin1" / "mods"
            receipt_root.mkdir(parents=True)
            (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
            managed_file.write_bytes(b"packaged receipt payload")
            (receipt_root / "allin1.packaged-smoke.json").write_text(json.dumps({
                "id": "allin1.packaged-smoke",
                "name": "Packaged Receipt Smoke",
                "version": "1.0.0",
                "type": "asi",
                "enabled": True,
                "files": [{
                    "destination": "scripts/PackagedSmoke.asi",
                    "sha256": hashlib.sha256(managed_file.read_bytes()).hexdigest(),
                    "backup": None,
                }],
                "rpf_entries": [],
            }), encoding="utf-8")
            receipt_preview = exchange(process, request(
                "smoke-package-receipt", "inspect_package_receipts", {
                    "gta_path": str(game),
                    "selected_id": "allin1.packaged-smoke",
                },
            ))
            receipt_result = receipt_preview["payload"].get("result", {})
            verification = receipt_result.get("verification") or {}
            if (
                receipt_preview["risk"] != "read_only"
                or receipt_result.get("kind") != "package_receipt_inventory"
                or receipt_result.get("package_count") != 1
                or verification.get("ownership_verified") is not True
                or receipt_result.get("game_write_performed") is not False
            ):
                raise RuntimeError(
                    f"packaged receipt inspection failed: {receipt_preview}"
                )
            disable_preview = exchange(process, request(
                "smoke-lifecycle-disable-review", "review_package_lifecycle", {
                    "action": "disable",
                    "gta_path": str(game),
                    "mod_id": "allin1.packaged-smoke",
                },
            ))
            disable_review = disable_preview["payload"].get("result", {})
            disable_operations = disable_review.get("operations", [])
            if (
                disable_preview["risk"] != "read_only"
                or disable_review.get("action") != "disable"
                or disable_review.get("ready") is not True
                or disable_review.get("current_enabled") is not True
                or disable_review.get("target_enabled") is not False
                or not disable_operations
                or disable_operations[0].get("disposition") != "disable_file"
                or disable_review.get("game_write_performed") is not False
            ):
                raise RuntimeError(
                    f"packaged disable review failed: {disable_preview}"
                )
            review_package = root / "review-package"
            review_package.mkdir()
            (review_package / "Review.dll").write_bytes(b"review-only payload")
            review_manifest = review_package / "mod.toml"
            review_manifest.write_text("\n".join((
                "schema_version = 1",
                'id = "packaged-review"',
                'name = "Packaged Review"',
                'version = "1.0.0"',
                'type = "script"',
                'editions = ["enhanced"]',
                '[[files]]',
                'source = "Review.dll"',
                'destination = "scripts/Review.dll"',
            )), encoding="utf-8")
            lifecycle_preview = exchange(process, request(
                "smoke-lifecycle-review", "review_package_lifecycle", {
                    "action": "install",
                    "gta_path": str(game),
                    "source": str(review_manifest),
                },
            ))
            lifecycle_result = lifecycle_preview["payload"].get("result", {})
            if (
                lifecycle_preview["risk"] != "read_only"
                or lifecycle_result.get("kind") != "package_lifecycle_review"
                or lifecycle_result.get("ready") is not True
                or lifecycle_result.get("game_write_performed") is not False
                or (game / "scripts" / "Review.dll").exists()
                or len(str(lifecycle_result.get("review_sha256", ""))) != 64
            ):
                raise RuntimeError(
                    f"packaged lifecycle review failed: {lifecycle_preview}"
                )
            lifecycle_apply = exchange(process, request(
                "smoke-lifecycle-apply", "apply_package_lifecycle", {
                    "action": "install",
                    "gta_path": str(game),
                    "source": str(review_manifest),
                    "review_sha256": lifecycle_result["review_sha256"],
                    "confirmation_id": "packaged-review",
                    "game_write_confirmed": True,
                    "replace_confirmed": False,
                },
            ))
            applied = lifecycle_apply["payload"].get("result", {})
            installed_payload = game / "scripts" / "Review.dll"
            if lifecycle_apply["operation"] == "error" and "Close GTA V" in str(
                lifecycle_apply["payload"].get("message", "")
            ):
                if lifecycle_apply["risk"] != "game_write" or installed_payload.exists():
                    raise RuntimeError(
                        "packaged lifecycle process gate did not fail closed"
                    )
            else:
                if (
                    lifecycle_apply["risk"] != "game_write"
                    or applied.get("kind") != "package_lifecycle_execution"
                    or applied.get("status") != "installed"
                    or applied.get("game_write_performed") is not True
                    or applied.get("process_check", {}).get("gta_closed") is not True
                    or not installed_payload.is_file()
                    or applied.get("rollback", {}).get("receipt_written") is not True
                ):
                    raise RuntimeError(
                        f"packaged lifecycle execution failed: {lifecycle_apply}"
                    )
                uninstall_preview = exchange(process, request(
                    "smoke-lifecycle-uninstall-review", "review_package_lifecycle", {
                        "action": "uninstall",
                        "gta_path": str(game),
                        "mod_id": "packaged-review",
                    },
                ))
                uninstall_review = uninstall_preview["payload"].get("result", {})
                lifecycle_uninstall = exchange(process, request(
                    "smoke-lifecycle-uninstall", "apply_package_lifecycle", {
                        "action": "uninstall",
                        "gta_path": str(game),
                        "mod_id": "packaged-review",
                        "review_sha256": uninstall_review.get("review_sha256"),
                        "confirmation_id": "packaged-review",
                        "game_write_confirmed": True,
                    },
                ))
                removed = lifecycle_uninstall["payload"].get("result", {})
                if (
                    removed.get("status") != "uninstalled"
                    or installed_payload.exists()
                    or removed.get("rollback", {}).get("receipt_removed") is not True
                ):
                    raise RuntimeError(
                        f"packaged lifecycle uninstall failed: {lifecycle_uninstall}"
                    )
            package = root / "package"
            package.mkdir()
            (package / "README.txt").write_text(
                "Packaged Asset Viewer preview\n", encoding="utf-8",
            )
            image = package / "preview.png"
            Image.new("RGB", (16, 10), (30, 120, 76)).save(image)
            text_preview = exchange(process, request(
                "smoke-text-preview", "preview_asset", {
                    "source": str(package), "entry": "README.txt",
                },
            ))
            text_result = text_preview["payload"].get("result", {})
            if (
                text_preview["risk"] != "read_only"
                or text_result.get("display_kind") != "text"
                or "Packaged Asset Viewer" not in text_result.get("text", "")
            ):
                raise RuntimeError(f"packaged text preview failed: {text_preview}")
            image_preview = exchange(process, request(
                "smoke-image-preview", "preview_asset", {
                    "source": str(package), "entry": "preview.png",
                },
            ))
            image_result = image_preview["payload"].get("result", {})
            artifact = image_result.get("artifact") or {}
            artifact_path = Path(artifact.get("path", ""))
            if (
                image_result.get("display_kind") != "image"
                or artifact_path.parent != preview_cache
                or not artifact_path.is_file()
                or hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                != artifact.get("sha256")
            ):
                raise RuntimeError(f"packaged image preview failed: {image_preview}")
            recipe = root / "recipe"
            recipe_content = recipe / "content"
            recipe_content.mkdir(parents=True)
            (recipe / "assembly.xml").write_text("""<package version="2.2">
<metadata><name>Packaged Recipe Smoke</name><version><major>1</major><minor>0</minor></version>
<author><displayName>ALLIN1</displayName></author><gameversion>enhanced</gameversion></metadata>
<content><add source="readme.txt">scripts/PackagedRecipe/readme.txt</add></content>
</package>""", encoding="utf-8")
            (recipe_content / "readme.txt").write_text(
                "Packaged recipe payload", encoding="utf-8",
            )
            recipe_preview = exchange(process, request(
                "smoke-recipe-preview", "inspect_recipe", {
                    "source": str(recipe),
                },
            ))
            recipe_result = recipe_preview["payload"].get("result", {})
            if (
                recipe_preview["risk"] != "read_only"
                or recipe_result.get("kind") != "recipe_plan"
                or recipe_result.get("name") != "Packaged Recipe Smoke"
                or recipe_result.get("operation_count") != 1
            ):
                raise RuntimeError(
                    f"packaged recipe inspection failed: {recipe_preview}"
                )
            recipe_report = root / "recipe-plan.md"
            exported_recipe = exchange(process, request(
                "smoke-recipe-report", "execute", {
                    "command": "oiv-plan",
                    "args": [
                        str(recipe), "--output", str(recipe_report),
                    ],
                    "authoring_confirmed": True,
                },
            ))
            if (
                exported_recipe["operation"] != "result"
                or exported_recipe["risk"] != "authoring_write"
                or not recipe_report.is_file()
                or not recipe_report.with_suffix(".json").is_file()
            ):
                raise RuntimeError(
                    f"packaged recipe report failed: {exported_recipe}"
                )

            # Exercise every shared offline authoring module through the same
            # protocol used by React. These checks operate only in this disposable
            # directory and prove that PyInstaller included the newer adapters.
            recipe_state = inspect_workspace(
                "recipe", {"module": "recipe", "source": str(recipe)},
            )
            recipe_output = root / "converted recipe package"
            recipe_conversion = apply_workspace("recipe-managed", {
                "module": "recipe", "source": str(recipe), "action": "managed",
                "destination": str(recipe_output),
                "expected_state_sha256": recipe_state["state_sha256"],
            })
            if (
                recipe_conversion.get("file_count") != 2
                or recipe_conversion.get("archive_write_performed") is not False
                or not (recipe_output / "mod.toml").is_file()
            ):
                raise RuntimeError("Packaged recipe conversion did not produce a managed package")

            # Real frozen parser checks: no installed Python, no Lua runtime,
            # malformed input rejected and reviewed copies verified byte-for-byte.
            canary = root / "code execution canary"
            canary.write_bytes(b"preserve")
            for language, valid_text, invalid_text in (
                ("xml", "<root><value>2</value></root>\n", "<root>"),
                ("lua", f"os.remove([[{canary}]])\nreturn {{ value = 2 }}\n", "local value ="),
            ):
                context = {"module": "code", "document": {"language": language}}
                code_state = inspect_workspace(f"{language}-new", context)
                invalid = inspect_workspace(f"{language}-invalid", {
                    **context, "document": {"language": language, "chunks": [invalid_text]},
                })
                if invalid["validation"]["valid"] or not invalid["validation"]["diagnostics"]:
                    raise RuntimeError(f"Frozen {language} parser accepted malformed syntax")
                output = root / f"code with spaces.{language}"
                code_result = apply_workspace(f"{language}-save", {
                    **context, "document": {"language": language, "chunks": [valid_text]},
                    "action": "save_copy", "destination": str(output),
                    "expected_state_sha256": code_state["state_sha256"],
                })
                if (output.read_bytes() != valid_text.encode("utf-8")
                        or code_result["output_sha256"] != sha256(output)
                        or canary.read_bytes() != b"preserve"):
                    raise RuntimeError(f"Frozen {language} editor violated its copy/no-execution contract")

            binary_source = root / "binary source.bin"
            binary_original = bytes(range(64))
            binary_source.write_bytes(binary_original)
            binary_state = inspect_workspace(
                "binary-source", {"module": "binary", "source": str(binary_source)},
            )
            binary_copy = apply_workspace("binary-create", {
                "module": "binary", "source": str(binary_source), "action": "create",
                "destination": str(root / "binary workspace"),
                "expected_state_sha256": binary_state["state_sha256"],
            })["session"]
            binary_context = {"module": "binary", "workspace": binary_copy["workspace"]}
            binary_patched = apply_workspace("binary-patch", {
                **binary_context, "action": "patch", "offset": 2,
                "expected_hex": "02 03", "replacement_hex": "aa bb",
                "expected_state_sha256": binary_copy["state_sha256"],
            })["session"]
            binary_output = root / "verified binary.bin"
            binary_built = apply_workspace("binary-build", {
                **binary_context, "action": "build", "destination": str(binary_output),
                "expected_state_sha256": binary_patched["state_sha256"],
            })
            if (
                binary_output.read_bytes()[2:4] != b"\xaa\xbb"
                or binary_source.read_bytes() != binary_original
                or hashlib.sha256(binary_output.read_bytes()).hexdigest()
                    != binary_built.get("output_sha256")
            ):
                raise RuntimeError("Packaged binary workspace failed verified copy build")

            map_document = {
                "schema_version": 1, "id": "packaged.map", "package_id": "packaged.map.package",
                "name": "Packaged map smoke", "version": "1.0.0",
                "editions": ["legacy", "enhanced"],
                "streaming": {"mode": "none", "pack_name": "packagedmap", "content_group": None,
                              "ipls": [], "activation_radius": 250, "release_radius": 450,
                              "keep_resident": False},
                "levels": [{"id": "garage", "name": "Garage",
                            "center": {"x": 0, "y": 0, "z": -50, "heading": 0}, "ipls": []}],
                "portals": [{"id": "door", "name": "Door", "mode": "both",
                             "from": {"level": "world", "position": {"x": 1, "y": 2, "z": 3, "heading": 0}},
                             "to": {"level": "garage", "position": {"x": 0, "y": 0, "z": -50, "heading": 180}},
                             "radius": 4, "one_way": False}],
                "garages": [{"id": "storage", "name": "Storage", "level_id": "garage",
                             "entrance_portal_id": "door", "capacity": 1, "vehicle_types": ["land"],
                             "slots": [{"id": "slot-1", "position": {"x": 0, "y": 2, "z": -50, "heading": 180}}],
                             "rules": {"allow_store": True, "allow_retrieve": True,
                                       "save_policy": "story_save_only"}}],
            }
            map_path = root / "map project.json"
            map_created = apply_workspace("map-create", {
                "module": "maps", "action": "create", "destination": str(map_path),
                "document": map_document,
            })["session"]
            map_document = map_created["document"]
            map_document["name"] = "Packaged map edited"
            map_saved = apply_workspace("map-save", {
                "module": "maps", "descriptor": str(map_path), "action": "save",
                "document": map_document,
                "expected_state_sha256": map_created["state_sha256"],
            })["session"]
            if map_saved["document"]["name"] != "Packaged map edited":
                raise RuntimeError("Packaged map create/save did not round-trip")

            vehicle_source = root / "vehicle source"
            vehicle_source.mkdir()
            vehicle_files = {
                "vehicles.meta": """<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>authorcar</modelName><txdName>authorcar</txdName><handlingId>AUTHORHAND</handlingId><gameName>AUTHORCAR</gameName><vehicleMakeName>AUTHOR</vehicleMakeName><audioNameHash>TAILGATER</audioNameHash><layout>LAYOUT_STANDARD</layout><type>VEHICLE_TYPE_CAR</type><vehicleClass>VC_SPORT</vehicleClass></Item></InitDatas></CVehicleModelInfo__InitDataList>""",
                "handling.meta": """<CHandlingDataMgr><HandlingData><Item><handlingName>AUTHORHAND</handlingName><fMass value="1500.0"/><fDriveBiasFront value="0.0"/><strHandlingFlags>440010</strHandlingFlags><nInitialDriveGears value="6"/><fInitialDriveForce value="0.30"/><fInitialDriveMaxFlatVel value="160.0"/><fBrakeForce value="0.8"/><fSteeringLock value="40.0"/></Item></HandlingData></CHandlingDataMgr>""",
                "carvariations.meta": """<CVehicleModelInfoVariation><variationData><Item><modelName>authorcar</modelName><colors/><kits><Item>123_authorkit</Item></kits><lightSettings value="1"/><sirenSettings value="0"/></Item></variationData></CVehicleModelInfoVariation>""",
                "carcols.meta": """<CVehicleModelInfoVarGlobal><Kits><Item><kitName>123_authorkit</kitName><id value="123"/><kitType>MKT_STANDARD</kitType><visibleMods/><linkMods/><statMods/><slotNames/><liveryNames/></Item></Kits></CVehicleModelInfoVarGlobal>""",
                "content.xml": """<CDataFileMgr__ContentsOfDataFileXml><dataFiles><Item><filename>dlc_authorcar:/common/data/vehicles.meta</filename></Item></dataFiles></CDataFileMgr__ContentsOfDataFileXml>""",
            }
            for name, content in vehicle_files.items():
                (vehicle_source / name).write_text(content, encoding="utf-8")
            vehicle_stream = vehicle_source / "stream"
            vehicle_stream.mkdir()
            (vehicle_stream / "authorcar.yft").write_bytes(b"fragment")
            (vehicle_stream / "authorcar.ytd").write_bytes(b"textures")
            vehicle_original = {
                item.relative_to(vehicle_source): item.read_bytes()
                for item in vehicle_source.rglob("*") if item.is_file()
            }
            vehicle_copy_request = {
                "source": str(vehicle_source), "parent": str(root),
                "name": "vehicle-authoring-copy", "model": "authorcar",
            }
            vehicle_copy_reviewed = exchange(process, request(
                "smoke-vehicle-copy-review", "review_vehicle_authoring_workspace",
                vehicle_copy_request,
            ))
            vehicle_copy_review = vehicle_copy_reviewed["payload"].get("result", {})
            vehicle_copied = exchange(process, request(
                "smoke-vehicle-copy-apply", "create_vehicle_authoring_workspace", {
                    **vehicle_copy_request,
                    "review_sha256": vehicle_copy_review.get("review_sha256"),
                    "authoring_confirmed": True,
                },
            ))
            vehicle_session = vehicle_copied["payload"].get("result", {})
            if (
                vehicle_copy_reviewed["operation"] != "result"
                or vehicle_copied["operation"] != "result"
                or vehicle_session.get("selected_model") != "authorcar"
            ):
                raise RuntimeError("Packaged vehicle workspace creation failed")
            identity_context = {
                "module": "vehicle_identity", "workspace": vehicle_session["workspace"],
                "model": "authorcar",
            }
            identity_state = inspect_workspace("vehicle-identity", identity_context)
            migrated = apply_workspace("vehicle-identity-migrate", {
                **identity_context, "action": "migrate", "new_model": "reactcar",
                "new_handling": "REACTHAND", "expected_revision": identity_state["revision"],
                "expected_state_sha256": identity_state["state_sha256"],
            })
            copied_source = Path(migrated["vehicle_session"]["source"])
            if (
                migrated["vehicle_session"].get("selected_model") != "reactcar"
                or not (copied_source / "stream/reactcar.yft").is_file()
                or (copied_source / "stream/authorcar.yft").exists()
                or vehicle_original != {
                    item.relative_to(vehicle_source): item.read_bytes()
                    for item in vehicle_source.rglob("*") if item.is_file()
                }
            ):
                raise RuntimeError("Packaged vehicle identity migration lost exact source ownership")

            graph_root = root / "package graph project"
            imported_graph = apply_workspace("graph-package-import", {
                "module": "graph", "action": "import_package",
                "source": str(vehicle_source), "destination": str(graph_root),
            })["session"]
            graph_context = {"module": "graph", "workspace": imported_graph["workspace"]}
            analyzed_graph = apply_workspace("graph-analyze", {
                **graph_context, "action": "analyze",
                "expected_state_sha256": imported_graph["state_sha256"],
            })["session"]
            semantic = analyzed_graph["document"].get("semantic", {})
            if (
                semantic.get("summary", {}).get("entities") != 1
                or not semantic.get("relations")
            ):
                raise RuntimeError("Packaged graph relationship analysis did not retain its vehicle")

            program_template = inspect_workspace("program-template", {
                "module": "program", "graph": analyzed_graph["workspace"],
                "template": "loose-export",
            })
            program_document = program_template["document"]
            materialized = root / "program materialized output"
            next(item for item in program_document["nodes"] if item["id"] == "materialize")["config"] = {
                "output": str(materialized),
            }
            program_path = root / "package program.json"
            program_created = apply_workspace("program-create", {
                "module": "program", "action": "create", "destination": str(program_path),
                "document": program_document,
            })["session"]
            program_receipt = root / "package program execution.json"
            program_run = apply_workspace("program-run", {
                "module": "program", "workspace": program_created["workspace"],
                "action": "run", "destination": str(program_receipt),
                "expected_state_sha256": program_created["state_sha256"],
            })
            if (
                program_run.get("execution", {}).get("status") != "verified"
                or not materialized.is_dir()
                or not program_receipt.is_file()
            ):
                raise RuntimeError("Packaged program execution did not verify its offline output")

            runtime_status = "NOT TESTED"
            if os.environ.get("ALLIN1_NATIVE_RUNTIME_TEST") == "1":
                runtime_state = inspect_workspace("runtime", {"module": "runtime"})
                if runtime_state.get("toolchain", {}).get("ready") is not True:
                    raise RuntimeError(
                        "Packaged Story Runtime toolchain is not ready: "
                        + "; ".join(runtime_state.get("toolchain", {}).get("problems", []))
                    )
                runtime_result = apply_workspace("runtime-build", {
                    "module": "runtime", "action": "build",
                    "targets": ["story-legacy", "story-enhanced"],
                    "destination": str(root / "packaged runtime candidate"),
                    "build_id": "sdk-0.6.4-packaged-sidecar-smoke",
                    "expected_state_sha256": runtime_state["state_sha256"],
                })
                runtime_build = runtime_result.get("runtime_build", {})
                if (
                    runtime_build.get("built_targets") != ["story-legacy", "story-enhanced"]
                    or runtime_build.get("candidate_status")
                        != {"supported": False, "game_acceptance": "not-tested"}
                    or not Path(runtime_result.get("output", "")).is_dir()
                    or not any(
                        item.get("name") == "Native CTest" and item.get("returncode") == 0
                        for item in runtime_build.get("commands", [])
                    )
                ):
                    raise RuntimeError("Packaged Story Runtime did not produce verified candidate-only outputs")
                runtime_status = "PASS"

            render_status = "NOT TESTED"
            if options.blender_executable is not None:
                blender = options.blender_executable.resolve(strict=True)
                render_source = root / "render source"
                render_source.mkdir()
                render_xml = render_source / "owned-tetrahedron.ydr.xml"
                render_xml.write_text("""<?xml version="1.0" encoding="utf-8"?>
<Drawable><Name>allin1_packaged_render_fixture</Name>
<BoundingSphereCenter x="0" y="0" z="0.5"/><BoundingSphereRadius value="2"/>
<BoundingBoxMin x="-1" y="-1" z="0"/><BoundingBoxMax x="1" y="1" z="2"/>
<LodDistHigh value="100"/><FlagsHigh value="1"/>
<ShaderGroup><Shaders><Item><Name>default</Name><FileName>default.sps</FileName><RenderBucket value="0"/>
<Parameters><Item name="DiffuseSampler" type="Texture"><Name>fixture_diffuse</Name></Item></Parameters>
</Item></Shaders></ShaderGroup><DrawableModelsHigh><Item><RenderMask value="255"/><Flags value="0"/>
<HasSkin value="0"/><BoneIndex value="0"/><Geometries><Item><ShaderIndex value="0"/>
<BoundingBoxMin x="-1" y="-1" z="0" w="0"/><BoundingBoxMax x="1" y="1" z="2" w="0"/>
<VertexBuffer><Flags value="0"/><Layout type="GTAV1"><Position/><Normal/><TexCoord0/></Layout><Data>
-1 -1 0 -0.577 -0.577 -0.577 0 0
1 -1 0 0.577 -0.577 -0.577 1 0
0 1 0 0 0.707 -0.707 0.5 1
0 0 2 0 0 1 0.5 0.5
</Data></VertexBuffer><IndexBuffer><Data>0 2 1 0 1 3 1 2 3 2 0 3</Data></IndexBuffer>
</Item></Geometries></Item></DrawableModelsHigh></Drawable>""", encoding="utf-8")
                render_model = render_source / "owned-tetrahedron.ydr"
                generated = subprocess.run([
                    str(resource_home / "tools/RpfPatcher/RpfPatcher.exe"),
                    "asset-from-xml", str(render_xml), str(render_model),
                    str(render_source), "legacy",
                ], cwd=root, env=environment, capture_output=True, text=True,
                    timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if generated.returncode:
                    raise RuntimeError(
                        "Could not create owned packaged render fixture: "
                        + (generated.stderr or generated.stdout)[-2000:]
                    )
                render_source_sha = hashlib.sha256(render_model.read_bytes()).hexdigest()
                rendered = inspect_workspace("render", {
                    "module": "render", "source": str(render_model),
                    "edition": "Legacy", "render": True,
                    "blender_executable": str(blender),
                    "settings": {"width": 384, "height": 384, "samples": 4,
                                 "quality": "preview", "engine": "cycles", "device": "cpu"},
                })
                render_record = rendered.get("render_record", {})
                render_identity = render_record.get("identity", {})
                render_output = root / "packaged-render.png"
                exported_render = apply_workspace("render-export", {
                    "module": "render", "action": "export",
                    "render_id": rendered["render_id"],
                    "expected_state_sha256": rendered["state_sha256"],
                    "destination": str(render_output),
                })
                with Image.open(render_output) as rendered_image:
                    rendered_size = rendered_image.size
                    color_count = rendered_image.convert("RGB").getcolors(maxcolors=100)
                if (
                    rendered_size != (384, 384)
                    or color_count is not None
                    or render_record.get("metadata", {}).get("triangle_count") != 4
                    or render_identity.get("renderer_identity_kind") != "frozen-sidecar"
                    or render_identity.get("renderer_sha256") != tested_binary
                    or render_identity.get("source_sha256") != render_source_sha
                    or exported_render.get("output_sha256")
                        != hashlib.sha256(render_output.read_bytes()).hexdigest()
                    or not Path(exported_render.get("receipt", "")).is_file()
                ):
                    raise RuntimeError("Packaged Render Studio lost geometry, pixels, or frozen identity")
                render_status = "PASS"
            print(
                "Packaged shared workspaces passed: XML, Lua, recipe, binary, maps, vehicle identity, graph, program, "
                f"Story Runtime ({runtime_status}), and render ({render_status}).",
                flush=True,
            )
            manifest = root / "addon.json"
            report = root / "linked-report.md"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "id": "smoke.desktop-report",
                "name": "Packaged Sidecar Smoke",
                "version": "1.0.0",
                "summary": "Failing review report smoke fixture",
                "editions": ["enhanced"],
                "nodes": [{
                    "id": "package.main",
                    "kind": "package",
                    "label": "Package",
                    "description": "",
                    "source": None,
                    "fields": {
                        "Registration": "dlclist.xml",
                        "Edition": "enhanced",
                    },
                }],
                "references": [],
                "install_steps": [],
            }), encoding="utf-8")
            exported = exchange(process, request("smoke-report", "execute", {
                "command": "link",
                "args": [
                    str(manifest), "--output", str(report),
                    "--allow-failing-report",
                ],
                "authoring_confirmed": True,
            }))
            if (
                exported["operation"] != "result"
                or exported["risk"] != "authoring_write"
            ):
                raise RuntimeError(f"packaged report export failed: {exported}")
            markdown = report.read_text(encoding="utf-8")
            if "Result: **FAIL**" not in markdown:
                raise RuntimeError("packaged report export did not preserve failing evidence")
        stopped = exchange(process, request("smoke-shutdown", "shutdown", {}))
        if stopped["payload"].get("state") != "stopped":
            raise RuntimeError(f"sidecar did not acknowledge shutdown: {stopped}")
        process.wait(timeout=10)
        if process.returncode != 0:
            raise RuntimeError(f"sidecar exited with {process.returncode}")
    finally:
        if process.poll() is None:
            # Let the Python child leave its working directory before the
            # one-file bootloader exits, including after failed assertions.
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:
                    process.kill()
                process.wait(timeout=5)
        preview_cache_context.cleanup()
    verify_inventory(resource_home)
    if tested_binary != sha256(executable) or tested_resources != sha256(resource_home / "resource-checksums.json"):
        raise RuntimeError("Tested artifact/resource identity changed during smoke execution")
    print(f"packaged desktop sidecar smoke passed: {executable}")
    # Automation evidence is deliberately not the live acceptance schema. No
    # consumer should infer success by scraping the human-readable line above.
    print(json.dumps({"schema_version": 1, "kind": "automated_test_result", "suite": "packaged-sidecar",
        "status": "PASS", "sdk_version": handshake["payload"].get("sdk_version"),
        "artifact_sha256": tested_binary, "resource_manifest_sha256": tested_resources,
        "build_identity": handshake["payload"].get("build_identity"),
        "live_acceptance": "NOT TESTED"}, sort_keys=True))


if __name__ == "__main__":
    main()
