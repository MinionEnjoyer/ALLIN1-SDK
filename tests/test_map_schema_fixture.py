from __future__ import annotations

import json
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
        Draft202012Validator(schema).validate(payload)

    project = MapProject.load(EXAMPLE)
    assert project.project_id == "example.harmony-garage"
    assert project.garages[0].capacity == 2
    assert len(project.garages[0].slots) == 2
