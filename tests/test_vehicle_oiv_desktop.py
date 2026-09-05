"""Desktop contract uses the real OIV writer with a deterministic scan fixture."""
import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import validate

from allin1_sdk import desktop_protocol as protocol
from allin1_sdk import vehicle_oiv_desktop as desktop
from test_vehicle_oiv_export import _plan, RPF_BYTES


@pytest.fixture
def context(tmp_path, monkeypatch):
    plan = _plan(tmp_path)
    game = tmp_path / "selected-game"
    game.mkdir()

    class Scanner:
        def __init__(self, _project, gta_path):
            self.gta_path = gta_path

        def inspect(self, source, *, preferred_edition):
            assert source == plan.source
            assert preferred_edition == "legacy"
            return source

        def plan(self, _inspection, **identity):
            data = (plan.source / plan.source_member).read_bytes()
            return SimpleNamespace(plan=replace(plan, **{k: v for k, v in identity.items() if v},
                source_member_size=len(data), source_member_sha256=hashlib.sha256(data).hexdigest()))

        def library_destination(self, _plan):
            pytest.fail("Standalone OIV export must not access a Launcher library")

    monkeypatch.setattr("allin1_sdk.vehicle_quick_import.VehicleQuickImportService", Scanner)
    return plan, {"source": str(plan.source), "gta_path": str(game), "edition": "legacy",
                  "author": "Fixture author", "destination": str(tmp_path / "export.oiv"),
                  "package_id": plan.package_id, "name": plan.name, "version": plan.version}


def confirmed(payload):
    return {**payload, "review_sha256": desktop.review(payload)["review_sha256"], "authoring_confirmed": True}


def test_review_and_export_real_oiv_without_launcher(context):
    plan, payload = context
    before = sorted(str(p) for p in plan.source.parent.rglob("*"))
    review = desktop.review(payload)
    assert sorted(str(p) for p in plan.source.parent.rglob("*")) == before
    assert review["review_only"] and not review["file_write_performed"]
    assert review["review_sha256"] == desktop.review(payload)["review_sha256"]
    assert "GBAY catalog" in review["excluded"]
    result = desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["kind"] == "vehicle_oiv_exported"
    assert result["game_write_performed"] is False
    archive = Path(result["archive"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["archive_sha256"]
    with zipfile.ZipFile(archive) as package:
        assert package.namelist() == review["members"]
        assert package.read(review["members"][1]) == RPF_BYTES
    assert not list(Path(payload["gta_path"]).iterdir())


@pytest.mark.parametrize("field,value", [("author", "Changed"), ("package_id", "fixture.changed"),
    ("name", "Changed vehicle"), ("version", "9.0.0"), ("destination", "changed.oiv")])
def test_identity_author_and_output_changes_invalidate_review(context, field, value):
    _plan_value, payload = context
    action = confirmed(payload)
    action[field] = str(Path(payload["destination"]).with_name(value)) if field == "destination" else value
    with pytest.raises(ValueError, match="changed after review"):
        desktop.apply(action)
    assert not Path(action["destination"]).exists()


def test_source_bytes_changed_after_review_are_rejected(context):
    plan, payload = context
    action = confirmed(payload)
    (plan.source / plan.source_member).write_bytes(b"changed payload")
    with pytest.raises(ValueError, match="changed after review"):
        desktop.apply(action)
    assert not Path(payload["destination"]).exists()


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_confirmation_is_strict(context, value):
    _, payload = context
    action = confirmed(payload)
    action["authoring_confirmed"] = value
    with pytest.raises(ValueError, match="explicit action-time"):
        desktop.apply(action)


@pytest.mark.parametrize("change", [{"edition": "enhanced"}, {"edition": "Legacy"}, {"author": ""},
    {"author": "Bad\x00name"}, {"updates": {}}, {"extra": True}, {"destination": "relative.oiv"}])
def test_invalid_or_ambiguous_requests_fail(context, change):
    _, payload = context
    with pytest.raises(ValueError):
        desktop.review({**payload, **change})


@pytest.mark.parametrize("name", ["CON.oiv", "aux.extra.oiv", "bad?.oiv", "bad.zip", " bad.oiv"])
def test_unsafe_output_names_fail(context, name):
    _, payload = context
    payload["destination"] = str(Path(payload["destination"]).with_name(name))
    with pytest.raises(ValueError):
        desktop.review(payload)


@pytest.mark.parametrize("location", ["source", "game", "existing", "missing"])
def test_output_boundaries_and_existing_files(context, location):
    plan, payload = context
    action = confirmed(payload)
    destination = Path(payload["destination"])
    if location == "source": action["destination"] = str(plan.source / "export.oiv")
    if location == "game": action["destination"] = str(Path(payload["gta_path"]) / "export.oiv")
    if location == "missing": action["destination"] = str(destination.parent / "missing" / "export.oiv")
    if location == "existing": destination.write_bytes(b"do not overwrite")
    with pytest.raises((ValueError, OSError)):
        desktop.apply(action)
    if location == "existing": assert destination.read_bytes() == b"do not overwrite"
    else: assert not Path(action["destination"]).exists()


def test_output_inside_another_game_is_blocked(context):
    _, payload = context
    other = Path(payload["destination"]).parent / "other-game"
    other.mkdir()
    (other / "GTA5.exe").write_bytes(b"fixture")
    payload["destination"] = str(other / "export.oiv")
    with pytest.raises(ValueError, match="outside GTA"):
        desktop.review(payload)


def test_redirected_source_is_rejected_before_scanning(context, monkeypatch):
    plan, payload = context
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == plan.source or original(path))
    with pytest.raises(ValueError, match="reparse"):
        desktop.review(payload)


def test_mutation_cannot_run_as_cancellable_job_and_contract_exposes_both(context):
    _, payload = context
    assert "review_vehicle_oiv_export" in protocol.JOB_OPERATIONS
    assert "apply_vehicle_oiv_export" not in protocol.JOB_OPERATIONS
    assert protocol._operation_risk("review_vehicle_oiv_export", payload) == "read_only"
    with pytest.raises(protocol.ProtocolError):
        protocol._operation_risk("apply_vehicle_oiv_export", payload)
    schema = json.loads((Path(__file__).parents[1] / "docs/desktop-protocol-v1.schema.json").read_text())
    for operation in ("review_vehicle_oiv_export", "apply_vehicle_oiv_export"):
        assert operation in protocol.OPERATIONS and operation in protocol.CLIENT_OPERATIONS
        validate(protocol.envelope(operation, payload, request_id="oiv-test", terminal=False), schema)
    risk, review = protocol.dispatch_operation("review_vehicle_oiv_export", payload)
    assert risk == "read_only"
    risk, exported = protocol.dispatch_operation("apply_vehicle_oiv_export", {**payload,
        "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert risk == "authoring_write" and exported["kind"] == "vehicle_oiv_exported"
