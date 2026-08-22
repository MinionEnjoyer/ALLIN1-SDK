import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main
from allin1_sdk.oiv_text import OivTextEdit, OivTextMergeEngine
from allin1_sdk.oiv_workbench import OivWorkbench


def test_text_engine_replays_bounded_official_line_operations():
    source = b"Line 1\r\nLine 3\r\nLine 4 old\r\nLine 5\r\n"
    result = OivTextMergeEngine.apply(source, (
        OivTextEdit("delete", line="Line 3", condition="Equal"),
        OivTextEdit(
            "replace", content="THIS IS NEW LINE", line="Line 4",
            condition="StartWith",
        ),
        OivTextEdit(
            "insert", content="This is last line", line="Line 5",
            condition="Equal", where="After",
        ),
        OivTextEdit(
            "insert", content="This is first line", line="Line 1",
            condition="Equal", where="Before",
        ),
        OivTextEdit("add", content="This line is added"),
    ), source_name="official.dat")

    assert result.data == (
        b"This is first line\r\nLine 1\r\nTHIS IS NEW LINE\r\nLine 5\r\n"
        b"This is last line\r\nThis line is added\r\n"
    )
    assert result.audit["encoding"] == "UTF-8"
    assert result.audit["newline"] == "CRLF"
    assert result.audit["final_newline_preserved"] is True
    assert result.audit["encoding_round_trip_verified"] is True
    assert len(result.audit["edits"]) == 5


@pytest.mark.parametrize(
    ("source", "edit", "message"),
    [
        (
            b"# one\n# two\n",
            OivTextEdit("delete", line="#*", condition="Mask"),
            "Mask selectors remain blocked",
        ),
        (
            b"Mode=one\nMode=two\n",
            OivTextEdit(
                "replace", content="Mode=new", line="Mode=",
                condition="StartWith",
            ),
            "ambiguous",
        ),
        (
            b"one\r\ntwo\n",
            OivTextEdit("add", content="three"),
            "mixed newline",
        ),
    ],
)
def test_text_engine_blocks_wildcards_ambiguous_matches_and_mixed_newlines(
    source, edit, message,
):
    with pytest.raises(ValueError, match=message):
        OivTextMergeEngine.apply(source, (edit,), source_name="blocked.dat")


def test_text_engine_preserves_bom_and_requires_xml_shaped_output_to_reparse():
    source = b"\xef\xbb\xbf<Root>\n  <Mode>OLD</Mode>\n</Root>\n"
    result = OivTextMergeEngine.apply(source, (
        OivTextEdit(
            "replace", content="  <Mode>NEW</Mode>", line="  <Mode>OLD",
            condition="StartWith",
        ),
    ), source_name="config.xml", verify_xml=True)

    assert result.data.startswith(b"\xef\xbb\xbf")
    assert result.audit["bom_preserved"] is True
    assert result.audit["structured_xml_reparse_verified"] is True
    with pytest.raises(ValueError, match="invalid XML"):
        OivTextMergeEngine.apply(source, (
            OivTextEdit(
                "replace", content="  <Mode>", line="  <Mode>OLD",
                condition="StartWith",
            ),
        ), source_name="config.xml", verify_xml=True)


def test_text_engine_preserves_utf16_encoding_and_bom():
    source = b"\xff\xfe" + "Mode=old\r\n".encode("utf-16-le")
    result = OivTextMergeEngine.apply(source, (
        OivTextEdit(
            "replace", content="Mode=new", line="Mode=old", condition="Equal",
        ),
    ), source_name="config.ini")

    assert result.data.startswith(b"\xff\xfe")
    assert result.data[2:].decode("utf-16-le") == "Mode=new\r\n"
    assert result.audit["encoding"] == "UTF-16LE"
    assert result.audit["bom_preserved"] is True


def _text_recipe(root: Path, *, created: bool = False) -> Path:
    package = root / ("created-text-oiv" if created else "text-oiv")
    (package / "content").mkdir(parents=True)
    create = ' createIfNotExist="true"' if created else ""
    prefix = '<add source="config.dat">common/data/config.dat</add>' if created else ""
    (package / "content" / "config.dat").write_bytes(b"Mode=old\r\nKeep=yes\r\n")
    (package / "assembly.xml").write_text(
        f"""<package version="2.2"><content>
        <archive path="update/update.rpf"{create}>{prefix}
          <text path="common/data/config.dat" createIfNotExist="false">
            <replace line="Mode=" condition="StartWith">Mode=better</replace>
            <insert where="After" line="Keep=yes" condition="Equal">Added=yes</insert>
          </text>
        </archive></content></package>""",
        encoding="utf-8",
    )
    return package


def test_inspection_accepts_bounded_text_and_rejects_mask_recipe(tmp_path):
    plan = OivWorkbench().inspect(_text_recipe(tmp_path))

    assert plan.recipe_supported
    assert plan.rpf_recipe_compilable
    assert not plan.xml_compilable
    assert len(plan.text_operations) == 1
    assert "VERIFIED RPF RECIPE COMPILE READY" in plan.to_markdown()

    assembly = plan.source / "assembly.xml"
    assembly.write_text(
        assembly.read_text(encoding="utf-8").replace(
            '<replace line="Mode=" condition="StartWith">Mode=better</replace>',
            '<delete condition="Mask">Mode=*</delete>',
        ),
        encoding="utf-8",
    )
    blocked = OivWorkbench().inspect(plan.source)
    assert not blocked.recipe_supported
    assert "invalid_text_recipe" in {item.code for item in blocked.findings}


class _FakeRpfService:
    def __init__(self, archive: Path, original: bytes | None):
        self.archive = archive
        entries = () if original is None else (
            SimpleNamespace(
                archive_path="", path="common/data/config.dat",
                kind="file", name="config.dat",
            ),
        )
        self.index_value = SimpleNamespace(
            source=archive, entries=entries, edition="enhanced",
        )
        self.original = original
        self.changes = None

    def index(self, archive):
        assert Path(archive) == self.archive
        return self.index_value

    def extract(self, index, entry, destination):
        assert index is self.index_value
        assert entry.path == "common/data/config.dat"
        Path(destination).write_bytes(self.original)

    def multi_change_plan(self, index, changes):
        assert index is self.index_value
        self.changes = changes
        return {
            "status": "ready", "archive_sha256": "a" * 64,
            "changes": changes,
        }


def test_existing_rpf_text_compiler_emits_verified_payload_and_audit(tmp_path):
    recipe = OivWorkbench().inspect(_text_recipe(tmp_path))
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7 canary")
    service = _FakeRpfService(archive, b"Mode=old\r\nKeep=yes\r\n")

    plan_path, audit_path = OivWorkbench().compile_rpf_recipe_bundle(
        recipe, archive, tmp_path / "compiled", service=service,
    )

    assert json.loads(plan_path.read_text(encoding="utf-8"))["status"] == "ready"
    assert len(service.changes) == 1
    payload = Path(service.changes[0]["payload"])
    assert payload.read_bytes() == b"Mode=better\r\nKeep=yes\r\nAdded=yes\r\n"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["operation"] == "oiv_rpf_recipe_compile"
    assert len(audit["text_merges"]) == 1
    assert audit["verification"][
        "all_text_outputs_encoding_round_trip_verified"
    ] is True


def test_created_rpf_text_recipe_is_built_and_audited(tmp_path, monkeypatch):
    recipe = OivWorkbench().inspect(_text_recipe(tmp_path, created=True))
    game = tmp_path / "game"
    game.mkdir()

    class FakeBuilder:
        def __init__(self, project_root, gta_path):
            assert Path(project_root) == tmp_path / "project"
            assert Path(gta_path) == game

        def build(self, loose, output):
            assert (Path(loose) / "common/data/config.dat").read_bytes() == (
                b"Mode=better\r\nKeep=yes\r\nAdded=yes\r\n"
            )
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"RPF7-verified-created-text")
            report = output.with_name(f"{output.name}.validation.json")
            report.write_text("{}", encoding="utf-8")
            return output, report

    monkeypatch.setattr("allin1_sdk.rpf_builder.RpfArchiveBuilder", FakeBuilder)
    destination = tmp_path / "managed-created-text"
    OivWorkbench().export_created_rpf_package(
        recipe, destination, project_root=tmp_path / "project", gta_path=game,
    )

    audit = json.loads(
        (destination / "created-rpf-compile-audit.json").read_text(encoding="utf-8")
    )
    assert [item["kind"] for item in audit["recipe_events"]] == ["add", "text"]
    assert len(audit["text_merges"]) == 1


def test_created_empty_text_must_begin_with_add_line(tmp_path):
    package = tmp_path / "bad-created-text"
    (package / "content").mkdir(parents=True)
    (package / "assembly.xml").write_text(
        """<package version="2.2"><content>
        <archive path="new.rpf" createIfNotExist="true">
          <text path="config.dat" createIfNotExist="true">
            <replace line="Mode=" condition="StartWith">Mode=new</replace>
          </text>
        </archive></content></package>""",
        encoding="utf-8",
    )

    plan = OivWorkbench().inspect(package)

    assert not plan.translatable
    assert "created_text_initial_edit" in {item.code for item in plan.findings}


def test_recipe_compiler_is_discoverable_through_cli_console_and_agent_api(
    tmp_path, monkeypatch,
):
    source = _text_recipe(tmp_path)
    archive = tmp_path / "update.rpf"
    archive.write_bytes(b"RPF7")
    output = tmp_path / "bundle"

    def fake_compile(self, recipe, selected, destination, *, service):
        assert recipe.text_operations
        assert selected == archive
        assert destination == output
        destination.mkdir()
        plan = destination / "rpf-plan.json"
        audit = destination / "compile-audit.json"
        plan.write_text('{"status":"ready"}', encoding="utf-8")
        audit.write_text("{}", encoding="utf-8")
        return plan, audit

    monkeypatch.setattr(OivWorkbench, "compile_rpf_recipe_bundle", fake_compile)
    result = CliRunner().invoke(main, [
        "compile-oiv-recipe", str(source), str(archive), "-o", str(output),
        "--workspace-root", str(tmp_path),
    ])
    assert result.exit_code == 0, result.output
    assert "1 bounded text operation" in result.output
    command = next(
        item for item in command_catalog() if item["name"] == "compile-oiv-recipe"
    )
    assert command["risk"] == "authoring_write"
    assert {item["name"] for item in command["parameters"]} >= {
        "source", "archive", "output", "workspace_root",
    }
