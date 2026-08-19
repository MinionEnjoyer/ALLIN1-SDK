from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from lxml import etree

from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.oiv_xml import OivXmlEdit, OivXmlMergeEngine


def test_oiv_xml_engine_applies_official_positions_and_case_fallback():
    source = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<Root><Items><Item>ALPHA</Item><Item>BRAVO</Item></Items>"
        b'<Mass value="1"/><RemoveMe/></Root>'
    )
    result = OivXmlMergeEngine.apply(source, (
        OivXmlEdit("add", "/Root/Items", "First", "<Item>FIRST</Item>"),
        OivXmlEdit(
            "add", "/Root/Items/Item[.='alpha']", "After",
            "<Item>AFTER</Item>",
        ),
        OivXmlEdit("add", "/Root/Items", "Last", "<Item>LAST</Item>"),
        OivXmlEdit(
            "add", "/Root/Items/Item[.='BRAVO']", "Before",
            "<Item>BEFORE</Item>",
        ),
        OivXmlEdit("replace", "/Root/Mass", content='<Mass value="2"/>'),
        OivXmlEdit("remove", "/Root/RemoveMe"),
    ), source_name="official.xml")

    root = etree.fromstring(result.data)
    assert root.xpath("/Root/Items/Item/text()") == [
        "FIRST", "ALPHA", "AFTER", "BEFORE", "BRAVO", "LAST",
    ]
    assert root.xpath("string(/Root/Mass/@value)") == "2"
    assert not root.xpath("/Root/RemoveMe")
    assert result.audit["canonical_reparse_verified"] is True
    assert result.audit["edits"][1]["case_insensitive_fallback"] is True
    assert result.audit["source_semantic_sha256"] != result.audit[
        "output_semantic_sha256"
    ]


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ((OivXmlEdit("remove", "/Root/Item"),), "ambiguous"),
        ((OivXmlEdit("remove", "/Root/Missing"),), "matched no elements"),
        ((OivXmlEdit("remove", "/Root | /Root/Item[1]"),), "unions"),
        ((OivXmlEdit("replace", "/Root/Item[1]", content="<A/><B/>"),),
         "exactly one"),
    ],
)
def test_oiv_xml_engine_rejects_ambiguous_missing_or_unbounded_edits(edits, message):
    with pytest.raises(ValueError, match=message):
        OivXmlMergeEngine.apply(
            b"<Root><Item>A</Item><Item>B</Item></Root>", edits,
            source_name="blocked.xml",
        )


def test_oiv_xml_engine_rejects_entities_and_semantic_noops():
    with pytest.raises(ValueError, match="DTD/entity"):
        OivXmlMergeEngine.apply(
            b'<!DOCTYPE Root [<!ENTITY x "bad">]><Root><Item>&x;</Item></Root>',
            (OivXmlEdit("remove", "/Root/Item"),), source_name="entity.xml",
        )
    with pytest.raises(ValueError, match="no semantic change"):
        OivXmlMergeEngine.apply(
            b"<Root><Item>A</Item></Root>",
            (OivXmlEdit("replace", "/Root/Item", content="<Item>A</Item>"),),
            source_name="noop.xml",
        )


def _oiv_xml_package(root: Path, *, mixed_add: bool = False) -> Path:
    package = root / "xml-oiv"
    (package / "content").mkdir(parents=True)
    prefix = (
        '<add source="new.xml">common/data/new.xml</add>' if mixed_add else ""
    )
    target = "common/data/new.xml" if mixed_add else "common/data/dlclist.xml"
    (package / "assembly.xml").write_text(
        f"""<package version="2.2" target="Five"><metadata><name>XML recipe</name>
        </metadata><content><archive path="update/update.rpf">{prefix}
        <xml path="{target}"><add append="Last" xpath="/Root/Items">
        <Item>NEW</Item></add></xml></archive></content></package>""",
        encoding="utf-8",
    )
    (package / "content" / "new.xml").write_text(
        "<Root><Items><Item>BASE</Item></Items></Root>", encoding="utf-8",
    )
    return package


def test_oiv_inspection_marks_official_xml_recipe_as_compile_ready(tmp_path):
    plan = OivWorkbench().inspect(_oiv_xml_package(tmp_path))
    assert plan.recipe_supported
    assert plan.xml_compilable
    assert not plan.translatable
    assert not plan.managed_exportable
    assert plan.xml_operations[0].edits[0] == OivXmlEdit(
        "add", "/Root/Items", "Last", "<Item>NEW</Item>",
    )
    assert "VERIFIED XML COMPILE READY" in plan.to_markdown()
    assert not {item.code for item in plan.findings} & {
        "unsupported_xml", "no_managed_payload",
    }


def test_oiv_xml_inspection_requires_official_21_or_22_format(tmp_path):
    package = _oiv_xml_package(tmp_path)
    assembly = package / "assembly.xml"
    assembly.write_text(
        assembly.read_text(encoding="utf-8").replace('version="2.2"', 'version="3.0"'),
        encoding="utf-8",
    )
    plan = OivWorkbench().inspect(package)
    assert not plan.xml_compilable
    assert "unsupported_oiv_xml_format" in {item.code for item in plan.findings}


class _FakeRpfService:
    def __init__(self, archive: Path, original: bytes | None):
        self.archive = archive
        entries = () if original is None else (
            SimpleNamespace(
                archive_path="", path="common/data/dlclist.xml",
                name="dlclist.xml", kind="binary",
            ),
        )
        self.index_value = SimpleNamespace(
            source=archive, edition="Enhanced", entries=entries,
        )
        self.original = original
        self.changes = None

    def index(self, archive: Path):
        assert archive == self.archive
        return self.index_value

    def extract(self, index, entry, destination: Path):
        assert index is self.index_value and self.original is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.original)
        return destination

    def multi_change_plan(self, index, changes):
        assert index is self.index_value
        self.changes = tuple(changes)
        for item in self.changes:
            if "payload" in item:
                assert Path(item["payload"]).is_file()
        return {
            "schema_version": 2,
            "operation": "rpf_multi_entry_change",
            "status": "ready",
            "archive": str(self.archive),
            "archive_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "changes": list(self.changes),
        }


def test_oiv_xml_compiler_emits_verified_payload_audit_and_inert_plan(tmp_path):
    package = _oiv_xml_package(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    original = b"<Root><Items><Item>BASE</Item></Items></Root>"
    service = _FakeRpfService(archive, original)
    output = tmp_path / "compiled"

    plan_path, audit_path = OivWorkbench().compile_xml_rpf_bundle(
        OivWorkbench().inspect(package), archive, output, service=service,
    )

    authored = json.loads(plan_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert authored["status"] == "ready"
    assert len(service.changes) == 1
    assert service.changes[0]["action"] == "replace"
    payload = Path(service.changes[0]["payload"])
    assert payload.parent == output / "payloads"
    assert etree.fromstring(payload.read_bytes()).xpath(
        "/Root/Items/Item/text()"
    ) == ["BASE", "NEW"]
    assert audit["archive_writes_performed"] is False
    assert audit["verification"] == {
        "all_xml_outputs_reparsed": True,
        "all_xml_outputs_canonical_verified": True,
        "source_archive_hash_bound": True,
        "source_assembly_hash_bound": True,
        "payload_hashes_bound_by_rpf_plan": True,
    }
    assert not (output / ".working").exists()
    assert archive.read_bytes() == b"RPF7 canary"


def test_oiv_xml_compiler_coalesces_add_then_xml_into_one_add(tmp_path):
    package = _oiv_xml_package(tmp_path, mixed_add=True)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    service = _FakeRpfService(archive, None)
    output = tmp_path / "compiled"

    OivWorkbench().compile_xml_rpf_bundle(
        OivWorkbench().inspect(package), archive, output, service=service,
    )

    assert len(service.changes) == 1
    assert service.changes[0]["action"] == "add"
    assert etree.fromstring(Path(service.changes[0]["payload"]).read_bytes()).xpath(
        "/Root/Items/Item/text()"
    ) == ["BASE", "NEW"]


def test_oiv_xml_compiler_requires_matching_archive_and_cleans_failures(tmp_path):
    package = _oiv_xml_package(tmp_path)
    archive = tmp_path / "wrong.rpf"
    archive.write_bytes(b"RPF7")
    output = tmp_path / "compiled"
    with pytest.raises(ValueError, match="does not match"):
        OivWorkbench().compile_xml_rpf_bundle(
            OivWorkbench().inspect(package), archive, output,
            service=_FakeRpfService(archive, b"<Root/>"),
        )
    assert not output.exists()


def test_oiv_xml_compiler_is_available_to_cli_sdk_console_and_agent_api(
    tmp_path, monkeypatch,
):
    package = _oiv_xml_package(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    service = _FakeRpfService(
        archive, b"<Root><Items><Item>BASE</Item></Items></Root>",
    )
    monkeypatch.setattr("allin1_sdk.cli._rpf_service", lambda *_args: service)
    game = tmp_path / "game"
    game.mkdir()
    output = tmp_path / "cli-compiled"

    result = CliRunner().invoke(main, [
        "sdk", "compile-oiv-xml", str(package), str(archive),
        "--gta-path", str(game), "-o", str(output),
    ])

    assert result.exit_code == 0, result.output
    assert "wrote ready inert RPF plan" in result.output
    command = next(
        item for item in command_catalog() if item["name"] == "compile-oiv-xml"
    )
    assert command["risk"] == "authoring_write"
    assert {item["name"] for item in command["parameters"]} >= {
        "source", "archive", "output", "workspace_root",
    }
