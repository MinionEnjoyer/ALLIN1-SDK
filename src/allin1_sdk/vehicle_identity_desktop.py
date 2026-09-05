"""Content-bound vehicle identity migration on an existing editable copy."""
import json

from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
from allin1_sdk.workspace_desktop import path, digest, _inventory


def _context(payload):
    root = path(payload.get("workspace"), writable=True)
    identity = digest(_inventory(root))  # Before constructors resolve paths.
    workspace = VehicleAuthoringWorkspace(root)
    model = payload.get("model")
    if not isinstance(model, str) or not model or len(model) > 96:
        raise ValueError("Choose one bounded vehicle identity")
    selected = workspace.inspect().model(model)
    return workspace, selected, identity


def inspect(payload):
    workspace, model, identity = _context(payload)
    return {"workspace": str(workspace.root), "state_sha256": identity,
            "model": model.model, "handling": model.handling_id, "revision": workspace.revision}


def review(payload):
    if payload.get("action") != "migrate":
        raise ValueError("Unknown vehicle identity action")
    workspace, model, identity = _context(payload)
    if payload.get("expected_state_sha256") != identity or type(payload.get("expected_revision")) is not int or payload["expected_revision"] != workspace.revision:
        raise ValueError("Vehicle workspace changed; inspect the current revision again")
    for key in ("new_model", "new_handling"):
        if not isinstance(payload.get(key), str) or not payload[key].strip() or len(payload[key]) > 96:
            raise ValueError("Both replacement identities must be bounded non-empty identifiers")
    plan = workspace.review_identity(model.model, new_model=payload["new_model"], new_handling=payload["new_handling"])
    if digest(_inventory(workspace.root)) != identity:
        raise ValueError("Vehicle workspace changed during identity review")
    return {"action": "migrate", "source": str(workspace.source), "state_sha256": identity,
            "changes": plan["changes"], "renames": plan["renames"], "document": plan,
            "outputs": [str(workspace.source / item) for item in plan["metadata_sources"]] + [str(workspace.source / item["after"]) for item in plan["renames"]]}


def apply(payload):
    workspace, model, identity = _context(payload)
    if identity != payload["expected_state_sha256"]:
        raise ValueError("Vehicle workspace changed before migration")
    result = workspace.migrate_identity(model.model, new_model=payload["new_model"], new_handling=payload["new_handling"])
    from allin1_sdk.desktop_protocol import _vehicle_authoring_snapshot
    return {"vehicle_session": json.loads(json.dumps(_vehicle_authoring_snapshot(workspace, model=result.model))),
            "changes": list(result.changes), "history": str(result.history), "revision": result.revision}
