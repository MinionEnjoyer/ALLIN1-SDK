"""Isolated Tauri portable deployment rehearsal, not NSIS/update qualification.

Only creates a new evidence directory. Never discovers or replaces an installed
SDK, changes registration, launches a WebView/game, or enables unsigned updates.
Executable probes require an explicit flag and an independently supplied ZIP hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import zipfile

from allin1_sdk.paths import gta_root_containing
from allin1_sdk.release_identity import sha256, verify_inventory
from allin1_sdk.release_paths import filesystem_path, no_links, strict_json, unique_paths
from allin1_sdk.self_update import MAX_ARCHIVE_BYTES, _archive_inventory, _extract_archive

SHELL = "allin1-sdk-desktop.exe"
SIDECAR = "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"
REQUIRED = {SHELL, SIDECAR, "release.json", "checksums.json", "build-identity.json", "resource-checksums.json",
            "tools/RpfPatcher/RpfPatcher.exe", "tools/RpfPatcher/RpfPatcher.dll"}
SHA = re.compile(r"[a-f0-9]{64}")
MAX_JSON = 2 * 1024**2


def inspect_archive(archive_path: Path, expected_sha256: str) -> dict:
    """Validate the entire selected archive before creating any output."""
    archive_path = no_links(archive_path)
    if not isinstance(expected_sha256, str) or not SHA.fullmatch(expected_sha256):
        raise ValueError("Supply the exact lowercase SHA-256 of the reviewed portable ZIP")
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Portable archive is missing or exceeds its size limit")
    if sha256(archive_path) != expected_sha256:
        raise ValueError("Portable archive does not match the supplied SHA-256")
    with zipfile.ZipFile(archive_path) as archive:
        inventory = _archive_inventory(archive)
        if len(inventory) != len(archive.infolist()):
            raise ValueError("Portable distribution must contain files only")
        if not REQUIRED <= inventory.keys():
            raise ValueError("Portable distribution is missing required companions or identity")
        def document(name):
            if inventory[name].file_size > MAX_JSON:
                raise ValueError(f"Portable metadata exceeds its size limit: {name}")
            value = strict_json(archive.read(inventory[name]))
            if not isinstance(value, dict):
                raise ValueError(f"Portable metadata must be an object: {name}")
            return value
        checksums = document("checksums.json")
        unique_paths(list(checksums))
        if set(checksums) != inventory.keys() - {"checksums.json"}:
            raise ValueError("Portable checksum manifest must exactly match all payload files")
        for name, expected in checksums.items():
            if not isinstance(expected, str) or not SHA.fullmatch(expected):
                raise ValueError(f"Invalid portable checksum: {name}")
            with archive.open(inventory[name]) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"Portable payload checksum mismatch: {name}")
        metadata, identity, resources = (document(name) for name in
            ("release.json", "build-identity.json", "resource-checksums.json"))
        resource_names = inventory.keys() - {SHELL, SIDECAR, "release.json", "checksums.json", "resource-checksums.json"}
        if resources != {name: checksums[name] for name in resource_names}:
            raise ValueError("Portable resource manifest does not match the selected build")
        expected_metadata = {"schema_version": 1, "product": "ALLIN1-SDK", "format": "tauri-v2",
                             "entrypoint": SHELL, "sidecar_entrypoint": SIDECAR,
                             "build_identity_sha256": checksums["build-identity.json"]}
        if any(metadata.get(key) != value for key, value in expected_metadata.items()):
            raise ValueError("Portable distribution metadata is incompatible")
        version, build_id = metadata.get("version"), metadata.get("build_id")
        if (type(metadata.get("schema_version")) is not int
                or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version)
                or not isinstance(build_id, str) or not re.fullmatch(r"[a-f0-9]{32}", build_id)
                or identity.get("kind") != "sdk_build_identity" or type(identity.get("schema_version")) is not int
                or identity.get("schema_version") != 1 or identity.get("sdk_version") != version
                or identity.get("build_id") != build_id):
            raise ValueError("Portable version/build identity does not agree")
        for name in (SHELL, SIDECAR, "tools/RpfPatcher/RpfPatcher.exe"):
            with archive.open(inventory[name]) as stream:
                if stream.read(2) != b"MZ":
                    raise ValueError(f"Portable executable is not a Windows PE: {name}")
    # Detect a concurrent archive replacement before any extraction is authorized.
    if sha256(archive_path) != expected_sha256:
        raise ValueError("Portable archive changed during inspection")
    return {"archive_sha256": expected_sha256, "version": version, "build_id": build_id,
            "identity": identity, "checksums": checksums, "members": len(inventory)}


def verify_tree(root: Path, package: dict) -> None:
    manifest = verify_inventory(root, "checksums.json")
    if manifest != package["checksums"]:
        raise ValueError("Extracted portable belongs to a different build")


def extract_new(archive: Path, target: Path, package: dict) -> None:
    target = no_links(target)
    if filesystem_path(target).exists():
        raise FileExistsError("Portable rehearsal never overwrites an existing destination")
    if sha256(archive) != package["archive_sha256"]:
        raise ValueError("Portable archive changed before extraction")
    _extract_archive(archive, target)
    verify_tree(target, package)
    if sha256(archive) != package["archive_sha256"]:
        raise ValueError("Portable archive changed during extraction; keep output for diagnosis")


def run_process(command, *, cwd, env, input=None, timeout=45):
    """Bound lifetime, including the PyInstaller child process on Windows."""
    # Pass lpApplicationName explicitly: Windows otherwise limits the executable
    # portion of lpCommandLine to MAX_PATH even with an extended-length path.
    process = subprocess.Popen(command, executable=command[0], cwd=filesystem_path(cwd), env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"),
                            "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            process.kill()
        process.communicate(timeout=10)
        raise
    if process.returncode != 0:
        raise RuntimeError(f"Packaged process exited {process.returncode}: {stderr[-2000:]}")
    return stdout


def probe(root: Path, package: dict, user_state: Path, *, expected_location="READY") -> dict:
    """Read-only shell identity plus real sidecar handshake/shutdown, no GUI."""
    verify_tree(root, package)
    user_state.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "ALLIN1", "TAURI", "VITE_", "QT_"))}
    for key in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "TEMP", "TMP"):
        environment[key] = str(user_state)
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
    environment["ALLIN1_SDK_HOME"] = str(filesystem_path(root))
    environment["DOTNET_ROOT"] = str(user_state / "no-dotnet")
    started = time.monotonic()
    actual = strict_json(run_process([str(filesystem_path(root / SHELL)), "--build-identity"], cwd=user_state, env=environment))
    if actual != package["identity"]:
        raise ValueError("Packaged shell embedded identity differs from its companion files")
    # Older shells open the GUI for unknown switches. Query an existing read-only
    # command first and never try a new switch unless that shell advertises it.
    capabilities = strict_json(run_process([str(filesystem_path(root / SHELL)), "--verify-embedded-frontend"], cwd=user_state, env=environment))
    if (type(capabilities.get("runtime_location_probe_version")) is not int
            or capabilities.get("runtime_location_probe_version") != 1
            or capabilities.get("build_id") != package["build_id"] or capabilities.get("status") != "PASS"):
        raise ValueError("Selected shell does not advertise the read-only runtime location probe; rebuild before testing")
    location = strict_json(run_process([str(filesystem_path(root / SHELL)), "--check-runtime-location"], cwd=user_state, env=environment))
    if (location.get("kind") != "sdk_runtime_location_probe" or type(location.get("schema_version")) is not int
            or location.get("schema_version") != 1 or location.get("build_identity") != package["identity"]
            or location.get("sidecar_process_started") is not False or location.get("release_ready") is not False
            or location.get("status") != expected_location):
        raise ValueError("Packaged runtime location probe is invalid or did not enforce its startup policy")
    if expected_location == "BLOCKED":
        if (not isinstance(location.get("error"), str) or "path is too long" not in location["error"]
                or "Move the entire SDK folder" not in location["error"] or location.get("long_path_runtime_supported") is not False):
            raise ValueError("Unsupported long installation path did not produce actionable guidance")
        verify_tree(root, package)
        return {"status": "PASS", "runtime_startup": "BLOCKED: unsupported long installation path",
                "sidecar_handshake_shutdown": "NOT TESTED", "sidecar_process_started": False,
                "long_path_runtime_supported": False, "guidance": location["error"]}
    if expected_location != "READY" or location.get("error") is not None:
        raise ValueError("Unexpected portable startup policy or error")
    def request(operation):
        return {"protocol_version": "1.0.0", "request_id": operation, "job_id": None,
                "sequence": 0, "risk": "none", "terminal": False, "operation": operation,
                "payload": {"supported_versions": ["1.0.0"], "client": {"name": "portable-rehearsal", "version": "1.0.0"}} if operation == "handshake" else {}}
    output = run_process([str(filesystem_path(root / SIDECAR))], cwd=user_state, env=environment,
                         input="".join(json.dumps(request(op)) + "\n" for op in ("handshake", "shutdown")))
    responses = [strict_json(line) for line in output.splitlines() if line.strip()]
    if len(responses) != 2:
        raise ValueError("Packaged sidecar did not complete exactly one handshake and shutdown")
    handshake, stopped = responses
    for response, operation in zip(responses, ("handshake", "shutdown")):
        if (response.get("request_id") != operation or response.get("operation") != "result"
                or response.get("protocol_version") != "1.0.0" or response.get("terminal") is not True):
            raise ValueError("Packaged sidecar response is invalid or unrelated")
    payload = handshake["payload"]
    if (payload.get("build_identity") != package["identity"] or payload.get("sdk_version") != package["version"]
            or any(payload.get(key) is not False for key in ("game_writes_enabled", "package_writes_enabled", "rpf_writes_enabled"))
            or stopped["payload"].get("state") != "stopped"):
        raise ValueError("Packaged sidecar identity, authority, or shutdown did not validate")
    verify_tree(root, package)
    return {"status": "PASS", "elapsed_seconds": round(time.monotonic() - started, 3),
            "shell_identity": "PASS", "sidecar_handshake_shutdown": "PASS", "game_write_authority": False}


def rehearse(archive: Path, expected_sha256: str, output: Path, *, execute_probes=False) -> dict:
    archive = no_links(archive)
    output = no_links(output)
    if output.exists() or not output.parent.is_dir() or gta_root_containing(output):
        raise ValueError("Choose a new evidence directory outside GTA with an existing parent")
    package = inspect_archive(archive, expected_sha256)
    output.mkdir()  # exclusive; nothing existed here before this invocation
    report = {"schema_version": 1, "kind": "sdk_portable_lifecycle_rehearsal", "status": "FAIL",
              "environment": "same-machine, disposable files and user state", "archive_sha256": expected_sha256,
              "build_id": package["build_id"], "version": package["version"], "cases": [],
              "release_qualified": False, "signature_verification": "NOT TESTED",
              "nsis_install_upgrade_uninstall": "NOT TESTED", "automatic_updater": "NOT TESTED", "live_acceptance": "NOT TESTED",
              "long_path_runtime_supported": False}
    canary = output / "outside-install-canary.bin"
    canary.write_bytes(b"Unrelated user data must survive every rehearsal step")
    canary_hash = sha256(canary)
    user_state = output / "isolated user state"
    user_state.mkdir()
    user_file = user_state / "retained-settings.json"
    user_file.write_text('{"keep":true}', encoding="utf-8")
    user_hash = sha256(user_file)
    def check(label, root, *, expected_location="READY"):
        verify_tree(root, package)
        result = probe(root, package, user_state, expected_location=expected_location) if execute_probes else {"status": "PASS", "process_probes": "NOT TESTED"}
        if sha256(canary) != canary_hash or sha256(user_file) != user_hash:
            raise ValueError("Portable rehearsal modified unrelated user data")
        report["cases"].append({"name": label, "path_length": len(str(root)), **result})
    try:
        active = output / "Portable SDK with spaces"
        extract_new(archive, active, package)
        check("fresh extraction and startup", active)
        relocated = output / "Relocated SDK with spaces"
        filesystem_path(active).rename(filesystem_path(relocated))
        check("relocation and restart", relocated)
        # Simulate a manual fresh-folder repair, retaining the old folder. This
        # is not the product updater and must never be reported as such.
        backup = output / "Retained previous portable"
        filesystem_path(relocated).rename(filesystem_path(backup))
        extract_new(archive, relocated, package)
        check("same-version fresh-folder repair", relocated)
        verify_tree(backup, package)
        rejected = output / "Retained repair copy"
        filesystem_path(relocated).rename(filesystem_path(rejected))
        filesystem_path(backup).rename(filesystem_path(relocated))
        check("manual folder rollback", relocated)
        long_root = output / "long paths"
        while len(str(long_root)) < 275:
            long_root /= "nested portable path with spaces"
        extract_new(archive, long_root, package)
        check("long-path extraction and explicit startup refusal", long_root, expected_location="BLOCKED")
        # Delete only fully reverified files in this invocation's exact copies.
        # Preserve user state, evidence, and anything unexpected on failure.
        for owned in (relocated, rejected, long_root):
            verify_tree(owned, package)
            if not owned.is_relative_to(output) or owned == output:
                raise ValueError("Invalid rehearsal cleanup boundary")
            shutil.rmtree(filesystem_path(owned))
        if sha256(canary) != canary_hash or sha256(user_file) != user_hash:
            raise ValueError("Portable removal modified unrelated user data")
        report["cases"].append({"name": "remove verified portable copies; preserve user state", "status": "PASS"})
        if sha256(archive) != expected_sha256:
            raise ValueError("Original ZIP changed during rehearsal")
        report["status"] = "PASS"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        with (output / "portable-lifecycle.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-probes", action="store_true", help="Run the reviewed shell's identity probe and sidecar handshake/shutdown; never opens the GUI")
    args = parser.parse_args()
    print(json.dumps(rehearse(args.archive, args.sha256, args.output, execute_probes=args.execute_probes), indent=2))


if __name__ == "__main__":
    main()
