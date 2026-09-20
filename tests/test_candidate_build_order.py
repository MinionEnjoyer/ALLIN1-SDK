"""Candidate integration tests must use freshly built, immutable payloads."""
from pathlib import Path


def test_python_gate_runs_against_fresh_frozen_payload_before_sealing():
    script = (Path(__file__).resolve().parents[1] / "scripts/build_tauri_desktop.ps1").read_text()
    assert script.index("$python -m PyInstaller") < script.index("$env:ALLIN1_FROZEN_SIDECAR = $sidecar")
    assert script.index("$testedSidecarHash =") < script.index("$pythonGateCommand =")
    assert script.index("$pythonGateCommand =") < script.index("if ($SidecarOnly)")
    assert script.index("$pythonGateCommand =") < script.index("seal --identity")
    assert "Frozen candidate bytes changed during the Python gate." in script
    assert "$env:ALLIN1_FROZEN_SIDECAR = $previousFrozenSidecar" in script
    assert "$env:ALLIN1_FROZEN_RESOURCES = $previousFrozenResources" in script
    assert "from allin1_sdk.release_paths import filesystem_path" in script
    assert "'scripts\\smoke_desktop_sidecar.py') $smokeSidecar.Trim()" in script


def test_hosted_workflow_runs_recorded_candidate_gates_once_before_artifact_upload():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/tauri-desktop.yml").read_text()
    script = (root / "scripts/build_tauri_desktop.ps1").read_text()
    candidate = workflow.index("- name: Build, identify, extract and smoke-test")
    assert workflow.index("- name: Verify pinned Blender archive") < candidate
    assert candidate < workflow.index("- name: Validate Rust broker")
    assert candidate < workflow.index("- name: Upload unsigned candidates")
    for gate in ("python", "react", "rust", "frontend", "native-rpf"):
        assert script.count("--name " + gate + " --cwd") == 1
        assert script.index("--name " + gate + " --cwd") < script.index("seal --identity")
    assert "python -m pytest --cov" not in workflow
    assert "pnpm --dir desktop test" not in workflow
    assert "pnpm --dir desktop build" not in workflow
    assert "gate-*.json" in workflow and "gate-*.xml" in workflow
