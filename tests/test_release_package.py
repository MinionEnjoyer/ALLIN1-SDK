"""Self-contained SDK release packaging contract."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.package_release import (
    _REQUIRED_AUTHORING_RESOURCES,
    _build_id,
    _validate_example_sources,
    package_release,
)


def test_release_package_contains_launcher_contract_and_checksums(tmp_path):
    root = tmp_path / "source"
    app = tmp_path / "app"
    rpf = tmp_path / "rpf"
    output = tmp_path / "output"
    (root / "sdk").mkdir(parents=True)
    (root / "assets").mkdir(parents=True)
    app.mkdir()
    rpf.mkdir()
    (root / "sdk" / "addon.schema.json").write_text("{}")
    (root / "sdk" / "runtime-api-contract.schema.json").write_text("{}")
    (root / "assets" / "ALLIN1_SDK.png").write_bytes(b"png")
    (root / "assets" / "favicon.ico").write_bytes(b"ico")
    for relative in _REQUIRED_AUTHORING_RESOURCES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}" if path.suffix == ".json" else "fixture")
    (root / "docs" / "adr" / "0001-fixture.md").parent.mkdir(
        parents=True, exist_ok=True,
    )
    (root / "docs" / "adr" / "0001-fixture.md").write_text("ADR")
    (root / "examples" / "axle-prefabs" / "five-axle-crane.json").write_text("{}")
    generated = root / "runtime" / "VehicleWorkbenchAxles" / "out"
    generated.mkdir(parents=True)
    (generated / "VehicleWorkbenchAxles.asi").write_bytes(b"MZgenerated")
    (root / "runtime" / "VehicleWorkbenchAxles" / "src" / "generated.dll").write_bytes(
        b"MZgenerated"
    )
    (root / "README.md").write_text("SDK")
    (root / "LICENSE").write_text("GPL")
    (app / "ALLIN1-SDK-Desktop.exe").write_bytes(b"MZapp")
    (app / "allin1-sdk.exe").write_bytes(b"MZcli")
    (app / "ALLIN1-SDK-Agent.exe").write_bytes(b"MZagent")
    (app / "ALLIN1-SDK-Updater.exe").write_bytes(b"MZupdater")
    (app / "_internal").mkdir()
    (app / "_internal" / "python312.dll").write_bytes(b"runtime")
    (rpf / "RpfPatcher.exe").write_bytes(b"MZhelper")
    (app / "tools" / "RpfPatcher").mkdir(parents=True)
    (app / "tools" / "RpfPatcher" / "stale.dll").write_bytes(b"MZold dependency")
    (app / "assets").mkdir()
    (app / "assets" / "removed-resource.txt").write_text("stale")
    (app / "_internal" / "checksums.json").write_text('{"dependency":"metadata"}')

    build_id = "0123456789abcdef0123456789abcdef01234567"
    archive, checksum = package_release(
        root, app, rpf, output, "0.5.0", build_id,
    )

    assert archive.name == "ALLIN1-SDK-0.5.0-win-x64.zip"
    assert checksum.read_text().startswith(hashlib.sha256(archive.read_bytes()).hexdigest())
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert "tools/RpfPatcher/stale.dll" not in names
        assert "assets/removed-resource.txt" not in names
        assert "_internal/checksums.json" in names
        assert {
            "ALLIN1-SDK-Desktop.exe", "allin1-sdk.exe", "ALLIN1-SDK-Agent.exe",
            "ALLIN1-SDK-Updater.exe",
            "release.json", "checksums.json",
            "RELEASE_NOTES.md", "CODE_SIGNING_POLICY.md", "RELEASE_SIGNING.md",
            "desktop/README.md",
            "sdk/addon.schema.json", "sdk/runtime-api-contract.schema.json",
            "tools/RpfPatcher/RpfPatcher.exe",
            "assets/ALLIN1_SDK.png", "assets/favicon.ico",
            "assets/axle-prefabs.json", "assets/visual-tyre-packages.json",
            "docs/axle-prefabs.md", "docs/oiv-story-packages.md",
            "docs/adr/0001-fixture.md",
            "examples/axle-prefabs/three-axle-bus.json",
            "examples/axle-prefabs/five-axle-crane.json",
            "examples/oiv-axle-bundles/vehicle-only-request.template.json",
            "runtime/VehicleWorkbenchAxles/CMakeLists.txt",
            "runtime/VehicleWorkbenchAxles/README.md",
            "runtime/VehicleWorkbenchAxles/include/vehicle_workbench_axles/types.hpp",
            "runtime/VehicleWorkbenchAxles/include/vehicle_workbench_axles/runtime_settings_document.hpp",
            "runtime/VehicleWorkbenchAxles/profiles/compatibility.json",
            "runtime/VehicleWorkbenchAxles/schemas/axle-config.schema.json",
            "runtime/VehicleWorkbenchAxles/src/runtime.cpp",
            "runtime/VehicleWorkbenchAxles/src/runtime_settings_document.cpp",
            "runtime/VehicleWorkbenchAxles/tests/core_tests.cpp",
            "runtime/VehicleWorkbenchAxles/tools/config_validator.cpp",
            "runtime/VehicleWorkbenchAxles/tools/settings_editor.cpp",
        } <= names
        assert "runtime/VehicleWorkbenchAxles/out/VehicleWorkbenchAxles.asi" not in names
        assert "runtime/VehicleWorkbenchAxles/src/generated.dll" not in names
        metadata = json.loads(package.read("release.json"))
        checksums = json.loads(package.read("checksums.json"))
        assert metadata == {
            "build_id": build_id,
            "entrypoint": "ALLIN1-SDK-Desktop.exe", "platform": "win-x64",
            "cli_entrypoint": "allin1-sdk.exe",
            "agent_entrypoint": "ALLIN1-SDK-Agent.exe",
            "updater_entrypoint": "ALLIN1-SDK-Updater.exe",
            "product": "ALLIN1-SDK", "version": "0.5.0",
        }
        assert set(checksums) == names - {"checksums.json"}
        assert all(
            hashlib.sha256(package.read(name)).hexdigest() == digest
            for name, digest in checksums.items()
        )
    assert (app / "tools/RpfPatcher/stale.dll").read_bytes() == b"MZold dependency"
    original_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError, match="never overwrite"):
        package_release(root, app, rpf, output, "0.5.0", build_id)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == original_digest


@pytest.mark.parametrize("value", ("", "contains spaces", "../escape", "x" * 129))
def test_release_build_id_rejects_ambiguous_or_unsafe_values(value):
    with pytest.raises(argparse.ArgumentTypeError, match="invalid release build ID"):
        _build_id(value)


def test_release_workflow_binds_package_identity_to_github_commit():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci-release.yml"
    ).read_text(encoding="utf-8")

    root = Path(__file__).resolve().parents[1]
    tauri = (root / ".github/workflows/tauri-desktop.yml").read_text(encoding="utf-8")
    builder = (root / "scripts/build_tauri_desktop.ps1").read_text(encoding="utf-8")
    assert '-BuildId "$env:GITHUB_SHA" -Unsigned' in workflow
    assert "./scripts/build_tauri_desktop.ps1" in tauri
    assert "prepare --pnpm" in builder and "check --identity $candidateIdentity" in builder
    assert "seal --identity $candidateIdentity" in builder
    assert "contents: write" not in tauri and "gh release create" not in tauri


def test_native_candidate_receipt_does_not_claim_a_signature_or_live_acceptance():
    builder = (Path(__file__).resolve().parents[1] / "scripts/build_native_asi.ps1").read_text(encoding="utf-8")
    assert "unsigned = [bool]$Unsigned" in builder
    assert "authenticode_certificate_present = $false" in builder
    assert "authenticode_certificate_present = $true" not in builder
    assert "game_acceptance = 'not-tested'" in builder and "supported = $false" in builder


def test_native_candidate_hashes_the_actual_axle_settings_editor_in_both_receipts():
    builder = (Path(__file__).resolve().parents[1] / "scripts/build_native_asi.ps1").read_text(encoding="utf-8")
    assert "VehicleWorkbenchAxles.Settings.exe" in builder
    assert "$settingsHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $settingsEditor)" in builder
    assert "sha256 = $settingsHash" in builder
    assert "settings_editor_sha256 = $settingsHash" in builder


def test_native_unit_ci_does_not_require_stale_installer_staging():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/tauri-desktop.yml").read_text(encoding="utf-8")
    unit_step = workflow.split("- name: Validate Rust broker")[1].split("- name:")[0]
    assert 'TAURI_CONFIG:' in unit_step and '"resources":[]' in unit_step
    assert "cargo test --locked" in unit_step and "cargo check --locked" in unit_step
    packaging_step = workflow.split("- name: Build, identify, extract and smoke-test the actual candidate bytes")[1].split("- name:")[0]
    assert 'TAURI_CONFIG' not in packaging_step


def test_release_rejects_bundled_example_with_missing_source(tmp_path):
    root = tmp_path / "source"
    example = root / "sdk" / "examples" / "broken"
    example.mkdir(parents=True)
    (example / "addon.json").write_text(json.dumps({
        "nodes": [{"id": "node", "source": "tools/missing.cs"}],
        "install_steps": [{"id": "step", "source": "tools/missing.cs"}],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="missing source tools.missing.cs"):
        _validate_example_sources(root)
