"""Data-driven multi-axle behavior prefabs and visual tyre packages.

The catalogs intentionally contain no wheel indices.  An explicit target
resolver maps canonical wheel-bone semantics only after a prefab is applied to
a specific vehicle.  Visual dual tyres remain geometry belonging to an
existing physics wheel and never create a runtime slot.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    CANONICAL_WHEEL_PAIRS,
    EXPORT_FIVEM_RUNTIME,
    EXPORT_MODES,
    PRESET_CUSTOM,
    SHARED_VISUAL_WARNING,
    VISUAL_FRONT,
    VISUAL_SHARED_MIDDLE_REAR,
    AxleAddonGeometry,
    AxleConfiguration,
    AxleFinding,
    BoneLike,
    VehicleAxle,
    detect_axle_configuration,
    joaat_hex,
    retarget_axle_configuration,
    resolve_runtime_wheel_index_map,
    stock_metadata_flags,
    validate_axle_configuration,
)


PREFAB_CATALOG_SCHEMA_VERSION = 1
VISUAL_TYRE_CATALOG_SCHEMA_VERSION = 1
VISUAL_TYRE_SELECTION_SCHEMA_VERSION = 1
PROJECT_PREFAB_CATALOG_SCHEMA_VERSION = 1

CAP_SELECTIVE_STEERING = "selectiveSteering"
CAP_SELECTIVE_DRIVE = "selectiveDrive"
CAP_LIFT_AXLE = "liftAxle"
CAP_TRAILER_STEERING = "trailerSteering"
CAPABILITIES = (
    CAP_SELECTIVE_STEERING,
    CAP_SELECTIVE_DRIVE,
    CAP_LIFT_AXLE,
    CAP_TRAILER_STEERING,
)

PATTERN_TOKENS = ("S", "D", "SD", "T", "RS", "LT", "LS")
STEERING_ROLES = ("none", "front", "rear")
CARRIER_ROLES = ("primary", "drive", "tag", "pusher", "trailer")
TYRE_STYLES = ("single", "dual", "super_single")
TYRE_RULES = (
    "all_singles",
    "dual_driven",
    "dual_tandem",
    "super_single_drive_tag",
    "dual_middle_drive_single_tag",
    "wide_all",
    "dual_non_steering",
    "selected_inner_addon",
)

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_LOCALIZATION_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,127}$")
_NOMINAL_LAYOUT = re.compile(r"^(\d{1,2})x(\d{1,2})$")
_GENERATED_TYRE_PREFIX = "generated/axle-tyres/"
_FINDING_LOCALIZATION_KEYS = {
    "missing_required_bone": "axleValidation.missingRequiredBone",
    "physical_order": "axleValidation.physicalOrder",
    "axle_count_mismatch": "axleValidation.axleCountMismatch",
    "runtime_mapping": "axleValidation.runtimeMapping",
    "missing_target_capability": "axleValidation.missingTargetCapability",
    "shared_visual_template": "axleValidation.sharedVisualTemplate",
    "inner_addon_required": "axleValidation.innerAddonRequired",
    "selected_axles_required": "axleValidation.selectedAxlesRequired",
    "selected_axle_range": "axleValidation.selectedAxleRange",
    "tandem_drive_missing": "axleValidation.tandemDriveMissing",
    "mixed_tag_roles": "axleValidation.mixedTagRoles",
    "visual_runtime_index_change": "axleValidation.visualRuntimeIndexChange",
    "visual_runtime_mapping_change": "axleValidation.visualRuntimeMappingChange",
    "visual_geometry_design_only": "axleValidation.visualGeometryDesignOnly",
    "visual_geometry_unsafe": "axleValidation.visualGeometryUnsafe",
}


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip().casefold()
    if not _SAFE_ID.fullmatch(text):
        raise ValueError(
            f"{label} must use 1-96 lowercase letters, numbers, dots, dashes, or underscores"
        )
    return text


def _text(value: Any, label: str, *, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(char in text for char in "\r\n"):
        raise ValueError(f"{label} must be non-empty single-line text")
    return text


def _localization_key(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _LOCALIZATION_KEY.fullmatch(text):
        raise ValueError(f"{label} is not a valid localization key")
    return text


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result = tuple(_safe_id(item, label) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicates")
    return result


def _canonical_json_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_unknown(
    payload: Mapping[str, Any], allowed: set[str], label: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: " + ", ".join(unknown))


@dataclass(frozen=True)
class PrefabAxle:
    order: int
    steered: bool
    powered: bool
    steering_role: str
    carrier_role: str
    liftable: bool

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PrefabAxle":
        _reject_unknown(
            payload,
            {"order", "steered", "powered", "steeringRole", "carrierRole", "liftable"},
            "Prefab axle",
        )
        order = payload.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0:
            raise ValueError("Prefab axle order must be a non-negative integer")
        steering_role = str(payload.get("steeringRole", "")).strip().casefold()
        carrier_role = str(payload.get("carrierRole", "")).strip().casefold()
        if steering_role not in STEERING_ROLES:
            raise ValueError("Prefab axle steeringRole is invalid")
        if carrier_role not in CARRIER_ROLES:
            raise ValueError("Prefab axle carrierRole is invalid")
        return cls(
            order=order,
            steered=_bool(payload.get("steered"), "Prefab axle steered state"),
            powered=_bool(payload.get("powered"), "Prefab axle powered state"),
            steering_role=steering_role,
            carrier_role=carrier_role,
            liftable=_bool(payload.get("liftable"), "Prefab axle liftable state"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "steered": self.steered,
            "powered": self.powered,
            "steeringRole": self.steering_role,
            "carrierRole": self.carrier_role,
            "liftable": self.liftable,
        }


@dataclass(frozen=True)
class AxleBehaviorPrefab:
    prefab_id: str
    localization_key: str
    display_name: str
    common_use_localization_key: str
    common_use: str
    nominal_layout: str
    category: str
    axle_count: int
    pattern: str
    axles: tuple[PrefabAxle, ...]
    tags: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    experimental: bool = False
    builtin: bool = True
    base_prefab_id: str | None = None
    user_overrides: tuple[tuple[int, tuple[tuple[str, Any], ...]], ...] = ()

    @property
    def id(self) -> str:
        """Stable short alias used by catalog cards and selectors."""
        return self.prefab_id

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, builtin: bool = True,
    ) -> "AxleBehaviorPrefab":
        _reject_unknown(
            payload,
            {
                "id", "localizationKey", "displayName",
                "commonUseLocalizationKey", "commonUse", "nominalLayout",
                "category", "axleCount", "pattern", "axles", "tags",
                "requiredCapabilities", "experimental", "basePrefabId",
                "userOverrides",
            },
            "Axle prefab",
        )
        raw_axles = payload.get("axles")
        if not isinstance(raw_axles, list):
            raise ValueError("Prefab axles must be an array")
        raw_capabilities = payload.get("requiredCapabilities", [])
        if not isinstance(raw_capabilities, list):
            raise ValueError("Prefab requiredCapabilities must be an array")
        capabilities = tuple(str(item).strip() for item in raw_capabilities)
        if any(item not in CAPABILITIES for item in capabilities):
            raise ValueError("Prefab contains an unknown required capability")
        if len(capabilities) != len(set(capabilities)):
            raise ValueError("Prefab requiredCapabilities contains duplicates")
        base = payload.get("basePrefabId")
        if base is not None:
            base = _safe_id(base, "Base prefab id")
        overrides: list[tuple[int, tuple[tuple[str, Any], ...]]] = []
        raw_overrides = payload.get("userOverrides", [])
        if not isinstance(raw_overrides, list):
            raise ValueError("Prefab userOverrides must be an array")
        for item in raw_overrides:
            if not isinstance(item, Mapping):
                raise ValueError("Prefab userOverrides contains an invalid row")
            order = item.get("order")
            values = item.get("values")
            if isinstance(order, bool) or not isinstance(order, int) or order < 0:
                raise ValueError("Prefab override order must be non-negative")
            if not isinstance(values, Mapping):
                raise ValueError("Prefab override values must be an object")
            allowed_overrides = {
                "steered", "powered", "steeringRole", "carrierRole", "liftable",
            }
            unknown = sorted(set(values) - allowed_overrides)
            if unknown:
                raise ValueError(
                    "Prefab override contains unsupported fields: " + ", ".join(unknown)
                )
            for key in ("steered", "powered", "liftable"):
                if key in values:
                    _bool(values[key], f"Prefab override {key}")
            if "steeringRole" in values and str(values["steeringRole"]).strip().casefold() not in STEERING_ROLES:
                raise ValueError("Prefab override steeringRole is invalid")
            if "carrierRole" in values and str(values["carrierRole"]).strip().casefold() not in CARRIER_ROLES:
                raise ValueError("Prefab override carrierRole is invalid")
            overrides.append((order, tuple(sorted(values.items()))))
        return cls(
            prefab_id=_safe_id(payload.get("id"), "Prefab id"),
            localization_key=_localization_key(
                payload.get("localizationKey"), "Prefab localization key",
            ),
            display_name=_text(payload.get("displayName"), "Prefab display name"),
            common_use_localization_key=_localization_key(
                payload.get("commonUseLocalizationKey"),
                "Prefab common-use localization key",
            ),
            common_use=_text(payload.get("commonUse"), "Prefab common use"),
            nominal_layout=_text(payload.get("nominalLayout"), "Prefab nominal layout", maximum=32).casefold(),
            category=_safe_id(payload.get("category"), "Prefab category"),
            axle_count=payload.get("axleCount"),
            pattern=_text(payload.get("pattern"), "Prefab pattern", maximum=48).upper(),
            axles=tuple(PrefabAxle.from_dict(item) for item in raw_axles),
            tags=_string_tuple(payload.get("tags", []), "Prefab tags"),
            required_capabilities=capabilities,
            experimental=_bool(payload.get("experimental", False), "Prefab experimental state"),
            builtin=builtin,
            base_prefab_id=base,
            user_overrides=tuple(overrides),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.prefab_id,
            "localizationKey": self.localization_key,
            "displayName": self.display_name,
            "commonUseLocalizationKey": self.common_use_localization_key,
            "commonUse": self.common_use,
            "nominalLayout": self.nominal_layout,
            "category": self.category,
            "axleCount": self.axle_count,
            "pattern": self.pattern,
            "axles": [item.to_dict() for item in self.axles],
            "tags": list(self.tags),
            "requiredCapabilities": list(self.required_capabilities),
            "experimental": self.experimental,
        }
        if self.base_prefab_id:
            result["basePrefabId"] = self.base_prefab_id
        if self.user_overrides:
            result["userOverrides"] = [
                {"order": order, "values": dict(values)}
                for order, values in self.user_overrides
            ]
        return result


@dataclass(frozen=True)
class VisualTyrePackage:
    package_id: str
    localization_key: str
    display_name: str
    description_localization_key: str
    description: str
    rule: str
    tags: tuple[str, ...]
    requires_selected_axles: bool
    required_geometry_keys: tuple[str, ...]
    design_only_without_geometry: bool

    @property
    def id(self) -> str:
        """Stable short alias used by visual-package selectors."""
        return self.package_id

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisualTyrePackage":
        _reject_unknown(
            payload,
            {
                "id", "localizationKey", "displayName",
                "descriptionLocalizationKey", "description", "rule", "tags",
                "requiresSelectedAxles", "requiredGeometryKeys",
                "designOnlyWithoutGeometry",
            },
            "Visual tyre package",
        )
        rule = str(payload.get("rule", "")).strip().casefold()
        if rule not in TYRE_RULES:
            raise ValueError("Visual tyre package rule is invalid")
        return cls(
            package_id=_safe_id(payload.get("id"), "Visual tyre package id"),
            localization_key=_localization_key(
                payload.get("localizationKey"), "Visual tyre localization key",
            ),
            display_name=_text(payload.get("displayName"), "Visual tyre display name"),
            description_localization_key=_localization_key(
                payload.get("descriptionLocalizationKey"),
                "Visual tyre description localization key",
            ),
            description=_text(payload.get("description"), "Visual tyre description"),
            rule=rule,
            tags=_string_tuple(payload.get("tags", []), "Visual tyre tags"),
            requires_selected_axles=_bool(
                payload.get("requiresSelectedAxles", False),
                "Visual tyre selected-axles state",
            ),
            required_geometry_keys=_string_tuple(
                payload.get("requiredGeometryKeys", []),
                "Visual tyre required geometry keys",
            ),
            design_only_without_geometry=_bool(
                payload.get("designOnlyWithoutGeometry", False),
                "Visual tyre design-only state",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.package_id,
            "localizationKey": self.localization_key,
            "displayName": self.display_name,
            "descriptionLocalizationKey": self.description_localization_key,
            "description": self.description,
            "rule": self.rule,
            "tags": list(self.tags),
            "requiresSelectedAxles": self.requires_selected_axles,
            "requiredGeometryKeys": list(self.required_geometry_keys),
            "designOnlyWithoutGeometry": self.design_only_without_geometry,
        }


@dataclass(frozen=True)
class VisualGeometryAsset:
    """Verified source geometry and its portable package-relative identity."""

    key: str
    source_path: Path
    package_asset: str

    def validate(self) -> "VisualGeometryAsset":
        key = _safe_id(self.key, "Visual geometry key")
        source = Path(self.source_path).expanduser().resolve(strict=False)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Visual geometry asset is missing or unsafe: {key}")
        package_asset = str(self.package_asset).strip().replace("\\", "/")
        parts = package_asset.split("/")
        if (
            not package_asset or package_asset.startswith("/") or ":" in parts[0]
            or any(part in {"", ".", ".."} for part in parts)
            or Path(package_asset).suffix.casefold() not in {".ydr", ".ydd", ".yft"}
        ):
            raise ValueError(
                "Visual geometry package asset must be a safe relative YDR/YDD/YFT path"
            )
        return VisualGeometryAsset(key, source, package_asset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sourcePath": str(self.source_path),
            "packageAsset": self.package_asset,
        }


def _parameter_value(value: Any, label: str) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool)):
        result = value
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(f"{label} must be a finite JSON scalar")
        result = value
    else:
        raise ValueError(f"{label} must be a JSON string, number, boolean, or null")
    if isinstance(result, str) and (len(result) > 512 or any(char in result for char in "\r\n")):
        raise ValueError(f"{label} string is too long or contains a newline")
    return result


@dataclass(frozen=True)
class VisualTyreSelection:
    schema_version: int
    package_id: str
    selected_axles: tuple[int, ...] = ()
    parameters: tuple[tuple[str, str | int | float | bool | None], ...] = ()

    @classmethod
    def create(
        cls,
        package_id: str,
        *,
        selected_axles: Iterable[int] = (),
        parameters: Mapping[str, Any] | None = None,
        package: VisualTyrePackage | None = None,
    ) -> "VisualTyreSelection":
        authored_orders = tuple(selected_axles)
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in authored_orders
        ):
            raise ValueError("Selected visual axles must be positive integer physical-order values")
        orders = tuple(sorted(set(authored_orders)))
        if package is not None and package.requires_selected_axles and not orders:
            raise ValueError(
                f"Visual tyre package {package.package_id} requires selected axles"
            )
        raw_parameters = parameters or {}
        if not isinstance(raw_parameters, Mapping):
            raise ValueError("Visual tyre parameters must be an object")
        normalized = tuple(sorted(
            (
                _safe_id(key, "Visual tyre parameter key"),
                _parameter_value(value, f"Visual tyre parameter {key}"),
            )
            for key, value in raw_parameters.items()
        ))
        return cls(
            VISUAL_TYRE_SELECTION_SCHEMA_VERSION,
            _safe_id(package_id, "Visual tyre package id"),
            orders,
            normalized,
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        catalog: "VisualTyreCatalog | None" = None,
    ) -> "VisualTyreSelection":
        _reject_unknown(
            payload,
            {"schemaVersion", "packageId", "selectedAxles", "parameters"},
            "Visual tyre selection",
        )
        schema = payload.get("schemaVersion")
        if schema != VISUAL_TYRE_SELECTION_SCHEMA_VERSION:
            raise ValueError(f"Unsupported visual tyre selection schema: {schema}")
        selected = payload.get("selectedAxles", [])
        parameters = payload.get("parameters", {})
        if not isinstance(selected, list):
            raise ValueError("Visual tyre selectedAxles must be an array")
        package_id = _safe_id(payload.get("packageId"), "Visual tyre package id")
        package = catalog.get(package_id) if catalog is not None else None
        return cls.create(
            package_id, selected_axles=selected, parameters=parameters,
            package=package,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "packageId": self.package_id,
            "selectedAxles": list(self.selected_axles),
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class PrefabAxleConfiguration(AxleConfiguration):
    """Axle schema-compatible extension carrying persistent visual intent."""

    visual_tyre_selection: VisualTyreSelection | None = None

    @classmethod
    def from_configuration(
        cls,
        config: AxleConfiguration,
        *,
        visual_tyre_selection: VisualTyreSelection | None = None,
    ) -> "PrefabAxleConfiguration":
        existing = (
            config.visual_tyre_selection
            if isinstance(config, PrefabAxleConfiguration) else None
        )
        return cls(
            schema_version=config.schema_version,
            vehicle_model=config.vehicle_model,
            preset=config.preset,
            export_mode=config.export_mode,
            axles=config.axles,
            runtime_reapplication=config.runtime_reapplication,
            configuration_id=config.configuration_id,
            model_hash=config.model_hash,
            minimum_runtime_version=config.minimum_runtime_version,
            compatibility=config.compatibility,
            handbrake_rear_steering=config.handbrake_rear_steering,
            steering_calculation=config.steering_calculation,
            visual_tyre_selection=(
                visual_tyre_selection
                if visual_tyre_selection is not None else existing
            ),
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        visual_catalog: "VisualTyreCatalog | None" = None,
    ) -> "PrefabAxleConfiguration":
        base = AxleConfiguration.from_dict(payload)
        raw_visual = payload.get("visual_tyre_package")
        if raw_visual is not None and not isinstance(raw_visual, Mapping):
            raise ValueError("visual_tyre_package must be an object")
        resolved_catalog = (
            visual_catalog or VisualTyreCatalog.load_builtin()
        ) if isinstance(raw_visual, Mapping) else visual_catalog
        selection = (
            VisualTyreSelection.from_dict(raw_visual, catalog=resolved_catalog)
            if isinstance(raw_visual, Mapping) else None
        )
        if selection is not None:
            orders = {item.physical_order for item in base.axles}
            invalid = sorted(set(selection.selected_axles) - orders)
            if invalid:
                raise ValueError(
                    "Visual tyre selection references unknown physical axles: "
                    + ", ".join(str(item) for item in invalid)
                )
        return cls.from_configuration(base, visual_tyre_selection=selection)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.visual_tyre_selection is not None:
            payload["visual_tyre_package"] = self.visual_tyre_selection.to_dict()
        return payload


def _asset_path(file_name: str, project_root: str | Path | None = None) -> Path:
    roots: list[Path] = []
    if project_root is not None:
        roots.append(Path(project_root).expanduser().resolve())
    module = Path(__file__).resolve()
    roots.extend((
        module.parents[2], module.parents[1],
        module.parents[1] / "share" / "allin1-sdk",
        Path(sys.prefix).resolve() / "share" / "allin1-sdk",
    ))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        candidates = (root / "assets" / file_name, root / file_name)
        for candidate in candidates:
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
    raise FileNotFoundError(f"ALLIN1 SDK catalog asset is missing: {file_name}")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read catalog: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Catalog root must be an object")
    return payload


@dataclass(frozen=True)
class AxlePrefabCatalog:
    schema_version: int
    catalog_id: str
    prefabs: tuple[AxleBehaviorPrefab, ...]
    localization: tuple[tuple[str, str], ...]
    source_digest: str

    @classmethod
    def load_builtin(
        cls, project_root: str | Path | None = None,
    ) -> "AxlePrefabCatalog":
        return cls.from_dict(
            _load_json_object(_asset_path("axle-prefabs.json", project_root)),
            builtin=True,
        )

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, builtin: bool = True,
    ) -> "AxlePrefabCatalog":
        _reject_unknown(
            payload,
            {"schemaVersion", "catalogId", "localization", "prefabs", "sourceDigest"},
            "Axle-prefab catalog",
        )
        schema = payload.get("schemaVersion")
        if schema != PREFAB_CATALOG_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported axle-prefab catalog schema: {schema}"
            )
        localization = payload.get("localization")
        if not isinstance(localization, Mapping):
            raise ValueError("Axle-prefab localization must be an object")
        strings = tuple(sorted(
            (_localization_key(key, "Localization key"), _text(value, "Localization value"))
            for key, value in localization.items()
        ))
        raw_prefabs = payload.get("prefabs")
        if not isinstance(raw_prefabs, list):
            raise ValueError("Axle-prefab catalog prefabs must be an array")
        result = cls(
            schema_version=schema,
            catalog_id=_safe_id(payload.get("catalogId"), "Axle-prefab catalog id"),
            prefabs=tuple(
                AxleBehaviorPrefab.from_dict(item, builtin=builtin)
                for item in raw_prefabs
            ),
            localization=strings,
            source_digest=_canonical_json_digest(payload),
        )
        result.validate()
        return result

    def validate(self) -> None:
        ids = [item.prefab_id for item in self.prefabs]
        if len(ids) != len(set(ids)):
            raise ValueError("Axle-prefab catalog contains duplicate ids")
        strings = dict(self.localization)
        for prefab in self.prefabs:
            validate_prefab(prefab, localization=strings)
            category_key = f"axleCategory.{prefab.category}"
            if category_key not in strings:
                raise ValueError(
                    f"Prefab {prefab.prefab_id} is missing category localization key {category_key}"
                )
        missing_validation = sorted(
            set(_FINDING_LOCALIZATION_KEYS.values()) - set(strings)
        )
        if missing_validation:
            raise ValueError(
                "Axle-prefab catalog is missing validation localization keys: "
                + ", ".join(missing_validation)
            )

    def get(self, prefab_id: str) -> AxleBehaviorPrefab:
        normalized = _safe_id(prefab_id, "Prefab id")
        for item in self.prefabs:
            if item.prefab_id == normalized:
                return item
        raise KeyError(f"Unknown axle prefab: {prefab_id}")

    def localize(self, key: str, default: str | None = None) -> str:
        return dict(self.localization).get(key, default if default is not None else key)

    def list_prefabs(
        self,
        *,
        axle_count: int | None = None,
        nominal_layout: str | None = None,
        category: str | None = None,
        steering_type: str | None = None,
        drive_type: str | None = None,
        lift_axle: bool | None = None,
        target: str | None = None,
        experimental: bool | None = None,
        tags: Iterable[str] = (),
    ) -> tuple[AxleBehaviorPrefab, ...]:
        tag_filter = {str(item).strip().casefold() for item in tags if str(item).strip()}
        result = []
        for prefab in self.prefabs:
            if axle_count is not None and prefab.axle_count != axle_count:
                continue
            if nominal_layout and prefab.nominal_layout != nominal_layout.strip().casefold():
                continue
            if category and prefab.category != category.strip().casefold():
                continue
            if experimental is not None and prefab.experimental != experimental:
                continue
            if lift_axle is not None and any(item.liftable for item in prefab.axles) != lift_axle:
                continue
            if tag_filter and not tag_filter.issubset(set(prefab.tags)):
                continue
            if steering_type and _steering_type(prefab) != steering_type.strip().casefold():
                continue
            if drive_type and _drive_type(prefab) != drive_type.strip().casefold():
                continue
            if target and not calculate_compatibility(prefab, target).requirements_met:
                continue
            result.append(prefab)
        return tuple(result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "catalogId": self.catalog_id,
            "localization": dict(self.localization),
            "prefabs": [item.to_dict() for item in self.prefabs],
            "sourceDigest": self.source_digest,
        }


@dataclass(frozen=True)
class VisualTyreCatalog:
    schema_version: int
    catalog_id: str
    packages: tuple[VisualTyrePackage, ...]
    localization: tuple[tuple[str, str], ...]
    source_digest: str

    @classmethod
    def load_builtin(
        cls, project_root: str | Path | None = None,
    ) -> "VisualTyreCatalog":
        return cls.from_dict(
            _load_json_object(_asset_path("visual-tyre-packages.json", project_root)),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisualTyreCatalog":
        _reject_unknown(
            payload,
            {"schemaVersion", "catalogId", "localization", "packages", "sourceDigest"},
            "Visual-tyre catalog",
        )
        schema = payload.get("schemaVersion")
        if schema != VISUAL_TYRE_CATALOG_SCHEMA_VERSION:
            raise ValueError(f"Unsupported visual-tyre catalog schema: {schema}")
        localization = payload.get("localization")
        if not isinstance(localization, Mapping):
            raise ValueError("Visual-tyre localization must be an object")
        strings = tuple(sorted(
            (_localization_key(key, "Localization key"), _text(value, "Localization value"))
            for key, value in localization.items()
        ))
        raw_packages = payload.get("packages")
        if not isinstance(raw_packages, list):
            raise ValueError("Visual-tyre catalog packages must be an array")
        packages = tuple(VisualTyrePackage.from_dict(item) for item in raw_packages)
        ids = [item.package_id for item in packages]
        if len(ids) != len(set(ids)):
            raise ValueError("Visual-tyre catalog contains duplicate ids")
        translated = dict(strings)
        for package in packages:
            if package.localization_key not in translated:
                raise ValueError(
                    f"Missing localization key for visual package {package.package_id}"
                )
            if package.description_localization_key not in translated:
                raise ValueError(
                    f"Missing description localization key for visual package {package.package_id}"
                )
            dual_rule = package.rule in {
                "dual_driven", "dual_tandem", "dual_middle_drive_single_tag",
                "dual_non_steering", "selected_inner_addon",
            }
            if dual_rule and set(package.required_geometry_keys) != {"inner_left", "inner_right"}:
                raise ValueError(
                    f"Dual visual package {package.package_id} must require verified left/right inner geometry"
                )
            if dual_rule and not package.design_only_without_geometry:
                raise ValueError(
                    f"Dual visual package {package.package_id} must fail closed as design-only without geometry"
                )
            if not package.required_geometry_keys and package.design_only_without_geometry:
                raise ValueError(
                    f"Visual package {package.package_id} cannot require geometry without geometry keys"
                )
        return cls(
            schema, _safe_id(payload.get("catalogId"), "Visual-tyre catalog id"),
            packages, strings, _canonical_json_digest(payload),
        )

    def get(self, package_id: str) -> VisualTyrePackage:
        normalized = _safe_id(package_id, "Visual tyre package id")
        for item in self.packages:
            if item.package_id == normalized:
                return item
        raise KeyError(f"Unknown visual tyre package: {package_id}")

    def list_packages(self, *, tags: Iterable[str] = ()) -> tuple[VisualTyrePackage, ...]:
        required = {str(item).strip().casefold() for item in tags if str(item).strip()}
        return tuple(
            item for item in self.packages if required.issubset(set(item.tags))
        )

    def localize(self, key: str, default: str | None = None) -> str:
        return dict(self.localization).get(key, default if default is not None else key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "catalogId": self.catalog_id,
            "localization": dict(self.localization),
            "packages": [item.to_dict() for item in self.packages],
            "sourceDigest": self.source_digest,
        }


def _token_for_axle(axle: PrefabAxle) -> str:
    if axle.liftable:
        return "LS" if axle.steered else "LT"
    if axle.steered and axle.steering_role == "rear" and not axle.powered:
        return "RS"
    if axle.steered and axle.powered:
        return "SD"
    if axle.steered:
        return "S"
    if axle.powered:
        return "D"
    return "T"


def _calculated_capabilities(prefab: AxleBehaviorPrefab) -> tuple[str, ...]:
    steered = {item.order for item in prefab.axles if item.steered}
    powered = {item.order for item in prefab.axles if item.powered}
    front = {0}
    rear = {prefab.axle_count - 1}
    capabilities: list[str] = []
    if steered not in (set(), front, rear, set(range(prefab.axle_count))):
        capabilities.append(CAP_SELECTIVE_STEERING)
    # Ordinary front/rear bias describes two-axle 4x2. Multi-axle selective
    # slots require the runtime's per-wheel powered state.
    if powered and powered != set(range(prefab.axle_count)):
        if not (
            prefab.axle_count == 2 and powered in ({0}, {1})
        ):
            capabilities.append(CAP_SELECTIVE_DRIVE)
    if any(item.liftable for item in prefab.axles):
        capabilities.append(CAP_LIFT_AXLE)
    if prefab.category == "trailer" and steered:
        capabilities.append(CAP_TRAILER_STEERING)
    return tuple(item for item in CAPABILITIES if item in capabilities)


def validate_prefab(
    prefab: AxleBehaviorPrefab,
    *,
    localization: Mapping[str, str] | None = None,
) -> None:
    if isinstance(prefab.axle_count, bool) or not isinstance(prefab.axle_count, int):
        raise ValueError(f"Prefab {prefab.prefab_id} axleCount must be an integer")
    if not 2 <= prefab.axle_count <= 5:
        raise ValueError(f"Prefab {prefab.prefab_id} must contain 2-5 axles")
    if len(prefab.axles) != prefab.axle_count:
        raise ValueError(f"Prefab {prefab.prefab_id} axleCount does not match axles")
    if [item.order for item in prefab.axles] != list(range(prefab.axle_count)):
        raise ValueError(f"Prefab {prefab.prefab_id} axle order must be contiguous from zero")
    tokens = tuple(prefab.pattern.split("-"))
    if len(tokens) != prefab.axle_count or any(item not in PATTERN_TOKENS for item in tokens):
        raise ValueError(f"Prefab {prefab.prefab_id} pattern is invalid")
    explicit = tuple(_token_for_axle(item) for item in prefab.axles)
    if explicit != tokens:
        raise ValueError(
            f"Prefab {prefab.prefab_id} pattern does not match explicit axle behavior"
        )
    if prefab.nominal_layout == "trailer":
        if prefab.category != "trailer" or any(item.powered for item in prefab.axles):
            raise ValueError(f"Prefab {prefab.prefab_id} trailer layout is inconsistent")
    else:
        match = _NOMINAL_LAYOUT.fullmatch(prefab.nominal_layout)
        if match is None:
            raise ValueError(f"Prefab {prefab.prefab_id} nominal layout is invalid")
        wheel_slots, driven_wheels = (int(item) for item in match.groups())
        if wheel_slots != prefab.axle_count * 2:
            raise ValueError(f"Prefab {prefab.prefab_id} nominal axle count is inconsistent")
        if driven_wheels != sum(item.powered for item in prefab.axles) * 2:
            raise ValueError(f"Prefab {prefab.prefab_id} nominal drive notation is inconsistent")
    calculated = _calculated_capabilities(prefab)
    if set(prefab.required_capabilities) != set(calculated):
        raise ValueError(
            f"Prefab {prefab.prefab_id} required capabilities do not match its axle behavior"
        )
    if localization is not None:
        for key in (prefab.localization_key, prefab.common_use_localization_key):
            if key not in localization:
                raise ValueError(f"Prefab {prefab.prefab_id} is missing localization key {key}")


def _steering_type(prefab: AxleBehaviorPrefab) -> str:
    steered = [item for item in prefab.axles if item.steered]
    if not steered:
        return "none"
    if len(steered) == prefab.axle_count:
        return "all"
    if len(steered) > 1:
        return "multi"
    return "rear" if steered[0].steering_role == "rear" else "front"


def _drive_type(prefab: AxleBehaviorPrefab) -> str:
    powered = sum(item.powered for item in prefab.axles)
    if powered == 0:
        return "none"
    if powered == prefab.axle_count:
        return "all"
    return "single" if powered == 1 else "multiple"


def required_canonical_pairs(axle_count: int) -> tuple[tuple[str, str], ...]:
    if isinstance(axle_count, bool) or not isinstance(axle_count, int) or not 2 <= axle_count <= 5:
        raise ValueError("Canonical axle mapping requires 2-5 physical axle pairs")
    pairs = tuple((left, right) for _role, left, right in CANONICAL_WHEEL_PAIRS)
    return pairs[: axle_count - 1] + (pairs[-1],)


class RuntimeWheelIndexResolver(Protocol):
    def resolve(
        self,
        *,
        target: str,
        pair_names: Sequence[tuple[str, str]],
        reported_wheel_count: int | None = None,
    ) -> Mapping[str, int]: ...


@dataclass(frozen=True)
class CanonicalTargetResolver:
    """Adapter around the shared target mapping with strict count validation."""

    def resolve(
        self,
        *,
        target: str,
        pair_names: Sequence[tuple[str, str]],
        reported_wheel_count: int | None = None,
    ) -> Mapping[str, int]:
        mapping = resolve_runtime_wheel_index_map(pair_names, target=target)
        expected = len(pair_names) * 2
        if reported_wheel_count is not None and reported_wheel_count != expected:
            raise ValueError(
                f"Target reported {reported_wheel_count} wheels; prefab requires {expected}."
            )
        values = list(mapping.values())
        if set(mapping) != {bone for pair in pair_names for bone in pair}:
            raise ValueError("Target resolver omitted one or more canonical wheel bones")
        if len(values) != expected or len(set(values)) != expected:
            raise ValueError("Target resolver returned duplicate runtime wheel indices")
        limit = reported_wheel_count if reported_wheel_count is not None else expected
        if any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item < limit for item in values):
            raise ValueError("Target resolver returned an out-of-range runtime wheel index")
        return dict(mapping)


@dataclass(frozen=True)
class TargetCompatibility:
    target: str
    badge: str
    required_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    requirements_met: bool
    exact_supported: bool
    experimental: bool
    design_only: bool
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_compatibility(
    prefab: AxleBehaviorPrefab,
    target: str,
    *,
    available_capabilities: Iterable[str] | None = None,
    acceptance_validated: bool | None = None,
) -> TargetCompatibility:
    normalized = str(target).strip().casefold()
    required = prefab.required_capabilities
    notes: list[str] = []
    if normalized in {"stock", "stock-metadata"}:
        available: set[str] = set()
        published = True
        badge = "Stock"
    else:
        try:
            from allin1_sdk.axle_runtime_bundler import target_capabilities

            capabilities = target_capabilities(normalized)
        except (ImportError, ValueError):
            capabilities = None
        if capabilities is None:
            available = set(available_capabilities or ())
            published = False
            badge = "Unsupported"
        else:
            available = set()
            if capabilities.supports_selective_steering:
                available.add(CAP_SELECTIVE_STEERING)
            if capabilities.supports_selective_drive:
                available.add(CAP_SELECTIVE_DRIVE)
            available.update(available_capabilities or ())
            published = capabilities.published_supported
            badge = "FiveM Runtime" if capabilities.family == "fivem" else "Story ASI"
    missing = tuple(item for item in required if item not in available)
    lift_missing = CAP_LIFT_AXLE in missing
    trailer_experimental = CAP_TRAILER_STEERING in required
    if lift_missing:
        badge = "Lift Runtime"
        notes.append("Lift behavior is design-only until a lift/suspension runtime is supplied.")
    if trailer_experimental:
        notes.append("Trailer steering remains experimental pending target-specific mapping tests.")
    requirements_met = not tuple(
        item for item in missing if item not in {CAP_LIFT_AXLE, CAP_TRAILER_STEERING}
    )
    accepted = published if acceptance_validated is None else bool(acceptance_validated)
    experimental = prefab.experimental or trailer_experimental or (
        bool(required) and requirements_met and not accepted
    )
    exact_supported = requirements_met and not missing and accepted and not experimental
    if missing and not lift_missing and not trailer_experimental:
        badge = "Unsupported"
    elif experimental and not lift_missing:
        notes.append("Runtime capabilities exist, but exact in-game acceptance is not published.")
    return TargetCompatibility(
        target=normalized,
        badge=badge,
        required_capabilities=required,
        missing_capabilities=missing,
        requirements_met=requirements_met,
        exact_supported=exact_supported,
        experimental=experimental,
        design_only=lift_missing,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class AxleMappingRow:
    physical_order: int
    pattern_token: str
    left_bone: str
    right_bone: str
    left_runtime_index: int
    right_runtime_index: int
    steered: bool
    powered: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AxleDiffRow:
    physical_order: int
    field_name: str
    previous: Any
    proposed: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrefabApplicationPreview:
    prefab: AxleBehaviorPrefab
    previous: AxleConfiguration
    proposed: AxleConfiguration
    target: str
    mapping: tuple[AxleMappingRow, ...]
    differences: tuple[AxleDiffRow, ...]
    compatibility: TargetCompatibility
    findings: tuple[AxleFinding, ...]
    handling_flags_before: int | None = None
    handling_flags_after: int | None = None
    visual_package_id: str | None = None

    @property
    def can_apply(self) -> bool:
        return not any(item.severity == "error" for item in self.findings) and self.compatibility.requirements_met

    @property
    def visual_limitations(self) -> tuple[str, ...]:
        return tuple(
            item.message for item in self.findings
            if item.code in {
                "shared_visual_template", "inner_addon_required",
                "mixed_tag_roles", "tandem_drive_missing",
                "visual_geometry_design_only", "visual_geometry_unsafe",
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefab": self.prefab.to_dict(),
            "previous": self.previous.to_dict(),
            "proposed": self.proposed.to_dict(),
            "target": self.target,
            "mapping": [item.to_dict() for item in self.mapping],
            "differences": [item.to_dict() for item in self.differences],
            "compatibility": self.compatibility.to_dict(),
            "findings": [_finding_dict(item) for item in self.findings],
            "handlingFlags": {
                "before": self.handling_flags_before,
                "after": self.handling_flags_after,
            },
            "visualPackageId": self.visual_package_id,
            "visualLimitations": list(self.visual_limitations),
            "canApply": self.can_apply,
        }


def _logical_role(order: int, count: int) -> str:
    if order == 0:
        return "front"
    return "rear" if order == count - 1 else "middle"


def _baseline_configuration(
    vehicle_model: str,
    bones: Sequence[BoneLike],
    *,
    target: str,
    export_mode: str,
    proposed: AxleConfiguration,
) -> AxleConfiguration:
    try:
        return detect_axle_configuration(
            vehicle_model, bones, preset=PRESET_CUSTOM,
            export_mode=export_mode, target=target,
        )
    except ValueError:
        return proposed


def _physical_order_findings(
    pairs: Sequence[tuple[str, str]], bones: Sequence[BoneLike],
) -> list[AxleFinding]:
    lookup = {str(item.name).strip().casefold(): item for item in bones}
    findings: list[AxleFinding] = []
    complete_canonical = [
        (left, right) for _role, left, right in CANONICAL_WHEEL_PAIRS
        if left in lookup and right in lookup
    ]
    if len(complete_canonical) != len(pairs):
        findings.append(AxleFinding(
            "error", "axle_count_mismatch",
            f"Skeleton exposes {len(complete_canonical)} complete canonical axle pairs; "
            f"the selected prefab requires {len(pairs)}.",
        ))
    forwards: list[float] = []
    for order, (left, right) in enumerate(pairs, start=1):
        missing = [name for name in (left, right) if name not in lookup]
        for name in missing:
            findings.append(AxleFinding(
                "error", "missing_required_bone", f"Required wheel bone is missing: {name}", order,
            ))
        if not missing:
            forwards.append((lookup[left].position[1] + lookup[right].position[1]) / 2.0)
    if len(forwards) == len(pairs) and any(
        forwards[index] <= forwards[index + 1] for index in range(len(forwards) - 1)
    ):
        findings.append(AxleFinding(
            "error", "physical_order",
            "Canonical wheel-bone pairs are not positioned in strict front-to-rear order.",
        ))
    return findings


def _configuration_differences(
    previous: AxleConfiguration, proposed: AxleConfiguration,
) -> tuple[AxleDiffRow, ...]:
    prior = {
        (item.left_bone, item.right_bone): item for item in previous.axles
    }
    result: list[AxleDiffRow] = []
    fields = ("steered", "powered", "logical_role", "left_runtime_index", "right_runtime_index")
    for axle in proposed.axles:
        old = prior.get((axle.left_bone, axle.right_bone))
        for field_name in fields:
            before = getattr(old, field_name) if old is not None else None
            after = getattr(axle, field_name)
            if before != after:
                result.append(AxleDiffRow(axle.physical_order, field_name, before, after))
    return tuple(result)


def apply_prefab(
    prefab_id: str,
    vehicle_model: str,
    bones: Iterable[BoneLike],
    target: str,
    export_mode: str,
    base_config: AxleConfiguration | None = None,
    *,
    catalog: AxlePrefabCatalog | None = None,
    resolver: RuntimeWheelIndexResolver | None = None,
    reported_wheel_count: int | None = None,
    handling_flags: int | None = None,
    tyre_package_id: str | None = None,
    selected_visual_axles: Iterable[int] = (),
    visual_parameters: Mapping[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> PrefabApplicationPreview:
    """Build a confirmed-before-write behavior preview for one vehicle."""
    if export_mode not in EXPORT_MODES:
        raise ValueError("Axle export mode is unsupported")
    selected_catalog = catalog or AxlePrefabCatalog.load_builtin(project_root)
    prefab = selected_catalog.get(prefab_id)
    bone_rows = tuple(bones)
    pairs = required_canonical_pairs(prefab.axle_count)
    runtime_resolver = resolver or CanonicalTargetResolver()
    findings = _physical_order_findings(pairs, bone_rows)
    try:
        runtime_mapping = dict(runtime_resolver.resolve(
            target=target, pair_names=pairs,
            reported_wheel_count=reported_wheel_count,
        ))
    except ValueError as exc:
        findings.append(AxleFinding("error", "runtime_mapping", str(exc)))
        # Produce a reviewable configuration even when the reported count
        # failed. The explicit resolver still supplies the semantic map.
        runtime_mapping = dict(runtime_resolver.resolve(
            target=target, pair_names=pairs, reported_wheel_count=None,
        ))
    previous_by_pair = {
        (item.left_bone, item.right_bone): item
        for item in (base_config.axles if base_config else ())
    }
    visual_selection = (
        base_config.visual_tyre_selection
        if isinstance(base_config, PrefabAxleConfiguration) else None
    )
    if tyre_package_id:
        visual_catalog = VisualTyreCatalog.load_builtin(project_root)
        visual_package = visual_catalog.get(tyre_package_id)
        try:
            visual_selection = VisualTyreSelection.create(
                visual_package.package_id,
                selected_axles=selected_visual_axles,
                parameters=visual_parameters,
                package=visual_package,
            )
        except ValueError as exc:
            findings.append(AxleFinding(
                "error", "selected_axles_required", str(exc),
            ))
    axles = []
    mapping_rows = []
    for order, ((left, right), authored) in enumerate(zip(pairs, prefab.axles)):
        existing = previous_by_pair.get((left, right))
        axle = VehicleAxle(
            physical_order=order + 1,
            logical_role=_logical_role(order, prefab.axle_count),
            left_bone=left,
            right_bone=right,
            left_runtime_index=runtime_mapping[left],
            right_runtime_index=runtime_mapping[right],
            steered=authored.steered,
            # Applying a behavior prefab invalidates any prior geometry-derived
            # gain evidence. Return to the exact schema-1 +1/0 behavior until
            # the author explicitly recalculates or supplies manual gains.
            steering_gain=1.0 if authored.steered else 0.0,
            powered=authored.powered,
            service_brake=existing.service_brake if existing else True,
            handbrake=existing.handbrake if existing else order == prefab.axle_count - 1,
            visual_family=VISUAL_FRONT if order == 0 else VISUAL_SHARED_MIDDLE_REAR,
            addon_geometry=existing.addon_geometry if existing else (),
        )
        axles.append(axle)
        mapping_rows.append(AxleMappingRow(
            order + 1, _token_for_axle(authored), left, right,
            runtime_mapping[left], runtime_mapping[right],
            authored.steered, authored.powered,
        ))
    model = str(vehicle_model).strip().casefold()
    proposed = PrefabAxleConfiguration(
        schema_version=AXLE_SCHEMA_VERSION,
        vehicle_model=model,
        configuration_id=(
            base_config.configuration_id if base_config else f"{model}-axles"
        ),
        model_hash=base_config.model_hash if base_config else joaat_hex(model),
        minimum_runtime_version=(
            base_config.minimum_runtime_version if base_config else "1.0.0"
        ),
        preset=PRESET_CUSTOM,
        export_mode=export_mode,
        axles=tuple(axles),
        runtime_reapplication=(
            base_config.runtime_reapplication if base_config else AxleConfiguration.from_dict({
                "schema_version": AXLE_SCHEMA_VERSION,
                "vehicle_model": model,
                "preset": PRESET_CUSTOM,
                "export_mode": export_mode,
                "axles": [item.to_dict() for item in axles],
            }).runtime_reapplication
        ),
        compatibility=base_config.compatibility if base_config else (
            ("fivem-legacy", False), ("fivem-enhanced", False),
            ("story-legacy", False), ("story-enhanced", False),
        ),
        handbrake_rear_steering=(
            base_config.handbrake_rear_steering if base_config else False
        ),
        visual_tyre_selection=visual_selection,
    )
    proposed = retarget_axle_configuration(
        proposed, target, wheel_index_map=runtime_mapping,
    )
    previous = base_config or _baseline_configuration(
        model, bone_rows, target=target, export_mode=export_mode, proposed=proposed,
    )
    findings.extend(validate_axle_configuration(
        proposed, bone_rows, handling_flags=handling_flags, target=target,
    ))
    compatibility = calculate_compatibility(prefab, target)
    for missing in compatibility.missing_capabilities:
        severity = "warning" if missing in {CAP_LIFT_AXLE, CAP_TRAILER_STEERING} else "error"
        findings.append(AxleFinding(
            severity, "missing_target_capability",
            f"Target does not provide required capability: {missing}",
        ))
    if tyre_package_id:
        visual_preview = apply_visual_package(
            tyre_package_id, proposed, project_root=project_root,
            selected_axles=selected_visual_axles,
            parameters=visual_parameters,
        )
        findings.extend(visual_preview.findings)
        if visual_preview.can_persist:
            proposed = visual_preview.proposed
    flags_after = None
    if handling_flags is not None:
        flags_after = stock_metadata_flags(proposed, handling_flags).updated_flags
    return PrefabApplicationPreview(
        prefab=prefab,
        previous=previous,
        proposed=proposed,
        target=str(target).strip().casefold(),
        mapping=tuple(mapping_rows),
        differences=_configuration_differences(previous, proposed),
        compatibility=compatibility,
        findings=tuple(_unique_findings(findings)),
        handling_flags_before=handling_flags,
        handling_flags_after=flags_after,
        visual_package_id=tyre_package_id,
    )


def confirm_prefab_application(
    preview: PrefabApplicationPreview, *, confirmed: bool,
) -> AxleConfiguration:
    if not confirmed:
        raise ValueError("Prefab application requires explicit confirmation")
    if not preview.can_apply:
        first = next(
            (item.message for item in preview.findings if item.severity == "error"),
            "Target requirements are not met",
        )
        raise ValueError(f"Prefab cannot be applied: {first}")
    return preview.proposed


@dataclass(frozen=True)
class VisualAxleState:
    physical_order: int
    tyre_style: str
    stock_template_family: str
    uses_inner_addon: bool
    addon_bones: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisualPackagePreview:
    package: VisualTyrePackage
    previous: AxleConfiguration
    proposed: AxleConfiguration
    selection: VisualTyreSelection | None
    axle_states: tuple[VisualAxleState, ...]
    findings: tuple[AxleFinding, ...]
    runtime_wheel_count_before: int
    runtime_wheel_count_after: int
    geometry_ready: bool
    design_only: bool
    missing_geometry_keys: tuple[str, ...] = ()

    @property
    def can_apply(self) -> bool:
        return (
            not any(item.severity == "error" for item in self.findings)
            and not self.design_only
        )

    @property
    def can_persist(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package.to_dict(),
            "previous": self.previous.to_dict(),
            "proposed": self.proposed.to_dict(),
            "selection": self.selection.to_dict() if self.selection else None,
            "axleStates": [item.to_dict() for item in self.axle_states],
            "findings": [_finding_dict(item) for item in self.findings],
            "runtimeWheelCountBefore": self.runtime_wheel_count_before,
            "runtimeWheelCountAfter": self.runtime_wheel_count_after,
            "geometryReady": self.geometry_ready,
            "designOnly": self.design_only,
            "missingGeometryKeys": list(self.missing_geometry_keys),
            "canApply": self.can_apply,
            "canPersist": self.can_persist,
        }


def _visual_styles(
    package: VisualTyrePackage,
    config: AxleConfiguration,
    selected_axles: set[int],
) -> tuple[list[str], list[AxleFinding]]:
    ordered = sorted(config.axles, key=lambda item: item.physical_order)
    findings: list[AxleFinding] = []
    if package.requires_selected_axles and not selected_axles:
        findings.append(AxleFinding(
            "error", "selected_axles_required",
            "Axle-Specific Inner Wheel requires at least one selected physical axle.",
        ))
    invalid = sorted(selected_axles - {item.physical_order for item in ordered})
    if invalid:
        findings.append(AxleFinding(
            "error", "selected_axle_range",
            "Visual tyre selection references an unknown physical axle: "
            + ", ".join(str(item) for item in invalid),
        ))
    if package.rule == "all_singles":
        styles = ["single"] * len(ordered)
    elif package.rule == "dual_driven":
        styles = ["dual" if item.powered else "single" for item in ordered]
    elif package.rule == "dual_tandem":
        powered = [item.physical_order for item in ordered if item.powered]
        if len(powered) < 2:
            findings.append(AxleFinding(
                "error", "tandem_drive_missing",
                "Dual Tandem Drive requires at least two powered axles.",
            ))
        tandem = set(powered[-2:])
        styles = ["dual" if item.physical_order in tandem else "single" for item in ordered]
    elif package.rule == "super_single_drive_tag":
        styles = [
            "super_single" if item.powered or (
                item.logical_role != "front" and not item.steered
            ) else "single"
            for item in ordered
        ]
    elif package.rule == "dual_middle_drive_single_tag":
        candidates = [
            item for item in ordered
            if item.logical_role == "middle" and item.powered
        ]
        if not candidates or ordered[-1].powered:
            findings.append(AxleFinding(
                "error", "mixed_tag_roles",
                "Dual Drive + Single Tag requires a middle drive axle and an unpowered final tag axle.",
            ))
        dual_orders = {item.physical_order for item in candidates}
        styles = ["dual" if item.physical_order in dual_orders else "single" for item in ordered]
    elif package.rule == "wide_all":
        styles = ["super_single"] * len(ordered)
    elif package.rule == "dual_non_steering":
        styles = ["dual" if not item.steered else "single" for item in ordered]
    else:
        styles = [
            "dual" if item.physical_order in selected_axles else "single"
            for item in ordered
        ]
    return styles, findings


def apply_visual_package(
    package_id: str,
    config: AxleConfiguration,
    *,
    catalog: VisualTyreCatalog | None = None,
    selected_axles: Iterable[int] = (),
    parameters: Mapping[str, Any] | None = None,
    geometry_assets: Iterable[VisualGeometryAsset] = (),
    project_root: str | Path | None = None,
) -> VisualPackagePreview:
    selected_catalog = catalog or VisualTyreCatalog.load_builtin(project_root)
    package = selected_catalog.get(package_id)
    authored_selected = tuple(selected_axles)
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in authored_selected
    ):
        raise ValueError("Selected visual axles must be integer physical-order values")
    selected = set(authored_selected)
    ordered = sorted(config.axles, key=lambda item: item.physical_order)
    styles, findings = _visual_styles(package, config, selected)
    selection: VisualTyreSelection | None
    try:
        selection = VisualTyreSelection.create(
            package.package_id,
            selected_axles=selected,
            parameters=parameters,
            package=package,
        )
    except ValueError as exc:
        selection = None
        findings.append(AxleFinding(
            "error", "selected_axles_required", str(exc),
        ))
    verified_geometry: dict[str, VisualGeometryAsset] = {}
    for authored in geometry_assets:
        if not isinstance(authored, VisualGeometryAsset):
            findings.append(AxleFinding(
                "error", "visual_geometry_unsafe",
                "Visual geometry bindings must be VisualGeometryAsset records.",
            ))
            continue
        try:
            verified = authored.validate()
        except ValueError as exc:
            findings.append(AxleFinding(
                "error", "visual_geometry_unsafe", str(exc),
            ))
            continue
        if verified.key in verified_geometry:
            findings.append(AxleFinding(
                "error", "visual_geometry_unsafe",
                f"Visual geometry key is duplicated: {verified.key}",
            ))
            continue
        verified_geometry[verified.key] = verified
    missing_geometry = tuple(
        key for key in package.required_geometry_keys if key not in verified_geometry
    )
    geometry_ready = not missing_geometry and not any(
        item.code == "visual_geometry_unsafe" for item in findings
    )
    if selection is not None and verified_geometry:
        persisted_parameters = dict(selection.parameters)
        persisted_parameters.update({
            f"geometry_{key}": asset.package_asset
            for key, asset in sorted(verified_geometry.items())
            if key in package.required_geometry_keys
        })
        selection = VisualTyreSelection.create(
            package.package_id,
            selected_axles=selection.selected_axles,
            parameters=persisted_parameters,
            package=package,
        )
    design_only = bool(
        package.design_only_without_geometry and not geometry_ready
    )
    if design_only:
        findings.append(AxleFinding(
            "warning", "visual_geometry_design_only",
            "Required tyre geometry is not available. The selection can be saved as design intent, but no visual geometry will be applied.",
        ))
    shared_styles = {
        style for axle, style in zip(ordered, styles)
        if axle.visual_family == VISUAL_SHARED_MIDDLE_REAR
    }
    mixed_shared = len(shared_styles) > 1
    if mixed_shared:
        findings.append(AxleFinding(
            "warning", "shared_visual_template", SHARED_VISUAL_WARNING,
        ))
        findings.append(AxleFinding(
            "warning", "inner_addon_required",
            "The shared middle/rear outer template stays single; dual-only axles use rigid bone-bound inner geometry with Is Wheel Mesh disabled.",
        ))
    proposed_axles: list[VehicleAxle] = []
    states: list[VisualAxleState] = []
    always_addon = package.rule == "selected_inner_addon"
    for axle, style in zip(ordered, styles):
        use_addon = geometry_ready and style == "dual" and (
            always_addon
            or (axle.visual_family == VISUAL_SHARED_MIDDLE_REAR and mixed_shared)
        )
        retained = tuple(
            item for item in axle.addon_geometry
            if not item.asset.startswith(_GENERATED_TYRE_PREFIX)
        )
        generated: tuple[AxleAddonGeometry, ...] = ()
        if use_addon:
            generated = (
                AxleAddonGeometry(
                    verified_geometry["inner_left"].package_asset,
                    axle.left_bone, False,
                ),
                AxleAddonGeometry(
                    verified_geometry["inner_right"].package_asset,
                    axle.right_bone, False,
                ),
            )
        proposed_axles.append(replace(
            axle, addon_geometry=retained + generated,
        ))
        states.append(VisualAxleState(
            physical_order=axle.physical_order,
            tyre_style=style,
            stock_template_family=axle.visual_family,
            uses_inner_addon=use_addon,
            addon_bones=tuple(item.bone for item in generated),
        ))
    proposed_base = replace(config, axles=tuple(proposed_axles))
    findings.extend(validate_axle_configuration(proposed_base))
    has_errors = any(item.severity == "error" for item in findings)
    proposed = (
        PrefabAxleConfiguration.from_configuration(
            proposed_base, visual_tyre_selection=selection,
        )
        if not has_errors and selection is not None
        else config
    )
    if proposed.expected_wheel_count != config.expected_wheel_count:
        findings.append(AxleFinding(
            "error", "visual_runtime_index_change",
            "Visual tyre packages must not add runtime wheel indices.",
        ))
    before_indices = tuple(
        value for item in config.axles
        for value in (item.left_runtime_index, item.right_runtime_index)
    )
    after_indices = tuple(
        value for item in proposed.axles
        for value in (item.left_runtime_index, item.right_runtime_index)
    )
    if before_indices != after_indices:
        findings.append(AxleFinding(
            "error", "visual_runtime_mapping_change",
            "Visual tyre packages must preserve the physical runtime wheel-index map.",
        ))
    return VisualPackagePreview(
        package, config, proposed, selection if not has_errors else None,
        tuple(states), tuple(_unique_findings(findings)),
        config.expected_wheel_count, proposed.expected_wheel_count,
        geometry_ready, design_only, missing_geometry,
    )


def confirm_visual_package(
    preview: VisualPackagePreview, *, confirmed: bool,
) -> AxleConfiguration:
    if not confirmed:
        raise ValueError("Visual tyre application requires explicit confirmation")
    if not preview.can_apply:
        first = next(
            (item.message for item in preview.findings if item.severity == "error"),
            "Required visual geometry is unavailable; this selection is design-only.",
        )
        raise ValueError(f"Visual tyre package cannot be applied: {first}")
    return preview.proposed


def persist_visual_design(
    preview: VisualPackagePreview, *, confirmed: bool,
) -> PrefabAxleConfiguration:
    """Persist valid design intent without claiming missing geometry was applied."""
    if not confirmed:
        raise ValueError("Visual tyre design persistence requires explicit confirmation")
    if not preview.can_persist or preview.selection is None:
        first = next(
            (item.message for item in preview.findings if item.severity == "error"),
            "Visual tyre selection is invalid",
        )
        raise ValueError(f"Visual tyre design cannot be persisted: {first}")
    if not isinstance(preview.proposed, PrefabAxleConfiguration):
        return PrefabAxleConfiguration.from_configuration(
            preview.proposed, visual_tyre_selection=preview.selection,
        )
    return preview.proposed


def load_prefab_axle_configuration(
    payload: Mapping[str, Any],
    *,
    visual_catalog: VisualTyreCatalog | None = None,
) -> AxleConfiguration:
    """Load behavior plus the optional visual extension from one JSON object."""
    if "visual_tyre_package" not in payload:
        return AxleConfiguration.from_dict(payload)
    return PrefabAxleConfiguration.from_dict(
        payload, visual_catalog=visual_catalog,
    )


def schematic_text(prefab: AxleBehaviorPrefab) -> str:
    """Return a color-independent, screen-reader-friendly axle schematic."""
    labels = {
        "S": "steer",
        "D": "drive",
        "SD": "steer + drive",
        "T": "fixed tag",
        "RS": "rear steer",
        "LT": "liftable tag",
        "LS": "liftable steer",
    }
    tokens = prefab.pattern.split("-")
    return " ─ ".join(
        f"[{index}: {token}; {labels[token]}]"
        for index, token in enumerate(tokens, start=1)
    )


_OVERRIDE_FIELDS = {
    "steered": "steered",
    "powered": "powered",
    "steeringRole": "steering_role",
    "carrierRole": "carrier_role",
    "liftable": "liftable",
}


def create_custom_prefab(
    base: AxleBehaviorPrefab,
    *,
    custom_id: str,
    display_name: str,
    axle_overrides: Mapping[int, Mapping[str, Any]],
) -> AxleBehaviorPrefab:
    """Create an immutable project prefab without mutating its built-in base."""
    custom_key = _safe_id(custom_id, "Custom prefab id")
    if not isinstance(axle_overrides, Mapping):
        raise ValueError("Custom prefab overrides must be an object")
    updated = list(base.axles)
    saved_overrides: list[tuple[int, tuple[tuple[str, Any], ...]]] = []
    for order, values in axle_overrides.items():
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order < base.axle_count:
            raise ValueError("Custom prefab override references an unknown axle")
        if not isinstance(values, Mapping) or not values:
            raise ValueError("Custom prefab override values must be a non-empty object")
        unknown = sorted(set(values) - set(_OVERRIDE_FIELDS))
        if unknown:
            raise ValueError("Unknown custom prefab override fields: " + ", ".join(unknown))
        changes: dict[str, Any] = {}
        for authored, field_name in _OVERRIDE_FIELDS.items():
            if authored not in values:
                continue
            value = values[authored]
            if authored in {"steered", "powered", "liftable"}:
                value = _bool(value, f"Custom prefab {authored}")
            elif authored == "steeringRole":
                value = str(value).strip().casefold()
                if value not in STEERING_ROLES:
                    raise ValueError("Custom prefab steeringRole is invalid")
            else:
                value = str(value).strip().casefold()
                if value not in CARRIER_ROLES:
                    raise ValueError("Custom prefab carrierRole is invalid")
            changes[field_name] = value
        updated[order] = replace(updated[order], **changes)
        saved_overrides.append((order, tuple(sorted(values.items()))))
    pattern = "-".join(_token_for_axle(item) for item in updated)
    nominal = (
        "trailer" if base.category == "trailer"
        else f"{base.axle_count * 2}x{sum(item.powered for item in updated) * 2}"
    )
    draft = replace(
        base,
        prefab_id=custom_key,
        localization_key=f"project.axlePrefab.{custom_key}",
        display_name=_text(display_name, "Custom prefab display name"),
        common_use_localization_key=f"project.axlePrefab.{custom_key}.use",
        nominal_layout=nominal,
        pattern=pattern,
        axles=tuple(updated),
        tags=tuple(dict.fromkeys(base.tags + ("custom",))),
        required_capabilities=(),
        experimental=base.experimental,
        builtin=False,
        base_prefab_id=base.prefab_id,
        user_overrides=tuple(sorted(saved_overrides)),
    )
    result = replace(draft, required_capabilities=_calculated_capabilities(draft))
    validate_prefab(result)
    return result


@dataclass(frozen=True)
class ProjectPrefabCatalog:
    schema_version: int = PROJECT_PREFAB_CATALOG_SCHEMA_VERSION
    prefabs: tuple[AxleBehaviorPrefab, ...] = ()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectPrefabCatalog":
        _reject_unknown(
            payload, {"schemaVersion", "prefabs"}, "Project prefab catalog",
        )
        schema = payload.get("schemaVersion")
        if schema != PROJECT_PREFAB_CATALOG_SCHEMA_VERSION:
            raise ValueError(f"Unsupported project prefab schema: {schema}")
        raw = payload.get("prefabs")
        if not isinstance(raw, list):
            raise ValueError("Project prefab catalog prefabs must be an array")
        prefabs = tuple(AxleBehaviorPrefab.from_dict(item, builtin=False) for item in raw)
        ids = [item.prefab_id for item in prefabs]
        if len(ids) != len(set(ids)):
            raise ValueError("Project prefab catalog contains duplicate ids")
        for item in prefabs:
            if not item.base_prefab_id:
                raise ValueError("Project prefab must identify its immutable built-in base")
            validate_prefab(item)
        return cls(schema, prefabs)

    @classmethod
    def load(cls, path: str | Path) -> "ProjectPrefabCatalog":
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"Project prefab catalog is missing: {source}")
        return cls.from_dict(_load_json_object(source))

    def add(
        self,
        prefab: AxleBehaviorPrefab,
        *,
        builtin_catalog: AxlePrefabCatalog | None = None,
    ) -> "ProjectPrefabCatalog":
        if prefab.builtin or not prefab.base_prefab_id:
            raise ValueError("Only custom project prefabs can be added")
        ids = {item.prefab_id for item in self.prefabs}
        if prefab.prefab_id in ids:
            raise ValueError(f"Project prefab already exists: {prefab.prefab_id}")
        if builtin_catalog is not None:
            if prefab.prefab_id in {item.prefab_id for item in builtin_catalog.prefabs}:
                raise ValueError("Project prefab cannot shadow a built-in prefab id")
            builtin_catalog.get(prefab.base_prefab_id)
        return replace(self, prefabs=self.prefabs + (prefab,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "prefabs": [item.to_dict() for item in self.prefabs],
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ValueError(f"Unsafe project prefab destination: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(self.to_dict(), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(target)
        return target


def _unique_findings(findings: Iterable[AxleFinding]) -> list[AxleFinding]:
    result: list[AxleFinding] = []
    seen: set[tuple[Any, ...]] = set()
    for item in findings:
        key = (item.severity, item.code, item.message, item.axle)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def validation_localization_key(code: str) -> str:
    """Return a stable UI localization key for a catalog validation code."""
    normalized = str(code).strip().casefold()
    return _FINDING_LOCALIZATION_KEYS.get(
        normalized,
        "axleValidation." + re.sub(
            r"_([a-z])", lambda match: match.group(1).upper(), normalized,
        ),
    )


def _finding_dict(finding: AxleFinding) -> dict[str, Any]:
    payload = finding.to_dict()
    payload["localizationKey"] = validation_localization_key(finding.code)
    return payload


__all__ = [
    "CAPABILITIES", "CAP_LIFT_AXLE", "CAP_SELECTIVE_DRIVE",
    "CAP_SELECTIVE_STEERING", "CAP_TRAILER_STEERING",
    "PREFAB_CATALOG_SCHEMA_VERSION", "PROJECT_PREFAB_CATALOG_SCHEMA_VERSION",
    "VISUAL_TYRE_CATALOG_SCHEMA_VERSION", "VISUAL_TYRE_SELECTION_SCHEMA_VERSION",
    "AxleBehaviorPrefab",
    "AxleDiffRow", "AxleMappingRow", "AxlePrefabCatalog",
    "CanonicalTargetResolver", "PrefabApplicationPreview", "PrefabAxle",
    "PrefabAxleConfiguration",
    "ProjectPrefabCatalog", "RuntimeWheelIndexResolver", "TargetCompatibility",
    "VisualAxleState", "VisualGeometryAsset", "VisualPackagePreview",
    "VisualTyreCatalog", "VisualTyrePackage", "VisualTyreSelection",
    "apply_prefab", "apply_visual_package",
    "calculate_compatibility", "confirm_prefab_application",
    "confirm_visual_package", "create_custom_prefab",
    "load_prefab_axle_configuration", "persist_visual_design",
    "required_canonical_pairs", "schematic_text", "validate_prefab",
    "validation_localization_key",
]
