"""Bounded XML/Lua source editing. Parsing never executes scripts or entities."""
from __future__ import annotations

import codecs
import difflib
import hashlib
import os
from pathlib import Path
import re
import tempfile

from lxml import etree

from allin1_sdk.paths import gta_root_containing
from allin1_sdk.release_paths import no_links
from allin1_sdk.workspace_desktop import digest, path

MAX_BYTES = 64 * 1024
MAX_LINES = 2000
CHUNK_SIZE = 8192
LANGUAGES = {".xml": "xml", ".meta": "xml", ".lua": "lua"}
TEMPLATES = {"xml": '<?xml version="1.0" encoding="UTF-8"?>\n<root>\n</root>\n',
             "lua": "-- Lua 5.4 source; the SDK never executes this file.\nlocal config = {}\nreturn config\n"}


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _text(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Code editor input must be UTF-8 text within 64 KiB")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise ValueError("Binary/control characters are not supported by the code editor")
    if len(value.splitlines()) > MAX_LINES:
        raise ValueError("Code editor input exceeds 2,000 lines")
    without_crlf = value.replace("\r\n", "")
    if "\r" in without_crlf or ("\r\n" in value and "\n" in without_crlf):
        raise ValueError("Use consistent LF or CRLF line endings before opening this file")
    return value


def _document(payload):
    document = payload.get("document", {})
    if not isinstance(document, dict) or set(document) - {"language", "chunks"}:
        raise ValueError("Unexpected code document fields")
    language = document.get("language", "xml")
    if not isinstance(language, str) or language not in TEMPLATES:
        raise ValueError("Choose XML or Lua")
    chunks = document.get("chunks")
    if chunks is not None and (not isinstance(chunks, list) or len(chunks) > 16
                              or any(not isinstance(s, str) or len(s) > CHUNK_SIZE for s in chunks)):
        raise ValueError("Code text chunks exceed the document limit")
    return language, None if chunks is None else _text("".join(chunks))


def _context(payload):
    language, draft = _document(payload)
    source = path(payload["source"]) if payload.get("source") else None
    if source:
        if not source.is_file() or source.suffix.casefold() not in LANGUAGES:
            raise ValueError("Choose a text .xml, .meta or .lua file")
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("Code editor input exceeds 64 KiB")
        with source.open("rb") as stream:
            original = stream.read(MAX_BYTES + 1)
        if len(original) > MAX_BYTES:
            raise ValueError("Code input grew beyond 64 KiB during inspection")
        language = LANGUAGES[source.suffix.casefold()]
        if payload.get("document") and payload["document"].get("language", language) != language:
            raise ValueError("Draft language does not match the selected file")
        try:
            baseline = _text(original.decode("utf-8-sig"))
        except UnicodeError as exc:
            raise ValueError("Open UTF-8 text, not compiled META or binary Lua") from exc
    else:
        baseline = TEMPLATES[language]
        original = baseline.encode("utf-8")
    state = digest({"source": str(source) if source else None, "sha256": _hash(original), "language": language})
    return source, original, baseline, language, state, draft


def validate(text, language):
    """Syntax only: not game-schema, API, runtime or security certification."""
    _text(text)
    diagnostics = []
    if language == "xml":
        try:
            if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I):
                raise ValueError("DTDs and entity declarations are forbidden; no external resources are loaded")
            declaration = re.match(r'\s*<\?xml[^?]*encoding\s*=\s*[\"\']([^\"\']+)', text)
            if declaration and declaration[1].casefold() not in {"utf-8", "utf8"}:
                raise ValueError("XML encoding declaration must be UTF-8")
            etree.fromstring(text.encode("utf-8"), etree.XMLParser(
                resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, recover=False))
        except (etree.XMLSyntaxError, ValueError) as exc:
            line, column = getattr(exc, "position", (1, 1))
            diagnostics.append({"line": line, "column": column, "message": str(exc)[:500]})
    elif language == "lua":
        from antlr4 import CommonTokenStream, InputStream
        from antlr4.error.ErrorListener import ErrorListener
        from luaparser.parser.LuaLexer import LuaLexer
        from luaparser.parser.LuaParser import LuaParser

        class ParseFailure(Exception):
            pass

        class Errors(ErrorListener):
            def syntaxError(self, recognizer, offendingSymbol, line, column, msg, e):
                diagnostics.append({"line": line, "column": column + 1, "message": msg[:500]})
                raise ParseFailure()

        try:
            lexer = LuaLexer(InputStream(text))
            lexer.removeErrorListeners()
            lexer.addErrorListener(Errors())
            parser = LuaParser(CommonTokenStream(lexer))
            parser.removeErrorListeners()
            parser.addErrorListener(Errors())
            parser.buildParseTrees = False
            parser.start_()
        except ParseFailure:
            pass
        except RecursionError:
            diagnostics.append({"line": 1, "column": 1, "message": "Lua nesting exceeds the syntax-check limit"})
    else:
        raise ValueError("Unsupported code language")
    return {"valid": not diagnostics, "diagnostics": diagnostics,
            "scope": "XML well-formedness; DTD/entities disabled" if language == "xml" else "Lua 5.4 syntax; no script execution or game API validation"}


def inspect(payload):
    source, original, baseline, language, state, draft = _context(payload)
    text = baseline if draft is None else draft
    return {"source": str(source) if source else None, "name": source.name if source else "untitled." + language,
            "language": language, "chunks": [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)],
            "state_sha256": state, "text_sha256": _hash(text.encode("utf-8")), "draft_check": draft is not None,
            "validation": validate(text, language), "size": len(text.encode("utf-8")),
            "line_ending": "CRLF" if "\r\n" in baseline else "LF", "bom": original.startswith(codecs.BOM_UTF8),
            "can_save": bool(source and not gta_root_containing(source)), "max_bytes": MAX_BYTES}


def _plan(payload):
    source, original, baseline, language, state, draft = _context(payload)
    if payload.get("expected_state_sha256") != state:
        raise ValueError("Source changed on disk; reopen it before reviewing a save")
    if draft is None:
        raise ValueError("The exact code draft is required")
    validation = validate(draft, language)
    if not validation["valid"]:
        first = validation["diagnostics"][0]
        raise ValueError(f"Syntax check failed at {first['line']}:{first['column']}: {first['message']}")
    if payload.get("action") == "save" and source:
        target = path(str(source), writable=True)
        if draft == baseline:
            raise ValueError("No source changes to save")
        backup = no_links(target.with_name(f".{target.name}.{_hash(original)}.allin1-backup"))
        if backup.exists() and not _matches(backup, original):
            raise ValueError("Backup destination contains different data")
    elif payload.get("action") == "save_copy":
        target = path(payload.get("destination"), new=True, writable=True)
        if LANGUAGES.get(target.suffix.casefold()) != language:
            raise ValueError("Output extension must match the XML or Lua document")
        backup = None
    else:
        raise ValueError("Choose save or save_copy for a code document")
    output = (codecs.BOM_UTF8 if original.startswith(codecs.BOM_UTF8) else b"") + draft.encode("utf-8")
    if len(output) > MAX_BYTES:
        raise ValueError("Encoded output exceeds 64 KiB")
    return source, original, baseline, state, target, backup, output, validation


def _matches(target, expected):
    target = no_links(target)
    if not target.is_file() or target.stat().st_size != len(expected):
        return False
    with target.open("rb") as stream:
        return stream.read(MAX_BYTES + 1) == expected


def review(payload):
    source, original, baseline, state, target, backup, output, validation = _plan(payload)
    changed = output.decode("utf-8-sig")
    diff = "".join(difflib.unified_diff(baseline.splitlines(keepends=True), changed.splitlines(keepends=True),
                                      fromfile="Before", tofile="After"))
    return {"source": str(source) if source else "New document", "destination": str(target),
            "state_sha256": state, "action": payload["action"], "backup": str(backup) if backup else None,
            "output_sha256": _hash(output), "size": len(output), "validation": validation,
            "diff": diff[:24000], "diff_truncated": len(diff) > 24000,
            "changes": ["Save exactly the reviewed UTF-8 source; do not execute it.",
                        f"Retain previous bytes at {backup}" if backup else "Create a new file; leave the original unchanged."]}


def apply(payload):
    source, original, _, _, target, backup, output, _ = _plan(payload)
    if backup and not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".allin1-code-", delete=False) as stream:
            staged = Path(stream.name)
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck source, backup and all parent links after staging, just before commit.
        path(str(target), new=backup is None, writable=True)
        if source and not _matches(source, original):
            raise ValueError("Source changed during save; its new content was preserved")
        if backup:
            if not _matches(backup, original):
                raise ValueError("Backup changed during save; source was not replaced")
            staged.replace(target)
        else:
            # link() publishes atomically without overwriting a competing file.
            os.link(staged, target)
            staged.unlink()
        from allin1_sdk.workspace_desktop import inspect as inspect_workspace
        return {"session": inspect_workspace({"module": "code", "source": str(target)}),
                "output": str(target), "output_sha256": _hash(output), "backup": str(backup) if backup else None}
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
