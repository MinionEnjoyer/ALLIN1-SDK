import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk import artifact_identity, package_provenance
from allin1_sdk.artifact_contract import seal, validate_manifest
from allin1_sdk.map_package import MapAddonPackageBuilder
from allin1_sdk.map_contract import MapProject
from allin1_sdk.vehicle_package import VehicleAddonPackageBuilder
from test_vehicle_package import _prebuilt_package
from test_map_package import _prebuilt_map, _use_prebuilt_scan, map_payload


def fixture(tmp_path, monkeypatch, kind):
    helper = tmp_path / "tools/RpfPatcher/RpfPatcher.exe"
    helper.parent.mkdir(parents=True); helper.write_bytes(b"fixture-helper")
    if kind == "map":
        _use_prebuilt_scan(monkeypatch)
        source = _prebuilt_map(tmp_path)
        build = lambda: MapAddonPackageBuilder(tmp_path).build(source, MapProject.from_dict(map_payload()), tmp_path / "out", edition="enhanced")
    else:
        source = _prebuilt_package(tmp_path)
        build = lambda: VehicleAddonPackageBuilder(tmp_path).build(source, tmp_path / "out", editions=("legacy",))
    return source, helper, build


@pytest.mark.parametrize("kind", ["map", "vehicle"])
def test_project_publication_binds_full_source_and_launcher_inventory(tmp_path, monkeypatch, kind):
    source, helper, build = fixture(tmp_path, monkeypatch, kind)
    original = package_provenance.snapshot(source)
    build()
    root = tmp_path / "out"
    artifact = validate_manifest(json.loads((root / "sdk-artifact.json").read_bytes()))
    inputs = json.loads((root / "sdk-inputs.json").read_bytes())
    assert inputs["source_files"] == original == package_provenance.snapshot(source)
    assert artifact["build"]["resource_files"]["tools/RpfPatcher/RpfPatcher.exe"] == hashlib.sha256(helper.read_bytes()).hexdigest()
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "ALLIN1/src"))
    from allin1.mods import ModManifest
    from allin1.sdk_provenance import read
    evidence = read(ModManifest.load(root), artifact["edition"].casefold())
    assert evidence["artifact"]["artifact_id"] == artifact["artifact_id"]


@pytest.mark.parametrize("kind", ["map", "vehicle"])
@pytest.mark.parametrize("drift", ["source", "helper", "sdk"])
def test_project_source_sdk_or_helper_drift_aborts_publication(tmp_path, monkeypatch, kind, drift):
    source, helper, build = fixture(tmp_path, monkeypatch, kind)
    complete = package_provenance.complete
    def mutate(*args, **kwargs):
        if drift == "source":
            (source / "changed.txt").write_text("concurrent change")
        elif drift == "helper":
            helper.write_bytes(b"different helper")
        else:
            identity = artifact_identity.current(resource_root=tmp_path)
            identity.pop("build_fingerprint"); identity["sdk_version"] = "changed"
            changed = seal(identity, "build_fingerprint")
            monkeypatch.setattr(artifact_identity, "current", lambda **kwargs: changed)
        return complete(*args, **kwargs)
    monkeypatch.setattr(package_provenance, "complete", mutate)
    with pytest.raises(ValueError, match="changed during"):
        build()
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out.*-package-*"))
