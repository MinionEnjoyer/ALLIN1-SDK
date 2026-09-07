"""Exact managed conversion identity survives publication without invented lineage."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from allin1_sdk import artifact_identity
from allin1_sdk.artifact_contract import validate_manifest
from allin1_sdk.managed_package_conversion import ManagedVehiclePackageConverter
from test_managed_package_conversion import _converter, _scan, _source_archive
from test_vehicle_oiv_export import _prepared


@pytest.mark.parametrize("edition", ["legacy", "enhanced"])
def test_conversion_and_republication_preserve_exact_build_and_inventory(tmp_path, edition):
    root = _prepared(tmp_path, edition=edition)
    envelope = (root / "sdk-artifact.json").read_bytes()
    artifact = validate_manifest(json.loads(envelope))
    assert artifact["edition"] == edition.title()
    assert artifact["build"] == artifact_identity.current(resource_root=tmp_path / f"project-{edition}")
    assert artifact["outputs"] == {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in root.rglob("*") if p.is_file() and p.name != "sdk-artifact.json"}
    assert artifact["inputs"]["selected/dlc.rpf"] == artifact["outputs"]["payload/dlc.rpf"]
    converter = ManagedVehiclePackageConverter(tmp_path / "unused-project", tmp_path / f"game-{edition}")
    # Repacking on another SDK does not relabel the conversion's originating build.
    result = converter.publish(root, tmp_path / "published.zip")
    with zipfile.ZipFile(result.archive) as archive:
        assert archive.read("sdk-artifact.json") == envelope
        assert set(archive.namelist()) == set(artifact["outputs"]) | {"sdk-artifact.json"}
    assert converter.review_publication(root)["artifact_identity"]["artifact_id"] == artifact["artifact_id"]


@pytest.mark.parametrize("mutation", ["seal", "payload", "inventory", "edition", "duplicate"])
def test_present_invalid_identity_is_never_downgraded_to_legacy(tmp_path, mutation):
    root = _prepared(tmp_path)
    path = root / "sdk-artifact.json"
    value = json.loads(path.read_bytes())
    if mutation == "payload":
        (root / "allin1.content.json").write_bytes((root / "allin1.content.json").read_bytes() + b" ")
    elif mutation == "duplicate":
        path.write_text(path.read_text().replace('"edition": "Legacy"', '"edition": "Legacy", "edition": "Legacy"'))
    else:
        from allin1_sdk.artifact_contract import seal
        value.pop("artifact_id")
        if mutation == "seal": value["changes_sha256"] = "0" * 64
        if mutation == "inventory": value["outputs"].pop("mod.toml")
        if mutation == "edition": value["edition"] = "Enhanced"
        value = seal(value, "artifact_id")
        if mutation == "seal": value["artifact_id"] = "f" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        ManagedVehiclePackageConverter.review_publication(root)


def test_absent_old_identity_is_explicitly_not_recorded(tmp_path):
    root = _prepared(tmp_path)
    (root / "sdk-artifact.json").unlink()
    review = ManagedVehiclePackageConverter.review_publication(root)
    assert review["artifact_identity"] == {"status": "not_recorded"}
    assert len(review["members"]) == 5


def test_helper_drift_during_conversion_discards_only_staging(tmp_path, monkeypatch):
    source = _source_archive(tmp_path)
    converter = _converter(tmp_path, source, _scan(source))
    plan = converter.plan(source, edition="enhanced")
    original = converter._manifest_text
    def drift(*args):
        (converter.project_root / "tools/RpfPatcher/RpfPatcher.exe").write_bytes(b"changed")
        return original(*args)
    monkeypatch.setattr(converter, "_manifest_text", drift)
    with pytest.raises(ValueError, match="identity changed"):
        converter.export(plan, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output-*"))
    assert source.exists()


def test_launcher_accepts_exported_envelope_and_exact_installed_files(tmp_path, monkeypatch):
    root = _prepared(tmp_path)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "ALLIN1/src"))
    from allin1.mods import ModManifest
    from allin1 import sdk_provenance
    manifest = ModManifest.load(root)
    provenance = sdk_provenance.read(manifest, "legacy")
    # Exercise the same final copied-file comparison used by the installer,
    # without claiming an actual RPF fixture is a deployable game asset.
    records = [{"source": item.source.as_posix(), "destination": item.destination.as_posix(),
                "sha256": hashlib.sha256((root / item.source).read_bytes()).hexdigest()}
               for item in manifest.files]
    result = sdk_provenance.installed(provenance, manifest, records, [])
    assert result["artifact_id"] == provenance["artifact"]["artifact_id"]
    records[0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="differs"):
        sdk_provenance.installed(provenance, manifest, records, [])
