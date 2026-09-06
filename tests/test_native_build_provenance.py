"""Exact native build identity and publication guards, not game acceptance."""
import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk import native_assets, artifact_identity
from allin1_sdk.artifact_contract import validate_manifest, digest
from test_rpf_tools import _native_workspace_inspector


def prepare(tmp_path, monkeypatch):
    inspector = _native_workspace_inspector(tmp_path, monkeypatch)
    source = tmp_path / "source.ydd"
    source.write_bytes(b"RSC8-original")
    workspace = inspector.export_workspace(source, tmp_path / "workspace", edition="Enhanced")
    return inspector, workspace


def test_native_receipt_binds_actual_helper_all_inputs_and_output(tmp_path, monkeypatch):
    inspector, workspace = prepare(tmp_path, monkeypatch)
    output, report = inspector.build_workspace(workspace, tmp_path / "new.ydd")
    result = json.loads(report.read_bytes())
    artifact = validate_manifest(result["artifact"])
    assert artifact["edition"] == "Enhanced"
    assert artifact["outputs"] == {output.name: hashlib.sha256(output.read_bytes()).hexdigest()}
    assert artifact["inputs"] == {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in workspace.rglob("*") if p.is_file()}
    assert artifact["build"]["resource_files"]["tools/RpfPatcher/RpfPatcher.exe"] == hashlib.sha256(b"exe").hexdigest()
    assert artifact["validation_reports"] == [digest(result["validation"])]
    assert artifact["build"]["mode"].startswith("development_")
    assert artifact["build"] == artifact_identity.current(resource_root=inspector.project_root)


@pytest.mark.parametrize("changed", ["helper", "xml", "dependency", "original", "manifest", "added_dependency"])
def test_native_refuses_changes_during_rebuild_without_publication(tmp_path, monkeypatch, changed):
    inspector, workspace = prepare(tmp_path, monkeypatch)
    converter = native_assets.run_hidden
    def mutate(args, **kwargs):
        result = converter(args, **kwargs)
        if str(args[1]) == "asset-xml":
            target = {"helper": inspector.patcher, "xml": workspace / "edit/source.ydd.xml",
                      "dependency": workspace / "edit/assets/texture.png", "original": workspace / "original/source.ydd",
                      "manifest": workspace / "native-workspace.json", "added_dependency": workspace / "edit/assets/added.bin"}[changed]
            target.write_bytes(b"<Drawable><Changed/></Drawable>" if changed == "xml" else b"changed after conversion")
        return result
    monkeypatch.setattr(native_assets, "run_hidden", mutate)
    output = tmp_path / "refused.ydd"
    with pytest.raises((ValueError, RuntimeError)):
        inspector.build_workspace(workspace, output)
    assert not output.exists()
    assert not output.with_name(output.name + ".allin1.json").exists()
    assert not list(tmp_path.glob("allin1-native-build-*"))


def test_native_review_refuses_sdk_change_before_build(tmp_path, monkeypatch):
    from allin1_sdk import workspace_desktop as desktop
    from test_native_workspace_desktop import workspace, request
    from test_artifact_identity import build
    from allin1_sdk.artifact_contract import seal
    state = build()
    monkeypatch.setattr(artifact_identity, "current", lambda **kwargs: state)
    context = workspace(tmp_path / "workspace")
    output = tmp_path / "refused.ymt"
    change = request(context, "build", destination=str(output))
    review = desktop.review(change)
    state = seal({**{key:value for key,value in state.items() if key != "build_fingerprint"}, "sdk_version":"changed"}, "build_fingerprint")
    with pytest.raises(ValueError, match="review|Review"):
        desktop.apply({**change, "review_sha256":review["review_sha256"], "authoring_confirmed":True})
    assert not output.exists()
