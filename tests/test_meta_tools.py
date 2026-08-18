from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.meta_tools import diff_meta, validate_meta_roundtrip


def test_structured_meta_diff_ignores_formatting_and_reports_paths(tmp_path):
    before = tmp_path / "before.meta"
    after = tmp_path / "after.meta"
    before.write_text(
        "<CVehicleModelInfo__InitDataList><InitDatas>"
        "<Item><modelName>testcar</modelName><value value='1'/></Item>"
        "</InitDatas></CVehicleModelInfo__InitDataList>",
        encoding="utf-8",
    )
    after.write_text(
        """<CVehicleModelInfo__InitDataList>
  <InitDatas><Item><modelName>testcar</modelName><value value="2"/>
  <extra>yes</extra></Item></InitDatas>
</CVehicleModelInfo__InitDataList>""",
        encoding="utf-8",
    )
    report = diff_meta(before, after)
    assert report.changed is True
    assert {change.kind for change in report.changes} == {"added", "changed"}
    assert any(change.path.endswith("/@value") for change in report.changes)
    assert any("extra" in change.path for change in report.changes)
    json_path = report.write(tmp_path / "diff.json")
    markdown = report.write(tmp_path / "diff.md")
    assert json.loads(json_path.read_text(encoding="utf-8"))["change_count"] == 2
    assert "| changed |" in markdown.read_text(encoding="utf-8")

    formatted = tmp_path / "formatted.meta"
    formatted.write_text("<root>\n <item value='1'/>\n</root>", encoding="utf-8")
    compact = tmp_path / "compact.meta"
    compact.write_text("<root><item value='1'/></root>", encoding="utf-8")
    assert diff_meta(formatted, compact).changed is False


def test_meta_roundtrip_and_safe_parser_guards(tmp_path):
    source = tmp_path / "handling.meta"
    source.write_text(
        "<?xml version='1.0'?><CHandlingDataMgr><HandlingData>"
        "<Item type='CHandlingData'><handlingName>TEST</handlingName></Item>"
        "</HandlingData></CHandlingDataMgr>", encoding="utf-8",
    )
    serialized = tmp_path / "roundtrip.meta"
    result = validate_meta_roundtrip(source, serialized_output=serialized)
    assert result["semantically_equivalent"] is True
    assert result["element_count"] == 4
    assert serialized.read_bytes().startswith(b"<?xml")

    binary = tmp_path / "binary.ymt"
    binary.write_bytes(b"RSC7\0\0\0")
    with pytest.raises(ValueError, match="Native Asset Viewer"):
        validate_meta_roundtrip(binary)

    dtd = tmp_path / "unsafe.xml"
    dtd.write_text("<!DOCTYPE root [<!ENTITY x 'bad'>]><root>&x;</root>", encoding="utf-8")
    with pytest.raises(ValueError, match="DTD|well-formed"):
        validate_meta_roundtrip(dtd)


def test_meta_cli_diff_and_roundtrip(tmp_path):
    before = tmp_path / "a.xml"
    after = tmp_path / "b.xml"
    before.write_text("<root><value>1</value></root>", encoding="utf-8")
    after.write_text("<root><value>2</value></root>", encoding="utf-8")
    runner = CliRunner()
    diff = runner.invoke(main, [
        "sdk", "diff-meta", str(before), str(after),
        "-o", str(tmp_path / "diff.json"),
    ])
    assert diff.exit_code == 0, diff.output
    assert "1 semantic change" in diff.output
    validated = runner.invoke(main, [
        "validate-meta-roundtrip", str(after),
        "--serialized-output", str(tmp_path / "serialized.xml"),
        "-o", str(tmp_path / "roundtrip.json"),
    ])
    assert validated.exit_code == 0, validated.output
    saved = json.loads((tmp_path / "roundtrip.json").read_text(encoding="utf-8"))
    assert saved["semantically_equivalent"] is True
