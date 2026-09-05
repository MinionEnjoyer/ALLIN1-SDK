"""Build an isolated, non-qualifying frozen service for same-machine debugging.

This never runs NSIS, replaces release staging, installs an app, launches GTA or
publishes. It intentionally cannot seal a release or produce a readiness PASS.
An optional compiled shell/portable probe is still not native UI acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.desktop_candidate import ROOT, check_source, execution_command, external_executable, prepare, verify_frontend_probe, write_new, write_portable
from scripts.frozen_desktop import EXCLUDED_MODULES, inspect_frozen
from scripts.stage_desktop_resources import stage_resources
from allin1_sdk.release_identity import sha256, verify_inventory
from allin1_sdk.release_paths import no_links, strict_json, tree_files


def run(command: list[str], root: Path, log: Path, *, env=None) -> None:
    print(f"Running {log.name}", flush=True)
    with log.open("xb") as output:
        result = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT, env=env,
            timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"Diagnostic command failed: {log}")


def build_shell(root: Path, identity_path: Path, resources: Path, sidecar: Path, pnpm: str) -> dict:
    """Compile and inspect a complete portable payload without touching staging."""
    identity = check_source(root, identity_path)
    folder = identity_path.parent
    manager = external_executable(Path(shutil.which(pnpm) or pnpm))
    invocation, _ = execution_command([pnpm, "build"], manager)
    run(invocation, root / "desktop", folder / "frontend.log")
    env = dict(os.environ, ALLIN1_BUILD_IDENTITY_FILE=str(identity_path),
               TAURI_CONFIG=json.dumps({"bundle": {"active": False, "resources": []}}))
    run(["cargo", "build", "--release", "--locked", "--features", "tauri/custom-protocol",
         "--manifest-path", str(root / "desktop/src-tauri/Cargo.toml")], root, folder / "native.log", env=env)
    shell = folder / "allin1-sdk-desktop.exe"
    # Cargo may hard-link its output. Copy to a newly owned candidate file.
    compiled = root / "desktop/src-tauri/target/release/allin1-sdk-desktop.exe"
    no_links(compiled.parent)
    if not compiled.is_file() or getattr(compiled.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("Invalid diagnostic shell")
    with compiled.open("rb") as incoming, shell.open("xb") as output:
        shutil.copyfileobj(incoming, output)
    run([str(shell), "--verify-embedded-frontend"], folder, folder / "frontend-probe.json")
    verify_frontend_probe(strict_json((folder / "frontend-probe.json").read_bytes()), identity, root / "desktop/dist")
    actual = tree_files(resources)
    actual.update({"allin1-sdk-desktop.exe": shell, "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe": sidecar})
    expected = {name: sha256(path) for name, path in actual.items()}
    portable = write_portable(folder / f"ALLIN1-SDK-{identity['sdk_version']}-diagnostic-{identity['build_id'][:12]}-portable.zip",
                              expected, actual, identity=identity)
    with (folder / (portable["file"] + ".sha256")).open("x", encoding="utf-8") as stream:
        stream.write(f"{portable['sha256']}  {portable['file']}\n")
    from scripts.portable_lifecycle import rehearse
    lifecycle = rehearse(folder / portable["file"], portable["sha256"], folder / "portable-lifecycle", execute_probes=True)
    return {"native_shell_build": "PASS", "embedded_frontend": "PASS", "portable": portable,
            "portable_lifecycle": {"status": lifecycle["status"],
                "report_sha256": sha256(folder / "portable-lifecycle/portable-lifecycle.json"),
                "long_path_runtime_supported": False},
            "shell_sha256": sha256(shell), "frontend_probe_sha256": sha256(folder / "frontend-probe.json")}


def build(root: Path, pnpm: str, *, with_shell: bool = False) -> Path:
    identity_path = prepare(root, pnpm)
    folder = identity_path.parent
    print(f"Non-qualifying SDK diagnostic: {folder}", flush=True)
    identity = check_source(root, identity_path)
    report = {"schema_version": 1, "kind": "sdk_diagnostic_frozen_service",
        "identity": identity, "environment": "same-machine, disposable user state",
        "release_readiness": "FAIL", "full_test_qualification": "NOT TESTED",
        "native_gui": "NOT TESTED", "installer_lifecycle": "NOT TESTED",
        "live_acceptance": "NOT TESTED", "status": "FAIL"}
    try:
        rpf = folder / "rpf-publish"
        run(["dotnet", "publish", str(root / "tools/RpfPatcher/RpfPatcher.csproj"),
             "-c", "Release", "-r", "win-x64", "--self-contained", "true", "-o", str(rpf)], root, folder / "publish.log")
        resources = stage_resources(root, rpf, identity_path, destination=folder / "resources")
        run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--console", "--onefile",
             "--name", "ALLIN1-SDK-Desktop-Sidecar", "--paths", str(root / "src"),
             "--icon", str(root / "assets/favicon.ico"), "--version-file", str(folder / "sidecar-version.txt"),
             "--add-data", str(identity_path) + os.pathsep + "allin1_sdk",
             "--add-data", str(resources / "resource-checksums.json") + os.pathsep + "allin1_sdk",
             "--distpath", str(folder / "sidecar"), "--workpath", str(folder / "work"), "--specpath", str(folder),
             *(arg for module in EXCLUDED_MODULES for arg in ("--exclude-module", module)),
             str(root / "scripts/desktop_sidecar_entry.py")], root, folder / "freeze.log")
        sidecar = folder / "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"
        report["tk_free_payload"] = inspect_frozen(sidecar)
        for script, arguments in (
            ("smoke_desktop_sidecar.py", ["--build-identity", str(identity_path)]),
            ("smoke_ped_desktop.py", []),
        ):
            run([sys.executable, str(root / "scripts" / script), str(sidecar),
                 "--resource-home", str(resources), *arguments], root, folder / (script + ".log"))
        if with_shell:
            report.update(build_shell(root, identity_path, resources, sidecar, pnpm))
        check_source(root, identity_path)
        verify_inventory(resources)
        owned_logs = ["publish.log", "freeze.log", "smoke_desktop_sidecar.py.log", "smoke_ped_desktop.py.log"]
        if with_shell:
            owned_logs.extend(["frontend.log", "native.log"])
        report.update(status="PASS", artifact_sha256=sha256(sidecar), artifact_bytes=sidecar.stat().st_size,
            resource_manifest_sha256=sha256(resources / "resource-checksums.json"),
            logs={name: sha256(folder / name) for name in owned_logs})
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        write_new(folder / "diagnostic-validation.json", report)
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pnpm", default="pnpm")
    parser.add_argument("--with-shell", action="store_true", help="Also probe/package the native shell; not GUI, installer or release acceptance")
    args = parser.parse_args()
    print(build(ROOT, args.pnpm, with_shell=args.with_shell))


if __name__ == "__main__":
    main()
