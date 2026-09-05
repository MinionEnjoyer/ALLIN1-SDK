"""Bounded GBAY add-on firearm catalogs. Data only; never spawn authority."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .vehicle_catalog import vehicle_model_hash
from .official_weapon_names import OFFICIAL_WEAPON_NAMES

MAX_WEAPON_CATALOG_BYTES = 4 * 1024 * 1024
MAX_WEAPON_CATALOG_ENTRIES = 2048
WEAPON_CATEGORIES = frozenset({
    "pistols", "smgs", "shotguns", "rifles", "machineguns", "snipers", "heavy",
})
_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_WEAPON = re.compile(r"WEAPON_[A-Z0-9_]{1,56}")
_PACK = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _object(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(fields))}")
    return value


def _text(value: object, label: str, maximum: int = 128) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or c == "~" for c in value)):
        raise ValueError(f"{label} is invalid")
    return value


def _integer(value: object, label: str, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label} must be an integer from 0 to {maximum}")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class WeaponCatalogEntry:
    weapon: str
    name: str
    category: str
    price: int
    ammo_cost_per_round: int
    source_pack: str

    @classmethod
    def from_dict(cls, value: object) -> "WeaponCatalogEntry":
        data = _object(value, {
            "weapon", "name", "category", "price", "ammo_cost_per_round", "source_pack",
        }, "weapon entry")
        weapon = _text(data["weapon"], "weapon", 63)
        pack = _text(data["source_pack"], "source_pack", 64)
        category = _text(data["category"], "category")
        if not _WEAPON.fullmatch(weapon) or not _PACK.fullmatch(pack) or pack == "base":
            raise ValueError("Invalid add-on weapon or source_pack identity")
        if category not in WEAPON_CATEGORIES:
            raise ValueError("Unsupported firearm category")
        return cls(
            weapon, _text(data["name"], "name"), category,
            _integer(data["price"], "price", 2_000_000_000),
            _integer(data["ammo_cost_per_round"], "ammo_cost_per_round", 1_000_000),
            pack,
        )


@dataclass(frozen=True)
class WeaponCatalog:
    catalog_id: str
    name: str
    weapons: tuple[WeaponCatalogEntry, ...]

    @classmethod
    def load(cls, path: str | Path) -> "WeaponCatalog":
        path = Path(path)
        if path.stat().st_size > MAX_WEAPON_CATALOG_BYTES:
            raise ValueError("Weapon catalog exceeds its 4 MiB limit")
        try:
            raw = path.read_bytes()
            if len(raw) > MAX_WEAPON_CATALOG_BYTES:
                raise ValueError("Weapon catalog exceeds its 4 MiB limit")
            return cls.from_dict(json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique_object,
            ))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"Invalid weapon catalog: {exc}") from exc

    @classmethod
    def from_dict(cls, value: object) -> "WeaponCatalog":
        data = _object(value, {"schema_version", "id", "name", "weapons"}, "catalog")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("Weapon catalog schema_version must be 1")
        catalog_id = _text(data["id"], "id", 64)
        if not _ID.fullmatch(catalog_id):
            raise ValueError("Invalid catalog id")
        values = data["weapons"]
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_WEAPON_CATALOG_ENTRIES:
            raise ValueError("Weapon catalog must contain 1 to 2048 entries")
        weapons = tuple(WeaponCatalogEntry.from_dict(item) for item in values)
        hashes = [vehicle_model_hash(item.weapon) for item in weapons]
        if len(set(hashes)) != len(hashes):
            raise ValueError("Duplicate weapon identity or hash")
        return cls(catalog_id, _text(data["name"], "name"), weapons)

    def validate_package_ownership(
        self, declared_dlc_packs: Iterable[str], *,
        reserved_weapons: Iterable[str] = OFFICIAL_WEAPON_NAMES,
    ) -> None:
        owned = {value.lower() for value in declared_dlc_packs}
        reserved = {vehicle_model_hash(value) for value in reserved_weapons}
        for item in self.weapons:
            if item.source_pack not in owned:
                raise ValueError(f"Weapon {item.weapon} advertises unowned DLC pack {item.source_pack}")
            if vehicle_model_hash(item.weapon) in reserved:
                raise ValueError(f"Weapon {item.weapon} collides with a stock weapon")
