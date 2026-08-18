"""Safe, structured comparison and round-trip validation for authored GTA metadata."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree


MAX_META_BYTES = 64 * 1024 * 1024
_IDENTITY_ATTRIBUTES = ("name", "type", "key", "id", "hash", "modelName")


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False, no_network=True, recover=False,
        remove_blank_text=True, huge_tree=False,
    )


def _read_xml(path: str | Path) -> tuple[Path, etree._ElementTree]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Metadata file not found: {source}")
    size = source.stat().st_size
    if size > MAX_META_BYTES:
        raise ValueError("Metadata file exceeds the 64 MiB structured-XML safety limit")
    try:
        tree = etree.parse(str(source), parser=_safe_parser())
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ValueError(
            f"Metadata is not safe, well-formed XML: {source.name}: {exc}. "
            "Binary PSO/RBF resources must first be exported with Native Asset Viewer."
        ) from exc
    if tree.docinfo.doctype:
        raise ValueError("Metadata containing a DTD is not accepted")
    return source, tree


def _canonical(tree: etree._ElementTree) -> bytes:
    return etree.tostring(tree, method="c14n", with_comments=False)


def _segment(element: etree._Element) -> str:
    parent = element.getparent()
    siblings = (
        [item for item in parent if item.tag == element.tag] if parent is not None else [element]
    )
    identity = next(
        (f"@{name}={element.get(name)!r}" for name in _IDENTITY_ATTRIBUTES
         if element.get(name) is not None),
        None,
    )
    if identity:
        return f"{element.tag}[{identity}]"
    return f"{element.tag}[{siblings.index(element) + 1}]"


def _flatten(tree: etree._ElementTree) -> dict[str, str]:
    values: dict[str, str] = {}

    def visit(element: etree._Element, parent_path: str) -> None:
        path = f"{parent_path}/{_segment(element)}"
        text = (element.text or "").strip()
        if text:
            values[f"{path}/#text"] = text
        for name, value in sorted(element.attrib.items()):
            values[f"{path}/@{name}"] = value
        if len(element) == 0 and not text and not element.attrib:
            values[f"{path}/#empty"] = ""
        for child in element:
            if isinstance(child.tag, str):
                visit(child, path)

    root = tree.getroot()
    if root is None:
        raise ValueError("Metadata XML has no root element")
    visit(root, "")
    return values


@dataclass(frozen=True)
class MetaChange:
    path: str
    kind: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class MetaDiffReport:
    before: Path
    after: Path
    before_root: str
    after_root: str
    changes: tuple[MetaChange, ...]
    before_sha256: str
    after_sha256: str

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "before": str(self.before), "after": str(self.after),
            "before_root": self.before_root, "after_root": self.after_root,
            "before_canonical_sha256": self.before_sha256,
            "after_canonical_sha256": self.after_sha256,
            "changed": self.changed, "change_count": len(self.changes),
            "changes": [asdict(change) for change in self.changes],
        }

    def write(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.casefold() == ".md":
            lines = [
                "# Structured META/XML diff", "",
                f"- Before: `{self.before}`", f"- After: `{self.after}`",
                f"- Semantic changes: **{len(self.changes)}**", "",
                "| Kind | Path | Before | After |", "|---|---|---|---|",
            ]
            for change in self.changes:
                escaped = [
                    str(value if value is not None else "—").replace("|", "\\|")
                    for value in (change.path, change.before, change.after)
                ]
                lines.append(
                    f"| {change.kind} | `{escaped[0]}` | `{escaped[1]}` | `{escaped[2]}` |"
                )
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target


def diff_meta(before: str | Path, after: str | Path) -> MetaDiffReport:
    """Compare XML semantically, ignoring insignificant whitespace and formatting."""
    before_path, before_tree = _read_xml(before)
    after_path, after_tree = _read_xml(after)
    left = _flatten(before_tree)
    right = _flatten(after_tree)
    changes: list[MetaChange] = []
    for path in sorted(left.keys() | right.keys(), key=str.casefold):
        old = left.get(path)
        new = right.get(path)
        if old == new:
            continue
        kind = "added" if path not in left else "removed" if path not in right else "changed"
        changes.append(MetaChange(path=path, kind=kind, before=old, after=new))
    import hashlib

    return MetaDiffReport(
        before=before_path, after=after_path,
        before_root=str(before_tree.getroot().tag),
        after_root=str(after_tree.getroot().tag),
        changes=tuple(changes),
        before_sha256=hashlib.sha256(_canonical(before_tree)).hexdigest(),
        after_sha256=hashlib.sha256(_canonical(after_tree)).hexdigest(),
    )


def validate_meta_roundtrip(
    source: str | Path, *, serialized_output: str | Path | None = None,
) -> dict[str, Any]:
    """Serialize and reparse authored XML, proving canonical semantic equivalence."""
    path, tree = _read_xml(source)
    before = _canonical(tree)
    serialized = etree.tostring(
        tree, encoding="utf-8", xml_declaration=True, pretty_print=True,
    )
    try:
        reparsed = etree.ElementTree(etree.fromstring(serialized, parser=_safe_parser()))
    except etree.XMLSyntaxError as exc:  # defensive; the serializer should be reversible
        raise RuntimeError(f"Serialized metadata could not be reparsed: {exc}") from exc
    after = _canonical(reparsed)
    if serialized_output is not None:
        destination = Path(serialized_output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(serialized)
    import hashlib

    return {
        "schema_version": 1, "source": str(path),
        "root": str(tree.getroot().tag),
        "element_count": sum(1 for _ in tree.iter() if isinstance(_.tag, str)),
        "serialized_bytes": len(serialized),
        "canonical_sha256_before": hashlib.sha256(before).hexdigest(),
        "canonical_sha256_after": hashlib.sha256(after).hexdigest(),
        "semantically_equivalent": before == after,
        "serialized_output": (
            str(Path(serialized_output).expanduser().resolve())
            if serialized_output is not None else None
        ),
    }
