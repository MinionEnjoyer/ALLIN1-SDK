from copy import deepcopy
from pathlib import Path

import pytest

from allin1_sdk import artifact_contract as contract, artifact_identity as identity


def build(**_kwargs):
    return contract.seal({"schema_version":1,"kind":"sdk_execution_identity","sdk_version":"test",
        "mode":"development_dirty","source":{"source_tree_sha256":"b"*64},
        "resource_files":{"tools/RpfPatcher/RpfPatcher.exe":"c"*64},"executable_sha256":"d"*64},"build_fingerprint")


def test_artifact_identity_binds_build_inputs_outputs_reports_changes_and_edition():
    original = identity.manifest(build(), {"source.ydr":"1"*64}, {"output.ydr":"2"*64}, edition="Legacy", reports=["3"*64], changes={"texture":"DXT5"})
    assert contract.validate_manifest(original) == original
    assert original == identity.manifest(build(), {"source.ydr":"1"*64}, {"output.ydr":"2"*64}, edition="Legacy", reports=["3"*64], changes={"texture":"DXT5"})
    for field, value in [("edition","Enhanced"),("outputs",{"output.ydr":"4"*64}),("inputs",{"source.ydr":"5"*64}),("validation_reports",["6"*64]),("changes_sha256","7"*64)]:
        changed = deepcopy(original); changed[field] = value
        with pytest.raises(ValueError, match="identity mismatch"):
            contract.validate_manifest(changed)


@pytest.mark.parametrize("name", ["../escape", "C:/game/file", "/absolute", "a\\b", "a/CON.txt", "a/file.", "a//b", "a/../b"])
def test_artifact_paths_fail_closed(name):
    with pytest.raises(ValueError): contract.inventory({name:"a"*64})


def test_case_and_parent_collisions_and_fake_build_fingerprint_are_rejected():
    for files in ({"a":"a"*64,"A":"a"*64},{"a":"a"*64,"a/b":"a"*64}):
        with pytest.raises(ValueError): contract.inventory(files)
    bad = build(); bad["resource_files"]["tools/RpfPatcher/RpfPatcher.exe"] = "e"*64
    with pytest.raises(ValueError, match="build_fingerprint"):
        identity.manifest(bad, {"a":"a"*64}, {"b":"b"*64}, edition=None)


def test_development_identity_tracks_actual_helper_and_not_just_version(tmp_path, monkeypatch):
    helper = tmp_path/"tools/RpfPatcher";helper.mkdir(parents=True)
    binary = helper/"RpfPatcher.exe";binary.write_bytes(b"helper-first")
    executable = tmp_path/"python.exe";executable.write_bytes(b"interpreter")
    monkeypatch.setattr(identity,"project_root",lambda:tmp_path)
    monkeypatch.setattr(identity.sys,"executable",str(executable))
    monkeypatch.setattr(identity.sys,"frozen",False,raising=False)
    monkeypatch.setattr(identity.release_identity,"embedded_build_identity",lambda:None)
    monkeypatch.setattr(identity.release_identity,"source_identity",lambda root:{"dirty":True,"source_tree_sha256":"a"*64})
    first = identity.current()
    binary.write_bytes(b"helper-second")
    second = identity.current()
    assert first["sdk_version"] == second["sdk_version"]
    assert first["build_fingerprint"] != second["build_fingerprint"]
    assert first["mode"] == "development_dirty"
    assert not any(str(tmp_path) in str(value) for value in first.values())


def test_frozen_identity_requires_embedded_identity_and_verified_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(identity,"project_root",lambda:tmp_path)
    monkeypatch.setattr(identity.sys,"frozen",True,raising=False)
    monkeypatch.setattr(identity.release_identity,"embedded_build_identity",lambda:None)
    with pytest.raises(ValueError, match="missing its embedded"):
        identity.current()
    monkeypatch.setattr(identity.release_identity,"embedded_build_identity",lambda:{"kind":"sdk_build_identity"})
    def reject(*args): raise ValueError("resource checksum mismatch")
    monkeypatch.setattr(identity.release_identity,"verify_runtime_resources",reject)
    with pytest.raises(ValueError, match="checksum mismatch"):
        identity.current()
