from __future__ import annotations

import json
from pathlib import Path

import pytest

from allin1_sdk.ped_ymt_inspector import (
    PedYmtInspector,
    classify_ped_ymt_xml,
)
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk import desktop_protocol

try:
    from jsonschema import Draft202012Validator
except ImportError:  # The core SDK intentionally keeps jsonschema optional.
    Draft202012Validator = None


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sdk" / "ped-ymt-report.schema.json"


def _variation_xml(*, owns_cloth: bool = True) -> bytes:
    value = "true" if owns_cloth else "false"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CPedVariationInfo name="mp_m_clothes_01">
  <aComponentData3 itemType="CPVComponentData">
    <Item>
      <aDrawblData3 itemType="CPVDrawblData">
        <Item>
          <aTexData itemType="CPVTextureData"><Item /></aTexData>
          <clothData><ownsCloth value="{value}" /></clothData>
        </Item>
      </aDrawblData3>
    </Item>
  </aComponentData3>
  <aSelectionSets itemType="CPedSelectionSet" />
  <compInfos itemType="CComponentInfo"><Item /></compInfos>
  <propInfo><aPropMetaData itemType="CPedPropMetaData"><Item /></aPropMetaData></propInfo>
  <dlcName>hash_F8457EEC</dlcName>
</CPedVariationInfo>
""".encode()


def test_classifies_variation_from_decoded_content_and_counts_dependencies() -> None:
    facts = classify_ped_ymt_xml(_variation_xml())

    assert facts.root_type == "CPedVariationInfo"
    assert facts.classification == "ped_variation"
    assert facts.identity == "mp_m_clothes_01"
    assert facts.sex == "male"
    assert facts.sex_evidence == ("decoded root identity=mp_m_clothes_01",)
    assert facts.metrics == {
        "component_data_count": 1,
        "drawable_count": 1,
        "texture_record_count": 1,
        "prop_metadata_count": 1,
        "selection_set_count": 0,
        "component_info_count": 1,
        "cloth_owned_drawable_count": 1,
        "dlc_name": "hash_F8457EEC",
    }


def test_classifies_creature_metadata_without_filename_inference() -> None:
    facts = classify_ped_ymt_xml(b"""<CCreatureMetaData>
      <shaderVariableComponents><Item /></shaderVariableComponents>
      <pedPropExpressions><Item /><Item /></pedPropExpressions>
      <pedCompExpressions><Item /></pedCompExpressions>
    </CCreatureMetaData>""")

    assert facts.classification == "creature_metadata"
    assert facts.identity is None
    assert facts.sex == "unknown"
    assert facts.metrics["shader_variable_component_count"] == 1
    assert facts.metrics["ped_prop_expression_count"] == 2
    assert facts.metrics["ped_component_expression_count"] == 1


def test_folder_report_separates_alternatives_dependencies_and_acceptance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "packed.ymt").write_bytes(b"packed")
    (source / "optional.ymt").write_bytes(b"optional")

    def decode(_name: str, data: bytes, _edition: str) -> bytes:
        return _variation_xml(owns_cloth=data == b"packed")

    report = PedYmtInspector(ROOT, decoder=decode).inspect(
        source, edition="legacy",
    )
    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["summary"] == {
        "ymt_definitions": 2,
        "decoded": 2,
        "ped_variation": 2,
        "creature_metadata": 0,
        "other": 0,
        "unknown": 0,
        "relationships": 2,
        "unresolved_relationships": 1,
        "declared_for_registration": 0,
    }
    assert payload["evidence_states"]["archive_structure"]["status"] == "observed"
    assert payload["evidence_states"]["metadata_decoding"]["status"] == "observed"
    assert payload["evidence_states"]["dependency_resolution"]["status"] == "partial"
    assert payload["evidence_states"]["target_runtime_compatibility"]["status"] == "unknown"
    assert payload["evidence_states"]["in_game_acceptance"]["status"] == "not_tested"
    assert {item["resolution"] for item in payload["dependencies"]} == {
        "alternative", "unresolved",
    }
    assert all(item["mount_state"] == "unknown" for item in payload["catalog"])

    if Draft202012Validator is not None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)


def test_failed_decode_does_not_infer_identity_or_sex_from_filename(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mp_m_freemode_01_male.ymt"
    source.write_bytes(b"not metadata")

    def fail(_name: str, _data: bytes, _edition: str) -> bytes:
        raise ValueError("unsupported fixture")

    report = PedYmtInspector(ROOT, decoder=fail).inspect(
        source, edition="Enhanced",
    )
    entry = report.catalog[0]

    assert entry.decode_status == "unsupported"
    assert entry.classification == "unknown"
    assert entry.identity is None
    assert entry.sex == "unknown"
    assert report.evidence_states["metadata_decoding"].status == "unsupported"
    assert report.evidence_states["dependency_resolution"].status == "unsupported"


def test_codewalker_xml_is_inspected_without_invoking_binary_decoder(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.ymt.xml"
    source.write_bytes(_variation_xml(owns_cloth=False))

    def must_not_decode(_name: str, _data: bytes, _edition: str) -> bytes:
        raise AssertionError("decoded XML must not use the binary decoder")

    report = PedYmtInspector(ROOT, decoder=must_not_decode).inspect(
        source, edition="Legacy",
    )

    assert report.source["kind"] == "ymt_xml"
    assert report.catalog[0].format == "codewalker_xml"
    assert report.catalog[0].decode_status == "decoded"
    assert report.evidence_states["dependency_resolution"].status == "partial"


def test_decoded_xml_rejects_entity_declarations() -> None:
    with pytest.raises(ValueError, match="DTD/entity"):
        classify_ped_ymt_xml(
            b" " * 5000
            + b'<!DOCTYPE x [<!ENTITY y "value">]><CPedVariationInfo name="&y;" />'
        )


def test_empty_source_does_not_report_decoding_or_dependencies_as_observed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty-package"
    source.mkdir()

    report = PedYmtInspector(ROOT, decoder=lambda *_: b"").inspect(
        source, edition="Legacy",
    )

    assert report.summary["ymt_definitions"] == 0
    assert report.evidence_states["metadata_decoding"].status == "unsupported"
    assert report.evidence_states["dependency_resolution"].status == "unsupported"
    assert any(item.code == "no_ymt_definitions" for item in report.findings)


def test_cli_operation_is_exposed_to_typed_agents_as_read_only() -> None:
    catalog = {item["name"]: item for item in command_catalog()}

    assert catalog["inspect-ped-ymt"]["risk"] == "read_only"
    assert [item["name"] for item in catalog["inspect-ped-ymt"]["parameters"]] == [
        "source", "edition", "gta_path", "output",
    ]


def test_typed_agent_executes_the_same_read_only_report(tmp_path: Path) -> None:
    source = tmp_path / "sample.ymt.xml"
    source.write_bytes(_variation_xml(owns_cloth=False))

    response = execute_request({
        "id": "ped-ymt",
        "action": "execute",
        "command": "inspect-ped-ymt",
        "args": [str(source), "--edition", "legacy"],
    }, audit_path=tmp_path / "audit.jsonl")

    assert response["ok"] is True
    assert response["risk"] == "read_only"
    payload = json.loads(response["result"]["output"])
    assert payload["operation"] == "inspect_ped_ymt"
    assert payload["summary"]["ped_variation"] == 1
    assert payload["evidence_states"]["in_game_acceptance"]["status"] == "not_tested"


def test_desktop_protocol_exposes_the_same_report_as_a_read_only_job(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.ymt.xml"
    source.write_bytes(_variation_xml(owns_cloth=False))

    assert "inspect_ped_ymt" in desktop_protocol.CLIENT_OPERATIONS
    assert "inspect_ped_ymt" in desktop_protocol.JOB_OPERATIONS
    risk, payload = desktop_protocol.dispatch_operation("inspect_ped_ymt", {
        "source": str(source), "edition": "legacy",
    })

    assert risk == "read_only"
    assert payload["operation"] == "inspect_ped_ymt"
    assert payload["summary"]["ped_variation"] == 1
