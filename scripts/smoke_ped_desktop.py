"""Exercise real JSONL ped jobs and mutations in an owned synthetic temp tree.

Optionally pass a frozen sidecar and resource home. No GTA or Launcher writes.
The fake drawable bytes deliberately test honest unavailable-preview handling,
not native model fidelity or runtime compatibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", nargs="?", type=Path)
    parser.add_argument("--resource-home", type=Path)
    parser.add_argument("--native-game", type=Path, help="Optional read-only decoder context")
    parser.add_argument("--native-archive", type=Path)
    parser.add_argument("--native-model", help="Exact indexed YDD/YDR member ID")
    parser.add_argument("--native-textures", help="Exact indexed YTD member ID")
    parser.add_argument("--edition", choices=("Legacy", "Enhanced"), default="Enhanced")
    args = parser.parse_args()
    native_args = (args.native_game, args.native_archive, args.native_model, args.native_textures)
    if any(native_args) and not all(native_args):
        parser.error("Real native preview requires game, archive, model and texture identities together")
    command = [str(args.sidecar.resolve(strict=True))] if args.sidecar else [sys.executable, "-m", "allin1_sdk.desktop_sidecar_host"]
    with tempfile.TemporaryDirectory(prefix="allin1-ped-smoke-") as temporary:
        root = Path(temporary)
        source = root / "synthetic-peds"
        source.mkdir()
        xml = """<CPedModelInfo__InitDataList><InitDatas><Item>
<Name>ig_demo</Name><Pedtype>PERSON</Pedtype><ModelType value="human"/>
<PropsName>ig_demo_p</PropsName><ClipDictionaryName ref="move_m@generic"/>
<ExpressionSetName>expr_set_ambient_male</ExpressionSetName><MovementClipSet>move_m@casual@d</MovementClipSet>
<CreatureMetadataName>METADATA_HUMAN_MALE</CreatureMetadataName><Unknown keep="yes"/>
</Item></InitDatas></CPedModelInfo__InitDataList>"""
        (source / "peds.meta").write_text(xml, encoding="utf-8")
        for name in ("ig_demo", "ig_demo_p", "ig_clone", "ig_clone_p"):
            for suffix in (".ydd", ".ytd"):
                (source / f"{name}{suffix}").write_bytes(b"synthetic-invalid-native-fixture")
        def hashes(directory):
            return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in directory.rglob("*") if p.is_file()}
        original = hashes(source)
        env = os.environ.copy()
        for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
            env[key] = str(root / "user")
        env["ALLIN1_PREVIEW_DIR"] = str(root / "preview")
        env.pop("ALLIN1_GTA_PATH", None)
        if args.resource_home:
            env["ALLIN1_SDK_HOME"] = str(args.resource_home.resolve(strict=True))
        if args.sidecar:
            for key in ("PYTHONPATH", "PYTHONHOME", "ALLIN1_DESKTOP_PYTHON", "ALLIN1_DESKTOP_SIDECAR"):
                env.pop(key, None)
            env["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
            env["DOTNET_ROOT"] = str(root / "no-dotnet")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        messages = queue.Queue()
        diagnostics = []
        def read_stdout():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    messages.put({"operation": "error", "payload": {"message": line}})
        def read_stderr():
            for line in process.stderr:
                diagnostics.append(line[-2000:])
                del diagnostics[:-20]
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        serial = 0
        def call(operation, payload, *, job=False, error=False):
            nonlocal serial
            serial += 1
            request_id = f"ped-smoke-{serial}"
            request = {"protocol_version": "1.0.0", "request_id": request_id, "job_id": None,
                       "operation": "start_job" if job else operation, "sequence": 0, "risk": "none", "terminal": False,
                       "payload": {"operation": operation, "payload": payload, "revision": request_id} if job else payload}
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while True:
                try:
                    message = messages.get(timeout=60)
                except queue.Empty:
                    raise RuntimeError("Ped sidecar timeout: " + "".join(diagnostics))
                if message.get("operation") == "error":
                    if error:
                        return message
                    raise RuntimeError(str(message))
                if job:
                    if message.get("job_id") and message.get("terminal"):
                        return message["payload"].get("result", message["payload"])
                elif message.get("request_id") == request_id and message.get("terminal"):
                    if error:
                        raise AssertionError("Expected refusal")
                    return message["payload"].get("result", message["payload"])
        try:
            call("handshake", {"client": {"name": "ped-smoke", "version": "1"}, "supported_versions": ["1.0.0"]})
            catalog = call("catalog", {})
            assert "inspect_ped_workbench" in catalog["job_operations"]
            snapshot = call("inspect_ped_workbench", {"source": str(source)}, job=True)
            assert snapshot["selected_ped"]["name"] == "ig_demo" and not snapshot["editable_fields"]
            call("apply_ped_authoring", {"action": "create", "source": str(source)}, error=True)
            payload = {"action": "create", "source": str(source), "parent": str(root), "name": "copy"}
            review = call("review_ped_authoring", payload, job=True)
            assert not (root / "copy").exists()
            snapshot = call("apply_ped_authoring", {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
            def act(action, **extra):
                nonlocal snapshot
                payload = {"action": action, "workspace": snapshot["workspace"], "ped": snapshot["selected_ped"]["name"],
                           "expected_revision": snapshot["revision"], "expected_state_sha256": snapshot["state_sha256"], **extra}
                reviewed = call("review_ped_authoring", payload, job=True)
                snapshot = call("apply_ped_authoring", {**payload, "review_sha256": reviewed["review_sha256"], "authoring_confirmed": True})
                assert snapshot["workspace_write_performed"] and snapshot["game_write_performed"] is False
            act("edit", updates={"ped.modelType": "animal"})
            assert snapshot["values"]["ped.modelType"] == "animal"
            act("undo")
            act("migrate", new_name="ig_renamed")
            assert snapshot["selected_ped"]["name"] == "ig_renamed"
            act("undo")
            assert snapshot["selected_ped"]["name"] == "ig_demo"
            act("clone", new_name="ig_clone")
            assert snapshot["selected_ped"]["name"] == "ig_clone"
            act("undo")
            assert snapshot["selected_ped"]["name"] == "ig_demo"
            assert hashes(Path(snapshot["source"])) == original and hashes(source) == original
            result = call("preview_asset", {"source": snapshot["source"], "entry": "peds.meta", "edition": "Enhanced"}, job=True)
            assert result["display_kind"] == "text" and "Unknown" in result["text"]
            result = call("preview_asset", {"source": snapshot["source"], "entry": "ig_demo.ydd", "edition": "Enhanced"}, job=True)
            assert result["artifact"] is None and result["warnings"]
            if all(native_args):
                for entry in (args.native_model, args.native_textures):
                    result = call("preview_asset", {"source": str(args.native_archive.resolve(strict=True)),
                                  "gta_path": str(args.native_game.resolve(strict=True)), "entry": entry,
                                  "edition": args.edition}, job=True)
                    assert result["path"] == entry and result["display_kind"] == "image" and result["artifact"], result
                    assert result["sha256"] and not result["truncated"] and not result["warnings"], result
                    print(f"PASS: exact native preview {entry}; source SHA-256 {result['sha256']}")
            call("shutdown", {})
            process.wait(timeout=15)
            assert process.returncode == 0
            print("PASS: real ped JSONL jobs, copy, confirmed edit/migration/clone/undo, source preservation and unavailable native preview; no GTA writes")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
