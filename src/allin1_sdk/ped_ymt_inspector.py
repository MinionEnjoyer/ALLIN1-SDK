"""Read-only, evidence-separated inspection for ped-related YMT metadata."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable
from xml.etree import ElementTree as ET

from allin1_sdk import __version__
from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageRegistrationRecord,
    PackageScan,
    RpfNativeEntryRecord,
)
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.rpf_tools import RpfExplorerService


PED_YMT_REPORT_VERSION = 1
MAX_YMT_BYTES = 128 * 1024 * 1024
MAX_YMT_XML_BYTES = 32 * 1024 * 1024
MAX_PACKAGED_RPF_BYTES = 512 * 1024 * 1024
MAX_YMT_ENTRIES = 512

_DecodeYmt = Callable[[str, bytes, str], bytes]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_xml_root(content: bytes) -> ET.Element:
    declarations = content.upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise ValueError("DTD/entity declarations are not allowed in decoded YMT XML")
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Decoded YMT XML is malformed: {exc}") from exc


def _container_items(root: ET.Element, name: str) -> tuple[ET.Element, ...]:
    containers = tuple(item for item in root.iter() if _local_name(item.tag) == name)
    return tuple(
        child for container in containers for child in container
        if _local_name(child.tag) == "Item"
    )


def _first_value(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) != name:
            continue
        return (
            element.attrib.get("value", "").strip()
            or element.attrib.get("ref", "").strip()
            or (element.text or "").strip()
        )
    return ""


def _sex_from_content(root: ET.Element, identity: str) -> tuple[str, tuple[str, ...]]:
    for element in root.iter():
        if _local_name(element.tag).casefold() not in {
            "gender", "sex", "pedgender", "gendername",
        }:
            continue
        value = (
            element.attrib.get("value", "") or element.text or ""
        ).strip().casefold()
        if value in {"male", "m"}:
            return "male", (f"{_local_name(element.tag)}={value}",)
        if value in {"female", "f"}:
            return "female", (f"{_local_name(element.tag)}={value}",)
        if value in {"shared", "unisex", "any"}:
            return "shared", (f"{_local_name(element.tag)}={value}",)
    normalized = identity.casefold()
    if re.search(r"(^|_)mp_m(_|$)", normalized):
        return "male", (f"decoded root identity={identity}",)
    if re.search(r"(^|_)mp_f(_|$)", normalized):
        return "female", (f"decoded root identity={identity}",)
    return "unknown", ()


@dataclass(frozen=True)
class DecodedYmtFacts:
    root_type: str
    classification: str
    identity: str | None
    sex: str
    classification_evidence: tuple[str, ...]
    sex_evidence: tuple[str, ...]
    metrics: dict[str, int | str | bool | None] = field(default_factory=dict)


def classify_ped_ymt_xml(content: bytes) -> DecodedYmtFacts:
    """Classify a decoded document from content, never from its filename."""
    root = _safe_xml_root(content)
    root_type = _local_name(root.tag)
    identity = root.attrib.get("name", "").strip() or None
    metrics: dict[str, int | str | bool | None] = {}
    evidence = (f"decoded XML root={root_type}",)
    if root_type == "CPedVariationInfo":
        classification = "ped_variation"
        component_data = _container_items(root, "aComponentData3")
        drawables = _container_items(root, "aDrawblData3")
        textures = _container_items(root, "aTexData")
        prop_metadata = _container_items(root, "aPropMetaData")
        selection_sets = _container_items(root, "aSelectionSets")
        component_info = _container_items(root, "compInfos")
        owns_cloth = sum(
            1 for element in root.iter()
            if _local_name(element.tag) == "ownsCloth"
            and element.attrib.get("value", "").strip().casefold() == "true"
        )
        metrics.update({
            "component_data_count": len(component_data),
            "drawable_count": len(drawables),
            "texture_record_count": len(textures),
            "prop_metadata_count": len(prop_metadata),
            "selection_set_count": len(selection_sets),
            "component_info_count": len(component_info),
            "cloth_owned_drawable_count": owns_cloth,
            "dlc_name": _first_value(root, "dlcName") or None,
        })
    elif root_type == "CCreatureMetaData":
        classification = "creature_metadata"
        metrics.update({
            "shader_variable_component_count": len(
                _container_items(root, "shaderVariableComponents")
            ),
            "ped_prop_expression_count": len(
                _container_items(root, "pedPropExpressions")
            ),
            "ped_component_expression_count": len(
                _container_items(root, "pedCompExpressions")
            ),
        })
    else:
        classification = "other"
    sex, sex_evidence = _sex_from_content(root, identity or "")
    return DecodedYmtFacts(
        root_type=root_type,
        classification=classification,
        identity=identity,
        sex=sex,
        classification_evidence=evidence,
        sex_evidence=sex_evidence,
        metrics=metrics,
    )


@dataclass(frozen=True)
class EvidenceState:
    status: str
    detail: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class YmtCatalogEntry:
    id: str
    format: str
    package_member: str | None
    archive_path: str | None
    path: str
    size: int
    indexed_size: int | None
    sha256: str
    decode_status: str
    decode_error: str | None
    decoded_sha256: str | None
    decoder_edition: str
    root_type: str | None
    classification: str
    identity: str | None
    sex: str
    classification_evidence: tuple[str, ...]
    sex_evidence: tuple[str, ...]
    metrics: dict[str, int | str | bool | None]
    registration_status: str
    registration_evidence: tuple[str, ...]
    mount_state: str = "unknown"


@dataclass(frozen=True)
class YmtDependencyEdge:
    id: str
    source_id: str
    target_id: str | None
    target_kind: str
    kind: str
    resolution: str
    reason: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class YmtFinding:
    severity: str
    code: str
    message: str
    source_id: str | None = None


@dataclass(frozen=True)
class _YmtCandidate:
    id: str
    format: str
    package_member: str | None
    archive_path: str | None
    path: str
    indexed_size: int | None
    data: bytes
    registration_evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PedYmtInspectionReport:
    report_version: int
    operation: str
    generated_at: str
    sdk_version: str
    source: dict[str, str | int | None]
    target: dict[str, str | None]
    evidence_states: dict[str, EvidenceState]
    summary: dict[str, int]
    catalog: tuple[YmtCatalogEntry, ...]
    dependencies: tuple[YmtDependencyEdge, ...]
    findings: tuple[YmtFinding, ...]

    def to_dict(self) -> dict[str, object]:
        # ``asdict`` preserves tuples. Round-trip once so every public surface
        # receives an ordinary JSON value tree and desktop bounds checks compare
        # the same representation they will transport.
        return json.loads(json.dumps(asdict(self)))

    def write(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        temporary.replace(target)
        return target


class PedYmtInspector:
    """Inventory and classify YMTs without installing, editing, or mounting them."""

    def __init__(
        self, project_root: str | Path, gta_path: str | Path | None = None, *,
        decoder: _DecodeYmt | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.gta_path = (
            Path(gta_path).expanduser().resolve() if gta_path is not None else None
        )
        native = NativeAssetInspector(self.project_root, self.gta_path)
        self._decoder = decoder or (
            lambda name, data, edition: native.decode_xml_bytes(
                name, data, edition=edition,
                maximum_xml_bytes=MAX_YMT_XML_BYTES,
            )
        )

    def inspect(
        self, source: str | Path, *, edition: str,
    ) -> PedYmtInspectionReport:
        authored_source = Path(source).expanduser()
        if authored_source.is_symlink():
            raise ValueError("YMT inspection source cannot be a symbolic link")
        resolved = authored_source.resolve(strict=True)
        decoder_edition = self._normalize_edition(edition)
        findings: list[YmtFinding] = []
        registrations: tuple[PackageRegistrationRecord, ...] = ()
        rpf_archives = 0
        archive_failures: list[str] = []
        if resolved.is_file() and (
            resolved.suffix.casefold() == ".ymt"
            or resolved.name.casefold().endswith(".ymt.xml")
        ):
            source_kind = (
                "ymt_xml" if resolved.name.casefold().endswith(".ymt.xml") else "ymt"
            )
            candidates = [self._loose_file_candidate(resolved)]
        else:
            scan = AddonPackageInspector(
                self.project_root, self.gta_path,
            ).inspect(resolved)
            source_kind = scan.source_kind
            if any(entry.suffix == ".rpf" for entry in scan.entries):
                if self.gta_path is None:
                    raise ValueError(
                        "YMT inspection inside RPF archives requires a matching "
                        "GTA V installation path"
                    )
                if not self.gta_path.is_dir():
                    raise FileNotFoundError(f"GTA V directory not found: {self.gta_path}")
            registrations = scan.registrations
            rpf_archives = len(scan.rpf_archives)
            archive_issue_codes = {
                "duplicate_member", "encrypted_member",
                "archive_member_size_mismatch", "rpf_inspection_limit",
                "rpf_recursive_inspection_failed", "rpf_metadata_extract_failed",
            }
            archive_failures = [
                f"{item.code}: {item.message}" for item in scan.findings
                if item.code in archive_issue_codes
            ]
            findings.extend(
                YmtFinding(item.severity, item.code, item.message, item.path)
                for item in scan.findings
                if item.code in archive_issue_codes
            )
            candidates = self._package_candidates(
                resolved, scan, registrations, findings,
            )
        if len(candidates) > MAX_YMT_ENTRIES:
            raise ValueError(
                f"YMT inventory exceeds the {MAX_YMT_ENTRIES} entry safety limit"
            )
        catalog = tuple(
            self._catalog_entry(candidate, decoder_edition, findings)
            for candidate in sorted(candidates, key=lambda item: item.id.casefold())
        )
        if not catalog:
            findings.append(YmtFinding(
                "warning", "no_ymt_definitions",
                "No complete YMT definitions were discovered in the selected source.",
            ))
        dependencies = self._dependency_edges(catalog, findings)
        decoded = sum(item.decode_status == "decoded" for item in catalog)
        unsupported = len(catalog) - decoded
        classified = sum(
            item.classification in {"ped_variation", "creature_metadata"}
            for item in catalog
        )
        unresolved = sum(
            item.resolution in {"unresolved", "ambiguous", "unsupported"}
            for item in dependencies
        )
        states = {
            "archive_structure": EvidenceState(
                "partial" if archive_failures else "observed",
                (
                    f"Indexed {rpf_archives} package-owned RPF archive(s); "
                    f"discovered {len(catalog)} YMT definition(s)."
                ),
                tuple(archive_failures),
            ),
            "metadata_decoding": EvidenceState(
                (
                    "unsupported" if not catalog or not decoded else
                    "observed" if not unsupported else "partial"
                ),
                f"Decoded {decoded} of {len(catalog)} complete YMT document(s).",
                tuple(
                    item.id for item in catalog if item.decode_status != "decoded"
                ),
            ),
            "dependency_resolution": EvidenceState(
                (
                    "partial" if classified else "unsupported"
                ),
                (
                    f"Derived {len(dependencies)} limited content-backed "
                    f"relationship(s); {unresolved} remain unresolved, ambiguous, "
                    "or unsupported. Broader dependency coverage is not yet available."
                ),
                tuple(
                    item.id for item in dependencies
                    if item.resolution != "resolved"
                ),
            ),
            "target_runtime_compatibility": EvidenceState(
                "unknown",
                "Decoder context is not proof that a runtime adapter supports this game build.",
            ),
            "in_game_acceptance": EvidenceState(
                "not_tested",
                "Read-only inspection does not launch GTA or certify in-game behavior.",
            ),
        }
        source_size = resolved.stat().st_size if resolved.is_file() else None
        source_sha = _sha256_file(resolved) if resolved.is_file() else None
        return PedYmtInspectionReport(
            report_version=PED_YMT_REPORT_VERSION,
            operation="inspect_ped_ymt",
            generated_at=datetime.now(timezone.utc).isoformat(),
            sdk_version=__version__,
            source={
                "path": str(resolved), "kind": source_kind,
                "size": source_size, "sha256": source_sha,
            },
            target={
                "decoder_edition": decoder_edition,
                "gta_context": str(self.gta_path) if self.gta_path else None,
                "runtime_compatibility": "unknown",
            },
            evidence_states=states,
            summary={
                "ymt_definitions": len(catalog),
                "decoded": decoded,
                "ped_variation": sum(
                    item.classification == "ped_variation" for item in catalog
                ),
                "creature_metadata": sum(
                    item.classification == "creature_metadata" for item in catalog
                ),
                "other": sum(item.classification == "other" for item in catalog),
                "unknown": sum(item.classification == "unknown" for item in catalog),
                "relationships": len(dependencies),
                "unresolved_relationships": unresolved,
                "declared_for_registration": sum(
                    item.registration_status == "declared" for item in catalog
                ),
            },
            catalog=catalog,
            dependencies=dependencies,
            findings=tuple(findings),
        )

    @staticmethod
    def _normalize_edition(edition: str) -> str:
        normalized = str(edition).strip().casefold()
        if normalized in {"enhanced", "gen9"}:
            return "Enhanced"
        if normalized == "legacy":
            return "Legacy"
        raise ValueError("YMT decoder edition must be Legacy or Enhanced")

    @staticmethod
    def _loose_file_candidate(source: Path) -> _YmtCandidate:
        size = source.stat().st_size
        if size <= 0 or size > MAX_YMT_BYTES:
            raise ValueError("Loose YMT is empty or exceeds the guarded limit")
        data = source.read_bytes()
        decoded = source.name.casefold().endswith(".ymt.xml")
        return _YmtCandidate(
            id=source.name,
            format="codewalker_xml" if decoded else "binary_ymt",
            package_member=None,
            archive_path=None,
            path=source.name,
            indexed_size=None,
            data=data,
        )

    def _package_candidates(
        self, source: Path, scan: PackageScan,
        registrations: tuple[PackageRegistrationRecord, ...],
        findings: list[YmtFinding],
    ) -> list[_YmtCandidate]:
        candidates: list[_YmtCandidate] = []
        reader = PackageAssetReader(
            source, project_root=self.project_root, gta_path=self.gta_path,
        )
        for entry in scan.entries:
            is_xml = entry.path.casefold().endswith(".ymt.xml")
            if entry.suffix != ".ymt" and not is_xml:
                continue
            if entry.size <= 0 or entry.size > MAX_YMT_BYTES:
                findings.append(YmtFinding(
                    "warning", "ymt_size_unsupported",
                    "YMT is empty or exceeds the guarded read limit.", entry.path,
                ))
                continue
            content = reader.read(entry.path, limit=entry.size + 1)
            if content.truncated or len(content.data) != entry.size:
                findings.append(YmtFinding(
                    "warning", "ymt_read_incomplete",
                    "YMT could not be read completely.", entry.path,
                ))
                continue
            candidates.append(_YmtCandidate(
                id=entry.path,
                format="codewalker_xml" if is_xml else "binary_ymt",
                package_member=None,
                archive_path=None,
                path=entry.path,
                indexed_size=entry.size,
                data=content.data,
            ))
        nested = tuple(
            item for item in scan.rpf_native_assets if item.suffix == ".ymt"
        )
        if not nested:
            return candidates
        assert self.gta_path is not None
        service = RpfExplorerService(self.project_root, self.gta_path)
        grouped: dict[str, list[RpfNativeEntryRecord]] = defaultdict(list)
        for item in nested:
            grouped[item.source].append(item)
        direct_rpf = source.is_file() and source.suffix.casefold() == ".rpf"
        with tempfile.TemporaryDirectory(prefix="allin1-ped-ymt-rpf-") as temporary:
            root = Path(temporary)
            for number, (member_path, records) in enumerate(
                sorted(grouped.items(), key=lambda item: item[0].casefold()), start=1,
            ):
                try:
                    if direct_rpf:
                        outer = source
                    else:
                        matches = [
                            item for item in scan.entries
                            if item.path.casefold() == member_path.casefold()
                        ]
                        if len(matches) != 1:
                            raise ValueError("Package RPF member was not found uniquely")
                        member = matches[0]
                        if member.size <= 0 or member.size > MAX_PACKAGED_RPF_BYTES:
                            raise ValueError("Package RPF exceeds the guarded read limit")
                        content = reader.read(member.path, limit=member.size + 1)
                        if content.truncated or len(content.data) != member.size:
                            raise ValueError("Package RPF could not be read completely")
                        outer = root / f"member-{number}.rpf"
                        outer.write_bytes(content.data)
                    index = service.index(outer)
                    selected = tuple(index.entry(item.entry_id) for item in records)
                    if len(selected) + len(candidates) > MAX_YMT_ENTRIES:
                        raise ValueError("YMT inventory exceeds the guarded entry limit")
                    output = service.extract_many(
                        index, selected, root / f"ymt-{number}",
                    )
                    for record, path in zip(records, output):
                        data = path.read_bytes()
                        if not data or len(data) > MAX_YMT_BYTES:
                            raise ValueError("Extracted YMT exceeds the guarded read limit")
                        registration_evidence = self._registration_evidence(
                            record, registrations,
                        )
                        candidates.append(_YmtCandidate(
                            id=f"{member_path}::{record.entry_id}",
                            format="binary_ymt",
                            package_member=member_path,
                            archive_path=record.archive_path or None,
                            path=record.path,
                            indexed_size=record.size,
                            data=data,
                            registration_evidence=registration_evidence,
                        ))
                except (KeyError, OSError, RuntimeError, ValueError) as exc:
                    findings.append(YmtFinding(
                        "warning", "ymt_rpf_extract_failed",
                        f"Could not extract indexed YMT evidence: {exc}", member_path,
                    ))
        return candidates

    @staticmethod
    def _registration_evidence(
        record: RpfNativeEntryRecord,
        registrations: Iterable[PackageRegistrationRecord],
    ) -> tuple[str, ...]:
        container = PurePosixPath(record.archive_path).name.casefold()
        leaf = PurePosixPath(record.path).name.casefold()
        evidence: list[str] = []
        for registration in registrations:
            declared = {
                PurePosixPath(item.replace("\\", "/")).name.casefold()
                for item in registration.metadata_files
            }
            if container in declared or leaf in declared:
                evidence.append(
                    f"{registration.kind} {registration.source} declares "
                    f"{PurePosixPath(record.archive_path).name or record.path}"
                )
        return tuple(dict.fromkeys(evidence))

    def _catalog_entry(
        self, candidate: _YmtCandidate, edition: str,
        findings: list[YmtFinding],
    ) -> YmtCatalogEntry:
        source_sha = _sha256_bytes(candidate.data)
        try:
            xml = (
                candidate.data if candidate.format == "codewalker_xml"
                else self._decoder(Path(candidate.path).name, candidate.data, edition)
            )
            if not xml or len(xml) > MAX_YMT_XML_BYTES:
                raise ValueError("Decoded YMT XML is empty or exceeds guarded limits")
            facts = classify_ped_ymt_xml(xml)
            return YmtCatalogEntry(
                id=candidate.id, format=candidate.format,
                package_member=candidate.package_member,
                archive_path=candidate.archive_path, path=candidate.path,
                size=len(candidate.data), indexed_size=candidate.indexed_size,
                sha256=source_sha, decode_status="decoded", decode_error=None,
                decoded_sha256=_sha256_bytes(xml), decoder_edition=edition,
                root_type=facts.root_type, classification=facts.classification,
                identity=facts.identity, sex=facts.sex,
                classification_evidence=facts.classification_evidence,
                sex_evidence=facts.sex_evidence, metrics=facts.metrics,
                registration_status=(
                    "declared" if candidate.registration_evidence else "not_observed"
                ),
                registration_evidence=candidate.registration_evidence,
            )
        except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
            findings.append(YmtFinding(
                "warning", "ymt_decode_unsupported", str(exc), candidate.id,
            ))
            return YmtCatalogEntry(
                id=candidate.id, format=candidate.format,
                package_member=candidate.package_member,
                archive_path=candidate.archive_path, path=candidate.path,
                size=len(candidate.data), indexed_size=candidate.indexed_size,
                sha256=source_sha, decode_status="unsupported",
                decode_error=str(exc), decoded_sha256=None,
                decoder_edition=edition, root_type=None,
                classification="unknown", identity=None, sex="unknown",
                classification_evidence=(), sex_evidence=(), metrics={},
                registration_status=(
                    "declared" if candidate.registration_evidence else "not_observed"
                ),
                registration_evidence=candidate.registration_evidence,
            )

    @staticmethod
    def _dependency_edges(
        catalog: tuple[YmtCatalogEntry, ...], findings: list[YmtFinding],
    ) -> tuple[YmtDependencyEdge, ...]:
        edges: list[YmtDependencyEdge] = []
        for entry in catalog:
            cloth_count = entry.metrics.get("cloth_owned_drawable_count")
            if isinstance(cloth_count, int) and cloth_count > 0:
                edge_id = f"{entry.id}#cloth-ownership"
                edges.append(YmtDependencyEdge(
                    id=edge_id, source_id=entry.id, target_id=None,
                    target_kind="cloth_resource", kind="declares_cloth_ownership",
                    resolution="unresolved",
                    reason=(
                        f"{cloth_count} drawable record(s) declare ownsCloth=true, "
                        "but this YMT does not expose exact cloth asset identities."
                    ),
                    evidence=("decoded CPedVariationInfo ownsCloth=true",),
                ))
                findings.append(YmtFinding(
                    "info", "cloth_asset_identity_unresolved",
                    "Cloth ownership is observable, but exact YLD dependencies "
                    "cannot be derived from this document alone.", entry.id,
                ))
        identities: dict[tuple[str, str], list[YmtCatalogEntry]] = defaultdict(list)
        for entry in catalog:
            if entry.identity and entry.classification != "unknown":
                identities[(
                    entry.classification, entry.identity.casefold(),
                )].append(entry)
        for records in identities.values():
            if len(records) < 2:
                continue
            ordered = sorted(records, key=lambda item: item.id.casefold())
            declared = [item for item in ordered if item.registration_status == "declared"]
            resolution = "ambiguous" if len(declared) > 1 else "alternative"
            code = (
                "competing_ymt_definition" if resolution == "ambiguous"
                else "alternative_ymt_definition"
            )
            severity = "warning" if resolution == "ambiguous" else "info"
            for target in ordered[1:]:
                edge_id = f"{ordered[0].id}#definition#{target.id}"
                edges.append(YmtDependencyEdge(
                    id=edge_id, source_id=ordered[0].id, target_id=target.id,
                    target_kind="ymt_definition", kind="same_decoded_identity",
                    resolution=resolution,
                    reason=(
                        "Multiple decoded YMT documents define the same root identity; "
                        "registration evidence is reported separately."
                    ),
                    evidence=(
                        f"classification={ordered[0].classification}",
                        f"identity={ordered[0].identity}",
                    ),
                ))
            findings.append(YmtFinding(
                severity, code,
                f"{len(ordered)} YMT documents define {ordered[0].identity}; "
                f"{len(declared)} have package registration evidence.",
                ordered[0].id,
            ))
        return tuple(edges)
