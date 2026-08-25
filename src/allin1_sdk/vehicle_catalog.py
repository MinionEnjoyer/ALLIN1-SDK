"""Typed, renderer-neutral GBAY vehicle catalog contracts.

Vehicle packages may advertise only models from DLC packs they own. Traffic is
an independent, opt-in policy on each listing; a GBAY listing never becomes an
ambient spawn merely by existing in the catalog.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VEHICLE_CATALOG_SCHEMA_VERSION = 1
MAX_VEHICLE_CATALOG_ENTRIES = 2048
MAX_VEHICLE_CATALOG_BYTES = 4 * 1024 * 1024
VEHICLE_CATEGORIES = frozenset({
    "compacts", "coupes", "sedans", "suvs", "muscle", "sports",
    "sportsclassics", "super", "offroad", "motorcycles", "vans", "boats",
    "helicopters", "planes", "military", "industrial", "openwheel",
    "emergency", "cycles", "service", "special",
})
ROAD_TRAFFIC_CATEGORIES = frozenset({
    "compacts", "coupes", "sedans", "suvs", "muscle", "sports",
    "sportsclassics", "super", "offroad", "motorcycles", "vans",
})
STORAGE_KINDS = frozenset({"garage", "harbour", "helipad", "hangar"})

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_PACK_PATTERN = re.compile(r"^(?:base|[A-Za-z0-9][A-Za-z0-9_-]{0,63})$")
_PREVIEW_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _reject_unknown(data: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unsupported fields: {', '.join(unknown)}")


def _required_text(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def vehicle_model_hash(value: str) -> int:
    """Return GTA's case-insensitive Jenkins one-at-a-time model hash."""

    result = 0
    for character in value.casefold():
        result = (result + ord(character)) & 0xFFFFFFFF
        result = (result + (result << 10)) & 0xFFFFFFFF
        result ^= result >> 6
    result = (result + (result << 3)) & 0xFFFFFFFF
    result ^= result >> 11
    result = (result + (result << 15)) & 0xFFFFFFFF
    return result


@dataclass(frozen=True)
class VehicleTrafficPolicy:
    enabled: bool = False
    weight: float = 1.0

    @classmethod
    def from_dict(cls, data: object, label: str) -> "VehicleTrafficPolicy":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {"enabled", "weight"}, label)
        enabled = data.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"{label}.enabled must be a boolean")
        weight = data.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValueError(f"{label}.weight must be a number")
        weight = float(weight)
        if not math.isfinite(weight) or weight < 0.1 or weight > 20.0:
            raise ValueError(f"{label}.weight must be between 0.1 and 20")
        return cls(enabled=enabled, weight=weight)

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "weight": self.weight}


@dataclass(frozen=True)
class VehicleCatalogEntry:
    model: str
    display_name: str
    manufacturer: str
    category: str
    price: int
    storage: str
    source_pack: str
    size_tier: int = 0
    preview_dictionary: str | None = None
    preview_texture: str | None = None
    traffic: VehicleTrafficPolicy = VehicleTrafficPolicy()

    @classmethod
    def from_dict(cls, data: object, index: int) -> "VehicleCatalogEntry":
        label = f"vehicles[{index}]"
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {
            "model", "name", "manufacturer", "category", "price", "storage",
            "source_pack", "size_tier", "preview_dictionary", "preview_texture",
            "traffic",
        }, label)
        model = _required_text(data, "model", label).lower()
        if not _MODEL_PATTERN.fullmatch(model):
            raise ValueError(f"{label}.model is invalid")
        category = _required_text(data, "category", label).lower()
        if category not in VEHICLE_CATEGORIES:
            raise ValueError(f"{label}.category must be one of {', '.join(sorted(VEHICLE_CATEGORIES))}")
        storage = _required_text(data, "storage", label).lower()
        if storage not in STORAGE_KINDS:
            raise ValueError(f"{label}.storage must be one of {', '.join(sorted(STORAGE_KINDS))}")
        expected_storage = {"boats": "harbour", "helicopters": "helipad", "planes": "hangar"}.get(category)
        if expected_storage and storage != expected_storage:
            raise ValueError(f"{label}.storage must be {expected_storage} for {category}")
        if category not in {"boats", "helicopters", "planes"} and storage != "garage":
            raise ValueError(f"{label}.storage must be garage unless the category is boats, helicopters, or planes")
        source_pack = _required_text(data, "source_pack", label)
        if not _PACK_PATTERN.fullmatch(source_pack):
            raise ValueError(f"{label}.source_pack is invalid")
        source_pack = source_pack.lower()
        price = data.get("price")
        if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= 2_000_000_000:
            raise ValueError(f"{label}.price must be an integer from 0 to 2000000000")
        size_tier = data.get("size_tier", 0)
        if isinstance(size_tier, bool) or not isinstance(size_tier, int) or size_tier not in {0, 1, 2}:
            raise ValueError(f"{label}.size_tier must be 0, 1, or 2")
        manufacturer = data.get("manufacturer", "")
        if not isinstance(manufacturer, str):
            raise ValueError(f"{label}.manufacturer must be a string")
        manufacturer = manufacturer.strip()
        display_name = _required_text(data, "name", label)
        if len(display_name) > 128:
            raise ValueError(f"{label}.name must not exceed 128 characters")
        if len(manufacturer) > 96:
            raise ValueError(f"{label}.manufacturer must not exceed 96 characters")
        preview = data.get("preview_dictionary")
        if preview is not None and (not isinstance(preview, str) or not _PREVIEW_PATTERN.fullmatch(preview)):
            raise ValueError(f"{label}.preview_dictionary is invalid")
        preview_texture = data.get("preview_texture")
        if preview_texture is not None and (not isinstance(preview_texture, str) or not _PREVIEW_PATTERN.fullmatch(preview_texture)):
            raise ValueError(f"{label}.preview_texture is invalid")
        if preview_texture is not None and preview is None:
            raise ValueError(f"{label}.preview_texture requires preview_dictionary")
        traffic = VehicleTrafficPolicy.from_dict(data.get("traffic"), f"{label}.traffic")
        if traffic.enabled and (category not in ROAD_TRAFFIC_CATEGORIES or storage != "garage"):
            raise ValueError(f"{label} cannot opt into ambient traffic because it is not a road vehicle")
        return cls(model, display_name, manufacturer, category, price, storage,
                   source_pack, size_tier, preview, preview_texture, traffic)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "model": self.model, "name": self.display_name,
            "manufacturer": self.manufacturer, "category": self.category,
            "price": self.price, "storage": self.storage,
            "source_pack": self.source_pack, "size_tier": self.size_tier,
            "traffic": self.traffic.to_dict(),
        }
        if self.preview_dictionary:
            result["preview_dictionary"] = self.preview_dictionary
        if self.preview_texture:
            result["preview_texture"] = self.preview_texture
        return result


@dataclass(frozen=True)
class VehicleCatalog:
    catalog_id: str
    name: str
    vehicles: tuple[VehicleCatalogEntry, ...]

    @classmethod
    def load(cls, path: str | Path) -> "VehicleCatalog":
        source = Path(path)
        if source.stat().st_size > MAX_VEHICLE_CATALOG_BYTES:
            raise ValueError("Vehicle catalog exceeds the 4 MiB safety limit")
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid GBAY vehicle catalog: {exc}") from exc
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, data: object) -> "VehicleCatalog":
        if not isinstance(data, dict):
            raise ValueError("Vehicle catalog must be a JSON object")
        _reject_unknown(data, {"schema_version", "id", "name", "vehicles"}, "catalog")
        schema_version = data.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or schema_version != VEHICLE_CATALOG_SCHEMA_VERSION
        ):
            raise ValueError(f"Vehicle catalog schema_version must be {VEHICLE_CATALOG_SCHEMA_VERSION}")
        catalog_id = _required_text(data, "id", "catalog").lower()
        if not _ID_PATTERN.fullmatch(catalog_id):
            raise ValueError("catalog.id is invalid")
        raw_vehicles = data.get("vehicles")
        if not isinstance(raw_vehicles, list) or not raw_vehicles:
            raise ValueError("catalog.vehicles must be a non-empty array")
        if len(raw_vehicles) > MAX_VEHICLE_CATALOG_ENTRIES:
            raise ValueError(f"Vehicle catalog contains more than {MAX_VEHICLE_CATALOG_ENTRIES} entries")
        vehicles = tuple(VehicleCatalogEntry.from_dict(v, i) for i, v in enumerate(raw_vehicles, start=1))
        models = [vehicle.model.casefold() for vehicle in vehicles]
        if len(models) != len(set(models)):
            raise ValueError("Vehicle catalog contains duplicate model names")
        hashes = [vehicle_model_hash(vehicle.model) for vehicle in vehicles]
        if len(hashes) != len(set(hashes)):
            raise ValueError("Vehicle catalog contains duplicate model hashes")
        name = _required_text(data, "name", "catalog")
        if len(name) > 128:
            raise ValueError("catalog.name must not exceed 128 characters")
        return cls(catalog_id, name, vehicles)

    def validate_package_ownership(
        self, declared_dlc_packs: Iterable[str], *, allow_base_game: bool = False,
        allow_traffic: bool = False, reserved_models: Iterable[str] = (),
    ) -> None:
        owned = {value.casefold() for value in declared_dlc_packs}
        reserved = {value.casefold() for value in reserved_models}
        reserved_hashes = {vehicle_model_hash(value) for value in reserved}
        for vehicle in self.vehicles:
            if vehicle.source_pack == "base":
                if not allow_base_game:
                    raise ValueError(f"Package vehicle catalog cannot claim base-game model: {vehicle.model}")
            elif vehicle.source_pack.casefold() not in owned:
                raise ValueError(f"Vehicle '{vehicle.model}' advertises unowned DLC pack '{vehicle.source_pack}'")
            if not allow_base_game and (
                vehicle.model.casefold() in reserved
                or vehicle_model_hash(vehicle.model) in reserved_hashes
            ):
                raise ValueError(f"Package vehicle catalog collides with an official GTA model: {vehicle.model}")
            if vehicle.traffic.enabled and not allow_traffic:
                raise ValueError(f"Vehicle '{vehicle.model}' enables traffic without the traffic.catalog capability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VEHICLE_CATALOG_SCHEMA_VERSION,
            "id": self.catalog_id,
            "name": self.name,
            "vehicles": [vehicle.to_dict() for vehicle in self.vehicles],
        }
