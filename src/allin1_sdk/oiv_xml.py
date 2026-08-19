"""Bounded, side-effect-free compiler for OpenIV 2.2 XML commands."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Iterable

from lxml import etree


MAX_OIV_XML_BYTES = 32 * 1024 * 1024
MAX_OIV_XML_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OIV_XML_NODES = 250_000
MAX_OIV_XML_EDITS = 512
MAX_OIV_XPATH_CHARS = 2_048
MAX_OIV_FRAGMENT_BYTES = 2 * 1024 * 1024
MAX_OIV_FRAGMENT_NODES = 2_048
_APPEND_MODES = {"first", "last", "before", "after"}
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_STRING_EQUALITY = re.compile(
    r"(?P<lhs>(?:@?[A-Za-z_][A-Za-z0-9_.-]*|\.|text\(\)))\s*=\s*"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)


@dataclass(frozen=True)
class OivXmlEdit:
    """One official OIV XML child command."""

    action: str
    xpath: str
    append: str = ""
    content: str = ""


@dataclass(frozen=True)
class OivXmlMergeResult:
    """Verified in-memory result and its complete audit record."""

    data: bytes
    audit: dict[str, object]


class OivXmlMergeEngine:
    """Apply a deliberately bounded XPath 1.0 subset without writing files.

    XPath execution is provided by libxml2 through lxml. No extension functions,
    variables, namespaces, XSLT, network access, DTDs, or entity resolution are
    enabled. Every selector must resolve to exactly one element.
    """

    @classmethod
    def validate_recipe_edit(cls, edit: OivXmlEdit) -> None:
        action = edit.action.casefold()
        if action not in {"add", "replace", "remove"}:
            raise ValueError(f"Unsupported OIV XML action: {edit.action}")
        cls._validate_xpath(edit.xpath)
        append = (edit.append or "last").casefold()
        if action == "add" and append not in _APPEND_MODES:
            raise ValueError(f"Unsupported OIV XML append mode: {edit.append}")
        if action != "add" and edit.append:
            raise ValueError(f"OIV XML {action} cannot include append")
        if action in {"add", "replace"}:
            elements = cls._parse_fragment(edit.content)
            if action == "replace" and len(elements) != 1:
                raise ValueError("OIV XML replace must contain exactly one element")
        elif edit.content.strip():
            raise ValueError("OIV XML remove cannot contain replacement content")

    @classmethod
    def apply(
        cls, source: bytes, edits: Iterable[OivXmlEdit], *, source_name: str,
    ) -> OivXmlMergeResult:
        requested = tuple(edits)
        if not requested:
            raise ValueError("OIV XML operation contains no edits")
        if len(requested) > MAX_OIV_XML_EDITS:
            raise ValueError(
                f"OIV XML operations are limited to {MAX_OIV_XML_EDITS} edits"
            )
        if not source or len(source) > MAX_OIV_XML_BYTES:
            raise ValueError(
                f"OIV XML source must be 1-{MAX_OIV_XML_BYTES:,} bytes"
            )
        cls._reject_entities(source, source_name)
        try:
            root = etree.fromstring(source, parser=cls._parser())
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ValueError(f"Invalid XML source {source_name}: {exc}") from exc
        tree = root.getroottree()
        source_nodes = cls._count_nodes(root, source_name)
        source_semantic = cls._canonical_sha256(tree)
        source_sha256 = hashlib.sha256(source).hexdigest()
        operation_audit: list[dict[str, object]] = []

        for number, edit in enumerate(requested, start=1):
            cls.validate_recipe_edit(edit)
            exact = cls._select(tree, edit.xpath)
            used_case_fallback = False
            selected_xpath = edit.xpath
            if not exact:
                relaxed = cls._casefold_xpath(edit.xpath)
                if relaxed != edit.xpath:
                    exact = cls._select(tree, relaxed)
                    used_case_fallback = bool(exact)
                    if used_case_fallback:
                        selected_xpath = relaxed
            if not exact:
                raise ValueError(
                    f"OIV XML edit {number} XPath matched no elements: {edit.xpath}"
                )
            if len(exact) != 1:
                raise ValueError(
                    f"OIV XML edit {number} XPath is ambiguous ({len(exact)} matches): "
                    f"{edit.xpath}"
                )
            target = exact[0]
            if not isinstance(target, etree._Element):
                raise ValueError(
                    f"OIV XML edit {number} XPath must select an element: {edit.xpath}"
                )
            parent = target.getparent()
            action = edit.action.casefold()
            append = (edit.append or "last").casefold()
            if action in {"replace", "remove"} and parent is None:
                raise ValueError(f"OIV XML edit {number} cannot {action} the root element")
            if action == "add" and append in {"before", "after"} and parent is None:
                raise ValueError(
                    f"OIV XML edit {number} cannot insert beside the root element"
                )
            target_path = tree.getpath(target)
            before_sha256 = cls._node_sha256(target)
            inserted = 0
            if action == "add":
                fragment = cls._parse_fragment(edit.content)
                clones = [copy.deepcopy(item) for item in fragment]
                if append == "first":
                    for offset, item in enumerate(clones):
                        target.insert(offset, item)
                elif append == "last":
                    target.extend(clones)
                else:
                    assert parent is not None
                    position = parent.index(target) + (append == "after")
                    for offset, item in enumerate(clones):
                        parent.insert(position + offset, item)
                inserted = len(clones)
            elif action == "replace":
                replacement = copy.deepcopy(cls._parse_fragment(edit.content)[0])
                assert parent is not None
                parent.replace(target, replacement)
                inserted = 1
            else:
                assert parent is not None
                parent.remove(target)

            operation_audit.append({
                "number": number,
                "action": action,
                "xpath": edit.xpath,
                "evaluated_xpath": selected_xpath,
                "append": append if action == "add" else None,
                "case_insensitive_fallback": used_case_fallback,
                "matched_elements": 1,
                "target_path": target_path,
                "target_before_sha256": before_sha256,
                "inserted_elements": inserted,
                "fragment_sha256": (
                    hashlib.sha256(edit.content.encode("utf-8")).hexdigest()
                    if action in {"add", "replace"} else None
                ),
            })

        result_nodes = cls._count_nodes(root, source_name)
        result_semantic = cls._canonical_sha256(tree)
        encoding = (tree.docinfo.encoding or "UTF-8").upper()
        if not re.fullmatch(r"[A-Z0-9._-]{1,40}", encoding):
            raise ValueError(f"Unsafe XML output encoding: {encoding}")
        had_declaration = source.lstrip().startswith(b"<?xml")
        try:
            output = etree.tostring(
                tree, encoding=encoding, xml_declaration=had_declaration,
                pretty_print=False, with_tail=False,
            )
        except (LookupError, ValueError) as exc:
            raise ValueError(f"Could not preserve XML encoding {encoding}: {exc}") from exc
        if not output or len(output) > MAX_OIV_XML_OUTPUT_BYTES:
            raise ValueError(
                f"OIV XML output exceeds {MAX_OIV_XML_OUTPUT_BYTES:,} bytes"
            )
        cls._reject_entities(output, f"compiled {source_name}")
        try:
            reparsed_root = etree.fromstring(output, parser=cls._parser())
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"Compiled OIV XML did not reparse: {exc}") from exc
        reparsed_tree = reparsed_root.getroottree()
        reparsed_semantic = cls._canonical_sha256(reparsed_tree)
        if reparsed_semantic != result_semantic:
            raise ValueError("Compiled OIV XML failed canonical reparse verification")
        if result_semantic == source_semantic:
            raise ValueError("OIV XML edits produced no semantic change")

        return OivXmlMergeResult(output, {
            "schema_version": 1,
            "operation": "oiv_xml_merge",
            "source_name": source_name,
            "source_size": len(source),
            "source_sha256": source_sha256,
            "source_semantic_sha256": source_semantic,
            "source_nodes": source_nodes,
            "output_size": len(output),
            "output_sha256": hashlib.sha256(output).hexdigest(),
            "output_semantic_sha256": result_semantic,
            "output_nodes": result_nodes,
            "encoding": encoding,
            "xml_declaration_preserved": had_declaration,
            "reparsed": True,
            "canonical_reparse_verified": True,
            "edits": operation_audit,
        })

    @staticmethod
    def _parser() -> etree.XMLParser:
        return etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True,
            recover=False, huge_tree=False, remove_blank_text=False,
            strip_cdata=False,
        )

    @staticmethod
    def _reject_entities(data: bytes, label: str) -> None:
        folded = data.upper()
        if b"<!DOCTYPE" in folded or b"<!ENTITY" in folded:
            raise ValueError(f"DTD/entity declarations are not allowed in {label}")

    @classmethod
    def _parse_fragment(cls, content: str) -> tuple[etree._Element, ...]:
        encoded = content.encode("utf-8")
        if not encoded or len(encoded) > MAX_OIV_FRAGMENT_BYTES:
            raise ValueError(
                f"OIV XML fragments must be 1-{MAX_OIV_FRAGMENT_BYTES:,} bytes"
            )
        cls._reject_entities(encoded, "OIV XML fragment")
        if b"<?xml" in encoded.lower():
            raise ValueError("OIV XML fragments cannot contain an XML declaration")
        try:
            wrapper = etree.fromstring(
                b"<allin1-fragment>" + encoded + b"</allin1-fragment>",
                parser=cls._parser(),
            )
        except etree.XMLSyntaxError as exc:
            raise ValueError(f"Invalid OIV XML fragment: {exc}") from exc
        if wrapper.text and wrapper.text.strip():
            raise ValueError("OIV XML fragments must contain elements, not raw text")
        children = tuple(wrapper)
        elements = tuple(item for item in children if isinstance(item.tag, str))
        if len(elements) != len(children):
            raise ValueError("OIV XML fragments cannot contain comments or instructions")
        if not elements or len(elements) > MAX_OIV_FRAGMENT_NODES:
            raise ValueError(
                f"OIV XML fragments must contain 1-{MAX_OIV_FRAGMENT_NODES:,} elements"
            )
        if any(item.tail and item.tail.strip() for item in wrapper):
            raise ValueError("OIV XML fragments cannot contain top-level raw text")
        node_count = sum(1 for item in elements for _ in item.iter())
        if node_count > MAX_OIV_FRAGMENT_NODES:
            raise ValueError(
                f"OIV XML fragments are limited to {MAX_OIV_FRAGMENT_NODES:,} nodes"
            )
        return elements

    @staticmethod
    def _validate_xpath(xpath: str) -> None:
        if not xpath or len(xpath) > MAX_OIV_XPATH_CHARS or "\0" in xpath:
            raise ValueError(
                f"OIV XPath must be 1-{MAX_OIV_XPATH_CHARS:,} characters"
            )
        if any(token in xpath for token in ("|", "$", "::", "`")):
            raise ValueError("OIV XPath unions, variables, axes, and backticks are blocked")
        folded = xpath.casefold()
        if any(token in folded for token in (
            "namespace-uri(", "local-name(", "processing-instruction(",
            "comment(", "document(", "system-property(",
        )):
            raise ValueError("OIV XPath contains a blocked node or extension function")
        if xpath.count("//") > 4 or xpath.count("[") > 32 or xpath.count("(") > 32:
            raise ValueError("OIV XPath exceeds the structural complexity limit")
        try:
            etree.XPath(xpath, smart_strings=False)
        except etree.XPathSyntaxError as exc:
            raise ValueError(f"Invalid OIV XPath: {exc}") from exc

    @staticmethod
    def _select(tree: etree._ElementTree, xpath: str) -> list[object]:
        try:
            result = tree.xpath(xpath, smart_strings=False)
        except etree.XPathError as exc:
            raise ValueError(f"OIV XPath evaluation failed: {exc}") from exc
        return result if isinstance(result, list) else [result]

    @staticmethod
    def _casefold_xpath(xpath: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = match.group("value")
            if not any(character.isalpha() for character in value):
                return match.group(0)
            quote = "'" if '"' in value else '"'
            return (
                f"translate({match.group('lhs')},'{_LOWER}','{_UPPER}')="
                f"{quote}{value.upper()}{quote}"
            )

        return _STRING_EQUALITY.sub(replace, xpath)

    @staticmethod
    def _canonical_sha256(tree: etree._ElementTree) -> str:
        canonical = etree.tostring(
            tree, method="c14n", exclusive=False, with_comments=True,
        )
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _node_sha256(node: etree._Element) -> str:
        canonical = etree.tostring(
            node, method="c14n", exclusive=False, with_comments=True,
        )
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _count_nodes(root: etree._Element, label: str) -> int:
        count = sum(1 for _ in root.iter())
        if count > MAX_OIV_XML_NODES:
            raise ValueError(
                f"XML source {label} exceeds {MAX_OIV_XML_NODES:,} nodes"
            )
        return count


def edit_to_dict(edit: OivXmlEdit) -> dict[str, str]:
    """Stable JSON representation used by OIV inspection reports."""
    return asdict(edit)
