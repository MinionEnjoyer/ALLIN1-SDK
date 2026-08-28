from __future__ import annotations

import math
import json
from copy import deepcopy

import pytest

from allin1_sdk.map_contract import MapProject, STORY_SAVE_POLICY


def map_payload() -> dict:
    return {
        "schema_version": 1,
        "id": "example.garage-map",
        "package_id": "example.custom-map",
        "name": "Example Garage Map",
        "version": "1.0.0",
        "editions": ["legacy", "enhanced"],
        "streaming": {
            "pack_name": "examplemap",
            "content_group": "EXAMPLE_MAP_GROUP",
            "ipls": ["example_map_placement"],
            "activation_radius": 250,
            "release_radius": 450,
            "keep_resident": False,
        },
        "levels": [{
            "id": "garage-level",
            "name": "Garage level",
            "center": {"x": 10, "y": 20, "z": -50, "heading": -90},
            "ipls": ["example_map_interior"],
        }],
        "portals": [{
            "id": "garage-door",
            "name": "Garage door",
            "mode": "both",
            "from": {
                "level": "world",
                "position": {"x": 100, "y": 200, "z": 30, "heading": 450},
            },
            "to": {
                "level": "garage-level",
                "position": {"x": 12, "y": 20, "z": -50, "heading": 180},
            },
            "radius": 4,
            "one_way": False,
        }],
        "garages": [{
            "id": "storage",
            "name": "Vehicle storage",
            "level_id": "garage-level",
            "entrance_portal_id": "garage-door",
            "capacity": 4,
            "vehicle_types": ["land"],
            "slots": [{
                "id": "slot-1",
                "position": {"x": 15, "y": 20, "z": -50, "heading": 180},
            }],
            "rules": {
                "allow_store": True,
                "allow_retrieve": True,
                "save_policy": STORY_SAVE_POLICY,
            },
        }],
    }


def test_map_contract_normalizes_and_round_trips_every_runtime_surface():
    project = MapProject.from_dict(map_payload())

    assert project.project_id == "example.garage-map"
    assert project.package_id == "example.custom-map"
    assert project.levels[0].center.heading == 270.0
    assert project.portals[0].source.position.heading == 90.0
    assert project.portals[0].mode == "both"
    assert project.garages[0].slots[0].vehicle_types == ("land",)
    assert MapProject.from_dict(project.to_dict()) == project


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["levels"].append(value["levels"][0]), "duplicate level"),
        (
            lambda value: value["portals"][0]["to"].update({"level": "missing"}),
            "unknown level",
        ),
        (
            lambda value: value["garages"][0].update(
                {"entrance_portal_id": "missing-door"},
            ),
            "unknown entrance portal",
        ),
        (
            lambda value: value["garages"][0]["rules"].update(
                {"save_policy": "immediate"},
            ),
            "story_save_only",
        ),
        (
            lambda value: value["levels"][0]["center"].update({"x": math.inf}),
            "finite number",
        ),
    ],
)
def test_map_contract_fails_closed_on_invalid_relationships_and_values(
    mutation, message,
):
    payload = map_payload()
    mutation(payload)
    with pytest.raises(ValueError, match=message):
        MapProject.from_dict(payload)


def test_map_contract_rejects_unknown_fields_and_unsafe_names():
    payload = map_payload()
    payload["surprise"] = True
    with pytest.raises(ValueError, match="Unsupported map project field"):
        MapProject.from_dict(payload)

    payload = map_payload()
    payload["streaming"]["pack_name"] = "../escape"
    with pytest.raises(ValueError, match="pack_name"):
        MapProject.from_dict(payload)


def test_map_contract_matches_story_runtime_limits(tmp_path):
    payload = map_payload()
    payload["schema_version"] = True
    with pytest.raises(ValueError, match="schema_version"):
        MapProject.from_dict(payload)

    payload = map_payload()
    payload["name"] = "Invalid\tMap"
    with pytest.raises(ValueError, match="single line"):
        MapProject.from_dict(payload)

    payload = map_payload()
    payload["levels"][0]["center"]["x"] = 1e100
    with pytest.raises(ValueError, match="32-bit float"):
        MapProject.from_dict(payload)

    payload = map_payload()
    template = payload["levels"][0]
    payload["levels"] = []
    for index in range(65):
        level = deepcopy(template)
        level["id"] = f"level-{index:02d}"
        payload["levels"].append(level)
    payload["portals"][0]["to"]["level"] = "level-00"
    payload["garages"][0]["level_id"] = "level-00"
    with pytest.raises(ValueError, match="at most 64 levels"):
        MapProject.from_dict(payload)

    oversized = tmp_path / "oversized-maps.json"
    oversized.write_text(
        json.dumps(map_payload()) + (" " * (1024 * 1024)), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="1 MiB"):
        MapProject.load(oversized)


def test_map_contract_requires_an_ipl_for_safe_runtime_loading():
    payload = map_payload()
    payload["streaming"]["ipls"] = []
    payload["levels"][0]["ipls"] = []
    with pytest.raises(ValueError, match="at least one project or level IPL"):
        MapProject.from_dict(payload)
