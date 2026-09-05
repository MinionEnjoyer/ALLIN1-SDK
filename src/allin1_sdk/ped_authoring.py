"""Guarded copied workspaces for existing ped metadata authoring."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageFinding,
    PackageScan,
    PedRecord,
)
from allin1_sdk.authoring_core import GuardedXmlWorkspace, create_copied_workspace


AUTHORING_SCHEMA_VERSION = 1
PROJECT_SCHEMA_VERSION = 1
MANIFEST_NAME = "ped-authoring.json"
WORKSPACE_OPERATION = "ped_authoring_workspace"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_@.+-]{1,160}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_ASSET_SUFFIXES = frozenset({".ydd", ".ydr", ".ytd", ".ymt"})
_DRAWABLE_SUFFIXES = frozenset({".ydd", ".ydr"})
_TEXTURE_SUFFIXES = frozenset({".ytd"})
_PROPS_ASSET_SUFFIXES = _DRAWABLE_SUFFIXES | _TEXTURE_SUFFIXES

PED_FIELDS: dict[str, str] = {
    "ped.pedType": "Pedtype",
    "ped.modelType": "ModelType",
    "ped.propsName": "PropsName",
    "ped.clipDictionary": "ClipDictionaryName",
    "ped.expressionSet": "ExpressionSetName",
    "ped.movementClipSet": "MovementClipSet",
    "ped.creatureMetadata": "CreatureMetadataName",
}
EDITABLE_FIELDS = tuple(PED_FIELDS)
_REQUIRED_FIELDS = frozenset({"ped.pedType", "ped.modelType"})


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _direct_child(parent: etree._Element, name: str) -> etree._Element | None:
    return next((
        child for child in parent
        if isinstance(child.tag, str) and _local_name(child) == name
    ), None)


def _element_value(element: etree._Element | None) -> str:
    if element is None:
        return ""
    if "ref" in element.attrib:
        return element.attrib["ref"].strip()
    if "value" in element.attrib:
        return element.attrib["value"].strip()
    return (element.text or "").strip()


def _set_preserving_representation(
    parent: etree._Element, name: str, value: str,
) -> tuple[str, str]:
    element = _direct_child(parent, name)
    if element is None:
        raise ValueError(
            f"Existing ped record has no {name} node; guarded authoring does "
            "not synthesize schema fields"
        )
    before = _element_value(element)
    if "ref" in element.attrib:
        element.attrib.pop("value", None)
        element.set("ref", value)
        element.text = None
    elif "value" in element.attrib:
        element.attrib.pop("ref", None)
        element.set("value", value)
        element.text = None
    else:
        element.attrib.pop("ref", None)
        element.attrib.pop("value", None)
        element.text = value
    return before, value


def _inventory_fingerprint(scan: PackageScan) -> str:
    evidence = sorted(
        ((item.path.casefold(), item.size) for item in scan.entries),
        key=lambda item: item[0],
    )
    return hashlib.sha256(
        json.dumps(evidence, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _finding_signature(finding: PackageFinding) -> tuple[str, str, str, str]:
    return (
        finding.severity,
        finding.code,
        finding.path,
        finding.message,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_unknown_children(item: etree._Element) -> tuple[bytes, ...]:
    known = {"Name", *PED_FIELDS.values()}
    return tuple(
        etree.tostring(child, method="c14n", with_comments=True)
        for child in item
        if not isinstance(child.tag, str) or _local_name(child) not in known
    )


@dataclass(frozen=True)
class PedAuthoringProject:
    source: Path
    source_kind: str
    edition: str
    inventory_fingerprint: str
    peds: tuple[PedRecord, ...]
    findings: tuple[PackageFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "source": str(self.source),
            "source_kind": self.source_kind,
            "edition": self.edition,
            "inventory_fingerprint": self.inventory_fingerprint,
            "summary": {
                "peds": len(self.peds),
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "peds": [asdict(item) for item in self.peds],
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class PedAuthoringValues:
    ped: str
    values: dict[str, str]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PedAuthoringResult:
    workspace: Path
    revision: int
    ped: str
    changes: tuple[dict[str, str], ...]
    history: Path
    project: PedAuthoringProject

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "workspace": str(self.workspace),
            "revision": self.revision,
            "ped": self.ped,
            "changes": list(self.changes),
            "history": str(self.history),
            "validation": self.project.to_dict(),
        }


@dataclass(frozen=True)
class PedCloneSpec:
    donor_ped: str
    ped_name: str
    updates: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "donor_ped": self.donor_ped,
            "ped_name": self.ped_name,
            "updates": dict(sorted(self.updates.items())),
        }


@dataclass(frozen=True)
class PedCloneFinding:
    severity: str
    code: str
    message: str
    field: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PedCloneAddition:
    kind: str
    name: str
    source: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PedClonePlan:
    workspace: Path
    revision: int
    inventory_fingerprint: str
    spec: PedCloneSpec
    selected_sources: dict[str, str]
    source_sha256: dict[str, str]
    additions: tuple[PedCloneAddition, ...]
    findings: tuple[PedCloneFinding, ...]

    @property
    def ready(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "operation": "ped_bundle_clone_plan",
            "workspace": str(self.workspace),
            "revision": self.revision,
            "inventory_fingerprint": self.inventory_fingerprint,
            "spec": self.spec.to_dict(),
            "selected_sources": dict(sorted(self.selected_sources.items())),
            "source_sha256": dict(sorted(self.source_sha256.items())),
            "additions": [item.to_dict() for item in self.additions],
            "findings": [item.to_dict() for item in self.findings],
            "ready": self.ready,
        }

    @property
    def plan_sha256(self) -> str:
        return hashlib.sha256(json.dumps(
            self._payload(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["plan_sha256"] = self.plan_sha256
        return payload


class PedAuthoringWorkspace:
    """Copied package source with transactional edits to existing ped records."""

    def __init__(self, workspace: str | Path) -> None:
        self._core = GuardedXmlWorkspace(
            workspace,
            manifest_name=MANIFEST_NAME,
            operation=WORKSPACE_OPERATION,
            schema_version=AUTHORING_SCHEMA_VERSION,
            subject_label="Ped",
        )

    @property
    def root(self) -> Path:
        return self._core.root

    @property
    def source(self) -> Path:
        return self._core.source

    @property
    def manifest(self) -> dict[str, Any]:
        return self._core.manifest

    @property
    def revision(self) -> int:
        return self._core.revision

    @classmethod
    def create(
        cls, source: str | Path, destination: str | Path,
    ) -> "PedAuthoringWorkspace":
        source_path = Path(source).expanduser().resolve()
        scan = AddonPackageInspector().inspect(source_path)
        project = cls._project(scan)
        if not project.peds:
            raise ValueError(
                "Ped authoring requires visible peds.meta records; extract an "
                "opaque dlc.rpf into a reviewed source tree first"
            )
        fingerprint = project.inventory_fingerprint

        def validate_copy(content_root: Path) -> dict[str, Any]:
            copied = cls._project(AddonPackageInspector().inspect(content_root))
            if copied.inventory_fingerprint != fingerprint:
                raise RuntimeError(
                    "Copied ped-authoring inventory does not match its source"
                )
            if tuple(item.name for item in copied.peds) != tuple(
                item.name for item in project.peds
            ):
                raise RuntimeError("Copied ped records do not match their source")
            return copied.to_dict()

        manifest = {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "operation": WORKSPACE_OPERATION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "original_source": str(source_path),
            "inventory_fingerprint": fingerprint,
            "peds": [item.name for item in project.peds],
            "revision": 0,
            "editable_fields": list(EDITABLE_FIELDS),
            "identity_fields_locked": ["ped.Name"],
            "builder_operations": ["clone complete ped record"],
            "identity_migration": "transactional",
            "created_records": [],
        }
        target = create_copied_workspace(
            source_path,
            Path(destination).expanduser(),
            scan,
            manifest_name=MANIFEST_NAME,
            manifest=manifest,
            validation_name="initial-validation.json",
            validate_copy=validate_copy,
        )
        return cls(target)

    @staticmethod
    def _project(scan: PackageScan) -> PedAuthoringProject:
        return PedAuthoringProject(
            source=scan.source,
            source_kind=scan.source_kind,
            edition=scan.edition_tag,
            inventory_fingerprint=_inventory_fingerprint(scan),
            peds=scan.peds,
            findings=scan.findings,
        )

    def _scan_project(self) -> tuple[PackageScan, PedAuthoringProject]:
        scan = AddonPackageInspector().inspect(self.source)
        return scan, self._project(scan)

    def inspect(self) -> PedAuthoringProject:
        return self._scan_project()[1]

    def state_sha256(self) -> str:
        """Bind reviews to actual copied bytes, not just file sizes or revision."""
        from allin1_sdk.managed_package_conversion import _safe_publication_path

        _safe_publication_path(self.root)
        _safe_publication_path(self._core.manifest_path)
        scan = AddonPackageInspector().inspect(self.source)
        files = []
        for entry in sorted(scan.entries, key=lambda item: item.path):
            authored = self.source / entry.path
            _safe_publication_path(authored)
            files.append((entry.path, _file_sha256(self._core.member(entry.path))))
        return hashlib.sha256(json.dumps(
            {"manifest": self.manifest, "files": files},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()

    def _check_state(self, expected: str | None) -> None:
        if expected is not None and (
            not isinstance(expected, str) or not _SHA256.fullmatch(expected)
            or self.state_sha256() != expected
        ):
            raise ValueError("Ped workspace changed after review; inspect and review again")

    def publish_source(self) -> Path:
        if self.source.name.casefold() == "dlc.rpf.source":
            return self.source
        authored = tuple(
            path for path in self.source.rglob("dlc.rpf.source")
            if path.is_dir() and not path.is_symlink()
        )
        if len(authored) == 1:
            return self.source
        if len(authored) > 1:
            raise ValueError(
                "Ped authoring workspace contains multiple dlc.rpf.source directories"
            )
        if self.revision:
            raise ValueError(
                "This edited workspace contains only a prebuilt dlc.rpf. Extract it "
                "into one reviewed dlc.rpf.source before publishing so metadata edits "
                "cannot be silently omitted."
            )
        return self.source

    def values(
        self, ped_name: str, *, _scan: PackageScan | None = None,
    ) -> PedAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        ped = self._unique_ped(scan, ped_name)
        return PedAuthoringValues(
            ped=ped.name,
            values={
                "ped.pedType": ped.ped_type,
                "ped.modelType": ped.model_type,
                "ped.propsName": ped.props_name,
                "ped.clipDictionary": ped.clip_dictionary,
                "ped.expressionSet": ped.expression_set,
                "ped.movementClipSet": ped.movement_clip_set,
                "ped.creatureMetadata": ped.creature_metadata,
            },
            source=ped.source,
        )

    def plan_ped_clone(
        self,
        donor_ped: str,
        *,
        ped_name: str,
        updates: dict[str, str] | None = None,
    ) -> PedClonePlan:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            scan, _project = self._scan_project()
            donor = self._unique_ped(scan, donor_ped)
            normalized_name = self._validate_identity(ped_name, "ped name")
            normalized_updates = self._normalize_clone_updates(
                donor, normalized_name, updates or {},
            )
            spec = PedCloneSpec(donor.name, normalized_name, normalized_updates)
            return self._plan_ped_clone_locked(scan, spec)

    def _plan_ped_clone_locked(
        self, scan: PackageScan, spec: PedCloneSpec,
    ) -> PedClonePlan:
        project = self._project(scan)
        findings: list[PedCloneFinding] = []
        additions: list[PedCloneAddition] = []
        selected: dict[str, str] = {}
        source_hashes: dict[str, str] = {}
        donor_matches = [
            item for item in scan.peds
            if item.name.casefold() == spec.donor_ped.casefold()
        ]
        donor = donor_matches[0] if len(donor_matches) == 1 else None
        if donor is None:
            findings.append(PedCloneFinding(
                "error", "donor_ped_not_unique",
                f"Donor ped must resolve exactly once: {spec.donor_ped}",
                "donor_ped",
            ))
        elif any(
            item.name.casefold() == spec.ped_name.casefold()
            for item in scan.peds
        ):
            findings.append(PedCloneFinding(
                "error", "target_ped_exists",
                f"Target ped metadata already exists: {spec.ped_name}",
                "ped_name",
            ))
        if donor is not None:
            selected["ped_metadata"] = donor.source
            try:
                tree = self._core.read_tree(donor.source)
                item = self._record_item(tree, donor.name)
                missing = [
                    node for node in ("Name", *PED_FIELDS.values())
                    if _direct_child(item, node) is None
                ]
                if missing:
                    findings.append(PedCloneFinding(
                        "error", "donor_ped_incomplete",
                        "Donor record is missing guarded schema nodes: "
                        + ", ".join(missing), "donor_ped", donor.source,
                    ))
            except (OSError, ValueError) as exc:
                findings.append(PedCloneFinding(
                    "error", "donor_ped_xml_invalid", str(exc),
                    "donor_ped", donor.source,
                ))

        self._collect_required_asset(
            scan, spec.ped_name, _DRAWABLE_SUFFIXES, "model_drawable",
            findings, selected,
        )
        self._collect_required_asset(
            scan, spec.ped_name, _TEXTURE_SUFFIXES, "model_texture",
            findings, selected,
        )
        props_name = spec.updates.get("ped.propsName", "")
        if props_name and props_name.casefold() != spec.ped_name.casefold():
            self._collect_required_asset(
                scan, props_name, _DRAWABLE_SUFFIXES, "props_drawable",
                findings, selected,
            )
            self._collect_required_asset(
                scan, props_name, _TEXTURE_SUFFIXES, "props_texture",
                findings, selected,
            )
        if donor is not None:
            additions.append(PedCloneAddition(
                "ped", spec.ped_name, donor.source,
                f"complete metadata clone of {donor.name}",
            ))
        for source in sorted(set(selected.values()), key=str.casefold):
            try:
                source_hashes[source] = _file_sha256(self._core.member(source))
            except (OSError, ValueError) as exc:
                findings.append(PedCloneFinding(
                    "error", "selected_source_unreadable", str(exc), path=source,
                ))
        return PedClonePlan(
            workspace=self.root,
            revision=self.revision,
            inventory_fingerprint=project.inventory_fingerprint,
            spec=spec,
            selected_sources=dict(sorted(selected.items())),
            source_sha256=dict(sorted(source_hashes.items())),
            additions=tuple(additions),
            findings=tuple(findings),
        )

    def clone_ped_bundle(
        self,
        plan: PedClonePlan | dict[str, Any],
        *,
        expected_revision: int,
        expected_plan_sha256: str,
        expected_state_sha256: str | None = None,
    ) -> PedAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            self._check_state(expected_state_sha256)
            normalized_sha = str(expected_plan_sha256).strip().casefold()
            if not _SHA256.fullmatch(normalized_sha):
                raise ValueError(
                    "Expected ped-clone plan SHA-256 must be 64 lowercase hex digits"
                )
            spec, supplied_sha = self._clone_plan_input(plan)
            if supplied_sha != normalized_sha:
                raise ValueError(
                    "Ped-clone plan SHA-256 does not match the reviewed plan"
                )
            scan, before_project = self._scan_project()
            reviewed = self._plan_ped_clone_locked(scan, spec)
            if reviewed.revision != expected_revision:
                raise ValueError("Ped-clone plan revision changed during validation")
            if reviewed.plan_sha256 != normalized_sha:
                raise ValueError(
                    "Ped-clone plan is stale; package evidence or requested "
                    "identities changed"
                )
            if not reviewed.ready:
                blockers = [
                    item.code for item in reviewed.findings
                    if item.severity == "error"
                ]
                raise ValueError(
                    "Ped-clone plan is not ready: "
                    + ", ".join(blockers or ["unknown_blocker"])
                )
            donor = self._unique_ped(scan, spec.donor_ped)
            tree = self._core.read_tree(donor.source)
            donor_item = self._record_item(tree, donor.name)
            donor_unknown = _canonical_unknown_children(donor_item)
            clone = deepcopy(donor_item)
            changes: list[dict[str, str]] = []
            before, after = _set_preserving_representation(
                clone, "Name", spec.ped_name,
            )
            changes.append({"field": "ped.Name", "before": before, "after": after})
            for field, value in spec.updates.items():
                before, after = _set_preserving_representation(
                    clone, PED_FIELDS[field], value,
                )
                changes.append({"field": field, "before": before, "after": after})
            donor_item.addnext(clone)
            created = tuple(item.to_dict() for item in reviewed.additions)
            changes.extend({
                "field": "ped.created_record",
                "before": "",
                "after": json.dumps(item, sort_keys=True, separators=(",", ":")),
            } for item in created)
            return self._commit(
                ped=spec.ped_name,
                trees={donor.source: tree},
                changes=tuple(changes),
                before_project=before_project,
                verify=lambda after_scan: self._verify_ped_clone(
                    after_scan, reviewed, donor_unknown,
                ),
                operation="ped_bundle_clone",
                manifest_created_records=created,
            )

    def migrate_identity(
        self,
        ped_name: str,
        *,
        new_name: str,
        new_props: str | None = None,
        expected_revision: int | None = None,
        expected_state_sha256: str | None = None,
    ) -> PedAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            self._check_state(expected_state_sha256)
            scan, before_project = self._scan_project()
            current = self._unique_ped(scan, ped_name)
            target = self._validate_identity(new_name, "ped name")
            if new_props is None:
                target_props = (
                    f"{target}_p"
                    if current.props_name.casefold() == f"{current.name}_p".casefold()
                    else current.props_name
                )
            else:
                target_props = self._validate_identity(new_props, "props name")
            if (
                target.casefold() == current.name.casefold()
                and target_props.casefold() == current.props_name.casefold()
            ):
                raise ValueError("Ped identity migration contains no changed values")
            if target.casefold() != current.name.casefold() and any(
                item.name.casefold() == target.casefold() for item in scan.peds
            ):
                raise ValueError(f"Ped identity already exists: {target}")
            tree = self._core.read_tree(current.source)
            item = self._record_item(tree, current.name)
            changes: list[dict[str, str]] = []
            if target.casefold() != current.name.casefold():
                before, after = _set_preserving_representation(item, "Name", target)
                changes.append({"field": "ped.Name", "before": before, "after": after})
            if target_props.casefold() != current.props_name.casefold():
                before, after = _set_preserving_representation(
                    item, "PropsName", target_props,
                )
                changes.append({
                    "field": "ped.propsName", "before": before, "after": after,
                })
            renames = self._identity_asset_renames(
                scan, current.name, target,
                current.props_name, target_props,
            )
            self._require_identity_assets(
                scan, current.name, current.props_name,
                change_props=(
                    target_props.casefold() != current.props_name.casefold()
                ),
            )
            return self._commit(
                ped=target,
                trees={current.source: tree},
                changes=tuple(changes),
                before_project=before_project,
                verify=lambda after_scan: self._verify_identity_migration(
                    after_scan, target, target_props, renames,
                ),
                operation="ped_identity_migration",
                renames=tuple(renames),
                extra_files=tuple(item["before"] for item in renames),
            )

    def update(
        self,
        ped_name: str,
        updates: dict[str, str],
        *,
        expected_revision: int | None = None,
        expected_state_sha256: str | None = None,
    ) -> PedAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            self._check_state(expected_state_sha256)
            return self._update_locked(ped_name, updates)

    def _update_locked(
        self, ped_name: str, updates: dict[str, str],
    ) -> PedAuthoringResult:
        unknown = sorted(set(updates) - set(EDITABLE_FIELDS))
        if unknown:
            raise ValueError(
                "Unsupported ped authoring fields: " + ", ".join(unknown)
            )
        if not updates:
            raise ValueError("Ped authoring update contains no fields")
        scan, before_project = self._scan_project()
        current = self.values(ped_name, _scan=scan)
        normalized = {
            key: self._validate_value(key, str(value).strip())
            for key, value in updates.items()
        }
        changed = {
            key: value for key, value in normalized.items()
            if value != current.values.get(key, "")
        }
        if not changed:
            raise ValueError("Ped authoring update contains no changed values")
        tree = self._core.read_tree(current.source)
        item = self._record_item(tree, current.ped)
        changes: list[dict[str, str]] = []
        for key, value in changed.items():
            before, after = _set_preserving_representation(
                item, PED_FIELDS[key], value,
            )
            changes.append({"field": key, "before": before, "after": after})
        return self._commit(
            ped=current.ped,
            trees={current.source: tree},
            changes=tuple(changes),
            before_project=before_project,
            verify=lambda after_scan: self._verify_values(
                after_scan, current.ped, changed,
            ),
        )

    def undo(
        self, *, expected_revision: int | None = None,
        expected_state_sha256: str | None = None,
    ) -> PedAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            self._check_state(expected_state_sha256)
            history = self._core.latest_history()
            self._core.verify_post_edit_state(history)
            record = self._core.history_record(history)
            ped = str(record.get("subject", ""))
            changes = tuple(
                dict(item) for item in record.get("changes", ())
                if isinstance(item, dict)
            )
            recovery = self._core.snapshot_current_for_undo(history)
            previous_manifest = dict(self.manifest)
            undone = history.with_name(f"{history.name}.undone")
            try:
                self._core.restore(history)
                project = self.inspect()
                revision = self.revision + 1
                self.manifest["revision"] = revision
                self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
                self.manifest["peds"] = [item.name for item in project.peds]
                removed_records = {
                    item.get("after") for item in changes
                    if item.get("field") == "ped.created_record"
                }
                if removed_records:
                    self.manifest["created_records"] = [
                        item for item in self.manifest.get("created_records", [])
                        if json.dumps(
                            item, sort_keys=True, separators=(",", ":"),
                        ) not in removed_records
                    ]
                history.rename(undone)
                self._core.write_manifest()
            except Exception:
                self.manifest.clear()
                self.manifest.update(previous_manifest)
                if undone.exists() and not history.exists():
                    undone.rename(history)
                self._core.restore(recovery)
                shutil.rmtree(recovery, ignore_errors=True)
                raise
            shutil.rmtree(recovery, ignore_errors=True)
            return PedAuthoringResult(
                workspace=self.root,
                revision=revision,
                ped=ped,
                changes=changes,
                history=undone,
                project=project,
            )

    def _commit(
        self,
        *,
        ped: str,
        trees: dict[str, etree._ElementTree],
        changes: tuple[dict[str, str], ...],
        before_project: PedAuthoringProject,
        verify: Any,
        operation: str = "ped_metadata_edit",
        renames: tuple[dict[str, str], ...] = (),
        extra_files: tuple[str, ...] = (),
        manifest_created_records: tuple[dict[str, str], ...] = (),
    ) -> PedAuthoringResult:
        history = self._core.snapshot(
            ped,
            tuple(dict.fromkeys((*trees, *extra_files))),
            changes,
            operation=operation,
            renames=renames,
        )
        previous_manifest = dict(self.manifest)
        try:
            self._core.commit_trees(trees)
            for rename in renames:
                source = self._core.member(rename["before"])
                destination = self._core.destination(rename["after"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
            after_scan, after_project = self._scan_project()
            self._reject_validation_regressions(before_project, after_project)
            verify(after_scan)
            self._core.record_post_edit_state(history)
            revision = self.revision + 1
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["peds"] = [item.name for item in after_project.peds]
            if manifest_created_records:
                self.manifest.setdefault("created_records", []).extend(
                    dict(item) for item in manifest_created_records
                )
            (history / "validation.json").write_text(
                json.dumps(after_project.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            self._core.write_manifest()
        except Exception:
            self.manifest.clear()
            self.manifest.update(previous_manifest)
            self._core.restore(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return PedAuthoringResult(
            workspace=self.root,
            revision=revision,
            ped=ped,
            changes=changes,
            history=history,
            project=after_project,
        )

    def _check_revision(self, expected: int | None) -> None:
        if expected is None:
            return
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError(
                "Expected ped-authoring revision must be a non-negative integer"
            )
        if expected != self.revision:
            raise ValueError(
                f"Ped-authoring revision conflict: expected {expected}, "
                f"current revision is {self.revision}"
            )

    @classmethod
    def _normalize_clone_updates(
        cls,
        donor: PedRecord,
        target_name: str,
        updates: dict[str, str],
    ) -> dict[str, str]:
        unknown = sorted(set(updates) - set(EDITABLE_FIELDS))
        if unknown:
            raise ValueError(
                "Unsupported ped clone fields: " + ", ".join(unknown)
            )
        normalized = {
            key: cls._validate_value(key, str(value).strip())
            for key, value in updates.items()
        }
        if "ped.propsName" not in normalized:
            normalized["ped.propsName"] = (
                f"{target_name}_p"
                if donor.props_name.casefold() == f"{donor.name}_p".casefold()
                else donor.props_name
            )
        return dict(sorted(normalized.items()))

    @classmethod
    def _clone_plan_input(
        cls, plan: PedClonePlan | dict[str, Any],
    ) -> tuple[PedCloneSpec, str]:
        if isinstance(plan, PedClonePlan):
            raw_spec: Any = plan.spec.to_dict()
            digest = plan.plan_sha256
        elif isinstance(plan, dict):
            raw_spec = plan.get("spec")
            digest = str(plan.get("plan_sha256", "")).strip().casefold()
        else:
            raise ValueError("Ped clone plan must be a reviewed plan object")
        if not isinstance(raw_spec, dict) or not _SHA256.fullmatch(digest):
            raise ValueError("Ped clone plan is missing its specification or digest")
        donor = raw_spec.get("donor_ped")
        target = raw_spec.get("ped_name")
        updates = raw_spec.get("updates")
        if (
            not isinstance(donor, str)
            or not isinstance(target, str)
            or not isinstance(updates, dict)
            or not all(isinstance(key, str) and isinstance(value, str)
                       for key, value in updates.items())
        ):
            raise ValueError("Ped clone plan has an invalid specification")
        normalized_updates = {
            key: cls._validate_value(key, value.strip())
            for key, value in updates.items()
        }
        unknown = sorted(set(normalized_updates) - set(EDITABLE_FIELDS))
        if unknown:
            raise ValueError(
                "Unsupported ped clone fields: " + ", ".join(unknown)
            )
        return PedCloneSpec(
            cls._validate_identity(donor, "donor identity"),
            cls._validate_identity(target, "ped name"),
            dict(sorted(normalized_updates.items())),
        ), digest

    @staticmethod
    def _asset_paths(
        scan: PackageScan, identity: str, suffixes: frozenset[str],
    ) -> tuple[str, ...]:
        return tuple(sorted(
            (
                entry.path for entry in scan.entries
                if PurePosixPath(entry.path).stem.casefold() == identity.casefold()
                and PurePosixPath(entry.path).suffix.casefold() in suffixes
            ),
            key=str.casefold,
        ))

    @classmethod
    def _collect_required_asset(
        cls,
        scan: PackageScan,
        identity: str,
        suffixes: frozenset[str],
        field: str,
        findings: list[PedCloneFinding],
        selected: dict[str, str],
    ) -> None:
        matches = cls._asset_paths(scan, identity, suffixes)
        if len(matches) != 1:
            findings.append(PedCloneFinding(
                "error", f"target_{field}_not_unique",
                f"Target {field.replace('_', ' ')} requires one exact package "
                + ("asset; found none" if not matches else
                   "asset; found " + ", ".join(matches)),
                field,
            ))
        else:
            selected[field] = matches[0]

    @classmethod
    def _identity_asset_renames(
        cls,
        scan: PackageScan,
        old_name: str,
        new_name: str,
        old_props: str,
        new_props: str,
    ) -> list[dict[str, str]]:
        requests: list[tuple[str, str, frozenset[str]]] = []
        if old_name.casefold() != new_name.casefold():
            requests.append((old_name, new_name, _MODEL_ASSET_SUFFIXES))
        if (
            old_props
            and old_props.casefold() != new_props.casefold()
            and old_props.casefold() != old_name.casefold()
        ):
            requests.append((old_props, new_props, _PROPS_ASSET_SUFFIXES))
        existing = {entry.path.casefold() for entry in scan.entries}
        sources: set[str] = set()
        destinations: set[str] = set()
        renames: list[dict[str, str]] = []
        for before_identity, after_identity, suffixes in requests:
            for before in cls._asset_paths(scan, before_identity, suffixes):
                member = PurePosixPath(before)
                after = member.with_name(
                    f"{after_identity}{member.suffix}"
                ).as_posix()
                if before.casefold() in sources or after.casefold() in destinations:
                    raise ValueError("Ped identity migration has conflicting asset paths")
                if after.casefold() in existing and after.casefold() != before.casefold():
                    raise ValueError(
                        f"Ped identity migration destination exists: {after}"
                    )
                sources.add(before.casefold())
                destinations.add(after.casefold())
                renames.append({"before": before, "after": after})
        return renames

    @classmethod
    def _require_identity_assets(
        cls,
        scan: PackageScan,
        model: str,
        props: str,
        *,
        change_props: bool,
    ) -> None:
        for identity, suffixes, label in (
            (model, _DRAWABLE_SUFFIXES, "model drawable"),
            (model, _TEXTURE_SUFFIXES, "model texture"),
        ):
            matches = cls._asset_paths(scan, identity, suffixes)
            if len(matches) != 1:
                raise ValueError(
                    f"Ped identity migration requires one owned {label}: {identity}"
                )
        if change_props and props and props.casefold() != model.casefold():
            for suffixes, label in (
                (_DRAWABLE_SUFFIXES, "props drawable"),
                (_TEXTURE_SUFFIXES, "props texture"),
            ):
                matches = cls._asset_paths(scan, props, suffixes)
                if len(matches) != 1:
                    raise ValueError(
                        f"Ped identity migration requires one owned {label}: {props}"
                    )

    @staticmethod
    def _validate_identity(value: str, label: str) -> str:
        normalized = str(value).strip()
        if not normalized or not _IDENTIFIER.fullmatch(normalized):
            raise ValueError(
                f"Ped {label} must be a safe game identifier (1-160 characters)"
            )
        return normalized

    @staticmethod
    def _unique_ped(scan: PackageScan, name: str) -> PedRecord:
        matches = [
            item for item in scan.peds if item.name.casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Ped was not found uniquely in workspace: {name}")
        return matches[0]

    @staticmethod
    def _validate_value(field: str, value: str) -> str:
        if not value:
            if field in _REQUIRED_FIELDS:
                raise ValueError(f"Ped field may not be empty: {field}")
            return ""
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(
                f"Ped field must be a safe game identifier (1-160 characters): {field}"
            )
        return value

    @staticmethod
    def _record_item(
        tree: etree._ElementTree, ped_name: str,
    ) -> etree._Element:
        matches: list[etree._Element] = []
        for container in tree.getroot().iter():
            if not isinstance(container.tag, str) or _local_name(container) != "InitDatas":
                continue
            for item in container:
                if not isinstance(item.tag, str) or _local_name(item) != "Item":
                    continue
                if _element_value(_direct_child(item, "Name")).casefold() \
                        == ped_name.casefold():
                    matches.append(item)
        if len(matches) != 1:
            raise ValueError(f"Ped XML record was not found uniquely: {ped_name}")
        return matches[0]

    @classmethod
    def _verify_values(
        cls, scan: PackageScan, ped_name: str, expected: dict[str, str],
    ) -> None:
        ped = cls._unique_ped(scan, ped_name)
        actual = {
            "ped.pedType": ped.ped_type,
            "ped.modelType": ped.model_type,
            "ped.propsName": ped.props_name,
            "ped.clipDictionary": ped.clip_dictionary,
            "ped.expressionSet": ped.expression_set,
            "ped.movementClipSet": ped.movement_clip_set,
            "ped.creatureMetadata": ped.creature_metadata,
        }
        mismatches = [
            key for key, value in expected.items() if actual.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(
                "Ped authoring verification failed for: " + ", ".join(mismatches)
            )

    @classmethod
    def _verify_ped_clone(
        cls,
        scan: PackageScan,
        plan: PedClonePlan,
        donor_unknown: tuple[bytes, ...],
    ) -> None:
        ped = cls._unique_ped(scan, plan.spec.ped_name)
        expected = plan.spec.updates
        actual = {
            "ped.pedType": ped.ped_type,
            "ped.modelType": ped.model_type,
            "ped.propsName": ped.props_name,
            "ped.clipDictionary": ped.clip_dictionary,
            "ped.expressionSet": ped.expression_set,
            "ped.movementClipSet": ped.movement_clip_set,
            "ped.creatureMetadata": ped.creature_metadata,
        }
        mismatches = [
            key for key, value in expected.items() if actual.get(key) != value
        ]
        if mismatches:
            raise RuntimeError(
                "Ped clone verification failed for: " + ", ".join(mismatches)
            )
        workspace = cls(plan.workspace)
        tree = workspace._core.read_tree(ped.source)
        cloned = workspace._record_item(tree, ped.name)
        if _canonical_unknown_children(cloned) != donor_unknown:
            raise RuntimeError("Ped clone did not preserve unknown donor XML")

    @classmethod
    def _verify_identity_migration(
        cls,
        scan: PackageScan,
        target: str,
        target_props: str,
        renames: list[dict[str, str]],
    ) -> None:
        ped = cls._unique_ped(scan, target)
        if ped.props_name.casefold() != target_props.casefold():
            raise RuntimeError("Migrated ped props identity did not round-trip")
        entries = {entry.path.casefold() for entry in scan.entries}
        missing = [
            item["after"] for item in renames
            if item["after"].casefold() not in entries
        ]
        retained = [
            item["before"] for item in renames
            if item["before"].casefold() in entries
            and item["before"].casefold() != item["after"].casefold()
        ]
        if missing or retained:
            raise RuntimeError(
                "Ped identity asset migration did not round-trip: "
                + ", ".join((*missing, *retained))
            )

    @staticmethod
    def _reject_validation_regressions(
        before: PedAuthoringProject, after: PedAuthoringProject,
    ) -> None:
        baseline = {_finding_signature(item) for item in before.findings}
        introduced = [
            item for item in after.findings
            if _finding_signature(item) not in baseline
            and item.severity in {"error", "warning"}
        ]
        if introduced:
            summary = "; ".join(
                f"{item.code}: {item.message}" for item in introduced[:4]
            )
            raise ValueError(
                "Ped metadata edit introduced new validation findings: " + summary
            )


__all__ = [
    "AUTHORING_SCHEMA_VERSION",
    "EDITABLE_FIELDS",
    "PED_FIELDS",
    "PedCloneAddition",
    "PedCloneFinding",
    "PedClonePlan",
    "PedCloneSpec",
    "PedAuthoringProject",
    "PedAuthoringResult",
    "PedAuthoringValues",
    "PedAuthoringWorkspace",
]
