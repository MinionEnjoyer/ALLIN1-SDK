"""Guarded copied workspaces for existing weapon metadata authoring."""

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
    AmmoRecord,
    PackageFinding,
    PackageScan,
    WeaponComponentLink,
    WeaponComponentRecord,
    WeaponAnimationRecord,
    WeaponRecord,
    WeaponShopRecord,
)
from allin1_sdk.addon_sdk import joaat
from allin1_sdk.authoring_core import (
    GuardedXmlWorkspace,
    create_copied_workspace,
    safe_relative_path,
)


AUTHORING_SCHEMA_VERSION = 1
PROJECT_SCHEMA_VERSION = 1
MANIFEST_NAME = "weapon-authoring.json"
WORKSPACE_OPERATION = "weapon_authoring_workspace"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

WEAPON_FIELDS: dict[str, str] = {
    "weapon.slot": "Slot",
    "weapon.ammoInfo": "AmmoInfo",
    "weapon.model": "Model",
    "weapon.humanNameHash": "HumanNameHash",
    "weapon.statName": "StatName",
}
AMMO_FIELDS: dict[str, str] = {
    "ammo.model": "Model",
    "ammo.ammoMax": "AmmoMax",
    "ammo.ammoMax50": "AmmoMax50",
    "ammo.explosion": "Explosion",
    "ammo.trailFx": "TrailFx",
    "ammo.primedFx": "PrimedFx",
}
COMPONENT_FIELDS: dict[str, str] = {
    "component.model": "Model",
    "component.locName": "LocName",
    "component.locDesc": "LocDesc",
    "component.attachBone": "AttachBone",
}
ATTACHMENT_FIELDS: tuple[str, ...] = ("attachment.default",)
SHOP_FIELDS: dict[str, str] = {
    "shop.cost": "cost",
    "shop.ammoCost": "ammoCost",
    "shop.textLabel": "textLabel",
    "shop.weaponDesc": "weaponDesc",
    "shop.weaponTT": "weaponTT",
    "shop.weaponUppercase": "weaponUppercase",
    "shop.availableInSP": "availableInSP",
}
EDITABLE_FIELDS = tuple((*WEAPON_FIELDS, *AMMO_FIELDS))
EDITABLE_COMPONENT_FIELDS = tuple(COMPONENT_FIELDS)
EDITABLE_SHOP_FIELDS = tuple(SHOP_FIELDS)

_RELATIONSHIP_FINDINGS = frozenset({
    "weapon_ammo_reference_missing",
    "ammo_definition_not_found",
    "animation_mapping_not_found",
    "storefront_mapping_not_found",
    "weapon_component_definition_not_found",
    "duplicate_record",
    "duplicate_weapon_animation_record",
    "duplicate_weapon_shop_record",
    "xml_parse_failed",
})


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
    parent: etree._Element,
    name: str,
    value: str,
) -> tuple[str, str]:
    element = _direct_child(parent, name)
    if element is None:
        raise ValueError(
            f"Existing authoring record has no {name} node; guarded authoring does "
            "not synthesize schema fields"
        )
    elif "ref" in element.attrib:
        representation = "ref"
    elif "value" in element.attrib:
        representation = "value"
    else:
        representation = "text"
    before = _element_value(element)
    if representation == "ref":
        element.attrib.pop("value", None)
        element.set("ref", value)
        element.text = None
    elif representation == "value":
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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _casefold_unique(values: Any) -> tuple[str, ...]:
    """Preserve canonical spellings without double-counting case aliases."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw)
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class WeaponAuthoringProject:
    source: Path
    source_kind: str
    edition: str
    inventory_fingerprint: str
    weapons: tuple[WeaponRecord, ...]
    ammo: tuple[AmmoRecord, ...]
    components: tuple[WeaponComponentRecord, ...]
    attachments: tuple[WeaponComponentLink, ...]
    animation_weapons: tuple[str, ...]
    shop_weapons: tuple[str, ...]
    animation_records: tuple[WeaponAnimationRecord, ...]
    shop_records: tuple[WeaponShopRecord, ...]
    findings: tuple[PackageFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def weapon(self, name: str) -> WeaponRecord:
        matches = [
            item for item in self.weapons if item.name.casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Weapon was not found uniquely: {name}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "source": str(self.source),
            "source_kind": self.source_kind,
            "edition": self.edition,
            "inventory_fingerprint": self.inventory_fingerprint,
            "summary": {
                "weapons": len(self.weapons),
                "ammo": len(self.ammo),
                "components": len(self.components),
                "attachments": len(self.attachments),
                "animation_records": len(self.animation_records),
                "shop_records": len(self.shop_records),
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "weapons": [asdict(item) for item in self.weapons],
            "ammo": [asdict(item) for item in self.ammo],
            "components": [asdict(item) for item in self.components],
            "attachments": [asdict(item) for item in self.attachments],
            "animation_weapons": list(self.animation_weapons),
            "shop_weapons": list(self.shop_weapons),
            "animation_records": [asdict(item) for item in self.animation_records],
            "shop_records": [asdict(item) for item in self.shop_records],
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class WeaponAuthoringValues:
    weapon: str
    values: dict[str, str]
    sources: dict[str, str]
    affected_weapons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponComponentAuthoringValues:
    component: str
    values: dict[str, str]
    source: str
    affected_weapons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponAnimationAuthoringValues:
    weapon: str
    source: str
    records: tuple[WeaponAnimationRecord, ...]
    set_names: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "weapon": self.weapon,
            "source": self.source,
            "records": [asdict(item) for item in self.records],
            "set_names": list(self.set_names),
        }


@dataclass(frozen=True)
class WeaponShopAuthoringValues:
    weapon: str
    source: str
    identity_field: str
    identity_representation: str
    values: dict[str, str]
    representations: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponCloneSpec:
    donor_weapon: str
    weapon_name: str
    slot: str
    ammo_info: str
    model: str
    human_name_hash: str
    stat_name: str
    clone_ammo: bool
    ammo_name: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponCloneFinding:
    severity: str
    code: str
    message: str
    field: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponCloneCollision:
    field: str
    value: str
    existing: str
    reason: str
    hash: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponCloneAddition:
    kind: str
    name: str
    source: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class WeaponClonePlan:
    workspace: Path
    revision: int
    inventory_fingerprint: str
    spec: WeaponCloneSpec
    donor_complete: bool
    donor_completeness: dict[str, Any]
    selected_sources: dict[str, str]
    source_sha256: dict[str, str]
    reused_components: tuple[str, ...]
    additions: tuple[WeaponCloneAddition, ...]
    collisions: tuple[WeaponCloneCollision, ...]
    findings: tuple[WeaponCloneFinding, ...]

    @property
    def ready(self) -> bool:
        return self.donor_complete and not self.collisions and not any(
            item.severity == "error" for item in self.findings
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "operation": "weapon_bundle_clone_plan",
            "workspace": str(self.workspace),
            "revision": self.revision,
            "inventory_fingerprint": self.inventory_fingerprint,
            "spec": self.spec.to_dict(),
            "donor_complete": self.donor_complete,
            "donor_completeness": self.donor_completeness,
            "selected_sources": self.selected_sources,
            "source_sha256": self.source_sha256,
            "reused_components": list(self.reused_components),
            "additions": [item.to_dict() for item in self.additions],
            "collisions": [item.to_dict() for item in self.collisions],
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


@dataclass(frozen=True)
class WeaponAuthoringResult:
    workspace: Path
    revision: int
    subject: str
    subject_kind: str
    changes: tuple[dict[str, str], ...]
    history: Path
    affected_weapons: tuple[str, ...]
    project: WeaponAuthoringProject

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "workspace": str(self.workspace),
            "revision": self.revision,
            "subject": self.subject,
            "subject_kind": self.subject_kind,
            "changes": list(self.changes),
            "history": str(self.history),
            "affected_weapons": list(self.affected_weapons),
            "validation": self.project.to_dict(),
        }


class WeaponAuthoringWorkspace:
    """Copied package source with transactional edits to existing weapon records."""

    def __init__(self, workspace: str | Path) -> None:
        self._core = GuardedXmlWorkspace(
            workspace,
            manifest_name=MANIFEST_NAME,
            operation=WORKSPACE_OPERATION,
            schema_version=AUTHORING_SCHEMA_VERSION,
            subject_label="Weapon",
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
    def manifest_path(self) -> Path:
        return self._core.manifest_path

    @property
    def revision(self) -> int:
        return self._core.revision

    @classmethod
    def create(
        cls, source: str | Path, destination: str | Path,
    ) -> "WeaponAuthoringWorkspace":
        source_path = Path(source).expanduser().resolve()
        scan = AddonPackageInspector().inspect(source_path)
        project = cls._project(scan)
        if not project.weapons:
            raise ValueError(
                "Weapon authoring requires visible weapons.meta records; extract an "
                "opaque dlc.rpf into a reviewed source tree first"
            )
        fingerprint = project.inventory_fingerprint

        def validate_copy(content_root: Path) -> dict[str, Any]:
            copied = cls._project(AddonPackageInspector().inspect(content_root))
            if copied.inventory_fingerprint != fingerprint:
                raise RuntimeError("Copied weapon-authoring inventory does not match its source")
            if tuple(item.name for item in copied.weapons) != tuple(
                item.name for item in project.weapons
            ):
                raise RuntimeError("Copied weapon records do not match their source")
            return copied.to_dict()

        manifest = {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "operation": WORKSPACE_OPERATION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "original_source": str(source_path),
            "inventory_fingerprint": fingerprint,
            "weapons": [item.name for item in project.weapons],
            "revision": 0,
            "editable_fields": list(EDITABLE_FIELDS),
            "editable_component_fields": list(EDITABLE_COMPONENT_FIELDS),
            "editable_attachment_fields": list(ATTACHMENT_FIELDS),
            "editable_shop_fields": list(EDITABLE_SHOP_FIELDS),
            "editable_animation_operations": ["clone_from_existing_template"],
            "builder_operations": ["clone_complete_weapon_bundle"],
            "created_records": [],
            "identity_fields_locked": [
                "weapon.Name", "ammo.Name", "component.Name",
                "animation.Item@key", "shop.nameHash", "shop.weaponName",
            ],
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
    def _project(scan: PackageScan) -> WeaponAuthoringProject:
        return WeaponAuthoringProject(
            source=scan.source,
            source_kind=scan.source_kind,
            edition=scan.edition_tag,
            inventory_fingerprint=_inventory_fingerprint(scan),
            weapons=scan.weapons,
            ammo=scan.ammo,
            components=scan.weapon_components,
            attachments=scan.weapon_component_links,
            animation_weapons=scan.animation_weapons,
            shop_weapons=scan.shop_weapons,
            animation_records=scan.weapon_animation_records,
            shop_records=scan.weapon_shop_records,
            findings=scan.findings,
        )

    def _scan_project(self) -> tuple[PackageScan, WeaponAuthoringProject]:
        scan = AddonPackageInspector().inspect(self.source)
        return scan, self._project(scan)

    def inspect(self) -> WeaponAuthoringProject:
        return self._scan_project()[1]

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
                "Weapon authoring workspace contains multiple dlc.rpf.source directories"
            )
        if self.revision:
            raise ValueError(
                "This edited workspace contains only a prebuilt dlc.rpf. Extract it "
                "into one reviewed dlc.rpf.source before publishing so metadata edits "
                "cannot be silently omitted."
            )
        return self.source

    def values(
        self, weapon_name: str, *, _scan: PackageScan | None = None,
    ) -> WeaponAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        weapon = self._unique_weapon(scan, weapon_name)
        ammo_matches = [
            item for item in scan.ammo
            if item.name.casefold() == weapon.ammo_info.casefold()
        ]
        ammo = ammo_matches[0] if len(ammo_matches) == 1 else None
        values = {
            "weapon.slot": weapon.slot,
            "weapon.ammoInfo": weapon.ammo_info,
            "weapon.model": weapon.model,
            "weapon.humanNameHash": weapon.human_name_hash,
            "weapon.statName": weapon.stat_name,
        }
        sources = {"weapon": weapon.source}
        affected = (weapon.name,)
        if ammo is not None:
            values.update({
                "ammo.model": ammo.model,
                "ammo.ammoMax": ammo.ammo_max,
                "ammo.ammoMax50": ammo.ammo_max_50,
                "ammo.explosion": ammo.explosion,
                "ammo.trailFx": ammo.trail_fx,
                "ammo.primedFx": ammo.primed_fx,
            })
            sources["ammo"] = ammo.source
            affected = self._weapons_using_ammo(scan, ammo.name)
        return WeaponAuthoringValues(weapon.name, values, sources, affected)

    def component_values(
        self, component_name: str, *, _scan: PackageScan | None = None,
    ) -> WeaponComponentAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        component = self._unique_component(scan, component_name)
        affected = _casefold_unique(
            item.weapon_name for item in scan.weapon_component_links
            if item.component_name.casefold() == component.name.casefold()
        )
        return WeaponComponentAuthoringValues(
            component=component.name,
            values={
                "component.model": component.model,
                "component.locName": component.loc_name,
                "component.locDesc": component.loc_desc,
                "component.attachBone": component.attach_bone,
                "component.type": component.component_type,
            },
            source=component.source,
            affected_weapons=affected,
        )

    def animation_values(
        self,
        weapon_name: str,
        source: str | None = None,
        *,
        _scan: PackageScan | None = None,
    ) -> WeaponAnimationAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        canonical_weapon = self._animation_identity(scan, weapon_name)
        records = tuple(
            item for item in scan.weapon_animation_records
            if item.weapon_name.casefold() == canonical_weapon.casefold()
        )
        selected_source = self._select_relationship_source(
            records, source, "weapon animation",
        )
        selected = tuple(sorted(
            (item for item in records if item.source == selected_source),
            key=lambda item: (item.set_ordinal, item.ordinal),
        ))
        tree = self._core.read_tree(selected_source)
        for record in selected:
            self._animation_item(tree, record)
        return WeaponAnimationAuthoringValues(
            weapon=canonical_weapon,
            source=selected_source,
            records=selected,
            set_names=tuple(item.set_name for item in selected),
        )

    def shop_values(
        self,
        weapon_name: str,
        source: str | None = None,
        *,
        _scan: PackageScan | None = None,
    ) -> WeaponShopAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        weapon = self._unique_weapon(scan, weapon_name)
        records = tuple(
            item for item in scan.weapon_shop_records
            if item.weapon_name.casefold() == weapon.name.casefold()
        )
        selected_source = self._select_relationship_source(
            records, source, "weapon shop",
        )
        selected = tuple(item for item in records if item.source == selected_source)
        tree = self._core.read_tree(selected_source)
        item, identity = self._shop_item(tree, weapon.name, selected)
        values: dict[str, str] = {}
        representations: dict[str, str] = {}
        for field, node_name in SHOP_FIELDS.items():
            node = _direct_child(item, node_name)
            values[field] = _element_value(node)
            representations[field] = self._element_representation(node)
        return WeaponShopAuthoringValues(
            weapon=weapon.name,
            source=selected_source,
            identity_field=_local_name(identity),
            identity_representation=self._element_representation(identity),
            values=values,
            representations=representations,
        )

    def plan_weapon_clone(
        self,
        donor_weapon: str,
        *,
        weapon_name: str,
        slot: str,
        ammo_info: str,
        model: str,
        human_name_hash: str,
        stat_name: str,
        clone_ammo: bool = True,
        ammo_name: str | None = None,
    ) -> WeaponClonePlan:
        spec = self._normalize_clone_spec(
            donor_weapon=donor_weapon,
            weapon_name=weapon_name,
            slot=slot,
            ammo_info=ammo_info,
            model=model,
            human_name_hash=human_name_hash,
            stat_name=stat_name,
            clone_ammo=clone_ammo,
            ammo_name=ammo_name,
        )
        with self._core.operation_lock():
            self._core.refresh_manifest()
            return self._plan_weapon_clone_locked(spec)

    def _plan_weapon_clone_locked(self, spec: WeaponCloneSpec) -> WeaponClonePlan:
        scan, project = self._scan_project()
        findings: list[WeaponCloneFinding] = []
        collisions: list[WeaponCloneCollision] = []
        additions: list[WeaponCloneAddition] = []
        selected_sources: dict[str, str] = {}
        source_sha256: dict[str, str] = {}
        completeness: dict[str, Any] = {
            "weapon_record": False,
            "ammo_record": False,
            "animation_mappings": 0,
            "animation_sets": [],
            "shop_record": False,
            "attachment_links": 0,
            "component_definitions": 0,
            "authorable_source": False,
        }

        donor_matches = [
            item for item in scan.weapons
            if item.name.casefold() == spec.donor_weapon.casefold()
        ]
        donor = donor_matches[0] if len(donor_matches) == 1 else None
        if donor is None:
            findings.append(WeaponCloneFinding(
                "error", "donor_weapon_not_unique",
                f"Donor weapon must resolve exactly once: {spec.donor_weapon}",
                "donor_weapon",
            ))
        elif self._has_duplicate_record_finding(scan, donor.name):
            findings.append(WeaponCloneFinding(
                "error", "donor_weapon_duplicated",
                f"Donor weapon has a duplicate package record: {donor.name}",
                "donor_weapon", donor.source,
            ))
        donor_ammo: AmmoRecord | None = None
        donor_animation_records: tuple[WeaponAnimationRecord, ...] = ()
        donor_shop_records: tuple[WeaponShopRecord, ...] = ()
        donor_links: tuple[WeaponComponentLink, ...] = ()
        reused_components: tuple[str, ...] = ()
        donor_weapon_tree: etree._ElementTree | None = None
        donor_weapon_item: etree._Element | None = None

        if donor is not None:
            selected_sources["weapon"] = donor.source
            required = {
                "Name": donor.name,
                "Slot": donor.slot,
                "AmmoInfo": donor.ammo_info,
                "Model": donor.model,
                "HumanNameHash": donor.human_name_hash,
                "StatName": donor.stat_name,
            }
            missing = [name for name, value in required.items() if not value]
            try:
                donor_weapon_tree = self._core.read_tree(donor.source)
                donor_weapon_item = self._record_item(
                    donor_weapon_tree, donor.name, "weapon",
                )
                missing.extend(
                    name for name in required
                    if _direct_child(donor_weapon_item, name) is None
                )
            except (OSError, ValueError) as exc:
                findings.append(WeaponCloneFinding(
                    "error", "donor_weapon_xml_invalid", str(exc),
                    "donor_weapon", donor.source,
                ))
            if missing:
                findings.append(WeaponCloneFinding(
                    "error", "donor_weapon_incomplete",
                    "Donor weapon is missing required fields: "
                    + ", ".join(sorted(set(missing))),
                    "donor_weapon", donor.source,
                ))
            else:
                completeness["weapon_record"] = True

            ammo_matches = [
                item for item in scan.ammo
                if item.name.casefold() == donor.ammo_info.casefold()
            ]
            if len(ammo_matches) != 1:
                findings.append(WeaponCloneFinding(
                    "error", "donor_ammo_not_unique",
                    "Donor linked ammo must resolve exactly once: "
                    f"{donor.ammo_info}",
                    "ammo_info",
                ))
            else:
                donor_ammo = ammo_matches[0]
                selected_sources["ammo"] = donor_ammo.source
                if self._has_duplicate_record_finding(scan, donor_ammo.name):
                    findings.append(WeaponCloneFinding(
                        "error", "donor_ammo_duplicated",
                        "Donor linked ammo has a duplicate package record: "
                        + donor_ammo.name,
                        "ammo_info", donor_ammo.source,
                    ))
                try:
                    ammo_tree = self._core.read_tree(donor_ammo.source)
                    ammo_item = self._record_item(
                        ammo_tree, donor_ammo.name, "ammo",
                    )
                    required_ammo = {
                        "Name": donor_ammo.name,
                        "Model": donor_ammo.model,
                        "AmmoMax": donor_ammo.ammo_max,
                        "Explosion": donor_ammo.explosion,
                        "TrailFx": donor_ammo.trail_fx,
                        "PrimedFx": donor_ammo.primed_fx,
                    }
                    missing_ammo = [
                        name for name in required_ammo
                        if _direct_child(ammo_item, name) is None
                    ]
                    missing_ammo.extend(
                        name for name in ("Name", "Model", "AmmoMax", "Explosion")
                        if not required_ammo[name]
                    )
                    if missing_ammo:
                        raise ValueError(
                            "Donor ammo record is missing required fields: "
                            + ", ".join(missing_ammo)
                        )
                    completeness["ammo_record"] = True
                except (OSError, ValueError) as exc:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_ammo_xml_invalid", str(exc),
                        "ammo_info", donor_ammo.source,
                    ))

            donor_animation_records = tuple(
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == donor.name.casefold()
            )
            animation_sources = tuple(dict.fromkeys(
                item.source for item in donor_animation_records
            ))
            if not donor_animation_records:
                findings.append(WeaponCloneFinding(
                    "error", "donor_animation_missing",
                    "Donor weapon has no animation mappings.", "animations",
                ))
            elif len(animation_sources) != 1:
                findings.append(WeaponCloneFinding(
                    "error", "donor_animation_source_ambiguous",
                    "Donor animations must exist in one unambiguous source.",
                    "animations",
                ))
            else:
                animation_source = animation_sources[0]
                selected_sources["animation"] = animation_source
                set_counts: dict[int, int] = {}
                for record in donor_animation_records:
                    set_counts[record.set_ordinal] = (
                        set_counts.get(record.set_ordinal, 0) + 1
                    )
                duplicates = [
                    ordinal for ordinal, count in set_counts.items() if count != 1
                ]
                try:
                    animation_tree = self._core.read_tree(animation_source)
                    for record in donor_animation_records:
                        self._animation_item(animation_tree, record)
                    if duplicates:
                        raise ValueError(
                            "Donor animation mapping is duplicated within set(s): "
                            + ", ".join(map(str, sorted(duplicates)))
                        )
                    completeness["animation_mappings"] = len(
                        donor_animation_records
                    )
                    completeness["animation_sets"] = [
                        item.set_name or str(item.set_ordinal)
                        for item in sorted(
                            donor_animation_records,
                            key=lambda item: item.set_ordinal,
                        )
                    ]
                except (OSError, ValueError) as exc:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_animation_xml_invalid", str(exc),
                        "animations", animation_source,
                    ))

            donor_shop_records = tuple(
                item for item in scan.weapon_shop_records
                if item.weapon_name.casefold() == donor.name.casefold()
            )
            if len(donor_shop_records) != 1:
                findings.append(WeaponCloneFinding(
                    "error", "donor_shop_not_unique",
                    "Donor weapon must have exactly one source-aware shop record.",
                    "shop",
                ))
            else:
                shop_source = donor_shop_records[0].source
                selected_sources["shop"] = shop_source
                try:
                    shop_tree = self._core.read_tree(shop_source)
                    self._shop_item(shop_tree, donor.name, donor_shop_records)
                    completeness["shop_record"] = True
                except (OSError, ValueError) as exc:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_shop_xml_invalid", str(exc),
                        "shop", shop_source,
                    ))

            donor_links = tuple(
                item for item in scan.weapon_component_links
                if item.weapon_name.casefold() == donor.name.casefold()
            )
            if donor_weapon_item is not None:
                raw_offers, malformed_offers = self._attachment_offer_inventory(
                    donor_weapon_item,
                )
                for message in malformed_offers:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_component_offer_malformed", message,
                        "components", donor.source,
                    ))
                resolved_offers = sorted(
                    (
                        item.attach_bone.casefold(),
                        item.component_name.casefold(),
                    )
                    for item in donor_links
                )
                if sorted(raw_offers) != resolved_offers:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_component_offer_unresolved",
                        "Donor XML component offers do not match the complete "
                        "resolved attachment inventory.",
                        "components", donor.source,
                    ))
            if donor_weapon_tree is not None:
                for link in donor_links:
                    try:
                        self._attachment_item(donor_weapon_tree, link)
                    except ValueError as exc:
                        findings.append(WeaponCloneFinding(
                            "error", "donor_attachment_xml_ambiguous", str(exc),
                            "components", link.source,
                        ))
            component_names = tuple(dict.fromkeys(
                item.component_name for item in donor_links
            ))
            valid_components: list[str] = []
            for component_name in component_names:
                definitions = [
                    item for item in scan.weapon_components
                    if item.name.casefold() == component_name.casefold()
                ]
                if len(definitions) != 1:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_component_not_package_owned",
                        "Every donor attachment must resolve one package-owned "
                        f"component definition: {component_name}",
                        "components",
                    ))
                    continue
                definition = definitions[0]
                if self._has_duplicate_record_finding(scan, definition.name):
                    findings.append(WeaponCloneFinding(
                        "error", "donor_component_duplicated",
                        "Donor component has a duplicate package record: "
                        + definition.name,
                        "components", definition.source,
                    ))
                    continue
                try:
                    component_tree = self._core.read_tree(definition.source)
                    self._record_item(component_tree, definition.name, "component")
                    valid_components.append(definition.name)
                    selected_sources[
                        f"component:{definition.name}"
                    ] = definition.source
                except (OSError, ValueError) as exc:
                    findings.append(WeaponCloneFinding(
                        "error", "donor_component_xml_invalid", str(exc),
                        "components", definition.source,
                    ))
            reused_components = tuple(valid_components)
            completeness["attachment_links"] = len(donor_links)
            completeness["component_definitions"] = len(valid_components)

        completeness["authorable_source"] = self._source_is_authorable(scan)
        if not completeness["authorable_source"]:
            findings.append(WeaponCloneFinding(
                "error", "opaque_authoring_source",
                "Bundle cloning requires a loose or extracted dlc.rpf.source tree; "
                "a prebuilt RPF cannot receive verifiable XML edits.",
            ))

        self._collect_clone_collisions(scan, spec, collisions)
        model_assets = self._model_asset_paths(scan, spec.model)
        if len(model_assets) != 1:
            findings.append(WeaponCloneFinding(
                "error", "target_model_asset_not_unique",
                "Target weapon Model requires one exact package model asset; "
                + ("found none" if not model_assets else "found " + ", ".join(model_assets)),
                "model",
            ))
        else:
            selected_sources["model_asset"] = model_assets[0]

        if not spec.clone_ammo:
            reused_ammo = [
                item for item in scan.ammo
                if item.name.casefold() == spec.ammo_info.casefold()
            ]
            if len(reused_ammo) != 1:
                findings.append(WeaponCloneFinding(
                    "error", "target_reused_ammo_not_unique",
                    "Reuse mode requires AmmoInfo to resolve exactly one existing "
                    f"ammo record: {spec.ammo_info}",
                    "ammo_info",
                ))

        if donor is not None and completeness["weapon_record"]:
            additions.append(WeaponCloneAddition(
                "weapon", spec.weapon_name, donor.source,
                f"clone of {donor.name}",
            ))
            for link in donor_links:
                additions.append(WeaponCloneAddition(
                    "attachment_link",
                    f"{spec.weapon_name}/{link.component_name}/{link.attach_bone}",
                    donor.source,
                    "reuses package component definition",
                ))
        if donor_ammo is not None and spec.clone_ammo:
            additions.append(WeaponCloneAddition(
                "ammo", str(spec.ammo_name), donor_ammo.source,
                f"clone of {donor_ammo.name}",
            ))
        for record in sorted(
            donor_animation_records,
            key=lambda item: (item.source.casefold(), item.set_ordinal),
        ):
            additions.append(WeaponCloneAddition(
                "animation_mapping", spec.weapon_name, record.source,
                record.set_name or str(record.set_ordinal),
            ))
        if len(donor_shop_records) == 1:
            additions.append(WeaponCloneAddition(
                "shop", spec.weapon_name, donor_shop_records[0].source,
                f"clone of {spec.donor_weapon}",
            ))

        for source in sorted(set(selected_sources.values()), key=str.casefold):
            try:
                source_sha256[source] = _file_sha256(self._core.member(source))
            except (OSError, ValueError) as exc:
                findings.append(WeaponCloneFinding(
                    "error", "selected_source_unreadable", str(exc), path=source,
                ))

        donor_complete = bool(
            donor is not None
            and completeness["weapon_record"]
            and completeness["ammo_record"]
            and completeness["animation_mappings"]
            and completeness["shop_record"]
            and completeness["authorable_source"]
            and completeness["component_definitions"] == len(reused_components)
            and not any(
                item.severity == "error"
                and item.code.startswith("donor_")
                for item in findings
            )
        )
        return WeaponClonePlan(
            workspace=self.root,
            revision=self.revision,
            inventory_fingerprint=project.inventory_fingerprint,
            spec=spec,
            donor_complete=donor_complete,
            donor_completeness=completeness,
            selected_sources=dict(sorted(selected_sources.items())),
            source_sha256=dict(sorted(source_sha256.items())),
            reused_components=reused_components,
            additions=tuple(additions),
            collisions=tuple(collisions),
            findings=tuple(findings),
        )

    def clone_weapon_bundle(
        self,
        plan: WeaponClonePlan | dict[str, Any],
        *,
        expected_revision: int,
        expected_plan_sha256: str,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            normalized_sha = str(expected_plan_sha256).strip().casefold()
            if not _SHA256.fullmatch(normalized_sha):
                raise ValueError(
                    "Expected weapon-clone plan SHA-256 must be 64 lowercase hex digits"
                )
            spec, supplied_sha = self._clone_plan_input(plan)
            if supplied_sha != normalized_sha:
                raise ValueError(
                    "Weapon-clone plan SHA-256 does not match the reviewed plan"
                )
            reviewed = self._plan_weapon_clone_locked(spec)
            if reviewed.revision != expected_revision:
                raise ValueError(
                    "Weapon-clone plan revision changed during validation"
                )
            if reviewed.plan_sha256 != normalized_sha:
                raise ValueError(
                    "Weapon-clone plan is stale; package evidence or requested "
                    "identities changed"
                )
            if not reviewed.ready:
                blockers = [
                    item.code for item in reviewed.findings
                    if item.severity == "error"
                ] + [
                    f"collision:{item.field}" for item in reviewed.collisions
                ]
                raise ValueError(
                    "Weapon-clone plan is not ready: "
                    + ", ".join(blockers or ["donor_incomplete"])
                )
            return self._clone_weapon_bundle_locked(reviewed)

    def _clone_weapon_bundle_locked(
        self, plan: WeaponClonePlan,
    ) -> WeaponAuthoringResult:
        scan, before_project = self._scan_project()
        spec = plan.spec
        donor = self._unique_weapon(scan, spec.donor_weapon)
        donor_ammo = next(
            item for item in scan.ammo
            if item.name.casefold() == donor.ammo_info.casefold()
        )
        animation_records = tuple(sorted(
            (
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == donor.name.casefold()
            ),
            key=lambda item: item.set_ordinal,
        ))
        shop_records = tuple(
            item for item in scan.weapon_shop_records
            if item.weapon_name.casefold() == donor.name.casefold()
        )
        metadata_sources = tuple(dict.fromkeys((
            donor.source,
            donor_ammo.source,
            *(item.source for item in animation_records),
            *(item.source for item in shop_records),
        )))
        trees = {
            source: self._core.read_tree(source) for source in metadata_sources
        }
        originals = {
            source: self._canonical_element(tree.getroot())
            for source, tree in trees.items()
        }
        changes: list[dict[str, str]] = []

        donor_weapon_item = self._record_item(
            trees[donor.source], donor.name, "weapon",
        )
        weapon_clone = deepcopy(donor_weapon_item)
        for field, node_name, value in (
            ("weapon.Name", "Name", spec.weapon_name),
            ("weapon.Slot", "Slot", spec.slot),
            ("weapon.AmmoInfo", "AmmoInfo", spec.ammo_info),
            ("weapon.Model", "Model", spec.model),
            ("weapon.HumanNameHash", "HumanNameHash", spec.human_name_hash),
            ("weapon.StatName", "StatName", spec.stat_name),
        ):
            before, after = _set_preserving_representation(
                weapon_clone, node_name, value,
            )
            changes.append({"field": field, "before": before, "after": after})
        donor_weapon_item.addnext(weapon_clone)

        if spec.clone_ammo:
            donor_ammo_item = self._record_item(
                trees[donor_ammo.source], donor_ammo.name, "ammo",
            )
            ammo_clone = deepcopy(donor_ammo_item)
            before, after = _set_preserving_representation(
                ammo_clone, "Name", str(spec.ammo_name),
            )
            changes.append({
                "field": "ammo.Name", "before": before, "after": after,
            })
            donor_ammo_item.addnext(ammo_clone)

        for record in animation_records:
            template = self._animation_item(trees[record.source], record)
            clone = deepcopy(template)
            clone.set("key", spec.weapon_name)
            template.addnext(clone)
            changes.append({
                "field": "animation.mapping",
                "before": donor.name,
                "after": spec.weapon_name,
                "source": record.source,
                "set": record.set_name or str(record.set_ordinal),
            })

        shop_item, shop_identity = self._shop_item(
            trees[shop_records[0].source], donor.name, shop_records,
        )
        shop_clone = deepcopy(shop_item)
        clone_identity = _direct_child(shop_clone, _local_name(shop_identity))
        if clone_identity is None:
            raise RuntimeError("Cloned shop record lost its identity node")
        before, after = _set_preserving_representation(
            shop_clone, _local_name(shop_identity), spec.weapon_name,
        )
        changes.append({
            "field": f"shop.{_local_name(shop_identity)}",
            "before": before,
            "after": after,
        })
        shop_item.addnext(shop_clone)

        created_records = tuple(item.to_dict() for item in plan.additions)
        changes.extend({
            "field": "bundle.created_record",
            "before": "",
            "after": json.dumps(item, sort_keys=True, separators=(",", ":")),
        } for item in created_records)
        return self._commit(
            subject=spec.weapon_name,
            subject_kind="bundle",
            affected_weapons=(spec.weapon_name,),
            trees=trees,
            changes=tuple(changes),
            before_project=before_project,
            verify=lambda after_scan: self._verify_weapon_bundle_clone(
                after_scan, plan, originals,
            ),
            operation="weapon_bundle_clone",
            manifest_created_records=created_records,
        )

    def clone_animation_mappings(
        self,
        weapon_name: str,
        template_weapon: str,
        source: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            scan, before_project = self._scan_project()
            weapon = self._unique_weapon(scan, weapon_name)
            template = self._animation_identity(scan, template_weapon)
            if weapon.name.casefold() == template.casefold():
                raise ValueError("Animation target and template weapons must differ")
            existing = [
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == weapon.name.casefold()
            ]
            if existing:
                raise ValueError(
                    f"Animation target already has mappings: {weapon.name}"
                )
            template_records = tuple(
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == template.casefold()
            )
            selected_source = self._select_relationship_source(
                template_records, source, "animation template",
            )
            selected = tuple(sorted(
                (
                    item for item in template_records
                    if item.source == selected_source
                ),
                key=lambda item: (item.set_ordinal, item.ordinal),
            ))
            if not selected:
                raise ValueError(
                    f"Animation template has no mappings in {selected_source}"
                )
            counts: dict[int, int] = {}
            for record in selected:
                counts[record.set_ordinal] = counts.get(record.set_ordinal, 0) + 1
            duplicated = sorted(index for index, count in counts.items() if count != 1)
            if duplicated:
                raise ValueError(
                    "Animation template must appear exactly once per animation set; "
                    "duplicate set ordinals: " + ", ".join(map(str, duplicated))
                )

            tree = self._core.read_tree(selected_source)
            original_document = self._canonical_element(tree.getroot())
            expected_templates: dict[int, bytes] = {}
            changes: list[dict[str, str]] = []
            for record in selected:
                template_item = self._animation_item(tree, record)
                expected_templates[record.set_ordinal] = \
                    self._canonical_animation_item(template_item, template)
                clone = deepcopy(template_item)
                clone.set("key", weapon.name)
                template_item.addnext(clone)
                changes.append({
                    "field": "animation.mapping",
                    "before": template,
                    "after": weapon.name,
                    "source": selected_source,
                    "set": record.set_name or str(record.set_ordinal),
                })
            expected_coverage = tuple(
                (item.set_ordinal, item.set_name) for item in selected
            )
            return self._commit(
                subject=weapon.name,
                subject_kind="animation",
                affected_weapons=(weapon.name,),
                trees={selected_source: tree},
                changes=tuple(changes),
                before_project=before_project,
                verify=lambda after_scan: self._verify_animation_clone(
                    after_scan,
                    weapon.name,
                    template,
                    selected_source,
                    expected_coverage,
                    expected_templates,
                    original_document,
                ),
                operation="weapon_animation_clone",
            )

    def update_shop(
        self,
        weapon_name: str,
        updates: dict[str, Any],
        source: str | None = None,
        *,
        expected_revision: int | None = None,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            unknown = sorted(set(updates) - set(EDITABLE_SHOP_FIELDS))
            if unknown:
                raise ValueError(
                    "Unsupported weapon-shop fields: " + ", ".join(unknown)
                )
            scan, before_project = self._scan_project()
            current = self.shop_values(weapon_name, source, _scan=scan)
            normalized = {
                key: self._validate_shop_value(key, value)
                for key, value in updates.items()
            }
            changed = {
                key: value for key, value in normalized.items()
                if value != current.values.get(key, "")
            }
            if not changed:
                raise ValueError("Weapon-shop update contains no changed values")
            tree = self._core.read_tree(current.source)
            records = tuple(
                item for item in scan.weapon_shop_records
                if item.weapon_name.casefold() == current.weapon.casefold()
                and item.source == current.source
            )
            item, _identity = self._shop_item(tree, current.weapon, records)
            changes: list[dict[str, str]] = []
            for key, value in changed.items():
                before, after = _set_preserving_representation(
                    item, SHOP_FIELDS[key], value,
                )
                changes.append({"field": key, "before": before, "after": after})
            return self._commit(
                subject=current.weapon,
                subject_kind="shop",
                affected_weapons=(current.weapon,),
                trees={current.source: tree},
                changes=tuple(changes),
                before_project=before_project,
                verify=lambda after_scan: self._verify_shop_values(
                    after_scan, current.weapon, current.source, changed,
                ),
                operation="weapon_shop_edit",
            )

    def update(
        self,
        weapon_name: str,
        updates: dict[str, str],
        *,
        expected_revision: int | None = None,
        acknowledge_shared: bool = False,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            return self._update_locked(
                weapon_name, updates, acknowledge_shared=acknowledge_shared,
            )

    def _update_locked(
        self,
        weapon_name: str,
        updates: dict[str, str],
        *,
        acknowledge_shared: bool,
    ) -> WeaponAuthoringResult:
        unknown = sorted(set(updates) - set(EDITABLE_FIELDS))
        if unknown:
            raise ValueError("Unsupported weapon authoring fields: " + ", ".join(unknown))
        scan, before_project = self._scan_project()
        current = self.values(weapon_name, _scan=scan)
        normalized = {
            key: self._validate_value(key, str(value).strip())
            for key, value in updates.items()
        }
        changed = {
            key: value for key, value in normalized.items()
            if value != current.values.get(key, "")
        }
        if not changed:
            raise ValueError("Weapon authoring update contains no changed values")
        if "weapon.model" in changed:
            self._require_model_asset(scan, changed["weapon.model"], "weapon.model")
        if "ammo.model" in changed:
            self._require_model_asset(scan, changed["ammo.model"], "ammo.model")
        ammo_changed = any(key.startswith("ammo.") for key in changed)
        if ammo_changed and "ammo" not in current.sources:
            raise ValueError("Linked ammo record was not found uniquely")
        if (
            ammo_changed
            and len(current.affected_weapons) > 1
            and not acknowledge_shared
        ):
            raise ValueError(
                "Ammo record is shared by multiple weapons; pass acknowledge_shared=True "
                "after reviewing the affected weapons"
            )
        if (
            "weapon.ammoInfo" in changed
            and any(key.startswith("ammo.") for key in changed)
        ):
            raise ValueError(
                "Change AmmoInfo and linked ammo fields in separate reviewed revisions"
            )
        if ammo_changed:
            maximum_text = changed.get(
                "ammo.ammoMax", current.values.get("ammo.ammoMax", ""),
            )
            maximum_50_text = changed.get(
                "ammo.ammoMax50", current.values.get("ammo.ammoMax50", ""),
            )
            # Some valid packages omit the optional 50-percent cap entirely.
            # Compare the caps only when both existing schema nodes are populated;
            # individual edited values were already bounded above.
            if maximum_text and maximum_50_text:
                try:
                    effective_max = int(maximum_text)
                    effective_max_50 = int(maximum_50_text)
                except ValueError as exc:
                    raise ValueError(
                        "Linked ammo record has invalid AmmoMax or AmmoMax50 values"
                    ) from exc
                if effective_max_50 > effective_max:
                    raise ValueError("ammo.ammoMax50 cannot exceed ammo.ammoMax")
        edited_sources = [current.sources["weapon"]]
        if ammo_changed:
            edited_sources.append(current.sources["ammo"])
        trees = {
            relative: self._core.read_tree(relative)
            for relative in tuple(dict.fromkeys(edited_sources))
        }
        weapon_item = self._record_item(
            trees[current.sources["weapon"]], weapon_name, "weapon",
        )
        ammo_item = (
            self._record_item(
                trees[current.sources["ammo"]], current.values["weapon.ammoInfo"], "ammo",
            )
            if ammo_changed else None
        )
        changes: list[dict[str, str]] = []
        for key, value in changed.items():
            if key in WEAPON_FIELDS:
                before, after = _set_preserving_representation(
                    weapon_item,
                    WEAPON_FIELDS[key],
                    value,
                )
            else:
                assert ammo_item is not None
                before, after = _set_preserving_representation(
                    ammo_item,
                    AMMO_FIELDS[key],
                    value,
                )
            changes.append({"field": key, "before": before, "after": after})
        affected = current.affected_weapons if ammo_changed else (current.weapon,)
        return self._commit(
            subject=current.weapon,
            subject_kind="weapon",
            affected_weapons=affected,
            trees=trees,
            changes=tuple(changes),
            before_project=before_project,
            verify=lambda after_scan: self._verify_weapon_values(
                after_scan, current.weapon, changed,
            ),
            operation="weapon_metadata_edit",
        )

    def update_component(
        self,
        component_name: str,
        updates: dict[str, str],
        *,
        expected_revision: int | None = None,
        acknowledge_shared: bool = False,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            return self._update_component_locked(
                component_name, updates, acknowledge_shared=acknowledge_shared,
            )

    def _update_component_locked(
        self,
        component_name: str,
        updates: dict[str, str],
        *,
        acknowledge_shared: bool,
    ) -> WeaponAuthoringResult:
        unknown = sorted(set(updates) - set(EDITABLE_COMPONENT_FIELDS))
        if unknown:
            raise ValueError(
                "Unsupported weapon-component fields: " + ", ".join(unknown)
            )
        scan, before_project = self._scan_project()
        current = self.component_values(component_name, _scan=scan)
        normalized = {
            key: self._validate_value(key, str(value).strip())
            for key, value in updates.items()
        }
        changed = {
            key: value for key, value in normalized.items()
            if value != current.values.get(key, "")
        }
        if not changed:
            raise ValueError("Weapon-component update contains no changed values")
        if "component.model" in changed:
            self._require_model_asset(
                scan, changed["component.model"], "component.model",
            )
        if len(current.affected_weapons) > 1 and not acknowledge_shared:
            raise ValueError(
                "Component definition is shared by multiple weapons; pass "
                "acknowledge_shared=True after reviewing the affected weapons"
            )
        tree = self._core.read_tree(current.source)
        item = self._record_item(tree, current.component, "component")
        changes: list[dict[str, str]] = []
        for key, value in changed.items():
            before, after = _set_preserving_representation(
                item, COMPONENT_FIELDS[key], value,
            )
            changes.append({"field": key, "before": before, "after": after})
        return self._commit(
            subject=current.component,
            subject_kind="component",
            affected_weapons=current.affected_weapons,
            trees={current.source: tree},
            changes=tuple(changes),
            before_project=before_project,
            verify=lambda after_scan: self._verify_component_values(
                after_scan, current.component, changed,
            ),
            operation="weapon_component_edit",
        )

    def update_attachment(
        self,
        weapon_name: str,
        component_name: str,
        updates: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            return self._update_attachment_locked(
                weapon_name, component_name, updates,
            )

    def _update_attachment_locked(
        self,
        weapon_name: str,
        component_name: str,
        updates: dict[str, Any],
    ) -> WeaponAuthoringResult:
        unknown = sorted(set(updates) - set(ATTACHMENT_FIELDS))
        if unknown:
            raise ValueError("Unsupported attachment fields: " + ", ".join(unknown))
        scan, before_project = self._scan_project()
        links = [
            item for item in scan.weapon_component_links
            if item.weapon_name.casefold() == weapon_name.casefold()
            and item.component_name.casefold() == component_name.casefold()
        ]
        if len(links) != 1:
            raise ValueError(
                "Weapon attachment link was not found uniquely: "
                f"{weapon_name} / {component_name}"
            )
        link = links[0]
        normalized: dict[str, str] = {}
        if "attachment.default" in updates:
            normalized["attachment.default"] = self._validate_boolean(
                updates["attachment.default"], "attachment.default",
            )
        current = {
            "attachment.attachBone": link.attach_bone,
            "attachment.default": "true" if link.default else "false",
        }
        changed = {
            key: value for key, value in normalized.items() if value != current[key]
        }
        if not changed:
            raise ValueError("Weapon attachment update contains no changed values")
        tree = self._core.read_tree(link.source)
        _attach_item, component_item, siblings = self._attachment_item(
            tree, link,
        )
        if changed.get("attachment.default") == "true":
            other_defaults = [
                item for item in siblings if item is not component_item
                and _element_value(_direct_child(item, "Default")).casefold()
                in {"1", "true", "yes"}
            ]
            if other_defaults:
                raise ValueError(
                    "Another component on this attachment point is already the default"
                )
        changes: list[dict[str, str]] = []
        if "attachment.default" in changed:
            before, after = _set_preserving_representation(
                component_item, "Default", changed["attachment.default"],
            )
            changes.append({
                "field": "attachment.default", "before": before, "after": after,
            })
        return self._commit(
            subject=f"{link.weapon_name}/{link.component_name}",
            subject_kind="attachment",
            affected_weapons=(link.weapon_name,),
            trees={link.source: tree},
            changes=tuple(changes),
            before_project=before_project,
            verify=lambda after_scan: self._verify_attachment(
                after_scan, link.weapon_name, link.component_name, changed,
            ),
            operation="weapon_attachment_edit",
        )

    def undo(
        self, *, expected_revision: int | None = None,
    ) -> WeaponAuthoringResult:
        with self._core.operation_lock():
            self._core.refresh_manifest()
            self._check_revision(expected_revision)
            return self._undo_locked()

    def _undo_locked(self) -> WeaponAuthoringResult:
        history = self._core.latest_history()
        self._core.verify_post_edit_state(history)
        record = self._core.history_record(history)
        subject = str(record.get("subject", ""))
        operation = str(record.get("operation", ""))
        subject_kind = {
            "weapon_metadata_edit": "weapon",
            "weapon_component_edit": "component",
            "weapon_attachment_edit": "attachment",
            "weapon_animation_clone": "animation",
            "weapon_shop_edit": "shop",
            "weapon_bundle_clone": "bundle",
        }.get(operation, "weapon")
        changes = tuple(record.get("changes", ()))
        affected = self._affected_from_changes(subject_kind, subject, changes)
        recovery = self._core.snapshot_current_for_undo(history)
        previous_manifest = dict(self.manifest)
        undone = history.with_name(f"{history.name}.undone")
        try:
            self._core.restore(history)
            project = self.inspect()
            revision = self.revision + 1
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["weapons"] = [item.name for item in project.weapons]
            if operation == "weapon_bundle_clone":
                remove: list[dict[str, Any]] = []
                for change in changes:
                    if not isinstance(change, dict) or change.get("field") \
                            != "bundle.created_record":
                        continue
                    try:
                        value = json.loads(str(change.get("after", "")))
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            "Weapon bundle history has an invalid created record"
                        ) from exc
                    if not isinstance(value, dict):
                        raise ValueError(
                            "Weapon bundle history has an invalid created record"
                        )
                    remove.append(value)
                inventory = self.manifest.get("created_records", [])
                if not isinstance(inventory, list):
                    raise ValueError(
                        "Weapon authoring created-record inventory is invalid"
                    )
                self.manifest["created_records"] = [
                    item for item in inventory if item not in remove
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
        return WeaponAuthoringResult(
            workspace=self.root,
            revision=revision,
            subject=subject,
            subject_kind=subject_kind,
            changes=changes,
            history=undone,
            affected_weapons=affected,
            project=project,
        )

    def _commit(
        self,
        *,
        subject: str,
        subject_kind: str,
        affected_weapons: tuple[str, ...],
        trees: dict[str, etree._ElementTree],
        changes: tuple[dict[str, str], ...],
        before_project: WeaponAuthoringProject,
        verify: Any,
        operation: str,
        manifest_created_records: tuple[dict[str, str], ...] = (),
    ) -> WeaponAuthoringResult:
        history = self._core.snapshot(
            subject, tuple(trees), changes, operation=operation,
        )
        previous_manifest = dict(self.manifest)
        try:
            self._core.commit_trees(trees)
            after_scan, after_project = self._scan_project()
            self._reject_relationship_regressions(before_project, after_project)
            verify(after_scan)
            self._core.record_post_edit_state(history)
            revision = self.revision + 1
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["weapons"] = [item.name for item in after_project.weapons]
            if manifest_created_records:
                existing = self.manifest.get("created_records", [])
                if not isinstance(existing, list) or not all(
                    isinstance(item, dict) for item in existing
                ):
                    raise ValueError("Weapon authoring created-record inventory is invalid")
                self.manifest["created_records"] = [
                    *existing,
                    *(dict(item) for item in manifest_created_records),
                ]
            (history / "validation.json").write_text(
                json.dumps(after_project.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            self._core.write_manifest()
        except Exception:
            self.manifest.clear()
            self.manifest.update(previous_manifest)
            self._core.restore(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return WeaponAuthoringResult(
            workspace=self.root,
            revision=revision,
            subject=subject,
            subject_kind=subject_kind,
            changes=changes,
            history=history,
            affected_weapons=affected_weapons,
            project=after_project,
        )

    def _check_revision(self, expected: int | None) -> None:
        if expected is None:
            return
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("Expected weapon-authoring revision must be a non-negative integer")
        if expected != self.revision:
            raise ValueError(
                f"Weapon-authoring revision conflict: expected {expected}, "
                f"current revision is {self.revision}"
            )

    @staticmethod
    def _unique_weapon(scan: PackageScan, name: str) -> WeaponRecord:
        matches = [item for item in scan.weapons if item.name.casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Weapon was not found uniquely in workspace: {name}")
        return matches[0]

    @classmethod
    def _animation_identity(cls, scan: PackageScan, name: str) -> str:
        definitions = [
            item.name for item in scan.weapons
            if item.name.casefold() == name.casefold()
        ]
        if len(definitions) > 1:
            raise ValueError(f"Weapon was not found uniquely in workspace: {name}")
        records = [
            item.weapon_name for item in scan.weapon_animation_records
            if item.weapon_name.casefold() == name.casefold()
        ]
        canonical = tuple(dict.fromkeys((*definitions, *records)))
        if not canonical:
            cls._validate_identifier(str(name).strip(), "animation.weapon")
            raise ValueError(f"Weapon animation mapping was not found: {name}")
        if len({item.casefold() for item in canonical}) != 1:
            raise ValueError(f"Weapon animation identity is ambiguous: {name}")
        return canonical[0]

    @staticmethod
    def _unique_component(scan: PackageScan, name: str) -> WeaponComponentRecord:
        matches = [
            item for item in scan.weapon_components
            if item.name.casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Weapon component was not found uniquely: {name}")
        return matches[0]

    @classmethod
    def _normalize_clone_spec(
        cls,
        *,
        donor_weapon: str,
        weapon_name: str,
        slot: str,
        ammo_info: str,
        model: str,
        human_name_hash: str,
        stat_name: str,
        clone_ammo: bool,
        ammo_name: str | None,
    ) -> WeaponCloneSpec:
        values = {
            "donor_weapon": cls._validate_identifier(
                str(donor_weapon).strip(), "donor_weapon",
            ),
            "weapon_name": cls._validate_identifier(
                str(weapon_name).strip(), "weapon_name",
            ),
            "slot": cls._validate_identifier(str(slot).strip(), "slot"),
            "ammo_info": cls._validate_identifier(
                str(ammo_info).strip(), "ammo_info",
            ),
            "model": cls._validate_identifier(str(model).strip(), "model"),
            "human_name_hash": cls._validate_identifier(
                str(human_name_hash).strip(), "human_name_hash",
            ),
            "stat_name": cls._validate_identifier(
                str(stat_name).strip(), "stat_name",
            ),
        }
        prefixes = {
            "donor_weapon": "WEAPON_",
            "weapon_name": "WEAPON_",
            "slot": "SLOT_",
            "ammo_info": "AMMO_",
        }
        for field, prefix in prefixes.items():
            if not values[field].upper().startswith(prefix):
                raise ValueError(f"{field} must begin with {prefix}")
        if not isinstance(clone_ammo, bool):
            raise ValueError("clone_ammo must be true or false")
        normalized_ammo: str | None = None
        if clone_ammo:
            if ammo_name is None:
                raise ValueError("ammo_name is required when clone_ammo is true")
            normalized_ammo = cls._validate_identifier(
                str(ammo_name).strip(), "ammo_name",
            )
            if not normalized_ammo.upper().startswith("AMMO_"):
                raise ValueError("ammo_name must begin with AMMO_")
            if normalized_ammo != values["ammo_info"]:
                raise ValueError(
                    "ammo_info and ammo_name must be identical in clone-ammo mode"
                )
        elif ammo_name is not None:
            raise ValueError("ammo_name is only valid when clone_ammo is true")
        return WeaponCloneSpec(
            donor_weapon=values["donor_weapon"],
            weapon_name=values["weapon_name"],
            slot=values["slot"],
            ammo_info=values["ammo_info"],
            model=values["model"],
            human_name_hash=values["human_name_hash"],
            stat_name=values["stat_name"],
            clone_ammo=clone_ammo,
            ammo_name=normalized_ammo,
        )

    @classmethod
    def _clone_plan_input(
        cls, plan: WeaponClonePlan | dict[str, Any],
    ) -> tuple[WeaponCloneSpec, str]:
        if isinstance(plan, WeaponClonePlan):
            return plan.spec, plan.plan_sha256
        if not isinstance(plan, dict):
            raise ValueError("Weapon clone requires a reviewed plan object")
        raw_spec = plan.get("spec")
        raw_sha = plan.get("plan_sha256")
        if not isinstance(raw_spec, dict) or not isinstance(raw_sha, str):
            raise ValueError("Serialized weapon-clone plan is missing spec or SHA-256")
        required = {
            "donor_weapon", "weapon_name", "slot", "ammo_info", "model",
            "human_name_hash", "stat_name", "clone_ammo", "ammo_name",
        }
        if set(raw_spec) != required:
            raise ValueError("Serialized weapon-clone plan has an invalid spec contract")
        spec = cls._normalize_clone_spec(
            donor_weapon=raw_spec["donor_weapon"],
            weapon_name=raw_spec["weapon_name"],
            slot=raw_spec["slot"],
            ammo_info=raw_spec["ammo_info"],
            model=raw_spec["model"],
            human_name_hash=raw_spec["human_name_hash"],
            stat_name=raw_spec["stat_name"],
            clone_ammo=raw_spec["clone_ammo"],
            ammo_name=raw_spec["ammo_name"],
        )
        return spec, raw_sha.strip().casefold()

    @staticmethod
    def _source_is_authorable(scan: PackageScan) -> bool:
        if scan.source.name.casefold() == "dlc.rpf.source":
            return True
        extracted = [
            path for path in scan.source.rglob("dlc.rpf.source")
            if path.is_dir() and not path.is_symlink()
        ] if scan.source.is_dir() else []
        if extracted:
            return len(extracted) == 1
        return not any(item.suffix == ".rpf" for item in scan.entries)

    @staticmethod
    def _has_duplicate_record_finding(scan: PackageScan, identity: str) -> bool:
        normalized = identity.casefold()
        return any(
            item.code == "duplicate_record"
            and item.message.partition(":")[2].strip().casefold() == normalized
            for item in scan.findings
        )

    @staticmethod
    def _model_asset_paths(scan: PackageScan, model: str) -> list[str]:
        return sorted(
            (
                item.path for item in scan.entries
                if item.suffix in {".ydr", ".ydd", ".yft"}
                and PurePosixPath(item.path).stem.casefold() == model.casefold()
            ),
            key=str.casefold,
        )

    @classmethod
    def _collect_clone_collisions(
        cls,
        scan: PackageScan,
        spec: WeaponCloneSpec,
        collisions: list[WeaponCloneCollision],
    ) -> None:
        domains: tuple[tuple[str, str, tuple[str, ...]], ...] = (
            (
                "weapon_name", spec.weapon_name,
                tuple(dict.fromkeys((
                    *(item.name for item in scan.weapons),
                    *scan.animation_weapons,
                    *scan.shop_weapons,
                ))),
            ),
            ("slot", spec.slot, tuple(item.slot for item in scan.weapons if item.slot)),
            (
                "model", spec.model,
                tuple(dict.fromkeys(
                    item.model for item in scan.weapons if item.model
                )),
            ),
            (
                "human_name_hash", spec.human_name_hash,
                tuple(item.human_name_hash for item in scan.weapons if item.human_name_hash),
            ),
            (
                "stat_name", spec.stat_name,
                tuple(item.stat_name for item in scan.weapons if item.stat_name),
            ),
        )
        if spec.clone_ammo:
            domains += ((
                "ammo_name", str(spec.ammo_name),
                tuple(item.name for item in scan.ammo),
            ),)
        for field, value, existing_values in domains:
            target_hash = joaat(value)
            seen: set[tuple[str, str]] = set()
            for existing in existing_values:
                if not existing:
                    continue
                if existing.casefold() == value.casefold():
                    reason = "casefold"
                elif joaat(existing) == target_hash:
                    reason = "joaat"
                else:
                    continue
                key = (existing.casefold(), reason)
                if key in seen:
                    continue
                seen.add(key)
                collisions.append(WeaponCloneCollision(
                    field=field,
                    value=value,
                    existing=existing,
                    reason=reason,
                    hash=f"0x{target_hash:08X}",
                ))

    @staticmethod
    def _select_relationship_source(
        records: tuple[Any, ...], source: str | None, label: str,
    ) -> str:
        if not records:
            raise ValueError(f"{label.title()} was not found")
        sources: dict[str, list[str]] = {}
        for record in records:
            discovered = safe_relative_path(
                str(record.source), label=f"{label} source",
            ).as_posix()
            sources.setdefault(discovered.casefold(), []).append(discovered)
        canonical_sources = tuple(
            values[0] for values in sources.values() if len(set(values)) == 1
        )
        if any(len(set(values)) != 1 for values in sources.values()):
            raise ValueError(f"{label.title()} source casing is ambiguous")
        if source is None:
            if len(canonical_sources) != 1:
                raise ValueError(
                    f"{label.title()} source is ambiguous; choose one exact "
                    "discovered source"
                )
            return canonical_sources[0]
        normalized = safe_relative_path(
            str(source).strip().replace("\\", "/"),
            label=f"{label} source",
        ).as_posix()
        match = sources.get(normalized.casefold())
        if match is None:
            raise ValueError(
                f"{label.title()} source was not found exactly: {normalized}"
            )
        return match[0]

    @staticmethod
    def _element_representation(element: etree._Element | None) -> str:
        if element is None:
            return "missing"
        if "ref" in element.attrib:
            return "ref"
        if "value" in element.attrib:
            return "value"
        return "text"

    @staticmethod
    def _animation_set_name(group: etree._Element) -> str:
        parent = group.getparent()
        while parent is not None and (
            not isinstance(parent.tag, str) or _local_name(parent) != "Item"
        ):
            parent = parent.getparent()
        if parent is None:
            return ""
        return parent.attrib.get("key", "").strip() or _element_value(
            _direct_child(parent, "Name")
        )

    @classmethod
    def _animation_item(
        cls, tree: etree._ElementTree, record: WeaponAnimationRecord,
    ) -> etree._Element:
        groups = [
            item for item in tree.getroot().iter()
            if isinstance(item.tag, str) and _local_name(item) == "WeaponAnimations"
        ]
        if record.set_ordinal < 0 or record.set_ordinal >= len(groups):
            raise ValueError(
                "Recorded animation set ordinal no longer resolves: "
                f"{record.set_ordinal}"
            )
        group = groups[record.set_ordinal]
        set_name = cls._animation_set_name(group)
        if set_name != record.set_name:
            raise ValueError(
                "Recorded animation set name no longer matches source: "
                f"{record.set_name or record.set_ordinal}"
            )
        matches = [
            item for item in group
            if isinstance(item.tag, str)
            and _local_name(item) == "Item"
            and item.attrib.get("key", "").strip().casefold()
            == record.weapon_name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(
                "Weapon animation mapping was not found exactly once in its set: "
                f"{record.weapon_name} / {record.set_name or record.set_ordinal}"
            )
        return matches[0]

    @classmethod
    def _shop_item(
        cls,
        tree: etree._ElementTree,
        weapon_name: str,
        records: tuple[WeaponShopRecord, ...],
    ) -> tuple[etree._Element, etree._Element]:
        containers = [
            item for item in tree.getroot()
            if isinstance(item.tag, str) and _local_name(item) == "weaponShopItems"
        ]
        matches: list[tuple[etree._Element, etree._Element]] = []
        for container in containers:
            for item in container:
                if not isinstance(item.tag, str) or _local_name(item) != "Item":
                    continue
                identities = [
                    child for child in item
                    if isinstance(child.tag, str)
                    and _local_name(child) in {"nameHash", "weaponName"}
                    and _element_value(child).casefold() == weapon_name.casefold()
                ]
                for identity in identities:
                    matches.append((item, identity))
        if len(matches) != 1:
            raise ValueError(
                "Direct weaponShopItems record was not found uniquely: "
                f"{weapon_name}"
            )
        item, identity = matches[0]
        actual_field = _local_name(identity)
        actual_representation = cls._element_representation(identity)
        source_records = [
            record for record in records
            if record.weapon_name.casefold() == weapon_name.casefold()
            and record.field_name == actual_field
            and record.representation == actual_representation
        ]
        if len(source_records) != 1:
            raise ValueError(
                "Weapon-shop identity does not match one source-aware record: "
                f"{weapon_name}"
            )
        return item, identity

    @staticmethod
    def _canonical_element(element: etree._Element) -> bytes:
        return etree.tostring(element, method="c14n", with_comments=True)

    @classmethod
    def _canonical_animation_item(
        cls, item: etree._Element, normalized_key: str,
    ) -> bytes:
        clone = deepcopy(item)
        clone.set("key", normalized_key)
        clone.tail = None
        return cls._canonical_element(clone)

    @staticmethod
    def _weapons_using_ammo(scan: PackageScan, ammo_name: str) -> tuple[str, ...]:
        return _casefold_unique(
            item.name for item in scan.weapons
            if item.ammo_info.casefold() == ammo_name.casefold()
        )

    @staticmethod
    def _require_model_asset(scan: PackageScan, model: str, field: str) -> None:
        matches = [
            item.path for item in scan.entries
            if item.suffix in {".ydr", ".ydd", ".yft"}
            and PurePosixPath(item.path).stem.casefold() == model.casefold()
        ]
        if len(matches) != 1:
            detail = "none" if not matches else ", ".join(matches)
            raise ValueError(
                f"{field} requires one exact package model asset; found {detail}"
            )

    @staticmethod
    def _record_item(
        tree: etree._ElementTree, identity: str, kind: str,
    ) -> etree._Element:
        hints = {
            "weapon": frozenset({"Slot", "AmmoInfo", "HumanNameHash", "StatName"}),
            "ammo": frozenset({
                "AmmoMax", "AmmoMax50", "Explosion", "TrailFx", "PrimedFx",
            }),
            "component": frozenset({"Model", "LocName", "LocDesc", "AttachBone"}),
        }[kind]
        matches: list[etree._Element] = []
        for item in tree.getroot().iter():
            if not isinstance(item.tag, str) or _local_name(item) != "Item":
                continue
            if _element_value(_direct_child(item, "Name")).casefold() != identity.casefold():
                continue
            children = {
                _local_name(child) for child in item if isinstance(child.tag, str)
            }
            if children & hints or (kind == "component" and item.get("type", "")):
                matches.append(item)
        if len(matches) != 1:
            raise ValueError(f"{kind.title()} XML record was not found uniquely: {identity}")
        return matches[0]

    @classmethod
    def _attachment_item(
        cls, tree: etree._ElementTree, link: WeaponComponentLink,
    ) -> tuple[etree._Element, etree._Element, tuple[etree._Element, ...]]:
        weapon = cls._record_item(tree, link.weapon_name, "weapon")
        matches: list[
            tuple[etree._Element, etree._Element, tuple[etree._Element, ...]]
        ] = []
        attach_points = _direct_child(weapon, "AttachPoints")
        for attach in attach_points if attach_points is not None else ():
            if not isinstance(attach.tag, str) or _local_name(attach) != "Item":
                continue
            if _element_value(_direct_child(attach, "AttachBone")).casefold() \
                    != link.attach_bone.casefold():
                continue
            components = _direct_child(attach, "Components")
            component_items = [
                item for item in (components if components is not None else ())
                if isinstance(item.tag, str) and _local_name(item) == "Item"
            ]
            for component in component_items:
                if _element_value(_direct_child(component, "Name")).casefold() \
                        == link.component_name.casefold():
                    matches.append((attach, component, tuple(component_items)))
        if len(matches) != 1:
            raise ValueError(
                "Weapon attachment XML was not found uniquely: "
                f"{link.weapon_name} / {link.component_name}"
            )
        return matches[0]

    @staticmethod
    def _attachment_offer_inventory(
        weapon_item: etree._Element,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        offers: list[tuple[str, str]] = []
        errors: list[str] = []
        attach_points = _direct_child(weapon_item, "AttachPoints")
        for attach in attach_points if attach_points is not None else ():
            if not isinstance(attach.tag, str) or _local_name(attach) != "Item":
                continue
            bone = _element_value(_direct_child(attach, "AttachBone"))
            components = _direct_child(attach, "Components")
            component_items = [
                item for item in (components if components is not None else ())
                if isinstance(item.tag, str) and _local_name(item) == "Item"
            ]
            if component_items and not bone:
                errors.append("Attachment point with component offers has no AttachBone")
            for component in component_items:
                name = _element_value(_direct_child(component, "Name"))
                if not name or not name.upper().startswith("COMPONENT_"):
                    errors.append(
                        "Component offer has no valid COMPONENT_ Name"
                    )
                    continue
                offers.append((bone.casefold(), name.casefold()))
        return offers, errors

    @classmethod
    def _validate_value(cls, key: str, value: str) -> str:
        if key in {"ammo.ammoMax", "ammo.ammoMax50"}:
            try:
                number = int(value, 10)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if not 0 <= number <= 10_000_000:
                raise ValueError(f"{key} must be between 0 and 10000000")
            return str(number)
        allow_empty = key in {
            "ammo.explosion", "ammo.trailFx", "ammo.primedFx",
            "component.model", "component.locName", "component.locDesc",
            "component.attachBone",
        }
        return cls._validate_identifier(value, key, allow_empty=allow_empty)

    @staticmethod
    def _validate_identifier(
        value: str, field: str, *, allow_empty: bool = False,
    ) -> str:
        if allow_empty and not value:
            return ""
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError(
                f"{field} must contain only letters, numbers, underscores, dots, or hyphens"
            )
        return value

    @staticmethod
    def _validate_boolean(value: Any, field: str) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        normalized = str(value).strip().casefold()
        if normalized in {"1", "true", "yes"}:
            return "true"
        if normalized in {"0", "false", "no"}:
            return "false"
        raise ValueError(f"{field} must be true or false")

    @classmethod
    def _validate_shop_value(cls, key: str, value: Any) -> str:
        if key in {"shop.cost", "shop.ammoCost"}:
            if isinstance(value, bool):
                raise ValueError(f"{key} must be an integer")
            try:
                number = int(str(value).strip(), 10)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer") from exc
            if not 0 <= number <= 2_147_483_647:
                raise ValueError(
                    f"{key} must be between 0 and 2147483647"
                )
            return str(number)
        if key == "shop.availableInSP":
            return cls._validate_boolean(value, key)
        return cls._validate_identifier(
            str(value).strip(), key,
            allow_empty=key in {
                "shop.textLabel", "shop.weaponDesc", "shop.weaponTT",
                "shop.weaponUppercase",
            },
        )

    def _verify_weapon_values(
        self, scan: PackageScan, weapon: str, expected: dict[str, str],
    ) -> None:
        values = self.values(weapon, _scan=scan).values
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError(f"Authored field did not round-trip: {key}")

    def _verify_component_values(
        self, scan: PackageScan, component: str, expected: dict[str, str],
    ) -> None:
        values = self.component_values(component, _scan=scan).values
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError(f"Authored field did not round-trip: {key}")

    def _verify_shop_values(
        self,
        scan: PackageScan,
        weapon: str,
        source: str,
        expected: dict[str, str],
    ) -> None:
        values = self.shop_values(weapon, source, _scan=scan).values
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError(f"Authored shop field did not round-trip: {key}")

    def _verify_animation_clone(
        self,
        scan: PackageScan,
        weapon: str,
        template: str,
        source: str,
        expected_coverage: tuple[tuple[int, str], ...],
        expected_templates: dict[int, bytes],
        original_document: bytes,
    ) -> None:
        target_records = tuple(
            item for item in scan.weapon_animation_records
            if item.weapon_name.casefold() == weapon.casefold()
        )
        actual_coverage = tuple(sorted(
            (
                (item.set_ordinal, item.set_name) for item in target_records
                if item.source == source
            ),
            key=lambda item: item[0],
        ))
        if len(target_records) != len(expected_coverage) or actual_coverage != tuple(
            sorted(expected_coverage, key=lambda item: item[0])
        ):
            raise RuntimeError("Authored animation mapping coverage did not round-trip")
        tree = self._core.read_tree(source)
        groups = [
            item for item in tree.getroot().iter()
            if isinstance(item.tag, str) and _local_name(item) == "WeaponAnimations"
        ]
        for set_ordinal, set_name in expected_coverage:
            if set_ordinal >= len(groups):
                raise RuntimeError("Authored animation set ordinal did not round-trip")
            group = groups[set_ordinal]
            if self._animation_set_name(group) != set_name:
                raise RuntimeError("Authored animation set identity did not round-trip")
            template_matches = [
                item for item in group
                if isinstance(item.tag, str) and _local_name(item) == "Item"
                and item.attrib.get("key", "").strip().casefold()
                == template.casefold()
            ]
            target_matches = [
                item for item in group
                if isinstance(item.tag, str) and _local_name(item) == "Item"
                and item.attrib.get("key", "").strip().casefold()
                == weapon.casefold()
            ]
            if len(template_matches) != 1 or len(target_matches) != 1:
                raise RuntimeError(
                    "Authored animation template/target count did not round-trip"
                )
            template_item = template_matches[0]
            target_item = target_matches[0]
            if template_item.getnext() is not target_item:
                raise RuntimeError("Authored animation mapping insertion order changed")
            expected = expected_templates[set_ordinal]
            if self._canonical_animation_item(template_item, template) != expected:
                raise RuntimeError("Animation template changed during authoring")
            if self._canonical_animation_item(target_item, template) != expected:
                raise RuntimeError("Authored animation mapping differs from its template")

        stripped = deepcopy(tree.getroot())
        stripped_groups = [
            item for item in stripped.iter()
            if isinstance(item.tag, str) and _local_name(item) == "WeaponAnimations"
        ]
        for set_ordinal, _set_name in expected_coverage:
            matches = [
                item for item in stripped_groups[set_ordinal]
                if isinstance(item.tag, str) and _local_name(item) == "Item"
                and item.attrib.get("key", "").strip().casefold()
                == weapon.casefold()
            ]
            if len(matches) != 1:
                raise RuntimeError("Authored animation mapping could not be normalized")
            stripped_groups[set_ordinal].remove(matches[0])
        if self._canonical_element(stripped) != original_document:
            raise RuntimeError(
                "Animation authoring changed data outside inserted mappings"
            )

    def _verify_weapon_bundle_clone(
        self,
        scan: PackageScan,
        plan: WeaponClonePlan,
        original_documents: dict[str, bytes],
    ) -> None:
        spec = plan.spec
        donor = self._unique_weapon(scan, spec.donor_weapon)
        target = self._unique_weapon(scan, spec.weapon_name)
        actual = {
            "Slot": target.slot,
            "AmmoInfo": target.ammo_info,
            "Model": target.model,
            "HumanNameHash": target.human_name_hash,
            "StatName": target.stat_name,
        }
        expected = {
            "Slot": spec.slot,
            "AmmoInfo": spec.ammo_info,
            "Model": spec.model,
            "HumanNameHash": spec.human_name_hash,
            "StatName": spec.stat_name,
        }
        if actual != expected:
            raise RuntimeError("Cloned weapon identities did not round-trip")

        donor_links = sorted(
            (
                item.component_name, item.attach_bone, item.default
            ) for item in scan.weapon_component_links
            if item.weapon_name.casefold() == donor.name.casefold()
        )
        target_links = sorted(
            (
                item.component_name, item.attach_bone, item.default
            ) for item in scan.weapon_component_links
            if item.weapon_name.casefold() == target.name.casefold()
        )
        if target_links != donor_links:
            raise RuntimeError("Cloned weapon attachment links differ from donor")
        for component_name, _bone, _default in target_links:
            definitions = [
                item for item in scan.weapon_components
                if item.name.casefold() == component_name.casefold()
            ]
            if len(definitions) != 1:
                raise RuntimeError(
                    "Cloned weapon no longer reuses one package component definition"
                )

        target_ammo: AmmoRecord | None = None
        if spec.clone_ammo:
            ammo_matches = [
                item for item in scan.ammo
                if item.name.casefold() == str(spec.ammo_name).casefold()
            ]
            if len(ammo_matches) != 1:
                raise RuntimeError("Cloned ammo identity did not round-trip")
            target_ammo = ammo_matches[0]
        else:
            reused = [
                item for item in scan.ammo
                if item.name.casefold() == spec.ammo_info.casefold()
            ]
            if len(reused) != 1:
                raise RuntimeError("Reused target ammo did not remain uniquely resolvable")

        donor_animation = tuple(sorted(
            (
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == donor.name.casefold()
            ),
            key=lambda item: (item.source.casefold(), item.set_ordinal),
        ))
        target_animation = tuple(sorted(
            (
                item for item in scan.weapon_animation_records
                if item.weapon_name.casefold() == target.name.casefold()
            ),
            key=lambda item: (item.source.casefold(), item.set_ordinal),
        ))
        donor_coverage = tuple(
            (item.source, item.set_ordinal, item.set_name)
            for item in donor_animation
        )
        target_coverage = tuple(
            (item.source, item.set_ordinal, item.set_name)
            for item in target_animation
        )
        if target_coverage != donor_coverage:
            raise RuntimeError("Cloned animation-set coverage differs from donor")

        donor_shop_records = tuple(
            item for item in scan.weapon_shop_records
            if item.weapon_name.casefold() == donor.name.casefold()
        )
        target_shop_records = tuple(
            item for item in scan.weapon_shop_records
            if item.weapon_name.casefold() == target.name.casefold()
        )
        if (
            len(donor_shop_records) != 1
            or len(target_shop_records) != 1
            or donor_shop_records[0].source != target_shop_records[0].source
            or donor_shop_records[0].field_name != target_shop_records[0].field_name
            or donor_shop_records[0].representation
            != target_shop_records[0].representation
        ):
            raise RuntimeError("Cloned shop record did not round-trip exactly once")

        parsed = {
            source: self._core.read_tree(source) for source in original_documents
        }
        donor_weapon_item = self._record_item(
            parsed[donor.source], donor.name, "weapon",
        )
        target_weapon_item = self._record_item(
            parsed[target.source], target.name, "weapon",
        )
        if donor_weapon_item.getnext() is not target_weapon_item:
            raise RuntimeError("Cloned weapon was not inserted after its donor")
        normalized_weapon = deepcopy(target_weapon_item)
        for node_name, value in (
            ("Name", donor.name),
            ("Slot", donor.slot),
            ("AmmoInfo", donor.ammo_info),
            ("Model", donor.model),
            ("HumanNameHash", donor.human_name_hash),
            ("StatName", donor.stat_name),
        ):
            _set_preserving_representation(normalized_weapon, node_name, value)
        normalized_weapon.tail = None
        donor_weapon_copy = deepcopy(donor_weapon_item)
        donor_weapon_copy.tail = None
        if self._canonical_element(normalized_weapon) != self._canonical_element(
            donor_weapon_copy
        ):
            raise RuntimeError("Cloned weapon payload differs from donor template")

        if target_ammo is not None:
            donor_ammo = next(
                item for item in scan.ammo
                if item.name.casefold() == donor.ammo_info.casefold()
            )
            donor_ammo_item = self._record_item(
                parsed[donor_ammo.source], donor_ammo.name, "ammo",
            )
            target_ammo_item = self._record_item(
                parsed[target_ammo.source], target_ammo.name, "ammo",
            )
            if donor_ammo_item.getnext() is not target_ammo_item:
                raise RuntimeError("Cloned ammo was not inserted after its donor")
            normalized_ammo = deepcopy(target_ammo_item)
            _set_preserving_representation(
                normalized_ammo, "Name", donor_ammo.name,
            )
            normalized_ammo.tail = None
            donor_ammo_copy = deepcopy(donor_ammo_item)
            donor_ammo_copy.tail = None
            if self._canonical_element(normalized_ammo) != self._canonical_element(
                donor_ammo_copy
            ):
                raise RuntimeError("Cloned ammo payload differs from donor template")

        for donor_record, target_record in zip(
            donor_animation, target_animation, strict=True,
        ):
            tree = parsed[donor_record.source]
            donor_item = self._animation_item(tree, donor_record)
            target_item = self._animation_item(tree, target_record)
            if donor_item.getnext() is not target_item:
                raise RuntimeError(
                    "Cloned animation mapping was not inserted after its donor"
                )
            if self._canonical_animation_item(
                donor_item, donor.name,
            ) != self._canonical_animation_item(target_item, donor.name):
                raise RuntimeError(
                    "Cloned animation mapping differs from donor template"
                )

        shop_tree = parsed[donor_shop_records[0].source]
        donor_shop, donor_identity = self._shop_item(
            shop_tree, donor.name, donor_shop_records,
        )
        target_shop, _target_identity = self._shop_item(
            shop_tree, target.name, target_shop_records,
        )
        if donor_shop.getnext() is not target_shop:
            raise RuntimeError("Cloned shop record was not inserted after its donor")
        normalized_shop = deepcopy(target_shop)
        _set_preserving_representation(
            normalized_shop, _local_name(donor_identity), donor.name,
        )
        normalized_shop.tail = None
        donor_shop_copy = deepcopy(donor_shop)
        donor_shop_copy.tail = None
        if self._canonical_element(normalized_shop) != self._canonical_element(
            donor_shop_copy
        ):
            raise RuntimeError("Cloned shop payload differs from donor template")

        stripped_roots = {
            source: deepcopy(tree.getroot()) for source, tree in parsed.items()
        }
        weapon_root = etree.ElementTree(stripped_roots[target.source])
        created_weapon = self._record_item(weapon_root, target.name, "weapon")
        created_weapon.getparent().remove(created_weapon)
        if target_ammo is not None:
            ammo_root = etree.ElementTree(stripped_roots[target_ammo.source])
            created_ammo = self._record_item(ammo_root, target_ammo.name, "ammo")
            created_ammo.getparent().remove(created_ammo)
        for record in target_animation:
            animation_root = etree.ElementTree(stripped_roots[record.source])
            created_mapping = self._animation_item(animation_root, record)
            created_mapping.getparent().remove(created_mapping)
        shop_root = etree.ElementTree(
            stripped_roots[target_shop_records[0].source]
        )
        created_shop, _identity = self._shop_item(
            shop_root, target.name, target_shop_records,
        )
        created_shop.getparent().remove(created_shop)
        for source, original in original_documents.items():
            if self._canonical_element(stripped_roots[source]) != original:
                raise RuntimeError(
                    "Weapon bundle clone changed data outside inserted records: "
                    + source
                )

    @staticmethod
    def _verify_attachment(
        scan: PackageScan,
        weapon: str,
        component: str,
        expected: dict[str, str],
    ) -> None:
        links = [
            item for item in scan.weapon_component_links
            if item.weapon_name.casefold() == weapon.casefold()
            and item.component_name.casefold() == component.casefold()
        ]
        if len(links) != 1:
            raise RuntimeError("Authored attachment did not remain uniquely resolvable")
        link = links[0]
        actual = {
            "attachment.attachBone": link.attach_bone,
            "attachment.default": "true" if link.default else "false",
        }
        for key, value in expected.items():
            if actual.get(key) != value:
                raise RuntimeError(f"Authored field did not round-trip: {key}")

    @staticmethod
    def _reject_relationship_regressions(
        before: WeaponAuthoringProject,
        after: WeaponAuthoringProject,
    ) -> None:
        def relevant(project: WeaponAuthoringProject) -> set[tuple[str, str, str, str]]:
            return {
                (item.severity, item.code, item.path or "", item.message)
                for item in project.findings if item.code in _RELATIONSHIP_FINDINGS
            }
        added = relevant(after) - relevant(before)
        if added:
            detail = ", ".join(sorted({item[1] for item in added}))
            raise ValueError(
                "Weapon edit introduced unresolved package relationships: " + detail
            )

    def _affected_from_changes(
        self,
        subject_kind: str,
        subject: str,
        changes: tuple[Any, ...],
    ) -> tuple[str, ...]:
        scan = AddonPackageInspector().inspect(self.source)
        if subject_kind == "weapon":
            changed_fields = {
                str(item.get("field", "")) for item in changes
                if isinstance(item, dict)
            }
            if not any(field.startswith("ammo.") for field in changed_fields):
                return (subject,)
            try:
                values = self.values(subject, _scan=scan)
            except ValueError:
                return (subject,)
            return values.affected_weapons
        if subject_kind == "component":
            try:
                return self.component_values(subject, _scan=scan).affected_weapons
            except ValueError:
                return ()
        if subject_kind in {"animation", "shop", "bundle"}:
            return (subject,) if subject else ()
        return (subject.split("/", 1)[0],) if subject else ()


__all__ = [
    "AMMO_FIELDS",
    "ATTACHMENT_FIELDS",
    "AUTHORING_SCHEMA_VERSION",
    "COMPONENT_FIELDS",
    "EDITABLE_COMPONENT_FIELDS",
    "EDITABLE_FIELDS",
    "EDITABLE_SHOP_FIELDS",
    "SHOP_FIELDS",
    "WEAPON_FIELDS",
    "WeaponAnimationAuthoringValues",
    "WeaponAuthoringProject",
    "WeaponAuthoringResult",
    "WeaponAuthoringValues",
    "WeaponAuthoringWorkspace",
    "WeaponCloneAddition",
    "WeaponCloneCollision",
    "WeaponCloneFinding",
    "WeaponClonePlan",
    "WeaponCloneSpec",
    "WeaponComponentAuthoringValues",
    "WeaponShopAuthoringValues",
]
