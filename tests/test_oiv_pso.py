from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from lxml import etree

from allin1_sdk import native_assets
from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main
from allin1_sdk.oiv_workbench import OivWorkbench


def _pso_package(root: Path, *, target: str = "common/data/settings.ymt") -> Path:
    package = root / "pso-oiv"
    package.mkdir(parents=True)
    (package / "assembly.xml").write_text(
        f'''<package version="2.2" target="Five"><metadata>
        <name>Native recipe</name></metadata><content>
        <archive path="update/update.rpf">
          <pso path="{target}"><replace xpath="/Root/Mode">
            <Mode>NEW</Mode></replace></pso>
        </archive></content></package>''',
        encoding="utf-8",
    )
    return package


class _FakePsoRpfService:
    def __init__(self, root: Path, archive: Path, original: bytes):
        self.archive = archive
        self.original = original
        self.project_root = root / "project"
        patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        patcher.parent.mkdir(parents=True)
        patcher.write_bytes(b"exe")
        self.gta_path = root / "game"
        self.gta_path.mkdir()
        self.entry = SimpleNamespace(
            archive_path="", path="common/data/settings.ymt",
            name="settings.ymt", kind="resource",
        )
        self.index_value = SimpleNamespace(
            source=archive, edition="Enhanced", entries=(self.entry,),
        )
        self.changes = None

    def index(self, archive: Path):
        assert archive == self.archive
        return self.index_value

    def extract(self, index, entry, destination: Path):
        assert index is self.index_value and entry is self.entry
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.original)
        return destination

    def multi_change_plan(self, index, changes):
        assert index is self.index_value
        self.changes = tuple(changes)
        return {
            "schema_version": 2,
            "operation": "rpf_multi_entry_change",
            "status": "ready",
            "archive": str(self.archive),
            "archive_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "changes": list(self.changes),
        }


def _native_round_trip(monkeypatch, *, semantic_drift: bool = False) -> None:
    def convert(args, **_kwargs):
        command = str(args[1])
        source = Path(args[2])
        output = Path(args[3])
        assets = Path(args[4])
        assets.mkdir(parents=True, exist_ok=True)
        if command == "asset-from-xml":
            output.write_bytes(b"RSC8" + source.read_bytes())
        elif command == "asset-xml":
            xml = source.read_bytes()[4:]
            if semantic_drift and source.name.startswith("op_"):
                xml = xml.replace(b"<Mode>NEW</Mode>", b"<Mode>DRIFT</Mode>")
            output.write_bytes(xml)
        else:  # pragma: no cover - catches a helper contract regression
            raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(native_assets, "run_hidden", convert)


@pytest.mark.parametrize(
    "target",
    [
        "common/data/settings.ymt",
        "common/data/settings.ymf",
        "common/data/settings.ymap",
        "common/data/settings.ytyp",
        "common/data/settings.pso",
    ],
)
def test_oiv_pso_recipe_is_bounded_and_compile_ready(tmp_path, target):
    plan = OivWorkbench().inspect(_pso_package(tmp_path, target=target))

    assert plan.recipe_supported
    assert plan.rpf_recipe_compilable
    assert not plan.xml_compilable
    assert not plan.translatable
    assert len(plan.pso_operations) == 1
    assert plan.pso_operations[0].edits[0].action == "replace"
    assert not [item for item in plan.findings if item.severity == "error"]
    assert "VERIFIED RPF RECIPE COMPILE READY" in plan.to_markdown()


def test_oiv_pso_uses_shared_cli_console_and_agent_api_route(tmp_path, monkeypatch):
    package = _pso_package(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7")
    output = tmp_path / "compiled"

    def compile_recipe(self, plan, selected, destination, *, service):
        assert len(plan.pso_operations) == 1
        assert Path(selected) == archive
        assert Path(destination) == output
        output.mkdir()
        authored = output / "rpf-plan.json"
        authored.write_text('{"status":"ready"}', encoding="utf-8")
        audit = output / "compile-audit.json"
        audit.write_text("{}", encoding="utf-8")
        return authored, audit

    monkeypatch.setattr(OivWorkbench, "compile_rpf_recipe_bundle", compile_recipe)
    monkeypatch.setattr("allin1_sdk.cli._rpf_service", lambda *_args: object())
    result = CliRunner().invoke(main, [
        "compile-oiv-recipe", str(package), str(archive),
        "--workspace-root", str(tmp_path), "-o", str(output),
    ])

    assert result.exit_code == 0, result.output
    assert "1 native PSO operation" in result.output
    command = next(
        item for item in command_catalog() if item["name"] == "compile-oiv-recipe"
    )
    assert command["risk"] == "authoring_write"
    assert {item["name"] for item in command["parameters"]} >= {
        "source", "archive", "output", "workspace_root",
    }


def test_oiv_pso_compiler_rebuilds_reparses_and_emits_inert_plan(
    tmp_path, monkeypatch,
):
    _native_round_trip(monkeypatch)
    package = _pso_package(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    original = b"RSC8<Root><Mode>OLD</Mode></Root>"
    service = _FakePsoRpfService(tmp_path, archive, original)
    output = tmp_path / "compiled"

    plan_path, audit_path = OivWorkbench().compile_rpf_recipe_bundle(
        OivWorkbench().inspect(package), archive, output, service=service,
    )

    authored = json.loads(plan_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert authored["status"] == "ready"
    assert len(service.changes) == 1
    payload = Path(service.changes[0]["payload"])
    assert service.changes[0]["action"] == "replace"
    assert etree.fromstring(payload.read_bytes()[4:]).xpath("string(/Root/Mode)") == "NEW"
    assert len(audit["pso_compiles"]) == 1
    native_validation = audit["pso_compiles"][0]["native_build"]["validation"]
    assert native_validation["reparsed"] is True
    assert native_validation["semantic_xml_match"] is True
    assert native_validation["dependency_count"] == 0
    assert native_validation["edited_semantic_xml_sha256"] == (
        native_validation["reparsed_semantic_xml_sha256"]
    )
    assert audit["verification"]["all_pso_sources_decoded_with_game_keys"] is True
    assert audit["verification"]["all_pso_outputs_reparsed"] is True
    assert audit["verification"]["all_pso_outputs_semantically_verified"] is True
    assert audit["archive_writes_performed"] is False
    assert not (output / ".working").exists()
    assert archive.read_bytes() == b"RPF7 canary"


def test_oiv_pso_compiler_rejects_semantic_reparse_drift_and_cleans_output(
    tmp_path, monkeypatch,
):
    _native_round_trip(monkeypatch, semantic_drift=True)
    package = _pso_package(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    service = _FakePsoRpfService(
        tmp_path, archive, b"RSC8<Root><Mode>OLD</Mode></Root>",
    )
    output = tmp_path / "compiled"

    with pytest.raises(RuntimeError, match="semantically match"):
        OivWorkbench().compile_rpf_recipe_bundle(
            OivWorkbench().inspect(package), archive, output, service=service,
        )

    assert not output.exists()
    assert archive.read_bytes() == b"RPF7 canary"


@pytest.mark.parametrize(
    ("assembly", "code"),
    [
        (
            '<archive path="update/update.rpf"><pso path="data.xml">'
            '<remove xpath="/Root/Mode" /></pso></archive>',
            "unsupported_pso",
        ),
        (
            '<archive path="new.rpf" createIfNotExist="true">'
            '<pso path="data.ymt"><remove xpath="/Root/Mode" /></pso></archive>',
            "unsupported_pso",
        ),
    ],
)
def test_oiv_pso_blocks_non_native_and_created_archive_targets(
    tmp_path, assembly, code,
):
    package = tmp_path / "blocked-pso"
    package.mkdir()
    (package / "assembly.xml").write_text(
        f'<package version="2.2"><content>{assembly}</content></package>',
        encoding="utf-8",
    )

    plan = OivWorkbench().inspect(package)

    assert not plan.recipe_supported
    assert code in {item.code for item in plan.findings}
