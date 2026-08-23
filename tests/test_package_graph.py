import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk.package_graph import PackageGraphWorkspace
from allin1_sdk.rpf_graph import RpfPackageGraph


def _package(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example/dlc.rpf", b"RPF7 sealed bytes")
        archive.writestr(
            "example/vehicles.meta",
            "<CVehicleModelInfo__InitDataList><InitDatas /></CVehicleModelInfo__InitDataList>",
        )
        archive.writestr("example/stream/example.yft", b"vehicle-model")
    return path


def test_package_graph_is_persistent_and_preserves_sealed_rpfs(tmp_path):
    package = _package(tmp_path / "vehicle.zip")
    service = PackageGraphWorkspace(tmp_path / "projects")

    created = service.import_package(package)
    state = RpfPackageGraph.validate(created.graph, verify_sources=True)
    assert created.reused is False
    assert created.member_count == 3
    assert created.sealed_rpf_count == 1
    assert state["sealed_archive_count"] == 1
    assert state["payload"]["origin"]["type"] == "mod_package_import"
    assert state["semantic"]["summary"]["entities"] == 0
    sealed = next(
        node for node in state["nodes"].values()
        if node["type"] == "sealed_archive"
    )
    assert sealed["name"] == "dlc.rpf"
    assert Path(sealed["source"]).is_relative_to(created.workspace)

    reused = service.import_package(package)
    assert reused.reused is True
    assert reused.workspace == created.workspace
    assert reused.graph == created.graph
    recent = service.list_projects()
    assert len(recent) == 1
    assert recent[0]["graph"] == created.graph
    assert recent[0]["sealed_rpfs"] == 1


def test_sealed_rpf_expands_into_hash_bound_nodes(tmp_path):
    project = PackageGraphWorkspace(tmp_path / "projects").import_package(
        _package(tmp_path / "vehicle.zip")
    )
    state = RpfPackageGraph.validate(project.graph, verify_sources=True)
    sealed = next(
        node for node in state["nodes"].values()
        if node["type"] == "sealed_archive"
    )

    class FakeExplorer:
        @staticmethod
        def index(source):
            assert Path(source) == Path(sealed["source"])
            return SimpleNamespace(edition="Enhanced")

        @staticmethod
        def extract_authoring_tree(_index, destination):
            target = Path(destination)
            (target / "common" / "data").mkdir(parents=True)
            (target / "common" / "data" / "handling.meta").write_text(
                "<HandlingData />", encoding="utf-8",
            )
            (target / ".allin1-rpf-export.json").write_text(
                json.dumps({"internal": True}), encoding="utf-8",
            )
            return target, {"summary": {"archives": 1, "files": 1}}

    report = RpfPackageGraph.expand_sealed_archive(
        project.graph, sealed["id"], FakeExplorer(),
    )
    expanded = RpfPackageGraph.validate(project.graph, verify_sources=True)
    node = expanded["nodes"][sealed["id"]]
    assert report["files"] == 1
    assert report["added_nodes"] == 3
    assert expanded["sealed_archive_count"] == 0
    assert node["type"] == "archive"
    assert node["expanded_from"]["sha256"] == sealed["sha256"]
    assert not any(
        item["name"] == ".allin1-rpf-export.json"
        for item in expanded["nodes"].values()
    )
    recent = PackageGraphWorkspace(tmp_path / "projects").list_projects()
    assert recent[0]["expanded_rpfs"] == 1
    assert recent[0]["sealed_rpfs"] == 0


def test_package_review_graph_cannot_build_as_one_rpf(tmp_path):
    project = PackageGraphWorkspace(tmp_path / "projects").import_package(
        _package(tmp_path / "vehicle.zip")
    )
    with pytest.raises(ValueError, match="package-review graph"):
        RpfPackageGraph.build(project.graph, None, tmp_path / "bad.rpf")


def test_package_graph_cli_import_is_structured_and_reusable(tmp_path):
    from click.testing import CliRunner

    from allin1_sdk.cli import main

    package = _package(tmp_path / "vehicle.zip")
    workspace = tmp_path / "projects"
    result = CliRunner().invoke(main, [
        "import-package-graph", str(package),
        "--workspace-root", str(workspace),
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["operation"] == "import_package_graph"
    assert payload["package_members"] == 3
    assert payload["sealed_rpf_nodes"] == 1
    assert payload["workspace_reused"] is False
    assert Path(payload["graph"]).is_file()
