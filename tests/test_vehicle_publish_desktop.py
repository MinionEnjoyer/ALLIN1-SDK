import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from allin1_sdk import desktop_protocol as protocol
from allin1_sdk import vehicle_publish_desktop as desktop
from allin1_sdk.managed_package_conversion import ManagedVehiclePackageConverter
from allin1_sdk.mods import open_mod_package
from test_vehicle_oiv_export import _prepared


@pytest.fixture(params=["legacy", "enhanced"])
def context(tmp_path, request):
    package = _prepared(tmp_path, edition=request.param)
    return {"source_package": str(package), "destination": str(tmp_path / "published.zip"),
            "gta_path": str(tmp_path / f"game-{request.param}")}


def confirmed(payload):
    return {**payload, "review_sha256": desktop.review(payload)["review_sha256"], "authoring_confirmed": True}


def test_review_does_not_write_and_real_zip_retains_the_gbay_catalog(context):
    source = Path(context["source_package"])
    (source / "private-notes.txt").write_text("not for publication")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    review = desktop.review(context)
    assert review["file_write_performed"] is False
    assert not Path(context["destination"]).exists()
    assert len(review["members"]) == 5
    assert review["vehicles"][0]["model"] == "lunga"
    assert not review["traffic_opt_in"]
    result = desktop.apply({**context, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["file_write_performed"] and not result["game_write_performed"] and not result["upload_performed"]
    archive = Path(result["archive"])
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["archive_sha256"]
    with zipfile.ZipFile(archive) as package:
        assert set(package.namelist()) == {row["path"] for row in review["members"]}
        for row in review["members"]:
            assert hashlib.sha256(package.read(row["path"])).hexdigest() == row["sha256"]
    with open_mod_package(archive) as package:
        assert package.editions == (review["edition"],)
        assert package.extension.gbay_catalogs[0].kind == "vehicle"
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert list(Path(context["gta_path"]).iterdir()) == []


@pytest.mark.parametrize("member", ["mod.toml", "allin1.content.json", "allin1.review.json", "payload/dlc.rpf", "payload/vehicles.json"])
def test_changed_prepared_member_invalidates_review(context, member):
    action = confirmed(context)
    path = Path(context["source_package"]) / member
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError): desktop.apply(action)
    assert not Path(context["destination"]).exists()


def test_changed_destination_and_newly_occupied_output_are_rejected(context):
    action = confirmed(context)
    changed = str(Path(context["destination"]).with_name("another.zip"))
    with pytest.raises(ValueError, match="changed after review"):
        desktop.apply({**action, "destination": changed})
    output = Path(context["destination"])
    output.write_bytes(b"someone else's archive")
    with pytest.raises(ValueError, match="already exists"):
        desktop.apply(action)
    assert output.read_bytes() == b"someone else's archive"


@pytest.mark.parametrize("value", [None, False, 1, "true"])
def test_explicit_confirmation_is_strict(context, value):
    with pytest.raises(ValueError, match="explicit action-time"):
        desktop.apply({**confirmed(context), "authoring_confirmed": value})


@pytest.mark.parametrize("target", ["source", "game", "other-game", "missing", "unsafe", "extension"])
def test_destination_boundaries(context, target):
    output = Path(context["destination"])
    if target == "source": output = Path(context["source_package"]) / "publish.zip"
    if target == "game": output = Path(context["gta_path"]) / "publish.zip"
    if target == "other-game":
        other = output.parent / "other-game"
        other.mkdir(); (other / "GTA5.exe").write_bytes(b"fixture")
        output = other / "publish.zip"
    if target == "missing": output = output.parent / "missing" / "publish.zip"
    if target == "unsafe": output = output.with_name("CON.zip")
    if target == "extension": output = output.with_suffix(".exe")
    with pytest.raises((ValueError, OSError)):
        desktop.review({**context, "destination": str(output)})
    assert not output.exists()


def test_unknown_draft_updates_are_not_silently_published(context):
    with pytest.raises(ValueError, match="not unsaved draft"):
        desktop.review({**context, "updates": {"price": 0}})


def test_publisher_rejects_redirection_at_member_level(context, monkeypatch):
    original = Path.is_symlink
    member = Path(context["source_package"]) / "payload"
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == member or original(path))
    with pytest.raises(ValueError, match="reparse|symlink|junction"):
        desktop.review(context)


def test_final_name_is_claimed_exclusively(context, monkeypatch):
    action = confirmed(context)
    output = Path(context["destination"])
    original = os.open
    def racing_open(path, flags, *args, **kwargs):
        if Path(path) == output and flags & os.O_EXCL:
            output.write_bytes(b"concurrent archive")
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(ValueError, match="already exists"):
        desktop.apply(action)
    assert output.read_bytes() == b"concurrent archive"


def test_source_change_during_archive_stream_is_rejected(context, monkeypatch):
    action = confirmed(context)
    original = zipfile.ZipFile.open
    def mutate_before_stream(archive, name, mode="r", *args, **kwargs):
        if mode == "w" and isinstance(name, zipfile.ZipInfo) and name.filename == "allin1.review.json":
            path = Path(context["source_package"]) / name.filename
            path.write_bytes(path.read_bytes() + b" ")
        return original(archive, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", mutate_before_stream)
    with pytest.raises(ValueError, match="changed while reading"):
        desktop.apply(action)
    assert not Path(context["destination"]).exists()


def test_protocol_routes_publication_and_mutation_is_never_a_job(context):
    assert "review_vehicle_package_publish" in protocol.JOB_OPERATIONS
    assert "apply_vehicle_package_publish" not in protocol.JOB_OPERATIONS
    with pytest.raises(protocol.ProtocolError): protocol._operation_risk("apply_vehicle_package_publish", context)
    risk, reviewed = protocol.dispatch_operation("review_vehicle_package_publish", context)
    assert risk == "read_only"
    risk, published = protocol.dispatch_operation("apply_vehicle_package_publish", {**context,
        "review_sha256": reviewed["review_sha256"], "authoring_confirmed": True})
    assert risk == "authoring_write" and published["kind"] == "vehicle_package_published"
