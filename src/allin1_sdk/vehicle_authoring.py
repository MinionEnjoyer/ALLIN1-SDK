"""Safe copied workspaces for cross-file vehicle metadata authoring."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from lxml import etree

from allin1_sdk.addon_importer import (
    MAX_PACKAGE_BYTES,
    AddonPackageInspector,
    PackageAssetReader,
    PackageScan,
)
from allin1_sdk.vehicle_project import (
    VehicleProject,
    VehicleProjectResolver,
)
from allin1_sdk.vehicle_catalog import (
    VehicleCatalog,
    VehicleCatalogEntry,
    VehicleTrafficPolicy,
)
from allin1_sdk.axle_configurator import (
    EXPORT_FIVEM_RUNTIME,
    AxleConfiguration,
    StockMetadataResult,
    format_handling_flags,
    parse_handling_flags,
    stock_metadata_flags,
    validate_axle_configuration,
)
from allin1_sdk.axle_prefabs import load_prefab_axle_configuration


AUTHORING_SCHEMA_VERSION = 1
MAX_AUTHORING_MEMBER_BYTES = 512 * 1024 * 1024
MAX_AUTHORING_XML_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]{1,96}$")

VEHICLE_FIELDS: dict[str, str] = {
    "vehicle.gameName": "gameName",
    "vehicle.vehicleMakeName": "vehicleMakeName",
    "vehicle.txdName": "txdName",
    "vehicle.vehicleClass": "vehicleClass",
    "vehicle.type": "type",
    "vehicle.layout": "layout",
    "vehicle.audioNameHash": "audioNameHash",
}

HANDLING_FIELDS: tuple[str, ...] = (
    "fMass",
    "fInitialDragCoeff",
    "fDriveBiasFront",
    "nInitialDriveGears",
    "fInitialDriveForce",
    "fDriveInertia",
    "fInitialDriveMaxFlatVel",
    "fBrakeForce",
    "fBrakeBiasFront",
    "fHandBrakeForce",
    "fSteeringLock",
    "fTractionCurveMax",
    "fTractionCurveMin",
    "fTractionCurveLateral",
    "fLowSpeedTractionLossMult",
    "fTractionBiasFront",
    "fTractionLossMult",
    "fSuspensionForce",
    "fSuspensionCompDamp",
    "fSuspensionReboundDamp",
    "fSuspensionUpperLimit",
    "fSuspensionLowerLimit",
    "fSuspensionRaise",
    "fSuspensionBiasFront",
    "fAntiRollBarForce",
    "fAntiRollBarBiasFront",
    "fCollisionDamageMult",
    "fWeaponDamageMult",
    "fDeformationDamageMult",
    "fEngineDamageMult",
)

EDITABLE_FIELDS = tuple(VEHICLE_FIELDS) + tuple(
    f"handling.{name}" for name in HANDLING_FIELDS
) + (
    "variation.lightSettings", "variation.sirenSettings", "variation.kits",
)

_IDENTITY_MODEL_ELEMENTS = frozenset({"modelName", "txdName"})
_IDENTITY_HANDLING_ELEMENTS = frozenset({"handlingId", "handlingName"})
_STREAMED_IDENTITY_SUFFIXES = frozenset({".yft", ".ytd"})

TUNING_COLLECTIONS = ("visibleMods", "linkMods", "statMods", "slotNames")
TUNING_ARRAY_FIELDS = frozenset({
    "linkedModels", "turnOffBones", "minIntVars", "maxIntVars",
})
VMT_TYPES = (
    "VMT_SPOILER", "VMT_BUMPER_F", "VMT_BUMPER_R", "VMT_SKIRT",
    "VMT_EXHAUST", "VMT_CHASSIS", "VMT_GRILL", "VMT_BONNET",
    "VMT_WING_L", "VMT_WING_R", "VMT_ROOF", "VMT_ENGINE", "VMT_BRAKES",
    "VMT_GEARBOX", "VMT_HORN", "VMT_SUSPENSION", "VMT_ARMOUR",
    "VMT_NITROUS", "VMT_TURBO", "VMT_SUBWOOFER", "VMT_TYRE_SMOKE",
    "VMT_HYDRAULICS", "VMT_XENON_LIGHTS", "VMT_WHEELS",
    "VMT_WHEELS_REAR_OR_HYDRAULICS", "VMT_PLTHOLDER", "VMT_PLTVANITY",
    "VMT_INTERIOR1", "VMT_INTERIOR2", "VMT_INTERIOR3", "VMT_INTERIOR4",
    "VMT_INTERIOR5", "VMT_SEATS", "VMT_STEERING", "VMT_KNOB",
    "VMT_PLAQUE", "VMT_ICE", "VMT_TRUNK", "VMT_HYDRO", "VMT_ENGINEBAY1",
    "VMT_ENGINEBAY2", "VMT_ENGINEBAY3", "VMT_CHASSIS2", "VMT_CHASSIS3",
    "VMT_CHASSIS4", "VMT_CHASSIS5", "VMT_DOOR_L", "VMT_DOOR_R",
    "VMT_LIVERY_MOD", "VMT_LIGHTBAR",
)

_TUNING_SCHEMAS: dict[str, dict[str, tuple[str, bool, str]]] = {
    "visibleMods": {
        "modelName": ("identifier", True, ""),
        "modShopLabel": ("identifier", True, ""),
        "linkedModels": ("identifier_array", False, ""),
        "turnOffBones": ("identifier_array", False, ""),
        "type": ("vmt", True, "VMT_SPOILER"),
        "bone": ("identifier", False, "chassis"),
        "collisionBone": ("identifier", False, ""),
        "cameraPos": ("identifier", False, "VMCP_DEFAULT"),
        "audioApply": ("float", False, "1.000000"),
        "weight": ("integer", False, "0"),
        "turnOffExtra": ("boolean", False, "false"),
        "disableBonnetCamera": ("boolean", False, "false"),
        "allowBonnetSlide": ("boolean", False, "true"),
        "weaponSlot": ("integer", False, ""),
        "weaponSlotSecondary": ("integer", False, ""),
        "disableProjectileDriveby": ("boolean", False, ""),
        "disableDriveby": ("boolean", False, ""),
        "disableDrivebySeat": ("integer", False, ""),
        "disableDrivebySeatSecondary": ("integer", False, ""),
        "minIntVars": ("integer_array", False, ""),
        "maxIntVars": ("integer_array", False, ""),
    },
    "linkMods": {
        "modelName": ("identifier", True, ""),
        "bone": ("identifier", False, "chassis"),
        "turnOffExtra": ("boolean", False, "false"),
    },
    "statMods": {
        "identifier": ("identifier", False, ""),
        "modifier": ("float", True, "25"),
        "audioApply": ("float", False, "1.000000"),
        "weight": ("integer", False, "0"),
        "type": ("vmt", True, "VMT_ENGINE"),
    },
    "slotNames": {
        "slot": ("vmt", True, "VMT_SPOILER"),
        "name": ("identifier", True, ""),
    },
}
TUNING_FIELDS = {
    collection: tuple(fields) for collection, fields in _TUNING_SCHEMAS.items()
}

_ATTRIBUTE_TUNING_FIELDS = frozenset({
    "audioApply", "weight", "turnOffExtra", "disableBonnetCamera",
    "allowBonnetSlide", "weaponSlot", "weaponSlotSecondary",
    "disableProjectileDriveby", "disableDriveby", "disableDrivebySeat",
    "disableDrivebySeatSecondary", "modifier",
})

_DISTRIBUTION_FIELDS = frozenset({
    "listed", "name", "manufacturer", "category", "price", "storage",
    "size_tier", "preview_dictionary", "preview_texture", "traffic_enabled",
    "traffic_weight",
})
_CLASS_TO_CATEGORY = {
    "VC_COMPACT": "compacts", "VC_COUPES": "coupes", "VC_COUPE": "coupes",
    "VC_SEDAN": "sedans", "VC_SEDANS": "sedans", "VC_SUV": "suvs",
    "VC_MUSCLE": "muscle", "VC_SPORT": "sports", "VC_SPORTS": "sports",
    "VC_SPORT_CLASSIC": "sportsclassics", "VC_SPORTS_CLASSIC": "sportsclassics",
    "VC_SUPER": "super", "VC_OFF_ROAD": "offroad", "VC_OFFROAD": "offroad",
    "VC_MOTORCYCLE": "motorcycles", "VC_MOTORCYCLES": "motorcycles",
    "VC_VAN": "vans", "VC_VANS": "vans", "VC_BOAT": "boats",
    "VC_BOATS": "boats", "VC_HELICOPTER": "helicopters",
    "VC_HELICOPTERS": "helicopters", "VC_PLANE": "planes",
    "VC_PLANES": "planes", "VC_MILITARY": "military",
    "VC_INDUSTRIAL": "industrial", "VC_OPEN_WHEEL": "openwheel",
    "VC_OPENWHEEL": "openwheel", "VC_EMERGENCY": "emergency",
    "VC_CYCLE": "cycles", "VC_CYCLES": "cycles", "VC_SERVICE": "service",
}


def _distribution_category(value: str) -> str:
    return _CLASS_TO_CATEGORY.get(
        value.strip().upper().replace("-", "_").replace(" ", "_"), "special",
    )


def _distribution_storage(category: str) -> str:
    return {"boats": "harbour", "helicopters": "helipad", "planes": "hangar"}.get(
        category, "garage",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        recover=False, huge_tree=False, remove_blank_text=False,
    )


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
    return (element.get("value", element.text or "")).strip()


def _set_element_value(
    parent: etree._Element, name: str, value: str, *, attribute: bool,
) -> tuple[str, str]:
    element = _direct_child(parent, name)
    if element is None:
        element = etree.SubElement(parent, name)
    before = _element_value(element)
    if attribute:
        element.set("value", value)
        element.text = None
    else:
        element.attrib.pop("value", None)
        element.text = value
    return before, value


def _scalar_descendants(element: etree._Element) -> dict[str, str]:
    """Flatten scalar descendants without discarding unknown profile fields."""
    values: dict[str, str] = {}

    def visit(parent: etree._Element, prefix: str) -> None:
        counts: dict[str, int] = {}
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            name = _local_name(child)
            counts[name] = counts.get(name, 0) + 1
            segment = name if counts[name] == 1 else f"{name}[{counts[name]}]"
            path = f"{prefix}.{segment}" if prefix else segment
            descendants = [item for item in child if isinstance(item.tag, str)]
            if descendants:
                visit(child, path)
            else:
                values[path] = _element_value(child)

    visit(element, "")
    return values


def _scalar_element_map(element: etree._Element) -> dict[str, etree._Element]:
    elements: dict[str, etree._Element] = {}

    def visit(parent: etree._Element, prefix: str) -> None:
        counts: dict[str, int] = {}
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            name = _local_name(child)
            counts[name] = counts.get(name, 0) + 1
            segment = name if counts[name] == 1 else f"{name}[{counts[name]}]"
            path = f"{prefix}.{segment}" if prefix else segment
            descendants = [item for item in child if isinstance(item.tag, str)]
            if descendants:
                visit(child, path)
            else:
                elements[path] = child

    visit(element, "")
    return elements


@dataclass(frozen=True)
class VehicleAuthoringValues:
    model: str
    values: dict[str, str]
    sources: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleDistributionValues:
    model: str
    listed: bool
    name: str
    manufacturer: str
    category: str
    price: int
    storage: str
    size_tier: int
    preview_dictionary: str | None
    preview_texture: str | None
    traffic_enabled: bool
    traffic_weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def catalog_entry(self, source_pack: str) -> VehicleCatalogEntry:
        return VehicleCatalogEntry.from_dict({
            "model": self.model,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "category": self.category,
            "price": self.price,
            "storage": self.storage,
            "source_pack": source_pack,
            "size_tier": self.size_tier,
            **({"preview_dictionary": self.preview_dictionary}
               if self.preview_dictionary else {}),
            **({"preview_texture": self.preview_texture}
               if self.preview_texture else {}),
            "traffic": {
                "enabled": self.traffic_enabled,
                "weight": self.traffic_weight,
            },
        }, 1)


@dataclass(frozen=True)
class VehicleColorSet:
    indices: tuple[int, ...]
    liveries: tuple[bool, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleTuningKitSummary:
    source: str
    name: str
    kit_id: str
    kit_type: str
    visible_mods: int
    link_mods: int
    stat_mods: int
    livery_names: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleLightProfile:
    source: str
    profile_id: str
    name: str
    values: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleAppearanceValues:
    model: str
    source: str
    colors: tuple[VehicleColorSet, ...]
    kits: tuple[str, ...]
    light_settings: str
    siren_settings: str
    available_kits: tuple[VehicleTuningKitSummary, ...]
    light_profiles: tuple[VehicleLightProfile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "source": self.source,
            "colors": [item.to_dict() for item in self.colors],
            "kits": list(self.kits),
            "light_settings": self.light_settings,
            "siren_settings": self.siren_settings,
            "available_kits": [item.to_dict() for item in self.available_kits],
            "light_profiles": [item.to_dict() for item in self.light_profiles],
        }


@dataclass(frozen=True)
class VehicleTuningEntry:
    collection: str
    index: int
    summary: str
    mod_type: str
    fields: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.collection}:{self.index}"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["key"] = self.key
        return payload


@dataclass(frozen=True)
class VehicleTuningAsset:
    path: str
    name: str
    kind: str
    referenced: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleTuningFinding:
    severity: str
    code: str
    message: str
    entry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VehicleTuningBuilderValues:
    model: str
    kit_name: str
    kit_id: str
    kit_type: str
    source: str
    entries: tuple[VehicleTuningEntry, ...]
    assets: tuple[VehicleTuningAsset, ...]
    findings: tuple[VehicleTuningFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "kit_name": self.kit_name,
            "kit_id": self.kit_id,
            "kit_type": self.kit_type,
            "source": self.source,
            "entries": [item.to_dict() for item in self.entries],
            "assets": [item.to_dict() for item in self.assets],
            "findings": [item.to_dict() for item in self.findings],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "collections": list(TUNING_COLLECTIONS),
            "vmt_types": list(VMT_TYPES),
            "field_schemas": {
                collection: {
                    field: {
                        "kind": kind, "required": required, "default": default,
                    }
                    for field, (kind, required, default) in fields.items()
                }
                for collection, fields in _TUNING_SCHEMAS.items()
            },
        }


@dataclass(frozen=True)
class VehicleAuthoringResult:
    workspace: Path
    revision: int
    model: str
    changes: tuple[dict[str, str], ...]
    history: Path
    project: VehicleProject
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "workspace": str(self.workspace),
            "revision": self.revision,
            "model": self.model,
            "changes": list(self.changes),
            "history": str(self.history),
            "validation": self.project.to_dict(),
            "warnings": list(self.warnings),
        }


class VehicleAuthoringWorkspace:
    """Copied package source with transactional structured metadata edits."""

    def __init__(self, workspace: str | Path) -> None:
        authored = Path(workspace).expanduser()
        if authored.is_symlink():
            raise ValueError("Vehicle authoring workspace cannot be a symbolic link")
        self.root = authored.resolve()
        self.manifest_path = self.root / "vehicle-authoring.json"
        if not self.root.is_dir() or not self.manifest_path.is_file():
            raise ValueError("Vehicle authoring workspace manifest is missing")
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid vehicle authoring manifest: {exc}") from exc
        if self.manifest.get("schema_version") != AUTHORING_SCHEMA_VERSION:
            raise ValueError("Unsupported vehicle authoring workspace schema")
        relative = self.manifest.get("content_root")
        if not isinstance(relative, str):
            raise ValueError("Vehicle authoring workspace has no content root")
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Vehicle authoring content root is unsafe")
        self.source = (self.root / Path(*path.parts)).resolve()
        if not self.source.is_relative_to(self.root) or not self.source.is_dir():
            raise ValueError("Vehicle authoring content root is missing or unsafe")

    @classmethod
    def create(
        cls, source: str | Path, destination: str | Path,
    ) -> "VehicleAuthoringWorkspace":
        source_path = Path(source).expanduser().resolve()
        scan = AddonPackageInspector().inspect(source_path)
        project = VehicleProjectResolver.inspect_scan(scan)
        if not project.models:
            raise ValueError(
                "Vehicle authoring requires visible vehicles.meta records; extract an "
                "opaque dlc.rpf into a reviewed source tree first"
            )
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Vehicle authoring destination already exists: {target}")
        if target == source_path or target.is_relative_to(source_path):
            raise ValueError("Vehicle authoring output must be outside its source tree")
        target.parent.mkdir(parents=True, exist_ok=True)
        required = scan.total_bytes + 64 * 1024 * 1024
        if shutil.disk_usage(target.parent).free < required:
            raise ValueError("Not enough free disk space for the copied authoring workspace")
        stage = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.vehicle-authoring-", dir=target.parent,
        )).resolve()
        try:
            relative_root = (
                PurePosixPath("source/dlc.rpf.source")
                if source_path.is_dir() and source_path.name.casefold() == "dlc.rpf.source"
                else PurePosixPath("source")
            )
            content_root = stage / Path(*relative_root.parts)
            content_root.mkdir(parents=True)
            cls._copy_scan(source_path, scan, content_root)
            copied_scan = AddonPackageInspector().inspect(content_root)
            copied_project = VehicleProjectResolver.inspect_scan(copied_scan)
            if copied_project.inventory_fingerprint != project.inventory_fingerprint:
                raise RuntimeError("Copied authoring inventory does not match its source")
            manifest = {
                "schema_version": AUTHORING_SCHEMA_VERSION,
                "operation": "vehicle_authoring_workspace",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "original_source": str(source_path),
                "content_root": relative_root.as_posix(),
                "inventory_fingerprint": project.inventory_fingerprint,
                "models": [item.model for item in project.models],
                "revision": 0,
                "editable_fields": list(EDITABLE_FIELDS),
                "identity_migration": "transactional",
                "identity_fields_locked": ["kitName", "id"],
                "axle_configurations": {},
                "distribution": {
                    item.model.casefold(): {
                        "listed": True,
                        "name": item.display_name or item.model,
                        "manufacturer": item.make_name,
                        "category": _distribution_category(item.vehicle_class),
                        "price": 0,
                        "storage": _distribution_storage(
                            _distribution_category(item.vehicle_class)
                        ),
                        "size_tier": 0,
                        "preview_dictionary": None,
                        "preview_texture": None,
                        "traffic_enabled": False,
                        "traffic_weight": 1.0,
                    }
                    for item in copied_project.models
                },
            }
            (stage / "history").mkdir()
            (stage / "reports").mkdir()
            (stage / "vehicle-authoring.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
            )
            (stage / "reports" / "initial-validation.json").write_text(
                json.dumps(copied_project.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            stage.rename(target)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return cls(target)

    @staticmethod
    def _copy_scan(source: Path, scan: PackageScan, target: Path) -> None:
        reader = PackageAssetReader(source)
        copied_bytes = 0
        for entry in scan.entries:
            if entry.size > MAX_AUTHORING_MEMBER_BYTES:
                raise ValueError(
                    f"Authoring member exceeds the guarded 512 MiB limit: {entry.path}"
                )
            destination = target / Path(*PurePosixPath(entry.path).parts)
            destination = destination.resolve(strict=False)
            if not destination.is_relative_to(target):
                raise ValueError(f"Authoring member escapes the workspace: {entry.path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                original = (
                    source / Path(*PurePosixPath(entry.path).parts)
                ).resolve(strict=True)
                if not original.is_relative_to(source) or original.is_symlink():
                    raise ValueError(f"Unsafe authoring source member: {entry.path}")
                shutil.copyfile(original, destination)
            else:
                content = reader.read(entry.path, limit=entry.size + 1)
                if content.truncated or len(content.data) != entry.size:
                    raise ValueError(f"Could not copy complete authoring member: {entry.path}")
                destination.write_bytes(content.data)
            if destination.stat().st_size != entry.size:
                raise RuntimeError(f"Authoring copy size mismatch: {entry.path}")
            copied_bytes += entry.size
            if copied_bytes > MAX_PACKAGE_BYTES:
                raise ValueError("Copied authoring source exceeds the package size limit")

    @property
    def revision(self) -> int:
        value = self.manifest.get("revision", 0)
        if not isinstance(value, int) or value < 0:
            raise ValueError("Vehicle authoring revision is invalid")
        return value

    def inspect(self) -> VehicleProject:
        return self._scan_project()[1]

    def _with_axle_configurations(self, project: VehicleProject) -> VehicleProject:
        raw = self.manifest.get("axle_configurations", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("Vehicle axle configurations are invalid")
        configurations = tuple(
            load_prefab_axle_configuration(value).to_dict()
            for _key, value in sorted(raw.items(), key=lambda item: str(item[0]).casefold())
            if isinstance(value, dict)
        )
        if len(configurations) != len(raw):
            raise ValueError("Vehicle axle configuration entry is invalid")
        return replace(project, axle_configurations=configurations)

    def axle_configuration(self, model: str) -> AxleConfiguration | None:
        project_model = self.inspect().model(model)
        raw = self.manifest.get("axle_configurations", {})
        if not isinstance(raw, dict):
            raise ValueError("Vehicle axle configurations are invalid")
        payload = raw.get(project_model.model.casefold())
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise ValueError(f"Axle configuration is invalid: {project_model.model}")
        configuration = load_prefab_axle_configuration(payload)
        if configuration.vehicle_model != project_model.model.casefold():
            raise ValueError("Axle configuration model does not match its project key")
        return configuration

    def set_axle_configuration(
        self,
        configuration: AxleConfiguration,
        *,
        bones: Iterable[Any] = (),
        expected_revision: int | None = None,
    ) -> VehicleAuthoringResult:
        """Apply project config and handling changes as one guarded revision."""
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError(
                f"Vehicle authoring revision changed (expected {expected_revision}, "
                f"found {self.revision})"
            )
        scan, before_project = self._scan_project()
        model = before_project.model(configuration.vehicle_model)
        if model.model.casefold() != configuration.vehicle_model:
            raise ValueError("Axle configuration model does not match the selected vehicle")
        findings = validate_axle_configuration(
            configuration, bones,
            asset_names=(item.path for item in model.assets),
        )
        errors = [item for item in findings if item.severity == "error"]
        if errors:
            raise ValueError("Axle configuration failed validation: " + errors[0].message)

        handling_matches = [
            item for item in scan.handlings
            if item.name.casefold() == model.handling_id.casefold()
        ]
        if len(handling_matches) != 1:
            raise ValueError(f"Handling record was not found uniquely: {model.handling_id}")
        handling_source = handling_matches[0].source
        tree = self._read_tree(handling_source)
        handling_item = self._handling_item(tree, model.handling_id)
        flags_element = _direct_child(handling_item, "strHandlingFlags")
        flags_text = _element_value(flags_element) or "00000000"
        flags_result: StockMetadataResult = stock_metadata_flags(
            configuration, parse_handling_flags(flags_text),
        )
        flags_after = format_handling_flags(flags_result.updated_flags, flags_text)

        raw_configs = self.manifest.get("axle_configurations", {})
        if raw_configs is None:
            raw_configs = {}
        if not isinstance(raw_configs, dict):
            raise ValueError("Vehicle axle configurations are invalid")
        model_key = configuration.vehicle_model.casefold()
        previous_config = raw_configs.get(model_key)
        next_config = configuration.to_dict()
        changes: list[dict[str, str]] = []
        if previous_config != next_config:
            changes.append({
                "field": "axles.configuration",
                "before": json.dumps(previous_config, sort_keys=True) if previous_config else "",
                "after": json.dumps(next_config, sort_keys=True),
            })
        trees: dict[str, etree._ElementTree] = {}
        if flags_after != flags_text:
            before, after = _set_element_value(
                handling_item, "strHandlingFlags", flags_after,
                attribute=bool(flags_element is not None and "value" in flags_element.attrib),
            )
            changes.append({"field": "handling.strHandlingFlags", "before": before, "after": after})
            trees[handling_source] = tree

        powered = {item.powered for item in configuration.axles}
        if configuration.export_mode == EXPORT_FIVEM_RUNTIME and len(powered) > 1:
            bias_element = _direct_child(handling_item, "fDriveBiasFront")
            bias_before = _element_value(bias_element)
            try:
                usable_bias = 0.0 < float(bias_before) < 1.0
            except ValueError:
                usable_bias = False
            if not usable_bias:
                before, after = _set_element_value(
                    handling_item, "fDriveBiasFront", "0.5", attribute=True,
                )
                changes.append({
                    "field": "handling.fDriveBiasFront", "before": before, "after": after,
                })
                trees[handling_source] = tree
        if not changes:
            raise ValueError("Axle configuration update contains no changed values")

        history = self._new_history(
            model.model, trees, tuple(changes),
            operation="vehicle_axle_configuration", snapshot_manifest=True,
        )
        previous_manifest = deepcopy(self.manifest)
        try:
            configs = self.manifest.setdefault("axle_configurations", {})
            if not isinstance(configs, dict):
                raise ValueError("Vehicle axle configurations are invalid")
            configs[model_key] = next_config
            if trees:
                self._commit_trees(trees)
            after_scan, after_project = self._scan_project()
            self._reject_new_findings(before_project, after_project, model.model)
            after_handling = next(
                item for item in after_scan.handlings
                if item.name.casefold() == model.handling_id.casefold()
            )
            roundtrip_tree = self._read_tree(after_handling.source)
            roundtrip_item = self._handling_item(roundtrip_tree, model.handling_id)
            roundtrip_flags = _element_value(
                _direct_child(roundtrip_item, "strHandlingFlags")
            ) or "00000000"
            if parse_handling_flags(roundtrip_flags) != flags_result.updated_flags:
                raise RuntimeError("Authored handling steering flags did not round-trip")
            revision = self._finish_revision(history, after_project)
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        warnings = tuple(
            dict.fromkeys((
                *(item.message for item in findings if item.severity != "error"),
                *flags_result.warnings,
            ))
        )
        return VehicleAuthoringResult(
            self.root, revision, model.model, tuple(changes), history,
            after_project, warnings,
        )

    def distribution(self, model: str) -> VehicleDistributionValues:
        project_model = self.inspect().model(model)
        category = _distribution_category(project_model.vehicle_class)
        defaults: dict[str, Any] = {
            "listed": True,
            "name": project_model.display_name or project_model.model,
            "manufacturer": project_model.make_name,
            "category": category,
            "price": 0,
            "storage": _distribution_storage(category),
            "size_tier": 0,
            "preview_dictionary": None,
            "preview_texture": None,
            "traffic_enabled": False,
            "traffic_weight": 1.0,
        }
        raw_distribution = self.manifest.get("distribution", {})
        if raw_distribution is not None and not isinstance(raw_distribution, dict):
            raise ValueError("Vehicle authoring distribution settings are invalid")
        raw = (raw_distribution or {}).get(project_model.model.casefold(), {})
        if not isinstance(raw, dict):
            raise ValueError(f"Distribution settings are invalid: {project_model.model}")
        unknown = sorted(set(raw) - _DISTRIBUTION_FIELDS)
        if unknown:
            raise ValueError("Unsupported distribution fields: " + ", ".join(unknown))
        values = {**defaults, **raw}
        candidate = VehicleDistributionValues(
            model=project_model.model.casefold(),
            listed=values["listed"],
            name=values["name"],
            manufacturer=values["manufacturer"],
            category=str(values["category"]).strip().lower(),
            price=values["price"],
            storage=str(values["storage"]).strip().lower(),
            size_tier=values["size_tier"],
            preview_dictionary=values["preview_dictionary"] or None,
            preview_texture=values["preview_texture"] or None,
            traffic_enabled=values["traffic_enabled"],
            traffic_weight=values["traffic_weight"],
        )
        if not isinstance(candidate.listed, bool):
            raise ValueError("Distribution listed must be a boolean")
        candidate.catalog_entry("addon")
        return candidate

    def set_distribution(
        self, model: str, updates: dict[str, Any], *, expected_revision: int | None = None,
    ) -> VehicleDistributionValues:
        unknown = sorted(set(updates) - _DISTRIBUTION_FIELDS)
        if unknown:
            raise ValueError("Unsupported distribution fields: " + ", ".join(unknown))
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError(
                f"Vehicle authoring revision changed (expected {expected_revision}, "
                f"found {self.revision})"
            )
        current = self.distribution(model)
        payload = current.to_dict()
        payload.pop("model", None)
        payload.update(updates)
        candidate = VehicleDistributionValues(
            model=current.model,
            listed=payload["listed"],
            name=str(payload["name"]).strip(),
            manufacturer=str(payload["manufacturer"]).strip(),
            category=str(payload["category"]).strip().lower(),
            price=payload["price"],
            storage=str(payload["storage"]).strip().lower(),
            size_tier=payload["size_tier"],
            preview_dictionary=(str(payload["preview_dictionary"]).strip()
                                if payload.get("preview_dictionary") else None),
            preview_texture=(str(payload["preview_texture"]).strip()
                             if payload.get("preview_texture") else None),
            traffic_enabled=payload["traffic_enabled"],
            traffic_weight=payload["traffic_weight"],
        )
        if not isinstance(candidate.listed, bool):
            raise ValueError("Distribution listed must be a boolean")
        candidate.catalog_entry("addon")
        distribution = self.manifest.setdefault("distribution", {})
        if not isinstance(distribution, dict):
            raise ValueError("Vehicle authoring distribution settings are invalid")
        if candidate.to_dict() == current.to_dict():
            raise ValueError("Vehicle distribution update contains no changed values")
        stored = candidate.to_dict()
        stored.pop("model", None)
        distribution[current.model.casefold()] = stored
        self.manifest["revision"] = self.revision + 1
        self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
        self._write_manifest()
        return candidate

    def distribution_catalog(
        self, package_id: str, package_name: str, source_pack: str,
    ) -> VehicleCatalog:
        entries = tuple(
            values.catalog_entry(source_pack)
            for item in self.inspect().models
            for values in (self.distribution(item.model),)
            if values.listed
        )
        if not entries:
            raise ValueError("List at least one vehicle in GBAY before building the package")
        return VehicleCatalog.from_dict({
            "schema_version": 1,
            "id": package_id,
            "name": package_name,
            "vehicles": [entry.to_dict() for entry in entries],
        })

    def _scan_project(self) -> tuple[PackageScan, VehicleProject]:
        """Scan once when an operation needs inventory and resolved relationships."""
        scan = AddonPackageInspector().inspect(self.source)
        return scan, self._with_axle_configurations(
            VehicleProjectResolver.inspect_scan(scan)
        )

    def publish_source(self) -> Path:
        """Return a source that can honestly include the workspace's current edits."""
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
                "Authoring workspace contains multiple dlc.rpf.source directories"
            )
        if self.revision:
            raise ValueError(
                "This edited workspace contains only a prebuilt dlc.rpf. Extract it "
                "into one reviewed dlc.rpf.source before publishing so metadata edits "
                "cannot be silently omitted."
            )
        return self.source

    def values(
        self, model: str, *, _scan: PackageScan | None = None,
    ) -> VehicleAuthoringValues:
        scan = _scan or AddonPackageInspector().inspect(self.source)
        vehicle_matches = [
            item for item in scan.vehicles if item.model_name.casefold() == model.casefold()
        ]
        if len(vehicle_matches) != 1:
            raise ValueError(f"Vehicle was not found uniquely in workspace: {model}")
        vehicle = vehicle_matches[0]
        handling_matches = [
            item for item in scan.handlings
            if item.name.casefold() == vehicle.handling_id.casefold()
        ]
        variation_matches = [
            item for item in scan.variations
            if item.model_name.casefold() == model.casefold()
        ]
        if len(handling_matches) != 1:
            raise ValueError(f"Handling record was not found uniquely: {vehicle.handling_id}")
        if len(variation_matches) != 1:
            raise ValueError(f"Variation record was not found uniquely: {model}")
        trees: dict[str, etree._ElementTree] = {}

        def tree(path: str) -> etree._ElementTree:
            if path not in trees:
                trees[path] = self._read_tree(path)
            return trees[path]

        vehicle_item = self._vehicle_item(tree(vehicle.source), model)
        handling_item = self._handling_item(
            tree(handling_matches[0].source), vehicle.handling_id,
        )
        variation_item = self._variation_item(
            tree(variation_matches[0].source), model,
        )
        values = {
            key: _element_value(_direct_child(vehicle_item, element_name))
            for key, element_name in VEHICLE_FIELDS.items()
        }
        values.update({
            f"handling.{name}": _element_value(_direct_child(handling_item, name))
            for name in HANDLING_FIELDS
        })
        values["variation.lightSettings"] = _element_value(
            _direct_child(variation_item, "lightSettings")
        )
        values["variation.sirenSettings"] = _element_value(
            _direct_child(variation_item, "sirenSettings")
        )
        kits = _direct_child(variation_item, "kits")
        values["variation.kits"] = ", ".join(
            (item.text or "").strip()
            for item in (kits if kits is not None else ())
            if isinstance(item.tag, str) and _local_name(item) == "Item"
            and (item.text or "").strip()
        )
        return VehicleAuthoringValues(
            model=vehicle.model_name,
            values=values,
            sources={
                "vehicle": vehicle.source,
                "handling": handling_matches[0].source,
                "variation": variation_matches[0].source,
            },
        )

    def appearance(
        self, model: str, *, _scan: PackageScan | None = None,
    ) -> VehicleAppearanceValues:
        """Return linked colors, liveries, kits, and local light definitions."""
        scan = _scan or AddonPackageInspector().inspect(self.source)
        matches = [
            item for item in scan.variations
            if item.model_name.casefold() == model.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Variation record was not found uniquely: {model}")
        variation = matches[0]
        tree = self._read_tree(variation.source)
        item = self._variation_item(tree, model)
        colors: list[VehicleColorSet] = []
        color_container = _direct_child(item, "colors")
        for color in color_container if color_container is not None else ():
            if not isinstance(color.tag, str) or _local_name(color) != "Item":
                continue
            indices_element = _direct_child(color, "indices")
            tokens = re.findall(r"[-+]?\d+", indices_element.text or "") \
                if indices_element is not None else []
            indices = tuple(int(token, 10) for token in tokens)
            livery_container = _direct_child(color, "liveries")
            liveries = tuple(
                _element_value(entry).casefold() in {"true", "1"}
                for entry in (livery_container if livery_container is not None else ())
                if isinstance(entry.tag, str) and _local_name(entry) == "Item"
            )
            colors.append(VehicleColorSet(indices, liveries))

        kit_summaries: list[VehicleTuningKitSummary] = []
        kit_sources = sorted({record.source for record in scan.kits}, key=str.casefold)
        for source in kit_sources:
            kit_tree = self._read_tree(source)
            for record in (entry for entry in scan.kits if entry.source == source):
                kit_item = self._find_item(kit_tree, "Kits", "kitName", record.name)
                def count(container_name: str) -> int:
                    container = _direct_child(kit_item, container_name)
                    return sum(
                        isinstance(child.tag, str) and _local_name(child) == "Item"
                        for child in (container if container is not None else ())
                    )
                livery_names = _direct_child(kit_item, "liveryNames")
                kit_summaries.append(VehicleTuningKitSummary(
                    source=source,
                    name=record.name,
                    kit_id=_element_value(_direct_child(kit_item, "id")),
                    kit_type=_element_value(_direct_child(kit_item, "kitType")),
                    visible_mods=count("visibleMods"),
                    link_mods=count("linkMods"),
                    stat_mods=count("statMods"),
                    livery_names=tuple(
                        _element_value(child) for child in (
                            livery_names if livery_names is not None else ()
                        )
                        if isinstance(child.tag, str) and _local_name(child) == "Item"
                        and _element_value(child)
                    ),
                ))

        profiles: list[VehicleLightProfile] = []
        for entry in scan.entries:
            if entry.suffix not in {".meta", ".xml"} or entry.size > MAX_AUTHORING_XML_BYTES:
                continue
            if "carcols" not in PurePosixPath(entry.path).name.casefold():
                continue
            light_tree = self._read_tree(entry.path)
            for container in light_tree.getroot().iter():
                if not isinstance(container.tag, str) or _local_name(container) != "Lights":
                    continue
                for light in container:
                    if not isinstance(light.tag, str) or _local_name(light) != "Item":
                        continue
                    profile_id = _element_value(_direct_child(light, "id"))
                    if not profile_id:
                        continue
                    profiles.append(VehicleLightProfile(
                        source=entry.path,
                        profile_id=profile_id,
                        name=_element_value(_direct_child(light, "name")),
                        values=_scalar_descendants(light),
                    ))
        kits = _direct_child(item, "kits")
        return VehicleAppearanceValues(
            model=model,
            source=variation.source,
            colors=tuple(colors),
            kits=tuple(
                (child.text or "").strip()
                for child in (kits if kits is not None else ())
                if isinstance(child.tag, str) and _local_name(child) == "Item"
                and (child.text or "").strip()
            ),
            light_settings=_element_value(_direct_child(item, "lightSettings")),
            siren_settings=_element_value(_direct_child(item, "sirenSettings")),
            available_kits=tuple(kit_summaries),
            light_profiles=tuple(profiles),
        )

    def update(
        self, model: str, updates: dict[str, str],
    ) -> VehicleAuthoringResult:
        unknown = sorted(set(updates) - set(EDITABLE_FIELDS))
        if unknown:
            raise ValueError("Unsupported vehicle authoring fields: " + ", ".join(unknown))
        scan, before_project = self._scan_project()
        current = self.values(model, _scan=scan)
        normalized = {
            key: self._validate_value(key, str(value).strip())
            for key, value in updates.items()
        }
        changed = {
            key: value for key, value in normalized.items()
            if value != current.values.get(key, "")
        }
        if not changed:
            raise ValueError("Vehicle authoring update contains no changed values")
        existing_kits = {
            value.casefold()
            for item in scan.kits for value in (item.name, item.kit_id) if value
        }
        if "variation.kits" in changed:
            requested = self._kit_values(changed["variation.kits"])
            missing = [item for item in requested if item.casefold() not in existing_kits]
            if missing:
                raise ValueError("Unknown tuning kits: " + ", ".join(missing))

        trees: dict[str, etree._ElementTree] = {}
        for path in set(current.sources.values()):
            trees[path] = self._read_tree(path)
        vehicle_item = self._vehicle_item(trees[current.sources["vehicle"]], model)
        handling_name = before_project.model(model).handling_id
        handling_item = self._handling_item(
            trees[current.sources["handling"]], handling_name,
        )
        variation_item = self._variation_item(
            trees[current.sources["variation"]], model,
        )
        changes: list[dict[str, str]] = []
        for key, value in changed.items():
            if key in VEHICLE_FIELDS:
                before, after = _set_element_value(
                    vehicle_item, VEHICLE_FIELDS[key], value, attribute=False,
                )
            elif key.startswith("handling."):
                before, after = _set_element_value(
                    handling_item, key.removeprefix("handling."), value,
                    attribute=True,
                )
            elif key in {"variation.lightSettings", "variation.sirenSettings"}:
                before, after = _set_element_value(
                    variation_item, key.removeprefix("variation."), value,
                    attribute=True,
                )
            else:
                kits = _direct_child(variation_item, "kits")
                if kits is None:
                    kits = etree.SubElement(variation_item, "kits")
                before = ", ".join(
                    (item.text or "").strip() for item in kits
                    if isinstance(item.tag, str) and _local_name(item) == "Item"
                )
                for child in tuple(kits):
                    kits.remove(child)
                for kit in self._kit_values(value):
                    etree.SubElement(kits, "Item").text = kit
                after = value
            changes.append({"field": key, "before": before, "after": after})

        history = self._new_history(model, trees, tuple(changes))
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            after_scan, after_project = self._scan_project()
            self._reject_new_findings(before_project, after_project, model)
            after_values = self.values(model, _scan=after_scan)
            for key, expected in changed.items():
                if after_values.values.get(key) != expected:
                    raise RuntimeError(f"Authored field did not round-trip: {key}")
            self._record_post_edit_state(history)
            revision = self.revision + 1
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            report = history / "validation.json"
            report.write_text(
                json.dumps(after_project.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            self._write_manifest()
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            workspace=self.root,
            revision=revision,
            model=model,
            changes=tuple(changes),
            history=history,
            project=after_project,
        )

    def update_appearance(
        self,
        model: str,
        *,
        colors: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        kits: list[str] | tuple[str, ...] | None = None,
        light_settings: int | str | None = None,
        siren_settings: int | str | None = None,
    ) -> VehicleAuthoringResult:
        """Replace structured variation data as one validated, undoable edit."""
        scan, before_project = self._scan_project()
        current = self.appearance(model, _scan=scan)
        normalized_colors = (
            self._normalize_colors(colors) if colors is not None else current.colors
        )
        normalized_kits = (
            tuple(dict.fromkeys(self._validate_identifier(item, "kit") for item in kits))
            if kits is not None else current.kits
        )
        normalized_light = (
            self._validate_setting(light_settings, "Light")
            if light_settings is not None else current.light_settings
        )
        normalized_siren = (
            self._validate_setting(siren_settings, "Siren")
            if siren_settings is not None else current.siren_settings
        )
        known_kits = {
            value.casefold() for record in scan.kits
            for value in (record.name, record.kit_id) if value
        }
        missing = [item for item in normalized_kits if item.casefold() not in known_kits]
        if missing:
            raise ValueError("Unknown tuning kits: " + ", ".join(missing))

        changed: list[dict[str, str]] = []
        if normalized_colors != current.colors:
            changed.append({
                "field": "variation.colors",
                "before": json.dumps([item.to_dict() for item in current.colors]),
                "after": json.dumps([item.to_dict() for item in normalized_colors]),
            })
        if normalized_kits != current.kits:
            changed.append({
                "field": "variation.kits", "before": ", ".join(current.kits),
                "after": ", ".join(normalized_kits),
            })
        for field, before, after in (
            ("variation.lightSettings", current.light_settings, normalized_light),
            ("variation.sirenSettings", current.siren_settings, normalized_siren),
        ):
            if before != after:
                changed.append({"field": field, "before": before, "after": after})
        if not changed:
            raise ValueError("Vehicle appearance update contains no changed values")

        tree = self._read_tree(current.source)
        item = self._variation_item(tree, model)
        if normalized_colors != current.colors:
            container = _direct_child(item, "colors")
            if container is None:
                container = etree.Element("colors")
                item.insert(1 if len(item) else 0, container)
            for child in tuple(container):
                container.remove(child)
            for color in normalized_colors:
                color_item = etree.SubElement(container, "Item")
                indices = etree.SubElement(color_item, "indices")
                indices.set("content", "char_array")
                indices.text = "\n" + "\n".join(str(value) for value in color.indices) + "\n"
                liveries = etree.SubElement(color_item, "liveries")
                for enabled in color.liveries:
                    etree.SubElement(
                        liveries, "Item", value="true" if enabled else "false",
                    )
        if normalized_kits != current.kits:
            container = _direct_child(item, "kits")
            if container is None:
                container = etree.SubElement(item, "kits")
            for child in tuple(container):
                container.remove(child)
            for kit in normalized_kits:
                etree.SubElement(container, "Item").text = kit
        if normalized_light != current.light_settings:
            _set_element_value(item, "lightSettings", normalized_light, attribute=True)
        if normalized_siren != current.siren_settings:
            _set_element_value(item, "sirenSettings", normalized_siren, attribute=True)
        trees = {current.source: tree}
        history = self._new_history(model, trees, tuple(changed))
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            after_scan, after_project = self._scan_project()
            self._reject_new_findings(before_project, after_project, model)
            after = self.appearance(model, _scan=after_scan)
            if (
                after.colors != normalized_colors or after.kits != normalized_kits
                or after.light_settings != normalized_light
                or after.siren_settings != normalized_siren
            ):
                raise RuntimeError("Vehicle appearance did not round-trip")
            revision = self._finish_revision(history, after_project)
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            self.root, revision, model, tuple(changed), history, after_project,
        )

    def update_tuning_kit(
        self, model: str, kit_name: str, *, kit_type: str | None = None,
        livery_names: list[str] | tuple[str, ...] | None = None,
    ) -> VehicleAuthoringResult:
        """Edit safe, structured fields on an existing linked tuning kit."""
        before_scan, before_project = self._scan_project()
        appearance = self.appearance(model, _scan=before_scan)
        matches = [
            item for item in appearance.available_kits
            if item.name.casefold() == kit_name.casefold()
        ]
        if len(matches) != 1 or not any(
            item.casefold() == kit_name.casefold() for item in appearance.kits
        ):
            raise ValueError(f"Linked tuning kit was not found uniquely: {kit_name}")
        current = matches[0]
        normalized_type = (
            self._validate_identifier(kit_type, "kit type")
            if kit_type is not None else current.kit_type
        )
        if normalized_type and not normalized_type.startswith("MKT_"):
            raise ValueError("Tuning kit type must start with MKT_")
        normalized_liveries = (
            tuple(dict.fromkeys(
                self._validate_identifier(value, "livery label")
                for value in livery_names
            )) if livery_names is not None else current.livery_names
        )
        changes: list[dict[str, str]] = []
        if normalized_type != current.kit_type:
            changes.append({
                "field": f"tuning.{kit_name}.kitType",
                "before": current.kit_type, "after": normalized_type,
            })
        if normalized_liveries != current.livery_names:
            changes.append({
                "field": f"tuning.{kit_name}.liveryNames",
                "before": ", ".join(current.livery_names),
                "after": ", ".join(normalized_liveries),
            })
        if not changes:
            raise ValueError("Tuning-kit update contains no changed values")
        tree = self._read_tree(current.source)
        item = self._find_item(tree, "Kits", "kitName", current.name)
        if normalized_type != current.kit_type:
            _set_element_value(item, "kitType", normalized_type, attribute=False)
        if normalized_liveries != current.livery_names:
            container = _direct_child(item, "liveryNames")
            if container is None:
                container = etree.SubElement(item, "liveryNames")
            for child in tuple(container):
                container.remove(child)
            for label in normalized_liveries:
                etree.SubElement(container, "Item").text = label
        trees = {current.source: tree}
        history = self._new_history(model, trees, tuple(changes))
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            after_project = self.inspect()
            self._reject_new_findings(before_project, after_project, model)
            revision = self._finish_revision(history, after_project)
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            self.root, revision, model, tuple(changes), history, after_project,
        )

    def tuning_builder(
        self, model: str, kit_name: str | None = None, *,
        _scan: PackageScan | None = None,
    ) -> VehicleTuningBuilderValues:
        """Return a structured inventory for one linked carcols tuning kit."""
        scan = _scan or AddonPackageInspector().inspect(self.source)
        appearance = self.appearance(model, _scan=scan)
        kit = self._resolve_tuning_kit(appearance, kit_name)
        tree = self._read_tree(kit.source)
        kit_item = self._find_item(tree, "Kits", "kitName", kit.name)
        entries: list[VehicleTuningEntry] = []
        for collection_name in TUNING_COLLECTIONS:
            container = _direct_child(kit_item, collection_name)
            collection_items = [
                item for item in (container if container is not None else ())
                if isinstance(item.tag, str) and _local_name(item) == "Item"
            ]
            for index, item in enumerate(collection_items):
                fields = self._tuning_entry_fields(item)
                summary = (
                    fields.get("modelName")
                    or fields.get("identifier")
                    or fields.get("name")
                    or fields.get("slotName")
                    or fields.get("type")
                    or fields.get("slot")
                    or f"Item {index + 1}"
                )
                entries.append(VehicleTuningEntry(
                    collection_name, index, summary,
                    fields.get("type", fields.get("slot", "")), fields,
                ))

        referenced_models = {
            entry.fields.get("modelName", "").casefold()
            for entry in entries if entry.fields.get("modelName")
        }
        for entry in entries:
            if not entry.fields.get("linkedModels"):
                continue
            referenced_models.update(
                value.strip().casefold()
                for value in entry.fields["linkedModels"].split(",") if value.strip()
            )
        assets = tuple(
            VehicleTuningAsset(
                path=entry.path,
                name=PurePosixPath(entry.path).stem,
                kind="Model" if entry.suffix == ".yft" else "Texture dictionary",
                referenced=(
                    PurePosixPath(entry.path).stem.casefold() in referenced_models
                    or PurePosixPath(entry.path).stem.casefold() == model.casefold()
                ),
            )
            for entry in scan.entries if entry.suffix in {".yft", ".ytd"}
        )
        findings = self._tuning_findings(
            model, kit, entries, assets, scan,
        )
        return VehicleTuningBuilderValues(
            model=model, kit_name=kit.name, kit_id=kit.kit_id,
            kit_type=kit.kit_type, source=kit.source,
            entries=tuple(entries), assets=assets, findings=findings,
        )

    def add_tuning_entry(
        self, model: str, kit_name: str, collection: str,
        values: dict[str, str], *, duplicate_index: int | None = None,
    ) -> VehicleAuthoringResult:
        """Add or duplicate a structured tuning entry transactionally."""
        self._validate_collection(collection)
        scan = AddonPackageInspector().inspect(self.source)
        builder = self.tuning_builder(model, kit_name, _scan=scan)
        tree = self._read_tree(builder.source)
        kit_item = self._find_item(tree, "Kits", "kitName", builder.kit_name)
        container = _direct_child(kit_item, collection)
        if container is None:
            container = etree.SubElement(kit_item, collection)
        items = [
            item for item in container
            if isinstance(item.tag, str) and _local_name(item) == "Item"
        ]
        if duplicate_index is not None:
            if duplicate_index < 0 or duplicate_index >= len(items):
                raise ValueError(f"Tuning entry index is out of range: {duplicate_index}")
            item = deepcopy(items[duplicate_index])
            container.append(item)
            self._apply_tuning_updates(item, collection, values)
            action = "duplicate"
        else:
            item = etree.SubElement(container, "Item")
            initial: dict[str, str] = {}
            for field, (_kind, required, default) in _TUNING_SCHEMAS[collection].items():
                if field in values:
                    initial[field] = values[field]
                elif default or required:
                    initial[field] = default
            self._apply_tuning_updates(item, collection, initial, creation=True)
            action = "add"
        self._validate_tuning_entry(item, collection, model, _scan=scan)
        fields = self._tuning_entry_fields(item)
        changes = ({
            "field": f"tuning.{builder.kit_name}.{collection}",
            "before": "", "after": json.dumps(fields, sort_keys=True),
            "action": action,
        },)
        return self._commit_tuning_tree(
            model, builder.source, tree, changes,
            before_builder=builder, before_scan=scan,
        )

    def update_tuning_entry(
        self, model: str, kit_name: str, collection: str, index: int,
        updates: dict[str, str],
    ) -> VehicleAuthoringResult:
        """Update scalar or array fields on one existing tuning entry."""
        self._validate_collection(collection)
        scan = AddonPackageInspector().inspect(self.source)
        builder = self.tuning_builder(model, kit_name, _scan=scan)
        tree = self._read_tree(builder.source)
        kit_item = self._find_item(tree, "Kits", "kitName", builder.kit_name)
        item = self._tuning_item(kit_item, collection, index)
        before = self._tuning_entry_fields(item)
        self._apply_tuning_updates(item, collection, updates)
        self._validate_tuning_entry(item, collection, model, _scan=scan)
        after = self._tuning_entry_fields(item)
        if before == after:
            raise ValueError("Tuning entry update contains no changed values")
        changes = ({
            "field": f"tuning.{builder.kit_name}.{collection}[{index}]",
            "before": json.dumps(before, sort_keys=True),
            "after": json.dumps(after, sort_keys=True),
            "action": "update",
        },)
        return self._commit_tuning_tree(
            model, builder.source, tree, changes,
            before_builder=builder, before_scan=scan,
        )

    def remove_tuning_entry(
        self, model: str, kit_name: str, collection: str, index: int,
    ) -> VehicleAuthoringResult:
        """Remove one tuning entry while retaining a complete undo snapshot."""
        self._validate_collection(collection)
        scan = AddonPackageInspector().inspect(self.source)
        builder = self.tuning_builder(model, kit_name, _scan=scan)
        tree = self._read_tree(builder.source)
        kit_item = self._find_item(tree, "Kits", "kitName", builder.kit_name)
        container = _direct_child(kit_item, collection)
        if container is None:
            raise ValueError(f"Tuning collection does not exist: {collection}")
        item = self._tuning_item(kit_item, collection, index)
        before = self._tuning_entry_fields(item)
        container.remove(item)
        changes = ({
            "field": f"tuning.{builder.kit_name}.{collection}[{index}]",
            "before": json.dumps(before, sort_keys=True), "after": "",
            "action": "remove",
        },)
        return self._commit_tuning_tree(
            model, builder.source, tree, changes,
            before_builder=builder, before_scan=scan,
        )

    def move_tuning_entry(
        self, model: str, kit_name: str, collection: str,
        index: int, new_index: int,
    ) -> VehicleAuthoringResult:
        """Move a tuning option within its category order."""
        self._validate_collection(collection)
        scan = AddonPackageInspector().inspect(self.source)
        builder = self.tuning_builder(model, kit_name, _scan=scan)
        tree = self._read_tree(builder.source)
        kit_item = self._find_item(tree, "Kits", "kitName", builder.kit_name)
        container = _direct_child(kit_item, collection)
        if container is None:
            raise ValueError(f"Tuning collection does not exist: {collection}")
        items = [
            item for item in container
            if isinstance(item.tag, str) and _local_name(item) == "Item"
        ]
        if index < 0 or index >= len(items) or new_index < 0 or new_index >= len(items):
            raise ValueError("Tuning entry move index is out of range")
        if index == new_index:
            raise ValueError("Tuning entry is already at the requested position")
        item = items[index]
        container.remove(item)
        item_positions = [
            position for position, child in enumerate(container)
            if isinstance(child.tag, str) and _local_name(child) == "Item"
        ]
        insertion = item_positions[new_index] if new_index < len(item_positions) else len(container)
        container.insert(insertion, item)
        changes = ({
            "field": f"tuning.{builder.kit_name}.{collection}",
            "before": str(index), "after": str(new_index), "action": "move",
        },)
        return self._commit_tuning_tree(
            model, builder.source, tree, changes,
            before_builder=builder, before_scan=scan,
        )

    def update_light_profile(
        self, model: str, profile_id: str, updates: dict[str, str],
    ) -> VehicleAuthoringResult:
        """Edit scalar values in one existing carcols light profile."""
        before_scan, before_project = self._scan_project()
        appearance = self.appearance(model, _scan=before_scan)
        matches = [
            item for item in appearance.light_profiles
            if item.profile_id.casefold() == str(profile_id).casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Light profile was not found uniquely: {profile_id}")
        current = matches[0]
        unknown = sorted(set(updates) - set(current.values))
        if unknown or "id" in updates:
            raise ValueError(
                "Unsupported light-profile fields: " + ", ".join(unknown or ["id"])
            )
        tree = self._read_tree(current.source)
        item = self._find_item(tree, "Lights", "id", current.profile_id)
        elements = _scalar_element_map(item)
        changes: list[dict[str, str]] = []
        for key, raw in updates.items():
            value = self._validate_profile_scalar(key, str(raw).strip(), elements[key])
            before = _element_value(elements[key])
            if value == before:
                continue
            if "value" in elements[key].attrib:
                elements[key].set("value", value)
                elements[key].text = None
            else:
                elements[key].text = value
            changes.append({
                "field": f"light.{current.profile_id}.{key}",
                "before": before, "after": value,
            })
        if not changes:
            raise ValueError("Light-profile update contains no changed values")
        trees = {current.source: tree}
        history = self._new_history(model, trees, tuple(changes))
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            after_project = self.inspect()
            self._reject_new_findings(before_project, after_project, model)
            revision = self._finish_revision(history, after_project)
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            self.root, revision, model, tuple(changes), history, after_project,
        )

    def migrate_identity(
        self, model: str, *, new_model: str | None = None,
        new_handling: str | None = None,
    ) -> VehicleAuthoringResult:
        """Rename a model/handling identity and every owned reference atomically."""
        scan, before_project = self._scan_project()
        current = before_project.model(model)
        target_model = self._validate_identifier(new_model or model, "model")
        target_handling = self._validate_identifier(
            new_handling or current.handling_id, "handling",
        )
        if (
            target_model.casefold() == model.casefold()
            and target_handling.casefold() == current.handling_id.casefold()
        ):
            raise ValueError("Identity migration contains no changed values")
        if target_model.casefold() != model.casefold() and any(
            item.model.casefold() == target_model.casefold()
            for item in before_project.models
        ):
            raise ValueError(f"Vehicle model identity already exists: {target_model}")
        handling_users = [
            item.model_name for item in scan.vehicles
            if item.handling_id.casefold() == current.handling_id.casefold()
        ]
        if target_handling.casefold() != current.handling_id.casefold() and len(handling_users) != 1:
            raise ValueError(
                "Handling identity is shared by multiple vehicles and cannot be renamed "
                "implicitly: " + ", ".join(handling_users)
            )
        if target_handling.casefold() != current.handling_id.casefold() and any(
            item.name.casefold() == target_handling.casefold() for item in scan.handlings
        ):
            raise ValueError(f"Handling identity already exists: {target_handling}")

        metadata_sources = sorted({
            item.source for item in (*scan.vehicles, *scan.handlings, *scan.variations, *scan.kits)
        }, key=str.casefold)
        trees = {source: self._read_tree(source) for source in metadata_sources}
        replacements = 0
        for tree in trees.values():
            for element in tree.getroot().iter():
                if not isinstance(element.tag, str):
                    continue
                name = _local_name(element)
                value = _element_value(element)
                if (
                    name in _IDENTITY_MODEL_ELEMENTS
                    and value.casefold() == model.casefold()
                    and target_model.casefold() != model.casefold()
                ):
                    if "value" in element.attrib:
                        element.set("value", target_model)
                    else:
                        element.text = target_model
                    replacements += 1
                elif (
                    name in _IDENTITY_HANDLING_ELEMENTS
                    and value.casefold() == current.handling_id.casefold()
                    and target_handling.casefold() != current.handling_id.casefold()
                ):
                    if "value" in element.attrib:
                        element.set("value", target_handling)
                    else:
                        element.text = target_handling
                    replacements += 1
        renames: list[dict[str, str]] = []
        if target_model.casefold() != model.casefold():
            for entry in scan.entries:
                member = PurePosixPath(entry.path)
                if member.suffix.casefold() not in _STREAMED_IDENTITY_SUFFIXES:
                    continue
                stem = member.stem
                suffix = "_hi" if stem.casefold() == f"{model}_hi".casefold() else ""
                if stem.casefold() not in {model.casefold(), f"{model}_hi".casefold()}:
                    continue
                target = member.with_name(f"{target_model}{suffix}{member.suffix}")
                destination = self._destination(target.as_posix())
                if destination.exists() or destination.is_symlink():
                    raise ValueError(f"Identity migration destination exists: {target}")
                renames.append({"before": entry.path, "after": target.as_posix()})
        if not replacements:
            raise ValueError("Identity migration found no metadata references to update")
        changes = tuple(filter(None, (
            ({"field": "identity.modelName", "before": model, "after": target_model}
             if target_model.casefold() != model.casefold() else None),
            ({"field": "identity.handlingId", "before": current.handling_id,
              "after": target_handling}
             if target_handling.casefold() != current.handling_id.casefold() else None),
        )))
        history = self._new_history(
            model, trees, changes, extra_files=tuple(item["before"] for item in renames),
            operation="vehicle_identity_migration", renames=tuple(renames),
        )
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            for rename in renames:
                self._member(rename["before"]).replace(
                    self._destination(rename["after"])
                )
            after_project = self.inspect()
            migrated = after_project.model(target_model)
            if migrated.handling_id.casefold() != target_handling.casefold():
                raise RuntimeError("Migrated handling identity did not round-trip")
            if after_project.error_count > before_project.error_count:
                raise ValueError("Identity migration introduced package validation errors")
            revision = self._finish_revision(history, after_project)
            self.manifest["models"] = [item.model for item in after_project.models]
            self._write_manifest()
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            self.root, revision, target_model, changes, history, after_project,
        )

    def undo(self) -> VehicleAuthoringResult:
        history_root = self.root / "history"
        candidates = sorted(
            (
                path for path in history_root.iterdir()
                if path.is_dir() and not path.is_symlink()
                and (path / "edit.json").is_file()
                and not path.name.endswith(".undone")
                and not path.name.endswith(".undo-recovery")
                and not path.name.endswith(".redo")
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        if not candidates:
            raise ValueError("Vehicle authoring workspace has no edit to undo")
        history = candidates[0]
        self._verify_post_edit_state(history)
        record = self._history_record(history)
        model = str(record.get("model", ""))
        recovery = self._snapshot_current_for_undo(history, model)
        previous_manifest = deepcopy(self.manifest)
        next_revision = self.revision + 1
        undone = history.with_name(f"{history.name}.undone")
        redo = history.with_name(f"{history.name}.redo")
        try:
            self._restore_history(history)
            project = self.inspect()
            revision = next_revision
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["models"] = [item.model for item in project.models]
            history.rename(undone)
            self._write_manifest()
        except Exception:
            self.manifest = previous_manifest
            if undone.exists() and not history.exists():
                undone.rename(history)
            self._restore_history(recovery)
            shutil.rmtree(recovery, ignore_errors=True)
            raise
        recovery.rename(redo)
        return VehicleAuthoringResult(
            workspace=self.root,
            revision=revision,
            model=model,
            changes=tuple(record.get("changes", ())),
            history=undone,
            project=project,
        )

    def axle_handling_evidence(self, model: str) -> dict[str, str]:
        """Return the two non-editable handling fields axle authoring may touch.

        Keeping this read-only evidence separate from ``values`` avoids exposing
        ``strHandlingFlags`` as a generic free-form editor field while still
        allowing the Axle Configurator to preview its exact transaction.
        """
        scan, project = self._scan_project()
        project_model = project.model(model)
        matches = [
            item for item in scan.handlings
            if item.name.casefold() == project_model.handling_id.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Handling record was not found uniquely: {project_model.handling_id}"
            )
        tree = self._read_tree(matches[0].source)
        item = self._handling_item(tree, project_model.handling_id)
        return {
            "strHandlingFlags": _element_value(
                _direct_child(item, "strHandlingFlags")
            ) or "00000000",
            "fDriveBiasFront": _element_value(
                _direct_child(item, "fDriveBiasFront")
            ),
        }

    def redo(self) -> VehicleAuthoringResult:
        """Reapply the most recently undone guarded vehicle edit."""
        history_root = self.root / "history"
        states: dict[str, tuple[Path | None, Path | None]] = {}
        for path in history_root.iterdir():
            if not path.is_dir() or path.is_symlink() or not (path / "edit.json").is_file():
                continue
            if path.name.endswith(".redo") or path.name.endswith(".undo-recovery"):
                continue
            base = path.name.removesuffix(".undone")
            active, undone = states.get(base, (None, None))
            states[base] = (
                path if not path.name.endswith(".undone") else active,
                path if path.name.endswith(".undone") else undone,
            )
        if not states:
            raise ValueError("Vehicle authoring workspace has no edit to redo")
        latest = sorted(states, reverse=True)[0]
        active, undone = states[latest]
        redo = history_root / f"{latest}.redo"
        if active is not None or undone is None or not redo.is_dir() or redo.is_symlink():
            raise ValueError("Vehicle authoring workspace has no edit to redo")
        self._verify_pre_edit_state(undone)
        record = self._history_record(undone)
        model = str(record.get("model", ""))
        previous_manifest = deepcopy(self.manifest)
        next_revision = self.revision + 1
        restored = history_root / latest
        try:
            self._restore_history(redo)
            project = self.inspect()
            revision = next_revision
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["models"] = [item.model for item in project.models]
            undone.rename(restored)
            self._write_manifest()
        except Exception:
            self.manifest = previous_manifest
            if restored.exists() and not undone.exists():
                restored.rename(undone)
            self._restore_history(undone)
            raise
        shutil.rmtree(redo)
        return VehicleAuthoringResult(
            workspace=self.root,
            revision=revision,
            model=model,
            changes=tuple(record.get("changes", ())),
            history=restored,
            project=project,
        )

    def _read_tree(self, relative: str) -> etree._ElementTree:
        path = self._member(relative)
        size = path.stat().st_size
        if not 0 < size <= MAX_AUTHORING_XML_BYTES:
            raise ValueError(f"Authoring XML is empty or exceeds 16 MiB: {relative}")
        try:
            tree = etree.parse(str(path), _safe_parser())
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ValueError(f"Invalid authoring XML {relative}: {exc}") from exc
        if tree.docinfo.doctype:
            raise ValueError(f"Authoring XML contains a prohibited document type: {relative}")
        return tree

    def _member(self, relative: str) -> Path:
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"Unsafe vehicle authoring member: {relative}")
        candidate = (self.source / Path(*path.parts)).resolve(strict=True)
        if not candidate.is_relative_to(self.source) or candidate.is_symlink():
            raise ValueError(f"Unsafe vehicle authoring member: {relative}")
        return candidate

    def _destination(self, relative: str) -> Path:
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"Unsafe vehicle authoring destination: {relative}")
        candidate = (self.source / Path(*path.parts)).resolve(strict=False)
        if not candidate.is_relative_to(self.source):
            raise ValueError(f"Unsafe vehicle authoring destination: {relative}")
        return candidate

    @staticmethod
    def _find_item(
        tree: etree._ElementTree, container_name: str,
        identity_name: str, identity: str,
    ) -> etree._Element:
        matches = []
        for container in tree.getroot().iter():
            if not isinstance(container.tag, str) or _local_name(container) != container_name:
                continue
            for item in container:
                if not isinstance(item.tag, str) or _local_name(item) != "Item":
                    continue
                value = _element_value(_direct_child(item, identity_name))
                if value.casefold() == identity.casefold():
                    matches.append(item)
        if len(matches) != 1:
            raise ValueError(
                f"Metadata record was not found uniquely: {identity_name}={identity}"
            )
        return matches[0]

    @classmethod
    def _vehicle_item(cls, tree: etree._ElementTree, model: str) -> etree._Element:
        return cls._find_item(tree, "InitDatas", "modelName", model)

    @classmethod
    def _handling_item(cls, tree: etree._ElementTree, name: str) -> etree._Element:
        return cls._find_item(tree, "HandlingData", "handlingName", name)

    @classmethod
    def _variation_item(cls, tree: etree._ElementTree, model: str) -> etree._Element:
        return cls._find_item(tree, "variationData", "modelName", model)

    @staticmethod
    def _validate_value(key: str, value: str) -> str:
        if any(ord(character) < 32 for character in value) or len(value) > 256:
            raise ValueError(f"Vehicle authoring value is unsafe: {key}")
        if key.startswith("handling."):
            if not value:
                raise ValueError(f"Handling value may not be empty: {key}")
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(f"Handling value must be numeric: {key}") from exc
            if not math.isfinite(number):
                raise ValueError(f"Handling value must be finite: {key}")
            if key == "handling.nInitialDriveGears" and not (
                number.is_integer() and 1 <= number <= 16
            ):
                raise ValueError("Initial drive gears must be an integer from 1 through 16")
            if key == "handling.nInitialDriveGears":
                return str(int(number))
            return value
        if key in {"variation.lightSettings", "variation.sirenSettings"}:
            try:
                number = int(value, 10)
            except ValueError as exc:
                label = key.removeprefix("variation.").replace("Settings", " settings")
                raise ValueError(f"{label.capitalize()} must be an integer") from exc
            if not 0 <= number <= 65535:
                label = key.removeprefix("variation.").replace("Settings", " settings")
                raise ValueError(f"{label.capitalize()} must be between 0 and 65535")
            return str(number)
        if key == "variation.kits":
            return ", ".join(VehicleAuthoringWorkspace._kit_values(value))
        if not value:
            if key == "vehicle.audioNameHash":
                return ""
            raise ValueError(f"Vehicle value may not be empty: {key}")
        if key not in {"vehicle.gameName", "vehicle.vehicleMakeName"} and not _IDENTIFIER.fullmatch(value):
            raise ValueError(f"Vehicle identifier contains unsupported characters: {key}")
        return value

    @staticmethod
    def _kit_values(value: str) -> tuple[str, ...]:
        kits = tuple(dict.fromkeys(
            item.strip() for item in value.split(",") if item.strip()
        ))
        if any(not _IDENTIFIER.fullmatch(item) for item in kits):
            raise ValueError("Tuning-kit names may contain only letters, numbers, and underscores")
        return kits

    @staticmethod
    def _validate_identifier(value: Any, label: str) -> str:
        text = str(value).strip()
        if not _IDENTIFIER.fullmatch(text):
            raise ValueError(
                f"{label.capitalize()} may contain only letters, numbers, and underscores"
            )
        return text

    @staticmethod
    def _validate_collection(collection: str) -> str:
        if collection not in TUNING_COLLECTIONS:
            raise ValueError(
                "Unsupported tuning collection: " + str(collection)
            )
        return collection

    @staticmethod
    def _resolve_tuning_kit(
        appearance: VehicleAppearanceValues, kit_name: str | None,
    ) -> VehicleTuningKitSummary:
        linked = {value.casefold() for value in appearance.kits}
        candidates = [
            item for item in appearance.available_kits
            if item.name.casefold() in linked or item.kit_id.casefold() in linked
        ]
        if kit_name is not None:
            requested = kit_name.strip().casefold()
            candidates = [
                item for item in candidates
                if item.name.casefold() == requested
                or item.kit_id.casefold() == requested
            ]
        if len(candidates) != 1:
            requested = kit_name or "linked vehicle kit"
            raise ValueError(f"Tuning kit was not found uniquely: {requested}")
        return candidates[0]

    @staticmethod
    def _array_values(element: etree._Element) -> tuple[str, ...]:
        items = [
            _element_value(child) for child in element
            if isinstance(child.tag, str) and _local_name(child) == "Item"
            and _element_value(child)
        ]
        if items:
            return tuple(items)
        return tuple(
            token for token in re.split(r"[\s,]+", element.text or "") if token
        )

    @classmethod
    def _tuning_entry_fields(cls, item: etree._Element) -> dict[str, str]:
        fields: dict[str, str] = {}
        for child in item:
            if not isinstance(child.tag, str):
                continue
            name = _local_name(child)
            if name in TUNING_ARRAY_FIELDS:
                fields[name] = ", ".join(cls._array_values(child))
                continue
            descendants = [entry for entry in child if isinstance(entry.tag, str)]
            if not descendants:
                fields[name] = _element_value(child)
                continue
            for path, value in _scalar_descendants(child).items():
                fields[f"{name}.{path}"] = value
        return fields

    @staticmethod
    def _tuning_item(
        kit_item: etree._Element, collection: str, index: int,
    ) -> etree._Element:
        container = _direct_child(kit_item, collection)
        items = [
            item for item in (container if container is not None else ())
            if isinstance(item.tag, str) and _local_name(item) == "Item"
        ]
        if index < 0 or index >= len(items):
            raise ValueError(f"Tuning entry index is out of range: {index}")
        return items[index]

    @staticmethod
    def _normalize_tuning_field(
        collection: str, field: str, raw: Any,
        element: etree._Element | None = None,
    ) -> str:
        value = str(raw).strip()
        definition = _TUNING_SCHEMAS[collection].get(field)
        if definition is None:
            if element is None:
                raise ValueError(f"Unsupported tuning field: {field}")
            return VehicleAuthoringWorkspace._validate_profile_scalar(
                field, value, element,
            )
        kind, required, _default = definition
        if not value:
            if required:
                raise ValueError(f"Tuning field may not be empty: {field}")
            return ""
        if len(value) > 4096 or any(ord(character) < 32 for character in value):
            raise ValueError(f"Tuning field is unsafe: {field}")
        if kind == "identifier":
            return VehicleAuthoringWorkspace._validate_identifier(value, field)
        if kind == "identifier_array":
            values = tuple(dict.fromkeys(
                token for token in re.split(r"[\s,]+", value) if token
            ))
            for token in values:
                VehicleAuthoringWorkspace._validate_identifier(token, field)
            return ", ".join(values)
        if kind == "integer_array":
            values: list[str] = []
            for token in (item for item in re.split(r"[\s,]+", value) if item):
                try:
                    number = int(token, 10)
                except ValueError as exc:
                    raise ValueError(f"Tuning array must contain integers: {field}") from exc
                if not -(2 ** 31) <= number < 2 ** 31:
                    raise ValueError(f"Tuning array integer is out of range: {field}")
                values.append(str(number))
            return ", ".join(values)
        if kind == "vmt":
            normalized = value.upper()
            if normalized not in VMT_TYPES:
                raise ValueError(f"Unknown vehicle modification type: {value}")
            return normalized
        if kind == "boolean":
            normalized = value.casefold()
            if normalized not in {"true", "false", "1", "0"}:
                raise ValueError(f"Tuning field must be true or false: {field}")
            return "true" if normalized in {"true", "1"} else "false"
        if kind == "integer":
            try:
                number = int(value, 10)
            except ValueError as exc:
                raise ValueError(f"Tuning field must be an integer: {field}") from exc
            if not -(2 ** 31) <= number < 2 ** 31:
                raise ValueError(f"Tuning field integer is out of range: {field}")
            return str(number)
        if kind == "float":
            try:
                number = float(value)
            except ValueError as exc:
                raise ValueError(f"Tuning field must be numeric: {field}") from exc
            if not math.isfinite(number) or abs(number) > 1_000_000_000:
                raise ValueError(f"Tuning field must be a finite number: {field}")
            return value
        raise ValueError(f"Unsupported tuning field kind: {kind}")

    @staticmethod
    def _insert_tuning_element(
        item: etree._Element, collection: str, field: str,
    ) -> etree._Element:
        element = etree.Element(field)
        order = tuple(_TUNING_SCHEMAS[collection])
        requested = order.index(field)
        for position, child in enumerate(item):
            if not isinstance(child.tag, str):
                continue
            name = _local_name(child)
            if name in order and order.index(name) > requested:
                item.insert(position, element)
                return element
        item.append(element)
        return element

    @classmethod
    def _set_tuning_field(
        cls, item: etree._Element, collection: str, field: str, value: str,
    ) -> None:
        element = _direct_child(item, field)
        if field in TUNING_ARRAY_FIELDS:
            if not value:
                if element is not None:
                    item.remove(element)
                return
            if element is None:
                element = cls._insert_tuning_element(item, collection, field)
            values = tuple(token.strip() for token in value.split(",") if token.strip())
            had_items = any(
                isinstance(child.tag, str) and _local_name(child) == "Item"
                for child in element
            )
            textual = bool(element.get("content")) and not had_items
            for child in tuple(element):
                element.remove(child)
            if textual:
                element.text = "\n" + "\n".join(values) + "\n"
            else:
                element.text = None
                for token in values:
                    etree.SubElement(element, "Item").text = token
            return
        if field in _TUNING_SCHEMAS[collection]:
            if not value:
                if element is not None:
                    item.remove(element)
                return
            if element is None:
                element = cls._insert_tuning_element(item, collection, field)
            if field in _ATTRIBUTE_TUNING_FIELDS:
                element.set("value", value)
                element.text = None
            else:
                element.attrib.pop("value", None)
                element.text = value
            return
        elements = _scalar_element_map(item)
        if field not in elements:
            raise ValueError(f"Unsupported tuning field: {field}")
        target = elements[field]
        if "value" in target.attrib:
            target.set("value", value)
            target.text = None
        else:
            target.text = value

    @classmethod
    def _apply_tuning_updates(
        cls, item: etree._Element, collection: str,
        updates: dict[str, str], *, creation: bool = False,
    ) -> None:
        if not isinstance(updates, dict) or not updates:
            if creation:
                raise ValueError("A new tuning entry requires field values")
            return
        existing = cls._tuning_entry_fields(item)
        elements = _scalar_element_map(item)
        allowed = set(_TUNING_SCHEMAS[collection]) | set(existing)
        unknown = sorted(set(updates) - allowed)
        if unknown:
            raise ValueError("Unsupported tuning fields: " + ", ".join(unknown))
        normalized = {
            field: cls._normalize_tuning_field(
                collection, field, raw, elements.get(field),
            )
            for field, raw in updates.items()
        }
        ordered = tuple(_TUNING_SCHEMAS[collection])
        for field in sorted(
            normalized, key=lambda name: (
                ordered.index(name) if name in ordered else len(ordered), name,
            ),
        ):
            cls._set_tuning_field(item, collection, field, normalized[field])
        fields = cls._tuning_entry_fields(item)
        for field, (_kind, required, _default) in _TUNING_SCHEMAS[collection].items():
            if required and not fields.get(field):
                raise ValueError(f"Tuning field may not be empty: {field}")

    def _validate_tuning_entry(
        self, item: etree._Element, collection: str, model: str, *,
        _scan: PackageScan | None = None,
    ) -> None:
        fields = self._tuning_entry_fields(item)
        for field, (_kind, required, _default) in _TUNING_SCHEMAS[collection].items():
            if required and not fields.get(field):
                raise ValueError(f"Tuning field may not be empty: {field}")
        if fields.get("minIntVars") and fields.get("maxIntVars"):
            minimums = [value for value in fields["minIntVars"].split(",") if value.strip()]
            maximums = [value for value in fields["maxIntVars"].split(",") if value.strip()]
            if len(minimums) != len(maximums):
                raise ValueError("Minimum and maximum tuning arrays must have equal lengths")
        part = fields.get("modelName", "")
        if collection in {"visibleMods", "linkMods"} and part:
            scan = _scan or AddonPackageInspector().inspect(self.source)
            models = {
                PurePosixPath(entry.path).stem.casefold()
                for entry in scan.entries if entry.suffix == ".yft"
            }
            if models and part.casefold() not in models:
                raise ValueError(
                    f"Tuning model asset was not found for {part}. Add its YFT first."
                )

    @staticmethod
    def _tuning_findings(
        model: str, kit: VehicleTuningKitSummary,
        entries: list[VehicleTuningEntry], assets: tuple[VehicleTuningAsset, ...],
        scan: PackageScan,
    ) -> tuple[VehicleTuningFinding, ...]:
        findings: list[VehicleTuningFinding] = []
        kit_names = [
            record for record in scan.kits
            if record.name.casefold() == kit.name.casefold()
            or (kit.kit_id and record.kit_id.casefold() == kit.kit_id.casefold())
        ]
        if len(kit_names) > 1:
            findings.append(VehicleTuningFinding(
                "error", "duplicate_tuning_kit",
                f"Tuning kit name or ID collides with {len(kit_names)} records.",
            ))
        model_assets = {
            asset.name.casefold() for asset in assets if asset.kind == "Model"
        }
        linked_entries = {
            entry.fields.get("modelName", "").casefold()
            for entry in entries if entry.collection == "linkMods"
        }
        seen: dict[tuple[str, str], VehicleTuningEntry] = {}
        for entry in entries:
            fields = entry.fields
            identity = ""
            if entry.collection in {"visibleMods", "linkMods"}:
                identity = fields.get("modelName", "")
            elif entry.collection == "statMods":
                identity = fields.get("identifier") or \
                    f"{fields.get('type', '')}:{fields.get('modifier', '')}"
            elif entry.collection == "slotNames":
                identity = fields.get("slot", "")
            key = (entry.collection, identity.casefold())
            if identity and key in seen:
                findings.append(VehicleTuningFinding(
                    "error", "duplicate_tuning_entry",
                    f"{entry.collection} contains duplicate identity {identity}.",
                    entry.key,
                ))
            elif identity:
                seen[key] = entry
            part = fields.get("modelName", "")
            if part and model_assets and part.casefold() not in model_assets:
                findings.append(VehicleTuningFinding(
                    "error", "missing_tuning_model",
                    f"Model asset {part}.yft is not present in the authoring package.",
                    entry.key,
                ))
            linked = tuple(
                token.strip() for token in fields.get("linkedModels", "").split(",")
                if token.strip()
            )
            for companion in linked:
                if model_assets and companion.casefold() not in model_assets:
                    findings.append(VehicleTuningFinding(
                        "error", "missing_linked_model",
                        f"Linked model asset {companion}.yft is not present.", entry.key,
                    ))
                if companion.casefold() not in linked_entries:
                    findings.append(VehicleTuningFinding(
                        "warning", "unregistered_linked_model",
                        f"Linked model {companion} has no linkMods entry.", entry.key,
                    ))
            if fields.get("minIntVars") and fields.get("maxIntVars"):
                minimums = fields["minIntVars"].split(",")
                maximums = fields["maxIntVars"].split(",")
                if len(minimums) != len(maximums):
                    findings.append(VehicleTuningFinding(
                        "error", "tuning_array_length_mismatch",
                        "Minimum and maximum tuning arrays have different lengths.",
                        entry.key,
                    ))
        return tuple(findings)

    def _commit_tuning_tree(
        self, model: str, source: str, tree: etree._ElementTree,
        changes: tuple[dict[str, str], ...], *,
        before_builder: VehicleTuningBuilderValues,
        before_scan: PackageScan,
    ) -> VehicleAuthoringResult:
        before_project = VehicleProjectResolver.inspect_scan(before_scan)
        trees = {source: tree}
        history = self._new_history(
            model, trees, changes, operation="vehicle_tuning_edit",
        )
        previous_manifest = dict(self.manifest)
        try:
            self._commit_trees(trees)
            after_scan, after_project = self._scan_project()
            self._reject_new_findings(before_project, after_project, model)
            after_builder = self.tuning_builder(
                model, before_builder.kit_name, _scan=after_scan,
            )
            before_errors = {
                (item.code, item.entry, item.message)
                for item in before_builder.findings if item.severity == "error"
            }
            new_errors = [
                item for item in after_builder.findings
                if item.severity == "error"
                and (item.code, item.entry, item.message) not in before_errors
            ]
            if new_errors:
                raise ValueError(
                    "Tuning edit introduced validation errors: "
                    + ", ".join(sorted({item.code for item in new_errors}))
                )
            revision = self._finish_revision(history, after_project)
        except Exception:
            self.manifest = previous_manifest
            self._restore_history(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return VehicleAuthoringResult(
            self.root, revision, model, changes, history, after_project,
        )

    @staticmethod
    def _validate_setting(value: Any, label: str) -> str:
        try:
            number = int(str(value).strip(), 10)
        except ValueError as exc:
            raise ValueError(f"{label} settings must be an integer") from exc
        if not 0 <= number <= 65535:
            raise ValueError(f"{label} settings must be between 0 and 65535")
        return str(number)

    @staticmethod
    def _normalize_colors(
        values: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[VehicleColorSet, ...]:
        if len(values) > 256:
            raise ValueError("A vehicle may not define more than 256 color sets")
        colors: list[VehicleColorSet] = []
        for index, raw in enumerate(values, start=1):
            if not isinstance(raw, dict):
                raise ValueError(f"Color set {index} must be an object")
            indices_raw = raw.get("indices")
            liveries_raw = raw.get("liveries", ())
            if not isinstance(indices_raw, (list, tuple)) or not 4 <= len(indices_raw) <= 8:
                raise ValueError(f"Color set {index} requires 4 through 8 color indices")
            try:
                indices = tuple(int(value) for value in indices_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Color set {index} contains a non-integer index") from exc
            if any(value < 0 or value > 255 for value in indices):
                raise ValueError(f"Color set {index} indices must be from 0 through 255")
            if not isinstance(liveries_raw, (list, tuple)) or len(liveries_raw) > 64:
                raise ValueError(f"Color set {index} liveries must be a list of at most 64 flags")
            liveries: list[bool] = []
            for flag in liveries_raw:
                if isinstance(flag, bool):
                    liveries.append(flag)
                elif isinstance(flag, (int, str)) and str(flag).casefold() in {
                    "0", "1", "true", "false",
                }:
                    liveries.append(str(flag).casefold() in {"1", "true"})
                else:
                    raise ValueError(f"Color set {index} contains an invalid livery flag")
            colors.append(VehicleColorSet(indices, tuple(liveries)))
        return tuple(colors)

    @staticmethod
    def _validate_profile_scalar(
        key: str, value: str, element: etree._Element,
    ) -> str:
        if not value or len(value) > 256 or any(ord(character) < 32 for character in value):
            raise ValueError(f"Light-profile value is empty or unsafe: {key}")
        current = _element_value(element)
        if current.casefold() in {"true", "false"}:
            if value.casefold() not in {"true", "false"}:
                raise ValueError(f"Light-profile field must be true or false: {key}")
            return value.casefold()
        if re.fullmatch(r"0x[0-9A-Fa-f]{8}", current):
            if not re.fullmatch(r"0x[0-9A-Fa-f]{8}", value):
                raise ValueError(f"Light-profile color must be 0x plus eight hex digits: {key}")
            return "0x" + value[2:].upper()
        try:
            current_number = float(current)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", value):
                raise ValueError(f"Light-profile text contains unsupported characters: {key}")
            return value
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"Light-profile field must be numeric: {key}") from exc
        if not math.isfinite(number):
            raise ValueError(f"Light-profile field must be finite: {key}")
        if current_number.is_integer() and re.fullmatch(r"[-+]?\d+", current):
            if not number.is_integer():
                raise ValueError(f"Light-profile field must be an integer: {key}")
            return str(int(number))
        return value

    def _finish_revision(self, history: Path, project: VehicleProject) -> int:
        self._record_post_edit_state(history)
        revision = self.revision + 1
        self.manifest["revision"] = revision
        self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
        (history / "validation.json").write_text(
            json.dumps(project.to_dict(), indent=2) + "\n", encoding="utf-8",
        )
        self._write_manifest()
        return revision

    def _new_history(
        self,
        model: str,
        trees: dict[str, etree._ElementTree],
        changes: tuple[dict[str, str], ...],
        *,
        extra_files: tuple[str, ...] = (),
        operation: str = "vehicle_metadata_edit",
        renames: tuple[dict[str, str], ...] = (),
        snapshot_manifest: bool = False,
    ) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        history = self.root / "history" / f"{stamp}-edit"
        history.mkdir()
        files = history / "files"
        files.mkdir()
        paths = tuple(dict.fromkeys((*trees, *extra_files)))
        hashes: dict[str, str] = {}
        try:
            for relative in sorted(paths, key=str.casefold):
                source = self._member(relative)
                target = files / Path(*PurePosixPath(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                hashes[relative] = _sha256(target)
        except Exception:
            shutil.rmtree(history, ignore_errors=True)
            raise
        record = {
            "operation": operation,
            "model": model,
            "revision_before": self.revision,
            "files": sorted(paths, key=str.casefold),
            "sha256": hashes,
            "changes": list(changes),
            "renames": list(renames),
        }
        if snapshot_manifest:
            record["manifest_before"] = deepcopy(self.manifest)
        (history / "edit.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8",
        )
        return history

    def _snapshot_current_for_undo(self, original: Path, model: str) -> Path:
        record = self._history_record(original)
        renames = tuple(
            item for item in record.get("renames", ())
            if isinstance(item, dict)
            and isinstance(item.get("before"), str)
            and isinstance(item.get("after"), str)
        )
        current_names = {
            item["before"]: item["after"] for item in renames
        }
        paths = tuple(
            current_names.get(str(item), str(item)) for item in record.get("files", ())
        )
        recovery_renames = tuple(
            {"before": item["after"], "after": item["before"]}
            for item in renames
        )
        snapshot = self._new_history(
            model, {}, (), extra_files=paths, operation="vehicle_undo_recovery",
            renames=recovery_renames,
            snapshot_manifest=isinstance(record.get("manifest_before"), dict),
        )
        recovery = snapshot.with_name(f"{snapshot.name}.undo-recovery")
        snapshot.rename(recovery)
        return recovery

    def _restore_history(self, history: Path) -> None:
        record = self._history_record(history)
        files = record.get("files")
        hashes = record.get("sha256")
        if not isinstance(files, list) or not all(
            isinstance(item, str) for item in files
        ):
            raise ValueError("Vehicle authoring history contains invalid files")
        if not isinstance(hashes, dict) or set(hashes) != set(files):
            raise ValueError("Vehicle authoring history has invalid backup hashes")
        backups: dict[str, Path] = {}
        for relative in files:
            # Validate the destination boundary before deriving the backup path.
            self._destination(relative)
            backup = history / "files" / Path(*PurePosixPath(relative).parts)
            expected = hashes.get(relative)
            if (
                not backup.is_file() or backup.is_symlink()
                or not isinstance(expected, str) or _sha256(backup) != expected
            ):
                raise ValueError(
                    f"Vehicle authoring backup hash is invalid: {relative}"
                )
            backups[relative] = backup
        renames = record.get("renames", ())
        if not isinstance(renames, list):
            raise ValueError("Vehicle authoring history contains invalid renames")
        for rename in reversed(renames):
            if not isinstance(rename, dict):
                raise ValueError("Vehicle authoring history contains an invalid rename")
            before = rename.get("before")
            after = rename.get("after")
            if not isinstance(before, str) or not isinstance(after, str):
                raise ValueError("Vehicle authoring history contains an invalid rename")
            before_path = self._destination(before)
            after_path = self._destination(after)
            if after_path.exists():
                if before_path.exists() or before_path.is_symlink():
                    raise ValueError(f"Vehicle authoring restore collision: {before}")
                after_path.replace(before_path)
            elif not before_path.exists():
                raise ValueError(f"Vehicle authoring renamed member is missing: {after}")
        for relative in files:
            backup = backups[relative]
            destination = self._destination(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(backup, destination)
        manifest_before = record.get("manifest_before")
        if manifest_before is not None:
            if not isinstance(manifest_before, dict):
                raise ValueError("Vehicle authoring history has an invalid manifest snapshot")
            self.manifest = deepcopy(manifest_before)

    def _record_post_edit_state(self, history: Path) -> None:
        record = self._history_record(history)
        files = record.get("files")
        renames = record.get("renames", ())
        if not isinstance(files, list) or not all(
            isinstance(item, str) for item in files
        ):
            raise ValueError("Vehicle authoring history contains invalid files")
        if not isinstance(renames, list):
            raise ValueError("Vehicle authoring history contains invalid renames")
        current_names: dict[str, str] = {}
        for rename in renames:
            if not isinstance(rename, dict):
                raise ValueError("Vehicle authoring history contains an invalid rename")
            before = rename.get("before")
            after = rename.get("after")
            if not isinstance(before, str) or not isinstance(after, str):
                raise ValueError("Vehicle authoring history contains an invalid rename")
            self._destination(before)
            self._destination(after)
            current_names[before] = after
        record["sha256_after"] = {
            relative: {
                "path": current_names.get(relative, relative),
                "sha256": _sha256(
                    self._member(current_names.get(relative, relative))
                ),
            }
            for relative in files
        }
        if isinstance(record.get("manifest_before"), dict):
            record["manifest_after"] = self._history_manifest_state(self.manifest)
        self._write_history_record(history, record)

    def _verify_pre_edit_state(self, history: Path) -> None:
        """Verify undo output before a redo writes its retained post-edit copy."""
        record = self._history_record(history)
        files = record.get("files")
        hashes = record.get("sha256")
        if not isinstance(files, list) or not isinstance(hashes, dict):
            raise ValueError("Vehicle authoring history has invalid pre-edit state")
        for relative in files:
            if not isinstance(relative, str) or not isinstance(hashes.get(relative), str):
                raise ValueError("Vehicle authoring history has invalid pre-edit state")
            try:
                current = _sha256(self._member(relative))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "Vehicle authoring member changed after undo: " + relative
                ) from exc
            if current != hashes[relative]:
                raise ValueError("Vehicle authoring member changed after undo: " + relative)
        manifest_before = record.get("manifest_before")
        if manifest_before is not None:
            if not isinstance(manifest_before, dict):
                raise ValueError("Vehicle authoring history has invalid manifest snapshot")
            if self._history_manifest_state(self.manifest) != self._history_manifest_state(
                manifest_before
            ):
                raise ValueError("Vehicle authoring manifest changed after undo")

    def _verify_post_edit_state(self, history: Path) -> None:
        record = self._history_record(history)
        files = record.get("files")
        state = record.get("sha256_after")
        if not isinstance(files, list) or not all(
            isinstance(item, str) for item in files
        ):
            raise ValueError("Vehicle authoring history contains invalid files")
        if not isinstance(state, dict) or set(state) != set(files):
            raise ValueError(
                "Vehicle authoring history has no verified post-edit state"
            )
        for relative in files:
            descriptor = state.get(relative)
            if not isinstance(descriptor, dict):
                raise ValueError(
                    f"Vehicle authoring post-edit state is invalid: {relative}"
                )
            current_path = descriptor.get("path")
            expected = descriptor.get("sha256")
            if not isinstance(current_path, str) or not isinstance(expected, str):
                raise ValueError(
                    f"Vehicle authoring post-edit state is invalid: {relative}"
                )
            try:
                current = _sha256(self._member(current_path))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "Vehicle authoring member changed after its edit: "
                    + current_path
                ) from exc
            if current != expected:
                raise ValueError(
                    "Vehicle authoring member changed after its edit: "
                    + current_path
                )
        manifest_after = record.get("manifest_after")
        if manifest_after is not None:
            if not isinstance(manifest_after, dict):
                raise ValueError("Vehicle authoring history has invalid post-edit manifest state")
            if self._history_manifest_state(self.manifest) != manifest_after:
                raise ValueError("Vehicle authoring manifest changed after its edit")

    @staticmethod
    def _history_manifest_state(manifest: dict[str, Any]) -> dict[str, Any]:
        state = deepcopy(manifest)
        state.pop("revision", None)
        state.pop("updated_utc", None)
        return state

    def _history_record(self, history: Path) -> dict[str, Any]:
        if history.parent != self.root / "history" or history.is_symlink():
            raise ValueError("Unsafe vehicle authoring history directory")
        try:
            value = json.loads((history / "edit.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid vehicle authoring history: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("Invalid vehicle authoring history")
        return value

    def _write_history_record(self, history: Path, record: dict[str, Any]) -> None:
        self._history_record(history)
        destination = history / "edit.json"
        temporary = destination.with_name(f".{destination.name}.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ValueError(f"Stale vehicle history temporary file exists: {temporary}")
        try:
            temporary.write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _commit_trees(self, trees: dict[str, etree._ElementTree]) -> None:
        staged: dict[str, Path] = {}
        try:
            for relative, tree in trees.items():
                destination = self._member(relative)
                temporary = destination.with_name(f".{destination.name}.authoring.tmp")
                if temporary.exists() or temporary.is_symlink():
                    raise ValueError(f"Stale authoring temporary file exists: {temporary}")
                tree.write(
                    str(temporary), encoding="utf-8", xml_declaration=True,
                    pretty_print=True,
                )
                etree.parse(str(temporary), _safe_parser())
                staged[relative] = temporary
            for relative, temporary in staged.items():
                temporary.replace(self._member(relative))
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _reject_new_findings(
        before: VehicleProject, after: VehicleProject, model: str,
    ) -> None:
        def relevant(project: VehicleProject) -> set[tuple[str, str, str, str]]:
            return {
                (item.severity, item.code, item.model.casefold(), item.message)
                for item in project.findings
                if not item.model or item.model.casefold() == model.casefold()
            }
        added = relevant(after) - relevant(before)
        if added:
            detail = ", ".join(sorted(code for _severity, code, _model, _message in added))
            raise ValueError(
                "Vehicle edit introduced unresolved package relationships: " + detail
            )

    def _write_manifest(self) -> None:
        temporary = self.manifest_path.with_name(".vehicle-authoring.json.tmp")
        temporary.write_text(
            json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8",
        )
        temporary.replace(self.manifest_path)
