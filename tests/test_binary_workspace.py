from __future__ import annotations

import hashlib
import json

import pytest
from click.testing import CliRunner

from allin1_sdk.binary_workspace import BinaryPatchWorkspace
from allin1_sdk.cli import main


def test_binary_workspace_patch_hexdump_undo_and_verified_build(tmp_path):
    source = bytes(range(64)) + b"ALLIN1"
    workspace = BinaryPatchWorkspace().export_bytes(
        "example.bin", source, tmp_path / "workspace",
        source_binding={"entry_id": "::example.bin"},
    )
    assert "00 01 02 03" in BinaryPatchWorkspace.hexdump(workspace, length=32)

    first = BinaryPatchWorkspace.patch(
        workspace, 4, "AABBCC", expected_hex="04 05 06",
    )
    assert first.name == "000001.json"
    assert (workspace / "editable.bin").read_bytes()[4:7] == b"\xAA\xBB\xCC"
    state = BinaryPatchWorkspace.validate(workspace)
    assert len(state["records"]) == 1

    undo = BinaryPatchWorkspace.undo(workspace)
    assert undo.name == "000002.json"
    assert (workspace / "editable.bin").read_bytes() == source
    with pytest.raises(ValueError, match="no changes"):
        BinaryPatchWorkspace.build(workspace, tmp_path / "unchanged.bin")

    BinaryPatchWorkspace.patch(workspace, 1, "FE FF", expected_hex="01 02")
    output, report_path = BinaryPatchWorkspace.build(
        workspace, tmp_path / "built" / "example.bin",
    )
    assert output.read_bytes()[1:3] == b"\xFE\xFF"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert report["changed_bytes"] == 2
    assert report["changed_ranges"] == [{
        "offset": 1, "length": 2, "original_hex": "0102", "edited_hex": "feff",
    }]
    assert report["source_binding"]["entry_id"] == "::example.bin"
    assert report["output_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_binary_workspace_rejects_wrong_expected_ranges_noops_and_overwrite(tmp_path):
    workspace = BinaryPatchWorkspace().export_bytes(
        "test.bin", b"abcdefgh", tmp_path / "workspace",
    )
    with pytest.raises(ValueError, match="Expected bytes do not match"):
        BinaryPatchWorkspace.patch(workspace, 2, "FFFF", expected_hex="0000")
    with pytest.raises(ValueError, match="outside"):
        BinaryPatchWorkspace.patch(workspace, 7, "FFFF")
    with pytest.raises(ValueError, match="would not change"):
        BinaryPatchWorkspace.patch(workspace, 0, "6162", expected_hex="6162")
    with pytest.raises(ValueError, match="complete hexadecimal"):
        BinaryPatchWorkspace.patch(workspace, 0, "A")
    with pytest.raises(ValueError, match="already exists"):
        BinaryPatchWorkspace().export_bytes(
            "again.bin", b"data", workspace,
        )

    BinaryPatchWorkspace.patch(workspace, 0, "FF", expected_hex="61")
    output = tmp_path / "output.bin"
    output.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="already exists"):
        BinaryPatchWorkspace.build(workspace, output)
    assert output.read_bytes() == b"preserve"


def test_binary_workspace_detects_snapshot_editable_and_history_tampering(tmp_path):
    workspace = BinaryPatchWorkspace().export_bytes(
        "test.bin", b"01234567", tmp_path / "workspace",
    )
    (workspace / "original.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="source snapshot"):
        BinaryPatchWorkspace.validate(workspace)

    workspace2 = BinaryPatchWorkspace().export_bytes(
        "test.bin", b"01234567", tmp_path / "workspace2",
    )
    BinaryPatchWorkspace.patch(workspace2, 0, "FF")
    (workspace2 / "editable.bin").write_bytes(b"87654321")
    with pytest.raises(ValueError, match="does not match its history"):
        BinaryPatchWorkspace.validate(workspace2)

    workspace3 = BinaryPatchWorkspace().export_bytes(
        "test.bin", b"01234567", tmp_path / "workspace3",
    )
    BinaryPatchWorkspace.patch(workspace3, 0, "FF")
    record = workspace3 / "history" / "000001.json"
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["before_sha256"] = "0" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash chain"):
        BinaryPatchWorkspace.validate(workspace3)


def test_binary_workspace_cli_inspect_patch_undo_and_build(tmp_path):
    workspace = BinaryPatchWorkspace().export_bytes(
        "test.bin", b"abcdefgh", tmp_path / "workspace",
    )
    runner = CliRunner()
    inspected = runner.invoke(main, [
        "sdk", "inspect-binary-workspace", str(workspace),
        "--offset", "0x1", "--length", "4",
    ])
    assert inspected.exit_code == 0, inspected.output
    assert "00000001" in inspected.output
    refused = runner.invoke(main, [
        "sdk", "patch-binary-workspace", str(workspace),
        "--offset", "0x1", "--hex", "FF",
    ])
    assert refused.exit_code != 0
    assert "--acknowledge-edit" in refused.output
    patched = runner.invoke(main, [
        "sdk", "patch-binary-workspace", str(workspace),
        "--offset", "0x1", "--hex", "FF", "--expected-hex", "62",
        "--acknowledge-edit",
    ])
    assert patched.exit_code == 0, patched.output
    undone = runner.invoke(main, [
        "sdk", "undo-binary-workspace", str(workspace), "--acknowledge-edit",
    ])
    assert undone.exit_code == 0, undone.output
    runner.invoke(main, [
        "sdk", "patch-binary-workspace", str(workspace),
        "--offset", "2", "--hex", "FE", "--acknowledge-edit",
    ])
    output = tmp_path / "built.bin"
    built = runner.invoke(main, [
        "sdk", "build-binary-workspace", str(workspace), "-o", str(output),
    ])
    assert built.exit_code == 0, built.output
    assert output.read_bytes()[2] == 0xFE
