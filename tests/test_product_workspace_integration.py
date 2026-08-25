"""CLI, linker, and automation integration for product workspace graphs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.addon_sdk import AddonLinker, AddonManifest, AddonSdkCatalog
from allin1_sdk import app as sdk_app
import allin1_sdk.cli as sdk_cli
import allin1_sdk.product_workspace as product_workspace
from allin1_sdk.agent_api import command_risk, execute_request
from allin1_sdk.cli import main
from allin1_sdk.product_workspace import WorkspaceFinding


WORKSPACE = Path(__file__).resolve().parents[2] / "ALLIN1" / "allin1.workspace.json"


def _workspace() -> Path:
    if not WORKSPACE.is_file():
        pytest.skip("Sibling ALLIN1 workspace fixture is not present")
    return WORKSPACE


def _minimal_workspace(root: Path) -> Path:
    (root / "host.py").write_text("HOST = True\n", encoding="utf-8")
    descriptor = root / "allin1.workspace.json"
    descriptor.write_text(json.dumps({
        "schema_version": 1,
        "id": "test.product",
        "name": "Test Product",
        "version": "1.0.0",
        "kind": "product_workspace",
        "editions": ["enhanced"],
        "source_policy": {
            "inventory": "git_tracked_allowlist",
            "follow_symlinks": False,
            "execute_sources": False,
            "allowlisted_roots": [],
            "allowlisted_files": ["host.py"],
            "excluded_roots": [],
        },
        "components": [{
            "id": "host.launcher",
            "name": "Host",
            "role": "launcher_host",
            "paths": ["host.py"],
        }],
        "relationships": [],
    }), encoding="utf-8")
    return descriptor


def test_product_workspace_adapts_to_existing_visual_linker() -> None:
    manifest = AddonManifest.load(_workspace())
    report = AddonLinker().link(manifest)

    assert manifest.addon_id == "allin1.core"
    assert manifest.catalog_origin == "product-workspace"
    assert len(manifest.nodes) == 9
    assert len(manifest.references) == 14
    assert report.valid, report.issues
    assert {node.kind for node in manifest.nodes} >= {
        "launcher_host", "story_runtime", "official_content_pack",
        "build_tool", "sdk_example", "optional_package", "test_evidence",
        "documentation_evidence",
    }
    assert all(node.source for node in manifest.nodes)
    for node in manifest.nodes:
        expected = node.fields.get("Manifest") or node.fields["Paths"][0]
        assert node.source == expected
        assert "Evidence" in node.fields
        assert isinstance(node.fields["ManagedBuiltin"], bool)
    assert manifest.workspace_summary
    assert manifest.workspace_summary["Tracked files"] > 0
    assert manifest.workspace_summary["Components"] == len(manifest.nodes)
    assert manifest.runtime_contracts is not None
    assert len(manifest.runtime_contracts.hosts) == 1
    assert {item.component_id for item in manifest.runtime_contracts.packages} == {
        "content.online", "content.experimental",
        "package.realistic-suppressors",
    }
    markdown = report.to_markdown()
    assert "## Install plan" not in markdown
    assert "## Runtime API contracts" in markdown
    assert "### Host `runtime.shared` — API v1" in markdown
    assert "### Package `realistic-suppressors`" in markdown
    assert "`RegisterWeaponComponentLifecycleParticipant`" in markdown


def test_product_workspace_cli_returns_compact_typed_graph() -> None:
    result = CliRunner().invoke(main, [
        "inspect-product-workspace", str(_workspace()),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workspace"]["id"] == "allin1.core"
    assert payload["inventory"]["entry_count"] > 0
    assert payload["inventory"]["entries"] == []
    assert payload["inventory"]["entries_included"] is False
    evidence = payload["evidence"]
    assert len(evidence["components"]) == 9
    assert all(item["matched_files"] >= 0 for item in evidence["components"])
    assert all(item["matched_bytes"] >= 0 for item in evidence["components"])
    assert evidence["shared"]["files"] > 0
    assert evidence["shared"]["samples"]
    assert evidence["unassigned"]["files"] > 0
    assert evidence["unassigned"]["samples"]
    assert len(payload["graph"]["nodes"]) == 9
    nodes = {item["node_id"]: item for item in payload["graph"]["nodes"]}
    assert nodes["content.online"]["managed_builtin"] is True
    assert nodes["content.online"]["install_candidate"] is False
    assert nodes["package.realistic-suppressors"]["managed_builtin"] is False
    assert nodes["package.realistic-suppressors"]["install_candidate"] is True
    assert payload["valid"] is True
    contracts = payload["api_contracts"]
    assert contracts["valid"] is True
    assert contracts["summary"] == {
        "hosts": 1,
        "packages": 3,
        "errors": 0,
        "warnings": 0,
    }
    assert contracts["hosts"][0]["public_type"] == (
        "ALLIN1.Allin1ExtensionApi"
    )


def test_product_workspace_cli_help_explains_evidence_and_no_execution() -> None:
    result = CliRunner().invoke(main, ["inspect-product-workspace", "--help"])
    assert result.exit_code == 0, result.output
    help_text = result.output.casefold()
    assert "managed built-ins" in help_text
    assert "installable packages" in help_text
    assert "file/byte coverage" in help_text
    assert "shared and unassigned" in help_text
    assert "runtime api contracts" in help_text
    assert "never executed" in help_text


def test_agent_api_returns_the_same_component_evidence(
    tmp_path: Path,
) -> None:
    descriptor = _minimal_workspace(tmp_path)
    host_size = (tmp_path / "host.py").stat().st_size
    response = execute_request({
        "id": "workspace-evidence",
        "action": "execute",
        "command": "inspect-product-workspace",
        "args": [str(descriptor)],
    }, audit_path=tmp_path / "agent-audit.jsonl")

    assert response["ok"] is True
    assert response["risk"] == "read_only"
    payload = json.loads(response["result"]["output"])
    assert payload["evidence"]["components"] == [{
        "component_id": "host.launcher",
        "declared_paths": ["host.py"],
        "matched_files": 1,
        "matched_bytes": host_size,
        "unique_files": 1,
        "unique_bytes": host_size,
        "shared_files": 0,
        "shared_bytes": 0,
    }]
    assert payload["inventory"]["entries"] == []
    assert payload["evidence"]["unassigned"]["files"] == 0
    assert payload["evidence"]["shared"]["files"] == 0
    assert payload["api_contracts"]["summary"]["hosts"] == 0
    assert payload["api_contracts"]["summary"]["packages"] == 0


def test_product_workspace_commands_have_explicit_read_only_risk() -> None:
    assert command_risk("inspect-product-workspace") == "read_only"
    assert command_risk("open-product-workspace") == "read_only"


def test_open_product_workspace_directory_uses_canonical_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _minimal_workspace(tmp_path)
    launched: dict[str, object] = {}

    class Process:
        pid = 4242

    original_popen = sdk_cli.subprocess.Popen

    def fake_popen(command, *args, **options):
        if command and str(command[0]).casefold().endswith("git"):
            return original_popen(command, *args, **options)
        launched.update(command=command, cwd=options.get("cwd"), options=options)
        return Process()

    monkeypatch.setattr(sdk_cli.subprocess, "Popen", fake_popen)
    pid, manifest = sdk_cli._open_addon_manifest_window(tmp_path)

    assert pid == 4242
    assert manifest.manifest_path == descriptor.resolve()
    assert launched["command"][-1] == str(descriptor.resolve())


def test_direct_cli_api_open_is_transient() -> None:
    calls: list[tuple[Path, bool]] = []

    class Dialog:
        def open_manifest_path(self, manifest: Path, *, remember: bool = True) -> None:
            calls.append((manifest, remember))

    selected = Path("allin1.workspace.json")
    sdk_app._open_direct_addon_manifest(Dialog(), selected)

    assert calls == [(selected, False)]


def test_product_workspace_catalog_normalizes_legacy_repository_source(
    tmp_path: Path,
) -> None:
    descriptor = _minimal_workspace(tmp_path)
    state = tmp_path / "state"
    catalog = AddonSdkCatalog(tmp_path, state_root=state)
    state.mkdir()
    catalog.registry_path.write_text(json.dumps({
        "schema_version": 1,
        "manifests": [{
            "manifest": str(descriptor),
            "source_root": str(tmp_path),
            # Early product-workspace builds persisted the repository root.
            "package_source": str(tmp_path),
        }],
    }), encoding="utf-8")

    discovered = catalog.discover(include_external=True)
    assert len(discovered) == 1
    assert discovered[0].package_source == descriptor.resolve()

    transient = AddonManifest.load(descriptor)
    legacy = replace(transient, package_source=tmp_path)
    assert transient.catalog_identity == legacy.catalog_identity


def test_remembered_product_workspace_persists_descriptor_identity(
    tmp_path: Path,
) -> None:
    descriptor = _minimal_workspace(tmp_path)
    catalog = AddonSdkCatalog(tmp_path, state_root=tmp_path / "state")

    remembered = catalog.remember(
        descriptor, source_root=tmp_path, package_source=tmp_path,
    )
    registry = json.loads(catalog.registry_path.read_text(encoding="utf-8"))

    assert remembered.package_source == descriptor.resolve()
    assert registry["manifests"][0]["package_source"] == str(descriptor.resolve())


def test_workspace_findings_survive_linker_adapter_and_cli_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _minimal_workspace(tmp_path)
    base = product_workspace.ProductWorkspaceInspector().inspect(descriptor)
    enriched = replace(base, findings=base.findings + (
        WorkspaceFinding(
            "warning", "workspace_review_recommended",
            "Review this bounded source before publishing.",
            "host.launcher", "host.py",
        ),
    ))
    monkeypatch.setattr(
        product_workspace.ProductWorkspaceInspector, "inspect",
        lambda _self, _source: enriched,
    )

    manifest = AddonManifest.load(descriptor)
    report = AddonLinker().link(manifest)
    issues = {item.code: item for item in report.issues}

    assert issues["git_inventory_unavailable"].severity == "info"
    assert issues["workspace_review_recommended"].severity == "warning"
    assert issues["workspace_review_recommended"].subject == (
        "host.launcher · host.py"
    )
    assert issues["workspace_review_recommended"].source == "host.py"
    assert report.warning_count == 1

    result = CliRunner().invoke(main, ["validate", str(descriptor)])
    assert result.exit_code == 0, result.output
    assert "INFO git_inventory_unavailable" in result.output
    assert "WARNING workspace_review_recommended" in result.output
