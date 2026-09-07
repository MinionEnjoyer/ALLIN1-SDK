from copy import deepcopy
import json
import pytest
from allin1_sdk.vehicle_hitches import validate_hitches, load_hitches, save_hitches
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
from allin1_sdk.vehicle_catalog import VehicleCatalogEntry
from allin1_sdk import workspace_desktop as desktop
from allin1_sdk.automation import authoring_catalog
from test_vehicle_authoring import _source


def profile(model="authorcar", mode="native"):
    return {"schema_version": 1, "vehicle_model": model, "points": [{"id": "rear", "mode": mode,
        "bone": "attach_female", "position": [0, 0, 0], "rotation": [0, 0, 0], "coupler_bone": "attach_male",
        "coupler_offset": [0, 0, 0], "compatible_models": ["trailers"], "connect_distance": 1, "break_force": 10000}]}


@pytest.mark.parametrize("field,value", [("position", [0, 1, 0]), ("mode", "rigid"), ("bone", "../chassis"),
    ("connect_distance", 3), ("connect_distance", True), ("break_force", float("nan")), ("rotation", [0, 0]),
    ("compatible_models", []), ("compatible_models", ["*"]), ("compatible_models", ["authorcar"]), ("compatible_models", ["trailers", "trailers"])])
def test_rejects_unsafe_profiles(field, value):
    document = profile(); document["points"][0][field] = value
    with pytest.raises(ValueError): validate_hitches(document)


def test_native_and_physical_contract():
    doc = profile(); front = deepcopy(doc["points"][0]); front.update(id="front", mode="physical", bone="chassis", position=[0, 2.5, .5])
    doc["points"].append(front)
    detached = validate_hitches(doc); detached["points"][0]["compatible_models"].append("other")
    assert doc["points"][0]["compatible_models"] == ["trailers"]
    front["mode"] = "native"
    with pytest.raises(ValueError): validate_hitches(doc)


def proposed(ws, document=None):
    context = {"module": "vehicle_hitches", "workspace": str(ws.root), "model": "authorcar"}
    state = desktop.inspect(context)
    request = {**context, "action": "configure", "document": document or profile(), "expected_revision": state["revision"], "expected_state_sha256": state["state_sha256"]}
    review = desktop.review(request)
    return {**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


def test_review_save_undo_export_identity(tmp_path):
    source = _source(tmp_path); ws = VehicleAuthoringWorkspace.create(source, tmp_path / "copy")
    before = desktop._inventory(ws.root); original = desktop._inventory(source)
    request = proposed(ws)
    assert desktop._inventory(ws.root) == before
    saved = desktop.apply(request)
    assert saved["vehicle_session"]["revision"] == 1
    ws = VehicleAuthoringWorkspace(ws.root)
    assert load_hitches(ws, "authorcar") == profile()
    entry = ws.distribution_catalog("vehicle.authorcar", "Car", "authorcar").vehicles[0]
    assert VehicleCatalogEntry.from_dict(entry.to_dict(), 1).hitches == profile()
    ws.undo(); assert load_hitches(ws, "authorcar")["points"] == []
    ws.redo(); assert load_hitches(ws, "authorcar") == profile()
    ws.migrate_identity("authorcar", new_model="nextcar", new_handling="NEXTHAND")
    assert load_hitches(ws, "nextcar")["vehicle_model"] == "nextcar"
    ws.undo(); assert load_hitches(ws, "authorcar") == profile()
    assert desktop._inventory(source) == original


@pytest.mark.parametrize("change", ["document", "source", "confirmation", "revision"])
def test_stale_review_does_not_write(tmp_path, change):
    ws = VehicleAuthoringWorkspace.create(_source(tmp_path), tmp_path / "copy")
    request = proposed(ws)
    if change == "document": request["document"]["points"][0]["connect_distance"] = .5
    elif change == "source": (ws.source / "stream/authorcar.yft").write_bytes(b"changed")
    elif change == "confirmation": request["authoring_confirmed"] = False
    else: request["expected_revision"] = True
    before = desktop._inventory(ws.root)
    with pytest.raises(ValueError): desktop.apply(request)
    assert desktop._inventory(ws.root) == before


def test_hitch_module_is_discoverable():
    assert any(m["module"] == "vehicle_hitches" for m in authoring_catalog()["modules"])
