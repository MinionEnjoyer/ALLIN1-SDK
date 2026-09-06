from pathlib import Path

from allin1_sdk import implementation_identity as identity,asset_validation,diagnostic_bundle
from allin1_sdk.workspace_desktop import file_hash


def test_source_identity_changes_with_actual_implementation_bytes(tmp_path):
    source=tmp_path/"check.py";source.write_bytes(b"version=1")
    before=identity.identify([source])
    source.write_bytes(b"version=2")
    after=identity.identify([source])
    assert before["sha256"]!=after["sha256"] and after["files"]["check.py"]==file_hash(source)
    assert "implementation_identity.py" in after["files"]


def test_frozen_reports_and_redaction_fingerprint_sidecar_without_loose_sources(tmp_path,monkeypatch):
    executable=tmp_path/"sidecar.exe";executable.write_bytes(b"test-owned-executable")
    monkeypatch.setattr(identity.sys,"frozen",True,raising=False)
    monkeypatch.setattr(identity.sys,"executable",str(executable))
    monkeypatch.setattr(identity,"__file__",str(tmp_path/"missing/implementation_identity.pyc"))
    monkeypatch.setattr(asset_validation,"__file__",str(tmp_path/"missing/asset_validation.pyc"))
    monkeypatch.setattr(diagnostic_bundle,"__file__",str(tmp_path/"missing/diagnostic_bundle.pyc"))
    result=asset_validation.analyze((Path(__file__).parent/"fixtures/animation_skin.ydr.xml").read_bytes())
    assert result["validator_identity"]["kind"]=="frozen_sidecar"
    assert result["validator_identity"]["executable_sha256"]==file_hash(executable)
    file=tmp_path/"log.txt";file.write_text("hello")
    bundle=diagnostic_bundle.inspect({"logs":[{"source":str(file),"start_line":1,"line_count":1}]},tmp_path)
    assert bundle["redaction_implementation"]["kind"]=="frozen_sidecar"
