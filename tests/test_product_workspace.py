from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import allin1_sdk.product_workspace as product_workspace
from allin1_sdk.product_workspace import (
    ProductWorkspaceInspector,
    load_product_workspace,
)


SIBLING_ALLIN1 = (
    Path(__file__).resolve().parents[2] / "ALLIN1" / "allin1.workspace.json"
)


def _descriptor() -> dict:
    return {
        "schema_version": 1,
        "id": "test.workspace",
        "name": "Test Workspace",
        "version": "1.2.3",
        "kind": "product_workspace",
        "editions": ["legacy", "enhanced"],
        "source_policy": {
            "inventory": "git_tracked_allowlist",
            "follow_symlinks": False,
            "execute_sources": False,
            "allowlisted_roots": ["src", "content", "docs"],
            "allowlisted_files": ["tool.txt"],
            "excluded_roots": ["src/build"],
        },
        "components": [
            {
                "id": "host.launcher",
                "name": "Host",
                "role": "launcher_host",
                "paths": ["src/main.py"],
            },
            {
                "id": "runtime.shared",
                "name": "Runtime",
                "role": "story_runtime",
                "paths": ["src/runtime.dll"],
                "runtime_artifact": "scripts/Runtime.dll",
            },
            {
                "id": "content.main",
                "name": "Content",
                "role": "official_content_pack",
                "package_id": "test.content",
                "manifest": "content/allin1.content.json",
                "paths": ["content/allin1.content.json"],
                "defaults": {"experimental_systems_enabled": False},
            },
            {
                "id": "tool.builder",
                "name": "Tool",
                "role": "build_tool",
                "paths": ["tool.txt"],
                "artifact_name": "Builder.exe",
            },
            {
                "id": "evidence.docs",
                "name": "Docs",
                "role": "documentation_evidence",
                "paths": ["docs/readme.md"],
                "package_discovery": False,
            },
        ],
        "relationships": [
            {"source": "host.launcher", "target": "runtime.shared", "type": "deploys"},
            {"source": "host.launcher", "target": "content.main", "type": "registers"},
            {
                "source": "content.main", "target": "runtime.shared",
                "type": "uses_shared_runtime",
            },
            {"source": "tool.builder", "target": "content.main", "type": "builds_install_time_assets"},
            {"source": "evidence.docs", "target": "host.launcher", "type": "documents"},
        ],
    }


def _workspace(root: Path, descriptor: dict | None = None) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src/main.py").write_text("value = 1\n", encoding="utf-8")
    (root / "src/runtime.dll").write_bytes(b"runtime")
    (root / "content").mkdir()
    (root / "content/allin1.content.json").write_text(json.dumps({
        "schema_version": 1,
        "api_version": 1,
        "id": "test.content",
        "name": "Content",
        "version": "1.2.3",
        "systems": [{"id": "system", "name": "System"}],
    }), encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs/readme.md").write_text("evidence", encoding="utf-8")
    (root / "tool.txt").write_text("tool source", encoding="utf-8")
    path = root / "allin1.workspace.json"
    path.write_text(json.dumps(descriptor or _descriptor()), encoding="utf-8")
    return path


def test_actual_allin1_product_workspace_is_a_bounded_typed_graph() -> None:
    if not SIBLING_ALLIN1.is_file():
        pytest.skip("Sibling ALLIN1 checkout is not available")
    report = ProductWorkspaceInspector().inspect(SIBLING_ALLIN1)

    assert report.valid, report.findings
    assert report.inventory.method == "git_tracked"
    assert report.workspace.editions == ("legacy", "enhanced")
    assert {item.node_id for item in report.nodes} == {
        "launcher.host",
        "runtime.shared",
        "content.online",
        "content.experimental",
        "tool.rpfpatcher",
        "example.colored-smokes",
        "package.realistic-suppressors",
        "evidence.tests",
        "evidence.docs",
    }
    runtime_nodes = [item for item in report.nodes if item.runtime_artifact]
    assert [(item.node_id, item.runtime_artifact) for item in runtime_nodes] == [
        ("runtime.shared", "scripts/ALLIN1.dll"),
    ]
    nodes = {item.node_id: item for item in report.nodes}
    assert nodes["launcher.host"].category == "host"
    assert nodes["runtime.shared"].category == "runtime"
    assert nodes["tool.rpfpatcher"].category == "tool"
    assert nodes["evidence.tests"].category == "evidence"
    assert nodes["evidence.docs"].category == "evidence"
    assert nodes["content.online"].package_id == "allin1.online-content"
    assert nodes["content.experimental"].package_id == "allin1.experimental-gameplay"
    assert nodes["content.online"].experimental is False
    assert nodes["content.experimental"].experimental is True
    assert nodes["content.online"].managed_builtin is True
    assert nodes["content.experimental"].managed_builtin is True
    assert nodes["content.online"].install_candidate is False
    assert nodes["content.experimental"].install_candidate is False
    components = {
        item.component_id: item for item in report.workspace.components
    }
    assert components["content.online"].defaults == {
        "experimental_systems_enabled": False,
    }
    assert components["content.experimental"].defaults == {
        "systems_enabled": False,
        "diagnostics_enabled": False,
    }
    assert nodes["package.realistic-suppressors"].package_id == "realistic-suppressors"
    assert nodes["package.realistic-suppressors"].managed_builtin is False
    assert nodes["package.realistic-suppressors"].install_candidate is True
    assert nodes["example.colored-smokes"].package_id == "allin1.colored_smokes"
    assert nodes["example.colored-smokes"].install_candidate is False

    assert {item.node_id for item in report.install_candidates} == {
        "package.realistic-suppressors",
    }
    assert (
        "mods/realistic-suppressors/RealisticSuppressors.csproj"
        in report.inventory.paths
    )
    assert (
        "mods/realistic-suppressors/src/RealisticSuppressorController.cs"
        in report.inventory.paths
    )
    assert not any(
        item.path.startswith("mods/realistic-suppressors/dist/")
        for item in report.inventory.entries
    )
    rollups = {
        item.component_id: item for item in report.evidence.components
    }
    assert rollups["package.realistic-suppressors"].matched_files > 0
    assert rollups["package.realistic-suppressors"].shared_files > 0
    assert report.evidence.unassigned.files > 0
    assert report.evidence.shared.files > 0
    assert len(report.evidence.unassigned.samples) <= 12
    assert len(report.evidence.shared.samples) <= 12
    assert {item.code for item in report.findings}.issuperset({
        "inventory_files_unassigned", "inventory_files_shared",
    })
    assert not any(item.path.startswith(".artifacts/") for item in report.inventory.entries)
    assert not any(
        item.target in {"evidence.docs", "evidence.tests"}
        for item in report.edges
    )


def test_git_inventory_uses_only_tracked_allowlisted_sources(tmp_path: Path) -> None:
    descriptor = _workspace(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([
        "git", "-C", str(tmp_path), "add", "src/main.py", "src/runtime.dll",
        "content/allin1.content.json", "docs/readme.md", "tool.txt",
    ], check=True)
    (tmp_path / "src/untracked.py").write_text("not evidence", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside allowlist", encoding="utf-8")

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert report.valid, report.findings
    assert report.inventory.method == "git_tracked"
    assert "src/main.py" in report.inventory.paths
    assert "src/untracked.py" not in report.inventory.paths
    assert "secret.txt" not in report.inventory.paths


def test_allowlist_fallback_excludes_ignored_roots_and_never_follows_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _workspace(tmp_path)
    (tmp_path / "src/build").mkdir()
    (tmp_path / "src/build/generated.py").write_text("generated", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside/secret.py").write_text("secret", encoding="utf-8")
    try:
        os.symlink(tmp_path / "outside", tmp_path / "src/linked", target_is_directory=True)
    except OSError:
        pass
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert report.valid, report.findings
    assert report.inventory.method == "declared_allowlists"
    assert "src/main.py" in report.inventory.paths
    assert "src/build/generated.py" not in report.inventory.paths
    assert "outside/secret.py" not in report.inventory.paths
    assert not any(item.path.startswith("src/linked/") for item in report.inventory.entries)
    assert any(item.code == "git_inventory_unavailable" for item in report.findings)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (
            lambda data: data["source_policy"].update(follow_symlinks=True),
            "follow_symlinks",
        ),
        (
            lambda data: data["source_policy"].update(execute_sources=True),
            "execute_sources",
        ),
        (
            lambda data: data["components"][0].update(paths=["../escape"]),
            "traversal",
        ),
        (
            lambda data: data["components"][-1].update(package_discovery=True),
            "evidence",
        ),
        (
            lambda data: data["components"][2].update(package_discovery=True),
            "only optional packages",
        ),
        (
            lambda data: data["relationships"][0].update(target="missing.node"),
            "unknown component",
        ),
        (
            lambda data: data["relationships"][0].update(type="verifies"),
            "incompatible endpoint",
        ),
        (
            lambda data: data["relationships"][0].update(type="unknown"),
            "not supported",
        ),
    ],
)
def test_workspace_contract_fails_closed(
    tmp_path: Path, mutate, message: str,
) -> None:
    data = _descriptor()
    mutate(data)
    path = tmp_path / "allin1.workspace.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_product_workspace(path)


def test_manifest_identity_failures_are_structured_and_evidence_is_not_installable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = _workspace(tmp_path)
    content_path = tmp_path / "content/allin1.content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["id"] = "wrong.content"
    content_path.write_text(json.dumps(content), encoding="utf-8")
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert not report.valid
    finding = next(item for item in report.findings if item.code == "component_manifest_invalid")
    assert finding.component_id == "content.main"
    assert "does not match" in finding.message
    evidence = next(item for item in report.nodes if item.node_id == "evidence.docs")
    assert evidence.category == "evidence"
    assert evidence.install_candidate is False
    payload = report.to_dict()
    assert payload["graph"]["nodes"]
    assert payload["graph"]["edges"]
    assert payload["evidence"]["components"]
    assert payload["evidence"]["unassigned"]["files"] == 0
    assert payload["valid"] is False


def test_component_evidence_rollups_are_deduplicated_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _descriptor()
    data["components"][0]["paths"].append("src")
    data["components"].append({
        "id": "package.optional",
        "name": "Optional package",
        "role": "optional_package",
        "package_id": "test.optional",
        "manifest": "content/optional.toml",
        "paths": ["content/optional.toml"],
        "package_discovery": True,
    })
    descriptor = _workspace(tmp_path, data)
    (tmp_path / "content/optional.toml").write_text(
        'schema_version = 1\nid = "test.optional"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "docs/orphan.txt").write_text("orphan", encoding="utf-8")
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert report.valid, report.findings
    nodes = {item.node_id: item for item in report.nodes}
    assert nodes["content.main"].managed_builtin is True
    assert nodes["content.main"].install_candidate is False
    assert nodes["package.optional"].managed_builtin is False
    assert nodes["package.optional"].install_candidate is True
    assert [item.node_id for item in report.install_candidates] == [
        "package.optional"
    ]

    rollups = {item.component_id: item for item in report.evidence.components}
    host = rollups["host.launcher"]
    runtime = rollups["runtime.shared"]
    assert (host.matched_files, host.unique_files, host.shared_files) == (2, 1, 1)
    assert host.matched_bytes == host.unique_bytes + host.shared_bytes
    assert (runtime.matched_files, runtime.unique_files, runtime.shared_files) == (
        1, 0, 1,
    )
    assert report.evidence.shared.files == 1
    shared = report.evidence.shared.samples[0]
    assert shared.path == "src/runtime.dll"
    assert shared.owner_count == 2
    assert shared.owners == ("host.launcher", "runtime.shared")
    assert shared.owners_truncated is False
    assert report.evidence.unassigned.files == 1
    assert report.evidence.unassigned.bytes == len(b"orphan")
    assert report.evidence.unassigned.samples[0].path == "docs/orphan.txt"
    assert [item.code for item in report.findings[-2:]] == [
        "inventory_files_unassigned", "inventory_files_shared",
    ]

    payload = report.to_dict()
    assert payload["evidence"]["components"][0]["component_id"] == "host.launcher"
    assert payload["evidence"]["shared"]["samples"][0]["owners"] == (
        "host.launcher", "runtime.shared",
    )


def test_component_evidence_samples_and_owner_lists_have_stable_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _descriptor()
    for index in range(16):
        data["components"].append({
            "id": f"host.extra-{index:02d}",
            "name": f"Extra host {index:02d}",
            "role": "launcher_host",
            "paths": ["docs/readme.md"],
        })
    descriptor = _workspace(tmp_path, data)
    for index in range(20):
        (tmp_path / "docs" / f"unassigned-{index:02d}.txt").write_text(
            str(index), encoding="utf-8",
        )
    monkeypatch.setattr(product_workspace, "_git_inventory", lambda *_args: None)

    report = ProductWorkspaceInspector().inspect(descriptor)

    assert report.evidence.unassigned.files == 20
    assert len(report.evidence.unassigned.samples) == 12
    assert [item.path for item in report.evidence.unassigned.samples] == [
        f"docs/unassigned-{index:02d}.txt" for index in range(12)
    ]
    shared = report.evidence.shared.samples[0]
    assert shared.path == "docs/readme.md"
    assert shared.owner_count == 17
    assert len(shared.owners) == 16
    assert shared.owners_truncated is True
