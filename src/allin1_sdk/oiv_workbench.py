"""Read-only OIV operation planning and explicit managed-package conversion."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree as ET

from allin1_sdk.addon_importer import (
    MAX_XML_BYTES,
    AddonPackageInspector,
    PackageAssetReader,
    _local_name,
    _parse_xml,
    _safe_member_path,
)


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


@dataclass(frozen=True)
class OivPlan:
    source: Path
    name: str
    version: str
    author: str
    editions: tuple[str, ...]
    operations: tuple[OivOperation, ...]
    findings: tuple[OivFinding, ...]

    @property
    def add_operations(self) -> tuple[OivOperation, ...]:
        return tuple(item for item in self.operations if item.kind == "add")

    @property
    def rpf_batch_operations(self) -> tuple[OivOperation, ...]:
        return tuple(
            item for item in self.operations
            if item.kind in {"add", "delete"} and item.archives
        )

    @property
    def translatable(self) -> bool:
        actionable = tuple(
            item for item in self.operations if item.kind in {"add", "delete"}
        )
        return bool(actionable) and not any(
            not item.supported for item in self.operations
        ) and not any(item.severity == "error" for item in self.findings)

    @property
    def managed_exportable(self) -> bool:
        return self.translatable and bool(self.add_operations) and all(
            item.kind in {"archive", "add"}
            and (item.kind != "add" or len(item.archives) <= 1)
            and (item.kind != "archive" or not item.archives)
            for item in self.operations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": str(self.source), "name": self.name,
            "version": self.version, "author": self.author,
            "editions": list(self.editions),
            "translatable": self.translatable,
            "managed_exportable": self.managed_exportable,
            "operations": [asdict(item) for item in self.operations],
            "findings": [asdict(item) for item in self.findings],
        }

    def to_markdown(self) -> str:
        result = "MANAGED EXPORT READY" if self.translatable else "REVIEW REQUIRED"
        lines = [
            f"# OIV operation plan: {self.name}", "",
            f"- Source: `{self.source}`",
            f"- Version: `{self.version or 'unknown'}`",
            f"- Author: `{self.author or 'unknown'}`",
            f"- Editions: {', '.join(value.title() for value in self.editions)}",
            f"- Result: **{result}**", "",
            "## Ordered operations", "",
            "| # | Action | Archive | Source | Target | Translation |",
            "|---:|---|---|---|---|---|",
        ]
        for item in self.operations:
            archive = " → ".join(item.archives) or "filesystem"
            status = "managed" if item.supported else "manual review"
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
            "exact RPF-entry transaction, or atomic nested-RPF batch. Wildcard text "
            "edits, XPath/PSO merges, archive creation, and unknown commands remain "
            "blocked.", "",
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

    _UNSUPPORTED = {"text", "xml", "pso", "defragmentation"}

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
                self._walk(child, (), entries, operations, findings)
        if not any(item.kind == "add" for item in operations):
            findings.append(OivFinding(
                "warning", "no_managed_payload",
                "The recipe contains no file additions that ALLIN1 can own.",
            ))
        return OivPlan(
            package, name, version, author, editions,
            tuple(operations), tuple(findings),
        )

    def _walk(
        self, node: ET.Element, archives: tuple[str, ...], entries: set[str],
        operations: list[OivOperation], findings: list[OivFinding],
    ) -> None:
        kind = _local_name(node.tag).casefold()
        number = len(operations) + 1
        if kind == "archive":
            raw_path = node.attrib.get("path", "").strip()
            path = self._target_path(raw_path, mods=not archives)
            create = node.attrib.get("createIfNotExist", "false").casefold() == "true"
            nested = archives + ((path or raw_path),)
            supported = (
                bool(path) and path.casefold().endswith(".rpf")
                and len(nested) <= 9 and not create
            )
            operations.append(OivOperation(
                number, kind, "", path, archives, supported,
                "RPF operation container" + ("; creates archive" if create else ""),
            ))
            if not supported:
                code = (
                    "archive_creation" if create else "archive_depth"
                    if len(nested) > 9 else "unsafe_archive"
                )
                findings.append(OivFinding(
                    "error", code,
                    "The archive container is unsafe, newly created, or deeper than "
                    "the eight nested levels supported by atomic RPF transactions.",
                    number,
                ))
            for child in node:
                self._walk(child, nested, entries, operations, findings)
            return

        source = node.attrib.get("source", "").strip() if kind == "add" else ""
        target = (node.text or "").strip() if kind in {"add", "delete"} else (
            node.attrib.get("path", "").strip()
        )
        supported = False
        detail = "OIV operation requires manual review"
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
            supported = bool(archives) and bool(target_path) and len(archives) <= 9
            target = target_path
            detail = "Exact RPF entry deletion"
            if not supported:
                findings.append(OivFinding(
                    "error", "unsupported_delete",
                    "Delete is supported only for an exact entry inside an existing "
                    "RPF archive tree.", number,
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

    def export_rpf_batch_manifests(
        self, plan: OivPlan, destination: str | Path,
    ) -> tuple[Path, ...]:
        """Export the exact existing-RPF portion as atomic batch manifests."""
        operations = plan.rpf_batch_operations
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
