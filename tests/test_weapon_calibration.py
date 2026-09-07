from copy import deepcopy
import json
import hashlib

import pytest
from PIL import Image
from click.testing import CliRunner

from allin1_sdk import weapon_calibration as calibration, weapon_desktop
from allin1_sdk.cli import main
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from test_weapon_camera import camera_workspace
from test_weapon_desktop import confirmed, tree_hashes

FIELD = "weapon.firstPersonLTOffset.x"


def capture(ws, picture, sid="first", error=10, profile="iron"):
    ws = WeaponAuthoringWorkspace(ws.root)
    return {"action": "calibration_session", "workspace": str(ws.root), "expected_revision": ws.revision,
            "weapon": "WEAPON_AUTHOR", "session": {"id": sid, "captured_at": "2026-09-07T10:00:00Z",
            "profile": profile, "edition": "Enhanced", "screenshot": str(picture),
            "screenshot_sha256": hashlib.sha256(picture.read_bytes()).hexdigest(),
            "conditions": {"camera": "first_person_aim", "fov_degrees": 30, "fov_axis": "vertical",
                           "distance_m": 10, "pose": "standing stationary", "attachments": []},
            "aim": [50, 50], "sight": [50 + error, 50], "impacts": [[51, 50], [49, 52]],
            "checks": {key: "pass" for key in calibration.CHECKS}, "accepted": False}}


@pytest.fixture
def setup(tmp_path):
    _, ws = camera_workspace(tmp_path)
    picture = tmp_path / "frame.png"
    Image.new("RGB", (100, 100), "gray").save(picture)
    return ws, picture


def record(payload):
    return weapon_desktop.apply(confirmed(payload))["session"]


def trial(setup):
    ws, picture = setup
    first = record(capture(ws, picture))
    weapon_desktop.apply(confirmed({"workspace": str(ws.root), "action": "edit", "weapon": "WEAPON_AUTHOR",
        "expected_revision": ws.revision, "updates": {FIELD: "0.01000"}}))
    second = record(capture(ws, picture, "trial", 2))
    ws.__init__(ws.root)
    evidence = {"workspace": str(ws.root), "baseline": "first", "trial": "trial", "field": FIELD,
                "axis": "x", "controlled_trial_confirmed": True}
    return first, second, evidence


def test_record_is_immutable_measurements_separate_and_no_game_claim(setup):
    ws, picture = setup
    before = tree_hashes(ws.source)
    payload = capture(ws, picture)
    reviewed = confirmed(payload)
    assert not (ws.root / calibration.STORE).exists()
    result = weapon_desktop.apply(reviewed)["session"]
    assert result["measurements"]["visual_error_px"] == [10, 0]
    assert result["measurements"]["impact_centroid_error_px"] == [0, 1]
    assert result["runtime_asset_use"] == "not_proven"
    assert tree_hashes(ws.source) == before and ws.revision == 0
    assert calibration.read(ws, "first") == result
    with pytest.raises(ValueError, match="already exists"):
        weapon_desktop.review(payload)
    assert len(calibration.inspect({"workspace": str(ws.root), "weapon": "WEAPON_AUTHOR"})["sessions"]) == 1


def test_stale_screenshot_review_rejected(setup):
    ws, picture = setup
    reviewed = confirmed(capture(ws, picture))
    Image.new("RGB", (100, 100), "red").save(picture)
    with pytest.raises(ValueError, match="marked preview"):
        weapon_desktop.apply(reviewed)
    assert not (ws.root / calibration.STORE).exists()


def test_proposal_reuses_review_and_exact_undo(setup):
    ws, _ = setup
    first, second, evidence = trial(setup)
    assert first["package_invariant_sha256"] == second["package_invariant_sha256"]
    proposed = calibration.proposal(evidence)
    assert proposed["updates"] == {FIELD: "0.01250"}
    assert proposed["impact_correction"] == "not_inferred"
    before = tree_hashes(ws.source)
    request = {"action": "edit", "workspace": str(ws.root), "weapon": "WEAPON_AUTHOR",
               "expected_revision": ws.revision, "updates": proposed["updates"], "calibration_evidence": evidence}
    review = confirmed(request)
    assert review["review_sha256"] and tree_hashes(ws.source) == before
    weapon_desktop.apply(review)
    weapon_desktop.apply(confirmed({"action": "undo", "workspace": str(ws.root), "expected_revision": ws.revision + 1}))
    assert tree_hashes(ws.source) == before
    assert calibration.read(ws, "first") == first


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p["session"].update(aim=[-1, 0]), "X"),
    (lambda p: p["session"]["conditions"].update(fov_degrees=float("nan")), "FOV"),
    (lambda p: p["session"].update(profile="both"), "independent"),
    (lambda p: p["session"].update(id="../escape"), "session ID"),
    (lambda p: p["session"].update(checks={}), "every repeatable check"),
])
def test_bad_observations_never_write(setup, mutation, match):
    ws, picture = setup
    payload = capture(ws, picture)
    mutation(payload)
    with pytest.raises(ValueError, match=match):
        weapon_desktop.review(payload)
    assert not (ws.root / calibration.STORE).exists()


def test_accepted_reference_and_profiles_protected(setup):
    ws, picture = setup
    request = capture(ws, picture)
    request["session"].update(accepted=True)
    first = record(request)
    second = record(capture(ws, picture, "scope", profile="scope"))
    with pytest.raises(ValueError, match="profile"):
        calibration.compare(first, second)
    request = capture(ws, picture, "bad")
    request["session"]["accepted"] = True
    request["session"]["checks"]["reload"] = "not_tested"
    with pytest.raises(ValueError, match="all checks"):
        weapon_desktop.review(request)


def test_proposals_reject_other_fields_stale_images_and_modified_evidence(setup):
    ws, _ = setup
    _, _, evidence = trial(setup)
    with pytest.raises(ValueError, match="independent sight profile"):
        calibration.proposal({**evidence, "field": "weapon.firstPersonScopeOffset.z"})
    proposed = calibration.proposal(evidence)
    request = {"action": "edit", "workspace": str(ws.root), "weapon": "WEAPON_AUTHOR",
               "expected_revision": ws.revision, "updates": proposed["updates"], "calibration_evidence": evidence}
    approved = confirmed(request)
    screenshot = ws.root / calibration.STORE / "trial" / "screenshot.png"
    Image.new("RGB", (100, 100), "red").save(screenshot, format="PNG")
    with pytest.raises(ValueError, match="screenshot changed"):
        weapon_desktop.apply(approved)


def test_conditions_and_resolution_must_match(setup):
    ws, picture = setup
    first = record(capture(ws, picture))
    second = deepcopy(first)
    second["conditions"]["fov_degrees"] = 60
    with pytest.raises(ValueError, match="conditions"):
        calibration.compare(first, second)


def test_cli_api_parity(setup):
    ws, picture = setup
    payload = capture(ws, picture)
    runner = CliRunner()
    review = runner.invoke(main, ["review-weapon-calibration", "--payload", json.dumps(payload)])
    assert review.exit_code == 0, review.output
    assert json.loads(review.output) == weapon_desktop.review(payload)
    payload.update(review_sha256=json.loads(review.output)["review_sha256"], authoring_confirmed=True)
    applied = runner.invoke(main, ["apply-weapon-calibration", "--payload", json.dumps(payload)])
    assert applied.exit_code == 0, applied.output
    listed = runner.invoke(main, ["inspect-weapon-calibration", "--payload", json.dumps({"workspace": str(ws.root), "weapon": "WEAPON_AUTHOR"})])
    assert listed.exit_code == 0 and len(json.loads(listed.output)["sessions"]) == 1


def test_diagnostic_build_bytes_are_rechecked_at_session_save(setup, tmp_path):
    from pathlib import Path
    from test_diagnostic_trail import fixture
    from allin1_sdk.artifact_identity import manifest
    ws, picture = setup
    diagnostic, artifact, receipt = fixture(tmp_path)
    artifact = manifest(artifact["build"], artifact["inputs"], artifact["outputs"], edition="Enhanced")
    receipt["sdk_provenance"].update(artifact=artifact, artifact_id=artifact["artifact_id"])
    Path(diagnostic["source"]).write_text(json.dumps(artifact))
    Path(diagnostic["comparison"]).write_text(json.dumps(receipt))
    payload = {**capture(ws, picture), "diagnostic": diagnostic}
    approved = confirmed(payload)
    provenance = weapon_desktop.review(payload)["session"]["diagnostic"]
    assert provenance["artifact_id"] == artifact["artifact_id"]
    assert provenance["files"][0]["status"] == "match"
    (Path(diagnostic["gta_path"]) / "scripts/owned.asi").write_bytes(b"stale install")
    with pytest.raises(ValueError, match="changed after review"):
        weapon_desktop.apply(approved)
    assert not (ws.root / calibration.STORE).exists()


def test_trial_with_another_asset_change_is_not_a_calibration_response(setup):
    ws, picture = setup
    record(capture(ws, picture))
    weapon_desktop.apply(confirmed({"workspace": str(ws.root), "action": "edit", "weapon": "WEAPON_AUTHOR",
        "expected_revision": 0, "updates": {FIELD: "0.01"}}))
    (ws.source / "unrelated.bin").write_bytes(b"also changed")
    record(capture(ws, picture, "trial", 2))
    with pytest.raises(ValueError, match="Other package content changed"):
        calibration.proposal({"workspace": str(ws.root), "baseline": "first", "trial": "trial", "field": FIELD,
                              "axis": "x", "controlled_trial_confirmed": True})


def test_noisy_response_and_large_extrapolation_are_blocked(setup):
    ws, picture = setup
    record(capture(ws, picture))
    weapon_desktop.apply(confirmed({"workspace": str(ws.root), "action": "edit", "weapon": "WEAPON_AUTHOR",
        "expected_revision": 0, "updates": {FIELD: "0.01"}}))
    evidence = {"workspace": str(ws.root), "baseline": "first", "field": FIELD, "axis": "x", "controlled_trial_confirmed": True}
    record(capture(ws, picture, "noise", 9))
    with pytest.raises(ValueError, match="under two pixels"):
        calibration.proposal({**evidence, "trial": "noise"})
    record(capture(ws, picture, "large", 7))
    with pytest.raises(ValueError, match="outside the measured step"):
        calibration.proposal({**evidence, "trial": "large"})


def test_mutation_command_is_not_available_to_read_only_agent_calls():
    from allin1_sdk.agent_api import command_catalog
    risks = {row["name"]: row["risk"] for row in command_catalog()}
    assert risks["inspect-weapon-calibration"] == "read_only"
    assert risks["review-weapon-calibration"] == "read_only"
    assert risks["apply-weapon-calibration"] == "authoring_write"


def test_desktop_protocol_uses_same_review_and_recording(setup):
    from allin1_sdk.desktop_protocol import DesktopProtocolService
    from test_desktop_protocol import handshake, request
    ws, picture = setup
    payload = capture(ws, picture)
    service = DesktopProtocolService()
    handshake(service)
    reviewed = service.handle(request("review_weapon_authoring", payload, "cal-review"))[0]
    assert reviewed["operation"] == "result", reviewed
    assert reviewed["risk"] == "read_only"
    assert reviewed["payload"]["result"] == weapon_desktop.review(payload)
    applied = service.handle(request("apply_weapon_authoring", {
        **payload, "review_sha256": reviewed["payload"]["result"]["review_sha256"], "authoring_confirmed": True,
    }, "cal-save"))[0]
    assert applied["risk"] == "authoring_write"
    assert applied["payload"]["result"]["kind"] == "weapon_calibration_saved"
    listed = service.handle(request("inspect_weapon_workbench", {
        "workspace": str(ws.root), "weapon": "WEAPON_AUTHOR", "calibration_action": "list",
    }, "cal-list"))[0]
    assert len(listed["payload"]["result"]["sessions"]) == 1
