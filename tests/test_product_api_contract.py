from __future__ import annotations

import json
from pathlib import Path

import pytest

import allin1_sdk.product_workspace as product_workspace
from allin1_sdk.product_workspace import ProductWorkspaceInspector


CORE_WORKSPACE = (
    Path(__file__).resolve().parents[2] / "ALLIN1" / "allin1.workspace.json"
)


def test_real_allin1_runtime_contract_is_proven_from_bounded_sources() -> None:
    if not CORE_WORKSPACE.is_file():
        pytest.skip("Sibling ALLIN1 checkout is not available")

    report = ProductWorkspaceInspector().inspect(CORE_WORKSPACE)
    contracts = report.runtime_contracts

    assert contracts.valid, contracts.findings
    assert contracts.to_dict()["summary"] == {
        "hosts": 1,
        "packages": 3,
        "errors": 0,
        "warnings": 0,
    }
    host = contracts.hosts[0]
    assert host.component_id == "runtime.shared"
    assert host.api_version == 1
    assert host.assembly == "scripts/ALLIN1.dll"
    assert host.public_type == "ALLIN1.Allin1ExtensionApi"
    assert host.status == "verified"
    assert all(item.status == "verified" for item in host.members)

    packages = {item.component_id: item for item in contracts.packages}
    assert set(packages) == {
        "content.online",
        "content.experimental",
        "package.realistic-suppressors",
    }
    suppressors = packages["package.realistic-suppressors"]
    assert suppressors.status == "verified"
    assert suppressors.entry_points == (
        "RealisticSuppressors.RealisticSuppressorController",
    )
    assert suppressors.entry_point_sources == (
        "mods/realistic-suppressors/src/RealisticSuppressorController.cs",
    )
    assert set(suppressors.interfaces) == {
        "IStorySaveParticipant",
        "IWeaponComponentLifecycleParticipant",
    }
    assert set(suppressors.settings) == {
        "realistic_suppressors",
        "suppressor_breakage",
        "suppressor_durability_scale",
        "suppressor_heat_smoke",
        "suppressor_smoke_intensity",
        "suppressor_temperature_debug",
    }
    assert set(call.member for call in suppressors.api_calls) == {
        "GetBooleanSetting",
        "GetNumberSetting",
        "IsGbayMenuActive",
        "IsPackageEnabled",
        "RegistryAvailable",
        "RegisterStorySaveParticipant",
        "RegisterWeaponComponentLifecycleParticipant",
    }
    assert suppressors.workbench_relationships == (
        "realistic-suppressors.thermal-components",
    )
    assert "script/ALLIN1.csproj" in suppressors.project_references
    payload = report.to_dict()
    assert payload["api_contracts"]["valid"] is True
    assert payload["structurally_valid"] is True


def _workspace(root: Path) -> Path:
    (root / "src").mkdir()
    (root / "content").mkdir()
    (root / "src/RuntimeApi.cs").write_text(
        "namespace Test { public static class ProductApi { "
        "public const int ApiVersion = 1; } }\n",
        encoding="utf-8",
    )
    (root / "runtime-api.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "assembly": "scripts/TestRuntime.dll",
        "public_type": "Test.ProductApi",
        "source": "src/RuntimeApi.cs",
        "symbols": [{
            "name": "ApiVersion", "kind": "constant",
            "return_type": "int", "value": "1",
        }],
    }), encoding="utf-8")
    (root / "content/content.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "id": "test.content",
        "name": "Content",
        "version": "1.0.0",
        "capabilities": [],
        "systems": [{"id": "system", "name": "System"}],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": []},
    }), encoding="utf-8")
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
            "allowlisted_roots": ["src", "content"],
            "allowlisted_files": ["runtime-api.json"],
            "excluded_roots": [],
        },
        "components": [
            {
                "id": "runtime.shared",
                "name": "Runtime",
                "role": "story_runtime",
                "paths": ["src/RuntimeApi.cs"],
                "runtime_artifact": "scripts/TestRuntime.dll",
                "api_contract": "runtime-api.json",
            },
            {
                "id": "content.main",
                "name": "Content",
                "role": "official_content_pack",
                "package_id": "test.content",
                "manifest": "content/content.json",
                "paths": ["content/content.json"],
            },
        ],
        "relationships": [{
            "source": "content.main",
            "target": "runtime.shared",
            "type": "uses_shared_runtime",
        }],
    }), encoding="utf-8")
    return descriptor


def test_semantic_api_failure_is_structured_but_workspace_remains_inspectable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _workspace(tmp_path)
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert report.valid is False
    assert report.structurally_valid is True
    finding = next(
        item for item in report.findings
        if item.code == "api_contract_shared_runtime_missing"
    )
    assert finding.component_id == "content.main"
    package = report.runtime_contracts.packages[0]
    assert package.status == "error"
    assert package.runtime_assemblies == ()


def _consumer_workspace(root: Path) -> Path:
    (root / "src").mkdir()
    package = root / "mods/test-package"
    (package / "src").mkdir(parents=True)
    (package / "payload").mkdir()
    (root / "src/RuntimeApi.cs").write_text(
        "namespace Test { public interface IThing {} "
        "public static class Allin1ExtensionApi { "
        "public const int ApiVersion = 1; "
        "public static void Register(string id, IThing value) {} "
        "public static double GetNumberSetting(string id, string key, double value) => value; "
        "} }\n",
        encoding="utf-8",
    )
    (root / "runtime-api.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "assembly": "scripts/TestRuntime.dll",
        "public_type": "Test.Allin1ExtensionApi",
        "source": "src/RuntimeApi.cs",
        "symbols": [
            {
                "name": "ApiVersion", "kind": "constant",
                "return_type": "int", "value": "1",
            },
            {
                "name": "IThing", "kind": "interface",
                "capability": "story-save.transactions",
            },
            {
                "name": "Register", "kind": "method",
                "capability": "story-save.transactions",
                "requires": ["IThing"],
                "return_type": "void",
                "parameters": [
                    {"name": "id", "type": "string"},
                    {"name": "value", "type": "IThing"},
                ],
            },
            {
                "name": "GetNumberSetting", "kind": "method",
                "capability": "launcher.settings",
                "return_type": "double",
                "parameters": [
                    {"name": "id", "type": "string"},
                    {"name": "key", "type": "string"},
                    {"name": "value", "type": "double"},
                ],
            },
        ],
    }), encoding="utf-8")
    (package / "payload/TestPackage.dll").write_bytes(b"test assembly")
    (package / "src/Controller.cs").write_text(
        "namespace Addon { public sealed class Controller { "
        'private const string PackageId = "test-package"; '
        "public void Start() { "
        "Allin1ExtensionApi.Register(PackageId, this); "
        'Allin1ExtensionApi.GetNumberSetting(PackageId, "amount", 1d); '
        "Allin1ExtensionApi.UnknownCall(); } } }\n",
        encoding="utf-8",
    )
    (package / "allin1.content.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "id": "test-package",
        "name": "Test package",
        "version": "1.0.0",
        "capabilities": ["launcher.settings"],
        "systems": [{
            "id": "test-system",
            "name": "Test system",
            "settings": [{
                "key": "amount", "label": "Amount",
                "type": "boolean", "default": True,
            }],
        }],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": [{
            "path": "scripts/TestPackage/TestPackage.dll",
            "entry_point": "Addon.Controller",
        }]},
    }), encoding="utf-8")
    (package / "mod.toml").write_text(
        'schema_version = 2\n'
        'id = "test-package"\n'
        'name = "Test package"\n'
        'version = "1.0.0"\n'
        'type = "script"\n'
        'editions = ["enhanced"]\n'
        'dependencies = []\n'
        'conflicts = []\n\n'
        '[allin1]\n'
        'api_version = 1\n'
        'content = "allin1.content.json"\n'
        'requires = []\n\n'
        '[[files]]\n'
        'source = "payload/TestPackage.dll"\n'
        'destination = "scripts/TestPackage/TestPackage.dll"\n\n'
        '[[files]]\n'
        'source = "allin1.content.json"\n'
        'destination = "scripts/TestPackage/allin1.content.json"\n',
        encoding="utf-8",
    )
    descriptor = root / "allin1.workspace.json"
    descriptor.write_text(json.dumps({
        "schema_version": 1,
        "id": "test.consumer",
        "name": "Test Consumer",
        "version": "1.0.0",
        "kind": "product_workspace",
        "editions": ["enhanced"],
        "source_policy": {
            "inventory": "git_tracked_allowlist",
            "follow_symlinks": False,
            "execute_sources": False,
            "allowlisted_roots": ["src", "mods"],
            "allowlisted_files": ["runtime-api.json"],
            "excluded_roots": [],
        },
        "components": [
            {
                "id": "runtime.shared",
                "name": "Runtime",
                "role": "story_runtime",
                "paths": ["src"],
                "runtime_artifact": "scripts/TestRuntime.dll",
                "api_contract": "runtime-api.json",
            },
            {
                "id": "package.test",
                "name": "Test package",
                "role": "optional_package",
                "package_id": "test-package",
                "manifest": "mods/test-package/mod.toml",
                "content_manifest": "mods/test-package/allin1.content.json",
                "paths": [
                    "mods/test-package",
                    "mods/test-package/mod.toml",
                    "mods/test-package/allin1.content.json",
                ],
                "package_discovery": True,
            },
        ],
        "relationships": [{
            "source": "package.test",
            "target": "runtime.shared",
            "type": "integrates_with_api",
        }],
    }), encoding="utf-8")
    return descriptor


def test_consumer_contract_fails_closed_on_unknown_calls_and_missing_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _consumer_workspace(tmp_path)
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    codes = {item.code for item in report.runtime_contracts.findings}
    assert codes.issuperset({
        "api_contract_unknown_member",
        "api_contract_capability_missing",
        "api_contract_interface_missing",
        "api_contract_setting_type_mismatch",
    })
    package = report.runtime_contracts.packages[0]
    assert package.entry_point_sources == ("mods/test-package/src/Controller.cs",)
    assert package.status == "error"
    assert report.structurally_valid is True
