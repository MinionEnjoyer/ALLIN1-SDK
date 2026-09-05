"""Real XML/Lua parsing and disposable, revision-bound source saves."""
import codecs
import hashlib
import os
from pathlib import Path

import pytest

from allin1_sdk import code_desktop as code, workspace_desktop as workspace
from allin1_sdk.desktop_protocol import dispatch_operation


def request(source, text, *, action="save", destination=None):
    context = {"module": "code", "source": str(source)}
    session = workspace.inspect(context)
    result = {**context, "action": action, "expected_state_sha256": session["state_sha256"],
              "document": {"language": session["language"], "chunks": [text]}}
    if destination:
        result["destination"] = str(destination)
    return result


def authorize(payload):
    review = workspace.review(payload)
    return {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


@pytest.mark.parametrize("suffix,before,after", [("xml", "<root/>\n", "<root><value>2</value></root>\n"),
    ("meta", "<CData value='1'/>\n", "<CData value='2'/>\n"),
    ("lua", "return { value = 1 }\n", "local value <const> = 2\nreturn { value = value }\n")])
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_code_save_copy_backup_and_reopen(tmp_path, suffix, before, after, bom, eol):
    before, after = before.replace("\n", eol), after.replace("\n", eol)
    source = tmp_path / f"source with spaces.{suffix}"
    prefix = codecs.BOM_UTF8 if bom else b""
    source.write_bytes(prefix + before.encode())
    payload = request(source, after)
    review = workspace.review(payload)
    assert source.read_bytes() == prefix + before.encode()
    assert review["validation"]["valid"]
    assert not Path(review["backup"]).exists()
    result = workspace.apply(authorize(payload))
    assert Path(result["backup"]).read_bytes() == prefix + before.encode()
    assert source.read_bytes() == prefix + after.encode()
    assert result["output_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "".join(result["session"]["chunks"]) == after
    assert result["session"]["line_ending"] == ("CRLF" if eol == "\r\n" else "LF")
    output = tmp_path / f"copy.{suffix}"
    copy_request = request(source, after, action="save_copy", destination=output)
    assert not output.exists()
    copied = workspace.apply(authorize(copy_request))
    assert output.read_bytes() == source.read_bytes()
    assert copied["backup"] is None
    assert source.stat().st_nlink == output.stat().st_nlink == 1
    with pytest.raises(ValueError, match="new destination"):
        workspace.review(copy_request)


@pytest.mark.parametrize("language,text,valid", [
    ("xml", "<root/>", True), ("xml", "<root>", False),
    ("xml", "<!DOCTYPE root SYSTEM 'https://example.invalid/dtd'><root/>", False),
    ("xml", "<!DOCTYPE root [<!ENTITY x SYSTEM 'file:///canary'>]><root>&x;</root>", False),
    ("xml", '<?xml version="1.0" encoding="UTF-16"?><root/>', False),
    ("lua", "local x = { nested = {1,2,3} }; return x", True),
    ("lua", "--[[ comment ]]\nlocal x <const> = 1\nreturn x << 2", True),
    ("lua", "function broken(\nreturn", False), ("lua", "local x = 'unterminated", False),
    ("lua", "local x = `fivem_extension`", False),
])
def test_syntax_results_are_structured_and_never_written_to_stdout(language, text, valid, capsys):
    result = code.validate(text, language)
    assert result["valid"] is valid
    if not valid:
        assert result["diagnostics"][0]["line"] >= 1
        assert result["diagnostics"][0]["column"] >= 1
    assert capsys.readouterr() == ("", "")


def test_lua_payload_is_parsed_not_executed(tmp_path):
    outside = tmp_path / "canary"
    outside.write_bytes(b"unchanged")
    script = f'os.remove([[{outside}]])\nwhile true do end\n'
    assert code.validate(script, "lua")["valid"]
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("mutation", ["source", "draft", "hash", "confirmation"])
def test_stale_or_unconfirmed_save_is_read_only(tmp_path, mutation):
    source = tmp_path / "draft.xml"
    source.write_bytes(b"<before/>")
    pending = authorize(request(source, "<after/>"))
    if mutation == "source": source.write_bytes(b"<external/>")
    elif mutation == "draft": pending["document"]["chunks"] = ["<different/>"]
    elif mutation == "hash": pending["review_sha256"] = "0" * 64
    else: pending["authoring_confirmed"] = False
    before = source.read_bytes()
    with pytest.raises(ValueError): workspace.apply(pending)
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]


def test_invalid_document_opens_for_repair_but_cannot_be_saved(tmp_path):
    source = tmp_path / "broken.xml"
    source.write_bytes(b"<root>")
    session = workspace.inspect({"module": "code", "source": str(source)})
    assert not session["validation"]["valid"]
    with pytest.raises(ValueError, match="Syntax check failed"):
        workspace.review(request(source, "<still_broken>"))
    workspace.apply(authorize(request(source, "<repaired/>")))
    assert source.read_bytes() == b"<repaired/>"


def test_checking_unsaved_draft_does_not_change_disk_or_baseline_identity(tmp_path):
    source = tmp_path / "draft.lua"
    source.write_bytes(b"return 1")
    payload = request(source, "local invalid =")
    check = workspace.inspect({k: v for k, v in payload.items() if k not in {"action", "expected_state_sha256"}})
    assert check["draft_check"] is True and not check["validation"]["valid"]
    assert check["state_sha256"] == payload["expected_state_sha256"]
    assert source.read_bytes() == b"return 1"


@pytest.mark.parametrize("language", ["xml", "lua"])
def test_new_document_protocol_happy_path(tmp_path, language):
    context = {"module": "code", "document": {"language": language}}
    risk, session = dispatch_operation("inspect_authoring_workspace", context)
    assert risk == "read_only" and session["validation"]["valid"]
    payload = {**context, "document": {"language": language, "chunks": session["chunks"]},
               "action": "save_copy", "destination": str(tmp_path / ("new." + language)),
               "expected_state_sha256": session["state_sha256"]}
    risk, result = dispatch_operation("apply_workspace_action", authorize(payload))
    assert risk == "authoring_write" and not result["game_write_performed"]
    assert Path(result["output"]).is_file()


def test_game_inputs_are_read_only_and_copy_output_must_be_outside_game(tmp_path):
    game = tmp_path / "fake GTA"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"synthetic; never executed")
    source = game / "data.xml"
    source.write_bytes(b"<root/>")
    assert not workspace.inspect({"module": "code", "source": str(source)})["can_save"]
    for payload in (request(source, "<changed/>"), request(source, "<root/>", action="save_copy", destination=game / "copy.xml")):
        with pytest.raises(ValueError, match="outside GTA"):
            workspace.review(payload)
    result = workspace.apply(authorize(request(source, "<root/>", action="save_copy", destination=tmp_path / "copy.xml")))
    assert Path(result["output"]).read_bytes() == source.read_bytes() == b"<root/>"


@pytest.mark.parametrize("payload", [b"\xff", b"\0", b"x" * (64 * 1024 + 1), b"a\nb\r\n", b"\n" * 2001],
                         ids=["invalid-utf8", "binary", "oversized", "mixed-newlines", "too-many-lines"])
def test_binary_oversized_or_inconsistent_text_is_rejected(tmp_path, payload):
    source = tmp_path / "bad.xml"
    source.write_bytes(payload)
    with pytest.raises(ValueError): workspace.inspect({"module": "code", "source": str(source)})
    assert source.read_bytes() == payload


def test_large_document_uses_bounded_chunks_without_silent_truncation(tmp_path):
    source = tmp_path / "large.xml"
    text = "<root>" + "a" * 50000 + "</root>"
    source.write_text(text, encoding="utf-8")
    _, session = dispatch_operation("inspect_authoring_workspace", {"module": "code", "source": str(source)})
    assert "".join(session["chunks"]) == text and len(session["chunks"]) > 1


def test_hardlink_and_backup_collision_preserve_outside_canary(tmp_path):
    source = tmp_path / "source.xml"
    source.write_bytes(b"<root/>")
    canary = tmp_path / "outside"
    os.link(source, canary)
    with pytest.raises(ValueError, match="Hard-linked"):
        workspace.inspect({"module": "code", "source": str(source)})
    canary.unlink()
    payload = request(source, "<after/>")
    backup = Path(workspace.review(payload)["backup"])
    backup.write_bytes(b"unowned content")
    with pytest.raises(ValueError, match="Backup destination"):
        workspace.review(payload)
    assert backup.read_bytes() == b"unowned content" and source.read_bytes() == b"<root/>"


def test_commit_failure_leaves_original_and_recovery_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source.xml"
    source.write_bytes(b"<root/>")
    pending = authorize(request(source, "<changed/>"))
    def fail(*args): raise PermissionError("injected commit failure")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError): workspace.apply(pending)
    assert source.read_bytes() == b"<root/>"
    backups = list(tmp_path.glob("*.allin1-backup"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"<root/>"
    assert not list(tmp_path.glob(".allin1-code-*"))


@pytest.mark.parametrize("fault", ["source-changed", "backup-changed", "stage-fsync"])
def test_recheck_after_staging_preserves_external_changes(tmp_path, monkeypatch, fault):
    source = tmp_path / "source.xml"
    source.write_bytes(b"<root/>")
    payload = request(source, "<changed/>")
    backup = Path(workspace.review(payload)["backup"])
    pending = authorize(payload)
    real_fsync = os.fsync
    calls = 0
    def fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == "stage-fsync": raise OSError("injected disk failure")
            (source if fault == "source-changed" else backup).write_bytes(b"external edit")
        real_fsync(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises((ValueError, OSError)): workspace.apply(pending)
    assert source.read_bytes() == (b"external edit" if fault == "source-changed" else b"<root/>")
    assert backup.read_bytes() == (b"external edit" if fault == "backup-changed" else b"<root/>")
    assert not list(tmp_path.glob(".allin1-code-*"))


def test_copy_publication_never_overwrites_a_competing_file(tmp_path, monkeypatch):
    source = tmp_path / "source.lua"
    source.write_bytes(b"return 1")
    target = tmp_path / "copy.lua"
    pending = authorize(request(source, "return 2", action="save_copy", destination=target))
    original_link = os.link
    def race(src, dst):
        Path(dst).write_bytes(b"user created this file")
        original_link(src, dst)
    monkeypatch.setattr(os, "link", race)
    with pytest.raises(FileExistsError): workspace.apply(pending)
    assert source.read_bytes() == b"return 1" and target.read_bytes() == b"user created this file"
    assert not list(tmp_path.glob(".allin1-code-*"))


@pytest.mark.parametrize("language", [None, [], "python"])
def test_unsupported_language_fails_before_creating_files(tmp_path, language):
    with pytest.raises(ValueError): workspace.inspect({"module": "code", "document": {"language": language}})
    assert list(tmp_path.iterdir()) == []


def test_xml_encoding_whitespace_cannot_override_utf8():
    result = code.validate('<?xml version="1.0" encoding = "ISO-8859-1"?><root>é</root>', "xml")
    assert not result["valid"] and "UTF-8" in result["diagnostics"][0]["message"]
