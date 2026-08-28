"""Declarative, edition-aware map projects used by the SDK and Story runtime.

The contract deliberately describes *intent*, not native archive internals.  A
map author can attach streamed DLC assets to named levels, connect them with
pedestrian and/or vehicle portals, and expose storage locations without the SDK
rewriting the source map or its model skeletons.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


MAP_SCHEMA_VERSION = 1
MAX_MAP_PROJECT_JSON_BYTES = 1024 * 1024
MAX_LEVELS = 64
MAX_PORTALS = 128
MAX_GARAGES = 64
MAX_IPLS_PER_SCOPE = 128
WORLD_LEVEL_ID = "world"
SUPPORTED_EDITIONS = frozenset({"legacy", "enhanced"})
SUPPORTED_PORTAL_MODES = frozenset({"ped", "vehicle", "both"})
SUPPORTED_VEHICLE_TYPES = frozenset({"land", "helicopter", "plane", "boat"})
STORY_SAVE_POLICY = "story_save_only"

_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_PACK_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NATIVE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_:.@-]{1,96}$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")


def _reject_unknown(
    data: Mapping[str, Any], allowed: Iterable[str], label: str,
) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValueError(
            f"Unsupported {label} field(s): " + ", ".join(sorted(unknown))
        )


def _object(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _text(value: object, label: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise ValueError(f"{label} must be a single line of at most {maximum} characters")
    return normalized


def _identifier(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64).casefold()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} must use 2-64 lowercase letters, numbers, dots, dashes, "
            "or underscores"
        )
    if normalized == WORLD_LEVEL_ID:
        raise ValueError(f"{label} may not use the reserved id '{WORLD_LEVEL_ID}'")
    return normalized


def _pack_name(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=64).casefold()
    if not _PACK_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} must use 1-64 lowercase letters, numbers, dashes, or underscores"
        )
    return normalized


def _native_name(value: object, label: str) -> str:
    normalized = _text(value, label, maximum=96)
    if not _NATIVE_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} may contain only letters, numbers, underscores, dots, colons, "
            "at signs, or dashes"
        )
    return normalized


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    # ScriptHookVDotNet exposes GTA coordinates as System.Single. Reject values
    # that the Story runtime could not represent instead of allowing an SDK-only
    # descriptor that later fails closed in-game.
    if abs(result) > 3.4028234663852886e38:
        raise ValueError(f"{label} must fit in a finite 32-bit float")
    return result


def _positive_number(
    value: object, label: str, *, minimum: float, maximum: float,
) -> float:
    result = _number(value, label)
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false")
    return value


@dataclass(frozen=True)
class MapPosition:
    x: float
    y: float
    z: float
    heading: float = 0.0

    @classmethod
    def from_dict(cls, value: object, label: str = "position") -> "MapPosition":
        data = _object(value, label)
        _reject_unknown(data, {"x", "y", "z", "heading"}, label)
        for axis in ("x", "y", "z"):
            if axis not in data:
                raise ValueError(f"{label}.{axis} is required")
        heading = _number(data.get("heading", 0.0), f"{label}.heading") % 360.0
        return cls(
            x=_number(data["x"], f"{label}.x"),
            y=_number(data["y"], f"{label}.y"),
            z=_number(data["z"], f"{label}.z"),
            heading=heading,
        )

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z, "heading": self.heading}


@dataclass(frozen=True)
class MapStreaming:
    pack_name: str
    content_group: str | None
    ipls: tuple[str, ...]
    activation_radius: float = 300.0
    release_radius: float = 500.0
    keep_resident: bool = False

    @classmethod
    def from_dict(cls, value: object) -> "MapStreaming":
        data = _object(value, "streaming")
        _reject_unknown(data, {
            "pack_name", "content_group", "ipls", "activation_radius",
            "release_radius", "keep_resident",
        }, "streaming")
        pack_name = _pack_name(data.get("pack_name"), "streaming.pack_name")
        raw_group = data.get("content_group")
        content_group = (
            None if raw_group is None else
            _native_name(raw_group, "streaming.content_group")
        )
        raw_ipls = _array(data.get("ipls", []), "streaming.ipls")
        if len(raw_ipls) > MAX_IPLS_PER_SCOPE:
            raise ValueError(
                f"streaming.ipls may contain at most {MAX_IPLS_PER_SCOPE} names"
            )
        ipls = tuple(
            _native_name(item, f"streaming.ipls[{index}]")
            for index, item in enumerate(raw_ipls)
        )
        if len({item.casefold() for item in ipls}) != len(ipls):
            raise ValueError("streaming.ipls contains duplicate names")
        if content_group is None and not ipls:
            raise ValueError("streaming must declare a content_group or at least one IPL")
        activation = _positive_number(
            data.get("activation_radius", 300.0), "streaming.activation_radius",
            minimum=10.0, maximum=10000.0,
        )
        release = _positive_number(
            data.get("release_radius", 500.0), "streaming.release_radius",
            minimum=10.0, maximum=20000.0,
        )
        if release < activation:
            raise ValueError(
                "streaming.release_radius must be greater than or equal to "
                "activation_radius"
            )
        return cls(
            pack_name=pack_name,
            content_group=content_group,
            ipls=ipls,
            activation_radius=activation,
            release_radius=release,
            keep_resident=_boolean(
                data.get("keep_resident", False), "streaming.keep_resident",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_name": self.pack_name,
            "content_group": self.content_group,
            "ipls": list(self.ipls),
            "activation_radius": self.activation_radius,
            "release_radius": self.release_radius,
            "keep_resident": self.keep_resident,
        }


@dataclass(frozen=True)
class MapLevel:
    level_id: str
    name: str
    center: MapPosition
    ipls: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: object, index: int) -> "MapLevel":
        label = f"levels[{index}]"
        data = _object(value, label)
        _reject_unknown(data, {"id", "name", "center", "ipls"}, label)
        raw_ipls = _array(data.get("ipls", []), f"{label}.ipls")
        if len(raw_ipls) > MAX_IPLS_PER_SCOPE:
            raise ValueError(
                f"{label}.ipls may contain at most {MAX_IPLS_PER_SCOPE} names"
            )
        ipls = tuple(
            _native_name(item, f"{label}.ipls[{item_index}]")
            for item_index, item in enumerate(raw_ipls)
        )
        if len({item.casefold() for item in ipls}) != len(ipls):
            raise ValueError(f"{label}.ipls contains duplicate names")
        return cls(
            level_id=_identifier(data.get("id"), f"{label}.id"),
            name=_text(data.get("name"), f"{label}.name"),
            center=MapPosition.from_dict(data.get("center"), f"{label}.center"),
            ipls=ipls,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.level_id,
            "name": self.name,
            "center": self.center.to_dict(),
            "ipls": list(self.ipls),
        }


@dataclass(frozen=True)
class MapEndpoint:
    level: str
    position: MapPosition

    @classmethod
    def from_dict(cls, value: object, label: str) -> "MapEndpoint":
        data = _object(value, label)
        _reject_unknown(data, {"level", "position"}, label)
        raw_level = _text(data.get("level"), f"{label}.level", maximum=64).casefold()
        level = WORLD_LEVEL_ID if raw_level == WORLD_LEVEL_ID else _identifier(
            raw_level, f"{label}.level",
        )
        return cls(
            level=level,
            position=MapPosition.from_dict(data.get("position"), f"{label}.position"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "position": self.position.to_dict()}


@dataclass(frozen=True)
class MapPortal:
    portal_id: str
    name: str
    mode: str
    source: MapEndpoint
    destination: MapEndpoint
    radius: float = 3.0
    one_way: bool = False

    @classmethod
    def from_dict(cls, value: object, index: int) -> "MapPortal":
        label = f"portals[{index}]"
        data = _object(value, label)
        _reject_unknown(
            data, {"id", "name", "mode", "from", "to", "radius", "one_way"},
            label,
        )
        portal_id = _identifier(data.get("id"), f"{label}.id")
        mode = _text(data.get("mode"), f"{label}.mode", maximum=16).casefold()
        if mode not in SUPPORTED_PORTAL_MODES:
            raise ValueError(
                f"{label}.mode must be one of {', '.join(sorted(SUPPORTED_PORTAL_MODES))}"
            )
        source = MapEndpoint.from_dict(data.get("from"), f"{label}.from")
        destination = MapEndpoint.from_dict(data.get("to"), f"{label}.to")
        if source.level == destination.level:
            raise ValueError(f"{label} must connect two different levels")
        raw_name = data.get("name")
        return cls(
            portal_id=portal_id,
            name=(
                portal_id if raw_name is None else _text(raw_name, f"{label}.name")
            ),
            mode=mode,
            source=source,
            destination=destination,
            radius=_positive_number(
                data.get("radius", 3.0), f"{label}.radius",
                minimum=0.5, maximum=50.0,
            ),
            one_way=_boolean(data.get("one_way", False), f"{label}.one_way"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.portal_id,
            "name": self.name,
            "mode": self.mode,
            "from": self.source.to_dict(),
            "to": self.destination.to_dict(),
            "radius": self.radius,
            "one_way": self.one_way,
        }


@dataclass(frozen=True)
class GarageRules:
    allow_store: bool = True
    allow_retrieve: bool = True
    save_policy: str = STORY_SAVE_POLICY

    @classmethod
    def from_dict(cls, value: object, label: str) -> "GarageRules":
        data = _object(value, label)
        _reject_unknown(data, {"allow_store", "allow_retrieve", "save_policy"}, label)
        save_policy = _text(
            data.get("save_policy", STORY_SAVE_POLICY), f"{label}.save_policy",
            maximum=32,
        ).casefold()
        if save_policy != STORY_SAVE_POLICY:
            raise ValueError(
                f"{label}.save_policy must be '{STORY_SAVE_POLICY}'; map garage "
                "changes may only become permanent with a Story Mode save"
            )
        allow_store = _boolean(data.get("allow_store", True), f"{label}.allow_store")
        allow_retrieve = _boolean(
            data.get("allow_retrieve", True), f"{label}.allow_retrieve",
        )
        if not allow_store and not allow_retrieve:
            raise ValueError(f"{label} must allow storing or retrieving vehicles")
        return cls(allow_store, allow_retrieve, save_policy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_store": self.allow_store,
            "allow_retrieve": self.allow_retrieve,
            "save_policy": self.save_policy,
        }


def _vehicle_types(value: object, label: str) -> tuple[str, ...]:
    items = _array(value, label)
    result = tuple(
        _text(item, f"{label}[{index}]", maximum=16).casefold()
        for index, item in enumerate(items)
    )
    if not result:
        raise ValueError(f"{label} must contain at least one vehicle type")
    unknown = set(result) - SUPPORTED_VEHICLE_TYPES
    if unknown:
        raise ValueError(f"{label} contains unsupported types: {', '.join(sorted(unknown))}")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate vehicle types")
    return result


@dataclass(frozen=True)
class GarageSlot:
    slot_id: str
    position: MapPosition
    vehicle_types: tuple[str, ...]

    @classmethod
    def from_dict(
        cls, value: object, index: int, garage_label: str,
        inherited_vehicle_types: tuple[str, ...],
    ) -> "GarageSlot":
        label = f"{garage_label}.slots[{index}]"
        data = _object(value, label)
        _reject_unknown(data, {"id", "position", "vehicle_types"}, label)
        vehicle_types = (
            inherited_vehicle_types if "vehicle_types" not in data else
            _vehicle_types(data["vehicle_types"], f"{label}.vehicle_types")
        )
        if not set(vehicle_types) <= set(inherited_vehicle_types):
            raise ValueError(
                f"{label}.vehicle_types must be a subset of its garage vehicle types"
            )
        return cls(
            slot_id=_identifier(data.get("id"), f"{label}.id"),
            position=MapPosition.from_dict(data.get("position"), f"{label}.position"),
            vehicle_types=vehicle_types,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.slot_id,
            "position": self.position.to_dict(),
            "vehicle_types": list(self.vehicle_types),
        }


@dataclass(frozen=True)
class MapGarage:
    garage_id: str
    name: str
    level_id: str
    entrance_portal_id: str
    capacity: int
    vehicle_types: tuple[str, ...]
    slots: tuple[GarageSlot, ...]
    rules: GarageRules

    @classmethod
    def from_dict(cls, value: object, index: int) -> "MapGarage":
        label = f"garages[{index}]"
        data = _object(value, label)
        _reject_unknown(data, {
            "id", "name", "level_id", "entrance_portal_id", "capacity",
            "vehicle_types", "slots", "rules",
        }, label)
        level_text = _text(data.get("level_id"), f"{label}.level_id", maximum=64)
        level_id = (
            WORLD_LEVEL_ID if level_text.casefold() == WORLD_LEVEL_ID else
            _identifier(level_text, f"{label}.level_id")
        )
        capacity_value = data.get("capacity")
        if isinstance(capacity_value, bool) or not isinstance(capacity_value, int):
            raise ValueError(f"{label}.capacity must be an integer")
        if not 1 <= capacity_value <= 100:
            raise ValueError(f"{label}.capacity must be between 1 and 100")
        vehicle_types = _vehicle_types(data.get("vehicle_types"), f"{label}.vehicle_types")
        raw_slots = _array(data.get("slots"), f"{label}.slots")
        if not raw_slots:
            raise ValueError(f"{label}.slots must contain at least one spawn/store slot")
        slots = tuple(
            GarageSlot.from_dict(item, slot_index, label, vehicle_types)
            for slot_index, item in enumerate(raw_slots)
        )
        slot_ids = [item.slot_id for item in slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError(f"{label}.slots contains duplicate ids")
        if len(slots) > capacity_value:
            raise ValueError(f"{label}.capacity may not be smaller than its slot count")
        return cls(
            garage_id=_identifier(data.get("id"), f"{label}.id"),
            name=_text(data.get("name"), f"{label}.name"),
            level_id=level_id,
            entrance_portal_id=_identifier(
                data.get("entrance_portal_id"), f"{label}.entrance_portal_id",
            ),
            capacity=capacity_value,
            vehicle_types=vehicle_types,
            slots=slots,
            rules=GarageRules.from_dict(data.get("rules", {}), f"{label}.rules"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.garage_id,
            "name": self.name,
            "level_id": self.level_id,
            "entrance_portal_id": self.entrance_portal_id,
            "capacity": self.capacity,
            "vehicle_types": list(self.vehicle_types),
            "slots": [item.to_dict() for item in self.slots],
            "rules": self.rules.to_dict(),
        }


@dataclass(frozen=True)
class MapProject:
    project_id: str
    package_id: str
    name: str
    version: str
    editions: tuple[str, ...]
    streaming: MapStreaming
    levels: tuple[MapLevel, ...]
    portals: tuple[MapPortal, ...]
    garages: tuple[MapGarage, ...]

    @classmethod
    def load(cls, path: str | Path) -> "MapProject":
        source = Path(path).expanduser().resolve()
        try:
            encoded = source.read_bytes()
            if len(encoded) > MAX_MAP_PROJECT_JSON_BYTES:
                raise ValueError("Map project JSON exceeds the 1 MiB runtime limit")
            data = json.loads(encoded.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid map project JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, value: object) -> "MapProject":
        data = _object(value, "map project")
        _reject_unknown(data, {
            "schema_version", "id", "package_id", "name", "version", "editions",
            "streaming", "levels", "portals", "garages",
        }, "map project")
        if (
            isinstance(data.get("schema_version"), bool)
            or data.get("schema_version") != MAP_SCHEMA_VERSION
        ):
            raise ValueError(f"map project schema_version must be {MAP_SCHEMA_VERSION}")
        project_id = _identifier(data.get("id"), "map project.id")
        package_id = _identifier(data.get("package_id"), "map project.package_id")
        version = _text(data.get("version"), "map project.version", maximum=32)
        if not _VERSION_PATTERN.fullmatch(version):
            raise ValueError("map project.version contains unsupported characters")
        raw_editions = _array(data.get("editions"), "map project.editions")
        editions = tuple(
            _text(item, f"map project.editions[{index}]", maximum=16).casefold()
            for index, item in enumerate(raw_editions)
        )
        if not editions or not set(editions) <= SUPPORTED_EDITIONS:
            raise ValueError("map project.editions may contain only legacy and enhanced")
        if len(set(editions)) != len(editions):
            raise ValueError("map project.editions contains duplicates")
        levels = tuple(
            MapLevel.from_dict(item, index)
            for index, item in enumerate(_array(data.get("levels"), "levels"))
        )
        if not levels:
            raise ValueError("map project must contain at least one named level")
        if len(levels) > MAX_LEVELS:
            raise ValueError(f"map project may contain at most {MAX_LEVELS} levels")
        streaming = MapStreaming.from_dict(data.get("streaming"))
        if not streaming.ipls and not any(level.ipls for level in levels):
            raise ValueError(
                "map project must declare at least one project or level IPL; "
                "arbitrary content-group execution is not used by the Story runtime"
            )
        level_ids = [item.level_id for item in levels]
        if len(level_ids) != len(set(level_ids)):
            raise ValueError("map project contains duplicate level ids")
        portals = tuple(
            MapPortal.from_dict(item, index)
            for index, item in enumerate(_array(data.get("portals"), "portals"))
        )
        if not portals:
            raise ValueError("map project must contain at least one entrance/exit portal")
        if len(portals) > MAX_PORTALS:
            raise ValueError(f"map project may contain at most {MAX_PORTALS} portals")
        portal_ids = [item.portal_id for item in portals]
        if len(portal_ids) != len(set(portal_ids)):
            raise ValueError("map project contains duplicate portal ids")
        known_levels = set(level_ids) | {WORLD_LEVEL_ID}
        for portal in portals:
            for endpoint, label in (
                (portal.source, "from"), (portal.destination, "to"),
            ):
                if endpoint.level not in known_levels:
                    raise ValueError(
                        f"portal '{portal.portal_id}' {label} references unknown level "
                        f"'{endpoint.level}'"
                    )
        garages = tuple(
            MapGarage.from_dict(item, index)
            for index, item in enumerate(_array(data.get("garages", []), "garages"))
        )
        garage_ids = [item.garage_id for item in garages]
        if len(garages) > MAX_GARAGES:
            raise ValueError(f"map project may contain at most {MAX_GARAGES} garages")
        if len(garage_ids) != len(set(garage_ids)):
            raise ValueError("map project contains duplicate garage ids")
        portals_by_id = {item.portal_id: item for item in portals}
        for garage in garages:
            if garage.level_id not in known_levels:
                raise ValueError(
                    f"garage '{garage.garage_id}' references unknown level "
                    f"'{garage.level_id}'"
                )
            portal = portals_by_id.get(garage.entrance_portal_id)
            if portal is None:
                raise ValueError(
                    f"garage '{garage.garage_id}' references unknown entrance portal "
                    f"'{garage.entrance_portal_id}'"
                )
            if garage.level_id not in {portal.source.level, portal.destination.level}:
                raise ValueError(
                    f"garage '{garage.garage_id}' entrance portal does not connect to "
                    f"its level '{garage.level_id}'"
                )
        project = cls(
            project_id=project_id,
            package_id=package_id,
            name=_text(data.get("name"), "map project.name"),
            version=version,
            editions=editions,
            streaming=streaming,
            levels=levels,
            portals=portals,
            garages=garages,
        )
        if len(project._encoded()) > MAX_MAP_PROJECT_JSON_BYTES:
            raise ValueError("Map project JSON exceeds the 1 MiB runtime limit")
        return project

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MAP_SCHEMA_VERSION,
            "id": self.project_id,
            "package_id": self.package_id,
            "name": self.name,
            "version": self.version,
            "editions": list(self.editions),
            "streaming": self.streaming.to_dict(),
            "levels": [item.to_dict() for item in self.levels],
            "portals": [item.to_dict() for item in self.portals],
            "garages": [item.to_dict() for item in self.garages],
        }

    def write(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Map project destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.write_bytes(self._encoded())
        temporary.replace(destination)
        return destination

    def _encoded(self) -> bytes:
        encoded = (json.dumps(self.to_dict(), indent=2) + "\n").encode("utf-8")
        if len(encoded) > MAX_MAP_PROJECT_JSON_BYTES:
            raise ValueError("Map project JSON exceeds the 1 MiB runtime limit")
        return encoded
