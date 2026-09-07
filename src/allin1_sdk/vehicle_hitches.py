"""Versioned hitch authoring contract shared with the Story Mode workbench.

Offsets are bone-local metres; rotation is degrees (X/Y/Z). This is runtime
configuration, not a YFT skeleton editor or a claim of native front towing.
"""
from copy import deepcopy
import math
import re

IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} requires exactly: {', '.join(fields)}")


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Hitch model/bone identifiers must be lowercase and bounded")


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Hitch number must be finite and between {low} and {high}")


def _vector(value, limit):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Hitch vectors require three numbers")
    for component in value:
        _number(component, -limit, limit)


def validate_hitches(value, model=None):
    """Return a detached canonical document; reject rather than clamp inputs."""
    _object(value, ("schema_version", "vehicle_model", "points"), "Hitch profile")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported hitch profile schema")
    _identifier(value["vehicle_model"])
    if model is not None and value["vehicle_model"] != model.casefold():
        raise ValueError("Hitch profile model does not match its vehicle")
    points = value["points"]
    if not isinstance(points, list) or len(points) > 2:
        raise ValueError("Configure at most one front and one rear hitch")
    used = set()
    native = 0
    for point in points:
        _object(point, ("id", "mode", "bone", "position", "rotation", "coupler_bone",
                        "coupler_offset", "compatible_models", "connect_distance", "break_force"), "Hitch point")
        if point["id"] not in ("front", "rear") or point["id"] in used:
            raise ValueError("Hitch ids must be unique front/rear slots")
        used.add(point["id"])
        if point["mode"] not in ("native", "physical"):
            raise ValueError("Hitch mode must be native or physical")
        _identifier(point["bone"])
        _identifier(point["coupler_bone"])
        _vector(point["position"], 20)
        _vector(point["coupler_offset"], 5)
        _vector(point["rotation"], 180)
        _number(point["connect_distance"], .25, 2)
        _number(point["break_force"], 1000, 100000)
        models = point["compatible_models"]
        if not isinstance(models, list) or not 1 <= len(models) <= 64:
            raise ValueError("List 1–64 compatible trailer models; wildcards are not supported")
        for item in models:
            _identifier(item)
        if len(set(models)) != len(models) or value["vehicle_model"] in models:
            raise ValueError("Duplicate/self trailer models are not supported")
        if point["mode"] == "native":
            native += 1
            if (point["bone"] != "attach_female" or point["coupler_bone"] != "attach_male"
                    or any(point["position"] + point["coupler_offset"] + point["rotation"])):
                raise ValueError("Native towing requires attach_female/attach_male and zero offsets/rotation")
    if native > 1:
        raise ValueError("A vehicle has only one native hitch; use physical mode for a second configured slot")
    return deepcopy(value)


def load_hitches(workspace, model):
    selected = workspace.inspect().model(model).model.casefold()
    configs = workspace.manifest.get("hitch_configurations", {})
    if not isinstance(configs, dict):
        raise ValueError("Invalid hitch configurations")
    return validate_hitches(configs.get(selected, {
        "schema_version": 1, "vehicle_model": selected, "points": [],
    }), selected)


def save_hitches(workspace, model, document, expected_revision):
    if type(expected_revision) is not int or expected_revision != workspace.revision:
        raise ValueError("Vehicle revision changed; review hitches again")
    before = load_hitches(workspace, model)
    after = validate_hitches(document, model)
    if before == after:
        raise ValueError("Hitch configuration has no changes")
    import json
    import shutil
    from allin1_sdk.vehicle_authoring import VehicleAuthoringResult
    changes = ({"field": "hitches.configuration", "before": json.dumps(before, sort_keys=True),
                "after": json.dumps(after, sort_keys=True)},)
    history = workspace._new_history(model, {}, changes, operation="vehicle_hitches", snapshot_manifest=True)
    previous = deepcopy(workspace.manifest)
    try:
        workspace.manifest.setdefault("hitch_configurations", {})[after["vehicle_model"]] = after
        project = workspace.inspect()
        revision = workspace._finish_revision(history, project)
    except Exception:
        workspace.manifest = previous
        workspace._restore_history(history)
        shutil.rmtree(history, ignore_errors=True)
        raise
    return VehicleAuthoringResult(workspace.root, revision, model, changes, history, project, ())
