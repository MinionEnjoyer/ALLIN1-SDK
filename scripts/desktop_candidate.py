"""Prepare/seal a uniquely identified unsigned candidate; never publish or install."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
import stat
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# PowerShell/NSIS invoke this file directly, including from unrelated working
# directories. Resolve sibling build helpers from this checkout, not cwd.
sys.path.insert(0, str(ROOT))

from allin1_sdk.release_identity import sha256, source_identity, verify_inventory
from allin1_sdk.release_paths import contained, no_links, relative_path, strict_json, tree_files, unique_paths
from scripts import candidate_test_evidence

PLUGIN_NAMES = {"System.dll", "modern-wizard.bmp", "nsDialogs.dll", "nsis_tauri_utils.dll", "StartMenu.dll", "NSISdl.dll"}
REQUIRED_GATES = frozenset({"python", "react", "rust", "native-rpf", "frontend"})


def external_executable(path: Path) -> Path:
    """Validate an external tool without applying payload hard-link policy.

    Windows system executables (notably cmd.exe) may legitimately have multiple
    hard links. They are read-only build inputs whose bytes are hashed, not
    candidate payloads, so reject reparse traversal while allowing that OS layout.
    """
    lexical = Path(os.path.abspath(path))
    for item in (*reversed(lexical.parents), lexical):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Reparse path is forbidden for candidate tool: {item}")
    resolved = lexical.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"Candidate tool is not a file: {resolved}")
    return resolved


def execution_command(command: list[str], executable: Path) -> tuple[list[str], Path | None]:
    """Return a Windows-safe invocation without losing the authored executable.

    CreateProcess cannot execute ``.cmd``/``.bat`` wrappers directly.  pnpm is
    commonly distributed as one, so invoke that exact, hash-bound wrapper through
    the OS command processor and record the processor identity as well.
    """
    if os.name == "nt" and executable.suffix.casefold() in {".cmd", ".bat"}:
        command_processor = Path(os.environ.get("COMSPEC", "cmd.exe"))
        if not command_processor.is_absolute():
            resolved = shutil.which(str(command_processor))
            if resolved is None:
                raise FileNotFoundError("Windows command processor was not found")
            command_processor = Path(resolved)
        command_processor = external_executable(command_processor)
        # Do not combine /s with subprocess' list-to-command-line quoting: /s
        # strips the outer quotes from wrapper paths containing spaces.
        return [str(command_processor), "/d", "/c", str(executable), *command[1:]], command_processor
    return [str(executable), *command[1:]], None


def tool_identity(executable: Path) -> dict[str, str]:
    """Pin bytes and selected location without shipping a developer-local path.

    The build-only gate receipt retains the actual invocation. The distributable
    identity needs only a one-way location binding to reject an independent
    lookup that selects an identical executable from another installation.
    """
    return {"sha256": sha256(executable),
            "path_binding_sha256": hashlib.sha256(str(executable).encode("utf-8")).hexdigest()}


def write_new(path: Path, value: dict) -> None:
    no_links(path)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")))


def prepare(root: Path, pnpm: str, *, allow_windows_symlink_skips: bool = False) -> Path:
    source = source_identity(root)
    if not source["versions_agree"]:
        raise ValueError("Source versions disagree")
    require_python_metadata(next(iter(source["versions"].values())))
    tools = {}
    toolchain_files = {}
    for name, command in {"python": [sys.executable, "--version"], "node": ["node", "--version"],
            "pnpm": [pnpm, "--version"], "cargo": ["cargo", "--version"],
            "rustc": ["rustc", "--version"], "dotnet": ["dotnet", "--version"]}.items():
        resolved = shutil.which(command[0])
        if resolved is None:
            authored = Path(command[0]).expanduser()
            if not authored.is_file():
                raise FileNotFoundError(f"Candidate toolchain executable was not found: {name} ({command[0]})")
            resolved = str(authored.resolve(strict=True))
        executable = external_executable(Path(resolved))
        toolchain_files[name] = tool_identity(executable)
        invocation, _launcher = execution_command(command, executable)
        try:
            tools[name] = subprocess.check_output(invocation, text=True, timeout=30).strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"Candidate toolchain probe failed: {name} ({executable})") from error
    build_id = uuid.uuid4().hex
    destination = contained(root, f"build/tauri-candidates/{build_id}")
    destination.mkdir(parents=True, exist_ok=False)
    identity = {"schema_version": 1, "kind": "sdk_build_identity", "build_id": build_id,
        "sdk_version": next(iter(source["versions"].values())), "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
        "toolchains": tools, "toolchain_files": toolchain_files,
        "python_packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())),
        "lockfiles": {name: sha256(contained(root, name)) for name in ["desktop/pnpm-lock.yaml", "desktop/src-tauri/Cargo.lock"]},
        "schema_versions": {"desktop_protocol": "1.0.0", "candidate_identity": 1, "candidate_gate": 2, "live_acceptance": 1},
        "release_qualified": False}
    if allow_windows_symlink_skips:
        if identity["sdk_version"] != "0.6.4":
            raise ValueError("Windows symlink privilege waiver is approved only for SDK 0.6.4")
        identity["python_skip_waiver"] = candidate_test_evidence.WINDOWS_SYMLINK_WAIVER
    path = destination / "_build_identity.json"
    write_new(path, identity)
    version = identity["sdk_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Candidate PE version must be an explicit three-part release version")
    numbers = tuple(map(int, version.split("."))) + (0,)
    with (destination / "sidecar-version.txt").open("x", encoding="utf-8") as stream:
        stream.write(f'''VSVersionInfo(
ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
kids=[StringFileInfo([StringTable('040904B0', [
StringStruct('FileDescription', 'ALLIN1 SDK Desktop Sidecar'),
StringStruct('FileVersion', {version!r}), StringStruct('ProductVersion', {version!r}),
StringStruct('ProductName', 'ALLIN1 SDK'), StringStruct('BuildId', {build_id!r})])]),
VarFileInfo([VarStruct('Translation', [1033, 1200])])])''')
    return path


def require_python_metadata(version: str) -> None:
    if importlib.metadata.version("allin1-sdk") != version:
        raise ValueError("Python SDK distribution metadata is stale; reinstall this checkout before building")


def check_source(root: Path, identity_path: Path) -> dict:
    identity = strict_json(no_links(identity_path).read_bytes())
    if (identity.get("schema_version") != 1 or identity.get("kind") != "sdk_build_identity"
            or identity.get("source") != source_identity(root)):
        raise ValueError("Source changed during candidate build; discard/rebuild the candidate")
    return identity


def run_gate(
    root: Path, identity_path: Path, name: str, command: list[str],
    *, cwd: str = ".", timeout: int = 2400,
) -> Path:
    """Execute and bind one required validation gate to an immutable candidate.

    The command, executable bytes, complete output log, source identity, result,
    and timestamps are captured by the same process that observes the exit code.
    A caller therefore cannot turn an unrelated or stale successful log into
    evidence for this candidate.
    """
    if name not in REQUIRED_GATES:
        raise ValueError(f"Unknown candidate gate: {name}")
    if not command or any(not isinstance(item, str) or not item or "\0" in item for item in command):
        raise ValueError("Candidate gate command must contain bounded arguments")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 7200:
        raise ValueError("Candidate gate timeout must be between 1 and 7,200 seconds")
    identity = check_source(root, identity_path)
    normalized_cwd = "." if cwd == "." else relative_path(cwd).as_posix()
    workdir = no_links(root) if normalized_cwd == "." else contained(root, normalized_cwd)
    if not workdir.is_dir():
        raise ValueError(f"Candidate gate working directory was not found: {cwd}")
    gate_path = contained(identity_path.parent, f"gate-{name}.json")
    log_path = contained(identity_path.parent, f"gate-{name}.log")
    no_links(gate_path)
    no_links(log_path)
    if gate_path.exists() or log_path.exists():
        raise FileExistsError(f"Candidate gate evidence already exists: {name}")
    executable = shutil.which(command[0])
    if executable is None:
        authored = Path(command[0]).expanduser()
        if not authored.is_file():
            raise FileNotFoundError(f"Candidate gate executable was not found: {command[0]}")
        executable = str(authored.resolve(strict=True))
    executable_path = external_executable(Path(executable))
    tool_name = {"python": "python", "react": "pnpm", "frontend": "pnpm", "rust": "cargo", "native-rpf": "dotnet"}[name]
    anchor = identity.get("toolchain_files", {}).get(tool_name)
    if anchor != tool_identity(executable_path):
        raise ValueError("Candidate gate tool differs from its prepared identity; prepare a fresh candidate")
    measured_command = candidate_test_evidence.instrument(name, command, root, identity_path.parent)
    invocation, launcher_path = execution_command(measured_command, executable_path)
    started = datetime.now(timezone.utc).isoformat()
    timed_out = False
    exit_code = -1
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    environment.pop("PYTEST_ADDOPTS", None)
    with log_path.open("x", encoding="utf-8") as stream:
        stream.write(f"candidate={identity['build_id']}\ngate={name}\n")
        stream.flush()
        try:
            completed = subprocess.run(
                invocation, cwd=workdir, stdout=stream, stderr=subprocess.STDOUT,
                text=True, timeout=timeout, check=False, env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            stream.write(f"\nGate timed out after {timeout} seconds.\n")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    skipped_reported = bool(re.search(r"\bskipped\b", log_text, re.IGNORECASE))
    coverage_failed = "FAIL Required test coverage" in log_text
    if name == "react" and skipped_reported and exit_code == 0:
        exit_code = 2
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write("\nCandidate React gate rejected: one or more tests were skipped.\n")
    if name == "python" and coverage_failed and exit_code == 0:
        exit_code = 2
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write("\nCandidate Python gate rejected: coverage reported a threshold failure.\n")
    source_after = check_source(root, identity_path)
    finished = datetime.now(timezone.utc).isoformat()
    measured_evidence = None
    evidence_error = None
    if exit_code == 0 and not timed_out:
        try:
            measured_evidence = candidate_test_evidence.collect(name, root, identity_path.parent,
                datetime.fromisoformat(started).timestamp(), datetime.fromisoformat(finished).timestamp())
        except Exception as error:
            evidence_error = str(error)
            exit_code = 2
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"\nCandidate gate rejected incomplete framework evidence: {error}\n")
    record = {
        "schema_version": 2,
        "kind": "sdk_candidate_gate",
        "build_id": identity["build_id"],
        "name": name,
        "status": "PASS" if exit_code == 0 and not timed_out else "FAIL",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "skipped_reported": skipped_reported,
        "coverage_failed": coverage_failed,
        "timeout_seconds": timeout,
        "command": measured_command,
        "evidence": measured_evidence,
        "evidence_error": evidence_error,
        "cwd": normalized_cwd,
        "started_at": started,
        "finished_at": finished,
        "source_tree_sha256": source_after["source"]["source_tree_sha256"],
        "log": {"file": log_path.name, "sha256": sha256(log_path), "bytes": log_path.stat().st_size},
        "executable": {
            "path": str(executable_path), "sha256": sha256(executable_path),
            "bytes": executable_path.stat().st_size,
        },
    }
    if launcher_path is not None:
        record["launcher"] = {
            "path": str(launcher_path), "sha256": sha256(launcher_path),
            "bytes": launcher_path.stat().st_size,
        }
    write_new(gate_path, record)
    if record["status"] != "PASS":
        if timed_out:
            raise TimeoutError(f"Candidate gate timed out: {name}")
        raise subprocess.CalledProcessError(exit_code, command)
    return gate_path


def gate_evidence(identity_path: Path, *, root: Path = ROOT) -> dict[str, dict]:
    """Load the exact complete validation set required to seal a candidate."""
    identity = strict_json(no_links(identity_path).read_bytes())
    evidence: dict[str, dict] = {}
    for name in sorted(REQUIRED_GATES):
        path = contained(identity_path.parent, f"gate-{name}.json")
        value = strict_json(no_links(path).read_bytes())
        expected = {
            "schema_version": 2,
            "kind": "sdk_candidate_gate",
            "build_id": identity.get("build_id"),
            "name": name,
            "status": "PASS",
            "source_tree_sha256": identity.get("source", {}).get("source_tree_sha256"),
        }
        if not isinstance(value, dict) or any(value.get(key) != item for key, item in expected.items()):
            raise ValueError(f"Candidate gate evidence is invalid or stale: {name}")
        log = value.get("log")
        if (
            not isinstance(log, dict) or log.get("file") != f"gate-{name}.log"
            or log.get("sha256") != sha256(contained(identity_path.parent, log["file"]))
            or log.get("bytes") != contained(identity_path.parent, log["file"]).stat().st_size
        ):
            raise ValueError(f"Candidate gate log changed after execution: {name}")
        if value.get("exit_code") != 0 or value.get("timed_out") is not False or value.get("evidence_error"):
            raise ValueError(f"Candidate gate did not complete: {name}")
        measured = candidate_test_evidence.collect(name, root, identity_path.parent,
            datetime.fromisoformat(value["started_at"]).timestamp(), datetime.fromisoformat(value["finished_at"]).timestamp(), replay=True)
        if measured != value.get("evidence"):
            raise ValueError(f"Candidate framework evidence changed after execution: {name}")
        evidence[name] = value
    return evidence


def capture_nsis_shell(root: Path, identity_path: Path, shell: Path) -> None:
    """Called by makensis before File, while Tauri's NSIS marker is present.

    Tauri restores the compiler binary after packaging. Pin the actual temporary
    NSIS staging bytes rather than accepting an arbitrary post-build mismatch.
    """
    identity = check_source(root, identity_path)
    expected_path = root / "desktop/src-tauri/target/release/allin1-sdk-desktop.exe"
    if shell.resolve(strict=True) != expected_path.resolve(strict=True):
        raise ValueError("Unexpected NSIS staged shell path")
    output = subprocess.check_output([str(shell), "--build-identity"], timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if strict_json(output) != identity:
        raise ValueError("NSIS staged shell belongs to another build")
    target = contained(identity_path.parent, "nsis-staged-shell.exe")
    with shell.open("rb") as source, target.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    write_new(identity_path.parent / "nsis-stage.json", {"schema_version": 1,
        "build_id": identity["build_id"], "shell_sha256": sha256(target)})


def nsis_members(listing: str) -> list[str]:
    try:
        body = listing.split("----------", 1)[1]
    except IndexError as error:
        raise ValueError("Unrecognized NSIS inventory") from error
    paths = [line[7:].replace("\\", "/") for line in body.splitlines() if line.startswith("Path = ")]
    if not paths:
        raise ValueError("Empty NSIS inventory")
    unique_paths(paths)
    for path in paths:
        relative_path(path)
    return paths


def compare_payload(expected: dict[str, str], actual: dict[str, Path]) -> dict:
    extras = set(actual) - set(expected)
    allowed = {f"$PLUGINSDIR/{name}" for name in PLUGIN_NAMES} | {"uninstall.exe"}
    if set(expected) - set(actual) or extras != allowed:
        raise ValueError(f"NSIS payload set mismatch: missing={sorted(set(expected) - set(actual))}, unexpected={sorted(extras ^ allowed)}")
    for name, digest in expected.items():
        if sha256(actual[name]) != digest:
            raise ValueError(f"Packaged/staged bytes differ: {name}")
    return {name: sha256(actual[name]) for name in sorted(extras)}


def verify_frontend_probe(report: dict, identity: dict, frontend: Path) -> None:
    """A native executable header/build ID is insufficient: require embedded UI."""
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
            or report.get("kind") != "embedded_frontend_probe" or report.get("status") != "PASS"
            or report.get("production") is not True or report.get("build_id") != identity["build_id"]
            or report.get("version") != identity["sdk_version"] or report.get("release_ready") is not False
            or report.get("native_ui") != "NOT TESTED"):
        raise ValueError("Unrelated or non-production embedded frontend evidence")
    assets = report.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Embedded frontend inventory is missing")
    names = []
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {"path", "bytes"} or type(asset["bytes"]) is not int or asset["bytes"] <= 0:
            raise ValueError("Invalid embedded frontend asset")
        names.append(asset["path"])
    unique_paths(names)
    if set(names) != set(tree_files(frontend)):
        raise ValueError("Compiled frontend inventory differs from the production build")


def write_portable(
    destination: Path, expected: dict[str, str], actual: dict[str, Path], *, identity: dict | None = None,
) -> dict:
    """Create and reverify one exact, deterministic portable application ZIP."""
    destination = no_links(destination)
    if destination.exists():
        raise FileExistsError(f"Portable candidate already exists: {destination}")
    unique_paths(list(expected))
    if set(expected) != set(actual):
        raise ValueError("Portable source inventory must exactly match its payload plan")
    for name, source in actual.items():
        relative_path(name)
        source = no_links(source)
        if not source.is_file() or sha256(source) != expected[name]:
            raise ValueError(f"Portable source payload changed: {name}")
    generated: dict[str, bytes] = {}
    expected = dict(expected)
    if identity is not None:
        if "release.json" in expected or "checksums.json" in expected:
            raise ValueError("Portable inputs collide with generated distribution metadata")
        unique_paths([*expected, "release.json", "checksums.json"])
        required = {"build-identity.json", "resource-checksums.json", "allin1-sdk-desktop.exe", "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"}
        if not required <= expected.keys():
            raise ValueError("Portable SDK is missing required identity or companion payloads")
        if strict_json(actual["build-identity.json"].read_bytes()) != identity:
            raise ValueError("Portable metadata identity differs from the actual payload")
        generated["release.json"] = json.dumps({
            "schema_version": 1, "product": "ALLIN1-SDK", "format": "tauri-v2",
            "version": identity["sdk_version"], "build_id": identity["build_id"],
            "entrypoint": "allin1-sdk-desktop.exe",
            "sidecar_entrypoint": "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe",
            "build_identity_sha256": expected["build-identity.json"],
        }, sort_keys=True, separators=(",", ":")).encode()
        expected["release.json"] = hashlib.sha256(generated["release.json"]).hexdigest()
        generated["checksums.json"] = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        expected["checksums.json"] = hashlib.sha256(generated["checksums.json"]).hexdigest()
    with zipfile.ZipFile(
        destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
    ) as archive:
        for name in sorted(expected, key=str.casefold):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if name in generated:
                archive.writestr(info, generated[name])
            else:
                with actual[name].open("rb") as source, archive.open(info, "w") as output:
                    shutil.copyfileobj(source, output)
    with zipfile.ZipFile(destination) as archive:
        infos = archive.infolist()
        names = [relative_path(info.orig_filename).as_posix() for info in infos]
        unique_paths(names)
        if set(names) != set(expected) or len(names) != len(expected):
            raise ValueError("Portable candidate inventory differs from its payload plan")
        for info, name in zip(infos, names):
            kind = stat.S_IFMT(info.external_attr >> 16)
            if info.orig_filename != info.filename or info.is_dir() or kind != stat.S_IFREG:
                raise ValueError(f"Portable candidate contains an unsafe member: {name}")
            if hashlib.sha256(archive.read(info)).hexdigest() != expected[name]:
                raise ValueError(f"Portable candidate payload differs: {name}")
    return {
        "file": destination.name, "sha256": sha256(destination),
        "bytes": destination.stat().st_size, "members": len(expected),
    }


def seal(root: Path, identity_path: Path, sevenzip: Path) -> Path:
    identity = check_source(root, identity_path)
    gates = gate_evidence(identity_path, root=root)
    destination = no_links(identity_path.parent)
    resources = root / "desktop/src-tauri/standalone-resources"
    expected = verify_inventory(resources)
    if strict_json((resources / "build-identity.json").read_bytes()) != identity:
        raise ValueError("Staged resources belong to another candidate")
    expected["resource-checksums.json"] = sha256(resources / "resource-checksums.json")
    sidecar = root / "desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"
    shell = root / "desktop/src-tauri/target/release/allin1-sdk-desktop.exe"
    # Cargo's executable may be a hard link to its deps output. This is read-only.
    no_links(shell.parent)
    info = shell.lstat()
    if not shell.is_file() or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Invalid staged shell")
    output = subprocess.check_output([str(shell), "--build-identity"], timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if strict_json(output) != identity:
        raise ValueError("Staged shell belongs to another candidate")
    compiler_shell_hash = sha256(shell)
    staged_shell = contained(destination, "nsis-staged-shell.exe")
    nsis_stage = strict_json(contained(destination, "nsis-stage.json").read_bytes())
    if nsis_stage != {"schema_version": 1, "build_id": identity["build_id"], "shell_sha256": sha256(staged_shell)}:
        raise ValueError("NSIS staging capture is invalid or belongs to another build")
    expected["allin1-sdk-desktop.exe"] = nsis_stage["shell_sha256"]
    expected["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"] = sha256(no_links(sidecar))
    installer = contained(root, f'desktop/src-tauri/target/release/bundle/nsis/ALLIN1 SDK_{identity["sdk_version"]}_x64-setup.exe')
    candidate = destination / f'ALLIN1-SDK-{identity["sdk_version"]}-candidate-{identity["build_id"][:12]}-setup.exe'
    if candidate.exists():
        raise ValueError("Never overwrite a sealed candidate")
    shutil.copy2(installer, candidate)
    digest = sha256(candidate)
    listing = subprocess.check_output([str(sevenzip), "l", "-slt", str(candidate)], text=True, timeout=60)
    members = nsis_members(listing)
    allowed = set(expected) | {f"$PLUGINSDIR/{name}" for name in PLUGIN_NAMES} | {"uninstall.exe"}
    if set(members) != allowed:
        raise ValueError("Generated installer contents do not exactly match the staged payload plan")
    extracted = destination / "extracted"
    extracted.mkdir(exist_ok=False)
    subprocess.run([str(sevenzip), "x", "-y", f"-o{extracted}", str(candidate)], check=True,
        capture_output=True, timeout=120)
    actual = tree_files(extracted)
    installer_components = compare_payload(expected, actual)
    frontend_probe = subprocess.check_output([str(actual["allin1-sdk-desktop.exe"]), "--verify-embedded-frontend"],
        timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    verify_frontend_probe(strict_json(frontend_probe), identity, root / "desktop/dist")
    write_new(destination / "frontend-probe.json", strict_json(frontend_probe))
    from scripts.frozen_desktop import inspect_frozen
    no_tk = inspect_frozen(actual["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"])
    portable_sources = {name: actual[name] for name in expected}
    portable = write_portable(
        destination / (
            f'ALLIN1-SDK-{identity["sdk_version"]}-candidate-'
            f'{identity["build_id"][:12]}-portable.zip'
        ),
        expected, portable_sources, identity=identity,
    )
    from scripts.portable_lifecycle import rehearse
    portable_lifecycle = rehearse(destination / portable["file"], portable["sha256"],
                                 destination / "portable-lifecycle", execute_probes=True)
    # Build a resource-only home from the *extracted* installer, not staging.
    smoke_home = destination / "extracted-resource-home"
    smoke_home.mkdir(exist_ok=False)
    for name in expected.keys() - {"allin1-sdk-desktop.exe", "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"}:
        path = contained(smoke_home, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(actual[name], path)
    for script, extra in [("smoke_desktop_sidecar.py", ["--build-identity", str(identity_path)]), ("smoke_ped_desktop.py", [])]:
        log = destination / (script.removesuffix(".py") + ".log")
        with log.open("x", encoding="utf-8") as stream:
            subprocess.run([sys.executable, str(root / "scripts" / script),
                str(actual["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"]), "--resource-home", str(smoke_home), *extra],
                stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=180)
    check_source(root, identity_path)
    compare_payload(expected, tree_files(extracted))
    if sha256(candidate) != digest:
        raise ValueError("Candidate bytes changed during validation")
    receipt = {"schema_version": 1, "kind": "sdk_candidate_validation", "identity": identity,
        "installer": {"file": candidate.name, "sha256": digest, "bytes": candidate.stat().st_size},
        "portable": portable,
        "portable_lifecycle": {"status": portable_lifecycle["status"],
            "report_sha256": sha256(destination / "portable-lifecycle/portable-lifecycle.json"),
            "environment": portable_lifecycle["environment"], "long_path_runtime_supported": False},
        "nsis_install_upgrade_uninstall": "NOT TESTED",
        "payloads": expected, "installer_components": installer_components,
        "compiler_shell_sha256": compiler_shell_hash, "nsis_staging_capture": nsis_stage,
        "package_integrity": "PASS", "automated_packaged_smokes": "PASS",
        "tk_free_frozen_payload": no_tk,
        "embedded_frontend": "PASS", "frontend_probe_sha256": sha256(destination / "frontend-probe.json"),
        "automated_full_suite": "PASS_WITH_APPROVED_WAIVERS" if gates["python"]["evidence"].get("waived_tests") else "PASS", "validation_gates": gates,
        "live_acceptance": "NOT TESTED",
        "reviewed_clean_source": "FAIL" if identity["source"]["dirty"] else "NOT TESTED",
        "signature": "NOT TESTED", "release_readiness": "FAIL",
        "logs": {name: sha256(destination / name) for name in ["smoke_desktop_sidecar.log", "smoke_ped_desktop.log"]},
        "inspection_tool": {"sha256": sha256(sevenzip), "dll_sha256": sha256(sevenzip.with_suffix(".dll"))}}
    # 7-Zip's format library is 7z.dll, not the console executable basename.
    write_new(destination / "candidate-validation.json", receipt)
    with candidate.with_suffix(".exe.sha256").open("x", encoding="utf-8") as stream:
        stream.write(f"{digest}  {candidate.name}\n")
    portable_path = destination / portable["file"]
    with portable_path.with_suffix(".zip.sha256").open("x", encoding="utf-8") as stream:
        stream.write(f"{portable['sha256']}  {portable['file']}\n")
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check", "gate", "capture", "seal"])
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--pnpm", default="pnpm")
    parser.add_argument("--allow-windows-symlink-skips", action="store_true", help="Apply only the maintainer-approved 0.6.4 Windows symlink privilege exception")
    parser.add_argument("--sevenzip", type=Path)
    parser.add_argument("--shell", type=Path)
    parser.add_argument("--name", choices=sorted(REQUIRED_GATES))
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--command-json")
    options = parser.parse_args()
    if options.action == "prepare":
        print(prepare(ROOT, options.pnpm, allow_windows_symlink_skips=options.allow_windows_symlink_skips))
    elif options.action == "check":
        check_source(ROOT, options.identity)
    elif options.action == "gate":
        command = json.loads(options.command_json) if options.command_json is not None else None
        if not isinstance(command, list):
            parser.error("--command-json must encode an argument array")
        if not options.identity or not options.name or not command:
            parser.error("gate requires --identity, --name, and --command-json")
        print(run_gate(
            ROOT, options.identity, options.name, command,
            cwd=options.cwd, timeout=options.timeout,
        ))
    elif options.action == "capture":
        capture_nsis_shell(ROOT, options.identity, options.shell)
    else:
        if not options.sevenzip:
            parser.error("seal requires --sevenzip")
        print(seal(ROOT, options.identity, options.sevenzip.resolve(strict=True)))


if __name__ == "__main__":
    main()
