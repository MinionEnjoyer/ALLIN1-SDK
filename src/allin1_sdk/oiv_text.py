"""Bounded, side-effect-free compiler for OIV line-oriented text commands."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Iterable

from lxml import etree


MAX_OIV_TEXT_BYTES = 32 * 1024 * 1024
MAX_OIV_TEXT_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OIV_TEXT_LINES = 500_000
MAX_OIV_TEXT_EDITS = 512
MAX_OIV_TEXT_LINE_CHARS = 1_048_576
_CONDITIONS = {"equal", "startwith"}
_POSITIONS = {"before", "after"}


@dataclass(frozen=True)
class OivTextEdit:
    """One supported child command from an OIV ``text`` operation."""

    action: str
    content: str = ""
    line: str = ""
    condition: str = ""
    where: str = ""


@dataclass(frozen=True)
class OivTextMergeResult:
    """Verified in-memory text result and its complete audit record."""

    data: bytes
    audit: dict[str, object]


@dataclass(frozen=True)
class _DecodedText:
    text: str
    codec: str
    encoding: str
    bom: bytes


class OivTextMergeEngine:
    """Replay a deliberately bounded subset of line-oriented text edits.

    Exact and prefix selectors must resolve to exactly one line. Mask selectors
    remain blocked because the package specification does not define their
    escaping and wildcard behavior precisely enough for guarded compilation.
    """

    @classmethod
    def validate_recipe_edit(cls, edit: OivTextEdit) -> None:
        action = edit.action.casefold()
        if action not in {"add", "insert", "replace", "delete"}:
            raise ValueError(f"Unsupported OIV text action: {edit.action}")
        cls._validate_line(edit.content, "text edit content", allow_empty=True)
        if action == "add":
            if edit.line or edit.condition or edit.where:
                raise ValueError("OIV text add cannot include a selector")
            return

        cls._validate_line(edit.line, "text selector", allow_empty=False)
        condition = edit.condition.casefold()
        if condition == "mask":
            raise ValueError("OIV text Mask selectors remain blocked")
        if condition not in _CONDITIONS:
            raise ValueError(f"Unsupported OIV text condition: {edit.condition}")
        if action == "insert":
            if edit.where.casefold() not in _POSITIONS:
                raise ValueError(f"Unsupported OIV text insert position: {edit.where}")
        elif edit.where:
            raise ValueError(f"OIV text {action} cannot include an insert position")
        if action == "delete" and edit.content:
            raise ValueError("OIV text delete cannot include replacement content")

    @classmethod
    def apply(
        cls, source: bytes, edits: Iterable[OivTextEdit], *, source_name: str,
        verify_xml: bool = False,
    ) -> OivTextMergeResult:
        requested = tuple(edits)
        if not requested:
            raise ValueError("OIV text operation contains no edits")
        if len(requested) > MAX_OIV_TEXT_EDITS:
            raise ValueError(
                f"OIV text operations are limited to {MAX_OIV_TEXT_EDITS} edits"
            )
        if len(source) > MAX_OIV_TEXT_BYTES:
            raise ValueError(
                f"OIV text source exceeds {MAX_OIV_TEXT_BYTES:,} bytes"
            )
        decoded = cls._decode(source, source_name)
        newline, had_final_newline, lines = cls._split_lines(decoded.text)
        if len(lines) > MAX_OIV_TEXT_LINES:
            raise ValueError(
                f"OIV text source exceeds {MAX_OIV_TEXT_LINES:,} lines"
            )
        source_structured_sha256 = (
            cls._structured_sha256(source, source_name) if verify_xml and source else None
        )
        operation_audit: list[dict[str, object]] = []

        for number, edit in enumerate(requested, start=1):
            cls.validate_recipe_edit(edit)
            action = edit.action.casefold()
            event: dict[str, object] = {
                "number": number,
                "action": action,
                "condition": edit.condition.casefold() or None,
                "selector_sha256": (
                    cls._string_sha256(edit.line) if edit.line else None
                ),
                "content_sha256": (
                    cls._string_sha256(edit.content)
                    if action != "delete" else None
                ),
            }
            if action == "add":
                lines.append(edit.content)
                event.update({"matched_lines": 0, "line_index": len(lines) - 1})
            else:
                matches = cls._matches(lines, edit)
                if not matches:
                    raise ValueError(
                        f"OIV text edit {number} matched no lines in {source_name}"
                    )
                if len(matches) != 1:
                    raise ValueError(
                        f"OIV text edit {number} is ambiguous ({len(matches)} lines) "
                        f"in {source_name}"
                    )
                index = matches[0]
                event.update({
                    "matched_lines": 1,
                    "line_index": index,
                    "matched_line_sha256": cls._string_sha256(lines[index]),
                })
                if action == "insert":
                    if edit.where.casefold() == "after":
                        index += 1
                    lines.insert(index, edit.content)
                    event["inserted_line_index"] = index
                elif action == "replace":
                    lines[index] = edit.content
                else:
                    lines.pop(index)
            if len(lines) > MAX_OIV_TEXT_LINES:
                raise ValueError(
                    f"OIV text output exceeds {MAX_OIV_TEXT_LINES:,} lines"
                )
            operation_audit.append(event)

        rendered = newline.join(lines)
        if had_final_newline and lines:
            rendered += newline
        output = decoded.bom + rendered.encode(decoded.codec, errors="strict")
        if len(output) > MAX_OIV_TEXT_OUTPUT_BYTES:
            raise ValueError(
                f"OIV text output exceeds {MAX_OIV_TEXT_OUTPUT_BYTES:,} bytes"
            )
        if output == source:
            raise ValueError("OIV text edits produced no byte change")
        reparsed = cls._decode(output, f"compiled {source_name}")
        if reparsed.text != rendered:
            raise ValueError("Compiled OIV text failed encoding round-trip verification")
        output_has_final_newline = bool(rendered) and rendered.endswith(newline)
        output_structured_sha256 = (
            cls._structured_sha256(output, f"compiled {source_name}")
            if verify_xml and output else None
        )
        if (
            verify_xml and source_structured_sha256 is not None
            and output_structured_sha256 == source_structured_sha256
        ):
            raise ValueError("OIV text edits produced no structured XML change")

        return OivTextMergeResult(output, {
            "schema_version": 1,
            "operation": "oiv_text_merge",
            "source_name": source_name,
            "source_size": len(source),
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "source_lines": len(cls._split_lines(decoded.text)[2]),
            "output_size": len(output),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_lines": len(lines),
            "encoding": decoded.encoding,
            "bom_preserved": bool(decoded.bom),
            "newline": {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[newline],
            "source_had_final_newline": had_final_newline,
            "output_has_final_newline": output_has_final_newline,
            "final_newline_preserved": (
                output_has_final_newline == had_final_newline
            ),
            "encoding_round_trip_verified": True,
            "structured_xml_reparse_verified": bool(verify_xml),
            "source_structured_sha256": source_structured_sha256,
            "output_structured_sha256": output_structured_sha256,
            "edits": operation_audit,
        })

    @staticmethod
    def _decode(source: bytes, label: str) -> _DecodedText:
        if source.startswith(b"\xef\xbb\xbf"):
            payload, codec, encoding, bom = source[3:], "utf-8", "UTF-8", source[:3]
        elif source.startswith(b"\xff\xfe"):
            payload, codec, encoding, bom = (
                source[2:], "utf-16-le", "UTF-16LE", source[:2]
            )
        elif source.startswith(b"\xfe\xff"):
            payload, codec, encoding, bom = (
                source[2:], "utf-16-be", "UTF-16BE", source[:2]
            )
        else:
            payload, codec, encoding, bom = source, "utf-8", "UTF-8", b""
        try:
            text = payload.decode(codec, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"OIV text source is not supported Unicode: {label}") from exc
        if "\0" in text:
            raise ValueError(f"OIV text source contains a null character: {label}")
        return _DecodedText(text, codec, encoding, bom)

    @staticmethod
    def _split_lines(text: str) -> tuple[str, bool, list[str]]:
        separators = re.findall(r"\r\n|\r|\n", text)
        styles = set(separators)
        if len(styles) > 1:
            raise ValueError("OIV text source uses mixed newline styles")
        newline = next(iter(styles), "\r\n")
        had_final_newline = bool(text) and text.endswith(newline)
        if not text:
            return newline, False, []
        lines = text.split(newline)
        if had_final_newline:
            lines.pop()
        return newline, had_final_newline, lines

    @staticmethod
    def _matches(lines: list[str], edit: OivTextEdit) -> list[int]:
        if edit.condition.casefold() == "equal":
            return [index for index, line in enumerate(lines) if line == edit.line]
        return [index for index, line in enumerate(lines) if line.startswith(edit.line)]

    @staticmethod
    def _validate_line(value: str, label: str, *, allow_empty: bool) -> None:
        if (not allow_empty and not value) or len(value) > MAX_OIV_TEXT_LINE_CHARS:
            lower = 0 if allow_empty else 1
            raise ValueError(
                f"OIV {label} must be {lower}-{MAX_OIV_TEXT_LINE_CHARS:,} characters"
            )
        if any(character in value for character in ("\0", "\r", "\n")):
            raise ValueError(f"OIV {label} must contain exactly one line")

    @staticmethod
    def _string_sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _structured_sha256(data: bytes, label: str) -> str:
        folded = data.upper()
        if b"<!DOCTYPE" in folded or b"<!ENTITY" in folded:
            raise ValueError(f"DTD/entity declarations are not allowed in {label}")
        try:
            root = etree.fromstring(data, parser=etree.XMLParser(
                resolve_entities=False, load_dtd=False, no_network=True,
                recover=False, huge_tree=False, remove_blank_text=False,
                strip_cdata=False,
            ))
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ValueError(f"OIV text edit left invalid XML in {label}: {exc}") from exc
        canonical = etree.tostring(
            root.getroottree(), method="c14n", exclusive=False, with_comments=True,
        )
        return hashlib.sha256(canonical).hexdigest()


def edit_to_dict(edit: OivTextEdit) -> dict[str, str]:
    """Stable JSON representation used by OIV inspection reports."""
    return asdict(edit)
