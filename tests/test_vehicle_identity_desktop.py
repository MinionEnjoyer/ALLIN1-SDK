"""Exact vehicle rename review, linked assets, history, and stale confirmations."""
import json
from pathlib import Path

import pytest

from allin1_sdk import workspace_desktop as desktop
from allin1_sdk.desktop_protocol import dispatch_operation
from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace
from test_vehicle_authoring import _source


@pytest.fixture
def copied(tmp_path):
    source = _source(tmp_path)
    workspace = VehicleAuthoringWorkspace.create(source, tmp_path / "Editable copy")
    return workspace, source


def proposed(workspace, **changes):
    context = {"module": "vehicle_identity", "workspace": str(workspace.root), "model": "authorcar"}
    _, state = dispatch_operation("inspect_authoring_workspace", context)
    request = {**context, "action": "migrate", "new_model": "reactcar", "new_handling": "REACTHAND",
               "expected_revision": state["revision"], "expected_state_sha256": state["state_sha256"], **changes}
    _, plan = dispatch_operation("review_workspace_action", request)
    return plan, {**request, "review_sha256": plan["review_sha256"], "authoring_confirmed": True}


def test_identity_protocol_reviews_exact_files_then_migrates_and_undo_redo(copied):
    workspace, source = copied
    original = desktop._inventory(source)
    copied_before = desktop._inventory(workspace.root)
    plan, request = proposed(workspace)
    assert desktop._inventory(workspace.root) == copied_before
    assert plan["renames"] == [{"before": "stream/authorcar.yft", "after": "stream/reactcar.yft"}, {"before": "stream/authorcar.ytd", "after": "stream/reactcar.ytd"}]
    assert {row["field"] for row in plan["changes"]} == {"identity.modelName", "identity.handlingId"}
    _, result = dispatch_operation("apply_workspace_action", request)
    assert result["vehicle_session"]["selected_model"] == "reactcar"
    assert result["vehicle_session"]["revision"] == 1
    assert (workspace.source / "stream/reactcar.yft").read_bytes() == b"fragment"
    assert not (workspace.source / "stream/authorcar.yft").exists()
    assert VehicleAuthoringWorkspace(workspace.root).inspect().model("reactcar").handling_id == "REACTHAND"
    assert desktop._inventory(source) == original
    workspace = VehicleAuthoringWorkspace(workspace.root)
    workspace.undo()
    assert workspace.inspect().model("authorcar").handling_id == "AUTHORHAND"
    workspace.redo()
    assert workspace.inspect().model("reactcar").handling_id == "REACTHAND"


@pytest.mark.parametrize("change", ["metadata", "asset", "unrelated", "request", "confirmation"])
def test_any_changed_input_rejects_migration_without_new_writes(copied, change):
    workspace, source = copied
    _, request = proposed(workspace)
    if change == "metadata":
        (workspace.source / "vehicles.meta").write_text((workspace.source / "vehicles.meta").read_text().replace("AUTHORHAND", "DIFFERENT"))
    elif change == "asset":
        (workspace.source / "stream/authorcar.yft").write_bytes(b"changed")
    elif change == "unrelated":
        (workspace.root / "note.txt").write_text("new input")
    elif change == "request":
        request["new_model"] = "anothercar"
    else:
        request["authoring_confirmed"] = False
    before = desktop._inventory(workspace.root)
    with pytest.raises(ValueError):
        desktop.apply(request)
    assert desktop._inventory(workspace.root) == before
    assert (source / "stream/authorcar.yft").read_bytes() == b"fragment"


@pytest.mark.parametrize("changes", [{"new_model": "../outside"}, {"new_handling": ""}, {"new_model": "authorcar", "new_handling": "AUTHORHAND"}, {"expected_revision": True}, {"action": "delete"}])
def test_invalid_identity_review_does_not_change_workspace(copied, changes):
    workspace, _ = copied
    before = desktop._inventory(workspace.root)
    with pytest.raises(ValueError):
        proposed(workspace, **changes)
    assert desktop._inventory(workspace.root) == before


def test_identity_collision_rejected_before_any_metadata_rewrite(copied):
    workspace, _ = copied
    (workspace.source / "stream/reactcar.ytd").write_bytes(b"existing canary")
    before = desktop._inventory(workspace.root)
    with pytest.raises(ValueError, match="destination exists"):
        proposed(workspace)
    assert desktop._inventory(workspace.root) == before
