from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from allin1_sdk import vehicle_catalog as sdk_catalog
from allin1_sdk.official_vehicle_models import OFFICIAL_VEHICLE_MODELS


def _launcher_catalog_module():
    path = Path(__file__).resolve().parents[2] / "ALLIN1" / "src" / "allin1" / "vehicle_catalog.py"
    if not path.is_file():
        pytest.skip("Sibling ALLIN1 launcher checkout is not present")
    spec = importlib.util.spec_from_file_location("launcher_vehicle_catalog_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _catalog(*, traffic: bool = False):
    return {
        "schema_version": 1,
        "id": "example.roadster",
        "name": "Example Roadster",
        "vehicles": [{
            "model": "roadsterx", "name": "Roadster X", "manufacturer": "Example",
            "category": "sports", "price": 125000, "storage": "garage",
            "source_pack": "roadster", "size_tier": 1,
            "preview_dictionary": "roadster_previews", "preview_texture": "roadsterx",
            "traffic": {"enabled": traffic, "weight": 0.75},
        }],
    }


def test_vehicle_catalog_contract_matches_launcher_behavior():
    launcher = _launcher_catalog_module()
    assert sdk_catalog.VEHICLE_CATEGORIES == launcher.VEHICLE_CATEGORIES
    assert sdk_catalog.ROAD_TRAFFIC_CATEGORIES == launcher.ROAD_TRAFFIC_CATEGORIES
    assert sdk_catalog.STORAGE_KINDS == launcher.STORAGE_KINDS
    assert sdk_catalog.VehicleCatalog.from_dict(_catalog()).to_dict() \
        == launcher.VehicleCatalog.from_dict(_catalog()).to_dict()


def test_vehicle_catalog_traffic_is_separate_and_package_owned():
    catalog = sdk_catalog.VehicleCatalog.from_dict(_catalog(traffic=True))
    with pytest.raises(ValueError, match="traffic.catalog"):
        catalog.validate_package_ownership(("roadster",))
    catalog.validate_package_ownership(("roadster",), allow_traffic=True)
    with pytest.raises(ValueError, match="unowned DLC pack"):
        catalog.validate_package_ownership(("different",), allow_traffic=True)


def test_vehicle_catalog_rejects_inert_zones_and_boolean_schema_version():
    zones = _catalog()
    zones["vehicles"][0]["traffic"]["zones"] = ["rich"]
    with pytest.raises(ValueError, match="unsupported fields: zones"):
        sdk_catalog.VehicleCatalog.from_dict(zones)
    boolean_schema = _catalog()
    boolean_schema["schema_version"] = True
    with pytest.raises(ValueError, match="schema_version"):
        sdk_catalog.VehicleCatalog.from_dict(boolean_schema)


def test_vehicle_catalog_rejects_jenkins_collisions_and_reserved_hashes():
    # These distinct safe identifiers have the same lowercase Jenkins hash.
    assert sdk_catalog.vehicle_model_hash("xqe8v7fz") == sdk_catalog.vehicle_model_hash("xc7xaymx")
    duplicate_hash = _catalog()
    duplicate_hash["vehicles"][0]["model"] = "xqe8v7fz"
    second = dict(duplicate_hash["vehicles"][0])
    second["model"] = "xc7xaymx"
    duplicate_hash["vehicles"].append(second)
    with pytest.raises(ValueError, match="duplicate model hashes"):
        sdk_catalog.VehicleCatalog.from_dict(duplicate_hash)

    reserved = _catalog()
    reserved["vehicles"][0]["model"] = "xqe8v7fz"
    catalog = sdk_catalog.VehicleCatalog.from_dict(reserved)
    with pytest.raises(ValueError, match="official GTA model"):
        catalog.validate_package_ownership(("roadster",), reserved_models=("xc7xaymx",))


@pytest.mark.parametrize("model", ("cog552", "xll6c000"))
def test_vehicle_catalog_rejects_official_name_and_hash_collisions(model: str):
    assert "cog552" in OFFICIAL_VEHICLE_MODELS
    assert sdk_catalog.vehicle_model_hash("xll6c000") == sdk_catalog.vehicle_model_hash("cog552")
    payload = _catalog()
    payload["vehicles"][0]["model"] = model
    catalog = sdk_catalog.VehicleCatalog.from_dict(payload)
    with pytest.raises(ValueError, match="official GTA model"):
        catalog.validate_package_ownership(
            ("roadster",), reserved_models=OFFICIAL_VEHICLE_MODELS,
        )


@pytest.mark.parametrize("field,value", [
    ("category", "Sports"),
    ("storage", "warehouse"),
    ("size_tier", 3),
    ("price", -1),
])
def test_vehicle_catalog_strict_limits(field, value):
    payload = _catalog()
    payload["vehicles"][0][field] = value
    if field == "category":
        # Category tokens normalize case exactly like the runtime contract.
        assert sdk_catalog.VehicleCatalog.from_dict(payload).vehicles[0].category == "sports"
    else:
        with pytest.raises(ValueError):
            sdk_catalog.VehicleCatalog.from_dict(payload)
