import json
import zipfile
from pathlib import Path

import pytest

from allin1_sdk import artifact_identity, runtime_provenance, story_axle_runtime_builder as builder
from allin1_sdk.artifact_contract import seal, validate_manifest
from test_story_axle_runtime_builder import test_candidate_happy_path_stages_both_editions_and_custom_paths as _candidate


def test_sdk_drift_before_compile_is_rejected(tmp_path):
    request = builder.StoryAxleRuntimeBuildRequest(output_directory=tmp_path / "candidate", targets=("story-legacy",))
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="SDK build changed"):
        builder.build_story_axle_runtime_candidate(request, expected_sdk_build={"stale": True})
    assert not request.output_directory.exists()


@pytest.mark.parametrize("drift", ["sdk", "source", "toolchain"])
def test_late_build_drift_never_publishes_candidate(tmp_path, monkeypatch, drift):
    original = runtime_provenance.write_edition
    def change(*args):
        value = original(*args)
        if drift == "sdk":
            identity = artifact_identity.current()
            identity.pop("build_fingerprint")
            identity["sdk_version"] = "test-drift"
            changed = seal(identity, "build_fingerprint")
            monkeypatch.setattr(artifact_identity, "current", lambda **kwargs: changed)
        elif drift == "source":
            monkeypatch.setattr(runtime_provenance, "source_identity", lambda *args: {"changed": "0" * 64})
        else:
            def reject(*args, **kwargs):
                raise builder.StoryAxleRuntimeBuildError("Selected compiler changed after preflight")
            monkeypatch.setattr(builder, "_verify_preflight_selection", reject)
        return value
    monkeypatch.setattr(runtime_provenance, "write_edition", change)
    with pytest.raises(builder.StoryAxleRuntimeBuildError, match="changed"):
        _candidate(monkeypatch, tmp_path)
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".candidate.native-axle-*"))


@pytest.mark.parametrize("custom", [False, True])
def test_runtime_envelope_matches_launcher_without_broadening_destination_policy(tmp_path, monkeypatch, custom):
    root = tmp_path / "candidate"; root.mkdir()
    (root / "VehicleWorkbenchAxles.asi").write_bytes(b"test-only-controller")
    (root / "VehicleWorkbenchAxles.Settings.exe").write_bytes(b"test-only-helper")
    (root / "build-validation-receipt.json").write_text('{"game_acceptance":"not-tested"}')
    config = root / ("custom/config.json" if custom else "VehicleWorkbenchAxles/runtime.json")
    config.parent.mkdir(); config.write_text("{}")
    build = artifact_identity.current()
    artifact = runtime_provenance.write_edition(root, build, {"input": "a"*64}, "Legacy", "4.5.0")
    validate_manifest(artifact)
    publication = json.loads((root / "sdk-publication.json").read_bytes())
    assert publication["installable_allin1_package"] is not custom
    assert (root / "mod.toml").exists() is not custom
    assert config.read_text() == "{}"
    if not custom:
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "ALLIN1/src"))
        from allin1.mods import ModManifest
        from allin1.sdk_provenance import read
        recorded = read(ModManifest.load(root), "legacy")
        assert recorded["artifact"]["artifact_id"] == artifact["artifact_id"]
        assert "VehicleWorkbenchAxles.Settings.exe" in artifact["outputs"]


@pytest.mark.parametrize("target", ["staging", "archive", "archive_comment"])
def test_changed_published_bytes_do_not_escape_final_readback(tmp_path, monkeypatch, target):
    if target == "staging":
        original = runtime_provenance.write_edition
        def mutate(root, *args):
            result = original(root, *args)
            (root / "VehicleWorkbenchAxles.asi").write_bytes(b"changed after validation")
            return result
        monkeypatch.setattr(runtime_provenance, "write_edition", mutate)
    else:
        original = builder._zip_directory
        def mutate(root, archive, **kwargs):
            checksum = original(root, archive, **kwargs)
            with zipfile.ZipFile(archive, "a") as output:
                if target == "archive_comment":
                    output.comment = b"changed container without changing members"
                else:
                    output.writestr("unexpected.txt", "not in artifact")
            return checksum
        monkeypatch.setattr(builder, "_zip_directory", mutate)
    with pytest.raises((ValueError, builder.StoryAxleRuntimeBuildError), match="changed|inventory differs"):
        _candidate(monkeypatch, tmp_path)
    assert not (tmp_path / "candidate").exists()
    assert not list(tmp_path.glob(".candidate.native-axle-*"))
