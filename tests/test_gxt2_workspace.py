import json
import struct

import pytest
from click.testing import CliRunner

from allin1_sdk.cli import main
from allin1_sdk.gxt2_workspace import Gxt2Workspace


def _gxt2(entries=((0x100, "Alpha"), (0x200, "Bravo"))):
    return Gxt2Workspace.encode(tuple(
        {"hash": label_hash, "text": text} for label_hash, text in entries
    ))


def test_gxt2_workspace_round_trip_edit_history_and_build(tmp_path):
    source = _gxt2()
    workspace = Gxt2Workspace().export_bytes(
        "global.gxt2", source, tmp_path / "workspace",
        source_binding={"entry_id": "lang.rpf::global.gxt2"},
    )
    initial = Gxt2Workspace.validate(workspace)
    assert [item["text"] for item in initial["entries"]] == ["Alpha", "Bravo"]
    assert initial["manifest"]["source_binding"]["entry_id"].endswith("global.gxt2")

    first = Gxt2Workspace.set_text(workspace, "0x100", "Edited Alpha")
    second = Gxt2Workspace.add(workspace, 0x180, "Added")
    third = Gxt2Workspace.remove(workspace, "0x200")
    assert [path.name for path in (first, second, third)] == [
        "000001.json", "000002.json", "000003.json",
    ]
    Gxt2Workspace.undo(workspace)
    state = Gxt2Workspace.validate(workspace)
    assert [(item["hash"], item["text"]) for item in state["entries"]] == [
        (0x100, "Edited Alpha"), (0x180, "Added"), (0x200, "Bravo"),
    ]

    output, report_path = Gxt2Workspace.build(workspace, tmp_path / "rebuilt.gxt2")
    parsed = Gxt2Workspace.parse(output.read_bytes())
    assert parsed == state["entries"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "verified"
    assert report["entry_count"] == 3
    assert report["source_binding"]["entry_id"].endswith("global.gxt2")
    assert (workspace / "original.gxt2").read_bytes() == source


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda data: b"BAD!" + data[4:], "header"),
        (
            lambda data: data[:16] + struct.pack("<II", 0x100, 32) + data[24:],
            "duplicate",
        ),
        (
            lambda data: data[:12] + struct.pack("<I", 3) + data[16:],
            "offset",
        ),
        (lambda data: data[:-1], "end offset"),
        (
            lambda data: data[:32] + b"\xff" + data[33:],
            "UTF-8",
        ),
        (
            lambda data: data[:-1] + b"X",
            "null terminated",
        ),
    ],
)
def test_gxt2_parser_rejects_malformed_tables(mutate, match):
    with pytest.raises(ValueError, match=match):
        Gxt2Workspace.parse(mutate(_gxt2()))


def test_gxt2_workspace_rejects_invalid_edits_and_tampering(tmp_path):
    workspace = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace",
    )
    with pytest.raises(ValueError, match="already exists"):
        Gxt2Workspace.add(workspace, 0x100, "Duplicate")
    with pytest.raises(ValueError, match="not found"):
        Gxt2Workspace.set_text(workspace, 0x999, "Missing")
    with pytest.raises(ValueError, match="invalid"):
        Gxt2Workspace.set_text(workspace, 0x100, "bad\0text")
    with pytest.raises(ValueError, match="no edit"):
        Gxt2Workspace.undo(workspace)

    (workspace / "original.gxt2").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="immutable source"):
        Gxt2Workspace.validate(workspace)


def test_gxt2_build_refuses_overwrite(tmp_path):
    workspace = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace",
    )
    output = tmp_path / "rebuilt.gxt2"
    output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="already exists"):
        Gxt2Workspace.build(workspace, output)


def test_gxt2_workspace_detects_entries_and_history_tampering(tmp_path):
    workspace = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace",
    )
    Gxt2Workspace.set_text(workspace, 0x100, "Edited")
    entries = workspace / "entries.json"
    authored = json.loads(entries.read_text(encoding="utf-8"))
    authored[0]["text"] = "Unrecorded edit"
    entries.write_text(json.dumps(authored), encoding="utf-8")
    with pytest.raises(ValueError, match="edit history"):
        Gxt2Workspace.validate(workspace)

    workspace2 = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace2",
    )
    record = Gxt2Workspace.set_text(workspace2, 0x100, "Edited")
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["before_sha256"] = "0" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash chain"):
        Gxt2Workspace.validate(workspace2)

    workspace3 = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace3",
    )
    (workspace3 / "history" / "orphan.before.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected records"):
        Gxt2Workspace.validate(workspace3)


def test_gxt2_cli_lists_edits_undoes_and_builds(tmp_path):
    workspace = Gxt2Workspace().export_bytes(
        "global.gxt2", _gxt2(), tmp_path / "workspace",
    )
    runner = CliRunner()
    listed = runner.invoke(main, ["list-gxt2-entries", str(workspace)])
    assert listed.exit_code == 0 and "0x00000100" in listed.output

    refused = runner.invoke(main, [
        "set-gxt2-text", str(workspace), "0x100", "Edited",
    ])
    assert refused.exit_code != 0 and "--acknowledge-edit" in refused.output
    assert runner.invoke(main, [
        "set-gxt2-text", str(workspace), "0x100", "Edited", "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "add-gxt2-entry", str(workspace), "0x180", "Added", "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "remove-gxt2-entry", str(workspace), "0x200", "--acknowledge-edit",
    ]).exit_code == 0
    assert runner.invoke(main, [
        "undo-gxt2-edit", str(workspace), "--acknowledge-edit",
    ]).exit_code == 0

    output = tmp_path / "cli.gxt2"
    built = runner.invoke(main, [
        "build-gxt2-workspace", str(workspace), "--output", str(output),
    ])
    assert built.exit_code == 0, built.output
    parsed = {item["hash"]: item["text"] for item in Gxt2Workspace.parse(output.read_bytes())}
    assert parsed == {0x100: "Edited", 0x180: "Added", 0x200: "Bravo"}
