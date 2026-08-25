"""Runtime contracts for product-workspace evidence in the Package Linker."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path

import pytest

import allin1_sdk.addon_sdk_ui as sdk_ui
from allin1_sdk.app import _configure_style


def _workspace(root: Path) -> Path:
    (root / "host.py").write_text("HOST = True\n", encoding="utf-8")
    (root / "content.json").write_text(json.dumps({
        "schema_version": 1,
        "id": "test.builtin",
        "name": "Built-in content",
        "version": "1.0.0",
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
            "allowlisted_roots": [],
            "allowlisted_files": ["host.py", "content.json"],
            "excluded_roots": [],
        },
        "components": [
            {
                "id": "host.launcher",
                "name": "Host",
                "role": "launcher_host",
                "paths": ["host.py"],
            },
            {
                "id": "content.builtin",
                "name": "Built-in content",
                "role": "official_content_pack",
                "paths": ["content.json"],
                "package_id": "test.builtin",
                "manifest": "content.json",
            },
        ],
        "relationships": [{
            "source": "host.launcher",
            "target": "content.builtin",
            "type": "registers",
        }],
    }), encoding="utf-8")
    return descriptor


def _api_workspace(root: Path) -> Path:
    (root / "data").mkdir()
    (root / "script").mkdir()
    (root / "content").mkdir()
    (root / "script/ExtensionRuntime.cs").write_text(
        "namespace ALLIN1 { public static class Allin1ExtensionApi { "
        "public const int ApiVersion = 1; "
        "public static void RegisterFeature() { } } }\n",
        encoding="utf-8",
    )
    (root / "content/Consumer.cs").write_text(
        "class Consumer { void Start() { "
        "ALLIN1.Allin1ExtensionApi.RegisterFeature(); } }\n",
        encoding="utf-8",
    )
    (root / "data/runtime-api.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "assembly": "scripts/ALLIN1.dll",
        "public_type": "ALLIN1.Allin1ExtensionApi",
        "source": "script/ExtensionRuntime.cs",
        "symbols": [
            {
                "name": "ApiVersion", "kind": "constant",
                "return_type": "int", "value": "1",
            },
            {
                "name": "RegisterFeature", "kind": "method",
                "capability": "gbay.sections",
                "return_type": "void", "parameters": [],
            },
        ],
    }), encoding="utf-8")
    (root / "content/allin1.content.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "id": "test.builtin",
        "name": "Built-in content",
        "version": "1.0.0",
        "capabilities": [],
        "systems": [],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": [{"path": "scripts/ALLIN1.dll"}]},
    }), encoding="utf-8")
    descriptor = root / "allin1.workspace.json"
    descriptor.write_text(json.dumps({
        "schema_version": 1,
        "id": "test.api-product",
        "name": "Test API Product",
        "version": "1.0.0",
        "kind": "product_workspace",
        "editions": ["enhanced"],
        "source_policy": {
            "inventory": "git_tracked_allowlist",
            "follow_symlinks": False,
            "execute_sources": False,
            "allowlisted_roots": ["data", "script", "content"],
            "allowlisted_files": [],
            "excluded_roots": [],
        },
        "components": [
            {
                "id": "runtime.shared", "name": "Shared runtime",
                "role": "story_runtime", "paths": ["script"],
                "runtime_artifact": "scripts/ALLIN1.dll",
                "api_contract": "data/runtime-api.json",
            },
            {
                "id": "content.builtin", "name": "Built-in content",
                "role": "official_content_pack",
                "paths": [
                    "content/allin1.content.json", "content/Consumer.cs",
                ],
                "package_id": "test.builtin",
                "manifest": "content/allin1.content.json",
            },
        ],
        "relationships": [{
            "source": "content.builtin", "target": "runtime.shared",
            "type": "uses_shared_runtime",
        }],
    }), encoding="utf-8")
    return descriptor


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    _configure_style(root)
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def test_product_workspace_linker_is_bounded_navigable_and_refreshable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk,
) -> None:
    descriptor = _workspace(tmp_path)
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        dialog.open_manifest_path(descriptor, remember=False)
        dialog.update()

        assert dialog.report is not None
        assert dialog.report.manifest.is_product_workspace
        assert dialog.package_source is None
        for label in (
            "Browse package assets…",
            "Open in Workbench…",
            "Open in Models & Materials…",
            "Inspect package RPFs…",
        ):
            assert dialog.review_menu.entrycget(label, "state") == "disabled"
        assert dialog.review_menu.entrycget(
            "Refresh current audit", "state",
        ) == "normal"

        roots = {
            dialog.graph.item(item, "text"): item
            for item in dialog.graph.get_children()
        }
        assert "Workspace evidence" in roots
        assert "Install plan" not in roots
        link_rows = dialog.graph.get_children(roots["Resolved references"])
        assert [dialog.graph.item(item, "text") for item in link_rows] == [
            "host.launcher — registers → content.builtin"
        ]

        selected = "node:content.builtin"
        dialog.graph.selection_set(selected)
        dialog._inspect_selection()
        assert dialog._selected_source() == (tmp_path / "content.json").resolve()

        dialog._refresh_audit()
        assert dialog.graph.selection() == (selected,)
        assert dialog.status.get().startswith("Refreshed · Product workspace")
    finally:
        dialog.destroy()


def test_api_contracts_are_hierarchical_inspectable_and_refreshable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tk_root: tk.Tk,
) -> None:
    descriptor = _api_workspace(tmp_path)
    monkeypatch.setattr(sdk_ui, "user_data_root", lambda: tmp_path / "state")
    dialog = sdk_ui.AddonSdkDialog(
        tk_root, Path(__file__).resolve().parents[1], standalone=True,
    )
    try:
        dialog.open_manifest_path(descriptor, remember=False)
        dialog.update()

        assert dialog.report is not None
        assert not dialog.report.valid
        assert dialog.report.manifest.runtime_contracts is not None
        assert dialog.report.manifest.runtime_contracts.error_count == 1

        roots = list(dialog.graph.get_children())
        labels = [dialog.graph.item(item, "text") for item in roots]
        assert labels.index("Diagnostics") < labels.index("API contracts")
        assert labels.index("API contracts") < labels.index("Content fields")
        api_root = "api:root"
        assert bool(dialog.graph.item(api_root, "open")) is True
        children = dialog.graph.get_children(api_root)
        assert children == (
            "api:host:runtime.shared",
            "api:package:content.builtin",
        )
        assert bool(dialog.graph.item(children[0], "open")) is False
        assert bool(dialog.graph.item(children[1], "open")) is False
        assert dialog.graph.item(children[1], "values")[1] == "error"

        package_id = "api:package:content.builtin"
        dialog.graph.selection_set(package_id)
        dialog._inspect_selection()
        fields = {
            dialog.fields.item(item, "text"): dialog.fields.item(item, "values")[0]
            for item in dialog.fields.get_children()
        }
        assert fields["API provider"] == "runtime.shared"
        assert fields["Contract status"] == "error"
        assert dialog._selected_source() == (
            tmp_path / "content/allin1.content.json"
        ).resolve()

        call_id = "api:package:content.builtin:calls:1:RegisterFeature"
        dialog.graph.selection_set(call_id)
        dialog._inspect_selection()
        assert dialog.heading.get() == "RegisterFeature"
        assert dialog._selected_source() == (tmp_path / "content/Consumer.cs").resolve()

        diagnostic_codes = {
            item.code for item in dialog.report.issues
            if item.code.startswith("api_contract_")
        }
        assert diagnostic_codes == {"api_contract_capability_missing"}

        dialog._refresh_audit()
        assert dialog.graph.selection() == (call_id,)
        assert dialog.status.get().startswith("Refreshed · Product workspace")
    finally:
        dialog.destroy()
