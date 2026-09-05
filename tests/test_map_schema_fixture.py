from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from allin1_sdk.map_contract import MapProject

try:
    from jsonschema import Draft202012Validator
except ImportError:  # The core SDK intentionally keeps jsonschema optional.
    Draft202012Validator = None


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sdk" / "map-project.schema.json"
EXAMPLE = ROOT / "sdk" / "examples" / "custom_garage" / "maps.json"


def test_custom_garage_example_matches_public_schema_and_runtime_contract() -> None:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    if Draft202012Validator is not None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(payload)
        legacy_payload = deepcopy(payload)
        legacy_payload["streaming"].pop("mode")
        validator.validate(legacy_payload)

    project = MapProject.load(EXAMPLE)
    assert project.project_id == "example.harmony-garage"
    assert project.streaming.mode == "ipl"
    assert project.garages[0].capacity == 2
    assert len(project.garages[0].slots) == 2


def test_public_schema_accepts_none_mode_and_rejects_loading_declarations() -> None:
    if Draft202012Validator is None:
        return

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload["streaming"].update({
        "mode": "none",
        "content_group": None,
        "ipls": [],
    })
    for level in payload["levels"]:
        level["ipls"] = []

    validator.validate(payload)

    with_content_group = deepcopy(payload)
    with_content_group["streaming"]["content_group"] = "INVENTED_GROUP"
    assert not validator.is_valid(with_content_group)

    with_streaming_ipl = deepcopy(payload)
    with_streaming_ipl["streaming"]["ipls"] = ["invented_ipl"]
    assert not validator.is_valid(with_streaming_ipl)

    with_level_ipl = deepcopy(payload)
    with_level_ipl["levels"][0]["ipls"] = ["invented_ipl"]
    assert not validator.is_valid(with_level_ipl)
