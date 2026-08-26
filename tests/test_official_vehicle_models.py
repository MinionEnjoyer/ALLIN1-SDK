from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from allin1_sdk.official_vehicle_models import (
    OFFICIAL_VEHICLE_HASHES,
    OFFICIAL_VEHICLE_MODEL_HASH_PAIRS,
    OFFICIAL_VEHICLE_MODELS,
    SNAPSHOT_SCHEMA_VERSION,
    SOURCE_FILES,
    SOURCE_VERSION,
)
from allin1_sdk.vehicle_catalog import vehicle_model_hash


def test_official_vehicle_snapshot_matches_sibling_core_catalogs():
    core_root = Path(__file__).resolve().parents[2] / "ALLIN1"
    if not core_root.is_dir():
        pytest.skip("Sibling ALLIN1 launcher checkout is not present")

    online_path = core_root / "data" / "vehicles.toml"
    story_path = core_root / "data" / "story_vehicles.json"
    online = tomllib.loads(online_path.read_text(encoding="utf-8"))
    story = json.loads(story_path.read_text(encoding="utf-8"))
    models = {
        str(vehicle["model"]).casefold() for vehicle in online["vehicles"]
    } | {
        str(vehicle["model"]).casefold() for vehicle in story["vehicles"]
    }

    assert SNAPSHOT_SCHEMA_VERSION == 1
    # SOURCE_VERSION records when the catalog snapshot was generated; it is
    # not the SDK or launcher product version. The source digests and resolved
    # model/hash pairs below are the authoritative freshness checks.
    assert re.fullmatch(r"\d+\.\d+\.\d+", SOURCE_VERSION)
    assert SOURCE_FILES == {
        "data/vehicles.toml": hashlib.sha256(online_path.read_bytes()).hexdigest(),
        "data/story_vehicles.json": hashlib.sha256(story_path.read_bytes()).hexdigest(),
    }
    assert OFFICIAL_VEHICLE_MODELS == models
    assert OFFICIAL_VEHICLE_MODEL_HASH_PAIRS == tuple(
        (model, vehicle_model_hash(model)) for model in sorted(models)
    )
    assert OFFICIAL_VEHICLE_HASHES == {
        vehicle_model_hash(model) for model in models
    }
