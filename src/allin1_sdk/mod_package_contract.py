"""Shared, dependency-free ALLIN1 package contract primitives.

This module is mirrored byte-for-byte by the launcher and SDK repositories.
Keep runtime-specific loading and installation outside this contract so both
applications reject and accept the same versioned package envelopes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


SUPPORTED_MOD_SCHEMA_VERSIONS = frozenset({1, 2})
EXTENSION_API_VERSION = 1
_HASH_PATTERN = re.compile(r"^(?:0x)?[0-9A-Fa-f]{8}$")
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_ENTRY_POINT_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
_WINDOWS_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})


def validate_mod_schema_envelope(
    data: Mapping[str, Any],
) -> tuple[int, Mapping[str, Any] | None]:
    """Validate schema selection and the version-2 ALLIN1 envelope."""
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_MOD_SCHEMA_VERSIONS:
        raise ValueError("mod.toml schema_version must be 1 or 2")
    raw_allin1 = data.get("allin1")
    if schema_version == 1:
        if raw_allin1 is not None:
            raise ValueError(
                "ALLIN1 extension declarations require mod.toml schema_version = 2"
            )
        return schema_version, None
    if raw_allin1 is None:
        raise ValueError(
            "mod.toml schema_version 2 requires an [allin1] extension table"
        )
    if not isinstance(raw_allin1, Mapping):
        raise ValueError("[allin1] must be a table")
    unknown = set(raw_allin1) - {"api_version", "content", "requires"}
    if unknown:
        raise ValueError(
            "Unsupported [allin1] field(s): " + ", ".join(sorted(unknown))
        )
    if raw_allin1.get("api_version") != EXTENSION_API_VERSION:
        raise ValueError(f"[allin1].api_version must be {EXTENSION_API_VERSION}")
    content = raw_allin1.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("[allin1].content must be a non-empty relative path")
    requires = raw_allin1.get("requires", [])
    if not isinstance(requires, list) or not all(
        isinstance(item, str) for item in requires
    ):
        raise ValueError("[allin1].requires must be an array of strings")
    return schema_version, raw_allin1


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    if normalized != normalized.strip():
        raise ValueError(f"{label} must not begin or end with whitespace")
    components = normalized.split("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in components):
        raise ValueError(f"{label} must not be absolute or contain traversal segments")
    for component in components:
        if component.endswith((".", " ")) or any(
            character in _WINDOWS_INVALID_CHARS or ord(character) < 32
            for character in component
        ):
            raise ValueError(f"{label} contains a Windows-invalid path component")
        if component.split(".", 1)[0].casefold() in _WINDOWS_DEVICE_NAMES:
            raise ValueError(f"{label} contains a reserved Windows device name")
    return path.as_posix()


def _required_text(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value.strip()):
        raise ValueError(f"{label} must be an eight-digit hexadecimal hash")
    return "0x" + value.strip().removeprefix("0x").removeprefix("0X").upper()


def _identifier(value: object, label: str) -> str:
    normalized = str(value or "").strip().casefold()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a safe lowercase identifier")
    return normalized


def _unique_text(values: object, label: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values or not all(
        isinstance(item, str) and item.strip() for item in values
    ):
        raise ValueError(f"{label} must be a non-empty array of strings")
    result = tuple(dict.fromkeys(item.strip() for item in values))
    return result


@dataclass(frozen=True)
class VanillaWeaponComponentLink:
    weapon_name: str
    weapon_hash: str
    component_name: str
    component_hash: str


@dataclass(frozen=True)
class VisualAssetProgression:
    dlc_pack: str
    archive: str
    families: tuple[str, ...]
    levels: int
    model_pattern: str
    base_model_pattern: str | None
    texture_dictionary: str
    texture_pattern: str
    archetype_dictionary: str
    base_level_uses_unsuffixed: bool = False


@dataclass(frozen=True)
class WeaponEnhancementContract:
    enhancement_id: str
    name: str
    mode: str
    weapon_components: tuple[VanillaWeaponComponentLink, ...]
    script_entry_points: tuple[str, ...]
    visual_assets: tuple[VisualAssetProgression, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["id"] = payload.pop("enhancement_id")
        return payload


def _visual_progression(
    data: object, label: str,
) -> VisualAssetProgression:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be an object")
    allowed = {
        "dlc_pack", "archive", "families", "levels", "model_pattern",
        "base_model_pattern",
        "texture_dictionary", "texture_pattern", "archetype_dictionary",
        "base_level_uses_unsuffixed",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unsupported {label} field(s): " + ", ".join(sorted(unknown)))
    dlc_pack = _required_text(data, "dlc_pack", label)
    if not _TOKEN_PATTERN.fullmatch(dlc_pack):
        raise ValueError(f"{label}.dlc_pack is invalid")
    families = _unique_text(data.get("families"), f"{label}.families")
    if any(not _TOKEN_PATTERN.fullmatch(item) for item in families):
        raise ValueError(f"{label}.families contains an invalid token")
    levels = data.get("levels")
    if isinstance(levels, bool) or not isinstance(levels, int) or not 2 <= levels <= 256:
        raise ValueError(f"{label}.levels must be an integer from 2 through 256")
    model_pattern = _required_text(data, "model_pattern", label)
    raw_base_pattern = data.get("base_model_pattern")
    base_model_pattern = None
    if raw_base_pattern is not None:
        base_model_pattern = _required_text(data, "base_model_pattern", label)
        if "{family}" not in base_model_pattern:
            raise ValueError(f"{label}.base_model_pattern must contain a family field")
    texture_pattern = _required_text(data, "texture_pattern", label)
    if "{family}" not in model_pattern or "{level" not in model_pattern:
        raise ValueError(f"{label}.model_pattern must contain family and level fields")
    if "{level" not in texture_pattern:
        raise ValueError(f"{label}.texture_pattern must contain a level field")
    try:
        model_pattern.format(family="sample", level=1)
        if base_model_pattern is not None:
            base_model_pattern.format(family="sample")
        texture_pattern.format(level=1)
    except (IndexError, KeyError, ValueError) as exc:
        raise ValueError(f"{label} contains an invalid format pattern") from exc
    unsuffixed = data.get("base_level_uses_unsuffixed", False)
    if not isinstance(unsuffixed, bool):
        raise ValueError(f"{label}.base_level_uses_unsuffixed must be true or false")
    if unsuffixed and base_model_pattern is None:
        raise ValueError(
            f"{label}.base_model_pattern is required when the final level is unsuffixed"
        )
    return VisualAssetProgression(
        dlc_pack=dlc_pack,
        archive=_safe_path(data.get("archive"), f"{label}.archive"),
        families=families,
        levels=levels,
        model_pattern=model_pattern,
        base_model_pattern=base_model_pattern,
        texture_dictionary=_safe_path(
            data.get("texture_dictionary"), f"{label}.texture_dictionary"
        ),
        texture_pattern=texture_pattern,
        archetype_dictionary=_safe_path(
            data.get("archetype_dictionary"), f"{label}.archetype_dictionary"
        ),
        base_level_uses_unsuffixed=unsuffixed,
    )


def parse_workbench_contract(
    value: object,
    *,
    runtime_entry_points: Iterable[str] = (),
) -> tuple[WeaponEnhancementContract, ...]:
    """Parse optional package-to-Workbench relationships without executing code."""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("content workbench must be an object")
    if set(value) - {"weapon_enhancements"}:
        raise ValueError(
            "Unsupported content workbench field(s): "
            + ", ".join(sorted(set(value) - {"weapon_enhancements"}))
        )
    authored = value.get("weapon_enhancements", [])
    if not isinstance(authored, list):
        raise ValueError("workbench.weapon_enhancements must be an array")
    runtime = {item.casefold() for item in runtime_entry_points}
    result: list[WeaponEnhancementContract] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(authored, start=1):
        label = f"workbench.weapon_enhancements[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} must be an object")
        allowed = {
            "id", "name", "mode", "weapon_components",
            "script_entry_points", "visual_assets",
        }
        unknown = set(item) - allowed
        if unknown:
            raise ValueError(f"Unsupported {label} field(s): " + ", ".join(sorted(unknown)))
        enhancement_id = _identifier(item.get("id"), f"{label}.id")
        if enhancement_id in seen_ids:
            raise ValueError("content workbench contains duplicate enhancement ids")
        seen_ids.add(enhancement_id)
        mode = _required_text(item, "mode", label).casefold()
        if mode != "scripted_vanilla_components":
            raise ValueError(f"{label}.mode must be scripted_vanilla_components")
        raw_links = item.get("weapon_components")
        if not isinstance(raw_links, list) or not raw_links:
            raise ValueError(f"{label}.weapon_components must be a non-empty array")
        links: list[VanillaWeaponComponentLink] = []
        seen_weapons: set[str] = set()
        for link_index, raw_link in enumerate(raw_links, start=1):
            link_label = f"{label}.weapon_components[{link_index}]"
            if not isinstance(raw_link, Mapping):
                raise ValueError(f"{link_label} must be an object")
            if set(raw_link) - {
                "weapon_name", "weapon_hash", "component_name", "component_hash"
            }:
                raise ValueError(f"Unsupported {link_label} field")
            weapon_name = _required_text(raw_link, "weapon_name", link_label)
            component_name = _required_text(raw_link, "component_name", link_label)
            if not weapon_name.startswith("WEAPON_"):
                raise ValueError(f"{link_label}.weapon_name must begin with WEAPON_")
            if not component_name.startswith("COMPONENT_"):
                raise ValueError(f"{link_label}.component_name must begin with COMPONENT_")
            weapon_hash = _hash(raw_link.get("weapon_hash"), f"{link_label}.weapon_hash")
            if weapon_hash.casefold() in seen_weapons:
                raise ValueError(f"{label} contains duplicate vanilla weapon hashes")
            seen_weapons.add(weapon_hash.casefold())
            links.append(VanillaWeaponComponentLink(
                weapon_name=weapon_name,
                weapon_hash=weapon_hash,
                component_name=component_name,
                component_hash=_hash(
                    raw_link.get("component_hash"), f"{link_label}.component_hash"
                ),
            ))
        entry_points = _unique_text(
            item.get("script_entry_points"), f"{label}.script_entry_points"
        )
        if any(not _ENTRY_POINT_PATTERN.fullmatch(value) for value in entry_points):
            raise ValueError(f"{label}.script_entry_points contains an invalid type name")
        if runtime and any(value.casefold() not in runtime for value in entry_points):
            raise ValueError(
                f"{label} references a script entry point not declared by runtime.assemblies"
            )
        raw_assets = item.get("visual_assets", [])
        if not isinstance(raw_assets, list) or not raw_assets:
            raise ValueError(f"{label}.visual_assets must be a non-empty array")
        result.append(WeaponEnhancementContract(
            enhancement_id=enhancement_id,
            name=_required_text(item, "name", label),
            mode=mode,
            weapon_components=tuple(links),
            script_entry_points=entry_points,
            visual_assets=tuple(
                _visual_progression(asset, f"{label}.visual_assets[{asset_index}]")
                for asset_index, asset in enumerate(raw_assets, start=1)
            ),
        ))
    return tuple(result)
