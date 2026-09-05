"""Candidate identities and generated package evidence fail closed."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.desktop_candidate import (
    PLUGIN_NAMES, REQUIRED_GATES, check_source, compare_payload, execution_command,
    gate_evidence, nsis_members, run_gate, tool_identity, write_new, write_portable,
)


def _fixture_tool_anchors(executable=Path(sys.executable)):
    anchor = tool_identity(executable.resolve())
    return {name: anchor for name in ("python", "pnpm", "cargo", "dotnet")}


def test_distributable_tool_identity_omits_paths_but_binds_exact_location_and_bytes(tmp_path):
    selected = tmp_path / "selected compiler.exe"
    copied = tmp_path / "another compiler.exe"
    selected.write_bytes(b"same synthetic tool bytes")
    copied.write_bytes(selected.read_bytes())
    identity = tool_identity(selected)
    encoded = json.dumps(identity)
    assert set(identity) == {"sha256", "path_binding_sha256"}
    assert str(tmp_path) not in encoded and selected.name not in encoded
    assert identity["sha256"] == tool_identity(copied)["sha256"]
    assert identity["path_binding_sha256"] != tool_identity(copied)["path_binding_sha256"]
    selected.write_bytes(b"changed tool bytes")
    assert identity["sha256"] != tool_identity(selected)["sha256"]


@pytest.mark.parametrize("stale_anchor", ["other-location", "legacy-readable-path"])
def test_candidate_gate_blocks_wrong_location_or_old_path_bearing_identity_before_execution(tmp_path, monkeypatch, stale_anchor):
    from scripts import desktop_candidate as candidate
    executable = candidate.external_executable(Path(sys.executable))
    anchor = tool_identity(executable)
    if stale_anchor == "other-location":
        anchor["path_binding_sha256"] = "0" * 64
    else:
        anchor = {"path": str(executable), "sha256": anchor["sha256"]}
    identity = {"toolchain_files": {"python": anchor}}
    monkeypatch.setattr(candidate, "check_source", lambda *_: identity)
    monkeypatch.setattr(candidate.subprocess, "run", lambda *_, **__: pytest.fail("Stale tool selection must not execute"))
    with pytest.raises(ValueError, match="differs from its prepared identity"):
        run_gate(tmp_path, tmp_path / "identity.json", "python", [sys.executable, "-m", "pytest"])
    assert not (tmp_path / "gate-python.log").exists()


def test_candidate_cli_resolves_build_helpers_from_an_unrelated_directory(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/desktop_candidate.py"
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert "prepare" in result.stdout and "seal" in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["../escape", "C:\\outside", "\\\\server\\share", "a\\..\\b", "a:stream", "con.txt", "a\\\\b"])
def test_nsis_inventory_rejects_unsafe_extraction_names(name):
    with pytest.raises(ValueError):
        nsis_members(f"----------\nPath = {name}\nSize = 1\n")


def test_nsis_inventory_rejects_duplicate_destinations_and_missing_header():
    for listing in ["Path = file", "----------\n", "----------\nPath = x\\y\nPath = X\\Y\n"]:
        with pytest.raises(ValueError):
            nsis_members(listing)
    assert nsis_members("----------\nPath = a\\b\nSize = 1") == ["a/b"]


@pytest.mark.parametrize("mutation", ["missing", "stale", "different_bytes", "missing_plugin"])
def test_candidate_compares_actual_packaged_bytes_and_complete_inventory(tmp_path, mutation):
    path = tmp_path / "sdk.exe"
    path.write_bytes(b"tested build")
    expected = {"sdk.exe": hashlib.sha256(b"tested build").hexdigest()}
    actual = {"sdk.exe": path, "uninstall.exe": path, **{f"$PLUGINSDIR/{name}": path for name in PLUGIN_NAMES}}
    assert compare_payload(expected, actual)
    if mutation == "missing":
        del actual["sdk.exe"]
    elif mutation == "stale":
        actual["stale.dll"] = path
    elif mutation == "different_bytes":
        path.write_bytes(b"another build")
    else:
        del actual["$PLUGINSDIR/System.dll"]
    with pytest.raises(ValueError):
        compare_payload(expected, actual)


def test_candidate_write_is_exclusive_and_source_is_pinned(tmp_path, monkeypatch):
    source = {"sdk_commit": "a" * 40, "dirty": True, "source_tree_sha256": "b" * 64}
    monkeypatch.setattr("scripts.desktop_candidate.source_identity", lambda _: source)
    identity = {"schema_version": 1, "kind": "sdk_build_identity", "source": dict(source)}
    path = tmp_path / "identity.json"
    write_new(path, identity)
    assert check_source(tmp_path, path) == identity
    with pytest.raises(FileExistsError):
        write_new(path, identity)
    source["source_tree_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="Source changed"):
        check_source(tmp_path, path)


def test_portable_candidate_has_exact_deterministic_safe_inventory(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    expected, actual = {}, {}
    for name, content in {
        "allin1-sdk-desktop.exe": b"MZ owned shell",
        "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe": b"MZ owned sidecar",
        "docs/help.txt": b"portable documentation",
    }.items():
        source = sources / name.replace("/", "-")
        source.write_bytes(content)
        expected[name] = hashlib.sha256(content).hexdigest()
        actual[name] = source
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    receipt = write_portable(first, expected, actual)
    write_portable(second, expected, actual)
    assert first.read_bytes() == second.read_bytes()
    assert receipt["members"] == len(expected)
    assert receipt["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    actual["docs/help.txt"].write_bytes(b"changed after review")
    with pytest.raises(ValueError, match="changed"):
        write_portable(tmp_path / "rejected.zip", expected, actual)


def test_portable_distribution_metadata_binds_real_payload_and_exact_inventory(tmp_path):
    import zipfile
    identity = {"schema_version": 1, "kind": "sdk_build_identity", "sdk_version": "0.6.4", "build_id": "unit-fixture"}
    payloads = {
        "build-identity.json": json.dumps(identity).encode(),
        "allin1-sdk-desktop.exe": b"MZ synthetic shell, not executable",
        "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe": b"MZ synthetic sidecar, not executable",
    }
    payloads["resource-checksums.json"] = json.dumps({"build-identity.json": hashlib.sha256(payloads["build-identity.json"]).hexdigest()}).encode()
    actual, expected = {}, {}
    for name, content in payloads.items():
        source = tmp_path / name; source.parent.mkdir(parents=True, exist_ok=True); source.write_bytes(content)
        actual[name] = source; expected[name] = hashlib.sha256(content).hexdigest()
    output = tmp_path / "portable.zip"
    write_portable(output, expected, actual, identity=identity)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("checksums.json"))
        assert set(manifest) == set(archive.namelist()) - {"checksums.json"}
        assert all(hashlib.sha256(archive.read(name)).hexdigest() == sha for name, sha in manifest.items())
        release = json.loads(archive.read("release.json"))
        assert release["version"] == identity["sdk_version"]
        assert release["build_id"] == identity["build_id"]
        assert release["build_identity_sha256"] == expected["build-identity.json"]
        assert release["entrypoint"] == "allin1-sdk-desktop.exe"
    for mutation in ("different_identity", "alias", "missing_companion"):
        plan, files, candidate_identity = dict(expected), dict(actual), dict(identity)
        if mutation == "different_identity": candidate_identity["build_id"] = "wrong-build"
        elif mutation == "alias": plan["RELEASE.JSON"] = expected["build-identity.json"]; files["RELEASE.JSON"] = actual["build-identity.json"]
        else: del plan["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"]; del files["sidecar/ALLIN1-SDK-Desktop-Sidecar.exe"]
        rejected = tmp_path / (mutation + ".zip")
        with pytest.raises(ValueError): write_portable(rejected, plan, files, identity=candidate_identity)
        assert not rejected.exists()


def test_stale_python_distribution_metadata_is_a_build_failure(monkeypatch):
    from scripts.desktop_candidate import require_python_metadata
    monkeypatch.setattr("scripts.desktop_candidate.importlib.metadata.version", lambda _: "0.6.3")
    with pytest.raises(ValueError, match="metadata is stale"):
        require_python_metadata("0.6.4")
    require_python_metadata("0.6.3")


def test_embedded_build_identity_never_falls_back_to_checkout(tmp_path, monkeypatch):
    from allin1_sdk import __version__
    from allin1_sdk import release_identity
    monkeypatch.setattr(release_identity, "__file__", str(tmp_path / "release_identity.py"))
    assert release_identity.embedded_build_identity() is None
    path = tmp_path / "_build_identity.json"
    value = {"schema_version": 1, "kind": "sdk_build_identity", "sdk_version": __version__}
    path.write_text(json.dumps(value))
    assert release_identity.embedded_build_identity() == value
    value["sdk_version"] = "0.0.1"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="version-mismatched"):
        release_identity.embedded_build_identity()


def test_pinned_nsis_template_and_candidate_pipeline_are_configured():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_bytes())
    nsis = config["bundle"]["windows"]["nsis"]
    assert nsis["template"] == "windows/installer.nsi"
    assert nsis["installerHooks"] == "windows/path-guards.nsh"
    assert json.loads((root / "desktop/package.json").read_bytes())["devDependencies"]["@tauri-apps/cli"] == "2.11.4"
    script = (root / "scripts/build_tauri_desktop.ps1").read_text()
    assert 'prepare --pnpm' in script and 'seal --identity' in script
    assert all(f'--name {name}' in script for name in REQUIRED_GATES)
    assert script.count('--command-json') == len(REQUIRED_GATES)
    assert '7zip-26.03\\unpacked\\7z.exe' in script
    assert 'Get-ChildItem' not in script  # Do not rehash unrelated/stale installers.


def test_candidate_gate_executes_and_binds_complete_logs(tmp_path, monkeypatch):
    # This test isolates log/identity persistence. Framework validation and
    # canonical commands have separate real-process/adversarial tests.
    monkeypatch.setattr("scripts.candidate_test_evidence.instrument", lambda _name, command, *_: command)
    monkeypatch.setattr("scripts.candidate_test_evidence.collect", lambda *_, **__: {"schema_version": 1, "tests": 1})
    source = {
        "schema_version": 1, "sdk_commit": "a" * 40, "dirty": True,
        "versions": {"test": "0.6.4"}, "versions_agree": True,
        "source_tree_sha256": "b" * 64, "input_count": 1, "submodules": {},
    }
    monkeypatch.setattr("scripts.desktop_candidate.source_identity", lambda _: dict(source))
    identity = {
        "schema_version": 1, "kind": "sdk_build_identity", "build_id": "candidate",
        "source": dict(source),
        "toolchain_files": _fixture_tool_anchors(),
    }
    identity_path = tmp_path / "_build_identity.json"
    write_new(identity_path, identity)
    for name in REQUIRED_GATES:
        run_gate(
            tmp_path, identity_path, name,
            [sys.executable, "-c", f"print('passed {name}')"],
        )
    evidence = gate_evidence(identity_path)
    assert set(evidence) == REQUIRED_GATES
    assert all(item["status"] == "PASS" for item in evidence.values())
    (tmp_path / "gate-react.log").write_text("unrelated stale output")
    with pytest.raises(ValueError, match="changed after execution"):
        gate_evidence(identity_path)


def test_candidate_react_gate_refuses_skipped_tests(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.candidate_test_evidence.instrument", lambda _name, command, *_: command)
    source = {
        "schema_version": 1, "sdk_commit": "a" * 40, "dirty": True,
        "versions": {"test": "0.6.4"}, "versions_agree": True,
        "source_tree_sha256": "b" * 64, "input_count": 1, "submodules": {},
    }
    monkeypatch.setattr("scripts.desktop_candidate.source_identity", lambda _: dict(source))
    identity_path = tmp_path / "_build_identity.json"
    write_new(identity_path, {"schema_version": 1, "kind": "sdk_build_identity", "build_id": "candidate", "source": source, "toolchain_files": _fixture_tool_anchors()})
    with pytest.raises(subprocess.CalledProcessError):
        run_gate(tmp_path, identity_path, "react", [sys.executable, "-c", "print('1 skipped')"])
    evidence = json.loads((tmp_path / "gate-react.json").read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["skipped_reported"] is True


def test_candidate_python_gate_refuses_coverage_failure_with_zero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.candidate_test_evidence.instrument", lambda _name, command, *_: command)
    source = {
        "schema_version": 1, "sdk_commit": "a" * 40, "dirty": True,
        "versions": {"test": "0.6.4"}, "versions_agree": True,
        "source_tree_sha256": "b" * 64, "input_count": 1, "submodules": {},
    }
    monkeypatch.setattr("scripts.desktop_candidate.source_identity", lambda _: dict(source))
    identity_path = tmp_path / "_build_identity.json"
    write_new(identity_path, {"schema_version": 1, "kind": "sdk_build_identity", "build_id": "candidate", "source": source, "toolchain_files": _fixture_tool_anchors()})
    with pytest.raises(subprocess.CalledProcessError):
        run_gate(tmp_path, identity_path, "python", [
            sys.executable, "-c", "print('FAIL Required test coverage of 80% not reached')",
        ])
    evidence = json.loads((tmp_path / "gate-python.json").read_text())
    assert evidence["status"] == "FAIL"
    assert evidence["coverage_failed"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch-launch regression")
def test_candidate_gate_executes_and_hashes_windows_batch_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.candidate_test_evidence.instrument", lambda _name, command, *_: command)
    monkeypatch.setattr("scripts.candidate_test_evidence.collect", lambda *_, **__: {"schema_version": 1, "tests": 1})
    source = {
        "schema_version": 1, "sdk_commit": "a" * 40, "dirty": True,
        "versions": {"test": "0.6.4"}, "versions_agree": True,
        "source_tree_sha256": "b" * 64, "input_count": 1, "submodules": {},
    }
    monkeypatch.setattr("scripts.desktop_candidate.source_identity", lambda _: dict(source))
    identity_path = tmp_path / "_build_identity.json"
    wrapper = tmp_path / "candidate gate.cmd"
    wrapper.write_text("@echo off\r\necho wrapper passed\r\n", encoding="utf-8")
    write_new(identity_path, {"schema_version": 1, "kind": "sdk_build_identity", "build_id": "candidate", "source": source, "toolchain_files": _fixture_tool_anchors(wrapper)})
    invocation, launcher = execution_command([str(wrapper)], wrapper)
    assert launcher is not None and invocation[-1] == str(wrapper)
    record_path = run_gate(tmp_path, identity_path, "frontend", [str(wrapper)])
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "PASS"
    assert record["executable"]["sha256"] == hashlib.sha256(wrapper.read_bytes()).hexdigest()
    assert record["launcher"]["path"].casefold().endswith("cmd.exe")


@pytest.mark.parametrize("mutation", ["binary", "manifest", "stale"])
def test_runtime_resources_are_bound_to_frozen_sidecar(tmp_path, mutation):
    from allin1_sdk.release_identity import verify_runtime_resources
    root = tmp_path / "installed"
    (root / "tools").mkdir(parents=True)
    dll = root / "tools/companion.dll"
    dll.write_bytes(b"tested")
    manifest = {"tools/companion.dll": hashlib.sha256(b"tested").hexdigest()}
    trusted = tmp_path / "frozen-manifest.json"
    trusted.write_text(json.dumps(manifest))
    actual_manifest = root / "resource-checksums.json"
    actual_manifest.write_bytes(trusted.read_bytes())
    user_data = root / "personal-project.txt"
    user_data.write_text("keep")
    verify_runtime_resources(root, trusted)
    if mutation == "binary":
        dll.write_bytes(b"other build")
    elif mutation == "manifest":
        actual_manifest.write_text("{}")
    else:
        (root / "tools/old.dll").write_bytes(b"stale")
    with pytest.raises(ValueError):
        verify_runtime_resources(root, trusted)
    assert user_data.read_text() == "keep"
