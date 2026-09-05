from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from allin1_sdk import cli
from allin1_sdk.agent_api import command_catalog
from allin1_sdk.assistant_client import validate_advisory
from allin1_sdk.assistant_context import build_assistant_context
from allin1_sdk.mods import ModIntegrationService, ModManifest


def _git_identity(root: Path, remote: str) -> None:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text(
        f'[remote "origin"]\n\turl = {remote}\n', encoding="utf-8",
    )


def _package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    payload = root / "grounded.asi"
    payload.write_bytes(b"managed package payload")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = root / "mod.toml"
    manifest.write_text(
        "\n".join((
            "schema_version = 1",
            'id = "grounded-test"',
            'name = "Grounded Test"',
            'version = "1.0.0"',
            'type = "asi"',
            'editions = ["legacy", "enhanced"]',
            "",
            "[[files]]",
            'source = "grounded.asi"',
            'destination = "grounded.asi"',
            f'sha256 = "{digest}"',
        )) + "\n",
        encoding="utf-8",
    )
    return manifest


def _workspaces(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    launcher = tmp_path / "ALLIN1"
    sdk = tmp_path / "ALLIN1-SDK"
    package = tmp_path / "GTAV-ALLIN1-VR"
    for root in (launcher, sdk, package):
        root.mkdir()
    _git_identity(launcher, "https://github.com/example/GTAV-ALLIN1.git")
    _git_identity(sdk, "https://github.com/example/ALLIN1-SDK.git")
    _git_identity(package, "https://github.com/example/EZ-GTA-V-R.git")
    manifest = _package(package)
    game = tmp_path / "Grand Theft Auto V Enhanced"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    return launcher, sdk, package, manifest, game


def test_context_broker_supplies_repository_manifest_game_and_typed_operations(
    tmp_path: Path,
) -> None:
    launcher, sdk, package, manifest, game = _workspaces(tmp_path)
    context = build_assistant_context(
        "Review and install this package safely", repository_root=package,
        workspace_roots=(launcher, sdk), manifest=manifest, gta_path=game,
    )
    assert context.current_repository.role == "vr_package"
    assert {item.role for item in context.workspace_repositories} >= {
        "launcher", "sdk", "vr_package",
    }
    assert context.package["validated"] is True
    assert context.package["id"] == "grounded-test"
    assert context.gta_installation == {
        "path": str(game.resolve()), "source": "explicit",
        "verified": True, "edition": "enhanced",
    }
    operations = {item["name"]: item for item in context.relevant_operations}
    assert operations["install-package"]["risk"] == "game_write"
    assert operations["validate-package"]["risk"] == "read_only"
    assert not context.missing_context


def test_context_uses_origin_remote_not_an_earlier_submodule_url(tmp_path: Path) -> None:
    launcher, sdk, package, manifest, game = _workspaces(tmp_path)
    (sdk / ".git" / "config").write_text(
        '[submodule "tools/vendor"]\n\turl = https://example.invalid/vendor.git\n'
        '[remote "origin"]\n\turl = https://example.invalid/ALLIN1-SDK.git\n',
        encoding="utf-8",
    )
    context = build_assistant_context(
        "Review the SDK", repository_root=sdk,
        workspace_roots=(launcher, package), manifest=manifest, gta_path=game,
    )
    assert context.current_repository.role == "sdk"
    assert context.current_repository.remote == "https://example.invalid/ALLIN1-SDK.git"


def test_response_guard_corrects_risk_rejects_unknown_api_and_manual_copy(
    tmp_path: Path,
) -> None:
    launcher, sdk, package, manifest, game = _workspaces(tmp_path)
    context = build_assistant_context(
        "Install this mod", repository_root=package,
        workspace_roots=(launcher, sdk), manifest=manifest, gta_path=game,
    )
    valid = json.dumps({
        "summary": "Use the managed transaction.",
        "findings": [{
            "severity_domain": "engineering", "severity": "info",
            "evidence": "mod.toml validated", "file": str(manifest),
            "line": 1, "confidence": 0.95, "status": "confirmed",
        }],
        "recommended_operations": [
            {"operation": "install-package", "arguments": [str(manifest)],
             "rationale": "The validated manifest requires the managed lifecycle.",
             "expected_result": "A receipt-backed installation."},
            {"operation": "made-up-installer", "arguments": [],
             "rationale": "It sounds related.",
             "expected_result": "Unknown."},
        ],
        "proposed_changes": [],
        "missing_context": [], "abstentions": [],
    })
    advisory, flags = validate_advisory(valid, context)
    operation = advisory["recommended_operations"][0]
    assert operation["risk"] == "game_write"
    assert operation["mutating"] is True
    assert operation["acknowledgement_required"] is True
    assert operation["arguments_grounded"] is True
    assert operation["blocked_reason"] == ""
    assert operation["executed"] is False
    assert "unsupported_operation" in flags
    assert any("made-up-installer" in item for item in advisory["abstentions"])

    unsafe = json.dumps({
        "summary": "Copy the ASI into the GTA5 directory.",
        "findings": [], "recommended_operations": [],
        "proposed_changes": [],
        "missing_context": [], "abstentions": [],
    })
    blocked, blocked_flags = validate_advisory(unsafe, context)
    assert blocked["recommended_operations"] == []
    assert "unsafe_guidance_withheld" in blocked_flags
    assert "withheld" in blocked["summary"].casefold()


def test_missing_manifest_blocks_and_removes_model_placeholder_arguments(
    tmp_path: Path,
) -> None:
    launcher, sdk, package, manifest, game = _workspaces(tmp_path)
    manifest.unlink()
    context = build_assistant_context(
        "Install this ASI package", repository_root=package,
        workspace_roots=(launcher, sdk), gta_path=game,
    )
    response = json.dumps({
        "summary": "Use the managed lifecycle.", "findings": [],
        "recommended_operations": [{
            "operation": "install-package",
            "arguments": ["path/to/your/mod.toml"],
            "rationale": "The request concerns a managed package installation.",
            "expected_result": "A receipt-backed installation.",
        }],
        "proposed_changes": [],
        "missing_context": [], "abstentions": [],
    })
    advisory, flags = validate_advisory(response, context)
    operation = advisory["recommended_operations"][0]
    assert operation["arguments"] == []
    assert operation["arguments_grounded"] is False
    assert operation["blocked_reason"] == "package manifest was not provided or found"
    assert operation["executed"] is False
    assert "ungrounded_operation_arguments" in flags
    assert any("install-package" in item and "blocked" in item for item in advisory["abstentions"])


def test_context_and_package_evidence_commands_are_available_through_cli_and_api(
    tmp_path: Path,
) -> None:
    launcher, sdk, package, manifest, game = _workspaces(tmp_path)
    runner = CliRunner()
    context = runner.invoke(cli.main, [
        "assistant", "context", "review", "package",
        "--repository-root", str(package), "--workspace-root", str(launcher),
        "--workspace-root", str(sdk), "--manifest", str(manifest),
        "--gta-path", str(game),
    ])
    assert context.exit_code == 0
    payload = json.loads(context.output)
    assert payload["current_repository"]["role"] == "vr_package"
    assert payload["operation_mode"] == "advisory"

    validated = runner.invoke(cli.main, ["validate-package", str(manifest)])
    assert validated.exit_code == 0 and json.loads(validated.output)["valid"] is True
    catalog = {item["name"]: item for item in command_catalog()}
    for name in (
        "validate-package", "inspect-package-receipt", "verify-package-ownership",
    ):
        assert catalog[name]["risk"] == "read_only"


def test_receipt_inspection_and_ownership_verification_detect_tampering(
    tmp_path: Path,
) -> None:
    game = tmp_path / "GTA V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    manifest_path = _package(tmp_path / "package")
    manifest = ModManifest.load(manifest_path)
    service = ModIntegrationService(game)
    service.install(manifest)
    receipt = service.inspect_receipt("grounded-test")
    assert receipt["files"][0]["sha256"] == hashlib.sha256(
        (game / "grounded.asi").read_bytes()
    ).hexdigest()
    healthy = service.verify_ownership("grounded-test")
    assert healthy["healthy"] is True and healthy["ownership_verified"] is True

    (game / "grounded.asi").write_bytes(b"externally changed")
    damaged = service.verify_ownership("grounded-test")
    assert damaged["healthy"] is False
    assert any("externally changed" in item for item in damaged["issues"])


def test_rpf_ownership_probe_stays_outside_the_game_installation(
    tmp_path: Path, monkeypatch,
) -> None:
    game = tmp_path / "GTA V"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ")
    service = ModIntegrationService(game)
    outputs: list[Path] = []

    def fake_extract(_archive, _entry, output, *, allow_missing=False):
        assert allow_missing is True
        outputs.append(output)
        return False

    monkeypatch.setattr(service, "_extract_rpf_entry", fake_extract)
    matches = service._rpf_entry_matches({
        "archive": "mods/update/update.rpf",
        "entry": "common/data/dlclist.xml",
        "owner": "grounded-test",
    }, None)

    assert matches is True
    assert len(outputs) == 1
    assert not outputs[0].is_relative_to(game.resolve())
    assert not outputs[0].exists()
