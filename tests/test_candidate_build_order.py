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
