"""Adversarial candidate framework reports and a real disposable pytest gate."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scripts import candidate_test_evidence as evidence, desktop_candidate as candidate


@pytest.fixture
def python_report(tmp_path):
    source = tmp_path / "src/allin1_sdk/example.py"; source.parent.mkdir(parents=True); source.write_text("value = 1")
    xml = b'<testsuites><testsuite><testcase classname="tests.real" name="roundtrip"/></testsuite></testsuites>'
    coverage = {"meta": {"branch_coverage": True}, "totals": {"percent_covered": 80, "num_statements": 1}, "files": {"src/allin1_sdk/example.py": {}}}
    return xml, coverage, tmp_path


def test_complete_python_evidence_keeps_unchanged_threshold(python_report):
    result = evidence.python_evidence(*python_report)
    assert result == {"tests": 1, "coverage_percent": 80, "coverage_threshold": 80, "branch_coverage": True}


@pytest.mark.parametrize("case", ["empty", "skip", "failure", "error", "duplicate", "no-branch", "low", "boolean", "no-source", "outside-source", "missing-source"])
def test_incomplete_python_evidence_is_not_qualification(python_report, case):
    xml, coverage, root = python_report
    if case == "empty": xml = b"<testsuites/>"
    elif case in {"skip", "failure", "error"}: xml = xml.replace(b'/>', f'><{"skipped" if case == "skip" else case}/></testcase>'.encode())
    elif case == "duplicate": xml = xml.replace(b'</testsuite>', b'<testcase classname="tests.real" name="roundtrip"/></testsuite>')
    elif case == "no-branch": coverage["meta"]["branch_coverage"] = False
    elif case == "low": coverage["totals"]["percent_covered"] = 79.99
    elif case == "boolean": coverage["totals"]["percent_covered"] = True
    elif case == "no-source": coverage["files"] = {}
    elif case == "outside-source": coverage["files"] = {str(root.parent / "unrelated.py"): {}}
    else: (root / "src/allin1_sdk/example.py").unlink()
    with pytest.raises(ValueError): evidence.python_evidence(xml, coverage, root)


@pytest.fixture
def react_report(tmp_path):
    path = tmp_path / "desktop/src/action.test.tsx"; path.parent.mkdir(parents=True); path.write_text("assertion source")
    inventory = {"schema_version": 1, "modules": [{"module": "authoring", "test_file": "src/action.test.tsx", "test_title": "saves and reopens"}]}
    (tmp_path / "desktop/module-happy-paths.json").write_text(json.dumps(inventory))
    report = {"success": True, "startTime": 1100, "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0,
        "testResults": [{"name": str(path), "status": "passed", "message": "", "startTime": 1200, "endTime": 1800,
            "assertionResults": [{"fullName": "workspace saves and reopens", "title": "saves and reopens", "status": "passed", "failureMessages": []}]}]}
    return report, inventory, tmp_path, 1, 2


def test_react_actual_module_evidence_passes(react_report):
    result = evidence.react_evidence(*react_report)
    assert result["tests"] == 1 and result["modules"] == ["authoring"]


@pytest.mark.parametrize("case", ["missing-count", "skip", "stale", "wrong-module", "missing-suite", "counts", "failed-assertion", "duplicate-suite", "schema", "empty-inventory", "duplicate-module", "unrelated-test"])
def test_react_partial_or_stale_evidence_fails(react_report, case):
    report, inventory, root, start, end = react_report
    suite = report["testResults"][0]
    if case == "missing-count": del report["numPassedTests"]
    elif case == "skip": report["numPendingTests"] = 1
    elif case == "stale": report["startTime"] = 999
    elif case == "wrong-module": inventory["modules"][0]["test_title"] = "another action"
    elif case == "missing-suite": report["testResults"] = []
    elif case == "counts": report.update(numTotalTests=2, numPassedTests=2)
    elif case == "failed-assertion": suite["assertionResults"][0]["status"] = "failed"
    elif case == "duplicate-suite": report["testResults"].append(copy.deepcopy(suite))
    elif case == "schema": inventory["schema_version"] = True
    elif case == "empty-inventory": inventory["modules"] = []
    elif case == "duplicate-module": inventory["modules"].append(copy.deepcopy(inventory["modules"][0]))
    else: suite["name"] = str(root / "outside.test.tsx")
    with pytest.raises(ValueError): evidence.react_evidence(report, inventory, root, start, end)


@pytest.mark.parametrize("name", sorted(candidate.REQUIRED_GATES))
def test_label_only_commands_cannot_qualify_a_gate(tmp_path, name):
    with pytest.raises(ValueError, match="canonical"):
        evidence.instrument(name, [sys.executable, "-c", "print('passed')"], tmp_path, tmp_path)
    assert not list(tmp_path.iterdir())


def test_filtered_python_and_reused_report_are_refused(tmp_path):
    command = [sys.executable, "-m", "pytest", "--cov=allin1_sdk", "--cov-report=term-missing"]
    with pytest.raises(ValueError): evidence.instrument("python", [*command, "-k", "one"], tmp_path, tmp_path)
    instrumented = evidence.instrument("python", command, tmp_path, tmp_path)
    assert any(value.startswith("--junitxml=") for value in instrumented)
    (tmp_path / "gate-python.xml").write_text("stale")
    with pytest.raises(FileExistsError): evidence.instrument("python", command, tmp_path, tmp_path)


def test_stale_report_is_not_fresh_evidence(tmp_path):
    path = tmp_path / "results.json"; path.write_text("{}")
    now = time.time(); os.utime(path, (now - 3600, now - 3600))
    with pytest.raises(ValueError, match="stale"): evidence.report_bytes(path, now, now + 1)


@pytest.mark.parametrize("skip", [False, True])
def test_real_python_gate_requires_actual_no_skip_framework_results(tmp_path, monkeypatch, skip):
    source = {"source_tree_sha256": "b" * 64}
    monkeypatch.setattr(candidate, "source_identity", lambda _: source)
    package = tmp_path / "src/allin1_sdk"; package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 1\n")
    (tmp_path / "test_actual.py").write_text("import pytest\nfrom allin1_sdk import value\ndef test_actual():\n    assert value == 1\n" + ("    pytest.skip('required check absent')\n" if skip else ""))
    (tmp_path / "pyproject.toml").write_text('[tool.coverage.run]\nbranch = true\n[tool.coverage.report]\nfail_under = 80\n')
    executable = candidate.external_executable(Path(sys.executable))
    identity = tmp_path / "identity.json"
    candidate.write_new(identity, {"schema_version": 1, "kind": "sdk_build_identity", "build_id": "disposable", "source": source,
        "toolchain_files": {"python": candidate.tool_identity(executable)}})
    command = [sys.executable, "-m", "pytest", "--cov=allin1_sdk", "--cov-report=term-missing"]
    if skip:
        with pytest.raises(subprocess.CalledProcessError): candidate.run_gate(tmp_path, identity, "python", command)
    else: candidate.run_gate(tmp_path, identity, "python", command)
    record = json.loads((tmp_path / "gate-python.json").read_text())
    assert record["schema_version"] == 2
    assert record["status"] == ("FAIL" if skip else "PASS")
    if not skip:
        assert record["evidence"]["tests"] == 1
        assert record["evidence"]["coverage_percent"] == 100
        monkeypatch.setattr(candidate, "REQUIRED_GATES", {"python"})
        assert candidate.gate_evidence(identity, root=tmp_path)["python"]["status"] == "PASS"
        (tmp_path / "gate-python.xml").write_text("<testsuites/>")
        with pytest.raises(ValueError): candidate.gate_evidence(identity, root=tmp_path)
    else:
        assert "skipped" in record["evidence_error"]
