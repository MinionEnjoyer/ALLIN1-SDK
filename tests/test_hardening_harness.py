"""Focused adversarial checks for the SDK's off-game hardening harness."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "hardening_harness", Path(__file__).resolve().parents[1] / "scripts" / "hardening_harness.py",
)
harness = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(harness)


def command(output: Path, name: str = "proof", code: int = 0) -> dict:
    log = output / f"{name}.log"
    log.write_text("synthetic\n", encoding="utf-8")
    return {"argv": [name], "cwd": str(output), "started_ms": 0, "ended_ms": 4_102_444_800_000,
            "returncode": code, "log": str(log), "log_sha256": harness.sha256(log)}


def options(**values):
    defaults = {"run_id": "fresh-run", "real_tools": False, "python": "python", "pnpm": "pnpm", "cargo": "cargo",
                "dotnet": "dotnet", "cmake": "cmake", "ctest": "ctest", "blender": None}
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_fresh_output_is_exclusive_and_records_off_game_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "source_snapshot", lambda _: {"source_tree_sha256": "same", "dirty": True})
    monkeypatch.setattr(harness, "safe_tool", lambda _: None)
    monkeypatch.setattr(harness, "profile", lambda *_args, **_kwargs: ({}, {"proof": {"status": "PASS"}}))
    code, output, summary = harness.run(options())
    assert code == 0 and output == tmp_path / "build" / "hardening" / "fresh-run"
    assert summary["unrun"]["GTA"] == "NOT TESTED"
    with pytest.raises(FileExistsError):
        harness.run(options())


def test_source_drift_fails_even_if_layers_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    snapshots = iter(({"source_tree_sha256": "one"}, {"source_tree_sha256": "two"}))
    monkeypatch.setattr(harness, "source_snapshot", lambda _: next(snapshots))
    monkeypatch.setattr(harness, "safe_tool", lambda _: None)
    monkeypatch.setattr(harness, "profile", lambda *_args, **_kwargs: ({}, {"proof": {"status": "PASS"}}))
    code, _output, summary = harness.run(options(run_id="drift-run"))
    assert code == 1 and summary["status"] == "FAIL"
    assert summary["layers"]["source-integrity"]["status"] == "FAIL"


def test_tool_drift_cannot_reuse_otherwise_passing_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "source_snapshot", lambda _: {"source_tree_sha256": "stable"})
    monkeypatch.setattr(harness, "safe_tool", lambda value: value)
    identity_calls = {}

    def identity(path):
        if path is None:
            return None
        identity_calls[path] = identity_calls.get(path, 0) + 1
        return {"path": path, "sha256": str(identity_calls[path])}

    monkeypatch.setattr(harness, "tool_identity", identity)
    monkeypatch.setattr(harness, "profile", lambda *_args, **_kwargs: ({}, {"proof": {"status": "PASS"}}))
    code, _output, summary = harness.run(options(run_id="tool-drift"))
    assert code == 1 and summary["layers"]["tool-integrity"]["status"] == "FAIL"


def test_timeout_targets_only_the_created_windows_process_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "IS_WINDOWS", True)
    calls = []

    class OwnedProcess:
        pid = 32123
        waits = 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise harness.subprocess.TimeoutExpired("owned", timeout)
            return 0  # Even a zero exit after timeout must remain a timeout.

    monkeypatch.setattr(harness.subprocess, "Popen", lambda *args, **kwargs: OwnedProcess())
    monkeypatch.setattr(harness.subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    result = harness.run_command(["owned"], tmp_path, tmp_path, "timed", {})
    assert result["timed_out"] is True
    assert calls == [["taskkill", "/PID", "32123", "/T", "/F"]]
    _, evidence = harness.invoke("timed", ["owned"], tmp_path, tmp_path, {},
                                 execute=lambda *_args: result)
    assert evidence["status"] == "FAIL"


def test_missing_prerequisites_are_incomplete_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    commands, layers = harness.profile(options(), {key: None for key in ("python", "pnpm", "cargo", "dotnet", "cmake", "ctest", "blender")}, tmp_path)
    assert commands == {}
    assert all(layers[name]["status"] == "INCOMPLETE" for name in ("python", "react", "rust-native", "retirement"))


def test_junit_empty_failed_and_skipped_evidence_never_passes(tmp_path):
    report = tmp_path / "python.xml"
    report.write_text("<testsuites/>", encoding="utf-8")
    with pytest.raises(ValueError, match="no test cases"):
        harness.validate_junit(report, command(tmp_path), "Python")
    report.write_text('<testsuite tests="1"><testcase name="x"><failure/></testcase></testsuite>', encoding="utf-8")
    failed = harness.validate_junit(report, command(tmp_path), "Python")
    assert failed["status"] == "FAIL" and failed["tests"] == 1 and failed["passed"] == 0
    report.write_text('<testsuite tests="1"><testcase name="x"><skipped message="fixture"/></testcase></testsuite>', encoding="utf-8")
    assert harness.validate_junit(report, command(tmp_path), "Python")["status"] == "INCOMPLETE"


@pytest.mark.parametrize("payload", [
    {}, {"meta": {"branch_coverage": True}, "totals": {"percent_covered": 79, "num_statements": 1}, "files": {}},
    {"meta": {"branch_coverage": False}, "totals": {"percent_covered": 100, "num_statements": 1}, "files": {}},
])
def test_coverage_requires_unchanged_threshold_branch_and_source_files(tmp_path, payload):
    report = tmp_path / "coverage.json"; report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        harness.validate_coverage(report, command(tmp_path), tmp_path)


def test_coverage_reconciles_every_non_omitted_source_file(tmp_path):
    package = tmp_path / "src" / "allin1_sdk"; package.mkdir(parents=True)
    first = package / "first.py"
    first.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[tool.coverage.run]\nsource=["allin1_sdk"]\nbranch=true\n[tool.coverage.report]\nfail_under=80\nomit=["src/allin1_sdk/updater_host.py","src/allin1_sdk/detector.py","src/allin1_sdk/mods.py"]\n', encoding="utf-8")
    payload = {"meta": {"branch_coverage": True}, "totals": {"percent_covered": 100, "num_statements": 1, "covered_lines": 1, "missing_lines": 0, "num_branches": 0, "covered_branches": 0, "missing_branches": 0},
               "files": {str(first): {"summary": {"num_statements": 1, "covered_lines": 1, "missing_lines": 0, "num_branches": 0, "covered_branches": 0, "missing_branches": 0}}}}
    report = tmp_path / "coverage.json"; report.write_text(json.dumps(payload), encoding="utf-8")
    assert harness.validate_coverage(report, command(tmp_path), tmp_path)["source_files"] == 1
    (package / "second.py").write_text("value = 2\n", encoding="utf-8")
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory"):
        harness.validate_coverage(report, command(tmp_path), tmp_path)


def test_react_requires_mapped_happy_path_not_success_flag(tmp_path):
    desktop = tmp_path / "desktop"; desktop.mkdir()
    test = desktop / "module.test.tsx"; test.write_text("fixture", encoding="utf-8")
    inventory = desktop / "module-happy-paths.json"
    inventory.write_text(json.dumps({"schema_version": 1, "modules": [{"module": "shell", "test_file": "module.test.tsx", "test_title": "works"}]}), encoding="utf-8")
    report = tmp_path / "vitest.json"
    report.write_text(json.dumps({"success": True, "startTime": 1, "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0,
        "testResults": [{"name": str(test), "status": "passed", "assertionResults": [{"fullName": "other", "title": "wrong", "status": "passed", "failureMessages": []}]}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="mapped happy path"):
        harness.validate_react(report, inventory, desktop, command(tmp_path))


def test_react_skips_are_incomplete(tmp_path):
    desktop = tmp_path / "desktop"; desktop.mkdir(); test = desktop / "module.test.tsx"; test.write_text("fixture")
    inventory = desktop / "module-happy-paths.json"; inventory.write_text('{"schema_version":1,"modules":[{"module":"native","test_file":"module.test.tsx","test_title":"needs fixture"}]}')
    report = tmp_path / "vitest.json"
    payload = {"success": True, "startTime": 1, "numTotalTests": 1, "numPassedTests": 0, "numFailedTests": 0, "numPendingTests": 1, "numTodoTests": 0,
               "testResults": [{"name": str(test), "status": "passed", "assertionResults": [{"fullName": "native fixture", "title": "needs fixture", "status": "skipped", "failureMessages": []}]}]}
    report.write_text(json.dumps(payload))
    assert harness.validate_react(report, inventory, desktop, command(tmp_path))["status"] == "INCOMPLETE"
    payload["testResults"][0]["assertionResults"][0]["title"] = "wrong mapping"
    report.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="mapped happy path"):
        harness.validate_react(report, inventory, desktop, command(tmp_path))


def test_react_identity_is_file_scoped_but_not_ambiguous_within_a_file(tmp_path):
    desktop = tmp_path / "desktop"; desktop.mkdir()
    first, second = desktop / "first.test.tsx", desktop / "second.test.tsx"
    first.write_text("fixture"); second.write_text("fixture")
    inventory = desktop / "module-happy-paths.json"
    inventory.write_text(json.dumps({"schema_version": 1, "modules": [
        {"module": "first", "test_file": "first.test.tsx", "test_title": "same name"},
    ]}))
    report = tmp_path / "vitest.json"
    payload = {"success": True, "startTime": 1, "numTotalTests": 2, "numPassedTests": 2,
               "numFailedTests": 0, "numPendingTests": 0, "numTodoTests": 0, "testResults": [
        {"name": str(first), "status": "passed", "assertionResults": [
            {"fullName": "same name", "title": "same name", "status": "passed", "failureMessages": []}]},
        {"name": str(second), "status": "passed", "assertionResults": [
            {"fullName": "same name", "title": "same name", "status": "passed", "failureMessages": []}]},
    ]}
    report.write_text(json.dumps(payload))
    assert harness.validate_react(report, inventory, desktop, command(tmp_path))["status"] == "PASS"
    payload["numTotalTests"] = 3; payload["numPassedTests"] = 3
    payload["testResults"][0]["assertionResults"].append(
        {"fullName": "same name", "title": "same name", "status": "passed", "failureMessages": []})
    report.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="ambiguous identity"):
        harness.validate_react(report, inventory, desktop, command(tmp_path))


def test_rust_requires_named_reconciled_results_and_binary(tmp_path):
    desktop = tmp_path / "desktop"; binary = desktop / "src-tauri" / "target" / "debug" / "tests.exe"; binary.parent.mkdir(parents=True); binary.write_bytes(b"test")
    log = tmp_path / "rust-native.log"
    artifact = {"reason": "compiler-artifact", "profile": {"test": True}, "executable": str(binary)}
    log.write_text(json.dumps(artifact) + "\ntest sdk::works ... ok\ntest result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n")
    assert harness.validate_rust(log, desktop)["test_names"] == ["sdk::works"]
    log.write_text(json.dumps(artifact) + "\ntest result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;\n")
    with pytest.raises(ValueError, match="named"):
        harness.validate_rust(log, desktop)


def test_rpf_runner_requires_unique_positive_custom_runner_result(tmp_path):
    report = tmp_path / "csharp.log"
    report.write_text("Exact native member resolution: 17 checks passed (no game required).\n", encoding="utf-8")
    assert harness.validate_rpf_runner(report)["tests"] == 17
    report.write_text("Exact native member resolution: 0 checks passed (no game required).\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique positive"):
        harness.validate_rpf_runner(report)


def test_human_audit_includes_commands_layers_and_untested_scope(tmp_path):
    summary = {"status": "INCOMPLETE", "run_id": "audit-run", "commands": {"python": {"argv": ["python", "-m", "pytest"], "returncode": 0}},
               "layers": {"python": {"status": "FAIL", "reason": "pytest exited 1", "tests": 3, "passed": 2,
                                      "failed": [{"classname": "tests.test_sdk", "name": "test_failure"}]},
                          "react": {"status": "INCOMPLETE", "tests": {"status": "INCOMPLETE", "skipped": [{"classname": "native", "name": "render", "reason": "fixture unavailable"}]}}},
               "unrun": {"GTA": "NOT TESTED"}}
    harness.write_audit(tmp_path / "AUDIT.md", summary)
    text = (tmp_path / "AUDIT.md").read_text(encoding="utf-8")
    assert "native::render: fixture unavailable" in text and "tests.test_sdk::test_failure" in text
    assert "2/3 passed" in text and "GTA" in text and "python -m pytest" in text


def test_zero_exit_with_missing_mandatory_evidence_is_failure(tmp_path):
    executed, result = harness.invoke("proof", ["proof"], tmp_path, tmp_path, {},
        lambda current: harness.validate_junit(tmp_path / "missing.xml", current, "Proof"),
        lambda *_args: command(tmp_path, "proof", code=0))
    assert executed["returncode"] == 0 and result["status"] == "FAIL"


def test_nonzero_command_retains_valid_diagnostic_evidence(tmp_path):
    executed, result = harness.invoke(
        "python", ["python"], tmp_path, tmp_path, {},
        lambda _command: {"status": "FAIL", "tests": 12, "passed": 9,
                          "coverage_percent": 81.68, "failed": [{"name": "case"}]},
        lambda *_args: command(tmp_path, "python", code=1),
    )
    assert executed["returncode"] == 1
    assert result == {"status": "FAIL", "reason": "python exited 1", "tests": 12,
                      "passed": 9, "coverage_percent": 81.68, "failed": [{"name": "case"}]}


def test_source_mode_scrubs_native_optins(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    (tmp_path / "desktop").mkdir()
    seen = {}
    tools = {key: f"{key}.exe" for key in ("python", "pnpm", "cargo", "dotnet", "cmake", "ctest")}
    tools["blender"] = None
    def execute(argv, cwd, output, name, env):
        seen[name] = dict(env)
        return command(output, name, code=1)
    monkeypatch.setenv("ALLIN1_NATIVE_RUNTIME_TEST", "1")
    monkeypatch.setenv("ALLIN1_NATIVE_RPF_TEST", "1")
    for name in harness.PYTHON_COVERAGE_PARENT_ENV:
        monkeypatch.setenv(name, "inherited coverage startup/config")
    for name in harness.PRIVATE_TEST_ENV:
        monkeypatch.setenv(name, "must-not-be-inherited")
    harness.profile(options(), tools, tmp_path, execute)
    assert "ALLIN1_NATIVE_RUNTIME_TEST" not in seen["react"]
    assert "ALLIN1_NATIVE_RPF_TEST" not in seen["react"]
    assert seen["python"]["COVERAGE_FILE"] == str(tmp_path / ".coverage")
    assert all(name not in seen["python"] for name in harness.PYTHON_COVERAGE_PARENT_ENV)
    assert all(name not in seen["python"] and name not in seen["react"]
               for name in harness.PRIVATE_TEST_ENV)
    # Prepared tools permit only synthetic native fixtures, not private retail
    # paths or packaged executables inherited from another test session.
    harness.profile(options(real_tools=True), tools, tmp_path, execute)
    assert seen["python"]["ALLIN1_NATIVE_RPF_TEST"] == "1"
    assert all(name not in seen["python"] and name not in seen["react"]
               for name in harness.PRIVATE_TEST_ENV)


def test_cpp_profile_keeps_debug_for_build_and_ctest(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "IS_WINDOWS", True)
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    (tmp_path / "desktop").mkdir(); (tmp_path / "runtime" / "VehicleWorkbenchAxles").mkdir(parents=True)
    (tmp_path / "runtime" / "VehicleWorkbenchAxles" / "CMakeLists.txt").write_text("fixture")
    (tmp_path / "tools" / "RpfPatcher.Tests").mkdir(parents=True)
    (tmp_path / "tools" / "RpfPatcher.Tests" / "RpfPatcher.Tests.csproj").write_text("fixture")
    tools = {key: f"{key}.exe" for key in ("python", "pnpm", "cargo", "dotnet", "cmake", "ctest")}; tools["blender"] = None
    seen = {}
    def execute(argv, cwd, output, name, env):
        seen[name] = argv
        return command(output, name, code=1)
    harness.profile(options(), tools, tmp_path, execute)
    assert "Debug" in seen["cpp-build"] and "Debug" in seen["cpp-ctest"]


def test_real_tools_requires_prepared_blender_if_react_otherwise_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    (tmp_path / "desktop").mkdir()
    tools = {key: f"{key}.exe" for key in ("python", "pnpm", "cargo", "dotnet", "cmake", "ctest")}
    tools["blender"] = None
    def execute(argv, cwd, output, name, env):
        # Prevent validators from running; we only need a successful nested shape.
        return command(output, name, code=1 if name != "typescript-build" else 0)
    _commands, layers = harness.profile(options(real_tools=True), tools, tmp_path, execute)
    assert layers["react"]["status"] == "FAIL"  # no fake success via command exit alone
