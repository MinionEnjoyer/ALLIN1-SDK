"""Construction identity, readback drift and exclusive RPF publication."""
import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk import artifact_identity
from allin1_sdk.artifact_contract import verify_seal, validate_build, seal
from test_rpf_builder import _service


def prepare(tmp_path, monkeypatch):
    builder = _service(tmp_path, monkeypatch)
    source = tmp_path / "source"
    (source / "nested.rpf.source").mkdir(parents=True)
    (source / "nested.rpf.source/item.bin").write_bytes(b"original")
    return builder, source


def test_complete_nested_source_and_actual_helper_are_bound(tmp_path, monkeypatch):
    builder, source = prepare(tmp_path, monkeypatch)
    output, report = builder.build(source, tmp_path / "out.rpf")
    evidence = json.loads(report.read_bytes())
    verify_seal(evidence, "report_sha256")
    validate_build(evidence["build"])
    assert evidence["build"] == artifact_identity.current(resource_root=builder.service.project_root)
    assert evidence["build"]["resource_files"]["tools/RpfPatcher/RpfPatcher.exe"] == hashlib.sha256(b"helper").hexdigest()
    assert evidence["archive"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert any(row["path"] == "nested.rpf.source/item.bin" and row["sha256"] == hashlib.sha256(b"original").hexdigest() for row in evidence["source_snapshot"])
    evidence["source_snapshot"][-1]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="identity mismatch"):
        verify_seal(evidence, "report_sha256")


@pytest.mark.parametrize("changed", ["source", "helper", "sdk"])
def test_readback_drift_discards_archive_and_receipt(tmp_path, monkeypatch, changed):
    builder, source = prepare(tmp_path, monkeypatch)
    fingerprint = builder.service.entry_content_fingerprints
    def mutate(*args, **kwargs):
        result = fingerprint(*args, **kwargs)
        if changed == "source":
            (source / "nested.rpf.source/item.bin").write_bytes(b"changed during readback")
        elif changed == "helper":
            (builder.service.project_root / "tools/RpfPatcher/RpfPatcher.exe").write_bytes(b"changed")
        else:
            current = artifact_identity.current(resource_root=builder.service.project_root)
            replacement = seal({**{k:v for k,v in current.items() if k!="build_fingerprint"}, "sdk_version":"changed"}, "build_fingerprint")
            monkeypatch.setattr(artifact_identity, "current", lambda **kwargs: replacement)
        return result
    monkeypatch.setattr(builder.service, "entry_content_fingerprints", mutate)
    output = tmp_path / "out.rpf"
    with pytest.raises(RuntimeError, match="changed"):
        builder.build(source, output)
    assert not output.exists()
    assert not builder.validation_path(output).exists()
    assert not list(tmp_path.glob(".out.rpf-build-*"))


@pytest.mark.parametrize("competitor", ["archive", "report"])
def test_post_preflight_competing_file_is_never_overwritten(tmp_path, monkeypatch, competitor):
    builder, source = prepare(tmp_path, monkeypatch)
    output = tmp_path / "out.rpf"
    report = builder.validation_path(output)
    owned_by_other = output if competitor == "archive" else report
    fingerprint = builder.service.entry_content_fingerprints
    def race(*args, **kwargs):
        result = fingerprint(*args, **kwargs)
        owned_by_other.write_bytes(b"another operation's file")
        return result
    monkeypatch.setattr(builder.service, "entry_content_fingerprints", race)
    with pytest.raises(FileExistsError):
        builder.build(source, output)
    assert owned_by_other.read_bytes() == b"another operation's file"
    assert not (report if competitor == "archive" else output).exists()
    assert not list(tmp_path.glob(".out.rpf-build-*"))


def test_graph_review_is_invalidated_by_sdk_identity_change(tmp_path, monkeypatch):
    from allin1_sdk import workspace_desktop as desktop
    from allin1_sdk.rpf_graph import RpfPackageGraph
    from test_artifact_identity import build
    builder, source = prepare(tmp_path, monkeypatch)
    graph = RpfPackageGraph.create_from_folder(source, tmp_path / "graph.json")
    state = build()
    monkeypatch.setattr(artifact_identity, "current", lambda **kwargs: state)
    context = {"module":"graph", "workspace":str(graph)}
    output = tmp_path / "refused.rpf"
    request = {**context, "action":"build", "destination":str(output), "gta_path":str(builder.service.gta_path),
               "expected_state_sha256":desktop.inspect(context)["state_sha256"]}
    reviewed = desktop.review(request)
    state = seal({**{k:v for k,v in state.items() if k!="build_fingerprint"}, "sdk_version":"changed"}, "build_fingerprint")
    with pytest.raises(ValueError, match="review|Review"):
        desktop.apply({**request,"review_sha256":reviewed["review_sha256"],"authoring_confirmed":True})
    assert not output.exists()
