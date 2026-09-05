"""Desktop boundary coverage around the existing transactional clone domain."""
import pytest
from dataclasses import replace

from allin1_sdk import weapon_desktop
from allin1_sdk.desktop_protocol import DesktopProtocolService, envelope
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from test_weapon_clone_core import _workspace
from test_weapon_desktop import confirmed, tree_hashes


def clone_request(workspace, **overrides):
    spec = {"donor_weapon": "WEAPON_DONOR", "weapon_name": "WEAPON_BUNDLE", "slot": "SLOT_BUNDLE",
            "ammo_info": "AMMO_BUNDLE", "model": "w_pi_bundle", "human_name_hash": "WT_BUNDLE",
            "stat_name": "ST_BUNDLE", "clone_ammo": True, "ammo_name": "AMMO_BUNDLE"}
    spec.update(overrides)
    return {"action": "clone", "workspace": str(workspace.root), "expected_revision": 0, "spec": spec}


@pytest.mark.parametrize("reuse_ammo", [False, True])
def test_clone_review_apply_and_exact_undo(tmp_path, reuse_ammo):
    source, workspace = _workspace(tmp_path)
    original = tree_hashes(source)
    before = tree_hashes(workspace.root)
    payload = clone_request(workspace, **({"clone_ammo": False, "ammo_name": None, "ammo_info": "AMMO_DONOR"} if reuse_ammo else {}))
    review = weapon_desktop.review(payload)
    plan = review["clone_plan"]
    assert review["review_only"] and plan["ready"]
    assert plan["donor_completeness"]["animation_mappings"] == 2
    assert len(plan["reused_components"]) == 2
    assert (any(item["kind"] == "ammo" for item in plan["additions"])) is not reuse_ammo
    assert tree_hashes(workspace.root) == before
    result = weapon_desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["revision"] == 1 and result["selected_weapon"] == "WEAPON_BUNDLE"
    assert result["values"]["values"]["weapon.ammoInfo"] == payload["spec"]["ammo_info"]
    assert len(result["project"]["weapons"]) == 3 and result["can_undo"]
    assert result["workspace_write_performed"] and not result["game_write_performed"]
    assert "UnknownWeapon" in (workspace.source / "weapons.meta").read_text()
    undo_payload = {"action": "undo", "workspace": str(workspace.root), "expected_revision": 1}
    assert weapon_desktop.review(undo_payload)["removed_records"] == plan["additions"]
    undo = weapon_desktop.apply(confirmed(undo_payload))
    assert undo["revision"] == 2 and undo["selected_weapon"] == "WEAPON_DONOR"
    assert len(undo["project"]["weapons"]) == 2
    assert tree_hashes(workspace.source) == original == tree_hashes(source)


@pytest.mark.parametrize("problem", ["collision", "missing_model", "incomplete_donor"])
def test_blocked_plans_are_visible_but_cannot_be_applied(tmp_path, problem):
    _, workspace = _workspace(tmp_path)
    payload = clone_request(workspace)
    if problem == "collision":
        payload["spec"]["weapon_name"] = "WEAPON_DONOR"
    elif problem == "missing_model":
        payload["spec"]["model"] = "missing_model"
    else:
        (workspace.source / "weapon_shop.meta").unlink()
    before = tree_hashes(workspace.root)
    result = weapon_desktop.review(payload)
    assert not result["clone_plan"]["ready"]
    assert result["clone_plan"]["collisions"] or result["clone_plan"]["findings"]
    with pytest.raises(ValueError, match="not ready"):
        weapon_desktop.apply(confirmed(payload))
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("overrides", [
    {"clone_ammo": "true"}, {"weapon_name": True}, {"model": "../escape"},
    {"human_name_hash": "x" * 161}, {"ammo_name": ["AMMO_BUNDLE"]},
    {"extra": "ignored"}, {"ammo_name": None}, {"clone_ammo": False},
])
def test_clone_spec_is_bounded_and_strict(tmp_path, overrides):
    _, workspace = _workspace(tmp_path)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.review(clone_request(workspace, **overrides))
    assert tree_hashes(workspace.root) == before


@pytest.mark.parametrize("tamper", ["spec", "source", "revision", "confirmation"])
def test_clone_rejects_stale_or_unconfirmed_requests(tmp_path, tamper):
    _, workspace = _workspace(tmp_path)
    approved = confirmed(clone_request(workspace))
    if tamper == "spec":
        approved["spec"]["weapon_name"] = "WEAPON_OTHER_TARGET"
    elif tamper == "source":
        path = workspace.source / "weaponanimations.meta"
        path.write_bytes(path.read_bytes().replace(b'clip_default', b'clip_changed'))
    elif tamper == "revision":
        approved["expected_revision"] = 1
    else:
        approved["authoring_confirmed"] = False
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError):
        weapon_desktop.apply(approved)
    assert tree_hashes(workspace.root) == before


def test_clone_domain_rechecks_after_desktop_review(tmp_path, monkeypatch):
    _, workspace = _workspace(tmp_path)
    approved = confirmed(clone_request(workspace))
    original_clone = WeaponAuthoringWorkspace.clone_weapon_bundle
    expected = {}

    def drift_then_clone(self, *args, **kwargs):
        path = self.source / "weaponanimations.meta"
        path.write_bytes(path.read_bytes().replace(b'clip_default', b'clip_changed'))
        expected.update(tree_hashes(self.root))
        return original_clone(self, *args, **kwargs)

    monkeypatch.setattr(WeaponAuthoringWorkspace, "clone_weapon_bundle", drift_then_clone)
    with pytest.raises(ValueError, match="stale"):
        weapon_desktop.apply(approved)
    assert tree_hashes(workspace.root) == expected


def test_clone_review_rejects_evidence_that_would_be_silently_truncated(tmp_path, monkeypatch):
    _, workspace = _workspace(tmp_path)
    payload = clone_request(workspace)
    plan = workspace.plan_weapon_clone(**payload["spec"])
    oversized = replace(plan, additions=plan.additions * 400)
    monkeypatch.setattr(WeaponAuthoringWorkspace, "plan_weapon_clone", lambda *_args, **_kwargs: oversized)
    before = tree_hashes(workspace.root)
    with pytest.raises(ValueError, match="exceeds desktop review limits"):
        weapon_desktop.review(payload)
    assert tree_hashes(workspace.root) == before


def test_clone_uses_existing_protocol_risk_and_confirmation_boundary(tmp_path):
    source, workspace = _workspace(tmp_path)
    service = DesktopProtocolService()
    def call(operation, payload):
        return service.handle(envelope(operation, payload, request_id="clone-test", terminal=False))[0]
    call("handshake", {"client": {"name": "test", "version": "1"}, "supported_versions": ["1.0.0"]})
    payload = clone_request(workspace)
    before = tree_hashes(workspace.root)
    result = call("review_weapon_authoring", payload)
    assert result["risk"] == "read_only" and result["payload"]["result"]["clone_plan"]["ready"]
    denied = call("apply_weapon_authoring", payload)
    assert denied["operation"] == "error" and denied["risk"] == "authoring_write"
    assert tree_hashes(workspace.root) == before
    with pytest.raises(ValueError):
        weapon_desktop.review({**payload, "workspace": str(source)})
    applied = call("apply_weapon_authoring", confirmed(payload))
    assert applied["operation"] == "result" and applied["risk"] == "authoring_write"
    assert applied["payload"]["result"]["selected_weapon"] == "WEAPON_BUNDLE"
