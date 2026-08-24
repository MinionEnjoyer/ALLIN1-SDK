"""Read-only OIV operation planning and explicit managed-package conversion."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from allin1_sdk.addon_importer import (
    MAX_XML_BYTES,
    AddonPackageInspector,
    PackageAssetReader,
    _local_name,
    _parse_xml,
    _safe_member_path,
)
from allin1_sdk.oiv_xml import (
    MAX_OIV_XML_BYTES,
    OivXmlEdit,
    OivXmlMergeEngine,
)
from allin1_sdk.oiv_text import (
    MAX_OIV_TEXT_BYTES,
    OivTextEdit,
    OivTextMergeEngine,
)
from allin1_sdk.native_assets import NativeAssetInspector


def _fold_archive_chain(chain: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.casefold() for part in chain)


def _archive_chain_starts_with(
    chain: tuple[str, ...], prefix: tuple[str, ...],
) -> bool:
    return _fold_archive_chain(chain[:len(prefix)]) == _fold_archive_chain(prefix)


@dataclass(frozen=True)
class OivFinding:
    severity: str
    code: str
    message: str
    operation: int | None = None


@dataclass(frozen=True)
class OivOperation:
    number: int
    kind: str
    source: str
    target: str
    archives: tuple[str, ...]
    supported: bool
    detail: str
    creates_archive: bool = False
    edits: tuple[OivXmlEdit | OivTextEdit, ...] = ()
    creates_file: bool = False


@dataclass(frozen=True)
class OivPlan:
    source: Path
    name: str
    version: str
    author: str
    editions: tuple[str, ...]
    operations: tuple[OivOperation, ...]
    findings: tuple[OivFinding, ...]
    assembly_sha256: str = ""
    format_version: str = ""

    @property
    def add_operations(self) -> tuple[OivOperation, ...]:
        return tuple(item for item in self.operations if item.kind == "add")

    @property
    def xml_operations(self) -> tuple[OivOperation, ...]:
        return tuple(item for item in self.operations if item.kind == "xml")

    @property
    def text_operations(self) -> tuple[OivOperation, ...]:
        return tuple(item for item in self.operations if item.kind == "text")

    @property
    def pso_operations(self) -> tuple[OivOperation, ...]:
        return tuple(item for item in self.operations if item.kind == "pso")

    @property
    def rpf_batch_operations(self) -> tuple[OivOperation, ...]:
        created = self._created_archive_chains()
        return tuple(
            item for item in self.operations
            if item.kind in {"add", "delete"} and item.archives
            and not any(
                _archive_chain_starts_with(item.archives, chain)
                for chain in created
            )
        )

    @property
    def created_archive_operations(self) -> tuple[OivOperation, ...]:
        return tuple(
            item for item in self.operations
            if item.kind == "archive" and item.creates_archive
        )

    def _created_archive_chains(self) -> tuple[tuple[str, ...], ...]:
        return tuple(
            item.archives + (item.target,) for item in self.created_archive_operations
        )

    @property
    def translatable(self) -> bool:
        actionable = tuple(
            item for item in self.operations
            if item.kind in {"add", "delete", "xml", "text", "pso"}
        )
        created = self._created_archive_chains()
        structured_inside_created = all(
            any(
                _archive_chain_starts_with(item.archives, chain)
                for chain in created
            )
            for item in self.xml_operations + self.text_operations + self.pso_operations
        )
        return bool(actionable) and not any(
            not item.supported for item in self.operations
        ) and structured_inside_created and not any(
            item.severity == "error" for item in self.findings
        )

    @property
    def recipe_supported(self) -> bool:
        actionable = tuple(
            item for item in self.operations
            if item.kind in {"add", "delete", "xml", "text", "pso"}
        )
        return bool(actionable) and not any(
            not item.supported for item in self.operations
        ) and not any(item.severity == "error" for item in self.findings)

    @property
    def xml_compilable(self) -> bool:
        """Whether the recipe is an XML-only existing-RPF compile workflow."""
        return bool(self.xml_operations) and not (
            self.text_operations or self.pso_operations
        ) and (
            self.rpf_recipe_compilable
        )

    @property
    def rpf_recipe_compilable(self) -> bool:
        """Whether one selected existing outer RPF can compile the full recipe."""
        actionable = tuple(
            item for item in self.operations if item.kind != "archive"
        )
        outer_archives = {
            item.archives[0].casefold() for item in actionable if item.archives
        }
        return bool(
            self.xml_operations or self.text_operations or self.pso_operations
        ) and self.recipe_supported and not (
            self.created_archive_operations
        ) and len(outer_archives) == 1 and all(
            item.kind in {"add", "delete", "xml", "text", "pso"}
            and bool(item.archives)
            for item in actionable
        )

    @property
    def managed_exportable(self) -> bool:
        return (
            self.translatable and bool(self.add_operations)
            and not self.created_archive_operations and all(
            item.kind in {"archive", "add"}
            and (item.kind != "add" or len(item.archives) <= 1)
            and (item.kind != "archive" or not item.archives)
            for item in self.operations
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source), "name": self.name,
            "version": self.version, "author": self.author,
            "format_version": self.format_version,
            "editions": list(self.editions),
            "assembly_sha256": self.assembly_sha256,
            "recipe_supported": self.recipe_supported,
            "translatable": self.translatable,
            "xml_compilable": self.xml_compilable,
            "rpf_recipe_compilable": self.rpf_recipe_compilable,
            "managed_exportable": self.managed_exportable,
            "operations": [asdict(item) for item in self.operations],
            "findings": [asdict(item) for item in self.findings],
        }

    def to_markdown(self) -> str:
        result = (
            "MANAGED EXPORT READY" if self.managed_exportable
            else "CREATED RPF EXPORT READY" if self.translatable
            and self.created_archive_operations
            else "VERIFIED XML COMPILE READY" if self.xml_compilable
            else "VERIFIED RPF RECIPE COMPILE READY" if self.rpf_recipe_compilable
            else "ATOMIC RPF EXPORT READY" if self.translatable
            else "REVIEW REQUIRED"
        )
        lines = [
            f"# OIV operation plan: {self.name}", "",
            f"- Source: `{self.source}`",
            f"- Version: `{self.version or 'unknown'}`",
            f"- OIV format: `{self.format_version or 'unspecified'}`",
            f"- Author: `{self.author or 'unknown'}`",
            f"- Editions: {', '.join(value.title() for value in self.editions)}",
            f"- assembly.xml SHA-256: `{self.assembly_sha256}`",
            f"- Result: **{result}**", "",
            "## Ordered operations", "",
            "| # | Action | Archive | Source | Target | Translation |",
            "|---:|---|---|---|---|---|",
        ]
        for item in self.operations:
            archive = " → ".join(item.archives) or "filesystem"
            status = "managed" if item.supported else "manual review"
            if item.kind in {"xml", "text", "pso"} and item.supported:
                status = "verified compile"
            lines.append(
                f"| {item.number} | {item.kind} | `{archive}` | "
                f"`{item.source or '-'}` | `{item.target or '-'}` | {status} |"
            )
        lines.extend(["", "## Findings", ""])
        if not self.findings:
            lines.append("No recipe blockers were found.")
        for item in self.findings:
            location = f" (operation {item.operation})" if item.operation else ""
            lines.append(
                f"- **{item.severity.upper()} `{item.code}`**{location}: {item.message}"
            )
        lines.extend([
            "", "## Safety boundary", "",
            "This report does not execute the OIV. Managed export is available only "
            "when every declared operation can be represented as an owned file copy, "
            "exact RPF-entry transaction, atomic nested-RPF batch, or a deterministic "
            "XML, bounded line-oriented payload, or source-aware native PSO rebuild "
            "compiled against an explicitly selected, hash-bound archive. Wildcard "
            "text masks, PSO edits inside newly created archives, unbounded archive "
            "creation, and unknown commands remain blocked.", "",
        ])
        return "\n".join(lines)

    def write_report(self, destination: str | Path) -> Path:
        path = Path(destination).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        return path


class OivWorkbench:
    """Parse actual OIV 2.x operations without executing package code."""

    _UNSUPPORTED = {"defragmentation"}
    _PSO_SUFFIXES = {".pso", ".ymap", ".ymf", ".ymt", ".ytyp"}
    _TEXT_SUFFIXES = {
        ".cfg", ".conf", ".csv", ".dat", ".ini", ".json", ".list",
        ".log", ".meta", ".txt", ".xml",
    }

    def inspect(self, source: str | Path) -> OivPlan:
        package = Path(source).expanduser().resolve()
        scan = AddonPackageInspector().inspect(package)
        errors = [item for item in scan.findings if item.severity == "error"]
        if errors:
            raise ValueError(errors[0].message)
        entries = {item.path.casefold() for item in scan.entries}
        try:
            assembly = PackageAssetReader(package).read(
                "assembly.xml", limit=MAX_XML_BYTES,
            )
        except FileNotFoundError as exc:
            raise ValueError("OIV package does not contain root assembly.xml") from exc
        root = _parse_xml(assembly.data, "assembly.xml")
        if _local_name(root.tag).casefold() != "package":
            raise ValueError("OIV assembly.xml root must be <package>")

        metadata = self._child(root, "metadata")
        format_version = root.attrib.get("version", "").strip()
        name = self._text(metadata, "name") or package.stem
        version_node = self._child(metadata, "version")
        major = self._text(version_node, "major")
        minor = self._text(version_node, "minor")
        tag = self._text(version_node, "tag")
        version = ".".join(value for value in (major, minor) if value) or tag
        author_node = self._child(metadata, "author")
        author = self._text(author_node, "displayName")
        game_version = next((
            (item.text or "").strip().casefold() for item in root.iter()
            if _local_name(item.tag).casefold() == "gameversion"
        ), "")
        editions = (
            ("enhanced",) if game_version in {"enhanced", "gen9"}
            else ("legacy",) if game_version in {"legacy", "gen8"}
            else ("legacy", "enhanced")
        )

        operations: list[OivOperation] = []
        findings: list[OivFinding] = []
        content = self._child(root, "content")
        if content is None:
            findings.append(OivFinding(
                "error", "missing_content", "assembly.xml has no <content> recipe.",
            ))
        else:
            for child in content:
                self._walk(child, (), (), entries, operations, findings)
        operations = list(self._validate_created_recipe_operations(
            tuple(operations), findings,
        ))
        if any(item.kind in {"xml", "text", "pso"} for item in operations) and format_version not in {
            "2.1", "2.2",
        }:
            code = (
                "unsupported_oiv_xml_format"
                if any(item.kind == "xml" for item in operations)
                and not any(item.kind in {"text", "pso"} for item in operations)
                else "unsupported_oiv_recipe_format"
            )
            findings.append(OivFinding(
                "error", code,
                "Verified XML/text/PSO compilation requires OIV package format 2.1 or 2.2.",
            ))
        if not any(item.kind in {"add", "xml", "text", "pso"} for item in operations):
            findings.append(OivFinding(
                "warning", "no_managed_payload",
                "The recipe contains no file additions that ALLIN1 can own.",
            ))
        return OivPlan(
            package, name, version, author, editions,
            tuple(operations), tuple(findings),
            hashlib.sha256(assembly.data).hexdigest(),
            format_version,
        )

    @staticmethod
    def _validate_created_recipe_operations(
        operations: tuple[OivOperation, ...], findings: list[OivFinding],
    ) -> tuple[OivOperation, ...]:
        """Prove ordered file state for edits inside newly created archives."""
        created = tuple(
            item.archives + (item.target,) for item in operations
            if item.kind == "archive" and item.creates_archive and item.supported
        )
        available: set[tuple[tuple[str, ...], str]] = set()
        invalid: dict[int, tuple[str, str]] = {}

        def is_inside_created(item: OivOperation) -> bool:
            return any(
                _archive_chain_starts_with(item.archives, chain)
                for chain in created
            )

        for item in operations:
            if item.kind not in {"add", "delete", "xml", "text", "pso"} or not is_inside_created(item):
                continue
            identity = (
                tuple(part.casefold() for part in item.archives),
                item.target.casefold(),
            )
            if item.kind == "add":
                if identity in available:
                    invalid[item.number] = (
                        "duplicate_created_entry",
                        "The created-RPF recipe adds the same entry more than once; "
                        "use one source followed by explicit supported edits.",
                    )
                else:
                    available.add(identity)
                continue
            if item.kind == "text" and item.creates_file and identity not in available:
                first_action = item.edits[0].action.casefold() if item.edits else ""
                if first_action != "add":
                    invalid[item.number] = (
                        "created_text_initial_edit",
                        "A newly created text entry must begin with an add-line command "
                        "before it can be selected, replaced, or deleted.",
                    )
                else:
                    available.add(identity)
                continue
            if identity not in available:
                invalid[item.number] = (
                    "created_entry_not_available",
                    f"The created-RPF {item.kind} target does not exist at that point "
                    "in the ordered recipe.",
                )
                continue
            if item.kind == "delete":
                available.remove(identity)

        if not invalid:
            return operations
        validated: list[OivOperation] = []
        for item in operations:
            error = invalid.get(item.number)
            if error is None:
                validated.append(item)
                continue
            code, message = error
            findings.append(OivFinding("error", code, message, item.number))
            validated.append(replace(item, supported=False))
        return tuple(validated)

    def _walk(
        self, node: ET.Element, archives: tuple[str, ...],
        created_archives: tuple[bool, ...], entries: set[str],
        operations: list[OivOperation], findings: list[OivFinding],
    ) -> None:
        kind = _local_name(node.tag).casefold()
        number = len(operations) + 1
        if kind == "archive":
            raw_path = node.attrib.get("path", "").strip()
            path = self._target_path(raw_path, mods=not archives)
            create = (
                node.attrib.get("createIfNotExist", "false").strip().casefold()
                == "true"
            )
            nested = archives + ((path or raw_path),)
            parent_is_created = any(created_archives)
            safe_archive = bool(path) and path.casefold().endswith(".rpf")
            safe_creation_target = (
                parent_is_created or len(archives) <= 1
            ) and (
                bool(archives) or (
                    self._managed_file_target(path)
                    and path.casefold().startswith("mods/")
                )
            )
            supported = bool(
                safe_archive and len(nested) <= 9
                and ((create and safe_creation_target) or (not create and not parent_is_created))
            )
            operations.append(OivOperation(
                number, kind, "", path, archives, supported,
                "RPF operation container" + ("; creates archive" if create else ""),
                create,
            ))
            if not supported:
                code = (
                    "archive_creation_depth" if create and len(archives) > 1
                    and not parent_is_created else "archive_creation_target" if create
                    else "missing_archive_creation" if parent_is_created
                    else "archive_depth"
                    if len(nested) > 9 else "unsafe_archive"
                )
                findings.append(OivFinding(
                    "error", code,
                    "The archive container is unsafe, has ambiguous creation ancestry, "
                    "or is deeper than the eight nested levels supported by atomic RPF "
                    "transactions.",
                    number,
                ))
            for child in node:
                self._walk(
                    child, nested, created_archives + (create,),
                    entries, operations, findings,
                )
            return

        source = node.attrib.get("source", "").strip() if kind == "add" else ""
        target = (node.text or "").strip() if kind in {"add", "delete"} else (
            node.attrib.get("path", "").strip()
        )
        supported = False
        detail = "OIV operation requires manual review"
        edits: tuple[OivXmlEdit | OivTextEdit, ...] = ()
        creates_file = False
        if kind == "add":
            try:
                source_path = _safe_member_path(source).as_posix()
                member = f"content/{source_path}".casefold()
                target_path = self._target_path(target, mods=not archives)
                supported = bool(target_path) and member in entries and len(archives) <= 9
                if not archives and target_path and not self._managed_file_target(target_path):
                    supported = False
                    findings.append(OivFinding(
                        "error", "unsupported_destination",
                        f"The destination is outside ALLIN1's managed roots: {target_path}",
                        number,
                    ))
                source = f"content/{source_path}"
                target = target_path
                detail = "Managed file copy" if not archives else "Exact RPF entry replacement"
                if any(created_archives):
                    detail = "New RPF build payload"
                if member not in entries:
                    findings.append(OivFinding(
                        "error", "missing_oiv_source",
                        f"Recipe source is absent from the package: content/{source_path}",
                        number,
                    ))
                if not target_path:
                    findings.append(OivFinding(
                        "error", "unsafe_target", "Recipe target is empty or unsafe.", number,
                    ))
            except ValueError as exc:
                findings.append(OivFinding(
                    "error", "unsafe_source", str(exc), number,
                ))
        elif kind == "delete":
            target_path = self._target_path(target, mods=False)
            supported = (
                bool(archives) and bool(target_path) and len(archives) <= 9
                and not target_path.casefold().endswith(".rpf")
            )
            target = target_path
            detail = (
                "Ordered new-archive entry deletion"
                if any(created_archives) else "Exact RPF entry deletion"
            )
            if not supported:
                findings.append(OivFinding(
                    "error", "unsupported_delete",
                    "Delete requires one exact non-container entry inside a bounded "
                    "existing or newly created RPF archive tree.", number,
                ))
        elif kind in {"xml", "pso"}:
            target_path = self._target_path(target, mods=False)
            parsed_edits: list[OivXmlEdit] = []
            recipe_error: str | None = None
            unknown_parent_attributes = set(node.attrib).difference({"path"})
            if unknown_parent_attributes:
                recipe_error = (
                    f"Unknown OIV {kind.upper()} attributes: "
                    + ", ".join(sorted(unknown_parent_attributes))
                )
            for child in node:
                if recipe_error:
                    break
                action = _local_name(child.tag).casefold()
                if action not in {"add", "replace", "remove"}:
                    recipe_error = (
                        f"Unknown OIV {kind.upper()} child command <{action}>"
                    )
                    break
                allowed_attributes = (
                    {"xpath", "append"} if action == "add" else {"xpath"}
                )
                unknown_attributes = set(child.attrib).difference(allowed_attributes)
                if unknown_attributes:
                    recipe_error = (
                        f"Unknown OIV {kind.upper()} {action} attributes: "
                        + ", ".join(sorted(unknown_attributes))
                    )
                    break
                if child.text and child.text.strip():
                    recipe_error = (
                        f"OIV {kind.upper()} edit content must contain elements, not raw text"
                    )
                    break
                content = "".join(
                    ET.tostring(item, encoding="unicode") for item in child
                )
                edit = OivXmlEdit(
                    action=action,
                    xpath=child.attrib.get("xpath", "").strip(),
                    append=(
                        child.attrib.get("append", "Last").strip()
                        if action == "add" else ""
                    ),
                    content=content,
                )
                try:
                    OivXmlMergeEngine.validate_recipe_edit(edit)
                except ValueError as exc:
                    recipe_error = str(exc)
                    break
                parsed_edits.append(edit)
            edits = tuple(parsed_edits)
            suffix = PurePosixPath(target_path).suffix.casefold() if target_path else ""
            suffixes = (
                {".xml", ".meta"} if kind == "xml"
                else self._PSO_SUFFIXES
            )
            safe_context = bool(
                archives and len(archives) <= 9
                and target_path and suffix in suffixes
                and (kind != "pso" or not any(created_archives))
            )
            supported = bool(safe_context and edits and recipe_error is None)
            target = target_path
            detail = (
                f"Compile {len(edits)} deterministic XPath edit(s) into a verified "
                + ("native resource" if kind == "pso" else "payload")
            )
            if recipe_error:
                findings.append(OivFinding(
                    "error", f"invalid_{kind}_recipe", recipe_error, number,
                ))
            elif not safe_context:
                findings.append(OivFinding(
                    "error", f"unsupported_{kind}",
                    (
                        "XML commands require a textual .xml/.meta entry inside one "
                        "bounded existing or newly created RPF archive tree."
                        if kind == "xml" else
                        "PSO commands require a supported native resource inside one "
                        "bounded existing RPF archive tree."
                    ), number,
                ))
            elif not edits:
                findings.append(OivFinding(
                    "error", f"empty_{kind}_recipe",
                    f"The {kind.upper()} operation contains no add, replace, or remove commands.",
                    number,
                ))
        elif kind == "text":
            target_path = self._target_path(target, mods=False)
            parsed_edits: list[OivTextEdit] = []
            recipe_error: str | None = None
            creates_file = (
                node.attrib.get("createIfNotExist", "false").strip().casefold()
                == "true"
            )
            unknown_parent_attributes = set(node.attrib).difference({
                "path", "createIfNotExist",
            })
            if unknown_parent_attributes:
                recipe_error = (
                    "Unknown OIV text attributes: "
                    + ", ".join(sorted(unknown_parent_attributes))
                )
            create_value = (
                node.attrib.get("createIfNotExist", "false").strip().casefold()
            )
            if create_value not in {"true", "false"}:
                recipe_error = "OIV text createIfNotExist must be True or False"
            for child in node:
                if recipe_error:
                    break
                action = _local_name(child.tag).casefold()
                allowed_attributes = {
                    "add": set(),
                    "insert": {"where", "line", "condition"},
                    "replace": {"line", "condition"},
                    "delete": {"condition"},
                }.get(action)
                if allowed_attributes is None:
                    recipe_error = f"Unknown OIV text child command <{action}>"
                    break
                unknown_attributes = set(child.attrib).difference(allowed_attributes)
                if unknown_attributes:
                    recipe_error = (
                        f"Unknown OIV text {action} attributes: "
                        + ", ".join(sorted(unknown_attributes))
                    )
                    break
                if len(child):
                    recipe_error = "OIV text edit content cannot contain child elements"
                    break
                raw_text = child.text or ""
                edit = OivTextEdit(
                    action=action,
                    content="" if action == "delete" else raw_text,
                    line=(
                        raw_text if action == "delete"
                        else child.attrib.get("line", "")
                    ),
                    condition=child.attrib.get("condition", ""),
                    where=child.attrib.get("where", ""),
                )
                try:
                    OivTextMergeEngine.validate_recipe_edit(edit)
                except ValueError as exc:
                    recipe_error = str(exc)
                    break
                parsed_edits.append(edit)
            edits = tuple(parsed_edits)
            suffix = PurePosixPath(target_path).suffix.casefold() if target_path else ""
            safe_context = bool(
                archives and len(archives) <= 9 and target_path
                and suffix in self._TEXT_SUFFIXES
            )
            supported = bool(safe_context and edits and recipe_error is None)
            target = target_path
            detail = (
                f"Compile {len(edits)} bounded line edit(s) into a verified payload"
            )
            if recipe_error:
                findings.append(OivFinding(
                    "error", "invalid_text_recipe", recipe_error, number,
                ))
            elif not safe_context:
                findings.append(OivFinding(
                    "error", "unsupported_text",
                    "Text commands require a recognized textual entry inside one "
                    "bounded existing or newly created RPF archive tree.", number,
                ))
            elif not edits:
                findings.append(OivFinding(
                    "error", "empty_text_recipe",
                    "The text operation contains no bounded line commands.", number,
                ))
        elif kind in self._UNSUPPORTED:
            findings.append(OivFinding(
                "error", f"unsupported_{kind}",
                f"The {kind} operation cannot be represented by the managed package schema.",
                number,
            ))
        else:
            findings.append(OivFinding(
                "error", "unknown_operation",
                f"Unknown OIV operation <{kind}> is blocked.", number,
            ))
        operations.append(OivOperation(
            number, kind, source, target, archives, supported, detail,
            False, edits, creates_file,
        ))

    @staticmethod
    def _target_path(value: str, *, mods: bool) -> str:
        normalized = value.replace("\\", "/").strip(" /\t\r\n")
        try:
            path = _safe_member_path(normalized).as_posix()
        except ValueError:
            return ""
        lowered = path.casefold()
        if mods and not lowered.startswith("mods/") and (
            lowered.startswith(("update/", "x64/")) or lowered.endswith(".rpf")
        ):
            path = f"mods/{path}"
        return path

    @staticmethod
    def _managed_file_target(value: str) -> bool:
        path = PurePosixPath(value)
        lowered = tuple(part.casefold() for part in path.parts)
        if len(lowered) == 1:
            return path.suffix.casefold() in {
                ".asi", ".dll", ".ini", ".toml", ".addon64",
            }
        return lowered[0] in {"scripts", "mods", "reshade-shaders"}

    @staticmethod
    def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
        if parent is None:
            return None
        expected = name.casefold()
        return next((
            item for item in parent
            if _local_name(item.tag).casefold() == expected
        ), None)

    @classmethod
    def _text(cls, parent: ET.Element | None, name: str) -> str:
        child = cls._child(parent, name)
        return (child.text or "").strip() if child is not None else ""

    def compile_xml_rpf_bundle(
        self, plan: OivPlan, archive: str | Path, destination: str | Path, *,
        service: Any,
    ) -> tuple[Path, Path]:
        """Backward-compatible XML-only entry point for guarded compilation."""
        if not plan.xml_compilable:
            raise ValueError(
                "OIV recipe is not a fully supported single-archive XML workflow"
            )
        return self.compile_rpf_recipe_bundle(
            plan, archive, destination, service=service,
        )

    def compile_rpf_recipe_bundle(
        self, plan: OivPlan, archive: str | Path, destination: str | Path, *,
        service: Any,
    ) -> tuple[Path, Path]:
        """Compile one supported XML/text/PSO recipe into an inert RPF plan.

        The caller explicitly selects the existing outer RPF. Recipe operations are
        evaluated in order against extracted payloads, then coalesced into one final
        add/replace/delete per entry. The archive itself is never written here.
        """
        if not plan.rpf_recipe_compilable:
            raise ValueError(
                "OIV recipe is not a fully supported single-archive XML/text/PSO workflow"
            )
        selected_archive = Path(archive).expanduser().resolve()
        if not selected_archive.is_file() or selected_archive.suffix.casefold() != ".rpf":
            raise ValueError(
                "OIV recipe compilation requires one existing loose .rpf archive"
            )
        actionable = tuple(
            item for item in plan.operations if item.kind != "archive"
        )
        expected_outer = next(
            item.archives[0] for item in actionable if item.archives
        )
        if selected_archive.name.casefold() != PurePosixPath(expected_outer).name.casefold():
            raise ValueError(
                "Selected archive filename does not match the OIV recipe target: "
                f"expected {PurePosixPath(expected_outer).name}"
            )
        current_assembly = PackageAssetReader(plan.source).read(
            "assembly.xml", limit=MAX_XML_BYTES,
        )
        if hashlib.sha256(current_assembly.data).hexdigest() != plan.assembly_sha256:
            raise RuntimeError("OIV assembly.xml changed after the operation plan was created")
        package_source_sha256 = self._sha256(plan.source) if plan.source.is_file() else None
        if plan.pso_operations and not all(
            hasattr(service, attribute) for attribute in ("project_root", "gta_path")
        ):
            raise ValueError(
                "OIV PSO compilation requires an RPF service bound to the SDK and "
                "matching GTA V installation"
            )

        root = Path(destination).expanduser().resolve()
        if root.exists() or root.is_symlink():
            raise ValueError("OIV recipe bundle destination must not already exist")
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        work_root = root / ".working"
        payload_root = root / "payloads"
        work_root.mkdir()
        payload_root.mkdir()
        try:
            index = service.index(selected_archive)
            if Path(index.source).resolve() != selected_archive:
                raise RuntimeError("RPF index source does not match the selected archive")
            indexed = {
                (item.archive_path.casefold(), item.path.casefold()): item
                for item in index.entries if item.kind != "directory"
            }
            if len(indexed) != sum(
                item.kind != "directory" for item in index.entries
            ):
                raise ValueError("Selected RPF has case-colliding entry identities")
            states: dict[tuple[str, str], dict[str, Any]] = {}
            xml_merge_audits: list[dict[str, object]] = []
            text_merge_audits: list[dict[str, object]] = []
            pso_compile_audits: list[dict[str, object]] = []
            recipe_events: list[dict[str, object]] = []
            native_inspector = (
                NativeAssetInspector(service.project_root, service.gta_path)
                if plan.pso_operations else None
            )

            def load_state(operation: OivOperation) -> tuple[tuple[str, str], dict[str, Any]]:
                nested = "!".join(operation.archives[1:])
                identity = (nested.casefold(), operation.target.casefold())
                if identity in states:
                    return identity, states[identity]
                entry = indexed.get(identity)
                if entry is not None and entry.kind == "archive":
                    raise ValueError(
                        "OIV structured workflow cannot replace a nested archive container: "
                        f"{nested}::{operation.target}"
                    )
                state_path: Path | None = None
                original_sha256: str | None = None
                original_size = 0
                if entry is not None:
                    state_path = work_root / f"state_{len(states) + 1:04d}{Path(entry.name).suffix}"
                    service.extract(index, entry, state_path)
                    original_size = state_path.stat().st_size
                    original_sha256 = self._sha256(state_path)
                state = {
                    "archive_path": nested,
                    "entry_path": operation.target,
                    "entry": entry,
                    "initial_exists": entry is not None,
                    "original_size": original_size,
                    "original_sha256": original_sha256,
                    "current": state_path,
                    "operations": [],
                }
                states[identity] = state
                return identity, state

            for operation in actionable:
                if operation.archives[0].casefold() != expected_outer.casefold():
                    raise ValueError("OIV recipe compiler supports exactly one outer archive")
                identity, state = load_state(operation)
                event: dict[str, object] = {
                    "oiv_operation": operation.number,
                    "kind": operation.kind,
                    "archive_path": state["archive_path"],
                    "entry": state["entry_path"],
                }
                if operation.kind == "add":
                    suffix = PurePosixPath(operation.target).suffix
                    authored = work_root / f"op_{operation.number:04d}{suffix}"
                    self._copy_member(plan.source, operation.source, authored)
                    state["current"] = authored
                    event.update({
                        "source": operation.source,
                        "source_size": authored.stat().st_size,
                        "source_sha256": self._sha256(authored),
                    })
                elif operation.kind == "delete":
                    if state["current"] is None:
                        raise ValueError(
                            "OIV recipe deletes an entry that does not exist at that point: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    state["current"] = None
                elif operation.kind == "xml":
                    current = state["current"]
                    if current is None:
                        raise ValueError(
                            "OIV XML target does not exist at that point in the recipe: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    current = Path(current)
                    if current.stat().st_size > MAX_OIV_XML_BYTES:
                        raise ValueError(
                            f"OIV XML target exceeds {MAX_OIV_XML_BYTES:,} bytes: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    result = OivXmlMergeEngine.apply(
                        current.read_bytes(), operation.edits,
                        source_name=(
                            f"{state['archive_path']}::{state['entry_path']}"
                            if state["archive_path"] else str(state["entry_path"])
                        ),
                    )
                    compiled = work_root / (
                        f"op_{operation.number:04d}{PurePosixPath(operation.target).suffix}"
                    )
                    compiled.write_bytes(result.data)
                    state["current"] = compiled
                    xml_merge_audits.append({
                        "oiv_operation": operation.number,
                        "archive_path": state["archive_path"],
                        "entry": state["entry_path"],
                        **result.audit,
                    })
                    event.update({
                        "compiled_size": len(result.data),
                        "compiled_sha256": result.audit["output_sha256"],
                        "edits": len(operation.edits),
                    })
                elif operation.kind == "text":
                    current = state["current"]
                    if current is None:
                        if not operation.creates_file:
                            raise ValueError(
                                "OIV text target does not exist at that point in the "
                                f"recipe: {state['archive_path']}::{state['entry_path']}"
                            )
                        current = work_root / (
                            f"op_{operation.number:04d}"
                            f"{PurePosixPath(operation.target).suffix}"
                        )
                        current.write_bytes(b"")
                    current = Path(current)
                    if current.stat().st_size > MAX_OIV_TEXT_BYTES:
                        raise ValueError(
                            f"OIV text target exceeds {MAX_OIV_TEXT_BYTES:,} bytes: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    result = OivTextMergeEngine.apply(
                        current.read_bytes(), operation.edits,
                        source_name=(
                            f"{state['archive_path']}::{state['entry_path']}"
                            if state["archive_path"] else str(state["entry_path"])
                        ),
                        verify_xml=(
                            PurePosixPath(operation.target).suffix.casefold()
                            in {".xml", ".meta"}
                        ),
                    )
                    compiled = work_root / (
                        f"op_{operation.number:04d}"
                        f"{PurePosixPath(operation.target).suffix}"
                    )
                    compiled.write_bytes(result.data)
                    state["current"] = compiled
                    text_merge_audits.append({
                        "oiv_operation": operation.number,
                        "archive_path": state["archive_path"],
                        "entry": state["entry_path"],
                        **result.audit,
                    })
                    event.update({
                        "compiled_size": len(result.data),
                        "compiled_sha256": result.audit["output_sha256"],
                        "edits": len(operation.edits),
                    })
                elif operation.kind == "pso":
                    current = state["current"]
                    if current is None:
                        raise ValueError(
                            "OIV PSO target does not exist at that point in the recipe: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    current = Path(current)
                    suffix = PurePosixPath(operation.target).suffix.casefold()
                    if suffix not in self._PSO_SUFFIXES:
                        raise ValueError(
                            f"OIV PSO target is not a supported native resource: "
                            f"{state['archive_path']}::{state['entry_path']}"
                        )
                    if native_inspector is None:  # pragma: no cover - plan invariant
                        raise RuntimeError("Native PSO compiler was not initialized")
                    workspace = work_root / f"pso_{operation.number:04d}_workspace"
                    native_inspector.export_workspace(
                        current, workspace, edition=index.edition,
                    )
                    manifest = json.loads(
                        (workspace / "native-workspace.json").read_text(encoding="utf-8")
                    )
                    xml_meta = manifest.get("xml")
                    xml_relative = (
                        xml_meta.get("path") if isinstance(xml_meta, dict) else None
                    )
                    if not isinstance(xml_relative, str):
                        raise RuntimeError("Native PSO workspace did not declare editable XML")
                    xml_path = workspace.joinpath(
                        *PurePosixPath(xml_relative.replace("\\", "/")).parts
                    ).resolve()
                    if not xml_path.is_relative_to(
                        workspace.resolve()
                    ) or not xml_path.is_file():
                        raise RuntimeError("Native PSO workspace XML path is unsafe or missing")
                    result = OivXmlMergeEngine.apply(
                        xml_path.read_bytes(), operation.edits,
                        source_name=(
                            f"{state['archive_path']}::{state['entry_path']} (decoded PSO)"
                            if state["archive_path"] else
                            f"{state['entry_path']} (decoded PSO)"
                        ),
                    )
                    xml_path.write_bytes(result.data)
                    compiled = work_root / f"op_{operation.number:04d}{suffix}"
                    rebuilt, validation_report = native_inspector.build_workspace(
                        workspace, compiled,
                    )
                    native_report = json.loads(
                        validation_report.read_text(encoding="utf-8")
                    )
                    validation = native_report.get("validation")
                    if not isinstance(validation, dict) or (
                        validation.get("reparsed") is not True
                        or validation.get("semantic_xml_match") is not True
                    ):
                        raise RuntimeError(
                            "Rebuilt PSO resource did not semantically match the edited XML"
                        )
                    if native_report.get("source_sha256") != self._sha256(current):
                        raise RuntimeError("Native PSO build report lost its source binding")
                    if native_report.get("edited_xml_sha256") != self._sha256(xml_path):
                        raise RuntimeError("Native PSO build report lost its edited XML binding")
                    if Path(rebuilt) != compiled:
                        raise RuntimeError("Native PSO builder returned an unexpected payload path")
                    state["current"] = rebuilt
                    pso_compile_audits.append({
                        "oiv_operation": operation.number,
                        "archive_path": state["archive_path"],
                        "entry": state["entry_path"],
                        "xml_merge": result.audit,
                        "native_build": native_report,
                    })
                    event.update({
                        "compiled_size": rebuilt.stat().st_size,
                        "compiled_sha256": self._sha256(rebuilt),
                        "decoded_xml_sha256": result.audit["source_sha256"],
                        "edited_xml_sha256": result.audit["output_sha256"],
                        "reparsed_semantic_xml_sha256": validation[
                            "reparsed_semantic_xml_sha256"
                        ],
                        "edits": len(operation.edits),
                    })
                else:
                    raise ValueError(
                        f"Unsupported operation reached OIV recipe compiler: {operation.kind}"
                    )
                state["operations"].append(operation.number)
                recipe_events.append(event)

            changes: list[dict[str, object]] = []
            audit_changes: list[dict[str, object]] = []
            for number, state in enumerate(states.values(), start=1):
                current = state["current"]
                initial_exists = bool(state["initial_exists"])
                if current is None:
                    if not initial_exists:
                        continue
                    changes.append({
                        "action": "delete", "archive_path": state["archive_path"],
                        "entry": state["entry_path"],
                    })
                    audit_changes.append({
                        "action": "delete", "archive_path": state["archive_path"],
                        "entry": state["entry_path"],
                        "original_sha256": state["original_sha256"],
                        "operations": state["operations"],
                    })
                    continue
                current = Path(current)
                final_sha256 = self._sha256(current)
                if initial_exists and final_sha256 == state["original_sha256"]:
                    continue
                payload = payload_root / (
                    f"{number:04d}_{PurePosixPath(str(state['entry_path'])).name}"
                )
                shutil.copyfile(current, payload)
                action = "replace" if initial_exists else "add"
                changes.append({
                    "action": action, "archive_path": state["archive_path"],
                    "entry": state["entry_path"], "payload": str(payload),
                })
                audit_changes.append({
                    "action": action, "archive_path": state["archive_path"],
                    "entry": state["entry_path"],
                    "original_size": state["original_size"],
                    "original_sha256": state["original_sha256"],
                    "payload": f"payloads/{payload.name}",
                    "payload_size": payload.stat().st_size,
                    "payload_sha256": final_sha256,
                    "operations": state["operations"],
                })
            if not changes:
                raise ValueError("OIV recipe produced no RPF payload changes")

            rpf_plan = service.multi_change_plan(index, changes)
            plan_path = root / "rpf-plan.json"
            plan_path.write_text(json.dumps(rpf_plan, indent=2) + "\n", encoding="utf-8")
            current_assembly = PackageAssetReader(plan.source).read(
                "assembly.xml", limit=MAX_XML_BYTES,
            )
            if hashlib.sha256(current_assembly.data).hexdigest() != plan.assembly_sha256:
                raise RuntimeError("OIV assembly.xml changed during recipe compilation")
            if package_source_sha256 is not None and self._sha256(
                plan.source
            ) != package_source_sha256:
                raise RuntimeError("OIV package changed during recipe compilation")
            package_binding = (
                {
                    "mode": "archive_sha256", "path": str(plan.source),
                    "sha256": package_source_sha256,
                }
                if plan.source.is_file() else {
                    "mode": "assembly_sha256", "path": str(plan.source),
                    "assembly_sha256": plan.assembly_sha256,
                }
            )
            audit = {
                "schema_version": 1,
                "operation": (
                    "oiv_xml_rpf_compile"
                    if plan.xml_compilable else "oiv_rpf_recipe_compile"
                ),
                "source_oiv": str(plan.source),
                "package_binding": package_binding,
                "assembly_sha256": plan.assembly_sha256,
                "expected_outer_archive": expected_outer,
                "selected_archive": str(selected_archive),
                "selected_archive_sha256": rpf_plan["archive_sha256"],
                "edition": index.edition,
                "status": rpf_plan["status"],
                "archive_writes_performed": False,
                "recipe_events": recipe_events,
                "xml_merges": xml_merge_audits,
                "text_merges": text_merge_audits,
                "pso_compiles": pso_compile_audits,
                "changes": audit_changes,
                "rpf_plan": "rpf-plan.json",
                "verification": {
                    "all_xml_outputs_reparsed": True,
                    "all_xml_outputs_canonical_verified": True,
                    **({
                        "all_text_outputs_encoding_round_trip_verified": True,
                        "xml_shaped_text_outputs_reparsed": True,
                    } if text_merge_audits else {}),
                    **({
                        "all_pso_sources_decoded_with_game_keys": True,
                        "all_pso_outputs_reparsed": True,
                        "all_pso_outputs_semantically_verified": True,
                    } if pso_compile_audits else {}),
                    "source_archive_hash_bound": True,
                    "source_assembly_hash_bound": True,
                    "payload_hashes_bound_by_rpf_plan": True,
                },
            }
            audit_path = root / "compile-audit.json"
            audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
            shutil.rmtree(work_root)
            return plan_path, audit_path
        except Exception:
            if root.is_dir():
                shutil.rmtree(root)
            raise

    def export_created_rpf_package(
        self, plan: OivPlan, destination: str | Path, *,
        project_root: str | Path, gta_path: str | Path,
    ) -> Path:
        """Build createIfNotExist trees into a verified managed package.

        Every new archive is produced by :class:`RpfArchiveBuilder`; OIV recipe
        text is never executed. Declared file adds, supported XML and bounded
        line-oriented edits, and cleanup deletes are replayed in order and retained
        in a compile audit. Creation roots may be installed as a new managed file or
        as one exact entry in an existing outer RPF.
        """
        if not plan.translatable or not plan.created_archive_operations:
            raise ValueError("OIV plan has no fully translatable created-RPF workflow")
        created = {
            operation.archives + (operation.target,): operation
            for operation in plan.created_archive_operations
        }
        created_keys = {
            _fold_archive_chain(chain): operation
            for chain, operation in created.items()
        }
        roots = tuple(
            operation for chain, operation in created.items()
            if _fold_archive_chain(operation.archives) not in created_keys
        )
        if any(len(operation.archives) > 1 for operation in roots):
            raise ValueError(
                "Created RPF roots deeper than one existing archive require a batch bundle"
            )
        for operation in plan.operations:
            if operation.kind in {"add", "delete", "xml", "text"} and operation.archives and not any(
                _archive_chain_starts_with(operation.archives, chain)
                for chain in created
            ):
                raise ValueError(
                    "Existing-RPF changes must be exported as a separate atomic batch"
                )

        current_assembly = PackageAssetReader(plan.source).read(
            "assembly.xml", limit=MAX_XML_BYTES,
        )
        if hashlib.sha256(current_assembly.data).hexdigest() != plan.assembly_sha256:
            raise RuntimeError("OIV assembly.xml changed after the operation plan was created")
        package_source_sha256 = self._sha256(plan.source) if plan.source.is_file() else None
        root = Path(destination).expanduser().resolve()
        if root.exists() or root.is_symlink():
            raise ValueError("Created-RPF package destination must not already exist")
        root.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{root.name}.created-rpf-", dir=root.parent,
        )).resolve()
        try:
            payload_root = stage / "payload"
            authoring_root = stage / "rpf-sources"
            payload_root.mkdir()
            authoring_root.mkdir()
            files: list[tuple[str, str, str]] = []
            entries: list[tuple[str, str, str, str]] = []
            recipe_events: list[dict[str, object]] = []
            xml_merges: list[dict[str, object]] = []
            text_merges: list[dict[str, object]] = []
            built_archives: list[dict[str, object]] = []

            for number, operation in enumerate(roots, start=1):
                root_chain = operation.archives + (operation.target,)
                loose = authoring_root / f"{number:03d}_{PurePosixPath(operation.target).stem}"
                loose.mkdir()

                def archive_source(chain: tuple[str, ...]) -> Path:
                    current = loose
                    for archive_path in chain[len(root_chain):]:
                        relative = _safe_member_path(archive_path)
                        current = current.joinpath(
                            *relative.parent.parts, f"{relative.name}.source",
                        )
                    return current

                for chain in sorted(created, key=lambda item: (len(item), item)):
                    if (
                        not _archive_chain_starts_with(chain, root_chain)
                        or _fold_archive_chain(chain) == _fold_archive_chain(root_chain)
                    ):
                        continue
                    archive_source(chain).mkdir(parents=True, exist_ok=True)

                materialized_outputs: dict[
                    tuple[tuple[str, ...], str], Path
                ] = {}
                for authored in plan.operations:
                    if authored.kind not in {"add", "delete", "xml", "text"} or (
                        not _archive_chain_starts_with(
                            authored.archives, root_chain,
                        )
                    ):
                        continue
                    target = _safe_member_path(authored.target)
                    output_identity = (
                        _fold_archive_chain(authored.archives),
                        authored.target.casefold(),
                    )
                    output = materialized_outputs.get(output_identity)
                    if output is None:
                        output = archive_source(authored.archives).joinpath(
                            *target.parts,
                        )
                    event: dict[str, object] = {
                        "oiv_operation": authored.number,
                        "kind": authored.kind,
                        "archive_path": "!".join(authored.archives),
                        "entry": authored.target,
                    }
                    if authored.kind == "add":
                        if output.exists() or output.is_symlink():
                            raise ValueError(
                                "Created RPF recipe has a duplicate ordered output: "
                                f"{authored.target}"
                            )
                        output.parent.mkdir(parents=True, exist_ok=True)
                        self._copy_member(plan.source, authored.source, output)
                        materialized_outputs[output_identity] = output
                        event.update({
                            "source": authored.source,
                            "output_size": output.stat().st_size,
                            "output_sha256": self._sha256(output),
                        })
                    elif authored.kind == "delete":
                        if not output.is_file() or output.is_symlink():
                            raise ValueError(
                                "Created RPF delete target is not an available file: "
                                f"{authored.target}"
                            )
                        event.update({
                            "deleted_size": output.stat().st_size,
                            "deleted_sha256": self._sha256(output),
                        })
                        output.unlink()
                        materialized_outputs.pop(output_identity, None)
                    elif authored.kind == "xml":
                        if not output.is_file() or output.is_symlink():
                            raise ValueError(
                                "Created RPF XML target is not an available file: "
                                f"{authored.target}"
                            )
                        if output.stat().st_size > MAX_OIV_XML_BYTES:
                            raise ValueError(
                                f"Created RPF XML target exceeds {MAX_OIV_XML_BYTES:,} "
                                f"bytes: {authored.target}"
                            )
                        merged = OivXmlMergeEngine.apply(
                            output.read_bytes(), authored.edits,
                            source_name=(
                                f"{'!'.join(authored.archives)}::{authored.target}"
                            ),
                        )
                        output.write_bytes(merged.data)
                        event.update({
                            "edits": len(authored.edits),
                            "output_size": len(merged.data),
                            "output_sha256": merged.audit["output_sha256"],
                        })
                        xml_merges.append({
                            "oiv_operation": authored.number,
                            "archive_path": "!".join(authored.archives),
                            "entry": authored.target,
                            **merged.audit,
                        })
                    else:
                        if not output.exists() and authored.creates_file:
                            output.parent.mkdir(parents=True, exist_ok=True)
                            output.write_bytes(b"")
                            materialized_outputs[output_identity] = output
                        if not output.is_file() or output.is_symlink():
                            raise ValueError(
                                "Created RPF text target is not an available file: "
                                f"{authored.target}"
                            )
                        if output.stat().st_size > MAX_OIV_TEXT_BYTES:
                            raise ValueError(
                                f"Created RPF text target exceeds {MAX_OIV_TEXT_BYTES:,} "
                                f"bytes: {authored.target}"
                            )
                        merged = OivTextMergeEngine.apply(
                            output.read_bytes(), authored.edits,
                            source_name=(
                                f"{'!'.join(authored.archives)}::{authored.target}"
                            ),
                            verify_xml=(
                                PurePosixPath(authored.target).suffix.casefold()
                                in {".xml", ".meta"}
                            ),
                        )
                        output.write_bytes(merged.data)
                        event.update({
                            "edits": len(authored.edits),
                            "output_size": len(merged.data),
                            "output_sha256": merged.audit["output_sha256"],
                        })
                        text_merges.append({
                            "oiv_operation": authored.number,
                            "archive_path": "!".join(authored.archives),
                            "entry": authored.target,
                            **merged.audit,
                        })
                    recipe_events.append(event)

                archive_name = PurePosixPath(operation.target).name
                built_relative = f"payload/{number:03d}_{archive_name}"
                built = stage.joinpath(*PurePosixPath(built_relative).parts)
                from allin1_sdk.rpf_builder import RpfArchiveBuilder
                RpfArchiveBuilder(project_root, gta_path).build(loose, built)
                digest = self._sha256(built)
                built_archives.append({
                    "root_archive": "!".join(root_chain),
                    "output": built_relative,
                    "size": built.stat().st_size,
                    "sha256": digest,
                })
                if operation.archives:
                    entries.append((
                        built_relative, operation.archives[0], operation.target, digest,
                    ))
                else:
                    files.append((built_relative, operation.target, digest))

            for number, operation in enumerate(
                (item for item in plan.add_operations if not item.archives), start=1,
            ):
                name = PurePosixPath(operation.source).name
                relative = f"payload/file_{number:03d}_{name}"
                output = stage.joinpath(*PurePosixPath(relative).parts)
                self._copy_member(plan.source, operation.source, output)
                files.append((relative, operation.target, self._sha256(output)))

            dlc_packs: list[str] = []
            pattern = re.compile(
                r"^mods/update/x64/dlcpacks/([a-z0-9._-]+)/dlc\.rpf$", re.I,
            )
            for _, target, _ in files:
                match = pattern.fullmatch(target)
                if match:
                    dlc_packs.append(match.group(1))
            dependencies = ["openrpf"] if entries or any(
                target.casefold().startswith("mods/") for _, target, _ in files
            ) else []
            mod_type = self._mod_type(files, entries)
            mod_id = re.sub(
                r"[^a-z0-9._-]+", "-", plan.name.casefold(),
            ).strip("-._")
            mod_id = (mod_id or "imported-oiv")[:64]
            current_assembly = PackageAssetReader(plan.source).read(
                "assembly.xml", limit=MAX_XML_BYTES,
            )
            if hashlib.sha256(current_assembly.data).hexdigest() != plan.assembly_sha256:
                raise RuntimeError("OIV assembly.xml changed during created-RPF compilation")
            if package_source_sha256 is not None and self._sha256(
                plan.source
            ) != package_source_sha256:
                raise RuntimeError("OIV package changed during created-RPF compilation")
            audit = {
                "schema_version": 1,
                "operation": "oiv_created_rpf_compile",
                "source_oiv": str(plan.source),
                "assembly_sha256": plan.assembly_sha256,
                "package_sha256": package_source_sha256,
                "recipe_events": recipe_events,
                "xml_merges": xml_merges,
                "text_merges": text_merges,
                "built_archives": built_archives,
                "verification": {
                    "ordered_recipe_evaluated": True,
                    "all_xml_outputs_reparsed": True,
                    "all_xml_outputs_canonical_verified": True,
                    "all_text_outputs_encoding_round_trip_verified": True,
                    "xml_shaped_text_outputs_reparsed": True,
                    "all_archives_recursively_verified": True,
                    "source_assembly_hash_bound": True,
                    "archive_package_hash_rechecked": package_source_sha256 is not None,
                },
            }
            (stage / "created-rpf-compile-audit.json").write_text(
                json.dumps(audit, indent=2) + "\n", encoding="utf-8",
            )
            lines = [
                "schema_version = 1", f"id = {json.dumps(mod_id)}",
                f"name = {json.dumps(plan.name)}",
                f"version = {json.dumps(plan.version or '1.0')}",
                f"type = {json.dumps(mod_type)}",
                "description = \"Converted from a verified OIV created-RPF recipe; review before installation.\"",
                "editions = [" + ", ".join(
                    json.dumps(value) for value in plan.editions
                ) + "]",
                "dependencies = [" + ", ".join(
                    json.dumps(value) for value in dependencies
                ) + "]",
                "dlc_packs = [" + ", ".join(
                    json.dumps(value) for value in dlc_packs
                ) + "]",
            ]
            for source, target, digest in files:
                lines.extend([
                    "", "[[files]]", f"source = {json.dumps(source)}",
                    f"destination = {json.dumps(target)}",
                    f"sha256 = {json.dumps(digest)}",
                ])
            for source, archive, entry, digest in entries:
                lines.extend([
                    "", "[[rpf_entries]]", f"source = {json.dumps(source)}",
                    f"archive = {json.dumps(archive)}", f"entry = {json.dumps(entry)}",
                    f"sha256 = {json.dumps(digest)}",
                ])
            manifest = stage / "mod.toml"
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            from allin1_sdk.mods import ModManifest
            ModManifest.load(manifest)
            stage.rename(root)
            return root / "mod.toml"
        except Exception:
            if stage.is_dir() and stage.parent == root.parent:
                shutil.rmtree(stage)
            raise

    def export_rpf_batch_manifests(
        self, plan: OivPlan, destination: str | Path,
    ) -> tuple[Path, ...]:
        """Export the exact existing-RPF portion as atomic batch manifests."""
        operations = plan.rpf_batch_operations
        if plan.xml_operations or plan.text_operations or plan.pso_operations:
            raise ValueError(
                "OIV recipes containing XML/text/PSO commands require verified recipe "
                "compilation; partial add/delete batch export is blocked"
            )
        if not operations:
            raise ValueError("OIV recipe contains no RPF entry changes")
        if any(not item.supported for item in operations):
            raise ValueError("OIV recipe contains an unsafe RPF entry operation")
        root = Path(destination).expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError("RPF batch destination must be empty")
        root.mkdir(parents=True, exist_ok=True)
        grouped: dict[str, list[OivOperation]] = {}
        for operation in operations:
            grouped.setdefault(operation.archives[0], []).append(operation)

        written: list[Path] = []
        for group_number, (outer_archive, changes) in enumerate(
            sorted(grouped.items(), key=lambda item: item[0].casefold()), start=1,
        ):
            label = re.sub(
                r"[^a-z0-9._-]+", "-", PurePosixPath(outer_archive).stem.casefold(),
            ).strip("-._") or "archive"
            discriminator = hashlib.sha256(outer_archive.casefold().encode("utf-8")).hexdigest()[:8]
            group = root / f"{group_number:02d}-{label}-{discriminator}"
            payload_root = group / "payloads"
            payload_root.mkdir(parents=True)
            authored_changes: list[dict[str, object]] = []
            seen: set[tuple[str, str]] = set()
            for change_number, operation in enumerate(changes, start=1):
                archive_path = "!".join(operation.archives[1:])
                identity = (archive_path.casefold(), operation.target.casefold())
                if identity in seen:
                    raise ValueError(
                        "OIV recipe targets the same RPF entry more than once: "
                        f"{archive_path}::{operation.target}"
                    )
                seen.add(identity)
                item: dict[str, object] = {
                    "action": "upsert" if operation.kind == "add" else "delete",
                    "archive_path": archive_path,
                    "entry": operation.target,
                    "oiv_operation": operation.number,
                }
                if operation.kind == "add":
                    name = PurePosixPath(operation.source).name
                    payload = payload_root / f"{change_number:04d}_{name}"
                    self._copy_member(plan.source, operation.source, payload)
                    item["payload"] = f"payloads/{payload.name}"
                    item["source_sha256"] = self._sha256(payload)
                authored_changes.append(item)
            manifest = group / "changes.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "operation": "rpf_multi_entry_change_manifest",
                "source_oiv": str(plan.source),
                "outer_archive": outer_archive,
                "changes": authored_changes,
            }, indent=2) + "\n", encoding="utf-8")
            written.append(manifest)
        return tuple(written)

    def export_managed_package(
        self, plan: OivPlan, destination: str | Path,
    ) -> Path:
        """Extract only proven add sources and emit a validated local package."""
        if not plan.managed_exportable:
            raise ValueError(
                "OIV recipe still contains unsupported or unsafe operations, or "
                "requires atomic nested-RPF batch manifests"
            )
        root = Path(destination).expanduser().resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError("Managed-package destination must be empty")
        root.mkdir(parents=True, exist_ok=True)
        payload = root / "payload"
        payload.mkdir()

        files: list[tuple[str, str, str]] = []
        entries: list[tuple[str, str, str, str]] = []
        for index, operation in enumerate(plan.add_operations, start=1):
            name = PurePosixPath(operation.source).name
            relative = f"payload/{index:03d}_{name}"
            output = root / Path(*PurePosixPath(relative).parts)
            self._copy_member(plan.source, operation.source, output)
            digest = self._sha256(output)
            if operation.archives:
                entries.append((relative, operation.archives[0], operation.target, digest))
            else:
                files.append((relative, operation.target, digest))

        dlc_packs = []
        pattern = re.compile(
            r"^mods/update/x64/dlcpacks/([a-z0-9._-]+)/dlc\.rpf$", re.I,
        )
        for _, target, _ in files:
            match = pattern.fullmatch(target)
            if match:
                dlc_packs.append(match.group(1))
        dependencies = ["openrpf"] if entries or any(
            target.casefold().startswith("mods/") for _, target, _ in files
        ) else []
        mod_type = self._mod_type(files, entries)
        mod_id = re.sub(r"[^a-z0-9._-]+", "-", plan.name.casefold()).strip("-._")
        mod_id = (mod_id or "imported-oiv")[:64]
        lines = [
            "schema_version = 1", f"id = {json.dumps(mod_id)}",
            f"name = {json.dumps(plan.name)}",
            f"version = {json.dumps(plan.version or '1.0')}",
            f"type = {json.dumps(mod_type)}",
            "description = \"Converted from a fully translatable OIV recipe; review before installation.\"",
            "editions = [" + ", ".join(json.dumps(value) for value in plan.editions) + "]",
            "dependencies = [" + ", ".join(json.dumps(value) for value in dependencies) + "]",
            "dlc_packs = [" + ", ".join(json.dumps(value) for value in dlc_packs) + "]",
        ]
        for source, target, digest in files:
            lines.extend([
                "", "[[files]]", f"source = {json.dumps(source)}",
                f"destination = {json.dumps(target)}", f"sha256 = {json.dumps(digest)}",
            ])
        for source, archive, entry, digest in entries:
            lines.extend([
                "", "[[rpf_entries]]", f"source = {json.dumps(source)}",
                f"archive = {json.dumps(archive)}", f"entry = {json.dumps(entry)}",
                f"sha256 = {json.dumps(digest)}",
            ])
        manifest = root / "mod.toml"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        from allin1_sdk.mods import ModManifest
        ModManifest.load(manifest)
        return manifest

    @staticmethod
    def _mod_type(
        files: Iterable[tuple[str, str, str]],
        entries: Iterable[tuple[str, str, str, str]],
    ) -> str:
        file_list = list(files)
        if list(entries):
            return "mixed" if file_list else "rpf"
        destinations = [PurePosixPath(item[1]) for item in file_list]
        if destinations and all(path.parts[0].casefold() == "scripts" for path in destinations):
            return "script"
        if destinations and all(len(path.parts) == 1 for path in destinations):
            return "asi"
        if destinations and all(
            path.parts[0].casefold() == "mods" and path.suffix.casefold() == ".rpf"
            for path in destinations
        ):
            return "rpf"
        return "mixed"

    @staticmethod
    def _copy_member(source: Path, member: str, destination: Path) -> None:
        relative = _safe_member_path(member).as_posix()
        if source.is_dir():
            candidate = source.joinpath(*PurePosixPath(relative).parts).resolve()
            if not candidate.is_relative_to(source) or not candidate.is_file():
                raise FileNotFoundError(f"OIV source disappeared: {relative}")
            shutil.copyfile(candidate, destination)
            return
        with zipfile.ZipFile(source) as package:
            matches = [item for item in package.infolist() if not item.is_dir() and
                       _safe_member_path(item.filename).as_posix().casefold() == relative.casefold()]
            if len(matches) != 1 or matches[0].flag_bits & 1:
                raise ValueError(f"OIV source is missing, ambiguous, or encrypted: {relative}")
            with package.open(matches[0]) as input_stream, destination.open("wb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
