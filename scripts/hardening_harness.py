"""Create fresh, off-game SDK hardening evidence.

This is deliberately neither a packager nor a launcher.  It uses installed
toolchains only, writes a new evidence directory for every invocation, and
does not inspect or change GTA, installers, or real user-state locations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from allin1_sdk.release_identity import sha256, source_identity
from allin1_sdk.release_paths import no_links, strict_json

IS_WINDOWS = os.name == "nt"
SOURCE_MODE_ENV = ("ALLIN1_NATIVE_RUNTIME_TEST", "ALLIN1_NATIVE_RPF_TEST", "ALLIN1_BLENDER_EXECUTABLE")
PRIVATE_TEST_ENV = (
    "ALLIN1_RETAIL_ANIMATION_PACKET", "ALLIN1_RETAIL_ASSET_REPORT",
    "ALLIN1_FROZEN_SIDECAR", "ALLIN1_FROZEN_RESOURCES",
    "SDK_SUPPRESSOR_REGRESSION_PACKAGE", "SDK_SUPPRESSOR_REGRESSION_GTA",
    "ALLIN1_GTA_PATH",
)
PYTHON_COVERAGE_PARENT_ENV = (
    # coverage.py process auto-start/config handoff must not leak from a
    # caller into this harness's explicitly instrumented pytest process.
    "COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG",
    "COVERAGE_RCFILE", "COVERAGE_FORCE_CONFIG",
)
EXPECTED_COVERAGE_OMIT = ("src/allin1_sdk/updater_host.py", "src/allin1_sdk/detector.py", "src/allin1_sdk/mods.py")


def write_new(path: Path, value: object) -> None:
    """Write an evidence file once.  Evidence is never overwritten or reused."""
    with no_links(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def source_snapshot(root: Path) -> dict:
    """Hash tracked and untracked source inputs, excluding only generated build trees."""
    return source_identity(root)


def safe_tool(value: str | None) -> str | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path(".") or candidate.exists():
        candidate = no_links(candidate if candidate.is_absolute() else ROOT / candidate)
        return str(candidate) if candidate.is_file() else None
    return shutil.which(value)


def _tool_version(path: str) -> str | None:
    """Capture a bounded identity hint; absence is recorded, never guessed."""
    if Path(path).suffix.casefold() == ".dll":
        return None  # A companion library is hashed, never launched as a tool.
    try:
        run = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (run.stdout or run.stderr).strip().replace("\r", " ").replace("\n", " ")
        return text[:500] if run.returncode == 0 and text else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def tool_identity(path: str | None) -> dict | None:
    if not path:
        return None
    file = Path(path)
    try:
        return {"path": str(file), "sha256": sha256(file), "version": _tool_version(path)}
    except OSError as error:
        return {"path": str(file), "sha256": None, "version": None, "error": str(error)}


def run_command(argv: list[str], cwd: Path, output: Path, name: str, env: dict[str, str]) -> dict:
    """Run one owned process group and stop its descendants on timeout."""
    started = time.time_ns() // 1_000_000
    log = no_links(output / f"{name}.log")
    timed_out = False
    with log.open("xb") as stream:
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=creationflags, start_new_session=not IS_WINDOWS)
            try:
                code = process.wait(timeout=1800)
            except subprocess.TimeoutExpired as error:
                timed_out = True
                if IS_WINDOWS:
                    # This PID is the process we created. /T is limited to its descendants.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                else:  # pragma: no cover - Windows is the native fixture platform
                    os.killpg(process.pid, signal.SIGKILL)
                code = process.wait(timeout=30)
                stream.write((str(error) + "\n").encode("utf-8", "replace"))
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write((str(error) + "\n").encode("utf-8", "replace"))
            code = -1
    return {"argv": argv, "cwd": str(cwd), "started_ms": started,
            "ended_ms": time.time_ns() // 1_000_000, "returncode": code,
            "log": str(log), "log_sha256": sha256(log), "timed_out": timed_out}


def incomplete(reason: str) -> dict:
    return {"status": "INCOMPLETE", "reason": reason}


def failed(reason: str) -> dict:
    return {"status": "FAIL", "reason": reason}


def _fresh_file(path: Path, command: dict) -> bytes:
    path = no_links(path)
    info = path.stat()
    if not path.is_file() or not 0 < info.st_size <= 32 * 1024**2:
        raise ValueError(f"Missing or empty required evidence: {path.name}")
    # Filesystems have coarse timestamp precision, hence the small tolerance.
    if not command["started_ms"] - 1500 <= int(info.st_mtime * 1000) <= command["ended_ms"] + 1500:
        raise ValueError(f"Stale evidence is not accepted: {path.name}")
    return path.read_bytes()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _case_nodes(root: ET.Element) -> list[ET.Element]:
    return [node for node in root.iter() if _local(node.tag) == "testcase"]


def _direct_cases(suite: ET.Element) -> list[ET.Element]:
    return [node for node in suite if _local(node.tag) == "testcase"]


def _child(case: ET.Element, name: str) -> ET.Element | None:
    return next((node for node in case if _local(node.tag) == name), None)


def validate_junit(path: Path, command: dict, label: str) -> dict:
    """Require reconciled, named JUnit outcomes; retain named failed evidence."""
    try:
        root = ET.fromstring(_fresh_file(path, command))
    except ET.ParseError as error:
        raise ValueError(f"Malformed {label} JUnit evidence") from error
    cases = _case_nodes(root)
    if not cases:
        raise ValueError(f"{label} JUnit has no test cases")
    identities = [(case.get("classname", ""), case.get("name", "")) for case in cases]
    if any(not name for _klass, name in identities) or len(set(identities)) != len(identities):
        raise ValueError(f"{label} JUnit has missing or duplicate test identities")
    suites = [node for node in root.iter() if _local(node.tag) == "testsuite"]
    for suite in suites:
        own = _direct_cases(suite)
        if not own:
            continue
        declared = suite.get("tests")
        if declared is not None and (not declared.isdigit() or int(declared) != len(own)):
            raise ValueError(f"{label} JUnit test count does not reconcile")
        for attribute, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
            value = suite.get(attribute)
            actual = sum(_child(case, tag) is not None for case in own)
            if value is not None and (not value.isdigit() or int(value) != actual):
                raise ValueError(f"{label} JUnit {attribute} count does not reconcile")
    failures = [case for case in cases if _child(case, "failure") is not None or _child(case, "error") is not None]
    skipped = []
    for case in cases:
        node = _child(case, "skipped")
        if node is not None:
            skipped.append({"name": case.get("name", ""), "classname": case.get("classname", ""),
                            "reason": node.get("message") or (node.text or "").strip() or "no reason recorded"})
    result = {"tests": len(cases), "passed": len(cases) - len(skipped) - len(failures), "report_sha256": sha256(path)}
    if failures:
        failed_cases = [{"name": case.get("name", ""), "classname": case.get("classname", "")}
                        for case in failures]
        return {"status": "FAIL", "reason": f"{label} evidence includes failing tests",
                "failed": failed_cases, **({"skipped": skipped} if skipped else {}), **result}
    return {"status": "INCOMPLETE", "skipped": skipped, **result} if skipped else {"status": "PASS", **result}


def validate_coverage(path: Path, command: dict, root: Path) -> dict:
    value = strict_json(_fresh_file(path, command))
    total = value.get("totals", {})
    percent = total.get("percent_covered")
    try:
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Coverage configuration is missing or malformed") from error
    coverage = config.get("tool", {}).get("coverage", {})
    if coverage.get("run", {}).get("source") != ["allin1_sdk"] or coverage.get("run", {}).get("branch") is not True:
        raise ValueError("Configured Python source/branch coverage scope changed")
    if coverage.get("report", {}).get("fail_under") != 80:
        raise ValueError("Configured Python coverage threshold changed")
    if value.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError("Python coverage is missing branch evidence")
    if type(percent) not in (int, float) or not 80 <= percent <= 100 or not total.get("num_statements", 0):
        raise ValueError("Python coverage is missing or below the unchanged 80% threshold")
    files = value.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Python coverage has no source-file evidence")
    package = (root / "src" / "allin1_sdk").resolve()
    configured_omit = tuple(coverage.get("report", {}).get("omit", []))
    if configured_omit != EXPECTED_COVERAGE_OMIT:
        raise ValueError("Configured Python coverage omit set changed")
    omitted = set(configured_omit)
    expected = {file.resolve() for file in package.rglob("*.py")
                if file.relative_to(root).as_posix() not in omitted}
    reported: dict[Path, dict] = {}
    for name, entry in files.items():
        file = Path(name) if Path(name).is_absolute() else root / name
        try:
            file = no_links(file).resolve()
            if not file.is_relative_to(package) or not file.is_file() or file in reported:
                raise ValueError("Coverage contains an unrelated or missing source file")
        except OSError as error:
            raise ValueError("Coverage contains an unreadable source file") from error
        if not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
            raise ValueError("Coverage has malformed source-file evidence")
        reported[file] = entry["summary"]
    if set(reported) != expected:
        missing = sorted(str(file.relative_to(root)) for file in expected - set(reported))
        extra = sorted(str(file.relative_to(root)) for file in set(reported) - expected)
        raise ValueError(f"Coverage source inventory differs from configured source/omit scope: missing={missing[:3]} extra={extra[:3]}")
    fields = ("num_statements", "covered_lines", "missing_lines", "num_branches", "covered_branches", "missing_branches")
    sums = {field: 0 for field in fields}
    for summary in reported.values():
        if any(type(summary.get(field)) is not int or summary[field] < 0 for field in fields):
            raise ValueError("Coverage source-file summary is incomplete")
        if summary["num_statements"] != summary["covered_lines"] + summary["missing_lines"]:
            raise ValueError("Coverage line summary does not reconcile")
        if summary["num_branches"] != summary["covered_branches"] + summary["missing_branches"]:
            raise ValueError("Coverage branch summary does not reconcile")
        for field in fields:
            sums[field] += summary[field]
    if any(type(total.get(field)) is not int or total[field] != sums[field] for field in fields):
        raise ValueError("Coverage totals do not reconcile with every measured source file")
    denominator = sums["num_statements"] + sums["num_branches"]
    calculated = 100 * (sums["covered_lines"] + sums["covered_branches"]) / denominator if denominator else 0
    if denominator == 0 or abs(percent - calculated) > 0.01:
        raise ValueError("Coverage percentage does not reconcile with measured totals")
    return {"coverage_percent": percent, "coverage_threshold": 80, "branch_coverage": True,
            "source_files": len(reported), "configured_omit": list(configured_omit),
            "coverage_report_sha256": sha256(path)}


def validate_react(path: Path, inventory_path: Path, desktop: Path, command: dict) -> dict:
    report = strict_json(_fresh_file(path, command))
    inventory = strict_json(no_links(inventory_path).read_bytes())
    counts = ("numTotalTests", "numPassedTests", "numFailedTests", "numPendingTests", "numTodoTests")
    if any(type(report.get(key)) is not int or report[key] < 0 for key in counts) or not report["numTotalTests"]:
        raise ValueError("React evidence has missing or invalid test counts")
    if report.get("success") is not True or report["numFailedTests"]:
        raise ValueError("React evidence includes failed tests")
    incomplete_rows = []
    if report["numPendingTests"] or report["numTodoTests"] or report["numTotalTests"] != report["numPassedTests"]:
        incomplete_rows.append({"classname": "Vitest summary", "name": "summary",
                                "reason": f"pending={report['numPendingTests']} todo={report['numTodoTests']} passed={report['numPassedTests']}/{report['numTotalTests']}"})
    if type(report.get("startTime")) not in (int, float) or not command["started_ms"] - 1500 <= report["startTime"] <= command["ended_ms"] + 1500:
        raise ValueError("React report is not from this invocation")
    rows, suite_paths = [], set()
    suites = report.get("testResults")
    if not isinstance(suites, list) or not suites:
        raise ValueError("React evidence has no test suites")
    for suite in suites:
        try:
            file = no_links(Path(suite["name"])).resolve()
        except (KeyError, OSError) as error:
            raise ValueError("React suite has an invalid file") from error
        if not file.is_relative_to(desktop.resolve()) or not file.is_file() or file in suite_paths:
            raise ValueError("React suite is unrelated, missing, or duplicated")
        suite_paths.add(file)
        suite_status = suite.get("status")
        if suite_status in ("skipped", "pending", "todo"):
            incomplete_rows.append({"classname": str(file.relative_to(desktop.resolve())), "name": "suite",
                                    "reason": suite.get("message") or suite_status})
        elif suite_status != "passed" or suite.get("message"):
            raise ValueError("React suite did not pass")
        seen = set()
        for assertion in suite.get("assertionResults", []):
            name = assertion.get("fullName")
            status = assertion.get("status")
            if status in ("pending", "skipped", "todo"):
                incomplete_rows.append({"classname": str(file.relative_to(desktop.resolve())),
                                        "name": assertion.get("title") or name or "unnamed assertion",
                                        "reason": status})
            elif status != "passed" or assertion.get("failureMessages"):
                raise ValueError("React assertion failed")
            if not isinstance(name, str) or not name or name in seen:
                raise ValueError("React assertion failed or has an ambiguous identity")
            seen.add(name)
            rows.append((file, assertion))
    if len(rows) != report["numTotalTests"]:
        raise ValueError("React summary does not match actual assertions")
    modules = inventory.get("modules") if inventory.get("schema_version") == 1 else None
    if not isinstance(modules, list) or not modules:
        raise ValueError("React happy-path inventory is invalid or empty")
    names, mapped = set(), []
    for module in modules:
        if not isinstance(module, dict) or set(module) - {"module", "test_file", "test_title", "native"}:
            raise ValueError("React happy-path inventory contains an unsupported entry")
        name = module.get("module")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("React happy-path inventory has ambiguous module identity")
        names.add(name)
        expected = no_links(desktop / module.get("test_file", "")).resolve()
        matches = [row for file, row in rows if file == expected and row.get("title") == module.get("test_title")]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous React mapped happy path: {name}")
        mapped.append({"module": name, "native": bool(module.get("native")), "test": matches[0]["fullName"],
                       "status": "PASS" if matches[0].get("status") == "passed" else "INCOMPLETE"})
    if incomplete_rows:
        expected_skips = report["numPendingTests"] + report["numTodoTests"]
        assertion_skips = sum(row.get("name") != "summary" and row.get("name") != "suite" for row in incomplete_rows)
        if assertion_skips and assertion_skips != expected_skips:
            raise ValueError("React skipped assertion count does not reconcile with summary")
        return {"status": "INCOMPLETE", "tests": len(rows), "passed": report["numPassedTests"], "files": len(suite_paths),
                "modules": mapped, "skipped": incomplete_rows, "report_sha256": sha256(path), "inventory_sha256": sha256(inventory_path)}
    return {"status": "PASS", "tests": len(rows), "passed": len(rows), "files": len(suite_paths),
            "modules": mapped, "report_sha256": sha256(path), "inventory_sha256": sha256(inventory_path)}


def validate_rust(log: Path, desktop: Path) -> dict:
    """Stable libtest has no result JSON; reconcile named stdout against Cargo artifacts."""
    text = no_links(log).read_text(encoding="utf-8", errors="replace")
    binaries = {}
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = strict_json(line.encode())
        except (ValueError, json.JSONDecodeError):
            continue
        if event.get("reason") == "compiler-artifact" and event.get("profile", {}).get("test") and event.get("executable"):
            path = no_links(Path(event["executable"]))
            if not path.is_relative_to((desktop / "src-tauri" / "target").resolve()) or not path.is_file():
                raise ValueError("Rust test executable is unrelated or missing")
            binaries[str(path)] = sha256(path)
    rows = re.findall(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\s*$", text, re.MULTILINE)
    summaries = re.findall(r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;", text, re.MULTILINE)
    if not binaries or not rows or not summaries:
        raise ValueError("Rust evidence lacks named tests, summaries, or test executables")
    if len({name for name, _state in rows}) != len(rows):
        raise ValueError("Rust evidence has duplicate named test results")
    if any(state != "ok" for _name, state in rows) or any(status != "ok" or any(int(value) for value in rest) for status, _passed, *rest in summaries):
        raise ValueError("Rust evidence includes failed, ignored, measured, or filtered tests")
    if sum(int(passed) for _status, passed, *_rest in summaries) != len(rows):
        raise ValueError("Rust test summaries do not reconcile with named results")
    return {"status": "PASS", "tests": len(rows), "passed": len(rows), "test_names": [name for name, _ in rows],
            "executables": binaries, "report_sha256": sha256(log)}


def validate_rpf_runner(log: Path) -> dict:
    """Validate the SDK's custom .NET runner, which is not a TRX/xUnit suite."""
    text = no_links(log).read_text(encoding="utf-8", errors="replace")
    matches = re.findall(r"(?m)^Exact native member resolution: ([1-9][0-9]*) checks passed \(no game required\)\.\s*$", text)
    if len(matches) != 1:
        raise ValueError("RpfPatcher runner has no unique positive named check result")
    count = int(matches[0])
    return {"status": "PASS", "tests": count, "passed": count,
            "runner": "Exact native member resolution", "report_sha256": sha256(log),
            "scope": "custom off-game .NET runner; not xUnit/TRX evidence"}


def invoke(name: str, argv: list[str], cwd: Path, output: Path, env: dict[str, str], validate=None, execute=run_command) -> tuple[dict, dict]:
    try:
        command = execute(argv, cwd, output, name, env)
    except Exception as error:
        return {"argv": argv, "cwd": str(cwd), "exception": str(error)}, failed(f"Could not execute {name}: {error}")
    if command.get("timed_out"):
        return command, failed(f"{name} exceeded its timeout; see its process cleanup log")
    if validate is None:
        result = {"status": "PASS"}
    else:
        try:
            result = validate(command)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            return command, failed(str(error))
    if command.get("returncode") != 0:
        # A command failure never passes, but parse fresh valid evidence first so
        # AUDIT.md retains the real test and coverage diagnostics.
        return command, {**result, "status": "FAIL", "reason": f"{name} exited {command.get('returncode')}"}
    return command, result


def profile(options, tools: dict[str, str | None], output: Path, execute=run_command) -> tuple[dict, dict]:
    """Run independent source-only layers. Missing prerequisites are explicit."""
    commands, layers = {}, {}
    env = dict(os.environ)
    for key in (*SOURCE_MODE_ENV, *PRIVATE_TEST_ENV, *PYTHON_COVERAGE_PARENT_ENV):
        env.pop(key, None)  # source mode never inherits a retail/private tool opt-in
    env["ALLIN1_SDK_TEST_PYTHON"] = tools.get("python") or ""
    env["COVERAGE_FILE"] = str(output / ".coverage")
    if options.real_tools:
        env.update(ALLIN1_NATIVE_RUNTIME_TEST="1", ALLIN1_NATIVE_RPF_TEST="1")
        blender = tools.get("blender")
        if blender:
            env["ALLIN1_BLENDER_EXECUTABLE"] = blender
    def require(key: str, layer_name: str) -> bool:
        if tools.get(key):
            return True
        layers[layer_name] = incomplete(f"Required prerequisite is unavailable: {key}")
        return False
    python, pnpm, cargo = tools.get("python"), tools.get("pnpm"), tools.get("cargo")
    desktop = ROOT / "desktop"
    if require("python", "python"):
        junit, coverage = output / "python.junit.xml", output / "python.coverage.json"
        commands["python"], evidence = invoke("python", [python, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
            f"--basetemp={output / 'pytest'}", "--cov=allin1_sdk", f"--cov-report=json:{coverage}", "--cov-fail-under=80", f"--junitxml={junit}"], ROOT, output, env,
            lambda command: {**validate_junit(junit, command, "Python"), **validate_coverage(coverage, command, ROOT)}, execute)
        layers["python"] = evidence
    if require("pnpm", "react"):
        commands["typescript-build"], build = invoke("typescript-build", [pnpm, "build"], desktop, output, env, execute=execute)
        report = output / "vitest.json"
        commands["react"], tests = invoke("react", [pnpm, "test", "--reporter=json", f"--outputFile={report}"], desktop, output, env,
            lambda command: validate_react(report, desktop / "module-happy-paths.json", desktop, command), execute)
        status = "FAIL" if "FAIL" in (build["status"], tests["status"]) else "INCOMPLETE" if "INCOMPLETE" in (build["status"], tests["status"]) else "PASS"
        layers["react"] = {"status": status, "mode": "prepared-native-fixtures" if options.real_tools else "source-only; native fixture opt-in disabled", "build": build, "tests": tests}
        if options.real_tools and not tools.get("blender") and layers["react"]["status"] == "PASS":
            layers["react"] = incomplete("--real-tools requires a prepared pinned Blender executable for native render workflows")
    if require("cargo", "rust-native"):
        rust_env = dict(env, TAURI_CONFIG=json.dumps({"bundle": {"active": False, "resources": []}}))
        commands["rust-native"], layers["rust-native"] = invoke("rust-native", [cargo, "test", "--locked", "--manifest-path", str(desktop / "src-tauri" / "Cargo.toml"), "--message-format=json", "--", "--format=pretty"], ROOT, output, rust_env,
            lambda _command: validate_rust(output / "rust-native.log", desktop), execute)
        commands["rust-native"]["configuration_override"] = json.loads(rust_env["TAURI_CONFIG"])
    if python:
        documentation = output / "documentation.junit.xml"
        commands["documentation"], layers["documentation"] = invoke("documentation", [python, "-m", "pytest", "tests/test_desktop_documentation.py", "-q", "-p", "no:cacheprovider", f"--junitxml={documentation}"], ROOT, output, env,
            lambda command: validate_junit(documentation, command, "Documentation"), execute)
        commands["retirement"], layers["retirement"] = invoke("retirement", [python, str(ROOT / "scripts" / "tk_retirement.py")], ROOT, output, env, execute=execute)
    else:
        layers["documentation"] = incomplete("Required prerequisite is unavailable: python")
        layers["retirement"] = incomplete("Required prerequisite is unavailable: python")
    # Native SDK tests are optional capabilities: only run configured projects on Windows.
    project = ROOT / "tools" / "RpfPatcher.Tests" / "RpfPatcher.Tests.csproj"
    if IS_WINDOWS and project.is_file():
        if require("dotnet", "csharp"):
            commands["csharp"], layers["csharp"] = invoke("csharp", [tools["dotnet"], "run", "--project", str(project), "-c", "Release", "--no-restore"], ROOT, output, env,
                lambda _command: validate_rpf_runner(output / "csharp.log"), execute)
    else:
        layers["csharp"] = incomplete("C# native fixture is not configured for this platform")
    cmake_project = ROOT / "runtime" / "VehicleWorkbenchAxles" / "CMakeLists.txt"
    if cmake_project.is_file() and IS_WINDOWS:
        if require("cmake", "cpp-native") and require("ctest", "cpp-native"):
            build = output / "cpp-build"
            steps = []
            for suffix, argv in (("configure", [tools["cmake"], "-S", str(cmake_project.parent), "-B", str(build), "-DCMAKE_BUILD_TYPE=Debug", "-DVWA_BUILD_STORY_HOSTS=OFF", "-DVWA_BUILD_TESTS=ON", "-DVWA_BUILD_CONFIG_VALIDATOR=OFF", "-DVWA_BUILD_SETTINGS_EDITOR=OFF"]),
                                 ("build", [tools["cmake"], "--build", str(build), "--config", "Debug"]),
                                 ("ctest", [tools["ctest"], "--test-dir", str(build), "-C", "Debug", "--output-on-failure", "--output-junit", str(output / "cpp.junit.xml")])):
                command, result = invoke("cpp-" + suffix, argv, ROOT, output, env,
                    (lambda command: validate_junit(output / "cpp.junit.xml", command, "C++")) if suffix == "ctest" else None, execute)
                commands["cpp-" + suffix] = command; steps.append(result)
            status = "FAIL" if any(step["status"] == "FAIL" for step in steps) else "INCOMPLETE" if any(step["status"] == "INCOMPLETE" for step in steps) else "PASS"
            layers["cpp-native"] = {"status": status, "steps": steps}
    else:
        layers["cpp-native"] = incomplete("C++ native fixture is not configured for this platform")
    return commands, layers


def write_audit(path: Path, summary: dict) -> None:
    """Write a compact human receipt without hiding skipped or untested scope."""
    lines = ["# ALLIN1 SDK off-game hardening audit", "", f"Status: **{summary['status']}**", f"Run: `{summary['run_id']}`", "",
             "## Commands", ""]
    for name, command in summary["commands"].items():
        rendered = " ".join(f'"{part}"' if any(char.isspace() for char in part) else part for part in command.get("argv", []))
        lines.append(f"- `{name}`: exit `{command.get('returncode', 'not started')}` — `{rendered}`")
    lines.extend(["", "## Layers", ""])
    def append_layer(name: str, layer: dict) -> None:
        detail = layer.get("reason", "")
        if "tests" in layer and isinstance(layer["tests"], int):
            detail = f"{layer.get('passed', 0)}/{layer['tests']} passed" + (f"; {detail}" if detail else "")
        if "coverage_percent" in layer:
            detail += f"; line/branch coverage {layer['coverage_percent']:.2f}%"
        skipped = layer.get("skipped")
        if skipped:
            reasons = "; ".join(f"{row.get('classname', '')}::{row.get('name', '')}: {row.get('reason', '')}" for row in skipped)
            detail += ("; " if detail else "") + reasons
        failures = layer.get("failed")
        if failures:
            names = "; ".join(f"{row.get('classname', '')}::{row.get('name', '')}" for row in failures)
            detail += ("; " if detail else "") + f"failed: {names}"
        lines.append(f"- `{name}`: **{layer['status']}**" + (f" — {detail}" if detail else ""))
        for child_name, child in layer.items():
            if isinstance(child, dict) and "status" in child:
                append_layer(f"{name}/{child_name}", child)
            elif isinstance(child, list) and child_name == "steps":
                for index, step in enumerate(child, 1):
                    append_layer(f"{name}/step-{index}", step)
    for name, layer in summary["layers"].items():
        append_layer(name, layer)
    lines.extend(["", "## Explicitly untested", ""])
    lines.extend(f"- `{name}`: **{status}**" for name, status in summary["unrun"].items())
    lines.extend(["", "This is an off-game source receipt, not release qualification.", ""])
    with no_links(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))


def run(options, *, execute=run_command) -> tuple[int, Path, dict]:
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = options.run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,31}", run_id):
        raise ValueError("run id must be 8-32 lowercase letters, digits, or hyphens")
    output = no_links(ROOT / "build" / "hardening" / run_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()  # exclusive: no arbitrary/reused output path and no deletion
    before = source_snapshot(ROOT)
    write_new(output / "source-before.json", before)
    requested = {name: getattr(options, name, None) for name in ("python", "node", "pnpm", "cargo", "dotnet", "cmake", "ctest", "blender")}
    tools = {name: safe_tool(value) for name, value in requested.items()}
    # Generated helper binaries are intentionally outside the source digest.
    # Bind the actual decoder and its companions as well as its checked-in code.
    for name, filename in (("rpf_helper", "RpfPatcher.exe"),
                           ("rpf_helper_dll", "RpfPatcher.dll"),
                           ("rpf_core_dll", "CodeWalker.Core.dll")):
        candidate = no_links(ROOT / "tools" / "RpfPatcher" / filename)
        tools[name] = str(candidate) if candidate.is_file() else None
    tools_before = {name: tool_identity(path) for name, path in tools.items()}
    commands, layers = profile(options, tools, output, execute)
    if options.real_tools and any(tools[name] is None for name in ("rpf_helper", "rpf_helper_dll", "rpf_core_dll")):
        layers["native-helper-prerequisites"] = incomplete("Prepared RpfPatcher and its companion libraries are required")
    after = source_snapshot(ROOT)
    write_new(output / "source-after.json", after)
    if before != after:
        layers["source-integrity"] = failed("Source inputs changed during the hardening run")
    else:
        layers["source-integrity"] = {"status": "PASS"}
    tools_after = {name: tool_identity(path) for name, path in tools.items()}
    tool_report = {name: {"before": tools_before[name], "after": tools_after[name], "unchanged": tools_before[name] == tools_after[name]}
                   for name in tools}
    if any((value and value.get("sha256") is None) for value in [*tools_before.values(), *tools_after.values()]):
        layers["tool-integrity"] = failed("A selected tool could not be hashed")
    elif any(value["before"] != value["after"] for value in tool_report.values() if value["before"] is not None):
        layers["tool-integrity"] = failed("A selected tool changed during the hardening run")
    statuses = {value["status"] for value in layers.values()}
    overall = "FAIL" if "FAIL" in statuses else "INCOMPLETE" if "INCOMPLETE" in statuses else "PASS"
    summary = {"schema_version": 1, "kind": "allin1_sdk_off_game_hardening", "run_id": run_id,
               "started_at": started_at, "ended_at": datetime.now(timezone.utc).isoformat(),
               "output": str(output), "source_before": before, "source_after": after, "source_unchanged": before == after,
               "tools": tool_report, "commands": commands, "layers": layers, "status": overall, "release_ready": False,
               "unrun": {"packaged_release": "NOT TESTED", "installer": "NOT TESTED", "native_installer_lifecycle": "NOT TESTED",
                           "live_game": "NOT TESTED", "GTA": "NOT TESTED", "real_user_state": "NOT TESTED"}, "human_audit": "AUDIT.md"}
    write_new(output / "summary.json", summary)
    write_audit(output / "AUDIT.md", summary)
    return (0 if overall == "PASS" else 1), output, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--real-tools", action="store_true", help="Opt into already-prepared native fixture workflows; never launches GTA.")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--node", default="node")
    parser.add_argument("--pnpm", default="pnpm")
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--ctest", default="ctest")
    parser.add_argument("--blender", help="Prepared checksum-pinned Blender executable required by --real-tools React rendering tests")
    options = parser.parse_args()
    try:
        code, output, summary = run(options)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"summary": str(output / "summary.json"), "sha256": sha256(output / "summary.json"), "status": summary["status"], "release_ready": False}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
