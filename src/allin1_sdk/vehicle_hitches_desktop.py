"""Content-bound hitch review/apply for UI, API and agent clients."""
import json
from allin1_sdk.vehicle_identity_desktop import _context
from allin1_sdk.vehicle_hitches import load_hitches, validate_hitches, save_hitches
from allin1_sdk.workspace_desktop import digest, _inventory


def inspect(payload):
    workspace, model, identity = _context(payload)
    return {"workspace": str(workspace.root), "state_sha256": identity,
            "model": model.model, "revision": workspace.revision,
            "document": load_hitches(workspace, model.model)}


def review(payload):
    if payload.get("action") != "configure":
        raise ValueError("Unknown hitch action")
    workspace, model, identity = _context(payload)
    if payload.get("expected_state_sha256") != identity or type(payload.get("expected_revision")) is not int or payload["expected_revision"] != workspace.revision:
        raise ValueError("Vehicle workspace changed; inspect hitches again")
    before = load_hitches(workspace, model.model)
    document = validate_hitches(payload.get("document"), model.model)
    if before == document:
        raise ValueError("Hitch configuration has no changes")
    if digest(_inventory(workspace.root)) != identity:
        raise ValueError("Vehicle workspace changed during hitch review")
    return {"action": "configure", "state_sha256": identity, "source": str(workspace.root),
            "document": document, "candidate_only": True,
            "changes": [{"before": before, "after": document}],
            "issues": ["Does not modify YFT bones. Named bones must exist at runtime.",
                       "One live connection per towing vehicle. Physical connections are experimental; test both editions."],
            "outputs": [str(workspace.root / "vehicle-authoring.json")]}


def apply(payload):
    workspace, model, identity = _context(payload)
    if identity != payload["expected_state_sha256"]:
        raise ValueError("Vehicle workspace changed before hitch save")
    result = save_hitches(workspace, model.model, payload["document"], payload["expected_revision"])
    from allin1_sdk.desktop_protocol import _vehicle_authoring_snapshot
    return {"vehicle_session": json.loads(json.dumps(_vehicle_authoring_snapshot(workspace, model=result.model))),
            "changes": list(result.changes), "history": str(result.history), "revision": result.revision}
