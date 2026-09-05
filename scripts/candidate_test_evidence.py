"""Fresh framework evidence for candidate gates, independent of display logs."""
from __future__ import annotations

import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from allin1_sdk.release_identity import sha256
from allin1_sdk.release_paths import contained, no_links, strict_json


def instrument(name: str, command: list[str], root: Path, folder: Path) -> list[str]:
    """Allow full canonical gates only; no selection flags or label-only scripts."""
    manifest = str(root / "desktop/src-tauri/Cargo.toml")
    native_project = str(root / "tools/RpfPatcher.Tests/RpfPatcher.Tests.csproj")
    expected = {
        "python": ["-m", "pytest", "--cov=allin1_sdk", "--cov-report=term-missing"],
        "react": ["test"], "frontend": ["build"],
        "rust": ["test", "--manifest-path", manifest],
        "native-rpf": ["run", "--project", native_project, "-c", "Release"],
    }[name]
    supplied = command[1:]
    # PowerShell may author native manifest paths with either Windows separator.
    if len(supplied) != len(expected) or any(
        Path(a) != Path(b) if b in {manifest, native_project} else a != b
        for a, b in zip(supplied, expected)
    ):
        raise ValueError(f"Candidate {name} gate requires the complete canonical command")
    extras = {
        "python": [f"--junitxml={folder / 'gate-python.xml'}", f"--cov-report=json:{folder / 'gate-coverage.json'}"],
        "react": ["--reporter=json", f"--outputFile={folder / 'gate-react-results.json'}"],
        "rust": ["--locked", "--message-format=json", "--", "--format=pretty"],
    }.get(name, [])
    for filename in report_names(name):
        if no_links(folder / filename).exists():
            raise FileExistsError(f"Candidate test report already exists: {filename}")
    return [*command, *extras]


def report_names(name: str) -> tuple[str, ...]:
    return {"python": ("gate-python.xml", "gate-coverage.json"),
            "react": ("gate-react-results.json",)}.get(name, ())


def report_bytes(path: Path, started: float, finished: float, *, replay: bool = False) -> bytes:
    path = no_links(path)
    info = path.stat()
    if not 0 < info.st_size <= 32 * 1024**2 or (not replay and not started - 1 <= info.st_mtime <= finished + 1):
        raise ValueError(f"Missing, oversized or stale candidate report: {path.name}")
    return path.read_bytes()


def python_evidence(xml_bytes: bytes, coverage: dict, root: Path) -> dict:
    cases = ET.fromstring(xml_bytes).findall(".//testcase")
    identities = [(row.get("classname"), row.get("name")) for row in cases]
    if not cases or any(not name for _, name in identities) or len(set(identities)) != len(cases):
        raise ValueError("Missing or ambiguous Python test evidence")
    if any(row.find(tag) is not None for row in cases for tag in ("skipped", "failure", "error")):
        raise ValueError("Python candidate tests failed or were skipped")
    if coverage.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError("Python candidate requires branch coverage")
    total = coverage.get("totals", {})
    percent = total.get("percent_covered")
    if type(percent) not in (int, float) or not 80 <= percent <= 100 or not total.get("num_statements", 0):
        raise ValueError("Python coverage is missing or below the unchanged 80% threshold")
    files = coverage.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Python coverage has no source-file evidence")
    for name in files:
        path = no_links(Path(name) if Path(name).is_absolute() else root / name)
        if not path.is_relative_to(root / "src/allin1_sdk") or not path.is_file():
            raise ValueError("Coverage contains an unrelated or missing source file")
    return {"tests": len(cases), "coverage_percent": percent, "coverage_threshold": 80,
            "branch_coverage": True}


def react_evidence(report: dict, inventory: dict, root: Path, started: float, finished: float) -> dict:
    counts = [report.get(key) for key in ("numTotalTests", "numPassedTests", "numFailedTests", "numPendingTests", "numTodoTests")]
    if any(type(value) is not int or value < 0 for value in counts) or not counts[0] or counts[0] != counts[1] or any(counts[2:]):
        raise ValueError("React candidate checks are missing, failed or skipped")
    if report.get("success") is not True or not started * 1000 <= report.get("startTime", 0) <= finished * 1000:
        raise ValueError("React report is not from this candidate invocation")
    assertions = []
    suites = report.get("testResults", [])
    suite_names = set()
    for suite in suites:
        path = no_links(Path(suite["name"]))
        if path in suite_names or not path.is_relative_to(root / "desktop") or not path.is_file():
            raise ValueError("React test file is duplicated, unrelated or missing")
        suite_names.add(path)
        if suite.get("status") != "passed" or suite.get("message") or not started * 1000 <= suite["startTime"] <= suite["endTime"] <= finished * 1000:
            raise ValueError("React suite failed or belongs to another session")
        seen = set()
        for assertion in suite["assertionResults"]:
            full = assertion.get("fullName")
            if assertion.get("status") != "passed" or assertion.get("failureMessages") or not full or full in seen:
                raise ValueError("React assertion failed, is ambiguous or was skipped")
            seen.add(full)
            assertions.append((path, assertion.get("title")))
    if len(assertions) != counts[0]:
        raise ValueError("React summary does not match its actual assertions")
    if type(inventory.get("schema_version")) is not int or inventory["schema_version"] != 1:
        raise ValueError("Unknown React acceptance inventory schema")
    modules = inventory.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("Empty React acceptance inventory")
    names = set()
    for module in modules:
        name = module.get("module")
        if not name or name in names:
            raise ValueError("Ambiguous React module identity")
        names.add(name)
        if assertions.count((contained(root / "desktop", module["test_file"]), module["test_title"])) != 1:
            raise ValueError(f"Missing or ambiguous React acceptance check: {name}")
    return {"tests": len(assertions), "modules": sorted(names), "inventory_sha256": sha256(root / "desktop/module-happy-paths.json")}


def collect(name: str, root: Path, folder: Path, started: float, finished: float, *, replay: bool = False) -> dict:
    reports = {filename: report_bytes(folder / filename, started, finished, replay=replay) for filename in report_names(name)}
    evidence = {"schema_version": 1, "reports": {filename: {"sha256": sha256(folder / filename), "bytes": len(content)} for filename, content in reports.items()}}
    if name == "python":
        evidence.update(python_evidence(reports["gate-python.xml"], strict_json(reports["gate-coverage.json"]), root))
    elif name == "react":
        inventory = strict_json((root / "desktop/module-happy-paths.json").read_bytes())
        evidence.update(react_evidence(strict_json(reports["gate-react-results.json"]), inventory, root, started, finished))
    elif name == "rust":
        text = (folder / "gate-rust.log").read_text(encoding="utf-8")
        summaries = re.findall(r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;", text, re.MULTILINE)
        rows = re.findall(r"^test (\S+) \.\.\. (ok|FAILED|ignored)\s*$", text, re.MULTILINE)
        if not summaries or not rows or any(status != "ok" or any(int(v) for v in rest) for status, _passed, *rest in summaries):
            raise ValueError("Rust candidate checks are missing, failed, ignored or filtered")
        if sum(int(row[1]) for row in summaries) != len(rows) or len({row[0] for row in rows}) != len(rows) or any(row[1] != "ok" for row in rows):
            raise ValueError("Rust summaries disagree with named test outcomes")
        binaries = {}
        for line in text.splitlines():
            if not line.startswith('{'): continue
            event = strict_json(line)
            if event.get("reason") == "compiler-artifact" and event.get("profile", {}).get("test") and event.get("executable"):
                path = no_links(Path(event["executable"]))
                if not path.is_relative_to(root / "desktop/src-tauri/target") or not path.is_file():
                    raise ValueError("Rust test executable is missing or unrelated")
                binaries[str(path)] = sha256(path)
        if not binaries: raise ValueError("Missing Rust test executable identities")
        evidence.update(tests=len(rows), test_names=[row[0] for row in rows], executables=binaries)
    elif name == "frontend":
        entry = root / "desktop/dist/index.html"
        report_bytes(entry, started, finished, replay=replay)
        from allin1_sdk.release_paths import tree_files
        evidence["artifacts"] = {key: sha256(path) for key, path in tree_files(entry.parent).items()}
    elif name == "native-rpf":
        text = (folder / "gate-native-rpf.log").read_text(encoding="utf-8")
        # This producer is versioned in the same reviewed source tree. Keep this
        # adapter narrow until its console harness emits structured results.
        matches = re.findall(r"^Exact native member resolution: (\d+) checks passed \(no game required\)\.$", text, re.MULTILINE)
        if len(matches) != 1 or int(matches[0]) < 1:
            raise ValueError("Missing native RPF acceptance evidence")
        evidence["tests"] = int(matches[0])
    return evidence
